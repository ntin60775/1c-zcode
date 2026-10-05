#!/usr/bin/env python3
"""session_context.py — где сессия: основное дерево или ворктри (SessionStart).

Работа по тикетам ведётся в git-ворктри, merge — в основном дереве; сессия,
запущенная не там, где работа, легко начнёт править не то. На старте сессии
хук сообщает, где cwd сессии и какие ворктри активны. Канал — stdout JSON
additionalContext (доставляется в разговор — пруф v0.4.23); всегда exit 0,
вне git-репо и без новостей — молчим.
"""
import json
import subprocess
import sys
from pathlib import Path


def git(*args: str, cwd: str) -> str:
	try:
		done = subprocess.run(
			["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)
	except (OSError, subprocess.TimeoutExpired):
		return ""
	return done.stdout.strip() if done.returncode == 0 else ""


def worktrees(toplevel: str) -> list:
	"""[(путь, ветка)] из `git worktree list --porcelain`; bare пропускаем."""
	out = git("worktree", "list", "--porcelain", cwd=toplevel)
	result = []
	entry = {}
	for line in out.splitlines():
		if not line:
			if entry.get("path") and entry.get("branch"):
				result.append((entry["path"], entry["branch"]))
			entry = {}
			continue
		key, _, value = line.partition(" ")
		if key == "worktree":
			entry["path"] = value
		elif key == "branch":
			entry["branch"] = value.rsplit("/", 1)[-1]
		elif key == "bare":
			entry.clear()
	if entry.get("path") and entry.get("branch"):
		result.append((entry["path"], entry["branch"]))
	return result


def main() -> int:
	from contour_common import read_event
	event = read_event()
	cwd = str((event or {}).get("cwd") or "").strip()
	if not cwd:
		return 0
	toplevel = git("rev-parse", "--show-toplevel", cwd=cwd)
	if not toplevel:
		return 0  # не git-репо — контуру нечего сообщать
	branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
	if branch == "HEAD":
		branch = "detached"
	boxes = worktrees(toplevel)
	main_path = Path(toplevel)
	is_worktree = (main_path / ".git").is_file()  # ворктри: .git — файл
	msg = ""
	if is_worktree:
		others = [f"{p} ({b})" for p, b in boxes if p != toplevel]
		msg = (f"сессия в ворктри {toplevel} ({branch}); "
			   f"основное дерево: {main_path} — merge тикета там.")
		if others:
			msg += " Прочие ворктри: " + ", ".join(others) + "."
	else:
		others = [f"{p} ({b})" for p, b in boxes if p != toplevel]
		if not others:
			return 0
		msg = ("cwd — основное дерево " + str(main_path)
			   + "; работа по тикетам ведётся в ворктри: " + ", ".join(others)
			   + ". Merge — здесь.")
	if msg:
		sys.stdout.write(json.dumps({"additionalContext": msg}, ensure_ascii=False) + "\n")
	return 0


if __name__ == "__main__":
	sys.exit(main())
