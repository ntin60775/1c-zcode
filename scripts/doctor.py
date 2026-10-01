#!/usr/bin/env python3
"""doctor.py — детерминированная проверка контура 1c-zcode в проекте.

Проверяет (без 1С и сети, кроме явных http-проб):
  1. Unica установлена и ровно один канал; версия >= UNICA_MIN.
  2. 1c-testpilot в PATH.
  3. Проектные файлы контура: v8project.yaml, .zcode/config.json (разводка
     через wire_config --check), .zcode/1c/contour.json, profiles.yaml.
  4. Стайл-чекер резолвится (env → PATH → вендоренный пак).
  5. Скилл bsp (БСП-пак) вендорен.
  6. Shadow-дубли контурных скиллов на пользовательском уровне.
  7. Замки/состояние гейтов (просроченные — предупреждение).

Выход: строки «OK/FAIL/WARN …», exit 0 — всё зелёное, 1 — есть FAIL.

Запуск: python3 doctor.py [project-root]
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# contour hooks доступны в обеих раскладках: репо (scripts/../hooks) и
# вендоренной (.zcode/1c/scripts/../../hooks)
for _rel in ("../../hooks", "../hooks"):
	_p = (Path(__file__).resolve().parent / _rel).resolve()
	if (_p / "contour_common.py").is_file():
		sys.path.insert(0, str(_p))

from contour_common import contour_config

UNICA_MIN = "0.13.0"
CONTOUR_SKILLS = {"1c-contour", "1c-test-contour", "1c-db-data",
                  "1c-project-bootstrap", "bsp"}
USER_SKILL_ROOTS = (Path.home() / ".zcode" / "skills", Path.home() / ".agents" / "skills")


def parse_version(text: str):
	parts = []
	for chunk in text.replace("-", ".").split("."):
		if chunk.isdigit():
			parts.append(int(chunk))
		else:
			break
	return parts or [0]


def version_ge(a: str, b: str) -> bool:
	pa, pb = parse_version(a), parse_version(b)
	length = max(len(pa), len(pb))
	pa += [0] * (length - len(pa))
	pb += [0] * (length - len(pb))
	return pa >= pb


def main() -> int:
	root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
	issues = []
	warnings = []

	def ok(msg):
		print(f"OK   {msg}")

	def warn(msg):
		warnings.append(msg)
		print(f"WARN {msg}")

	def fail(msg):
		issues.append(msg)
		print(f"FAIL {msg}")

	# ── 1. Unica: установлена, один канал, версия ──
	installed = Path.home() / ".zcode" / "cli" / "plugins" / "installed_plugins.json"
	unica_entries = []
	try:
		registry = json.loads(installed.read_text(encoding="utf-8"))
		plugins = registry.get("plugins") if isinstance(registry, dict) else registry
		for meta in plugins or []:
			if isinstance(meta, dict) and str(meta.get("name") or "") == "unica":
				plugin_id = str(meta.get("id") or meta.get("name") or "unica")
				unica_entries.append((plugin_id, str(meta.get("version") or "0")))
	except (OSError, json.JSONDecodeError, AttributeError):
		warn("реестр установленных плагинов не читается — проверь unica руками")
	if not unica_entries:
		fail("unica не установлена (нужна unica@unica-next или unica@unica, "
		     f"версия >= {UNICA_MIN})")
	elif len(unica_entries) > 1:
		fail("unica установлена в нескольких каналах одновременно "
		     f"({', '.join(k for k, _ in unica_entries)}) — конфликт имени MCP, "
		     "оставь ровно один")
	else:
		key, version = unica_entries[0]
		if version_ge(version, UNICA_MIN):
			ok(f"unica {version} ({key}) >= {UNICA_MIN}")
		else:
			fail(f"unica {version} < минимальной {UNICA_MIN}")
	# exec-бит bootstrap: распаковщик кэша ZCode не сохраняет права —
	# без бита launch.sh падает «Permission denied», MCP не стартует
	for cache_root in sorted((Path.home() / ".zcode" / "cli" / "plugins" / "cache").glob("unica*/*/")):
		bootstrap = cache_root / "bootstrap" / "bin" / "linux-x64" / "unica-bootstrap"
		if bootstrap.is_file() and not os.access(str(bootstrap), os.X_OK):
			fail(f"нет exec-бита: {bootstrap} — MCP юники не стартует; "
			     f"лечится: chmod +x '{cache_root}/bootstrap/bin/'*/*/unica-bootstrap*")
		elif bootstrap.is_file():
			ok(f"bootstrap исполняем ({cache_root.name})")

	# живая проверка MCP: handshake + tools/list (без хоста и без 1С)
	if "--no-ping" not in sys.argv and unica_entries:
		ping_path = Path(__file__).resolve().parent / "unica_ping.py"
		try:
			done = subprocess.run(
				[sys.executable, str(ping_path), "--timeout=180", "--json"],
				capture_output=True, text=True, timeout=200)
		except subprocess.TimeoutExpired:
			warn("unica ping превысил общий таймаут — первый старт качает "
			     "рантаймы; повтори /1c-doctor позже")
		else:
			import json as _json
			try:
				result = _json.loads(done.stdout.strip() or "{}")
			except json.JSONDecodeError:
				result = {"code": 1, "message": f"ping вывел не-JSON: {done.stdout[:120]}"}
			if result.get("code") == 0:
				ok(f"MCP жив: {result.get('message')}")
			elif result.get("code") == 2:
				warn(f"unica ping: {result.get('message')}")
			else:
				fail(f"unica MCP: {result.get('message')}")

	# ── 2. 1c-testpilot ──
	if shutil.which("1c-testpilot"):
		ok("1c-testpilot в PATH")
	else:
		fail("1c-testpilot не найден в PATH (pipx install 1c-testpilot)")

	# ── 3. Проектные файлы ──
	if (root / "v8project.yaml").is_file():
		ok("v8project.yaml на месте")
		try:
			v8text = (root / "v8project.yaml").read_text(encoding="utf-8")
		except OSError:
			v8text = ""
		if re.search(r"(?m)^builder\s*:", v8text):
			fail("v8project.yaml: ключ builder отвергается схемой 0.13 — убери его, "
			     "исполнителя задавай per-operation providers (файловая — ibcmd, "
			     "серверная — designer); пока он там, unica.run не примет конфиг")
	if re.search(r"(?m)^infobases\s*:", v8text):
		ok("связь базы в форме 0.13 (infobases.origin)")
	elif re.search(r"(?m)^infobase\s*:", v8text):
		warn("v8project.yaml в форме 0.12 (infobase.connection) — 0.13 читает "
		     "legacy на миграции, но лучше перейти на infobases.origin.connection")
	# платформа: заявленная — из local-оверлея (машинное решение) или
	# v8project.yaml; не существует — скан /opt/1cv8/x86_64/*/: одна версия —
	# готовая строка оверлея, несколько — вопрос пользователю, ноль — FAIL
	local_platform = ""
	local_file = root / "v8project.local.yaml"
	if local_file.is_file():
		try:
			local_platform = next(
				(line.split("path:", 1)[1].strip().strip("'\"")
				 for line in local_file.read_text(encoding="utf-8").splitlines()
				 if line[:1] in (" ", "\t") and line.strip().startswith("path:")
				 and "/opt/1cv8/" in line),
				"")
		except OSError:
			pass
	declared = local_platform or next(
		(match.group(1) for match in
		 (re.search(r"(?m)^\s*path\s*:\s*['\"]?(/opt/1cv8/\S+?)['\"]?\s*$", v8text),)
		 if match), None)
	if declared and Path(declared).exists():
		source = "локальный оверлей" if local_platform else "v8project.yaml"
		ok(f"платформа на месте ({source}): {declared}")
	elif declared:
		found = sorted(str(p) for p in Path("/opt/1cv8/x86_64").glob("*/1cv8")) \
			if Path("/opt/1cv8/x86_64").is_dir() else []
		if not found:
			fail(f"платформа из v8project.yaml не найдена: {declared}, "
			     "и в /opt/1cv8/x86_64/ платформ нет — установи нужную версию")
		elif len(found) == 1:
			warn(f"заявленная платформа не найдена: {declared}; "
			     f"установлена одна — {found[0]}. Пропиши её в v8project.local.yaml: "
			     f"tools: path: {found[0]}")
		else:
			warn("заявленная платформа не найдена "
			     f"{declared}; установлено несколько — выбери с пользователем: "
			     + "; ".join(found))
	else:
		fail("v8project.yaml не найден — это не проект 1С или файл не создан")

	wire = Path(__file__).resolve().parent / "wire_config.py"
	if (root / ".zcode" / "config.json").is_file():
		done = subprocess.run(
			[sys.executable, str(wire), str(root), "--check"],
			capture_output=True, text=True)
		if done.returncode == 0:
			ok(".zcode/config.json разведён (hooks+MCP)")
		else:
			fail(".zcode/config.json разошёлся с разводкой — прогони wire_config.py")
	else:
		warn(".zcode/config.json нет — контур не разведён (навык 1c-project-bootstrap)")

	if (root / ".zcode" / "1c" / "contour.json").is_file():
		ok("contour.json на месте")
	else:
		warn(".zcode/1c/contour.json нет — url 1c-db и override'ы по умолчанию")

	if (root / ".zcode" / "testpilot" / "profiles.yaml").is_file():
		ok("profiles.yaml testpilot на месте")
	else:
		fail(".zcode/testpilot/profiles.yaml нет — e2e-контур не поднимется")

	# 1c-db жив? (нужен клиент 1С с открытой MCP_Toolkit.epf)
	try:
		db_url = (json.loads((root / ".zcode" / "1c" / "contour.json").read_text(encoding="utf-8")).get("1c_db") or {}).get("url")
	except (OSError, json.JSONDecodeError):
		db_url = None
	if db_url:
		import urllib.error, urllib.request
		try:
			urllib.request.urlopen(db_url, timeout=3)
			ok(f"1c-db отвечает: {db_url}")
		except urllib.error.HTTPError as e:
			# 406 — норма для MCP Streamable HTTP на голом GET (нет Accept)
			if e.code == 406:
				ok(f"1c-db отвечает (406 на GET без Accept — норма): {db_url}")
			else:
				ok(f"1c-db отвечает (HTTP {e.code}): {db_url}")
		except Exception:
			warn(f"1c-db не отвечает ({db_url}) — подними: python3 "
			     ".zcode/1c/scripts/start_1c_db.sh . (Linux: сам поднимет "
			     "прокси и клиент с mode=proxy; install_1c_mcp_proxy.sh один раз)")

	# ── 4. Стайл-чекер ──
	style_paths = (
		root / ".zcode" / "skills" / "1c-bsl-code-style" / "scripts" / "bsl_style_check.py",
		root / ".zcode" / "style" / "scripts" / "bsl_style_check.py",
	)
	engine = os.environ.get("1C_STYLE_ENGINE")
	if engine and Path(engine).exists():
		ok(f"стайл-движок: env 1C_STYLE_ENGINE ({engine})")
	elif shutil.which("bsl-style-engine"):
		ok("стайл-движок: bsl-style-engine в PATH")
	elif any(p.is_file() for p in style_paths):
		ok("стайл-чекер: вендоренный пак 1c-bsl-code-style")
	else:
		warn("стайл-чекер не найден — bsl-style-gate пропускает проверки "
		     "(вендорь пак 1c-bsl-code-style)")

	# ── 5. БСП-пак ──
	if any((root / ".zcode" / "skills" / name / "SKILL.md").is_file() for name in ("bsp",)):
		ok("скилл bsp (БСП-пак) вендорен")
	else:
		warn("скилл bsp не вендорен — вызовы БСП без справочника сигнатур")

	# ── 6. Shadow-дубли на пользовательском уровне ──
	shadows = []
	for user_root in USER_SKILL_ROOTS:
		for name in CONTOUR_SKILLS:
			if (user_root / name / "SKILL.md").is_file():
				shadows.append(str(user_root / name))
	if shadows:
		fail("пользовательские копии контурных скиллов затеняют вендоренные: "
		     + ", ".join(shadows) + " — удали (поставщик — плагин)")
	else:
		ok("shadow-дублей контурных скиллов нет")

	# ── 7. Состояние гейтов ──
	state = Path(os.environ.get("ZCODE_1C_STATE_DIR") or Path.home() / ".zcode") / "state" / "1c"
	locks = list((state / "ib-locks").glob("*.json")) if (state / "ib-locks").is_dir() else []
	if locks:
		warn(f"замков инфобаз: {len(locks)} (SessionStart выметет просроченные)")
	else:
		ok("замков инфобаз нет")

	print()
	if issues:
		print(f"итог: {len(issues)} FAIL, {len(warnings)} WARN")
		return 1
	print(f"итог: зелёный ({len(warnings)} WARN)")
	return 0


if __name__ == "__main__":
	sys.exit(main())
