#!/usr/bin/env python3
"""doc_gate.py — «сначала документация платформы, потом решение» (docs-before-design).

Порт СТРОГОГО проектного гейта боевого проекта (docs-gate.ts дона 1c-omp),
взят как канон вместо более либерального плагинного doc-gate дона:
плагинный засчитывал сверкой любое чтение исходников и любой read-only вызов
юники, и сессия могла спроектировать решение, ни разу не открыв документацию.
Исходники говорят, КАК сделано, но не говорят, ЧТО допустимо платформой.

Поверхность Unica 0.15: сверка — unica.docs / unica.search (0.12-имена
documentation_search/standards_search retir'нуты); мутации — unica.apply
(код/структура) и unica.run {op: push|upload|apply|reset}. Наборы имён —
contour_common.UNICA_TOOLS_DEFAULT с override из contour.json.

Что делает гейт. Перед мутацией требует, чтобы в ЭТОЙ сессии был НЕПУСТОЙ
документационный вызов (пустой ответ/ошибка сверкой не считаются).
Структурная мутация сбрасывает флаг: следующее структурное решение снова
требует свежей сверки; серия правок КОДА (unica.apply с ops вида code.*)
флаг не сбрасывает. Прямые правки исходников (эскалации unica-source-gate)
тоже требуют сверки.

План и публикация unica.apply — одна логическая мутация, и флаг тратит
публикация: preview-звонок (at+ops) сверку требует, но не тратит; звонок
с executionToken (публикация готового плана) требует и тратит. Цикл
«сверка → план (в т. ч. со схемной ошибкой) → план → запись» проходит
на одной сверке.

У unica.run та же пара: прогон с dryRun:true сверку требует, но не тратит;
исполнение (dryRun:false; отсутствие dryRun считается исполнением —
fail-closed) требует и тратит. op раннера живёт на верхнем уровне вызова
(аргументы операции вложены в args) — резолв только с верхнего уровня,
иначе канонический вызов проходит мимо гейта как «не мутация».

Осмысленность docs-ответа считается по секциям провайдеров: ответ
содержателен, если хотя бы одна секция имеет status ok с непустыми hits.
Секции недоступных провайдеров (пустые hits, status unavailable) ответ
не роняют — на стендах с частично настроенными провайдерами иначе сверка
невозможна. Ответ вне формы «JSON со списком sections» судится по
прежним маркерам пустоты.

Состояние (хуки stateless): state/1c/doc-gate/<session_id>.json.
PostToolUse документационного вызова проставляет verified по результату.

Журнал: rule-audit.jsonl, rule: "docs-before-design".
"""
import json
import re
import sys
import time
from pathlib import Path

from contour_common import (
	SOURCE_PREFIXES,
	audit,
	project_root,
	read_event,
	session_id,
	session_state_dir,
	unica_verb,
	unica_tools,
)

RULE = "docs-before-design"

# unica.run-операции, меняющие базу/структуру (требуют сверки).
VERIFY_OPS_DEFAULT = ["push", "upload", "apply", "reset"]

CODE_OP_RE = re.compile(r"^code\.", re.I)

BLOCK_REASON = "\n".join([
	"Мутация 1С-исходников без сверки с документацией платформы (docs-before-design).",
	"",
	"Память — не источник: у памяти нет версии, а у платформы есть. Перед решением",
	"о механизме сверься с документацией (один вызов, недорого):",
	"",
	"  - unica.docs {source: platform-help | development-standard} — справка",
	"    платформы и стандарты разработки;",
	"  - unica.search — поиск по конфигурации как дополнение.",
	"",
	"Источник configuration-documentation в 0.15 отвечает unsupported_source:",
	"это граница поверхности, а не ответ. Ответ задачи (status: working) тоже",
	"не считается сверкой, пока не дочитан через unica.task.result. Поиск без",
	"совпадений сверкой не считается.",
	"",
	"Сверка исходниками (unica.view, grep по src/) отвечает на вопрос «как",
	"сделано здесь», но не «что допустимо платформой». После структурной",
	"мутации флаг сбрасывается: следующее структурное решение — снова свежая",
	"сверка.",
])


# ── файловое состояние сессии ────────────────────────────────────────────────

def _state_path(sid: str) -> Path:
	return session_state_dir("doc-gate") / f"{sid}.json"


