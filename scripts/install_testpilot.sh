#!/usr/bin/env bash
# install_testpilot.sh — воспроизводимая установка окружения e2e-контура:
# venv с 1c-testpilot (+pytest/allure для прогонов) и симлинк команды в PATH.
#
# Idempotent: повторный запуск обновляет пакет до пина. MCP-сервер .zcode/config.json
# вызывает команду `1c-testpilot` из PATH — после установки перезапусти сессию ZCode.
set -euo pipefail

PIN="${TESTPILOT_PIN:-1c-testpilot[allure]>=1.8,<2}"
VENV="$HOME/.local/venvs/1c-testpilot"
BIN="$HOME/.local/bin"

if [ ! -x "$VENV/bin/python" ]; then
	echo "→ создаю venv: $VENV"
	python3 -m venv "$VENV"
fi

echo "→ ставлю $PIN"
"$VENV/bin/python" -m pip install -q --upgrade pip
"$VENV/bin/python" -m pip install -q "$PIN"

"$VENV/bin/python" - <<'PY'
import pytest, testpilot
print("✓ testpilot ok, pytest", pytest.__version__)
PY

mkdir -p "$BIN"
ln -sfn "$VENV/bin/1c-testpilot" "$BIN/1c-testpilot"
echo "✓ готово: $BIN/1c-testpilot → $VENV"
echo "  e2e-прогоны: scripts/run_e2e.sh [профиль] [tests/e2e]"
