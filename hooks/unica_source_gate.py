#!/usr/bin/env python3
"""unica_source_gate.py — гейт прямой правки исходников 1С (ZCode-порт unica-gate.ts).

Полноправный порт дона (1c-omp/extensions/unica-gate.ts), в четыре ветки:

1. PreToolUse Write/Edit/ApplyPatch/Bash — правка исходников
   src/{cf,cfe,epf,erf} и tests/{cfe,epf} мимо Unica MCP блокируется.
   Единственный обход — allowlist .zcode/unica-gate-escalations.txt.
2. PreToolUse mcp__unica__* — инварианты пересборки: расширение собирается
   только с fullRebuild:true (частичная загрузка не работает), основная
   конфигурация — только инкрементально (полная пересборка — десятки минут);
   невыгодная операция на занятой базе блокируется замком ib_lock;
   unica-вызов из неинициализированного ворктри блокируется.
3. PostToolUse mcp__unica__* — освобождение замка после синхронной операции
   (задача runtime.job держит замок до конца — дона отпускал его на
   session_shutdown, в ZCode этого события нет: работает TTL + SessionStart).
4. Правка самого allowlist'а эскалаций журналируется (бумажный след обхода).

Контракт хука ZCode: вход — JSON на stdin (hook_event_name, session_id,
tool_name, tool_input, cwd); выход — 0 проходит, 2 блокирует с причиной
в stderr. Неразборчивый вход пропускается молча.

Состав source-set'ов EXTENSION читается из v8project.yaml проекта
(line-парсер, без yaml-библиотек): у каждого проекта свой состав расширений,
и хук не должен знать имена чужих объектов.

Журнал: $ZCODE_1C_STATE_DIR/logs/rule-audit.jsonl (rule: "unica-source-gate").
"""
import json
import re
import sys
from pathlib import Path

from contour_common import (
	SOURCE_PREFIXES,
	audit,
	is_agents_doc,
	mcp_short_name,
	normalize_source_path,
	project_root,
	read_event,
	session_id,
	unica_server_name,
)
from ib_lock import acquire_base_lock, describe_holder, release_base_lock, resolve_infobase_connection

RULE = "unica-source-gate"

ESCALATION_FILE = ".zcode/unica-gate-escalations.txt"

SED_INPLACE_RE = re.compile(r"sed\s+(-[a-zA-Z]*i[a-zA-Z]*|--in-place)")
REDIRECT_RE = re.compile(r">>?\s*([^\s;|&]+)")

# Операции, которые меняют базу или снимают с неё снимок (замок ib_lock).
IB_MUTATING = {"build", "load", "update", "test", "launch", "make"}

# Пути вызова build-операций дона: unica.runtime.execute / runtime.job.start /
# unica.build.load. Короткие имена инструментов юники те же.
BUILD_TOOLS = {"runtime_execute", "runtime_job_start", "build_load"}


# ── маршрутизация правки ─────────────────────────────────────────────────────

def suggest_unica_tool(rel: str) -> str:
	"""Подсказка: какой инструмент Unica использовать для данного файла."""
	lower = rel.lower()
	if lower.endswith(".bsl"):
		return "unica.code.patch"
	if "/forms/" in lower and lower.endswith(".xml"):
		return "unica.form.edit / unica.form.compile"
	if lower.endswith("configuration.xml"):
		return "unica.cf.edit"
	if "/roles/" in lower:
		return "unica.role.edit / unica.role.compile"
	if "/datacompositionschemas/" in lower:
		return "unica.dcs.edit / unica.dcs.compile"
	if "/templates/" in lower:
		return "unica.mxl.compile / unica.mxl.decompile"
	if "/subsystems/" in lower:
		return "unica.subsystem.edit / unica.subsystem.compile"
	if "/commandinterfaces/" in lower:
		return "unica.interface.edit"
	if "/xdtopackages/" in lower:
		return "unica.xdto.edit"
	return "unica.meta.edit / unica.meta.add (или unica.cfe.borrow для расширений)"


