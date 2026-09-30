#!/usr/bin/env python3
"""Тесты ib_lock.py (модуль) и session_clean.py (SessionStart-уборка).

Запуск: python3 tests/test_ib_lock.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
from _lib import check, run_hook, summary  # noqa: E402

import ib_lock  # noqa: E402


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		state = str(Path(tmp) / "state")
		os.environ["ZCODE_1C_STATE_DIR"] = state
		cwd = str(Path(tmp))
		CONN = 'Srvr="host";Ref="erp";'

		# ── модуль: захват, продление, конфликт, освобождение ──
		status, _ = ib_lock.acquire_base_lock("/tree/a", CONN, "build", "s1")
		check("захват свободной базы", status == "ok", status)
		status, _ = ib_lock.acquire_base_lock("/tree/a", CONN, "load", "s1")
		check("продление своим деревом", status == "ok", status)
		status, holder = ib_lock.acquire_base_lock("/tree/b", CONN, "build", "s2")
		check("чужое дерево — busy", status == "busy", status)
		check("описание держателя называет дерево",
		      "/tree/a" in ib_lock.describe_holder(holder), str(holder))

		# ── файловая база: относительный путь даёт каждому дереву свой ключ ──
		status, _ = ib_lock.acquire_base_lock("/tree/b", "File=build/ib", "build", "s2")
		check("файловая база другого дерева — свой ключ", status == "ok", status)

		# ── connection из v8project: локальный оверлей перекрывает основной ──
		proj = Path(tmp) / "proj"
		proj.mkdir()
		(proj / "v8project.yaml").write_text(
			'infobase:\n  connection: Srvr="base";Ref="main";\n', encoding="utf-8")
		(proj / "v8project.local.yaml").write_text(
			'infobase:\n  connection: Srvr="base";Ref="wt";\n', encoding="utf-8")
		check("local-оверлей перекрывает связь",
		      ib_lock.resolve_infobase_connection(str(proj)) == 'Srvr="base";Ref="wt";',
		      str(ib_lock.resolve_infobase_connection(str(proj))))

		# ── освобождение: чужой не отпускает, свой отпускает ──
		ib_lock.release_base_lock("/tree/b", CONN, "s2")  # чужая сессия
		status, _ = ib_lock.acquire_base_lock("/tree/c", CONN, "build", "s3")
		check("чужой не отпустил (busy)", status == "busy", status)
		ib_lock.release_base_lock("/tree/a", CONN, "s1")
		status, _ = ib_lock.acquire_base_lock("/tree/c", CONN, "build", "s3")
		check("после освобождения своим — ок", status == "ok", status)

		# ── просроченный замок мёртв ──
		ib_lock.acquire_base_lock("/tree/old", 'Srvr="h";Ref="old";', "build", "s-old", ttl_s=-1)
		status, _ = ib_lock.acquire_base_lock("/tree/new", 'Srvr="h";Ref="old";', "build", "s-new")
		check("просроченный замок не держит базу", status == "ok", status)

		# ── session_clean: выметает просроченные замки и старое состояние ──
		ib_lock.acquire_base_lock("/tree/expired", 'Srvr="h";Ref="gone";', "build", "s-x", ttl_s=-1)
		locks_dir = Path(state) / "state" / "1c" / "ib-locks"
		doc_dir = Path(state) / "state" / "1c" / "doc-gate"
		doc_dir.mkdir(parents=True, exist_ok=True)
		old_state = doc_dir / "dead-session.json"
		old_state.write_text('{"verified": true}', encoding="utf-8")
		fresh_state = doc_dir / "live-session.json"
		fresh_state.write_text('{"verified": true}', encoding="utf-8")
		old_ts = time.time() - 8 * 86400
		os.utime(old_state, (old_ts, old_ts))
		r = run_hook("session_clean.py", {
			"hook_event_name": "SessionStart", "source": "startup",
			"session_id": "s-new", "cwd": cwd}, state, cwd)
		check("session_clean выходит 0", r.returncode == 0, f"exit {r.returncode}")
		check("старое состояние выметено", not old_state.exists(), "файл жив")
		check("свежее состояние не тронуто", fresh_state.exists(), "файл удалён")
		expired_key = ib_lock.lock_key('Srvr="h";Ref="gone";', "/tree/expired")
		check("просроченный замок выметен", not (locks_dir / f"{expired_key}.json").exists(),
		      "замок жив")
		fresh_key = ib_lock.lock_key("File=build/ib", "/tree/b")
		check("живой замок не тронут", (locks_dir / f"{fresh_key}.json").exists(), "замок удалён")

	return summary()


if __name__ == "__main__":
	sys.exit(main())
