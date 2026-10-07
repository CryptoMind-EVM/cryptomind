"""Email 早報（PR-8；EMAIL_BRIEF_ENABLED 2026-09-27 起預設開，寄信設定不齊一律不動作）。

全部 mock：不打 DB、不寄信（Resend 用 httpx.MockTransport）。
設計：docs/plans/2026-09-27-pr8-email-brief-impl.md
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
USER = {"user_id": "u_mail", "username": "mail", "membership_tier": "free"}
NOW = datetime(2026, 9, 27, 0, 5, tzinfo=timezone.utc)

ENV_OK = {
    "EMAIL_BRIEF_ENABLED": "true",
    "EMAIL_PROVIDER": "resend",
    "RESEND_API_KEY": "test-resend-key-not-real",
    "EMAIL_FROM": "CryptoMind <brief@example.com>",
    "EMAIL_SENDER_POSTAL_ADDRESS": "CryptoMind, 1 Test Road, Taipei",
    "PUBLIC_BASE_URL": "https://app.example.com",
    "EMAIL_TOKEN_SECRET": "unit-test-email-token-secret-0123456789",
}
REQUIRED_ENV = (
    "RESEND_API_KEY",
    "EMAIL_FROM",
    "EMAIL_SENDER_POSTAL_ADDRESS",
)


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _token_from(url: str) -> str:
    return parse_qs(urlparse(url).query)["token"][0]


class FakeSender:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.sent = []

    async def send(self, message) -> bool:
        self.sent.append(message)
        return self.ok


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from api.middleware.rate_limit import limiter
    from core.email_brief import provider

    for k in list(ENV_OK) + ["ENVIRONMENT"]:
        monkeypatch.delenv(k, raising=False)
    provider._reset_warnings()
    limiter.reset()
    yield
    limiter.reset()
    provider._reset_warnings()


@pytest.fixture
def flag_on(monkeypatch):
    for k, v in ENV_OK.items():
        monkeypatch.setenv(k, v)


def _client():
    from slowapi.errors import RateLimitExceeded

    from api.middleware.rate_limit import limiter, rate_limit_exceeded_handler
    from api.routers import email_brief as mod

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(mod.router)
    app.include_router(mod.public_router)

    async def fake_user():
        return USER

    app.dependency_overrides[mod.get_current_user] = fake_user
    return TestClient(app)


def _sub_row(**kw):
    base = {
        "user_id": "u_mail",
        "email": "danny@example.com",
        "confirm_token_hash": None,
        "confirm_sent_at": NOW - timedelta(hours=1),
        "verified_at": NOW - timedelta(minutes=30),
        "unsubscribe_token_hash": "x" * 64,
        "unsubscribed_at": None,
        "language": "en",
    }
    base.update(kw)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# 旗標關＝零行為改變
# ─────────────────────────────────────────────────────────────────────────────


class TestFlagOff:
    """沒設好寄信服務（2026-09-27 起旗標預設開，但 Resend 還沒設）＝零行為改變。"""

    def test_flag_defaults_on_but_unavailable_without_config(self):
        from core.email_brief import provider
        from core.feature_flags import email_brief_enabled

        assert email_brief_enabled() is True
        assert provider.email_brief_available() is False

    def test_user_endpoints_are_404(self):
        client = _client()
        with patch("core.email_brief.store.get_subscription") as get:
            assert client.get("/api/user/email-brief").status_code == 404
            assert (
                client.put(
                    "/api/user/email-brief", json={"email": "a@example.com"}
                ).status_code
                == 404
            )
            assert client.delete("/api/user/email-brief").status_code == 404
        get.assert_not_called()

    def test_confirm_is_404(self):
        client = _client()
        with patch("core.email_brief.store.find_by_confirm_hash") as find:
            get = client.get("/api/email/confirm", params={"token": "a" * 43})
            post = client.post("/api/email/confirm", params={"token": "a" * 43})
        assert get.status_code == post.status_code == 404
        find.assert_not_called()

    def test_unsubscribe_keeps_working_when_flag_off(self):
        """刻意例外：旗標關掉後，舊信裡的退訂連結仍要有效（只會少寄、不會多寄）。"""
        client = _client()
        with patch(
            "core.email_brief.store.unsubscribe_by_hash",
            return_value={"user_id": "u_mail", "language": "en"},
        ) as unsub:
            page = client.get("/api/email/unsubscribe", params={"token": "a" * 43})
            res = client.post("/api/email/unsubscribe", params={"token": "a" * 43})
        assert page.status_code == res.status_code == 200
        unsub.assert_called_once_with(_sha("a" * 43))

    def test_no_sender_and_no_send(self):
        from core.email_brief import provider, service

        assert provider.get_sender() is None
        assert provider.email_brief_available() is False
        with patch("core.email_brief.store.get_active") as get_active:
            import asyncio

            ok = asyncio.run(
                service.send_brief_email("u_mail", "en", "hi", on_date="2026-09-27")
            )
        assert ok is False
        get_active.assert_not_called()

    def test_cron_does_not_query_email_table(self):
        from core.email_brief import service

        rows = [{"user_id": "u1"}]
        with patch("core.email_brief.store.active_user_ids") as active:
            out = service.annotate_rows(rows)
        assert out is rows
        active.assert_not_called()

    def test_api_config_hides_email_brief(self):
        from api.routers import system

        app = FastAPI()
        app.include_router(system.router)
        res = TestClient(app).get("/api/config")
        assert res.status_code == 200
        assert res.json()["email_brief_enabled"] is False

    def test_api_config_shows_email_brief_when_fully_configured(self, flag_on):
        from api.routers import system

        app = FastAPI()
        app.include_router(system.router)
        res = TestClient(app).get("/api/config")
        assert res.json()["email_brief_enabled"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 邊界驗證與遮罩
# ─────────────────────────────────────────────────────────────────────────────


class TestEmailValidation:
    def test_normalizes_case_and_whitespace(self):
        from core.email_brief.service import normalize_email

        assert normalize_email("  Danny.Chen+brief@Example.COM ") == (
            "danny.chen+brief@example.com"
        )

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "no-at-sign",
            "a@b",
            "a@@b.com",
            "a b@c.com",
            "a@b..com",
            ".a@b.com",
            "a.@b.com",
            "a..b@c.com",
            "x" * 65 + "@b.com",
            "a@" + "b" * 250 + ".com",
            "a@b.c",
            "a@-b.com",
            "<script>@x.com",
            "a@b.com\nBcc: victim@x.com",
            "測試@example.com",
            "a@example.com,b@example.com",
        ],
    )
    def test_rejects_invalid(self, raw):
        from core.email_brief.service import normalize_email

        with pytest.raises(ValueError):
            normalize_email(raw)

    @pytest.mark.parametrize(
        "raw,masked",
        [
            ("danny@gmail.com", "d***@g***.com"),
            ("a@b.co", "a***@b***.co"),
            ("x@mail.example.org", "x***@m***.org"),
            ("garbage", "***"),
            ("", "***"),
        ],
    )
    def test_mask_email(self, raw, masked):
        from core.email_brief.provider import mask_email

        assert mask_email(raw) == masked


class TestTokens:
    def test_confirm_token_is_random_and_urlsafe(self):
        from core.email_brief.service import is_token_shaped, new_confirm_token

        a, b = new_confirm_token(), new_confirm_token()
        assert a != b and len(a) >= 40
        assert re.fullmatch(r"[A-Za-z0-9_-]+", a)
        assert is_token_shaped(a)

    @pytest.mark.parametrize("bad", ["", "short", "a" * 200, "a" * 42 + "!", None])
    def test_token_shape_rejects_garbage(self, bad):
        from core.email_brief.service import is_token_shaped

        assert is_token_shaped(bad) is False

    def test_hash_is_sha256_not_the_token(self):
        from core.email_brief.service import hash_token

        assert hash_token("abc") == _sha("abc") != "abc"

    def test_unsubscribe_token_is_stable_and_unguessable(self, flag_on, monkeypatch):
        from core.email_brief.service import is_token_shaped, unsubscribe_token

        t1 = unsubscribe_token("u_mail", "danny@example.com")
        assert t1 == unsubscribe_token("u_mail", "danny@example.com")
        assert t1 != unsubscribe_token("u_other", "danny@example.com")
        assert t1 != unsubscribe_token("u_mail", "other@example.com")
        assert "danny" not in t1 and "u_mail" not in t1
        assert is_token_shaped(t1)
        monkeypatch.setenv("EMAIL_TOKEN_SECRET", "another-secret-another-secret-12345")
        assert unsubscribe_token("u_mail", "danny@example.com") != t1

    def test_unsubscribe_token_falls_back_to_jwt_secret(self, flag_on, monkeypatch):
        from core.email_brief.service import unsubscribe_token

        monkeypatch.delenv("EMAIL_TOKEN_SECRET")
        monkeypatch.setenv("JWT_SECRET_KEY", "jwt-secret-for-tests-0123456789abcdef")
        assert unsubscribe_token("u_mail", "danny@example.com")


# ─────────────────────────────────────────────────────────────────────────────
# 設定不全就 fail closed
# ─────────────────────────────────────────────────────────────────────────────


class TestProviderConfig:
    def test_fully_configured(self, flag_on):
        from core.email_brief import provider

        assert provider.missing_config() == []
        assert isinstance(provider.get_sender(), provider.ResendSender)
        assert provider.email_brief_available() is True

    @pytest.mark.parametrize("name", REQUIRED_ENV)
    def test_each_required_env_fails_closed(self, flag_on, monkeypatch, name):
        from core.email_brief import provider

        monkeypatch.delenv(name)
        assert name in provider.missing_config()
        assert provider.get_sender() is None
        assert provider.email_brief_available() is False

    def test_missing_token_secret_fails_closed(self, flag_on, monkeypatch):
        from core.email_brief import provider

        monkeypatch.delenv("EMAIL_TOKEN_SECRET")
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        assert provider.get_sender() is None

    def test_production_requires_https_base(self, flag_on, monkeypatch):
        from core.email_brief import provider

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("PUBLIC_BASE_URL", "http://app.example.com")
        assert "PUBLIC_BASE_URL" in provider.missing_config()
        assert provider.get_sender() is None

    def test_unknown_provider_fails_closed(self, flag_on, monkeypatch):
        from core.email_brief import provider

        monkeypatch.setenv("EMAIL_PROVIDER", "carrier-pigeon")
        assert "EMAIL_PROVIDER" in provider.missing_config()
        assert provider.get_sender() is None

    def test_warns_only_once_and_never_logs_secret(self, flag_on, monkeypatch, caplog):
        from core.email_brief import provider

        monkeypatch.delenv("EMAIL_FROM")
        with caplog.at_level(logging.WARNING, logger="core.email_brief.provider"):
            assert provider.get_sender() is None
            assert provider.get_sender() is None
            assert provider.get_sender() is None
        warnings = [r for r in caplog.records if "EMAIL_FROM" in r.getMessage()]
        assert len(warnings) == 1
        assert ENV_OK["RESEND_API_KEY"] not in caplog.text

    def test_sender_name_and_postal_address(self, flag_on):
        from core.email_brief import provider

        assert provider.sender_name() == "CryptoMind"
        assert provider.postal_address() == ENV_OK["EMAIL_SENDER_POSTAL_ADDRESS"]


# ─────────────────────────────────────────────────────────────────────────────
# Resend REST（httpx.MockTransport；不打網路）
# ─────────────────────────────────────────────────────────────────────────────


def _msg(**kw):
    from core.email_brief.provider import OutgoingEmail

    base = dict(
        to="danny@example.com",
        subject="Hi",
        html="<p>Hi</p>",
        text="Hi",
        headers={"List-Unsubscribe": "<https://app.example.com/u?token=t>"},
        idempotency_key="daily-brief/abc/2026-09-27",
    )
    base.update(kw)
    return OutgoingEmail(**base)


class TestResendSender:
    async def test_payload_headers_and_auth(self):
        from core.email_brief.provider import ResendSender

        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"id": "email_1"})

        sender = ResendSender(
            "test-resend-key-not-real",
            "CryptoMind <brief@example.com>",
            transport=httpx.MockTransport(handler),
            backoff=0,
        )
        assert await sender.send(_msg()) is True
        req = seen[0]
        assert str(req.url) == "https://api.resend.com/emails"
        assert req.method == "POST"
        assert req.headers["authorization"] == "Bearer test-resend-key-not-real"
        assert req.headers["idempotency-key"] == "daily-brief/abc/2026-09-27"
        import json

        body = json.loads(req.content)
        assert body["from"] == "CryptoMind <brief@example.com>"
        assert body["to"] == ["danny@example.com"]
        assert body["subject"] == "Hi" and body["html"] == "<p>Hi</p>"
        assert body["text"] == "Hi"
        assert body["headers"]["List-Unsubscribe"].startswith("<https://")

    async def test_retries_once_on_5xx(self):
        from core.email_brief.provider import ResendSender

        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(503 if len(calls) == 1 else 200, json={})

        sender = ResendSender(
            "k", "a <a@b.com>", transport=httpx.MockTransport(handler), backoff=0
        )
        assert await sender.send(_msg()) is True
        assert len(calls) == 2

    async def test_client_error_is_not_retried(self):
        from core.email_brief.provider import ResendSender

        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(422, json={"message": "bad"})

        sender = ResendSender(
            "k", "a <a@b.com>", transport=httpx.MockTransport(handler), backoff=0
        )
        assert await sender.send(_msg()) is False
        assert len(calls) == 1

    async def test_network_error_never_raises_and_logs_masked(self, caplog):
        from core.email_brief.provider import ResendSender

        def handler(request):
            raise httpx.ConnectError("boom", request=request)

        sender = ResendSender(
            "k", "a <a@b.com>", transport=httpx.MockTransport(handler), backoff=0
        )
        with caplog.at_level(logging.WARNING):
            assert await sender.send(_msg()) is False
        assert "danny@example.com" not in caplog.text
        assert "d***@e***.com" in caplog.text


# ─────────────────────────────────────────────────────────────────────────────
# 信件內容：跳脫、退訂、實體地址、沒有遠端資源
# ─────────────────────────────────────────────────────────────────────────────


class TestRender:
    def _brief(self, flag_on_env=True, text=None, language="en"):
        from core.email_brief import render

        return render.brief_email(
            to="danny@example.com",
            text=text
            or "☀️ Morning brief 9/27 | <b>D</b>\n\n📊 Your positions\n  BTC  -2.1%",
            language=language,
            on_date="2026-09-27",
            unsubscribe_url="https://app.example.com/api/email/unsubscribe?token=abc&x=1",
            manage_url="https://app.example.com/#settings",
        )

    def test_brief_html_escapes_user_content(self, flag_on):
        msg = self._brief(text='Hi <script>alert(1)</script>\n\nnote & "q"')
        assert "<script>" not in msg.html
        assert "&lt;script&gt;" in msg.html
        assert "&amp;" in msg.html

    def test_brief_has_unsubscribe_postal_sender_and_headers(self, flag_on):
        msg = self._brief()
        assert msg.to == "danny@example.com"
        assert "unsubscribe?token=abc&amp;x=1" in msg.html
        assert ENV_OK["EMAIL_SENDER_POSTAL_ADDRESS"] in msg.html
        assert "CryptoMind" in msg.html
        assert msg.headers["List-Unsubscribe"] == (
            "<https://app.example.com/api/email/unsubscribe?token=abc&x=1>"
        )
        assert msg.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert "https://app.example.com/api/email/unsubscribe?token=abc&x=1" in msg.text
        assert ENV_OK["EMAIL_SENDER_POSTAL_ADDRESS"] in msg.text
        assert '<html lang="en">' in msg.html
        assert msg.idempotency_key and "u_mail" not in msg.idempotency_key

    def test_no_remote_resources_or_tracking(self, flag_on):
        html = self._brief().html
        assert "<img" not in html.lower()
        assert "<script" not in html.lower()
        assert "<link" not in html.lower()
        assert "url(" not in html.lower()

    def test_brief_body_is_localized(self, flag_on):
        msg = self._brief(language="zh-TW")
        assert '<html lang="zh-TW">' in msg.html
        assert "取消訂閱" in msg.html

    def test_confirmation_email(self, flag_on):
        from core.email_brief import render

        msg = render.confirmation_email(
            to="danny@example.com",
            confirm_url="https://app.example.com/api/email/confirm?token=t&a=<b>",
            language="en",
        )
        assert "confirm?token=t&amp;a=&lt;b&gt;" in msg.html
        assert "https://app.example.com/api/email/confirm?token=t&a=<b>" in msg.text
        assert "48" in msg.text
        assert ENV_OK["EMAIL_SENDER_POSTAL_ADDRESS"] in msg.html
        assert "List-Unsubscribe" not in msg.headers

    @pytest.mark.parametrize(
        "outcome", ["confirmed", "expired", "invalid", "unsubscribed", "error"]
    )
    def test_result_pages(self, outcome):
        from core.email_brief import render

        html = render.result_page(outcome, None)
        assert html.startswith("<!doctype html>")
        assert '<html lang="en">' in html
        assert 'name="robots" content="noindex"' in html
        assert "<script" not in html and "<form" not in html

    @pytest.mark.parametrize("outcome", ["confirm_prompt", "unsubscribe_prompt"])
    def test_prompt_pages_post_to_escaped_action(self, outcome):
        from core.email_brief import render

        html = render.result_page(
            outcome, "zh-TW", action_url='/api/email/x?token=a"><script>'
        )
        assert (
            '<form method="post" action="/api/email/x?token=a&quot;&gt;&lt;script&gt;">'
            in html
        )
        assert "<script>" not in html
        assert '<html lang="zh-TW">' in html


# ─────────────────────────────────────────────────────────────────────────────
# 設定端點
# ─────────────────────────────────────────────────────────────────────────────


class TestUserApi:
    def test_get_status_none(self, flag_on):
        client = _client()
        with patch("core.email_brief.store.get_subscription", return_value=None):
            res = client.get("/api/user/email-brief")
        assert res.status_code == 200
        assert res.json()["subscription"] == {
            "status": "none",
            "email": None,
            "confirm_sent_at": None,
        }

    def test_put_stores_pending_and_sends_confirmation(self, flag_on):
        client = _client()
        sender = FakeSender()
        with (
            patch("core.email_brief.provider.get_sender", return_value=sender),
            patch("core.email_brief.store.get_subscription", return_value=None),
            patch(
                "core.email_brief.store.count_recent_requests_for_email",
                return_value=0,
            ),
            patch("core.email_brief.store.upsert_pending") as upsert,
        ):
            res = client.put(
                "/api/user/email-brief",
                json={"email": " Danny@Example.com ", "language": "en"},
            )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["subscription"]["status"] == "pending"
        assert body["subscription"]["email"] == "danny@example.com"
        upsert.assert_called_once()
        user_id, email, confirm_hash, unsub_hash = upsert.call_args.args
        assert (user_id, email) == ("u_mail", "danny@example.com")
        assert len(sender.sent) == 1
        msg = sender.sent[0]
        assert msg.to == "danny@example.com"
        url = re.search(
            r"https://app\.example\.com/api/email/confirm\?token=[\w-]+", msg.text
        )
        assert url, msg.text
        token = _token_from(url.group(0))
        assert confirm_hash == _sha(token)  # 只存 hash
        assert token not in (confirm_hash, unsub_hash)
        from core.email_brief.service import unsubscribe_token

        assert unsub_hash == _sha(unsubscribe_token("u_mail", "danny@example.com"))

    def test_put_rejects_invalid_email(self, flag_on):
        client = _client()
        with (
            patch("core.email_brief.store.upsert_pending") as upsert,
            patch("core.email_brief.provider.get_sender") as gs,
        ):
            res = client.put("/api/user/email-brief", json={"email": "not-an-email"})
        assert res.status_code == 422
        upsert.assert_not_called()
        gs.assert_not_called()

    def test_put_same_verified_email_does_not_resend(self, flag_on):
        client = _client()
        sender = FakeSender()
        with (
            patch("core.email_brief.provider.get_sender", return_value=sender),
            patch("core.email_brief.store.get_subscription", return_value=_sub_row()),
            patch("core.email_brief.store.upsert_pending") as upsert,
        ):
            res = client.put(
                "/api/user/email-brief", json={"email": "DANNY@example.com"}
            )
        assert res.status_code == 200
        assert res.json()["subscription"]["status"] == "active"
        assert sender.sent == []
        upsert.assert_not_called()

    def test_put_throttles_address_targeted_by_other_accounts(self, flag_on):
        client = _client()
        sender = FakeSender()
        with (
            patch("core.email_brief.provider.get_sender", return_value=sender),
            patch("core.email_brief.store.get_subscription", return_value=None),
            patch(
                "core.email_brief.store.count_recent_requests_for_email",
                return_value=3,
            ),
            patch("core.email_brief.store.upsert_pending") as upsert,
        ):
            res = client.put(
                "/api/user/email-brief", json={"email": "victim@example.com"}
            )
        assert res.status_code == 429
        # 訊息不能透露「這個地址被別的帳號用過」（列舉 oracle）
        assert "address" not in res.json()["detail"].lower()
        assert sender.sent == []
        upsert.assert_not_called()

    def test_put_fails_closed_when_not_configured(self, flag_on, monkeypatch):
        monkeypatch.delenv("EMAIL_SENDER_POSTAL_ADDRESS")
        client = _client()
        with patch("core.email_brief.store.upsert_pending") as upsert:
            res = client.put("/api/user/email-brief", json={"email": "a@example.com"})
        # 旗標預設開之後：設定不齊＝端點不開放（跟 /api/config 不顯示 Email 區塊一致），不存任何東西
        assert res.status_code == 404
        upsert.assert_not_called()

    def test_put_send_failure_is_502_and_row_stays_pending(self, flag_on):
        client = _client()
        with (
            patch(
                "core.email_brief.provider.get_sender",
                return_value=FakeSender(ok=False),
            ),
            patch("core.email_brief.store.get_subscription", return_value=None),
            patch(
                "core.email_brief.store.count_recent_requests_for_email",
                return_value=0,
            ),
            patch("core.email_brief.store.upsert_pending") as upsert,
        ):
            res = client.put("/api/user/email-brief", json={"email": "a@example.com"})
        assert res.status_code == 502
        upsert.assert_called_once()

    def test_put_is_rate_limited_3_per_hour(self, flag_on):
        client = _client()
        with (
            patch("core.email_brief.provider.get_sender", return_value=FakeSender()),
            patch("core.email_brief.store.get_subscription", return_value=None),
            patch(
                "core.email_brief.store.count_recent_requests_for_email",
                return_value=0,
            ),
            patch("core.email_brief.store.upsert_pending"),
        ):
            codes = [
                client.put(
                    "/api/user/email-brief", json={"email": f"a{i}@example.com"}
                ).status_code
                for i in range(4)
            ]
        assert codes == [200, 200, 200, 429]

    def test_delete_removes_row(self, flag_on):
        client = _client()
        with patch(
            "core.email_brief.store.delete_for_user", return_value=True
        ) as delete:
            res = client.delete("/api/user/email-brief")
        assert res.status_code == 200
        assert res.json() == {"success": True, "removed": True}
        delete.assert_called_once_with("u_mail")

    def test_delete_also_drops_email_channel_in_same_transaction(self):
        """移除 email 後早報偏好不能殘留 'email' 頻道（重新整理後會被隱藏的勾選框存回去）。"""
        from core.email_brief import store

        executed = []

        class _Cursor:
            rowcount = 1

            def execute(self, sql, params=None):
                executed.append((" ".join(sql.split()), params))

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _Conn:
            def cursor(self):
                return _Cursor()

        class _Tx:
            def __enter__(self):
                return _Conn()

            def __exit__(self, *exc):
                return False

        with patch("core.email_brief.store.transaction", return_value=_Tx()):
            assert store.delete_for_user("u_mail") is True
        sqls = [s for s, _ in executed]
        assert any(s.startswith("DELETE FROM user_email_subscriptions") for s in sqls)
        assert any(
            "array_remove(channels, 'email')" in s
            and s.startswith("UPDATE user_brief_prefs")
            for s in sqls
        )
        assert all(p == ("u_mail",) for _, p in executed)

    def test_logs_mask_email(self, flag_on, caplog):
        client = _client()
        with (
            caplog.at_level(logging.INFO),
            patch("core.email_brief.provider.get_sender", return_value=FakeSender()),
            patch("core.email_brief.store.get_subscription", return_value=None),
            patch(
                "core.email_brief.store.count_recent_requests_for_email",
                return_value=0,
            ),
            patch("core.email_brief.store.upsert_pending"),
        ):
            client.put("/api/user/email-brief", json={"email": "secret.person@corp.io"})
        assert "secret.person@corp.io" not in caplog.text
        assert "s***@c***.io" in caplog.text


class TestStatus:
    @pytest.mark.parametrize(
        "row,expected",
        [
            (None, "none"),
            (
                _sub_row(
                    verified_at=None,
                    confirm_token_hash="h",
                    confirm_sent_at=NOW - timedelta(hours=1),
                ),
                "pending",
            ),
            (
                _sub_row(
                    verified_at=None,
                    confirm_token_hash="h",
                    confirm_sent_at=NOW - timedelta(hours=49),
                ),
                "expired",
            ),
            (_sub_row(), "active"),
            (_sub_row(unsubscribed_at=NOW), "unsubscribed"),
        ],
    )
    def test_subscription_status(self, row, expected):
        from core.email_brief.service import subscription_status

        assert subscription_status(row, NOW)["status"] == expected


# ─────────────────────────────────────────────────────────────────────────────
# 確認連結：單次、無效、過期
# ─────────────────────────────────────────────────────────────────────────────


class TestConfirm:
    """GET 只顯示一顆按鈕（信箱安全掃描器會預先點開信裡的連結，GET 不能改狀態）；POST 才確認。"""

    TOKEN = "t" * 43

    def _pending(self, hours_ago=1):
        return _sub_row(
            verified_at=None,
            confirm_token_hash=_sha(self.TOKEN),
            confirm_sent_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        )

    def test_get_only_shows_button_and_never_touches_db(self, flag_on):
        client = _client()
        with (
            patch("core.email_brief.store.find_by_confirm_hash") as find,
            patch("core.email_brief.store.mark_verified") as mark,
        ):
            res = client.get("/api/email/confirm", params={"token": self.TOKEN})
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        assert (
            '<form method="post" action="/api/email/confirm?token=' + self.TOKEN
            in res.text
        )
        # no-referrer 會讓表單 POST 的 Origin 變 null、被 CSRF 檢查擋掉
        assert res.headers["referrer-policy"] == "same-origin"
        assert "no-store" in res.headers["cache-control"]
        find.assert_not_called()
        mark.assert_not_called()

    def test_post_verifies_once(self, flag_on):
        client = _client()
        with (
            patch(
                "core.email_brief.store.find_by_confirm_hash",
                return_value=self._pending(),
            ) as find,
            patch("core.email_brief.store.mark_verified", return_value=True) as mark,
        ):
            res = client.post("/api/email/confirm", params={"token": self.TOKEN})
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        find.assert_called_once_with(_sha(self.TOKEN))
        assert mark.call_args.args[:2] == ("u_mail", _sha(self.TOKEN))

    def test_used_or_unknown_token_is_invalid(self, flag_on):
        client = _client()
        with (
            patch("core.email_brief.store.find_by_confirm_hash", return_value=None),
            patch("core.email_brief.store.mark_verified") as mark,
        ):
            res = client.post("/api/email/confirm", params={"token": self.TOKEN})
        assert res.status_code == 400
        mark.assert_not_called()

    def test_race_second_use_is_invalid(self, flag_on):
        """同一個 token 同時點兩次：UPDATE 只會成功一次。"""
        from core.email_brief import service

        with (
            patch(
                "core.email_brief.store.find_by_confirm_hash",
                return_value=self._pending(),
            ),
            patch("core.email_brief.store.mark_verified", return_value=False),
        ):
            assert service.confirm(self.TOKEN)[0] == "invalid"

    def test_expired_token(self, flag_on):
        client = _client()
        with (
            patch(
                "core.email_brief.store.find_by_confirm_hash",
                return_value=self._pending(hours_ago=49),
            ),
            patch("core.email_brief.store.mark_verified") as mark,
        ):
            res = client.post("/api/email/confirm", params={"token": self.TOKEN})
        assert res.status_code == 410
        mark.assert_not_called()

    @pytest.mark.parametrize("bad", ["", "short", "x" * 300, "a" * 42 + "$"])
    def test_malformed_token_never_hits_db(self, flag_on, bad):
        client = _client()
        with patch("core.email_brief.store.find_by_confirm_hash") as find:
            get = client.get("/api/email/confirm", params={"token": bad})
            post = client.post("/api/email/confirm", params={"token": bad})
        assert get.status_code == post.status_code == 400
        assert "<form" not in get.text
        find.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 退訂：免登入、冪等、一鍵 POST；GET 只顯示按鈕（掃描器預先點開不會把人退訂掉）
# ─────────────────────────────────────────────────────────────────────────────


class TestUnsubscribe:
    TOKEN = "u" * 43

    def _anon_client(self):
        """不覆寫 get_current_user：證明退訂不需要登入。"""
        from api.routers import email_brief as mod

        app = FastAPI()
        app.include_router(mod.public_router)
        return TestClient(app)

    def test_get_only_shows_button(self, flag_on):
        client = self._anon_client()
        with patch("core.email_brief.store.unsubscribe_by_hash") as unsub:
            res = client.get("/api/email/unsubscribe", params={"token": self.TOKEN})
        assert res.status_code == 200
        assert (
            '<form method="post" action="/api/email/unsubscribe?token=' + self.TOKEN
            in res.text
        )
        assert res.headers["referrer-policy"] == "same-origin"
        unsub.assert_not_called()

    def test_post_unsubscribes_without_login_and_is_idempotent(self, flag_on):
        client = self._anon_client()
        with patch(
            "core.email_brief.store.unsubscribe_by_hash",
            return_value={"user_id": "u_mail", "language": "zh-TW"},
        ) as unsub:
            first = client.post("/api/email/unsubscribe", params={"token": self.TOKEN})
            second = client.post("/api/email/unsubscribe", params={"token": self.TOKEN})
        assert first.status_code == second.status_code == 200
        assert "已取消訂閱" in first.text
        assert unsub.call_count == 2

    def test_one_click_post(self, flag_on):
        """RFC 8058：信箱服務商直接 POST 到 List-Unsubscribe 網址（body 是 List-Unsubscribe=One-Click）。"""
        client = self._anon_client()
        with patch(
            "core.email_brief.store.unsubscribe_by_hash",
            return_value={"user_id": "u_mail", "language": "en"},
        ) as unsub:
            res = client.post(
                "/api/email/unsubscribe",
                params={"token": self.TOKEN},
                data={"List-Unsubscribe": "One-Click"},
            )
        assert res.status_code == 200
        unsub.assert_called_once_with(_sha(self.TOKEN))

    def test_unknown_token(self, flag_on):
        client = self._anon_client()
        with patch("core.email_brief.store.unsubscribe_by_hash", return_value=None):
            res = client.post("/api/email/unsubscribe", params={"token": self.TOKEN})
        assert res.status_code == 400

    def test_malformed_token_gets_no_button(self, flag_on):
        client = self._anon_client()
        res = client.get("/api/email/unsubscribe", params={"token": "short"})
        assert res.status_code == 400 and "<form" not in res.text

    def test_sql_keeps_first_unsubscribe_time(self):
        src = (REPO / "core" / "email_brief" / "store.py").read_text(encoding="utf-8")
        assert "unsubscribed_at = COALESCE(unsubscribed_at, NOW())" in src


# ─────────────────────────────────────────────────────────────────────────────
# 早報送出：只送已確認且沒退訂的人
# ─────────────────────────────────────────────────────────────────────────────


class TestDelivery:
    def _active(self):
        from core.email_brief.service import hash_token, unsubscribe_token

        return _sub_row(
            unsubscribe_token_hash=hash_token(
                unsubscribe_token("u_mail", "danny@example.com")
            )
        )

    async def test_sends_to_verified_subscriber(self, flag_on):
        from core.email_brief import service

        sender = FakeSender()
        with (
            patch("core.email_brief.provider.get_sender", return_value=sender),
            patch("core.email_brief.store.get_active", return_value=self._active()),
            patch("core.email_brief.store.set_unsubscribe_hash") as rehash,
        ):
            ok = await service.send_brief_email(
                "u_mail", "en", "☀️ Morning brief\n\nBTC -2%", on_date="2026-09-27"
            )
        assert ok is True
        rehash.assert_not_called()
        msg = sender.sent[0]
        assert msg.to == "danny@example.com"
        unsub = msg.headers["List-Unsubscribe"].strip("<>")
        assert unsub.startswith("https://app.example.com/api/email/unsubscribe?token=")
        assert _sha(_token_from(unsub)) == self._active()["unsubscribe_token_hash"]

    async def test_idempotency_key_is_per_user_and_day(self, flag_on):
        """兩個帳號填同一個信箱：服務商去重鍵不能撞（否則第二份被當重送吃掉）。"""
        from core.email_brief import service

        sender = FakeSender()
        with (
            patch("core.email_brief.provider.get_sender", return_value=sender),
            patch("core.email_brief.store.get_active", return_value=self._active()),
            patch("core.email_brief.store.set_unsubscribe_hash"),
        ):
            await service.send_brief_email("u_mail", "en", "x", on_date="2026-09-27")
            await service.send_brief_email("u_other", "en", "x", on_date="2026-09-27")
            await service.send_brief_email("u_mail", "en", "x", on_date="2026-09-28")
        keys = [m.idempotency_key for m in sender.sent]
        assert len(set(keys)) == 3
        assert all("u_mail" not in k and "danny" not in k for k in keys)

    async def test_no_active_subscription_no_send(self, flag_on):
        from core.email_brief import service

        sender = FakeSender()
        with (
            patch("core.email_brief.provider.get_sender", return_value=sender),
            patch("core.email_brief.store.get_active", return_value=None),
        ):
            assert (
                await service.send_brief_email("u_mail", "en", "x", on_date="d")
                is False
            )
        assert sender.sent == []

    async def test_missing_env_no_send_and_no_db(self, flag_on, monkeypatch):
        from core.email_brief import service

        monkeypatch.delenv("RESEND_API_KEY")
        with patch("core.email_brief.store.get_active") as get_active:
            assert (
                await service.send_brief_email("u_mail", "en", "x", on_date="d")
                is False
            )
        get_active.assert_not_called()

    async def test_rotated_secret_rehashes_so_new_link_works(self, flag_on):
        from core.email_brief import service

        sender = FakeSender()
        with (
            patch("core.email_brief.provider.get_sender", return_value=sender),
            patch(
                "core.email_brief.store.get_active",
                return_value=_sub_row(unsubscribe_token_hash="stale" * 12),
            ),
            patch("core.email_brief.store.set_unsubscribe_hash") as rehash,
        ):
            assert await service.send_brief_email("u_mail", "en", "x", on_date="d")
        new_token = _token_from(sender.sent[0].headers["List-Unsubscribe"].strip("<>"))
        rehash.assert_called_once_with("u_mail", _sha(new_token))

    async def test_never_raises_into_cron(self, flag_on):
        from core.email_brief import service

        with (
            patch("core.email_brief.provider.get_sender", return_value=FakeSender()),
            patch(
                "core.email_brief.store.get_active", side_effect=RuntimeError("db down")
            ),
        ):
            assert (
                await service.send_brief_email("u_mail", "en", "x", on_date="d")
                is False
            )

    def test_annotate_rows_marks_active_users(self, flag_on):
        from core.email_brief import service

        rows = [{"user_id": "u1"}, {"user_id": "u2"}]
        with patch("core.email_brief.store.active_user_ids", return_value={"u2"}):
            out = service.annotate_rows(rows)
        assert [r["email_active"] for r in out] == [False, True]
        assert "email_active" not in rows[0]  # 不改傳入的 row

    def test_annotate_rows_survives_db_error(self, flag_on):
        from core.email_brief import service

        rows = [{"user_id": "u1"}]
        with patch(
            "core.email_brief.store.active_user_ids", side_effect=RuntimeError("x")
        ):
            assert service.annotate_rows(rows) is rows


class TestScheduleCountsEmail:
    def _row(self, **kw):
        base = {
            "user_id": "u1",
            "language": "en",
            "display_name": "D",
            "membership_tier": "free",
            "telegram_id": None,
            "prefs_user_id": "u1",
            "user_set": True,
            "enabled": True,
            "send_hour": 8,
            "timezone": "UTC",
            "channels": ["email"],
            "include_spend": True,
            "last_sent_on": None,
        }
        base.update(kw)
        return base

    def test_email_is_a_valid_but_not_default_channel(self):
        from core.daily_brief.schedule import DEFAULT_CHANNELS, VALID_CHANNELS

        assert "email" in VALID_CHANNELS
        assert "email" not in DEFAULT_CHANNELS

    def test_active_email_makes_user_due(self):
        from core.daily_brief.schedule import effective_prefs, is_due

        at8 = datetime(2026, 9, 27, 8, 5, tzinfo=timezone.utc)
        prefs = effective_prefs(self._row(email_active=True))
        assert prefs.channels == ("email",) and prefs.email_active is True
        assert is_due(prefs, at8) is True

    def test_inactive_email_is_not_a_channel(self):
        from core.daily_brief.schedule import effective_prefs, is_due

        at8 = datetime(2026, 9, 27, 8, 5, tzinfo=timezone.utc)
        assert is_due(effective_prefs(self._row()), at8) is False
        assert is_due(effective_prefs(self._row(email_active=False)), at8) is False


@pytest.fixture
def claim_ok():
    """不打 DB：今天這一份一律搶得到（搶占本身在 test_daily_brief_cron 驗）"""
    with (
        patch("core.daily_brief.store.claim_day", return_value=True),
        patch("core.daily_brief.store.release_day"),
    ):
        yield


@pytest.mark.usefixtures("claim_ok")
class TestCronEmailBranch:
    def _prefs(self, **kw):
        from core.daily_brief.schedule import effective_prefs

        row = {
            "user_id": "u1",
            "language": "en",
            "display_name": "D",
            "membership_tier": "free",
            "telegram_id": 111,
            "prefs_user_id": "u1",
            "user_set": True,
            "enabled": True,
            "send_hour": 8,
            "timezone": "UTC",
            "channels": ["telegram", "email"],
            "include_spend": True,
            "last_sent_on": None,
        }
        row.update(kw)
        return effective_prefs(row)

    def _data(self):
        from core.daily_brief.compose import BriefData

        return BriefData(
            language="en",
            date_local=NOW.date(),
            display_name="D",
            alerts=[{"title": "x", "body": "BTC < 60,000"}],
        )

    async def test_email_gets_brief_without_telegram_reply_hint(self):
        from core.i18n import t
        from scripts.cron_daily_brief import send_one

        hint = t("ui_messages.daily_brief.reply_hint", "en")
        tg = AsyncMock(return_value=True)
        email = AsyncMock(return_value=True)
        with (
            patch(
                "core.daily_brief.collect.collect_brief_data",
                new=AsyncMock(return_value=self._data()),
            ),
            patch(
                "core.daily_brief.insight.generate_insight",
                new=AsyncMock(return_value=None),
            ),
            patch("core.daily_brief.send.send_telegram_text", new=tg),
            patch("core.email_brief.service.send_brief_email", new=email),
            patch("core.daily_brief.store.mark_sent"),
        ):
            assert await send_one(self._prefs(email_active=True), NOW) == "sent"
        assert hint in tg.call_args.args[1]
        email.assert_awaited_once()
        assert email.call_args.args[0] == "u1"
        assert "BTC < 60,000" in email.call_args.args[2]
        assert hint not in email.call_args.args[2]

    async def test_inactive_email_is_skipped(self):
        from scripts.cron_daily_brief import send_one

        email = AsyncMock(return_value=True)
        with (
            patch(
                "core.daily_brief.collect.collect_brief_data",
                new=AsyncMock(return_value=self._data()),
            ),
            patch(
                "core.daily_brief.insight.generate_insight",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "core.daily_brief.send.send_telegram_text",
                new=AsyncMock(return_value=True),
            ),
            patch("core.email_brief.service.send_brief_email", new=email),
            patch("core.daily_brief.store.mark_sent"),
        ):
            await send_one(self._prefs(email_active=False), NOW)
        email.assert_not_awaited()

    async def test_run_annotates_rows_with_email_status(self, flag_on):
        from scripts import cron_daily_brief as mod

        row = {
            "user_id": "u1",
            "language": "en",
            "display_name": "D",
            "membership_tier": "free",
            "telegram_id": None,
            "prefs_user_id": "u1",
            "user_set": True,
            "enabled": True,
            "send_hour": 0,
            "timezone": "UTC",
            "channels": ["email"],
            "include_spend": True,
            "last_sent_on": None,
        }
        seen = []

        async def fake_send_one(prefs, now_utc, dry_run=False, force=False):
            seen.append(prefs)
            return "sent"

        with (
            patch("core.daily_brief.store.list_candidates", return_value=[row]),
            patch("core.email_brief.store.active_user_ids", return_value={"u1"}),
            patch.object(mod, "send_one", new=fake_send_one),
        ):
            summary = await mod.run(NOW)
        assert summary["due"] == 1 and seen[0].email_active is True


# ─────────────────────────────────────────────────────────────────────────────
# Schema／migration／登記處
# ─────────────────────────────────────────────────────────────────────────────


def _ddl(src: str) -> str:
    m = re.search(
        r"CREATE TABLE IF NOT EXISTS user_email_subscriptions \((.*?)\n\s*\)\s*\"\"\"",
        src,
        re.S,
    )
    assert m, "找不到 user_email_subscriptions DDL"
    return re.sub(r"\s+", " ", m.group(1)).strip()


class TestSchemaAndMigration:
    MIG = REPO / "alembic" / "versions" / "c053_user_email_subscriptions.py"

    def test_migration_chain_and_downgrade(self):
        src = self.MIG.read_text(encoding="utf-8")
        assert 'revision = "c053"' in src and 'down_revision = "c052"' in src
        assert "DROP TABLE IF EXISTS user_email_subscriptions" in src
        assert "op.create_table" not in src  # 冪等：reconcile 也會建同一張表

    def test_single_head(self):
        downs = []
        for f in (REPO / "alembic" / "versions").glob("c05*.py"):
            m = re.search(
                r'^down_revision = "(\w+)"', f.read_text(encoding="utf-8"), re.M
            )
            if m:
                downs.append(m.group(1))
        assert downs.count("c052") == 1, (
            "c052 之後只能有一個 migration（兩個 head 會卡部署）"
        )

    def test_schema_ddl_matches_migration(self):
        schema = (REPO / "core" / "database" / "schema.py").read_text(encoding="utf-8")
        assert (
            '("user_email_subscriptions", create_user_email_subscriptions_table)'
            in schema
        )
        mig = self.MIG.read_text(encoding="utf-8")
        assert _ddl(schema) == _ddl(mig)
        ddl = _ddl(mig)
        assert "user_id TEXT PRIMARY KEY" in ddl
        assert "REFERENCES users(user_id) ON DELETE CASCADE" in ddl
        for col in (
            "email TEXT NOT NULL",
            "confirm_token_hash TEXT",
            "confirm_sent_at TIMESTAMPTZ",
            "verified_at TIMESTAMPTZ",
            "unsubscribe_token_hash TEXT",
            "unsubscribed_at TIMESTAMPTZ",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        ):
            assert col in ddl, col

    def test_env_example_documents_every_new_var(self):
        text = (REPO / ".env.example").read_text(encoding="utf-8")
        for name in list(ENV_OK) + ["EMAIL_PROVIDER"]:
            assert name in text, name

    def test_flag_registered_default_on(self):
        from core.feature_flags import FLAG_REGISTRY

        getter, default, _ = FLAG_REGISTRY["EMAIL_BRIEF_ENABLED"]
        assert default == "**on**" and getter() is True


class TestExplicitlyOff:
    """寄信設定齊全，但 env 明確設 false（一鍵關）→ 全部關閉，退訂仍可用。"""

    @pytest.fixture(autouse=True)
    def _configured_but_off(self, monkeypatch):
        for k, v in ENV_OK.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("EMAIL_BRIEF_ENABLED", "false")

    def test_endpoints_are_404(self):
        client = _client()
        assert client.get("/api/user/email-brief").status_code == 404
        assert client.get("/api/email/confirm", params={"token": "a" * 43}).status_code == 404

    def test_not_available_and_no_sender(self):
        from core.email_brief import provider

        assert provider.email_brief_available() is False
        assert provider.get_sender() is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
def test_settings_send_button_shows_verified():
    """已驗證的同一個信箱：「寄確認信」反灰顯示「已驗證」、點了不送；換信箱才恢復。"""
    out = subprocess.run(
        ["node", "tests/js/email_brief_verified.mjs"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, (out.stderr or out.stdout)[-3000:]
