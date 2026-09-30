#!/usr/bin/env python3
"""session_clean.py — уборка состояния на старте сессии (SessionStart).

В ZCode нет события остановки сессии (донор omp отпускал замки на
session_shutdown), поэтому SessionStart каждой новой сессии выметает:

  - просроченные замки инфобаз (state/1c/ib-locks/, TTL истёк);
  - файлы состояния гейтов старше CLEAN_AFTER_DAYS (doc-gate, retry):
    забытые флаги сверки и счётчики ретраев мёртвых сессий.

Хук всегда выходит 0: уборка не должна ронять сессию. События SessionStart
приходят с source: startup | resume | clear | compact — чистим при любом.
"""
import sys
import time
from pathlib import Path

from contour_common import read_event, session_state_dir
from ib_lock import purge_stale_locks

CLEAN_AFTER_DAYS = 7


def purge_old_session_state() -> int:
	removed = 0
	cutoff = time.time() - CLEAN_AFTER_DAYS * 86400
	for name in ("doc-gate", "retry"):
		directory = session_state_dir(name)
		if not directory.is_dir():
			continue
		for path in directory.glob("*.json"):
			try:
				if path.stat().st_mtime < cutoff:
					path.unlink()
					removed += 1
			except OSError:
				pass
	return removed


def main() -> int:
	read_event()  # вход парсить не обязательно — чистим по возрасту/TTL
	try:
		locks = purge_stale_locks()
	except Exception:
		locks = 0  # noqa: TRY - уборка best-effort, сессию не роняем
	try:
		stale = purge_old_session_state()
	except Exception:
		stale = 0
	if locks or stale:
		sys.stderr.write(
			f"1c-zcode: выметено замков {locks}, файлов состояния {stale}\n"
		)
	return 0


if __name__ == "__main__":
	sys.exit(main())
