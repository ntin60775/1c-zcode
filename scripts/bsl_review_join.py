#!/usr/bin/env python3
"""bsl_review_join.py — скрипт-джойн ревьюера 1c-bsl-review-standards (ишью #24).

Склейка трёх входов: сохранённые страницы unica.check одного узла ×
references/routing.md × .zcode/1c/standards-exceptions.yaml → размеченные
находки и строки отчёта. Чистая функция: MCP и сеть не трогает; незнакомая
схема любого входа — fail-closed (exit 2), частичного отчёта нет.

Домен A (стайл-пак), строки-семьи routing (naming, «используйте X.Y»),
раскрытие стандартов и компиляционная сверка по метаданным — не здесь,
их применяет агент (шаги 4–6 SKILL.md). Скрипт ускоряет шаги 4–5,
его отсутствие процедуру не ломает.

Вход страниц — structured content ответа unica.check 0.13: обёртка {ok, data}
или сам data; обязательное ядро — status и diagnostics[]; отсутствие
validators даёт предупреждение (readiness-ответ юники validators не отдаёт).
Точные имена полей места живого ответа фиксирует снапшот-фикстура.

Спека: docs/BSL-REVIEW-JOIN-SPEC.md.
Запуск:
  python3 bsl_review_join.py --node <addr> --check-out FILE [--check-out FILE…] \
      --project ROOT [--routing PATH] [--exceptions PATH] [--text]
exit 0 — отчёт готов (чисто или с находками — решает агент по JSON);
exit 2 — fail-closed по любому входу, причина в stderr.
"""
import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA = "bsl-review-join/1"
DEFAULT_ROUTING = "../skills/1c-bsl-review-standards/references/routing.md"
DEFAULT_EXCEPTIONS = ".zcode/1c/standards-exceptions.yaml"
LOCATION_KEYS = ("location", "range", "span", "position")
CURSOR_KEYS = ("cursor", "next_cursor")
REPORT_CLASSES = ("query-performance", "metric")


class FailClosed(Exception):
	"""Незнакомая схема входа: частичного отчёта не будет."""


# ── вход 1: страницы unica.check ─────────────────────────────────────────────

def page_core(obj, origin):
	"""Ядро страницы: data обёртки или сам объект; валидация обязательного."""
	if not isinstance(obj, dict):
		raise FailClosed(f"{origin}: ответ не объект")
	data = obj.get("data")
	if not isinstance(data, dict):
		if "status" in obj:
			data = obj
		else:
			raise FailClosed(f"{origin}: нет data и нет status")
	status = data.get("status")
	if status not in ("passed", "failed"):
		raise FailClosed(f"{origin}: status={status!r} вне passed/failed")
	diags = data.get("diagnostics")
	if not isinstance(diags, list):
		raise FailClosed(f"{origin}: diagnostics отсутствует или не список")
	return data, diags


def finding_of(diag, origin):
	"""Находка: (code, message, location); без code/message — fail-closed."""
	if not isinstance(diag, dict):
		raise FailClosed(f"{origin}: элемент diagnostics не объект")
	code = diag.get("code")
	message = diag.get("message")
	if not isinstance(code, str) or not code.strip():
		raise FailClosed(f"{origin}: диагностика без кода: {diag!r}")
	if not isinstance(message, str) or not message.strip():
		raise FailClosed(f"{origin}: {code}: диагностика без message")
	location = ""
	for key in LOCATION_KEYS:
		val = diag.get(key)
		if isinstance(val, str) and val.strip():
			location = val.strip()
			break
		if isinstance(val, (dict, list)):
			location = json.dumps(val, sort_keys=True, ensure_ascii=False)
			break
	return {"code": code.strip(), "message": message, "location": location}


def load_pages(paths):
	"""Страницы узла → (находки с дедупом, suppressed, pages_complete, warnings)."""
	warnings = []
	findings, seen = [], set()
	suppressed_known = suppressed_new = 0
	pages_complete = True
	for path in paths:
		origin = Path(path).name
		try:
			obj = json.loads(Path(path).read_text(encoding="utf-8"))
		except OSError as e:
			raise FailClosed(f"{origin}: файл не читается: {e}")
		except json.JSONDecodeError as e:
			raise FailClosed(f"{origin}: не JSON: {e}")
		data, diags = page_core(obj, origin)
		if "validators" not in data:
			warnings.append(f"{origin}: validators не заявлены в ответе")
		for key in ("known", "new"):
			val = data.get("suppressed", {})
			if isinstance(val, dict) and isinstance(val.get(key), int):
				if key == "known":
					suppressed_known = val[key]
				else:
					suppressed_new = val[key]
		for diag in diags:
			f = finding_of(diag, origin)
			key = (f["code"], f["message"], f["location"])
			if key not in seen:
				seen.add(key)
				findings.append(f)
		cursor = ""
		for holder in (data, obj):
			for key in CURSOR_KEYS:
				val = holder.get(key)
				if isinstance(val, str) and val.strip():
					cursor = val.strip()
		if cursor:
			pages_complete = False
	return findings, (suppressed_known, suppressed_new), pages_complete, warnings


