#!/usr/bin/env bash
# install_kd_rules.sh — установка станции kd-rules-mcp: MCP-сервер правил
# обмена «Конвертации данных» 2/3 (апстрим egordoronchenko/kd-rules-mcp, MIT).
#
# Станция одна на машину: $HOME/.local/share/1c-zcode/kd-rules-mcp — код из
# вендор-копии контура (vendor/kd-rules-mcp), окружение uv (.venv рядом).
# Состояние станции (projects.yaml, projects.local.yaml, workspace/, cache/)
# переустановкой не трогается. MCP «1c-kd» в .zcode/config.json проекта
# указывает на её URL (http://127.0.0.1:8060/mcp) — после установки
# перезапусти сессию ZCode; сам сервер поднимает start_kd_rules.sh.
#
# Источник кода, по порядку:
#   1. vendor/kd-rules-mcp рядом со скриптом (раскладка репо контура);
#   2. уже установленная станция (только проверка версии);
#   3. тарбол релиза v$KD_PIN с GitHub (раскладка вендоренного проекта).
#
# Idempotent: повторный запуск пересинхронизирует код и пересоберёт .venv
# по uv.lock. Обновления затирают прежний код — сосуществования версий нет.
#
# Переменные: KD_RULES_STATION — другой путь станции.
set -euo pipefail

KD_PIN="1.9.1"
HERE="$(cd "$(dirname "$0")" && pwd)"
VENDOR="$HERE/../vendor/kd-rules-mcp"
STATION="${KD_RULES_STATION:-$HOME/.local/share/1c-zcode/kd-rules-mcp}"

command -v uv >/dev/null || {
	echo "нет uv — поставь: https://docs.astral.sh/uv/ (uv сам скачает Python 3.12)" >&2
	exit 1
}

# ── источник кода ────────────────────────────────────────────────────────────
src=""
if [ -f "$VENDOR/pyproject.toml" ]; then
	src="$VENDOR"
	echo "→ источник: вендор-копия контура ($VENDOR)"
elif [ -f "$STATION/pyproject.toml" ]; then
	src="$STATION"
	echo "→ вендор-копии рядом нет; проверяю уже установленную станцию ($STATION)"
else
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' EXIT
	echo "→ качаю релиз v$KD_PIN с GitHub"
	curl -fsSL "https://github.com/egordoronchenko/kd-rules-mcp/archive/refs/tags/v$KD_PIN.tar.gz" \
		| tar -xz -C "$tmp"
	src="$tmp/kd-rules-mcp-$KD_PIN"
	echo "→ источник: тарбол v$KD_PIN"
fi

seen="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$src/pyproject.toml" | head -1)"
if [ "$seen" != "$KD_PIN" ]; then
	echo "ПИН НЕ СОВПАЛ: источник $seen, ожидается $KD_PIN — синк вендора не завершён" >&2
	exit 1
fi

# ── код в станцию (состояние станции не трогаем) ────────────────────────────
mkdir -p "$STATION"
rsync -a --delete \
	--exclude .git --exclude .venv --exclude cache --exclude workspace \
	--exclude projects.yaml --exclude projects.local.yaml \
	--exclude '*.log' \
	"$src/" "$STATION/"
echo "→ код синхронизирован: $STATION (пин $KD_PIN)"

# ── окружение ────────────────────────────────────────────────────────────────
echo "→ uv sync (первый раз качает Python 3.12 и колёса — минуты)"
(cd "$STATION" && uv sync --no-dev -q)

(cd "$STATION" && uv run --no-sync python -c "from importlib.metadata import version; print('✓ kd-rules-mcp импортируется, версия', version('kd-rules-mcp'))")
echo "✓ станция готова: $STATION"
echo "  дальше: start_kd_rules.sh — поднять сервер; проект подключает kd_register_project.py из корня 1С-проекта"
