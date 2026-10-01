/* zcode-workflow
args:
  goal:
    type: string
    description: "Бизнес-поток, который надо зафиксировать e2e-тестом: что открыть, что сделать, что проверить"
  name:
    type: string
    description: "Имя теста (kebab-case) для tests/e2e/test_<name>.py; пусто — автор придумает из goal"
  profile:
    type: string
    description: "Имя профиля тестовой базы из .zcode/testpilot/profiles.yaml"
    default: "main"
description: "Агент сам пишет e2e-тест 1С (pytest + Python API 1c-testpilot): разведка формы, тест с readback-проверками, прогон до зелёного, коммит. Человек ничего не нажимает"
whenToUse: "Нужно покрыть бизнес-сценарий 1С e2e-тестом: новая приёмка, регресс на найденный баг, фиксация эталонного потока. Сценарий пишется агентом, не записывается за человеком"
*/

// Write-first: носитель сценария — код теста в tests/e2e/, а не запись чужих
// кликов. Автор сам разведёт форму Python API testpilot (тест-клиент поднимется
// сам, в приватном Xvfb), напишет тест, прогонит run_e2e.sh и отитерит до
// зелёного. Независимый сверщик после этого читает журнал регистрации: тест,
// оставляющий ошибки в ЖР, не принимается.

interface E2eSummary {
	collected: number;
	passed: number;
	failed: number;
	errors: number;
	skipped: number;
	exit_code: number;
	junit: boolean;
}

interface AuthorResult {
	/** Путь к написанному тесту, относительный. */
	testFile: string;
	/** Сколько тестов в файле. */
	testCount: number;
	/** Финальный прогон зелёный (run_e2e.sh exit 0). */
	green: boolean;
	/** E2E_SUMMARY_JSON финального прогона; null — до прогона не дошло. */
	summary: E2eSummary | null;
	/** Какой бизнес-поток фиксирует тест, по-русски. */
	covers: string;
	/** Как тест очищает за собой данные. */
	cleanup: string;
	/** Тест закоммичен. */
	committed: boolean;
	/** Что не доведено (пусто — всё ок). */
	blockers: string;
}

interface JournalScan {
	scanned: boolean;
	errors: string[];
	warnings: string[];
	note: string;
}

const goal = String(args.goal ?? "");
const profile = String(args.profile ?? "main");
const name = String(args.name ?? "");
if (!goal) throw new Error("Задай args.goal: какой бизнес-поток фиксировать e2e-тестом");

phase("Автор пишет и отлаживает e2e-тест");
const author = agent("автор e2e-теста", {
	system:
		"Ты — автор e2e-тестов 1С. Носитель сценария — код: pytest + Python API 1c-testpilot " +
		"в tests/e2e/ проекта. Человек в цикле не участвует: тест-клиент testpilot поднимает сам " +
		"по профилю (base из .zcode/testpilot/profiles.yaml, desktop: isolated — приватный Xvfb).\n\n" +
		"Канон работы:\n" +
		"1. Разведка формы — Python API, не по памяти. Окружение: $HOME/.local/venvs/1c-testpilot/bin/python. " +
		"Из корня проекта:\n" +
		"   TC1C_PROFILES_FILE=.zcode/testpilot/profiles.yaml <venv-python> -c '...'\n" +
		"   from testpilot import Client; c = Client(profile=\"" + profile + "\") — клиент поднимется сам; " +
		"c.execute_command(command=\"e1cib/data/Справочник.X\"), c.find_object(cls=\"ManagedForm\"), " +
		"form.get_context() — имена формы и элементов, c.call(\"find_objects\", cls=\"Edit\", ...) — поля. " +
		"После разведки c.close() + c.stop_client() — не оставляй клиент висеть.\n" +
		"2. Шаблон теста:\n" +
		"```python\n" +
		"import pytest\n\n" +
		"@pytest.fixture\ndef form(testpilot):\n" +
		"    testpilot.execute_command(command=\"e1cib/data/Справочник.X\")\n" +
		"    f = testpilot.find_object(cls=\"ManagedForm\", timeout=30)\n" +
		"    ctx = f.get_context()\n" +
		"    assert ctx[\"form\"][\"form_name\"].startswith(\"Справочник.X.\"), ctx[\"form\"][\"form_name\"]\n" +
		"    return f\n\n" +
		"def test_<name>(testpilot, form):\n" +
		"    ...  # шаги + readback-проверки\n" +
		"```\n" +
		"   Проверка имени формы отличает целевую карточку от неожиданного диалога.\n" +
		"3. Каждое действие — readback: set/ввод подтверждай get_text()/get_data_presentation(), " +
		"«нажал и надеюсь» не проверка. find_object требует ровно одно совпадение — уточняй условия поиска.\n" +
		"4. Данные: создавай с уникальным маркером (префикс «Е2Е-» + дата-время), в конце удаляй " +
		"созданное (через форму или пометку на удаление). Чужие данные не мутируй.\n" +
		"5. Прогон и итерации: bash .zcode/1c/scripts/run_e2e.sh " + profile + " <путь к файлу> — " +
		"таймаут не меньше 300000. До зелёного не больше 6 итераций; не сошлось — честные blockers, " +
		"не выдумывай зелёный.\n" +
		"6. Финальный прогон гонит весь файл. Закоммить ТОЛЬКО файл теста: " +
		"git add <файл> && git commit -m \"e2e: <что покрыл>\".\n\n" +
		"Справка по API и граблям: скилл .zcode/skills/1c-test-contour/SKILL.md. Отвечай по-русски.",
});
const authored = await author.ask<AuthorResult>(
	"Напиши e2e-тест по требованию:\n" + goal + "\n\n" +
		(name ? "Имя файла: tests/e2e/test_" + name + ".py. " : "Имя файла — tests/e2e/test_<kebab>.py, выбери сам. ") +
		"Профиль: " + profile + ". Разведи форму живьём, напиши тест, прогони до зелёного, закоммить файл. " +
		"Верни типизированный результат. Отвечай по-русски.",
);
log(
	"Автор: " + authored.testFile + ", тестов " + authored.testCount +
		", зелёный: " + authored.green + (authored.blockers ? ", блокеры: " + authored.blockers : ""),
);

