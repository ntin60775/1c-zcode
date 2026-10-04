#!/usr/bin/env python3
"""e2e_sweep.py — зачистка и постпроверка Е2Е-данных на тестовой базе 1С.

Изоляция e2e-тестов без транзакционного отката (yAxUnit-механика в новом
стеке невыразима: execute_code — отдельные вызовы; краш посреди теста
оставил бы открытую транзакцию с блокировками). Договор вместо отката:
тестовые данные носят маркер («Е2Е-» по умолчанию) в Наименование
(справочники) и Номер (документы) — мусор всегда виден и вычищаем. Этот
скрипт делает дисциплину машиной, а не памятью агента: после прогона
run_e2e.sh сам находит остатки по маркеру, удаляет их и красит прогон,
если вычистить не удалось.

Работает через MCP-прокси 1c-db (execute_code). Проектонезависим: имена
объектов не заданы в коде — запрос строится по Метаданные.Справочники /
Метаданные.Документы динамически. Регистры не покрываются (нет общего
строкового реквизита) — такие данные тесты обязаны чистить финализатором.

Режимы:
  (по умолчанию)  только проверка: exit 0 — чисто, 1 — есть остатки,
                  2 — прокси недоступен/ошибка.
  --apply         проверка → удаление (до 3 проходов, документы раньше
                  справочников) → повторная проверка: exit 0 — чисто,
                  3 — остатки после зачистки, 2 — прокси недоступен.

Опции: --marker S (по умолчанию Е2Е-), --url U (по умолчанию из
.zcode/1c/contour.json 1c_db.url, иначе http://127.0.0.1:6003/mcp),
--timeout SEC, --json (машинный вывод {found_before, deleted, found_after,
failed}).

Запуск: python3 e2e_sweep.py <project-root> [--apply] [--json]
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_MARKER = "Е2Е-"
DEFAULT_URL = "http://127.0.0.1:6003/mcp"
MAX_PASSES = 3

# ── минимальный MCP-клиент (Streamable HTTP, stdlib) ────────────────────────

_SEQ = 0


def _post(url: str, payload: dict, session: str, timeout: int):
	req = urllib.request.Request(
		url, data=json.dumps(payload).encode("utf-8"),
		headers={"Content-Type": "application/json",
		         "Accept": "application/json, text/event-stream",
		         **({"Mcp-Session-Id": session} if session else {})})
	with urllib.request.urlopen(req, timeout=timeout) as resp:
		body = resp.read().decode("utf-8", errors="replace")
		return resp.headers.get("mcp-session-id"), body


def _parse_body(body: str):
	body = body.strip()
	if body.startswith("{"):
		return json.loads(body)
	for line in body.splitlines():  # SSE: event: message / data: {...}
		if line.startswith("data:"):
			return json.loads(line[5:].strip())
	raise RuntimeError("неожиданный ответ прокси: " + body[:200])


def mcp_call(url: str, method: str, params: dict, session: str,
             timeout: int):
	"""MCP-вызов; возвращает result (или бросает RuntimeError)."""
	global _SEQ
	_SEQ += 1
	_, body = _post(url, {"jsonrpc": "2.0", "id": _SEQ, "method": method,
	                      "params": params}, session, timeout)
	resp = _parse_body(body)
	if "error" in resp:
		raise RuntimeError(f"MCP {method}: {resp['error'].get('message')}")
	return resp["result"]


def mcp_session(url: str, timeout: int) -> str:
	"""Handshake: initialize → initialized → session id."""
	_SEQ = 0
	req_id, body, headers = 1, None, None
	# initialize возвращает session id в заголовке
	payload = {"jsonrpc": "2.0", "id": req_id, "method": "initialize",
	           "params": {"protocolVersion": "2024-11-05", "capabilities": {},
	                      "clientInfo": {"name": "e2e-sweep", "version": "1"}}}
	req = urllib.request.Request(
		url, data=json.dumps(payload).encode("utf-8"),
		headers={"Content-Type": "application/json",
		         "Accept": "application/json, text/event-stream"})
	with urllib.request.urlopen(req, timeout=timeout) as resp:
		body = resp.read().decode("utf-8", errors="replace")
		session = resp.headers.get("mcp-session-id", "")
	_parse_body(body)
	try:  # уведомление подтверждения — ответ не обязателен
		_post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"},
		      session, timeout)
	except urllib.error.HTTPError as e:
		if e.code not in (200, 202, 204):
			raise
	return session


def execute_code(url: str, session: str, timeout: int, code: str):
	"""execute_code через 1c-db; возвращает значение Результат (str)."""
	result = mcp_call(url, "tools/call",
	                  {"name": "execute_code",
	                   "arguments": {"code": code}}, session, timeout)
	if result.get("isError"):
		text = "; ".join(c.get("text", "") for c in result.get("content", []))
		raise RuntimeError("execute_code: " + text[:400])
	for c in result.get("content", []):
		if c.get("type") == "text":
			return c.get("text", "")
	raise RuntimeError("execute_code: пустой ответ")


# ── генерация BSL ────────────────────────────────────────────────────────────

CHECK_TMPL = """\
Маркер = "{marker}";
Кав = Символ(34);
Пат = Маркер + "%";
Счёт = 0;
Элементы = Новый Массив;
Для Каждого МД Из Метаданные.Справочники Цикл
	Попытка
		ТекстЗапроса = "ВЫБРАТЬ ПЕРВЫЕ 1000 Ссылка ИЗ " + МД.ПолноеИмя() + " ГДЕ Наименование ПОДОБНО " + Кав + Пат + Кав;
		Выборка = Новый Запрос(ТекстЗапроса).Выполнить().Выбрать();
		Счёт = Счёт + Выборка.Количество();
		Пока Выборка.Следующий() Цикл
			Элементы.Добавить(Новый Структура("t, g, n", Выборка.Ссылка.Метаданные().ПолноеИмя(), Строка(Выборка.Ссылка.УникальныйИдентификатор()), Строка(Выборка.Ссылка)));
		КонецЦикла;
	Исключение
	КонецПопытки;
