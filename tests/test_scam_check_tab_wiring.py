"""詐騙檢查改成 SPA 分頁 #scamcheck（2026-09-27）。

plan：docs/plans/2026-09-27-scam-check-tab-impl.md
行為（node 實跑）：tests/js/scam_check.mjs（判定對應、地址驗證、escape）、guest_nav.mjs（訪客選單）。
這裡鎖住分散在各檔的接線（漏一處就是「點了沒反應」而且沒有錯誤）、舊網址導向、
四語文案與合規用字（判定文案不能出現 safe／安全）。
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
I18N = ROOT / "web" / "js" / "i18n"
LOCALES = ("en", "zh-TW", "zh-CN", "ru")
EVM = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _catalog(lang: str) -> dict:
    return json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))


def _flat(tree: dict, prefix: str = "") -> dict:
    out = {}
    for key, value in tree.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flat(value, path))
        else:
            out[path] = value
    return out


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")


@needs_node
@pytest.mark.parametrize("script", ["tests/js/scam_check.mjs", "tests/js/guest_nav.mjs"])
def test_node_behaviour(script):
    out = subprocess.run(["node", script], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, (out.stderr or out.stdout)[-3000:]


class TestTabWiring:
    def test_registered_in_all_four_places(self):
        assert 'id="scamcheck-tab"' in _read("web/index.html")
        spa = _read("web/js/spa.js")
        valid = re.search(r"var VALID_TABS = \[(.*?)\];", spa, re.S)
        assert valid and "'scamcheck'" in valid.group(1)
        modules = re.search(r"const _TAB_MODULES = \{(.*?)\n\};", spa, re.S)
        assert modules and re.search(r"\n    scamcheck:", modules.group(1))
        assert "import('./scam-check.js')" in modules.group(1)
        assert "import('./components/tab-scamcheck.js')" in modules.group(1)
        assert re.search(r"id:\s*'scamcheck'", _read("web/js/nav-config.js"))
        assert "window.Components.scamcheck" in _read("web/js/components/tab-scamcheck.js")

    def test_tab_switch_runs_init(self):
        assert "tabId === 'scamcheck' && window.ScamCheckTab" in _read("web/js/spa.js")

    def test_tab_rerenders_itself_on_language_change(self):
        """登入用戶切語言時 spa.js 不重跑本分頁（列在 SELF_HANDLED），由分頁自己監聽重畫。"""
        spa = _read("web/js/spa.js")
        handled = re.search(r"const SELF_HANDLED = new Set\(\[(.*?)\]\);", spa, re.S)
        assert handled and "'scamcheck'" in handled.group(1)
        assert "addEventListener('languageChanged'" in _read("web/js/scam-check.js")

    def test_guest_gate_lets_scamcheck_through_even_when_data_flag_is_off(self):
        """公開查詢不是市場數據：GUEST_DATA_ACCESS 關掉時訪客仍然進得去（以前是獨立頁，一直公開）。"""
        spa = _read("web/js/spa.js")
        body = spa[spa.index("async function switchTab(") : spa.index("window.switchTab = switchTab;")]
        assert "navItem?.guestAlways === true" in body
        nav = _read("web/js/global-nav.js")
        allowed = nav[nav.index("_isGuestAllowed(item) {") :]
        allowed = allowed[: allowed.index("\n    },")]
        assert "item.guestAlways === true" in allowed

    def test_no_external_scam_link_left_in_guest_menus(self):
        for rel in ("web/js/nav-config.js", "web/js/global-nav.js", "web/js/site-sidebar.js", "web/index.html"):
            src = _read(rel)
            assert "/scam-tracker/'" not in src and 'href="/scam-tracker/"' not in src, rel
        assert "GUEST_NAV_LINKS" not in _read("web/js/nav-config.js")

    def test_guest_banner_switches_to_the_tab(self):
        html = _read("web/index.html")
        banner = html[html.index('id="guest-banner"') :]
        banner = banner[: banner.index("</div>")]
        assert 'data-click="switchTab" data-click-arg="scamcheck"' in banner
        assert 'data-i18n="nav.scamcheck"' in banner

    def test_delegated_actions_are_allowed(self):
        delegator = _read("web/js/click-delegator.js")
        roots = delegator[delegator.index("var allowedRoots = new Set([") :]
        roots = roots[: roots.index("]);")]
        assert "'ScamCheckTab'" in roots
        src = _read("web/js/scam-check.js") + _read("web/js/components/tab-scamcheck.js")
        actions = set(re.findall(r'data-click="ScamCheckTab\.(\w+)"', src))
        assert {"check", "tryExample", "askAI", "report", "retry"} <= actions
        module = _read("web/js/scam-check.js")
        for action in actions:
            assert re.search(rf"\n    (async )?{action}\(", module), f"ScamCheckTab.{action} 不存在"
        assert 'data-enter="ScamCheckTab.submitFromInput"' in _read("web/js/components/tab-scamcheck.js")

    def test_no_inline_handlers(self):
        for rel in ("web/js/scam-check.js", "web/js/components/tab-scamcheck.js"):
            assert not re.search(r"\son[a-z]+\s*=\s*[\"']", _read(rel)), f"{rel} 有 inline handler（CSP 會擋）"

    def test_reads_address_param_and_reports_via_existing_submit_page(self):
        src = _read("web/js/scam-check.js")
        assert "takeAddressParam(window.location.search)" in src
        assert "/static/scam-tracker/submit.html?address=" in src
        assert "openLoginModal" in src or "login-modal" in src


class TestI18n:
    def test_keys_present_in_all_four_locales(self):
        flats = {lang: _flat(_catalog(lang)) for lang in LOCALES}
        keys = {k for k in flats["zh-TW"] if k.startswith("scamcheck.")} | {"nav.scamcheck"}
        assert len(keys) > 20
        for lang in LOCALES:
            missing = sorted(k for k in keys if not flats[lang].get(k))
            assert not missing, f"{lang} 缺 {missing[:5]}"

    def test_every_scamcheck_key_used_in_code_exists(self):
        src = _read("web/js/scam-check.js") + _read("web/js/components/tab-scamcheck.js")
        used = set(re.findall(r"['\"`](scamcheck\.[a-zA-Z.]+)['\"`]", src))
        used |= set(re.findall(r'data-i18n="(scamcheck\.[a-zA-Z.]+)"', src))
        en = _flat(_catalog("en"))
        assert used, "掃描沒抓到任何 key——正則壞了"
        dynamic = {k for k in used if k.endswith(".")}
        missing = sorted(k for k in used - dynamic if k not in en)
        assert not missing, missing

    @pytest.mark.parametrize(
        ("lang", "banned"),
        [("en", r"\bsafe(ly|ty)?\b"), ("zh-TW", "安全"), ("zh-CN", "安全"), ("ru", "безопасн")],
    )
    def test_verdict_copy_never_says_safe(self, lang, banned):
        """合規：分析工具不下「安全」結論——查無紅旗也只說 no red flags／not a guarantee。"""
        flat = _flat(_catalog(lang).get("scamcheck", {}))
        offenders = {k: v for k, v in flat.items() if re.search(banned, v, re.I)}
        assert not offenders, offenders

    def test_clean_state_and_note_say_not_a_guarantee(self):
        en = _catalog("en")["scamcheck"]
        assert "guarantee" in en["state"]["clean"]["desc"].lower()
        assert "guarantee" in en["note"].lower()
        assert "not a guarantee" in en["subtitle"].lower()

    def test_no_buy_sell_wording(self):
        blob = json.dumps(_catalog("en")["scamcheck"]).lower()
        for word in ("buy ", "sell ", "signal to", "recommend buying"):
            assert word not in blob, word

    def test_obsolete_guest_link_key_removed(self):
        for lang in LOCALES:
            assert "linkScam" not in _catalog(lang).get("guest", {}), lang


class TestOldUrls:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from api_server import app

        return TestClient(app)

    @pytest.mark.parametrize(
        "path",
        [
            "/scam-tracker",
            "/scam-tracker/",
            "/scam-tracker/index.html",
            "/static/scam-tracker/",
            "/static/scam-tracker/index.html",
        ],
    )
    def test_old_list_page_redirects_to_tab(self, client, path):
        res = client.get(path, follow_redirects=False)
        assert res.status_code in (301, 302, 307, 308)
        assert res.headers["location"] == "/#scamcheck"

    def test_address_is_passed_through(self, client):
        res = client.get(f"/scam-tracker/?address={EVM}", follow_redirects=False)
        assert res.headers["location"] == f"/?address={EVM}#scamcheck"
        res = client.get(f"/static/scam-tracker/index.html?address=%20{EVM}%20", follow_redirects=False)
        assert res.headers["location"] == f"/?address={EVM}#scamcheck", "前後空白修掉"

    def test_address_is_encoded_and_length_capped(self, client):
        res = client.get("/scam-tracker/?address=a%26b%3Dc%23x", follow_redirects=False)
        assert res.headers["location"] == "/?address=a%26b%3Dc%23x#scamcheck"
        res = client.get("/scam-tracker/?address=a%2Bb%20c", follow_redirects=False)
        assert res.headers["location"] == "/?address=a%2Bb%20c#scamcheck", "空白編成 %20（不是 +）"
        res = client.get("/scam-tracker/?address=" + "a" * 101, follow_redirects=False)
        assert res.headers["location"] == "/#scamcheck", "超過 API 上限的不帶"

    def test_detail_and_submit_pages_still_served(self, client):
        for path in ("/static/scam-tracker/detail.html", "/static/scam-tracker/submit.html", "/scam-tracker/detail.html"):
            res = client.get(path, follow_redirects=False)
            assert res.status_code == 200, path
            assert "text/html" in res.headers.get("content-type", "")

    def test_list_page_file_removed(self):
        assert not (ROOT / "web" / "scam-tracker" / "index.html").exists()


class TestSubpagesFollowFourLanguages:
    PAGES = ("web/scam-tracker/detail.html", "web/scam-tracker/submit.html")

    def test_two_state_toggle_replaced_with_language_switcher(self):
        for rel in self.PAGES:
            html = _read(rel)
            assert "toggleScamLanguage" not in html, rel
            assert 'id="scam-lang-switcher"' in html, rel
        patch = _read("web/scam-tracker/js/scam-tracker-i18n.js")
        assert "toggleScamLanguage" not in patch
        assert "new window.LanguageSwitcher(" in patch
        assert "toggleScamLanguage" not in _read("web/js/click-delegator.js")

    def test_dates_use_the_selected_language(self):
        patch = _read("web/scam-tracker/js/scam-tracker-i18n.js")
        assert "getLang() === 'zh-TW' ? 'zh-TW' : 'en-US'" not in patch
        assert "toLocaleDateString(getLang())" in patch

    def test_back_links_return_to_the_tab(self):
        for rel in self.PAGES:
            html = _read(rel)
            assert "/static/scam-tracker/index.html" not in html, rel
            assert 'href="/static/index.html#scamcheck"' in html, rel
        js = _read("web/scam-tracker/js/scam-tracker.js")
        assert "/static/scam-tracker/index.html" not in js

    def test_submit_prefills_address_param(self):
        js = _read("web/scam-tracker/js/scam-tracker.js")
        body = js[js.index("initSubmitPage() {") :]
        body = body[: body.index("\n    },")]
        assert "get('address')" in body

    def test_list_only_code_removed(self):
        js = _read("web/scam-tracker/js/scam-tracker.js")
        for gone in ("initListPage", "bindListEvents", "renderCheckVerdict", "handleSearch"):
            assert gone not in js, gone
        assert "list:" not in _read("web/scam-tracker/js/scam-tracker-boot.js")
