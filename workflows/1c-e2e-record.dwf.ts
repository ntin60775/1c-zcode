/* zcode-workflow
name: 1c-e2e-record
description: Ассистированная запись e2e-сценария 1С: тест-клиент поднимается, человек водит сессию, агент останавливает запись, сохраняет XML-сценарий и проверяет его реплеем.
whenToUse: когда нужен новый e2e-сценарий testpilot с внешним оракулом — записать реальную человеческую сессию (record-first), а не сочинять шаги агентом.
scope: project
args:
  profile:
    type: string
    description: Имя профиля тестовой базы в profiles.yaml.
    default: main
  name:
    type: string
    description: Имя сценария (файл .zcode/testpilot/scenarios/<name>.xml).
    required: true
*/

// Record-first: канонический сценарий пишет человек в живой сессии, агент —
// сопровождает (поднимает клиент, запускает запись, ждёт готовности через
// эскалацию, останавливает, сохраняет, реплеем доказывает воспроизводимость).
// Агент НЕ сочиняет шаги с нуля — только параметризует записанное.

interface RecordOutcome {
	/** Тест-клиент запущен. */
	launched: boolean;
	/** Запись начата. */
	recording: boolean;
	/** Что сказать человеку перед его сессией. */
	instructions: string;
}

interface SavedScenario {
	/** Сценарий сохранён в .zcode/testpilot/scenarios/. */
	saved: boolean;
	/** Путь к XML относительно корня проекта. */
	path: string;
	/** Шагов в сценарии. */
	steps: number;
	/** Реплей записанного прошёл без ошибок. */
	replayOk: boolean;
}

const profile = String(args.profile ?? "main");
const name = String(args.name ?? "");

phase("Подготовка записи сессии");
const tester = agent("оператор записи", {
	system:
		"Ты — оператор записи e2e-сценариев 1С через MCP сервер 1c-testpilot. " +
		"Подними тест-клиент (tc_session launch_client с профилем), начни запись " +
		"сценария (tc_scenario). Когда запись идёт — НЕ выполняй шаги за человека: " +
		"сессию водит человек. Если запись или клиент недоступны — эскалируй с " +
		"фактами, не имитируй.",
});
const prepared = await tester.ask<RecordOutcome>(
	"Профиль: " + profile + ", имя сценария: " + name + ". " +
		"Подними тест-клиент и начни запись. Затем эскалируй вопрос человеку: " +
		"«Запись идёт — води сессию в тест-клиенте; ответь, когда закончил». " +
		"В instructions — краткая памятка человеку (какие формы открывать, чего " +
		"избегать). Отвечай по-русски.",
);
log("Запись подготовлена: клиент " + (prepared.launched ? "поднят" : "НЕ поднят"));

phase("Человек водит сессию");
// Ожидание идёт через эскалацию оператора: сессию водит человек, оператор
// стоит на паузе до его ответа.
const humanDone = await tester.ask<string>(
	"Человек ответил. Останови запись, сохрани сценарий в " +
		".zcode/testpilot/scenarios/" + (name || "scenario") + ".xml и сразу " +
		"проверь воспроизводимость одним реплеем. Отвечай по-русски.",
);

phase("Сохранение и контрольный реплей");
const saver = agent("контролёр сценария", {
	system:
		"Ты — контролёр записанного сценария: смотри XML файла, посчитай шаги, " +
		"оцени параметризуемость (захардкоженные данные пометь), не редактируй " +
		"исходники 1С. Если реплей не запускался — честно верни replayOk=false.",
});
const saved = await saver.ask<SavedScenario>(
	"Сценарий записан оператором: " + JSON.stringify(humanDone) + ". " +
		"Проверь файл, шаги и результат контрольного реплея. Отвечай по-русски.",
);

phase("Итог записи");
const ok = saved.saved && saved.replayOk;
await artifact.markdown(
	"record-report",
	[
		"# Запись e2e-сценария: " + (ok ? "готов" : "требует доработки"),
		"",
		"- Сценарий: " + saved.path,
		"- Шагов: " + saved.steps,
		"- Контрольный реплей: " + (saved.replayOk ? "прошёл" : "НЕ прошёл"),
		"",
		"Записанный сценарий — канон: правки формы кода по нему проверяются " +
			"прогоном workflow 1c-e2e-run.",
	].join("\n"),
	{ title: "Итог записи сценария", primary: true },
);

return {
	saved: saved.saved,
	replayOk: saved.replayOk,
	steps: saved.steps,
	path: saved.path,
};
