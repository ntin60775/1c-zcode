# Карта переноса 1c-omp → 1c-zcode

Донор: `../1c-omp`. Здесь — куда ложится каждый слой донора на механики ZCode
и что с ним сейчас. Правило переноса: копируется смысл, механика переводится
(см. AGENTS.md корня).

## Сделано

| Слой 1c-omp | Механика ZCode | Что вышло |
|---|---|---|
| `package.json` → `omp.extensions` | `.zcode-plugin/plugin.json` | манифест: имя, версия, `skills`, `hooks` |
| `unica-gate.ts`, ветка edit/write/bash (маршрутизация исходников) | `PreToolUse` (матчер `^(Write\|Edit\|ApplyPatch\|Bash)$`) + `process`-скрипт | `hooks/unica_source_gate.py`: блок прямой правки `src/{cf,cfe,epf,erf}`, `tests/{cfe,epf}`; эскалация `.zcode/unica-gate-escalations.txt`; аудит |
| аудит `~/.omp/logs/rule-audit.jsonl` | то же, в `~/.zcode/logs/` | формат записи сохранён (rule/action/decision/reason/timestamp) |
| вход «Проверить без 1С» (bun-тесты) | python3-тесты, процесс + stdin | `tests/test_unica_source_gate.py` |
| `rules/unica-source-gate.md` (проза маршрутизации) | навык | `skills/1c-contour/SKILL.md` (свернуто в инварианты) |

## Не начато (в порядке ценности)

1. **Инвариант пересборки** (`unica-gate.ts`: расширение — всегда
   `fullRebuild: true`, `main` — никогда). В ZCode вызовы Unica MCP — это
   инструменты с именами вида `mcp__unica__*`: нужен отдельный матчер
   `PreToolUse` на них и разбор аргументов. До уточнения фактических имён
   инструментов unica под ZCode не делается — сначала снять `tools/list`
   живого сервера.
2. **Замок на инфобазу** (`lib/ib-lock.ts`): серверная база одна на все
   деревья. Аналог: `PreToolUse` берёт замок (файл в
   `~/.zcode/state/1c-ib-locks/`, ключ — hash строки подключения),
   `PostToolUse` отпускает для синхронных операций; долгие джобы — до конца
   сессии/TTL. Отдельно решить, чем заменить `session_shutdown`
   (кандидат — `SessionStart`: подчищать протухшие замки своего хоста).
3. **Проверка ворктри** (`unica-gate.ts`: `v8project.local.yaml` +
   `build/tools/` в дереве): тот же `PreToolUse` на вызовы Unica из ворктри.
4. **doc-gate** («сверка перед записью»): в omp — флаг в замыкании модуля;
   здесь — файл состояния по `session_id` из входа хука (хуки stateless).
   Мутирующий вызов без записи о сверке в состоянии → блок.
5. **retry-gate** (третий идентичный повтор): состояние «команда → подряд
   ошибок» внешне по `session_id`; ошибки ловит `PostToolUseFailure`.
6. **mcp-gate** (изменяющие инструменты `MCP_Сервер` в прод-контуре):
   распознавание вызова в `Bash`/`Write`; признак контура — `.zcode/contour`
   или ключ в `v8project.local.yaml`; fail-closed.
7. **Навыки донора** (`1c-project-bootstrap`, `unica-project-setup`,
   `unica-test-contour`, `1c-mcp-server`): переносить по одному, проверяя,
   что команды omp-специфичные заменены (`omp plugin …` → маркетплейс ZCode).
   Скрипты навыков (bash/python) переносятся почти как есть.
8. **Проза правил** (`test-contour`, `ib-contour`, `worktree-env`,
   `unica-mcp`, стиль BSL…): sticky-блоки — в AGENTS.md целевого проекта
   (шаблоном в bootstrap-навыке), операционные — справочниками внутри
   соответствующих навыков.
9. **Поставка**: отдельный каталог-репо (аналог `sot-omp-marketplace`) вместо
   локального `marketplace.json` в корне.

## Осознанные различия

- **Нет слоя `rules/`.** Механики TTSR/sticky в ZCode нет; её замена —
  AGENTS.md проекта (push, всегда в контексте) и навыки (pull, по
  description). Дублировать omp-поведение «правило срабатывает по потоку»
  можно `UserPromptSubmit`-хуком с `additionalContext`, но это push в каждый
  промпт — делать только при доказанной нужде.
- **Замки и состояние — вне процесса.** В omp хук-модуль жил в сессии; здесь
  каждый запуск — новый процесс. Все будущие гейты с состоянием проектируются
  от файла состояния, не от памяти.
- **Тесты на python3**, не bun: гейт — python-скрипт, тестируем тем же
  рантаймом, что и исполняет хук.
