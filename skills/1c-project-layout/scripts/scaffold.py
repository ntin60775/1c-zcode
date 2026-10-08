#!/usr/bin/env python3
"""1C Project Layout — scaffold топологии каталогов 1С-проекта контура.

Субкоманды:
  structure   — обязательная топология (src/{cf,cfe,epf,erf}, tests/e2e,
                tools, vendor) + компоненты (tools/mcp, docs), каталожные
                AGENTS.md, корневой AGENTS.md с таблицей структуры, пустой
                packagedef (маркер 1C Platform Tools в VS Code)
  v8project   — каркас v8project.yaml + шаблон v8project.local.yaml
  git         — git init + .gitignore (только дозапись недостающих строк)
                + .gitattributes + remote origin
  check       — обязательный минимум топологии (состав = REQUIRED_MINIMUM):
                exit 0 выполнен, exit 1 нет (машиночитаемые строки ok/miss)
                — детектор, по которому навык срабатывает ПОЛНЫМ ритуалом;
                юнит-наследие omp-эпохи (tests/cfe|tests/epf вместо
                tests/e2e) опознаётся отдельной подсказкой про мигратор
  all         — structure + v8project + git одним вызовом

Идемпотентность: существующие файлы не перезаписываются без --force.
Исключение — .gitignore: только дозапись недостающих строк, --force на
него не действует (append безвреден по построению).

Взаимодействие с пользователем отсутствует: все параметры — аргументы CLI.
Дизайн — docs/PROJECT-LAYOUT-SKILL.md; донор кода — bootstrap.py из
1c-omp (наш MIT), vanessa-bootstrap текстами не заимствовался.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(SKILL_ROOT, "templates")

# Каталоги исходников по типам артефактов (--kinds).
KIND_DIRS = {
	"cf": "src/cf",
	"cfe": "src/cfe",
	"epf": "src/epf",
	"erf": "src/erf",
}

# Компоненты сверх обязательной топологии (--components); дефолт — нет.
COMPONENT_DIRS = {
	"tools-mcp": "tools/mcp",
	"docs": "docs",
}
DEFAULT_KINDS = "cf,cfe,epf,erf"
DEFAULT_COMPONENTS = ""

# Обязательный минимум топологии: без него каталог — не проект контура.
# Отсутствие чего-либо из списка = триггер ПОЛНОГО ритуала навыка (все
# вопросы оператору), не молчаливого дооснащения дефолтами. Состав утверждён
# владельцем по эмпирике живых проектов (dealer-network-orders, erp-mini):
# все kinds, tests/e2e, tools/ (служебные скрипты проекта), vendor/.
# Единственный источник этого списка — здесь; doctor зовёт check процессом
# и ретранслирует, не дублируя.
REQUIRED_MINIMUM = [
    ("src/cf", "dir", "исходники конфигурации (source-set main)"),
    ("src/cfe", "dir", "исходники расширений (по подкаталогу на расширение)"),
    ("src/epf", "dir", "исходники внешних обработок"),
    ("src/erf", "dir", "исходники внешних отчётов"),
    ("tests/e2e", "dir", "e2e-тесты контура (pytest): логика, API, GUI"),
    ("tools", "dir", "служебные скрипты и конфигурации проекта"),
    ("vendor", "dir", "вендоренный внешний код (паки, тулкиты)"),
    ("v8project.yaml", "file", "контракт юники"),
    (".gitignore", "file", "git-гигиена (строки шаблона — субкоманда git)"),
    (".gitattributes", "file", "EOL/бинарные — repositoryReady первого коммита"),
    ("packagedef", "file", "маркер 1C Platform Tools в VS Code (новым — пустой; "
                           "существующий не трогать: бывает живым "
                           "OneScript-дескриптором)"),
    ("AGENTS.md", "file", "входная точка агентов"),
]

# Каталоги обязательной топологии (не kinds, не компоненты): создаются
# structure всегда.
ALWAYS_DIRS = ["tests/e2e", "tools", "vendor"]

# Шаблон каталожного AGENTS.md для каждого каталога ("" — корень проекта).
AGENTS_FOR_DIR = {
	"": "root",
	"src": "src",
	"src/cf": "src_cf",
	"src/cfe": "src_cfe",
	"src/epf": "src_epf",
	"src/erf": "src_erf",
	"tests/e2e": "tests_e2e",
	"tools": "tools",
	"tools/mcp": "tools_mcp",
	"vendor": "vendor",
	"docs": "docs",
}

# Строки таблицы структуры корневого AGENTS.md: каталог -> (условие, назначение).
# Условие "always" — обязательная топология, kind — по --kinds,
# иначе имя компонента из --components.
TABLE_ROWS = [
	("src/cf", "cf",
	 "исходники конфигурации (XML-выгрузка, source-set `main`)"),
	("src/cfe", "cfe",
	 "исходники расширений; на каждое расширение — свой подкаталог (EXTENSION source-set в `v8project.yaml`)"),
	("src/epf", "epf",
	 "исходники внешних обработок; имя подкаталога = имя обработки"),
	("src/erf", "erf",
	 "исходники внешних отчётов; имя подкаталога = имя отчёта"),
	("tests/e2e", "always",
	 "e2e-тесты контура (pytest): логика, API, GUI"),
	("tools", "always",
	 "служебные скрипты и конфигурации проекта (не вендоренная поставка контура)"),
	("vendor", "always",
	 "вендоренный внешний код (паки, тулкиты); источник и лицензия фиксируются"),
	("tools/mcp", "tools-mcp",
	 "`MCP_Toolkit.epf` для 1c-db (копия, в git не попадает)"),
	("docs", "docs",
	 "документация проекта"),
]


def log(message: str) -> None:
	print(message)


def make_dirs(paths: list[str], dry_run: bool) -> tuple[int, int]:
	created = skipped = 0
	for rel in paths:
		dst = os.path.join(TARGET_DIR, rel)
		if os.path.isdir(dst):
			skipped += 1
			continue
		log(f"  mkdir   {rel}")
		if not dry_run:
			os.makedirs(dst, exist_ok=True)
		created += 1
	return created, skipped


def read_template(template_rel: str) -> str:
	with open(os.path.join(TEMPLATES, template_rel), encoding="utf-8") as f:
		return f.read()


def write_file(dst_rel: str, content: str, dry_run: bool, force: bool) -> str:
	"""Пишет файл, не трогая существующий без --force.
	Возвращает статус: created | exists | overwritten."""
	dst = os.path.join(TARGET_DIR, dst_rel)
	if os.path.exists(dst) and not force:
		log(f"  skip    {dst_rel} (уже есть)")
		return "exists"
	action = "overwrite" if os.path.exists(dst) else "create"
	log(f"  {action:<8} {dst_rel}")
	if not dry_run:
		os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
		with open(dst, "w", encoding="utf-8") as f:
			f.write(content)
	return action


def copy_template(template_rel: str, dst_rel: str, dry_run: bool, force: bool,
                  replacements: dict[str, str] | None = None) -> str:
	content = read_template(template_rel)
	for old, new in (replacements or {}).items():
		content = content.replace(old, new)
	return write_file(dst_rel, content, dry_run, force)


def write_agents(dirs: list[str], dry_run: bool, force: bool) -> tuple[int, int]:
	"""AGENTS.md в перечисленные каталоги (без корня — он отдельным слоем)."""
	written = skipped = 0
	for rel in dirs:
		template_rel = os.path.join("agents", AGENTS_FOR_DIR[rel] + ".md")
		status = copy_template(template_rel, os.path.join(rel, "AGENTS.md"),
		                       dry_run, force, {"{project_name}": PROJECT_NAME})
		if status == "exists":
			skipped += 1
		else:
			written += 1
	return written, skipped


def structure_table(kinds: list[str], components: list[str]) -> str:
	entries = [
		(f"`{rel}/`", purpose)
		for rel, flag, purpose in TABLE_ROWS
		if flag == "always" or flag in kinds or flag in components
	]
	entries.append((
		"`build/`",
		"артефакты сборки: файловая ИБ (`build/ib`), дампы (`*.dt`), инструменты (`build/tools/`) — в git не попадают",
	))
	rows = "\n".join(f"| {path} | {purpose} |" for path, purpose in entries)
	return "| Каталог | Назначение |\n|---|---|\n" + rows


def write_root_agents(kinds: list[str], components: list[str],
                      dry_run: bool, force: bool) -> None:
	"""Корневой AGENTS.md: нет — создать с таблицей; есть — не трогать,
	таблицу отдать в stdout для вставки агентом."""
	dst = os.path.join(TARGET_DIR, "AGENTS.md")
	table = structure_table(kinds, components)
	if os.path.exists(dst) and not force:
		log("  skip    AGENTS.md (уже есть) — таблица структуры для вставки:")
		print()
		print(table)
		print()
		return
	content = read_template(os.path.join("agents", "root.md"))
	content = (content
	           .replace("{project_name}", PROJECT_NAME)
	           .replace("{structure_table}", table))
	action = "overwrite" if os.path.exists(dst) else "create"
	log(f"  {action:<8} AGENTS.md")
	if not dry_run:
		with open(dst, "w", encoding="utf-8") as f:
			f.write(content)


def parse_csv(value: str, allowed: dict[str, str], flag: str) -> list[str]:
	items = [v.strip() for v in value.split(",") if v.strip()]
	unknown = [v for v in items if v not in allowed]
	if unknown:
		sys.stderr.write(
			f"неизвестные значения --{flag}: {', '.join(unknown)} "
			f"(доступно: {', '.join(allowed)})\n")
		sys.exit(2)
	return items


def cmd_structure(args: argparse.Namespace) -> int:
	kinds = parse_csv(args.kinds, KIND_DIRS, "kinds")
	components = parse_csv(args.components, COMPONENT_DIRS, "components")
	dirs: list[str] = []
	if kinds:
		dirs.append("src")
		dirs.extend(KIND_DIRS[k] for k in kinds if k in KIND_DIRS)
	dirs.extend(ALWAYS_DIRS)
	dirs.extend(COMPONENT_DIRS[c] for c in components if c in COMPONENT_DIRS)
	known = set(AGENTS_FOR_DIR) - {""}
	unknown = [d for d in dirs if d not in known]
	if unknown:  # защита при расширении таблиц выше
		sys.stderr.write(f"нет AGENTS-шаблона для: {', '.join(unknown)}\n")
		return 2
	log(f"Каталоги: {len(dirs)}")
	created, skipped = make_dirs(dirs, args.dry_run)
	log("AGENTS.md (каталожные):")
	written, ag_skipped = write_agents(dirs, args.dry_run, args.force)
	log("Корневой AGENTS.md:")
	write_root_agents(kinds, components, args.dry_run, args.force)
	log("packagedef (пустой маркер 1C Platform Tools в VS Code):")
	write_file("packagedef", "", args.dry_run, args.force)
	log(f"structure: каталогов {created}/{skipped} (создано/пропущено), "
	    f"каталожных AGENTS.md {written}/{ag_skipped}")
	return 0


def cmd_v8project(args: argparse.Namespace) -> int:
	log("v8project.yaml:")
	status = copy_template(
		"v8project.template.yaml", "v8project.yaml", args.dry_run, args.force,
		{"{ib_connection}": args.ib_connection})
	log("v8project.local.yaml:")
	copy_template("v8project.local.template.yaml", "v8project.local.yaml",
	              args.dry_run, args.force)
	if status != "exists" and not args.dry_run:
		log("заполни tools.platform.path под машину (контейнер/установка платформы)")
	return 0


def append_missing(dst_rel: str, dry_run: bool) -> int:
	"""Дозапись в .gitignore только недостающих строк; --force не действует:
	append безвреден, перезапись чужих строк запрещена по разводке владельцев
