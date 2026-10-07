"""選取句子問 AI（2026-10-05，DANNY：選某句話問 AI，要直接帶進輸入框，不要自己複製貼上）。

行為（整理選取文字、按鈕位置、附掛／拆除、引用格式）由 tests/js/selection_ask.mjs 在 node 實跑，這裡包成 pytest 並守接線。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
def test_node_behaviour():
    out = subprocess.run(
        ["node", "tests/js/selection_ask.mjs"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert out.returncode == 0, (out.stderr or out.stdout)[-3000:]
    assert "selection_ask: ok" in out.stderr


def test_wiring():
    friends = (REPO / "web/js/friends.js").read_text(encoding="utf-8")
    assert "import { attachSelectionAsk } from './selection-ask.js';" in friends
    assert "this.initSelectionAsk();" in friends
    # 在聊天訊息氣泡裡選字；AI 助理沒開（assistantEnabled）就不浮按鈕
    assert "eligible: '.msg-bubble'" in friends and "enabled: () => assistantEnabled()" in friends
    # 選取的句子一路傳到抽屜（兩種聊天室：私訊與群組）
    assert friends.count("                quote,\n") == 2

    assistant = (REPO / "web/js/chat-assistant.js").read_text(encoding="utf-8")
    assert "import { attachSelectionAsk } from './selection-ask.js';" in assistant
    assert "quoteForInput(ctx.quote)" in assistant
    # AI 回答裡選一句話追問；抽屜關掉要拆掉監聽
    assert "eligible: '.assistant-answer'" in assistant and "detachSelection();" in assistant


def test_menu_ask_ai_carries_the_message_text():
    """觸控裝置的氣泡不能選字（長按是選單），選單「問 AI」要把那則訊息的原文帶進輸入框（2026-10-06）。"""
    friends = (REPO / "web/js/friends.js").read_text(encoding="utf-8")
    groups = (REPO / "web/js/social-groups.js").read_text(encoding="utf-8")
    assert "askAi: ({ id, text }) => this.openAssistant(id, text)," in friends
    assert "askAi: ({ id, text }) => this.openAssistant(id, text)," in groups
    # 選單 handler 拿到的 info.text 來自氣泡原文（dm-message-actions.js rowInfo）
    actions = (REPO / "web/js/dm-message-actions.js").read_text(encoding="utf-8")
    assert "text: messagePlainText(row.querySelector('.msg-text'))," in actions


def test_chat_pane_can_shrink_below_unbreakable_content():
    """長網址／錢包地址／程式碼／寬表格不能把聊天欄撐出手機畫面（2026-10-06 DANNY 截圖：版面被切）。

    flex-1 預設 min-width:auto，子孫有不斷行內容時整欄跟著變寬；兩層都要 min-w-0。
    """
    tab = (REPO / "web/js/components/tab-friends.js").read_text(encoding="utf-8")
    for pane in ("social-chat-section", "social-chat-content"):
        line = next(ln for ln in tab.splitlines() if f'id="{pane}"' in ln)
        assert "min-w-0" in line.split("class=")[1], pane