def block_message(rel: str, suggestion: str) -> str:
	return "\n".join([
		"Прямое редактирование исходников 1С заблокировано (unica-source-gate).",
		"",
		f"Файл: {rel}",
		f"Используй: {suggestion}",
		"",
		"Маршрутизация:",
		"  .bsl модуль         -> unica.code.patch",
		"  Form.xml            -> unica.form.edit / unica.form.compile",
		"  Configuration.xml   -> unica.cf.edit",
		"  метаданные (XML)    -> unica.meta.edit / unica.meta.add",
		"  Role.xml            -> unica.role.edit / unica.role.compile",
		"  СКД Template.xml    -> unica.dcs.edit / unica.dcs.compile",
		"  MXL Template.xml    -> unica.mxl.compile",
		"  Subsystem.xml       -> unica.subsystem.edit",
		"  CommandInterface    -> unica.interface.edit",
		"  XDTO                -> unica.xdto.edit",
		"",
		"Разовая прямая правка (когда unica.* не выражает изменение) —",
		"впиши путь в .zcode/unica-gate-escalations.txt и повтори.",
		"",
		"Все вызовы Unica требуют cwd корня проекта.",
	])


def escalation_entries(target_root: str, session_cwd: str):
	"""Строки allowlist'а эскалаций для дерева target_root (пусто, если файла нет)."""
	for root in (target_root, session_cwd):
		path = Path(root) / ESCALATION_FILE
		if not path.is_file():
			continue
		try:
			lines = path.read_text(encoding="utf-8").splitlines()
		except OSError:
			return []
		return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
	return []


def paths_from_tool_input(tool_input: dict):
	"""Write/Edit/ApplyPatch: путь в file_path (в ZCode), откат на path."""
	path = tool_input.get("file_path") or tool_input.get("path") or ""
	return [str(path)] if path else []


def bash_writes_to_source(command: str, cwd: str):
	"""Цель записи в исходники через bash (sed -i / перенаправление) или None."""
	if SED_INPLACE_RE.search(command):
		for prefix in SOURCE_PREFIXES:
			if prefix in command:
				return prefix
	for m in REDIRECT_RE.finditer(command):
		norm = normalize_source_path(m.group(1), cwd)
		if norm and not is_agents_doc(norm[0]):
			return m.group(1)
	return None


# ── v8project.yaml: source-set'ы EXTENSION ──────────────────────────────────

def extension_source_sets(tree: str) -> set:
	"""Source-set'ы типа EXTENSION из v8project.yaml (line-парсер, как в доне)."""
	found = set()
	try:
		raw = (Path(tree) / "v8project.yaml").read_text(encoding="utf-8")
	except OSError:
		return found
	in_source_set = False
	current = None
	for line in raw.splitlines():
		if line[:1] not in (" ", "\t"):
			in_source_set = line.startswith("source-set:")
			current = None
			continue
		if not in_source_set:
			continue
		name = re.match(r"\s*-\s*name:\s*(.+?)\s*$", line)
		if name:
			current = name.group(1).strip().strip("'\"")
			continue
		obj_type = re.match(r"\s*type:\s*(.+?)\s*$", line)
		if obj_type and current:
			if obj_type.group(1).strip().strip("'\"").upper() == "EXTENSION":
				found.add(current)
			current = None
	return found


# ── ворктри ──────────────────────────────────────────────────────────────────

def is_worktree(directory: str) -> bool:
	"""Каталог — git-ворктри: .git является файлом, а не каталогом."""
	git_path = Path(directory) / ".git"
	return git_path.is_file()


def missing_worktree_workspace(directory: str) -> list:
	"""Отсутствующие элементы инициализации воркспейса в ворктри."""
	missing = []
	if not (Path(directory) / "v8project.local.yaml").is_file():
		missing.append("v8project.local.yaml")
	if not (Path(directory) / "build" / "tools").is_dir():
		missing.append("build/tools/")
	return missing


