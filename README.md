# 1c-zcode

ZCode-плагин контура 1С-разработки: гейты, e2e-тест-контур, workflows,
bootstrap и миграция. Нативный порт [1c-omp](../1c-omp) на механики ZCode
(карта переноса — [docs/migration-map.md](docs/migration-map.md)).

Работает вместе с Unica 0.15+ (`mcp__unica__*`, мягкая зависимость — каналы
и переключение в [docs/UNICA.md](docs/UNICA.md)) и новым тест-контуром
1c-testpilot + 1c-mcp-toolkit (объявляются на уровне проекта).

## Состав

| Путь | Что |
|---|---|
| `hooks/` | гейты (python3 stdlib, stateless, состояние в `~/.zcode/state/1c/`): `unica_source_gate` (правка только через Unica + инварианты пересборки + ворктри + замок ИБ), `doc_gate` (мутации после сверки с документацией), `retry_gate`, `mcp_gate` (прод-контур fail-closed), `bsl_style_gate` (пак стайла), `ib_lock`, `session_clean` |
| `skills/` | `1c-contour` (карта контура, приоритеты качества), `1c-bsl-review-standards` (формальный ревью BSL: стайл-пак + v8std), `1c-test-contour` (e2e), `1c-testpilot` (GUI-клиент), `1c-db-data` (данные базы), `1c-ibsrv-publish` (HTTP-публикация), `1c-kd-rules` + `kd2-rules-build` / `kd2-exchange-pitfalls` / `kd3-rules` (правила обмена КД 2/3 через MCP `1c-kd` — [docs/KD-RULES.md](docs/KD-RULES.md)), `1c-unica-worktree` / `1c-unica-artifacts` (каналы юники), `1c-project-bootstrap`, `1c-project-layout` (топология каталогов) |
| `commands/` | `/1c-doctor`, `/1c-test`, `/1c-bootstrap`, `/1c-migrate-from-omp` |
| `workflows/` | `1c-e2e-author` (агент сам пишет e2e-тест), `1c-e2e-run` (прогон pytest без модели + ЖР-сверка и приёмка-кодом) |
| `scripts/` | `wire_config.py` (разводка `.zcode/config.json`), `init_worktree.sh`, `migrate_from_omp.py`, `doctor.py`, `lint_skill_frontmatter.py`, `run_e2e.sh` (автономный e2e-прогон с постпроверкой/зачисткой Е2Е-данных — [e2e_sweep.py](scripts/e2e_sweep.py)), `e2e_sweep.py` (зачистка и проверка остатков по маркеру), `install_testpilot.sh` (+ пакет [1c-onec-db](https://github.com/ntin60775/1c-onec-db) — фикстуры уровня логики), `install_1c_mcp_proxy.sh` (прокси 1c-mcp-toolkit), `start_1c_db.sh` (двухфазный подъём стенда 1c-db: клиент + autostart-флаги + прогрев), `publish_ib.sh` (HTTP-публикация тестовой базы для API-тестов), `unica_ping.py` (живость unica MCP для doctor), `bsl_review_join.py` (скрипт-джойн ревьюера 1c-bsl-review-standards: сохранённые ответы unica.check × routing × журнал исключений → размеченные находки и строки отчёта, [спека](docs/BSL-REVIEW-JOIN-SPEC.md)), `install_kd_rules.sh` + `start_kd_rules.sh` (станция kd-rules-mcp: MCP `1c-kd`, правила обмена КД — [docs/KD-RULES.md](docs/KD-RULES.md)) |
| `vendor/` | `kd-rules-mcp` — вендор-копия апстрима (MIT), источник станции 1c-kd; в проекты не маппится, синк по [UPSTREAM.md](vendor/kd-rules-mcp/UPSTREAM.md) |
| `templates/` | contour.json, profiles.example.yaml, agents-section.md (секция AGENTS.md проекта) |
| `deploy.json` | контракт v1 sot-zcode-marketplace (вендоринг в `<repo>/.zcode/`) |
| `docs/` | migration-map, UNICA (каналы), KD-RULES (правила обмена КД), MIGRATION-FROM-OMP |

## Поставка

Основной путь — **вендоринг раннером** sot-zcode-marketplace в проекты 1С
(контур меняет данные проекта: `.zcode/config.json`, секция AGENTS.md,
профили тестов):

```bash
python3 <sot-zcode-marketplace>/deploy/deploy-plugin.py install 1c-zcode <проект>
```

Глобальная установка из маркетплейса тоже возможна (манифест валиден), но
основной путь — вендоринг: гейты вне 1С-проектов молча no-op.

После установки: `python3 .zcode/1c/scripts/wire_config.py <проект>` +
перезапуск сессии; далее `/1c-bootstrap` (профили, contour.json, секция
AGENTS.md) и `/1c-doctor`.

## Проверить без 1С

Всё исполняемое контура — python3 stdlib, платформа и база не нужны:

```bash
for t in tests/test_*.py; do python3 "$t"; done   # гейты: прогон как процесс
python3 scripts/lint_skill_frontmatter.py         # скиллы под слоты ZCode
python3 scripts/doctor.py <проект>                # проверка контура в проекте
```

## Миграция со старого omp-контура

`/1c-migrate-from-omp` или `scripts/migrate_from_omp.py <проект>` —
атомарная замена `.omp/` на `.zcode/`, перенос эскалаций и аудита, критерий
чистоты. Полная стратегия (пилот → парк, юзер-слой) —
[docs/MIGRATION-FROM-OMP.md](docs/MIGRATION-FROM-OMP.md).

## Соседи

- `1c-bsl-code-style` — пак стиля BSL (приоритет 1 над стандартами и
  стат-анализом), отдельный репо, вендорится рядом.
- `1c-ssl-skills` (зеркало `brake71/1c-ssl-skills`, MIT) — скилл `bsp`:
  публичный API БСП 3.1.11.
- `kd-rules-mcp` ([egordoronchenko/kd-rules-mcp](https://github.com/egordoronchenko/kd-rules-mcp),
  MIT) — вендорится в `vendor/`, ставится станцией на машину (MCP `1c-kd`,
  [docs/KD-RULES.md](docs/KD-RULES.md)).
- Unica — внешний плагин, ставится на машину (см. docs/UNICA.md).

## Лицензия

MIT — см. [LICENSE](LICENSE).
