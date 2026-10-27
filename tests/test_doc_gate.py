#!/usr/bin/env python3
"""Тесты doc_gate.py (docs-before-design): сверка документацией до мутаций.

Строгий канон боевого проекта, поверхность Unica 0.15: сверка — unica.docs /
unica.search; мутации — unica.apply (код/структура) и unica.run {op: push|
upload|apply|reset}; серия правок кода (ops code.*) флаг не сбрасывает.

Семантика учёта (issue #32): флаг тратит публикация — unica.apply с
executionToken и unica.run с исполнением (dryRun не true); preview-план
(at+ops) и dryRun-прогон сверку требуют, но не тратят. Осмысленность
docs-ответа считается по секциям провайдеров: недоступные секции живой
ответ не роняют.

Запуск: python3 tests/test_doc_gate.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import audit_entries, check, run_hook, summary  # noqa: E402

DOCS_OK_RESPONSE = {"content": [{"type": "text", "text": '{"results": [{"id": "doc-1", "title": "Хеширование"}]}'}]}
DOCS_EMPTY_RESPONSE = {"content": [{"type": "text", "text": '{"hits": []}'}]}
SEARCH_EMPTY_RESPONSE = {"content": [{"type": "text", "text": '{"matches": []}'}]}
DOCS_UNSUPPORTED = {"content": [{"type": "text", "text": json.dumps(
	{"ok": False, "code": "unsupported_source",
	 "message": "configuration-documentation is not available"})}]}
DOCS_WORKING = {"content": [{"type": "text", "text": json.dumps(
	{"status": "working", "taskId": "task-doc-1"})}]}
DOCS_TASK_RESULT = {"content": [{"type": "text", "text": json.dumps(
	{"status": "completed", "taskId": "task-doc-1",
	 "result": {"sections": [{"status": "ok", "hits": [{"id": "doc-9"}]}]}})}]}
SEARCH_ZERO_TOTALS = {"content": [{"type": "text", "text": json.dumps(
	{"data": {"totals": {"matches": 0, "files": 0}, "items": []}})}]}

DOCS_SECTIONS_MIXED = {"content": [{"type": "text", "text": json.dumps({"sections": [
	{"provider": "platform-syntax-help", "status": "unavailable", "hits": []},
	{"provider": "kb-1ci", "status": "ok", "hits": [{"id": "doc-1"}]},
]})}]}
DOCS_SECTIONS_DEAD = {"content": [{"type": "text", "text": json.dumps({"sections": [
	{"provider": "platform-syntax-help", "status": "unavailable", "hits": []},
	{"provider": "kb-administrator-guide", "status": "unavailable", "hits": []},
]})}]}
DOCS_SECTIONS_WRAPPED = {"content": [{"type": "text", "text": json.dumps({"data": {"sections": [
	{"provider": "platform-syntax-help", "status": "unavailable", "hits": []},
	{"provider": "v8std", "status": "ok", "hits": [{"id": "std-1"}]},
]}})}]}


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

		def code_edit(sid="s1"):
			return pre("mcp__unica__apply",
			           {"ops": [{"op": "code.replace", "anchor": "X", "code": "…"}]}, sid=sid)

		def plan(sid="s1"):
			return pre("mcp__unica__apply",
			           {"ops": [{"op": "meta.addAttribute", "object": "Документ.З"}]}, sid=sid)

		def publish(token, sid="s1"):
			return pre("mcp__unica__apply", {"executionToken": token}, sid=sid)

		# ── мутация без сверки блокируется ──
		r = code_edit()
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
		r = code_edit()
		check("unica.view не засчитывается как сверка", r.returncode == 2, f"exit {r.returncode}")

		# ── пустой результат docs — не сверка; непустой — сверка ──
		docs("ХешФормат", DOCS_EMPTY_RESPONSE)
		r = code_edit()
		check("пустой результат поиска — не сверка", r.returncode == 2, f"exit {r.returncode}")
		docs("ХешФормат")
		r = code_edit()
		check("после непустой сверки apply проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── серия правок кода (ops code.*) флаг не сбрасывает ──
		r = pre("mcp__unica__apply", {"ops": [{"op": "code.replace", "anchor": "Y", "code": "…"}]})
		check("вторая правка кода в серии проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── план сверку требует, но не тратит; тратит публикация ──
		r = plan()
		check("preview-план проходит со сверкой", r.returncode == 0, f"exit {r.returncode}")
		r = code_edit()
		check("preview-план не израсходовал сверку", r.returncode == 0, f"exit {r.returncode}")
		r = plan()
		check("ретрай плана после схемной ошибки — на той же сверке", r.returncode == 0, f"exit {r.returncode}")
		r = publish("tok-1")
		check("публикация по executionToken проходит", r.returncode == 0, f"exit {r.returncode}")
		r = code_edit()
		check("публикация израсходовала сверку", r.returncode == 2, f"exit {r.returncode}")
		r = publish("tok-2")
		check("публикация без свежей сверки блокируется", r.returncode == 2, f"exit {r.returncode}")

		# ── run push — исполнение: требует и тратит флаг ──
		docs("модули")
		r = pre("mcp__unica__run", {"args": {"op": "push", "force": True, "sourceSet": "main"}})
		check("run push со сверкой проходит", r.returncode == 0, f"exit {r.returncode}")
		r = code_edit()
		check("run push сбросил флаг сверки", r.returncode == 2, f"exit {r.returncode}")

		# ── ошибка docs-вызова — не сверка ──
		docs("упавший", DOCS_OK_RESPONSE, is_error=True)
		r = code_edit()
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
		check("plugin-обёртка: preview-план прошёл", r.returncode == 0, f"exit {r.returncode}")
		r = pre("mcp__plugin_unica_unica__unica.apply",
		        {"ops": [{"op": "code.replace", "anchor": "Z", "code": "…"}]})
		check("plugin-обёртка: preview не израсходовал сверку", r.returncode == 0, f"exit {r.returncode}")
		r = pre("mcp__plugin_unica_unica__unica.apply", {"executionToken": "tok-p"})
		check("plugin-обёртка: публикация прошла", r.returncode == 0, f"exit {r.returncode}")
		r = pre("mcp__plugin_unica_unica__unica.apply",
		        {"ops": [{"op": "code.replace", "anchor": "Z", "code": "…"}]})
		check("plugin-обёртка: публикация сбросила флаг", r.returncode == 2, f"exit {r.returncode}")

		# ── живая подчёркнутая форма: mcp__plugin_unica_unica__unica_run ──
		docs("подчёркнутая форма")
		r = pre("mcp__plugin_unica_unica__unica_run",
		        {"args": {"op": "push", "force": True, "sourceSet": "main"}})
		check("unica_run: push со сверкой проходит", r.returncode == 0, f"exit {r.returncode}")
		r = pre("mcp__plugin_unica_unica__unica_run",
		        {"args": {"op": "push", "force": True, "sourceSet": "main"}})
		check("unica_run: push без свежей сверки блокируется", r.returncode == 2, f"exit {r.returncode}")

		# ── каноническая форма run: op на верхнем уровне (обход закрыт) ──
		r = pre("mcp__unica__run", {"op": "push", "args": {"sourceSet": "main"}, "dryRun": False})
		check("канонический run push без сверки блокируется", r.returncode == 2, f"exit {r.returncode}")
		docs("загрузка расширения")
		r = pre("mcp__unica__run", {"op": "push", "args": {"sourceSet": "main"}, "dryRun": True})
		check("run dryRun:true проходит со сверкой", r.returncode == 0, f"exit {r.returncode}")
		r = pre("mcp__unica__run", {"op": "push", "args": {"sourceSet": "main"}, "dryRun": True})
		check("повторный dryRun:true — на той же сверке", r.returncode == 0, f"exit {r.returncode}")
		r = pre("mcp__unica__run", {"op": "push", "args": {"sourceSet": "main"}, "dryRun": False})
		check("исполнение run dryRun:false проходит", r.returncode == 0, f"exit {r.returncode}")
		r = pre("mcp__unica__run", {"op": "push", "args": {"sourceSet": "main"}})
		check("исполнение run без dryRun израсходовало сверку", r.returncode == 2, f"exit {r.returncode}")
		r = pre("mcp__unica__run", {"op": "reset", "args": {}, "dryRun": True})
		check("dryRun без свежей сверки блокируется", r.returncode == 2, f"exit {r.returncode}")

		# ── секции провайдеров: недоступные не роняют живой ответ ──
		docs("секции", DOCS_SECTIONS_MIXED)
		r = code_edit()
		check("живая секция при мёртвых — сверка зачтена", r.returncode == 0, f"exit {r.returncode}")
		publish("tok-3")
		docs("секции", DOCS_SECTIONS_DEAD)
		r = code_edit()
		check("все секции недоступны — не сверка", r.returncode == 2, f"exit {r.returncode}")
		post("mcp__unica__search", {"query": "динсписок"}, SEARCH_EMPTY_RESPONSE)
		r = code_edit()
		check("пустой unica.search не сверка", r.returncode == 2, f"exit {r.returncode}")
		publish("tok-4")
		docs("секции в data", DOCS_SECTIONS_WRAPPED)
		r = code_edit()
		check("секции внутри data тоже разобраны", r.returncode == 0, f"exit {r.returncode}")

		# ── 0.15: отказ источника, задача и нулевая сводка — не сверка ──
		publish("tok-5")
		docs("configuration-documentation", DOCS_UNSUPPORTED)
		r = code_edit()
		check("unsupported_source не сверка", r.returncode == 2, f"exit {r.returncode}")
		docs("долгий вопрос", DOCS_WORKING)
		r = code_edit()
		check("status: working не сверка", r.returncode == 2, f"exit {r.returncode}")
		post("mcp__unica__task_result", {"taskId": "task-doc-1"}, DOCS_TASK_RESULT)
		r = code_edit()
		check("ответ task.result закрывает сверку", r.returncode == 0, f"exit {r.returncode}")
		publish("tok-6")
		post("mcp__unica__search", {"query": "нет"}, SEARCH_ZERO_TOTALS)
		r = code_edit()
		check("нулевая сводка поиска не сверка", r.returncode == 2, f"exit {r.returncode}")

		# ── успешная сверка липкая: пустой доп. вызов её не отменяет ──
		docs("живой ответ")
		docs("пустой повтор", DOCS_EMPTY_RESPONSE)
		r = code_edit()
		check("пустой повтор после успеха не отменяет сверку", r.returncode == 0, f"exit {r.returncode}")

		# ── состояние сессионно: другая сессия не наследует сверку ──
		r = code_edit(sid="s2")
		check("другая сессия начинает без сверки", r.returncode == 2, f"exit {r.returncode}")

		# ── неразборчивый вход проходит молча ──
		r = run_hook("doc_gate.py", {}, state, cwd)
		check("пустой stdin проходит молча", r.returncode == 0, f"exit {r.returncode}")

	return summary()


if __name__ == "__main__":
	sys.exit(main())