phase("Независимая сверка по журналу регистрации");
const oracle = agent("сверщик журнала", {
	system:
		"Ты — независимый сверщик: смотришь только журнал регистрации базы 1С через " +
		"MCP сервер 1c-db (get_event_log), не зная, что делал автор теста. Найди записи " +
		"Error/Fatal за указанное окно. Если сервер недоступен — честно верни scanned=false " +
		"с причиной, не подставляй пустой результат.",
});
const scan = await oracle.ask<JournalScan>(
	"Сверь журнал регистрации тестовой базы за последние 20 минут (окно авторинга и прогона " +
		"e2e-теста, профиль " + profile + "). Верни ошибки Error/Fatal строками «Тип — Дата — Комментарий», " +
		"предупреждения — отдельно. Отвечай по-русски.",
);
log("ЖР-сверка: " + (scan.scanned ? "ошибок " + scan.errors.length : "не выполнена — " + scan.note));

phase("Вердикт и отчёт");
const s = authored.summary;
const verdictOk =
	authored.green &&
	authored.testCount > 0 &&
	s !== null &&
	s.junit &&
	s.failed === 0 &&
	s.errors === 0 &&
	scan.scanned &&
	scan.errors.length === 0;
const reasons: string[] = [];
if (!authored.green) reasons.push("автор не довёл прогон до зелёного");
if (authored.testCount === 0) reasons.push("ноль тестов в файле");
if (s === null || !s.junit) reasons.push("junit-отчёта нет — прогон не выполнился");
else if (s.failed > 0 || s.errors > 0) reasons.push("упавших: " + s.failed + ", ошибок: " + s.errors);
if (!scan.scanned) reasons.push("ЖР-сверка не выполнена: " + scan.note);
if (scan.errors.length > 0) reasons.push("ошибки в ЖР: " + scan.errors.length);
if (authored.blockers) reasons.push("блокеры автора: " + authored.blockers);

const reportLines = [
	"# E2E-авторинг: " + (verdictOk ? "ЗЕЛЁНЫЙ" : "КРАСНЫЙ"),
	"",
	"- Тест: " + authored.testFile + " (тестов: " + authored.testCount + ", закоммичен: " + (authored.committed ? "да" : "НЕТ") + ")",
	"- Покрывает: " + authored.covers,
	"- Очистка данных: " + authored.cleanup,
	s ? "- Финальный прогон: собрано " + s.collected + ", прошло " + s.passed + ", упало " + (s.failed + s.errors) : "- Прогон не дошёл до junit",
	"- ЖР-сверка: " + (scan.scanned ? "выполнена, ошибок " + scan.errors.length : "НЕ выполнена — " + scan.note),
	"",
	"## Вердикт",
	verdictOk
		? "Тест принят: написан, прогнан до зелёного, журнал регистрации чист" + (authored.committed ? ", закоммичен." : ". ЗАКОММИТЬ ФАЙЛ ТЕСТА.")
		: "Тест не принят:",
	...(verdictOk ? [] : reasons.map((r) => "- " + r)),
	"",
	"## Журнал регистрации",
	...(scan.errors.length ? scan.errors.map((e) => "- ERROR: " + e) : ["- ошибок Error/Fatal нет"]),
	...(scan.warnings.length ? scan.warnings.map((w) => "- WARN: " + w) : []),
];
await artifact.markdown("e2e-author-report", reportLines.join("\n"), {
	title: "Отчёт e2e-авторинга",
	description: "Написанный агентом e2e-тест: покрытие, прогон, ЖР-сверка, вердикт.",
	primary: true,
});

return {
	verdict: verdictOk ? "passed" : "failed",
	testFile: authored.testFile,
	testCount: authored.testCount,
	journalErrors: scan.errors.length,
	committed: authored.committed,
	blockers: authored.blockers,
};
