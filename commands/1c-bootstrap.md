---
description: Подключение проекта 1С к контуру: contour.json, profiles.yaml, разводка config.json, секция AGENTS.md, gitignore
---

Подними контур в проекте по навыку `1c-project-bootstrap` (он же — после
каждого обновления вендоренных файлов deploy-runner'ом):

1. Прочитай `.zcode/skills/1c-project-bootstrap/SKILL.md` и выполни шаги
   1–7 для этого проекта (корень = cwd).
2. Шаблоны бери из `.zcode/1c/templates/`; секцию AGENTS.md — только свою,
   между маркерами `<!-- BEGIN 1C CONTOUR (1c-zcode) -->` /
   `<!-- END 1C CONTOUR -->`, чужие секции не трогай.
3. Закончили — прогони `/1c-doctor` и добейся зелёного.
