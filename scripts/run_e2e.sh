#!/usr/bin/env bash
# run_e2e.sh — автономный e2e-прогон 1С: pytest + Python API 1c-testpilot.
#
# Использование: run_e2e.sh [профиль] [каталог-тестов]
#   профиль — имя из .zcode/testpilot/profiles.yaml (по умолчанию main);
#   каталог — по умолчанию tests/e2e.
#
# Тест-клиент testpilot поднимает сам (профиль с base; desktop: isolated —
# приватный Xvfb на Linux) и сам гасит после прогона. Модель в прогоне не
# участвует: тот же результат в CI и в терминале. Автор тестов — workflow
# 1c-e2e-author или скилл 1c-test-contour.
#
# В конце печатает строку E2E_SUMMARY_JSON {...} — её парсит workflow
# 1c-e2e-run; человек может игнорировать.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
PROFILE="${1:-${E2E_PROFILE:-main}}"
TESTS="${2:-tests/e2e}"

# ── окружение testpilot ──
PY="${TESTPILOT_PYTHON:-}"
[ -n "$PY" ] || PY="$HOME/.local/venvs/1c-testpilot/bin/python"
[ -x "$PY" ] || PY="$HOME/.local/pipx/venvs/1c-testpilot/bin/python"
if [ ! -x "$PY" ]; then
	echo "✗ окружение testpilot не найдено (~/.local/venvs/1c-testpilot)" >&2
	echo "  поставь: scripts/install_testpilot.sh" >&2
	exit 2
fi

"$PY" -c 'import pytest' 2>/dev/null || "$PY" -m pip install -q 'pytest>=8,<10'

PROFILES="$ROOT/.zcode/testpilot/profiles.yaml"
[ -f "$PROFILES" ] || { echo "✗ нет $PROFILES — прогони bootstrap контура" >&2; exit 2; }
[ -d "$ROOT/$TESTS" ] || [ -f "$ROOT/$TESTS" ] || {
	echo "✗ нет e2e-тестов: $TESTS" >&2
	echo "  напиши их workflow 1c-e2e-author или по скиллу 1c-test-contour" >&2
	exit 2
}

# ── пароль: password_env профиля, пусто в окружении → добрать из local-оверлея.
# Значения не печатаются — сюда только экспорт. Поддерживаются обе раскладки
# profiles.yaml (с корневым profiles: и плоская) и оба места пароля в
# v8project.local.yaml (плоский password: и вложенный infobases.origin.password).
eval "$("$PY" - "$PROFILES" "$PROFILE" "$ROOT" <<'PY'
import os, re, sys, pathlib
profiles_path, profile, root = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
lines = pathlib.Path(profiles_path).read_text(encoding="utf-8").splitlines()
# блок профиля: заголовок на отступе 2-6, потомки глубже
head = re.compile(rf"^\s{{2,6}}{re.escape(profile)}:\s*$")
block, start = [], None
for i, line in enumerate(lines):
    if head.match(line):
        indent = len(line) - len(line.lstrip())
        block = [line]
        for j in range(i + 1, len(lines)):
            ln = lines[j]
            if not ln.strip():
                block.append(ln); continue
            if len(ln) - len(ln.lstrip()) > indent:
                block.append(ln)
            else:
                break
        break
text = "\n".join(block)
env_name = re.search(r"password_env:\s*['\"]?([\w]+)", text)
if env_name and not os.environ.get(env_name.group(1)):
    pwd = ""
    local = root / "v8project.local.yaml"
    if local.is_file():
        ltext = local.read_text(encoding="utf-8")
        m = re.search(r"(?m)^password:\s*['\"]?(.+?)\s*['\"]?\s*$", ltext)
        if not m:  # вложенный infobases.origin.password
            m = re.search(r"(?ms)^infobases:.*?password:\s*['\"]?(.+?)\s*['\"]?\s*$", ltext)
        pwd = m.group(1) if m else ""
    if pwd:
        safe = pwd.replace("'", "'\\''")
        print(f"export {env_name.group(1)}='{safe}'")
    else:
        print(f"echo 'WARN: пароль для профиля {profile} не найден "
              f"(env {env_name.group(1)} пуста, v8project.local.yaml без password)' >&2")
PY
)"

export TC1C_PROFILES_FILE="$PROFILES"
# Тест-клиент наследует окружение: без этого под Xvfb ловим чёрные окна
# (Wayland/GTK, аппаратный GL) — тот же фикс, что у клиента 1c-db.
export GDK_BACKEND=x11
export LIBGL_ALWAYS_SOFTWARE=1
unset WAYLAND_DISPLAY 2>/dev/null || true
mkdir -p "$ROOT/test-results"
cd "$ROOT"

set +e
"$PY" -m pytest "$TESTS" \
	--tc-profile "$PROFILE" \
	--tc-artifacts test-results \
	--junitxml=test-results/e2e-junit.xml \
	-v
RC=$?
set -e

"$PY" - "$ROOT/test-results/e2e-junit.xml" "$RC" <<'PY'
import json, sys, pathlib
junit, rc = pathlib.Path(sys.argv[1]), sys.argv[2]
summary = {"collected": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0,
           "exit_code": int(rc), "junit": False}
if junit.is_file():
    text = junit.read_text(encoding="utf-8")
    root = None
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        pass
    if root is not None:
        suite = root if root.tag == "testsuite" else (root.find("testsuite") or root)
        def num(attr):
            try: return int(float(suite.get(attr, "0")))
            except ValueError: return 0
        summary.update(collected=num("tests"),
                       passed=num("tests") - num("failures") - num("errors") - num("skipped"),
                       failed=num("failures"),
                       errors=num("errors"),
                       skipped=num("skipped"), junit=True)
summary["passed"] = max(summary["passed"], 0)
print("E2E_SUMMARY_JSON " + json.dumps(summary, ensure_ascii=False))
PY

# ── постпроверка остатков Е2Е-данных: зачистка машиной, не памятью агента ──
# Изоляция без транзакционного отката (см. scripts/e2e_sweep.py): тестовые
# данные носят маркер, sweep находит остатки, удаляет и красит прогон,
# если вычистить не удалось. Прокси недоступен — WARN, прогон не портим.
SWEEP="$(cd "$(dirname "$0")" && pwd)/e2e_sweep.py"
if [ -f "$SWEEP" ]; then
	set +e
	python3 "$SWEEP" "$ROOT" --apply --json \
		> "$ROOT/test-results/e2e-sweep.json" 2> "$ROOT/test-results/e2e-sweep.err"
	SRC=$?
	set -e
	if [ "$SRC" = "2" ]; then
		echo "WARN: постпроверка остатков Е2Е-данных недоступна — прокси 1c-db не отвечает" >&2
	elif [ "$SRC" != "0" ]; then
		echo "✗ остатки Е2Е-данных после зачистки — см. test-results/e2e-sweep.json:" >&2
		python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print('   было', d['found_before'], '| удалено', d['deleted'], '| осталось', d['found_after'])" "$ROOT/test-results/e2e-sweep.json" >&2
		[ "$RC" = "0" ] && RC=3
	fi
fi

exit "$RC"
