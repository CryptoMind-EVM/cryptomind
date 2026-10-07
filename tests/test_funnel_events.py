"""轉換漏斗事件（PR-5，設計：docs/plans/2026-09-27-launch-readiness-design.md §4）。

守住：
- 每個事件點都會發（mock ``api.funnel.audit_log``，不碰 DB）
- 訪客事件不帶 IP／user agent／username，只存 guest_id 的 uuid 本體（不含簽章）
- 登入時的轉換對應只認有效簽章的 guest_id cookie（竄改 → 不記）
- 記錄失敗絕不擋請求
- admin 漏斗端點：非 admin 403、回傳形狀、SQL 參數化、限流登記
"""

from __future__ import annotations

import json
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import funnel
from api.routers import guest as guest_router
from core import shared_cache

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
RAW = "a" * 32


def _valid_cookie(raw: str = RAW) -> str:
    return f"{raw}.{guest_router._sign(raw)}"


def _tampered_cookie(raw: str = RAW) -> str:
    return f"{raw}.{'0' * 16}"


def _calls(mock, action):
    return [c for c in mock.call_args_list if c.args and c.args[0] == action]


def _assert_anonymous(call):
    """訪客事件：沒有 IP、UA、username，request_data 只有匿名欄位。"""
    kw = call.kwargs
    assert kw.get("ip_address") is None
    assert kw.get("user_agent") is None
    assert kw.get("username") is None
    assert kw.get("metadata") is None
    data = kw["request_data"]
    assert data["guest_id"] == RAW or len(data["guest_id"]) == 32
    assert "." not in data["guest_id"]  # 簽章不進 DB


@pytest.fixture(autouse=True)
def _reset_guest_state():
    shared_cache.reset()
    from api.middleware.rate_limit import limiter

    limiter.reset()
    guest_router._local_quota.clear()
    guest_router._local_global.clear()
    yield
    shared_cache.reset()
    limiter.reset()
    guest_router._local_quota.clear()
    guest_router._local_global.clear()


@pytest.fixture
def audit():
    with patch("api.funnel.audit_log") as m:
        yield m


# ── 訪客事件 ──────────────────────────────────────────────────────────────────


class TestGuestFirstSeen:
    async def test_new_cookie_emits_once(self, audit):
        req = SimpleNamespace(cookies={})
        resp = MagicMock()
        with patch.object(guest_router, "set_site_cookie") as set_cookie:
            value = guest_router._resolve_guest(req, resp)
        set_cookie.assert_called_once()
        calls = _calls(audit, "guest_first_seen")
        assert len(calls) == 1
        _assert_anonymous(calls[0])
        assert calls[0].kwargs["request_data"] == {"guest_id": value.rpartition(".")[0]}
        assert calls[0].kwargs.get("user_id") is None

    async def test_existing_valid_cookie_emits_nothing(self, audit):
        req = SimpleNamespace(cookies={"guest_id": _valid_cookie()})
        guest_router._resolve_guest(req, MagicMock())
        assert audit.call_count == 0

    async def test_tampered_cookie_is_reissued_and_counted_as_new(self, audit):
        req = SimpleNamespace(cookies={"guest_id": _tampered_cookie()})
        with patch.object(guest_router, "set_site_cookie"):
            value = guest_router._resolve_guest(req, MagicMock())
        assert value != _tampered_cookie()
        assert len(_calls(audit, "guest_first_seen")) == 1

    async def test_quota_endpoint_mints_and_emits(self, client, audit):
        res = await client.get("/api/guest/quota")
        assert res.status_code == 200
        assert len(_calls(audit, "guest_first_seen")) == 1
        # 帶著剛發的 cookie 再來一次：不是新訪客
        cookie = res.cookies.get("guest_id")
        assert cookie
        res2 = await client.get(
            "/api/guest/quota", headers={"Cookie": f"guest_id={cookie}"}
        )
        assert res2.status_code == 200
        assert len(_calls(audit, "guest_first_seen")) == 1


