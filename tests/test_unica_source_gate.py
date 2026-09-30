#!/usr/bin/env python3
"""Тесты гейта unica_source_gate.py: stdin JSON → вердикт (exit 0/2).

Гейт гоняется как процесс — ровно так, как его вызывает ZCode; журнал
перенаправляется во временный каталог через ZCODE_1C_STATE_DIR. Платформа
и база 1С не нужны: покрываются маршрутизация правки, эскалации, bash,
инварианты пересборки, ворктри и замок на инфобазу.

Запуск: python3 tests/test_unica_source_gate.py
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import audit_entries, check, run_hook, summary  # noqa: E402

V8PROJECT = """\
workPath: src
infobase:
  connection: Srvr="host";Ref="erp";
source-set:
  - name: main
    type: CONFIGURATION
    path: src/cf
  - name: Тесты
    type: EXTENSION
    path: src/cfe/Тесты
"""


def make_tree(tmp: Path) -> Path:
	tree = tmp / "tree"
	(tree / "src" / "cf" / "CommonModules").mkdir(parents=True)
	(tree / "src" / "docs").mkdir(parents=True)
	(tree / "v8project.yaml").write_text(V8PROJECT, encoding="utf-8")
	return tree


def busy_lock(state_dir: Path, tree: str, other_tree: str) -> None:
	"""Замок на связь дерева tree, удерживаемый other_tree (не просрочен)."""
	from ib_lock import acquire_base_lock  # noqa: PLC0415
	acquire_base_lock(other_tree, 'Srvr="host";Ref="erp";', "build", "s-other")


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		tmp_path = Path(tmp)
		state = str(tmp_path / "state")
		tree = make_tree(tmp_path)
		cwd = str(tree)
		bsl = "src/cf/CommonModules/ОбщегоНазначения.bsl"

		def pre(tool, tool_input, sid="s1", cwd_override=None):
			return run_hook("unica_source_gate.py", {
				"hook_event_name": "PreToolUse", "session_id": sid,
				"tool_name": tool, "tool_input": tool_input,
				"cwd": cwd_override or cwd}, state, cwd)

		# ── write исходника → блок с маршрутизацией на unica.code.patch ──
		r = pre("Write", {"file_path": bsl, "content": ""})
		check("write .bsl в src/cf блокируется", r.returncode == 2, f"exit {r.returncode}")
		check("причина называет unica.code.patch", "unica.code.patch" in r.stderr, r.stderr[:200])
		check("блок записан в журнал", any(
			e.get("rule") == "unica-source-gate" and e.get("decision") == "blocked"
			for e in audit_entries(state, "unica-source-gate")), "нет записи")

		# ── абсолютный путь исходника → блок ──
		r = pre("Edit", {"file_path": str(tree / bsl), "old_string": "a", "new_string": "b"},
		        cwd_override="/tmp")
		check("edit .bsl по абсолютному пути блокируется", r.returncode == 2, f"exit {r.returncode}")

		# ── AGENTS.md внутри src/ — документация, не исходник ──
		r = pre("Write", {"file_path": "src/cf/AGENTS.md", "content": ""})
		check("AGENTS.md в src/ не блокируется", r.returncode == 0, f"exit {r.returncode}")

		# ── файл вне src/tests — не предмет гейта ──
		r = pre("Write", {"file_path": "src/docs/readme.md", "content": ""})
		check("файл вне исходников 1С проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── эскалация: allowlist в дереве разрешает правку и журналируется ──
		esc = tree / ".zcode"
		esc.mkdir(exist_ok=True)
		(esc / "unica-gate-escalations.txt").write_text(
			f"# разовая прямая правка\n{bsl}\n", encoding="utf-8")
		r = pre("Write", {"file_path": bsl, "content": ""})
		check("эскалация из allowlist разрешает правку", r.returncode == 0, f"exit {r.returncode}")
		check("разрешение записано в журнал (confirmed)", any(
			e.get("decision") == "confirmed"
			for e in audit_entries(state, "unica-source-gate")), "нет confirmed")

		# ── правка самого allowlist'а журналируется ──
		r = pre("Edit", {"file_path": str(esc / "unica-gate-escalations.txt"),
		                 "old_string": "a", "new_string": "b"})
		check("правка allowlist'а проходит", r.returncode == 0, f"exit {r.returncode}")
		check("правка allowlist'а в журнале", any(
			"изменён allowlist" in str(e.get("reason"))
			for e in audit_entries(state, "unica-source-gate")), "нет записи")

		# ── эскалация своего дерева не открывает чужое ──
		other = tmp_path / "other-tree"
		(other / "src" / "cfe" / "Расширение").mkdir(parents=True)
		r = pre("Write", {"file_path": str(other / "src/cfe/Расширение/Module.bsl"), "content": ""})
		check("запись чужого пути из-под своего cwd блокируется", r.returncode == 2,
		      f"exit {r.returncode}")

		# ── bash: sed -i и перенаправление в исходники ──
		r = pre("Bash", {"command": f"sed -i 's/a/b/' {bsl}"})
		check("sed -i по исходнику блокируется", r.returncode == 2, f"exit {r.returncode}")
		r = pre("Bash", {"command": "echo x >> src/cf/CommonModules/Модуль.bsl"})
		check("перенаправление в исходник блокируется", r.returncode == 2, f"exit {r.returncode}")
		r = pre("Bash", {"command": "echo x >> build/out/log.txt"})
		check("перенаправление вне исходников проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── прочие инструменты и неразборчивый вход не задеваются ──
		r = pre("Read", {"file_path": bsl})
		check("read не блокируется", r.returncode == 0, f"exit {r.returncode}")
		r = run_hook("unica_source_gate.py", {}, state, cwd)
		check("пустой stdin проходит молча", r.returncode == 0, f"exit {r.returncode}")

		# ── инвариант пересборки: расширение без fullRebuild ──
		r = pre("mcp__unica__runtime_execute",
		        {"args": {"operation": "build", "sourceSet": "Тесты", "dryRun": False, "cwd": cwd}})
		check("сборка расширения без fullRebuild блокируется", r.returncode == 2, f"exit {r.returncode}")
		check("причина называет fullRebuild", "fullRebuild" in r.stderr, r.stderr[:200])

		r = pre("mcp__unica__runtime_execute",
		        {"args": {"operation": "build", "sourceSet": "Тесты",
		                  "fullRebuild": True, "dryRun": False, "cwd": cwd}})
		check("сборка расширения с fullRebuild проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── инвариант пересборки: main с fullRebuild запрещён ──
		r = pre("mcp__unica__runtime_execute",
		        {"args": {"operation": "build", "sourceSet": "main",
		                  "fullRebuild": True, "dryRun": False, "cwd": cwd}})
		check("полная пересборка main блокируется", r.returncode == 2, f"exit {r.returncode}")
		r = pre("mcp__unica__runtime_execute",
		        {"args": {"operation": "build", "sourceSet": "main", "dryRun": False, "cwd": cwd}})
		check("инкрементальная загрузка main проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── чужой сервер MCP не задевается ──
		r = pre("mcp__1c-db__execute_query", {"query": "ВЫБРАТЬ 1"})
		check("вызов не-yunica MCP проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── ворктри без инициализации ──
		wt = tmp_path / "wt"
		wt.mkdir()
		(wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt\n", encoding="utf-8")
		r = pre("mcp__unica__runtime_execute",
		        {"args": {"operation": "load", "sourceSet": "main", "cwd": str(wt)}})
		check("unica в неинициализированном ворктри блокируется", r.returncode == 2, f"exit {r.returncode}")
		check("причина называет init_worktree", "init_worktree" in r.stderr, r.stderr[:200])
		(wt / "v8project.local.yaml").write_text("infobase:\n  user: bot\n", encoding="utf-8")
		(wt / "build" / "tools").mkdir(parents=True)
		r = pre("mcp__unica__runtime_execute",
		        {"args": {"operation": "load", "sourceSet": "main", "cwd": str(wt)}})
		check("инициализированный ворктри проходит", r.returncode == 0, f"exit {r.returncode}")

		# ── замок на инфобазу: база занята другим деревом ──
		sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
		import os
		os.environ["ZCODE_1C_STATE_DIR"] = state
		from ib_lock import acquire_base_lock, resolve_infobase_connection  # noqa: E402
		from contour_common import session_state_dir  # noqa: E402
		connection = resolve_infobase_connection(cwd)
		check("связь базы прочитана из v8project.yaml", bool(connection), "пусто")
		# предыдущие успешные вызовы сами захватывали замок — чистим перед сценарием
		for lock_file in (session_state_dir("ib-locks")).glob("*.json"):
			lock_file.unlink()
		status, _ = acquire_base_lock(str(tmp_path / "derzhit"), connection, "build", "s-other")
		check("замок захвачен деревом-держателем", status == "ok", status)
		r = pre("mcp__unica__runtime_execute",
		        {"args": {"operation": "build", "sourceSet": "main", "dryRun": False, "cwd": cwd}})
		check("операция на занятой базе блокируется", r.returncode == 2, f"exit {r.returncode}")
		check("причина описывает держателя", "База занята" in r.stderr, r.stderr[:200])
		# держатель закончил — отпускаем его замок
		from ib_lock import release_base_lock  # noqa: E402
		release_base_lock(str(tmp_path / "derzhit"), connection, "s-other")

		# ── PostToolUse освобождает замок своего дерева ──
		status, _ = acquire_base_lock(cwd, connection, "build", "s1")
		check("своё дерево продлило замок", status == "ok", status)
		r = run_hook("unica_source_gate.py", {
			"hook_event_name": "PostToolUse", "session_id": "s1",
			"tool_name": "mcp__unica__runtime_execute",
			"tool_input": {"args": {"operation": "build", "sourceSet": "main", "cwd": cwd}},
			"cwd": cwd}, state, cwd)
		check("PostToolUse прошёл", r.returncode == 0, f"exit {r.returncode}")
		time.sleep(0.05)
		r = pre("mcp__unica__runtime_execute",
		        {"args": {"operation": "build", "sourceSet": "main", "dryRun": False, "cwd": cwd}})
		check("после освобождения замка операция проходит", r.returncode == 0, f"exit {r.returncode}")

	return summary()


if __name__ == "__main__":
	sys.exit(main())
