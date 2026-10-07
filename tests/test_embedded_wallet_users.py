"""Email／Google 登入（Reown 內嵌錢包）記錄給後台看（2026-09-30，c064）。

DANNY：Reown 免費方案每月 500 個內嵌錢包用戶、超過直接停用，想在自家後台看到人數與名單
（原本後端完全不知道哪些帳號是用 Email／Google 登入的）。前端登入時回報 login_via，
Email／Google 的記進 embedded_wallet_users（audit_logs 90 天就清，不能拿來算累計）。
前端的對應（readEvmConnection 帶 authProvider、loginViaFor）在 tests/js/reown_social_login.mjs。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_evm_login_autobind import ADDR, ADDR_LOWER, _login_env

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


# ── 登入端點：只有 Email／Google 會記，audit 也帶 login_via ────────────────────


async def _login(client, **extra):
    return await client.post(
        "/api/user/evm-login",
        json={"address": ADDR, "signature": "0x" + "0" * 130, "nonce_token": "tok", **extra},
    )


@pytest.mark.parametrize(
    "extra, recorded",
    [
        ({"login_via": "google"}, "google"),
        ({"login_via": "email"}, "email"),
        ({"login_via": "social"}, "social"),
        ({"login_via": "wallet"}, None),
        ({}, None),  # 舊版前端／One-Click 路徑不帶
    ],
)
async def test_login_records_only_embedded_wallets(client, extra, recorded):
    with (
        _login_env(),
        patch("api.routers.user.audit_log") as audit,
        patch("api.routers.user._sync_trust_evm_binding"),
        patch("api.routers.user.record_guest_converted"),
        patch("core.embedded_wallet_users.record_login") as record,
    ):
        resp = await _login(client, **extra)
    assert resp.status_code == 200
    if recorded:
        record.assert_called_once_with(f"evm_{ADDR_LOWER}", ADDR_LOWER, recorded)
    else:
        record.assert_not_called()
    assert audit.call_args.kwargs["action"] == "wallet_connected"
    assert audit.call_args.kwargs["metadata"]["login_via"] == extra.get("login_via")


async def test_record_failure_never_blocks_login(client):
    with (
        _login_env(),
        patch("api.routers.user._sync_trust_evm_binding"),
        patch("api.routers.user.record_guest_converted"),
        patch("core.embedded_wallet_users.record_login", side_effect=RuntimeError("db down")),
    ):
        resp = await _login(client, login_via="google")
    assert resp.status_code == 200


async def test_unknown_login_via_is_rejected(client):
    with _login_env():
        resp = await _login(client, login_via="facebook-password")
    assert resp.status_code == 422


# ── 資料表：真 PostgreSQL（commit 後清掉）────────────────────────────────────


def test_record_and_stats_on_real_db():
    from core import embedded_wallet_users as ewu
    from core.database.base import DatabaseBase

    try:
        from core.database.connection import get_connection

        get_connection().close()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 連不到：{e}")
    s = uuid.uuid4().hex[:8]
    g, e_ = f"t-ewu-g-{s}", f"t-ewu-e-{s}"
    try:
        for uid in (g, e_):
            DatabaseBase.execute(
                "INSERT INTO users (user_id, username) VALUES (%s, %s)", (uid, f"name-{uid}")
            )
        before = ewu.stats()

        ewu.record_login(g, "0x" + "1" * 40, "google")
        ewu.record_login(g, "0x" + "1" * 40, "google")
        ewu.record_login(e_, "0x" + "2" * 40, "email")
        DatabaseBase.execute(
            "INSERT INTO audit_logs (user_id, action, endpoint, method, metadata) "
            "VALUES (%s, 'wallet_connected', '/api/user/evm-login', 'POST', %s::jsonb)",
            (g, '{"chain": "evm", "login_via": "google"}'),
        )
        row = DatabaseBase.query_one(
            "SELECT provider, login_count, first_seen_at <= last_seen_at AS ok "
            "FROM embedded_wallet_users WHERE user_id = %s",
            (g,),
        )
        assert row == {"provider": "google", "login_count": 2, "ok": True}

        after = ewu.stats()
        assert after["total"] == before["total"] + 2
        assert after["month_active"] == before["month_active"] + 2
        assert after["month_new"] == before["month_new"] + 2
        assert after["free_limit"] == 500
        assert after["evm_logins_this_month"] == before["evm_logins_this_month"] + 1
        by = {p["provider"]: p for p in after["by_provider"]}
        assert by["google"]["total"] >= 1 and by["email"]["total"] >= 1
        mine = {r["user_id"]: r for r in after["recent"] if r["user_id"] in (g, e_)}
        assert mine[g]["username"] == f"name-{g}" and mine[g]["login_count"] == 2
        assert mine[e_]["provider"] == "email"
        assert all("email" not in r for r in after["recent"]), "名單不帶 Email 本身"
    finally:
        for uid in (g, e_):
            DatabaseBase.execute("DELETE FROM embedded_wallet_users WHERE user_id = %s", (uid,))
            DatabaseBase.execute("DELETE FROM audit_logs WHERE user_id = %s", (uid,))
            DatabaseBase.execute("DELETE FROM users WHERE user_id = %s", (uid,))


# ── 後台端點只給管理員；前端有把 login_via 送出去 ─────────────────────────────


def test_admin_endpoint_requires_admin():
    from api.deps import require_admin
    from api.routers.admin.stats import router

    route = next(r for r in router.routes if r.path.endswith("/stats/login-methods"))
    assert require_admin in {d.call for d in route.dependant.dependencies}


async def test_admin_endpoint_returns_stats(monkeypatch):
    from starlette.requests import Request

    import api.routers.admin.stats as mod
    from core import embedded_wallet_users

    monkeypatch.setattr(embedded_wallet_users, "stats", lambda: {"month_active": 3, "free_limit": 500})
    req = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "client": ("t", 1)})
    body = await mod.admin_stats_login_methods.__wrapped__(req, admin_user={"user_id": "admin"})
    assert body == {"success": True, "month_active": 3, "free_limit": 500}


def test_frontend_sends_login_via_and_passes_auth_provider():
    auth = (REPO / "web" / "js" / "evm-auth.js").read_text(encoding="utf-8")
    assert "login_via: loginViaFor(opts)" in auth
    # 兩條 WalletConnect／內嵌錢包登入路徑都要把 authProvider 往下傳
    assert len(re.findall(r"authProvider: conn\.authProvider", auth)) == 2
    wc = (REPO / "web" / "js" / "evm-walletconnect.js").read_text(encoding="utf-8")
    assert "authProvider: fromAppKit.authProvider" in wc


def test_migration_chain():
    mig = (REPO / "alembic" / "versions" / "c064_embedded_wallet_users.py").read_text(encoding="utf-8")
    assert 'revision = "c064"' in mig and 'down_revision = "c063"' in mig
    schema = (REPO / "core" / "database" / "schema.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS embedded_wallet_users" in schema
