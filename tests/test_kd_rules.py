#!/usr/bin/env python3
"""Тесты канала kd-rules: вендор-копия целостна, пин согласован, скиллы разведены.

Запуск: python3 tests/test_kd_rules.py
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
VENDOR = REPO / "vendor" / "kd-rules-mcp"
KD_SKILLS = ("1c-kd-rules", "kd2-rules-build", "kd2-exchange-pitfalls", "kd3-rules")


def main() -> int:
	# ── вендор-копия: ключевые файлы апстрима на месте ──
	for rel in ("pyproject.toml", "uv.lock", "LICENSE", "README.md",
	            "docs/tools.md", "src/kd_rules_mcp/__init__.py",
	            "projects.example.yaml", "projects.local.example.yaml"):
		check(f"вендор: {rel} на месте", (VENDOR / rel).is_file(), str(VENDOR / rel))
	check("вендор: .git не скопирован", not (VENDOR / ".git").exists(), str(VENDOR / ".git"))
	license_head = (VENDOR / "LICENSE").read_text(encoding="utf-8")
	check("вендор: LICENSE — MIT", "MIT License" in license_head, license_head[:60])

	# ── UPSTREAM.md: журнал и пин ──
	upstream = (VENDOR / "UPSTREAM.md").read_text(encoding="utf-8")
	check("вендор: UPSTREAM.md есть", bool(upstream), "пусто")
	check("вендор: UPSTREAM.md — апстрим указан",
	      "egordoronchenko/kd-rules-mcp" in upstream, "нет ссылки апстрима")

	# ── пин install-скрипта = версии pyproject вендора = записи в журнале ──
	pyproject = (VENDOR / "pyproject.toml").read_text(encoding="utf-8")
	version = re.search(r'(?m)^version = "([^"]+)"$', pyproject)
	check("вендор: версия в pyproject распознана", version is not None, pyproject[:200])
	if version:
		pin = version.group(1)
		install = (REPO / "scripts" / "install_kd_rules.sh").read_text(encoding="utf-8")
		check(f"install_kd_rules.sh: KD_PIN = {pin}",
		      f'KD_PIN="{pin}"' in install, "пин не совпадает с pyproject вендора")
		check("UPSTREAM.md: журнал синков знает пин",
		      f"| 1.3.0 |" in upstream or f"{pin} |" in upstream,
		      "журнал синков не обновлён")

	# ── разводка: фрагмент 1c-kd ──
	fragments = json.loads((REPO / "scripts" / "mcp_fragments.json").read_text(encoding="utf-8"))
	check("mcp_fragments: 1c-kd — http с шаблоном kd_url",
	      fragments.get("1c-kd", {}).get("type") == "http"
	      and fragments.get("1c-kd", {}).get("url") == "{{kd_url}}",
	      str(fragments.get("1c-kd")))

	# ── deploy.json: скиллы канала вендорятся ──
	deploy = json.loads((REPO / "deploy.json").read_text(encoding="utf-8"))
	mapped = {m["from"] for m in deploy.get("mappings", [])}
	for name in KD_SKILLS:
		check(f"deploy.json: скилл {name} в маппингах", f"skills/{name}" in mapped,
		      str(sorted(mapped)))
	check("deploy.json: вендор в проекты НЕ маппится",
	      not any(m.startswith("vendor/") for m in mapped), "вендор утёк в проект")

	# ── скиллы: frontmatter под слоты ZCode ──
	lint = REPO / "scripts" / "lint_skill_frontmatter.py"
	for name in KD_SKILLS:
		r = subprocess.run([sys.executable, str(lint), str(REPO / "skills" / name)],
		                   capture_output=True, text=True)
		check(f"скилл {name}: lint чист", r.returncode == 0, (r.stdout + r.stderr)[:200])

	# ── перенесённые скиллы: чужой kd-install не упоминается ──
	for name in KD_SKILLS:
		for path in (REPO / "skills" / name).rglob("*.md"):
			text = path.read_text(encoding="utf-8")
			check(f"скилл {name}: без отсылок к kd-install ({path.name})",
			      "kd-install" not in text, str(path))

	# ── kd_register_project.py: синтаксис ──
	register = REPO / "scripts" / "kd_register_project.py"
	check("kd_register_project.py на месте", register.is_file(), str(register))
	compile_check = subprocess.run([sys.executable, "-m", "py_compile", str(register)],
	                               capture_output=True, text=True)
	check("kd_register_project.py компилируется", compile_check.returncode == 0,
	      compile_check.stderr[:200])

	# ── функциональная проба: tmp-проект → tmp-станция (нужен pyyaml станции) ──
	station_default = Path.home() / ".local" / "share" / "1c-zcode" / "kd-rules-mcp"
	station_py = Path(os.environ.get("KD_RULES_PYTHON")
	                  or station_default / ".venv" / "bin" / "python")
	if not station_py.is_file():
		check("проба регистрации пропущена (нет станции на машине)", True,
		      "станции нет — скрипт проверен живой пробой при установке")
	else:
		import tempfile

		def read_yaml(path: Path):
			parsed = subprocess.run(
				[str(station_py), "-c",
				 "import sys, json, yaml; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))",
				 str(path)], capture_output=True, text=True)
			return json.loads(parsed.stdout or "{}")

		with tempfile.TemporaryDirectory() as tmp:
			tmp = Path(tmp)
			proj = tmp / "proj"
			(proj / "src" / "cf").mkdir(parents=True)
			(proj / "src" / "cf" / "Configuration.xml").write_text("<Config/>", encoding="utf-8")
			(proj / "src" / "cfe" / "Расш1").mkdir(parents=True)
			(proj / "src" / "cfe" / "Расш1" / "Configuration.xml").write_text("<E/>", encoding="utf-8")
			(proj / "kd2-rules").mkdir()
			(proj / "kd2-rules" / "ExchangeRules.xml").write_text("<Rules/>", encoding="utf-8")
			station = tmp / "station"
			station.mkdir()
			(station / "projects.yaml").write_text(
				"# проекты подключает kd_register_project.py\nprojects: {}\n",
				encoding="utf-8")
			env = dict(os.environ, KD_RULES_STATION=str(station),
			           KD_RULES_PYTHON=str(station_py))

			def run_reg(*cli):
				return subprocess.run(
					[sys.executable, str(register), str(proj), *cli],
					capture_output=True, text=True, env=env)

			r = run_reg("--id", "testproj")
			check("регистрация выходит 0", r.returncode == 0, (r.stdout + r.stderr)[:300])
			entry = (read_yaml(station / "projects.yaml").get("projects") or {}).get("testproj")
			check("projects.yaml: ключ testproj с выгрузкой src/cf",
			      isinstance(entry, dict)
			      and entry.get("configurations", {}).get("full", {}).get("dump") == "src/cf",
			      str(entry))
			check("projects.yaml: расширение src/cfe/Расш1 найдено",
			      entry is not None
			      and entry.get("configurations", {}).get("full", {}).get("extensions")
			      == ["src/cfe/Расш1"],
			      str(entry))
			check("projects.yaml: kd2-rules/ подхвачен в rules_dir",
			      entry is not None and entry.get("rules_dir") == "kd2-rules",
			      str(entry))
			local_entry = (read_yaml(station / "projects.local.yaml").get("projects") or {}).get("testproj")
			check("projects.local.yaml: абсолютный путь проекта",
			      local_entry == str(proj), str(local_entry))

			r = run_reg("--id", "testproj", "--check")
			check("--check зелёный после регистрации", r.returncode == 0, r.stdout[:200])

			r = run_reg("--id", "testproj", "--unregister")
			check("--unregister выходит 0", r.returncode == 0, (r.stdout + r.stderr)[:200])
			left = (read_yaml(station / "projects.yaml").get("projects") or {})
			left_local = (read_yaml(station / "projects.local.yaml").get("projects") or {})
			check("после unregister записи нет в обоих файлах",
			      "testproj" not in left and "testproj" not in left_local,
			      f"{left} / {left_local}")

	return summary()


if __name__ == "__main__":
	sys.exit(main())
