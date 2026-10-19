#!/usr/bin/env python3
"""Тесты bsl_review_join.py — скрипт-джойн ревьюера 1c-bsl-review-standards.

Скрипт как процесс (argv → код возврата, stdout/stderr), парсеры вендорен-
таблицы routing.md — юнит-импортом. Спека: docs/BSL-REVIEW-JOIN-SPEC.md.

Запуск: python3 tests/test_bsl_review_join.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bsl_review_join import parse_exceptions, parse_routing  # noqa: E402
from _lib import check, summary  # noqa: E402

SCRIPT = REPO / "scripts" / "bsl_review_join.py"
VENDOR_ROUTING = REPO / "skills" / "1c-bsl-review-standards" / "references" / "routing.md"
FIXTURES = REPO / "tests" / "fixtures" / "bsl-review-join"
NODE = "main:CommonModule.Продажи"

JOURNAL_OK = """version: 1
exceptions:
  - id: style-element-color-reset
    diagnostic: StyleElementConstructors
    subject: "Новый Цвет"
    scope: all
    standard: "#std660"
    rationale: >
      Сброс цвета через „Новый Цвет“ — осознанный приём, эквивалента нет.
    decided: 2026-10-11
    owner: owner
"""

JOURNAL_INVALID = """version: 1
exceptions:
  - id: no-rationale
    diagnostic: StyleElementConstructors
    subject: "Новый Цвет"
    scope: all
    standard: "#std660"
    decided: 2026-10-11
"""

JOURNAL_V2 = """version: 2
exceptions: []
"""


def run(*argv):
	return subprocess.run([sys.executable, str(SCRIPT), *argv],
	                      capture_output=True, text=True, cwd=str(REPO))


def write_journal(tmp, text, name="standards-exceptions.yaml"):
	path = Path(tmp) / name
	path.write_text(text, encoding="utf-8")
	return str(path)


def main() -> int:
	project = str(FIXTURES)  # проект здесь не важен: журнал подаём явно
	page = str(FIXTURES / "check_page.json")

	with tempfile.TemporaryDirectory() as tmp:
		# ── вендорен-таблица: маркерные строки раньше fallback ──
		r = run("--node", NODE, "--check-out", page, "--project", project,
		        "--routing", str(VENDOR_ROUTING), "--exceptions", write_journal(tmp, JOURNAL_OK))
		check("прогон по вендорен-таблице exit 0", r.returncode == 0, r.stderr[:200])
		rep = json.loads(r.stdout)
		check("schema", rep["schema"] == "bsl-review-join/1", rep.get("schema"))
		f = {x["code"] + "@" + x["location"][-2:]: x for x in rep["findings"]}

		tek = f["DeprecatedPlatformApi@12"]
		check("ТекущаяДата → маркерная строка", "АПК:1388" not in tek["report_line"]
		      and tek["verdict_candidate"] == "fix", tek["report_line"][:120])
		check("fallback-строка (общий) не съела маркерную",
		      tek["row"]["standard"] == "std из подсказки", tek["row"]["standard"])

		bsp = f["DeprecatedPlatformApi@16"]
		check("ПодробноеПредставлениеОшибки → risk", bsp["verdict_candidate"] == "risk",
		      bsp["report_line"][:120])
		check("класс compile-risk присутствует", "compile-risk" in bsp["row"]["classes"],
		      bsp["row"]["classes"])

		sty = f["StyleElementConstructors@20"]
		check("waived гасит находку", sty["verdict_candidate"] == "waived",
		      sty["report_line"][:120])
		check("waived сохраняет строку отчёта", "#std660" in sty["report_line"]
		      and "style-element-color-reset" in sty["report_line"],
		      sty["report_line"][:160])

		qry = f["LogicalOrInTheWhereSectionOfQuery@24"]
		check("query-performance → report", qry["verdict_candidate"] == "report",
		      qry["report_line"][:120])

		unk = f["UnknownDiagnostic@28"]
		check("нет строки таблицы → needs-docs, exit 0", unk["verdict_candidate"] == "needs-docs",
		      unk["report_line"][:120])

		c = rep["counts"]
		check("счётчики", (c["total"], c["waived"], c["report"], c["unknown"])
		      == (5, 1, 1, 1), c)
		check("pages_complete", rep["pages_complete"] is True, rep["pages_complete"])
		check("pages", rep["pages"] == 1, rep.get("pages"))

		# ── журнал без rationale/owner: не гасит, виден в problems ──
		r = run("--node", NODE, "--check-out", page, "--project", project,
		        "--routing", str(VENDOR_ROUTING), "--exceptions", write_journal(tmp, JOURNAL_INVALID))
		rep = json.loads(r.stdout)
		sty = [x for x in rep["findings"] if x["code"] == "StyleElementConstructors"][0]
		check("недействительная запись не гасит", sty["verdict_candidate"] == "fix",
		      sty["verdict_candidate"])
		check("недействительная запись в journal_problems", any("no-rationale" in p
		      for p in rep["journal_problems"]), rep["journal_problems"])

		# ── version: 2 — fail-closed ──
		r = run("--node", NODE, "--check-out", page, "--project", project,
		        "--routing", str(VENDOR_ROUTING), "--exceptions", write_journal(tmp, JOURNAL_V2))
		check("version: 2 → exit 2", r.returncode == 2, f"exit {r.returncode}")
		check("fail-closed называет version", "version" in r.stderr, r.stderr[:160])

		# ── отсутствие файла журнала легально ──
		r = run("--node", NODE, "--check-out", page, "--project", tmp,
		        "--routing", str(VENDOR_ROUTING))
		check("нет журнала → exit 0, все живые", r.returncode == 0, r.stderr[:200])
		rep = json.loads(r.stdout)
		sty = [x for x in rep["findings"] if x["code"] == "StyleElementConstructors"][0]
		check("без журнала StyleElementConstructors живой (#std660)",
		      sty["verdict_candidate"] == "fix" and "#std660" in sty["report_line"],
		      sty["report_line"][:160])

		# ── waived не гасит compile-risk: запись под risk-находку ──
		risk_journal = write_journal(tmp, """version: 1
