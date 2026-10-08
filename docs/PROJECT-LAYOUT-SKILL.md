# Дизайн навыка 1c-project-layout — топология каталогов 1С-проекта

Статус: реализовано (2026-10-08, `skills/1c-project-layout/`; тесты —
`tests/test_project_layout.py`). Источник требования — живой деплой
2026-10-08: скелет каталогов собирается агентом вручную по единственному
эталону, решения «что создавать» не воспроизводимы, а шаблоны git-гигиены
и каркас `v8project.yaml` каждый раз открываются заново.

## 1. Зачем

Донор `1c-omp` имел `bootstrap.py` с топологией vanessa-эры; при порте на
ZCode перенесены только контурные конфиги (`wire_config.py`,
`init_worktree.sh`), а топология выпала. migration-map.md при этом помечает
перенос навыка как сделанный — провал не виден. Что это стоило на живом
деплое:

- скелет строился руками по живому образцу, ответы «переносить ли
  `features`/`fixtures`/`tasks`» принимались на месте, без закрепления;
- первый коммит упал на `unica.check` (`repositoryReady: false`) — нет
  `.gitattributes`-политики для CRLF/PNG, шаблон искали в образце;
- каркас `v8project.yaml`/`v8project.local.yaml` и грабли GTK/strict-версии
  платформы открывались эмпирически (три итерации до рабочего push).

Навык закрывает: воспроизводимую топологию, git-шаблоны, каркас
`v8project.yaml` и закреплённые знания деплоя (GTK-обход, путь платформы
каталогом) — проблем № 4, 5, 6 и частично № 7 из разбора деплоя.

## 2. Решение и отвергнутые альтернативы

**Отдельный навык `1c-project-layout` в этом репо.**

| Вариант | Почему отвергнут |
|---|---|
| Расширить `1c-project-bootstrap` | Разные триггеры и время жизни: скелет — однократная операция, конфиги контура — повторяемая при каждом обновлении вендоренных файлов. Два сценария в одном description ≤ 1024 симв. размоют матчинг скилла. |
| Отдельный репо/плагин | Критерий выделения (выработан на `1c-ibsrv-publish`): свой канал поставки, свой апстрим или свой потребитель. Здесь всё общее с контуром: поставка тем же deploy-runner'ом, потребитель — те же 1С-проекты. Апстрим `vanessa-bootstrap` — только источник идей, не форк. |

Лицензионная гигиена: код-базис — донор `1c-omp` (наш MIT); у
`vanessa-bootstrap` LICENSE — незаполненный BSD-шаблон без правообладателя,
поэтому его тексты не заимствуются, только структура идеи.

## 3. Границы навыка

**Делает:** каталоги-скелеты исходников по типам, каталожные `AGENTS.md`,
корневую таблицу структуры, `.gitignore`/`.gitattributes`, каркас
`v8project.yaml` + шаблон `v8project.local.yaml`, `git init` + remote.

**Не делает (разводка владельцев):**

| Файл/слой | Владелец |
|---|---|
| `.zcode/1c/contour.json`, `profiles.yaml`, `.zcode/config.json`, секция контура в AGENTS.md | `1c-project-bootstrap` |
| `v8project.yaml` как контракт (правки после каркаса), source-set'ы расширений по мере появления | проект + скиллы юники (`cf-init`, `cfe-init`) |
| Первый источник правды (dump ИБ → исходники, build исходники → ИБ) | unica (`unica.run`, канон в `1c-contour`) |
| Миграция omp-наследия (`features`/`fixtures` → `docs/omp-migrated/tests`) | `scripts/migrate_from_omp.py` |
| Vanessa-обвязка (VAParams, packagedef, env.json) | никому: стек не переносится |

## 4. Топология

Принцип: **каталог = source-set юники**. Топология и каркас `v8project.yaml`
рождается одним шагом, а не двумя рассинхронизирующимися.

