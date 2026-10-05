#!/usr/bin/env python3
"""wire_config.py — идемпотентное подключение контура в <repo>/.zcode/config.json.

Выполняется раннером sot-zcode-marketplace (post_update) и навыком
1c-project-bootstrap. Делает две вещи:

1. hooks: переносит определения из вендоренного .zcode/hooks/hooks.json
   (плагинная форма с ${CLAUDE_PLUGIN_ROOT}) в config.json (workspace-форма,
   пути относительно корня проекта) и ставит hooks.enabled = true. Наши
   предыдущие записи (args содержат .zcode/hooks/) заменяются, чужие
   hook-записи не трогаются.
2. mcp: вливает фрагменты из scripts/mcp_fragments.json (шаблоны 1c-testpilot
   и 1c-db) в mcp.servers, подставляя url toolkit из .zcode/1c/contour.json
   (по умолчанию http://127.0.0.1:6003/mcp).

Работает из обеих раскладок: в репо плагина (hooks/hooks.json, scripts/)
и вендоренно в проекте (.zcode/hooks/hooks.json, .zcode/1c/scripts/) —
плагин-корень вычисляется от местоположения этого файла.

Третья вещь — env-настройки контура: .zcode/1c/contour.json инициализируется
каркасом по факту машины (1c_db.url, 1c.publish.ibsrv, платформа, venv
testpilot). Существующие значения не затираются — дозаполняются только
отсутствующие ключи; битый файл не трогается. --check contour.json не пишет
и не проверяет (его контракт — только config.json).

Режим --check: exit 1, если config.json не совпадает с ожидаемой разводкой
(для CI и /1c-doctor). Секретов не пишет: креды тестовых баз живут в
.zcode/testpilot/profiles.yaml через password_env.

Запуск: python3 wire_config.py <project-root> [--check]
"""
import json
import sys
from pathlib import Path

CONFIG = ".zcode/config.json"
CONTOUR = ".zcode/1c/contour.json"
DEFAULT_DB_URL = "http://127.0.0.1:6003/mcp"
HOOK_EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "SessionStart",
               "UserPromptSubmit", "PermissionRequest", "Stop")


def load_json(path: Path, default):
	try:
		return json.loads(path.read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError):
		return default


def contour_of(root: Path) -> dict:
	data = load_json(root / CONTOUR, {})
	return data if isinstance(data, dict) else {}


# ── contour.json: каркас env-настроек ────────────────────────────────────────

def scaffold_contour(root: Path) -> dict:
	"""Каркас env-настроек по факту машины; что не нашли — ключ не пишем."""
	scaffold = {
		"1c_db": {"url": DEFAULT_DB_URL},
		"1c": {
			# ibsrv всегда живёт в distrobox-контейнере (на хосте его нет):
			# auto — искать по живым контейнерам при публикации
			"publish": {"ibsrv": "distrobox:auto"},
		},
	}
	platforms = sorted(str(p) for p in Path("/opt/1cv8/x86_64").glob("*/1cv8")) \
		if Path("/opt/1cv8/x86_64").is_dir() else []
	if len(platforms) == 1:
		scaffold["1c"]["platform"] = {"path": platforms[0]}
	tp_python = Path.home() / ".local" / "venvs" / "1c-testpilot" / "bin" / "python"
	if tp_python.is_file():
		scaffold["1c"]["testpilot"] = {"python": str(tp_python)}
	_ = root  # root оставлен в сигнатуре: каркас может вырасти в проектные ключи
	return scaffold


def fill_missing(node: dict, extra: dict) -> list:
	"""Дописать отсутствующие ключи (рекурсивно); существующие не трогаем."""
	added = []
	for key, value in extra.items():
		if key not in node:
			node[key] = value
			added.append(key)
		elif isinstance(node[key], dict) and isinstance(value, dict):
			added += fill_missing(node[key], value)
	return added


def ensure_contour(root: Path) -> str:
	"""Инициализация/дозаполнение contour.json; текст для отчёта."""
	path = root / CONTOUR
	scaffold = scaffold_contour(root)
	if path.is_file():
		existing = load_json(path, None)
		if not isinstance(existing, dict):
			return f"contour.json битый, не трогаю: {path}"
		added = fill_missing(existing, scaffold)
		if added:
			path.write_text(json.dumps(existing, ensure_ascii=False, indent="\t") + "\n",
			                encoding="utf-8")
			return f"дозаполнен contour.json ({', '.join(added)}): {path}"
		return f"contour.json полон: {path}"
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(scaffold, ensure_ascii=False, indent="\t") + "\n",
	                encoding="utf-8")
	return f"создан каркас env-настроек: {path}"


