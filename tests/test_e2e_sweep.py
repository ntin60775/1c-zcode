#!/usr/bin/env python3
"""Тесты e2e_sweep.py: прогон как процесса против fake MCP-прокси 1c-db.

Fake-сервер эмулирует handshake + tools/call execute_code: check возвращает
зафиксированный список «остатков», apply — удаляет (счётчик) или отказывает
(маркер с FAIL); отдельные режимы — «клиент молчит» (SSE keep-alive без
ответа, бюджет режет по --timeout) и «ответ без схемы found» (прокси жив,
клиент 1С не подключен — тот самый KeyError из issue #20).
Запуск: python3 tests/test_e2e_sweep.py
"""
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SWEEP = REPO / "scripts" / "e2e_sweep.py"

ITEMS = [
	{"t": "Справочник.Контрагенты", "g": "guid-cat-1", "n": "Е2Е-0101-А"},
	{"t": "Справочник.Контрагенты", "g": "guid-cat-2", "n": "Е2Е-0101-Б"},
	{"t": "Документ.Платёжка", "g": "guid-doc-1", "n": "Е2Е-0101-1"},
]


class FakeProxy(ThreadingHTTPServer):
	def __init__(self, clean_after_apply: bool = True):
		super().__init__(("127.0.0.1", 0), Handler)
		self.cleaned = False
		self.clean_after_apply = clean_after_apply
		self.checks = 0
		self.deleted_calls = 0
		self.hang_calls = False
		self.nokey_calls = False


