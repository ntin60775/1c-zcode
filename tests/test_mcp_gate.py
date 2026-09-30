#!/usr/bin/env python3
"""Тесты mcp_gate.py: изменяющие инструменты тест-контура в прод — блок.

Прод-контур fail-closed: без явного признака test всё считается продом.
Распознаются нативные MCP-вызовы (1c-testpilot, 1c-db) и REST-toolkit
через bash.

Запуск: python3 tests/test_mcp_gate.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import audit_entries, check, run_hook, summary  # noqa: E402


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		tmp_path = Path(tmp)
		state = str(tmp_path / "state")
		tree = tmp_path / "tree"
		tree.mkdir()
		(tree / "v8project.yaml").write_text("workPath: src\n", encoding="utf-8")
		cwd = str(tree)

		def call(tool, tool_input, command=None):
			payload = {"command": command} if command is not None else tool_input
			return run_hook("mcp_gate.py", {
				"hook_event_name": "PreToolUse", "session_id": "s1",
				"tool_name": tool, "tool_input": payload, "cwd": cwd}, state, cwd)

		# ── fail-closed: без признака контура — прод ──
		r = call("mcp__1c-db__execute_code", {"code": "…"})
		check("execute_code в прод-контуре блокируется", r.returncode == 2, f"exit {r.returncode}")
		check("блок в журнале", any(e.get("decision") == "blocked"
			for e in audit_entries(state, "mcp-unsafe-gate")), "нет записи")

		r = call("mcp__1c-testpilot__tc_execute_code", {})
		check("tc_execute_code блокируется", r.returncode == 2, f"exit {r.returncode}")

		r = call("mcp__1c-db__execute_query", {"query": "ВЫБРАТЬ 1"})
		check("читающий инструмент проходит", r.returncode == 0, f"exit {r.returncode}")
		r = call("mcp__1c-testpilot__tc_field", {})
		check("tc_field (UI-чтение) проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── признак контура: ключ в v8project.local.yaml ──
		(tree / "v8project.local.yaml").write_text(
			"infobase:\n  user: bot\ncontour: test\n", encoding="utf-8")
		r = call("mcp__1c-db__execute_code", {})
		check("тест-контур разрешает", r.returncode == 0, f"exit {r.returncode}")
		check("разрешение в журнале", any(e.get("decision") == "allowed"
			for e in audit_entries(state, "mcp-unsafe-gate")), "нет записи")
		(tree / "v8project.local.yaml").unlink()

		# ── явный prod в .zcode/contour ──
		zdir = tree / ".zcode"
		zdir.mkdir(exist_ok=True)
		(zdir / "contour").write_text("prod\n", encoding="utf-8")
		r = call("mcp__1c-db__execute_code", {})
		check("файл .zcode/contour=prod блокирует", r.returncode == 2, f"exit {r.returncode}")

		# ── эскалация: allowlist ──
		(zdir / "mcp-write-allow.txt").write_text("# разово\nexecute_code\n", encoding="utf-8")
		r = call("mcp__1c-db__execute_code", {})
		check("эскалация из allowlist разрешает", r.returncode == 0, f"exit {r.returncode}")
		r = call("mcp__1c-testpilot__tc_execute_query", {})
		check("allowlist точечен (другой инструмент блокируется)", r.returncode == 2,
		      f"exit {r.returncode}")
		(zdir / "mcp-write-allow.txt").unlink()

		# ── REST-toolkit через bash ──
		r = call("Bash", {}, command="curl -X POST http://127.0.0.1:6003/api/execute_code -d '{}'")
		check("REST execute_code через curl блокируется", r.returncode == 2, f"exit {r.returncode}")
		r = call("Bash", {}, command="curl http://127.0.0.1:6003/api/execute_query?text=ВЫБРАТЬ")
		check("REST execute_query проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── override опасных инструментов через contour.json ──
		c1dir = zdir / "1c"
		c1dir.mkdir(exist_ok=True)
		(c1dir / "contour.json").write_text(
			'{"mcp_gate": {"dangerous": {"1c-db": ["my_dangerous_tool"]}}}',
			encoding="utf-8")
		r = call("mcp__1c-db__my_dangerous_tool", {})
		check("инструмент из override блокируется", r.returncode == 2, f"exit {r.returncode}")
		r = call("mcp__1c-db__submit_for_deanonymization", {})
		check("дефолтный список перекрыт (не блокируется)", r.returncode == 0, f"exit {r.returncode}")

		# ── прочее не задето ──
		r = call("mcp__unica__code_patch", {})
		check("unica-вызовы не предмет этого гейта", r.returncode == 0, f"exit {r.returncode}")

		return summary()


if __name__ == "__main__":
	sys.exit(main())
