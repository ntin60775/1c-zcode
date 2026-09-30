#!/usr/bin/env python3
"""Тесты гейта unica_source_gate.py: stdin JSON → вердикт (exit 0/2).

Гейт гоняется как процесс — ровно так, как его вызывает ZCode; журнал
перенаправляется в временный каталог через ZCODE_1C_STATE_DIR. Платформа
и база 1С не нужны.

Запуск: python3 tests/test_unica_source_gate.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "hooks" / "unica_source_gate.py"

failures = []


def run_gate(event: dict, state_dir: str, cwd: str):
	env = dict(os.environ, ZCODE_1C_STATE_DIR=state_dir)
	return subprocess.run(
		[sys.executable, str(GATE)],
		input=json.dumps(event),
		capture_output=True,
		text=True,
		cwd=cwd,
		env=env,
	)


def audit_entries(state_dir: str):
	log = Path(state_dir) / "logs" / "rule-audit.jsonl"
	if not log.is_file():
		return []
	return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln]


def check(name: str, ok: bool, detail: str = ""):
	print(("  OK  " if ok else " FAIL ") + name)
	if not ok:
		failures.append(f"{name}: {detail}")


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		state = str(Path(tmp) / "state")
		tree = Path(tmp) / "tree"
		(tree / "src" / "cf" / "CommonModules").mkdir(parents=True)
		(tree / "src" / "docs").mkdir(parents=True)
		cwd = str(tree)

		bsl = "src/cf/CommonModules/ОбщегоНазначения.bsl"

		# ── write исходника → блок с маршрутизацией на unica.code.patch ──
		r = run_gate({"tool_name": "Write", "tool_input": {"file_path": bsl, "content": ""},
		              "cwd": cwd}, state, cwd)
		check("write .bsl в src/cf блокируется", r.returncode == 2, f"exit {r.returncode}")
		check("причина называет unica.code.patch", "unica.code.patch" in r.stderr, r.stderr[:200])
		check("блок записан в журнал", any(
			e.get("rule") == "unica-source-gate" and e.get("decision") == "blocked"
			for e in audit_entries(state)), str(audit_entries(state)))

		# ── абсолютный путь исходника → блок ──
		r = run_gate({"tool_name": "Edit",
		              "tool_input": {"file_path": str(tree / bsl), "old_string": "a", "new_string": "b"},
		              "cwd": "/tmp"}, state, cwd)
		check("edit .bsl по абсолютному пути блокируется", r.returncode == 2, f"exit {r.returncode}")

		# ── AGENTS.md внутри src/ — документация, не исходник ──
		r = run_gate({"tool_name": "Write",
		              "tool_input": {"file_path": "src/cf/AGENTS.md", "content": ""},
		              "cwd": cwd}, state, cwd)
		check("AGENTS.md в src/ не блокируется", r.returncode == 0, f"exit {r.returncode}")

		# ── файл вне src/tests — не предмет гейта ──
		r = run_gate({"tool_name": "Write",
		              "tool_input": {"file_path": "src/docs/readme.md", "content": ""},
		              "cwd": cwd}, state, cwd)
		check("файл вне исходников 1С проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── эскалация: allowlist в дереве разрешает правку и журналируется ──
		esc = tree / ".zcode"
		esc.mkdir(exist_ok=True)
		(esc / "unica-gate-escalations.txt").write_text(
			f"# разовая прямая правка\n{bsl}\n", encoding="utf-8")
		r = run_gate({"tool_name": "Write", "tool_input": {"file_path": bsl, "content": ""},
		              "cwd": cwd}, state, cwd)
		check("эскалация из allowlist разрешает правку", r.returncode == 0, f"exit {r.returncode}")
		check("разрешение записано в журнал (confirmed)", any(
			e.get("decision") == "confirmed" for e in audit_entries(state)),
			str(audit_entries(state)))

		# ── эскалация своего дерева не открывает чужое ──
		other = Path(tmp) / "other-tree"
		(other / "src" / "cfe" / "Расширение").mkdir(parents=True)
		r = run_gate({"tool_name": "Write",
		              "tool_input": {"file_path": str(other / "src/cfe/Расширение/Module.bsl"), "content": ""},
		              "cwd": cwd}, state, cwd)
		check("запись чужого пути из-под своего cwd блокируется", r.returncode == 2,
		      f"exit {r.returncode}")

		# ── bash: sed -i и перенаправление в исходники ──
		r = run_gate({"tool_name": "Bash",
		              "tool_input": {"command": f"sed -i 's/a/b/' {bsl}"},
		              "cwd": cwd}, state, cwd)
		check("sed -i по исходнику блокируется", r.returncode == 2, f"exit {r.returncode}")
		r = run_gate({"tool_name": "Bash",
		              "tool_input": {"command": f"echo x >> src/cf/CommonModules/Модуль.bsl"},
		              "cwd": cwd}, state, cwd)
		check("перенаправление в исходник блокируется", r.returncode == 2, f"exit {r.returncode}")
		r = run_gate({"tool_name": "Bash",
		              "tool_input": {"command": "echo x >> build/out/log.txt"},
		              "cwd": cwd}, state, cwd)
		check("перенаправление вне исходников проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── прочие инструменты и неразборчивый вход не задеваются ──
		r = run_gate({"tool_name": "Read", "tool_input": {"file_path": bsl}, "cwd": cwd}, state, cwd)
		check("read не блокируется", r.returncode == 0, f"exit {r.returncode}")
		r = subprocess.run([sys.executable, str(GATE)], input="not json",
		                   capture_output=True, text=True, cwd=cwd,
		                   env=dict(os.environ, ZCODE_1C_STATE_DIR=state))
		check("неразборчивый stdin проходит молча", r.returncode == 0, f"exit {r.returncode}")

	print()
	if failures:
		print(f"провалено проверок: {len(failures)}")
		for f in failures:
			print(f"  - {f}")
		return 1
	print("все проверки прошли")
	return 0


if __name__ == "__main__":
	sys.exit(main())
