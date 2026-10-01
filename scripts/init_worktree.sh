#!/usr/bin/env bash
# init_worktree.sh — инициализация воркспейса Unica и базы в git-ворктри
# (порт 1c-omp skills/1c-project-bootstrap/scripts/init-worktree.sh под
# контур 1c-zcode). Запускать из основного дерева или из ворктри.
#
# Использование:
#   init_worktree.sh /path/to/worktree
#   init_worktree.sh /path/to/worktree --empty-ib     # файловая база: не копировать, создать пустую
#   init_worktree.sh /path/to/worktree --test-contour # пометить дерево тестовым (.zcode/contour):
#                                                     # изменяющие инструменты тест-контура не блокируются
#
# Что делает:
#   1. Копирует v8project.local.yaml (креды ИБ, путь к платформе)
#   2. Копирует build/tools/ (артефакты инструментов v8-runner)
#   3. Разбирается с базой (см. «База» ниже)
#   4. Копирует build/hash-storages/ — только если состояние базы совпадает
#      с основным деревом (иначе инкрементальное состояние невалидно)
#   5. Legacy Vanessa-артефакты (tools/VAParams.*) — копирует, если есть,
#      с предупреждением: старый тест-стек выведен из контура
#   6. Проверяет результат
#
# НЕ делает (в отличие от канона omp): не ставит плагины — контур
# вендорится в git (.zcode/, deploy-журнал), в ворктри попадает checkout'ом;
# проверка .zcode/config.json — шаг 6.
#
# База:
#   File=<относительный путь> — у каждого дерева своя файловая база.
#       Нет базы: без флага — копия из основного дерева (долго и много места),
#       с --empty-ib — пустая база (годится для сборки из исходников).
#   Srvr=<сервер>;Ref=<база> — база ОДНА на все деревья: изоляции нет и быть
#       не может, поэтому одновременно с базой работает одно дерево — это
#       разводит замок хука unica-source-gate (TTL 20 минут).
#
# ВАЖНО: только реальные копии, НЕ симлинки.

set -euo pipefail

EMPTY_IB=0
TEST_CONTOUR=0
ARGS=()
for arg in "$@"; do
	case "$arg" in
	--empty-ib) EMPTY_IB=1 ;;
	--test-contour) TEST_CONTOUR=1 ;;
	*) ARGS+=("$arg") ;;
	esac
done
WORKTREE="${ARGS[0]:-.}"
WORKTREE="$(cd "$WORKTREE" && pwd)"

# Признак контура для хука mcp-gate: в тестовом дереве изменяющие инструменты
# 1c-testpilot/1c-db не блокируются, в проде — да. Без признака дерево
# считается прод-контуром (fail-closed).
if [[ $TEST_CONTOUR -eq 1 ]]; then
	mkdir -p "$WORKTREE/.zcode"
	printf 'test\n' >"$WORKTREE/.zcode/contour"
	echo "• контур помечен тестовым: .zcode/contour (изменяющие вызовы тест-контура свободны)"
fi

GIT_COMMON_DIR="$(git -C "$WORKTREE" rev-parse --git-common-dir 2>/dev/null || true)"
if [[ -z "$GIT_COMMON_DIR" ]]; then
	echo "✗ не git-репозиторий: $WORKTREE" >&2
	exit 2
fi
GIT_COMMON_DIR="$(cd "$GIT_COMMON_DIR" && pwd)"
MAIN_TREE="$(dirname "$GIT_COMMON_DIR")"

if [[ "$MAIN_TREE" == "$WORKTREE" ]]; then
	echo "✗ это основное дерево, а не ворктри: $WORKTREE" >&2
	exit 2
fi

echo "Основное дерево: $MAIN_TREE"
echo "Ворктри:         $WORKTREE"
echo ""

ERRORS=0

