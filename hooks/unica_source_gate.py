#!/usr/bin/env python3
"""unica_source_gate.py — гейт прямой правки исходников 1С (ZCode-порт unica-gate.ts).

Перехватывает PreToolUse для Write/Edit/ApplyPatch/Bash: правка исходников
src/{cf,cfe,epf,erf} и tests/{cfe,epf} мимо Unica MCP блокируется — exit 2,
причина в stderr. Единственный обход — allowlist .zcode/unica-gate-escalations.txt
(одна строка = путь исходника относительно корня дерева; файл ищется в целевом
дереве с откатом на cwd сессии, чтобы запись своего дерева не разрешала правку
в чужом). Каждое блокирование и разрешение пишется в журнал.

Контракт хука ZCode: вход — JSON на stdin (tool_name, tool_input, cwd);
выход — код возврата: 0 проходит, 2 блокирует, прочий ненулевой — ошибка.
Неразборчивый вход пропускается молча: сломанный хук не должен ронять сессию.

Журнал: $ZCODE_1C_STATE_DIR/logs/rule-audit.jsonl (по умолчанию ~/.zcode/logs/
rule-audit.jsonl), rule: "unica-source-gate". Переменная окружения — для тестов.

Скелет: из unica-gate.ts перенесена только маршрутизация правки исходников
(edit/write/bash). Инвариант пересборки, замок на инфобазу и проверка ворктри —
не перенесены, статус в docs/migration-map.md.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

RULE = "unica-source-gate"

# Каталоги исходников 1С, редактируемые только через Unica.
SOURCE_PREFIXES = (
	"src/cf/",
	"src/cfe/",
	"src/epf/",
	"src/erf/",
	"tests/cfe/",
	"tests/epf/",
)

ESCALATION_FILE = ".zcode/unica-gate-escalations.txt"

SED_INPLACE_RE = re.compile(r"sed\s+(-[a-zA-Z]*i[a-zA-Z]*|--in-place)")
REDIRECT_RE = re.compile(r">>?\s*([^\s;|&]+)")


# ── журнал ───────────────────────────────────────────────────────────────────

def state_dir() -> Path:
	base = os.environ.get("ZCODE_1C_STATE_DIR")
	return Path(base).expanduser() if base else Path.home() / ".zcode"


def audit(action: str, reason: str, decision: str = "blocked") -> None:
	"""Best-effort: сбой записи в журнал работу сессии не ломает."""
	try:
		log_dir = state_dir() / "logs"
		log_dir.mkdir(parents=True, exist_ok=True)
		entry = {
			"rule": RULE,
			"action": action,
			"decision": decision,
			"reason": reason,
			"timestamp": datetime.now(timezone.utc).isoformat(),
		}
		with (log_dir / "rule-audit.jsonl").open("a", encoding="utf-8") as f:
			f.write(json.dumps(entry, ensure_ascii=False) + "\n")
	except OSError:
		pass


# ── маршрутизация ────────────────────────────────────────────────────────────

def normalize_source_path(file_path: str, cwd: str):
	"""[rel, корень_дерева] для исходника 1С; None — путь не исходник.

	Работает и для абсолютных путей git-ворктри, где cwd сессии — основное
	дерево: корнем считается каталог, в котором лежит src/… или tests/….
	"""
	p = Path(file_path)
	abs_path = str(p.resolve()) if p.is_absolute() else str((Path(cwd) / p).resolve())
	for prefix in SOURCE_PREFIXES:
		idx = abs_path.find("/" + prefix)
		if idx >= 0:
			return abs_path[idx + 1:], abs_path[:idx]
	return None


def is_agents_doc(rel: str) -> bool:
	"""AGENTS.md внутри src/ — документация, не исходник 1С."""
	return rel == "AGENTS.md" or rel.endswith("/AGENTS.md")


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


# ── эскалации: allowlist прямой правки ───────────────────────────────────────

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


# ── извлечение путей из tool_input ───────────────────────────────────────────

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


# ── входная точка хука ───────────────────────────────────────────────────────

def main() -> int:
	try:
		event = json.loads(sys.stdin.read())
	except json.JSONDecodeError:
		return 0
	if not isinstance(event, dict):
		return 0

	tool_name = str(event.get("tool_name") or "")
	tool_input = event.get("tool_input")
	if not isinstance(tool_input, dict):
		return 0
	cwd = str(event.get("cwd") or os.getcwd())

	if tool_name == "Bash":
		command = str(tool_input.get("command") or "")
		if not command:
			return 0
		hit = bash_writes_to_source(command, cwd)
		if hit:
			audit(f"bash -> {hit}", "запись в исходники 1С через bash")
			sys.stderr.write(
				"Запись в исходники 1С через bash заблокирована (unica-source-gate).\n"
				f"Цель: {hit}\n"
				"Используй Unica MCP (маршрутизация — в навыке 1c-contour).\n"
			)
			return 2
		return 0

	if tool_name not in ("Write", "Edit", "ApplyPatch"):
		return 0

	for file_path in paths_from_tool_input(tool_input):
		norm = normalize_source_path(file_path, cwd)
		if norm is None:
			continue
		rel, root = norm
		if is_agents_doc(rel):
			continue
		if rel in escalation_entries(root, cwd):
			audit(
				f"{tool_name.lower()} {rel} (дерево {root})",
				f"эскалация: разрешена прямой правкой ({ESCALATION_FILE})",
				"confirmed",
			)
			continue
		suggestion = suggest_unica_tool(rel)
		audit(f"{tool_name.lower()} {rel}", f"требуется {suggestion}")
		sys.stderr.write(block_message(rel, suggestion) + "\n")
		return 2
	return 0


if __name__ == "__main__":
	sys.exit(main())
