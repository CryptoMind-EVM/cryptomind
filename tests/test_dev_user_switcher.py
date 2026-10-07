"""TEST_MODE 設定頁的「切換帳號」區塊要看得到、按得動（2026-09-29）。

兩層靜默失效疊在一起：
1. auth.js 的 handleDevSwitchUser 只 export 沒掛 window，click-delegator 找不到
   （由 test_click_delegator_globals 守）。
2. #dev-user-switcher 只在 auth 初始化時取消 hidden，但設定頁是切過去才注入，
   當下元素不存在 → 區塊永遠藏著。改由進設定頁會跑的 initTestMode 同步，
   前端行為在 tests/js/test_mode_dev_switcher.mjs 實跑。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def test_init_test_mode_syncs_dev_user_switcher_node_gate():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "test_mode_dev_switcher.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "test_mode_dev_switcher: ok" in proc.stderr