class TestGuestQuestion:
    async def test_consume_quota_emits_running_count(self, audit):
        cookie = _valid_cookie()
        guest_router._consume_quota(cookie)
        guest_router._consume_quota(cookie)
        calls = _calls(audit, "guest_question")
        assert [c.kwargs["request_data"] for c in calls] == [
            {"guest_id": RAW, "n": 1},
            {"guest_id": RAW, "n": 2},
        ]
        for c in calls:
            _assert_anonymous(c)

    def test_successful_answer_path_still_consumes_quota(self):
        """guest_question 掛在 _consume_quota（成功回答才扣額）——
        guest_analyze 改寫時不能把這一步拿掉，否則事件默默消失。"""
        import inspect

        src = inspect.getsource(guest_router.guest_analyze)
        assert "_consume_quota(" in src

    async def test_payload_reaching_db_has_no_ip_or_ua(self):
        """走到真正的 AuditLogger payload（DB 欄位順序）也沒有 IP／UA。"""
        from core.audit import AuditLogger

        with patch("core.audit.AuditLogger.log") as log:
            funnel.record_guest_question(_valid_cookie(), 1)
        args, kwargs = log.call_args
        payload = AuditLogger._prepare_payload(*args, **kwargs)
        assert payload[0] is None  # user_id
        assert payload[1] is None  # username
        assert payload[7] is None  # ip_address
        assert payload[8] is None  # user_agent
        assert json.loads(payload[9]) == {"guest_id": RAW, "n": 1}


class TestNeverBreaksRequest:
    async def test_audit_failure_is_swallowed(self):
        with patch("api.funnel.audit_log", side_effect=RuntimeError("db down")):
            funnel.record_guest_first_seen(_valid_cookie())
            funnel.record_guest_question(_valid_cookie(), 1)
            funnel.record_brief_enabled("u1")

    def test_off_event_loop_is_skipped(self, audit):
        """worker thread／cron 裡沒有 running loop：不呼叫 audit_log（它會
        asyncio.run 開新 loop，觸發 ORM engine 換 loop 重建）。"""
        funnel.record_guest_first_seen(_valid_cookie())
        funnel.record_brief_enabled("u1")
        assert audit.call_count == 0

    async def test_quota_endpoint_survives_audit_failure(self, client):
        with patch("api.funnel.audit_log", side_effect=RuntimeError("db down")):
            res = await client.get("/api/guest/quota")
        assert res.status_code == 200


# ── 登入轉換 ──────────────────────────────────────────────────────────────────


class TestGuestConvertedHelper:
    def _req(self, cookie):
        return SimpleNamespace(cookies={"guest_id": cookie} if cookie else {})

    async def test_valid_signature_links_guest_to_user(self, audit):
        funnel.record_guest_converted(
            self._req(_valid_cookie()), "evm_0xabc", "evm", True
        )
        calls = _calls(audit, "guest_converted")
        assert len(calls) == 1
        kw = calls[0].kwargs
        assert kw["user_id"] == "evm_0xabc"
        assert kw["request_data"] == {
            "guest_id": RAW,
            "method": "evm",
            "is_new_user": True,
        }
        assert kw.get("ip_address") is None and kw.get("user_agent") is None

    @pytest.mark.parametrize(
        "cookie",
        [None, "", _tampered_cookie(), RAW, "zz." + "0" * 16, "not-a-guest-id"],
    )
    async def test_missing_or_tampered_cookie_emits_nothing(self, audit, cookie):
        funnel.record_guest_converted(self._req(cookie), "evm_0xabc", "evm", False)
        assert audit.call_count == 0


ADDR = "0x" + "ab" * 20


@contextmanager
def _evm_login_env(is_new=True):
    user = {
        "user_id": f"evm_{ADDR}",
        "username": "EVM_ababab",
        "auth_method": "evm_wallet",
        "role": "user",
        "membership_tier": "free",
        "is_new": is_new,
    }
    patchers = [
        patch("api.routers.user.MULTICHAIN_ENABLED", True),
        patch(
            "api.routers.user.recover_siwe_signer_async",
            new=AsyncMock(return_value=ADDR),
        ),
        patch("api.routers.user._enforce_auth_lockout"),
        patch("api.routers.user._clear_auth_failures"),
        patch("api.routers.user.audit_log"),
        patch("api.routers.user.TRUST_EVM_BINDING_ENABLED", False),
        patch(
            "api.routers.user.user_wallet_repo.get_binding",
            new=AsyncMock(return_value=None),
        ),
        patch("api.routers.user.create_or_get_user", return_value=user),
        patch("api.routers.user.user_wallet_repo.register_binding", new=AsyncMock()),
    ]
    with ExitStack() as stack:
        yield [stack.enter_context(p) for p in patchers]


