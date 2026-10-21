#!/usr/bin/env python3
"""kd_session_start.py — будим станцию kd-rules-mcp на старте сессии (SessionStart).

MCP «1c-kd» разведён как http-запись: ZCode подключается к уже работающему
сервису и процесс сам не поднимает. Хук закрывает разрыв: в проекте с
разведённым 1c-kd пробует url из config.json; станция спит — поднимает
start_kd_rules.sh фоном. Станция общая на машину и хуком не гасится
(модель владельца «нужна только в проекте»: будится сессией проекта,
гасится руками — start_kd_rules.sh --stop).

Правила хука: всегда exit 0, готовности не ждём (start-скрипт сам
дожидается и пишет лог станции), на stdin-событие не отвечаем. Станции нет
на машине — молча выходим: мягкая зависимость, подсказку даёт doctor.
Известная граница: http-подключение MCP происходит на старте сессии, поэтому
инструменты могут появиться только со следующей сессии; канон — скилл
1c-kd-rules.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_STATION = Path.home() / ".local" / "share" / "1c-zcode" / "kd-rules-mcp"


def event_cwd() -> Path:
	"""Корень проекта из stdin-события; нет события — текущий каталог."""
	try:
		raw = sys.stdin.read() if not sys.stdin.isatty() else ""
		event = json.loads(raw) if raw.strip() else {}
	except (ValueError, OSError):
		event = {}
	cwd = event.get("cwd") if isinstance(event, dict) else None
	return Path(str(cwd)).resolve() if cwd else Path.cwd()


def kd_url(root: Path) -> str:
	"""Url из разведённого config.json; 1c-kd не разведён — пусто."""
	try:
		config = json.loads((root / ".zcode" / "config.json").read_text(encoding="utf-8"))
		return str((config.get("mcp", {}).get("servers", {}).get("1c-kd") or {}).get("url") or "")
	except (OSError, ValueError):
		return ""


def station_alive(url: str) -> bool:
	"""Любой HTTP-ответ от /mcp — станция жива (406 на голый GET — норма)."""
	try:
		urllib.request.urlopen(url, timeout=1)
		return True
	except urllib.error.HTTPError:
		return True
	except Exception:
		return False


def wake(root: Path, url: str) -> bool:
	"""Поднимаем start-скриптом из вендоренного проекта, фоном; True — запустили."""
	station = Path(os.environ.get("KD_RULES_STATION") or DEFAULT_STATION)
	if not (station / "pyproject.toml").is_file():
		return False  # станция не установлена — не наша забота (doctor подскажет)
	script = root / ".zcode" / "1c" / "scripts" / "start_kd_rules.sh"
	if not script.is_file():
		return False
	with open(os.devnull, "wb") as devnull:
		subprocess.Popen(
			["bash", str(script), "--url", url],
			stdin=subprocess.DEVNULL, stdout=devnull, stderr=devnull,
			start_new_session=True)
	return True


def main() -> int:
	try:
		root = event_cwd()
		url = kd_url(root)
		if url and not station_alive(url):
			wake(root, url)
	except Exception:
		pass  # noqa: S110 - подъём best-effort, сессию не роняем
	return 0


if __name__ == "__main__":
	sys.exit(main())
