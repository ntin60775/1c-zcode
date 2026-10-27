#!/usr/bin/env python3
"""migrate_from_omp.py — миграция проекта 1С со старого omp-контура на 1c-zcode.

Атомарна: старое удаляется и новое разводится в одном заходе (двойных
гейтов в одном проекте не бывает). По умолчанию — dry-run (печать плана),
--apply — исполнение. Git-коммиты делает агент, не скрипт: tracked-файлы
удаляются через `git rm`, но не коммитятся.

Шаги:
  1. Архив аудита старого контура: ~/.omp/logs/rule-audit.jsonl →
     <проект>/docs/ops/gate-audit-archive.jsonl.
  2. Эскалации: .omp/unica-gate-escalations.txt → .zcode/unica-gate-escalations.txt
     (формат сохранён; докомментарии переносятся).
  3. AGENTS.md: вставить/заменить секцию контура между маркерами
     BEGIN/END 1C CONTOUR (шаблон .zcode/1c/templates/agents-section.md).
     KB-секцию (ontoship, .gitmark) НЕ трогаем.
  4. v8project.yaml → форма 0.15: убрать builder/basePath/execution_timeout/
     partialLoadThreshold/верхнеуровневый connection; привязку ИБ перенести
     в v8project.local.yaml как infobases.origin; build → push; прежние
     имена providers переименовать.
  5. Правила проекта переезжают: .omp/rules/*.md → docs/omp-migrated/rules/
     (git mv; смысловое из RULES.md покрывает секция контура).
  5.5 Старый тест-стек (Vanessa/xUnit): фичи features/*.feature →
     docs/omp-migrated/tests/features/ (источник конвертации в pytest-e2e);
     VAParams/yaxunit-конфиги → docs/omp-migrated/tests/; шаблоны фич,
     tasks/mcp, Vanessa-epf, ключи tests:/va в v8project.yaml — снос.
     Юнит-расширение (Тесты) не переносится принципиально — задача в отчёт.
  6. Удалить .omp/ целиком: tracked — git rm, ignored (plugins/, backup) — rm.
  7. .gitignore: убрать строки omp-блока (.omp/…).
  8. tasks/init-worktree.sh: перепривязать на .zcode/1c/scripts/init_worktree.sh.
  9. Разводка .zcode/config.json (wire_config.py), если контур вендорен.
  10. Критерий чистоты: grep `\\.omp|omp plugin|unica-gate\\.ts|~/\\.omp` = 0
      находок вне git-истории; legacy-артефакты — отчётом.

Запуск: python3 migrate_from_omp.py <project-root> [--apply]
Exit: 0 — план напечатан/миграция прошла, 1 — проект не подходит/ошибка.
"""
import re
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
AUDIT_SRC = HOME / ".omp" / "logs" / "rule-audit.jsonl"
OMP_DIR = ".omp"
OMP_GITIGNORE_RE = re.compile(r"^\.omp/")
SECTION_BEGIN = "<!-- BEGIN 1C CONTOUR (1c-zcode) -->"
SECTION_END = "<!-- END 1C CONTOUR -->"
LEGACY_MARKERS = ("tasks/mcp", "tools/yaxunit.json", "tools/VAParams.json",
                  "features/")
CLEAN_GREP = re.compile(
	r"\.omp\b|omp plugin|unica-gate\.ts|~/\.omp|vanessa|yaxunit|VAParams", re.IGNORECASE)

PLANNED = []
DONE = []
FAILED = []


def note(msg):
	PLANNED.append(msg)
	print("  • " + msg)


def run_git(root: Path, *args):
	return subprocess.run(["git", "-C", str(root), *args],
	                      capture_output=True, text=True)


def tracked(root: Path, rel: str) -> bool:
	done = run_git(root, "ls-files", "--", rel)
	return bool(done.stdout.strip())


