#!/usr/bin/env python3
"""kd_register_project.py — подключение 1С-проекта к станции kd-rules-mcp (MCP 1c-kd).

Заполняет projects.yaml и projects.local.yaml станции записью этого проекта —
агент запускает скрипт в корне проекта при первой задаче по правилам обмена;
вручную описывать проекты не нужно. Выгрузки существующего подключения сервер
перечитывает на каждый вызов; НОВЫЙ проект (или снятый) требует рестарта
сервера — список папок (projects.local.yaml) читается один раз на старте:
start_kd_rules.sh --stop && start_kd_rules.sh.

Что делает:
  1. Находит XML-выгрузку конфигурации и расширений (формат Конфигуратора,
     не EDT): src/*/Configuration.xml — основная (кратчайший путь), src/cfe/*/
     Configuration.xml — расширения в алфавитном порядке (порядок наложения
     уточняет агент, см. скилл 1c-kd-rules).
  2. Вписывает проект в projects.yaml станции (dump/extensions относительно
     корня проекта; rules_dir — если задан --rules-dir) и в
     projects.local.yaml — абсолютный путь проекта на этой машине.
  3. Проверяет итог загрузчиком станции (kd_rules_mcp.projects.load_catalog).

Хуки/прочее не пишет; базы (bases), data_mcp и writable_extensions не
заполняет — решаются по потребности (поля описаны в projects.example.yaml
станции, канон записи — скилл 1c-kd-rules).

Использование (из корня 1С-проекта):
  python3 kd_register_project.py .                # найти выгрузки и подключить
  python3 kd_register_project.py . --id myproj    # свой ключ (умолчание — имя каталога)
  python3 kd_register_project.py . --dump src/Main --extensions src/cfe/A,src/cfe/B
  python3 kd_register_project.py . --rules-dir ПравилаОбмена
  python3 kd_register_project.py . --check        # зарегистрирован и пути живы?
  python3 kd_register_project.py . --unregister   # убрать запись из станции

Переменные: KD_RULES_STATION — путь станции (умолчание
~/.local/share/1c-zcode/kd-rules-mcp); KD_RULES_PYTHON — интерпретатор с
pyyaml (умолчание .venv станции). Скрипту нужен pyyaml: если текущий python
его не имеет, скрипт перезапускается интерпретатором станции.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_STATION = Path.home() / ".local" / "share" / "1c-zcode" / "kd-rules-mcp"

STUB_HEADER = (
    "# Проекты подключает kd_register_project.py из скриптов контура 1c-zcode;\n"
    "# поля (bases, rules_dir, writable_extensions) — projects.example.yaml рядом.\n"
)


def station_dir() -> Path:
	return Path(os.environ.get("KD_RULES_STATION") or DEFAULT_STATION)


def yaml_available() -> bool:
	try:
		import yaml  # noqa: F401
	except ImportError:
		return False
	return True


def reexec_with_station_python() -> None:
	"""Текущий python без pyyaml — перезапуск интерпретатором станции."""
	candidate = os.environ.get("KD_RULES_PYTHON")
	if not candidate:
		candidate = str(station_dir() / ".venv" / "bin" / "python")
	if Path(candidate).is_file():
		os.execv(candidate, [candidate, __file__, *sys.argv[1:]])
	print(f"pyyaml недоступен, интерпретатора станции нет ({candidate}) — "
	      f"поставь станцию: install_kd_rules.sh", file=sys.stderr)
	sys.exit(2)


# ── детект выгрузок (формат Конфигуратора: каталог с Configuration.xml) ──────

def find_dumps(root: Path) -> tuple[str, list[str]]:
	"""(основная выгрузка, расширения) относительно root; без найденных — ошибка."""
	mains, extensions = [], []
	seen: set[str] = set()
	for candidate in sorted(root.glob("src/*/Configuration.xml")) + \
			sorted(root.glob("src/cfe/*/Configuration.xml")) + \
			sorted(root.glob("src/*/*/Configuration.xml")):
		rel = candidate.parent.relative_to(root).as_posix()
		if rel in seen:  # src/cfe/* матчится и вторым глобом
			continue
		seen.add(rel)
		if rel.startswith("src/cfe/"):
			extensions.append(rel)
		else:
			mains.append(rel)
	if not mains:
		raise SystemExit("не нашёл выгрузку конфигурации (src/*/Configuration.xml, "
		                 "формат Конфигуратора) — укажи вручную: --dump <путь>")
	dump = min(mains, key=len)  # src/cf короче src/<имя>/src/Main
	return dump, extensions


def sanitize_id(raw: str) -> str:
	cleaned = re.sub(r"[^0-9a-zA-Z]+", "-", raw.lower()).strip("-")
	return cleaned or "project"


def load_yaml(path: Path, default):
	if not path.is_file():
		return default
	import yaml
	return yaml.safe_load(path.read_text(encoding="utf-8")) or default


def dump_yaml(path: Path, data, header: str = "") -> None:
	import yaml
	text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
	                      default_flow_style=False)
	path.write_text(header + text, encoding="utf-8")


def validate_catalog(station: Path, project_id: str) -> None:
	"""Проверка загрузчиком станции: запись разбирается и видна."""
	py = os.environ.get("KD_RULES_PYTHON") or str(station / ".venv" / "bin" / "python")
	code = ("import sys; sys.path.insert(0, %r); "
	        "from pathlib import Path; "
	        "from kd_rules_mcp.projects import load_catalog; "
	        "c = load_catalog(Path(%r)); "
	        "print(','.join(c.projects))" % (str(station / "src"), str(station / "projects.yaml")))
	done = subprocess.run([py, "-c", code], capture_output=True, text=True)
	ids = done.stdout.strip() if done.returncode == 0 else ""
	if project_id not in ids.split(","):
		raise SystemExit(f"проверка загрузчиком станции не прошла: {done.stderr.strip()[:300]}")


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument("root", nargs="?", default=".", help="корень 1С-проекта")
	parser.add_argument("--id", help="ключ проекта (умолчание — имя каталога)")
	parser.add_argument("--name", help="название для людей")
	parser.add_argument("--dump", help="путь к выгрузке от корня (умолчание — авто-поиск)")
	parser.add_argument("--extensions", help="выгрузки расширений через запятую")
	parser.add_argument("--rules-dir", help="папка живых правил от корня")
	parser.add_argument("--check", action="store_true", help="только проверить")
	parser.add_argument("--unregister", action="store_true", help="убрать запись")
	args = parser.parse_args()

	if not yaml_available():
		reexec_with_station_python()
	station = station_dir()
	projects_file = station / "projects.yaml"
	local_file = station / "projects.local.yaml"
	if not projects_file.is_file():
		raise SystemExit(f"нет {projects_file} — подними станцию: start_kd_rules.sh "
		                 "(первый старт создаст каркас)")

	root = Path(args.root).resolve()
	project_id = args.id or sanitize_id(root.name)

	catalog = load_yaml(projects_file, {})
	projects = catalog.setdefault("projects", {})
	local = load_yaml(local_file, {})
	local_projects = local.setdefault("projects", {})

	if args.unregister:
		removed = [f for f in (projects.pop(project_id, None),
		                       local_projects.pop(project_id, None)) if f is not None]
		if not removed:
			raise SystemExit(f"проекта «{project_id}» в станции нет")
		dump_yaml(projects_file, catalog, STUB_HEADER)
		dump_yaml(local_file, local)
		print(f"✓ «{project_id}» убран из станции")
		return 0

	if args.check:
		entry = projects.get(project_id)
		if entry is None:
			raise SystemExit(f"проекта «{project_id}» нет в {projects_file}")
		folder = Path(str(local_projects.get(project_id, "")))
		ok_folder = folder.is_dir()
		ok_dump = (folder / str((entry.get("configurations") or {}).get("full", {}).get("dump", "x"))).is_dir()
		if not (ok_folder and ok_dump):
			raise SystemExit(f"«{project_id}»: папка {'жива' if ok_folder else 'НЕ найдена'}, "
			                 f"выгрузка {'на месте' if ok_dump else 'НЕ найдена'} — переподключи")
		print(f"ок: «{project_id}» → {folder} (выгрузка "
		      f"{entry['configurations']['full']['dump']})")
		return 0

	entry = projects.get(project_id)
	dump = args.dump
	extensions = [p.strip() for p in args.extensions.split(",")] if args.extensions else None
	if not dump or extensions is None:
		auto_dump, auto_ext = find_dumps(root)
		dump = dump or auto_dump
		extensions = extensions if extensions is not None else auto_ext

	projects[project_id] = {
		"name": args.name or project_id,
		"configurations": {"full": {"dump": dump, **({"extensions": extensions} if extensions else {})}},
		**({"rules_dir": args.rules_dir} if args.rules_dir else {}),
	}
	local_projects[project_id] = str(root)

	dump_yaml(projects_file, catalog, STUB_HEADER)
	dump_yaml(local_file, local)
	validate_catalog(station, project_id)
	ext_note = f", расширений: {len(extensions)}" if extensions else ""
	print(f"✓ «{project_id}» подключён к станции: выгрузка {dump}{ext_note}")
	print(f"  папка: {root}")
	if entry is None:
		print("  новый проект — перезапусти сервер: start_kd_rules.sh --stop && start_kd_rules.sh"
		      " (список папок читается на старте)")
	print("  проверка в сессии: project_list → structure_load_project «%s»" % project_id)
	print("  базы/writable_extensions — по потребности, поля в projects.example.yaml станции")
	return 0


if __name__ == "__main__":
	sys.exit(main())