exceptions:
  - id: try-waive-risk
    diagnostic: DeprecatedPlatformApi
    subject: "ПодробноеПредставлениеОшибки"
    scope: all
    standard: "АПК:1388"
    rationale: >
      Пытаемся гасить compile-risk журналом — запрещено контрактом.
    decided: 2026-10-11
    owner: owner
""", name="risk-journal.yaml")
		r = run("--node", NODE, "--check-out", page, "--project", project,
		        "--routing", str(VENDOR_ROUTING), "--exceptions", risk_journal)
		rep = json.loads(r.stdout)
		bsp = [x for x in rep["findings"] if x["code"] == "DeprecatedPlatformApi"
		       and x["verdict_candidate"] == "risk"]
		check("журнал не гасит risk", len(bsp) == 1, rep["findings"])

		# ── scope file: без различимого пути — предупреждение, не молчание ──
		file_journal = write_journal(tmp, """version: 1
exceptions:
  - id: file-scope
    diagnostic: StyleElementConstructors
    subject: "Новый Цвет"
    scope: file:src/cf/Продажи.bsl
    standard: "#std660"
    rationale: >
      Только один файл, но в находке пути нет.
    decided: 2026-10-11
    owner: owner
""", name="file-journal.yaml")
		r = run("--node", NODE, "--check-out", page, "--project", project,
		        "--routing", str(VENDOR_ROUTING), "--exceptions", file_journal)
		rep = json.loads(r.stdout)
		check("file: без пути в находке — предупреждение", any("не покрыл находку" in w
		      for w in rep["warnings"]), rep["warnings"])
		sty = [x for x in rep["findings"] if x["code"] == "StyleElementConstructors"][0]
		check("file: без пути не гасит", sty["verdict_candidate"] == "fix",
		      sty["verdict_candidate"])

		# ── недочитанный cursor ──
		cursor_page = str(FIXTURES / "check_page_cursor.json")
		r = run("--node", NODE, "--check-out", page, "--check-out", cursor_page,
		        "--project", project, "--routing", str(VENDOR_ROUTING),
		        "--exceptions", write_journal(tmp, JOURNAL_OK))
		rep = json.loads(r.stdout)
		check("cursor → pages_complete false", rep["pages_complete"] is False,
		      rep["pages_complete"])
		check("дедуп страниц: total 6", rep["counts"]["total"] == 6, rep["counts"])
		r = run("--node", NODE, "--check-out", page, "--check-out", cursor_page,
		        "--project", project, "--routing", str(VENDOR_ROUTING),
		        "--exceptions", write_journal(tmp, JOURNAL_OK), "--text")
		check("text помечает недочитанное", "НЕТ — дочитай" in r.stdout, r.stdout[:200])

	# ── незнакомая структура: диагностика без code → exit 2 ──
	with tempfile.TemporaryDirectory() as tmp:
		broken = Path(tmp) / "broken.json"
		broken.write_text(json.dumps({"data": {"status": "failed", "diagnostics": [
			{"message": "без кода"}]}}), encoding="utf-8")
		r = run("--node", NODE, "--check-out", str(broken), "--project", tmp,
		        "--routing", str(VENDOR_ROUTING))
		check("диагностика без code → exit 2", r.returncode == 2, f"exit {r.returncode}")
		check("stderr называет причину", "без кода" in r.stderr or "код" in r.stderr,
		      r.stderr[:160])

		# ── routing с нарушенным заголовком → exit 2 ──
		r = run("--node", NODE, "--check-out", page, "--project", tmp,
		        "--routing", str(FIXTURES / "routing_bad.md"))
		check("битая routing-таблица → exit 2", r.returncode == 2, f"exit {r.returncode}")

		# ── не JSON → exit 2 ──
		garbage = Path(tmp) / "garbage.json"
		garbage.write_text("не json", encoding="utf-8")
		r = run("--node", NODE, "--check-out", str(garbage), "--project", tmp,
		        "--routing", str(VENDOR_ROUTING))
		check("не JSON → exit 2", r.returncode == 2, f"exit {r.returncode}")

	# ── юнит: парсер вендорен-таблицы, строки-семьи — без code ──
	rows = parse_routing(VENDOR_ROUTING)
	codes = [r["code"] for r in rows if r["code"]]
	check("вендорен-таблица: 10 строк с кодом, 8 уникальных",
	      len(codes) == 10 and len(set(codes)) == 8, codes)
	check("DeprecatedPlatformApi: маркерные + fallback",
	      sum(1 for r in rows if r["code"] == "DeprecatedPlatformApi") == 3, rows)
	check("семья naming без code", any(r["code"] is None and "naming" in r["classes"]
	      for r in rows), [r["classes"] for r in rows])
	check("compile-risk-класс у ПодробноеПредставлениеОшибки",
	      any(r["marker_core"] == "ПодробноеПредставлениеОшибки"
	          and "compile-risk" in r["classes"] for r in rows),
	      [r["marker_core"] for r in rows])

	# ── юнит: журнал — неизвестный ключ верхнего уровня падает, записи с key-value ──
	with tempfile.TemporaryDirectory() as tmp:
		p = write_journal(tmp, "version: 1\nextra: x\nexceptions: []\n")
		warnings = []
		try:
			parse_exceptions(p, warnings)
			check("незнакомый верхний ключ → fail-closed", False, "не упал")
		except Exception as e:
			check("незнакомый верхний ключ → fail-closed", "extra" in str(e), str(e))
		p = write_journal(tmp, """version: 1
exceptions:
  - id: a
    diagnostic: X
    subject: "s"
    scope: all
    rationale: r
    owner: o
    future-key: reserved
""")
		warnings = []
		recs = parse_exceptions(p, warnings)
		check("неизвестный ключ записи — предупреждение, запись жива",
		      recs[0]["valid"] is True and any("future-key" in w for w in warnings),
		      (recs, warnings))

	return summary()


if __name__ == "__main__":
	sys.exit(main())
