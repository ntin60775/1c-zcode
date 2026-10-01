# Карта переноса 1c-omp → 1c-zcode

Донор: `../1c-omp`. Здесь — куда ложится каждый слой донора на механики ZCode
и что с ним. Правило переноса: копируется смысл, механика переводится
(см. AGENTS.md корня).

## Сделано (v0.2.0)

| Слой 1c-omp | Механика ZCode | Что вышло |
|---|---|---|
| `package.json` → `omp.extensions` | `.zcode-plugin/plugin.json` | манифест: имя, версия, `skills`, `hooks`, `commands` |
| `unica-gate.ts` (весь) | `PreToolUse`/`PostToolUse` process-скрипты | `hooks/unica_source_gate.py`: маршрутизация правки + rebuild-инвариант (расширение `fullRebuild:true`, main — инкрементально) + worktree-check + замок ИБ + аудит правки allowlist'а |
| `lib/ib-lock.ts` | файловый замок по `session_id` | `hooks/ib_lock.py`: ключ sha1(connection+tree), TTL 20 мин (pid-проверка дона неприменима — процессы хуков одноразовые), освобождение на `PostToolUse`, выметание на `SessionStart` |
| `session_shutdown`-уборка | `SessionStart`-хук | `hooks/session_clean.py`: просроченные замки + состояние гейтов старше 7 дней |
| `doc-gate` (строгий проектный вариант боевого проекта) | состояние по `session_id` | `hooks/doc_gate.py`: мутации юники — только после непустого `documentation.*`/`standards.*`; мутация метаданных сбрасывает флаг |
| `retry-gate` | `PostToolUseFailure`/`PostToolUse` + состояние | `hooks/retry_gate.py`: третий идентичный повтор после двух ошибок — блок |
| `mcp-gate` | fail-closed контур | `hooks/mcp_gate.py`: новый стек — `1c-testpilot` (`tc_execute_*`) и `1c-db` (execute_code и др.); признак `contour:` в v8project\*.yaml или `.zcode/contour`; REST-toolkit через bash тоже распознаётся |
| `session_shutdown` отсутствует | — | осознанная замена: TTL + SessionStart (см. выше) |
| проза правил (свернуто) | навыки + генерируемая секция AGENTS.md | `skills/1c-contour` (инварианты, гейты, приоритеты качества), `templates/agents-section.md` |
| `1c-project-bootstrap` (навык + скрипты) | навык + скрипты | `skills/1c-project-bootstrap`, `scripts/{wire_config.py,init_worktree.sh}`, `scripts/mcp_fragments.json` |
| `unica-test-contour` | НЕ переносится — заменён | новый стек: testpilot/mcp-toolkit (полный переход); взамен — `skills/1c-test-contour` + workflows `1c-e2e-author`/`1c-e2e-run` (write-first: агент пишет тест, readback, ЖР, приёмка-код) |
| `1c-mcp-server` (MCP_Сервер) | НЕ переносится — заменён | MCP `1c-db` (1c-mcp-toolkit) + `skills/1c-db-data`; прод-гейт расширен на оба новых сервера |
| стайл-гейт (новое, в omp — TTSR-правило) | `PostToolUse` | `hooks/bsl_style_gate.py`: чекер пака 1c-bsl-code-style по изменённым `.bsl`, advisory-отчёт; приоритет пака над стандартами/диагностикой — в `1c-contour` |
| поставка | каталог + deploy-runner | `deploy.json` (контракт v1 sot-zcode-marketplace), вендоринг в `<repo>/.zcode/`; self-`marketplace.json` удалён |
| миграция со старого контура | скрипт + команда | `scripts/migrate_from_omp.py`, `/1c-migrate-from-omp`, `docs/MIGRATION-FROM-OMP.md` |

## Переход на поверхность Unica 0.13 (v0.2.1)

