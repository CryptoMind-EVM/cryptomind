"""Telegram 一鍵綁定（PR-7）：deep link ``t.me/<bot>?start=link_<code>``＋確認步驟。

守住：
1. start 參數只收 [A-Za-z0-9_-]、最多 64 字——短碼＋前綴要放得進去
2. ``/start link_<code>`` 與 ``/link <token>`` 都**不直接綁定**：先 link-preview 建一筆待確認，
   bot 顯示「要綁到誰」＋警告＋[確認][取消]；只有同一個 Telegram 使用者按確認才綁
   （攻擊者把自己帳號的連結丟給受害者 → 受害者的 Telegram 被綁走 → 攻擊者在網頁讀到
   受害者的 bot 對話；web↔Telegram 共用 current_session_id）
3. 確認時才消耗短碼（一次性、5 分鐘）；取消作廢；過期、重用、別人按確認都擋
4. 這支 Telegram 已綁在別的帳號 → 確認文字明講「從 A 移到 B」
5. 綁定後回報早報狀態：沒偏好列＝綁 TG 就開；有明確偏好就照偏好（不覆寫）
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

START_PAYLOAD_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TARGET_ID = "evm_0x12aa000000000000000000000000000000000cab"
OTHER_ID = "evm_0x9800000000000000000000000000000000000dcd"
USER = {"user_id": TARGET_ID, "username": "EVM_12aa00"}
USERS = {
    TARGET_ID: {"user_id": TARGET_ID, "username": "EVM_12aa00", "is_active": True},
    OTHER_ID: {"user_id": OTHER_ID, "username": "EVM_980000", "is_active": True},
}
NAMES = {TARGET_ID: "Danny", OTHER_ID: "Bob"}
TG = 555
OTHER_TG = 999


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-at-least-32-chars-long!")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "@getcryptomind_bot")
    from api.middleware.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()


class FakeCache:
    """共用快取的替身：跟 core.database.cache 一樣，TTL 不保證（DB 那層不吃 TTL）。"""

    def __init__(self):
        self.data: dict = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ttl=None):
        self.data[key] = value

    def consume(self, key):
        return self.data.pop(key, None) is not None


@pytest.fixture
def cache():
    fake = FakeCache()
    with (
        patch("api.routers.telegram_link.get_cache", side_effect=fake.get),
        patch("api.routers.telegram_link.set_cache", side_effect=fake.set),
        patch("api.routers.telegram_link.consume_cache", side_effect=fake.consume),
        patch("api.routers.telegram_link.purge_cache_prefix", return_value=0),
    ):
        yield fake


class FakeDb:
    """綁定表＋使用者表的替身。"""

    def __init__(self, bindings=None, prefs_row=None):
        self.bindings: dict = dict(bindings or {})  # telegram_id -> user_id
        self.prefs_row = prefs_row
        self.bind_calls: list = []

    def binding_by_tg(self, telegram_id):
        uid = self.bindings.get(telegram_id)
        return {"telegram_id": telegram_id, "user_id": uid} if uid else None

    def create_binding(self, telegram_id, user_id, username=None, first_name=None):
        self.bind_calls.append((telegram_id, user_id))
        self.bindings[telegram_id] = user_id
        return True


@pytest.fixture
def db():
    fake = FakeDb()
    with (
        patch("api.routers.telegram_link.get_user_by_id", side_effect=USERS.get),
        patch("api.routers.telegram_link.get_user_display_name", side_effect=NAMES.get),
        patch(
            "api.routers.telegram_link.get_binding_by_telegram_id",
            side_effect=fake.binding_by_tg,
        ),
        patch(
            "api.routers.telegram_link.create_telegram_binding",
            side_effect=fake.create_binding,
        ),
        patch(
            "core.daily_brief.store.get_prefs_row",
            side_effect=lambda uid: fake.prefs_row or _prefs_row(user_id=uid),
        ),
    ):
        yield fake


def _client():
    from api.routers import telegram_link as tl

    app = FastAPI()
    app.include_router(tl.router)

    async def fake_user():
        return USER

    app.dependency_overrides[tl.get_current_user] = fake_user
    return TestClient(app)


def _prefs_row(**kw):
    base = {
        "user_id": TARGET_ID,
        "language": "en",
        "display_name": None,
        "membership_tier": "free",
        "telegram_id": TG,
        "prefs_user_id": None,
        # c063：只有使用者自己設定過（user_set）才算明確偏好；mark_sent 自動建的列不算
        "user_set": False,
        "enabled": None,
        "send_hour": None,
        "timezone": None,
        "channels": None,
        "include_spend": None,
        "include_macro": None,
        "last_sent_on": None,
    }
    base.update(kw)
    return base


def _issue(client) -> dict:
    res = client.post("/api/telegram/link-token", json={})
    assert res.status_code == 200, res.text
    return res.json()


def _code_from(deep_link: str) -> str:
    payload = deep_link.split("?start=", 1)[1]
    assert payload.startswith("link_")
    return payload[len("link_") :]


def _preview(client, token: str, telegram_id: int = TG):
    return client.post(
        "/api/telegram/link-preview",
        json={"token": token, "telegram_id": telegram_id},
    )


def _confirm(client, pending_id: str, telegram_id: int = TG):
    return client.post(
        "/api/telegram/verify-link",
        json={
            "pending_id": pending_id,
            "telegram_id": telegram_id,
            "username": "tg_user",
        },
    )


def _cancel(client, pending_id: str, telegram_id: int = TG):
    return client.post(
        "/api/telegram/link-cancel",
        json={"pending_id": pending_id, "telegram_id": telegram_id},
    )


def _pending_for(client, token: str, telegram_id: int = TG) -> dict:
    res = _preview(client, token, telegram_id)
    assert res.status_code == 200, res.text
    return res.json()


# ── link-token 回 deep link ─────────────────────────────────────────────────


class TestDeepLinkFormat:
    def test_link_token_returns_deep_link_with_valid_start_payload(self, cache):
        data = _issue(_client())
        link = data["deep_link"]
        assert link.startswith("https://t.me/getcryptomind_bot?start=link_")
        payload = link.split("?start=", 1)[1]
        # Telegram 的 start 參數：只收 A-Z a-z 0-9 _ -，最多 64 字
        assert START_PAYLOAD_RE.fullmatch(payload), payload
        # 手動 /link 的完整 token 照舊回傳（備援）
        assert data["token"].count(".") == 3

    def test_short_code_maps_to_the_full_token_in_cache(self, cache):
        data = _issue(_client())
        code = _code_from(data["deep_link"])
        stored = cache.data[f"tglink_start:{code}"]
        assert stored == {"t": data["token"]}

    def test_codes_are_unique_per_issue(self, cache):
        client = _client()
        a = _code_from(_issue(client)["deep_link"])
        b = _code_from(_issue(client)["deep_link"])
        assert a != b

    def test_no_bot_username_means_no_deep_link(self, cache, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "")
        data = _issue(_client())
        assert data["deep_link"] is None
        assert data["token"]  # 手動指令照常可用

    def test_cache_failure_degrades_to_manual_link(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("cache down")

        with (
            patch("api.routers.telegram_link.set_cache", side_effect=boom),
            patch("api.routers.telegram_link.purge_cache_prefix", return_value=0),
        ):
            data = _issue(_client())
        assert data["deep_link"] is None and data["token"]

    def test_issue_prunes_stale_link_rows_best_effort(self, cache):
        """DB 那層快取不吃 TTL：每次發碼順手清掉 10 分鐘前的短碼、待確認與 nonce 列。"""
        with patch("api.routers.telegram_link.purge_cache_prefix") as purge:
            _issue(_client())
        prefixes = sorted(c.args[0] for c in purge.call_args_list)
        assert prefixes == ["tglink_pending:", "tglink_start:", "tglink_used:"]
        assert all(c.args[1] >= 600 for c in purge.call_args_list)

        with patch(
            "api.routers.telegram_link.purge_cache_prefix",
            side_effect=RuntimeError("db"),
        ):
            data = _issue(_client())  # 清不掉也照發
        assert data["deep_link"]

    def test_purge_cache_prefix_is_parameterized_and_escapes_like(self):
        from core.database import cache as cache_mod

        with patch.object(cache_mod.DatabaseBase, "execute", return_value=3) as ex:
            assert cache_mod.purge_cache_prefix("tglink_start:", 600) == 3
        sql, params = ex.call_args.args
        assert "LIKE %s" in sql and "updated_at <" in sql and "tglink" not in sql
        assert params == ("tglink\\_start:%", 600)

    def test_build_deep_link_rejects_payload_over_64(self):
        from api.routers.telegram_link import build_deep_link

        assert build_deep_link("bot", "a" * 59) is not None  # link_ + 59 = 64
        assert build_deep_link("bot", "a" * 60) is None
        assert build_deep_link("", "abc") is None
        assert build_deep_link("bot", "bad.code") is None


# ── 預覽：不綁定、說清楚要綁給誰 ─────────────────────────────────────────


class TestPreview:
    def test_start_code_preview_does_not_bind(self, cache, db):
        client = _client()
        code = _code_from(_issue(client)["deep_link"])
        body = _pending_for(client, code)
        assert db.bind_calls == []  # 還沒綁
        assert re.fullmatch(r"[0-9a-f]{16}", body["pending_id"])
        # callback_data＝"tgl:ok:" + pending_id，要在 64 bytes 內，且不含完整 token
        assert len(("tgl:ok:" + body["pending_id"]).encode()) <= 64
        assert body["target_label"] == "Danny (0x12…ab)"
        assert body["current_label"] is None
        assert 0 < body["expires_in"] <= 300
        # 短碼還沒被消耗（確認時才消耗）
        assert f"tglink_start:{code}" in cache.data

    def test_manual_token_preview_does_not_bind(self, cache, db):
        client = _client()
        body = _pending_for(client, _issue(client)["token"])
        assert db.bind_calls == [] and body["target_label"].startswith("Danny")

    def test_already_bound_elsewhere_says_it_will_move(self, cache, db):
        db.bindings[TG] = OTHER_ID
        client = _client()
        code = _code_from(_issue(client)["deep_link"])
        body = _pending_for(client, code)
        assert body["current_label"] == "Bob (0x98…cd)"
        assert body["target_label"] == "Danny (0x12…ab)"

    def test_already_bound_to_same_account_is_not_a_move(self, cache, db):
        db.bindings[TG] = TARGET_ID
        client = _client()
        body = _pending_for(client, _code_from(_issue(client)["deep_link"]))
        assert body["current_label"] is None

    def test_unknown_code_is_rejected(self, cache, db):
        assert _preview(_client(), "A" * 32).status_code == 400

    @pytest.mark.parametrize("bad", ["", "x" * 600, "has space", "bad/char!"])
    def test_malformed_code_is_rejected(self, cache, db, bad):
        assert _preview(_client(), bad).status_code in (400, 422)

    def test_expired_code_is_rejected(self, cache, db):
        """DB 那層快取不吃 TTL：過期要靠完整 token 內的 exp 擋。"""
        client = _client()
        with patch("api.routers.telegram_link.LINK_TOKEN_TTL_SECONDS", -10):
            code = _code_from(_issue(client)["deep_link"])
        assert f"tglink_start:{code}" in cache.data
        assert _preview(client, code).status_code == 400

    @pytest.mark.parametrize(
        "user_id,expected",
        [
            ("evm_0x12aa000000000000000000000000000000000cab", "0x12…ab"),
            ("UQAbcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ", "UQAb…IJ"),
            ("tg_12345678", "tg_1…78"),
            ("short", "sh…"),
        ],
    )
    def test_account_mask(self, user_id, expected):
        from api.routers.telegram_link import _mask_account

        assert _mask_account(user_id) == expected


# ── 確認／取消 ───────────────────────────────────────────────────────────────


class TestConfirm:
    def test_confirm_binds_the_signed_user(self, cache, db):
        client = _client()
        code = _code_from(_issue(client)["deep_link"])
        pid = _pending_for(client, code)["pending_id"]
        res = _confirm(client, pid)
        assert res.status_code == 200, res.text
        assert res.json()["user_id"] == TARGET_ID
        assert db.bind_calls == [(TG, TARGET_ID)]
        # 確認時才消耗短碼
        assert f"tglink_start:{code}" not in cache.data

    def test_confirm_by_a_different_telegram_user_is_rejected(self, cache, db):
        client = _client()
        pid = _pending_for(client, _code_from(_issue(client)["deep_link"]))[
            "pending_id"
        ]
        res = _confirm(client, pid, telegram_id=OTHER_TG)
        assert res.status_code == 403
        assert db.bind_calls == []
        # 真正開啟的人仍然可以確認
        assert _confirm(client, pid).status_code == 200
        assert db.bind_calls == [(TG, TARGET_ID)]

    def test_cancel_invalidates_code_and_token(self, cache, db):
        client = _client()
        data = _issue(client)
        code = _code_from(data["deep_link"])
        pid = _pending_for(client, code)["pending_id"]
        assert _cancel(client, pid).status_code == 200
        assert _confirm(client, pid).status_code == 400
        assert _preview(client, code).status_code == 400
        assert _preview(client, data["token"]).status_code == 400
        assert db.bind_calls == []

    def test_cancel_by_a_different_telegram_user_is_rejected(self, cache, db):
        client = _client()
        pid = _pending_for(client, _code_from(_issue(client)["deep_link"]))[
            "pending_id"
        ]
        assert _cancel(client, pid, telegram_id=OTHER_TG).status_code == 403
        assert _confirm(client, pid).status_code == 200

    def test_reused_code_is_rejected_after_bind(self, cache, db):
        client = _client()
        data = _issue(client)
        code = _code_from(data["deep_link"])
        pid = _pending_for(client, code)["pending_id"]
        assert _confirm(client, pid).status_code == 200
        assert _confirm(client, pid).status_code == 400  # 同一個確認鈕再按
        assert _preview(client, code, telegram_id=OTHER_TG).status_code == 400
        assert _preview(client, data["token"]).status_code == 400  # 手動 token 也死了
        assert db.bind_calls == [(TG, TARGET_ID)]

    def test_token_that_expired_while_waiting_is_rejected(self, cache, db):
        client = _client()
        with patch("api.routers.telegram_link.LINK_TOKEN_TTL_SECONDS", 1):
            token = _issue(client)["token"]
        pid = _pending_for(client, token)["pending_id"]
        with patch(
            "api.routers.telegram_link.time.time",
            return_value=__import__("time").time() + 120,
        ):
            assert _confirm(client, pid).status_code == 400
        assert db.bind_calls == []

    def test_concurrent_confirm_only_one_wins(self, cache, db):
        client = _client()
        pid = _pending_for(client, _code_from(_issue(client)["deep_link"]))[
            "pending_id"
        ]
        with patch("api.routers.telegram_link.consume_cache", return_value=False):
            assert _confirm(client, pid).status_code == 400
        assert db.bind_calls == []

    def test_manual_token_confirm_binds(self, cache, db):
        client = _client()
        pid = _pending_for(client, _issue(client)["token"])["pending_id"]
        assert _confirm(client, pid).status_code == 200
        assert db.bind_calls == [(TG, TARGET_ID)]

    def test_old_bot_sending_token_to_verify_link_cannot_bind(self, cache, db):
        """舊 bot（沒確認步驟）直接拿 token 打 verify-link：不能綁。"""
        client = _client()
        token = _issue(client)["token"]
        res = client.post(
            "/api/telegram/verify-link", json={"token": token, "telegram_id": TG}
        )
        assert res.status_code == 422
        assert db.bind_calls == []


# ── 綁定後的早報狀態 ────────────────────────────────────────────────────────


class TestBriefAfterBind:
    def _bind_with(self, db, prefs_row):
        db.prefs_row = prefs_row
        client = _client()
        pid = _pending_for(client, _code_from(_issue(client)["deep_link"]))[
            "pending_id"
        ]
        res = _confirm(client, pid)
        assert res.status_code == 200, res.text
        return res.json()

    def test_no_explicit_prefs_means_brief_on_at_8(self, cache, db):
        body = self._bind_with(db, _prefs_row())
        assert body["brief_enabled"] is True and body["brief_hour"] == 8

    def test_explicit_off_is_respected(self, cache, db):
        body = self._bind_with(db, _prefs_row(prefs_user_id=TARGET_ID, user_set=True, enabled=False))
        assert body["brief_enabled"] is False and body["brief_hour"] is None

    def test_explicit_inapp_only_is_not_reported_as_telegram(self, cache, db):
        body = self._bind_with(
            db,
            _prefs_row(
                prefs_user_id=TARGET_ID,
                user_set=True,
                enabled=True,
                channels=["inapp"],
            ),
        )
        assert body["brief_enabled"] is False

    def test_explicit_hour_is_reported(self, cache, db):
        body = self._bind_with(
            db,
            _prefs_row(
                prefs_user_id=TARGET_ID,
                user_set=True,
                enabled=True,
                send_hour=7,
                channels=["telegram", "inapp"],
            ),
        )
        assert body["brief_enabled"] is True and body["brief_hour"] == 7

    def test_prefs_lookup_failure_does_not_fail_the_bind(self, cache, db):
        client = _client()
        pid = _pending_for(client, _code_from(_issue(client)["deep_link"]))[
            "pending_id"
        ]
        with patch(
            "core.daily_brief.store.get_prefs_row", side_effect=RuntimeError("db")
        ):
            res = _confirm(client, pid)
        assert res.status_code == 200
        assert res.json()["brief_enabled"] is False


# ── bot：/start link_<code>、/link、確認鈕 ────────────────────────────────


def _message(tg_id=TG):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=tg_id, username="tg_user", first_name="T"),
        answer=AsyncMock(),
    )


def _preview_result(current=None):
    return {
        "pending_id": "0123456789abcdef",
        "target_label": "Danny (0x12…ab)",
        "current_label": current,
        "expires_in": 280,
    }


class TestBotStartPayload:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("/start link_abcDEF123_-x", "abcDEF123_-x"),
            ("/start@getcryptomind_bot link_abc", "abc"),
            ("/start", None),
            ("/start hello", None),
            ("/start link_", None),
            ("", None),
        ],
    )
    def test_parse_start_link_code(self, text, expected):
        from bot.telegram_bot import _start_link_code

        assert _start_link_code(text) == expected

    async def test_start_asks_for_confirmation_and_does_not_bind(self):
        from bot.telegram_bot import _link_account

        api = SimpleNamespace(
            link_preview=AsyncMock(return_value=_preview_result()),
            confirm_link=AsyncMock(),
        )
        msg = _message()
        await _link_account(api, msg, "abc", "en")
        api.link_preview.assert_awaited_once()
        assert api.link_preview.call_args.kwargs["token"] == "abc"
        assert api.link_preview.call_args.kwargs["telegram_id"] == TG
        api.confirm_link.assert_not_awaited()
        text = msg.answer.call_args.args[0]
        assert "Danny (0x12…ab)" in text
        assert "Only confirm if you started this from your own browser" in text
        assert "move" not in text.lower()
        kb = msg.answer.call_args.kwargs["reply_markup"]
        data = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert data == ["tgl:ok:0123456789abcdef", "tgl:no:0123456789abcdef"]
        assert all(len(d.encode()) <= 64 for d in data)

    async def test_already_bound_elsewhere_variant(self):
        from bot.telegram_bot import _link_account

        api = SimpleNamespace(
            link_preview=AsyncMock(return_value=_preview_result("Bob (0x98…cd)"))
        )
        msg = _message()
        await _link_account(api, msg, "abc", "en")
        text = msg.answer.call_args.args[0]
        assert "move your Telegram from account Bob (0x98…cd) to Danny (0x12…ab)" in (
            text
        )

    async def test_invalid_or_reused_code_reply(self):
        from bot.telegram_bot import ApiError, _link_account

        api = SimpleNamespace(
            link_preview=AsyncMock(side_effect=ApiError("INVALID_TOKEN", 400))
        )
        msg = _message()
        await _link_account(api, msg, "abc", "en")
        assert "invalid or expired" in msg.answer.call_args.args[0].lower()


class TestBotConfirmCallback:
    @pytest.mark.parametrize(
        "data,expected",
        [
            ("tgl:ok:0123456789abcdef", ("ok", "0123456789abcdef")),
            ("tgl:no:0123456789abcdef", ("no", "0123456789abcdef")),
            ("tgl:ok:", None),
            ("tgl:zz:0123456789abcdef", None),
            ("tgl:ok:NOT-HEX-VALUE!!", None),
        ],
    )
    def test_parse_link_callback(self, data, expected):
        from bot.telegram_bot import _parse_link_callback

        assert _parse_link_callback(data) == expected

    async def test_confirm_uses_the_pressing_user_and_reports_brief(self):
        from bot.telegram_bot import _confirm_link_reply

        api = SimpleNamespace(
            confirm_link=AsyncMock(
                return_value={
                    "success": True,
                    "username": "EVM_12aa00",
                    "brief_enabled": True,
                    "brief_hour": 8,
                }
            )
        )
        user = SimpleNamespace(id=TG, username="tg_user", first_name="T")
        text, done = await _confirm_link_reply(
            api, "ok", "0123456789abcdef", user, "en"
        )
        assert api.confirm_link.call_args.kwargs["telegram_id"] == TG
        assert done is True
        assert "@EVM_12aa00" in text and "08:00" in text and "/brief off" in text

    async def test_confirm_by_someone_else_keeps_the_buttons(self):
        from bot.telegram_bot import ApiError, _confirm_link_reply

        api = SimpleNamespace(
            confirm_link=AsyncMock(side_effect=ApiError("LINK_NOT_YOURS", 403))
        )
        user = SimpleNamespace(id=OTHER_TG, username=None, first_name="X")
        text, done = await _confirm_link_reply(
            api, "ok", "0123456789abcdef", user, "en"
        )
        assert done is False  # 按鈕留給真正開啟的人
        assert "not for you" in text.lower()

    async def test_cancel_reply(self):
        from bot.telegram_bot import _confirm_link_reply

        api = SimpleNamespace(cancel_link=AsyncMock(return_value={"success": True}))
        user = SimpleNamespace(id=TG, username=None, first_name="T")
        text, done = await _confirm_link_reply(
            api, "no", "0123456789abcdef", user, "en"
        )
        api.cancel_link.assert_awaited_once()
        assert done is True and "nothing was linked" in text.lower()

    async def test_expired_confirm_reply(self):
        from bot.telegram_bot import ApiError, _confirm_link_reply

        api = SimpleNamespace(
            confirm_link=AsyncMock(side_effect=ApiError("INVALID_TOKEN", 400))
        )
        user = SimpleNamespace(id=TG, username=None, first_name="T")
        text, done = await _confirm_link_reply(
            api, "ok", "0123456789abcdef", user, "en"
        )
        assert done is True and "invalid or expired" in text.lower()
