#!/usr/bin/env python3
"""bsl_style_gate.py — стайл-гейт изменённых *.bsl (PostToolUse, блокирующий).

Единственный исполняемый авторитет формы BSL-кода — пак 1c-bsl-code-style
(приоритет 1 в иерархии качества контура: стандарты и стат-анализ его не
переопределяют). После правки .bsl из src/|tests/ гейт прогоняет чекер пака
и БЛОКИРУЕТ правку (exit 2), если нарушения стоят В ИЗМЕНЁННЫХ СТРОКАХ:
чужой старый код не в счёт (он остаётся фоном в stderr, чинится при случае).
Правка уже применена, поэтому «блок» = принуждение агента немедленно
исправить форму и повторить правку.

Два канала правки:
  1. Write/Edit/ApplyPatch (эскалационные прямые правки мимо юники);
  2. unica.apply — штатный канал: unica_source_gate направляет в него правки
     src/**, поэтому без этого бриджа основной путь стилем не покрывался
     (ишью #10/#11). unica.apply двухфазный: вызов-план несёт at+ops с полным
     новым текстом (записи ещё нет), вызов-исполнение — только executionToken.
     Бридж строится только на публичном контракте вызова, юника не трогается;
     от tool_response он не зависит (в payload PostToolUse поле есть — case
     PostToolUse рантайма ZCode, — но рендер для модели усечённый, опора на
     него хрупка).

Фаза плана (tool_input = at+ops): ops, адресованные в тела BSL-модулей
(at заканчивается ".Body" — контракт адресации 0.13), сохраняются в
состояние сессии (state/1c/style-bridge/, TTL 20 мин как у ib_lock,
уборка в session_clean) и прогоняются предварительным снипет-чеком —
advisory ещё до записи (текст без контекста модуля, часть проверок
неприменима — не блок).
Фаза исполнения (tool_input = executionToken): правка уже на диске; тексты
незакрытых планов сессии ищутся по всем .bsl канонических каталогов
исходников (SOURCE_PREFIXES) — файл и диапазон даёт совпадение текста
(_span_of_new_text). Синтетический резолв at → физический путь сознательно
не применяется: раскладка — внутренняя деталь юники (наружу только аварийный
unica.resolve), зависимость от неё хрупка. Текст может совпасть в нескольких
файлах — проверяются все (объединение, safe-направление ишью #11: больше
покрытия, не меньше). Далее штатный чекер, hot/cold; hot → БЛОК (exit 2),
как у прямых правок. Потеря состояния (рестарт сессии, TTL) — тихий пропуск.

Резолв чекера (первый найденный):
  1. env 1C_STYLE_ENGINE — исполняемый движок с контрактом
     `check <files> [--scenarios <dir>] [--fix]`, exit 0/1 (будущий Go-движок);
  2. bsl-style-engine в PATH — тот же контракт;
  3. вендоренный пак 1c-bsl-code-style:
     <корень>/.zcode/skills/1c-bsl-code-style/scripts/bsl_style_check.py
     (`check` не принимает, вызывается как `<скрипт> <files>`, exit 0/1);
  4. <корень>/.zcode/style/scripts/bsl_style_check.py — прежняя раскладка.
Не найден — тихий пропуск (наличие подсветит /1c-doctor). Чекер упал или
таймаут — правку не роняем (не сигнал о нарушениях).

Журнал: rule-audit.jsonl, rule: "bsl-style-gate".
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from contour_common import (
	SOURCE_PREFIXES,
	audit,
	normalize_source_path,
	project_root,
	read_event,
	session_id,
	session_state_dir,
	unica_args,
	unica_verb,
)

RULE = "bsl-style-gate"
CHECKER_TIMEOUT_S = 30
# Блокирующий режим: exit 2 у PostToolUse не откатывает применённую правку,
# но принуждает агента немедленно исправить форму (stderr — причина и отчёт).
# Блокируем только нарушения В ИЗМЕНЁННЫХ строках: чужой старый код не в счёт.
HARD_BLOCK = True

ENGINE_ENV = "1C_STYLE_ENGINE"
ENGINE_NAME = "bsl-style-engine"
PACK_CHECKERS = (
	".zcode/skills/1c-bsl-code-style/scripts/bsl_style_check.py",
	".zcode/style/scripts/bsl_style_check.py",
)

# ── бридж unica.apply (ишью #11) ─────────────────────────────────────────────
BRIDGE_DIR = "style-bridge"
BRIDGE_TTL_S = 20 * 60
BSL_OP_AT_SUFFIX = ".Body"  # узел тела модуля/метода в адресации 0.13

APPLY_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Update|Add) File: (.+)$", re.M)
APPLY_PATCH_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
CHECKER_LINE_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+): (?P<body>.+)$")


# ── изменённые диапазоны строк (1-based, inclusive); None = весь файл ────────

def _span_of_new_text(new_text: str, needle: str):
	"""Диапазон строк, который занимает needle в уже применённом файле."""
	if not needle:
		return None
	idx = new_text.find(needle)
	if idx < 0:
		return None
	start = new_text.count("\n", 0, idx) + 1
	return [(start, start + needle.count("\n"))]


def _patch_file_spans(patch: str) -> dict[str, list[tuple[int, int]]]:
	"""Диапазоны новых строк по hunk-заголовкам, по файлам патча."""
	out: dict[str, list[tuple[int, int]]] = {}
	current = None
	for line in patch.splitlines():
		fm = APPLY_PATCH_FILE_RE.match(line)
		if fm:
			current = fm.group(1).strip()
			continue
		hm = APPLY_PATCH_HUNK_RE.match(line)
		if hm and current:
			start, count = int(hm.group(1)), int(hm.group(2) or 1)
			if count:
				out.setdefault(current, []).append((start, start + count - 1))
	return out


def changed_lines(event: dict, tool_name: str, rel_path: str, root: str):
	tool_input = event.get("tool_input") or {}
	if tool_name == "Write":
		return None  # файл записан целиком — вся ответственность на агенте
	try:
		new_text = (Path(root) / rel_path).read_text(encoding="utf-8", errors="replace")
	except OSError:
		return None
	if tool_name == "Edit":
		return _span_of_new_text(new_text, str(tool_input.get("new_string") or "")) \
			or None  # new_string не найден (гонка/правка не применилась) — весь файл
	spans = _patch_file_spans(str(tool_input.get("patch") or tool_input.get("input") or ""))
	match = next((sp for path, sp in spans.items()
	              if path == rel_path or path.endswith("/" + rel_path)
	              or rel_path.endswith("/" + path)), None)
	return match or None


def split_report(report: str, targets: list[str], spans_by_target: dict):
	"""Разделить вывод чекера: hot (в изменённых строках) / cold (старое)."""
	hot, cold = [], []
	for line in report.splitlines():
		m = CHECKER_LINE_RE.match(line.strip())
		assigned = False
		if m:
			for target in targets:
				if not (line.strip().startswith(target + ":")
				        or m.group("path").endswith("/" + target)):
					continue
				spans = spans_by_target.get(target)
				number = int(m.group("line"))
				if spans is None or any(a <= number <= b for a, b in spans):
					hot.append(line)
				else:
					cold.append(line)
				assigned = True
				break
		if not assigned:
			hot.append(line)  # нераспознанный вывод — считаем важным
	return hot, cold


# ── резолв чекера ────────────────────────────────────────────────────────────

def resolve_checker(root: str):
	"""(argv_префикс, контракт_check) либо None — чекер не найден."""
	env_exe = os.environ.get(ENGINE_ENV)
	if env_exe and Path(env_exe).exists():
		return [env_exe, "check"], True
	which = shutil.which(ENGINE_NAME)
	if which:
		return [which, "check"], True
	for rel in PACK_CHECKERS:
		pack = Path(root) / rel
		if pack.is_file():
			return [sys.executable, str(pack)], False
	return None


def run_checker(argv_prefix: list, check_contract: bool, targets: list[str], root: str):
	"""Отчёт чекера: строка с нарушениями, "" — чисто, None — упал/таймаут
	(сбой отличим от чистого прогона: чистая проверка закрывает план бриджа)."""
	args = argv_prefix + (["check"] if check_contract else []) + targets
	try:
		done = subprocess.run(
			args, capture_output=True, text=True, timeout=CHECKER_TIMEOUT_S, cwd=root)
	except (subprocess.TimeoutExpired, OSError):
		return None
	if done.returncode not in (0, 1):
		return None  # краш чекера — не сигнал о нарушениях
	if done.returncode == 0:
		return ""
	return (done.stdout or done.stderr or "").strip() or "bsl_style_check: нарушения найдены"


# ── кандидаты на проверку: прямые правки ─────────────────────────────────────

def bsl_targets(event: dict, cwd: str):
	"""Изменённые .bsl-исходники из tool_input (Write/Edit/ApplyPatch)."""
	tool_name = str(event.get("tool_name") or "")
	tool_input = event.get("tool_input")
	if not isinstance(tool_input, dict):
		return []
	raw_paths = []
	direct = str(tool_input.get("file_path") or tool_input.get("path") or "")
	if direct:
		raw_paths.append(direct)
	if tool_name == "ApplyPatch" or not raw_paths:
		patch = str(tool_input.get("patch") or tool_input.get("input") or "")
		raw_paths.extend(APPLY_PATCH_FILE_RE.findall(patch))
	targets = []
	for raw in raw_paths:
		norm = normalize_source_path(raw, cwd)
		if norm and norm[0].lower().endswith(".bsl"):
			targets.append(norm[0])
	return targets


# ── бридж unica.apply: состояние планов сессии ───────────────────────────────

def _bridge_path(sid: str) -> Path:
	return session_state_dir(BRIDGE_DIR) / f"{sid}.json"


def _load_plans(sid: str) -> list:
	"""Незакрытые TTL-свежие планы сессии; [] — файла нет/битый/всё истекло."""
	try:
		data = json.loads(_bridge_path(sid).read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError):
		return []
	plans = data.get("plans") if isinstance(data, dict) else None
	if not isinstance(plans, list):
		return []
	now = time.time()
	fresh = []
	for plan in plans:
		try:
			if float(plan.get("expires_ts", 0)) > now:
				fresh.append(plan)
		except (TypeError, ValueError):
			continue
	return fresh


def _save_plans(sid: str, plans: list) -> None:
	"""Атомарная запись; пустой список удаляет файл (TTL дочистит остальное)."""
	path = _bridge_path(sid)
	if not plans:
		try:
			path.unlink(missing_ok=True)
		except OSError:
			pass
		return
	try:
		path.parent.mkdir(parents=True, exist_ok=True)
		tmp = path.with_suffix(".tmp")
		tmp.write_text(json.dumps({"plans": plans}, ensure_ascii=False), encoding="utf-8")
		os.replace(tmp, path)
	except OSError:
		pass  # нет доступа к состоянию — бридж работает в рамках одной жизни


def plan_bsl_texts(args: dict) -> list[str]:
	"""Тексты ops плана, адресованные в тела BSL-модулей (at → …Body)."""
	texts = []
	ops = args.get("ops")
	if not isinstance(ops, list):
		return texts
	for op in ops:
		if not isinstance(op, dict):
			continue
		op_args = op.get("args")
		if not isinstance(op_args, dict):
			continue
		at = str(op_args.get("at") or "")
		text = op_args.get("text")
		if at.endswith(BSL_OP_AT_SUFFIX) and isinstance(text, str) and text.strip():
			texts.append(text)
	return texts


def bsl_files_under(root: str) -> list[str]:
	"""Все *.bsl в канонических каталогах исходников контура (SOURCE_PREFIXES)."""
	files = []
	for prefix in SOURCE_PREFIXES:
		base = Path(root) / prefix.rstrip("/")
		if not base.is_dir():
			continue
		files.extend(str(p) for p in base.rglob("*")
		             if p.is_file() and p.suffix.lower() == ".bsl")
	return files


def _locate_plans(root: str, plans: list):
	"""Файлы, где нашёлся текст сохранённых планов: (targets, spans, matched).

	Совпадение в нескольких файлах — все проверяются (объединение, безопасное
	направление); не найденные планы остаются незакрытыми (их исполнение,
	возможно, ещё впереди).
	"""
	contents = {}
	for path in bsl_files_under(root):
		try:
			contents[path] = Path(path).read_text(encoding="utf-8", errors="replace")
		except OSError:
			continue
	targets: list[str] = []
	spans_by_target: dict[str, list] = {}
	matched = []
	for plan in plans:
		found = False
		for text in plan.get("texts") or []:
			if not isinstance(text, str) or not text:
				continue
			for path, content in contents.items():
				span = _span_of_new_text(content, text)
				if not span:
					continue
				rel = os.path.relpath(path, root)
				if rel not in spans_by_target:
					spans_by_target[rel] = []
					targets.append(rel)
				spans_by_target[rel].extend(span)
				found = True
		if found:
			matched.append(plan)
	return targets, spans_by_target, matched


def snippet_check(root: str, texts: list[str]):
	"""Предварительный чек текста правки без контекста модуля; отчёт или None."""
	resolved = resolve_checker(root)
	if resolved is None:
		return None
	argv_prefix, check_contract = resolved
	fd, tmp_name = tempfile.mkstemp(suffix=".bsl", prefix="style-bridge-")
	os.close(fd)
	try:
		Path(tmp_name).write_text("\n".join(texts), encoding="utf-8")
		args = argv_prefix + (["check"] if check_contract else []) + [tmp_name]
		try:
			done = subprocess.run(args, capture_output=True, text=True,
			                      timeout=CHECKER_TIMEOUT_S, cwd=root)
		except (subprocess.TimeoutExpired, OSError):
			return None
		if done.returncode != 1:
			return None  # чисто либо чекер не отработал — предупреждения нет
		return (done.stdout or done.stderr or "").strip().replace(
			tmp_name + ":", "фрагмент:")
	finally:
		try:
			os.unlink(tmp_name)
		except OSError:
			pass


# ── фазы бриджа ──────────────────────────────────────────────────────────────

def handle_apply_plan(event: dict, root: str, args: dict) -> int:
	sid = session_id(event)
	label = f"unica.apply план {args.get('at') or ''}".strip()
	texts = plan_bsl_texts(args)
	if not texts:
		audit(RULE, label, "ops вне тел BSL-модулей — стиль не проверяется", "advisory")
		return 0
	plans = _load_plans(sid)
	plans.append({
		"saved_at": int(time.time()),
		"expires_ts": time.time() + BRIDGE_TTL_S,
		"texts": texts,
	})
	_save_plans(sid, plans)
	audit(RULE, label,
	      f"план сохранён ({len(texts)} BSL-ops, TTL {BRIDGE_TTL_S // 60} мин)", "advisory")
	warn = snippet_check(root, texts)
	if not warn:
		return 0
	reason = "\n".join([
		"Предварительный стайл-чек текста правки unica.apply (до записи, не блокирует:",
		"текст без контекста модуля; после исполнения проверка повторится по факту):",
		"",
		warn,
	])
	sys.stderr.write(reason + "\n")
	sys.stdout.write(json.dumps({"additionalContext": reason}, ensure_ascii=False) + "\n")
	return 0


def handle_apply_execute(event: dict, root: str) -> int:
	sid = session_id(event)
	plans = _load_plans(sid)
	if not plans:
		return 0  # потеря состояния (рестарт сессии/TTL) — тихий пропуск
	targets, spans_by_target, matched = _locate_plans(root, plans)
	if not targets:
		audit(RULE, "unica.apply исполнение",
		      "тексты сохранённых планов не найдены в .bsl — пропуск (планы оставлены)",
		      "advisory")
		return 0
	resolved = resolve_checker(root)
	if resolved is None:
		return 0
	report = run_checker(resolved[0], resolved[1], targets, root)
	if report is None:
		return 0  # чекер не отработал — планы оставляем до следующего раза
	_save_plans(sid, [p for p in plans if p not in matched])
	if not report:
		return 0
	return gate_verdict(report, targets, spans_by_target,
	                    "unica.apply: " + ", ".join(targets), "unica.apply")


# ── вердикт (общий для обоих каналов) ────────────────────────────────────────

def gate_verdict(report: str, targets: list[str], spans_by_target: dict,
                 action: str, origin: str) -> int:
	hot, cold = split_report(report, targets, spans_by_target)
	if not hot and cold:
		audit(RULE, action,
		      f"нарушения вне изменённых строк ({len(cold)}) — не блокируют", "advisory")
		sys.stderr.write(
			f"1c-zcode стайл-чек: {len(cold)} нарушений в файле вне твоей правки "
			"(старый код — не блокируют, чинить при случае):\n" + "\n".join(cold) + "\n")
		return 0

	blocks = hot or report.splitlines()
	header = [
		"БЛОК: правка нарушает стиль BSL (пак 1c-bsl-code-style — приоритетный",
		f"авторитет формы; канал: {origin}).",
		"Исправь перечисленные строки и повтори правку.",
	]
	ids = sorted({CHECKER_LINE_RE.match(line.strip()).group("body").split("]")[0] + "]"
	              for line in blocks if CHECKER_LINE_RE.match(line.strip())})
	if ids:
		header.append("Машиннофиксируемые пункты: " + ", ".join(ids)
		              + " — чекер чинит их с --fix при праве на запись.")
	if cold:
		header.append(f"Вне твоей правки (не блокируют, но есть): {len(cold)} наруш.")
	audit(RULE, action,
	      f"нарушения стиля в изменённых строках ({len(blocks)})",
	      "blocked" if HARD_BLOCK else "advisory")
	reason = "\n".join(header + ["", *blocks])
	sys.stderr.write(reason + "\n")
	# Рантайм ZCode не доставляет модели stderr блокирующего PostToolUse
	# (живой пруф sess_52a3e8cc: Edit отвечает «updated successfully»).
	# Канал доставки — stdout JSON additionalContext: попадает в разговор.
	if HARD_BLOCK:
		sys.stdout.write(json.dumps(
			{"additionalContext": reason}, ensure_ascii=False) + "\n")
	return 2 if HARD_BLOCK else 0


# ── маршрутизация ────────────────────────────────────────────────────────────

def handle_unica(event: dict, tool_name: str, cwd: str, root: str) -> int:
	"""PostToolUse вызова юники: стайл-бридж интересует только unica.apply."""
	if unica_verb(tool_name, root) != "apply":
		return 0
	args = unica_args(event.get("tool_input"))
	if "executionToken" in args:
		return handle_apply_execute(event, root)
	if isinstance(args.get("ops"), list):
		return handle_apply_plan(event, root, args)
	return 0


def main() -> int:
	event = read_event()
	if not event:
		return 0
	tool_name = str(event.get("tool_name") or "")
	cwd = str(event.get("cwd") or os.getcwd())
	root = project_root(cwd)
	if tool_name.startswith("mcp__"):
		return handle_unica(event, tool_name, cwd, root)
	if tool_name not in ("Write", "Edit", "ApplyPatch"):
		return 0
	targets = bsl_targets(event, cwd)
	if not targets:
		return 0

	resolved = resolve_checker(root)
	if resolved is None:
		return 0  # чекер не установлен — advisory-пропуск (см. /1c-doctor)
	report = run_checker(resolved[0], resolved[1], targets, root)
	if not report:
		return 0  # чисто или чекер не отработал — не сигнал о нарушениях
	spans_by_target = {t: changed_lines(event, tool_name, t, root) for t in targets}
	return gate_verdict(report, targets, spans_by_target,
	                    ", ".join(targets), "прямая правка Write/Edit/ApplyPatch")


if __name__ == "__main__":
	sys.exit(main())
