"""AuthManager.init() 每次頁面載入只跑一次（2026-09-25 盤查）。

api-client 的 auth gate 讓每支 AppAPI 請求都 await AuthManager.init()；init 原本
完成後就把 _initPromise 清掉，於是每支請求都重打 /api/user/me＋/api/user/refresh、
重發 auth:initialized。行為測試在 tests/js/auth_init_once.mjs（node 實跑
api-client.js＋auth.js＋ton-auth.js，stub fetch 數呼叫次數），這裡包成 pytest 閘門。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def test_auth_init_runs_once_per_page_load():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "auth_init_once.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "auth_init_once: ok" in proc.stderr
