# 1c-zcode

ZCode-плагин контура 1С-разработки: исполняемый гейт исходников Unica и навыки
контура. Порт [1c-omp](../1c-omp) на механики ZCode. **Сейчас скелет** —
перенесён один слой (гейт прямой правки исходников), план остального — в
[docs/migration-map.md](docs/migration-map.md).

Ставится в проект и работает только вместе с Unica (MCP `unica.*`) — без неё
гейт бессмысленен: он блокирует прямую правку исходников 1С, требуя Unica.

## Чем отличается от 1c-omp

Механики расширения у хостов разные, поэтому перенос — не копирование:

| 1c-omp ( omp ) | 1c-zcode ( ZCode ) |
|---|---|
| хуки — TS-модули с `HookAPI`, объявлены в `package.json` → `omp.extensions` | хуки — декларативный `hooks/hooks.json` (7 событий) + process-скрипты |
| блокировка — `return { block: true, reason }` из обработчика | блокировка — код возврата `2`, причина в stderr; `0` — проходит |
| хук-модуль живёт в процессе сессии, состояние в замыкании | хук — одноразовый процесс: состояние сессии (сверка, счётчик ретраев) придётся держать внешне, по `session_id` |
| правила `rules/*.md`: sticky (`alwaysApply`) и TTSR по `condition` | такого слоя нет: всегда-загружаемое — `AGENTS.md` целевого проекта, остальное — навыки (pull по описанию) |
| установка `omp plugin install` из маркетплейса omp | маркетплейс ZCode: локальный каталог, GitHub-репо или каталог-репо |
| журнал `~/.omp/logs/rule-audit.jsonl` | журнал `~/.zcode/logs/rule-audit.jsonl` |
| служебные файлы проекта `.omp/…` | `.zcode/…` (эскалации гейта — `.zcode/unica-gate-escalations.txt`) |

## Состав

| Путь | Что |
|---|---|
| `.zcode-plugin/plugin.json` | манифест плагина: имя, версия, компоненты |
| `hooks/hooks.json` | `PreToolUse` на `Write\|Edit\|ApplyPatch\|Bash` → гейт |
| `hooks/unica_source_gate.py` | гейт исходников: блокирует правку `src/{cf,cfe,epf,erf}`, `tests/{cfe,epf}` мимо Unica; эскалация allowlist'ом, аудит в журнал |
| `skills/1c-contour/SKILL.md` | входной навык контура: инварианты и маршрутизация файл → инструмент Unica |
| `tests/test_unica_source_gate.py` | тесты гейта: процесс + stdin JSON → вердикт |
| `docs/migration-map.md` | послойная карта переноса 1c-omp → 1c-zcode со статусами |
| `marketplace.json` | локальный каталог для установки из клона |

## Установка (локально, из клона)

1. Склонируйте репозиторий.
2. ZCode → **Plugin Marketplace → Add → Add Plugin Marketplace** — вставьте
   путь к **корню клона** (там лежит `marketplace.json`).
3. **Personal → 1c-zcode → Install**.
4. **Settings → Plugins** — у плагина виден хук `unica_source_gate.py`, и он
   runnable. Хук плагина включает раннер хуков автоматически; отдельно ничего
   включать не нужно.

Если локальный каталог не примет запись с `source: "./"` (плагин в корне
каталога), публикация — отдельным каталог-репо, как `sot-omp-marketplace` для
1c-omp; сами файлы плагина при этом не меняются.

Проверка после установки: попытка `Write` в `src/cf/…` блокируется, а в
`~/.zcode/logs/rule-audit.jsonl` появляется запись с `"rule":
"unica-source-gate"`. Отказ без записи в журнале — гейт не загрузился.

## Зависимость: Unica

Плагин не работает сам по себе: правка 1С идёт через Unica MCP, а это
сторонний плагин (`IngvarConsulting/unica-marketplace`, под ZCode — канал
unica-next). Механизма зависимостей нет, поэтому при развёртывании контура
Unica ставят первым. Без Unica гейт этого плагина отрежет единственный путь
правки исходников.

## Проверить без 1С

Гейт — обычный Python, платформа и база не нужны:

```bash
python3 tests/test_unica_source_gate.py          # 14 проверок
echo '{"tool_name":"Write","tool_input":{"file_path":"src/cf/x.bsl"},"cwd":"."}' \
  | python3 hooks/unica_source_gate.py           # exit 2 + причина в stderr
```

## Лицензия

MIT — см. [LICENSE](LICENSE).