# ── вход 2: таблица маршрутизации ────────────────────────────────────────────

ROUTING_HEADER = ("код диагностики", "класс", "действие по умолчанию",
                  "стандарт", "блокер")
CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def parse_routing(path):
	"""routing.md → строки таблицы. Ячейка «Код»: `CODE` или `CODE (маркер)`.
	Маркер «общий» — fallback-строка кода; ядро иного маркера — часть до «→»,
	сопоставляется подстрокой сообщения. Ячейки не в этой форме (семьи,
	«Неизвестный код») возвращаются без code — их применяет агент."""
	try:
		lines = Path(path).read_text(encoding="utf-8").splitlines()
	except OSError as e:
		raise FailClosed(f"routing не читается: {e}")
	try:
		head = next(i for i, ln in enumerate(lines)
		            if [c.strip().lower() for c in ln.strip().strip("|").split("|")]
		            == list(ROUTING_HEADER))
	except StopIteration:
		raise FailClosed("routing: нет строки заголовка «Код диагностики | …»")
	rows = []
	for ln in lines[head + 1:]:
		if not ln.strip().startswith("|"):
			if rows:
				break
			continue
		cells = [c.strip() for c in ln.strip().strip("|").split("|")]
		if len(cells) != 5:
			if set("".join(cells)) <= set("-: "):
				continue
			raise FailClosed(f"routing: в строке {len(cells)} ячеек вместо 5: {ln[:80]}")
		raw_code, raw_class, action, standard, blocker = cells
		code, marker_core, is_fallback = None, None, False
		naked = raw_code.replace("`", "").strip()
		if "(" in naked and naked.endswith(")"):
			naked, marker = [p.strip() for p in naked.rsplit("(", 1)]
			marker = marker.rstrip(")").replace("`", "").strip()
			core = marker.split("→")[0].strip()
			if core == "общий":
				is_fallback = True
			elif core:
				marker_core = core
		if CODE_RE.match(naked):
			code = naked
		elif naked:
			code = None
		rows.append({
			"code": code, "marker_core": marker_core, "is_fallback": is_fallback,
			"classes": {c.strip() for c in raw_class.split("+") if c.strip()},
			"action": action, "standard": standard,
			"blocker": blocker.lower().startswith("да"),
		})
	if not rows:
		raise FailClosed("routing: таблица пуста")
	return rows


def match_row(rows, finding):
	"""Строка таблицы: маркерные раньше fallback; ни одной — None."""
	code, message = finding["code"], finding["message"]
	marked = [r for r in rows if r["code"] == code and r["marker_core"]
	          and r["marker_core"] in message]
	if marked:
		return marked[0]
	generic = [r for r in rows if r["code"] == code and not r["marker_core"]]
	if generic:
		return generic[0]
	return None


# ── вход 3: журнал исключений (узкое подмножество yaml, stdlib) ──────────────

class YamlBroken(Exception):
	pass


def parse_scalar(text):
	text = text.strip()
	if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
		return text[1:-1]
	return text


BLOCK_INDICATORS = (">", ">-", "|", "|-")


