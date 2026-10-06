#!/usr/bin/env python3
"""Тесты bsl_style_gate.py: стайл-чек изменённых .bsl (advisory).

Чекер подменяется фейком в .zcode/style/scripts/ — реальный пак
1c-bsl-code-style вендорится отдельно и здесь не нужен.

Запуск: python3 tests/test_bsl_style_gate.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import audit_entries, check, run_hook, summary  # noqa: E402

FAKE_CHECKER = """\
import sys
found = False
for p in sys.argv[1:]:
    for number, line in enumerate(open(p, encoding="utf-8").read().splitlines(), 1):
        if "ПЛОХО" in line:
            print(f"{p}:{number}: [tab-rhythm] отступы")
            found = True
sys.exit(1 if found else 0)
"""


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		tmp_path = Path(tmp)
		state = str(tmp_path / "state")
		tree = tmp_path / "tree"
		cwd = str(tree)
		pack_new = tree / ".zcode" / "skills" / "1c-bsl-code-style" / "scripts"
		pack_new.mkdir(parents=True)
		(tree / "src" / "cf" / "M").mkdir(parents=True)
		checker = pack_new / "bsl_style_check.py"
		checker.write_text(FAKE_CHECKER, encoding="utf-8")

		def post(tool, tool_input, env_checker=None):
			import os
			env = {"ZCODE_1C_STATE_DIR": state}
			if env_checker:
				env["1C_STYLE_ENGINE"] = env_checker
			import json
			import subprocess
			return subprocess.run(
				[sys.executable, str(Path(__file__).parent.parent / "hooks" / "bsl_style_gate.py")],
				input=json.dumps({"hook_event_name": "PostToolUse", "session_id": "s1",
				                  "tool_name": tool, "tool_input": tool_input, "cwd": cwd}),
				capture_output=True, text=True, cwd=cwd,
				env={**os.environ, **env})

		bsl = "src/cf/M/Модуль.bsl"
		(tree / bsl).write_text("Если ПЛОХО Тогда\nКонецЕсли;\n", encoding="utf-8")

		# ── Write целиком: нарушение блокирует (exit 2) ──
		r = post("Write", {"file_path": bsl, "content": "…"})
		check("Write: нарушение в файле блокирует (exit 2)", r.returncode == 2,
		      f"exit {r.returncode}")
		check("Write: отчёт в stderr", "tab-rhythm" in r.stderr, r.stderr[:200])
		# рантайм ZCode не доставляет stderr PostToolUse модели — канал доставки
		# additionalContext в stdout-JSON (живой пруф sess_52a3e8cc)
		additional = None
		try:
			additional = json.loads(r.stdout).get("additionalContext")
		except ValueError:
			pass
		check("Write: additionalContext в stdout с причиной",
		      isinstance(additional, str) and "tab-rhythm" in additional,
		      (r.stdout or "")[:200])
		check("нарушение в журнале (blocked)", any(
			e.get("rule") == "bsl-style-gate"
			for e in audit_entries(state, "bsl-style-gate")), "нет записи")

		# ── Edit: старое нарушение вне new_string — фон, не блокирует ──
		# PostToolUse выполняется ПОСЛЕ правки: на диске — уже новое состояние
		(tree / bsl).write_text("Если ПЛОХО Тогда\nКонецЕсли; // хвост\n", encoding="utf-8")
		r = post("Edit", {"file_path": bsl, "old_string": "КонецЕсли;",
		                  "new_string": "КонецЕсли; // хвост"})
		check("Edit: старое нарушение вне правки не блокирует", r.returncode == 0,
		      f"exit {r.returncode} / {r.stderr[:120]}")

		# ── Edit: нарушение в new_string — блок ──
		(tree / bsl).write_text("Если Хорошо Тогда\nЕсли ПЛОХО Тогда\nКонецЕсли;\n",
		                        encoding="utf-8")
		r = post("Edit", {"file_path": bsl, "old_string": "хвост",
		                  "new_string": "Если ПЛОХО Тогда"})
		check("Edit: нарушение в new_string блокирует", r.returncode == 2,
		      f"exit {r.returncode} / {r.stderr[:120]}")

		# ── ApplyPatch: hunk-диапазон по заголовку ──
		(tree / bsl).write_text("Если ПЛОХО Тогда\nКонецЕсли;\n", encoding="utf-8")
		r = post("ApplyPatch", {"patch":
			f"*** Begin Patch\n*** Update File: {bsl}\n@@ -1,2 +1,2 @@\n-Если Хорошо Тогда\n"
			"+Если ПЛОХО Тогда\n+КонецЕсли;\n*** End Patch\n"})
		check("ApplyPatch: hunk-диапазон с нарушением блокирует", r.returncode == 2,
		      f"exit {r.returncode} / {r.stderr[:120]}")

		# ── чистый файл: тихо ──
		(tree / bsl).write_text("Если Хорошо Тогда\nКонецЕсли;\n", encoding="utf-8")
		r = post("Edit", {"file_path": bsl, "old_string": "a", "new_string": "b"})
		check("чистый файл проходит тихо", r.returncode == 0 and not r.stderr.strip(),
		      r.stderr[:200])

		# ── ApplyPatch: путь из текста патча ──
		(tree / bsl).write_text("Если ПЛОХО Тогда\n", encoding="utf-8")
		r = post("ApplyPatch", {"patch": f"*** Begin Patch\n*** Update File: {bsl}\n@@\n-a\n+b\n*** End Patch\n"})
		check("ApplyPatch по .bsl проверяется", "tab-rhythm" in r.stderr, r.stderr[:200])

		# ── вне исходников и не .bsl — не предмет гейта ──
		r = post("Write", {"file_path": "docs/Module.bsl", "content": "ПЛОХО"})
		check(".bsl вне src/ не проверяется", r.returncode == 0 and not r.stderr.strip(), r.stderr[:120])
		r = post("Write", {"file_path": "src/cf/M/Form.xml", "content": "ПЛОХО"})
		check("не-.bsl исходник не проверяется", r.returncode == 0 and not r.stderr.strip(), r.stderr[:120])

		# ── прежняя раскладка .zcode/style/… тоже резолвится ──
		pack_old = tree / ".zcode" / "style" / "scripts"
		pack_old.mkdir(parents=True)
		(pack_old / "bsl_style_check.py").write_text(FAKE_CHECKER, encoding="utf-8")
		(tree / bsl).write_text("Если ПЛОХО Тогда\n", encoding="utf-8")
		r = post("Write", {"file_path": bsl, "content": "…"})
		check("прежняя раскладка пака резолвится", "tab-rhythm" in r.stderr, r.stderr[:200])
		(pack_old / "bsl_style_check.py").unlink()

		# ── чекер не установлен — тихий пропуск ──
		checker.unlink()
		(tree / bsl).write_text("Если ПЛОХО Тогда\n", encoding="utf-8")
		r = post("Write", {"file_path": bsl, "content": "…"})
		check("без чекера — тихий пропуск", r.returncode == 0 and not r.stderr.strip(), r.stderr[:120])

		# ── env-движок: контракт `check <files>` ──
		engine = tmp_path / "engine.sh"
		engine.write_text('#!/bin/sh\necho "engine: bad"\nexit 1\n', encoding="utf-8")
		engine.chmod(0o755)
		r = post("Write", {"file_path": bsl, "content": "…"}, env_checker=str(engine))
		check("env-движок используется", "engine: bad" in r.stderr, r.stderr[:200])

		# ══ бридж unica.apply (ишью #11): план → состояние → исполнение ════
		# кейсы выше удалили фейк-чекер и проверили тихий пропуск без него —
		# бридж гоняет тот же резолв, вернём фейк в прежнюю раскладку
		pack_bridge = tree / ".zcode" / "style" / "scripts"
		pack_bridge.mkdir(parents=True, exist_ok=True)
		(pack_bridge / "bsl_style_check.py").write_text(FAKE_CHECKER, encoding="utf-8")

		def post_unica(tool_input, tool="mcp__unica__unica_apply", sid="s1", state_dir=None):
			return subprocess.run(
				[sys.executable, str(Path(__file__).parent.parent / "hooks" / "bsl_style_gate.py")],
				input=json.dumps({"hook_event_name": "PostToolUse", "session_id": sid,
				                  "tool_name": tool, "tool_input": tool_input, "cwd": cwd}),
				capture_output=True, text=True, cwd=cwd,
				env={**os.environ, "ZCODE_1C_STATE_DIR": state_dir or state})

		def plan_ops(text, at="main:CommonModule.М.Body"):
			return [{"op": "code.insert", "args": {"at": at, "text": text}}]

		def ctx(response):
			try:
				return json.loads(response.stdout).get("additionalContext") or ""
			except ValueError:
				return ""

		plan_file = Path(state) / "state" / "1c" / "style-bridge" / "s1.json"

		# план: advisory-снипет-чек до записи, план в состоянии сессии
		bsl_disk = tree / bsl
		bsl_disk.write_text("Если Хорошо Тогда\nКонецЕсли;\n", encoding="utf-8")
		r = post_unica({"at": "main:CommonModule.М", "ops": plan_ops("Если ПЛОХО Тогда")})
		check("apply план: advisory, не блок", r.returncode == 0, f"exit {r.returncode}")
		check("apply план: снипет-чек в stderr", "tab-rhythm" in r.stderr and "фрагмент:" in r.stderr,
		      r.stderr[:200])
		check("apply план: additionalContext доставлен", "tab-rhythm" in ctx(r), r.stdout[:120])
		check("apply план: план в состоянии сессии", plan_file.is_file(), str(plan_file))

		# исполнение: правка уже на диске, нарушение в вставке — блок, план закрыт
		bsl_disk.write_text("Если Хорошо Тогда\nЕсли ПЛОХО Тогда\n", encoding="utf-8")
		r = post_unica({"executionToken": "tok-1"}, tool="mcp__plugin_unica_unica__unica_apply")
		check("apply исполнение: нарушение вставки блокирует (exit 2)", r.returncode == 2,
		      f"exit {r.returncode}")
		check("apply исполнение: additionalContext с причиной", "tab-rhythm" in ctx(r),
		      r.stdout[:200])
		check("apply исполнение: план закрыт", not plan_file.exists())

		# чистый план и чистое исполнение — тихо
		r = post_unica({"at": "main:CommonModule.М",
		                "ops": plan_ops("Если Хорошо Тогда\nКонецЕсли;")})
		check("apply чистый план: тихо", r.returncode == 0 and "Предварительный" not in r.stderr,
		      r.stderr[:120])
		bsl_disk.write_text("Если Хорошо Тогда\nКонецЕсли;\n", encoding="utf-8")
		r = post_unica({"executionToken": "tok-2"})
		check("apply чистое исполнение: тихо", r.returncode == 0 and not r.stderr.strip(),
		      r.stderr[:120])

		# нарушение вне вставки (cold) не блокирует
		r = post_unica({"at": "main:CommonModule.М", "ops": plan_ops("Комментарий хвоста")})
		bsl_disk.write_text("Если ПЛОХО Тогда\nКомментарий хвоста\n", encoding="utf-8")
		r = post_unica({"executionToken": "tok-3"})
		check("apply: cold-нарушение вне вставки не блокирует",
		      r.returncode == 0 and "вне твоей правки" in r.stderr,
		      f"exit {r.returncode} / {r.stderr[:150]}")

		# чередование plan A → plan B → exec A → exec B
		bsl_disk.write_text("Заглушка\n", encoding="utf-8")
		post_unica({"at": "main:CommonModule.М", "ops": plan_ops("Если ПЛОХО_А Тогда")})
		post_unica({"at": "main:CommonModule.М", "ops": plan_ops("Если ПЛОХО_Б Тогда")})
		bsl_disk.write_text("Заглушка\nЕсли ПЛОХО_А Тогда\n", encoding="utf-8")
		r = post_unica({"executionToken": "tok-A"})
		check("чередование: исполнение A блокирует по A", r.returncode == 2, f"exit {r.returncode}")
		plans_after = json.loads(plan_file.read_text(encoding="utf-8")).get("plans") or []
		check("чередование: план A закрыт, план B остался",
		      len(plans_after) == 1 and "ПЛОХО_Б" in str(plans_after[0]), str(plans_after)[:150])
		bsl_disk.write_text("Заглушка\nЕсли ПЛОХО_Б Тогда\n", encoding="utf-8")
		r = post_unica({"executionToken": "tok-B"})
		check("чередование: исполнение B блокирует по B", r.returncode == 2, f"exit {r.returncode}")
		check("чередование: все планы закрыты", not plan_file.exists())

		# потеря состояния (рестарт сессии) — тихий пропуск
		r = post_unica({"executionToken": "tok-ghost"},
		               state_dir=str(tmp_path / "empty-state"))
		check("исполнение без состояния: тихий пропуск",
		      r.returncode == 0 and not r.stderr.strip(), r.stderr[:120])

		# TTL: просроченный план не проверяется
		post_unica({"at": "main:CommonModule.М", "ops": plan_ops("Если ПЛОХО_Т Тогда")})
		data = json.loads(plan_file.read_text(encoding="utf-8"))
		data["plans"][0]["expires_ts"] = 1
		plan_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
		bsl_disk.write_text("Если ПЛОХО_Т Тогда\n", encoding="utf-8")
		r = post_unica({"executionToken": "tok-ttl"})
		check("TTL: исполнение просроченного плана — тихий пропуск",
		      r.returncode == 0 and not r.stderr.strip(), r.stderr[:120])

		# session_clean выметает старые файлы бриджа вместе с прочими
		sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
		from session_clean import purge_old_session_state  # noqa: E402
		stale_time = time.time() - 8 * 86400
		os.utime(plan_file, (stale_time, stale_time))
		os.environ["ZCODE_1C_STATE_DIR"] = state  # чистка — в тестовое состояние
		try:
			removed = purge_old_session_state()
		finally:
			os.environ.pop("ZCODE_1C_STATE_DIR", None)
		check("session_clean: старый файл бриджа выметается",
		      removed >= 1 and not plan_file.exists(), f"removed={removed}")

		# не-apply юника-вызовы мимо бриджа
		r = post_unica({"at": "main:CommonModule.М"}, tool="mcp__unica__unica_view")
		check("не-apply юника-вызов мимо бриджа",
		      r.returncode == 0 and not r.stderr.strip(), r.stderr[:120])

		# ops вне тел BSL-модулей (at не .Body) — план не сохраняется
		r = post_unica({"at": "main:DataProcessor.М", "ops": [
			{"op": "meta.edit", "args": {"at": "main:DataProcessor.М.Form",
			                             "text": "Если ПЛОХО Тогда"}}]})
		check("ops вне .Body: план не сохраняется",
		      r.returncode == 0 and not plan_file.exists(), r.stderr[:120])

		# неоднозначность: текст в двух файлах — проверяется объединение
		second = tree / "src" / "cf" / "M" / "Второй.bsl"
		shared = "Если ПЛОХО_ОБЩ Тогда"
		post_unica({"at": "main:CommonModule.М", "ops": plan_ops(shared)})
		bsl_disk.write_text("Чисто\n" + shared + "\n", encoding="utf-8")
		second.write_text("Чисто\n" + shared + "\n", encoding="utf-8")
		r = post_unica({"executionToken": "tok-both"})
		check("неоднозначность: объединение файлов — блок", r.returncode == 2,
		      f"exit {r.returncode}")

		# завёрнутые args клиента MCP разворачиваются
		r = post_unica({"args": {"at": "main:CommonModule.М",
		                         "ops": plan_ops("Если ПЛОХО_W Тогда")}})
		check("завёрнутые args разворачиваются", plan_file.is_file(), str(plan_file))

		return summary()


if __name__ == "__main__":
	sys.exit(main())
