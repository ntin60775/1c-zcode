#!/usr/bin/env bash
# start_1c_db.sh — автоматический подъём MCP 1c-db (1c-mcp-toolkit) на Linux.
#
# Архитектура (выяснена на живом стенде 2026-10-01): встроенный HTTP-транспорт
# тулкита — NativeAPI-компонента Windows (PE32+ DLL), на Linux «Тип не
# определен». Рабочий путь: python-прокси onec_mcp_toolkit_proxy держит
# :6003 (MCP Streamable HTTP для хоста + /1c/poll long-poll для клиента 1С),
# а клиент 1С подключается к нему сам — через ПараметрЗапуска
# "startup;mode=proxy;url=http://localhost:PORT" (ручных кликов в форме нет).
# На Windows embedded-режим работает нативно — там скрипт тоже корректен,
# но прокси не нужен (mode=embedded поднимет сервер внутри клиента).
#
# Использование:
#   start_1c_db.sh <project-root>            # прокси (если надо) + клиент, ждать :6003
#   start_1c_db.sh <project-root> --headless # клиент под Xvfb (среда без GUI)
#   start_1c_db.sh <project-root> --stop     # погасить клиент и прокси (свои)
#   start_1c_db.sh <project-root> --proxy-only / --stop-proxy
#
# Конфиг (.zcode/1c/contour.json → "1c_db"):
#   url      — MCP-endpoint (по умолчанию http://127.0.0.1:6003/mcp)
#   epf_path — MCP_Toolkit.epf относительно корня (tools/mcp/, в git не кладём)
#   mode     — "proxy" (Linux, по умолчанию) | "embedded" (Windows)
#   channel  — канал изоляции прокси (опционально, ?channel=)
#
# Зависимости: install_1c_mcp_proxy.sh (один раз; venv ~/.local/venvs/
# 1c-mcp-proxy, путь к клону тулкита — $ONEC_MCP_TOOLKIT_DIR или аргумент).
# Строка подключения и пароли не печатаются (но видны в cmdline клиента —
# известное ограничение /IBConnectionString).
set -euo pipefail

MODE="start"
HEADLESS=0
ROOT=""
for arg in "$@"; do
	case "$arg" in
	--headless) HEADLESS=1 ;;
	--stop) MODE="stop" ;;
	--proxy-only) MODE="proxy_only" ;;
	--stop-proxy) MODE="stop_proxy" ;;
	*) [ -z "$ROOT" ] && ROOT="$arg" ;;
	esac
done
[ -n "$ROOT" ] || ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
ROOT="$(cd "$ROOT" && pwd)"

HASH="$(printf '%s' "$ROOT" | sha1sum | cut -c1-8)"
PIDFILE="/tmp/1c-db-$HASH.pid"
LOG="/tmp/1c-db-$HASH.log"
PROXY_LOG="/tmp/1c-mcp-proxy-$HASH.log"
PROXY_PIDFILE="/tmp/1c-mcp-proxy-$HASH.pid"
VENV="$HOME/.local/venvs/1c-mcp-proxy"
TOOLKIT="${ONEC_MCP_TOOLKIT_DIR:-$HOME/home/dev/contrib/clones/1c-mcp-toolkit}"

# ── hooks контура: репо (scripts/../hooks) или вендор (scripts/../../hooks) ──
HOOKS_DIR=""
for rel in "../../hooks" "../hooks"; do
	# не-фатально: несуществующий кандидат не должен убить скрипт при set -e
	cand="$(cd "$(dirname "${BASH_SOURCE[0]}")/$rel" 2>/dev/null && pwd || true)"
	[[ -f "$cand/ib_lock.py" ]] && { HOOKS_DIR="$cand"; break; }
done

