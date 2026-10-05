#!/usr/bin/env python3
"""Тесты worktree_hint.py: после git worktree add — подсказка пути в additionalContext.

Запуск: python3 tests/test_worktree_hint.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, run_hook, summary  # noqa: E402


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		state = str(Path(tmp) / "state")
		cwd = str(Path(tmp))

		def post(command, **extra):
			event = {"hook_event_name": "PostToolUse", "session_id": "s1",
			         "tool_name": "Bash", "tool_input": {"command": command},
			         "cwd": cwd}
			event.update(extra)
			return run_hook("worktree_hint.py", event, state, cwd)

		def context(result):
			try:
				return json.loads(result.stdout).get("additionalContext", "")
			except (json.JSONDecodeError, ValueError):
				return ""

		# ── относительный путь резолвится от cwd сессии ──
		rel_repo = Path(tmp) / "wt-1"
		rel_repo.mkdir(parents=True)
		r = post("git worktree add wt-1 main")
		check("ворктри-команда: exit 0", r.returncode == 0, f"exit {r.returncode}")
		check("подсказка содержит резолвнутый путь", str(rel_repo) in context(r), r.stdout[:200])

		# ── флаг -b с веткой не принимается за путь ──
		abs_dir = Path(tmp) / "wt-2"
		abs_dir.mkdir()
		r = post(f"git worktree add -b feature/x {abs_dir}")
		check("-b не путается с путём", str(abs_dir) in context(r), r.stdout[:200])

		# ── путь с commit-ish: путь первый ──
		dir3 = Path(tmp) / "wt-3"
		dir3.mkdir()
		r = post("git worktree add wt-3 HEAD~1")
		check("commit-ish после пути отброшен", str(dir3) in context(r), r.stdout[:200])

		# ── cd … && git worktree add — тоже матчится ──
		dir4 = Path(tmp) / "wt-4"
		dir4.mkdir()
		r = post(f"cd /somewhere && git worktree add {dir4}")
		check("команда с cd && матчится", str(dir4) in context(r), r.stdout[:200])

		# ── не ворктри-команда: молчание ──
		r = post("git status")
		check("git status молчит", r.stdout.strip() == "", r.stdout[:200])
		r = post("git worktree list")
		check("worktree list молчит", r.stdout.strip() == "", r.stdout[:200])

		# ── несуществующий каталог: молчание ──
		r = post("git worktree add wt-nope main")
		check("несозданный каталог молчит", r.stdout.strip() == "", r.stdout[:200])

		# ── не PostToolUse: молчание ──
		r = run_hook("worktree_hint.py",
		             {"hook_event_name": "PreToolUse", "session_id": "s1",
		              "tool_name": "Bash",
		              "tool_input": {"command": "git worktree add wt-1 main"},
		              "cwd": cwd}, state, cwd)
		check("PreToolUse молчит", r.stdout.strip() == "" and r.returncode == 0, r.stdout[:200])

		# ── упавший Bash: молчание ──
		dir5 = Path(tmp) / "wt-5"
		dir5.mkdir()
		r = post(f"git worktree add {dir5}", is_error=True)
		check("is_error молчит", r.stdout.strip() == "", r.stdout[:200])

		# ── подсказка зовёт перезапуск сессии ──
		r = post("git worktree add wt-1 main")
		check("подсказка упоминает перезапуск сессии", "перезапустить сессию" in context(r), context(r)[:200])

		return summary()


if __name__ == "__main__":
	sys.exit(main())