| Каталог | Назначение | Обязательность |
|---|---|---|
| `src/cf/` | исходники конфигурации (XML-выгрузка, source-set `main`) | да |
| `src/cfe/` | XML-выгрузки расширений, подкаталог на расширение | `--kinds` |
| `src/epf/` | XML-выгрузки внешних обработок, подкаталог = имя обработки | `--kinds` |
| `src/erf/` | XML-выгрузки внешних отчётов, подкаталог = имя отчёта | `--kinds` |
| `tests/e2e/` | pytest e2e: логика (1c-db), API (HTTP), GUI (testpilot) | компонент, дефолт вкл. |
| `tools/mcp/` | `MCP_Toolkit.epf` для 1c-db (копия, вне git) | компонент |
| `docs/` | документация проекта | компонент |
| `build/` | артефакты: файловая ИБ `build/ib`, дампы `*.dt`, `build/tools/` (вне git, только gitignore-строка — каталог не создаём) | да (строка) |

Отброшенное из vanessa-bootstrap/донора и почему:

- `features/`, `fixtures/`, `tests/cfe/`, `tests/epf/` — Vanessa/YAxUnit
  заменены testpilot-e2e; фикстуры создают/удаляют данные сами тесты;
  унаследованное — в `docs/omp-migrated/tests/`, исполнять нельзя;
- `lib/`, `vendor/`, `examples/`, `tasks/`, `env.json`,
  `oscript_modules/` — OneScript вне стека контура;
- `.bsl-language-server.json`, sonar, cliff — форма BSL и качество — пак
  `1c-bsl-code-style` (приоритет контура над стандартами и стат-анализом).

Исключение — `packagedef`: в доноре это дескриптор OneScript-проекта, здесь —
пустой файл-маркер в корне, без расширения и содержимого. 1C Platform Tools
в VS Code распознаёт проект по его наличию: нет `packagedef` — нет
подсветки/языкового сервера. Создаёт scaffold (`structure`), наполнять
содержимым не нужно (контракт проекта — `v8project.yaml` юники, не EDT).

Каталожные `AGENTS.md` вместо `.gitkeep`: пустые каталоги трекаются в git и
сразу несут правила (решение живого деплоя). Тексты — из живого деплоя,
не из донора: `unica.apply` вместо «не редактируй XML руками», EXTENSION
source-set, три уровня тестов.

## 5. Скрипт `scripts/scaffold.py`

Порт донорского `bootstrap.py` (stdlib-only, сабкоманды, идемпотентность,
`--dry-run`, `--force`; существующее не перезаписывается без `--force`).
Имя сменено с `bootstrap.py`, чтобы не сталкиваться с «bootstrap»-языком
навыка `1c-project-bootstrap`.

```
scaffold.py structure --dir <проект> --name <slug>
    [--kinds cf,cfe,epf,erf]        # дефолт: все четыре
    [--components tests-e2e,tools-mcp,docs]   # дефолт: tests-e2e
    [--dry-run] [--force]
scaffold.py v8project --dir <проект> [--ib-connection 'File=build/ib']
    [--dry-run] [--force]
scaffold.py git --dir <проект> [--remote git@host:owner/repo.git]
    [--dry-run] [--force]
scaffold.py all …    # structure + v8project + git
```

Правила поведения:

- **Корневой `AGENTS.md` не перезаписывается.** Нет — создаётся с каркасом и
  таблицей, сгенерированной по факту kinds/components (не статичный шаблон);
  есть — скрипт печатает блок таблицы в stdout для вставки агентом.
  Секцию контура между маркерами не трогает (владелец —
  `1c-project-bootstrap`).
- **`.gitignore` — append-only:** в существующий файл дописываются только
  недостающие строки. Владение строками разведено: layout — 1С-артефакты
  (`build/`, `src/cf/ConfigDumpInfo.xml`, `src/cf/DumpFilesIndex.txt`,
  `src/cf/.build/`, `.build/unica`, `tools/mcp/`), `1c-project-bootstrap` —
  `.zcode`-строки. Обе стороны append-only, конфликтов нет.
- **`.gitattributes`** — целиком за layout (другого владельца в контуре
  нет): текст из проверенного живым `unica.check` файла (`eol=crlf` для
  bsl/xml/os, binary для png/cf/cfe/epf/erf/архивов). Закрывает падение
  `repositoryReady` после первого коммита.
- **`v8project.local.yaml`** — шаблон с закреплёнными знаниями деплоя
  (см. § 6), не 0600: секретов в нём нет, креды появятся — потребуют
  отдельной политики.

## 6. Каркасы (содержание шаблонов)

