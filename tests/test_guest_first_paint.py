"""訪客首屏載入速度守衛（2026-09-27 上市準備 PR-1）。

正式站訪客量測（暖快取）：FCP ~1.65s、LCP ~5.9s、~120 個請求（77 個在 4 秒後）。
本檔守住三件不噴錯、只會默默變慢的事：

1. AppKit（整套錢包 SDK）不在開頁時無條件預載，只在使用者表現出意圖時才載
2. `<html lang>`／OG 標籤跟備援文案一致（英文）、og:image 是真的大圖
3. 歡迎畫面的淡入延遲不拖 LCP（LCP 不算看不見的元素）

SW precache 的守衛在 tests/test_sw_precache_config.py。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "web"
EVM_AUTH = WEB / "js" / "evm-auth.js"
INDEX = WEB / "index.html"


def _fn_body(js: str, signature: str) -> str:
    start = js.index(signature)
    return js[start : js.index("\n}", start) + 2]


class TestAppKitIntentPreload:
    def _attach(self) -> str:
        return _fn_body(
            EVM_AUTH.read_text(encoding="utf-8"),
            "(function _attachWalletConnectWarmers() {",
        )

    def test_no_unconditional_timer_preload(self):
        """以前 DOMContentLoaded 後 2.5 秒對所有人預載（約 60 個 chunk＋遠端設定）。"""
        js = EVM_AUTH.read_text(encoding="utf-8")
        assert "setTimeout(trigger, 2500)" not in js
        attach = self._attach()
        # 開頁就會跑的預載只剩「上次登出沒斷完」這一種
        assert "hasPendingDisconnect(" in attach, (
            "待斷線旗標在時仍要預載收尾（登出閉環）"
        )
        # 每一個呼叫 _warmWalletConnect 的地方（不含定義與註解）都只能是：
        # 意圖 handler（onIntent），或 if (pendingDisconnect) 區塊裡的閒置排程
        pending = attach[attach.index("if (pendingDisconnect) {") :]
        pending = pending[: pending.index("\n        }\n") + 10]
        calls = [
            line.strip()
            for line in js.splitlines()
            if "_warmWalletConnect" in line
            and not line.lstrip().startswith(("*", "//", "function _warmWalletConnect"))
        ]
        assert calls, "找不到任何 _warmWalletConnect 呼叫"
        for call in calls:
            if call == "if (!_appKitUnused()) _warmWalletConnect();":
                continue
            assert call in pending, f"開頁就預載 AppKit 只能在待斷線條件裡：{call}"

    def test_warm_on_button_intent(self):
        attach = self._attach()
        for btn_id in ("evm-login-btn", "guest-connect-btn"):
            assert f"'{btn_id}'" in attach, f"{btn_id} 要掛意圖預載"
        for ev in ("pointerenter", "focus", "touchstart"):
            assert f"'{ev}'" in attach, f"缺 {ev}（滑鼠／鍵盤／觸控意圖都要涵蓋）"

    def test_warm_when_login_modal_opens(self):
        """登入視窗的開啟入口有十幾個（一律拿掉 hidden），用 observer 一次涵蓋。"""
        attach = self._attach()
        assert "'login-modal'" in attach
        assert "MutationObserver" in attach
        assert "attributeFilter: ['class']" in attach

    def test_skip_inside_telegram_and_base_app(self):
        """Telegram Mini App 不能用 EVM 錢包、Base App 用宿主 provider——都用不到 AppKit。"""
        js = EVM_AUTH.read_text(encoding="utf-8")
        body = _fn_body(js, "function _appKitUnused()")
        assert "'tma'" in body and "'miniapp'" in body
        assert "_appKitUnused()" in self._attach()

    def test_click_path_still_awaits_appkit(self):
        """沒預載也要能連：點擊路徑自己載模組、connectViaWalletConnect 自己 await AppKit。"""
        js = EVM_AUTH.read_text(encoding="utf-8")
        login = js[js.index("window.safeEvmLogin = async function") :]
        assert "await _loadWalletConnect()" in login
        wc = (WEB / "js" / "evm-walletconnect.js").read_text(encoding="utf-8")
        connect = _fn_body(wc, "function connectViaWalletConnect()")
        assert "_ensureAppKit()" in connect


class TestHtmlLangAndOg:
    def test_html_lang_is_english_fallback(self):
        html = INDEX.read_text(encoding="utf-8")
        assert re.search(r'<html lang="en">', html), "pre-JS 備援文案是英文"
        assert '<meta property="og:locale" content="en_US">' in html

    def test_og_image_is_large_card(self):
        """twitter:card=summary_large_image 要配 1200×630 的圖，不能是小方圖示。"""
        html = INDEX.read_text(encoding="utf-8")
        hero = "https://getcryptomind.com/img/miniapp/hero-1200x630.png"
        assert f'<meta property="og:image" content="{hero}">' in html
        assert f'<meta name="twitter:image" content="{hero}">' in html
        assert '<meta property="og:image:width" content="1200">' in html
        assert '<meta property="og:image:height" content="630">' in html
        assert (WEB / "img" / "miniapp" / "hero-1200x630.png").exists()

    def test_i18n_sets_document_lang(self):
        """lang 要跟著目前語言：updatePageContent 在初始化與 languageChanged 都會跑。"""
        js = (WEB / "js" / "i18n.js").read_text(encoding="utf-8")
        body = _fn_body(js, "function updatePageContent()")
        assert "document.documentElement.lang = i18n.language" in body
        on_change = js[js.index("i18n.on('languageChanged'") :]
        assert "updatePageContent()" in on_change.split("});")[0]


class TestWelcomePaintTiming:
    MAX_DELAY = 0.15

    def test_css_welcome_delays(self):
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        for cls in ("welcome-title", "welcome-sub"):
            m = re.search(
                rf"\.{cls}\s*{{[^}}]*animation:[^;]*?([\d.]+)s\s+forwards", css
            )
            assert m, f".{cls} 的 animation 宣告找不到"
            assert float(m.group(1)) <= self.MAX_DELAY, (
                f".{cls} 延遲 {m.group(1)}s 會拖 LCP"
            )

    def test_js_welcome_delays(self):
        js = (WEB / "js" / "chat-sessions.js").read_text(encoding="utf-8")
        body = _fn_body(js, "async function showWelcomeScreen()")
        delays = [float(d) for d in re.findall(r"animation-delay:\s*([\d.]+)s", body)]
        assert delays, "歡迎畫面應該保留淡入"
        assert max(delays) <= self.MAX_DELAY, f"歡迎畫面延遲 {delays} 會拖 LCP"

    def test_body_not_hidden_on_boot(self):
        """首屏在 DOMContentLoaded 前就畫好了，boot 時把 body 設透明再淡入＝整頁閃一下、LCP 延後。"""
        js = (WEB / "js" / "spa.js").read_text(encoding="utf-8")
        boot = js[
            js.index("document.addEventListener('DOMContentLoaded', async () => {") :
        ]
        boot = boot[: boot.index("const waitForGlobal")]
        assert "document.body.style.opacity = '0'" not in boot