def parse_exceptions(path, warnings):
	"""Формат exceptions-contract.md version: 1. Неизвестный ключ — предупреждение;
	битая структура или version ≠ 1 — fail-closed. Файла нет — None."""
	if not Path(path).is_file():
		return None
	try:
		lines = Path(path).read_text(encoding="utf-8").splitlines()
	except OSError as e:
		raise FailClosed(f"журнал не читается: {e}")
	version, records, cur, block_key, block_indent = None, [], None, None, None
	for n, raw in enumerate(lines, 1):
		stripped = raw.strip()
		if not stripped or stripped.startswith("#"):
			continue
		indent = len(raw) - len(raw.lstrip())
		if cur is not None and block_key is not None and indent > block_indent:
			cur[block_key] = (cur.get(block_key, "") + " " + stripped).strip()
			continue
		if cur is not None and block_key is not None:
			block_key, block_indent = None, None
		if indent == 0:
			if stripped.startswith("- "):
				raise YamlBroken(f"строка {n}: элемент списка без exceptions:")
			key, sep, val = stripped.partition(":")
			if not sep:
				raise YamlBroken(f"строка {n}: не «ключ: значение»: {stripped[:60]}")
			key, val = key.strip(), parse_scalar(val)
			if key == "version":
				version = val
			elif key == "exceptions":
				if val and val != "[]":
					raise YamlBroken(f"строка {n}: exceptions: со скаляром")
			else:
				raise YamlBroken(f"строка {n}: незнакомый ключ верхнего уровня {key!r}")
		elif stripped.startswith("- "):
			body = stripped[2:]
			key, sep, val = body.partition(":")
			if not sep:
				raise YamlBroken(f"строка {n}: элемент без ключа: {body[:60]}")
			cur = {"__line": n}
			records.append(cur)
			key, val = key.strip(), parse_scalar(val)
			if val in BLOCK_INDICATORS or not val:
				block_key, block_indent = key, indent
			else:
				cur[key] = val
		else:
			if cur is None:
				raise YamlBroken(f"строка {n}: ключ вне записи: {stripped[:60]}")
			key, sep, val = stripped.partition(":")
			if not sep:
				raise YamlBroken(f"строка {n}: не «ключ: значение»: {stripped[:60]}")
			key, val = key.strip(), parse_scalar(val)
			if key in cur and key not in ("id",):
				warnings.append(f"строка {n}: ключ {key!r} перезаписывает "
				                f"значение той же записи")
			if val in BLOCK_INDICATORS or not val:
				block_key, block_indent = key, indent
			else:
				cur[key] = val
	if version is None:
		raise FailClosed("журнал: нет version")
	if version != "1":
		raise FailClosed(f"журнал: незнакомая version={version!r}, "
		                 f"сверку исключений остановил (fail-closed)")
	out, seen_ids = [], set()
	known_keys = {"id", "diagnostic", "subject", "scope", "standard",
	              "rationale", "decided", "owner"}
	for i, rec in enumerate(records, 1):
		line = rec.pop("__line", i)
		rec_id = rec.get("id") or f"<#{line}>"
		for k in rec:
			if k not in known_keys:
				warnings.append(f"журнал: запись {rec_id}: незнакомый ключ {k!r}")
		if rec_id in seen_ids:
			warnings.append(f"журнал: id {rec_id!r} повторяется, обе записи не применяю")
			out.append({"id": rec_id, "valid": False, "problem": "дубликат id"})
			continue
		seen_ids.add(rec_id)
		missing = [k for k in ("rationale", "owner") if not rec.get(k)]
		if missing:
			out.append({"id": rec_id, "valid": False,
			            "problem": f"пусто: {', '.join(missing)} (трактуется как "
			                       f"отсутствие исключения)"})
			continue
		rec["valid"] = True
		out.append(rec)
	return out


def scope_covers(rec, node, location):
	scope = rec.get("scope", "all")
	if scope == "all":
		return True
	if scope.startswith("node:"):
		return scope[len("node:"):] == node
	if scope.startswith("file:"):
		return bool(location) and scope[len("file:"):] in location
	return False


def exception_for(records, finding, node, warnings):
	"""Первая действующая запись: diagnostic точно + subject в сообщении +
	scope покрывает; file: без различимого пути — предупреждение, не молчание."""
	code, message, location = finding["code"], finding["message"], finding["location"]
	for rec in records:
		if not rec.get("valid") or rec.get("diagnostic") != code:
			continue
		subject = rec.get("subject")
		if not subject or subject not in message:
			continue
		scope = rec.get("scope", "all")
		if scope.startswith("file:"):
			if scope[len("file:"):] not in location:
				warn = (f"журнал: запись {rec['id']}: file-scope не покрыл находку "
				        f"{code} (путь находки не различим или другой), запись "
				        f"не применена")
				if warn not in warnings:
					warnings.append(warn)
				continue
		elif not scope_covers(rec, node, location):
			continue
		return rec
	return None


# ── склейка ──────────────────────────────────────────────────────────────────

def verdict_of(row):
	"""Кандидат вердикта из набора классов строки; порядок сильнейших первый."""
	classes = row["classes"]
	if "compile-risk" in classes:
		return "risk"
	if "metadata" in classes:
		return "fix"  # escalated: правка кода не лечит, см. escalated
	if "form" in classes:
		return "fix"  # route: domain-A
	if "analyzer-noise" in classes:
		return "verify-noise"
	if classes & set(REPORT_CLASSES):
		return "report"
	if classes & {"naming", "deprecated-api"}:
		return "fix"
	return "needs-docs"