def remove_path(root: Path, rel: str, apply: bool):
	path = root / rel
	if not path.exists() and not path.is_symlink():
		note(f"нет: {rel}")
		return
	if tracked(root, rel):
		if apply:
			done = run_git(root, "rm", "-rq", "--ignore-unmatch", rel)
			if done.returncode != 0:
				FAILED.append(f"git rm {rel}: {done.stderr.strip()}")
				print(f"  ✗ git rm {rel}: {done.stderr.strip()}")
				return
			# git rm не трогает ignored-файлы внутри (plugins/, backup) — дочищаем
			if path.exists():
				import shutil
				shutil.rmtree(path, ignore_errors=True)
		DONE.append(f"git rm {rel}")
		note(f"git rm: {rel} (tracked)")
	elif apply:
		if path.is_dir():
			import shutil
			shutil.rmtree(path)
		else:
			path.unlink()
		DONE.append(f"rm {rel}")
		note(f"rm: {rel} (не в git)")
	else:
		note(f"rm: {rel} (не в git)")


def migrate_test_stack(root: Path, apply: bool):
	"""Шаг 5.5: старый Vanessa/xUnit-стек → активы в архив, инфраструктура снос."""
	# ── 5.5 Старый тест-стек (xUnit/Vanessa): активы переехали, инфраструктура сносится ──
	print("5.5 Старый тест-стек (Vanessa/xUnit)")
	dst_features = root / "docs" / "omp-migrated" / "tests" / "features"
	features_dir = root / "features"
	if features_dir.is_dir():
		fl = sorted(features_dir.glob("**/*.feature"))
		if apply:
			dst_features.mkdir(parents=True, exist_ok=True)
		moved = 0
		for f in fl:
			target = dst_features / f.relative_to(features_dir)
			if apply:
				target.parent.mkdir(parents=True, exist_ok=True)
				if tracked(root, str(f.relative_to(root))):
					run_git(root, "mv", str(f.relative_to(root)), str(target))
				else:
					import shutil
					shutil.move(str(f), str(target))
				DONE.append(str(target))
			moved += 1
		if moved:
			note(f"фичи ({moved}) → docs/omp-migrated/tests/features/ — ИСТОЧНИК конвертации "
			     "в pytest-e2e (скилл 1c-test-contour, «Перенос Vanessa-сценариев»); "
			     "после зелёного прогона конвертированного теста фичу из архива удаляй")
		else:
			note("features/ пуст")
		if apply:
			for d in sorted((p for p in features_dir.rglob("*") if p.is_dir()),
			                key=lambda x: len(str(x)), reverse=True):
				if not any(d.iterdir()):
					d.rmdir()
			if not any(features_dir.iterdir()):
				features_dir.rmdir()
				DONE.append("rmdir features/")
		import shutil as _sh
		for tpl in sorted((root / "fixtures" / "шаблоны-фич").glob("*")) if (root / "fixtures" / "шаблоны-фич").is_dir() else []:
			if apply:
				if tracked(root, str(tpl.relative_to(root))):
					run_git(root, "rm", "-q", str(tpl.relative_to(root)))
				else:
					tpl.unlink()
			note(f"снести шаблон фичи: {tpl.relative_to(root)} (болванка Vanessa, не актив)")
	else:
		note("features/ нет")

	for rel in ("tools/VAParams.json", "tools/yaxunit.json"):
		p = root / rel
		if p.is_file():
			target = root / "docs" / "omp-migrated" / "tests" / p.name
			if apply:
				target.parent.mkdir(parents=True, exist_ok=True)
				if tracked(root, rel):
					run_git(root, "mv", rel, str(target))
				else:
					import shutil
					shutil.move(str(p), str(target))
				DONE.append(str(target))
			note(f"{rel} → docs/omp-migrated/tests/{p.name} (параметры/таймауты — контекст конвертации)")
	for rel in ("tasks/mcp",):
		if (root / rel).exists():
			if apply:
				remove_path(root, rel, apply=True)
			else:
				note(f"снести: {rel} (замещает MCP 1c-db)")
	epf = root / "build" / "tools" / "vanessa-automation-single.epf"
	if epf.is_file() and apply:
		epf.unlink()
		DONE.append(str(epf))
		note("снести build/tools/vanessa-automation-single.epf (обработка Vanessa)")

	# v8project.yaml: тест-блоки старого стека (tests: c yaxunit/va; va-подблоки)
	v8 = root / "v8project.yaml"
	if v8.is_file():
		lines = v8.read_text(encoding="utf-8").splitlines()
		out, drop_tests, drop_va = [], False, False
		changed_stack = []
		i = 0
		while i < len(lines):
			ln = lines[i]
			if re.match(r"^tests\s*:", ln):
				block, keys, j = [], [], i + 1
				while j < len(lines) and lines[j][:1] in (" ", "\t"):
					if lines[j][:2] == "  " and lines[j][2:3] not in (" ", "\t"):
						keys.append(lines[j].split(":", 1)[0].strip())
					block.append(lines[j]); j += 1
				if keys and set(keys) <= {"yaxunit", "va", "execution_timeout_seconds"}:
					changed_stack.append(f"убран блок tests: ({', '.join(keys)} — стек старого контура)")
					i = j
					continue
				else:
					changed_stack.append("block tests: содержит посторонние ключи — НЕ тронут, разбери руками")
					out.append(ln); out.extend(block); i = j
					continue
			if re.match(r"^\s*va\s*:\s*$", ln):
				indent = len(ln) - len(ln.lstrip())
				block, j = [ln], i + 1
				while j < len(lines) and (not lines[j].strip()
						or len(lines[j]) - len(lines[j].lstrip()) > indent):
					block.append(lines[j]); j += 1
				body = "\n".join(block)
				if "vanessa" in body.lower() or "VAParams" in body:
					changed_stack.append("убран va-подблок (vanessa epf/параметры)")
					i = j
					continue
			out.append(ln); i += 1
		if apply and changed_stack:
			v8.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
			DONE.append("v8project.yaml (тест-стек)")
		for entry in changed_stack:
			note(f"v8project.yaml: {entry}")
		src_test = (root / "tests" / "cfe" / "Тесты").is_dir() or "Тесты" in "\n".join(lines)
		if src_test:
			note("расширение юнит-тестов (tests/cfe/Тесты): код не переносится, но "
			     "ЗАЩИЩАЕМАЯ ЛОГИКА обязана переехать — инварианты модулей → "
			     "юниты tests/unit (фикстура onec_db, execute_code), "
			     "пользовательские пути → GUI-e2e "
			     "(скилл 1c-test-contour, «Юниты и e2e»); после переноса всех "
			     "проверок снеси source-set «Тесты»")



