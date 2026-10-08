# Каталог tests/e2e — e2e-тесты контура (pytest)

Три уровня: логика (`execute_code` через MCP 1c-db), API (HTTP-публикация),
GUI (1c-testpilot). Тесты пишут workflows `1c-e2e-author` / `1c-e2e-run`.

## Правила
- Стиль: write-first, readback, сверка ЖР; данные фикстур тесты создают и удаляют сами.
- Прогон GUI: `bash .zcode/1c/scripts/run_e2e.sh <профиль>`; профили — `.zcode/testpilot/profiles.yaml`.
- База тестового контура без пользователей — авторизация не нужна, пока не появились пользователи.
