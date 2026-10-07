"""Base App／Farcaster mini app 上架（manifest、嵌入 meta、宿主 provider）與 Telegram「TON 模式」
（EVM 入口在 Telegram 內藏起來、不建 USDC 訂單）。2026-09-13 DANNY：「Base App 做，Telegram TON 模式也做。」"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import miniapp

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


class TestManifest:
    def test_manifest_shape_and_limits(self, monkeypatch):
        monkeypatch.delenv("FARCASTER_ACCOUNT_ASSOCIATION_HEADER", raising=False)
        monkeypatch.delenv("BASE_BUILDER_ALLOWED_ADDRESSES", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")
        m = miniapp.build_manifest("https://cryptomind.example/")
        assert miniapp.validate_manifest(m) == []
        app = m["miniapp"]
        assert m["frame"] == app  # 舊客戶端讀 frame
        assert app["homeUrl"] == "https://cryptomind.example/"
        assert app["iconUrl"].endswith("/img/miniapp/icon-1024.png")
        assert app["requiredChains"] == ["eip155:8453"] and app["noindex"] is False
        assert "accountAssociation" not in m and "baseBuilder" not in m

    def test_manifest_includes_association_and_builder_when_configured(
        self, monkeypatch
    ):
        monkeypatch.setenv("FARCASTER_ACCOUNT_ASSOCIATION_HEADER", "h")
        monkeypatch.setenv("FARCASTER_ACCOUNT_ASSOCIATION_PAYLOAD", "p")
        monkeypatch.setenv("FARCASTER_ACCOUNT_ASSOCIATION_SIGNATURE", "s")
        monkeypatch.setenv("BASE_BUILDER_ALLOWED_ADDRESSES", "0xabc, 0xdef")
        monkeypatch.setenv("ENVIRONMENT", "development")
        m = miniapp.build_manifest("https://x.test")
        assert m["accountAssociation"] == {
            "header": "h",
            "payload": "p",
            "signature": "s",
        }
        assert m["baseBuilder"] == {"allowedAddresses": ["0xabc", "0xdef"]}
        assert m["miniapp"]["noindex"] is True  # 非正式環境不進搜尋

    def _clear_association_env(self, monkeypatch):
        for k in ("HEADER", "PAYLOAD", "SIGNATURE"):
            monkeypatch.delenv(f"FARCASTER_ACCOUNT_ASSOCIATION_{k}", raising=False)

    def test_prod_domain_gets_builtin_association(self, monkeypatch):
        """正式網域沒設 env 也帶內建簽名（2026-09-24 DANNY 在 Farcaster manifest 工具簽的）。"""
        self._clear_association_env(monkeypatch)
        m = miniapp.build_manifest("https://getcryptomind.com/")
        assoc = m["accountAssociation"]
        assert set(assoc) == {"header", "payload", "signature"}
        assert all(assoc.values())

    def test_builtin_association_payload_matches_its_domain(self):
        """簽名只對 payload 裡的網域有效；表的 key 跟 payload 對不上＝放錯簽名。"""
        import base64

        for domain, assoc in miniapp.SIGNED_ASSOCIATIONS.items():
            raw = assoc["payload"] + "=" * (-len(assoc["payload"]) % 4)
            assert json.loads(base64.urlsafe_b64decode(raw)) == {"domain": domain}

    def test_other_domains_do_not_get_builtin_association(self, monkeypatch):
        self._clear_association_env(monkeypatch)
        for base in (
            "https://www.getcryptomind.com",
            "http://localhost:8080",
            "https://x.test",
        ):
            assert "accountAssociation" not in miniapp.build_manifest(base), base

    def test_env_overrides_builtin_association(self, monkeypatch):
        monkeypatch.setenv("FARCASTER_ACCOUNT_ASSOCIATION_HEADER", "h")
        monkeypatch.setenv("FARCASTER_ACCOUNT_ASSOCIATION_PAYLOAD", "p")
        monkeypatch.setenv("FARCASTER_ACCOUNT_ASSOCIATION_SIGNATURE", "s")
        m = miniapp.build_manifest("https://getcryptomind.com")
        assert m["accountAssociation"] == {"header": "h", "payload": "p", "signature": "s"}

    def test_partial_association_is_ignored(self, monkeypatch):
        monkeypatch.setenv("FARCASTER_ACCOUNT_ASSOCIATION_HEADER", "h")
        monkeypatch.delenv("FARCASTER_ACCOUNT_ASSOCIATION_PAYLOAD", raising=False)
        monkeypatch.delenv("FARCASTER_ACCOUNT_ASSOCIATION_SIGNATURE", raising=False)
        assert miniapp.account_association() is None

    def test_embed_meta_is_valid_json_and_escaped(self):
        content = miniapp.embed_meta_content("https://x.test")
        data = json.loads(content)
        assert data["button"]["action"]["type"] == "launch_miniapp"
        assert data["imageUrl"].endswith("/img/miniapp/embed-1200x800.png")
        tags = miniapp.embed_meta_tags("https://x.test")
        assert 'name="fc:miniapp"' in tags and 'name="fc:frame"' in tags
        assert (
            "launch_frame" in tags
            and '"' not in tags.split('content="')[1].split('">')[0]
        )

    def test_assets_exist_with_spec_sizes(self):
        # 直接讀 PNG 檔頭（IHDR），不依賴 Pillow：CI 沒裝它，為了讀尺寸不值得多一個依賴。
        # 檔頭：8 byte 簽章 → 4 byte 長度 → "IHDR" → 寬(4) 高(4) 位元深度(1) 色彩類型(1)
        import struct

        sizes = {
            "icon-1024.png": (1024, 1024),
            "splash-200.png": (200, 200),
            "embed-1200x800.png": (1200, 800),
            "hero-1200x630.png": (1200, 630),
            "screenshot-1.png": (1284, 2778),
            "screenshot-2.png": (1284, 2778),
            "screenshot-3.png": (1284, 2778),
        }
        for name, size in sizes.items():
            head = (REPO / "web" / "img" / "miniapp" / name).read_bytes()[:26]
            assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{name} 不是 PNG"
            assert head[12:16] == b"IHDR", name
            width, height = struct.unpack(">II", head[16:24])
            bit_depth, color_type = head[24], head[25]
            assert (width, height) == size, name
            # 色彩類型 2 ＝ RGB（沒有 alpha）；4／6 才帶 alpha
            assert color_type == 2 and bit_depth == 8, f"{name} 不能有 alpha（icon 規格）"

    def test_validate_flags_overlong_fields(self):
        bad = {
            "miniapp": {
                "name": "x" * 33,
                "subtitle": "y" * 31,
                "primaryCategory": "nope",
                "tags": ["a"] * 6,
                "homeUrl": "u",
                "iconUrl": "i",
                "version": "1",
            }
        }
        problems = miniapp.validate_manifest(bad)
        assert {
            "name > 32",
            "subtitle > 30",
            "primaryCategory invalid",
            "tags > 5 or tag > 20 chars",
        } <= set(problems)


class TestRoutes:
    def _client(self):
        from api.routers import miniapp as mod
        from api.routers import system as sysmod

        app = FastAPI()
        app.include_router(mod.router)
        app.include_router(sysmod.router)
        return TestClient(app)

    def test_manifest_route_and_disable(self, monkeypatch):
        monkeypatch.delenv("MINIAPP_ENABLED", raising=False)
        client = self._client()
        res = client.get(
            "/.well-known/farcaster.json",
            headers={"x-forwarded-proto": "https", "host": "cm.test"},
        )
        assert res.status_code == 200
        assert res.json()["miniapp"]["homeUrl"] == "https://cm.test/"
        assert res.headers["cache-control"].startswith("public")
        monkeypatch.setenv("MINIAPP_ENABLED", "false")
        assert client.get("/.well-known/farcaster.json").status_code == 404

    def test_index_injects_embed_meta(self, monkeypatch):
        monkeypatch.delenv("MINIAPP_ENABLED", raising=False)
        client = self._client()
        res = client.get("/", headers={"x-forwarded-proto": "https", "host": "cm.test"})
        assert res.status_code == 200
        body = res.text
        assert (
            '<meta name="fc:miniapp" content="' in body
            and "https://cm.test/img/miniapp/embed-1200x800.png" in body
        )
        assert "<!-- FC_MINIAPP_META -->" not in body
        assert "no-cache" in res.headers["cache-control"]


def test_miniapp_images_allow_cross_origin_reads():
    """Base Dashboard／Farcaster 客戶端要跨站抓 icon 與縮圖；只開 /img/miniapp/，別的靜態路徑不動。"""
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse
    from fastapi.testclient import TestClient

    from api.middleware_setup import setup_middleware

    app = FastAPI()
    setup_middleware(app)

    @app.get("/img/miniapp/x.png")
    async def _img():
        return PlainTextResponse("png")

    @app.get("/img/store/tapps/x.png")
    async def _store_img():
        return PlainTextResponse("png")

    @app.get("/js/app.js")
    async def _js():
        return PlainTextResponse("js")

    client = TestClient(app)
    for path in ("/img/miniapp/x.png", "/img/store/tapps/x.png"):
        img = client.get(path)
        assert img.headers.get("access-control-allow-origin") == "*", path
        assert img.headers.get("cache-control", "").startswith("public"), path
    js = client.get("/js/app.js")
    assert "access-control-allow-origin" not in js.headers


def test_csp_lets_miniapp_hosts_frame_us():
    """網頁版 Base App／Farcaster／Telegram 把 mini app 放 iframe；frame-ancestors 只放行這些宿主。"""
    from api.middleware_setup import content_security_policy
    from core import miniapp

    csp = content_security_policy()
    directive = next(
        d.strip() for d in csp.split(";") if d.strip().startswith("frame-ancestors")
    )
    sources = directive.split()[1:]
    assert sources == ["'self'", *miniapp.FRAME_ANCESTORS]
    for host in (
        "https://*.farcaster.xyz",
        "https://*.base.app",
        "https://*.coinbase.com",
        "https://web.telegram.org",
    ):
        assert host in sources
    assert "*" not in sources and "https:" not in sources  # 不能退化成誰都能框
    assert all(src.startswith("https://") for src in sources[1:])
    # 其他指令不動
    assert "script-src 'self' https://cdn.jsdelivr.net" in csp  # 無 'unsafe-inline'
    assert "frame-src 'self' https://verify.walletconnect.org" in csp


def test_ton_auth_toasts_never_print_raw_i18n_keys():
    """Telegram 自動登入早於 i18n 字典載入，I18n.t 只回 key；toast 一律走帶 fallback 的 _t。"""
    src = (REPO / "web" / "js" / "ton-auth.js").read_text(encoding="utf-8")
    assert "function _t(key, fallback)" in src
    assert "window.I18n.t('tonAuth." not in src, (
        "ton-auth.js 不可直接 I18n.t 沒 fallback"
    )
    assert "_t('tonAuth.loginSuccess', 'Login successful!')" in src
    assert (
        src.count("_t('tonAuth.loginFailedShort', 'Login failed, please retry')") == 2
    )


class TestFrontendWiring:
    def test_index_loads_host_and_tma_scripts_before_app(self):
        html = (REPO / "web" / "index.html").read_text(encoding="utf-8")
        assert "<!-- FC_MINIAPP_META -->" in html
        assert html.index('src="/js/tma-mode.js"') < html.index(
            'src="/js/click-delegator.js"'
        )
        assert html.index('src="/js/miniapp-host.js"') < html.index(
            'src="/js/click-delegator.js"'
        )

    def test_miniapp_host_calls_ready_and_exposes_provider(self):
        js = (REPO / "web" / "js" / "miniapp-host.js").read_text(encoding="utf-8")
        assert "sdk.actions.ready(" in js
        assert "window.__miniAppEthereumProvider = p" in js
        assert (
            "cdn.jsdelivr.net/npm/@farcaster/miniapp-sdk" in js
        )  # CSP 已放行 jsdelivr
        assert "if (!framed && !webview) return;" in js  # 一般瀏覽器零成本
        csp = (REPO / "api" / "middleware_setup.py").read_text(encoding="utf-8")
        assert "https://cdn.jsdelivr.net" in csp

    def test_evm_auth_prefers_host_provider(self):
        inj = (REPO / "web" / "js" / "evm-injected.js").read_text(encoding="utf-8")
        assert "window.__miniAppEthereumProvider" in inj and "source: 'miniapp'" in inj
        auth = (REPO / "web" / "js" / "evm-auth.js").read_text(encoding="utf-8")
        assert (
            auth.count("injected.source === 'miniapp'") == 2
        )  # 登入＋綁定兩條都走宿主 provider
        # 宿主／錢包內建瀏覽器直接進 _completeEvmLogin（inWalletBrowser 讓提示改「請在登入視窗按確認」），
        # 再往下才是「在錢包 App 內開啟」引導（PR-6：Email／Google 入口跳過引導，但宿主 provider 仍優先）
        assert (
            "return await _completeEvmLogin(injected.provider, { inWalletBrowser: true });\n        }\n        if (!social && shouldShowOpenInWallet"
            in auth
        )
        assert "injected.source === 'miniapp' || (!social && injected.source === 'wallet-browser')" in auth

    def test_tma_mode_hides_evm_entries_and_blocks_usdc_orders(self):
        html = (REPO / "web" / "index.html").read_text(encoding="utf-8")
        assert re.search(
            r'id="evm-login-btn" data-click="safeEvmLogin" data-tma-hide', html
        )
        css = (REPO / "web" / "styles.css").read_text(encoding="utf-8")
        assert "html.tma [data-tma-hide] { display: none !important; }" in css
        for path, needle in (
            (
                "web/js/components/tab-settings.js",
                'data-click="safeEvmBind" data-tma-hide',
            ),
            ("web/js/wallet.js", 'data-click-arg="settings" data-tma-hide'),
            ("web/js/friends.js", 'data-click="safeEvmLogin" data-tma-hide'),
            # 論壇 dashboard 沒錢包時改連到設定頁綁定（那頁沒載 evm-auth.js）
            ("web/js/forum-app.js", 'href="/static/index.html#settings" data-tma-hide'),
        ):
            assert needle in (REPO / path).read_text(encoding="utf-8"), path
        tma = (REPO / "web" / "js" / "tma-mode.js").read_text(encoding="utf-8")
        assert "classList.add('tma')" in tma
        premium = (REPO / "web" / "js" / "premium.js").read_text(encoding="utf-8")
        # 建單前就攔：Telegram 內不能出現 USDC on Base 的訂單
        assert premium.index(
            "if (isTelegramMiniApp()) {\n                this._showTmaUpgradeNotice();"
        ) < premium.index("const rail = await this.chooseRail();")
        assert (
            "_showTmaUpgradeNotice()" in premium
            and "premium.tmaUpgradeTitle" in premium
        )


@pytest.mark.parametrize("lang", ["en", "zh-TW", "zh-CN", "ru"])
def test_locales(lang):
    d = json.loads(
        (REPO / "web" / "js" / "i18n" / f"{lang}.json").read_text(encoding="utf-8")
    )
    assert "tmaUpgradeTitle" in d["premium"] and "tmaUpgradeHint" in d["premium"]




def test_csp_allows_cloudflare_insights_beacon():
    """Cloudflare 自動注入的 Web Analytics beacon 曾被 script-src 擋掉（2026-09-21 console 截圖）。"""
    from api.middleware_setup import content_security_policy

    csp = content_security_policy()
    script = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    assert "https://static.cloudflareinsights.com" in script
