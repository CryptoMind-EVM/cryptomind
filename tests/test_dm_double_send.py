"""私訊「送一次、出現兩則」的守衛（2026-09-29）。

私訊聊天室（好友頁的 SocialHub）Enter 沒擋輸入法選字，
送出中也只 disable 按鈕、Enter 路徑照送。行為由 tests/js/dm_double_send.mjs
（node 實跑）看守，這裡包成 pytest，免得那支 .mjs 沒人跑。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def test_dm_enter_does_not_double_send():
    try:
        proc = subprocess.run(
            ["node", str(REPO / "tests" / "js" / "dm_double_send.mjs")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(REPO),
            timeout=60,
            check=False,
        )
    except FileNotFoundError:
        pytest.skip("node 不在 PATH")
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "dm_double_send: ok" in proc.stderr
