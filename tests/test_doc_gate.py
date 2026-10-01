#!/usr/bin/env python3
"""Тесты doc_gate.py (docs-before-design): сверка документацией до мутаций.

Строгий канон боевого проекта, поверхность Unica 0.13: сверка — unica.docs /
unica.search; мутации — unica.apply (код/структура) и unica.run {op: push|
upload|apply|reset}; серия правок кода (ops code.*) флаг не сбрасывает.

Запуск: python3 tests/test_doc_gate.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import audit_entries, check, run_hook, summary  # noqa: E402

DOCS_OK_RESPONSE = {"content": [{"type": "text", "text": '{"results": [{"id": "doc-1", "title": "Хеширование"}]}'}]}
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

		def docs(query, response=DOCS_OK_RESPONSE, sid="s1", is_error=False):
			pre("mcp__unica__docs", {"query": query}, sid=sid)
			return post("mcp__unica__docs", {"query": query}, response, sid=sid, is_error=is_error)

		# ── мутация без сверки блокируется ──
		r = pre("mcp__unica__apply", {"ops": [{"op": "code.replace", "anchor": "X", "code": "…"}]})
		check("unica.apply без сверки блокируется", r.returncode == 2, f"exit {r.returncode}")
		check("причина называет unica.docs", "unica.docs" in r.stderr, r.stderr[:160])
		check("блок в журнале", any(e.get("rule") == "docs-before-design"
			for e in audit_entries(state, "docs-before-design")), "нет записи")

		# ── run с мутабельным op требует сверку; с прочим — нет ──
		r = pre("mcp__unica__run", {"args": {"op": "push", "force": True, "sourceSet": "main"}})
		check("run push без сверки блокируется", r.returncode == 2, f"exit {r.returncode}")
		r = pre("mcp__unica__run", {"args": {"op": "make", "target": "epf"}})
		check("run make не блокируется", r.returncode == 0, f"exit {r.returncode}")

		# ── view не сверка; search — сверка ──
		post("mcp__unica__view", {"path": "src/cf"}, {"content": [{"type": "text", "text": "…"}]})
		r = pre("mcp__unica__apply", {"ops": [{"op": "code.replace", "anchor": "X", "code": "…"}]})
		check("unica.view не засчитывается как сверка", r.returncode == 2, f"exit {r.returncode}")

		# ── пустой результат docs — не сверка; непустой — сверка ──
		docs("ХешФормат", DOCS_EMPTY_RESPONSE)
		r = pre("mcp__unica__apply", {"ops": [{"op": "code.replace", "anchor": "X", "code": "…"}]})
		check("пустой результат поиска — не сверка", r.returncode == 2, f"exit {r.returncode}")
		docs("ХешФормат")
		r = pre("mcp__unica__apply", {"ops": [{"op": "code.replace", "anchor": "X", "code": "…"}]})
		check("после непустой сверки apply проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── серия правок кода (ops code.*) флаг не сбрасывает ──
		r = pre("mcp__unica__apply", {"ops": [{"op": "code.replace", "anchor": "Y", "code": "…"}]})
		check("вторая правка кода в серии проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── структурная мутация израсходовала сверку ──
		r = pre("mcp__unica__apply", {"ops": [{"op": "meta.addAttribute", "object": "Документ.З"}]})
		check("структурная мутация проходит (сверка была)", r.returncode == 0, f"exit {r.returncode}")
		r = pre("mcp__unica__apply", {"ops": [{"op": "code.replace", "anchor": "X", "code": "…"}]})
		check("после структурной мутации флаг сброшен", r.returncode == 2, f"exit {r.returncode}")

		# ── run push — структурная: сбрасывает флаг ──
		docs("модули")
		r = pre("mcp__unica__run", {"args": {"op": "push", "force": True, "sourceSet": "main"}})
		check("run push со сверкой проходит", r.returncode == 0, f"exit {r.returncode}")
		r = pre("mcp__unica__apply", {"ops": [{"op": "code.replace", "anchor": "X", "code": "…"}]})
		check("run push сбросил флаг сверки", r.returncode == 2, f"exit {r.returncode}")

		# ── ошибка docs-вызова — не сверка ──
		docs("упавший", DOCS_OK_RESPONSE, is_error=True)
		r = pre("mcp__unica__apply", {"ops": [{"op": "code.replace", "anchor": "X", "code": "…"}]})
		check("упавший docs-вызов не сверка", r.returncode == 2, f"exit {r.returncode}")

		# ── прямая правка исходника (эскалация) требует сверку ──
		r = pre("Write", {"file_path": "src/cfe/Расшир/Ext/Module.bsl", "content": ""})
		check("прямая правка .bsl без сверки блокируется", r.returncode == 2, f"exit {r.returncode}")
		docs("модули")
		r = pre("Write", {"file_path": "src/cfe/Расшир/Ext/Module.bsl", "content": ""})
		check("после сверки прямая правка проходит", r.returncode == 0, f"exit {r.returncode}")
		r = pre("Write", {"file_path": "docs/notes.md", "content": ""})
		check("правка вне исходников не предмет гейта", r.returncode == 0, f"exit {r.returncode}")

		# ── plugin-обёртка хоста: mcp__plugin_unica_unica__unica.apply ──
		docs("plugin-обёртка")
		r = pre("mcp__plugin_unica_unica__unica.apply",
		        {"ops": [{"op": "code.replace", "anchor": "Z", "code": "…"}]})
		check("plugin-обёртка: apply со сверкой проходит", r.returncode == 0, f"exit {r.returncode}")
		r = pre("mcp__plugin_unica_unica__unica.apply",
		        {"ops": [{"op": "meta.addAttribute", "object": "Справочник.X"}]})
		check("plugin-обёртка: структурная мутация прошла", r.returncode == 0, f"exit {r.returncode}")
		r = pre("mcp__plugin_unica_unica__unica.apply",
		        {"ops": [{"op": "code.replace", "anchor": "Z", "code": "…"}]})
		check("plugin-обёртка: флаг сброшен структурной", r.returncode == 2, f"exit {r.returncode}")

		# ── состояние сессионно: другая сессия не наследует сверку ──
		r = pre("mcp__unica__apply", {"ops": [{"op": "code.replace", "anchor": "X", "code": "…"}]}, sid="s2")
		check("другая сессия начинает без сверки", r.returncode == 2, f"exit {r.returncode}")

		# ── неразборчивый вход проходит молча ──
		r = run_hook("doc_gate.py", {}, state, cwd)
		check("пустой stdin проходит молча", r.returncode == 0, f"exit {r.returncode}")

	return summary()


if __name__ == "__main__":
	sys.exit(main())
