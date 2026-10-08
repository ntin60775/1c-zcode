---
name: 1c-project-layout
description: "Топология (структура) каталогов 1С-проекта под контур: заведение нового проекта, скелет src/{cf,cfe,epf,erf} + tests/e2e + tools/mcp + docs, каталожные AGENTS.md, .gitignore/.gitattributes, каркас v8project.yaml и шаблон локального оверлея. Дооснащение структуры существующего проекта тоже здесь. Не для объектов метаданных (cf-init/cfe-init у юники) и не для контурных конфигов .zcode/ (1c-project-bootstrap)."
when_to_use: "Заводится новый 1С-проект (пустой каталог), либо существующему проекту недостаёт скелета каталогов / git-шаблонов / каркаса v8project.yaml."
---

# 1c-project-layout — топология каталогов 1С-проекта

Принцип: **каталог = source-set юники**. Топология и каркас `v8project.yaml`
создаются одним шагом, а не двумя рассинхронизирующимися. Дизайн и разводка
владельцев файлов — docs/PROJECT-LAYOUT-SKILL.md репо контура.

## Что делает скрипт

```bash
SKILL_DIR=.zcode/skills/1c-project-layout   # каталог скилла при вызове
python3 "$SKILL_DIR/scripts/scaffold.py" all \
  --dir <проект> --name <slug> \
  --kinds cf,cfe,epf,erf \
  --components tests-e2e,tools-mcp,docs \
  --remote git@github.com:<owner>/<repo>.git
```

Субкоманды (используй по отдельности для дооснащения):

- `structure` — каталоги по `--kinds` (cf/cfe/epf/erf) и `--components`
  (дефолт `tests-e2e`; `tools-mcp` — только когда нужен MCP `1c-db`,
  `docs` — по запросу), каталожные `AGENTS.md` в каждый каталог, корневой
  `AGENTS.md` с таблицей структуры, пустой `packagedef` в корне
  (без расширения; маркер проекта для 1C Platform Tools в VS Code —
  без него расширение не распознаёт проект; содержимое не наполнять);
- `v8project` — каркас `v8project.yaml` (`--ib-connection`, дефолт
  `File=build/ib`) + шаблон `v8project.local.yaml` с закреплёнными знаниями
  деплоя (ibcmd для файловой базы из-за GTK, путь платформы каталогом без
  strict-пина);
- `git` — `git init`, `.gitignore` (только дозапись недостающих строк),
  `.gitattributes` (политика CRLF/binary — без неё `unica.check` роняет
  `repositoryReady` первым коммитом), `--remote`.

Скрипт идемпотентен: существующее не перезаписывается без `--force`
(`.gitignore` — всегда только дозапись). `--dry-run` — показать план.
Корневой `AGENTS.md` не перезаписывается: если существует — таблица
структуры печатается в stdout для вставки агентом.

## Порядок

1. Определи вход: проект с нуля / ИБ есть, исходников нет (тогда `src/cf`
   создаст выгрузка unica — скелет докинет остальное) / дооснащение
   существующего. Идемпотентность покрывает все три.
2. Один блок вопросов оператору с дефолтами: имя проекта, kinds,
   components, remote.
3. `--dry-run` → покажи план → запуск.
4. Первый коммит атомарен: скелет + `v8project.yaml` + `v8project.local.yaml`
   вне git + `.gitignore` + `.gitattributes`.

## Что дальше (не этот навык)

- Контурные конфиги (`.zcode/1c/contour.json`, профили testpilot, разводка
  hooks+MCP, секция AGENTS.md между маркерами) — навык `1c-project-bootstrap`.
- Первый источник правды: ИБ есть → выгрузка в `src/cf` (`unica.run`,
  канон в `1c-contour`); с нуля → `build`. Расширения объявляются
  EXTENSION-source-set'ами по мере появления (скиллы юники `cfe-init`).
- Миграция omp-наследия (features/VAParams) — `scripts/migrate_from_omp.py`.

## Инварианты

1. Каталог = source-set: `path: .` у source-set — ошибка; каждое расширение
   — свой подкаталог `src/cfe/<Имя>` и свой source-set.
2. Каталожные `AGENTS.md` вместо `.gitkeep`: пустые каталоги трекаются в git
   и несут правила.
3. Корневой `AGENTS.md` принадлежит проекту: не перезаписывать, секцию
   контура не трогать.
4. `.gitignore` append-only у всех владельцев: layout — 1С-артефакты,
   `1c-project-bootstrap` — `.zcode`-строки.
5. Секреты (креды ИБ) — только в `v8project.local.yaml`; каркас их не пишет.
