#!/usr/bin/env python3
"""scaffold.py навыка 1c-project-layout: топология каталогов, каталожные
AGENTS.md, каркас v8project.yaml, git-шаблоны, check минимума топологии.
Проверяется как процесс во временном каталоге — ровно как зовёт агент.
Инварианты: идемпотентность (повтор — всё skip), --dry-run ничего не
создаёт, --force перезаписывает, .gitignore только дозаписывает недостающее,
корневой AGENTS.md не перезаписывается (таблица — в stdout), check —
exit 0/1 с машиночитаемыми miss-строками."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib

SCAFFOLD = REPO / "skills" / "1c-project-layout" / "scripts" / "scaffold.py"


def sh(*args: str, cwd: str = None) -> subprocess.CompletedProcess:
	return subprocess.run(
		[sys.executable, str(SCAFFOLD), *args],
		capture_output=True, text=True, cwd=cwd,
	)


def main() -> None:
	tmp = tempfile.mkdtemp(prefix="layout-test-")
	try:
		# ── 1. structure: полный набор kinds, дефолт компонентов ──
		p1 = os.path.join(tmp, "full")
		r = sh("structure", "--dir", p1, "--name", "proj")
		_lib.check("structure exit 0", r.returncode == 0, r.stderr[:200])
		for rel in ("src/cf", "src/cfe", "src/epf", "src/erf", "tests/e2e",
		            "tools", "vendor"):
			_lib.check(f"каталог {rel} создан", os.path.isdir(os.path.join(p1, rel)))
		for rel in ("tools/mcp", "docs"):
			_lib.check(f"без компонента {rel} каталога нет",
			           not os.path.exists(os.path.join(p1, rel)))
		for rel in ("src", "src/cf", "src/cfe", "src/epf", "src/erf",
		            "tests/e2e", "tools", "vendor"):
			_lib.check(f"AGENTS.md в {rel}",
			           os.path.isfile(os.path.join(p1, rel, "AGENTS.md")))
		root = Path(p1) / "AGENTS.md"
		root_text = root.read_text(encoding="utf-8")
		_lib.check("корневой AGENTS.md с именем проекта",
		           "# proj" in root_text, root_text[:80])
		_lib.check("таблица: src/cf и build есть",
		           "| `src/cf/` |" in root_text and "| `build/` |" in root_text)
		_lib.check("таблица: tools/ и vendor/ в обязательной части",
		           "`tools/`" in root_text and "`vendor/`" in root_text)
		_lib.check("таблица: tools/mcp и docs отсутствуют",
		           "tools/mcp" not in root_text and "`docs/`" not in root_text)
		pd = Path(p1) / "packagedef"
		_lib.check("packagedef создан в корне", pd.is_file())
		_lib.check("packagedef пуст (0 байт)",
		           pd.is_file() and pd.stat().st_size == 0,
		           f"size={pd.stat().st_size if pd.exists() else 'нет файла'}")

		# ── 2. идемпотентность: повтор ничего не меняет ──
		before = {p: Path(p1, p).read_bytes()
		          for p in ("AGENTS.md", "src/cf/AGENTS.md", "src/cfe/AGENTS.md")}
		(Path(p1) / "packagedef").write_text("# свой маркер\n", encoding="utf-8")
		r = sh("structure", "--dir", p1, "--name", "proj")
		_lib.check("повтор exit 0", r.returncode == 0, r.stderr[:200])
		unchanged = all(Path(p1, p).read_bytes() == data for p, data in before.items())
		_lib.check("повтор: файлы не перезаписаны", unchanged)
		_lib.check("повтор: packagedef не перезаписан",
		           (Path(p1) / "packagedef").read_text(encoding="utf-8")
		           == "# свой маркер\n")
		_lib.check("повтор: лог содержит skip",
		           "skip" in r.stdout, r.stdout[:200])

		# ── 3. kinds подмножеством; компоненты всеми ──
		p2 = os.path.join(tmp, "kinds")
		sh("structure", "--dir", p2, "--kinds", "cf", "--components",
		   "tools-mcp,docs")
		_lib.check("kinds=cf: src/cf есть", os.path.isdir(os.path.join(p2, "src/cf")))
		_lib.check("kinds=cf: src/cfe нет", not os.path.exists(os.path.join(p2, "src/cfe")))
		_lib.check("компоненты все: tools/mcp и docs с AGENTS.md",
		           os.path.isfile(os.path.join(p2, "tools/mcp/AGENTS.md"))
		           and os.path.isfile(os.path.join(p2, "docs/AGENTS.md")))
		_lib.check("устаревший компонент tests-e2e — громкий отказ",
		           sh("structure", "--dir", os.path.join(tmp, "x1"),
		              "--components", "tests-e2e").returncode == 2)
		t2 = (Path(p2) / "AGENTS.md").read_text(encoding="utf-8")
		_lib.check("таблица kinds=cf без src/cfe", "src/cfe" not in t2)

		# ── 4. неизвестный kind — громкий отказ ──
		r = sh("structure", "--dir", os.path.join(tmp, "bad"), "--kinds", "cf,zzz")
		_lib.check("неизвестный kind: exit 2", r.returncode == 2, str(r.returncode))

		# ── 5. dry-run ничего не создаёт ──
		p4 = os.path.join(tmp, "dry")
		r = sh("structure", "--dir", p4, "--dry-run")
		_lib.check("dry-run exit 0", r.returncode == 0, r.stderr[:200])
		_lib.check("dry-run: файлов нет", not os.path.exists(p4)
		           or not any(os.scandir(p4)))

		# ── 6. --force перезаписывает ──
		(Path(p1) / "src/cf/AGENTS.md").write_text("# затёрт руками\n", encoding="utf-8")
		sh("structure", "--dir", p1, "--force")
		t6 = (Path(p1) / "src/cf/AGENTS.md").read_text(encoding="utf-8")
		_lib.check("--force восстановил шаблон",
		           t6.startswith("# Каталог src/cf"), t6[:60])
		_lib.check("--force перезаписал packagedef пустым",
		           (Path(p1) / "packagedef").read_bytes() == b"")

		# ── 7. v8project: каркас + local; существующий не трогается ──
		p5 = os.path.join(tmp, "v8")
		r = sh("v8project", "--dir", p5)
		v8 = (Path(p5) / "v8project.yaml").read_text(encoding="utf-8")
		_lib.check("каркас: main + path + EXTENSION-комментарий",
		           "name: main" in v8 and "path: src/cf" in v8
		           and "type: EXTENSION" in v8, v8[:200])
		_lib.check("каркас: дефолт File=build/ib", "File=build/ib" in v8)
		_lib.check("local-оверлей создан",
		           os.path.isfile(os.path.join(p5, "v8project.local.yaml")))
		(Path(p5) / "v8project.yaml").write_text("# свой контракт\n", encoding="utf-8")
		sh("v8project", "--dir", p5)
		_lib.check("существующий v8project.yaml не перезаписан",
		           (Path(p5) / "v8project.yaml").read_text(encoding="utf-8")
		           == "# свой контракт\n")
		p5b = os.path.join(tmp, "v8b")
		sh("v8project", "--dir", p5b, "--ib-connection", 'Srvr="s";Ref="b";')
		_lib.check("--ib-connection попадает в каркас",
		           'Srvr="s";Ref="b";' in (Path(p5b) / "v8project.yaml")
		           .read_text(encoding="utf-8"))

		# ── 8. git: init, gitignore append-only, gitattributes, remote ──
		p6 = os.path.join(tmp, "gitproj")
		r = sh("git", "--dir", p6, "--remote", "git@example.com:o/r.git")
		_lib.check("git exit 0", r.returncode == 0, r.stderr[:200])
		_lib.check("git init сработал", os.path.isdir(os.path.join(p6, ".git")))
		gi = (Path(p6) / ".gitignore").read_text(encoding="utf-8")
		_lib.check("gitignore: 1С-строки есть",
		           "build/" in gi and "v8project.local.yaml" in gi)
		_lib.check("gitattributes создан",
		           os.path.isfile(os.path.join(p6, ".gitattributes")))
		remote = subprocess.run(["git", "remote", "get-url", "origin"],
		                        cwd=p6, capture_output=True, text=True)
		_lib.check("remote origin задан", remote.stdout.strip() == "git@example.com:o/r.git",
		           remote.stdout)
		# append: существующий .gitignore с частью строк — дозапись без дублей
		(Path(p6) / ".gitignore").write_text("# свой\nbuild/\n", encoding="utf-8")
		sh("git", "--dir", p6)
		gi2 = (Path(p6) / ".gitignore").read_text(encoding="utf-8")
		_lib.check("append: своя строка цела", "# свой" in gi2)
		_lib.check("append: build/ не задублирован",
		           gi2.splitlines().count("build/") == 1, gi2)
		_lib.check("append: недостающие строки добавлены",
		           "v8project.local.yaml" in gi2)

		# ── 9. корневой AGENTS.md существует → таблица в stdout ──
		p7 = os.path.join(tmp, "hasroot")
		os.makedirs(p7)
		(Path(p7) / "AGENTS.md").write_text("# мой проект\n", encoding="utf-8")
		r = sh("structure", "--dir", p7)
		_lib.check("чужой корневой AGENTS.md не перезаписан",
		           (Path(p7) / "AGENTS.md").read_text(encoding="utf-8")
		           == "# мой проект\n")
		_lib.check("таблица отдана в stdout",
		           "| Каталог | Назначение |" in r.stdout and "`src/cf/`" in r.stdout,
		           r.stdout[-300:])

		# ── 10. all: всё одним вызовом ──
		p8 = os.path.join(tmp, "allp")
		r = sh("all", "--dir", p8, "--name", "allproj")
		_lib.check("all exit 0", r.returncode == 0, r.stderr[:300])
		_lib.check("all: структура + v8project + git вместе",
		           os.path.isfile(os.path.join(p8, "src/cf/AGENTS.md"))
		           and os.path.isfile(os.path.join(p8, "v8project.yaml"))
		           and os.path.isfile(os.path.join(p8, ".gitignore")))
		_lib.check("all: packagedef в корне",
		           os.path.isfile(os.path.join(p8, "packagedef")))

		# ── 12. registry: шаблон и схема реестра потребителей ──
		import json as _json
		schema_p = REPO / "skills/1c-project-layout/templates/registry.schema.json"
		tpl_p = REPO / "skills/1c-project-layout/templates/registry.template.json"
		try:
			schema = _json.loads(schema_p.read_text(encoding="utf-8"))
			tpl = _json.loads(tpl_p.read_text(encoding="utf-8"))
			parsed = True
		except (OSError, _json.JSONDecodeError) as e:
			parsed = False
			_lib.check("registry: шаблон и схема — валидные JSON", False, str(e))
		if parsed:
			_lib.check("registry: контракт schema=contour-consumers/1",
			           schema.get("properties", {}).get("schema", {}).get("const")
			           == "contour-consumers/1"
			           and tpl.get("schema") == "contour-consumers/1")
			_lib.check("registry: required схемы покрыты каркасом",
			           all(k in tpl for k in schema.get("required", [])))
			_lib.check("registry: каркас ссылается на схему",
			           "registry.schema.json" in tpl.get("notes", ""))
		gi_tpl = (REPO / "skills/1c-project-layout/templates/gitignore.template") \
			.read_text(encoding="utf-8")
		_lib.check("registry: gitignore-шаблон держит файл вне git",
		           ".zcode/1c/registry.local.json" in gi_tpl)

		# ── 11. check: детектор обязательного минимума ──
		p9 = os.path.join(tmp, "bare")
		os.makedirs(p9)
		r = sh("check", "--dir", p9)
		_lib.check("check: пустой каталог → exit 1", r.returncode == 1,
		           str(r.returncode))
		_lib.check("check: miss-строки машиночитаемы",
		           "miss\tv8project.yaml" in r.stdout and "miss\tpackagedef" in r.stdout
		           and "miss\ttests/e2e" in r.stdout and "miss\tvendor" in r.stdout,
		           r.stdout[:400])
		_lib.check("check: итог зовёт полный ритуал, не дефолты",
		           "ПОЛНЫМ ритуалом" in r.stdout, r.stdout[-300:])
		_lib.check("check: без юнит-наследия подсказки про мигратор нет",
		           "migrate_from_omp" not in r.stdout, r.stdout[:400])
		r = sh("check", "--dir", p8)
		_lib.check("check: после all → exit 0 (все kinds, tests/e2e, tools, vendor)",
		           r.returncode == 0 and "ok\tsrc/cfe" in r.stdout
		           and "ok\ttests/e2e" in r.stdout and "ok\tvendor" in r.stdout,
		           r.stdout[:400])
		(Path(p9) / "v8project.yaml").write_text("# заглушка\n", encoding="utf-8")
		r = sh("check", "--dir", p9)
		missed_ok = (r.returncode == 1
		             and "miss\tv8project.yaml" not in r.stdout
		             and "miss\tsrc/cf" in r.stdout)
		_lib.check("check: частичный — ругается только на недостающее",
		           missed_ok, r.stdout[:300])
		# юнит-наследие omp-эпохи: miss tests/e2e при tests/cfe|epf
		os.makedirs(os.path.join(p9, "tests/cfe"))
		r = sh("check", "--dir", p9)
		_lib.check("check: юнит-наследие опознано подсказкой про мигратор",
		           "migrate_from_omp" in r.stdout and "tests/cfe" in r.stdout,
		           r.stdout[:500])
		_lib.check("check: смешанное наследие (куча miss) — path\tritual",
		           "path\tritual" in r.stdout, r.stdout[:500])
		# чистое наследие (miss только tests/e2e) — финал ведёт в мигратор,
		# не в полный ритуал; смешанный случай — ритуал остаётся
		p10 = os.path.join(tmp, "legacy")
		for rel in ("src/cf", "src/cfe", "src/epf", "src/erf", "tests/cfe",
		            "tests/epf", "tools", "vendor"):
			os.makedirs(os.path.join(p10, rel), exist_ok=True)
		for rel in ("v8project.yaml", ".gitignore", ".gitattributes",
		            "packagedef", "AGENTS.md"):
			open(os.path.join(p10, rel), "w").close()
		r = sh("check", "--dir", p10)
		_lib.check("check: чистое наследие — финал про мигратор",
		           r.returncode == 1 and "путь лечения выше (мигратор)" in r.stdout
		           and "ПОЛНЫМ ритуалом" not in r.stdout, r.stdout[-300:])
		_lib.check("check: path-маркер migrator для чистого наследия",
		           "path\tmigrator" in r.stdout, r.stdout[-300:])
		os.rmdir(os.path.join(p10, "vendor"))
		r = sh("check", "--dir", p10)
		_lib.check("check: наследие + ещё miss — полный ритуал возвращается",
		           r.returncode == 1 and "ПОЛНЫМ ритуалом" in r.stdout,
		           r.stdout[-300:])
		_lib.check("check: path-маркер ritual в смешанном кейсе",
		           "path\tritual" in r.stdout, r.stdout[-300:])
	finally:
		shutil.rmtree(tmp, ignore_errors=True)
	sys.exit(_lib.summary())


if __name__ == "__main__":
	main()
