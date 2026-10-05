#!/usr/bin/env python3
"""worktree_hint.py — навигация после git worktree add (PostToolUse Bash).

Смена cwd сессии хуком невозможна (stateless-процесс), но после
`git worktree add` агент должен продолжать работу в новом ворктри, иначе
гейты, doctor и e2e смотрят в основное дерево. Хук извлекает путь из
команды и подсказывает через stdout JSON additionalContext (канал
доставляется в разговор — пруф v0.4.23, bsl_style_gate).

Это навигация, не гейт: блокировать нечего, всегда exit 0. Не матчили
ворктри-команду, путь не извлекли или Bash упал — молчим.
"""
import json
import os
import sys
from pathlib import Path

from contour_common import read_event

# флаги git worktree add, требующие значения (пропускаем вместе с ним)
_VALUE_FLAGS = {"-b", "-B", "-m", "-M", "-c", "--checkout", "--guess-remote", "--track"}


def extract_path(command: str) -> str:
	"""Путь из `git worktree add …`; пусто — не нашли."""
	tokens = command.split()
	for i in range(len(tokens) - 2):
		if (tokens[i], tokens[i + 1], tokens[i + 2]) != ("git", "worktree", "add"):
			continue
		rest = tokens[i + 3:]
		j = 0
		while j < len(rest):
			token = rest[j]
			if token == "--":
				j += 1
				break
			if token.startswith("-"):
				if token in _VALUE_FLAGS:
					j += 2
				else:
					j += 1
				continue
			return token
		break
	return ""


def main() -> int:
	event = read_event()
	if not event:
		return 0
	if str(event.get("hook_event_name") or "") != "PostToolUse":
		return 0
	if event.get("is_error"):
		return 0
	if str(event.get("tool_name") or "") != "Bash":
		return 0
	command = str((event.get("tool_input") or {}).get("command") or "")
	rel = extract_path(command)
	if not rel:
		return 0
	cwd = str(event.get("cwd") or os.getcwd())
	path = Path(rel) if Path(rel).is_absolute() else Path(cwd) / rel
	try:
		path = path.resolve()
		if not path.is_dir():
			return 0
	except OSError:
		return 0
	msg = (
		f"ворктри создан: {path}. Дальнейшая работа — в нём: команды с cd {path}, "
		f"doctor и e2e — из него; надёжнее перезапустить сессию ZCode в {path}."
	)
	sys.stdout.write(json.dumps({"additionalContext": msg}, ensure_ascii=False) + "\n")
	return 0


if __name__ == "__main__":
	sys.exit(main())