# ── вызовы юники ─────────────────────────────────────────────────────────────

def unica_call(tool_name: str, tool_input: dict, cwd: str):
	"""(короткое_имя, аргументы, cwd_вызова) для вызова юники; None — не юника."""
	root = project_root(cwd)
	short = mcp_short_name(tool_name, unica_server_name(root))
	if not short:
		return None
	args = tool_input if isinstance(tool_input, dict) else {}
	# Аргументы юники могут лежать в args (клиент MCP заворачивает) — разворачиваем.
	if isinstance(args.get("args"), dict):
		args = args["args"]
	call_cwd = str(args.get("cwd") or cwd)
	return short, args, call_cwd


def extension_build_without_full_rebuild(short: str, args: dict, cwd: str):
	"""Имя source-set'а, если это сборка расширения без fullRebuild; иначе None."""
	if short not in BUILD_TOOLS:
		return None
	if args.get("operation") != "build" and short != "build_load":
		return None
	source_set = str(args.get("sourceSet") or "")
	if source_set not in extension_source_sets(cwd):
		return None
	if args.get("fullRebuild") is True:
		return None
	return source_set


def main_build_with_full_rebuild(short: str, args: dict):
	"""Имя source-set'а, если это полная пересборка main; иначе None."""
	if short not in BUILD_TOOLS:
		return None
	if args.get("operation") != "build" and short != "build_load":
		return None
	if str(args.get("sourceSet") or "") != "main":
		return None
	if args.get("fullRebuild") is not True:
		return None
	return "main"


# ── входные точки ────────────────────────────────────────────────────────────

def block(text: str) -> int:
	sys.stderr.write(text + "\n")
	return 2


