#!/usr/bin/env python3
"""ib_lock.py — замок на информационную базу (порт lib/ib-lock.ts дона 1c-omp).

Серверная база одна на все деревья проекта: отдельную на каждый ворктри не
поднять. Изоляции там быть не может, поэтому единственный достижимый
инвариант — «в один момент с базой работает одно дерево». Файловая база
изолируется сама: `File=build/ib` — путь относительный, у каждого дерева
свой, ключи замка разные, и замок не мешает.

Отличие от дона: в ZCode хуки — одноразовые процессы, pid держателя умирает
сразу после захвата, поэтому живость замка определяется только TTL (20 мин),
а не проверкой pid. Держатель идентифицируется парой (дерево, session_id);
тот же держатель продлевает свой замок. SessionStart-хук выметает
просроченные замки, событие остановки сессии в ZCode нет.
"""
import hashlib
import json
import os
import time
from pathlib import Path

from contour_common import session_state_dir

DEFAULT_TTL_S = 20 * 60

LOCK_FIELDS = ("connection", "tree", "session_id", "operation", "started_at", "expires_ts")


def _connection_from_file(path: Path):
	"""`infobase.connection` (0.12) или `infobases.origin.connection` (0.13)."""
	if not path.is_file():
		return None
	try:
		lines = path.read_text(encoding="utf-8").splitlines()
	except OSError:
		return None
	top = None
	in_origin = False
	for line in lines:
		if line[:1] not in (" ", "\t"):
			top = line.split(":", 1)[0].strip()
			in_origin = False
			continue
		stripped = line.strip()
		if top == "infobases" and stripped.rstrip(":").strip() == "origin":
			in_origin = True
			continue
		if not stripped.startswith("connection:"):
			continue
		if top == "infobase" or (top == "infobases" and in_origin):
			value = stripped[len("connection:"):].strip().strip("'\"")
			if value:
				return value
	return None


def resolve_infobase_connection(tree: str):
	"""Строка подключения к базе проекта: локальный оверлей перекрывает основной."""
	tree_path = Path(tree)
	return (
		_connection_from_file(tree_path / "v8project.local.yaml")
		or _connection_from_file(tree_path / "v8project.yaml")
	)


def lock_key(connection: str, tree: str) -> str:
	"""Ключ замка: у файловой базы относительный путь разворачивается от дерева."""
	norm = "".join(connection.lower().split())
	if norm.startswith("file="):
		file_part = norm[len("file="):]
		if not file_part.startswith("/"):
			file_part = str((Path(tree) / file_part).resolve())
		norm = f"file={file_part}"
	return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def _lock_path(connection: str, tree: str) -> Path:
	return session_state_dir("ib-locks") / f"{lock_key(connection, tree)}.json"


def _read_lock(path: Path):
	try:
		lock = json.loads(path.read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError):
		return None
	return lock if isinstance(lock, dict) else None


def _is_stale(lock: dict) -> bool:
	"""Замок мёртв: истёк срок (pid-проверка дона здесь неприменима — см. модуль)."""
	try:
		return float(lock.get("expires_ts", 0)) <= time.time()
	except (TypeError, ValueError):
		return True


def acquire_base_lock(tree: str, connection: str, operation: str, sid: str,
                      ttl_s: int = DEFAULT_TTL_S):
	"""Берёт замок. ('ok', None) — захвачен/продлён; ('busy', lock) — держит другой."""
	path = _lock_path(connection, tree)
	existing = _read_lock(path)
	if existing and not _is_stale(existing):
		if existing.get("tree") != tree:
			return "busy", existing
	path.parent.mkdir(parents=True, exist_ok=True)
	lock = {
		"connection": connection,
		"tree": tree,
		"session_id": sid,
		"operation": operation,
		"started_at": existing.get("started_at") if existing and existing.get("tree") == tree
		else datetime_iso(),
		"expires_ts": time.time() + ttl_s,
	}
	try:
		path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
	except OSError:
		# нет доступа к каталогу состояния — из-за этого работу не блокируем
		pass
	return "ok", None


def release_base_lock(tree: str, connection: str, sid: str) -> None:
	"""Отпускает замок, если он принадлежит этому дереву (и сессии)."""
	path = _lock_path(connection, tree)
	lock = _read_lock(path)
	if lock and lock.get("tree") == tree:
		if lock.get("session_id") in (sid, None):
			try:
				path.unlink(missing_ok=True)
			except OSError:
				pass  # истечёт по TTL


def purge_stale_locks() -> int:
	"""Выметает просроченные замки (вызывается SessionStart-хуком). Возвращает счёт."""
	directory = session_state_dir("ib-locks")
	if not directory.is_dir():
		return 0
	removed = 0
	for path in directory.glob("*.json"):
		if _is_stale(_read_lock(path) or {}):
			try:
				path.unlink()
				removed += 1
			except OSError:
				pass
	return removed


def describe_holder(lock: dict) -> str:
	"""Описание держателя для сообщения о блокировке."""
	started = str(lock.get("started_at") or "?")
	operation = str(lock.get("operation") or "?")
	sid = str(lock.get("session_id") or "?")
	return f"{lock.get('tree')} — операция {operation}, с {started}, сессия {sid}"


def datetime_iso() -> str:
	from datetime import datetime, timezone
	return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
	# Ручная проверка без 1С: захват, повторный захват, конфликт, чистка.
	import shutil
	import tempfile

	with tempfile.TemporaryDirectory() as tmp:
		os.environ["ZCODE_1C_STATE_DIR"] = tmp
		status, _ = acquire_base_lock("/tree/a", "Srvr=host;Ref=erp;", "build", "s1")
		print("первый захват:", status)
		status, _ = acquire_base_lock("/tree/a", "Srvr=host;Ref=erp;", "load", "s1")
		print("продление своим деревом:", status)
		status, holder = acquire_base_lock("/tree/b", "Srvr=host;Ref=erp;", "build", "s2")
		print("чужое дерево:", status, "—", describe_holder(holder) if holder else "")
		status, _ = acquire_base_lock("/tree/b", "File=build/ib", "build", "s2")
		print("файловая база другого дерева (другой ключ):", status)
		release_base_lock("/tree/a", "Srvr=host;Ref=erp;", "s1")
		status, _ = acquire_base_lock("/tree/b", "Srvr=host;Ref=erp;", "build", "s2")
		print("после освобождения:", status)
		print("выметено просроченных:", purge_stale_locks())
