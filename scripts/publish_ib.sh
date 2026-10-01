#!/usr/bin/env bash
# publish_ib.sh — HTTP-публикация тестовой базы для API-контура (автономный
# сервер 1С ibsrv, без Apache и root).
#
# Использование: publish_ib.sh start|stop|status [путь-к-базе]
#   start  — поднять публикацию (идемпотентно: живую не трогает), ждёт
#            готовности, печатает хвост E2E_PUBLISH_JSON {url, port, pid,
#            ibsrv_cmd} — его читают API-тесты и агент;
#   stop   — погасить публикацию; status — exit 0, если жива.
# База: второй аргумент или build/ib от корня проекта. Порт и команда ibsrv —
# contour.json (1c.publish.port, 1c.publish.ibsrv) или env PUBLISH_IBSRV.
# Сервер слушает только 127.0.0.1; регламентные задания выключены.
set -euo pipefail

CMD="${1:-status}"
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
BASE="${2:-$ROOT/build/ib}"
WORK="$ROOT/build/.ibsrv"

# ── настройки из contour.json ──
read_setting() { # $1 = python-выражение-путь
	python3 - "$ROOT/.zcode/1c/contour.json" "$1" <<'PY'
import json, sys, pathlib
try:
    cfg = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    node = cfg.get("1c", {}).get("publish", {})
    key = sys.argv[2]
    print(node.get(key, "") or "")
except Exception:
    print("")
PY
}
PORT="$(read_setting port)"; PORT="${PORT:-8414}"
IBSRV_LINE="$(read_setting ibsrv)"; IBSRV_LINE="${PUBLISH_IBSRV:-$IBSRV_LINE}"

# ── резолв ibsrv: env/contour → хост → живой distrobox-контейнер ──
NOTE=""
if [ -z "$IBSRV_LINE" ]; then
	IBSRV_LINE="$(ls /opt/1cv8/x86_64/*/ibsrv 2>/dev/null | sort | tail -1 || true)"
fi
if [ -z "$IBSRV_LINE" ] && command -v distrobox >/dev/null 2>&1; then
	for C in $(distrobox list 2>/dev/null | awk -F'|' 'NR>1 {gsub(/ /,"",$2); print $2}'); do
		FOUND="$(distrobox enter "$C" -- sh -c 'ls /opt/1cv8/x86_64/*/ibsrv 2>/dev/null | sort | tail -1' 2>/dev/null || true)"
		if [ -n "$FOUND" ]; then
			IBSRV_LINE="distrobox enter $C -- $FOUND"
			NOTE="ibsrv найден в distrobox-контейнере $C; чтобы прибить гвоздём — PUBLISH_IBSRV=… или 1c.publish.ibsrv в contour.json"
			break
		fi
	done
fi
if [ -z "$IBSRV_LINE" ]; then
	echo "✗ ibsrv (автономный сервер 1С) не найден: ни в /opt/1cv8, ни в контейнерах distrobox" >&2
	echo "  задай PUBLISH_IBSRV='<команда запуска ibsrv>' или 1c.publish.ibsrv в .zcode/1c/contour.json" >&2
	exit 2
fi

alive() { [ -f "$WORK/ibases.json" ] && curl -s -o /dev/null --max-time 5 "http://127.0.0.1:$PORT/" 2>/dev/null; }

print_json() { # $1 = pid
	python3 - "$PORT" "${1:-}" "$IBSRV_LINE" <<'PY'
import json, sys
print("E2E_PUBLISH_JSON " + json.dumps(
    {"url": f"http://127.0.0.1:{sys.argv[1]}/", "port": int(sys.argv[1]),
     "pid": int(sys.argv[2]) if sys.argv[2] else None,
     "ibsrv_cmd": sys.argv[3]}, ensure_ascii=False))
PY
}

case "$CMD" in
status)
	if alive; then print_json "$(pgrep -f -- "--config=$WORK/ibases.json" | head -1)"; exit 0
	else echo "✗ публикация не поднята (порт $PORT)" >&2; exit 1; fi
	;;
stop)
	PIDS="$(pgrep -f -- "--config=$WORK/ibases.json" || true)"
	if [ -z "$PIDS" ]; then echo "✓ публикация и не была поднята"; exit 0; fi
	kill $PIDS 2>/dev/null || true
	for _ in 1 2 3 4 5; do pgrep -f -- "--config=$WORK/ibases.json" >/dev/null || break; sleep 1; done
	PIDS="$(pgrep -f -- "--config=$WORK/ibases.json" || true)"
	[ -n "$PIDS" ] && kill -9 $PIDS 2>/dev/null || true
	echo "✓ публикация погашена (порт $PORT)"
	;;
start)
	if alive; then
		echo "✓ публикация уже жива"
		[ -n "$NOTE" ] && echo "WARN: $NOTE" >&2
		print_json "$(pgrep -f -- "--config=$WORK/ibases.json" | head -1)"
		exit 0
	fi
	[ -d "$BASE" ] || { echo "✗ нет каталога базы: $BASE" >&2; exit 2; }
	mkdir -p "$WORK/data"
	UUID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
	cat > "$WORK/ibases.json" <<EOF
server:
  address: 127.0.0.1
  port: $PORT
database:
  path: $BASE
infobase:
  id: $UUID
  name: e2e-publish
  distribute-licenses: yes
  schedule-jobs: deny
  disable-local-speech-to-text: no
  access-right-audit-events-recording: no
http:
  base: /
EOF
	echo "→ поднимаю публикацию $BASE на :$PORT"
	# shellcheck disable=SC2086
	eval "$IBSRV_LINE" --data="$WORK/data" --config="$WORK/ibases.json" \
		>"$WORK/ibsrv.log" 2>&1 &
	OK=0
	for _ in $(seq 1 60); do
		if alive; then OK=1; break; fi
		sleep 2
		pgrep -f -- "--config=$WORK/ibases.json" >/dev/null || { echo "✗ ibsrv упал, лог: $WORK/ibsrv.log" >&2; tail -3 "$WORK/ibsrv.log" >&2; exit 2; }
	done
	[ "$OK" = 1 ] || { echo "✗ публикация не поднялась за 120с, лог: $WORK/ibsrv.log" >&2; exit 2; }
	echo "✓ публикация жива: http://127.0.0.1:$PORT/"
	[ -n "$NOTE" ] && echo "WARN: $NOTE" >&2
	print_json "$(pgrep -f -- "--config=$WORK/ibases.json" | head -1)"
	;;
*)
	echo "использование: publish_ib.sh start|stop|status [путь-к-базе]" >&2
	exit 2
	;;
esac
