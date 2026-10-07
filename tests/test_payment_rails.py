"""Unit tests for payment rails (multichain design Part C)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import api.payment_rails as pr

# --- quotes -------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_stable_micro_adds_unique_suffix():
    micros = {pr.resolve_stable_micro(12.0) for _ in range(20)}
    # anchor micro = 12_000_000; suffix 1..4999 keeps amount within a hair of $12
    for m in micros:
        assert 12_000_001 <= m <= 12_004_999
    assert len(micros) > 1  # suffix varies per order


@pytest.mark.unit
def test_resolve_rail_quote_evm_usdc(evm_recv):
    quote = pr.resolve_rail_quote("premium_monthly", pr.RAIL_EVM_USDC, 12.0)
    assert quote["chain"] == "evm"
    assert quote["asset"] == "USDC"
    assert quote["decimals"] == 6
    assert quote["micro"] > 12_000_000


@pytest.mark.unit
def test_resolve_rail_quote_rejects_ton_native_and_unavailable():
    with pytest.raises(HTTPException):
        pr.resolve_rail_quote("premium_monthly", "ton_native", 12.0)
    with patch.object(pr, "EVM_USDC_RECEIVING_ADDRESS", ""):
        with pytest.raises(HTTPException) as exc:
            pr.resolve_rail_quote("premium_monthly", pr.RAIL_EVM_USDC, 12.0)
    assert exc.value.status_code == 503


# --- 訂閱統一（2026-09-09 DANNY：USDC on Base only）---------------------------


@pytest.mark.unit
def test_subscription_ton_native_not_orderable():
    """ton_native 停止下單（2026-09-25 常數與 claim 分支一併移除）。"""
    assert pr.rail_available("ton_native") is False


@pytest.mark.unit
def test_ton_usdt_rail_removed():
    """TON USD₮ 軌從沒在正式環境驗過任何一筆（toncenter v3 參數名、回應 key
    都錯），2026-09-25 整條移除：常數、驗證函式、jetton master 設定都不在了，
    帶這個 rail 名稱來報價 → 503。"""
    import core.config as cfg

    assert not hasattr(pr, "RAIL_TON_USDT")
    assert not hasattr(pr, "verify_ton_usdt_payment")
    assert not hasattr(cfg, "TON_USDT_JETTON_MASTER")
    assert pr.rail_available("ton_usdt") is False
    with pytest.raises(HTTPException) as exc:
        pr.resolve_rail_quote("premium_monthly", "ton_usdt", 12.0)
    assert exc.value.status_code == 503


@pytest.mark.unit
def test_subscription_evm_usdc_orderable_when_configured(evm_recv, monkeypatch):
    monkeypatch.setattr(pr, "EVM_RPC_URL", "https://rpc.example")
    assert pr.rail_available(pr.RAIL_EVM_USDC) is True


# --- USDC verification (mocked eth_getLogs) -----------------------------------


def _mock_rpc(logs, latest_hex="0x5208", block_ts_hex="0x0", calls=None):
    """Mock httpx.AsyncClient.post: blockNumber / getLogs / getBlockByNumber.

    ``calls`` (optional list) receives every JSON-RPC request body so tests can
    assert on the topics filter actually sent.
    """
    head = MagicMock()
    head.json.return_value = {"result": latest_hex}
    head.raise_for_status = MagicMock()
    logs_resp = MagicMock()
    logs_resp.json.return_value = {"result": logs}
    logs_resp.raise_for_status = MagicMock()
    block = MagicMock()
    block.json.return_value = {"result": {"timestamp": block_ts_hex}}
    block.raise_for_status = MagicMock()

    client = MagicMock()

    async def _post(url, json=None, **kwargs):
        if calls is not None:
            calls.append(json)
        method = (json or {}).get("method")
        if method == "eth_blockNumber":
            return head
        if method == "eth_getBlockByNumber":
            return block
        return logs_resp

    client.post = _post
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


_PAYER = "0x" + "cd" * 20
_MICRO = 12_003_699


@pytest.fixture
def evm_recv(monkeypatch):
    """Rail availability needs a receiving address; confirmations pinned to 8."""
    monkeypatch.setattr(pr, "EVM_USDC_RECEIVING_ADDRESS", "0x" + "ab" * 20)
    monkeypatch.setattr(pr, "EVM_CONFIRMATIONS", 8)
    return pr.EVM_USDC_RECEIVING_ADDRESS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_evm_usdc_matches_transfer(evm_recv):
    logs = [
        {
            "data": hex(_MICRO),  # exact per-order suffix
            "blockNumber": hex(0x5200),  # 8 confirmations behind latest
            "transactionHash": "0xdeadbeef",
            "topics": [pr.ERC20_TRANSFER_TOPIC],
        }
    ]
    with patch.object(pr.httpx, "AsyncClient", _mock_rpc(logs)):
        payment = await pr.verify_evm_usdc_payment(
            _MICRO, payer_addresses=[_PAYER]
        )
    assert payment["tx_hash"] == "0xdeadbeef"
    assert payment["confirmations"] == 8


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_evm_usdc_requires_payer_binding(evm_recv):
    # fail-closed: no bound addresses → attribution impossible → no scan
    with pytest.raises(HTTPException) as exc:
        await pr.verify_evm_usdc_payment(_MICRO, payer_addresses=[])
    assert "bound" in str(exc.value.detail)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_evm_usdc_filters_topics_to_payer(evm_recv):
    calls = []
    logs = []
    with patch.object(
        pr.httpx, "AsyncClient", _mock_rpc(logs, calls=calls)
    ):
        with pytest.raises(HTTPException):
            await pr.verify_evm_usdc_payment(_MICRO, payer_addresses=[_PAYER])
    getlogs = next(c for c in calls if c.get("method") == "eth_getLogs")
    topics = getlogs["params"][0]["topics"]
    assert topics[1] == [pr._topic_for_address(_PAYER)]  # from-binding present
    assert topics[2] == pr._topic_for_address(pr.EVM_USDC_RECEIVING_ADDRESS)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_evm_usdc_overpay_rejected(evm_recv):
    # exact-match rule: a larger transfer must not satisfy a smaller order
    logs = [
        {"data": hex(_MICRO + 5), "blockNumber": hex(0x5200), "transactionHash": "0xdeadbeef"}
    ]
    with patch.object(pr.httpx, "AsyncClient", _mock_rpc(logs)):
        with pytest.raises(HTTPException) as exc:
            await pr.verify_evm_usdc_payment(_MICRO, payer_addresses=[_PAYER])
    assert exc.value.status_code == 400


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_evm_usdc_predating_order_rejected(evm_recv):
    # exact amount + confirmed, but the block predates order issuance
    logs = [
        {"data": hex(_MICRO), "blockNumber": hex(0x5200), "transactionHash": "0xdeadbeef"}
    ]
    issued_ts = 1_000_000  # block ts 900_000 < issued-120 → transfer predates order
    with patch.object(
        pr.httpx, "AsyncClient", _mock_rpc(logs, block_ts_hex=hex(900_000))
    ):
        with pytest.raises(HTTPException) as exc:
            await pr.verify_evm_usdc_payment(
                _MICRO, payer_addresses=[_PAYER], issued_ts=issued_ts
            )
    assert "predates" in str(exc.value.detail)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_evm_usdc_unconfirmed_rejected(evm_recv):
    logs = [
        {
            "data": hex(_MICRO),
            "blockNumber": hex(0x5206),  # only 2 confirmations
            "transactionHash": "0xdeadbeef",
        }
    ]
    with patch.object(pr.httpx, "AsyncClient", _mock_rpc(logs)):
        with pytest.raises(HTTPException) as exc:
            await pr.verify_evm_usdc_payment(_MICRO, payer_addresses=[_PAYER])
    assert "confirmations" in str(exc.value.detail)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_evm_usdc_no_match(evm_recv):
    with patch.object(pr.httpx, "AsyncClient", _mock_rpc([])):
        with pytest.raises(HTTPException) as exc:
            await pr.verify_evm_usdc_payment(_MICRO, payer_addresses=[_PAYER])
    assert exc.value.status_code == 400


# --- order token integration with rails ---------------------------------------


@pytest.mark.unit
def test_stable_order_token_roundtrip_carries_rail_and_micro():
    from api.evm_verification import (
        create_multichain_order,
        verify_multichain_order_token,
    )

    token = create_multichain_order(
        "user-1",
        "premium_monthly",
        pr.RAIL_EVM_USDC,
        12.0,
        quoted_amount=12.003699,
        extra={"memo": "cmabc", "micro": 12_003_699, "recv": "0xabc"},
    )
    decoded = verify_multichain_order_token(token["order_token"], "user-1")
    assert decoded["rail"] == "evm_usdc"
    assert decoded["micro"] == 12_003_699
    assert decoded["memo"] == "cmabc"
    assert decoded["iat"] > 0  # order issue time — time-window attribution check


# --- receiving_address（論壇打賞付給作者；2026-09-25）---------------------------
#
# 打賞直接付到作者自己的 EVM 地址，所以兩個驗證函式多一個 receiving_address。
# 預設（None）必須完全等於以前：平台收款地址。premium 的呼叫點不帶這個參數。

_AUTHOR = "0x" + "a1" * 20
_TX = "0x" + "9f" * 32


def _mock_receipt_rpc(logs, latest_hex="0x5208", status="0x1", block_ts_hex="0x0"):
    """Mock eth_getTransactionReceipt / eth_blockNumber / eth_getBlockByNumber."""
    receipt = MagicMock()
    receipt.json.return_value = {"result": {"status": status, "logs": logs}}
    receipt.raise_for_status = MagicMock()
    head = MagicMock()
    head.json.return_value = {"result": latest_hex}
    head.raise_for_status = MagicMock()
    block = MagicMock()
    block.json.return_value = {"result": {"timestamp": block_ts_hex}}
    block.raise_for_status = MagicMock()

    client = MagicMock()

    async def _post(url, json=None, **kwargs):
        method = (json or {}).get("method")
        if method == "eth_blockNumber":
            return head
        if method == "eth_getBlockByNumber":
            return block
        return receipt

    client.post = _post
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


def _transfer_log(to_addr, micro=_MICRO, payer=_PAYER):
    return {
        "address": pr.EVM_USDC_CONTRACT,
        "topics": [
            pr.ERC20_TRANSFER_TOPIC,
            pr._topic_for_address(payer),
            pr._topic_for_address(to_addr),
        ],
        "data": hex(micro),
        "blockNumber": hex(0x5200),
    }


@pytest.mark.unit
def test_receiving_address_defaults_to_none_on_both_verifiers():
    """預設值是 None（呼叫時才解析成平台地址）——寫死成模組常數會讓 monkeypatch 失效。"""
    import inspect

    for fn in (pr.verify_evm_usdc_payment, pr.verify_evm_usdc_tx):
        param = inspect.signature(fn).parameters["receiving_address"]
        assert param.default is None
        assert param.kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_explicit_receiver_filters_topics_to_that_address(evm_recv):
    calls = []
    with patch.object(pr.httpx, "AsyncClient", _mock_rpc([], calls=calls)):
        with pytest.raises(HTTPException):
            await pr.verify_evm_usdc_payment(
                _MICRO, payer_addresses=[_PAYER], receiving_address=_AUTHOR
            )
    getlogs = [c for c in calls if c.get("method") == "eth_getLogs"]
    assert getlogs, "should scan"
    for c in getlogs:
        assert c["params"][0]["topics"][2] == pr._topic_for_address(_AUTHOR)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_default_receiver_is_platform_address(evm_recv):
    calls = []
    with patch.object(pr.httpx, "AsyncClient", _mock_rpc([], calls=calls)):
        with pytest.raises(HTTPException):
            await pr.verify_evm_usdc_payment(_MICRO, payer_addresses=[_PAYER])
    getlogs = [c for c in calls if c.get("method") == "eth_getLogs"]
    assert getlogs
    for c in getlogs:
        assert c["params"][0]["topics"][2] == pr._topic_for_address(evm_recv)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_receipt_explicit_receiver_matches_transfer_to_author(evm_recv):
    rpc = _mock_receipt_rpc([_transfer_log(_AUTHOR)])
    with patch.object(pr.httpx, "AsyncClient", rpc):
        payment = await pr.verify_evm_usdc_tx(
            _TX, _MICRO, payer_addresses=[_PAYER], receiving_address=_AUTHOR
        )
    assert payment["amount_micro"] == _MICRO


@pytest.mark.unit
@pytest.mark.asyncio
async def test_receipt_explicit_receiver_rejects_transfer_to_platform(evm_recv):
    """付給平台的轉帳不能拿來當打賞（反之亦然）。"""
    rpc = _mock_receipt_rpc([_transfer_log(evm_recv)])
    with patch.object(pr.httpx, "AsyncClient", rpc):
        with pytest.raises(HTTPException) as exc:
            await pr.verify_evm_usdc_tx(
                _TX, _MICRO, payer_addresses=[_PAYER], receiving_address=_AUTHOR
            )
    assert exc.value.status_code == 400


@pytest.mark.unit
@pytest.mark.asyncio
async def test_receipt_default_receiver_is_platform_address(evm_recv):
    rpc = _mock_receipt_rpc([_transfer_log(evm_recv)])
    with patch.object(pr.httpx, "AsyncClient", rpc):
        payment = await pr.verify_evm_usdc_tx(_TX, _MICRO, payer_addresses=[_PAYER])
    assert payment["amount_micro"] == _MICRO
    rpc = _mock_receipt_rpc([_transfer_log(_AUTHOR)])
    with patch.object(pr.httpx, "AsyncClient", rpc):
        with pytest.raises(HTTPException):
            await pr.verify_evm_usdc_tx(_TX, _MICRO, payer_addresses=[_PAYER])


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "   ", "0x123", "not-an-address", "0x" + "g" * 40])
async def test_explicit_bad_receiver_never_falls_back_to_platform(evm_recv, bad):
    """空字串／格式錯的收款地址不可退回平台地址（fail-closed），也不打 RPC。"""
    rpc = MagicMock(side_effect=AssertionError("must not reach the RPC"))
    with patch.object(pr.httpx, "AsyncClient", rpc):
        with pytest.raises(HTTPException) as exc:
            await pr.verify_evm_usdc_payment(
                _MICRO, payer_addresses=[_PAYER], receiving_address=bad
            )
        assert exc.value.status_code == 400
        with pytest.raises(HTTPException) as exc:
            await pr.verify_evm_usdc_tx(
                _TX, _MICRO, payer_addresses=[_PAYER], receiving_address=bad
            )
        assert exc.value.status_code == 400


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_receiver_works_without_platform_address(monkeypatch):
    """打賞不經平台：平台收款地址沒設也能驗作者收款；預設（平台）路徑照舊 503。"""
    monkeypatch.setattr(pr, "EVM_USDC_RECEIVING_ADDRESS", "")
    monkeypatch.setattr(pr, "EVM_CONFIRMATIONS", 8)
    rpc = _mock_receipt_rpc([_transfer_log(_AUTHOR)])
    with patch.object(pr.httpx, "AsyncClient", rpc):
        payment = await pr.verify_evm_usdc_tx(
            _TX, _MICRO, payer_addresses=[_PAYER], receiving_address=_AUTHOR
        )
    assert payment["amount_micro"] == _MICRO
    with pytest.raises(HTTPException) as exc:
        await pr.verify_evm_usdc_tx(_TX, _MICRO, payer_addresses=[_PAYER])
    assert exc.value.status_code == 503


@pytest.mark.unit
def test_premium_call_sites_do_not_pass_receiving_address():
    """premium 的兩個呼叫點不帶 receiving_address → 收款地址維持平台預設。"""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "api/routers/premium.py").read_text(
        encoding="utf-8"
    )
    calls = re.findall(
        r"await (verify_evm_usdc_(?:tx|payment))\((.*?)\n\s*\)", src, re.S
    )
    assert {name for name, _ in calls} == {
        "verify_evm_usdc_tx",
        "verify_evm_usdc_payment",
    }
    for _, args in calls:
        assert "receiving_address" not in args
