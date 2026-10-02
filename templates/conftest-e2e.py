# conftest-e2e.py — шаблон для tests/e2e/ проекта (контур 1c-zcode).
#
# Копируй в tests/e2e/conftest.py при первом тесте. Даёт фикстуру `onec_db` —
# тонкий MCP-клиент к серверу 1c-db (python3 stdlib, без зависимостей) для
# тестов уровня ЛОГИКИ: инварианты модулей, расчёты, проверки заполнения —
# то, что в старом стеке защищали юниты (yAxUnit).
#
# Контракт execute_code (MCP_Toolkit): BSL-код выполняется на сервере;
# результат читается из переменной `Результат`, которую код обязан установить.
# Ответ: {"success": true, "data": ...} — сериализация 1С-значений.
#
# Тест-контур: 1c-db должен быть поднят (scripts/start_1c_db.sh); в проде
# execute_code заблокирован гейтом — уровень только для тестовых деревьев.

import json
import os
import pathlib
import re
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class OnecDB:
	"""Минимальный MCP-клиент (Streamable HTTP) к 1c-db: execute/query."""

	def __init__(self, url: str, timeout: int = 120):
		self.url = url
		self.timeout = timeout
		self._sid = None
		self._id = 0
		self._initialize()

	def _next_id(self) -> int:
		self._id += 1
		return self._id

	def _post(self, payload: dict, notify: bool = False):
		headers = {"Content-Type": "application/json",
		           "Accept": "application/json, text/event-stream"}
		if self._sid:
			headers["Mcp-Session-Id"] = self._sid
		req = urllib.request.Request(
			self.url, data=json.dumps(payload).encode("utf-8"),
			headers=headers, method="POST")
		try:
			with urllib.request.urlopen(req, timeout=self.timeout) as resp:
				sid = resp.headers.get("Mcp-Session-Id")
				if sid:
					self._sid = sid
				raw = resp.read().decode("utf-8")
				ctype = resp.headers.get("Content-Type", "")
		except urllib.error.HTTPError as e:
			raise RuntimeError(f"1c-db HTTP {e.code}: {e.read()[:300]}") from e
		if notify:
			return None
		if "text/event-stream" in ctype:
			for line in raw.splitlines():
				if line.startswith("data:"):
					candidate = line[5:].strip()
					if candidate:
						return json.loads(candidate)
			raise RuntimeError("1c-db вернул пустой SSE-поток")
		return json.loads(raw)

	def _initialize(self):
		resp = self._post({"jsonrpc": "2.0", "id": self._next_id(),
		                   "method": "initialize",
		                   "params": {"protocolVersion": "2025-03-26",
		                              "capabilities": {},
		                              "clientInfo": {"name": "1c-zcode-pytest", "version": "0.4"}}})
		if "error" in resp:
			raise RuntimeError(f"1c-db initialize: {resp['error']}")
		self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, notify=True)

	def _call(self, tool: str, args: dict):
		resp = self._post({"jsonrpc": "2.0", "id": self._next_id(),
		                   "method": "tools/call",
		                   "params": {"name": tool, "arguments": args}})
		if "error" in resp:
			raise RuntimeError(f"1c-db {tool}: {resp['error']}")
		result = resp.get("result", {})
		if result.get("isError"):
			text = "; ".join(c.get("text", "") for c in result.get("content", [])
			                 if isinstance(c, dict))
			raise RuntimeError(f"1c-db {tool}: {text[:500]}")
		if result.get("structuredContent") is not None:
			return result["structuredContent"]
		for c in result.get("content", []):
			if isinstance(c, dict) and c.get("type") == "text":
				try:
					return json.loads(c["text"])
				except json.JSONDecodeError:
					return c["text"]
		return result

	# ── публичное API ──

	def execute(self, code: str, context: str = "server"):
		"""Выполнить BSL на сервере; вернуть значение переменной `Результат`."""
		data = self._call("execute_code",
		                  {"code": code, "execution_context": context})
		# тулкит оборачивает ответ дополнительно в {"result": {...}}
		if isinstance(data, dict) and isinstance(data.get("result"), dict):
			data = data["result"]
		if isinstance(data, dict) and data.get("success") is False:
			raise RuntimeError(f"BSL ошибка: {data.get('error', '?')[:500]}")
		payload = data.get("data") if isinstance(data, dict) else data
		if payload is None:
			raise RuntimeError("execute_code: код не установил переменную `Результат`")
		# сериализатор тулкита отдаёт 1С-строку как JSON-литерал: '"e74e-…"'
		if isinstance(payload, str) and payload[:1] == '"':
			try:
				payload = json.loads(payload)
			except json.JSONDecodeError:
				pass
		return payload

	def execute_json(self, code: str):
		"""BSL обязан установить `Результат` JSON-строкой; вернуть python-объект."""
		return json.loads(self.execute(code))

	def query(self, text: str, limit: int = 100):
		"""Выполнить запрос; вернуть строки результата."""
		data = self._call("execute_query", {"query": text, "limit": limit})
		if isinstance(data, dict) and isinstance(data.get("result"), dict):
			data = data["result"]
		if isinstance(data, dict) and isinstance(data.get("data"), str):
			try:
				data = {**data, "data": json.loads(data["data"])}
			except json.JSONDecodeError:
				pass
		return data

	# ── тестовые данные (замена юнит-фикстур) ──

	@staticmethod
	def _bsl_str(value) -> str:
		# строковый литерал 1С: двойные кавычки, экранирование удвоением
		return '"' + str(value).replace('"', '""') + '"'

	def create_object(self, type_name: str, attrs: dict,
	                  post: bool = False, marker: str = "Е2Е-"):
		"""Создать справочник/документ с плоскими реквизитами; вернуть GUID ссылки.

		attrs: {ИмяРеквизита: значение(строка/число/bool)}; Наименование/Номер
		без маркера получают префикс `marker` (очистка по нему).
		post=True — провести документ. Только менеджерный путь
		(Справочники.X.СоздатьЭлемент()): Новый(Тип("СправочникОбъект.X"))
		в окружении Выполнить тулкита ломает установку реквизитов.
		"""
		name = attrs.get("Наименование") or attrs.get("Номер") or ""
		body = dict(attrs)
		if name and not str(name).startswith(marker):
			key = "Наименование" if "Наименование" in body else "Номер"
			body[key] = marker + str(body[key])
		if not re.fullmatch(r"[А-Яа-яЁё\w]+", type_name):
			raise ValueError(f"type_name должен быть идентификатором 1С: {type_name!r}")
		klass = "документ" if post or "Номер" in body else "справочник"
		manager = {"документ": "Документы", "справочник": "Справочники"}[klass]
		create = "СоздатьДокумент()" if klass == "документ" else "СоздатьЭлемент()"
		lines = [f"Е2ЕОб = {manager}.{type_name}.{create};"]
		for key, value in body.items():
			if isinstance(value, bool):
				expr = "Истина" if value else "Ложь"
			elif isinstance(value, (int, float)):
				expr = str(value)
			else:
				expr = self._bsl_str(value)
			lines.append(f"Е2ЕОб.{key} = {expr};")
		if klass == "документ":
			lines.append("Е2ЕОб.Записать(" + ("РежимЗаписиДокумента.Проведение);" if post else "РежимЗаписиДокумента.Запись);"))
		else:
			lines.append("Е2ЕОб.Записать();")
		lines.append("Результат = Е2ЕОб.Ссылка.УникальныйИдентификатор();")
		guid = self.execute("\n".join(lines))
		return {"type": type_name, "guid": str(guid), "klass": klass}

	def delete_object(self, type_name: str, guid: str, klass: str = "справочник"):
		if not re.fullmatch(r"[А-Яа-яЁё\w]+", type_name):
			raise ValueError(f"type_name должен быть идентификатором 1С: {type_name!r}")
		manager = {"документ": "Документы", "справочник": "Справочники"}[klass]
		self.execute(
			f"Е2ЕСсылка = {manager}.{type_name}.ПолучитьСсылку("
			f"Новый УникальныйИдентификатор({self._bsl_str(guid)}));\n"
			"Е2ЕОб = Е2ЕСсылка.ПолучитьОбъект();\n"
			"Если Е2ЕОб <> Неопределено Тогда Е2ЕОб.Удалить(); КонецЕсли;\n"
			"Результат = Истина;")

	def set_constant(self, name: str, value: str):
		"""Записать константу (канон мока интеграции: URL внешнего сервиса — из константы)."""
		self.execute(f"Константы.{name}.Установить({self._bsl_str(value)});\n"
		             "Результат = Истина;")


