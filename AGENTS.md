# 1c-zcode — вход для агента

ZCode-плагин контура 1С-разработки: нативный порт 1c-omp. Статус слоёв —
docs/migration-map.md; каналы юники — docs/UNICA.md; миграция проектов —
docs/MIGRATION-FROM-OMP.md.

## Что знать до правки

- **Специфики проектов здесь быть не должно.** Ни имён баз, ни учёток, ни
  объектов, ни путей. Всё зависящее от проекта читается из `v8project.yaml` /
  `v8project.local.yaml` / `.zcode/1c/contour.json` либо живёт в самом
  проекте отдельным файлом.
- **Хуки — это hooks.json + скрипты, не omp-расширения.** Семь событий
  (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`,
  `PostToolUse`, `PostToolUseFailure`, `Stop`), матчер — regex по имени
  инструмента, блок = exit 2 с причиной в stderr, таймауты — `timeoutMs`.
  Вызовы MCP видны хукам как `mcp__<server>__<tool>`. После правки гейта —
  тест + ручной прогон stdin → код возврата.
- **Хуки ZCode stateless.** Каждый запуск — новый процесс; состояние сессии
  (флаг сверки doc-гейта, счётчик ретраев, замки ИБ) — внешне по `session_id`
  в `~/.zcode/state/1c/` (для тестов — `ZCODE_1C_STATE_DIR`), не в памяти.
- **Общий слой гейтов — hooks/contour_common.py** (журнал, состояние, пути,
  имена MCP-инструментов). Новый гейт начинает с него, не с копипасты.
- **Имя плагина = имя каталога = `^[a-z0-9][a-z0-9._-]{0,127}$`.** Версия в
  `.zcode-plugin/plugin.json` синхронна записи в sot-zcode-marketplace
  (self-`marketplace.json` удалён — его формат отбивается схемой каталога).
- **deploy.json — контракт v1 sot-zcode-marketplace**: версии в нём нет
  (пин-тег — единственное место), mappings только в `.zcode/…`.
- **Прозу правил omp сюда не копировать как есть.** Всегда-загружаемое —
  секция AGENTS.md целевого проекта между маркерами BEGIN/END 1C CONTOUR
  (`templates/agents-section.md`), остальное — навыки с честным description.
- **Скиллы — под слоты ZCode:** ключи frontmatter `name`, `description`,
  `when_to_use`, `license`, `metadata`; description ≤ 1024 симв., триггер —
  в первых ~250. Проверка: `python3 scripts/lint_skill_frontmatter.py`.
- **Приоритет качества: пак `1c-bsl-code-style` над стандартами (v8std/БСП)
  и стат-анализом** — это правило контура, отражать в скиллах и гейтах.
- **Лицензии:** тексты testpilot (AGPL) и mcp-toolkit (GPL) не заимствуются;
  agent-kit и ssl-skills (MIT) — можно, с фиксацией источника.

## Проверка перед коммитом

```bash
# 1. Ни одного имени чужого проекта, базы, учётки или объекта.
#    Сами имена — в .forbidden-names.txt (gitignored, одна строка = grep-паттерн):
PAT=$(cat .forbidden-names.txt 2>/dev/null)
[ -z "$PAT" ] && echo 'НЕТ .forbidden-names.txt — заведи его, проверка не выполнена' \
  || (grep -rnE "$PAT" --include='*.md' --include='*.py' --include='*.json' . \
        --exclude-dir=.git --exclude='.forbidden-names.txt' \
      && echo 'НАЙДЕНО — править' || echo 'чисто')

# 2. Все гейты и разводка: тесты гоняют хуки как процессы (stdin → exit)
for t in tests/test_*.py; do python3 "$t" || echo "ПРОВАЛ: $t"; done

# 3. Скиллы под слоты ZCode
python3 scripts/lint_skill_frontmatter.py
```

## Донор и соседи

- `../1c-omp` — донор; перенос послойно по docs/migration-map.md, механику
  переводить (`.omp/` → `.zcode/`, `return { block: true }` → `exit 2` +
  stderr, TS → python3 stdlib). Vanessa/YAxUnit/MCP_Сервер не переносятся:
  им замена — testpilot + mcp-toolkit.
- `../1c-bsl-code-style` — пак стиля (deploy.json там же, вендорится рядом).
- Зеркало `1c-ssl-skills` (БСП) — отдельный репо; минимальный diff над
  апстримом (процедура синка — UPSTREAM.md там).
- sot-zcode-marketplace — каталог: тег → пин → `validate --fetch` →
  `deploy-plugin.py install/check/remove`.