async def _evm_login(client, cookie=None):
    headers = {"Cookie": f"guest_id={cookie}"} if cookie else {}
    return await client.post(
        "/api/user/evm-login",
        json={"address": ADDR, "signature": "0x" + "0" * 130, "nonce_token": "tok"},
        headers=headers,
    )


@contextmanager
def _telegram_login_env():
    user = {
        "user_id": "tg_42",
        "username": "TG_x",
        "auth_method": "telegram",
        "role": "user",
        "membership_tier": "free",
        "is_new": False,
    }
    with (
        patch(
            "api.telegram_verification.verify_telegram_init_data",
            return_value={"id": 42, "username": "x"},
        ),
        patch(
            "core.database.telegram.get_binding_by_telegram_id",
            return_value={"user_id": "tg_42"},
        ),
        patch("api.routers.user.create_or_get_user", return_value=user),
        patch("api.routers.user._enforce_auth_lockout"),
        patch("api.routers.user._clear_auth_failures"),
    ):
        yield


class TestLoginEndpointsEmitConversion:
    async def test_evm_login_with_guest_cookie(self, client, audit):
        with _evm_login_env(is_new=True):
            res = await _evm_login(client, _valid_cookie())
        assert res.status_code == 200
        calls = _calls(audit, "guest_converted")
        assert len(calls) == 1
        assert calls[0].kwargs["user_id"] == f"evm_{ADDR}"
        assert calls[0].kwargs["request_data"] == {
            "guest_id": RAW,
            "method": "evm",
            "is_new_user": True,
        }

    async def test_evm_login_tampered_cookie_no_event(self, client, audit):
        with _evm_login_env():
            res = await _evm_login(client, _tampered_cookie())
        assert res.status_code == 200
        assert _calls(audit, "guest_converted") == []

    async def test_evm_login_without_cookie_no_event(self, client, audit):
        with _evm_login_env():
            res = await _evm_login(client)
        assert res.status_code == 200
        assert _calls(audit, "guest_converted") == []

    async def test_evm_login_survives_audit_failure(self, client):
        with (
            _evm_login_env(),
            patch("api.funnel.audit_log", side_effect=RuntimeError("db down")),
        ):
            res = await _evm_login(client, _valid_cookie())
        assert res.status_code == 200

    async def test_telegram_login_with_guest_cookie(self, client, audit):
        with _telegram_login_env():
            res = await client.post(
                "/api/user/telegram-login",
                json={"init_data": "query_id=x&user=%7B%7D&hash=abc"},
                headers={"Cookie": f"guest_id={_valid_cookie()}"},
            )
        assert res.status_code == 200, res.text
        calls = _calls(audit, "guest_converted")
        assert len(calls) == 1
        assert calls[0].kwargs["user_id"] == "tg_42"
        assert calls[0].kwargs["request_data"] == {
            "guest_id": RAW,
            "method": "telegram",
            "is_new_user": False,
        }

    async def test_telegram_login_tampered_cookie_no_event(self, client, audit):
        with _telegram_login_env():
            res = await client.post(
                "/api/user/telegram-login",
                json={"init_data": "query_id=x&user=%7B%7D&hash=abc"},
                headers={"Cookie": f"guest_id={_tampered_cookie()}"},
            )
        assert res.status_code == 200, res.text
        assert _calls(audit, "guest_converted") == []

    def test_google_login_with_guest_cookie(self, audit, monkeypatch):
        from api.routers import google_auth as g

        identity = SimpleNamespace(
            sub="123",
            email="a@example.com",
            email_verified=True,
            name="A",
            locale="en",
        )
        monkeypatch.setattr(g.auth_google, "enabled", lambda: True)
        monkeypatch.setattr(g.auth_google, "verify_id_token", lambda cred: identity)
        monkeypatch.setattr(g, "get_binding_by_sub", lambda sub: {"user_id": "g_123"})
        monkeypatch.setattr(g, "update_last_used", lambda sub: None)
        monkeypatch.setattr(
            "core.database.user.create_or_get_user",
            lambda **kw: {"user_id": "g_123", "username": "A", "role": "user"},
        )
        monkeypatch.setattr("api.routers.user._enforce_auth_lockout", lambda ip: None)
        monkeypatch.setattr("api.routers.user._clear_auth_failures", lambda ip: None)
        app = FastAPI()
        app.state.limiter = g.limiter
        app.include_router(g.router)
        client = TestClient(app)
        client.cookies.set("guest_id", _valid_cookie())
        res = client.post("/api/user/google-login", json={"credential": "x" * 40})
        assert res.status_code == 200, res.text
        calls = _calls(audit, "guest_converted")
        assert len(calls) == 1
        assert calls[0].kwargs["user_id"] == "g_123"
        assert calls[0].kwargs["request_data"]["method"] == "google"


