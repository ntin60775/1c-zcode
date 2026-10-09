#!/usr/bin/env python3
"""Тесты скилла 1c-unica-worktree: поставка и полнота канона.

Запуск: python3 tests/test_unica_worktree_skill.py
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "1c-unica-worktree"


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
	check("маппинг skills/1c-unica-worktree → .zcode/skills",
	      ("skills/1c-unica-worktree", ".zcode/skills/1c-unica-worktree") in pairs,
	      str(sorted(f for f, _ in pairs)))

	# канон не выветрился: маркеры живых проб обеих сессий
	for marker in (
		"UNICA_HOST_CONTEXT_REQUIRED",       # строгий режим bootstrap
		"UNICA_RUNTIME_MANIFEST",            # вторая строгая переменная
		"CLAUDE_PROJECT_DIR",                # env-канал воркспейса
		"LaunchCwd",                         # cwd-канал standalone
		"The host did not supply workspace context",  # диагноз ошибочного вывода
		"workspaceRootOrigin",               # факт привязки смотрят unica.view
		"structuredContent",                 # данные не в content
		"init_worktree.sh",                  # инициализация воркспейса
	):
		check(f"маркер канона: {marker}", marker in text, "")

	# канон не дублирует враньё про удалённую --cwd-механику
	check("cwd-механика заявлена живой", "не переключает" in text
	      and "standalone" in text, "")

	# перекрёстные ссылки: карта контура и UNICA.md ведут в скилл
	contour = (ROOT / "skills" / "1c-contour" / "SKILL.md").read_text(encoding="utf-8")
	check("1c-contour ссылается на скилл", "1c-unica-worktree" in contour, "")
	unica_doc = (ROOT / "docs" / "UNICA.md").read_text(encoding="utf-8")
	check("docs/UNICA.md ссылается на скилл", "1c-unica-worktree" in unica_doc, "")

	return summary()


if __name__ == "__main__":
	sys.exit(main())
