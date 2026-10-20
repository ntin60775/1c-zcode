#!/usr/bin/env bash
# start_1c_db.sh — автоматический подъём MCP 1c-db (1c-mcp-toolkit) на Linux.
#
# Архитектура (выяснена на живом стенде 2026-10-01): встроенный HTTP-транспорт
# тулкита — NativeAPI-компонента Windows (PE32+ DLL), на Linux «Тип не
# определен». Рабочий путь: python-прокси onec_mcp_toolkit_proxy держит
# :6003 (MCP Streamable HTTP для хоста + /1c/poll long-poll для клиента 1С),
# а клиент 1С подключается к нему сам — через ПараметрЗапуска
# "startup;mode=proxy;url=http://localhost:PORT" (ручных кликов в форме нет).
# Подъём двухфазный (v0.4.46): фаза 1 — startup (новая база: оператор
# подтверждает диалоги на видимом дисплее; база запоминает настройки канала),
# фаза 2 — перезапуск с /C "autostart;…" включает сеансовые danger-флаги
# («Запись», «УстановитьПривилегированныйРежим») и прогрев словаря
# анонимизации. CONTOUR_AUTOSTART=off — только фаза 1.
# На Windows embedded-режим работает нативно — там скрипт тоже корректен,
# но прокси не нужен (mode=embedded поднимет сервер внутри клиента).
#
# Использование:
#   start_1c_db.sh <project-root>            # прокси (если надо) + клиент, ждать :6003
#   start_1c_db.sh <project-root> --headless # клиент под Xvfb (среда без GUI)
#   start_1c_db.sh <project-root> --stop     # погасить клиент и прокси (свои)
#   start_1c_db.sh <project-root> --proxy-only / --stop-proxy
#   start_1c_db.sh <project-root> --print-config  # url порт epf mode channel — и выход
#
# Конфиг (.zcode/1c/contour.json → "1c_db"):
#   url      — MCP-endpoint (по умолчанию http://127.0.0.1:6003/mcp)
#   epf_path — обработка относительно корня (tools/mcp/, в git не кладём).
#              Не задан — раннер выбирает файл под ОС хоста сам
#              (v0.4.51, решение владельца после ROCTUP/1c-mcp-toolkit#34):
#              Linux → MCP_Toolkit_linux.epf, macOS → MCP_Toolkit_macos.epf,
#              Windows → MCP_Toolkit.epf. Отсутствующий файл доставляется
#              сам: build/ клона → релиз GitHub того же тега; замена
#              уже лежащего файла автоматом не делается (доверие
#              платформы к пути — канон навыка 1c-db-data).
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
	--print-config) MODE="print_config" ;;
	*) [ -z "$ROOT" ] && ROOT="$arg" ;;
	esac
done
[ -n "$ROOT" ] || ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
ROOT="$(cd "$ROOT" && pwd)"

HASH="$(printf '%s' "$ROOT" | sha1sum | cut -c1-8)"
PIDFILE="/tmp/1c-db-$HASH.pid"
LOG="/tmp/1c-db-$HASH.log"
XVFB_PIDFILE="/tmp/1c-db-xvfb-$HASH.pid"
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
url = "http://127.0.0.1:6003/mcp"
# Обработка — под ОС хоста (v0.4.51, ROCTUP/1c-mcp-toolkit#34): на Linux
# вин-файл грузится, но его Windows-компоненты (QueryLineageAnalyzer и др.)
# молча отсутствуют. Явный 1c_db.epf_path перекрывает детект.
EPF_STEM = "tools/mcp/MCP_Toolkit"
if sys.platform.startswith("linux"):
    epf = EPF_STEM + "_linux.epf"
elif sys.platform == "darwin":
    epf = EPF_STEM + "_macos.epf"
else:
    epf = EPF_STEM + ".epf"
mode = "proxy" if sys.platform.startswith("linux") else "embedded"
db = {}
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

if [[ $MODE == print_config ]]; then
	echo "$URL $PORT $EPF_REL $DBMODE $CHANNEL"
	exit 0
fi

alive() { curl -so /dev/null --max-time 3 -w '%{http_code}' "$URL" 2>/dev/null | grep -qE '^[1-5]'; }