# ── 第一筆帳 ──────────────────────────────────────────────────────────────────


class TestJournalFirstEntry:
    async def test_first_entry_emits(self, audit):
        with patch("api.funnel._journal_rows_upto", return_value=1) as rows:
            await funnel.record_journal_first_entry_if_first("u1", "manual")
        rows.assert_called_once_with("u1", 2)
        calls = _calls(audit, "journal_first_entry")
        assert len(calls) == 1
        assert calls[0].kwargs["user_id"] == "u1"
        assert calls[0].kwargs["request_data"] == {"source": "manual"}

    async def test_not_first_entry_no_event(self, audit):
        with patch("api.funnel._journal_rows_upto", return_value=2):
            await funnel.record_journal_first_entry_if_first("u1", "manual")
        assert audit.call_count == 0

    async def test_batch_first_sync_counts_all_added(self, audit):
        """鏈上同步一次加 3 筆、帳本剛好 3 筆 → 第一次。"""
        with patch("api.funnel._journal_rows_upto", return_value=3) as rows:
            await funnel.record_journal_first_entry_if_first("u1", "onchain", added=3)
        rows.assert_called_once_with("u1", 4)
        assert len(_calls(audit, "journal_first_entry")) == 1

    async def test_nothing_added_skips_query(self, audit):
        with patch("api.funnel._journal_rows_upto") as rows:
            await funnel.record_journal_first_entry_if_first("u1", "onchain", added=0)
        rows.assert_not_called()
        assert audit.call_count == 0

    async def test_count_failure_is_swallowed(self, audit):
        from core.database.base import DatabaseError

        with patch("api.funnel._journal_rows_upto", side_effect=DatabaseError("x")):
            await funnel.record_journal_first_entry_if_first("u1", "manual")
        assert audit.call_count == 0

    def test_rows_query_is_parameterized_and_bounded(self):
        with patch(
            "core.database.base.DatabaseBase.query_one", return_value={"n": 1}
        ) as q:
            assert funnel._journal_rows_upto("u1", 2) == 1
        sql, params = q.call_args.args
        assert "%s" in sql and "LIMIT %s" in sql and params == ("u1", 2)

    def test_manual_create_route_emits(self, audit):
        from api.routers import journal as mod

        class _Repo:
            def __init__(self, user_id, base_currency=None):
                self.user_id = user_id

            def add_entry(self, **kw):
                return {"ok": True, "id": 7}

        app = FastAPI()
        app.state.limiter = mod.limiter
        app.include_router(mod.router)

        async def fake_user():
            return {"user_id": "u1"}

        app.dependency_overrides[mod.get_current_user] = fake_user
        with (
            patch("core.orm.trade_journal_repo.TradeJournalRepo", _Repo),
            patch("api.funnel._journal_rows_upto", return_value=1),
        ):
            res = TestClient(app).post(
                "/api/journal/entry", json={"amount": 100, "currency": "TWD"}
            )
        assert res.status_code == 200, res.text
        calls = _calls(audit, "journal_first_entry")
        assert len(calls) == 1 and calls[0].kwargs["request_data"] == {
            "source": "manual"
        }

    def test_onchain_sync_route_emits_with_added_count(self, audit):
        from api.routers import onchain as mod

        app = FastAPI()
        app.state.limiter = mod.limiter
        app.include_router(mod.router)

        async def fake_user():
            return {"user_id": "u1"}

        app.dependency_overrides[mod.get_current_user] = fake_user
        mod.limiter.reset()
        with (
            patch.object(mod.store, "list_wallets", return_value=[{"address": "0x1"}]),
            patch.object(mod, "sync_user", return_value={"added": 3}),
            patch.object(mod, "_status_payload", return_value={}),
            patch("api.funnel._journal_rows_upto", return_value=3) as rows,
        ):
            res = TestClient(app).post("/api/journal/onchain/sync")
        assert res.status_code == 200, res.text
        rows.assert_called_once_with("u1", 4)
        calls = _calls(audit, "journal_first_entry")
        assert len(calls) == 1 and calls[0].kwargs["request_data"] == {
            "source": "onchain"
        }

    def test_chat_consent_write_is_wired(self):
        """聊天確認卡寫入（單張與批次都走 _apply）後要查是不是第一筆。"""
        src = (REPO / "core" / "agents" / "manager" / "claw_loop.py").read_text(
            encoding="utf-8"
        )
        apply_body = src.split("async def _apply(uid_, signal_, edited_):")[1].split(
            "use_single ="
        )[0]
        assert "record_journal_first_entry_if_first" in apply_body
        assert '"journal_entry"' in apply_body and '"chat"' in apply_body