def handle_pre(event: dict) -> int:
	tool_name = str(event.get("tool_name") or "")
	tool_input = event.get("tool_input")
	if not isinstance(tool_input, dict):
		return 0
	cwd = str(event.get("cwd") or __import__("os").getcwd())
	sid = session_id(event)

	# ── ветка 2: вызовы юники ──
	call = unica_call(tool_name, tool_input, cwd)
	if call:
		short, args, call_cwd = call

		# ворктри: воркспейс должен быть инициализирован
		if is_worktree(call_cwd):
			missing = missing_worktree_workspace(call_cwd)
			if missing:
				audit(RULE, f"unica в ворктри {call_cwd}",
				      f"воркспейс не инициализирован: {', '.join(missing)}")
				return block("\n".join([
					"Ворктри не инициализирован для Unica (worktree-env).",
					"",
					f"Отсутствует: {', '.join(missing)}",
					"",
					"Инициализируй воркспейс скриптом:",
					"  .zcode/1c/scripts/init_worktree.sh " + call_cwd,
					"",
					"Скрипт копирует из основного дерева:",
					"  - v8project.local.yaml (креды ИБ, платформа)",
					"  - build/tools/ (артефакты инструментов)",
					"",
					f"Затем проверь: unica.project.status {{\"cwd\": \"{call_cwd}\"}}",
					"Симлинки НЕ использовать — только реальные копии.",
				]))

		# инвариант пересборки расширений
		ext_name = extension_build_without_full_rebuild(short, args, call_cwd)
		if ext_name:
			audit(RULE, f"unica build {ext_name}", "сборка расширения без fullRebuild: true")
			return block("\n".join([
				f"Сборка расширения «{ext_name}» без fullRebuild заблокирована.",
				"",
				"Частичная/инкрементальная загрузка расширений НЕ работает.",
				"Добавь \"fullRebuild\": true в аргументы вызова.",
			]))

		# запрет полной пересборки main
		main_name = main_build_with_full_rebuild(short, args)
		if main_name:
			audit(RULE, f"unica build {main_name}", "полная пересборка основной конфигурации запрещена")
			return block("\n".join([
				"Полная пересборка основной конфигурации (main) заблокирована.",
				"",
				"Основная конфигурация загружается ТОЛЬКО частично (инкрементально).",
				"Убери \"fullRebuild\": true из аргументов вызова.",
			]))

		# замок на инфобазу
		operation = str(args.get("operation") or "")
		if operation in IB_MUTATING:
			connection = resolve_infobase_connection(call_cwd)
			if connection:
				status, holder = acquire_base_lock(call_cwd, connection, operation, sid)
				if status == "busy":
					audit(RULE, f"unica {operation}",
					      f"база занята деревом {holder.get('tree') if holder else '?'}")
					return block("\n".join([
						"База занята другим деревом (worktree-env).",
						"",
						f"База:   {connection}",
						f"Держит: {describe_holder(holder) if holder else '?'}",
						"",
						"Серверная база одна на все деревья проекта, поэтому с ней",
						"одновременно работает только одно дерево. Дождись освобождения",
						"(TTL замка — 20 минут) или закончи операцию в том дереве.",
					]))
		return 0

	# ── ветка 1: правка исходников / bash ──
	if tool_name == "Bash":
		command = str(tool_input.get("command") or "")
		if not command:
			return 0
		hit = bash_writes_to_source(command, cwd)
		if hit:
			audit(RULE, f"bash -> {hit}", "запись в исходники 1С через bash")
			return block("\n".join([
				"Запись в исходники 1С через bash заблокирована (unica-source-gate).",
				f"Цель: {hit}",
				"Используй Unica MCP (маршрутизация — в навыке 1c-contour).",
			]))
		return 0

	if tool_name not in ("Write", "Edit", "ApplyPatch"):
		return 0

	for file_path in paths_from_tool_input(tool_input):
		# правка allowlist'а эскалаций — бумажный след появления обхода
		if file_path.endswith(ESCALATION_FILE.split("/", 1)[1]) and ESCALATION_FILE in file_path:
			audit(RULE, f"{tool_name.lower()} {ESCALATION_FILE}",
			      "изменён allowlist эскалаций — последующие прямые правки исходников будут им разрешены",
			      "confirmed")
			continue
		norm = normalize_source_path(file_path, cwd)
		if norm is None:
			continue
		rel, root = norm
		if is_agents_doc(rel):
			continue
		if rel in escalation_entries(root, cwd):
			audit(RULE, f"{tool_name.lower()} {rel} (дерево {root})",
			      f"эскалация: разрешена прямой правкой ({ESCALATION_FILE})", "confirmed")
			continue
		suggestion = suggest_unica_tool(rel)
		audit(RULE, f"{tool_name.lower()} {rel}", f"требуется {suggestion}")
		return block(block_message(rel, suggestion))
	return 0


def handle_post(event: dict) -> int:
	"""PostToolUse: синхронная операция юники закончилась — замок отпускается."""
	tool_name = str(event.get("tool_name") or "")
	tool_input = event.get("tool_input")
	if not isinstance(tool_input, dict):
		return 0
	cwd = str(event.get("cwd") or __import__("os").getcwd())
	call = unica_call(tool_name, tool_input, cwd)
	if not call:
		return 0
	short, args, call_cwd = call
	if short == "runtime_job_start":
		return 0  # задача держит замок до конца (TTL / SessionStart выметет)
	operation = str(args.get("operation") or "")
	if operation not in IB_MUTATING:
		return 0
	connection = resolve_infobase_connection(call_cwd)
	if connection:
		release_base_lock(call_cwd, connection, session_id(event))
	return 0


def main() -> int:
	event = read_event()
	if not event:
		return 0
	event_name = str(event.get("hook_event_name") or "")
	if event_name == "PostToolUse":
		return handle_post(event)
	return handle_pre(event)


if __name__ == "__main__":
	sys.exit(main())