# ── hooks ────────────────────────────────────────────────────────────────────

def read_hook_definitions(root: Path) -> dict:
	"""hooks.json (вендоренная раскладка, откат на раскладку репо) → dict событий."""
	for candidate in (root / ".zcode" / "hooks" / "hooks.json", root / "hooks" / "hooks.json"):
		data = load_json(candidate, {})
		if isinstance(data, dict) and data.get("hooks"):
			return data["hooks"]
	return {}


def plugin_hooks_to_config(root: Path) -> dict:
	"""Определения хуков → workspace-форма (пути .zcode/hooks/…, без макросов)."""
	result = {}
	for event, entries in read_hook_definitions(root).items():
		if event not in HOOK_EVENTS:
			continue
		rewired = []
		for entry in entries or []:
			hooks_out = []
			for hook in entry.get("hooks") or []:
				args = []
				for arg in hook.get("args") or []:
					text = str(arg)
					if "${CLAUDE_PLUGIN_ROOT}/hooks/" in text:
						text = ".zcode/hooks/" + text.split("/hooks/", 1)[1]
					args.append(text)
				wired = {k: v for k, v in hook.items() if k != "args"}
				wired["args"] = args
				hooks_out.append(wired)
			if hooks_out:
				rewired.append({"matcher": entry.get("matcher") or ".*", "hooks": hooks_out})
		if rewired:
			result[event] = rewired
	return result


def merge_hooks(config: dict, wired: dict) -> None:
	hooks = config.setdefault("hooks", {})
	hooks["enabled"] = True
	# Workspace-схема ZCode читает конфиг-хуки только из hooks.events.<Event>;
	# записи напрямую в hooks.<Event> рантайм не видит — переносим их в events.
	legacy = {ev: hooks.pop(ev) for ev in list(hooks) if ev in HOOK_EVENTS}
	events = hooks.setdefault("events", {})
	for event in set(wired) | set(legacy):
		pool = list(legacy.get(event) or []) + list(events.get(event) or [])
		foreign = []
		for entry in pool:
			args = [a for h in entry.get("hooks") or [] for a in (h.get("args") or [])]
			if any(isinstance(a, str) and ".zcode/hooks/" in a for a in args):
				continue  # наша прежняя запись — заменяем
			foreign.append(entry)
		ours = wired.get(event) or []
		if foreign or ours:
			events[event] = foreign + ours


# ── mcp ──────────────────────────────────────────────────────────────────────

def merge_mcp(config: dict, root: Path, plugin_root: Path) -> None:
	fragments = load_json(plugin_root / "scripts" / "mcp_fragments.json", {})
	if not isinstance(fragments, dict) or not fragments:
		return
	url = (contour_of(root).get("1c_db") or {}).get("url") or DEFAULT_DB_URL
	servers = config.setdefault("mcp", {}).setdefault("servers", {})
	for name, fragment in fragments.items():
		server = json.loads(json.dumps(fragment))  # копия шаблона
		if isinstance(server, dict) and server.get("url") == "{{1c_db_url}}":
			server["url"] = url
		servers[name] = server


# ── входная точка ────────────────────────────────────────────────────────────

def expected_config(root: Path, current: dict) -> dict:
	"""Конфиг, который получился бы при wire (на копии текущего)."""
	config = json.loads(json.dumps(current))
	merge_hooks(config, plugin_hooks_to_config(root))
	merge_mcp(config, root, Path(__file__).resolve().parent.parent)
	return config


def main() -> int:
	flags = {a for a in sys.argv[1:] if a.startswith("--")}
	positional = [a for a in sys.argv[1:] if not a.startswith("--")]
	root = Path(positional[0]).resolve() if positional else Path.cwd()

	if "--check" not in flags:
		print(ensure_contour(root))

	current = load_json(root / CONFIG, {})
	if not isinstance(current, dict):
		current = {}
	expect = expected_config(root, current)
	expect_dump = json.dumps(expect, sort_keys=True, ensure_ascii=False)

	if "--check" in flags:
		if json.dumps(current, sort_keys=True, ensure_ascii=False) == expect_dump:
			print("ок: контур подключён в .zcode/config.json")
			return 0
		print("РАСХОЖДЕНИЕ: .zcode/config.json не совпадает с ожидаемой разводкой")
		return 1

	config_path = root / CONFIG
	config_path.parent.mkdir(parents=True, exist_ok=True)
	config_path.write_text(json.dumps(expect, ensure_ascii=False, indent="\t") + "\n",
	                       encoding="utf-8")
	print(f"разведено: {config_path}")
	print("перезапусти сессию ZCode, чтобы hooks и MCP вступили в силу")
	return 0


if __name__ == "__main__":
	sys.exit(main())