def load_state(sid: str) -> dict:
	try:
		data = json.loads(_state_path(sid).read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError):
		return {"verified": False, "pending": False}
	return data if isinstance(data, dict) else {"verified": False, "pending": False}


def save_state(sid: str, state: dict) -> None:
	try:
		_state_path(sid).parent.mkdir(parents=True, exist_ok=True)
		_state_path(sid).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
	except OSError:
		pass  # состояние не сохранилось — гейт деградирует до «не сверено»


# ── разбор ответа документационного вызова ───────────────────────────────────

def response_text(tool_response) -> str:
	"""Текст ответа MCP-инструмента; реакция на неизвестную форму — ''. """
	if tool_response is None:
		return ""
	if isinstance(tool_response, dict):
		content = tool_response.get("content")
		if isinstance(content, list):
			parts = []
			for chunk in content:
				if isinstance(chunk, dict) and chunk.get("type") == "text":
					parts.append(str(chunk.get("text") or ""))
			if parts:
				return "\n".join(parts)
		return json.dumps(tool_response, ensure_ascii=False)
	if isinstance(tool_response, list):
		return json.dumps(tool_response, ensure_ascii=False)
	return str(tool_response)


def _content_nodes(payload):
	"""Словари ответа, где может лежать содержимое: корень, data, result."""
	if not isinstance(payload, dict):
		return
	yield payload
	for key in ("data", "result"):
		node = payload.get(key)
		if isinstance(node, dict):
			yield node


def _live_section_answers(sections) -> "bool | None":
	"""Живой провайдер с непустыми хитами; None — секции не распознаны."""
	saw_sections = False
	for section in sections:
		if not isinstance(section, dict):
			continue
		saw_sections = True
		if str(section.get("status") or "").lower() != "ok":
			continue
		hits = section.get("hits")
		if isinstance(hits, list) and hits:
			return True
	return False if saw_sections else None


def _sections_meaningful(payload) -> "bool | None":
	"""Осмысленность по секциям ответа docs-провайдеров.

	Секции недоступных провайдеров (status unavailable, пустые hits) ответ
	не роняют, если другой провайдер ответил содержательно. None — форму
	«JSON со списком sections» распознать не удалось.
	"""
	for node in _content_nodes(payload):
		sections = node.get("sections")
		if isinstance(sections, list):
			return _live_section_answers(sections)
	return None


def _non_answer(payload) -> "str | None":
	"""'task' — принятая задача, 'error' — отказ/пустота; None — ответ.

	0.15 умеет отвечать задачей (status: working) и отказывать кодом
	unsupported_source; ни то, ни другое не является сверкой.
	"""
	if not isinstance(payload, dict):
		return None
	for node in _content_nodes(payload):
		status = str(node.get("status") or "").lower()
		if status in ("working", "pending", "accepted", "queued"):
			return "task"
		if status in ("unavailable", "error", "failed", "cancelled"):
			return "error"
		code = str(node.get("code") or "").lower()
		if code in ("unsupported_source", "provider_unavailable", "unavailable"):
			return "error"
		if node.get("ok") is False:
			return "error"
	return None


def _has_content(payload) -> bool:
	"""Есть ли в ответе непустые хиты, элементы или текст документа."""
	if not isinstance(payload, dict):
		return False
	for node in _content_nodes(payload):
		for key in ("items", "hits", "matches", "documents", "results"):
			value = node.get(key)
			if isinstance(value, list) and value:
				return True
		document = node.get("document")
		if isinstance(document, dict) and str(document.get("text") or "").strip():
			return True
	return False


def _zero_totals(payload) -> bool:
	"""Сводка объёма говорит «совпадений нет», а списков содержимого нет."""
	if not isinstance(payload, dict):
		return False
	for node in _content_nodes(payload):
		totals = node.get("totals")
		if not isinstance(totals, dict):
			continue
		numbers = [value for value in totals.values()
		           if isinstance(value, (int, float)) and not isinstance(value, bool)]
		if numbers and all(value == 0 for value in numbers):
			if not any(isinstance(node.get(key), list) and node[key]
			           for key in ("items", "hits", "matches", "documents", "results")):
				return True
	return False