class Handler(BaseHTTPRequestHandler):
	server: FakeProxy

	def log_message(self, *a):
		pass

	def _respond(self, payload, session="fake-session", status=200):
		body = json.dumps(payload).encode("utf-8")
		self.send_response(status)
		self.send_header("Content-Type", "application/json")
		if session:
			self.send_header("Mcp-Session-Id", session)
		self.send_header("Content-Length", str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def _hang_open(self, req_id):
		"""Long-poll без клиента: SSE-поток с keep-alive — socket-timeout
		не срабатывает (прокси капает данными), резать должен общий бюджет."""
		self.send_response(200)
		self.send_header("Content-Type", "text/event-stream")
		self.send_header("Mcp-Session-Id", "fake-session")
		self.end_headers()
		try:
			while True:
				self.wfile.write(b": keepalive\n\n")
				self.wfile.flush()
				time.sleep(0.2)
		except OSError:
			pass

	def do_POST(self):
		req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
		method = req.get("method", "")
		if method == "initialize":
			return self._respond({"jsonrpc": "2.0", "id": req["id"], "result": {
				"protocolVersion": "2024-11-05", "capabilities": {}}})
		if method.startswith("notifications/"):
			return self._respond({}, session=None, status=202)
		if method == "tools/call":
			if self.server.hang_calls:
				return self._hang_open(req["id"])
			code = req["params"]["arguments"]["code"]
			if self.server.nokey_calls:
				# клиент заявился и погас: прокси отдаёт ответ тулкита без схемы свипа
				text = json.dumps({"success": True,
				                   "data": json.dumps(
				                       {"error": "клиент не подключен"},
				                       ensure_ascii=False)},
				                  ensure_ascii=False)
				return self._respond({"jsonrpc": "2.0", "id": req["id"], "result": {
					"content": [{"type": "text", "text": text}]}})
			if "Удалить" in code:
				self.server.deleted_calls += 1
				if self.server.clean_after_apply:
					self.server.cleaned = True
					result = {"found": len(ITEMS), "deleted": len(ITEMS), "failed": []}
				else:
					result = {"found": len(ITEMS), "deleted": 0,
					          "failed": [i["t"] + ": " + i["n"] for i in ITEMS]}
				return self._respond({"jsonrpc": "2.0", "id": req["id"], "result": {
					"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}})
			# check: маркер доходит до запроса?
			if "ПОДОБНО" not in code:
				return self._respond({"jsonrpc": "2.0", "id": req["id"], "error":
					{"message": "ожидался запрос ПОДОБНО"}}, session=None)
			self.server.checks += 1
			found = [] if self.server.cleaned else ITEMS
			result = {"found": len(found), "items": found}
			return self._respond({"jsonrpc": "2.0", "id": req["id"], "result": {
				"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}})
		return self._respond({"jsonrpc": "2.0", "id": req.get("id", 0), "error":
			{"message": "unknown method"}}, session=None)


def run_sweep(*args):
	return subprocess_run([sys.executable, str(SWEEP), *args])


def subprocess_run(cmd):
	import subprocess
	return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def main() -> int:
	with FakeProxy() as srv:
		threading.Thread(target=srv.serve_forever, daemon=True).start()
		url = f"http://127.0.0.1:{srv.server_address[1]}/mcp"

		# ── check: чисто → exit 0 ──
		srv.cleaned = True
		r = run_sweep("/tmp/nowhere", "--url", url)
		check("check чисто: exit 0", r.returncode == 0, r.stdout + r.stderr)
		check("check чисто: текст про отсутствие остатков", "нет" in r.stdout, r.stdout)

		# ── check: есть остатки → exit 1, счёт назван ──
		srv.cleaned = False
		r = run_sweep("/tmp/nowhere", "--url", url)
		check("check остатки: exit 1", r.returncode == 1, r.stdout + r.stderr)
		check("check остатки: счёт в выводе", "3" in r.stdout, r.stdout)

		# ── apply: чистит, документы раньше справочников ──
		srv.deleted_calls = 0
		srv.cleaned = False
		r = run_sweep("/tmp/nowhere", "--url", url, "--apply")
		check("apply: exit 0", r.returncode == 0, r.stdout + r.stderr)
		check("apply: один проход удаления", srv.deleted_calls == 1, str(srv.deleted_calls))
		check("apply: удалено 3", "зачищено: 3; осталось: 0" in r.stdout, r.stdout)

		# ── apply: машиночитаемый json-вывод ──
		srv.cleaned = False
		r = run_sweep("/tmp/nowhere", "--url", url, "--apply", "--json")
		data = json.loads(r.stdout.strip().splitlines()[-1])
		check("apply json: deleted=3, found_after=0",
		      data.get("deleted") == 3 and data.get("found_after") == 0, str(data))

		# ── apply: не удаляется → exit 3, неудачи названы ──
		srv.cleaned = False
		srv.clean_after_apply = False
		r = run_sweep("/tmp/nowhere", "--url", url, "--apply")
		check("apply неудача: exit 3", r.returncode == 3, r.stdout + r.stderr)
		check("apply неудача: остались после зачистки", "осталось: 3" in r.stdout, r.stdout)
		srv.clean_after_apply = True

		# ── недоступный прокси → exit 2 ──
		r = run_sweep("/tmp/nowhere", "--url", "http://127.0.0.1:1/mcp", "--json")
		check("недоступен: exit 2", r.returncode == 2, r.stdout + r.stderr)
		check("недоступен: причина в stderr", "недоступен" in r.stderr, r.stderr)
		data = json.loads(r.stdout.strip().splitlines()[-1])
		check("недоступен: error=proxy", data.get("error") == "proxy", str(data))

		# ── прокси жив, клиент никогда не подключался: long-poll с keep-alive,
		#    режет общий бюджет (--timeout), не висит (issue #20) ──
		srv.hang_calls = True
		t0 = time.monotonic()
		r = run_sweep("/tmp/nowhere", "--url", url, "--timeout", "2", "--json")
		elapsed = time.monotonic() - t0
		srv.hang_calls = False
		check("клиент молчит: exit 2 по бюджету", r.returncode == 2, r.stdout + r.stderr)
		check("клиент молчит: не висит (уложился в 30 с)", elapsed < 30, f"{elapsed:.1f} с")
		data = json.loads(r.stdout.strip().splitlines()[-1])
		check("клиент молчит: error=client-unavailable",
		      data.get("error") == "client-unavailable", str(data))
		check("клиент молчит: причина в stderr", "не подключен" in r.stderr, r.stderr)

		# ── прокси жив, клиент ответил без схемы found → exit 2, не KeyError ──
		srv.nokey_calls = True
		r = run_sweep("/tmp/nowhere", "--url", url, "--json")
		srv.nokey_calls = False
		check("ответ без found: exit 2", r.returncode == 2, r.stdout + r.stderr)
		check("ответ без found: KeyError пойман", "KeyError" not in r.stderr, r.stderr)
		data = json.loads(r.stdout.strip().splitlines()[-1])
		check("ответ без found: error=client-unavailable",
		      data.get("error") == "client-unavailable", str(data))
		check("ответ без found: причина в stderr", "не подключен" in r.stderr, r.stderr)

	# ── маркер кастомный доходит до запроса (в коде ПОДОБНО с маркером) ──
	with FakeProxy() as srv:
		threading.Thread(target=srv.serve_forever, daemon=True).start()
		url = f"http://127.0.0.1:{srv.server_address[1]}/mcp"
		seen = []
		orig = Handler.do_POST
		def spy(self):
			# самодостаточный обработчик: orig нельзя звать после чтения потока
			req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
			method = req.get("method", "")
			if method == "initialize":
				return self._respond({"jsonrpc": "2.0", "id": req["id"], "result": {
					"protocolVersion": "2024-11-05", "capabilities": {}}})
			if method.startswith("notifications/"):
				return self._respond({}, session=None, status=202)
			seen.append(req["params"]["arguments"]["code"])
			result = {"found": 0, "items": []}
			return self._respond({"jsonrpc": "2.0", "id": req.get("id", 0), "result": {
				"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}})
		Handler.do_POST = spy
		try:
			run_sweep("/tmp/nowhere", "--url", url, "--marker", "Е2Е-0502-")
		finally:
			Handler.do_POST = orig
		check("кастомный маркер в запросе",
		      any("Е2Е-0502-" in c for c in seen), str(seen)[:200])

	return summary()


if __name__ == "__main__":
	sys.exit(main())
