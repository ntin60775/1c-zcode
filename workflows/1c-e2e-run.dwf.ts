/* zcode-workflow
name: 1c-e2e-run
description: E2E-прогон бизнес-сценария 1С через testpilot с независимой сверкой по журналу регистрации и анти-ложно-зелёным вердиктом (нулёвые проверки = красный).
whenToUse: когда нужно выполнить записанный e2e-сценарий (или набор) на тестовой базе 1С и получить доказательный вердикт: прогоны после правок, приёмка задач, регресс перед поставкой.
scope: project
args:
  scenario:
    type: string
    description: Путь к файлу сценария testpilot (XML) относительно корня проекта; пусто — все сценарии из .zcode/testpilot/scenarios/.
  profile:
    type: string
    description: Имя профиля тестовой базы в profiles.yaml.
    default: main
*/

// E2E-прогон с внешним оракулом (контур 1c-zcode):
//   1) тестировщик гонит сценарий инструментами mcp__1c-testpilot__* и
//      возвращает readback-доказательства;
//   2) НЕЗАВИСИМЫЙ субагент сверяет журнал регистрации через mcp__1c-db__get_event_log —
//      он не видел, что делал тестировщик;
//   3) вердикт приёмки считает КОД, не модель: нулевые проверки, отсутствие
//      ЖР-сверки или ошибки в ЖР = красный (анти-ложно-зелёный принцип).

interface StepOutcome {
	/** Имя шага сценария. */
	name: string;
	/** Шаг прошёл (readback подтвердил ожидание). */
	ok: boolean;
	/** Сколько содержательных проверок сделано на шаге (readback != 0 — проверка). */
	checks: number;
	/** Доказательство: value_before/value_after, сообщения форм, скриншот-путь. */
	evidence: string;
}

interface RunResult {
	/** Тест-клиент запущен и сценарий выполнялся. */
	launched: boolean;
	/** Имя профиля тестовой базы. */
	profile: string;
	/** Шаги с доказательствами. */
	steps: StepOutcome[];
	/** Сводка одним-двумя предложениями, по-русски. */
	summary: string;
}

interface JournalScan {
	/** ЖР-сверка выполнена (get_event_log по окну прогона). */
	scanned: boolean;
	/** Записи Error/Fatal за окно прогона: «Тип — Дата — Комментарий». */
	errors: string[];
	/** Предупреждения, не решающие вердикт. */
	warnings: string[];
	/** Если сверка не удалась — почему (для отчёта). */
	note: string;
}

const profile = String(args.profile ?? "main");
const scenario = String(args.scenario ?? "");

phase("Прогон сценария тест-клиентом");
const runner = agent("e2e-тестировщик", {
	system:
		"Ты — e2e-тестировщик 1С. Гоняешь записанные сценарии инструментами MCP " +
		"сервера 1c-testpilot (базовый набор: tc_session, tc_find, tc_window, tc_field, " +
		"tc_table; прочие — точечно, их схемы дороги). Каждое изменение формы " +
		"подтверждай readback-ом (value_before/value_after из ответа инструмента) — " +
		"«нажал и надеюсь» не считается. Сценарии — XML testpilot; реплей через " +
		"tc_scenario. Если инструмент недоступен или сценарий не воспроизводится — " +
		"эскалируй с фактами, не выдумывай результат.",
});
const run = await runner.ask<RunResult>(
	"Прогони e2e-сценарий 1С на тестовой базе и верни типизированный результат. " +
		"Профиль: " + profile + ". " +
		(scenario
			? "Сценарий: " + scenario + ". "
			: "Сценарии: все XML из .zcode/testpilot/scenarios/ (нет каталога — эскалируй). ") +
		"Поток: tc_session launch_client(profile) → шаги → tc_session close. " +
		"Запиши время старта и финиша прогона (для окна ЖР-сверки) в summary. " +
		"Отвечай по-русски.",
);
log("Прогон завершён: шагов " + run.steps.length + ", запущен: " + run.launched);

phase("Независимая сверка по журналу регистрации");
const checksTotal = run.steps.reduce((sum, s) => sum + s.checks, 0);
const oracle = agent("сверщик журнала", {
	system:
		"Ты — независимый сверщик: смотришь только журнал регистрации базы 1С через " +
		"MCP сервер 1c-db (get_event_log), не зная, что делал тестировщик. Твоя задача — " +
		"найти записи Error/Fatal за указанное окно времени. Если сервер недоступен — " +
		"честно верни scanned=false с причиной, не подставляй пустой результат.",
});
const scan = await oracle.ask<JournalScan>(
	"Сверь журнал регистрации тестовой базы за окно прогона e2e-сценария " +
		"(окно и контекст: " + JSON.stringify(run.summary) + "). " +
		"Верни ошибки Error/Fatal строками «Тип — Дата — Комментарий», предупреждения — " +
		"отдельно. Отвечай по-русски.",
);
log(
	"ЖР-сверка: " + (scan.scanned ? "ошибок " + scan.errors.length : "не выполнена — " + scan.note),
);

phase("Вердикт приёмки и отчёт");
// Вердикт считает код — модель не может «оценить» прогон зелёным.
const verdictOk =
	run.launched &&
	checksTotal > 0 &&
	scan.scanned &&
	scan.errors.length === 0 &&
	run.steps.every((s) => s.ok);
const reasons: string[] = [];
if (!run.launched) reasons.push("тест-клиент не запущен или сценарий не выполнялся");
if (checksTotal === 0) reasons.push("ноль содержательных проверок — анти-ложно-зелёный принцип");
if (!scan.scanned) reasons.push("ЖР-сверка не выполнена: " + scan.note);
if (scan.errors.length > 0) reasons.push("ошибки в ЖР: " + scan.errors.length);

const reportLines = [
	"# E2E-прогон: " + (verdictOk ? "ЗЕЛЁНЫЙ" : "КРАСНЫЙ"),
	"",
	"- Профиль: " + run.profile,
	"- Шагов: " + run.steps.length + ", проверок: " + checksTotal,
	"- ЖР-сверка: " + (scan.scanned ? "выполнена, ошибок " + scan.errors.length : "НЕ выполнена — " + scan.note),
	"",
	"## Вердикт",
	verdictOk ? "Сценарий принят: проверки подтверждены readback-ом, ЖР чист." : "Прогон не принят:",
	...(verdictOk ? [] : reasons.map((r) => "- " + r)),
	"",
	"## Шаги",
	...run.steps.map((s) => "- " + (s.ok ? "✓" : "✗") + " " + s.name + " (проверок: " + s.checks + ") — " + s.evidence),
	"",
	"## Журнал регистрации",
	...(scan.errors.length ? scan.errors.map((e) => "- ERROR: " + e) : ["- ошибок Error/Fatal нет"]),
	...(scan.warnings.length ? scan.warnings.map((w) => "- WARN: " + w) : []),
	"",
	"## Сводка прогона",
	run.summary,
];
await artifact.markdown("e2e-report", reportLines.join("\n"), {
	title: "Отчёт e2e-прогона",
	description: "Вердикт приёмки, шаги с readback-доказательствами и ЖР-сверка.",
	primary: true,
});

return {
	verdict: verdictOk ? "passed" : "failed",
	checksTotal,
	journalErrors: scan.errors.length,
	profile: run.profile,
	summary: run.summary,
};
