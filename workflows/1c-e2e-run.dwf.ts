/* zcode-workflow
args:
  tests:
    type: string
    description: "Каталог или файл e2e-тестов pytest относительно корня проекта; пусто — tests/e2e"
  profile:
    type: string
    description: "Имя профиля тестовой базы из .zcode/testpilot/profiles.yaml"
    default: "main"
description: "E2E-прогон 1С без человека: pytest + Python API 1c-testpilot (тест-клиент поднимается сам в приватном Xvfb), при фейле — расследование, независимая ЖР-сверка, анти-ложно-зелёный вердикт кодом"
whenToUse: "Прогнать e2e-тесты 1С (tests/e2e) на тестовой базе и получить доказательный вердикт: приёмка после правок, регресс, прогон равный CI"
*/

// Прогон исполняет scripts/run_e2e.sh — детерминированный pytest, модель в
// прогоне не участвует (тот же результат в CI). Субагенты нужны только для
// расследования фейлов и независимой ЖР-сверки. Вердикт считает КОД:
// ноль собранных тестов, ошибки прогона или ошибки в журнале регистрации = красный.

interface E2eSummary {
	collected: number;
	passed: number;
	failed: number;
	errors: number;
	skipped: number;
	exit_code: number;
	junit: boolean;
}

interface RunOutcome {
	/** Код возврата run_e2e.sh. */
	exitCode: number;
	/** E2E_SUMMARY_JSON из хвоста вывода; null — скрипт упал до прогона. */
	summary: E2eSummary | null;
	/** Хвост вывода (последние ~80 строк) для отчёта и расследования. */
	tail: string;
}

interface JournalScan {
	/** ЖР-сверка выполнена (get_event_log за окно прогона). */
	scanned: boolean;
	/** Записи Error/Fatal за окно прогона: «Тип — Дата — Комментарий». */
	errors: string[];
	/** Предупреждения, не решающие вердикт. */
	warnings: string[];
	/** Если сверка не удалась — почему (для отчёта). */
	note: string;
}

const profile = String(args.profile ?? "main");
const tests = String(args.tests ?? "tests/e2e");

phase("Прогон e2e-тестов (pytest, без модели)");
const runner = agent("e2e-прогонщик", {
	system:
		"Ты запускаешь ровно одну команду и честно возвращаешь её результат. " +
		"Ничего не чини, не перезапускай, не интерпретируй — только факты вывода.",
});
const run = await runner.ask<RunOutcome>(
	"Выполни в корне проекта команду:\n" +
		"  bash .zcode/1c/scripts/run_e2e.sh " + profile + " " + tests + "\n" +
		"Это может занять несколько минут (поднимается тест-клиент 1С) — задай таймаут не меньше 300000. " +
		"Из вывода возьми: последнюю строку вида E2E_SUMMARY_JSON {...} (распарси JSON в поле summary; " +
		"если строки нет — summary=null) и последние ~80 строк вывода (в поле tail). " +
		"Код возврата команды верни как exitCode. Отвечай по-русски.",
);
log(
	"Прогон: exit " + run.exitCode +
	(run.summary ? ", собрано " + run.summary.collected + ", упало " + (run.summary.failed + run.summary.errors) : ", summary нет"),
);

phase("Независимая сверка по журналу регистрации");
const oracle = agent("сверщик журнала", {
	system:
		"Ты — независимый сверщик: смотришь только журнал регистрации базы 1С через " +
		"MCP сервер 1c-db (get_event_log), не зная, что делал прогон. Твоя задача — " +
		"найти записи Error/Fatal за указанное окно времени. Если сервер недоступен — " +
		"честно верни scanned=false с причиной, не подставляй пустой результат.",
});
const scan = await oracle.ask<JournalScan>(
	"Сверь журнал регистрации тестовой базы за последние 15 минут (окно e2e-прогона, " +
		"профиль " + profile + "). Верни ошибки Error/Fatal строками «Тип — Дата — Комментарий», " +
		"предупреждения — отдельно. Отвечай по-русски.",
);
log("ЖР-сверка: " + (scan.scanned ? "ошибок " + scan.errors.length : "не выполнена — " + scan.note));

