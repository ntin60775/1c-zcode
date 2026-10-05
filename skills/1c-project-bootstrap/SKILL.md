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

1. **`.zcode/1c/contour.json`** — контурный конфиг. Каркас env-ключей
   (`1c_db.url`, `1c.publish.ibsrv` — по факту машины) создаёт
   `wire_config.py` (шаг 3) сам; руками дозаполняй проектные ключи из
   шаблона `.zcode/1c/templates/contour.json` — профиль testpilot,
   `http_services`, `mcp_gate.dangerous`, `style.pack`. Существующие
   значения wire не затирает.
2. **`.zcode/testpilot/profiles.yaml`** — профили тестовых баз (если нет):
   по образцу `.zcode/1c/templates/profiles.example.yaml`. Пароли — только
   `password_env`; значения переменных живут вне git (окружение машины).
   У профиля для e2e нужен `base` (путь к тестовой копии базы) и
   `desktop: isolated`. E2E-тесты живут в `tests/e2e/` проекта
   (workflow `1c-e2e-author` пишет их сам); XML-сценарии (`uilog`) —
   разовые smoke, не носитель регресса.
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
8. **E2E/API-ритуал** (после зелёного doctor): GUI-e2e — прогон
   `bash .zcode/1c/scripts/run_e2e.sh <профиль>`; API — публикация
   `bash .zcode/1c/scripts/publish_ib.sh start` (порт в `contour.json →
   1c.publish.port`), тесты дергают `PUBLISH_URL` из `E2E_PUBLISH_JSON`.
   Первые тесты пишет workflow `1c-e2e-author`.

## Ворктри

Ворктри наследует контур через git (checkout `.zcode/`), но НЕ наследует
игнорируемые файлы: `.zcode/1c/scripts/init_worktree.sh <ворктри>` копирует
`v8project.local.yaml`, `build/tools/`, разбирается с базой. Флаг
`--test-contour` помечает дерево тестовым.

## Машинный слой (один раз на машину, не в проект)

Скрипты ставят окружение, doctor его проверяет:

- `bash .zcode/1c/scripts/install_testpilot.sh` — venv с
  `1c-testpilot[allure]` (e2e: MCP-команда + pytest-прогоны), симлинк в
  `~/.local/bin`;
- `bash .zcode/1c/scripts/install_1c_mcp_proxy.sh` — venv python-прокси
  `1c-db` (встроенный транспорт тулкита — Windows-only, на Linux только
  прокси; клиент подключается сам через `startup;mode=proxy`);
- `bash .zcode/1c/scripts/doctor.py <проект>` — итог зелёный;
- `xvfb` в системе обязателен (`desktop: isolated` = приватный Xvfb;
  Debian/Ubuntu: `sudo apt install xvfb`).

## Чего НЕ делает bootstrap

- Не ставит Unica (внешний плагин, `unica@unica-next`/`unica` — см.
  `docs/UNICA.md` в репо контура); машинный слой (testpilot, прокси 1c-db,
  xvfb) — см. «Машинный слой» выше, это машина, не проект.
- Не трогает `v8project.yaml` (внешний контракт v8-runner/unica).
- Не пишет креды никуда, кроме `password_env`-переменных.