# ── контурные url/порт/epf/mode/channel ──
read -r URL PORT EPF_REL DBMODE CHANNEL < <(python3 - "$ROOT" <<'PY'
import json, sys, pathlib
root = pathlib.Path(sys.argv[1])
url, epf = "http://127.0.0.1:6003/mcp", "tools/mcp/MCP_Toolkit.epf"
mode = "proxy" if sys.platform.startswith("linux") else "embedded"
cfg = root / ".zcode" / "1c" / "contour.json"
try:
    db = json.loads(cfg.read_text(encoding="utf-8")).get("1c_db") or {}
    url = db.get("url") or url
    epf = db.get("epf_path") or epf
    mode = db.get("mode") or mode
except Exception:
    pass
tail = url.split("//", 1)[-1]
port = tail.split(":", 1)[1].split("/", 1)[0] if ":" in tail else "6003"
print(url, port, epf, mode, db.get("channel") or "")
PY
)

alive() { curl -so /dev/null --max-time 3 -w '%{http_code}' "$URL" 2>/dev/null | grep -qE '^[1-5]'; }

proxy_pid_alive() {
	[[ -f "$PROXY_PIDFILE" ]] && kill -0 "$(cat "$PROXY_PIDFILE")" 2>/dev/null
}

stop_proxy() {
	if proxy_pid_alive; then
		kill "$(cat "$PROXY_PIDFILE")" 2>/dev/null || true
		rm -f "$PROXY_PIDFILE"
		echo "• прокси остановлен"
	else
		echo "• прокси не поднимался этим скриптом"
	fi
}

if [[ $MODE == stop_proxy ]]; then
	stop_proxy
	exit 0
fi

if [[ $MODE == proxy_only ]]; then
	alive && { echo "✓ уже отвечает: $URL"; exit 0; }
fi

# ── прокси (нужен только в режиме proxy) ──
if [[ $DBMODE == proxy && $MODE != stop ]]; then
	if ! alive; then
		if [[ ! -x "$VENV/bin/python" ]]; then
			echo "✗ нет $VENV — один раз выполни:" >&2
			echo "  $(dirname "${BASH_SOURCE[0]}")/install_1c_mcp_proxy.sh $TOOLKIT" >&2
			exit 2
		fi
		if [[ ! -d "$TOOLKIT/onec_mcp_toolkit_proxy" ]]; then
			echo "✗ нет клона тулкита: $TOOLKIT (ONEC_MCP_TOOLKIT_DIR)" >&2
			exit 2
		fi
		# Тестовый контур: логика-тесты пишут/удаляют данные фикстур, поэтому
		# «Записать»/«Удалить» НЕ блокируются; системные опасности (COM, файлы,
		# монопольный/привилегированный режимы) остаются заблокированы.
		# Прод защищён независимо: mcp_gate блокирует execute_code в прод-контуре.
		DANGER_KEEP="УдалитьФайлы,DeleteFiles,КопироватьФайл,CopyFile,ПереместитьФайл,MoveFile,СоздатьКаталог,CreateDirectory,COMОбъект,COMObject,УстановитьПривилегированныйРежим,SetPrivilegedMode,ПодключитьВнешнююКомпоненту,AttachAddIn,УстановитьВнешнююКомпоненту,InstallAddIn,УстановитьМонопольныйРежим,SetExclusiveMode"
		( cd "$TOOLKIT" && setsid env PORT="$PORT" DANGEROUS_KEYWORDS="$DANGER_KEEP" \
			"$VENV/bin/python" -m onec_mcp_toolkit_proxy >"$PROXY_LOG" 2>&1 & echo $! > "$PROXY_PIDFILE" )
		echo "• прокси поднят (pid $(cat "$PROXY_PIDFILE")); лог: $PROXY_LOG"
		for _ in $(seq 1 20); do
			alive && break
			sleep 1
		done
	fi
	if alive; then
		echo "✓ прокси отвечает: $URL"
	else
		echo "✗ прокси не поднялся; смотри $PROXY_LOG" >&2
		exit 1
	fi
fi

if [[ $MODE == proxy_only ]]; then
	exit 0
fi

