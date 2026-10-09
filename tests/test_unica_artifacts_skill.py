#!/usr/bin/env python3
"""Тесты скилла 1c-unica-artifacts: поставка и полнота канона.

Запуск: python3 tests/test_unica_artifacts_skill.py
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "1c-unica-artifacts"


def main() -> int:
	skill_md = SKILL_DIR / "SKILL.md"
	check("SKILL.md существует", skill_md.is_file(), str(SKILL_DIR))
	if not skill_md.is_file():
		return summary()
	text = skill_md.read_text(encoding="utf-8")

	# фронтматтер под слоты ZCode — тем же линтером, что в pre-commit
	lint = subprocess.run(
		[sys.executable, str(ROOT / "scripts" / "lint_skill_frontmatter.py"), str(SKILL_DIR)],
		capture_output=True, text=True)
	check("линтер фронтматтера чист", lint.returncode == 0, lint.stdout[:300])

	# скилл вендорится в проекты (контракт deploy.json v1)
	deploy = json.loads((ROOT / "deploy.json").read_text(encoding="utf-8"))
	pairs = {(m.get("from"), m.get("to")) for m in deploy.get("mappings", [])}
	check("маппинг skills/1c-unica-artifacts → .zcode/skills",
	      ("skills/1c-unica-artifacts", ".zcode/skills/1c-unica-artifacts") in pairs,
	      str(sorted(f for f, _ in pairs)))

	# канон не выветрился: маркеры живого рана (dealer, 2026-10-09)
	for marker in (
		"runtime-manifest.json",                      # контракт артефактов
		"provider_unavailable",                       # отказ неготового провайдера
		"installed_plugins.json",                     # версия+installPath установки
		".ready.json",                                # маркер завершённости артефакта
		".partial",                                   # след оборванной закачки
		"sha256",                                     # сверка при переносе
		"~/.codex/unica/runtimes",                    # соседний кэш установки
		"bsl-analyzer is not bundled in this workspace",  # строка из логов
		"bsl_diagnostics",                            # симптом в статусе
		"Рестарт сервера\nне нужен",                  # свойство починки
	):
		check(f"маркер канона: {marker}", marker in text, "")

	# границы канона: пустой ответ ≠ чисто; перенос легален только после сверки
	check("провайдер отказывает честно (пустой ≠ чисто)",
	      "не выполнялся" in text and "code-diagnostics" in text, "")
	check("перенос требует байт-сверки", "байт-в-байт" in text
	      or "совпасть с манифестом" in text, "")

	# перекрёстные ссылки: карта контура, UNICA.md и doctor ведут в скилл
	contour = (ROOT / "skills" / "1c-contour" / "SKILL.md").read_text(encoding="utf-8")
	check("1c-contour ссылается на скилл", "1c-unica-artifacts" in contour, "")
	unica_doc = (ROOT / "docs" / "UNICA.md").read_text(encoding="utf-8")
	check("docs/UNICA.md ссылается на скилл", "1c-unica-artifacts" in unica_doc, "")
	doctor = (ROOT / "scripts" / "doctor.py").read_text(encoding="utf-8")
	check("doctor ссылается на скилл", "1c-unica-artifacts" in doctor, "")

	return summary()


if __name__ == "__main__":
	sys.exit(main())