# ── строка подключения к базе: локальный оверлей перекрывает основной файл.
# Формы: 0.12 `infobase.connection` и 0.13 `infobases.origin.connection`.
resolve_connection() {
	python3 - "$1" <<'PY'
import re, sys, pathlib
tree = pathlib.Path(sys.argv[1])
def read(name):
    f = tree / name
    if not f.exists():
        return None
    top, in_origin = None, False
    for line in f.read_text(encoding="utf-8").splitlines():
        if re.match(r"^\S", line):
            top = line.split(":", 1)[0].strip()
            in_origin = False
            continue
        stripped = line.strip()
        if top == "infobases" and stripped.rstrip(":").strip() == "origin":
            in_origin = True
            continue
        m = re.match(r"^connection\s*:\s*(.+?)\s*$", stripped)
        if m and (top == "infobase" or (top == "infobases" and in_origin)):
            return m.group(1).strip().strip("'\"")
    return None
print(read("v8project.local.yaml") or read("v8project.yaml") or "")
PY
}

# 1. v8project.local.yaml
if [[ -f "$MAIN_TREE/v8project.local.yaml" ]]; then
	cp -p "$MAIN_TREE/v8project.local.yaml" "$WORKTREE/v8project.local.yaml"
	echo "✓ v8project.local.yaml"
else
	echo "✗ нет $MAIN_TREE/v8project.local.yaml — креды и платформа не перенесены" >&2
	ERRORS=$((ERRORS + 1))
fi

# 2. build/tools/
mkdir -p "$WORKTREE/build"
if [[ -d "$MAIN_TREE/build/tools" ]]; then
	cp -r "$MAIN_TREE/build/tools" "$WORKTREE/build/tools"
	echo "✓ build/tools/"
else
	echo "✗ нет $MAIN_TREE/build/tools — в основном дереве выполни tools-download" >&2
	ERRORS=$((ERRORS + 1))
fi

# 3. Legacy Vanessa-артефакты: старый тест-стек выведен из контура, но если
# проект ими ещё пользуется — копируем (шаблон предпочтительнее готового
# файла: в готовом абсолютный путь базы основного дерева).
mkdir -p "$WORKTREE/tools"
if [[ -f "$MAIN_TREE/tools/VAParams.template.json" ]]; then
	cp -p "$MAIN_TREE/tools/VAParams.template.json" "$WORKTREE/tools/VAParams.template.json"
	echo "⚠ legacy: tools/VAParams.template.json скопирован (Vanessa вне контура)"
elif [[ -f "$MAIN_TREE/tools/VAParams.json" ]]; then
	cp -p "$MAIN_TREE/tools/VAParams.json" "$WORKTREE/tools/VAParams.json"
	chmod 600 "$WORKTREE/tools/VAParams.json" 2>/dev/null || true
	echo "⚠ legacy: tools/VAParams.json скопирован как есть — проверь ПутьКИнфобазе:" >&2
	echo "  в нём может быть путь базы ОСНОВНОГО дерева" >&2
fi
if [[ -f "$MAIN_TREE/tools/va-env.local.json" && ! -f "$WORKTREE/tools/va-env.local.json" ]]; then
	cp -p "$MAIN_TREE/tools/va-env.local.json" "$WORKTREE/tools/va-env.local.json"
	chmod 600 "$WORKTREE/tools/va-env.local.json" 2>/dev/null || true
	echo "⚠ legacy: tools/va-env.local.json скопирован"
fi

# 4. База
CONNECTION="$(resolve_connection "$WORKTREE")"
COPY_HASHES=1
echo ""
if [[ -z "$CONNECTION" ]]; then
	echo "⚠ база не объявлена ни в v8project.yaml, ни в оверлее"
	COPY_HASHES=0
