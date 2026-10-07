"""Base App／Farcaster mini app 通知（2026-09-13）：JFS 驗簽、Key Registry 編碼、webhook 事件、
送出批次、早報接線、前端接線。不打網路、不打 DB。"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.miniapp_notifications import jfs, key_registry, send, service

pytestmark = pytest.mark.unit
REPO = Path(__file__).resolve().parents[1]


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _signed(payload: dict, *, fid=123, key_type="app_key", priv=None, tamper=False):
    priv = priv or Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes_raw()
    header = _b64url(
        json.dumps({"fid": fid, "type": key_type, "key": "0x" + pub.hex()}).encode()
    )
    body = _b64url(json.dumps(payload).encode())
    sig = priv.sign(f"{header}.{body}".encode())
    if tamper:
        sig = bytes([sig[0] ^ 1]) + sig[1:]
    return {
        "header": header,
        "payload": body,
        "signature": _b64url(sig),
    }, "0x" + pub.hex()


class TestJfs:
    def test_valid_signature_and_key(self):
        body, key = _signed({"event": "miniapp_added"})
        seen = {}

        def verify(fid, k):
            seen["args"] = (fid, k)
            return True

        ev = jfs.verify_jfs(body, verify)
        assert ev.fid == 123 and ev.payload == {"event": "miniapp_added"}
        assert seen["args"] == (123, key)

    def test_tampered_signature_rejected(self):
        body, _ = _signed({"event": "x"}, tamper=True)
        with pytest.raises(jfs.JfsError, match="signature"):
            jfs.verify_jfs(body, lambda f, k: True)

    def test_wrong_header_type_rejected(self):
        body, _ = _signed({"event": "x"}, key_type="custody")
        with pytest.raises(jfs.JfsError, match="app_key"):
            jfs.verify_jfs(body, lambda f, k: True)

    def test_key_not_owned_by_fid_rejected(self):
        body, _ = _signed({"event": "x"})
        with pytest.raises(jfs.JfsError, match="not valid"):
            jfs.verify_jfs(body, lambda f, k: False)

    @pytest.mark.parametrize(
        "bad", [None, "str", {}, {"header": "!!", "payload": "", "signature": ""}]
    )
    def test_garbage_rejected(self, bad):
        with pytest.raises(jfs.JfsError):
            jfs.verify_jfs(bad, lambda f, k: True)


class TestKeyRegistry:
    def test_encode_call_layout(self):
        data = key_registry.encode_call(3, "0x" + "ab" * 32)
        assert data.startswith("0x" + key_registry._SELECTOR)
        words = data[10:]
        assert words[:64] == "3".rjust(64, "0")  # fid
        assert words[64:128] == "40".rjust(64, "0")  # offset
        assert words[128:192] == "20".rjust(64, "0")  # len 32
        assert words[192:256] == "ab" * 32

    def test_decode_state(self):
        assert key_registry.decode_state("0x" + "0" * 63 + "1" + "0" * 64) == 1
        assert key_registry.decode_state("0x" + "0" * 128) == 0
        assert key_registry.decode_state("0x") is None

    def test_rpc_failure_returns_none(self, monkeypatch):
        import httpx

        key_registry.clear_cache()

        def boom(*a, **k):
            raise httpx.ConnectError("down")

        monkeypatch.setattr(key_registry.httpx, "post", boom)
        assert key_registry.app_key_active(1, "0x" + "00" * 32) is None

    def test_cache_hit(self, monkeypatch):
        key_registry.clear_cache()
        calls = []

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"result": "0x" + "0" * 63 + "1" + "0" * 64}

        def post(*a, **k):
            calls.append(1)
            return R()

        monkeypatch.setattr(key_registry.httpx, "post", post)
        assert key_registry.app_key_active(7, "0x" + "11" * 32) is True
        assert key_registry.app_key_active(7, "0x" + "11" * 32) is True
        assert len(calls) == 1


class TestServiceEvents:
    def _ev(self, payload, fid=9):
        return jfs.VerifiedEvent(fid=fid, app_key="0x" + "aa" * 32, payload=payload)

    def test_added_stores_token(self, monkeypatch):
        got = {}
        monkeypatch.setattr(
            service.store,
            "upsert_token",
            lambda fid, url, token: got.update(fid=fid, url=url, token=token),
        )
        r = service.handle_event(
            self._ev(
                {
                    "event": "miniapp_added",
                    "notificationDetails": {
                        "url": "https://api.farcaster.xyz/v1/frame-notifications",
                        "token": "t1",
                    },
                }
            )
        )
        assert r["ok"] and got == {
            "fid": 9,
            "url": "https://api.farcaster.xyz/v1/frame-notifications",
            "token": "t1",
        }

    def test_added_without_details_rejected(self, monkeypatch):
        monkeypatch.setattr(
            service.store, "upsert_token", lambda *a, **k: pytest.fail("must not store")
        )
        r = service.handle_event(self._ev({"event": "notifications_enabled"}))
        assert not r["ok"]

    def test_http_url_rejected(self, monkeypatch):
        monkeypatch.setattr(
            service.store, "upsert_token", lambda *a, **k: pytest.fail("must not store")
        )
        r = service.handle_event(
            self._ev(
                {
                    "event": "miniapp_added",
                    "notificationDetails": {"url": "http://x", "token": "t"},
                }
            )
        )
        assert not r["ok"]

    def test_removed_disables(self, monkeypatch):
        got = {}
        monkeypatch.setattr(
            service.store,
            "disable_by_fid",
            lambda fid, reason: got.update(fid=fid, reason=reason) or 1,
        )
        r = service.handle_event(self._ev({"event": "notifications_disabled"}))
        assert r["ok"] and got == {"fid": 9, "reason": "notifications_disabled"}

    def test_unknown_event(self):
        assert not service.handle_event(self._ev({"event": "weird"}))["ok"]

    def test_registry_unavailable_fails_closed(self, monkeypatch):
        monkeypatch.setattr(service, "app_key_active", lambda fid, key: None)
        body, _ = _signed({"event": "miniapp_removed"})
        with pytest.raises(jfs.JfsError, match="unavailable"):
            service.parse_event(body)


class TestSend:
    def test_batches_and_disables_invalid(self, monkeypatch):
        posted = []
        disabled = []
        sent = []

        class R:
            status_code = 200

            def __init__(self, tokens):
                self._t = tokens

            def json(self):
                return {
                    "successfulTokens": self._t[:-1],
                    "invalidTokens": self._t[-1:],
                    "rateLimitedTokens": [],
                }

        def post(url, json=None, timeout=None):
            posted.append((url, list(json["tokens"]), json["title"], json["body"]))
            return R(json["tokens"])

        monkeypatch.setattr(send.httpx, "post", post)
        monkeypatch.setattr(send, "_url_allowed", lambda url: True)  # 假主機名沒有 DNS
        monkeypatch.setattr(
            send.store, "disable_tokens", lambda toks, reason: disabled.extend(toks)
        )
        monkeypatch.setattr(send.store, "mark_sent", lambda toks: sent.extend(toks))
        targets = [{"url": "https://h/a", "token": f"t{i}"} for i in range(150)] + [
            {"url": "https://h/b", "token": "z"}
        ]
        res = send.send_notification(
            targets,
            notification_id="daily-brief-2026-09-13",
            title="x" * 40,
            body="y" * 200,
            target_url="https://cm.test/?brief=2026-09-13",
        )
        assert [len(p[1]) for p in posted] == [100, 50, 1]
        assert len(posted[0][2]) == 32 and len(posted[0][3]) == 128
        assert res.invalid == ["t99", "t149", "z"] and disabled == res.invalid
        assert len(res.successful) == 148 and len(sent) == 148

    def test_http_error_marks_failed(self, monkeypatch):
        import httpx

        def post(*a, **k):
            raise httpx.ConnectError("x")

        monkeypatch.setattr(send.httpx, "post", post)
        monkeypatch.setattr(send, "_url_allowed", lambda url: True)  # 假主機名沒有 DNS
        res = send.send_notification(
            [{"url": "https://h", "token": "t"}],
            notification_id="n",
            title="t",
            body="b",
            target_url="https://cm.test/",
        )
        assert res.failed == ["t"] and not res.any_success


    @pytest.mark.parametrize(
        "url",
        [
            "https://127.0.0.1/hook",
            "https://169.254.169.254/latest/meta-data/",
            "https://10.0.0.5/admin",
            "https://localhost/x",
        ],
    )
    def test_internal_url_never_posted(self, monkeypatch, url):
        """推播網址可能是前端 register 帶來的：內網／本機／metadata 一律不打（SSRF）。"""
        posted = []
        monkeypatch.setattr(send.httpx, "post", lambda *a, **k: posted.append(a))
        res = send.send_notification(
            [{"url": url, "token": "t"}],
            notification_id="n",
            title="t",
            body="b",
            target_url="https://cm.test/",
        )
        assert posted == [] and res.failed == ["t"]


class TestManifestAndRouter:
    def test_manifest_has_webhook_url(self, monkeypatch):
        from core import miniapp

        monkeypatch.delenv("MINIAPP_NOTIFICATIONS_ENABLED", raising=False)
        m = miniapp.build_manifest("https://cm.test")
        assert m["miniapp"]["webhookUrl"] == "https://cm.test/api/miniapp/webhook"
        monkeypatch.setenv("MINIAPP_NOTIFICATIONS_ENABLED", "false")
        assert "webhookUrl" not in miniapp.build_manifest("https://cm.test")["miniapp"]

    @pytest.fixture
    def client(self):
        from api.routers.miniapp import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    @pytest.mark.parametrize(
        "url, status",
        [
            ("https://169.254.169.254/latest/meta-data/", 400),
            ("https://127.0.0.1/x", 400),
            ("http://api.farcaster.xyz/v1/frame-notifications", 400),
        ],
    )
    def test_register_rejects_unsafe_url(self, monkeypatch, url, status):
        from api.routers import miniapp as r

        app = FastAPI()
        app.include_router(r.router)

        async def fake_user():
            return {"user_id": "u1"}

        app.dependency_overrides[r.get_current_user] = fake_user
        saved = []
        monkeypatch.setattr(r.store, "upsert_token", lambda *a, **k: saved.append(a))
        res = TestClient(app).post(
            "/api/miniapp/notifications/register",
            json={"fid": 1, "url": url, "token": "t"},
        )
        assert res.status_code == status and saved == []

    def test_webhook_invalid_signature_401(self, client, monkeypatch):
        from api.routers import miniapp as r

        monkeypatch.setattr(
            r.service,
            "parse_event",
            lambda body: (_ for _ in ()).throw(jfs.JfsError("invalid signature")),
        )
        assert (
            client.post(
                "/api/miniapp/webhook",
                json={"header": "a", "payload": "b", "signature": "c"},
            ).status_code
            == 401
        )

    def test_webhook_registry_unavailable_503(self, client, monkeypatch):
        from api.routers import miniapp as r

        monkeypatch.setattr(
            r.service,
            "parse_event",
            lambda body: (_ for _ in ()).throw(
                jfs.JfsError("key registry unavailable")
            ),
        )
        assert client.post("/api/miniapp/webhook", json={}).status_code == 503

    def test_webhook_ok(self, client, monkeypatch):
        from api.routers import miniapp as r

        ev = jfs.VerifiedEvent(
            fid=5, app_key="0x" + "aa" * 32, payload={"event": "miniapp_added"}
        )
        monkeypatch.setattr(r.service, "parse_event", lambda body: ev)
        monkeypatch.setattr(
            r.service, "handle_event", lambda e: {"ok": True, "event": "miniapp_added"}
        )
        res = client.post(
            "/api/miniapp/webhook",
            json={"header": "a", "payload": "b", "signature": "c"},
        )
        assert res.status_code == 200 and res.json()["event"] == "miniapp_added"


class TestBriefWiring:
    def test_default_channels_include_baseapp(self):
        from core.daily_brief.schedule import DEFAULT_CHANNELS, VALID_CHANNELS

        assert "baseapp" in DEFAULT_CHANNELS and "baseapp" in VALID_CHANNELS

    def test_send_baseapp_noop_without_tokens(self, monkeypatch):
        from core.daily_brief import send as bsend

        monkeypatch.setattr(service, "notify_user", lambda *a, **k: None)
        monkeypatch.setattr(service, "notify_user_via_base_app", lambda *a, **k: None)
        assert bsend.send_baseapp("u1", "t", "b", on_date="2026-09-13") is False

    def test_send_baseapp_uses_public_base_and_date(self, monkeypatch):
        from core.daily_brief import send as bsend

        got = {}

        def fake(user_id, **kw):
            got.update(kw, user_id=user_id)
            return SimpleNamespace(
                successful=["t"],
                invalid=[],
                rate_limited=[],
                failed=[],
                any_success=True,
            )

        monkeypatch.setattr(service, "notify_user", fake)
        monkeypatch.setattr(service, "notify_user_via_base_app", lambda *a, **k: None)
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://cm.test/")
        assert bsend.send_baseapp("u1", "T", "B", on_date="2026-09-13") is True
        assert got["target_url"] == "https://cm.test/?brief=2026-09-13"
        assert got["notification_id"] == "daily-brief-2026-09-13"

    async def test_cron_send_one_calls_baseapp(self):
        from datetime import datetime, timezone

        from core.daily_brief.compose import BriefData
        from core.daily_brief.schedule import effective_prefs
        from scripts.cron_daily_brief import send_one

        now = datetime(2026, 9, 13, 0, 5, tzinfo=timezone.utc)
        prefs = effective_prefs(
            {
                "user_id": "u1",
                "language": "zh-TW",
                "telegram_id": None,
                "prefs_user_id": None,
            }
        )
        data = BriefData(language="zh-TW", date_local=now.date(), display_name="D")
        data.positions = [{"symbol": "BTC", "market": "crypto", "change_pct": 1.0}]
        with (
            patch(
                "core.daily_brief.collect.collect_brief_data",
                new=AsyncMock(return_value=data),
            ),
            patch(
                "core.daily_brief.insight.generate_insight",
                new=AsyncMock(return_value=None),
            ),
            patch("core.daily_brief.send.send_inapp", return_value=False),
            patch("core.daily_brief.send.send_baseapp", return_value=True) as ba,
            patch("core.daily_brief.store.claim_day", return_value=True),
            patch("core.daily_brief.store.mark_sent"),
        ):
            assert await send_one(prefs, now) == "sent"
        assert ba.call_args.args[0] == "u1"
        assert ba.call_args.kwargs["on_date"] == "2026-09-13"


class TestFrontendAndSchema:
    def test_host_registers_tokens_and_has_no_add_banner(self):
        # 2026-09-13 DANNY：「加入」橫幅拔掉（Base App 不理 addMiniApp、又醜）；只留通知 token 回報
        html = (REPO / "web" / "index.html").read_text(encoding="utf-8")
        assert (
            'id="miniapp-add-banner"' not in html and "safety-chat-banner" not in html
        )
        host = (REPO / "web" / "js" / "miniapp-host.js").read_text(encoding="utf-8")
        assert "/api/miniapp/notifications/register" in host
        assert "addMiniApp" not in host

    def test_settings_has_baseapp_channel(self):
        tab = (REPO / "web" / "js" / "components" / "tab-settings.js").read_text(
            encoding="utf-8"
        )
        assert (
            'id="brief-channel-baseapp"' in tab
            and 'id="brief-channel-baseapp-row"' in tab
        )
        js = (REPO / "web" / "js" / "brief-settings.js").read_text(encoding="utf-8")
        assert "channels.push('baseapp')" in js and "baseapp_available" in js

    def test_schema_and_migration_chain(self):
        schema = (REPO / "core" / "database" / "schema.py").read_text(encoding="utf-8")
        assert (
            '("miniapp_notification_tokens", create_miniapp_notification_tokens_table)'
            in schema
        )
        mig = (
            REPO / "alembic" / "versions" / "c049_miniapp_notification_tokens.py"
        ).read_text(encoding="utf-8")
        assert 'revision = "c049"' in mig and 'down_revision = "c048"' in mig
        assert "CREATE TABLE IF NOT EXISTS miniapp_notification_tokens" in mig
        assert "UNIQUE (fid, url)" in mig and "UNIQUE (fid, url)" in schema

    def test_i18n_keys_present(self):
        for lang in ("zh-TW", "zh-CN", "en", "ru"):
            d = json.loads(
                (REPO / "web" / "js" / "i18n" / f"{lang}.json").read_text(
                    encoding="utf-8"
                )
            )
            assert d["settings"]["brief"]["channelBaseapp"]
        ui = json.loads(
            (REPO / "core" / "i18n" / "ui_messages.json").read_text(encoding="utf-8")
        )
        for lang in ("zh-TW", "zh-CN", "en", "ru"):
            assert len(ui[lang]["daily_brief.push_title"]) <= 32
            assert len(ui[lang]["daily_brief.push_body"]) <= 128


class TestBaseDashboardNotifications:
    """2026-04-09 起 Base App 不走 Farcaster 規格：Dashboard API 以錢包地址推播。"""

    @pytest.fixture(autouse=True)
    def _no_pacing(self, monkeypatch):
        from core.miniapp_notifications import basedev

        monkeypatch.setattr(basedev, "MIN_INTERVAL", 0.0)
        monkeypatch.setattr(basedev, "_disabled_logged", False)
        basedev.clear_cache()

    def _on(self, monkeypatch):
        monkeypatch.setenv("BASE_DASHBOARD_API_KEY", "k")
        monkeypatch.setenv("BASE_APP_NOTIFICATIONS_ENABLED", "true")
        monkeypatch.setenv("BASE_APP_URL", "https://cm.test/")

    @staticmethod
    def _no_network(monkeypatch):
        """回傳呼叫紀錄：光 raise 不夠，send_baseapp 的 except 會把它吞掉。"""
        from core.miniapp_notifications import basedev

        calls = []

        def boom(*a, **k):
            calls.append(a)
            raise AssertionError("關閉時不可以打 Base API")

        monkeypatch.setattr(basedev.httpx, "get", boom)
        monkeypatch.setattr(basedev.httpx, "post", boom)
        return calls

    def test_disabled_without_key(self, monkeypatch):
        from core.miniapp_notifications import basedev

        monkeypatch.delenv("BASE_DASHBOARD_API_KEY", raising=False)
        monkeypatch.setenv("BASE_APP_NOTIFICATIONS_ENABLED", "true")
        self._no_network(monkeypatch)
        assert basedev.enabled() is False
        assert basedev.opted_in_addresses() is None
        assert basedev.send(["0xabc"], title="t", message="m").sent == []

    def test_key_alone_does_not_enable(self, monkeypatch, caplog):
        """部署時 VM 上有 key 也不能自己開始推：首次全體推播要 DANNY 拍板打開旗標。"""
        from core.miniapp_notifications import basedev

        monkeypatch.setenv("BASE_DASHBOARD_API_KEY", "k")
        monkeypatch.delenv("BASE_APP_NOTIFICATIONS_ENABLED", raising=False)
        self._no_network(monkeypatch)
        with caplog.at_level("INFO", logger=basedev.logger.name):
            assert basedev.enabled() is False
            assert basedev.enabled() is False
            assert basedev.opted_in_addresses() is None
            assert basedev.send(["0x" + "a" * 40], title="t", message="m").sent == []
            assert (
                service.notify_user_via_base_app("u1", title="T", message="M") is None
            )
            assert service.base_app_opted_in("u1") is False
        msgs = [
            r for r in caplog.records if "BASE_APP_NOTIFICATIONS_ENABLED" in r.message
        ]
        assert len(msgs) == 1, "關閉的原因只記一次，不要每個使用者洗一行"

    @pytest.mark.parametrize("flag", ["false", "0", "off", ""])
    def test_flag_off_values(self, monkeypatch, flag):
        from core.miniapp_notifications import basedev

        monkeypatch.setenv("BASE_DASHBOARD_API_KEY", "k")
        monkeypatch.setenv("BASE_APP_NOTIFICATIONS_ENABLED", flag)
        assert basedev.enabled() is False

    def test_send_payload_shape_and_auth_header(self, monkeypatch):
        from core.miniapp_notifications import basedev

        self._on(monkeypatch)
        calls = []

        class R:
            status_code = 200
            content = b"x"

            def json(self):
                return {
                    "success": True,
                    "results": [{"walletAddress": addr, "sent": True}],
                    "sentCount": 1,
                    "failedCount": 0,
                }

        addr = "0x52908400098527886E0F7030069857D2E4169EE7"  # EIP-55 範例地址

        def post(url, json=None, headers=None, timeout=None):
            calls.append((url, json, headers))
            return R()

        monkeypatch.setattr(basedev.httpx, "post", post)
        res = basedev.send(
            [addr.lower()], title="早報", message="好了", target_path="/?brief=d"
        )
        url, body, headers = calls[0]
        assert url == "https://dashboard.base.org/api/v1/notifications/send"
        assert headers == {"x-api-key": "k"}
        assert body == {
            "app_url": "https://cm.test/",
            # 送 EIP-55（Base 的 status 端點也是正規化成這個）
            "wallet_addresses": [addr],
            "title": "早報",
            "message": "好了",
            "target_path": "/?brief=d",
        }
        assert res.sent == [addr.lower()] and res.failed == {}

    def test_send_skips_malformed_addresses(self, monkeypatch):
        from core.miniapp_notifications import basedev

        self._on(monkeypatch)
        self._no_network(monkeypatch)
        assert basedev.send(["0xabc", "EQ..", ""], title="t", message="m").sent == []

    @pytest.mark.parametrize("status", [401, 403, 429, 503])
    def test_send_http_status_errors_are_reported_not_raised(self, monkeypatch, status):
        from core.miniapp_notifications import basedev

        self._on(monkeypatch)

        class R:
            status_code = status
            content = b'{"error":"x"}'
            text = '{"error":"x"}'

            def json(self):
                return {"error": "x"}

        monkeypatch.setattr(basedev.httpx, "post", lambda *a, **k: R())
        a = "0x" + "a" * 40
        res = basedev.send([a], title="t", message="m")
        assert res.sent == [] and res.failed == {a: f"status {status}"}

    def test_send_network_error_reported_not_raised(self, monkeypatch):
        from core.miniapp_notifications import basedev

        self._on(monkeypatch)

        def post(*a, **k):
            raise basedev.httpx.ConnectError("down")

        monkeypatch.setattr(basedev.httpx, "post", post)
        a = "0x" + "b" * 40
        assert basedev.send([a], title="t", message="m").failed == {a: "http error"}

    def test_pacing_keeps_under_rate_limit(self, monkeypatch):
        """Dashboard 端點共用 20 req/min/IP：連續兩次呼叫之間至少隔 MIN_INTERVAL。"""
        from core.miniapp_notifications import basedev

        clock = {"t": 100.0}
        slept = []
        monkeypatch.setattr(basedev, "MIN_INTERVAL", 3.0)
        monkeypatch.setattr(basedev, "_last_call", 0.0)
        monkeypatch.setattr(basedev.time, "monotonic", lambda: clock["t"])

        def sleep(s):
            slept.append(s)
            clock["t"] += s

        monkeypatch.setattr(basedev.time, "sleep", sleep)
        basedev._pace()
        clock["t"] += 1.0
        basedev._pace()
        assert slept == [pytest.approx(2.0)]

    def test_audience_paginates_filters_and_caches(self, monkeypatch):
        from core.miniapp_notifications import basedev

        self._on(monkeypatch)
        basedev.clear_cache()
        calls = []

        class R:
            status_code = 200

            def __init__(self, page):
                self._p = page

            def json(self):
                if self._p == 0:
                    return {
                        "success": True,
                        "users": [
                            {"address": "0xAAA", "notificationsEnabled": True},
                            {"address": "0xBBB", "notificationsEnabled": False},
                        ],
                        "nextCursor": "c2",
                    }
                return {
                    "success": True,
                    "users": [{"address": "0xCCC", "notificationsEnabled": True}],
                }

        def get(url, params=None, headers=None, timeout=None):
            calls.append(
                (params.get("cursor"), headers.get("x-api-key"), params.get("app_url"))
            )
            return R(len(calls) - 1)

        monkeypatch.setattr(basedev.httpx, "get", get)
        assert basedev.opted_in_addresses() == {"0xaaa", "0xccc"}
        assert (
            calls[0][1] == "k"
            and calls[0][2] == "https://cm.test/"
            and calls[1][0] == "c2"
        )
        assert basedev.opted_in_addresses() == {"0xaaa", "0xccc"} and len(calls) == 2, (
            "快取命中不再打 API"
        )

    def test_send_clips_batches_and_reports(self, monkeypatch):
        from core.miniapp_notifications import basedev

        self._on(monkeypatch)
        posted = []

        class R:
            status_code = 200
            content = b"x"

            def __init__(self, addrs):
                self._a = addrs

            def json(self):
                return {
                    "success": False,
                    "results": [
                        {
                            "walletAddress": a,
                            "sent": i > 0,
                            "failureReason": None
                            if i > 0
                            else "user has not saved this app",
                        }
                        for i, a in enumerate(self._a)
                    ],
                }

        def post(url, json=None, headers=None, timeout=None):
            posted.append(json)
            return R(json["wallet_addresses"])

        monkeypatch.setattr(basedev.httpx, "post", post)
        addrs = [f"0x{i:040x}" for i in range(1001)] + [
            "0x" + "0" * 39 + "1"
        ]  # 重複一個
        res = basedev.send(
            addrs, title="x" * 40, message="y" * 300, target_path="rewards"
        )
        assert [len(p["wallet_addresses"]) for p in posted] == [1000, 1]
        assert len(posted[0]["title"]) == 30 and len(posted[0]["message"]) == 200
        assert (
            posted[0]["target_path"] == "/rewards"
            and posted[0]["app_url"] == "https://cm.test/"
        )
        assert len(res.sent) == 999 and len(res.failed) == 2

    def test_notify_user_via_base_app_intersects_bound_wallets(self, monkeypatch):
        from core.miniapp_notifications import basedev

        self._on(monkeypatch)
        monkeypatch.setattr(
            basedev, "opted_in_addresses", lambda **k: {"0xaaa", "0xbbb"}
        )
        monkeypatch.setattr(
            "core.onchain.store.list_wallets",
            lambda uid: [
                {"chain": "evm", "address": "0xAAA"},
                {"chain": "evm", "address": "0xzzz"},
                {"chain": "ton", "address": "EQ.."},
            ],
        )
        got = {}

        def fake_send(addrs, **kw):
            got.update(addrs=addrs, **kw)
            return basedev.BaseSendResult(sent=list(addrs))

        monkeypatch.setattr(basedev, "send", fake_send)
        r = service.notify_user_via_base_app(
            "u1", title="T", message="M", target_path="/?brief=2026-09-13"
        )
        assert (
            r.any_success
            and got["addrs"] == ["0xaaa"]
            and got["target_path"] == "/?brief=2026-09-13"
        )
        assert service.base_app_opted_in("u1") is True

    def test_notify_user_via_base_app_none_when_no_match(self, monkeypatch):
        from core.miniapp_notifications import basedev

        self._on(monkeypatch)
        monkeypatch.setattr(basedev, "opted_in_addresses", lambda **k: {"0xaaa"})
        monkeypatch.setattr(
            "core.onchain.store.list_wallets",
            lambda uid: [{"chain": "evm", "address": "0xzzz"}],
        )
        assert service.notify_user_via_base_app("u1", title="T", message="M") is None
        assert service.base_app_opted_in("u1") is False

    @staticmethod
    def _fc_ok(calls):
        def fake(uid, **kw):
            calls.append(uid)
            return SimpleNamespace(
                successful=["t"],
                invalid=[],
                rate_limited=[],
                failed=[],
                any_success=True,
            )

        return fake

    def test_send_baseapp_no_double_send_when_base_app_delivered(self, monkeypatch):
        """同一人兩條路都有：Base App 送到了就不再用 Farcaster token 送第二則。"""
        from core.daily_brief import send as bsend

        fc_calls = []
        monkeypatch.setattr(
            service,
            "notify_user_via_base_app",
            lambda uid, **kw: SimpleNamespace(
                any_success=True, sent=["0xaaa"], failed={}
            ),
        )
        monkeypatch.setattr(service, "notify_user", self._fc_ok(fc_calls))
        assert bsend.send_baseapp("u1", "T", "B", on_date="2026-09-13") is True
        assert fc_calls == []

    @pytest.mark.parametrize(
        "base_result",
        [
            None,  # 沒開／沒名單／沒對上
            SimpleNamespace(any_success=False, sent=[], failed={"0xaaa": "status 429"}),
        ],
    )
    def test_send_baseapp_falls_back_to_farcaster(self, monkeypatch, base_result):
        from core.daily_brief import send as bsend

        fc_calls = []
        monkeypatch.setattr(
            service, "notify_user_via_base_app", lambda uid, **kw: base_result
        )
        monkeypatch.setattr(service, "notify_user", self._fc_ok(fc_calls))
        assert bsend.send_baseapp("u1", "T", "B", on_date="2026-09-13") is True
        assert fc_calls == ["u1"]
        monkeypatch.setattr(service, "notify_user", lambda uid, **kw: None)
        assert bsend.send_baseapp("u1", "T", "B", on_date="2026-09-13") is False

    def test_send_baseapp_base_app_crash_does_not_break_brief(self, monkeypatch):
        from core.daily_brief import send as bsend

        def boom(uid, **kw):
            raise RuntimeError("base api exploded")

        fc_calls = []
        monkeypatch.setattr(service, "notify_user_via_base_app", boom)
        monkeypatch.setattr(service, "notify_user", self._fc_ok(fc_calls))
        assert bsend.send_baseapp("u1", "T", "B", on_date="2026-09-13") is True
        assert fc_calls == ["u1"]

    def test_send_baseapp_end_to_end_default_off(self, monkeypatch):
        """真的 basedev＋service（只擋網路）：有 key 沒旗標＝Base App 路徑整個不動。"""
        from core.daily_brief import send as bsend

        monkeypatch.setenv("BASE_DASHBOARD_API_KEY", "k")
        monkeypatch.delenv("BASE_APP_NOTIFICATIONS_ENABLED", raising=False)
        calls = self._no_network(monkeypatch)
        monkeypatch.setattr(service, "notify_user", lambda uid, **kw: None)
        assert bsend.send_baseapp("u1", "T", "B", on_date="2026-09-13") is False
        assert calls == []

    def test_push_titles_fit_base_limit(self):
        ui = json.loads(
            (REPO / "core" / "i18n" / "ui_messages.json").read_text(encoding="utf-8")
        )
        for lang in ("zh-TW", "zh-CN", "en", "ru"):
            assert len(ui[lang]["daily_brief.push_title"]) <= 30, (
                lang
            )  # Base Dashboard 上限 30

    def test_frontend_direct_connects_in_wallet_browser(self):
        inj = (REPO / "web" / "js" / "evm-injected.js").read_text(encoding="utf-8")
        assert "source: 'wallet-browser'" in inj
        auth = (REPO / "web" / "js" / "evm-auth.js").read_text(encoding="utf-8")
        assert auth.count("injected.source === 'wallet-browser'") == 2


class TestSwReload:
    def test_sw_update_never_reloads_the_page(self):
        """2026-09-27：新版 SW 接管時不自動重整。#904 起 HTML 一律走網路，開頁拿到的就是新版；
        舊的「開頁 20 秒內重整」反而會在部署後打斷進行中的流程——Google 登入去 Google 分頁時
        原頁被重整，回來登入就斷了（DANNY 錄影）。開著很久的頁面只提示有新版。"""
        sw = (REPO / "web" / "public" / "sw-register.js").read_text(encoding="utf-8")
        assert "controllerchange" in sw and "reg.update()" in sw
        assert "location.reload" not in sw
        assert "newVersionReady" in sw
        for lang in ("zh-TW", "zh-CN", "en", "ru"):
            d = json.loads(
                (REPO / "web" / "js" / "i18n" / f"{lang}.json").read_text(
                    encoding="utf-8"
                )
            )
            assert d["common"]["newVersionReady"]
