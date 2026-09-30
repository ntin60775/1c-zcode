#!/usr/bin/env python3
"""Тесты retry_gate.py: третий идентичный повтор упавшей команды — блок.

Запуск: python3 tests/test_retry_gate.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import audit_entries, check, run_hook, summary  # noqa: E402

CMD = "oscript build.os"


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		state = str(Path(tmp) / "state")
		cwd = str(Path(tmp))

		def hook(event):
			return run_hook("retry_gate.py", event, state, cwd)

		def pre(command):
			return hook({"hook_event_name": "PreToolUse", "session_id": "s1",
			             "tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd})

		def result(command, ok, event_name):
			return hook({"hook_event_name": event_name, "session_id": "s1",
			             "tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd})

		# ── до ошибок команда свободна ──
		r = pre(CMD)
		check("без ошибок команда проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── первая ошибка: повтор ещё свободен ──
		result(CMD, False, "PostToolUseFailure")
		r = pre(CMD)
		check("после первой ошибки повтор свободен", r.returncode == 0, f"exit {r.returncode}")

		# ── вторая ошибка: третий идентичный повтор блокируется ──
		result(CMD, False, "PostToolUseFailure")
		r = pre(CMD)
		check("третий идентичный повтор блокируется", r.returncode == 2, f"exit {r.returncode}")
		check("причина называет счётчик ошибок", "2 раза" in r.stderr, r.stderr[:200])
		check("блок в журнале", any(e.get("rule") == "retry-discipline"
			for e in audit_entries(state, "retry-discipline")), "нет записи")

		# ── другая команда не задета ──
		r = pre("oscript build.os --verbose")
		check("изменённая команда свободна", r.returncode == 0, f"exit {r.returncode}")

		# ── успех сбрасывает счётчик ──
		result(CMD, True, "PostToolUse")
		r = pre(CMD)
		check("после успеха счётчик сброшен", r.returncode == 0, f"exit {r.returncode}")

		# ── счётчик сессионный: чужая сессия не наследует ──
		result(CMD, False, "PostToolUseFailure")
		result(CMD, False, "PostToolUseFailure")
		r = hook({"hook_event_name": "PreToolUse", "session_id": "s2",
		          "tool_name": "Bash", "tool_input": {"command": CMD}, "cwd": cwd})
		check("другая сессия не наследует счётчик", r.returncode == 0, f"exit {r.returncode}")

		# ── не-bash события не задеты ──
		r = hook({"hook_event_name": "PreToolUse", "session_id": "s1",
		          "tool_name": "Write", "tool_input": {"file_path": "x"}, "cwd": cwd})
		check("не-bash инструмент проходит", r.returncode == 0, f"exit {r.returncode}")

		return summary()


if __name__ == "__main__":
	sys.exit(main())
