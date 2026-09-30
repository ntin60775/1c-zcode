#!/usr/bin/env python3
"""bsl_style_gate.py — стайл-чек изменённых *.bsl (PostToolUse).

Единственный исполняемый авторитет формы BSL-кода — пак 1c-bsl-code-style
(приоритет 1 в иерархии качества контура: стандарты и стат-анализ его не
переопределяют). После правки .bsl из src/|tests/ гейт прогоняет чекер пака
по изменённому файлу и отдаёт отчёт моделью в stderr для самофикса.

Резолв чекера (первый найденный):
  1. env 1C_STYLE_ENGINE — исполняемый движок с контрактом
     `check <files> [--scenarios <dir>] [--fix]`, exit 0/1 (будущий Go-движок);
  2. bsl-style-engine в PATH — тот же контракт;
  3. <корень>/.zcode/style/scripts/bsl_style_check.py — вендоренный пак
     (`check` не принимает, вызывается как `<скрипт> <files>`, exit 0/1).
Не найден — тихий пропуск (наличие подсветит /1c-doctor).

Вердикт гейта — advisory (exit 0 + отчёт в stderr): правка уже применена,
гейт требует доработки, а не откатывает. HARD_BLOCK оставлен точкой роста —
семантика exit 2 у PostToolUse выясняется на живом стенде.

Журнал: rule-audit.jsonl, rule: "bsl-style-gate".
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from contour_common import (
	SOURCE_PREFIXES,
	audit,
	normalize_source_path,
	project_root,
	read_event,
)

RULE = "bsl-style-gate"
CHECKER_TIMEOUT_S = 30
HARD_BLOCK = False  # точка роста: перевести в True после проверки exit 2 у PostToolUse

ENGINE_ENV = "1C_STYLE_ENGINE"
ENGINE_NAME = "bsl-style-engine"
PACK_CHECKER = ".zcode/style/scripts/bsl_style_check.py"

APPLY_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Update|Add) File: (.+)$", re.M)


# ── резолв чекера ────────────────────────────────────────────────────────────

def resolve_checker(root: str):
	"""(argv_префикс, контракт_check) либо None — чекер не найден."""
	env_exe = os.environ.get(ENGINE_ENV)
	if env_exe and Path(env_exe).exists():
		return [env_exe, "check"], True
	which = shutil.which(ENGINE_NAME)
	if which:
		return [which, "check"], True
	pack = Path(root) / PACK_CHECKER
	if pack.is_file():
		return [sys.executable, str(pack)], False
	return None


# ── кандидаты на проверку ────────────────────────────────────────────────────

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


# ── хук ──────────────────────────────────────────────────────────────────────

def main() -> int:
	event = read_event()
	if not event:
		return 0
	tool_name = str(event.get("tool_name") or "")
	if tool_name not in ("Write", "Edit", "ApplyPatch"):
		return 0
	cwd = str(event.get("cwd") or os.getcwd())
	root = project_root(cwd)
	targets = bsl_targets(event, cwd)
	if not targets:
		return 0

	resolved = resolve_checker(root)
	if resolved is None:
		return 0  # чекер не установлен — advisory-пропуск (см. /1c-doctor)
	argv_prefix, check_contract = resolved

	args = argv_prefix + (["check"] if check_contract else []) + targets
	try:
		done = subprocess.run(
			args, capture_output=True, text=True, timeout=CHECKER_TIMEOUT_S, cwd=root)
	except (subprocess.TimeoutExpired, OSError):
		return 0  # чекер упал — правку не роняем
	if done.returncode not in (0, 1):
		return 0  # краш чекера — не сигнал о нарушениях

	if done.returncode == 0:
		return 0

	report = (done.stdout or done.stderr or "").strip() or "bsl_style_check: нарушения найдены"
	audit(RULE, ", ".join(targets), "найдены нарушения стиля 1c-bsl-code-style", "blocked")
	sys.stderr.write("\n".join([
		"Нарушения стиля BSL (1c-bsl-code-style — приоритетный авторитет формы кода).",
		"Замечания стандартов/диагностики, противоречащие паку, не применяются.",
		"",
		report,
		"",
		"Исправь форму кода по отчёту (правки исходников 1С — через Unica MCP;",
		"машинофиксуемые проблемы чекер умеет чинить с --fix при праве на запись).",
	]) + "\n")
	return 2 if HARD_BLOCK else 0


if __name__ == "__main__":
	sys.exit(main())
