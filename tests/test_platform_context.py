"""平台情境（docs/plans/2026-09-13-google-play-twa-design.md §3）：能力表、X-Platform 閘門、
assetlinks、manifest、前端接線。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core import platform

pytestmark = pytest.mark.unit
REPO = Path(__file__).resolve().parents[1]


class TestCapabilities:
    def test_every_platform_has_same_keys(self):
        keys = {frozenset(v) for v in platform.CAPABILITIES.values()}
        assert len(keys) == 1

    @pytest.mark.parametrize(
        "plat, rail, allowed",
        [
            ("web", "evm_usdc", True),
            ("tma", "evm_usdc", False),
            ("baseapp", "evm_usdc", True),
            ("play", "evm_usdc", False),
            # TON 兩軌 2026-09-25 移除：任何平台都不放行
            ("web", "ton_native", False),
            ("tma", "ton_native", False),
            ("web", "ton_usdt", False),
            ("tma", "ton_usdt", False),
            ("play", "google_play", True),
            ("web", "google_play", False),
        ],
    )
    def test_rail_allowed(self, plat, rail, allowed):
        assert platform.rail_allowed(plat, rail) is allowed

    def test_normalize_unknown_is_web(self):
        assert platform.normalize("PLAY") == "play"
        assert platform.normalize("ios") == "web"
        assert platform.normalize(None) == "web"

    def test_play_has_google_login_no_crypto_rails(self):
        caps = platform.capabilities("play")
        assert caps["google_login"] and caps["rail_google_play"]
        assert not caps["rail_evm_usdc"]

    def test_ton_rail_keys_removed(self):
        for caps in platform.CAPABILITIES.values():
            assert "rail_ton_usdt" not in caps
            assert "rail_ton_native" not in caps


class TestRequestGate:
    def test_from_request_reads_header(self):
        app = FastAPI()

        @app.get("/p")
        async def _p(request: Request):
            return {"p": platform.from_request(request)}

        c = TestClient(app)
        assert c.get("/p").json()["p"] == "web"
        assert c.get("/p", headers={"X-Platform": "play"}).json()["p"] == "play"
        assert c.get("/p", headers={"X-Platform": "nonsense"}).json()["p"] == "web"

    def test_payment_order_blocked_on_play(self, monkeypatch):
        from api.deps import get_current_user
        from api.routers import premium

        app = FastAPI()
        app.include_router(premium.router)
        app.dependency_overrides[get_current_user] = lambda: {"user_id": "u1"}
        c = TestClient(app)
        res = c.post(
            "/api/premium/payment-order",
            json={"plan": "premium_monthly", "rail": "evm_usdc"},
            headers={"X-Platform": "play"},
        )
        assert res.status_code == 403 and "play" in res.json()["detail"]

    def test_config_exposes_platform(self, monkeypatch):
        from api.routers import system

        app = FastAPI()
        app.include_router(system.router)
        c = TestClient(app)
        data = c.get("/api/config", headers={"X-Platform": "play"}).json()
        assert (
            data["platform"] == "play"
            and data["capabilities"]["rail_google_play"] is True
        )
        assert c.get("/api/config").json()["platform"] == "web"


class TestAssetlinks:
    def test_empty_without_env(self, monkeypatch):
        monkeypatch.delenv("ANDROID_PACKAGE_NAME", raising=False)
        monkeypatch.delenv("ANDROID_CERT_SHA256", raising=False)
        assert platform.android_assetlinks_payload() == []

    def test_payload_shape(self, monkeypatch):
        monkeypatch.setenv("ANDROID_PACKAGE_NAME", "app.cryptomind.twa")
        monkeypatch.setenv("ANDROID_CERT_SHA256", "aa:bb, CC:DD")
        out = platform.android_assetlinks_payload()
        assert out[0]["relation"] == ["delegate_permission/common.handle_all_urls"]
        assert out[0]["target"] == {
            "namespace": "android_app",
            "package_name": "app.cryptomind.twa",
            "sha256_cert_fingerprints": ["AA:BB", "CC:DD"],
        }

    def test_route_and_manifest(self):
        src = (REPO / "api_server.py").read_text(encoding="utf-8")
        assert '"/.well-known/assetlinks.json"' in src
        assert '"start_url": f"{base}/?platform=play"' in src
        assert '"id": "/"' in src and '"screenshots"' in src


class TestFrontendWiring:

    def test_platform_context_js_semantics(self):
        import subprocess

        try:
            subprocess.run(["node", "--version"], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            pytest.skip("node 不在 PATH")
        proc = subprocess.run(
            ["node", str(REPO / "tests" / "js" / "platform_context.mjs")],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert proc.returncode == 0, proc.stderr or proc.stdout