def docs_result_is_meaningful(text: str) -> bool:
	"""Непустой результат сверки: есть содержимое, нет признаков пустоты."""
	if not text.strip():
		return False
	try:
		payload = json.loads(text)
	except json.JSONDecodeError:
		payload = None
	if isinstance(payload, dict):
		if _non_answer(payload) is not None:
			return False
		by_sections = _sections_meaningful(payload)
		if by_sections is not None:
			return by_sections
		if _has_content(payload):
			return True
		if _zero_totals(payload):
			return False
		if any(isinstance(node.get(key), list) and not node[key]
		       for node in _content_nodes(payload)
		       for key in ("items", "hits", "matches", "documents", "results")):
			return False
	# Ответ вне формы «JSON со sections» — прежняя эвристика по маркерам.
	if re.search(r'"hits"\s*:\s*\[\s*\]', text):
		return False
	if re.search(r'"status"\s*:\s*"(?:unavailable|working|error|failed)"', text):
		return False
	if re.search(r'"code"\s*:\s*"(?:unsupported_source|provider_unavailable)"', text):
		return False
	return True


def task_id_from(payload) -> str:
	"""taskId из ответа или аргументов вызова; '' — задачи нет."""
	if not isinstance(payload, dict):
		return ""
	for node in _content_nodes(payload):
		value = node.get("taskId")
		if value:
			return str(value)
	return ""


def docs_task_pending(payload) -> bool:
	"""Ответ — принятая задача, а не готовый результат."""
	return isinstance(payload, dict) and _non_answer(payload) == "task"


def apply_is_publication(tool_input: dict) -> bool:
	"""unica.apply с executionToken и без ops — публикация готового плана.

	Флаг сверки тратит публикация: preview (at+ops) сверку требует, но не
	тратит, иначе исполнение по токену блокируется собственным планом.
	"""
	args = tool_input.get("args") if isinstance(tool_input.get("args"), dict) else tool_input
	return bool(args.get("executionToken")) and not args.get("ops")


def run_op(tool_input: dict) -> str:
	"""op раннера: на верхнем уровне вызова, не во вложенных args операции.

	Вложенные args — аргументы операции (контракт unica 0.15); op и dryRun
	живут рядом с ними. Резолв с верхнего уровня с фолбэком на вложенные,
	иначе канонический вызов классифицируется как «не мутация».
	"""
	args = tool_input.get("args") if isinstance(tool_input.get("args"), dict) else {}
	return str(tool_input.get("op") or tool_input.get("operation")
			   or args.get("op") or args.get("operation") or "")


def run_is_execution(tool_input: dict) -> bool:
	"""unica.run — исполнение, если dryRun явно не true (fail-closed)."""
	return tool_input.get("dryRun") is not True


# ── классификация мутаций (0.15) ─────────────────────────────────────────────

def is_structural_mutation(short: str, tool_input: dict, verify_ops: set) -> bool:
	"""Структурное решение (сбрасывает флаг) либо правка кода (не сбрасывает).

    unica.apply с ops вида code.* — серия правок кода; прочие ops или
    неопределимое — структурно (строже: неизвестное считаем структурным).
    Элемент ops может быть строкой ('code.replace') или объектом
    ({"op": "code.replace", …}). unica.run с op из verify_ops — загрузка/
    применение — структурно.
    """
	args = tool_input.get("args") if isinstance(tool_input.get("args"), dict) else tool_input
	if short == "apply":
		ops = args.get("ops")
		if isinstance(ops, list) and ops:
			def op_name(entry) -> str:
				if isinstance(entry, dict):
					return str(entry.get("op") or entry.get("name") or "")
				return str(entry)
			return not all(CODE_OP_RE.match(op_name(entry)) for entry in ops)
		return True
	if short == "run":
		return run_op(tool_input) in verify_ops
	return True


# ── ветки хука ───────────────────────────────────────────────────────────────

