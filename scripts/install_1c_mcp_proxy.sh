#!/usr/bin/env bash
# install_1c_mcp_proxy.sh — установка MCP-прокси 1c-mcp-toolkit в стабильный
# venv (Linux-путь тулкита: встроенный HTTP-транспорт тулкита — Windows-DLL,
# на Linux сервер живёт в python-прокси, а клиент 1С ходит к нему long-poll'ом).
#
# Один раз на машину:
#   install_1c_mcp_proxy.sh [путь-к-клону-1c-mcp-toolkit]
#     (по умолчанию ~/home/dev/contrib/clones/1c-mcp-toolkit; переопределяется
#      env ONEC_MCP_TOOLKIT_DIR)
#
# Что делает: venv ~/.local/venvs/1c-mcp-proxy + зависимости с пинами,
# выверенными на живом стенде: mcp<2 (2.x ломает импорт fastmcp),
# pyahocorasick (отсутствует в requirements тулкита). Запуск сервера —
# start_1c_db.sh, он же поднимает клиент с mode=proxy.
set -euo pipefail

TOOLKIT="${1:-${ONEC_MCP_TOOLKIT_DIR:-$HOME/home/dev/contrib/clones/1c-mcp-toolkit}}"
VENV="$HOME/.local/venvs/1c-mcp-proxy"

if [[ ! -d "$TOOLKIT/onec_mcp_toolkit_proxy" ]]; then
	echo "✗ нет $TOOLKIT/onec_mcp_toolkit_proxy — передай путь к клону тулкита" >&2
	exit 2
fi

python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
# Пины по итогам живого стенда (sess_ce5b1a59): mcp 2.2.0 ломал импорт
# fastmcp; pyahocorasick нужен, но в requirements тулкита отсутствует.
"$VENV/bin/pip" install -q fastapi uvicorn "mcp<2" pyahocorasick httpx pydantic
"$VENV/bin/pip" install -q -r "$TOOLKIT/onec_mcp_toolkit_proxy/requirements.txt" 2>/dev/null || true

cat <<FIN
готово: $VENV

проверка:   cd "$TOOLKIT" && PORT=6003 timeout 5 "$VENV/bin/python" -m onec_mcp_toolkit_proxy
подъём:     python3 <контур>/scripts/start_1c_db.sh <проект>   (сам поднимет прокси и клиент)
FIN
