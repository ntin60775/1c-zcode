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

		# ── 1c-db недоступен: подсказка зовёт bash, не python3 (#14) ──
		scaffold(root, '{"1c_db": {"url": "http://127.0.0.1:9/mcp"}}')
		r = run_doctor(root)
		check("1c-db закрыт — WARN с подсказкой подъёма",
		      "1c-db не отвечает" in r.stdout and "start_1c_db.sh" in r.stdout,
		      r.stdout[:400])
		check("подсказка запускает скрипт через bash",
		      "bash .zcode/1c/scripts/start_1c_db.sh" in r.stdout, r.stdout[:400])
		check("python3 перед bash-скриптом не советуется",
		      "python3 .zcode/1c/scripts/start_1c_db.sh" not in r.stdout, r.stdout[:400])

		# ── 3b. обязательный минимум топологии (зовёт scaffold check) ──
		r = run_doctor(root)
		check("неполный минимум — FAIL с перечнем",
		      "обязательная топология неполна" in r.stdout
		      and "packagedef" in r.stdout and "vendor" in r.stdout,
		      r.stdout[:600])
		check("FAIL ведёт в навык и полный ритуал",
		      "1c-project-layout" in r.stdout and "ПОЛНЫЙ ритуал" in r.stdout,
		      r.stdout[:600])
		for rel in ("src/cf", "src/cfe", "src/epf", "src/erf", "tools", "vendor"):
			(root / rel).mkdir(parents=True, exist_ok=True)
		for rel in ("packagedef", "AGENTS.md", ".gitignore", ".gitattributes"):
			(root / rel).write_text("", encoding="utf-8")
		# юнит-наследие omp-эпохи (erp-mini-кейс): tests/cfe жив, tests/e2e нет
		(root / "tests/cfe/Тесты").mkdir(parents=True, exist_ok=True)
		r = run_doctor(root)
		check("чистое наследие — doctor ведёт в мигратор (#18)",
		      "обязательная топология неполна: tests/e2e" in r.stdout
		      and "migrate_from_omp" in r.stdout, r.stdout[:600])
		check("чистое наследие — без «ПОЛНЫЙ ритуал»",
		      "ПОЛНЫЙ ритуал" not in r.stdout, r.stdout[:600])
		import shutil as _sh
		_sh.rmtree(root / "tests/cfe")
		# канон: каталог рождается первым перенесённым тестом (конвертация из архива)
		(root / "tests/e2e").mkdir(parents=True, exist_ok=True)
		r = run_doctor(root)
		check("полный минимум — OK-строка",
		      "минимум топологии выполнен" in r.stdout, r.stdout[:600])
		check("FAIL топологии исчез", "обязательная топология неполна" not in r.stdout,
		      r.stdout[:600])

		# ── 1b. артефакты рантайма: манифест ↔ кэш (fake HOME) ──
		import json as _json
		SHA86 = "e" * 64

		def make_home(state: str) -> Path:
			home = root / f"fakehome-{state}"
			ver = "0.13.0-rc.7"
			install = home / ".zcode/cli/plugins/cache/unica-next/unica" / ver
			install.mkdir(parents=True)
			(install / "runtime-manifest.json").write_text(_json.dumps({
				"artifacts": {"bsl-analyzer": {
					"version": "0.2.86", "role": "engine",
					"targets": {"linux-x64": {
						"asset": {"sha256": SHA86},
						"files": [{"path": "bsl-analyzer", "sha256": SHA86,
						           "executable": True}],
					}},
				}},
			}, ), encoding="utf-8")
			reg = home / ".zcode/cli/plugins"
			reg.mkdir(parents=True, exist_ok=True)
			(reg / "installed_plugins.json").write_text(_json.dumps({
				"plugins": [{"id": "unica@unica-next", "name": "unica",
				             "version": ver, "installPath": str(install)}],
			}), encoding="utf-8")
			rt = home / ".zcode/cli/plugins/data/unica@unica-next/runtimes"
			if state in ("stale", "ok", "partial"):
				if state == "stale":
					good = rt / f"bsl-analyzer/0.2.67--{'c' * 64}/linux-x64"
					aver, asha = "0.2.67", "c" * 64
				else:
					good = rt / f"bsl-analyzer/0.2.86--{SHA86}/linux-x64"
					aver, asha = "0.2.86", SHA86
				good.mkdir(parents=True)
				binary = good / "bsl-analyzer"
				binary.write_text("#!/bin/sh\n", encoding="utf-8")
				binary.chmod(0o755)
				(good / ".ready.json").write_text(_json.dumps(
					{"artifact": "bsl-analyzer", "version": aver,
				     "target": "linux-x64", "assetSha256": asha}), encoding="utf-8")
			if state == "partial":
				(rt / ".partial/bsl-analyzer").mkdir(parents=True)
			return home

		scaffold(root, "{}")
		r = run_doctor(root, {"HOME": str(make_home("stale"))})
		check("устаревший кэш — FAIL с артефактом и версией",
		      "артефакт bsl-analyzer 0.2.86 отсутствует" in r.stdout, r.stdout[:600])
		check("FAIL называет provider_unavailable", "provider_unavailable" in r.stdout,
		      r.stdout[:600])
		check("FAIL ведёт в скилл 1c-unica-artifacts", "1c-unica-artifacts" in r.stdout,
		      r.stdout[:600])

		r = run_doctor(root, {"HOME": str(make_home("ok"))})
		check("полный кэш — OK-строка", "артефакты манифеста на месте" in r.stdout,
		      r.stdout[:600])

		r = run_doctor(root, {"HOME": str(make_home("partial"))})
		check(".partial — WARN", "остался .partial" in r.stdout, r.stdout[:600])
		check(".partial не роняет чек артефактов",
		      "артефакты манифеста на месте" in r.stdout, r.stdout[:600])

		r = run_doctor(root, {"HOME": str(make_home("empty"))})
		check("нет кэша рантаймов — WARN про первый старт",
		      "кэш рантаймов юники не создан" in r.stdout, r.stdout[:600])
		check("без кэша нет FAIL по артефактам",
		      "отсутствует в кэше" not in r.stdout, r.stdout[:600])

		return summary()


if __name__ == "__main__":
	sys.exit(main())
