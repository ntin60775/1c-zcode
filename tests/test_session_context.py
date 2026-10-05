#!/usr/bin/env python3
"""Тесты session_context.py: SessionStart сообщает основное дерево vs ворктри.

Запуск: python3 tests/test_session_context.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, run_hook, summary  # noqa: E402

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]


def run(cwd, *args):
	return subprocess.run([*GIT, *args], cwd=cwd, capture_output=True, text=True)


def context(result):
	try:
		return json.loads(result.stdout).get("additionalContext", "")
	except (json.JSONDecodeError, ValueError):
		return ""


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		state = str(Path(tmp) / "state")
		repo = Path(tmp) / "main"
		repo.mkdir()
		assert run(str(repo), "init", "-q").returncode == 0
		assert run(str(repo), "commit", "--allow-empty", "-q", "-m", "init").returncode == 0
		assert run(str(repo), "worktree", "add", "-b", "ticket-12", str(Path(tmp) / "wt-12")).returncode == 0

		def start(cwd):
			return run_hook("session_context.py",
			                {"hook_event_name": "SessionStart", "session_id": "s1",
			                 "source": "startup", "cwd": str(cwd)}, state, str(cwd))

		# ── сессия в основном дереве при живых ворктри: подсказка со списком ──
		r = start(repo)
		check("основное дерево: exit 0", r.returncode == 0, f"exit {r.returncode}")
		text = context(r)
		check("основное дерево названо", "основное дерево" in text, text[:200])
		check("ворктри в списке", "wt-12" in text and "ticket-12" in text, text[:200])

		# ── сессия в ворктри: сказано прямо ──
		r = start(Path(tmp) / "wt-12")
		text = context(r)
		check("ворктри назван ворктри", "сессия в ворктри" in text, text[:200])
		check("основное дерево указано для merge", str(repo) in text, text[:200])

		# ── основное дерево без ворктри: молчание ──
		assert run(str(repo), "worktree", "remove", str(Path(tmp) / "wt-12")).returncode == 0
		r = start(repo)
		check("без ворктри молчим", r.stdout.strip() == "" and r.returncode == 0, r.stdout[:200])

		# ── вне git-репо: молчание ──
		plain = Path(tmp) / "plain"
		plain.mkdir()
		r = start(plain)
		check("вне git молчим", r.stdout.strip() == "" and r.returncode == 0, r.stdout[:200])

		return summary()


if __name__ == "__main__":
	sys.exit(main())
