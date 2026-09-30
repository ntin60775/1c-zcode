---
name: 1c-project-bootstrap
description: "Подключение проекта 1С к контуру 1c-zcode: секция AGENTS.md, разводка .zcode/config.json (hooks+MCP), профили testpilot, contour.json, gitignore, сценарии. Применяй при онбординге проекта на контур или после его обновления (deploy-runner уже разложил файлы)."
when_to_use: "Проект 1С подключается к контуру (впервые или обновление вендоренных файлов), нужно собрать проектные конфиги контура и проверить их /1c-doctor."
---

# Bootstrap проекта 1С на контур 1c-zcode

Файлы контура в проект кладёт deploy-runner sot-zcode-marketplace
(`.zcode/skills/`, `.zcode/hooks/`, `.zcode/1c/scripts/`, `.zcode/workflows/`).
Навык доводит ПРОЕКТНЫЕ файлы — их runner не пишет.

## Порядок

1. **`.zcode/1c/contour.json`** — контурный конфиг (если нет):
   скопируй `.zcode/1c/templates/contour.json`, поправь url `1c-db`
   (машина с 1С) и имя профиля testpilot.
2. **`.zcode/testpilot/profiles.yaml`** — профили тестовых баз (если нет):
   по образцу `.zcode/1c/templates/profiles.example.yaml`. Пароли — только
   `password_env`; значения переменных живут вне git (окружение машины).
   Создай и `.zcode/testpilot/scenarios/` под XML-сценарии.
3. **`.zcode/config.json`** — разводка hooks + MCP:
   `python3 .zcode/1c/scripts/wire_config.py <корень>`. Идемпотентно:
   повторный запуск безопасен, чужие hook-записи сохраняет.
4. **Секция AGENTS.md** — инварианты контура в инструкции проекта:
   возьми шаблон `.zcode/1c/templates/agents-section.md` и вставь между
   маркерами `<!-- BEGIN 1C CONTOUR (1c-zcode) -->` … `<!-- END 1C CONTOUR -->`.
   **Только своя секция**: секцию KB/dev-flow ведёт ontoship-zcode,
   остальной AGENTS.md принадлежит проекту. Обновление = замена секции
   между маркерами.
5. **`.gitignore`** — строки контура (если нет):
   `.zcode/testpilot/profiles.local.yaml`, `tools/VAParams.json`,
   `tools/va-env.local.json`, `v8project.local.yaml`, `build/`,
   `.zcode/contour` (если помечаешь деревья по месту). Сами `.zcode/`
   (скиллы, hooks, config.json, workflows, журнала deploy) — в git.
6. **Признак контура**: тестовое дерево — `contour: test` в
   `v8project.local.yaml` или файл `.zcode/contour` (прод по умолчанию,
   fail-closed).
7. **Проверка**: `/1c-doctor` — все проверки зелёные; тестовый unica-вызов
   (`mcp__unica__project_status` с `cwd` корня) отвечает.

## Ворктри

Ворктри наследует контур через git (checkout `.zcode/`), но НЕ наследует
игнорируемые файлы: `.zcode/1c/scripts/init_worktree.sh <ворктри>` копирует
`v8project.local.yaml`, `build/tools/`, разбирается с базой. Флаг
`--test-contour` помечает дерево тестовым.

## Чего НЕ делает bootstrap

- Не ставит Unica (внешний плагин, `unica@unica-next`/`unica` — см.
  `docs/UNICA.md` в репо контура) и `1c-testpilot` (`pipx install
  1c-testpilot`) — это машина, не проект.
- Не трогает `v8project.yaml` (внешний контракт v8-runner/unica).
- Не пишет креды никуда, кроме `password_env`-переменных.