# Гасим клиента по уникальному маркеру обработки в cmdline: реальный ELF может
# жить за shim-обёрткой (podman/distrobox-exec), pidfile указывает лишь на
# обёртку. Используется и в --stop, и в фазе 2 (перезапуск с autostart).
kill_client() {
	local MARKER="$ROOT/$EPF_REL"
	pkill -f -- "$MARKER" 2>/dev/null || true
	for _ in 1 2 3 4 5 6 7 8; do
		pgrep -f -- "$MARKER" >/dev/null || return 0
		sleep 1
	done
	if pgrep -f -- "$MARKER" >/dev/null; then
		pkill -9 -f -- "$MARKER" 2>/dev/null || true
		echo "• клиент 1С добит принудительно" >&2
	fi
	return 0
}

proxy_pid_alive() {
	[[ -f "$PROXY_PIDFILE" ]] && kill -0 "$(cat "$PROXY_PIDFILE")" 2>/dev/null
}

stop_proxy() {
	local KILLED=0
	if proxy_pid_alive; then
		kill "$(cat "$PROXY_PIDFILE")" 2>/dev/null || true
		KILLED=1
	fi
	# pidfile может указывать на обёртку setsid, а не на python (наблюдение
	# 2026-10-11: --stop промахивался, прокси переживал остановку и держал
	# порт) — добиваем по владельцу порта; каналы делят :PORT, чужих там быть
	# не должно.
	local PP
	PP=$(ss -tlnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | head -1) || true
	if [[ -n "$PP" ]]; then
		kill "$PP" 2>/dev/null || true
		KILLED=1
	fi
	if [[ $KILLED -eq 1 ]]; then
		for _ in 1 2 3 4 5; do
			ss -tln 2>/dev/null | grep -q ":$PORT " || break
			sleep 1
		done
		echo "• прокси остановлен"
	fi
	rm -f "$PROXY_PIDFILE"
	[[ $KILLED -eq 0 ]] && echo "• прокси не поднимался этим скриптом"
	return 0
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
		# «Записать»/«Удалить» НЕ блокируются; привилегированный и монопольный
		# режимы разрешены всегда (решение владельца 2026-10-04:
		# allow_privileged_mode; монополия нужна сценариям обновления базы по
		# результатам тестов — вызвавший обязан снять режим сам). COM, файлы
		# и внешние компоненты остаются заблокированы.
		# Прод защищён независимо: mcp_gate блокирует execute_code в прод-контуре.
		DANGER_KEEP="УдалитьФайлы,DeleteFiles,КопироватьФайл,CopyFile,ПереместитьФайл,MoveFile,СоздатьКаталог,CreateDirectory,COMОбъект,COMObject,ПодключитьВнешнююКомпоненту,AttachAddIn,УстановитьВнешнююКомпоненту,InstallAddIn"
		# Анонимизация канала — ПО УМОЛЧАНИЮ ВКЛЮЧЕНА (решение владельца
		# 2026-10-11: работа с базой без анонимизации запрещена): ответы
		# execute_query/execute_code/get_object_by_link/get_event_log/
		# find_references/get_access_rights токенизируются ([ORG-00001] и т.п.),
		# токены восстанавливаются в параметрах обратных вызовов. Отключение —
		# только явное CONTOUR_ANON=off (e2e-прогоны: сравнение сырых значений;
		# run_e2e.sh экспортирует это сам) с WARN в логе прокси.
		CONTOUR_ANON="${CONTOUR_ANON:-on}"
		if [[ "$CONTOUR_ANON" == "on" ]]; then
			ANON_ENV=(ANONYMIZATION_ENABLED=true)
		else
			ANON_ENV=(ANONYMIZATION_ENABLED=false)
			echo "WARN: анонимизация канала 1c-db ВЫКЛЮЧЕНА (CONTOUR_ANON=off) — данные базы идут в ответах как есть" >&2
		fi
		# nohup вместо subshell+setsid: subshell-обёртка оставляла живой bash
		# (wait4 на дочернем прокси) — скрипт «зависал» после «✓ готов», а
		# pidfile ловил pid обёртки, не python (наблюдение 2026-10-12).
		# cd нужен python -m (пакет ищется по cwd); клиент позже сам делает
		# cd "$ROOT".
		cd "$TOOLKIT"
		nohup env PORT="$PORT" DANGEROUS_KEYWORDS="$DANGER_KEEP" \
			"${ANON_ENV[@]}" \
			"$VENV/bin/python" -m onec_mcp_toolkit_proxy \
			>"$PROXY_LOG" 2>&1 </dev/null &
		echo $! > "$PROXY_PIDFILE"
		disown 2>/dev/null || true
		echo "• прокси поднят (pid $(cat "$PROXY_PIDFILE")); лог: $PROXY_LOG"
		for _ in $(seq 1 20); do
			alive && break
			sleep 1
		done
	fi
	if alive; then
		# pid в pidfile чиним по факту — владельцу порта (обёртки nohup/env
		# могли сместить pid); stop_proxy дополнительно добивает по порту.
		PP=$(ss -tlnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | head -1) || true
		[[ -n "$PP" ]] && echo "$PP" > "$PROXY_PIDFILE"
		echo "✓ прокси отвечает: $URL (pid $(cat "$PROXY_PIDFILE"))"
	else
		echo "✗ прокси не поднялся; смотри $PROXY_LOG" >&2
		exit 1
	fi
fi

if [[ $MODE == proxy_only ]]; then
	exit 0
fi

if [[ $MODE == stop ]]; then
	# Клиент гасим по маркеру обработки в cmdline (pidfile указывает лишь на
	# shim-обёртку — см. kill_client).
	kill_client
	rm -f "$PIDFILE"
	echo "• клиент 1С остановлен"
	# Xvfb --headless-стенда тоже свой — гасим по pidfile, иначе оседают
	# «забытые» Xvfb :97 (наблюдение приёмки dealer 2026-10-12).
	if [[ -f "$XVFB_PIDFILE" ]] && kill -0 "$(cat "$XVFB_PIDFILE")" 2>/dev/null; then
		kill "$(cat "$XVFB_PIDFILE")" 2>/dev/null || true
		echo "• Xvfb остановлен"
	fi
	rm -f "$XVFB_PIDFILE"
	stop_proxy
	exit 0
fi

# ── уже полностью поднят? (клиент опрашивает прокси) ──
# Честная проба: безобидный execute_code через MCP. /1c/poll не годится:
# при POLL_TIMEOUT=0 прокси отвечает мгновенным 204 и с клиентом, и без.
# Проба завёрнута в coreutils timeout: прокси v1.9.0 удерживает execute_code
# без клиента 5-10 минут (SSE-keepalive обнуляет urllib timeout — грабля
# 2026-10-11), и скрипт зависал до запуска клиента; 30 с достаточно живому
# каналу.
client_alive() {
	timeout -k 5 30 python3 - "$URL" <<'PY' 2>/dev/null
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

# ── обработка: под ОС хоста, при отсутствии — автодоставка (v0.4.51) ──
# Источники по канону «пара прокси+epf одного тега»: build/ клона (локальная
# сборка), затем релиз GitHub того же тега клона. Уже лежащий файл не
# перезаписываем: замена живой обработки сбрасывает доверие платформы к пути
# (диалог безопасности, headless-стенд молчит — канон навыка 1c-db-data).
EPF="$ROOT/$EPF_REL"
if [[ ! -f "$EPF" ]]; then
	EPF_NAME="$(basename "$EPF_REL")"
	SRC="$TOOLKIT/build/$EPF_NAME"
	if [[ -f "$SRC" ]]; then
		mkdir -p "$(dirname "$EPF")"
		cp "$SRC" "$EPF"
		echo "• обработка скопирована из клона (build/): $EPF_NAME"
	else
		TAG="$(git -C "$TOOLKIT" describe --tags --abbrev=0 2>/dev/null || true)"
		REPO=""
		if [[ -n "$TAG" ]]; then
			ORIGIN="$(git -C "$TOOLKIT" remote get-url origin 2>/dev/null || true)"
			REPO="$(printf '%s' "$ORIGIN" | sed -E 's#.*github\.com[:/]##; s#\.git$##')"
		fi
		if [[ -n "$REPO" ]]; then
			if curl -fsSL --max-time 120 \
				-o "$EPF.part" \
				"https://github.com/$REPO/releases/download/$TAG/$EPF_NAME"; then
				mkdir -p "$(dirname "$EPF")"
				mv "$EPF.part" "$EPF"
				echo "• обработка скачана из релиза $REPO@$TAG: $EPF_NAME"
			else
				rm -f "$EPF.part"
			fi
		fi
	fi
fi
if [[ ! -f "$EPF" ]]; then
	{
		echo "✗ нет обработки под эту ОС: $EPF_REL"
		echo "  раннер ждёт файл под текущую ОС: Linux → MCP_Toolkit_linux.epf,"
		echo "  Windows → MCP_Toolkit.epf, macOS → MCP_Toolkit_macos.epf;"
		echo "  источник — build/ клона $TOOLKIT или релиз того же тега"
		echo "  (github.com/ROCTUP/1c-mcp-toolkit/releases), либо явный"
		echo "  1c_db.epf_path в .zcode/1c/contour.json"
	} >&2
	{ ls "$ROOT/tools/mcp/" 2>/dev/null | sed 's/^/  в tools\/mcp\/: /'; } || true
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
CHILD_ENV=(env -u WAYLAND_DISPLAY GDK_BACKEND=x11 LIBGL_ALWAYS_SOFTWARE=1)
if [[ $HEADLESS -eq 1 ]]; then
	if ! command -v Xvfb >/dev/null 2>&1; then
		echo "✗ --headless требует Xvfb" >&2
		exit 2
	fi
	XVFB_LOG="/tmp/1c-db-xvfb-$HASH.log"
	# Xvfb :97 — ресурс стенда: если живой уже есть (хвост прошлого стенда),
	# переиспользуем; если сокет остался от мёртвого — сносим и стартуем чисто
	# (иначе второй Xvfb молча умирает «address in use», а клиент садится на
	# чужой — наблюдение 2026-10-12).
	if [[ -S /tmp/.X11-unix/X97 ]] && pgrep -f 'X[v]fb :97' >/dev/null 2>&1; then
		pgrep -f 'X[v]fb :97' | head -1 > "$XVFB_PIDFILE"
		echo "• Xvfb :97 уже поднят — переиспользую (pid $(cat "$XVFB_PIDFILE"))"
	elif [[ -S /tmp/.X11-unix/X97 ]]; then
		rm -f /tmp/.X11-unix/X97
		setsid Xvfb :97 -screen 0 1920x1080x24 >"$XVFB_LOG" 2>&1 &
		echo $! > "$XVFB_PIDFILE"
		echo "• Xvfb :97 поднят заново (убран stale-сокет; pid $(cat "$XVFB_PIDFILE"))"
	else
		setsid Xvfb :97 -screen 0 1920x1080x24 >"$XVFB_LOG" 2>&1 &
		echo $! > "$XVFB_PIDFILE"
		echo "• Xvfb :97 поднят (pid $(cat "$XVFB_PIDFILE"))"
	fi
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
	CHILD_ENV+=(DISPLAY=:97)
else
	CHILD_ENV+=(DISPLAY="${DISPLAY:-:0}")
fi

launch_client() { # $1 — значение /C
	setsid "${CHILD_ENV[@]}" "$CLIENT" ENTERPRISE /IBConnectionString "$CONN" \
		/Execute "$EPF" /C "$1" >"$LOG" 2>&1 &
	echo $! > "$PIDFILE"
	echo "• клиент запущен (pid $(cat "$PIDFILE")); лог: $LOG"
}

wait_client() { # $1 — метка фазы
	echo -n "• фаза $1: жду подключения клиента к $URL "
	# первый старт под Xvfb с software-GL грузит интерфейс до ~3 минут — норма
	for _ in $(seq 1 210); do
		if client_alive; then
			echo "— ✓ подключён"
			return 0
		fi
		printf '.'
		sleep 2
	done
	echo "— ✗ не подключился за ~420с; смотри $LOG" >&2
	return 1
}

cd "$ROOT"

# ── фаза 1: startup. Первая в новой/перезалитой базе требует оператора
# (диалоги платформы на видимом дисплее) и запоминает настройки канала в
# настройках базы — без них autostart-фаза подключиться не сможет.
if [[ $DBMODE == proxy ]]; then
	CARG="startup;mode=proxy;url=http://localhost:$PORT"
	[[ -n "$CHANNEL" ]] && CARG="$CARG;channel=$CHANNEL"
else
	CARG="startup;mode=embedded;port=$PORT"
fi
launch_client "$CARG"
if ! wait_client 1; then
	echo "  сначала: база НОВАЯ или перезалитая (dt, пересоздан build/ib)?" >&2
	echo "  → разовое подтверждение диалогов платформы (NativeAPI-компонента," >&2
	echo "    аутентификация) руками владельца на видимом дисплее — навык" >&2
	echo "    1c-db-data, раздел «Первая/перезалитая база»" >&2
	echo "  прочее: wrong mode (embedded на Linux невозможен)" >&2
	exit 1
fi

# ── фаза 2: перезапуск с autostart — включает сеансовые флаги «Автоматически
# разрешать операции» («Запись», «УстановитьПривилегированныйРежим»; канон
# владельца 2026-10-04/10-11). Флаги читаются epf только из autostart-JSON
# (прямой startup-парсер их не несёт — ROCTUP/1c-mcp-toolkit#35), поэтому
# каждый подъём двухфазный. Протокол владения epf: nonce-файл «valid»,
# claimed/signal-пути, окно 300 с, связь — из сохранённых настроек базы.
if [[ $DBMODE == proxy && "${CONTOUR_AUTOSTART:-on}" != "off" ]]; then
	AD="$ROOT/build/.1c-db-autostart"
	rm -rf "$AD"
	mkdir -p "$AD"
	ATS="$(date +%Y%m%d%H%M%S)"
	ANONCE="$AD/nonce.tmp"; ACLAIMED="$AD/claimed.tmp"; ASIGNAL="$AD/signal.tmp"
	APARAMS="$AD/params.json"
	printf 'valid' > "$ANONCE"
	python3 - "$APARAMS" "$ASIGNAL" "$ANONCE" "$ACLAIMED" <<'PY'
import json, sys
params, signal, nonce, claimed = sys.argv[1:5]
d = {"signal_path": signal, "mappings_path": "", "mcp_sessions": [],
     "nonce_path": nonce, "claimed_path": claimed, "restart_password": "",
     "danger_auto_write": True, "danger_auto_priv": True,
     "danger_confirm": True, "automatic_recovery": False}
open(params, "w", encoding="utf-8").write(json.dumps(d))
PY
	kill_client
	echo "• фаза 2: перезапуск с autostart (danger-флаги «Запись»/«Привилегированный»)"
	launch_client "autostart;$ATS;$APARAMS"
	if ! wait_client 2; then
		[[ -f "$ASIGNAL" ]] && echo "  signal: $(head -c 200 "$ASIGNAL")" >&2
		echo "  escape-люк: CONTOUR_AUTOSTART=off пропускает фазу 2" >&2
		exit 1
	fi
	rm -rf "$AD"
fi

# ── прогрев словаря анонимизации: словарь строится прокси запросами через
# epf, на холодном старте прелоад бьётся в таймауты и первые ответы бывают
# ЧАСТИЧНО СЫРЫМИ (факт erp-mini 2026-10-11, лечится рестартом канала).
# Пара best-effort запросов запускает сборку/докачку до боевой работы;
# жёсткой проверки нет — у баз без каталогов-источников словаря не бывает
# (dealer), а критерий готовности — канон навыка 1c-db-data.
for _ in 1 2; do
	timeout -k 5 30 python3 - "$URL" <<'PY' 2>/dev/null || true
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
                             "clientInfo": {"name": "dict-warmup", "version": "0"}}})
    if sid:
        rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "execute_query",
                    "arguments": {"query": "ВЫБРАТЬ ПЕРВЫЕ 5 Наименование, ИНН ИЗ Справочник.Контрагенты"}}}, sid)
except Exception:
    pass
PY
	sleep 3
done

echo "✓ 1c-db готов: клиент с danger-флагами, словарь прогрет, MCP на $URL"
