"""crontab.txt 引用的每支腳本都要在 .dockerignore 的白名單裡。

cron-worker 共用 app image（root Dockerfile 的 `COPY . .`）帶入排程腳本，而 .dockerignore 把 `scripts/*` 整個排除、
再逐一 `!scripts/xxx.py` 放行。漏放行的腳本不會進 image，cron 到點只會在 Runtime Logs
留一行 can't open file，排程等於沒跑（2026-08-09 #422 漏了兩支；2026-09-13 又漏了
早報／鏈上同步／判斷評分／行事曆同步四支）。這裡直接拿 crontab 對白名單，漏一支就紅。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent


def _crontab_scripts() -> set[str]:
    text = (_ROOT / "crontab.txt").read_text(encoding="utf-8")
    found = set()
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        found.update(re.findall(r"/app/(scripts/[\w.-]+\.py)", line))
    assert found, "crontab.txt 裡沒讀到任何 /app/scripts/*.py"
    return found


def _dockerignore_whitelist() -> set[str]:
    text = (_ROOT / ".dockerignore").read_text(encoding="utf-8")
    return {m for m in re.findall(r"^!(scripts/[\w.-]+\.py)\s*$", text, re.M)}




