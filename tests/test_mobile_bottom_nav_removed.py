"""手機底部導覽列拔除（DANNY 2026-09-24）。

側邊欄（手機是 ☰ 抽屜）的「功能選單」已列出全部功能，底部列只是重複的捷徑，
卻是版面 bug 的最大來源（貼底量測、安全區、拖拉、標籤換行）。拔除後：

- index.html／論壇頁都不再有底部列；GlobalNav 不再注入 pill
- 舊入口 renderNavButtons 保留名稱、改為重畫側欄選單（FeatureMenu 存檔、登入、
  切語言等呼叫端不用改，順帶修掉「自訂後側欄不更新」）
- 手機抽屜與桌機一樣列出全部啟用項（不再 slice 5），NavPreferences 取消 5 個上限
- 在非聊天頁打開抽屜直接顯示「功能選單」（聊天頁仍是「對話歷史」）
- iPhone 底部安全區原本只由底部列墊，改由輸入列／各分頁自己墊 --mobile-safe-bottom
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _node(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", *args], cwd=ROOT, capture_output=True, text=True, timeout=60
    )


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")


class TestMarkupGone:
    def test_index_has_no_bottom_bar(self):
        html = _read("web/index.html")
        for marker in (
            'id="global-nav-container"',
            'id="nav-buttons"',
            'id="draggable-nav"',
        ):
            assert marker not in html, marker

    def test_global_nav_no_longer_injects_pill(self):
        src = _read("web/js/global-nav.js")
        assert "getNavTemplate" not in src
        assert "injectNav" not in src

    def test_render_nav_buttons_redraws_sidebar(self):
        src = _read("web/js/global-nav.js")
        body = src[src.index("renderNavButtons() {") :]
        body = body[: body.index("},")]
        assert "this.renderSidebarNav()" in body


class TestMenuShowsEverything:
    def test_spa_sidebar_does_not_cap_by_width(self):
        src = _read("web/js/global-nav.js")
        body = src[src.index("renderSidebarNav() {") :]
        body = body[: body.index("toggleSidebarNavMore()")]
        assert ".slice(" not in body
        assert "innerWidth >= 768" not in body

    def test_forum_drawer_does_not_cap(self):
        src = _read("web/js/site-sidebar.js")
        body = src[src.index("function renderMenu()") :]
        body = body[: body.index("items.forEach")]
        assert ".slice(0, 5)" not in body

    @needs_node
    def test_preferences_min_max_limits(self):
        # 上限 6（含固定項）、下限 3；固定項關不掉要回報 locked 而不是 min
        out = _node(["tests/js/nav_limits.mjs"])
        assert out.returncode == 0, out.stderr or out.stdout

    @needs_node
    def test_hidden_tabs_contract_still_holds(self):
        # 這支 .mjs 先前沒有 pytest 包裝、從沒被跑過——一併接上
        out = _node(["tests/js/nav_hidden_tabs.mjs"])
        assert out.returncode == 0, out.stderr or out.stdout


class TestDrawerOpensOnMenu:
    CASES = [
        ("chat", "history"),
        ("", "history"),
        ("crypto", "menu"),
        ("journal", "menu"),
    ]

    @needs_node
    def test_tab_choice_runs_in_node(self):
        src = _read("web/js/chat-state.js")
        m = re.search(r"^function sidebarTabOnOpen\(.*?^\}", src, re.M | re.S)
        assert m, "sidebarTabOnOpen 不存在"
        script = (
            m.group(0)
            + "\nconsole.log(JSON.stringify("
            + json.dumps([c[0] for c in self.CASES])
            + ".map(sidebarTabOnOpen)));"
        )
        out = _node(["-e", script])
        assert out.returncode == 0, out.stderr
        assert json.loads(out.stdout) == [c[1] for c in self.CASES]

    def test_open_sidebar_applies_it_on_mobile(self):
        src = _read("web/js/chat-state.js")
        body = src[src.index("function openSidebar()") :]
        body = body[: body.index("function closeSidebar()")]
        assert "sidebarTabOnOpen(" in body
        assert "innerWidth < 768" in body


class TestSafeAreaMovedOffTheBar:
    def test_css_has_no_bar_rules_or_nav_height(self):
        css = _read("web/styles.css")
        assert "#global-nav-container" not in css
        assert "--shell-fixed-nav-height" not in css
        assert "--mobile-nav-clearance" not in css

    def test_chat_input_pads_safe_area(self):
        css = _read("web/styles.css")
        block = css[css.index("[data-shell-fixed-input] {") :]
        block = block[: block.index("}")]
        assert "var(--mobile-safe-bottom)" in block

    def test_other_tabs_pad_safe_area(self):
        css = _read("web/styles.css")
        m = re.search(r"\.tab-content:not\(#chat-tab\)\s*\{([^}]*)\}", css)
        assert m and "var(--mobile-safe-bottom)" in m.group(1)

    def test_ui_shell_stops_measuring_a_nav(self):
        src = _read("web/js/ui-shell.js")
        assert "data-shell-fixed-nav" not in src
        assert "--shell-fixed-nav-height" not in src