if [[ $MODE == stop ]]; then
	if [[ -f "$PIDFILE" ]] && kill "$(cat "$PIDFILE")" 2>/dev/null; then
		rm -f "$PIDFILE"
		echo "• клиент 1С остановлен"
	else
		echo "• pidfile клиента не найден или процесс погашен"
	fi
	stop_proxy
	exit 0
fi

# ── уже полностью поднят? (клиент опрашивает прокси) ──
# Честная проба: безобидный execute_code через MCP. /1c/poll не годится:
# при POLL_TIMEOUT=0 прокси отвечает мгновенным 204 и с клиентом, и без.
client_alive() {
	python3 - "$URL" <<'PY' 2>/dev/null
import json, sys, urllib.request
url = sys.argv[1]
def rpc(payload, sid=None, timeout=25):
    h = {"Content-Type": "application/json",
         "Accept": "application/json, text/event-stream"}
    if sid:
        h["Mcp-Session-Id"] = sid
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
        new_sid = r.headers.get("Mcp-Session-Id")
        ctype = r.headers.get("Content-Type", "")
    if "text/event-stream" in ctype:
        raw = next((ln[5:].strip() for ln in raw.splitlines()
                    if ln.startswith("data:") and ln[5:].strip()), "{}")
    return new_sid, (json.loads(raw) if raw.strip() else {})
try:
    sid, _ = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                             "clientInfo": {"name": "alive-probe", "version": "0"}}})
    if sid:
        rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    _, res = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "execute_code",
                             "arguments": {"code": "Результат = 6 * 7;"}}}, sid)
    data = res.get("result", {}).get("structuredContent") or {}
    inner = data.get("result", data)
    ok = inner.get("success") is True and str(inner.get("data")) .strip('"') == "42"
    sys.exit(0 if ok else 1)
except Exception:
    sys.exit(1)
PY
}
if curl -so /dev/null --max-time 3 "$URL"; then
	if client_alive; then
		echo "✓ 1c-db уже отвечает (клиент подключён): $URL"
		exit 0
	fi
fi

# ── обработка ──
EPF="$ROOT/$EPF_REL"
if [[ ! -f "$EPF" ]]; then
	echo "✗ нет обработки: $EPF_REL" >&2
	echo "  положи MCP_Toolkit.epf из клона 1c-mcp-toolkit (build/) по этому" >&2
	echo "  пути или пропиши 1c_db.epf_path в .zcode/1c/contour.json" >&2
	exit 2
fi

# ── платформа ──
CLIENT="${ONEC_PLATFORM:-}"
if [[ -z "$CLIENT" ]]; then
	CLIENT="$(python3 - "$ROOT" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
for name in ("v8project.local.yaml", "v8project.yaml"):
    f = root / name
    if not f.is_file():
        continue
    in_tools = False
    for line in f.read_text(encoding="utf-8").splitlines():
        if line[:1] not in (" ", "\t"):
            in_tools = line.startswith("tools:")
            continue
        if in_tools and "path:" in line:
            print(line.split("path:", 1)[1].strip().strip("'\""))
            raise SystemExit
PY
)"
fi
for candidate in "$CLIENT" "$CLIENT/1cv8c" "$CLIENT/1cv8" /opt/1cv8/x86_64/*/1cv8c; do
	if [[ -n "$candidate" && -f "$candidate" ]]; then
		CLIENT="$candidate"
		break
	fi
done
if [[ -z "$CLIENT" || ! -f "$CLIENT" ]]; then
	echo "✗ клиент 1С (1cv8c) не найден: ни в v8project*.yaml, ни в /opt/1cv8/x86_64/*/" >&2
	echo "  установи платформу или задай ONEC_PLATFORM" >&2
	exit 2
fi

# ── строка подключения (не печатается; user/password из local-оверлея) ──
CONN="$(python3 - "$ROOT" "$HOOKS_DIR" <<'PY'
import re, sys, pathlib
if sys.argv[2]:
    sys.path.insert(0, sys.argv[2])
