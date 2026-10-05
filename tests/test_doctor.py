#!/usr/bin/env python3
"""Тесты doctor.py: contour.json — distrobox-режим ibsrv, платформа, venv testpilot.

Doctor на tmp-проекте без unica даёт FAIL-строки — тест проверяет не итоговый
exit, а конкретные строки вывода про contour-ключи. Запуск:
python3 tests/test_doctor.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DOCTOR = REPO / "scripts" / "doctor.py"


def run_doctor(root: Path, env_extra=None):
	import os
	env = dict(os.environ)
	env.pop("PUBLISH_IBSRV", None)
	env.pop("TESTPILOT_PYTHON", None)
	env.update(env_extra or {})
	# doctor импортирует contour_common из репо-хуков и вендоренной раскладки;
	# в tmp-проекте раскладки нет — раскладку репо он находит от себя
	return subprocess.run(
		[sys.executable, str(DOCTOR), str(root), "--no-ping"],
		capture_output=True, text=True, env=env, timeout=120)


def scaffold(root: Path, contour: str):
	(root / ".zcode" / "1c").mkdir(parents=True, exist_ok=True)
	(root / ".zcode" / "1c" / "contour.json").write_text(contour, encoding="utf-8")
	(root / "v8project.yaml").write_text("infobases:\n  origin:\n", encoding="utf-8")


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		root = Path(tmp)

		# ── distrobox:auto — легитимный режим, WARN про хост не звучит ──
		scaffold(root, '{"1c": {"publish": {"ibsrv": "distrobox:auto"}}}')
		r = run_doctor(root)
		check("distrobox:auto — OK-строка", "distrobox-режим (auto)" in r.stdout, r.stdout[:400])
		check("WARN про хост-поиск исчез", "на хосте не найден" not in r.stdout, r.stdout[:400])

		# ── distrobox:<имя> ──
		scaffold(root, '{"1c": {"publish": {"ibsrv": "distrobox:onec-dev"}}}')
		r = run_doctor(root)
		check("именованный контейнер отражён в выводе", "onec-dev" in r.stdout, r.stdout[:400])

		# ── 1c.platform.path из contour.json резолвится ──
		fake_platform = Path(tmp) / "fake-1cv8"
		fake_platform.write_text("#!/bin/sh\n", encoding="utf-8")
		scaffold(root, '{"1c": {"publish": {"ibsrv": "distrobox:auto"},'
		               '"platform": {"path": "%s"}}}' % fake_platform)
		r = run_doctor(root)
		check("платформа из contour.json — OK", "(contour.json)" in r.stdout, r.stdout[:400])

		# ── 1c.testpilot.python из contour.json подхвачен ──
		fake_py = Path(tmp) / "fake-python"
		fake_py.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
		fake_py.chmod(0o755)
		scaffold(root, '{"1c": {"publish": {"ibsrv": "distrobox:auto"},'
		               '"testpilot": {"python": "%s"}}}' % fake_py)
		r = run_doctor(root)
		check("venv testpilot из contour.json в выводе", str(fake_py) in r.stdout, r.stdout[:400])

		# ── env перекрывает contour (ibsrv) ──
		scaffold(root, '{"1c": {"publish": {"ibsrv": "distrobox:auto"}}}')
		r = run_doctor(root, {"PUBLISH_IBSRV": str(fake_platform)})
		check("env PUBLISH_IBSRV старше contour", "резолвится: " + str(fake_platform) in r.stdout,
		      r.stdout[:400])

		# ── пустой contour: прежняя WARN-логика хоста ──
		scaffold(root, "{}")
		r = run_doctor(root)
		check("без ibsrv — WARN с подсказкой distrobox:<имя>", "distrobox:<имя>" in r.stdout,
		      r.stdout[:400])

		return summary()


if __name__ == "__main__":
	sys.exit(main())
