"""
Unit tests for Telegram link token generation/verification.

Covers the HMAC-signed, stateless link token used to bind a Telegram
account to an existing platform user. Security-critical: tokens must
not be forgeable, re-targetable, or replayable after expiry.
"""

import time
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_env_vars():
    with patch.dict(
        "os.environ",
        {
            "JWT_SECRET_KEY": "test-secret-key-at-least-32-chars-long!",
            "ENVIRONMENT": "development",
            "BOT_INTERNAL_SECRET": "",
        },
    ):
        yield


@pytest.mark.unit
class TestLinkTokenRoundTrip:
    def test_generate_then_verify_recovers_user_id(self):
        from api.routers.telegram_link import generate_link_token, verify_link_token

        user_id = "UQSomeWalletAddress123"
        token = generate_link_token(user_id)

        result = verify_link_token(token)
        assert result is not None
        recovered_uid, exp_ts = result
        assert recovered_uid == user_id
        assert exp_ts > int(time.time())

    def test_token_for_different_users_are_distinct(self):
        from api.routers.telegram_link import generate_link_token

        t1 = generate_link_token("user-alpha")
        t2 = generate_link_token("user-beta")
        assert t1 != t2

    def test_unicode_user_id_roundtrip(self):
        from api.routers.telegram_link import generate_link_token, verify_link_token

        user_id = "wallet_with_special_chars!@#$%/\\"
        token = generate_link_token(user_id)
        result = verify_link_token(token)
        assert result is not None
        assert result[0] == user_id


@pytest.mark.unit
class TestLinkTokenSecurity:
    def test_tampered_signature_rejected(self):
        from api.routers.telegram_link import generate_link_token, verify_link_token

        token = generate_link_token("user-x")
        parts = token.split(".")
        parts[3] = "a" * 64
        tampered = ".".join(parts)

        assert verify_link_token(tampered) is None

    def test_tampered_user_id_rejected(self):
        from api.routers.telegram_link import generate_link_token, verify_link_token

        token = generate_link_token("user-original")
        parts = token.split(".")
        import base64

        fake_uid = base64.urlsafe_b64encode(b"user-fake").decode().rstrip("=")
        parts[0] = fake_uid
        tampered = ".".join(parts)

        assert verify_link_token(tampered) is None

    def test_expired_token_rejected(self):
        import base64
        import hashlib
        import hmac
        import secrets

        from api.routers.telegram_link import verify_link_token

        secret = "test-secret-key-at-least-32-chars-long!"
        user_id = "expired-user"
        b64uid = base64.urlsafe_b64encode(user_id.encode()).decode().rstrip("=")
        exp_ts = int(time.time()) - 10
        nonce = secrets.token_hex(8)
        payload = f"{b64uid}.{exp_ts}.{nonce}"
        sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        token = f"{payload}.{sig}"

        assert verify_link_token(token) is None

    def test_malformed_token_rejected(self):
        from api.routers.telegram_link import verify_link_token

        assert verify_link_token("") is None
        assert verify_link_token("not.enough.parts") is None
        assert verify_link_token("a.b.c.d.e") is None
        assert verify_link_token("garbage") is None

    def test_token_with_wrong_secret_rejected(self):
        from api.routers.telegram_link import generate_link_token

        token = generate_link_token("user-y")
        with patch.dict(
            "os.environ", {"JWT_SECRET_KEY": "different-secret-key-also-32-chars!"}
        ):
            from importlib import reload

            import api.routers.telegram_link as mod

            reload(mod)
            assert mod.verify_link_token(token) is None


@pytest.mark.unit
class TestLinkTokenFormat:
    def test_token_has_four_parts(self):
        from api.routers.telegram_link import generate_link_token

        token = generate_link_token("format-user")
        assert token.count(".") == 3

    def test_token_expires_in_five_minutes(self):
        from api.routers.telegram_link import (
            LINK_TOKEN_TTL_SECONDS,
            generate_link_token,
        )

        assert LINK_TOKEN_TTL_SECONDS == 300

        token = generate_link_token("ttl-user")
        exp_ts = int(token.split(".")[1])
        now = int(time.time())
        assert exp_ts - now <= LINK_TOKEN_TTL_SECONDS + 2
        assert exp_ts - now >= LINK_TOKEN_TTL_SECONDS - 2
