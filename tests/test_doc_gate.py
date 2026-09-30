#!/usr/bin/env python3
"""Тесты doc_gate.py (docs-before-design): сверка документацией до мутаций.

Строгий канон боевого проекта: мутация юники требует НЕПУСТОГО вызова
documentation.*/standards.* в этой сессии; read-only вызовы и чтение
исходников сверкой не считаются; мутация метаданных сбрасывает флаг.

Запуск: python3 tests/test_doc_gate.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import audit_entries, check, run_hook, summary  # noqa: E402

DOCS_OK_RESPONSE = {"content": [{"type": "text", "text": '{"hits": [{"id": "doc-1"}]}'}]}
DOCS_EMPTY_RESPONSE = {"content": [{"type": "text", "text": '{"hits": []}'}]}


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		tmp_path = Path(tmp)
		state = str(tmp_path / "state")
		tree = tmp_path / "tree"
		(tree / "src" / "cf").mkdir(parents=True)
		(tree / "v8project.yaml").write_text("workPath: src\n", encoding="utf-8")
		cwd = str(tree)

		def hook(event):
			return run_hook("doc_gate.py", event, state, cwd)

		def pre(tool, tool_input, sid="s1"):
			return hook({"hook_event_name": "PreToolUse", "session_id": sid,
			             "tool_name": tool, "tool_input": tool_input, "cwd": cwd})

		def post(tool, tool_input, response, sid="s1", is_error=False):
			return hook({"hook_event_name": "PostToolUse", "session_id": sid,
			             "tool_name": tool, "tool_input": tool_input,
			             "tool_response": response, "is_error": is_error, "cwd": cwd})

		# ── мутация без сверки блокируется ──
		r = pre("mcp__unica__code_patch", {"sourceSet": "main", "patch": "…"})
		check("code.patch без сверки блокируется", r.returncode == 2, f"exit {r.returncode}")
		check("причина — документация до решения", "docs-before-design" in r.stderr, r.stderr[:120])
		check("блок в журнале", any(e.get("rule") == "docs-before-design"
			for e in audit_entries(state, "docs-before-design")), "нет записи")

		# ── read-only вызов сверкой не считается ──
		r = post("mcp__unica__meta_info", {"object": "Документ.Заказ"}, {"content": [
			{"type": "text", "text": '{"attributes": ["Номер"]}'}
		]})
		r = pre("mcp__unica__code_patch", {"sourceSet": "main", "patch": "…"})
		check("meta.info не засчитывается как сверка", r.returncode == 2, f"exit {r.returncode}")

		# ── документационный вызов: пустой результат — не сверка ──
		pre("mcp__unica__documentation_search", {"query": "ХешФормат"})
		post("mcp__unica__documentation_search", {"query": "ХешФормат"}, DOCS_EMPTY_RESPONSE)
		r = pre("mcp__unica__code_patch", {"sourceSet": "main", "patch": "…"})
		check("пустой результат поиска — не сверка", r.returncode == 2, f"exit {r.returncode}")

		# ── непустой результат — сверка; мутации проходят ──
		pre("mcp__unica__documentation_search", {"query": "ХешФормат"})
		post("mcp__unica__documentation_search", {"query": "ХешФормат"}, DOCS_OK_RESPONSE)
		r = pre("mcp__unica__code_patch", {"sourceSet": "main", "patch": "…"})
		check("после непустой сверки code.patch проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── мутация метаданных израсходовала сверку ──
		r = pre("mcp__unica__meta_add", {"object": "Справочник.Новый"})
		check("первая мутация метаданных проходит", r.returncode == 0, f"exit {r.returncode}")
		r = pre("mcp__unica__code_patch", {"sourceSet": "main", "patch": "…"})
		check("после мутации метаданных флаг сброшен", r.returncode == 2, f"exit {r.returncode}")

		# ── standards.* тоже сверка; ошибка вызова — нет ──
		pre("mcp__unica__standards_search", {"query": "именованные"})
		post("mcp__unica__standards_search", {"query": "именованные"}, DOCS_OK_RESPONSE)
		r = pre("mcp__unica__code_patch", {"sourceSet": "main", "patch": "…"})
		check("standards.search засчитан", r.returncode == 0, f"exit {r.returncode}")
		pre("mcp__unica__documentation_get", {"documentId": "doc-1"})
		post("mcp__unica__documentation_get", {"documentId": "doc-1"}, DOCS_OK_RESPONSE, is_error=True)
		r = pre("mcp__unica__code_patch", {"sourceSet": "main", "patch": "…"})
		check("упавший docs-вызов не сверка (флаг сброшен)", r.returncode == 2, f"exit {r.returncode}")

		# ── runtime build (applied) требует сверку; dryRun и прочие ops — нет ──
		r = pre("mcp__unica__runtime_execute", {"args": {"operation": "build", "sourceSet": "main", "dryRun": False}})
		check("runtime build без сверки блокируется", r.returncode == 2, f"exit {r.returncode}")
		r = pre("mcp__unica__runtime_execute", {"args": {"operation": "dump", "sourceSet": "main"}})
		check("runtime dump не блокируется", r.returncode == 0, f"exit {r.returncode}")
		r = pre("mcp__unica__runtime_execute", {"args": {"operation": "build", "sourceSet": "main", "dryRun": True}})
		check("runtime build в dryRun не блокируется", r.returncode == 0, f"exit {r.returncode}")

		# ── прямая правка исходника (эскалация) требует сверку ──
		r = pre("Write", {"file_path": "src/cfe/Расшир/Ext/Module.bsl", "content": ""})
		check("прямая правка .bsl без сверки блокируется", r.returncode == 2, f"exit {r.returncode}")
		pre("mcp__unica__documentation_search", {"query": "модули"})
		post("mcp__unica__documentation_search", {"query": "модули"}, DOCS_OK_RESPONSE)
		r = pre("Write", {"file_path": "src/cfe/Расшир/Ext/Module.bsl", "content": ""})
		check("после сверки прямая правка проходит", r.returncode == 0, f"exit {r.returncode}")
		r = pre("Write", {"file_path": "docs/notes.md", "content": ""})
		check("правка вне исходников не предмет гейта", r.returncode == 0, f"exit {r.returncode}")

		# ── состояние сессионно: другая сессия не наследует сверку ──
		r = pre("mcp__unica__code_patch", {"sourceSet": "main", "patch": "…"}, sid="s2")
		check("другая сессия начинает без сверки", r.returncode == 2, f"exit {r.returncode}")

		# ── неразборчивый вход проходит молча ──
		r = run_hook("doc_gate.py", {}, state, cwd)
		check("пустой stdin проходит молча", r.returncode == 0, f"exit {r.returncode}")

	return summary()


if __name__ == "__main__":
	sys.exit(main())