let diagnosis = "";
if (run.exitCode !== 0 || (run.summary !== null && run.summary.collected === 0)) {
	phase("Расследование фейла");
	const investigator = agent("расследователь e2e-фейла", {
		system:
			"Ты — диагност e2e-контура 1С. Ищешь причину фейла прогона pytest + testpilot, " +
			"не чиня ничего: только диагноз и конкретное предложение. Можешь читать " +
			"test-results/ (артефакты, скриншоты, junit), сами тесты tests/e2e/, и при " +
			"необходимости развести форму Python API testpilot " +
			"($HOME/.local/venvs/1c-testpilot/bin/python, Client из testpilot, профиль из " +
			".zcode/testpilot/profiles.yaml). Канон — скилл .zcode/skills/1c-test-contour/SKILL.md. " +
			"Базу не мутируй: только чтение форм/данных.",
	});
	diagnosis = await investigator.ask<string>(
		"Прогон e2e упал. Вывод:\n\n" + run.tail.slice(-4000) + "\n\n" +
			"Дай диагноз: что упало, почему (корневая причина, не симптом), что конкретно править — " +
			"тест или код конфигурации. Не применяй правки. Отвечай по-русски, абзац-два.",
	);
	log("Диагноз получен");
}

phase("Вердикт приёмки и отчёт");
// Вердикт считает код — модель не может «оценить» прогон зелёным.
// exit 3 run_e2e.sh = зелёный pytest, но остатки Е2Е-данных после sweep
// (зачистка в run_e2e.sh). Ложнозелёным быть не должно — красный с причиной.
const s = run.summary;
const verdictOk =
	run.exitCode === 0 &&
	s !== null &&
	s.junit &&
	s.collected > 0 &&
	s.failed === 0 &&
	s.errors === 0 &&
	scan.scanned &&
	scan.errors.length === 0;
const reasons: string[] = [];
if (run.exitCode !== 0) reasons.push("run_e2e.sh завершился с кодом " + run.exitCode);
if (run.exitCode === 3) reasons.push("exit 3 = остатки Е2Е-данных после зачистки sweep — база грязная, тесты всерьёз не считаются зелёными");
if (s === null) reasons.push("нет E2E_SUMMARY_JSON — прогон не дошёл до тестов");
else {
	if (!s.junit) reasons.push("junit-отчёт не создан — тесты не выполнились");
	if (s.collected === 0) reasons.push("ноль собранных тестов — анти-ложно-зелёный принцип");
	if (s.failed > 0 || s.errors > 0) reasons.push("упавших: " + s.failed + ", ошибок: " + s.errors);
}
if (!scan.scanned) reasons.push("ЖР-сверка не выполнена: " + scan.note);
if (scan.errors.length > 0) reasons.push("ошибки в ЖР: " + scan.errors.length);

const reportLines = [
	"# E2E-прогон: " + (verdictOk ? "ЗЕЛЁНЫЙ" : "КРАСНЫЙ"),
	"",
	"- Профиль: " + profile + ", тесты: " + tests,
	s ? "- Собрано: " + s.collected + ", прошло: " + s.passed + ", упало: " + (s.failed + s.errors) + (s.skipped ? ", пропущено: " + s.skipped : "") : "- Сводки нет",
	"- ЖР-сверка: " + (scan.scanned ? "выполнена, ошибок " + scan.errors.length : "НЕ выполнена — " + scan.note),
	"",
	"## Вердикт",
	verdictOk
		? "Прогон принят: pytest зелёный, тесты собраны и выполнены, журнал регистрации чист."
		: "Прогон не принят:",
	...(verdictOk ? [] : reasons.map((r) => "- " + r)),
	"",
	...(diagnosis ? ["## Диагноз", diagnosis, ""] : []),
	"## Вывод pytest",
	"```",
	run.tail.trim(),
	"```",
	"",
	"## Журнал регистрации",
	...(scan.errors.length ? scan.errors.map((e) => "- ERROR: " + e) : ["- ошибок Error/Fatal нет"]),
	...(scan.warnings.length ? scan.warnings.map((w) => "- WARN: " + w) : []),
];
await artifact.markdown("e2e-report", reportLines.join("\n"), {
	title: "Отчёт e2e-прогона",
	description: "Вердикт приёмки, сводка pytest, диагноз фейла и ЖР-сверка.",
	primary: true,
});

return {
	verdict: verdictOk ? "passed" : "failed",
	collected: s ? s.collected : 0,
	failed: s ? s.failed + s.errors : -1,
	journalErrors: scan.errors.length,
	profile,
};
