#!/usr/bin/env python3
"""unica_ping.py — живая проверка MCP-сервера юники без хоста и без 1С.

Делает ровно то, что делает ZCode при старте сервера: находит кэш плагина
(unica*), подставляет CLAUDE_PLUGIN_ROOT/UNICA_RUNTIME_CACHE_DIR, запускает
unica-bootstrap и прогоняет MCP-handshake (initialize → initialized →
tools/list). На stdin EOF сервер корректно завершается — это ожидаемо.

Проверяет:
  - сервер отвечает (иначе «MCP process failed to start», как его видит хост);
  - serverInfo.version и набор глаголов tools/list;
  - соответствие ожидаемым глаголам контура (UNICA_TOOLS_DEFAULT → ключи
    docs/mutations/op_tools; сверка по глаголам с отрезанным 'unica.').

Exit: 0 — жив и совместим; 1 — не стартует/не совместим; 2 — таймаут
(первый старт качает рантаймы — минуты; повтори позже).

Запуск: python3 unica_ping.py [--timeout S] [--json]
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

for _rel in ("../../hooks", "../hooks"):
	_p = (Path(__file__).resolve().parent / _rel).resolve()
	if (_p / "contour_common.py").is_file():
		sys.path.insert(0, str(_p))

HANDSHAKE = (
	'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"1c-zcode-doctor","version":"0"}}}\n'
	'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
	'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
)

# Глаголы, без которых контур неработоспособен (по UNICA_TOOLS_DEFAULT).
REQUIRED_VERBS = {"run", "apply", "docs", "view", "check", "search"}


def latest_plugin_cache() -> Path | None:
	"""Свежий кэш плагина unica* с bootstrap'ом; None — не найден.

	Кэш: cache/<маркетплейс>/<плагин>/<версия>/ — берём каталоги, где лежит
	bootstrap (отсев маркетплейс-уровня), свежий по mtime.
	"""
	base = Path.home() / ".zcode" / "cli" / "plugins" / "cache"
	candidates = [
		p for p in base.glob("unica*/*/*")
		if (p / "bootstrap" / "bin" / "linux-x64" / "unica-bootstrap").is_file()
	]
	if not candidates:
		return None
	return max(candidates, key=lambda p: p.stat().st_mtime)


def parse_tools(text: str):
	"""(server_version, verbs) из stdout handshake; ('', set()) если ответа нет."""
	version, verbs = "", set()
	for line in text.splitlines():
		try:
			msg = json.loads(line)
		except json.JSONDecodeError:
			continue
		if msg.get("id") == 1 and "result" in msg:
			version = str((msg["result"].get("serverInfo") or {}).get("version") or "")
		if msg.get("id") == 2 and "result" in msg:
			for tool in msg["result"].get("tools", []):
				name = str(tool.get("name") or "")
				if name.startswith("unica."):
					name = name[len("unica."):]
				verbs.add(name)
	return version, verbs


def ping(timeout_s: int):
	"""(код, сообщение, verbs) — код: 0 ок, 1 упал, 2 таймаут."""
	cache = latest_plugin_cache()
	if cache is None:
		return 1, "кэш плагина unica не найден (unica не установлена?)", set()
	bootstrap = cache / "bootstrap" / "bin" / "linux-x64" / "unica-bootstrap"
	if not bootstrap.is_file():
		return 1, f"bootstrap не найден: {bootstrap}", set()
	if not os.access(str(bootstrap), os.X_OK):
		return 1, (f"нет exec-бита: {bootstrap} — распаковщик ZCode теряет права; "
		           f"лечение: chmod +x '{cache}/bootstrap/bin/'*/*/unica-bootstrap*"), set()
	data_dir = Path.home() / ".zcode" / "cli" / "plugins" / "data"
	cache_dirs = sorted(data_dir.glob("unica@*/runtimes"))
	env = dict(
		os.environ,
		CLAUDE_PLUGIN_ROOT=str(cache),
		UNICA_HOST_CONTEXT_REQUIRED="1",
		UNICA_RUNTIME_CACHE_DIR=str(cache_dirs[0]) if cache_dirs
		else str(Path(tempfile.gettempdir()) / "unica-ping-runtimes"),
	)
	try:
		done = subprocess.run(
			[str(bootstrap), "run", "--plugin-root", str(cache)],
			input=HANDSHAKE, capture_output=True, text=True,
			timeout=timeout_s, env=env)
	except subprocess.TimeoutExpired:
		return 2, ("таймаут: вероятно, первый старт качает рантаймы (минуты); "
		           "повтори позже или проверь сеть до github.com"), set()
	if done.returncode != 0 and not done.stdout.strip():
		return 1, (f"bootstrap exit {done.returncode}: "
		           f"{(done.stderr or '').strip()[:200] or 'без вывода'}"), set()
	version, verbs = parse_tools(done.stdout)
	if not verbs:
		return 1, (f"сервер не ответил на tools/list (exit {done.returncode}): "
		           f"{(done.stderr or '').strip()[:200]}"), set()
	missing = REQUIRED_VERBS - verbs
	if missing:
		return 1, f"сервер {version or '?'} без обязательных глаголов: {sorted(missing)}", verbs
	return 0, f"unica {version or '?'} жив: {len(verbs)} глаголов", verbs


def main() -> int:
	timeout_s = 120
	args = [a for a in sys.argv[1:] if not a.startswith("--")]
	for a in sys.argv[1:]:
		if a.startswith("--timeout="):
			timeout_s = int(a.split("=", 1)[1])
	as_json = "--json" in sys.argv
	code, message, verbs = ping(timeout_s)
	if as_json:
		print(json.dumps({"code": code, "message": message,
		                  "verbs": sorted(verbs)}, ensure_ascii=False))
	else:
		print(message)
	return code


if __name__ == "__main__":
	sys.exit(main())
