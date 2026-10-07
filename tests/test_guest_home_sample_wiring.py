"""訪客首頁＋Sample portfolio 的接線（2026-09-27 上市準備 PR-2／PR-3）。

design：docs/plans/2026-09-27-launch-readiness-design.md §3、§4
行為（node 實跑）：tests/js/guest_nav.mjs、sample_portfolio.mjs、stablecoin_filter.mjs、
guest_home.mjs——這裡包起來並鎖住分散在各檔的接線，漏一處就是「點了沒反應」而且沒有錯誤。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _node(rel: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", rel], cwd=ROOT, capture_output=True, text=True, timeout=60
    )


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")


@needs_node
@pytest.mark.parametrize(
    "script",
    [
        "tests/js/guest_nav.mjs",
        "tests/js/sample_portfolio.mjs",
        "tests/js/stablecoin_filter.mjs",
        "tests/js/guest_home.mjs",
        "tests/js/chat_stick_release.mjs",
        "tests/js/shell_stick_release.mjs",
    ],
)
def test_node_behaviour(script):
    out = _node(script)
    assert out.returncode == 0, (out.stderr or out.stdout)[-3000:]


class TestSampleTabWiring:
    def test_registered_in_all_four_places(self):
        assert 'id="sample-tab"' in _read("web/index.html")
        spa = _read("web/js/spa.js")
        valid = re.search(r"var VALID_TABS = \[(.*?)\];", spa, re.S)
        assert valid and "'sample'" in valid.group(1)
        modules = re.search(r"const _TAB_MODULES = \{(.*?)\n\};", spa, re.S)
        assert modules and re.search(r"\n    sample:", modules.group(1))
        assert "import('./sample-portfolio.js')" in modules.group(1)
        assert re.search(r"id:\s*'sample'", _read("web/js/nav-config.js"))
        assert "window.Components.sample" in _read("web/js/components/tab-sample.js")

    def test_tab_switch_runs_init(self):
        spa = _read("web/js/spa.js")
        assert "tabId === 'sample' && window.SampleTab" in spa

    def test_logged_in_users_are_sent_to_journal(self):
        spa = _read("web/js/spa.js")
        body = spa[
            spa.index("async function switchTab(") : spa.index(
                "window.switchTab = switchTab;"
            )
        ]
        assert "guestOnly" in body
        assert "'journal'" in body

    def test_guest_gate_lets_sample_through_even_when_data_flag_is_off(self):
        spa = _read("web/js/spa.js")
        body = spa[
            spa.index("async function switchTab(") : spa.index(
                "window.switchTab = switchTab;"
            )
        ]
        assert "navItem?.guestOnly === true" in body

    def test_sample_module_is_static_only(self):
        """示範資料是前端 fixture：不打任何 API（尤其要登入的帳本 API）。"""
        for rel in ("web/js/sample-portfolio.js", "web/js/components/tab-sample.js"):
            src = _read(rel)
            for banned in ("fetch(", "AppAPI", "/api/", "XMLHttpRequest", "WebSocket"):
                assert banned not in src, f"{rel} 出現 {banned}"

    def test_no_advice_wording(self):
        """合規定位：分析工具不是投顧——示範頁不能出現買賣建議或「訊號」。"""
        import json

        en = json.loads(_read("web/js/i18n/en.json"))
        blob = json.dumps(en["sample"]).lower() + json.dumps(en["guestHome"]).lower()
        for word in ("buy ", "sell ", "signal", "recommend", "should "):
            assert word not in blob, word


class TestGuestHome:
    def test_welcome_screen_branches_to_guest_hero_first(self):
        src = _read("web/js/chat-sessions.js")
        body = src[src.index("async function showWelcomeScreen") :]
        guest = body.index("renderGuestWelcome(container)")
        assert guest < body.index("shouldShowApiKeyBanner("), (
            "訪客分支要在 API key 卡判斷之前 return"
        )
        assert "import { renderGuestWelcome } from './guest-home.js';" in src
        # 切語言要重畫 hero：hero 沒有 .welcome-title，監聽器要認 #guest-home
        listener = src[src.index("window.addEventListener('languageChanged'") :]
        listener = listener[: listener.index("});")]
        assert "#guest-home" in listener

    def test_delegated_actions_exist(self):
        src = _read("web/js/click-delegator.js")
        assert "action === 'focusChatInput'" in src
        assert "action === 'openLoginModal'" in src

    def test_hero_uses_delegation_not_inline_handlers(self):
        for rel in (
            "web/js/guest-home.js",
            "web/js/sample-portfolio.js",
            "web/js/components/tab-sample.js",
        ):
            src = _read(rel)
            assert not re.search(r"\son[a-z]+\s*=\s*[\"']", src), (
                f"{rel} 有 inline handler（CSP 會擋）"
            )

    def test_forum_hidden_from_guests(self):
        src = _read("web/js/nav-config.js")
        block = src.split("id: 'forum',", 1)[1].split("},", 1)[0]
        assert "guestAllowed: true" not in block


class TestGuestMenus:
    def test_spa_sidebar_uses_guest_items(self):
        src = _read("web/js/global-nav.js")
        body = src[
            src.index("renderSidebarNav() {") : src.index("toggleSidebarNavMore()")
        ]
        assert "getGuestItems()" in body
        pop = src[
            src.index("renderSidebarNavPopover() {") : src.index("_navLimitMessage(")
        ]
        assert "getGuestMoreItems()" in pop

    def test_standalone_sidebar_uses_guest_items(self):
        src = _read("web/js/site-sidebar.js")
        body = src[src.index("function renderMenu()") :]
        body = body[: body.index("/* ---------- footer")]
        assert "getGuestItems()" in body