def _resolve_url() -> str:
	url = os.environ.get("ONEC_DB_URL")
	if url:
		return url
	cfg = pathlib.Path(".zcode/1c/contour.json")
	if cfg.is_file():
		try:
			stored = json.loads(cfg.read_text(encoding="utf-8")) \
				.get("1c_db", {}).get("url")
			if stored:
				return stored
		except json.JSONDecodeError:
			pass
	return "http://127.0.0.1:6003/mcp"


@pytest.fixture(scope="session")
def onec_db():
	try:
		client = OnecDB(_resolve_url())
		client.execute("Результат = Истина;")
	except Exception as e:  # noqa: BLE001 — внятный фейл вместо трейса внутри фикстуры
		raise RuntimeError(
			"1c-db не отвечает — подними тестовый клиент: "
			"bash .zcode/1c/scripts/start_1c_db.sh (прод-гейт execute_code блокирует)"
		) from e
	return client


class StubServer:
	"""Локальная HTTP-заглушка внешней системы (мок интеграции).

	routes: {(method, path): (status, body_str)}. Запросы пишутся в .calls —
	тест может проверить, что конфигурация обратилась и с теми ли параметрами.
	Использование: ss.route(...); httpd = ThreadingHTTPServer(("127.0.0.1", 0),
	ss.handler_class()); ss._port = httpd.server_address[1] — или фикстура
	stub_server, которая делает это сама.
	"""

	def __init__(self):
		self.calls = []
		self.routes = {}
		self._port = 0

	def route(self, method: str, path: str, status: int = 200, body: str = "{}"):
		self.routes[(method.upper(), path)] = (status, body)
		return self

	@property
	def url(self) -> str:
		return f"http://127.0.0.1:{self._port}"

	def handler_class(self):
		server = self

		class Handler(BaseHTTPRequestHandler):
			def _route(self):
				key = (self.command, self.path.split("?", 1)[0])
				server.calls.append({"method": self.command, "path": self.path})
				status, body = server.routes.get(
					key, (404, '{"error": "stub: нет маршрута"}'))
				payload = body.encode("utf-8")
				self.send_response(status)
				self.send_header("Content-Type", "application/json; charset=utf-8")
				self.send_header("Content-Length", str(len(payload)))
				self.end_headers()
				self.wfile.write(payload)

			do_GET = do_POST = do_PUT = _route
			log_message = lambda *a, **k: None  # noqa: E731

		return Handler


@pytest.fixture()
def stub_server():
	server = StubServer()
	httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.handler_class())
	server._port = httpd.server_address[1]
	thread = threading.Thread(target=httpd.serve_forever, daemon=True)
	thread.start()
	yield server
	httpd.shutdown()
	httpd.server_close()
