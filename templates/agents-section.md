<!-- BEGIN 1C CONTOUR (1c-zcode) -->
## Контур 1С (1c-zcode)

Владелец секции — плагин 1c-zcode; правится контуром, не руками.
Версия секции следует за тегом контура в `.zcode/deployed.json`.

### Поверхность Unica 0.15

Чтение — `unica.search / view / diff / resolve / docs / check`; правка —
`unica.apply` (план → запись); сборка/ИБ — `unica.run {op, args: {…},
dryRun: true|false}` (аргументы операции — ВЛОЖЕННО в `args`; `dryRun`
обязателен явно, платформенные операции возвращают async-задачу — результат
через `unica.task.result`, waitMs ≤ 7000); источник
контракта — словарь `unica.run {}` (вызов без op) и словари узлов (`view`).
Канон правки: `search → view → apply (план) → apply (запись) → check/diff`.

Длинные ответы 0.15 отдаёт страницами: пока в ответе есть `cursor`, читай
следующую страницу тем же вызовом. Буквальный `search` без `role` идёт до
последнего совпадения, первая страница несёт `data.totals`. Пакет путей —
`unica.resolve {paths: [...]}`, длинное свойство — `<узел>.Props.<имя>`.
`upload`, `apply`, `reset` как операции `unica.run` в 0.15 недоступны
(#1246): загрузка `.cf`/`.cfe` — через ibcmd, не через раннер.

### Инварианты

1. Исходники 1С (`src/{cf,cfe,epf,erf}`) правятся только через Unica —
   прямая правка блокируется гейтом.
   Разовый обход: `.zcode/unica-gate-escalations.txt`.
2. Мутации (`unica.apply`, `unica.run {op: push|upload|apply|reset}`) —
   только после непустой сверки с `unica.docs`. Пустой ответ не считается;
   источники — `platform-help` и `development-standard`,
   `configuration-documentation` в 0.15 отвечает `unsupported_source`;
   принятая задача (`status: working`) — не сверка, пока не дочитана через
   `unica.task.result`; поиск без совпадений — тоже не сверка.
3. Инварианты пересборки — правила проекта (`.zcode/1c/contour.json` →
   `unica.rebuild_rules`); включаются сверкой на стенде. Ключ `builder`
   в v8project.yaml не используется (0.15: per-operation `providers`).
4. Адрес инфобазы и креды — только из `v8project.local.yaml`
   (`infobases.origin.connection`); 0.15 читает базу только из local-слоя,
   поддерживает только `origin`. Ключи `basePath`, `execution_timeout`,
   `partialLoadThreshold` и верхнеуровневый `connection` отвергаются;
   полная загрузка — `push full: true`. Назначение всех полей и регламент
   переезда — навык `1c-project-layout`, `references/v8project.md`.
5. Прод-контур по умолчанию (fail-closed): изменяющие инструменты
   тест-контура блокируются; признак `test` — файл `.zcode/contour` со
   словом test. Ключ `contour:` в `v8project.local.yaml` не писать:
   v8-runner 0.15 отвергает незнакомый верхнеуровневый ключ local-слоя.
6. Третий идентичный повтор упавшей команды заблокирован.

### Качество кода

Форма BSL — пак `1c-bsl-code-style` (приоритет над стандартами и
стат-анализом). Любая BSL-правка: guard `bsl-style-gate` при правке;
формальный ревью `1c-bsl-review-standards` (требования пака + стандарты
v8std, конфликт — приоритет пака) — перед отчётом о завершении и перед
push; пропуск любого — отклонение от процедуры. Вызовы БСП — скилл `bsp`
(БСП 3.1.11). Обзор — скилл `1c-contour` (глаголы 0.15, приоритеты
качества).

### Тесты и данные

Тесты агент пишет сам, все на pytest: юниты в `tests/unit/`, e2e в
`tests/e2e/`. Юниты покрывают уровень логики 1С (execute_code);
e2e — поведение: write-first, readback, ЖР-сверка (workflows
`1c-e2e-author` / `1c-e2e-run`) на уровнях API (HTTP) и GUI
(testpilot). Данные — MCP `1c-db`.
<!-- END 1C CONTOUR -->
