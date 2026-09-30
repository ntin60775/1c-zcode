#!/usr/bin/env python3
"""Тесты bsl_style_gate.py: стайл-чек изменённых .bsl (advisory).

Чекер подменяется фейком в .zcode/style/scripts/ — реальный пак
1c-bsl-code-style вендорится отдельно и здесь не нужен.

Запуск: python3 tests/test_bsl_style_gate.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import audit_entries, check, run_hook, summary  # noqa: E402

FAKE_CHECKER = """\
import sys
bad = any("ПЛОХО" in open(p, encoding="utf-8").read() for p in sys.argv[1:])
print("tab-rhythm: отступы" if bad else "bsl_style_check_ok")
sys.exit(1 if bad else 0)
"""


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		tmp_path = Path(tmp)
		state = str(tmp_path / "state")
		tree = tmp_path / "tree"
		cwd = str(tree)
		pack_new = tree / ".zcode" / "skills" / "1c-bsl-code-style" / "scripts"
		pack_new.mkdir(parents=True)
		(tree / "src" / "cf" / "M").mkdir(parents=True)
		checker = pack_new / "bsl_style_check.py"
		checker.write_text(FAKE_CHECKER, encoding="utf-8")

		def post(tool, tool_input, env_checker=None):
			import os
			env = {"ZCODE_1C_STATE_DIR": state}
			if env_checker:
				env["1C_STYLE_ENGINE"] = env_checker
			import json
			import subprocess
			return subprocess.run(
				[sys.executable, str(Path(__file__).parent.parent / "hooks" / "bsl_style_gate.py")],
				input=json.dumps({"hook_event_name": "PostToolUse", "session_id": "s1",
				                  "tool_name": tool, "tool_input": tool_input, "cwd": cwd}),
				capture_output=True, text=True, cwd=cwd,
				env={**os.environ, **env})

		bsl = "src/cf/M/Модуль.bsl"
		(tree / bsl).write_text("Если ПЛОХО Тогда\nКонецЕсли;\n", encoding="utf-8")

		# ── нарушение: advisory-отчёт в stderr, код 0 ──
		r = post("Write", {"file_path": bsl, "content": "…"})
		check("нарушение даёт advisory (exit 0)", r.returncode == 0, f"exit {r.returncode}")
		check("отчёт чекера в stderr", "tab-rhythm" in r.stderr, r.stderr[:200])
		check("нарушение в журнале", any(e.get("rule") == "bsl-style-gate"
			for e in audit_entries(state, "bsl-style-gate")), "нет записи")

		# ── чистый файл: тихо ──
		(tree / bsl).write_text("Если Хорошо Тогда\nКонецЕсли;\n", encoding="utf-8")
		r = post("Edit", {"file_path": bsl, "old_string": "a", "new_string": "b"})
		check("чистый файл проходит тихо", r.returncode == 0 and not r.stderr.strip(),
		      r.stderr[:200])

		# ── ApplyPatch: путь из текста патча ──
		(tree / bsl).write_text("Если ПЛОХО Тогда\n", encoding="utf-8")
		r = post("ApplyPatch", {"patch": f"*** Begin Patch\n*** Update File: {bsl}\n@@\n-a\n+b\n*** End Patch\n"})
		check("ApplyPatch по .bsl проверяется", "tab-rhythm" in r.stderr, r.stderr[:200])

		# ── вне исходников и не .bsl — не предмет гейта ──
		r = post("Write", {"file_path": "docs/Module.bsl", "content": "ПЛОХО"})
		check(".bsl вне src/ не проверяется", r.returncode == 0 and not r.stderr.strip(), r.stderr[:120])
		r = post("Write", {"file_path": "src/cf/M/Form.xml", "content": "ПЛОХО"})
		check("не-.bsl исходник не проверяется", r.returncode == 0 and not r.stderr.strip(), r.stderr[:120])

		# ── прежняя раскладка .zcode/style/… тоже резолвится ──
		pack_old = tree / ".zcode" / "style" / "scripts"
		pack_old.mkdir(parents=True)
		(pack_old / "bsl_style_check.py").write_text(FAKE_CHECKER, encoding="utf-8")
		(tree / bsl).write_text("Если ПЛОХО Тогда\n", encoding="utf-8")
		r = post("Write", {"file_path": bsl, "content": "…"})
		check("прежняя раскладка пака резолвится", "tab-rhythm" in r.stderr, r.stderr[:200])
		(pack_old / "bsl_style_check.py").unlink()

		# ── чекер не установлен — тихий пропуск ──
		checker.unlink()
		(tree / bsl).write_text("Если ПЛОХО Тогда\n", encoding="utf-8")
		r = post("Write", {"file_path": bsl, "content": "…"})
		check("без чекера — тихий пропуск", r.returncode == 0 and not r.stderr.strip(), r.stderr[:120])

		# ── env-движок: контракт `check <files>` ──
		engine = tmp_path / "engine.sh"
		engine.write_text('#!/bin/sh\necho "engine: bad"\nexit 1\n', encoding="utf-8")
		engine.chmod(0o755)
		r = post("Write", {"file_path": bsl, "content": "…"}, env_checker=str(engine))
		check("env-движок используется", "engine: bad" in r.stderr, r.stderr[:200])

		return summary()


if __name__ == "__main__":
	sys.exit(main())
