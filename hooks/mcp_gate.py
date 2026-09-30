#!/usr/bin/env python3
"""mcp_gate.py — блок изменяющих инструментов testpilot/mcp-toolkit в прод-контуре
(порт mcp-gate.ts дона 1c-omp; стек MCP_Сервер заменён на новый тест-контур).

Тест-контур даёт агенту доступ к живой базе: 1c-testpilot (UI-автоматизация,
execute-группы инструментов) и 1c-db = 1c-mcp-toolkit (запросы/код/ЖР через
.epf в сессии 1С). Читающие инструменты безопасны, а меняющие данные или
состояние сессии могут базу повредить. Поэтому:

  прод-контур   — вызов блокируется до эскалации;
  тест-контур   — вызовы свободны;
  признака нет  — считаем прод (fail-closed).

Признак контура (первое найденное, как в доне):
  - файл .zcode/contour со словом test или prod;
  - ключ `contour: test|prod` в v8project.local.yaml / v8project.yaml.

Опасные инструменты (короткие имена, override — contour.json → mcp_gate.dangerous):
  1c-testpilot: tc_execute_code, tc_execute_query, tc_execute_custom_bsl_function
  1c-db:        execute_code, submit_for_deanonymization,
                restart_1c_session, close_1c_session

Распознаются нативные вызовы MCP (mcp__<server>__<tool>) и обращения к REST
toolkit через bash (curl в /api/ или на порт 6003 с опасным токеном).

Эскалация — файл .zcode/mcp-write-allow.txt: строка = короткое имя
инструмента либо `*`. Комментарии — `#`. Каждое решение пишется в журнал.

Журнал: rule-audit.jsonl, rule: "mcp-unsafe-gate".
"""
import re
import sys
from pathlib import Path

from contour_common import (
	audit,
	contour_config,
	mcp_short_name,
	project_root,
	read_event,
)

RULE = "mcp-unsafe-gate"

DEFAULT_DANGEROUS = {
	"1c-testpilot": {
		"tc_execute_code",
		"tc_execute_query",
		"tc_execute_custom_bsl_function",
	},
	"1c-db": {
		"execute_code",
		"submit_for_deanonymization",
		"restart_1c_session",
		"close_1c_session",
	},
}

CONTOUR_KEY_RE = re.compile(r"^\s*contour\s*:\s*['\"]?([A-Za-zА-Яа-я]+)", re.M)
# REST-toolkit в bash: url/порт сервиса + опасный токен в том же вызове.
TOOLKIT_REST_RE = re.compile(r"(:6003|/api/|/mcp\b)")


# ── контур проекта ───────────────────────────────────────────────────────────

def contour_of(root: str) -> str:
	"""'test' | 'prod'; fail-closed: всё, кроме явного test — прод."""
	marker = Path(root) / ".zcode" / "contour"
	if marker.is_file():
		try:
			value = marker.read_text(encoding="utf-8").strip().lower()
			if value.startswith("test"):
				return "test"
			if value.startswith("prod"):
				return "prod"
		except OSError:
			pass
	for name in ("v8project.local.yaml", "v8project.test.local.yaml", "v8project.yaml"):
		path = Path(root) / name
		if not path.is_file():
			continue
		try:
			match = CONTOUR_KEY_RE.search(path.read_text(encoding="utf-8"))
		except OSError:
			continue
		if not match:
			continue
		value = match.group(1).lower()
		if value.startswith("test"):
			return "test"
		if value.startswith("prod"):
			return "prod"
	return "prod"


def dangerous_tools(root: str) -> dict:
	"""Опасные инструменты с учётом override из contour.json."""
	tools = {server: set(names) for server, names in DEFAULT_DANGEROUS.items()}
	override = contour_config(root).get("mcp_gate", {}).get("dangerous")
	if isinstance(override, dict):
		for server, names in override.items():
			if isinstance(names, list):
				tools[str(server)] = {str(n) for n in names}
	return tools


def escalations(root: str):
	"""Разрешённые короткие имена из .zcode/mcp-write-allow.txt."""
	path = Path(root) / ".zcode" / "mcp-write-allow.txt"
	allowed = set()
	all_tools = False
	if not path.is_file():
		return allowed, all_tools
	try:
		for raw in path.read_text(encoding="utf-8").splitlines():
			line = raw.split("#", 1)[0].strip()
			if not line:
				continue
			if line == "*":
				all_tools = True
			else:
				allowed.add(line)
	except OSError:
		pass
	return allowed, all_tools


# ── распознавание вызова ─────────────────────────────────────────────────────

def mutating_via_mcp(tool_name: str, root: str):
	"""(server, short) опасного инструмента из нативного вызова MCP, иначе None."""
	tools = dangerous_tools(root)
	for server in tools:
		short = mcp_short_name(tool_name, server)
		if short and short in tools[server]:
			return server, short
	return None


def mutating_via_bash(command: str, root: str):
	"""Опасный вызов REST-toolkit через bash: (server, short) либо None."""
	tokens = dangerous_tools(root).get("1c-db", set())
	if not tokens or not TOOLKIT_REST_RE.search(command):
		return None
	for short in tokens:
		if re.search(rf"\b{re.escape(short)}\b", command):
			return "1c-db", short
	return None


def block_message(server: str, short: str, root: str, contour: str) -> str:
	return "\n".join([
		f"Инструмент {short} (сервер {server}) меняет данные/состояние базы,",
		f"а контур проекта — {contour} (mcp-unsafe-gate).",
		"",
		f"Проект: {root}",
		"",
		"Что делать:",
		"  • если это тестовая база — объяви контур: ключ `contour: test` в",
		"    v8project.local.yaml либо файл .zcode/contour со словом test;",
		f"  • разовое разрешение — строка «{short}» в .zcode/mcp-write-allow.txt",
		"    (каждое разрешение пишется в журнал аудита);",
		"  • на проде безопаснее читать: execute_query, get_metadata,",
		"    get_event_log, find_references_to_object, get_access_rights.",
	])


# ── хук ──────────────────────────────────────────────────────────────────────

def main() -> int:
	event = read_event()
	if not event:
		return 0
	if str(event.get("hook_event_name") or "PreToolUse") != "PreToolUse":
		return 0
	tool_name = str(event.get("tool_name") or "")
	tool_input = event.get("tool_input")
	cwd = str(event.get("cwd") or "")
	if not cwd:
		import os
		cwd = os.getcwd()

	hit = None
	if tool_name == "Bash":
		if isinstance(tool_input, dict):
			hit = mutating_via_bash(str(tool_input.get("command") or ""), project_root(cwd))
	elif tool_name.startswith("mcp__"):
		hit = mutating_via_mcp(tool_name, project_root(cwd))
	if hit is None:
		return 0

	server, short = hit
	root = project_root(cwd)
	contour = contour_of(root)
	if contour == "test":
		audit(RULE, f"{short} ({root})", "тест-контур: изменяющий вызов разрешён", "allowed")
		return 0

	allowed, all_tools = escalations(root)
	if all_tools or short in allowed:
		audit(RULE, f"{short} ({root})", "эскалация из .zcode/mcp-write-allow.txt", "allowed")
		return 0

	audit(RULE, f"{short} ({root})", f"{contour}-контур: изменяющий вызов без эскалации", "blocked")
	sys.stderr.write(block_message(server, short, root, contour) + "\n")
	return 2


if __name__ == "__main__":
	sys.exit(main())