Разведка по докам/спекам 0.13.0-rc.3 (v8-runner 0.5.1 → 0.11.2, смена репо
раннера) показала: MCP-поверхность сжата до глаголов
`run/view/check/apply/search/docs/diff/resolve/task.*`; каталог
сценариев-блоков — https://ingvarconsulting.github.io/unica/scenarios.html .
Правила 0.12 устарели, контур пересобран:

| Было (0.12-донор) | Стало (0.13) |
|---|---|
| гейт матчит `runtime_execute`/`code_patch`/… | имена/операции — данные: `contour_common.UNICA_TOOLS_DEFAULT` + override `contour.json → unica.*`; дефолты 0.13 (`run`/`apply`/`docs`) |
| инвариант пересборки зашит в код (fullRebuild) | правила-данные `unica.rebuild_rules` в contour.json; **дефолт пуст** — semantics `push {force, full}` отличается, включается проектом после сверки на стенде |
| doc_gate: сверка `documentation_search`/`standards_*` | сверка `unica.docs`/`search`; мутации — `apply` и `run {op: push/upload/apply/reset}`; структурность мутации — по ops (`code.*` — серия кода, флаг не сбрасывает) |
| парсинг `infobase.connection` | обе формы: `infobase.connection` (0.12) и `infobases.origin.connection` (0.13) |
| `builder: DESIGNER/IBCMD` | в скиллах: ключ отвергается схемой 0.13, per-operation `providers`; практика проектов: файловая — ibcmd, серверная — дизайнер |
| публикации для тестов | в скиллах: веб-публикация вне поверхности 0.13; тест-контуру не нужна (testpilot поднимает клиент сам, toolkit живёт в сессии) |

Маршрутизация в скиллах переписана с «файл → инструмент» на модель
сценариев-блоков: чтение — `search/view/diff/resolve/docs`, правка —
`apply` план→запись (канон S159: search → view → план → запись → check/diff).

## Не начато / сознательно не переносится

1. **`unica-project-setup`** (authoring `v8project.yaml`): у юники 0.13 свои
   скиллы (`unica:cf-init`, `unica:cfe-init` и др.) — дублировать нечего;
   наш bootstrap ссылается на них.
2. **Vanessa/YAxUnit-обвязка** (`test-yaxunit.sh`, `xvfb-run-1c.sh`,
   `yaxunit.json`, VAParams-генератор): полный переход на testpilot;
   legacy-файлы проектов не удаляются, помечаются legacy при миграции.
3. **`UserPromptSubmit`-пуши** (замена TTSR): не делаем без доказанной нужды.
4. **Юнит-тесты от агента**: не поддерживаются контуром (внутренний оракул) —
   e2e + ЖР вместо них.

## Проверки на живом стенде (не закрыты без 1С)

- фактические короткие имена `mcp__unica__*` снять живым `tools/list`
  (дефолты гейтов — из донора; сервер юники в ZCode называется `unica`);
- семантика exit 2 у `PostToolUse` (для bsl_style_gate точка роста
  `HARD_BLOCK`);
- формат hooks в `.zcode/config.json` (wire_config) и подхват вендоренных
  `.zcode/workflows/*.dwf.ts` (ListSavedWorkflows);
- видимость workspace-MCP субагентами workflow.

## Осознанные различия

- **Нет слоя `rules/`**: AGENTS.md проекта (push) + навыки (pull);
  секция контура в AGENTS.md — между маркерами, владелец — плагин.
- **Замки и состояние — вне процесса**: всё состояние по `session_id` в
  `~/.zcode/state/1c/…`; гейты не имеют памяти между запусками.
- **Тесты на python3**: гейт — python-скрипт, тестируем тем же рантаймом.
- **Мягкая зависимость от юники**: `UNICA_MIN=0.13.0` в `scripts/doctor.py`
  + `docs/UNICA.md`; формальная dependency не используется (нет ranges,
  переключение каналов ручное).
