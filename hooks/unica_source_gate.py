#!/usr/bin/env python3
"""unica_source_gate.py — гейт прямой правки исходников 1С (ZCode-порт unica-gate.ts).

Полноправный порт дона (1c-omp/extensions/unica-gate.ts) на поверхность
Unica 0.13 (run/apply/docs; 0.12-имена runtime_execute/code_patch retir'нуты):

1. PreToolUse Write/Edit/ApplyPatch/Bash — правка исходников
   src/{cf,cfe,epf,erf} и tests/{cfe,epf} мимо Unica MCP блокируется.
   Единственный обход — allowlist .zcode/unica-gate-escalations.txt.
2. PreToolUse mcp__unica__* — unica-вызов из неинициализированного ворктри
   блокируется; невыгодная операция на занятой базе блокируется замком
   ib_lock; правила пересборки (см. ниже) применяются по конфигу проекта.
3. PostToolUse mcp__unica__* — освобождение замка после синхронной операции
   (в ZCode нет session_shutdown: работают TTL + SessionStart-уборка).
4. Правка самого allowlist'а эскалаций журналируется (бумажный след обхода).

Инварианты пересборки — ДАННЫЕ, не код: contour.json → unica.rebuild_rules
(дефолт пуст — нейтрален). Причины: semantics 0.13 (push {force, full},
upload без apply) отличается от 0.12 (fullRebuild), и блокировать по
устаревшему правилу хуже, чем не блокировать. Проект включает правила
сверкой на живом стенде. Схема правила:
  {"tool": "run", "op": "push", "source_sets": "extensions",
   "require": {"full": true}, "message": "…"}     # require нарушен → блок
  {"tool": "run", "op": "push", "source_sets": "main",
   "forbid": {"full": true}, "message": "…"}      # forbid присутствует → блок
Селекторы source_sets: "main" | "extensions" (EXTENSION-наборы v8project.yaml)
| список имён | "any"; вызов без sourceSet считается "main" — дефолт раннера
основной набор, правила для main защищают и его.

Контракт хука ZCode: вход — JSON на stdin (hook_event_name, session_id,
tool_name, tool_input, cwd); выход — 0 проходит, 2 блокирует с причиной
в stderr. Неразборчивый вход пропускается молча.

Имена инструментов/операций — contour_common.UNICA_TOOLS_DEFAULT с override
из contour.json (сверяются живым tools/list на стенде). Состав source-set'ов
EXTENSION читается из v8project.yaml (line-парсер).

Журнал: $ZCODE_1C_STATE_DIR/logs/rule-audit.jsonl (rule: "unica-source-gate").
"""
import json
import os
import re
import sys
from pathlib import Path

from contour_common import (
	SOURCE_PREFIXES,
	audit,
	is_agents_doc,
	normalize_source_path,
	project_root,
	read_event,
	session_id,
	unica_args,
	unica_verb,
	unica_tools,
)
from ib_lock import acquire_base_lock, describe_holder, release_base_lock, resolve_infobase_connection

RULE = "unica-source-gate"

ESCALATION_FILE = ".zcode/unica-gate-escalations.txt"

SED_INPLACE_RE = re.compile(r"sed\s+(-[a-zA-Z]*i[a-zA-Z]*|--in-place)")
REDIRECT_RE = re.compile(r">>?\s*([^\s;|&]+)")


# ── маршрутизация правки (подсказки в глаголах 0.13) ────────────────────────

def suggest_unica_tool(rel: str) -> str:
	"""Подсказка: как выразить правку файла через поверхность 0.13."""
	lower = rel.lower()
	if lower.endswith(".bsl"):
		return "unica.apply (ops code.insert / code.replace)"
	if lower.endswith("configuration.xml"):
		return "unica.apply / unica.run (контракт — словарь unica.run {})"
	if "/forms/" in lower:
		return "unica.apply (форма; сверка — unica.view)"
	return "unica.apply / unica.run (контракт узла — словарь unica.run {}; сверка — unica.view)"


