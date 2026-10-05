#!/usr/bin/env python3
"""ROOT контурного проекта не берётся молча из чужого CWD (регрессия
2026-10-05: publish_ib.sh из чужого git-репо принял его за корень —
git rev-parse успешен, скрипт смешал BASE из аргумента с WORK/contour.json
своего CWD). Фикс: ROOT от местоположения вендоренного скрипта + fail-fast
валидация маркеров контурного проекта (publish_ib.sh и run_e2e.sh)."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib

MARKER_YAML = "# fake contour project (тест ROOT-резолва)\n"


def sh(script: Path, cwd: Path, *args: str):
	return subprocess.run(
		["bash", str(script), *args],
		capture_output=True, text=True, cwd=str(cwd),
		env=dict(os.environ, PUBLISH_IBSRV="/bin/true"),
	)


def main():
	tmp = Path(tempfile.mkdtemp(prefix="publish-ib-root-"))
	try:
		# вендоренная раскладка: <проект>/.zcode/1c/scripts
		proj = tmp / "proj"
		(proj / ".zcode" / "1c" / "scripts").mkdir(parents=True)
		(proj / "v8project.yaml").write_text(MARKER_YAML, encoding="utf-8")
		pub = REPO / "scripts" / "publish_ib.sh"
		run = REPO / "scripts" / "run_e2e.sh"
		shutil.copy(pub, proj / ".zcode" / "1c" / "scripts" / "publish_ib.sh")
		shutil.copy(run, proj / ".zcode" / "1c" / "scripts" / "run_e2e.sh")

		# чужой git-репо как CWD: git rev-parse УСПЕШЕН — точный сценарий инцидента
		wrong = tmp / "wrongrepo"
		wrong.mkdir()
		subprocess.run(["git", "init", "-q", str(wrong)], check=True)

		# 1. вендоренный publish_ib резолвит СВОЙ проект из чужого CWD
		r = sh(proj / ".zcode" / "1c" / "scripts" / "publish_ib.sh", wrong, "status")
		_lib.check(
			"publish_ib status из чужого CWD смотрит в свой проект (exit 1 «не поднята», не exit 2)",
			r.returncode == 1 and "не поднята" in r.stderr,
			f"rc={r.returncode}, stderr={r.stderr[:200]!r}",
		)
		_lib.check(
			"чужой CWD не загрязнён build/",
			not (wrong / "build").exists(),
			"в wrongrepo появился build/",
		)

		# 2. скрипт без контурного окружения — громкий отказ, не молчаливая работа
		bare = tmp / "bare" / "scripts"
		bare.mkdir(parents=True)
		shutil.copy(pub, bare / "publish_ib.sh")
		shutil.copy(run, bare / "run_e2e.sh")
		r = sh(tmp / "bare" / "scripts" / "publish_ib.sh", wrong, "status")
		_lib.check(
			"publish_ib без маркеров проекта → exit 2 с внятной причиной",
			r.returncode == 2 and "не похож на проект контура" in r.stderr,
			f"rc={r.returncode}, stderr={r.stderr[:200]!r}",
		)

		# 3. run_e2e из чужого git-репо → exit 2 (раньше: молча взял бы чужой корень)
		r = sh(bare / "run_e2e.sh", wrong, "main")
		_lib.check(
			"run_e2e из чужого git-репо → exit 2 с внятной причиной",
			r.returncode == 2 and "не похож на проект контура" in r.stderr,
			f"rc={r.returncode}, stderr={r.stderr[:200]!r}",
		)

		# 4. валидация не даёт ложного срабатывания на настоящем проекте:
		#    из КОРНЯ проекта run_e2e проходит ROOT-проверку и падает дальше
		#    на своей следующей (нет profiles.yaml → bootstrap-канон)
		r = sh(proj / ".zcode" / "1c" / "scripts" / "run_e2e.sh", proj, "main")
		_lib.check(
			"run_e2e в контурном проекте из его корня проходит ROOT-валидацию (падает дальше на profiles)",
			r.returncode == 2 and "profiles.yaml" in r.stderr and "не похож" not in r.stderr,
			f"rc={r.returncode}, stderr={r.stderr[:200]!r}",
		)
	finally:
		shutil.rmtree(tmp, ignore_errors=True)
	sys.exit(_lib.summary())


if __name__ == "__main__":
	main()
