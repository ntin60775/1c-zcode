#!/usr/bin/env python3
"""contour_common.py — общие механизмы гейтов контура 1С.

Контракт хука ZCode: вход — JSON на stdin, выход — код возврата
(0 проходит, 2 блокирует, прочий ненулевой — ошибка). Хуки stateless:
каждый запуск — новый процесс, состояние сессии живёт в файлах
$ZCODE_1C_STATE_DIR/state/1c/ по session_id из входа хука.

Переменная окружения ZCODE_1C_STATE_DIR перенаправляет корень состояния
(для тестов); по умолчанию — ~/.zcode.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Каталоги исходников 1С, редактируемые только через Unica.
SOURCE_PREFIXES = (
	"src/cf/",
	"src/cfe/",
	"src/epf/",
	"src/erf/",
	"tests/cfe/",
	"tests/epf/",
)

# Маркеры корня проекта 1С (вверх по дереву от cwd).
PROJECT_MARKERS = ("v8project.yaml", "v8project.local.yaml", "v8project.test.local.yaml")


# ── состояние и журнал ───────────────────────────────────────────────────────

def state_dir() -> Path:
	"""Корень состояния контура (~/.zcode или ZCODE_1C_STATE_DIR)."""
	base = os.environ.get("ZCODE_1C_STATE_DIR")
	return Path(base).expanduser() if base else Path.home() / ".zcode"


def session_state_dir(name: str) -> Path:
	"""Каталог состояния по имени подсистемы: state/1c/<name>/."""
	return state_dir() / "state" / "1c" / name


def audit(rule: str, action: str, reason: str, decision: str = "blocked") -> None:
	"""Best-effort запись в журнал решений гейтов (формат omp сохранён)."""
	try:
		log_dir = state_dir() / "logs"
		log_dir.mkdir(parents=True, exist_ok=True)
		entry = {
			"rule": rule,
			"action": action,
			"decision": decision,
			"reason": reason,
			"timestamp": datetime.now(timezone.utc).isoformat(),
		}
		with (log_dir / "rule-audit.jsonl").open("a", encoding="utf-8") as f:
			f.write(json.dumps(entry, ensure_ascii=False) + "\n")
	except OSError:
		pass


# ── разбор входа хука ────────────────────────────────────────────────────────

def read_event() -> dict:
	"""Вход хука; пустой словарь — неразборчивый stdin (пропускаем молча)."""
	try:
		event = json.loads(sys.stdin.read())
	except json.JSONDecodeError:
		return {}
	return event if isinstance(event, dict) else {}


def session_id(event: dict) -> str:
	"""Идентификатор сессии для файлового состояния; 'default' — фолбэк."""
	sid = str(event.get("session_id") or "").strip()
	return sid or "default"


# ── пути проекта ─────────────────────────────────────────────────────────────

def project_root(start: str) -> str:
	"""Вверх до каталога с маркером проекта 1С; не нашли — сам start."""
	try:
		dir_path = Path(start).resolve()
	except OSError:
		return start
	for candidate in (dir_path, *dir_path.parents):
		if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
			return str(candidate)
		if (candidate / ".zcode").is_dir() and (candidate / "src").is_dir():
			return str(candidate)
	return str(dir_path)


def normalize_source_path(file_path: str, cwd: str):
	"""[rel, корень_дерева] для исходника 1С; None — путь не исходник.

	Работает и для абсолютных путей git-ворктри, где cwd сессии — основное
	дерево: корнем считается каталог, в котором лежит src/… или tests/….
	"""
	p = Path(file_path)
	try:
		abs_path = str(p.resolve()) if p.is_absolute() else str((Path(cwd) / p).resolve())
	except OSError:
		return None
	for prefix in SOURCE_PREFIXES:
		idx = abs_path.find("/" + prefix)
		if idx >= 0:
			return abs_path[idx + 1:], abs_path[:idx]
	return None


def is_agents_doc(rel: str) -> bool:
	"""AGENTS.md внутри src/ — документация, не исходник 1С."""
	return rel == "AGENTS.md" or rel.endswith("/AGENTS.md")


# ── контурный конфиг проекта ─────────────────────────────────────────────────

def contour_config(root: str) -> dict:
	""".zcode/1c/contour.json проекта; пустой словарь — файла нет/битый."""
	path = Path(root) / ".zcode" / "1c" / "contour.json"
	try:
		data = json.loads(path.read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError):
		return {}
	return data if isinstance(data, dict) else {}


# ── имена MCP-инструментов ───────────────────────────────────────────────────

def mcp_short_name(tool_name: str, server: str) -> str:
	"""Короткое имя инструмента MCP ('code_patch') либо ''.

    Полное имя в ZCode: mcp__<server>__<tool>. Сервер юники объявлен в её
    собственном .mcp.json как 'unica' независимо от канала установки.
    """
	prefix = f"mcp__{server}__"
	return tool_name[len(prefix):] if tool_name.startswith(prefix) else ""


def unica_server_name(root: str) -> str:
	"""Имя MCP-сервера юники (переопределяется contour.json: unica.server)."""
	return str(contour_config(root).get("unica", {}).get("server") or "unica")