from ib_lock import resolve_infobase_connection
root = pathlib.Path(sys.argv[1])
conn = resolve_infobase_connection(str(root)) or ""
f = root / "v8project.local.yaml"
if f.is_file():
    text = f.read_text(encoding="utf-8")
    mu = re.search(r"(?m)^\s*user\s*:\s*['\"]?(.+?)\s*['\"]?\s*$", text)
    mp = re.search(r"(?m)^\s*password\s*:\s*['\"]?(.+?)\s*['\"]?\s*$", text)
    # канон строки подключения 1С: Usr= (не User=)
    if mu and "Usr=" not in conn:
        conn += f';Usr="{mu.group(1)}"'
    if mp and "Pwd=" not in conn:
        conn += f';Pwd="{mp.group(1)}"'
print(conn)
PY
)"
if [[ -z "$CONN" ]]; then
	echo "✗ связь базы не найдена в v8project*.yaml" >&2
	exit 2
fi

# ── запуск клиента ──
# cwd корня базы (File=build/ib относительный); x11-окружение (Wayland ломает
# GTK, LibreGL — от чёрных окон под Xvfb). Параметр запуска тулкита:
# Linux — mode=proxy;url=… (клиент сам подключится к прокси, ручных кликов
# в форме нет); Windows — mode=embedded;port=… (сервер внутри клиента).
if [[ $DBMODE == proxy ]]; then
	STARTUP="startup;mode=proxy;url=http://localhost:$PORT"
	[[ -n "$CHANNEL" ]] && STARTUP="$STARTUP;channel=$CHANNEL"
else
	STARTUP="startup;mode=embedded;port=$PORT"
fi
LAUNCH=("$CLIENT" ENTERPRISE /IBConnectionString "$CONN" /Execute "$EPF" /C "$STARTUP")
CHILD_ENV=(env -u WAYLAND_DISPLAY GDK_BACKEND=x11 LIBGL_ALWAYS_SOFTWARE=1)
if [[ $HEADLESS -eq 1 ]]; then
	if ! command -v Xvfb >/dev/null 2>&1; then
		echo "✗ --headless требует Xvfb" >&2
		exit 2
	fi
	XVFB_LOG="/tmp/1c-db-xvfb-$HASH.log"
	setsid Xvfb :97 -screen 0 1920x1080x24 >"$XVFB_LOG" 2>&1 &
	# ждём X-сокет: клиент стартует мгновенно и без него падает «Unable to
	# initialize GTK» — это была реальная поломка прогона
	XREADY=0
	for _ in $(seq 1 20); do
		if [[ -S /tmp/.X11-unix/X97 ]]; then XREADY=1; break; fi
		sleep 0.5
	done
	if [[ $XREADY -ne 1 ]]; then
		echo "✗ Xvfb :97 не поднялся за 10с; лог: $XVFB_LOG" >&2
		tail -3 "$XVFB_LOG" >&2
		exit 2
	fi
	echo "• Xvfb :97 поднят (pid $!)"
	CHILD_ENV+=(DISPLAY=:97)
else
	CHILD_ENV+=(DISPLAY="${DISPLAY:-:0}")
fi
cd "$ROOT"
setsid "${CHILD_ENV[@]}" "${LAUNCH[@]}" >"$LOG" 2>&1 &
echo $! > "$PIDFILE"
echo "• клиент запущен (pid $(cat "$PIDFILE")); режим $DBMODE; лог: $LOG"

echo -n "• жду подключения клиента к $URL "
# первый старт под Xvfb с software-GL грузит интерфейс до ~3 минут — норма
for _ in $(seq 1 210); do
	if client_alive; then
		echo "— ✓ 1c-db работает: клиент опрашивает прокси, MCP на $URL"
		exit 0
	fi
	printf '.'
	sleep 2
done
echo "— ✗ клиент не подключился за 180с; смотри $LOG" >&2
echo "  частые причины: первый запуск — диалоги платформы (подтверждение" >&2
echo "  NativeAPI-компоненты, аутентификация — на видимом экране пройди" >&2
echo "  руками один раз); wrong mode (embedded на Linux невозможен)" >&2
exit 1