def main() -> int:
	apply = "--apply" in sys.argv
	test_only = "--test-stack-only" in sys.argv
	positional = [a for a in sys.argv[1:] if not a.startswith("--")]
	root = Path(positional[0]).resolve() if positional else Path.cwd()

	if not (root / "v8project.yaml").is_file():
		print(f"✗ {root} — не проект 1С (нет v8project.yaml)")
		return 1
	omp = root / OMP_DIR
	if not test_only and not omp.is_dir():
		print(f"✗ {root} — следа старого контура нет ({OMP_DIR}/); миграция не нужна")
		return 1

	if not test_only:
		# Префлайт до первой мутации: шаги 3 и 9 требуют вендоренного контура;
		# без проверки миграция падает в середине, оставив проект полумигрированным.
		missing = [str(p.relative_to(root)) for p in (
			root / ".zcode" / "1c" / "scripts" / "wire_config.py",
			root / ".zcode" / "1c" / "templates" / "agents-section.md") if not p.is_file()]
		if missing:
			print("✗ контур не вендорен в проекте (нет: " + ", ".join(missing) + ") — сначала:")
			print("    python3 <sot-zcode-marketplace>/deploy/deploy-plugin.py install 1c-zcode <корень>")
			if apply:
				return 1
			print("  (dry-run продолжается; --apply прервётся здесь же)")

	if test_only:
		print(f"Проект: {root}")
		print("Режим: ТОЛЬКО тест-стек Vanessa/xUnit (--test-stack-only)")
		print()
		migrate_test_stack(root, apply)
		print()
		if not apply:
			print("DRY-RUN завершён. Применить: --apply")
			return 0
		if FAILED:
			print(f"С ЗАМЕЧАНИЯМИ: {len(FAILED)} ошибок")
			for f in FAILED:
				print(f"  - {f}")
			return 1
		print(f"ТЕСТ-СТЕК ПЕРЕНЕСЁН: {len(DONE)} изменений. Проверь /1c-doctor, затем git-коммит.")
		return 0

	print(f"Проект: {root}")
	print(f"Режим: {'ПРИМЕНЕНИЕ' if apply else 'DRY-RUN (план; --apply для исполнения)'}")
	print()

	# ── 1. Аудит старого контура ──
	print("1. Аудит гейтов старого контура")
	audit_dst = root / "docs" / "ops" / "gate-audit-archive.jsonl"
	if AUDIT_SRC.is_file() and not audit_dst.is_file():
		if apply:
			audit_dst.parent.mkdir(parents=True, exist_ok=True)
			audit_dst.write_bytes(AUDIT_SRC.read_bytes())
			DONE.append(str(audit_dst))
		note(f"архив: {AUDIT_SRC} → {audit_dst}")
	elif audit_dst.is_file():
		note(f"архив уже на месте: {audit_dst}")
	else:
		note("журнал аудита старого контура не найден — архивировать нечего")

	# ── 2. Эскалации ──
	print("2. Эскалации прямой правки")
	esc_src = omp / "unica-gate-escalations.txt"
	esc_dst = root / ".zcode" / "unica-gate-escalations.txt"
	if esc_src.is_file() and not esc_dst.is_file():
		if apply:
			esc_dst.parent.mkdir(parents=True, exist_ok=True)
			esc_dst.write_text(esc_src.read_text(encoding="utf-8"), encoding="utf-8")
			DONE.append(str(esc_dst))
		note(f"перенос: {OMP_DIR}/unica-gate-escalations.txt → .zcode/unica-gate-escalations.txt")
	elif esc_dst.is_file():
		note(".zcode/unica-gate-escalations.txt уже существует — исходник удалится вместе с .omp/")
	else:
		note("файла эскалаций нет — нечего переносить")

	# ── 3. AGENTS.md ──
	print("3. AGENTS.md: секция контура")
	agents = root / "AGENTS.md"
	section_path = root / ".zcode" / "1c" / "templates" / "agents-section.md"
	if section_path.is_file() and agents.is_file():
		section = section_path.read_text(encoding="utf-8")
		text = agents.read_text(encoding="utf-8")
		if SECTION_BEGIN in text:
			note("секция контура уже стоит — заменю на актуальный шаблон")
			pattern = re.compile(
				re.escape(SECTION_BEGIN) + r"[\s\S]*?" + re.escape(SECTION_END))
			text = pattern.sub(section.strip(), text)
			action = "заменена"
		else:
			text = text.rstrip() + "\n\n" + section.strip() + "\n"
			action = "добавлена"
		if apply:
			agents.write_text(text, encoding="utf-8")
			DONE.append("AGENTS.md")
		note(f"AGENTS.md: секция 1C CONTOUR {action} (KB-секция не тронута)")
	else:
		if not agents.is_file():
			note("AGENTS.md в проекте нет — секция вставится при bootstrap")
		if not section_path.is_file():
			print("  ⚠ шаблон секции не вендорен — сначала deploy контура в проект")
			FAILED.append("шаблон agents-section.md не найден")

	# ── 4. v8project.yaml: переход на форму 0.15 ──
	print("4. v8project.yaml → 0.15")
	v8 = root / "v8project.yaml"
	local = root / "v8project.local.yaml"
	if v8.is_file():
		try:
			v8_lines = v8.read_text(encoding="utf-8").splitlines()
		except OSError:
			v8_lines = []
		try:
			local_lines = local.read_text(encoding="utf-8").splitlines() if local.is_file() else []
		except OSError:
			local_lines = []
		changed = []

		def drop_top(lines, keys):
			"""Убрать верхнеуровневые ключи (скаляр или блок) из строк."""
			out, i, removed = [], 0, []
			while i < len(lines):
				line = lines[i]
				match = re.match(r"^([A-Za-z0-9_.-]+)\s*:", line)
				if match and match.group(1) in keys:
					removed.append(match.group(1))
					i += 1
					while i < len(lines) and lines[i][:1] in (" ", "\t"):
						i += 1
					continue
				out.append(line); i += 1
			return out, removed

		def drop_any(lines, pattern):
			"""Убрать строки по regex (например, partialLoadThreshold)."""
			out = [ln for ln in lines if not re.search(pattern, ln)]
			return out, len(out) != len(lines)

		def rename_provider_keys(lines):
			"""Прежние имена providers.* → имена 0.15 внутри секции providers."""
			mapping = {"build": "push", "dump": "pull", "init": "infobase.create",
			           "load": "upload", "infobase.configuration.export": "download"}
			out, inside, renamed = [], False, []
			for line in lines:
				if re.match(r"^providers\s*:", line):
					inside = True
					out.append(line)
					continue
				if inside:
					if line[:1] not in (" ", "\t"):
						inside = False
					else:
						match = re.match(r"^(\s+)([A-Za-z0-9_.-]+)(\s*:.*)$", line)
						if match and match.group(2) in mapping:
							renamed.append(f"{match.group(2)} → {mapping[match.group(2)]}")
							line = match.group(1) + mapping[match.group(2)] + match.group(3)
				out.append(line)
			return out, renamed

		# 0.15 отвергает эти ключи — убираем; build — прежнее имя push.
		v8_lines, removed = drop_top(
			v8_lines, {"builder", "basePath", "execution_timeout", "connection"})
		for key in removed:
			changed.append(f"убран ключ {key} (0.15 отвергает)")
		v8_lines, dropped = drop_any(v8_lines, r"^\s*partialLoadThreshold\s*:")
		if dropped:
			changed.append("убран partialLoadThreshold (0.15: полная загрузка — full:true)")
		if any(re.match(r"^build\s*:", ln) for ln in v8_lines):
			if any(re.match(r"^push\s*:", ln) for ln in v8_lines):
				v8_lines, _ = drop_top(v8_lines, {"build"})
				changed.append("удалён устаревший build: (есть push:)")
			else:
				v8_lines = [re.sub(r"^build(\s*:)", r"push\1", ln, count=1)
				            for ln in v8_lines]
				changed.append("build: → push: (прежнее имя)")
		v8_lines, renamed = rename_provider_keys(v8_lines)
		changed.extend(f"providers.{entry}" for entry in renamed)

		# Привязка ИБ — в local-слой: infobases.origin. Если local уже
		# объявляет базу, main-блок не трогаем: слияние YAML вручную.
		main_keys = [re.match(r"^(infobase|infobases)\s*:", ln).group(1)
		             for ln in v8_lines if re.match(r"^(infobase|infobases)\s*:", ln)]
		local_has = any(re.match(r"^(infobase|infobases)\s*:", ln) for ln in local_lines)
		if len(main_keys) > 1:
			note("v8project.yaml: infobase и infobases в одном файле — разбери вручную")
		elif main_keys and local_has:
			note("v8project.yaml: привязка ИБ уже есть в local-слое — main-блок не тронут")
		elif main_keys:
			key = main_keys[0]
			out, i, block = [], 0, []
			while i < len(v8_lines):
				line = v8_lines[i]
				if re.match(rf"^{key}\s*:", line):
					i += 1
					while i < len(v8_lines) and v8_lines[i][:1] in (" ", "\t"):
						block.append(v8_lines[i]); i += 1
					continue
				out.append(line); i += 1
			if key == "infobase":
				block = ["  origin:"] + ["  " + ln for ln in block]
			moved = ["infobases:"] + block if key == "infobase" else [f"{key}:"] + block
			if not local_lines:
				local_lines = ["# Локальный оверлей машины: в git не попадает."]
			local_lines += ["", "# Перенесено из v8project.yaml (0.15: привязка машины)."] + moved
			v8_lines = out
			changed.append("привязка ИБ перенесена в v8project.local.yaml (0.15)")

		if apply and changed:
			v8.write_text("\n".join(v8_lines).rstrip() + "\n", encoding="utf-8")
			if local_lines:
				local.write_text("\n".join(local_lines).rstrip() + "\n", encoding="utf-8")
			DONE.append("v8project.yaml (0.15)")
		for entry in changed:
			note(f"v8project.yaml: {entry}")
		if not changed:
			note("v8project.yaml уже в форме 0.15")
	else:
		note("v8project.yaml нет — пропуск")

	# ── 5. Правила проекта: переезд в docs/omp-migrated/rules/ ──
	print("5. Правила проекта (переживают удаление .omp/)")
	rules_dir = omp / "rules"
	if rules_dir.is_dir():
		dst = root / "docs" / "omp-migrated" / "rules"
		if apply:
			dst.mkdir(parents=True, exist_ok=True)
		for rule_file in sorted(rules_dir.glob("*.md")):
			target = dst / rule_file.name
			if apply:
				if tracked(root, f".omp/rules/{rule_file.name}"):
					run_git(root, "mv", f".omp/rules/{rule_file.name}", str(target))
				else:
					import shutil
					shutil.copy2(rule_file, target)
				DONE.append(str(target))
			note(f"переехало: .omp/rules/{rule_file.name} → {target.relative_to(root)}")
		note("смысловое из RULES.md/APPEND_SYSTEM.md покрывается секцией контура в AGENTS.md")
	else:
		note(".omp/rules/ нет")

	migrate_test_stack(root, apply)


	print("6. Старый контур .omp/")
	omp_entries = sorted(p.name for p in omp.iterdir())
	note(f"удалить целиком {OMP_DIR}/ ({', '.join(omp_entries)})")
	if apply:
		remove_path(root, OMP_DIR, apply=True)

	# ── 5. .gitignore ──
	print("7. .gitignore: omp-блок")
	gitignore = root / ".gitignore"
	if gitignore.is_file():
		lines = gitignore.read_text(encoding="utf-8").splitlines()
		kept = [ln for ln in lines if not OMP_GITIGNORE_RE.match(ln.strip())]
		if len(kept) != len(lines):
			if apply:
				gitignore.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
				DONE.append(".gitignore")
			note(f"удалить {len(lines) - len(kept)} строк(и) .omp/* из .gitignore")
		else:
			note("omp-строк в .gitignore нет")

	# ── 6. tasks/init-worktree.sh ──
	print("8. Канон init-worktree")
	init_sh = root / "tasks" / "init-worktree.sh"
	if init_sh.is_file():
		text = init_sh.read_text(encoding="utf-8")
		if "plugin-cache" in text or "node_modules/1c-omp" in text or ".omp/" in text:
			if apply:
				fixed = re.sub(
					r"[^\s\"']*1c-omp/skills/1c-project-bootstrap/scripts/init-worktree\.sh",
					".zcode/1c/scripts/init_worktree.sh", text)
				fixed = fixed.replace(".omp/contour", ".zcode/contour")
				init_sh.write_text(fixed, encoding="utf-8")
				DONE.append("tasks/init-worktree.sh")
			note("tasks/init-worktree.sh: перепривязать на .zcode/1c/scripts/init_worktree.sh")
		else:
			note("tasks/init-worktree.sh не ссылается на старый канон")
	else:
		note("tasks/init-worktree.sh нет")

	# ── 7. Разводка нового контура ──
	print("9. Новый контур")
	wire = root / ".zcode" / "1c" / "scripts" / "wire_config.py"
	if wire.is_file():
		if apply:
			done = subprocess.run([sys.executable, str(wire), str(root)],
			                       capture_output=True, text=True)
			if done.returncode == 0:
				DONE.append(".zcode/config.json")
				note(".zcode/config.json разведён (hooks+MCP); перезапусти сессию ZCode")
			else:
				FAILED.append(f"wire_config: {done.stderr.strip()}")
				print(f"  ✗ wire_config: {done.stderr.strip()}")
		else:
			note("разводка .zcode/config.json (wire_config.py)")
	else:
		print("  ⚠ файлы контура не вендорены в проект — сначала:")
		print("    python3 <sot-zcode-marketplace>/deploy/deploy-plugin.py install 1c-zcode <корень>")
		print("    … затем повтори миграцию (шаги 3 и 7 довершились бы сейчас)")
		FAILED.append("контур не вендорен в проект")

	# ── 8. Критерий чистоты + готовность v8project к 0.15 ──
	print("10. Критерий чистоты")
	v8 = root / "v8project.yaml"
	local = root / "v8project.local.yaml"
	if v8.is_file():
		try:
			v8text = v8.read_text(encoding="utf-8")
		except OSError:
			v8text = ""
		try:
			localtext = local.read_text(encoding="utf-8") if local.is_file() else ""
		except OSError:
			localtext = ""
		for key in ("builder", "basePath", "execution_timeout", "connection"):
			if re.search(rf"(?m)^{key}\s*:", v8text):
				print(f"  ⚠ v8project.yaml: ключ {key} отвергается 0.15 — убери")
		if re.search(r"(?m)^\s*partialLoadThreshold\s*:", v8text):
			print("  ⚠ v8project.yaml: partialLoadThreshold отвергается 0.15 —")
			print("    полная загрузка запрашивается push full:true")
		if re.search(r"(?m)^infobase\s*:", v8text):
			print("  • v8project.yaml: infobase — legacy-синоним; канон 0.15 —")
			print("    infobases.origin.connection в v8project.local.yaml")
		if re.search(r"(?m)^infobases\s*:", v8text):
			print("  • v8project.yaml: infobases читается 0.15, но канон — local-слой")
		if not re.search(r"(?m)^infobases\s*:", localtext) \
				and not re.search(r"(?m)^infobase\s*:", localtext):
			print("  • v8project.local.yaml: привязка базы не объявлена — unica.run")
			print("    не запустится без infobases.origin.connection")
	dirty = []
	if apply:
		for path in root.rglob("*"):
			rel = path.relative_to(root)
			if path.is_file() and ".git/" not in str(rel) and path.suffix in (
					".md", ".sh", ".py", ".json", ".yaml", ".yml", ".txt", ".ts"):
				# .zcode/ — вендоренный слой контура: канон конвертации сам
				# упоминает Vanessa/yaxunit; секция AGENTS.md — тоже новый контур.
				if rel.parts[0] in (".git", ".zcode"):
					continue
				try:
					text = path.read_text(encoding="utf-8", errors="ignore")
				except OSError:
					continue
				if rel == Path("AGENTS.md"):
					text = re.sub(re.escape(SECTION_BEGIN) + r"[\s\S]*?"
					              + re.escape(SECTION_END), "", text)
				for line in text.splitlines():
					if CLEAN_GREP.search(line):
						dirty.append(f"{rel}: {line.strip()[:100]}")
						break
		if dirty:
			print("  ⚠ остаточные упоминания старого контура (дочисти руками/агентом):")
			for entry in dirty[:30]:
				print(f"    - {entry}")
		else:
			print("  ✓ упоминаний .omp / omp plugin / unica-gate.ts нет")
	else:
		note("после применения: grep-проверка остаточных упоминаний старого контура")

	print()
	# пустые каталоги после переноса/сноса — убрать, чтобы не числились legacy
	for empty in ("features", "fixtures/шаблоны-фич", "fixtures", "tools"):
		p = root / empty
		if p.is_dir() and not any(p.iterdir()) and apply:
			p.rmdir()
			DONE.append(f"rmdir {empty}/")
	legacy = [m for m in LEGACY_MARKERS if (root / m).is_dir() and any((root / m).iterdir())]
	if legacy:
		print(f"Legacy-артефакты старого тест-стека (не удаляются, вне нового контура): {', '.join(legacy)}")
		print("  Vanessa-активы конвертируются в tests/e2e (скилл 1c-test-contour), "
		      "затем удаляются из docs/omp-migrated/tests/.")
	print()
	if not apply:
		print("DRY-RUN завершён. Применить: --apply")
		return 0
	if FAILED:
		print(f"МИГРАЦИЯ С ЗАМЕЧАНИЯМИ: {len(FAILED)} ошибок")
		for f in FAILED:
			print(f"  - {f}")
		return 1
	print(f"МИГРАЦИЯ ЗАВЕРШЕНА: {len(DONE)} изменений. Проверь: /1c-doctor, затем git-коммит.")
	return 0


if __name__ == "__main__":
	sys.exit(main())
