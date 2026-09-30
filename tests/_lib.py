#!/usr/bin/env python3
"""_lib.py — общие механизмы тестов гейтов: прогон хука как процесса
(ровно как вызывает ZCode), чтение журнала аудита, счётчик проверок.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "hooks"

failures = []


def run_hook(script: str, event: dict, state_dir: str, cwd: str):
	"""Хук как процесс: stdin JSON → (returncode, stdout, stderr)."""
	return subprocess.run(
		[sys.executable, str(HOOKS / script)],
		input=json.dumps(event),
		capture_output=True,
		text=True,
		cwd=cwd,
		env=dict(os.environ, ZCODE_1C_STATE_DIR=state_dir),
	)


def audit_entries(state_dir: str, rule: str = None):
	log = Path(state_dir) / "logs" / "rule-audit.jsonl"
	if not log.is_file():
		return []
	entries = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln]
	return [e for e in entries if rule is None or e.get("rule") == rule]


def check(name: str, ok: bool, detail: str = ""):
	print(("  OK  " if ok else " FAIL ") + name)
	if not ok:
		failures.append(f"{name}: {detail}")


def summary() -> int:
	print()
	if failures:
		print(f"провалено проверок: {len(failures)}")
		for f in failures:
			print(f"  - {f}")
		return 1
	print("все проверки прошли")
	return 0
