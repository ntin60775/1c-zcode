#!/usr/bin/env python3
"""lint_skill_frontmatter.py — линтер SKILL.md под слоты ZCode.

Правила адаптации скиллов контура (включая сторонние):
  - распознаваемые ключи frontmatter: name, description, when_to_use,
    license, metadata (Claude-поля argument-hint/allowed-tools не используем);
  - name обязателен и совпадает с именем каталога;
  - description обязательна и ≤ 1024 символов (длиннее — ZCode дропает скилл);
  - рекомендуем when_to_use: триггерные слова в первых ~250 символов описания.

Запуск: python3 scripts/lint_skill_frontmatter.py [корни...]   # по умолчанию skills/
Exit: 0 — чисто, 1 — есть ошибки.
"""
import re
import sys
from pathlib import Path

RECOGNIZED_KEYS = {"name", "description", "when_to_use", "license", "metadata"}
DESCRIPTION_LIMIT = 1024
FRONTLOAD_LIMIT = 250


def parse_frontmatter(text: str):
	"""(dict, список_строк_ошибок_формата) из SKILL.md."""
	if not text.startswith("---"):
		return {}, ["нет frontmatter (--- в первой строке)"]
	lines = text.splitlines()
	try:
		end = lines[1:].index("---") + 1
	except ValueError:
		return {}, ["frontmatter не закрыт (--- )"]
	fields = {}
	for line in lines[1:end]:
		match = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
		if not match:
			if line.strip():
				continue  # вложенные строки многострочных значений
			continue
		fields[match.group(1)] = match.group(2).strip()
	return fields, []


def lint_skill(skill_dir: Path):
	issues = []
	skill_md = skill_dir / "SKILL.md"
	if not skill_md.is_file():
		return [f"{skill_dir}: нет SKILL.md"]
	text = skill_md.read_text(encoding="utf-8")
	fields, fmt = parse_frontmatter(text)
	issues += [f"{skill_dir.name}: {msg}" for msg in fmt]
	if fmt:
		return issues
	for key in fields:
		if key not in RECOGNIZED_KEYS:
			issues.append(f"{skill_dir.name}: ключ '{key}' не распознаётся ZCode")
	name = fields.get("name", "")
	if not name:
		issues.append(f"{skill_dir.name}: нет name (скилл не загрузится)")
	elif name != skill_dir.name:
		issues.append(f"{skill_dir.name}: name '{name}' != имени каталога")
	description = fields.get("description", "")
	if not description:
		issues.append(f"{skill_dir.name}: нет description (скилл не триггерится)")
	if len(description) > DESCRIPTION_LIMIT:
		issues.append(f"{skill_dir.name}: description {len(description)} > {DESCRIPTION_LIMIT} (скилл не загрузится)")
	elif description and not fields.get("when_to_use") and len(description) > FRONTLOAD_LIMIT \
			and not re.search(r"[аЯa-zA-Z]", description[:FRONTLOAD_LIMIT]):
		issues.append(f"{skill_dir.name}: первые {FRONTLOAD_LIMIT} символов description без триггерных слов")
	return issues


def main() -> int:
	roots = [Path(a) for a in sys.argv[1:]] or [Path(__file__).resolve().parent.parent / "skills"]
	issues = []
	skills_found = 0
	for root in roots:
		if not root.is_dir():
			issues.append(f"корень {root} не существует")
			continue
		for skill_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
			if not (skill_dir / "SKILL.md").is_file() and not any(skill_dir.glob("*/SKILL.md")):
				continue
			skills_found += 1
			issues += lint_skill(skill_dir)
	if not skills_found:
		issues.append("скиллов не найдено")
	if issues:
		for issue in issues:
			print(f"FAIL {issue}")
		return 1
	print(f"ок: {skills_found} скилл(ов) без замечаний")
	return 0


if __name__ == "__main__":
	sys.exit(main())
