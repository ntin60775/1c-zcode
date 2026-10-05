#!/usr/bin/env python3
"""Тесты wire_config.py: идемпотентная разводка hooks+MCP в .zcode/config.json.

Запуск: python3 tests/test_wire_config.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
WIRE = REPO / "scripts" / "wire_config.py"


def run_wire(root: Path, *flags):
	return subprocess.run(
		[sys.executable, str(WIRE), str(root), *flags],
		capture_output=True, text=True, cwd=str(root),
		env=dict(os.environ, ZCODE_1C_STATE_DIR=str(root / "state")))


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		root = Path(tmp)

		# ── вендоренная раскладка: hooks + фрагменты в .zcode/… ──
		vendored_hooks = root / ".zcode" / "hooks"
		vendored_hooks.mkdir(parents=True)
		(vendored_hooks / "hooks.json").write_text(json.dumps({
			"hooks": {"PreToolUse": [{"matcher": "^Bash$", "hooks": [{
				"type": "process", "command": "python3",
				"args": ["${CLAUDE_PLUGIN_ROOT}/hooks/retry_gate.py"],
				"timeoutMs": 5000}]}]}
		}), encoding="utf-8")
		vendored_scripts = root / ".zcode" / "1c" / "scripts"
		vendored_scripts.mkdir(parents=True)
		(vendored_scripts / "mcp_fragments.json").write_text(
			(REPO / "scripts" / "mcp_fragments.json").read_text(encoding="utf-8"),
			encoding="utf-8")

		# wire_config в тесте берёт плагин-корень от себя (репо), а не от цели —
		# эмулируем вендоренный запуск копированием скрипта в раскладку цели
		vendored_wire = vendored_scripts / "wire_config.py"
		vendored_wire.write_text(WIRE.read_text(encoding="utf-8"), encoding="utf-8")

		def run_vendored(*flags):
			return subprocess.run(
				[sys.executable, str(vendored_wire), str(root), *flags],
				capture_output=True, text=True, cwd=str(root))

		r = run_vendored()
		check("wire выходит 0", r.returncode == 0, r.stderr[:200])
		config = json.loads((root / ".zcode" / "config.json").read_text(encoding="utf-8"))

		check("hooks.enabled выставлен", config.get("hooks", {}).get("enabled") is True, str(config.get("hooks", {}).keys()))
		check("хуки в hooks.events (workspace-схема)",
		      isinstance(config.get("hooks", {}).get("events"), dict)
		      and "PreToolUse" in config["hooks"]["events"]
		      and "PreToolUse" not in {k: v for k, v in config["hooks"].items() if k not in ("enabled", "events", "timeoutMs", "maxOutputBytes")},
		      str(config.get("hooks", {}).keys()))
		pre = config["hooks"]["events"]["PreToolUse"]
		args = [a for entry in pre for h in entry["hooks"] for a in h["args"]]
		check("путь хука переписан на .zcode/hooks/", any(".zcode/hooks/retry_gate.py" in str(a) for a in args), str(args))
		check("макрос PLUGIN_ROOT не остался", not any("PLUGIN_ROOT" in str(a) for a in args), str(args))

		servers = config.get("mcp", {}).get("servers", {})
		check("mcp 1c-testpilot разведён", servers.get("1c-testpilot", {}).get("command") == "1c-testpilot", str(servers.keys()))
		check("mcp 1c-db с url по умолчанию", servers.get("1c-db", {}).get("url") == "http://127.0.0.1:6003/mcp", str(servers.get("1c-db")))
		check("профили testpilot указывают в .zcode", servers.get("1c-testpilot", {}).get("env", {}).get("TC1C_PROFILES_FILE") == ".zcode/testpilot/profiles.yaml", "нет")

		# ── idемпотентность: повторный wire не дублирует и не меняет конфиг ──
		before = (root / ".zcode" / "config.json").read_text(encoding="utf-8")
		run_vendored()
		after = (root / ".zcode" / "config.json").read_text(encoding="utf-8")
		check("повторный wire идемпотентен", before == after, "конфиг изменился")

		# ── --check зелёный после wire ──
		r = run_vendored("--check")
		check("--check зелёный после wire", r.returncode == 0, r.stdout[:200])

		# ── --check ловит расхождение ──
		config["hooks"]["events"]["PreToolUse"] = []
		(root / ".zcode" / "config.json").write_text(
			json.dumps(config, ensure_ascii=False), encoding="utf-8")
		r = run_vendored("--check")
		check("--check красный при расхождении", r.returncode == 1, r.stdout[:200])

		# ── чужие hook-записи сохраняются ──
		config["hooks"]["events"]["PreToolUse"] = [
			{"matcher": "^Bash$", "hooks": [{"type": "process", "command": "my-own-hook"}]},
			*[e for e in config["hooks"]["events"].get("PreToolUse", [])],
		]
		(root / ".zcode" / "config.json").write_text(
			json.dumps(config, ensure_ascii=False), encoding="utf-8")
		run_vendored()
		config2 = json.loads((root / ".zcode" / "config.json").read_text(encoding="utf-8"))
		commands = [h.get("command") for e in config2["hooks"]["events"]["PreToolUse"] for h in e["hooks"]]
		check("чужая hook-запись сохранена", "my-own-hook" in commands, str(commands))
		check("наша запись не задублирована",
		      sum(1 for c in commands if c == "python3") == 1, str(commands))

		# ── legacy-записи из hooks.<Event> переносятся в hooks.events ──
		config2["hooks"]["PreToolUse"] = [
			{"matcher": "^Bash$", "hooks": [{"type": "process", "command": "legacy-own-hook"}]}]
		(root / ".zcode" / "config.json").write_text(
			json.dumps(config2, ensure_ascii=False), encoding="utf-8")
		run_vendored()
		config3 = json.loads((root / ".zcode" / "config.json").read_text(encoding="utf-8"))
		cmds = [h.get("command") for e in config3["hooks"]["events"]["PreToolUse"] for h in e["hooks"]]
		check("legacy-запись переехала в events",
		      "legacy-own-hook" in cmds and "PreToolUse" not in config3["hooks"], str(config3["hooks"].keys()))
		check("legacy-перенос без дублей нашей записи",
		      sum(1 for c in cmds if c == "python3") == 1, str(cmds))

		# ── url из contour.json подставляется ──
		c1 = root / ".zcode" / "1c" / "contour.json"
		c1.write_text('{"1c_db": {"url": "http://192.168.1.50:6003/mcp"}}', encoding="utf-8")
		run_vendored()
		config3 = json.loads((root / ".zcode" / "config.json").read_text(encoding="utf-8"))
		check("url 1c-db из contour.json",
		      config3["mcp"]["servers"]["1c-db"]["url"] == "http://192.168.1.50:6003/mcp",
		      config3["mcp"]["servers"]["1c-db"]["url"])

		# ── каркас env-настроек: дозаполнение без затирания ──
		contour3 = json.loads(c1.read_text(encoding="utf-8"))
		check("пользовательский url 1c_db.url не затёрт каркасом",
		      contour3["1c_db"]["url"] == "http://192.168.1.50:6003/mcp",
		      str(contour3.get("1c_db")))
		check("каркас добавил 1c.publish.ibsrv (distrobox-режим)",
		      contour3.get("1c", {}).get("publish", {}).get("ibsrv") == "distrobox:auto",
		      str(contour3.get("1c")))

		# ── каркас не создаёт лишнего при повторном wire ──
		before_contour = c1.read_text(encoding="utf-8")
		run_vendored()
		check("повторный wire не меняет полный contour.json",
		      c1.read_text(encoding="utf-8") == before_contour, "файл изменился")

		# ── отсутствующий contour.json создаётся каркасом ──
		c1.unlink()
		run_vendored()
		contour4 = json.loads(c1.read_text(encoding="utf-8"))
		check("создан каркас с 1c_db.url по умолчанию",
		      contour4.get("1c_db", {}).get("url") == "http://127.0.0.1:6003/mcp",
		      str(contour4.get("1c_db")))
		check("создан каркас с publish.ibsrv=distrobox:auto",
		      contour4.get("1c", {}).get("publish", {}).get("ibsrv") == "distrobox:auto",
		      str(contour4.get("1c")))

		# ── битый contour.json не трогается и не роняет wire ──
		c1.write_text("{oops", encoding="utf-8")
		r = run_vendored()
		check("битый contour.json: wire выходит 0", r.returncode == 0, r.stderr[:200])
		check("битый contour.json не перезаписан",
		      c1.read_text(encoding="utf-8") == "{oops", "файл перезаписан")

		# ── --check не создаёт contour.json ──
		c1.unlink()
		r = run_vendored("--check")
		check("--check не создаёт contour.json", not c1.exists(), "файл появился")

		return summary()


if __name__ == "__main__":
	sys.exit(main())
