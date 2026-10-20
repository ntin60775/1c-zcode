#!/usr/bin/env python3
"""Резолв обработки 1c-db по ОС хоста (v0.4.51, ROCTUP/1c-mcp-toolkit#34).

Раннер start_1c_db.sh обязан выбирать файл epf под ОС (Linux →
MCP_Toolkit_linux.epf и т. д.): раньше всегда брался вин-файл
MCP_Toolkit.epf — на Linux его Windows-компоненты (QueryLineageAnalyzer)
молча отсутствовали. Явный 1c_db.epf_path перекрывает детект.
Проверяется через --print-config (резолв без подъёма канала)."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib

SCRIPT = REPO / "scripts" / "start_1c_db.sh"


def expected_os_epf() -> str:
    if sys.platform.startswith("linux"):
        return "tools/mcp/MCP_Toolkit_linux.epf"
    if sys.platform == "darwin":
        return "tools/mcp/MCP_Toolkit_macos.epf"
    return "tools/mcp/MCP_Toolkit.epf"


def print_config(proj: Path):
    return subprocess.run(
        ["bash", str(SCRIPT), str(proj), "--print-config"],
        capture_output=True, text=True, cwd=str(tmp),
        env=dict(os.environ),
    )


def write_cfg(proj: Path, cfg: str):
    d = proj / ".zcode" / "1c"
    d.mkdir(parents=True, exist_ok=True)
    (d / "contour.json").write_text(cfg, encoding="utf-8")


tmp = Path(tempfile.mkdtemp(prefix="start-1c-db-cfg-"))
try:
    # 1. без 1c_db-секции — детект по ОС хоста
    proj = tmp / "plain"
    write_cfg(proj, '{"1c": {}}')
    r = print_config(proj)
    fields = r.stdout.split()
    _lib.check(
        "без epf_path резолв идёт в файл текущей ОС + mode по ОС",
        r.returncode == 0 and len(fields) in (4, 5)
        and fields[2] == expected_os_epf()
        and fields[3] == ("proxy" if sys.platform.startswith("linux") else "embedded"),
        f"rc={r.returncode}, stdout={r.stdout!r}, stderr={r.stderr[:200]!r}",
    )
    _lib.check(
        "--print-config: url/порт дефолтные",
        fields[0] == "http://127.0.0.1:6003/mcp" and fields[1] == "6003",
        f"fields={fields}",
    )

    # 2. явный epf_path перекрывает детект (том же ОС-логики не трогает)
    proj = tmp / "explicit"
    write_cfg(proj, '{"1c_db": {"epf_path": "tools/mcp/Custom.epf"}}')
    fields = print_config(proj).stdout.split()
    _lib.check(
        "явный 1c_db.epf_path берётся как есть",
        fields[2] == "tools/mcp/Custom.epf",
        f"fields={fields}",
    )

    # 3. пустой epf_path — не «файл с пустым именем», а детект
    proj = tmp / "empty"
    write_cfg(proj, '{"1c_db": {"epf_path": ""}}')
    fields = print_config(proj).stdout.split()
    _lib.check(
        "пустой epf_path ведёт себя как незаданный (детект по ОС)",
        fields[2] == expected_os_epf(),
        f"fields={fields}",
    )

    # 4. url с кастомным портом и channel доезжают до запуска прокси
    proj = tmp / "port"
    write_cfg(proj, '{"1c_db": {"url": "http://127.0.0.1:6005/mcp", "channel": "t1"}}')
    fields = print_config(proj).stdout.split()
    _lib.check(
        "порт из url и channel пробрасываются в скрипт",
        fields[1] == "6005" and fields[4] == "t1",
        f"fields={fields}",
    )
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
sys.exit(_lib.summary())
