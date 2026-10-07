"""Wallet Monitor API entitlement gate 測試（design 2026-08-13 §9.4 / §9.7）。

驗證 WALLET_MONITOR_PREMIUM_GATE_ENABLED 開啟時：
- overview：Free 只查 wallet_snapshot_count 個錢包；回傳 entitlement。
- PUT /settings：Free 啟用排程警示 → 403 upgrade；Premium 放行。
- POST /wallets：Free 新增監測錢包 → 403 upgrade；Premium 放行。
gate 關閉時維持既有行為（不擋）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.entitlement import resolve_entitlement

PREMIUM = resolve_entitlement(tier="premium", is_expired=False)
FREE = resolve_entitlement(tier="free")


def _mocked_user(uid: str) -> dict:
    return {
        "user_id": uid, "username": "U", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }


def _ent_fn(ent):
    """fake resolve_entitlement_for_user 回傳固定 Entitlement。"""
    return lambda _uid: ent


# --------------------------------------------------------------------------- #
# overview                                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_overview_free_capped_to_snapshot_when_gate_on(client):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQf"})
    settings = {
        "monitored_wallets": [
            {"address": "EQaaa1", "chain": "ton"},
            {"address": "EQaaa2", "chain": "ton"},
            {"address": "EQaaa3", "chain": "ton"},
        ],
        "alerts": {"incoming": {"enabled": False, "min_amount_ton": 10}},
        "channels": {"in_app": True},
    }
    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_mocked_user("EQf"))), \
         patch("api.routers.wallet_monitor.WALLET_MONITOR_PREMIUM_GATE_ENABLED", True), \
         patch("api.routers.wallet_monitor.resolve_entitlement_for_user", _ent_fn(FREE)), \
         patch("api.routers.wallet_monitor.get_wallet_alert_settings", return_value=settings), \
         patch("api.routers.wallet_monitor.get_user_trust_tier", return_value="known"), \
         patch("api.routers.wallet_monitor._fetch_balance", new=AsyncMock(return_value=(1.0, "TON"))):
        resp = await client.get(
            "/api/wallet-monitor/overview", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200
    payload = resp.json()
    # Free snapshot_count = 1 → 只回 1 個錢包
    assert len(payload["wallets"]) == 1
    assert payload["entitlement"]["tier"] == "free"
    assert payload["entitlement"]["can_use_scheduled_monitoring"] is False


@pytest.mark.asyncio
async def test_overview_premium_keeps_all_wallets_when_gate_on(client):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQp"})
    settings = {
        "monitored_wallets": [
            {"address": "EQbbb1", "chain": "ton"},
            {"address": "EQbbb2", "chain": "ton"},
            {"address": "EQbbb3", "chain": "ton"},
        ],
        "alerts": {"incoming": {"enabled": True, "min_amount_ton": 10}},
        "channels": {"in_app": True},
    }
    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_mocked_user("EQp"))), \
         patch("api.routers.wallet_monitor.WALLET_MONITOR_PREMIUM_GATE_ENABLED", True), \
         patch("api.routers.wallet_monitor.resolve_entitlement_for_user", _ent_fn(PREMIUM)), \
         patch("api.routers.wallet_monitor.get_wallet_alert_settings", return_value=settings), \
         patch("api.routers.wallet_monitor.get_user_trust_tier", return_value="known"), \
         patch("api.routers.wallet_monitor._fetch_balance", new=AsyncMock(return_value=(1.0, "TON"))):
        resp = await client.get(
            "/api/wallet-monitor/overview", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200
    assert len(resp.json()["wallets"]) == 3
    assert resp.json()["entitlement"]["is_premium"] is True


# --------------------------------------------------------------------------- #
# PUT /settings                                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_put_settings_free_blocked_from_alerts_when_gate_on(client):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQs"})
    settings = {
        "monitored_wallets": [],
        "alerts": {"incoming": {"enabled": True, "min_amount_ton": 10}},  # 啟用排程警示
        "channels": {"in_app": True},
    }
    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_mocked_user("EQs"))), \
         patch("api.routers.wallet_monitor.WALLET_MONITOR_PREMIUM_GATE_ENABLED", True), \
         patch("api.routers.wallet_monitor.resolve_entitlement_for_user", _ent_fn(FREE)):
        resp = await client.put(
            "/api/wallet-monitor/settings",
            headers={"Authorization": f"Bearer {token}"},
            json={"settings": settings},
        )
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["upgrade_required"] is True
    assert detail["reason"] == "premium_required_for_scheduled_monitoring"


@pytest.mark.asyncio
async def test_put_settings_premium_allowed_alerts_when_gate_on(client):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQs"})
    settings = {
        "monitored_wallets": [],
        "alerts": {"incoming": {"enabled": True, "min_amount_ton": 10}},
        "channels": {"in_app": True},
    }
    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_mocked_user("EQs"))), \
         patch("api.routers.wallet_monitor.WALLET_MONITOR_PREMIUM_GATE_ENABLED", True), \
         patch("api.routers.wallet_monitor.resolve_entitlement_for_user", _ent_fn(PREMIUM)), \
         patch("api.routers.wallet_monitor.save_wallet_alert_settings", return_value=(True, "ok")):
        resp = await client.put(
            "/api/wallet-monitor/settings",
            headers={"Authorization": f"Bearer {token}"},
            json={"settings": settings},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_put_settings_free_disabled_alerts_allowed(client):
    """Free 仍可存「全部關閉」的設定（不啟用排程警示）。"""
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQs"})
    settings = {
        "monitored_wallets": [],
        "alerts": {"incoming": {"enabled": False, "min_amount_ton": 10}},
        "channels": {"in_app": True},
    }
    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_mocked_user("EQs"))), \
         patch("api.routers.wallet_monitor.WALLET_MONITOR_PREMIUM_GATE_ENABLED", True), \
         patch("api.routers.wallet_monitor.resolve_entitlement_for_user", _ent_fn(FREE)), \
         patch("api.routers.wallet_monitor.save_wallet_alert_settings", return_value=(True, "ok")):
        resp = await client.put(
            "/api/wallet-monitor/settings",
            headers={"Authorization": f"Bearer {token}"},
            json={"settings": settings},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_put_settings_gate_off_allows_free_alerts(client):
    """gate 關閉時 Free 啟用警示不擋（既有行為、安全漸進）。"""
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQs"})
    settings = {
        "monitored_wallets": [],
        "alerts": {"incoming": {"enabled": True, "min_amount_ton": 10}},
        "channels": {"in_app": True},
    }
    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_mocked_user("EQs"))), \
         patch("api.routers.wallet_monitor.WALLET_MONITOR_PREMIUM_GATE_ENABLED", False), \
         patch("api.routers.wallet_monitor.resolve_entitlement_for_user", _ent_fn(FREE)), \
         patch("api.routers.wallet_monitor.save_wallet_alert_settings", return_value=(True, "ok")):
        resp = await client.put(
            "/api/wallet-monitor/settings",
            headers={"Authorization": f"Bearer {token}"},
            json={"settings": settings},
        )
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# POST /wallets                                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_add_wallet_free_blocked_when_gate_on(client):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQa"})
    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_mocked_user("EQa"))), \
         patch("api.routers.wallet_monitor.WALLET_MONITOR_PREMIUM_GATE_ENABLED", True), \
         patch("api.routers.wallet_monitor.resolve_entitlement_for_user", _ent_fn(FREE)):
        resp = await client.post(
            "/api/wallet-monitor/wallets",
            headers={"Authorization": f"Bearer {token}"},
            json={"address": "UQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqEBI"},
        )
    assert resp.status_code == 403
    assert resp.json()["detail"]["action"] == "add_wallet"


@pytest.mark.asyncio
async def test_add_wallet_premium_allowed_when_gate_on(client):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQa"})
    existing = {
        "monitored_wallets": [],
        "alerts": {"incoming": {"enabled": False, "min_amount_ton": 10}},
        "channels": {"in_app": True},
    }
    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_mocked_user("EQa"))), \
         patch("api.routers.wallet_monitor.WALLET_MONITOR_PREMIUM_GATE_ENABLED", True), \
         patch("api.routers.wallet_monitor.resolve_entitlement_for_user", _ent_fn(PREMIUM)), \
         patch("api.routers.wallet_monitor.get_wallet_alert_settings", return_value=existing), \
         patch("api.routers.wallet_monitor.save_wallet_alert_settings", return_value=(True, "ok")):
        resp = await client.post(
            "/api/wallet-monitor/wallets",
            headers={"Authorization": f"Bearer {token}"},
            json={"address": "UQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqEBI"},
        )
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# GET /settings 回傳 entitlement                                               #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_get_settings_returns_entitlement(client):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQg"})
    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_mocked_user("EQg"))), \
         patch("api.routers.wallet_monitor.resolve_entitlement_for_user", _ent_fn(FREE)), \
         patch("api.routers.wallet_monitor.get_wallet_alert_settings", return_value=None):
        resp = await client.get(
            "/api/wallet-monitor/settings", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200
    assert resp.json()["entitlement"]["tier"] == "free"


# --------------------------------------------------------------------------- #
# GET /events 與 /wallet/{address}/detail 的快照範圍 gate（review P1#3）       #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_events_free_other_address_blocked_when_gate_on(client):
    """gate 開啟時 Free 查他人地址的 events → 403（只能查自己的快照）。"""
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQev"})
    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_mocked_user("EQev"))), \
         patch("api.routers.wallet_monitor.WALLET_MONITOR_PREMIUM_GATE_ENABLED", True), \
         patch("api.routers.wallet_monitor.resolve_entitlement_for_user", _ent_fn(FREE)):
        resp = await client.get(
            "/api/wallet-monitor/events?address=EQsomeoneelse",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403
    assert resp.json()["detail"]["action"] == "view_events"


@pytest.mark.asyncio
async def test_events_free_own_wallet_allowed(client):
    """Free 查自己錢包（不帶 address，預設=登入錢包）→ 200。

    user_id 要是合法 TON 地址：「自己的錢包」只認合法錢包（tg_… 填自己的 id 不算）。
    """
    from api.deps import create_access_token

    own = "UQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqEBI"
    token = create_access_token(data={"sub": own})
    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_mocked_user(own))), \
         patch("api.routers.wallet_monitor.WALLET_MONITOR_PREMIUM_GATE_ENABLED", True), \
         patch("api.routers.wallet_monitor.resolve_entitlement_for_user", _ent_fn(FREE)), \
         patch("api.routers.wallet_monitor.fetch_events", new=AsyncMock(return_value=[])):
        resp = await client.get(
            "/api/wallet-monitor/events", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_events_premium_any_address_allowed(client):
    """Premium 查任意地址 → 200。"""
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQevp"})
    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_mocked_user("EQevp"))), \
         patch("api.routers.wallet_monitor.WALLET_MONITOR_PREMIUM_GATE_ENABLED", True), \
         patch("api.routers.wallet_monitor.resolve_entitlement_for_user", _ent_fn(PREMIUM)), \
         patch("api.routers.wallet_monitor.fetch_events", new=AsyncMock(return_value=[])):
        resp = await client.get(
            "/api/wallet-monitor/events?address=EQanyone",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_wallet_detail_free_other_address_blocked_when_gate_on(client):
    """gate 開啟時 Free 查他人錢包明細 → 403。"""
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQdet"})
    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_mocked_user("EQdet"))), \
         patch("api.routers.wallet_monitor.WALLET_MONITOR_PREMIUM_GATE_ENABLED", True), \
         patch("api.routers.wallet_monitor.resolve_entitlement_for_user", _ent_fn(FREE)):
        resp = await client.get(
            "/api/wallet-monitor/wallet/EQsomeoneelse/detail",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403
    assert resp.json()["detail"]["action"] == "view_wallet_detail"


@pytest.mark.asyncio
async def test_events_gate_off_allows_free_other_address(client):
    """gate 關閉時 Free 查他人 → 不擋（既有行為）。"""
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQevo"})
    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_mocked_user("EQevo"))), \
         patch("api.routers.wallet_monitor.WALLET_MONITOR_PREMIUM_GATE_ENABLED", False), \
         patch("api.routers.wallet_monitor.fetch_events", new=AsyncMock(return_value=[])):
        resp = await client.get(
            "/api/wallet-monitor/events?address=EQanyone",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
