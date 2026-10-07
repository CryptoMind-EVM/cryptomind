"""同一個 TON 錢包的每種寫法都要對到同一個身份（2026-09-25）。

TON 地址有 raw（``0:hex``／``-1:hex``）與 friendly（bounceable EQ／non-bounceable UQ／
testnet kQ／0Q，masterchain 是 Ef／Uf…）幾種寫法。TonAPI 事件裡的 sender／recipient
一律是 raw，使用者輸入與登入身份是 friendly——用前綴判斷或字串相等比對，
同一個錢包就會被當成兩個（錢包監測的轉入／轉出規則因此從沒命中過）。
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.onchain import addresses

TON_RAW = "0:83dfd552e63729b472fcbcc8c45ebcc6691702558b68ec7527e1ba403a0f31a8"
OTHER_RAW = "0:" + "b" * 64


def _crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def _friendly(raw: str, tag: int, *, urlsafe: bool = True) -> str:
    wc, h = raw.split(":")
    body = bytes([tag, 0xFF if wc == "-1" else 0x00]) + bytes.fromhex(h)
    full = body + _crc16(body).to_bytes(2, "big")
    enc = base64.urlsafe_b64encode if urlsafe else base64.b64encode
    return enc(full).decode()


BOUNCEABLE = _friendly(TON_RAW, 0x11)  # EQ…
NON_BOUNCEABLE = _friendly(TON_RAW, 0x51)  # UQ…
TESTNET_BOUNCEABLE = _friendly(TON_RAW, 0x91)  # kQ…
TESTNET_NON_BOUNCEABLE = _friendly(TON_RAW, 0xD1)  # 0Q…
ALL_FORMATS = [
    TON_RAW,
    TON_RAW.upper(),
    BOUNCEABLE,
    NON_BOUNCEABLE,
    TESTNET_BOUNCEABLE,
    TESTNET_NON_BOUNCEABLE,
]
OTHER_FRIENDLY = _friendly(OTHER_RAW, 0x51)


def test_fixture_matches_known_real_addresses():
    """自製的 friendly 產生器要跟真實地址一致，否則下面的測試沒意義。
    （兩個值都用 TonAPI /v2/address/{addr}/parse 核對過。）"""
    assert BOUNCEABLE == "EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N"
    assert NON_BOUNCEABLE == "UQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqEBI"
    assert TESTNET_BOUNCEABLE.startswith("kQ")
    assert TESTNET_NON_BOUNCEABLE.startswith("0Q")


# ── 正規化 ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", ALL_FORMATS)
def test_every_format_normalizes_to_same_raw(fmt):
    assert addresses.ton_friendly_to_raw(fmt) == TON_RAW
    assert addresses.is_ton_address(fmt)
    assert addresses.same_ton_address(fmt, BOUNCEABLE)


def test_masterchain_and_standard_base64_forms():
    master = "-1:" + "c" * 64
    for fmt in (_friendly(master, 0x11), _friendly(master, 0x51)):  # Ef… / Uf…
        assert fmt[:2] in ("Ef", "Uf")
        assert addresses.ton_friendly_to_raw(fmt) == master
    # 非 url-safe 的 base64（+ /）也是合法寫法
    std = _friendly(TON_RAW, 0x11, urlsafe=False)
    assert addresses.ton_friendly_to_raw(std) == TON_RAW


@pytest.mark.parametrize(
    "value",
    [
        "",
        "EQabc",  # 前綴對但不是地址
        "tg_123456",
        "evm_0x3304e22ddaa22bcdc5fca2269b418046ae7b566a",
        "0x3304e22ddaa22bcdc5fca2269b418046ae7b566a",
        "test-user-001",
        "A" * 48,  # 長度對、tag 不對
        "1:" + "a" * 64,  # 不存在的 workchain
        BOUNCEABLE[:-1] + "M",  # 打錯一個字（CRC 對不上）
    ],
)
def test_non_ton_values_rejected(value):
    assert not addresses.is_ton_address(value)
    assert addresses.ton_friendly_to_raw(value) is None


def test_different_wallets_are_not_same():
    assert not addresses.same_ton_address(BOUNCEABLE, OTHER_FRIENDLY)


# ── 錢包監測：規則比對 ────────────────────────────────────────────────────────


def _event(event_id, sender, recipient, amount_ton, ts=1_700_000_000):
    return {
        "event_id": event_id,
        "timestamp": ts,
        "is_scam": False,
        "actions": [
            {
                "type": "TonTransfer",
                "TonTransfer": {
                    "amount": int(amount_ton * 1e9),
                    "sender": {"address": sender},
                    "recipient": {"address": recipient},
                },
            }
        ],
    }


_SETTINGS = {
    "alerts": {
        "incoming": {"enabled": True, "min_amount_ton": 1},
        "outgoing": {"enabled": True, "min_amount_ton": 1},
        "scam": {"enabled": True},
        "large_out": {"enabled": False},
    }
}


@pytest.mark.parametrize("monitored", ALL_FORMATS)
def test_match_rules_incoming_and_outgoing_for_every_format(monitored):
    """TonAPI 事件的地址是 raw；監測地址不管用哪種寫法都要命中。"""
    from core.wallet_monitor.engine import match_rules

    events = [
        _event("e2", TON_RAW, OTHER_RAW, 20),  # 轉出
        _event("e1", OTHER_RAW, TON_RAW, 50),  # 轉入
    ]
    with (
        # 游標停在更早的一筆（沒有游標＝第一次輪詢，只記基準不警示）
        patch("core.wallet_monitor.engine.get_json", return_value="e0"),
        patch("core.wallet_monitor.engine.set_json"),
    ):
        matched = match_rules(monitored, events, _SETTINGS, user_id="u1")

    kinds = {(m.event_id, m.event_type) for m in matched}
    assert kinds == {("e2", "outgoing"), ("e1", "incoming")}
    incoming = next(m for m in matched if m.event_type == "incoming")
    assert incoming.counterparty == OTHER_RAW
    assert incoming.wallet_address == monitored


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", [TON_RAW, TESTNET_BOUNCEABLE, NON_BOUNCEABLE])
async def test_fetch_events_accepts_every_format(fmt):
    from core.wallet_monitor import engine

    resp = MagicMock(status_code=200)
    resp.json.return_value = {"events": [{"event_id": "e1"}], "next_from": 0}
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("core.wallet_monitor.engine.httpx.AsyncClient", return_value=client):
        events = await engine.fetch_events(fmt)
    assert [e["event_id"] for e in events] == ["e1"]


@pytest.mark.parametrize("fmt", ALL_FORMATS)
def test_ton_adapter_accepts_every_format(fmt):
    from core.wallet_monitor.adapters import detect_chain
    from core.wallet_monitor.adapters.ton_adapter import TonAdapter

    assert TonAdapter().is_valid_address(fmt)
    assert detect_chain(fmt) == "ton"


# ── 錢包監測：加入監測不因換寫法而重複 ─────────────────────────────────────────


def _user(uid):
    return {
        "user_id": uid,
        "username": "U",
        "role": "user",
        "auth_method": "ton_wallet",
        "is_active": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("new_format", [NON_BOUNCEABLE, TON_RAW, TESTNET_BOUNCEABLE])
async def test_add_wallet_same_wallet_other_format_is_already_monitored(
    client, new_format
):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": BOUNCEABLE})
    existing = {
        "monitored_wallets": [
            {"address": BOUNCEABLE, "label": "", "reason": "", "chain": "ton"}
        ],
        "alerts": {"incoming": {"enabled": False, "min_amount_ton": 10}},
        "channels": {"in_app": True},
    }
    save = MagicMock(return_value=(True, "ok"))
    with (
        patch(
            "api.deps.user_repo.get_by_id",
            new=AsyncMock(return_value=_user(BOUNCEABLE)),
        ),
        patch(
            "api.routers.wallet_monitor.get_wallet_alert_settings",
            return_value=existing,
        ),
        patch("api.routers.wallet_monitor.save_wallet_alert_settings", new=save),
    ):
        resp = await client.post(
            "/api/wallet-monitor/wallets",
            headers={"Authorization": f"Bearer {token}"},
            json={"address": new_format},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["message"] == "already_monitored"
    save.assert_not_called()


@pytest.mark.asyncio
async def test_add_wallet_rejects_prefix_only_garbage(client):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": BOUNCEABLE})
    with patch(
        "api.deps.user_repo.get_by_id", new=AsyncMock(return_value=_user(BOUNCEABLE))
    ):
        resp = await client.post(
            "/api/wallet-monitor/wallets",
            headers={"Authorization": f"Bearer {token}"},
            json={"address": "EQ" + "not-a-real-address-at-all-xx"},
        )
    assert resp.status_code == 400


# ── 身份：登入錢包 → 帳本同步錢包清單 ────────────────────────────────────────


@pytest.mark.parametrize("login_id", [TON_RAW, NON_BOUNCEABLE, TESTNET_NON_BOUNCEABLE])
def test_login_wallet_recognized_in_every_format(login_id):
    from core.onchain import store

    with patch("core.onchain.store.DatabaseBase.query_all", return_value=[]):
        wallets = store.list_wallets(login_id)
    assert wallets == [{"chain": "ton", "address": login_id, "is_primary": True}]


def test_non_ton_login_not_added_as_wallet():
    from core.onchain import store

    with patch("core.onchain.store.DatabaseBase.query_all", return_value=[]):
        assert (
            store.list_wallets("evm_0x3304e22ddaa22bcdc5fca2269b418046ae7b566a") == []
        )


def test_onchain_sync_candidates_include_every_ton_format():
    """帳本同步 cron 原本用 ``LIKE 'EQ%' OR LIKE 'UQ%'`` 認 TON 身份：用 raw／testnet／
    masterchain 寫法登入、沒另外綁錢包的人永遠不會被同步。SQL 粗篩地址形狀，
    CRC 由 is_ton_address 確認；有綁錢包的人不管 user_id 長怎樣都照舊是對象。"""
    import re

    from core.onchain import store

    master = "-1:" + "c" * 64
    ton_ids = ALL_FORMATS + [
        master,
        _friendly(master, 0x11),  # Ef…
        _friendly(master, 0x51),  # Uf…
        _friendly(master, 0x91),  # kf…
        _friendly(master, 0xD1),  # 0f…
        _friendly(TON_RAW, 0x11, urlsafe=False),
    ]
    bound = "evm_0x3304e22ddaa22bcdc5fca2269b418046ae7b566a"
    rows = [{"user_id": uid, "has_wallet": False} for uid in ton_ids] + [
        {"user_id": bound, "has_wallet": True},
        {"user_id": BOUNCEABLE[:-1] + "M", "has_wallet": False},  # CRC 對不上
        {"user_id": "A" * 48, "has_wallet": False},  # 長度對、不是地址
        {"user_id": "1:" + "a" * 64, "has_wallet": False},  # 不存在的 workchain
    ]
    with patch("core.onchain.store.DatabaseBase.query_all", return_value=rows) as q:
        assert store.list_sync_candidates() == ton_ids + [bound]

    sql = q.call_args.args[0]
    assert "LIKE 'EQ" not in sql and "LIKE 'UQ" not in sql
    # SQL 粗篩：friendly 一律 48 字元，raw 走正則（POSIX 這個子集跟 Python re 一致）
    assert "length(u.user_id) = 48" in sql
    raw_re = re.search(r"u\.user_id ~ '([^']+)'", sql).group(1)
    for uid in ton_ids:
        assert len(uid) == 48 or re.search(raw_re, uid), uid


# ── 身份：信任分數排程要涵蓋所有寫法的 TON 使用者 ──────────────────────────────


def test_trust_cron_includes_every_ton_format_and_skips_non_ton():
    from scripts import cron_recompute_trust as mod

    # (user_id, has_evm)——2026-09-25 起 cron 也重算 EVM 使用者（見 test_evm_first_identity）
    rows = [
        (BOUNCEABLE, False),
        (NON_BOUNCEABLE, False),
        (TON_RAW, False),
        ("tg_42", False),  # 舊 fallback 建出來的非錢包帳號
    ]
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    with patch.object(mod, "get_connection", return_value=conn):
        users = mod._fetch_wallet_users()
    assert [u for u, _ in users] == [BOUNCEABLE, NON_BOUNCEABLE, TON_RAW]
    sql = cur.execute.call_args.args[0]
    assert "LIKE 'EQ%'" not in sql


@pytest.mark.asyncio
async def test_onchain_signals_accepts_raw_address():
    from core.identity import onchain_signals

    resp = MagicMock(status_code=404)
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("core.identity.onchain_signals.httpx.AsyncClient", return_value=client):
        result = await onchain_signals.fetch_wallet_onchain_signals(TON_RAW)
    assert "error" not in result
    client.get.assert_awaited_once()


# ── 其他認 TON 錢包的地方：agent 身份錨點、錢包頁用的餘額工具 ─────────────────


@pytest.mark.parametrize("fmt", [TON_RAW, TESTNET_NON_BOUNCEABLE, NON_BOUNCEABLE])
def test_agent_identity_line_recognizes_every_ton_format(fmt):
    from core.agents.agents.cryptomind_agent import _user_identity_line

    assert "ton_proof" in _user_identity_line("en", None, fmt, "free")


def test_agent_identity_line_evm_is_not_ton():
    from core.agents.agents.cryptomind_agent import _user_identity_line

    line = _user_identity_line(
        "en", None, "0x3304e22ddaa22bcdc5fca2269b418046ae7b566a", "free"
    )
    assert "ton_proof" not in line


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module, tool",
    [
        ("core.tools.crypto_modules.ton_balance", "get_ton_balance"),
        ("core.tools.crypto_modules.ton_jetton_balances", "get_ton_jetton_balances"),
    ],
)
async def test_balance_tools_accept_raw_address(module, tool):
    """錢包監測頁的餘額／jetton 走這兩支工具；raw 地址要能查（網路層失敗是另一回事）。"""
    import importlib

    import httpx

    mod = importlib.import_module(module)
    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("offline"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch(f"{module}.httpx.AsyncClient", return_value=client):
        result = await getattr(mod, tool).ainvoke({"address": TON_RAW})
    assert result["error"] != "Invalid TON address"
    client.get.assert_awaited_once()


@pytest.mark.parametrize("value", [1, 12.5, {"a": 1}, ["EQ"], b"EQ"])
def test_non_string_input_is_not_an_address(value):
    """舊的 regex 版對非字串回 False；換成共用版後不能變成 500（#865 review）。"""
    from core.onchain.addresses import is_ton_address, ton_friendly_to_raw

    assert ton_friendly_to_raw(value) is None
    assert is_ton_address(value) is False
