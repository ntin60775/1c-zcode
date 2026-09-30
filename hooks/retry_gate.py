#!/usr/bin/env python3
"""retry_gate.py — дисциплина ретраев: третий идентичный повтор упавшей
bash-команды блокируется (порт retry-gate.ts дона 1c-omp).

После двух подряд ошибок одной и той же команды третий идентичный вызов
блокируется с требованием диагностики. Причина C постмортема тикета 09:
3–4 идентичных ретрая exit 137 до какой-либо диагностики.

Хуки stateless: счётчик подряд идущих ошибок живёт в
state/1c/retry/<session_id>.json (ключ — sha1 команды, для сообщения
хранится урезанный текст). PostToolUseFailure наращивает, успешный
PostToolUse сбрасывает, PreToolUse проверяет. SessionStart выметает
файлы старше 7 дней.

Журнал: rule-audit.jsonl, rule: "retry-discipline".
"""
import hashlib
import json
import sys
from pathlib import Path

from contour_common import read_event, session_id, session_state_dir

RULE = "retry-discipline"
MAX_CONSECUTIVE = 2


def _state_path(sid: str) -> Path:
	return session_state_dir("retry") / f"{sid}.json"


def load_counts(sid: str) -> dict:
	try:
		data = json.loads(_state_path(sid).read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError):
		return {}
	return data.get("commands", {}) if isinstance(data, dict) else {}


def save_counts(sid: str, counts: dict) -> None:
	try:
		_state_path(sid).parent.mkdir(parents=True, exist_ok=True)
		_state_path(sid).write_text(
			json.dumps({"commands": counts}, ensure_ascii=False), encoding="utf-8")
	except OSError:
		pass


def cmd_key(command: str) -> str:
	return hashlib.sha1(command.encode("utf-8")).hexdigest()


def bash_command(event: dict):
	tool_input = event.get("tool_input")
	if not isinstance(tool_input, dict):
		return None
	command = str(tool_input.get("command") or "")
	return command or None


def handle_failure(event: dict) -> int:
	"""PostToolUseFailure: ошибка bash — счётчик команды растёт."""
	command = bash_command(event)
	if not command:
		return 0
	sid = session_id(event)
	counts = load_counts(sid)
	entry = counts.get(cmd_key(command), {"cmd": command[:200], "count": 0})
	entry["count"] += 1
	counts[cmd_key(command)] = entry
	save_counts(sid, counts)
	return 0


def handle_success(event: dict) -> int:
	"""PostToolUse: успешный bash — счётчик команды сброшен."""
	command = bash_command(event)
	if not command:
		return 0
	sid = session_id(event)
	counts = load_counts(sid)
	if counts.pop(cmd_key(command), None) is not None:
		save_counts(sid, counts)
	return 0


def handle_pre(event: dict) -> int:
	"""PreToolUse: третий идентичный повтор после двух ошибок — блок."""
	command = bash_command(event)
	if not command:
		return 0
	sid = session_id(event)
	entry = load_counts(sid).get(cmd_key(command))
	if not entry or entry.get("count", 0) < MAX_CONSECUTIVE:
		return 0
	count = entry["count"]
	from contour_common import audit
	audit(RULE, command[:200], f"третий идентичный повтор после {count} ошибок подряд")
	sys.stderr.write("\n".join([
		"Третий идентичный повтор упавшей команды заблокирован (retry-discipline).",
		"",
		f"Команда уже падала {count} раза подряд. Повтор без изменения условий запрещён.",
		"",
		"Дальше — развилка:",
		"  1. Диагностика: прочитать вывод ошибки целиком, сформулировать гипотезу.",
		"  2. Смена условий: другое дерево/инструмент/путь, исправить окружение.",
		"  3. Эскалация: сообщить пользователю с фактами (команда, вывод, что пробовал).",
		"",
		"Измени команду (хотя бы диагностической обвязкой) — счётчик идентичности сбросится.",
	]) + "\n")
	return 2


def main() -> int:
	event = read_event()
	if not event:
		return 0
	event_name = str(event.get("hook_event_name") or "")
	if event_name == "PostToolUseFailure":
		return handle_failure(event)
	if event_name == "PostToolUse":
		return handle_success(event)
	return handle_pre(event)


if __name__ == "__main__":
	sys.exit(main())