(layout — 1С-строки, 1c-project-bootstrap — .zcode-строки)."""
	template = read_template("gitignore.template")
	template_lines = [ln for ln in template.splitlines() if ln.strip()]
	dst = os.path.join(TARGET_DIR, dst_rel)
	existing = set()
	if os.path.exists(dst):
		with open(dst, encoding="utf-8") as f:
			existing = {ln.strip() for ln in f.read().splitlines()}
	missing = [ln for ln in template_lines if ln.strip() not in existing]
	if not missing:
		log(f"  skip    {dst_rel} (все строки уже есть)")
		return 0
	log(f"  append  {dst_rel} (+{len(missing)} строк)")
	if not dry_run:
		prefix = "" if (not os.path.exists(dst) or
		                os.path.getsize(dst) == 0 or
		                _ends_with_newline(dst)) else "\n"
		with open(dst, "a", encoding="utf-8") as f:
			f.write(prefix + "\n".join(missing) + "\n")
	return len(missing)


def _ends_with_newline(path: str) -> bool:
	with open(path, "rb") as f:
		f.seek(-1, os.SEEK_END)
		return f.read(1) == b"\n"


def cmd_git(args: argparse.Namespace) -> int:
	if os.path.isdir(os.path.join(TARGET_DIR, ".git")):
		log("git: репозиторий уже есть, git init пропущен")
	elif not args.dry_run:
		log("git: git init")
		subprocess.run(["git", "init"], cwd=TARGET_DIR, check=True,
		               capture_output=True)
	else:
		log("git: git init (dry-run, пропущен)")
	append_missing(".gitignore", args.dry_run)
	copy_template("gitattributes.template", ".gitattributes",
	              args.dry_run, args.force)
	if args.remote:
		if os.path.isdir(os.path.join(TARGET_DIR, ".git")) and not args.dry_run:
			existing = subprocess.run(
				["git", "remote", "get-url", "origin"],
				cwd=TARGET_DIR, capture_output=True, text=True)
			if existing.returncode == 0:
				log(f"git: remote origin уже задан ({existing.stdout.strip()})")
			else:
				log(f"git: git remote add origin {args.remote}")
				subprocess.run(["git", "remote", "add", "origin", args.remote],
				               cwd=TARGET_DIR, check=True)
		else:
			log("git: remote origin не добавлен (dry-run или репозитория нет)")
	log("git: готово")
	return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Детектор минимума: ok/miss строки для doctor и агента, exit 0/1."""
    missing = []
    for rel, kind, why in REQUIRED_MINIMUM:
        dst = os.path.join(TARGET_DIR, rel)
        present = os.path.isdir(dst) if kind == "dir" else os.path.isfile(dst)
        if present:
            log(f"ok\t{rel}")
        else:
            log(f"miss\t{rel}\t{why}")
            missing.append(rel)
    if missing:
        # Юнит-стек omp-эпохи вместо tests/e2e — другая болезнь и другое
        # лечение: мигратор + демонтаж, не полный ритуал layout.
        legacy = [d for d in ("tests/cfe", "tests/epf")
                  if os.path.isdir(os.path.join(TARGET_DIR, d))]
        if legacy:
            log(f"наследие: найден юнит-стек omp-эпохи ({', '.join(legacy)}) — "
                "tests/e2e лечится переносом покрытия и демонтажём "
                "(scripts/migrate_from_omp.py, см. MIGRATION-FROM-OMP.md), "
                "не полным ритуалом layout")
        if legacy and missing == ["tests/e2e"]:
            log("минимум топологии не выполнен — путь лечения выше (мигратор)")
        else:
            log("минимум топологии не выполнен — зови навык 1c-project-layout "
                "ПОЛНЫМ ритуалом (все вопросы оператору: имя, kinds, components, "
                "remote, ib-connection; dry-run → план → запуск), "
                "не молчаливым дооснащением дефолтами")
        return 1
    log("минимум топологии выполнен")
    return 0


