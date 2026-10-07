"""錢包監測「查自己的錢包」判定要比正規化後的地址。

以前 gate 用 ``address != user_id`` 字串比對：TON 身份 user_id 是 EQ…，查自己錢包的
UQ／raw 寫法被當成「他人」擋掉；EVM 身份 user_id 是 ``evm_<小寫地址>``，查自己的
0x 地址永遠對不上。監測清單的 label／chain 查找與移除也是字串比對，換個寫法就找不到。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.entitlement import resolve_entitlement

pytestmark = pytest.mark.unit

FREE = resolve_entitlement(tier="free")
PREMIUM = resolve_entitlement(tier="premium", is_expired=False)

OWN_EQ = "EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N"
OWN_UQ = "UQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqEBI"
OWN_RAW = "0:83dfd552e63729b472fcbcc8c45ebcc6691702558b68ec7527e1ba403a0f31a8"
OWN_TESTNET = "0QCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqPvC"
OTHER_EQ = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
OTHER_UQ = "UQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_p0p"
OTHER_RAW = "0:b113a994b5024a16719f69139328eb759596c38a25f59028b146fecdc3621dfe"
EVM_LOWER = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
EVM_CHECKSUM = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
EVM_UPPER_HEX = "0x" + EVM_LOWER[2:].upper()
OTHER_EVM = "0x" + "12ab" * 10

_WM = "api.routers.wallet_monitor"


def _user(uid: str, auth_method: str = "ton_wallet") -> dict:
    return {
        "user_id": uid,
        "username": "U",
        "role": "user",
        "auth_method": auth_method,
        "is_active": True,
    }


def _auth(uid: str) -> dict:
    from api.deps import create_access_token

    return {"Authorization": f"Bearer {create_access_token(data={'sub': uid})}"}


def _jetton_tool():
    tool = MagicMock()
    tool.ainvoke = AsyncMock(return_value={"jettons": [], "usd_total": 0})
    return tool


async def _get_events(client, uid, address, ent=FREE, auth_method="ton_wallet"):
    with (
        patch(
            "api.deps.user_repo.get_by_id",
            new=AsyncMock(return_value=_user(uid, auth_method)),
        ),
        patch(f"{_WM}.WALLET_MONITOR_PREMIUM_GATE_ENABLED", True),
        patch(f"{_WM}.resolve_entitlement_for_user", lambda _uid: ent),
        patch(f"{_WM}.fetch_events", new=AsyncMock(return_value=[])),
    ):
        return await client.get(
            "/api/wallet-monitor/events",
            params={"address": address},
            headers=_auth(uid),
        )


async def _get_detail(
    client,
    uid,
    address,
    ent=FREE,
    settings=None,
    balance=None,
    auth_method="ton_wallet",
):
    balance = balance or AsyncMock(return_value=(1.0, "TON"))
    with (
        patch(
            "api.deps.user_repo.get_by_id",
            new=AsyncMock(return_value=_user(uid, auth_method)),
        ),
        patch(f"{_WM}.WALLET_MONITOR_PREMIUM_GATE_ENABLED", True),
        patch(f"{_WM}.resolve_entitlement_for_user", lambda _uid: ent),
        patch(f"{_WM}.get_wallet_alert_settings", return_value=settings),
        patch(f"{_WM}._fetch_balance", new=balance),
        patch(f"{_WM}.fetch_events", new=AsyncMock(return_value=[])),
        patch(
            "core.tools.crypto_modules.ton_jetton_balances.get_ton_jetton_balances",
            new=_jetton_tool(),
        ),
        patch(
            "core.tools.crypto_modules.ton_price.get_ton_usd_price", return_value=2.0
        ),
    ):
        return await client.get(
            f"/api/wallet-monitor/wallet/{address}/detail", headers=_auth(uid)
        )


# ─── TON 身份：自己的錢包任何寫法都放行，別人的任何寫法都擋 ─────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address", [OWN_EQ, OWN_UQ, OWN_RAW, OWN_TESTNET, f"{OWN_UQ} "]
)
async def test_ton_identity_own_wallet_any_format_allowed(client, address):
    assert (await _get_events(client, OWN_EQ, address)).status_code == 200
    assert (await _get_detail(client, OWN_EQ, address)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("uid", [OWN_UQ, OWN_RAW])
async def test_ton_identity_stored_in_other_format_still_matches(client, uid):
    assert (await _get_events(client, uid, OWN_EQ)).status_code == 200
    assert (await _get_detail(client, uid, OWN_EQ)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("address", [OTHER_EQ, OTHER_UQ, OTHER_RAW, EVM_LOWER])
async def test_ton_identity_other_wallet_any_format_denied(client, address):
    resp = await _get_events(client, OWN_EQ, address)
    assert resp.status_code == 403
    assert resp.json()["detail"]["action"] == "view_events"
    resp = await _get_detail(client, OWN_EQ, address)
    assert resp.status_code == 403
    assert resp.json()["detail"]["action"] == "view_wallet_detail"


# ─── EVM 身份（user_id = evm_<小寫地址>）：大小寫／checksum 不影響 ──────────


@pytest.mark.asyncio
@pytest.mark.parametrize("address", [EVM_LOWER, EVM_CHECKSUM, EVM_UPPER_HEX])
async def test_evm_identity_own_wallet_any_case_allowed(client, address):
    uid = f"evm_{EVM_LOWER}"
    assert (
        await _get_events(client, uid, address, auth_method="evm_wallet")
    ).status_code == 200
    assert (
        await _get_detail(client, uid, address, auth_method="evm_wallet")
    ).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [OTHER_EVM, OTHER_EVM.upper().replace("0X", "0x"), OWN_EQ, f"evm_{EVM_LOWER}"],
)
async def test_evm_identity_other_wallet_denied(client, address):
    uid = f"evm_{EVM_LOWER}"
    assert (
        await _get_events(client, uid, address, auth_method="evm_wallet")
    ).status_code == 403
    assert (
        await _get_detail(client, uid, address, auth_method="evm_wallet")
    ).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uid, expected", [(f"evm_{EVM_LOWER}", EVM_LOWER), (OWN_EQ, OWN_EQ)]
)
async def test_events_without_address_defaults_to_own_wallet(client, uid, expected):
    with (
        patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_user(uid))),
        patch(f"{_WM}.WALLET_MONITOR_PREMIUM_GATE_ENABLED", True),
        patch(f"{_WM}.resolve_entitlement_for_user", lambda _uid: FREE),
        patch(f"{_WM}.fetch_events", new=AsyncMock(return_value=[])),
    ):
        resp = await client.get("/api/wallet-monitor/events", headers=_auth(uid))
    assert resp.status_code == 200
    assert resp.json()["address"] == expected


# ─── 非地址輸入：乾淨地擋掉／不炸 ───────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address", ["not-a-wallet", "EQshort", "0:zz", "0x123", "evm_"]
)
async def test_garbage_address_denied_cleanly_for_free(client, address):
    assert (await _get_events(client, OWN_EQ, address)).status_code == 403
    assert (await _get_detail(client, OWN_EQ, address)).status_code == 403


@pytest.mark.asyncio
async def test_non_wallet_identity_cannot_claim_a_wallet(client):
    """tg_／google 之類的身份不是錢包，查任何地址都不算「自己的」。"""
    assert (
        await _get_events(client, "tg_12345", OWN_UQ, auth_method="telegram")
    ).status_code == 403
    assert (
        await _get_detail(client, "tg_12345", EVM_LOWER, auth_method="telegram")
    ).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uid", ["tg_12345", "google_abc", "evm_not-an-address", "not-a-wallet-id"]
)
async def test_non_wallet_identity_echoing_its_own_id_is_not_own_wallet(client, uid):
    """address 填自己的 user_id 字串（tg_…）不算「自己的錢包」，不能繞過 premium gate。"""
    resp = await _get_events(client, uid, uid, auth_method="telegram")
    assert resp.status_code == 403
    resp = await _get_detail(client, uid, uid, auth_method="telegram")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_garbage_address_premium_returns_cleanly(client):
    resp = await _get_events(client, OWN_EQ, "not-a-wallet", ent=PREMIUM)
    assert resp.status_code == 200
    assert resp.json()["events"] == []


# ─── 監測清單：label／chain 查找、移除、去重也比正規化後的地址 ─────────────


@pytest.mark.asyncio
async def test_detail_finds_monitored_meta_across_formats(client):
    settings = {
        "monitored_wallets": [
            {"address": OTHER_EQ, "label": "cold", "reason": "watch", "chain": "ton"},
            {"address": EVM_CHECKSUM, "label": "hot", "reason": "", "chain": "eth"},
        ]
    }
    resp = await _get_detail(client, OWN_EQ, OTHER_UQ, ent=PREMIUM, settings=settings)
    assert resp.status_code == 200
    assert resp.json()["label"] == "cold"

    balance = AsyncMock(return_value=(3.0, "ETH"))
    resp = await _get_detail(
        client, OWN_EQ, EVM_LOWER, ent=PREMIUM, settings=settings, balance=balance
    )
    assert resp.json()["label"] == "hot"
    assert resp.json()["chain"] == "eth"
    balance.assert_awaited_once_with(EVM_LOWER, "eth")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored, requested",
    [(OTHER_EQ, OTHER_UQ), (OTHER_UQ, OTHER_RAW), (EVM_CHECKSUM, EVM_LOWER)],
)
async def test_remove_wallet_matches_any_format(client, stored, requested):
    existing = {
        "monitored_wallets": [
            {"address": stored, "label": "", "reason": "", "chain": "ton"}
        ]
    }
    with (
        patch(
            "api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_user(OWN_EQ))
        ),
        patch(f"{_WM}.get_wallet_alert_settings", return_value=existing),
        patch(f"{_WM}.save_wallet_alert_settings", return_value=(True, "ok")),
    ):
        resp = await client.delete(
            f"/api/wallet-monitor/wallets/{requested}", headers=_auth(OWN_EQ)
        )
    assert resp.status_code == 200
    assert resp.json()["settings"]["monitored_wallets"] == []


@pytest.mark.asyncio
async def test_add_wallet_evm_dedup_is_case_insensitive(client):
    existing = {
        "monitored_wallets": [
            {"address": EVM_CHECKSUM, "label": "", "reason": "", "chain": "eth"}
        ]
    }
    save = MagicMock(return_value=(True, "ok"))
    with (
        patch(
            "api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_user(OWN_EQ))
        ),
        patch(f"{_WM}.WALLET_MONITOR_PREMIUM_GATE_ENABLED", False),
        patch(f"{_WM}.get_wallet_alert_settings", return_value=existing),
        patch(f"{_WM}.save_wallet_alert_settings", new=save),
    ):
        resp = await client.post(
            "/api/wallet-monitor/wallets",
            headers=_auth(OWN_EQ),
            json={"address": EVM_LOWER, "chain": "eth"},
        )
    assert resp.status_code == 200
    assert resp.json()["message"] == "already_monitored"
    save.assert_not_called()