elif [[ "$CONNECTION" == File=* ]]; then
	REL="${CONNECTION#File=}"
	IB="$WORKTREE/$REL"
	MAIN_IB="$MAIN_TREE/$REL"
	if [[ -d "$IB" ]]; then
		echo "✓ файловая база на месте: $REL"
	elif [[ $EMPTY_IB -eq 1 ]]; then
		mkdir -p "$IB"
		echo "• файловая база создана пустой: $REL (данных нет)"
		COPY_HASHES=0
	elif [[ -d "$MAIN_IB" ]]; then
		echo "• копирую файловую базу из основного дерева: $REL ($(du -sh "$MAIN_IB" 2>/dev/null | cut -f1))…"
		# Толерантная копия: в базе бывают 0-байтовые заглушки чужого UID
		# (1CHelpIndex в distrobox) — cp -a на них рвёт set -e. Копируем
		# по-файлово, нечитаемое пропускаем с предупреждением (это
		# регенерируемые индексы платформы, базе они не нужны).
		mkdir -p "$IB"
		if cp -a "$MAIN_IB/." "$IB/" 2>/dev/null; then
			:
		else
			SKIPPED=0
			while IFS= read -r -d '' src; do
				rel="${src#"$MAIN_IB"/}"
				dest="$IB/$rel"
				if [[ -d "$src" ]]; then
					mkdir -p "$dest"
				elif cp -p "$src" "$dest" 2>/dev/null; then
					:
				else
					SKIPPED=$((SKIPPED + 1))
					[[ $SKIPPED -le 5 ]] && echo "  ⚠ пропущено (не читается): $rel" >&2
				fi
			done < <(find "$MAIN_IB" -print0)
			[[ $SKIPPED -gt 5 ]] && echo "  ⚠ …и ещё $((SKIPPED - 5)) файлов пропущено" >&2
			[[ $SKIPPED -gt 0 ]] && echo "• пропущено нечитаемых: $SKIPPED (обычно 1CHelpIndex — регенерируется)"
		fi
		echo "✓ база скопирована"
	else
		echo "✗ файловой базы нет ни в ворктри, ни в основном дереве: $REL" >&2
		echo "  подними пустую: $0 $WORKTREE --empty-ib" >&2
		ERRORS=$((ERRORS + 1))
		COPY_HASHES=0
	fi
else
	echo "⚠ база серверная и общая для всех деревьев: $CONNECTION"
	echo "  изоляции здесь нет и быть не может — одновременные операции разводит"
	echo "  замок хука unica-source-gate (TTL 20 минут)."
fi

# 5. build/hash-storages/ — инкрементальное состояние сборки
if [[ $COPY_HASHES -eq 1 && -d "$MAIN_TREE/build/hash-storages" ]]; then
	cp -r "$MAIN_TREE/build/hash-storages" "$WORKTREE/build/hash-storages"
	echo "✓ build/hash-storages/"
elif [[ $COPY_HASHES -eq 0 ]]; then
	echo "• build/hash-storages/ не копирую: база не совпадает с основным деревом"
fi

# 6. Проверка
echo ""
echo "=== Проверка воркспейса ==="
MISSING=0
REQUIRED=(v8project.local.yaml v8project.yaml .zcode/config.json .zcode/testpilot/profiles.yaml)
for f in "${REQUIRED[@]}"; do
	if [[ -f "$WORKTREE/$f" ]]; then
		echo "✓ $f"
	else
		echo "✗ $f" >&2
		MISSING=$((MISSING + 1))
	fi
done
for d in build/tools; do
	if [[ -d "$WORKTREE/$d" ]]; then
		echo "✓ $d/"
	else
		echo "✗ $d/" >&2
		MISSING=$((MISSING + 1))
	fi
done

if [[ $MISSING -gt 0 || $ERRORS -gt 0 ]]; then
	echo ""
	echo "Не хватает: $MISSING, ошибок: $ERRORS" >&2
	exit 1
fi

echo ""
echo "Воркспейс готов. Проверь: unica.project.status { \"cwd\": \"$WORKTREE\" }"
echo "Контур самодостаточен (вендорен в git); если .zcode/config.json правился"
echo "под эту машину — прогони: python3 .zcode/1c/scripts/wire_config.py \"$WORKTREE\""
if [[ "$CONNECTION" == Srvr=* ]]; then
	echo "База общая: перед операциями убедись, что в основном дереве никто не грузит."
fi