# ── 早報開啟 ──────────────────────────────────────────────────────────────────


def _brief_row(**kw):
    base = {
        "user_id": "u1",
        "language": "zh-TW",
        "display_name": None,
        "membership_tier": "free",
        "telegram_id": None,
        "prefs_user_id": None,
        # 測試裡給了 prefs_user_id 的列都當成使用者自己設的（c063）；自動建的列另外測
        "user_set": True,
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


def _brief_client():
    from api.routers import brief as mod

    app = FastAPI()
    app.include_router(mod.router)

    async def fake_user():
        return {"user_id": "u1"}

    app.dependency_overrides[mod.get_current_user] = fake_user
    return TestClient(app)


_BRIEF_BODY = {
    "enabled": True,
    "send_hour": 8,
    "timezone": "Asia/Taipei",
    "channels": ["inapp"],
    "include_spend": True,
}


class TestBriefEnabled:
    @pytest.mark.parametrize(
        "before_row, body_enabled, expected",
        [
            # 沒設過、沒綁 TG（預設關）→ 開：算
            (_brief_row(), True, 1),
            # 設過且關 → 開：算
            (_brief_row(prefs_user_id="u1", enabled=False), True, 1),
            # 本來就開（有列）→ 再存一次開：不算
            (_brief_row(prefs_user_id="u1", enabled=True), True, 0),
            # 沒設過但綁了 TG（預設開）→ 開：不算
            (_brief_row(telegram_id=111), True, 0),
            # 開 → 關：不算
            (_brief_row(prefs_user_id="u1", enabled=True), False, 0),
        ],
    )
    def test_emits_only_on_off_to_on(self, audit, before_row, body_enabled, expected):
        with (
            patch("core.daily_brief.store.get_prefs_row", return_value=before_row),
            patch("core.daily_brief.store.upsert_prefs"),
        ):
            res = _brief_client().put(
                "/api/user/brief-prefs", json={**_BRIEF_BODY, "enabled": body_enabled}
            )
        assert res.status_code == 200, res.text
        calls = _calls(audit, "brief_enabled")
        assert len(calls) == expected
        if expected:
            assert calls[0].kwargs["user_id"] == "u1"

    def test_before_state_failure_does_not_block_save(self, audit):
        from core.database.base import DatabaseError

        rows = iter([DatabaseError("boom")])

        def flaky(user_id):
            item = next(rows, None)
            if isinstance(item, Exception):
                raise item
            return _brief_row(prefs_user_id="u1", enabled=True)

        with (
            patch("core.daily_brief.store.get_prefs_row", side_effect=flaky),
            patch("core.daily_brief.store.upsert_prefs") as up,
        ):
            res = _brief_client().put("/api/user/brief-prefs", json=_BRIEF_BODY)
        assert res.status_code == 200, res.text
        up.assert_called_once()
        assert _calls(audit, "brief_enabled") == []


# ── Admin 漏斗端點 ────────────────────────────────────────────────────────────


def _fake_conn(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn


def _stats_app(role="admin"):
    from api.routers.admin import stats as stats_module

    app = FastAPI()
    app.state.limiter = stats_module.limiter
    app.include_router(stats_module.router)

    async def fake_user():
        return {"user_id": "someone", "role": role, "is_active": True}

    from api.deps import get_current_user

    app.dependency_overrides[get_current_user] = fake_user
    return TestClient(app), stats_module


_GET_CONN = "core.database.connection.get_connection"

# 每個窗口兩個查詢：訪客（6 欄）→ 帳號（4 欄）
_SEEDED = [
    (40, 25, 10, 8, 5, 6),  # 7d guests
    (6, 3, 2, 1),  # 7d accounts
    (120, 70, 30, 20, 12, 15),  # 30d guests
    (18, 9, 6, 4),  # 30d accounts
]


class TestAdminFunnelEndpoint:
    def test_non_admin_is_forbidden(self):
        client, _ = _stats_app(role="user")
        res = client.get("/stats/funnel")
        assert res.status_code == 403

    def test_admin_gets_both_windows(self):
        client, stats = _stats_app()
        stats.limiter.reset()
        cur = MagicMock()
        cur.fetchone.side_effect = list(_SEEDED)
        with patch(_GET_CONN, return_value=_fake_conn(cur)):
            res = client.get("/stats/funnel")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["success"] is True
        w7, w30 = body["windows"]
        assert w7 == {
            "days": 7,
            "guests_first_seen": 40,
            "guests_asked_1": 25,
            "guests_asked_2": 10,
            "guests_converted": 8,
            "guests_converted_after_question": 5,
            "guests_converted_new": 6,
            "new_accounts": 6,
            "journal_first_entry": 3,
            "brief_enabled": 2,
            "returned_7d": 1,
            "return_rate_7d": 0.167,
        }
        assert w30["days"] == 30 and w30["new_accounts"] == 18
        assert w30["return_rate_7d"] == 0.222

        # 參數化：SQL 是模組常數（每次請求不拼字串），窗口天數只從 params 進
        executed = cur.execute.call_args_list
        assert len(executed) == 4
        for call, days in zip(executed, (7, 7, 30, 30)):
            sql, params = call.args
            assert sql in (funnel._FUNNEL_GUEST_SQL, funnel._FUNNEL_ACCOUNT_SQL)
            assert "%s" in sql and params and set(params) == {days}

    def test_zero_new_accounts_rate_is_zero(self):
        client, stats = _stats_app()
        stats.limiter.reset()
        cur = MagicMock()
        cur.fetchone.side_effect = [
            (0, 0, 0, 0, 0, 0),
            (0, 0, 0, 0),
            (0, 0, 0, 0, 0, 0),
            (0, 0, 0, 0),
        ]
        with patch(_GET_CONN, return_value=_fake_conn(cur)):
            res = client.get("/stats/funnel")
        assert res.status_code == 200
        assert all(w["return_rate_7d"] == 0.0 for w in res.json()["windows"])

    def test_db_failure_hides_details(self):
        client, stats = _stats_app()
        stats.limiter.reset()
        with patch(_GET_CONN, side_effect=RuntimeError("password=secret")):
            res = client.get("/stats/funnel")
        assert res.status_code == 500
        assert "secret" not in res.text

    def test_guest_sql_uses_events_and_cohort(self):
        sql = funnel._FUNNEL_GUEST_SQL
        for action in ("guest_first_seen", "guest_question", "guest_converted"):
            assert f"'{action}'" in sql
        acc = funnel._FUNNEL_ACCOUNT_SQL
        for action in ("journal_first_entry", "brief_enabled", "chat_page_visited"):
            assert f"'{action}'" in acc
        assert "FROM users" in acc and "telegram_bindings" in acc


# ── 前端接線＋i18n ────────────────────────────────────────────────────────────


FUNNEL_KEYS = (
    "funnelTitle",
    "funnelStep",
    "funnelNote",
    "funnelGuestsFirstSeen",
    "funnelGuestsAsked1",
    "funnelGuestsAsked2",
    "funnelGuestsConverted",
    "funnelConvertedAfterQuestion",
    "funnelNewAccounts",
    "funnelJournalFirst",
    "funnelBriefEnabled",
    "funnelReturned7d",
    "funnelLoadFailed",
)


class TestFrontendWiring:
    def test_admin_visitors_loads_funnel(self):
        js = (REPO / "web" / "js" / "admin-visitors.js").read_text(encoding="utf-8")
        assert "/api/admin/stats/funnel" in js
        assert 'id="visitors-funnel-body"' in js
        assert "this.loadFunnel()" in js
        # CSP：不得有 inline handler
        assert "onclick=" not in js.lower()

    @pytest.mark.parametrize("lang", ["en", "zh-TW", "zh-CN", "ru"])
    def test_funnel_i18n_keys(self, lang):
        d = json.loads(
            (REPO / "web" / "js" / "i18n" / f"{lang}.json").read_text(encoding="utf-8")
        )["admin"]
        for key in FUNNEL_KEYS:
            assert d.get(key), f"{lang} 缺 admin.{key}"
