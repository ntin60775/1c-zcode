# KD-RULES — правила обмена КД 2/3 в контуре (MCP `1c-kd`)

Канал работает с правилами «Конвертации данных» 2 (ПравилаОбмена 2.01, правила
регистрации) и «Конвертации данных» 3 (EnterpriseData, модуль менеджера обмена)
через вендоренную копию [kd-rules-mcp](https://github.com/egordoronchenko/kd-rules-mcp)
(MIT, © egordoronchenko). Сервер детерминированный: разбирает XML-выгрузки
конфигураций, предлагает кандидатов сопоставления, пишет правила и проверяет их;
решения о соответствии — за агентом, каноны работы — скиллы `kd2-rules-build`,
`kd2-exchange-pitfalls`, `kd3-rules`, канон канала — скилл `1c-kd-rules`.

## Архитектура

```
ZCode-сессия (проект 1С)                     машина
└─ .zcode/config.json → mcp.servers["1c-kd"]  (wire_config: {{kd_url}})
   └─ http://127.0.0.1:8060/mcp  ← станция ~/.local/share/1c-zcode/kd-rules-mcp
      ├─ код из vendor/kd-rules-mcp (install_kd_rules.sh, uv sync, .venv)
      ├─ projects.yaml (+ projects.local.yaml — машинные пути, не в git)
      ├─ workspace/  ← сервер пишет сюда и в rules_dir проектов (writable)
      └─ cache/      ← кэш структур конфигураций (ключ включает хеш загрузчика)
```

Станция одна на машину; в проекты вендорится только скилл-слой и скрипты,
не код сервера. Сервис stateless к сессиям ZCode: рестарт сессии соединение
пересоздаёт, состояние станции (открытые проекты правил — снимки в `workspace`)
не теряет.

## Разводка и конфигурация

- `scripts/mcp_fragments.json`: фрагмент `1c-kd` с шаблоном `{{kd_url}}`;
  `scripts/wire_config.py` подставляет URL из `contour.json → 1c_kd.url`
  (умолчание `http://127.0.0.1:8060/mcp`, каркас создаёт `scaffold_contour`).
- Сервер настраивается только env `KD2_*` (`KD2_HOST/KD2_PORT/KD2_PROJECTS_FILE/
  KD2_WORKSPACE/KD2_CACHE_DIR/KD2_TOKEN/KD2_LOG_LEVEL`) — их задаёт
  `start_kd_rules.sh`; в конфиге проекта настраивается лишь URL.
- `projects.yaml` станции — описания 1С-проектов (выгрузки Конфигуратора, базы,
  `rules_dir`, обмены); `projects.local.yaml` — пути машины и логины, в git не
  попадает. Первый старт копирует `projects.example.yaml → projects.yaml`.

## Эксплуатация

```bash
scripts/install_kd_rules.sh     # код станции + uv sync (пин сверяется с pyproject)
scripts/start_kd_rules.sh       # старт; --status/--stop/--print-config; лог станции
python3 scripts/doctor.py .     # секция 2c: станция/живость (мягкая зависимость)
```

Обновление станции: `install_kd_rules.sh` (пересинхронизирует код, состояние не
трогает) + `start_kd_rules.sh --stop` и старт. Кэш структур пересобирается сам,
если код загрузчика сменился (ключ кэша включает его хеш).

## Синк апстрима

Правки вендор-кода запрещены (минимальный diff) — вся интеграция снаружи: env,
скрипты, разводка, скиллы. Новый релиз апстрима — по `vendor/kd-rules-mcp/UPSTREAM.md`
(замена копии, forbidden-names, тесты, пин `KD_PIN` в install-скрипте, журнал).

## Границы

- Пакет MCP comol (vibecoding1c) не поставляется: код обработчиков пишется через
  unica, синтаксис — каналами BSL контура.
- `kd_check`/`bsp_check` (загрузка правил в базу КД/БСП) — опциональные проверки
  Windows с платформой; без них сервер полноценен.
- Апстрим-скилл `kd-install` не переносится (чужой канон установки через Docker/клон) —
  установка контура: `install_kd_rules.sh` + `start_kd_rules.sh`.