def join(node, findings, rows, records, suppressed, pages_complete, warnings):
	counts = {"total": len(findings), "live": 0, "waived": 0, "report": 0,
	          "unknown": 0, "escalated": 0}
	out = []
	for f in findings:
		row = match_row(rows, f)
		entry = dict(f)
		if row is None:
			entry.update({"matched": False, "verdict_candidate": "needs-docs",
			              "exception": None,
			              "report_line": f"unica.check: {f['code']} → ? → needs-docs → "
			                             f"нет строки в таблице, раскрыть unica.docs"})
			counts["unknown"] += 1
			counts["live"] += 1
			out.append(entry)
			continue
		entry.update({"matched": True,
		              "row": {"classes": sorted(row["classes"]), "action": row["action"],
		                      "standard": row["standard"], "blocker": row["blocker"]}})
		verdict = verdict_of(row)
		entry["verdict_candidate"] = verdict
		entry["escalated"] = "metadata" in row["classes"]
		entry["route"] = "domain-A" if "form" in row["classes"] else None
		rec = None
		if verdict != "risk" and records:
			rec = exception_for(records, f, node, warnings)
		if rec is not None:
			entry["verdict_candidate"] = "waived"
			entry["exception"] = rec["id"]
			counts["waived"] += 1
			rationale = " ".join(rec.get("rationale", "").split())
			entry["report_line"] = (f"unica.check: {f['code']} → {row['standard']} → "
			                        f"waived → {rec['id']}: {rationale}")
		else:
			counts["live"] += 1
			if entry["escalated"]:
				counts["escalated"] += 1
				entry["report_line"] = (f"unica.check: {f['code']} → {row['standard']} → "
				                        f"fix (эскалация) → {row['action']}")
			elif verdict == "needs-docs":
				warnings.append(f"{f['code']}: строка есть, класс незнаком: "
				                f"{sorted(row['classes'])}")
				entry["report_line"] = (f"unica.check: {f['code']} → ? → needs-docs → "
				                        f"незнакомый класс, раскрыть unica.docs")
			else:
				entry["report_line"] = (f"unica.check: {f['code']} → {row['standard']} → "
				                        f"{verdict} → {row['action']}")
			if verdict == "report":
				counts["report"] += 1
		out.append(entry)
	return {"schema": SCHEMA, "node": node, "pages": None,
	        "pages_complete": pages_complete,
	        "suppressed_known": suppressed[0], "suppressed_new": suppressed[1],
	        "counts": counts, "findings": out, "journal_problems": [
		        f"журнал: {r['id']}: {r['problem']}" for r in (records or [])
		        if not r.get("valid")], "warnings": warnings}


def to_text(report):
	lines = [f"Узел {report['node']} — страниц: {report['pages']}, "
	         f"дочитаны: {'да' if report['pages_complete'] else 'НЕТ — дочитай cursor'}"]
	if report["suppressed_known"] or report["suppressed_new"]:
		lines.append(f"Базовая линия скрыла: known={report['suppressed_known']}, "
		             f"new={report['suppressed_new']}")
	for f in report["findings"]:
		lines.append(f["report_line"])
	c = report["counts"]
	lines.append(f"Итого: всего {c['total']}, живых {c['live']}, waived {c['waived']}, "
	             f"report {c['report']}, needs-docs {c['unknown']}, "
	             f"эскалаций {c['escalated']}")
	lines.extend(report["journal_problems"])
	lines.extend(f"замечание: {w}" for w in report["warnings"])
	return "\n".join(lines)


def main():
	ap = argparse.ArgumentParser(description="Джойн unica.check × routing × exceptions")
	ap.add_argument("--node", required=True, help="адрес узла unica")
	ap.add_argument("--check-out", action="append", required=True, dest="pages",
	                help="JSON-файл со страницей ответа unica.check (повторяем)")
	ap.add_argument("--project", required=True, help="корень 1С-проекта")
	ap.add_argument("--routing", default=None)
	ap.add_argument("--exceptions", default=None)
	group = ap.add_mutually_exclusive_group()
	group.add_argument("--json", dest="fmt", action="store_const", const="json")
	group.add_argument("--text", dest="fmt", action="store_const", const="text")
	ap.set_defaults(fmt="json")
	args = ap.parse_args()

	routing = (Path(args.routing) if args.routing
	           else Path(__file__).resolve().parent.parent / DEFAULT_ROUTING)
	exceptions = (Path(args.exceptions) if args.exceptions
	              else Path(args.project) / DEFAULT_EXCEPTIONS)
	try:
		warnings = []
		findings, suppressed, complete, page_warn = load_pages(args.pages)
		warnings.extend(page_warn)
		rows = parse_routing(routing)
		records = parse_exceptions(exceptions, warnings)
	except (FailClosed, YamlBroken) as e:
		print(f"fail-closed: {e}", file=sys.stderr)
		return 2
	report = join(args.node, findings, rows, records, suppressed, complete, warnings)
	report["pages"] = len(args.pages)
	if args.fmt == "text":
		print(to_text(report))
	else:
		print(json.dumps(report, ensure_ascii=False, indent=2))
	return 0


if __name__ == "__main__":
	sys.exit(main())
