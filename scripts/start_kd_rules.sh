#!/usr/bin/env bash
# start_kd_rules.sh — подъём/останов/статус MCP kd-rules-mcp (станция 1c-kd).
#
# Сервер апстрима — streamable HTTP на петлевом интерфейсе (порт 8060 по
# умолчанию, URL меняется в .zcode/1c/contour.json → 1c_kd.url). Станция
# ставится install_kd_rules.sh; её состояние (projects.yaml,
# projects.local.yaml, workspace/, cache/) переживает старт/стоп.
# Первый старт создаёт пустой каркас projects.yaml; проект подключает к
# станции kd_register_project.py из корня 1С-проекта (машинные пути — в
# projects.local.yaml, он в git не попадает).
#
# Использование:
#   start_kd_rules.sh                   # поднять и дождаться готовности
#   start_kd_rules.sh --url http://127.0.0.1:8061/mcp   # свой порт
#   start_kd_rules.sh --stop            # погасить (свой pid-файл)
#   start_kd_rules.sh --status          # pid + HTTP-проба
#   start_kd_rules.sh --print-config    # url, станция, проекты — и выход
#   start_kd_rules.sh --wait 60         # свой таймаут готовности (сек)
#
# Лог сервера: <станция>/kd-rules.log (дописывается между стартами).
set -euo pipefail

MODE="start"
URL="http://127.0.0.1:8060/mcp"
WAIT="30"
STATION="${KD_RULES_STATION:-$HOME/.local/share/1c-zcode/kd-rules-mcp}"

while [ $# -gt 0 ]; do
	case "$1" in
	--stop) MODE="stop" ;;
	--status) MODE="status" ;;
	--print-config) MODE="print_config" ;;
	--url) URL="$2"; shift ;;
	--wait) WAIT="$2"; shift ;;
	*) echo "неизвестный аргумент: $1" >&2; exit 2 ;;
	esac
	shift
done

PIDFILE="$STATION/kd-rules.pid"
LOG="$STATION/kd-rules.log"

if [[ "$URL" =~ ^https?://([^/:]+):([0-9]+)(/|$) ]]; then
	HOST="${BASH_REMATCH[1]}"; PORT="${BASH_REMATCH[2]}"
else
	echo "URL без хоста:порта: $URL" >&2
	exit 2
fi

probe() {
	# живость: любой HTTP-ответ с /mcp (GET без MCP-сессии отвечает 4xx — это норм)
	local code
	code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://$HOST:$PORT/mcp" || true)"
	[ -n "$code" ] && [ "$code" != "000" ]
}

alive_pid() {
	[ -f "$PIDFILE" ] || return 1
	local pid
	pid="$(cat "$PIDFILE")"
	kill -0 "$pid" 2>/dev/null || return 1
	printf '%s' "$pid"
}

case "$MODE" in
print_config)
	echo "url: $URL"
	echo "station: $STATION"
	echo "projects: $STATION/projects.yaml"
	echo "log: $LOG"
	alive_pid >/dev/null && echo "pid: $(cat "$PIDFILE") (жив)" || echo "pid: —"
	;;

status)
	if pid="$(alive_pid)"; then
		if probe; then
			echo "жив: pid $pid, $URL отвечает"
		else
			echo "pid $pid есть, но $URL не отвечает — смотри $LOG"
			exit 1
		fi
	else
		probe && echo "отвечает $URL, но pid-файла нет (чужой процесс?)" \
			|| echo "не запущен (pid-файла нет, $URL не отвечает)"
	fi
	;;

stop)
	if pid="$(alive_pid)"; then
		kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
		for _ in $(seq 1 10); do
			kill -0 "$pid" 2>/dev/null || break
			sleep 1
		done
		rm -f "$PIDFILE"
		echo "✓ погашен (pid $pid)"
	else
		echo "не запущен (по pid-файлу)"
		rm -f "$PIDFILE"
	fi
	;;

start)
	[ -f "$STATION/pyproject.toml" ] || {
		echo "станции нет ($STATION) — сначала scripts/install_kd_rules.sh" >&2
		exit 1
	}
	if pid="$(alive_pid)"; then
		echo "уже запущен: pid $pid"
		probe && exit 0
		echo "…pid жив, но порт не отвечает — гашу и поднимаю заново"
		kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
		rm -f "$PIDFILE"
		sleep 1
	fi
	if [ ! -f "$STATION/projects.yaml" ]; then
		printf '# Проекты подключает kd_register_project.py из скриптов контура 1c-zcode;\n# поля (bases, rules_dir, writable_extensions) — projects.example.yaml рядом.\nprojects: {}\n' >"$STATION/projects.yaml"
		echo "→ создан пустой каркас $STATION/projects.yaml — проекты подключает kd_register_project.py (скилл 1c-kd-rules)"
	fi
	cd "$STATION"
	echo "→ поднимаю $URL (лог: $LOG)"
	KD2_HOST="$HOST" KD2_PORT="$PORT" \
	KD2_PROJECTS_FILE="$STATION/projects.yaml" \
	KD2_WORKSPACE="$STATION/workspace" KD2_CACHE_DIR="$STATION/cache" \
	setsid nohup uv run --no-sync kd-rules-mcp >>"$LOG" 2>&1 &
	pid=$!
	echo "$pid" >"$PIDFILE"
	for _ in $(seq 1 "$WAIT"); do
		if ! kill -0 "$pid" 2>/dev/null; then
			echo "сервер упал при старте — конец лога:" >&2
			tail -20 "$LOG" >&2
			rm -f "$PIDFILE"
			exit 1
		fi
		if probe; then
			echo "✓ жив: pid $pid, $URL"
			exit 0
		fi
		sleep 1
	done
	echo "не ответил за ${WAIT} с (первый запрос может собирать кэш структур) — статус: start_kd_rules.sh --status" >&2
	exit 1
	;;
esac