КонецЦикла;
Для Каждого МД Из Метаданные.Документы Цикл
	Попытка
		ТекстЗапроса = "ВЫБРАТЬ ПЕРВЫЕ 1000 Ссылка ИЗ " + МД.ПолноеИмя() + " ГДЕ Номер ПОДОБНО " + Кав + Пат + Кав;
		Выборка = Новый Запрос(ТекстЗапроса).Выполнить().Выбрать();
		Счёт = Счёт + Выборка.Количество();
		Пока Выборка.Следующий() Цикл
			Элементы.Добавить(Новый Структура("t, g, n", Выборка.Ссылка.Метаданные().ПолноеИмя(), Строка(Выборка.Ссылка.УникальныйИдентификатор()), Строка(Выборка.Ссылка)));
		КонецЦикла;
	Исключение
	КонецПопытки;
КонецЦикла;
ЗаписьJSON = Новый ЗаписьJSON;
ЗаписьJSON.УстановитьСтроку();
ЗаписатьJSON(ЗаписьJSON, Новый Структура("found, items", Счёт, Элементы));
Результат = ЗаписьJSON.Закрыть();
"""


def manager_name(full_name: str) -> str:
	"""ПолноеИмя метаданных → имя менеджера: Справочник.X → Справочники.X."""
	kind, _, rest = full_name.partition(".")
	plural = {"Справочник": "Справочники", "Документ": "Документы"}.get(kind)
	if not plural:
		raise ValueError("не покрытый sweep-тип: " + full_name)
	return plural + "." + rest


def apply_tmpl(items: list) -> str:
	"""BSL-скрипт удаления: список «менеджер|guid» (документы раньше
	справочников — ссылки живут в документах)."""
	rows = []
	for it in sorted(items, key=lambda x: 0 if x["t"].startswith("Документ") else 1):
		rows.append(f'\tСписок.Добавить("{manager_name(it["t"])}|{it["g"]}");')
	body = "\n".join(rows)
	return (
		"Удалено = 0;\nНеудачи = Новый Массив;\nСписок = Новый Массив;\n"
		+ body
		+ "\nКав = Символ(34);\n"
		"Для Каждого Стр Из Список Цикл\n"
		"\tЧасти = СтрРазделить(Стр, \"|\");\n"
		"\tПопытка\n"
		"\t\tВыраж = Части[0] + \".ПолучитьСсылку(Новый УникальныйИдентификатор(\" + Кав + Части[1] + Кав + \"))\";\n"
		"\t\tСсылка = Вычислить(Выраж);\n"
		"\t\tСсылка.Удалить();\n"
		"\t\tУдалено = Удалено + 1;\n"
		"\tИсключение\n"
		"\t\tНеудачи.Добавить(Стр);\n"
		"\tКонецПопытки;\n"
		"КонецЦикла;\n"
		"ЗаписьJSON = Новый ЗаписьJSON;\n"
		"ЗаписьJSON.УстановитьСтроку();\n"
		"ЗаписатьJSON(ЗаписьJSON, Новый Структура(\"deleted, failed\", Удалено, Неудачи));\n"
		"Результат = ЗаписьJSON.Закрыть();\n")


# ── обвязка ──────────────────────────────────────────────────────────────────

def proxy_url(root: Path, explicit: str) -> str:
	if explicit:
		return explicit
	import os
	env = os.environ.get("ONEC_DB_URL")
	if env:
		return env
	data = {}
	try:
		data = json.loads((root / ".zcode" / "1c" / "contour.json")
		                  .read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError):
		pass
	return ((data or {}).get("1c_db") or {}).get("url") or DEFAULT_URL


def sweep(url: str, marker: str, timeout: int, do_apply: bool) -> dict:
	session = mcp_session(url, timeout)
	check = json.loads(execute_code(
		url, session, timeout, CHECK_TMPL.format(marker=marker)))
	out = {"found_before": check["found"], "deleted": 0,
	       "found_after": check["found"], "failed": []}
	if not do_apply or check["found"] == 0:
		return out
	pending = check["items"]
	for _ in range(MAX_PASSES):
		applied = json.loads(execute_code(url, session, timeout,
		                                  apply_tmpl(pending)))
		out["deleted"] += applied["deleted"]
		pending = [{"t": f.split("|", 1)[0].replace("Справочники.", "Справочник.")
		                              .replace("Документы.", "Документ."),
		            "g": f.split("|", 1)[1], "n": ""}
		           for f in applied["failed"]]
		if not applied["failed"]:
			break
	after = json.loads(execute_code(
		url, session, timeout, CHECK_TMPL.format(marker=marker)))
	out["found_after"] = after["found"]
	out["failed"] = [f"{i['t']}|{i['n']}" for i in pending]
	return out


def main() -> int:
	flags = {a for a in sys.argv[1:] if a.startswith("--")}
	args = [a for a in sys.argv[1:] if not a.startswith("--")]
	do_apply = "--apply" in flags
	as_json = "--json" in flags
	positional = [a for a in args if "=" not in a or a.startswith("/")]
	# значения --marker/--url/--timeout идут парами
	def opt(name, default=None):
		if name in sys.argv:
			return sys.argv[sys.argv.index(name) + 1]
		return default

	marker = opt("--marker", DEFAULT_MARKER)
	url_opt = opt("--url")
	timeout = int(opt("--timeout", "120"))
	root = Path(positional[0]).resolve() if positional else Path.cwd()
	url = proxy_url(root, url_opt)

	try:
		out = sweep(url, marker, timeout, do_apply)
	except (urllib.error.URLError, RuntimeError, OSError) as e:
		msg = f"✗ прокси 1c-db недоступен ({url}): {e}"
		print(msg, file=sys.stderr)
		if as_json:
			print(json.dumps({"error": str(e), "url": url}, ensure_ascii=False))
		return 2

	if as_json:
		print(json.dumps(out, ensure_ascii=False))
	elif out["found_before"] == 0:
		print(f"✓ остатков по маркеру {marker!r} нет")
	elif not do_apply:
		print(f"✗ остатков по маркеру {marker!r}: {out['found_before']} "
		      "(зачистка: --apply)")
	else:
		print(f"зачищено: {out['deleted']}; осталось: {out['found_after']}")
		for f in out["failed"][:20]:
			print(f"  не удалено: {f}")

	if out["found_before"] == 0 or out["found_after"] == 0:
		return 0
	return 3 if do_apply else 1


if __name__ == "__main__":
	sys.exit(main())
