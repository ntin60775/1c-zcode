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
		data = json.loads(installed.read_text(encoding="utf-8"))
		for key, meta in (data or {}).items():
			if key.startswith("unica@"):
				unica_entries.append((key, str(meta.get("version") or "0")))
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
