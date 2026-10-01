#!/usr/bin/env python3
"""doc_gate.py — «сначала документация платформы, потом решение» (docs-before-design).

Порт СТРОГОГО проектного гейта боевого проекта (docs-gate.ts дона 1c-omp),
взят как канон вместо более либерального плагинного doc-gate дона:
плагинный засчитывал сверкой любое чтение исходников и любой read-only вызов
юники, и сессия могла спроектировать решение, ни разу не открыв документацию.
Исходники говорят, КАК сделано, но не говорят, ЧТО допустимо платформой.

Поверхность Unica 0.13: сверка — unica.docs / unica.search (0.12-имена
documentation_search/standards_search retir'нуты); мутации — unica.apply
(код/структура) и unica.run {op: push|upload|apply|reset}. Наборы имён —
contour_common.UNICA_TOOLS_DEFAULT с override из contour.json.

Что делает гейт. Перед мутацией требует, чтобы в ЭТОЙ сессии был НЕПУСТОЙ
документационный вызов (пустой ответ/ошибка сверкой не считаются).
Структурная мутация сбрасывает флаг: следующее структурное решение снова
требует свежей сверки; серия правок КОДА (unica.apply с ops вида code.*)
флаг не сбрасывает. Прямые правки исходников (эскалации unica-source-gate)
тоже требуют сверки.

Состояние (хуки stateless): state/1c/doc-gate/<session_id>.json.
PostToolUse документационного вызова проставляет verified по результату.

Журнал: rule-audit.jsonl, rule: "docs-before-design".
"""
import json
import re
import sys
from pathlib import Path

from contour_common import (
	SOURCE_PREFIXES,
	audit,
	mcp_short_name,
	project_root,
	read_event,
	session_id,
	session_state_dir,
	unica_server_name,
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
	"  - unica.docs {source: platform-help | development-standard |",
	"    configuration-documentation} — справка платформы, стандарты, документация",
	"    конфигурации (замена 0.12 documentation.search / standards.search);",
	"  - unica.search — поиск по конфигурации как дополнение.",
	"",
	"Сверка исходниками (unica.view, grep по src/) отвечает на вопрос «как",
	"сделано здесь», но не «что допустимо платформой». Пустой ответ сверкой",
	"не считается. После структурной мутации флаг сбрасывается: следующее",
	"структурное решение — снова свежая сверка.",
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


def docs_result_is_meaningful(text: str) -> bool:
	"""Непустой результат сверки: есть содержимое, нет признаков пустоты."""
	if not text.strip():
		return False
	if re.search(r'"hits"\s*:\s*\[\s*\]', text):
		return False
	if re.search(r'"status"\s*:\s*"unavailable"', text):
		return False
	return True


# ── классификация мутаций (0.13) ─────────────────────────────────────────────

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
		op = str(args.get("op") or args.get("operation") or "")
		return op in verify_ops
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
	short = mcp_short_name(tool_name, unica_server_name(root))
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
			args = tool_input.get("args") if isinstance(tool_input.get("args"), dict) else tool_input
			op = str(args.get("op") or args.get("operation") or "")
			is_mutation = op in verify_ops
		if not is_mutation:
			return 0

		if state.get("verified"):
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


def handle_post(event: dict) -> int:
	"""PostToolUse: результат документационного вызова → флаг verified."""
	tool_name = str(event.get("tool_name") or "")
	cwd = str(event.get("cwd") or "")
	sid = session_id(event)
	root = project_root(cwd)
	short = mcp_short_name(tool_name, unica_server_name(root))
	if short not in set(unica_tools(root).get("docs") or []):
		return 0
	state = load_state(sid)
	state["pending"] = False
	if event.get("is_error") or event.get("isError"):
		state["verified"] = False
	else:
		text = response_text(event.get("tool_response"))
		state["verified"] = docs_result_is_meaningful(text)
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
