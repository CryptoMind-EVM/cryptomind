"""PR-6：Reown Email／Google 登入＋刷卡買 USDC（REOWN_SOCIAL_LOGIN_ENABLED，2026-09-27 起預設開）。

行為（AppKit 設定、情境判定、內嵌錢包標記、onramp 開啟條件、登出）在
tests/js/reown_social_login.mjs 用 node 實跑；本檔包那支 gate，另外守後端開關、
接線與條款文字。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
FLAG = "REOWN_SOCIAL_LOGIN_ENABLED"


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_node_behaviour_gate():
    try:
        proc = subprocess.run(
            ["node", "tests/js/reown_social_login.mjs"],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except FileNotFoundError:
        pytest.skip("node 不可用")
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-3000:]
    assert "reown_social_login: ok" in proc.stdout


class TestBackendFlag:
    def _config(self):
        from api.routers import system

        app = FastAPI()
        app.include_router(system.router)
        return TestClient(app).get("/api/config").json()

    def test_default_on(self, monkeypatch):
        """2026-09-27 DANNY：打開。Reown 後台 social_login 已開且未指定供應商清單（遠端 config 查過）。
        env 設 false 仍可一鍵關。"""
        monkeypatch.delenv(FLAG, raising=False)
        assert self._config()["reown_social_login"] is True

    @pytest.mark.parametrize("raw", ["true", "1", "yes"])
    def test_on(self, monkeypatch, raw):
        monkeypatch.setenv(FLAG, raw)
        assert self._config()["reown_social_login"] is True

    @pytest.mark.parametrize("raw", ["false", "0", "", "maybe"])
    def test_off_values(self, monkeypatch, raw):
        monkeypatch.setenv(FLAG, raw)
        assert self._config()["reown_social_login"] is False

    def test_registered_and_documented(self):
        from core.feature_flags import FLAG_REGISTRY

        assert FLAG in FLAG_REGISTRY
        assert FLAG_REGISTRY[FLAG][1] == "**on**"
        assert f"# {FLAG}=" in _read(".env.example"), (
            ".env.example 要寫成註解（預設開，設 false 關）"
        )


class TestFrontendWiring:
    def test_login_button_hidden_by_default_and_tma_hidden(self):
        html = _read("web/index.html")
        m = re.search(r"<button[^>]*id=\"social-login-btn\"[^>]*>", html)
        assert m, "登入視窗要有 #social-login-btn"
        tag = m.group(0)
        assert 'data-click="safeSocialLogin"' in tag, (
            "CSP：走 data-click 委派，不能 inline handler"
        )
        assert "data-tma-hide" in tag
        assert re.search(r'class="[^"]*\bhidden\b', tag), "預設 hidden——旗標關時看不到"
        area = html.split('id="wallet-login-area"')[1].split('id="dev-login-area"')[0]
        assert 'id="social-login-btn"' in area

    def test_click_delegator_allows_action(self):
        js = _read("web/js/click-delegator.js")
        allow = js.split("var allowedActions = new Set([")[1].split("]);")[0]
        assert "'safeSocialLogin'" in allow

    def test_evm_auth_exposes_social_entry(self):
        js = _read("web/js/evm-auth.js")
        assert "window.safeSocialLogin" in js
        assert "from './social-login.js'" in js

    def test_appkit_config_uses_resolved_flag(self):
        js = _read("web/js/evm-walletconnect.js")
        ensure = js.split("function _ensureAppKit()")[1].split("\n}\n")[0]
        assert "resolveSocialLogin()" in ensure
        assert "socialLogin" in ensure

    def test_premium_onramp_behind_flag(self):
        js = _read("web/js/premium.js")
        assert "resolveSocialLogin" in js, "刷卡買幣入口要跟著同一個判定"
        assert "openOnramp" in js

    def test_csp_allows_embedded_wallet_iframe(self):
        """內嵌錢包的 iframe 來自 secure.walletconnect.org。"""
        from api.middleware_setup import content_security_policy

        frame = next(
            d
            for d in content_security_policy().split(";")
            if d.strip().startswith("frame-src")
        )
        assert "https://*.walletconnect.org" in frame


def test_terms_cover_email_social_wallets_in_four_languages():
    html = _read("web/legal/terms-of-service.html")
    m = re.search(r'<li><span data-zh="[^"]*Email[^"]*Google[^"]*"[^>]*>', html)
    assert m, "服務條款要說明 Email／Google 建立的錢包"
    span = m.group(0)
    for attr in ("data-zh=", "data-zh-cn=", "data-en=", "data-ru="):
        assert attr in span
    assert "private key" in span.lower()


def test_plain_browser_drops_telegram_global_for_appkit():
    """2026-09-27：iPhone 看不到 Google 登入——telegram-web-app.js 在一般瀏覽器也建
    window.Telegram，Reown AppKit 據此判定「在 Telegram 裡」並在 iOS 拿掉 Google。
    tma-mode.js 在不是 Mini App 時要清掉它（node 實跑兩種情境）。"""
    import shutil
    import subprocess

    if shutil.which("node") is None:
        pytest.skip("node 不可用")
    out = subprocess.run(
        ["node", "tests/js/tma_mode_telegram_global.mjs"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, (out.stderr or out.stdout)[-3000:]
