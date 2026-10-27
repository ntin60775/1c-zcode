#!/usr/bin/env python3
"""Тесты unica_ping.py: выбор установки и контракт handshake без запуска MCP.

Проверяем то, что ломалось при смене канала: ping обязан брать installPath
из installed_plugins.json, а не свежий по mtime кэш, и рантаймы — из
data/<id> той же установки. Запуск: python3 tests/test_unica_ping.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import unica_ping  # noqa: E402


def make_install(home: Path, channel: str, version: str) -> Path:
	install = home / ".zcode" / "cli" / "plugins" / "cache" / channel / "unica" / version
	bootstrap = install / "bootstrap" / "bin" / "linux-x64" / "unica-bootstrap"
	bootstrap.parent.mkdir(parents=True)
	bootstrap.write_text("#!/bin/sh\n", encoding="utf-8")
	bootstrap.chmod(0o755)
	return install


def write_registry(home: Path, entries: list) -> None:
	plugins = home / ".zcode" / "cli" / "plugins"
	plugins.mkdir(parents=True, exist_ok=True)
	(plugins / "installed_plugins.json").write_text(
		json.dumps({"version": 1, "plugins": entries}), encoding="utf-8")


def main() -> int:
	old_home = os.environ.get("HOME")
	with tempfile.TemporaryDirectory() as tmp:
		home = Path(tmp)
		os.environ["HOME"] = str(home)
		try:
			old = make_install(home, "unica", "0.13.0")
			new = make_install(home, "unica-next", "0.15.0-rc.1")
			old.touch()
			new.touch()
			os.utime(old, (time.time() - 10, time.time() - 10))
			os.utime(new, (time.time(), time.time()))

			write_registry(home, [
				{"id": "unica@unica", "name": "unica", "version": "0.13.0",
				 "installPath": str(old)},
				{"id": "unica@unica-next", "name": "unica", "version": "0.15.0-rc.1",
				 "installPath": str(new)},
			])
			check("два канала: installed_unica не угадывает",
			      unica_ping.installed_unica() is None, "выбрала одну из двух")
			check("фолбэк кэша — свежий по mtime", unica_ping.latest_plugin_cache() == new,
			      str(unica_ping.latest_plugin_cache()))

			write_registry(home, [
				{"id": "unica@unica-next", "name": "unica",
				 "version": "0.15.0-rc.1", "installPath": str(new)},
			])
			entry = unica_ping.installed_unica()
			check("один канал: установка из реестра", entry and entry["id"] == "unica@unica-next",
			      str(entry))
			check("кэш берётся по installPath",
			      unica_ping.latest_plugin_cache(entry) == new,
			      str(unica_ping.latest_plugin_cache(entry)))
			runtimes = unica_ping.runtime_cache_dir(entry)
			check("рантаймы — data/<id> установки",
			      runtimes.endswith("/data/unica@unica-next/runtimes"), runtimes)

			check("handshake 0.15: протокол 2025-11-25",
			      "2025-11-25" in unica_ping.HANDSHAKE, unica_ping.HANDSHAKE)
			check("handshake 0.15: старый протокол не используется",
			      "2024-11-05" not in unica_ping.HANDSHAKE, unica_ping.HANDSHAKE)
			check("обязательные глаголы включают resolve/diff",
			      {"resolve", "diff"} <= unica_ping.REQUIRED_VERBS,
			      str(sorted(unica_ping.REQUIRED_VERBS)))

			version, verbs = unica_ping.parse_tools("\n".join([
				json.dumps({"id": 1, "result": {"serverInfo": {"version": "0.15.0-rc.1"}}}),
				json.dumps({"id": 2, "result": {"tools": [
					{"name": "unica.view"}, {"name": "unica.run"},
					{"name": "unica.task.result"}]}}),
			]))
			check("parse_tools: версия", version == "0.15.0-rc.1", version)
			check("parse_tools: глаголы с точками", {"view", "run", "task.result"} <= verbs,
			      str(sorted(verbs)))
		finally:
			if old_home is None:
				os.environ.pop("HOME", None)
			else:
				os.environ["HOME"] = old_home
	return summary()


if __name__ == "__main__":
	sys.exit(main())