"""私訊在窄桌機視窗的版面（DANNY 2026-10-01）。

以前 768 以上就「對話清單＋聊天」並排：860 寬時網站側欄 288＋清單 320，聊天欄只剩 218px
（好友頁 284px），長訊息五六個字就換行。改成 1024 以上才並排，以下跟手機一樣一次一欄：
外框 data-pane="list|chat" 由 JS 標，顯示哪一欄交給 CSS（視窗寬度跨過 1024 也不會卡住），
聊天欄上方有「返回對話列表」（手機版以前也沒有這顆）。實際寬度在 Playwright 量。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _tag(src: str, element_id: str) -> str:
    m = re.search(r'<[a-z]+[^>]*\bid="%s"[^>]*>' % re.escape(element_id), src)
    assert m, element_id
    return m.group(0)


@pytest.mark.parametrize(
    "path, panes, sidebar, chat, header, back_action",
    [
        ("web/js/components/tab-friends.js", "social-content-messages", "social-conv-sidebar", "social-chat-section", "social-chat-header", "socialBackToList"),
    ],
)
def test_two_panes_only_from_1024(path, panes, sidebar, chat, header, back_action):
    src = _read(path)
    assert 'data-dm-panes' in _tag(src, panes) and 'data-pane="list"' in _tag(src, panes)
    side = _tag(src, sidebar)
    assert "data-dm-list" in side and "w-full" in side and " md:w-" not in side, "1024 以下清單佔滿一欄"
    sec = _tag(src, chat)
    assert "data-dm-chat" in sec and "lg:flex" in sec and "md:flex" not in sec, "1024 以上才並排"
    head = src[src.index(f'id="{header}"') :]
    head = head[: head.index("</div>")]
    assert f'data-click="{back_action}"' in head and "data-dm-back" in head, "聊天欄上方要有返回對話列表"


def test_css_decides_which_pane_shows():
    css = _read("web/styles.css")
    narrow = css[css.index("@media (max-width: 1023.98px)") :][:900]
    assert '[data-pane="chat"] [data-dm-list]' in narrow and '[data-pane="list"] [data-dm-chat]' in narrow
    wide = css[css.index("[data-dm-panes] [data-dm-chat]") - 200 :][:600]
    assert "min-width: 1024px" in wide and "[data-dm-back]" in wide


def test_back_actions_are_delegated():
    src = _read("web/js/click-delegator.js")
    assert "action === 'socialBackToList'" in src and "SocialHub.backToList()" in src
    assert "MessagesPage" not in src, "獨立私訊頁已移除，click-delegator 不該再留它的動作"
    social = _read("web/js/friends.js")
    assert "this.setPane('chat')" in social and "this.setPane('list')" in social and "backToList: function" in social
    assert "dataset.pane = pane" in social


def test_conv_list_collapses_when_wide_and_chatting():
    """DANNY 2026-10-01：「不用的時候可以扁平化放大聊天視窗」。並排時聊天標題列的按鈕把對話清單收起來；
    只在聊天中收（沒開對話時收起來就剩空白「選一個對話」），1024 以下有返回鈕就不顯示這顆。"""
    src = _read("web/js/components/tab-friends.js")
    head = src[src.index('id="social-chat-header"') :]
    head = head[: head.index("</div>")]
    assert 'data-click="SocialHub.toggleConvList"' in head and "data-dm-list-toggle" in head
    css = _read("web/styles.css")
    narrow = css[css.index("@media (max-width: 1023.98px)") :][:900]
    assert "[data-dm-list-toggle]" in narrow, "一次一欄時不顯示收合鈕"
    wide = css[css.index("[data-dm-panes] [data-dm-chat]") - 200 :][:900]
    assert '[data-list-collapsed="1"][data-pane="chat"] [data-dm-list]' in wide, "只在聊天中、並排時收"
    social = _read("web/js/friends.js")
    assert "toggleConvList: function" in social and "'dmListCollapsed'" in social
