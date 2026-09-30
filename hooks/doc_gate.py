#!/usr/bin/env python3
"""doc_gate.py — «сначала документация платформы, потом решение» (docs-before-design).

Порт СТРОГОГО проектного гейта боевого проекта (.omp/extensions/docs-gate.ts),
взят как канон вместо более либерального плагинного doc-gate дона 1c-omp:
плагинный засчитывал сверкой любое чтение исходников и любой read-only вызов
юники, и сессия могла спроектировать решение, ни разу не открыв документацию.
Исходники говорят, КАК сделано, но не говорят, ЧТО допустимо платформой.

Что делает гейт. Перед мутацией 1С-исходников (unica.code.patch, meta.*,
form.*, build/load и др.) требует, чтобы в ЭТОЙ сессии был НЕПУСТОЙ вызов
документации или стандартов юники:
  unica.documentation.search / unica.documentation.get
  unica.standards.search / unica.standards.explain
Пустой результат (нет hits, status unavailable) сверкой не считается.
После мутации МЕТАДАННЫХ флаг сбрасывается: следующее структурное решение
(новый объект, схема регистра, роль, подсистема) снова требует свежей
сверки. Серия правок кода (.bsl) флаг не сбрасывает. Прямые правки
исходников (эскалации unica-source-gate) тоже требуют сверки.

Состояние (хуки stateless): state/1c/doc-gate/<session_id>.json —
флаги verified/pending. PostToolUse проставляет verified по непустому
результату документационного вызова.

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
)

RULE = "docs-before-design"

DOCS_TOOLS = {
	"documentation_search",
	"documentation_get",
	"standards_search",
	"standards_explain",
}

# Мутации метаданных: структурное решение — флаг сверки сбрасывается.
METADATA_MUTATIONS = {
	"meta_add", "meta_edit", "meta_remove",
	"cf_edit", "cfe_borrow", "cfe_patch_method",
	"role_compile", "role_edit",
	"subsystem_compile", "subsystem_edit",
	"interface_edit",
	"form_add", "form_compile", "form_edit", "form_remove",
	"dcs_compile", "dcs_edit",
	"mxl_compile",
	"xdto_edit",
	"template_add", "template_remove",
	"help_add", "support_edit",
}

# Мутации кода: сверка нужна, но серию правок не разрывает.
CODE_MUTATIONS = {"code_patch"}

# Runtime-операции с применением (не dryRun) — как в доне: dump/make/convert
# не пишут угаданные имена в исходники, не блокируются.
GATED_RUNTIME_OPS = {"build", "load"}

BLOCK_REASON = "\n".join([
	"Мутация 1С-исходников без сверки с документацией платформы (docs-before-design).",
	"",
	"Память — не источник: у памяти нет версии, а у платформы есть. Перед решением",
	"о механизме сверься с документацией (один вызов, недорого):",
	"",
	"  - unica.documentation.search — справка платформы, руководство разработчика,",
	"    справка конфигурации (вендор + 1ci KB + v8std в одном ответе);",
	"  - unica.documentation.get — полный текст найденного документа по documentId;",
	"  - unica.standards.search / explain — стандарты разработки.",
	"",
	"Сверка исходниками (read/grep, meta.info, code.search) отвечает на вопрос",
	"«как сделано здесь», но не на вопрос «что вообще допустимо платформой».",
	"Пустой результат поиска сверкой не считается.",
	"",
	"После мутации метаданных флаг сбрасывается: следующее структурное решение",
	"снова требует свежей сверки.",
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
	"""Непустой результат сверки: есть hits/документы, нет признаков пустоты."""
	if not text.strip():
		return False
	empty = (
		re.search(r'"hits"\s*:\s*\[\s*\]', text)
		or re.search(r'"status"\s*:\s*"unavailable"', text)
	)
	if empty:
		return False
	return '"hits"' in text or '"document"' in text or '"standards"' in text


# ── ветки хука ───────────────────────────────────────────────────────────────

def unica_short(tool_name: str, cwd: str) -> str:
	return mcp_short_name(tool_name, unica_server_name(project_root(cwd)))


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
	short = unica_short(tool_name, cwd)
	state = load_state(sid)

	# ── вызовы юники ──
	if short:
		if short in DOCS_TOOLS:
			state["pending"] = True
			save_state(sid, state)
			return 0
		if short in METADATA_MUTATIONS or short in CODE_MUTATIONS:
			if state.get("verified"):
				if short in METADATA_MUTATIONS:
					# структурное решение израсходовало сверку
					state["verified"] = False
					save_state(sid, state)
				return 0
			audit(RULE, f"mcp__unica__{short}", "мутация метаданных/кода без сверки с документацией платформы")
			sys.stderr.write(BLOCK_REASON + "\n")
			return 2
		# runtime build/load (applied, не dryRun) — тоже мутация
		if short in ("runtime_execute", "build_load"):
			args = tool_input.get("args") if isinstance(tool_input.get("args"), dict) else tool_input
			op = str(args.get("operation") or "")
			is_gated = op in GATED_RUNTIME_OPS or short == "build_load"
			if is_gated and args.get("dryRun") is not False:
				is_gated = False
			if is_gated and not state.get("verified"):
				audit(RULE, f"mcp__unica__{short} ({op})", "runtime build/load без сверки с документацией платформы")
				sys.stderr.write(BLOCK_REASON + "\n")
				return 2
		return 0

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
	short = unica_short(tool_name, cwd)
	if short not in DOCS_TOOLS:
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
