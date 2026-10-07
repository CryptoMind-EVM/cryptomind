"""私訊訊息選單（2026-09-29，PR 2）。

以前每則氣泡旁常駐收回／垃圾桶 hover 按鈕：手機沒有 hover 等於藏起來，桌機一排又亂。
改成單一入口（手機長按、桌機「⋯」／右鍵）。規則與位置計算在 tests/js/dm_actions.mjs 實跑。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("path", ["web/js/messages.js", "web/js/friends.js"])
def test_hover_tools_sit_outside_the_bubble(path):
    """工具列放在列上、氣泡與時間之間（不在氣泡裡），hover 時接在時間的位置；桌機氣泡留出工具列寬度"""
    src = (REPO / path).read_text(encoding="utf-8")
    start = min(i for i in (src.find("renderMessageBubble(msg, isPro"), src.find("renderMessageBubble: function")) if i >= 0)
    body = src[start:]
    body = body[: body.index("\n    },")]
    assert "${content}${tools}" not in body and "</span>${tools}" not in body, "工具列不能放在氣泡裡（會蓋到相鄰訊息）"
    assert body.count("${tools}") == 2, "一般訊息自己／對方都要有"
    assert "tools + " in body and " + tools" in body, "已收回的（只剩⋯）也要有"
    css = (REPO / "web" / "styles.css").read_text(encoding="utf-8")
    hover_block = css[css.index("@media (hover: hover)") :]
    assert "calc(100% - " in hover_block[: hover_block.index("}") + 200]


def test_menu_rules_node_gate():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "dm_actions.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]


def test_report_dialog_block_choice_node_gate():
    """檢舉對話框：送出的 block 就是畫面上的勾選狀態；勾選框不能是滿寬的列（手機點空白會默默勾上）"""
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "dm_report_dialog.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "dm_report_dialog: ok" in proc.stdout


def test_reaction_pills_leave_room_below_the_row():
    """表情膠囊絕對定位掛在氣泡下緣外（不撐大氣泡）：那一列底下要留位置，不然蓋到下一則"""
    css = (REPO / "web" / "styles.css").read_text(encoding="utf-8")
    assert ".msg-row:has(.msg-reactions) { padding-bottom:" in css
