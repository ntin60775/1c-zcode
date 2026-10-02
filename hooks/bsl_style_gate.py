#!/usr/bin/env python3
"""bsl_style_gate.py — стайл-гейт изменённых *.bsl (PostToolUse, блокирующий).

Единственный исполняемый авторитет формы BSL-кода — пак 1c-bsl-code-style
(приоритет 1 в иерархии качества контура: стандарты и стат-анализ его не
переопределяют). После правки .bsl из src/|tests/ гейт прогоняет чекер пака
и БЛОКИРУЕТ правку (exit 2), если нарушения стоят В ИЗМЕНЁННЫХ СТРОКАХ:
чужой старый код не в счёт (он остаётся фоном в stderr, чинится при случае).
Правка уже применена, поэтому «блок» = принуждение агента немедленно
исправить форму и повторить правку (stderr видит модель).

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
	spans_by_target = {t: changed_lines(event, tool_name, t, root) for t in targets}
	hot, cold = split_report(report, targets, spans_by_target)
	if not hot and cold:
		audit(RULE, ", ".join(targets),
		      f"нарушения вне изменённых строк ({len(cold)}) — не блокируют", "advisory")
		sys.stderr.write(
			f"1c-zcode стайл-чек: {len(cold)} нарушений в файле вне твоей правки "
			"(старый код — не блокируют, чинить при случае):\n" + "\n".join(cold) + "\n")
		return 0

	blocks = hot or report.splitlines()
	header = [
		"БЛОК: правка нарушает стиль BSL (пак 1c-bsl-code-style — приоритетный",
		"авторитет формы; замечания стандартов/диагностики ему не противоречат).",
		"Исправь перечисленные строки и повтори правку.",
	]
	ids = sorted({CHECKER_LINE_RE.match(line.strip()).group("body").split("]")[0] + "]"
	              for line in blocks if CHECKER_LINE_RE.match(line.strip())})
	if ids:
		header.append("Машиннофиксируемые пункты: " + ", ".join(ids)
		              + " — чекер чинит их с --fix при праве на запись.")
	if cold:
		header.append(f"Вне твоей правки (не блокируют, но есть): {len(cold)} наруш.")
	audit(RULE, ", ".join(targets),
	      f"нарушения стиля в изменённых строках ({len(blocks)})",
	      "blocked" if HARD_BLOCK else "advisory")
	sys.stderr.write("\n".join(header + ["", *blocks]) + "\n")
	return 2 if HARD_BLOCK else 0


if __name__ == "__main__":
	sys.exit(main())
