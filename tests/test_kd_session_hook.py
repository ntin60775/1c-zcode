#!/usr/bin/env python3
"""Тесты kd_session_start.py: SessionStart будит станцию 1c-kd фоном.

Хук запускается процессом (stdin-событие → exit 0); «подъём станции» —
фейковый start_kd_rules.sh, пишущий маркер. Без станции на машине пробы
всё равно проходят: она подменяется KD_RULES_STATION. Запуск:
python3 tests/test_kd_session_hook.py
"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "kd_session_start.py"

FAKE_START = '#!/usr/bin/env bash\necho "$@" > "$MARKER"\n'


def run_hook(root: Path) -> subprocess.CompletedProcess:
	return subprocess.run(
		[sys.executable, str(HOOK)],
		input=json.dumps({"cwd": str(root)}), capture_output=True, text=True, timeout=15)


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		tmp = Path(tmp)
		project = tmp / "proj"
		scripts = project / ".zcode" / "1c" / "scripts"
		scripts.mkdir(parents=True)
		marker = tmp / "started.marker"

		def make_env(url: str, with_station: bool = True) -> dict:
			(project / ".zcode" / "config.json").write_text(json.dumps({
				"mcp": {"servers": {"1c-kd": {"type": "http", "url": url}}}
			}), encoding="utf-8")
			(scripts / "start_kd_rules.sh").write_text(FAKE_START, encoding="utf-8")
			env = dict(os.environ, MARKER=str(marker), KD_RULES_STATION=str(tmp / "station"))
			if with_station:
				station = tmp / "station"
				station.mkdir(exist_ok=True)
				(station / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
			marker.unlink(missing_ok=True)
			return env

		def spawned(env: dict, wait_s: float = 3.0) -> bool:
			deadline = time.time() + wait_s
			while time.time() < deadline:
				if Path(env["MARKER"]).is_file():
					return True
				time.sleep(0.05)
			return False

		# ── 1c-kd не разведён: хук молчит ──
		empty = tmp / "empty"
		empty.mkdir()
		env = dict(os.environ, MARKER=str(marker), KD_RULES_STATION=str(tmp / "station"))
		r = run_hook(empty)
		check("без config.json — exit 0", r.returncode == 0, r.stderr[:200])
		check("без config.json — станцию не будим", not spawned(env, 0.5), "маркер есть")

		# ── станции нет на машине: молча выходим ──
		env = make_env("http://127.0.0.1:9/mcp", with_station=False)
		r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"cwd": str(project)}),
		                   capture_output=True, text=True, timeout=15, env=env)
		check("станции нет — exit 0", r.returncode == 0, r.stderr[:200])
		check("станции нет — старт-скрипт не звался", not spawned(env, 0.5), "маркер есть")

		# ── станция спит: фоновый подъём с url из config.json ──
		DEAD = "http://127.0.0.1:9/mcp"
		env = make_env(DEAD)
		r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"cwd": str(project)}),
		                   capture_output=True, text=True, timeout=15, env=env)
		check("спящая станция — exit 0 и быстрый", r.returncode == 0, r.stderr[:200])
		check("спящая станция — start-скрипт зван фоном", spawned(env), "маркера нет")
		if Path(env["MARKER"]).is_file():
			args = Path(env["MARKER"]).read_text(encoding="utf-8")
			check("start-скрипту передан --url из config.json", DEAD in args, args[:120])

		# ── станция жива: хук ничего не делает ──
		with http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler) as live:
			threading.Thread(target=live.serve_forever, daemon=True).start()
			url_live = f"http://127.0.0.1:{live.server_port}/mcp"
			env = make_env(url_live)
			r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"cwd": str(project)}),
			                   capture_output=True, text=True, timeout=15, env=env)
			check("живая станция — exit 0", r.returncode == 0, r.stderr[:200])
			check("живая станция — повторно не будим", not spawned(env, 0.5), "маркер есть")
			live.shutdown()

	return summary()


if __name__ == "__main__":
	sys.exit(main())