def block_message(rel: str, suggestion: str) -> str:
	return "\n".join([
		"Прямое редактирование исходников 1С заблокировано (unica-source-gate).",
		"",
		f"Файл: {rel}",
		f"Как выразить: {suggestion}",
		"",
		"Поверхность Unica 0.13 (глаголы, не инструменты-на-файл):",
		"  unica.view / unica.search  — чтение/поиск (без ограничений)",
		"  unica.apply                — правка кода и структуры (ops)",
		"  unica.run {op: push, …}    — сборка/загрузка; unica.check — валидация",
		"  словарь unica.run {} (вызов без op) — источник контракта",
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
	short = unica_verb(tool_name, project_root(cwd))
	if not short:
		return None
	args = unica_args(tool_input)
	call_cwd = str(args.get("cwd") or cwd)
	return short, args, call_cwd


def runner_op(args: dict) -> str:
	"""Операция раннера из аргументов: 0.13 `op`, переходно `operation`."""
	return str(args.get("op") or args.get("operation") or "")


def _rule_matches_source(rule: dict, source_set: str, extensions: set) -> bool:
	"""Селекторы: None/"any" — все; "extensions" — EXTENSION-наборы
	v8project.yaml; "main" — основной набор; список/строка — точные имена.
	Вызов без sourceSet (дефолт раннера — основной набор) считается "main":
	правила для main обязаны защищать и такой вызов, молчаливый пропуск
	хуже широкого срабатывания.
	"""
	if not source_set:
		source_set = "main"
	selector = rule.get("source_sets")
	if selector is None or selector == "any":
		return True
	if selector == "extensions":
		return source_set in extensions
	if selector == "main":
		return source_set == "main"
	if isinstance(selector, list):
		return source_set in selector
	return source_set == selector


def violated_rebuild_rule(rule: dict, short: str, args: dict, call_cwd: str):
	"""Правило нарушено → message; иначе None. Схема — см. модуль."""
	if str(rule.get("tool") or "") != short:
		return None
	if rule.get("op") and runner_op(args) != str(rule.get("op")):
		return None
	extensions = extension_source_sets(call_cwd)
	if not _rule_matches_source(rule, str(args.get("sourceSet") or ""), extensions):
		return None
	for key, value in (rule.get("require") or {}).items():
		if args.get(key) != value:
			return str(rule.get("message") or f"правило пересборки: требуется {key}={value!r}")
	for key, value in (rule.get("forbid") or {}).items():
		if args.get(key) == value:
			return str(rule.get("message") or f"правило пересборки: {key}={value!r} запрещено")
	return None


# ── входные точки ────────────────────────────────────────────────────────────

def block(text: str) -> int:
	sys.stderr.write(text + "\n")
	return 2


def handle_pre(event: dict) -> int:
	tool_name = str(event.get("tool_name") or "")
	tool_input = event.get("tool_input")
	if not isinstance(tool_input, dict):
		return 0
	cwd = str(event.get("cwd") or os.getcwd())
	sid = session_id(event)

	# ── ветка 2: вызовы юники ──
	call = unica_call(tool_name, tool_input, cwd)
	if call:
		short, args, call_cwd = call
		tools = unica_tools(project_root(cwd))

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
					"Симлинки НЕ использовать — только реальные копии.",
				]))

		# правила пересборки проекта (контур нейтрален, пока не включены)
		for rule in tools.get("rebuild_rules") or []:
			violation = violated_rebuild_rule(rule, short, args, call_cwd)
			if violation:
				audit(RULE, f"unica {runner_op(args)} {args.get('sourceSet') or ''}".strip(),
				      violation)
				return block(violation)

		# замок на инфобазу
		op = runner_op(args)
		if op and op in (tools.get("ib_mutating_ops") or []):
			connection = resolve_infobase_connection(call_cwd)
			if connection:
				status, holder = acquire_base_lock(call_cwd, connection, op, sid)
				if status == "busy":
					audit(RULE, f"unica {op}",
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
	cwd = str(event.get("cwd") or os.getcwd())
	call = unica_call(tool_name, tool_input, cwd)
	if not call:
		return 0
	short, args, call_cwd = call
	if short.startswith("task."):
		return 0  # durable-задача держит замок до конца (TTL / SessionStart)
	tools = unica_tools(project_root(cwd))
	op = runner_op(args)
	if op and op in (tools.get("ib_mutating_ops") or []):
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