def handle_pre(event: dict) -> int:
	tool_name = str(event.get("tool_name") or "")
	tool_input = event.get("tool_input")
	if not isinstance(tool_input, dict):
		return 0
	cwd = str(event.get("cwd") or "")
	if not cwd:
		import os
		cwd = os.getcwd()
	sid = session_id(event)
	root = project_root(cwd)
	tools = unica_tools(root)
	short = unica_verb(tool_name, root)
	state = load_state(sid)

	# ── вызовы юники ──
	if short:
		docs_tools = set(tools.get("docs") or [])
		mutation_tools = set(tools.get("mutations") or [])
		verify_ops = set(tools.get("verify_ops") or VERIFY_OPS_DEFAULT)

		if short in docs_tools:
			state["pending"] = True
			save_state(sid, state)
			return 0

		is_mutation = short in mutation_tools
		# unica.run без мутабельного op (make/launch/dump-preview) — не мутация
		if is_mutation and short in set(tools.get("op_tools") or []):
			is_mutation = run_op(tool_input) in verify_ops
		if not is_mutation:
			return 0

		if state.get("verified"):
			if short == "apply" and not apply_is_publication(tool_input):
				return 0  # preview плана: сверку потратит публикация
			if short == "run" and not run_is_execution(tool_input):
				return 0  # dryRun-прогон: сверку потратит исполнение
			if is_structural_mutation(short, tool_input, verify_ops):
				state["verified"] = False  # структурное решение израсходовало сверку
				save_state(sid, state)
			return 0
		audit(RULE, f"mcp__unica__{short}", "мутация метаданных/кода без сверки с документацией платформы")
		sys.stderr.write(BLOCK_REASON + "\n")
		return 2

	# ── прямые правки исходников (эскалации unica-source-gate) ──
	if tool_name in ("Write", "Edit", "ApplyPatch"):
		path = str(tool_input.get("file_path") or tool_input.get("path") or "")
		if not path:
			return 0
		is_source = (
			(path.endswith(".bsl") or path.endswith(".xml"))
			and any(prefix in path for prefix in SOURCE_PREFIXES)
		)
		if not is_source:
			return 0
		if state.get("verified"):
			return 0
		audit(RULE, path, "правка исходников 1С без сверки с документацией платформы")
		sys.stderr.write(BLOCK_REASON + "\n")
		return 2
	return 0


def _prune_tasks(state: dict) -> None:
	"""Старые задачи не должны жить дольше сессии: чистим по возрасту."""
	tasks = state.get("docs_tasks")
	if not isinstance(tasks, dict):
		state["docs_tasks"] = {}
		return
	cutoff = time.time() - 3600
	state["docs_tasks"] = {key: ts for key, ts in tasks.items()
	                       if isinstance(ts, (int, float)) and ts >= cutoff}


def _json_or_none(text: str):
	try:
		return json.loads(text)
	except json.JSONDecodeError:
		return None


def handle_post(event: dict) -> int:
	"""PostToolUse: результат документационного вызова → флаг verified.

	0.15 умеет отвечать задачей: принятие задачи само по себе сверкой не
	считается, поэтому taskId запоминается, а флаг ставится на ответ
	unica.task.result. Успешная сверка липкая: неудачный дополнительный
	вызов её не отменяет — флаг снимает только структурная мутация.
	"""
	tool_name = str(event.get("tool_name") or "")
	cwd = str(event.get("cwd") or "")
	sid = session_id(event)
	root = project_root(cwd)
	short = unica_verb(tool_name, root)
	tools = unica_tools(root)
	state = load_state(sid)
	_prune_tasks(state)

	if short in ("task.result", "task.get"):
		payload = _json_or_none(response_text(event.get("tool_response")))
		task_id = task_id_from(payload)
		if not task_id:
			args = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
			task_id = str(args.get("taskId") or "")
		if task_id and task_id in state.get("docs_tasks", {}):
			if not (event.get("is_error") or event.get("isError")) \
					and docs_result_is_meaningful(response_text(event.get("tool_response"))):
				state["verified"] = True
				state["docs_tasks"].pop(task_id, None)
			save_state(sid, state)
		return 0

	if short not in set(tools.get("docs") or []):
		return 0
	state["pending"] = False
	if event.get("is_error") or event.get("isError"):
		save_state(sid, state)
		return 0
	text = response_text(event.get("tool_response"))
	payload = _json_or_none(text)
	if docs_task_pending(payload):
		task_id = task_id_from(payload)
		if task_id:
			state.setdefault("docs_tasks", {})[task_id] = time.time()
	else:
		state["verified"] = bool(state.get("verified")) or docs_result_is_meaningful(text)
	save_state(sid, state)
	return 0


def main() -> int:
	event = read_event()
	if not event:
		return 0
	if str(event.get("hook_event_name") or "") == "PostToolUse":
		return handle_post(event)
	return handle_pre(event)


if __name__ == "__main__":
	sys.exit(main())