`v8project.yaml` — проверенный живой деплоем каркас 0.13:

```yaml
workPath: 'build'
execution_timeout: 300000
format: DESIGNER
infobases:
  origin:
    connection: 'File=build/ib'
source-set:
  - name: main
    type: CONFIGURATION
    path: src/cf
  # каждое расширение — отдельный source-set:
  # - name: <ИмяРасширения>
  #   type: EXTENSION
  #   path: src/cfe/<ИмяРасширения>
push:
  partialLoadThreshold: 20
```

`v8project.local.yaml` — шаблон несёт знания, открытые эмпирически:

```yaml
# Локальный оверлей машины: в git не попадает.
# Файловая база на Linux — мутации через ibcmd (headless): пакетный
# конфигуратор падает по GTK в Wayland-сессии (разовый обход:
# env -u WAYLAND_DISPLAY GDK_BACKEND=x11). Ключ builder не используется
# (0.13: per-operation providers).
providers:
  push: ibcmd
  extensions: ibcmd
tools:
  platform:
    path: '/opt/1cv8/x86_64/<версия>'   # каталог установки, БЕЗ strict-пина:
                                        # строгая проверка версии ломается
                                        # на установке без версии в пути
# infobases:
#   origin:
#     user: <пользователь ИБ>          # пароль — не в git, не в чат
# contour: test   # тест-дерево; прод по умолчанию (fail-closed), см. 1c-contour
```

Каталожные `AGENTS.md` — тексты живого деплоя (src/cfe, src/epf, src/erf,
tests/e2e) дословно; для `src/cf`, `tools/mcp`, `docs` — по той же форме.

## 7. Порядок работы (SKILL.md)

1. Определи вход: проект с нуля / ИБ есть, исходников нет / дооснащение
   существующего. Идёмпотентность скрипта покрывает все три.
2. Один блок вопросов оператору с дефолтами: имя, kinds, components,
   remote (как донор — не interrogate по одному).
3. `--dry-run` → показать план → запуск.
4. Дальше по цепочке: контурные конфиги — `1c-project-bootstrap`; первый
   источник правды — unica (ИБ есть → выгрузка в `src/cf`; с нуля → build);
   первый коммит атомарен: скелет + `v8project.yaml` + `.gitignore` +
   `.gitattributes`.

Триггер description: заведение нового 1С-проекта, подъём скелета
каталогов, дооснащение структуры существующего проекта — и явно «не для
создания объектов метаданных и не для контурных конфигов».

## 8. Тесты (`tests/test_project_layout.py`)

Скрипт гоняется как процесс во временном каталоге:

- structure: полный набор kinds+компонентов → каталоги и AGENTS.md на месте;
- подмножество kinds / отключённый компонент → лишних каталогов нет;
- идемпотентность: повторный прогон — 0 создано, всё skip;
- `--force` перезаписывает; `--dry-run` ничего не создаёт;
- v8project: каркас содержит main + EXTENSION-комментарий; существующий
  файл не трогается;
- git: `.gitignore` append — в существующий файл добавляются только
  недостающие строки;
- корневой AGENTS.md: в пустом проекте создаётся с таблицей по kinds;
  в существующем — не перезаписывается, таблица уходит в stdout.

## 9. Поставка

- `deploy.json` + mapping `skills/1c-project-layout → .zcode/skills/…`
  (skills в plugin.json уже каталогом — автоподхват);
- версия — следующий минорный тег контура; правки `1c-project-bootstrap`
  (перекрёстные ссылки) — тем же релизом;
- migration-map.md: строку переноса `1c-project-bootstrap` уточнить —
  «конфиги контура; топология — 1c-project-layout».

## 10. Открытые вопросы имплементации

1. Объявляются ли `epf`/`erf` в `v8project.yaml` 0.13 отдельными типами
   source-set (или живут вне source-set'ов)? Сверить по словарю `unica.run`
   и докам юники; до решения — не объявлять, правки через `unica.apply`.
2. `execution_timeout`/`partialLoadThreshold` — значения из живого деплоя
   (300000/20); сверить с донорским опытом перед фиксацией в шаблоне.
3. Точка роста (не блокирует): проверка топологии в `doctor.py` — наличие
   каталожных AGENTS.md и `.gitattributes` как warn, не fail.
