"""Wallet Monitor 測試 — 規則比對 + channel 分發 + router

涵蓋：
    - 單元：match_rules（轉入/轉出/詐騙/大額 + min_amount + 去重）
    - 單元：channels（InApp/Telegram adapter，mock 發送）
    - router：overview/events/settings/wallets CRUD

對應 docs/plans/2026-08-08-wallet-monitor-dashboard-design.md。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# 單元：match_rules
# ──────────────────────────────────────────────────────────────────────────────

# 常用測試地址
WALLET = "0:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER = "0:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
# 規則比對測的是「之後的輪詢」：游標停在更早的一筆（沒有游標＝第一次輪詢，只記基準不警示）
PREV_CURSOR = "e_prev"


def _mk_event(event_id, sender, recipient, amount_nano, ts=1700000000, is_scam=False):
    """組一個 TonAPI event（含 TonTransfer action）。"""
    return {
        "event_id": event_id,
        "timestamp": ts,
        "is_scam": is_scam,
        "actions": [
            {
                "type": "TonTransfer",
                "TonTransfer": {
                    "amount": amount_nano,
                    "sender": {"address": sender},
                    "recipient": {"address": recipient},
                },
            }
        ],
    }


def _default_settings(**overrides):
    s = {
        "monitored_wallets": [WALLET],
        "alerts": {
            "incoming": {"enabled": True, "min_amount_ton": 10},
            "outgoing": {"enabled": True, "min_amount_ton": 10},
            "scam": {"enabled": True},
            "large_out": {"enabled": False, "threshold_ton": 500},
            "custom": [],
        },
        "channels": {"in_app": True, "telegram": True},
    }
    s.update(overrides)
    return s


def test_match_incoming():
    """轉入 ≥ min_amount → incoming event。"""
    from core.wallet_monitor.engine import match_rules

    events = [_mk_event("e1", OTHER, WALLET, int(50 * 1e9))]  # 50 TON 轉入
    with patch("core.wallet_monitor.engine.get_json", return_value=PREV_CURSOR), \
         patch("core.wallet_monitor.engine.set_json"):
        matched = match_rules(WALLET, events, _default_settings(), user_id="u1")

    assert len(matched) == 1
    assert matched[0].event_type == "incoming"
    assert matched[0].amount_ton == 50.0
    assert matched[0].counterparty == OTHER


def test_match_incoming_below_min_amount_ignored():
    """轉入 < min_amount → 不觸發（防吵）。"""
    from core.wallet_monitor.engine import match_rules

    events = [_mk_event("e1", OTHER, WALLET, int(1 * 1e9))]  # 1 TON < 10
    with patch("core.wallet_monitor.engine.get_json", return_value=PREV_CURSOR), \
         patch("core.wallet_monitor.engine.set_json"):
        matched = match_rules(WALLET, events, _default_settings(), user_id="u1")

    assert matched == []


def test_match_outgoing():
    """轉出 ≥ min_amount → outgoing event。"""
    from core.wallet_monitor.engine import match_rules

    events = [_mk_event("e1", WALLET, OTHER, int(30 * 1e9))]  # 30 TON 轉出
    with patch("core.wallet_monitor.engine.get_json", return_value=PREV_CURSOR), \
         patch("core.wallet_monitor.engine.set_json"):
        matched = match_rules(WALLET, events, _default_settings(), user_id="u1")

    assert len(matched) == 1
    assert matched[0].event_type == "outgoing"


def test_match_large_out_threshold():
    """大額轉出 > 閾值 → large_out event。"""
    from core.wallet_monitor.engine import match_rules

    settings = _default_settings()
    settings["alerts"]["large_out"] = {"enabled": True, "threshold_ton": 500}
    events = [_mk_event("e1", WALLET, OTHER, int(800 * 1e9))]  # 800 TON
    with patch("core.wallet_monitor.engine.get_json", return_value=PREV_CURSOR), \
         patch("core.wallet_monitor.engine.set_json"):
        matched = match_rules(WALLET, events, settings, user_id="u1")

    assert len(matched) == 2  # outgoing(800≥10) + large_out(800≥500)
    types = {m.event_type for m in matched}
    assert "large_out" in types


def test_match_scam_contact():
    """event 標記 is_scam → scam_contact event。"""
    from core.wallet_monitor.engine import match_rules

    events = [_mk_event("e1", OTHER, WALLET, int(5 * 1e9), is_scam=True)]
    with patch("core.wallet_monitor.engine.get_json", return_value=PREV_CURSOR), \
         patch("core.wallet_monitor.engine.set_json"):
        matched = match_rules(WALLET, events, _default_settings(), user_id="u1")

    assert any(m.event_type == "scam_contact" for m in matched)
    assert all(m.is_scam for m in matched if m.event_type == "scam_contact")


def test_match_skips_already_processed():
    """已處理的 event_id → 不重複觸發（去重）。"""
    from core.wallet_monitor.engine import match_rules

    events = [_mk_event("e_old", OTHER, WALLET, int(50 * 1e9))]
    with patch("core.wallet_monitor.engine.get_json", return_value="e_old"), \
         patch("core.wallet_monitor.engine.set_json"):
        matched = match_rules(WALLET, events, _default_settings(), user_id="u1")

    assert matched == []  # 碰到 last_id 就 break


def test_match_disabled_alert_not_triggered():
    """用戶關閉轉出 → 轉出不觸發。"""
    from core.wallet_monitor.engine import match_rules

    settings = _default_settings()
    settings["alerts"]["outgoing"] = {"enabled": False, "min_amount_ton": 10}
    events = [_mk_event("e1", WALLET, OTHER, int(30 * 1e9))]
    with patch("core.wallet_monitor.engine.get_json", return_value=PREV_CURSOR), \
         patch("core.wallet_monitor.engine.set_json"):
        matched = match_rules(WALLET, events, settings, user_id="u1")

    assert matched == []


# ──────────────────────────────────────────────────────────────────────────────
# 單元：channels（adapter 分發）
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_telegram_channel_sends_to_bound_user():
    """TG adapter：反查 binding → sendMessage。"""
    from core.wallet_monitor.channels import TelegramChannel
    from core.wallet_monitor.engine import AlertEvent

    event = AlertEvent(
        wallet_address=WALLET, event_id="e1", event_type="outgoing",
        amount_ton=50.0, counterparty=OTHER, is_scam=False, timestamp=1700000000,
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    ch = TelegramChannel()
    with patch("core.wallet_monitor.channels.get_binding_by_user_id",
               return_value={"telegram_id": 123456789}), \
         patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token"}), \
         patch("core.wallet_monitor.channels.httpx.AsyncClient", return_value=mock_client):
        ok = await ch.send("EQuser", event)

    assert ok is True
    # 驗證 sendMessage 帶 chat_id
    call_kwargs = mock_client.post.call_args.kwargs
    assert call_kwargs["json"]["chat_id"] == 123456789


@pytest.mark.asyncio
async def test_telegram_channel_no_binding_returns_false():
    """用戶未綁 TG → 回 False（不發）。"""
    from core.wallet_monitor.channels import TelegramChannel
    from core.wallet_monitor.engine import AlertEvent

    event = AlertEvent(WALLET, "e1", "outgoing", 50.0, OTHER, False, 1700000000)
    ch = TelegramChannel()
    with patch("core.wallet_monitor.channels.get_binding_by_user_id", return_value=None), \
         patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token"}):
        ok = await ch.send("EQuser", event)

    assert ok is False


@pytest.mark.asyncio
async def test_inapp_channel_creates_notification():
    """InApp adapter：create_notification 被呼叫。"""
    from core.wallet_monitor.channels import InAppChannel
    from core.wallet_monitor.engine import AlertEvent

    event = AlertEvent(WALLET, "e1", "incoming", 50.0, OTHER, False, 1700000000)
    ch = InAppChannel()
    with patch("core.database.notifications.create_notification", return_value={}):
        ok = await ch.send("EQuser", event)

    assert ok is True


@pytest.mark.asyncio
async def test_dispatcher_respects_channel_config():
    """用戶關掉 telegram channel → 只送 in_app。"""
    from core.wallet_monitor.channels import (
        AlertDispatcher,
        InAppChannel,
        TelegramChannel,
    )
    from core.wallet_monitor.engine import AlertEvent

    inapp = AsyncMock(spec=InAppChannel)
    inapp.name = "in_app"
    inapp.send = AsyncMock(return_value=True)
    tg = AsyncMock(spec=TelegramChannel)
    tg.name = "telegram"
    tg.send = AsyncMock(return_value=True)

    disp = AlertDispatcher(channels=[inapp, tg])
    event = AlertEvent(WALLET, "e1", "outgoing", 50.0, OTHER, False, 1700000000)
    settings = {"channels": {"in_app": True, "telegram": False}}  # TG 關掉

    sent = await disp.dispatch("EQuser", [event], settings)

    assert sent == 1
    inapp.send.assert_awaited_once()
    tg.send.assert_not_awaited()


# ──────────────────────────────────────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_overview_returns_wallets_and_trust(client):
    from api.deps import create_access_token

    # 沒設監測錢包時預設看登入錢包——user_id 要是真的 TON 地址才會被認出來
    login = "EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N"
    token = create_access_token(data={"sub": login})
    mocked_user = {
        "user_id": login, "username": "U", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.wallet_monitor.get_wallet_alert_settings", return_value=None), \
         patch("api.routers.wallet_monitor.get_user_trust_tier", return_value="known_wallet"), \
         patch("api.routers.wallet_monitor._fetch_balance", new=AsyncMock(return_value=(123.45, "TON"))):
        response = await client.get(
            "/api/wallet-monitor/overview", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["wallets"][0]["balance_ton"] == 123.45
    assert payload["trust"]["tier"] == "known_wallet"


@pytest.mark.asyncio
async def test_events_returns_simplified(client):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQev"})
    mocked_user = {
        "user_id": "EQev", "username": "U", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }
    fake_events = [
        {
            "event_id": "e1", "timestamp": 1700000000, "is_scam": False,
            "actions": [{"type": "TonTransfer", "TonTransfer": {
                "amount": 5000000000,
                "sender": {"address": OTHER},
                "recipient": {"address": "EQev"},
            }}],
        }
    ]

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.wallet_monitor.fetch_events", new=AsyncMock(return_value=fake_events)):
        response = await client.get(
            "/api/wallet-monitor/events", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["events"][0]["actions"][0]["amount_ton"] == 5.0


@pytest.mark.asyncio
async def test_update_settings_valid(client):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQset"})
    mocked_user = {
        "user_id": "EQset", "username": "U", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }
    settings = {
        "monitored_wallets": [],
        "alerts": {"incoming": {"enabled": True, "min_amount_ton": 10}},
        "channels": {"in_app": True, "telegram": True},
    }

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.wallet_monitor.save_wallet_alert_settings", return_value=(True, "ok")):
        response = await client.put(
            "/api/wallet-monitor/settings",
            headers={"Authorization": f"Bearer {token}"},
            json={"settings": settings},
        )

    assert response.status_code == 200
    assert response.json()["success"] is True


@pytest.mark.asyncio
async def test_update_settings_missing_alerts_rejected(client):
    """缺 alerts 結構 → 400。"""
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQset"})
    mocked_user = {
        "user_id": "EQset", "username": "U", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)):
        response = await client.put(
            "/api/wallet-monitor/settings",
            headers={"Authorization": f"Bearer {token}"},
            json={"settings": {"monitored_wallets": []}},  # 缺 alerts
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_add_wallet_valid(client):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQadd"})
    mocked_user = {
        "user_id": "EQadd", "username": "U", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }
    existing = {
        "monitored_wallets": [],
        "alerts": {"incoming": {"enabled": False, "min_amount_ton": 10}},
        "channels": {"in_app": True, "telegram": True},
    }

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.wallet_monitor.get_wallet_alert_settings", return_value=existing), \
         patch("api.routers.wallet_monitor.save_wallet_alert_settings", return_value=(True, "ok")):
        response = await client.post(
            "/api/wallet-monitor/wallets",
            headers={"Authorization": f"Bearer {token}"},
            json={"address": "UQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqEBI"},
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert len(response.json()["settings"]["monitored_wallets"]) == 1


@pytest.mark.asyncio
async def test_add_wallet_invalid_address_rejected(client):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQadd"})
    mocked_user = {
        "user_id": "EQadd", "username": "U", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)):
        response = await client.post(
            "/api/wallet-monitor/wallets",
            headers={"Authorization": f"Bearer {token}"},
            json={"address": "not-a-ton-address"},
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_remove_wallet(client):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQrm"})
    mocked_user = {
        "user_id": "EQrm", "username": "U", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }
    target = "EQremoveaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    existing = {
        "monitored_wallets": [target],
        "alerts": {"incoming": {"enabled": False, "min_amount_ton": 10}},
        "channels": {"in_app": True, "telegram": True},
    }

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.wallet_monitor.get_wallet_alert_settings", return_value=existing), \
         patch("api.routers.wallet_monitor.save_wallet_alert_settings", return_value=(True, "ok")):
        response = await client.delete(
            f"/api/wallet-monitor/wallets/{target}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["settings"]["monitored_wallets"] == []