def cmd_all(args: argparse.Namespace) -> int:
	for cmd in (cmd_structure, cmd_v8project, cmd_git):
		print(f"== {cmd.__name__[4:]} ==")
		code = cmd(args)
		if code:
			return code
	log("all: готово. Дальше: контурные конфиги — навык 1c-project-bootstrap; "
	    "первый источник правды (dump/build) — unica (навык 1c-contour).")
	return 0


def make_common_parser() -> argparse.ArgumentParser:
	common = argparse.ArgumentParser(add_help=False)
	common.add_argument("--dir", required=True,
	                    help="целевой каталог проекта (создаётся при необходимости)")
	common.add_argument("--name", dest="project_name",
	                    help="имя проекта (slug); по умолчанию — имя каталога")
	common.add_argument("--dry-run", action="store_true",
	                    help="показать план без изменений")
	common.add_argument("--force", action="store_true",
	                    help="перезаписывать существующие файлы (кроме .gitignore)")
	return common


def main(argv: list[str] | None = None) -> int:
	global TARGET_DIR, PROJECT_NAME
	common = make_common_parser()
	parser = argparse.ArgumentParser(
		prog="scaffold.py",
		description="Scaffold топологии каталогов 1С-проекта контура 1c-zcode.",
	)
	sub = parser.add_subparsers(dest="command", required=True)

	p_structure = sub.add_parser(
		"structure", help="каталоги + AGENTS.md + корневая таблица", parents=[common])
	p_structure.add_argument("--kinds", default=DEFAULT_KINDS,
	                         help=f"типы исходников (csv): {','.join(KIND_DIRS)}")
	p_structure.add_argument("--components", default=DEFAULT_COMPONENTS,
	                         help=f"компоненты (csv): {','.join(COMPONENT_DIRS)}; дефолт {DEFAULT_COMPONENTS}")
	p_structure.set_defaults(func=cmd_structure)

	p_v8project = sub.add_parser(
		"v8project", help="каркас v8project.yaml + шаблон local-оверлея",
		parents=[common])
	p_v8project.add_argument("--ib-connection", dest="ib_connection",
	                         default="File=build/ib",
	                         help="строка подключения ИБ (infobases.origin.connection)")
	p_v8project.set_defaults(func=cmd_v8project)

	p_git = sub.add_parser(
		"git", help="git init + .gitignore (append) + .gitattributes + remote",
		parents=[common])
	p_git.add_argument("--remote", help="URL remote origin (опционально)")
	p_git.set_defaults(func=cmd_git)

	p_check = sub.add_parser(
		"check", help="обязательный минимум топологии (exit 0/1)",
		parents=[common])
	p_check.set_defaults(func=cmd_check)

	p_all = sub.add_parser(
		"all", help="structure + v8project + git", parents=[common])
	p_all.add_argument("--kinds", default=DEFAULT_KINDS,
	                   help=f"типы исходников (csv): {','.join(KIND_DIRS)}")
	p_all.add_argument("--components", default=DEFAULT_COMPONENTS,
	                   help=f"компоненты (csv): {','.join(COMPONENT_DIRS)}; дефолт {DEFAULT_COMPONENTS}")
	p_all.add_argument("--remote", help="URL remote origin (опционально)")
	p_all.add_argument("--ib-connection", dest="ib_connection",
	                   default="File=build/ib", help="строка подключения ИБ")
	p_all.set_defaults(func=cmd_all)

	args = parser.parse_args(argv)
	TARGET_DIR = os.path.abspath(args.dir)
	if not args.project_name:
		args.project_name = os.path.basename(TARGET_DIR.rstrip(os.sep)) or "project"
	PROJECT_NAME = args.project_name
	if not args.dry_run:
		os.makedirs(TARGET_DIR, exist_ok=True)
	if args.dry_run:
		log(f"DRY-RUN: изменений не вносится. Цель: {TARGET_DIR}")
	else:
		log(f"Цель: {TARGET_DIR}")
	return args.func(args)


if __name__ == "__main__":
	sys.exit(main())
