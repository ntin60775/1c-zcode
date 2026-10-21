#!/usr/bin/env python3
"""Тесты канала kd-rules: вендор-копия целостна, пин согласован, скиллы разведены.

Запуск: python3 tests/test_kd_rules.py
"""
import json
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

	return summary()


if __name__ == "__main__":
	sys.exit(main())
