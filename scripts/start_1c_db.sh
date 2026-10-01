#!/usr/bin/env bash
# start_1c_db.sh — автоматический подъём MCP 1c-db (1c-mcp-toolkit):
# запускает клиент 1С так, чтобы он сам открыл MCP_Toolkit.epf (канонический
# автозапуск внешней обработки платформой, /Execute), и ждёт порт 6003.
# Ручного открытия обработки нет.
#
# Использование:
#   start_1c_db.sh <project-root>            # поднять и дождаться :6003
#   start_1c_db.sh <project-root> --headless # под Xvfb (среда без GUI)
#   start_1c_db.sh <project-root> --stop     # погасить клиент по pidfile
#
# Конфиг (.zcode/1c/contour.json → "1c_db"):
#   url      — куда стучимся (по умолчанию http://127.0.0.1:6003/mcp)
#   epf_path — путь к MCP_Toolkit.epf относительно корня проекта
#              (по умолчанию tools/mcp/MCP_Toolkit.epf; в git не кладём —
#              добавить в .gitignore при bootstrap)
#
# Платформа: первый существующий из —
#   $1C_DB_PLATFORM → tools: path: из v8project.local.yaml →
#   v8project.yaml tools: path: → клиент 1cv8c в /opt/1cv8/x86_64/*/
# Строка подключения и пароли не печатаются.
set -euo pipefail

ROOT="${1:-.}"
ROOT="$(cd "$ROOT" && pwd)"
MODE="start"
HEADLESS=0
for arg in "$@"; do
	case "$arg" in
	--headless) HEADLESS=1 ;;
	--stop) MODE="stop" ;;
	esac
done

HASH="$(printf '%s' "$ROOT" | sha1sum | cut -c1-8)"
PIDFILE="/tmp/1c-db-$HASH.pid"
LOG="/tmp/1c-db-$HASH.log"

# ── контурные url/порт/epf ──
read -r URL PORT EPF_REL < <(python3 - "$ROOT" <<'PY'
import json, sys, pathlib
root = pathlib.Path(sys.argv[1])
url, epf = "http://127.0.0.1:6003/mcp", "tools/mcp/MCP_Toolkit.epf"
cfg = root / ".zcode" / "1c" / "contour.json"
try:
    db = json.loads(cfg.read_text(encoding="utf-8")).get("1c_db") or {}
    url = db.get("url") or url
    epf = db.get("epf_path") or epf
except Exception:
    pass
tail = url.split("//", 1)[-1]
port = tail.split(":", 1)[1].split("/", 1)[0] if ":" in tail else "6003"
print(url, port, epf)
PY
)

if [[ $MODE == stop ]]; then
	if [[ -f "$PIDFILE" ]] && kill "$(cat "$PIDFILE")" 2>/dev/null; then
		rm -f "$PIDFILE"
		echo "• клиент 1С остановлен"
	else
		echo "• pidfile не найден или процесс уже погашен"
	fi
	exit 0
fi

# ── уже поднят? ──
if curl -so /dev/null --max-time 3 "$URL"; then
	echo "✓ 1c-db уже отвечает: $URL"
	exit 0
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
CLIENT="${1C_DB_PLATFORM:-}"
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
for candidate in "$CLIENT" "$CLIENT/1cv8c" /opt/1cv8/x86_64/*/1cv8c; do
	if [[ -n "$candidate" && -x "$candidate" ]]; then
		CLIENT="$candidate"
		break
	fi
	if [[ -n "$candidate" && -x "${candidate%/1cv8c}/1cv8c" ]]; then
		CLIENT="${candidate%/1cv8c}/1cv8c"
		break
	fi
done
if [[ -z "$CLIENT" || ! -x "$CLIENT" ]]; then
	echo "✗ клиент 1С (1cv8c) не найден: ни в v8project*.yaml, ни в /opt/1cv8/x86_64/*/" >&2
	echo "  установи платформу или задай 1C_DB_PLATFORM" >&2
	exit 2
fi

# ── строка подключения (не печатается; user/password из local-оверлея) ──
CONN="$(python3 - "$ROOT" <<'PY'
import re, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ib_lock import resolve_infobase_connection
root = pathlib.Path(sys.argv[1])
conn = resolve_infobase_connection(str(root)) or ""
f = root / "v8project.local.yaml"
if f.is_file():
    text = f.read_text(encoding="utf-8")
    mu = re.search(r"(?m)^\s*user\s*:\s*['\"]?(.+?)\s*['\"]?\s*$", text)
    mp = re.search(r"(?m)^\s*password\s*:\s*['\"]?(.+?)\s*['\"]?\s*$", text)
    if mu and "User=" not in conn:
        conn += f';User="{mu.group(1)}"'
    if mp and "Pwd=" not in conn:
        conn += f';Pwd="{mp.group(1)}"'
print(conn)
PY
)"
if [[ -z "$CONN" ]]; then
	echo "✗ связь базы не найдена в v8project*.yaml" >&2
	exit 2
fi

# ── запуск и ожидание порта ──
LAUNCH=("$CLIENT" ENTERPRISE /IBConnectionString "$CONN" /Execute "$EPF")
if [[ $HEADLESS -eq 1 ]]; then
	if ! command -v Xvfb >/dev/null 2>&1; then
		echo "✗ --headless требует Xvfb" >&2
		exit 2
	fi
	setsid Xvfb :97 -screen 0 1280x1024x24 >/dev/null 2>&1 &
	XVFB_PID=$!
	DISPLAY_ARG=(":97")
	set -- env DISPLAY=:97 "${LAUNCH[@]}"
	setsid "$@" >"$LOG" 2>&1 &
	echo $! > "$PIDFILE"
	echo "• клиент запущен под Xvfb :97 (xvfb pid $XVFB_PID, клиент pid $(cat "$PIDFILE")); лог: $LOG"
else
	setsid "${LAUNCH[@]}" >"$LOG" 2>&1 &
	echo $! > "$PIDFILE"
	echo "• клиент запущен (pid $(cat "$PIDFILE")); лог: $LOG"
fi

echo -n "• жду порт $PORT "
for _ in $(seq 1 60); do
	if curl -so /dev/null --max-time 2 "$URL"; then
		echo "— ✓ 1c-db отвечает: $URL"
		exit 0
	fi
	printf '.'
	sleep 2
done
echo "— ✗ не поднялся за 120с; смотри $LOG" >&2
echo "  (клиенту нужна графика: в headless-среде — --headless; частая причина" >&2
echo "   первого запуска — диалог аутентификации, если user/password не в оверлее)" >&2
exit 1
