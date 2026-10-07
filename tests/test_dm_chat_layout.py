"""私訊聊天室 LINE 式版面的守衛（2026-09-29）。

分組規則（同一人同一分鐘只標一次時間、換日插日期分隔）、兩支氣泡渲染的列結構
（寬度上限掛在氣泡本身、時間不換行）、字數快到上限才顯示——由
tests/js/dm_chat_layout.mjs（node 實跑）看守，這裡包成 pytest（獨立的 messages.html 已於 2026-10-05
併入好友頁，頁面版面的守衛隨它退場）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def test_dm_chat_layout_node_gate():
    try:
        proc = subprocess.run(
            ["node", str(REPO / "tests" / "js" / "dm_chat_layout.mjs")],
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
    assert "dm_chat_layout: ok" in proc.stderr
