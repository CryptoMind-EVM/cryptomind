"""i18n.js 只下載用得到的語言檔、登入事件只查一次 /api/user/me（2026-09-26 量測）。

以前一開頁就抓四種語言（壓縮後約 230 KB，/static/js 又是 no-store，每頁都重抓），
一頁還會因為 auth:ready ×2 ＋ auth:initialized 打 3 次 /api/user/me 只為了拿語言偏好。
行為測試在 tests/js/i18n_lazy_load.mjs（node 實跑 i18n.js，stub fetch／AppAPI 數次數），
這裡包成 pytest 閘門。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def test_i18n_loads_only_needed_languages_and_checks_server_language_once():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "i18n_lazy_load.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "i18n lazy-load tests passed" in proc.stdout
