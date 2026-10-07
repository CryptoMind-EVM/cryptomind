"""帳本鏈上同步（2026-09-12）：地址工具、EVM／TON 來源解析、轉帳→條目對應、
sync 冪等與跳過規則、holdings 合計、API 契約、schema／cron／文件接線守衛。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.onchain import addresses, evm_source, holdings, sync, ton_source
from core.onchain.transfers import Transfer, normalize_symbol

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
TON_FRIENDLY = "EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N"
TON_RAW = "0:83dfd552e63729b472fcbcc8c45ebcc6691702558b68ec7527e1ba403a0f31a8"
ME = "0x3304e22ddaa22bcdc5fca2269b418046ae7b566a"
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"


# ── 地址 ─────────────────────────────────────────────────────────────────────


class TestAddresses:
    def test_ton_friendly_to_raw_matches_tonapi_format(self):
        assert addresses.ton_friendly_to_raw(TON_FRIENDLY) == TON_RAW
        # UQ（non-bounceable）同一把 hash
        assert (
            addresses.ton_friendly_to_raw(
                "UQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqEBI"
            )
            == TON_RAW
        )
        assert addresses.ton_friendly_to_raw(TON_RAW.upper()) == TON_RAW
        assert addresses.ton_friendly_to_raw("garbage") is None
        assert addresses.same_ton_address(TON_FRIENDLY, TON_RAW)

    def test_evm_and_short(self):
        assert addresses.is_evm_address(ME) and not addresses.is_evm_address(
            TON_FRIENDLY
        )
        assert (
            addresses.short_address("0x912CE59144191C1204E64559FE8253a0e49E6548")
            == "0x912C…6548"
        )
        assert addresses.short_address("abc") == "abc"

    def test_symbol_aliases(self):
        assert normalize_symbol("USD₮") == "USDT" and normalize_symbol("weth") == "ETH"
        assert normalize_symbol("cbBTC") == "BTC" and normalize_symbol("aero") == "AERO"


# ── EVM 來源（RPC 全部假造） ───────────────────────────────────────────────────


def _fake_rpc(calls):
    def rpc(method, params, url=None):
        calls.append((method, params))
        if method == "eth_blockNumber":
            return hex(1000)
        if method == "eth_getBalance":
            return hex(2 * 10**18)
        if method == "eth_call":
            data = params[0]["data"]
            if data.startswith("0x70a08231"):  # balanceOf
                return hex(1_500_000)
            if data == "0x95d89b41":  # symbol() → ABI string "AERO"
                return (
                    "0x"
                    + (32).to_bytes(32, "big").hex()
                    + (4).to_bytes(32, "big").hex()
                    + b"AERO".ljust(32, b"\0").hex()
                )
            if data == "0x313ce567":
                return hex(18)
        if method == "eth_getLogs":
            p = params[0]
            topics = p["topics"]
            if topics[1] is not None:  # 自己是 from
                return [
                    {
                        "address": "0xaero000000000000000000000000000000000001",
                        "topics": [
                            evm_source.ERC20_TRANSFER_TOPIC,
                            evm_source._pad_address(ME),
                            evm_source._pad_address("0x" + "b" * 40),
                        ],
                        "data": hex(5 * 10**18),
                        "blockNumber": hex(999),
                        "transactionHash": "0xout",
                        "logIndex": "0x0",
                    }
                ]
            return [
                {
                    "address": USDC,
                    "topics": [
                        evm_source.ERC20_TRANSFER_TOPIC,
                        evm_source._pad_address("0x" + "c" * 40),
                        evm_source._pad_address(ME),
                    ],
                    "data": hex(25_000_000),
                    "blockNumber": hex(998),
                    "transactionHash": "0xin",
                    "logIndex": "0x1",
                },
                # 同一筆 log 重複（from/to 兩次查詢可能重疊）→ 去重
                {
                    "address": USDC,
                    "topics": [
                        evm_source.ERC20_TRANSFER_TOPIC,
                        evm_source._pad_address("0x" + "c" * 40),
                        evm_source._pad_address(ME),
                    ],
                    "data": hex(25_000_000),
                    "blockNumber": hex(998),
                    "transactionHash": "0xin",
                    "logIndex": "0x1",
                },
            ]
        if method == "eth_getBlockByNumber":
            return {"timestamp": hex(1_789_000_000)}
        raise AssertionError(method)

    return rpc


class TestEvmSource:
    def test_balances_native_plus_usdc_when_blockscout_unavailable(self, monkeypatch):
        calls = []
        monkeypatch.setattr(evm_source, "_rpc", _fake_rpc(calls))
        monkeypatch.setattr(evm_source, "usdc_contract", lambda: USDC)
        monkeypatch.setattr(
            evm_source, "fetch_blockscout_token_balances", lambda addr, chain: None
        )
        out = evm_source.fetch_evm_balances(ME, chain="base")
        assert out == [
            {"symbol": "ETH", "amount": 2.0, "contract": None},
            {"symbol": "USDC", "amount": 1.5, "contract": USDC},
        ]

    def test_balances_prefer_blockscout_full_token_list(self, monkeypatch):
        calls = []
        monkeypatch.setattr(evm_source, "_rpc", _fake_rpc(calls))
        monkeypatch.setattr(
            evm_source,
            "fetch_blockscout_token_balances",
            lambda addr, chain: [
                {
                    "symbol": "AERO",
                    "amount": 3.0,
                    "contract": "0xa",
                    "usd": 1.5,
                    "verified": True,
                }
            ],
        )
        out = evm_source.fetch_evm_balances(ME, chain="base")
        assert [o["symbol"] for o in out] == ["ETH", "AERO"]
        assert not any(m == "eth_call" for m, _ in calls), (
            "有 Blockscout 就不打 balanceOf"
        )

    def test_blockscout_token_balances_parse_and_spoof_filter(self, monkeypatch):
        class _Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return [
                    {
                        "value": "1500000",
                        "token": {
                            "type": "ERC-20",
                            "symbol": "USDC",
                            "decimals": "6",
                            "address": USDC,
                            "exchange_rate": "0.9999",
                            "circulating_market_cap": "74000000000",
                        },
                    },
                    # 同名假幣：估值超過真幣市值 → 當沒價
                    {
                        "value": str(402_690_263_538 * 10**18),
                        "token": {
                            "type": "ERC-20",
                            "symbol": "SAND",
                            "decimals": "18",
                            "address": "0xfake",
                            "exchange_rate": "0.036",
                            "circulating_market_cap": "106664328",
                        },
                    },
                    {
                        "value": "10",
                        "token": {
                            "type": "ERC-721",
                            "symbol": "NFT",
                            "decimals": None,
                            "address": "0xnft",
                        },
                    },
                    {
                        "value": "0",
                        "token": {
                            "type": "ERC-20",
                            "symbol": "ZERO",
                            "decimals": "18",
                            "address": "0x0",
                        },
                    },
                    {
                        "value": "5000000000000000000",
                        "token": {
                            "type": "ERC-20",
                            "symbol": "JUNK",
                            "decimals": "18",
                            "address": "0xjunk",
                            "exchange_rate": None,
                        },
                    },
                ]

        monkeypatch.setattr(evm_source.httpx, "get", lambda *a, **k: _Resp())
        out = evm_source.fetch_blockscout_token_balances(ME, chain="base")
        assert [(o["symbol"], o["usd"]) for o in out] == [
            ("USDC", 1.5),
            ("SAND", None),
            ("JUNK", None),
        ]
        assert out[0]["verified"] is True and out[1]["verified"] is False

    def test_blockscout_404_means_no_tokens_and_errors_mean_none(self, monkeypatch):
        class _NotFound:
            status_code = 404

            def raise_for_status(self):
                pass

            def json(self):
                return {}

        monkeypatch.setattr(evm_source.httpx, "get", lambda *a, **k: _NotFound())
        assert evm_source.fetch_blockscout_token_balances(ME, chain="base") == []
        monkeypatch.setattr(
            evm_source.httpx,
            "get",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")),
        )
        assert evm_source.fetch_blockscout_token_balances(ME, chain="base") is None
        assert evm_source.fetch_blockscout_token_balances(ME, chain="nochain") is None

    def test_blockscout_url_override_and_disable(self, monkeypatch):
        monkeypatch.setenv("ONCHAIN_EVM_CHAIN", "base")
        monkeypatch.delenv("ONCHAIN_BLOCKSCOUT_URL", raising=False)
        assert evm_source.blockscout_base("base") == "https://base.blockscout.com"
        assert evm_source.blockscout_base("ethereum") == "https://eth.blockscout.com"
        monkeypatch.setenv("ONCHAIN_BLOCKSCOUT_URL", "https://my.explorer/")
        assert evm_source.blockscout_base("base") == "https://my.explorer"
        monkeypatch.setenv("ONCHAIN_BLOCKSCOUT_URL", "")
        assert evm_source.blockscout_base("base") is None  # 空字串＝停用

    def test_logs_to_transfers_dedupes_and_resolves_meta(self, monkeypatch):
        calls = []
        monkeypatch.setattr(evm_source, "_rpc", _fake_rpc(calls))
        evm_source._META_CACHE.clear()
        txs = evm_source.fetch_erc20_transfers_via_logs(
            ME, chain="base", lookback_days=0.01
        )
        assert {(t.tx_hash, t.direction, t.symbol, t.amount) for t in txs} == {
            ("0xout", "out", "AERO", 5.0),
            ("0xin", "in", "USDC", 25.0),
        }
        assert all(t.timestamp == 1_789_000_000 and t.chain == "base" for t in txs)
        # 區塊視窗：lookback 0.01 天／2 秒一塊 = 432 塊 → 1 段（≤2000）
        assert sum(1 for m, _ in calls if m == "eth_getLogs") == 2

    def test_etherscan_path_maps_native_and_tokens(self, monkeypatch):
        def fake(chain_id, params, api_key, *, endpoint=evm_source.ETHERSCAN_V2):
            assert chain_id == 8453 and api_key == "k"
            if params["action"] == "txlist":
                return [
                    {
                        "hash": "0x1",
                        "timeStamp": "1789000000",
                        "value": str(10**18),
                        "from": ME,
                        "to": "0x" + "d" * 40,
                        "isError": "0",
                        "gasUsed": "21000",
                        "gasPrice": str(10**9),
                    },
                    {
                        "hash": "0x2",
                        "timeStamp": "1",
                        "value": str(10**18),
                        "from": ME,
                        "to": "0x" + "d" * 40,
                        "isError": "0",
                    },  # 太舊
                    {
                        "hash": "0x3",
                        "timeStamp": "1789000000",
                        "value": "0",
                        "from": ME,
                        "to": "0x" + "d" * 40,
                        "isError": "0",
                    },  # 合約呼叫無轉帳
                ]
            return [
                {
                    "hash": "0x4",
                    "timeStamp": "1789000000",
                    "value": "3000000",
                    "tokenDecimal": "6",
                    "tokenSymbol": "USDC",
                    "from": "0x" + "e" * 40,
                    "to": ME,
                    "contractAddress": USDC,
                },
            ]

        monkeypatch.setattr(evm_source, "_etherscan", fake)
        txs = evm_source.fetch_evm_transfers_via_etherscan(
            ME, chain="base", api_key="k", since_ts=1_000_000
        )
        by = {t.tx_hash: t for t in txs}
        assert set(by) == {"0x1", "0x4"}
        assert by["0x1"].direction == "out" and by["0x1"].fee_native == pytest.approx(
            21000 * 1e9 / 1e18
        )
        assert (
            by["0x4"].direction == "in"
            and by["0x4"].amount == 3.0
            and by["0x4"].contract == USDC
        )

    def test_fetch_evm_transfers_chooses_path(self, monkeypatch):
        monkeypatch.delenv("ETHERSCAN_SERVICE_API_KEY", raising=False)
        monkeypatch.setenv("ONCHAIN_EVM_CHAIN", "base")
        # 沒 key：Blockscout（Etherscan 相容端點）優先，原生＋ERC-20 都有
        monkeypatch.delenv("ONCHAIN_BLOCKSCOUT_URL", raising=False)
        with patch.object(
            evm_source, "fetch_evm_transfers_via_etherscan", return_value=[]
        ) as bs:
            txs, notes = evm_source.fetch_evm_transfers(ME, since_ts=0, lookback_days=1)
        assert notes == ["blockscout"]
        assert bs.call_args.kwargs["endpoint"] == "https://base.blockscout.com/api"
        assert bs.call_args.kwargs["api_key"] == ""
        # Blockscout 掛了 → 退 RPC logs（只有 ERC-20），note 兩段都留
        with (
            patch.object(
                evm_source,
                "fetch_evm_transfers_via_etherscan",
                side_effect=RuntimeError("x"),
            ),
            patch.object(
                evm_source, "fetch_erc20_transfers_via_logs", return_value=[]
            ) as logs,
        ):
            txs, notes = evm_source.fetch_evm_transfers(ME, since_ts=0, lookback_days=1)
        logs.assert_called_once()
        assert notes == [
            "blockscout:RuntimeError",
            "rpc_logs_only:native_transfers_not_included",
        ]
        # 停用 Blockscout → 直接 RPC logs
        monkeypatch.setenv("ONCHAIN_BLOCKSCOUT_URL", "")
        with patch.object(
            evm_source, "fetch_erc20_transfers_via_logs", return_value=[]
        ) as logs:
            txs, notes = evm_source.fetch_evm_transfers(ME, since_ts=0, lookback_days=1)
        logs.assert_called_once()
        assert notes == ["rpc_logs_only:native_transfers_not_included"]
        with patch.object(
            evm_source, "fetch_evm_transfers_via_etherscan", return_value=[]
        ) as es:
            txs, notes = evm_source.fetch_evm_transfers(
                ME, since_ts=0, lookback_days=1, api_key="k", chains=["base", "bogus"]
            )
        es.assert_called_once()
        assert notes == []


# ── TON 來源 ─────────────────────────────────────────────────────────────────


class TestTonSource:
    def test_events_to_transfers(self, monkeypatch):
        events = {
            "events": [
                {
                    "event_id": "ev1",
                    "timestamp": 1_789_000_000,
                    "in_progress": False,
                    "is_scam": False,
                    "extra": -6_000_000,
                    "actions": [
                        {
                            "type": "TonTransfer",
                            "TonTransfer": {
                                "amount": 1_500_000_000,
                                "sender": {"address": TON_RAW},
                                "recipient": {"address": "0:" + "a" * 64},
                                "comment": "hi",
                            },
                        },
                        {
                            "type": "JettonTransfer",
                            "JettonTransfer": {
                                "amount": "2500000",
                                "sender": {"address": "0:" + "b" * 64},
                                "recipient": {"address": TON_RAW},
                                "jetton": {
                                    "symbol": "USD₮",
                                    "decimals": 6,
                                    "verification": "whitelist",
                                    "address": "0:" + "c" * 64,
                                },
                            },
                        },
                        {"type": "SmartContractExec"},
                    ],
                },
                {
                    "event_id": "old",
                    "timestamp": 10,
                    "in_progress": False,
                    "actions": [
                        {
                            "type": "TonTransfer",
                            "TonTransfer": {
                                "amount": 1,
                                "sender": {"address": TON_RAW},
                                "recipient": {"address": "0:" + "a" * 64},
                            },
                        }
                    ],
                },
                {
                    "event_id": "scam",
                    "timestamp": 1_789_000_001,
                    "in_progress": False,
                    "is_scam": True,
                    "actions": [
                        {
                            "type": "TonTransfer",
                            "TonTransfer": {
                                "amount": 10**9,
                                "sender": {"address": "0:" + "f" * 64},
                                "recipient": {"address": TON_RAW},
                            },
                        }
                    ],
                },
                {
                    "event_id": "pending",
                    "timestamp": 1_789_000_002,
                    "in_progress": True,
                    "actions": [],
                },
            ]
        }
        monkeypatch.setattr(ton_source, "_get", lambda path, params=None: events)
        txs = ton_source.fetch_ton_transfers(TON_FRIENDLY, since_ts=1_000)
        assert [(t.tx_hash, t.direction, t.symbol, t.amount) for t in txs] == [
            ("ev1", "out", "TON", 1.5),
            ("ev1:1", "in", "USD₮", 2.5),
            ("scam", "in", "TON", 1.0),
        ]
        assert txs[0].fee_native == pytest.approx(0.006) and txs[1].fee_native == 0
        assert txs[2].is_scam is True and txs[1].price_symbol == "USDT"

    def test_balances(self, monkeypatch):
        def fake(path, params=None):
            if path.endswith("/jettons"):
                return {
                    "balances": [
                        {
                            "balance": "4280000000000",
                            "price": {"prices": {"USD": 2}},
                            "jetton": {
                                "symbol": "X",
                                "decimals": 9,
                                "verification": "whitelist",
                                "address": "0:1",
                            },
                        }
                    ]
                }
            return {"balance": 3_000_000_000}

        monkeypatch.setattr(ton_source, "_get", fake)
        out = ton_source.fetch_ton_balances(TON_FRIENDLY)
        assert out[0] == {"symbol": "TON", "amount": 3.0, "contract": None}
        assert (
            out[1]["symbol"] == "X"
            and out[1]["amount"] == 4280.0
            and out[1]["usd"] == 8560.0
        )


# ── 轉帳 → 帳本 ──────────────────────────────────────────────────────────────


class _Repo:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.added = []

    def has_onchain_entry(self, chain, tx_hash, symbol, side):
        return (chain, tx_hash, symbol.upper(), side) in self.existing

    def add_entry(self, **kw):
        if kw["tx_hash"] == "dup-at-insert":
            return {"ok": False, "duplicate": True}
        self.added.append(kw)
        return {"ok": True, "id": len(self.added)}


class TestSync:
    def test_transfer_to_entry_mapping(self):
        tr = Transfer(
            chain="base",
            tx_hash="0xabc",
            timestamp=1_789_000_000,
            direction="out",
            symbol="ETH",
            amount=0.5,
            counterparty="0x" + "d" * 40,
            fee_native=0.0003,
            native_symbol="ETH",
        )
        e = sync.transfer_to_entry(tr, 2500.0, "zh-TW")
        assert (
            e["side"] == "sell"
            and e["quantity"] == 0.5
            and e["price"] == 2500.0
            and e["currency"] == "USD"
        )
        assert (
            e["fee"] == 0.0003
            and e["fee_currency"] == "ETH"
            and e["source"] == "onchain"
        )
        assert e["chain"] == "base" and e["tx_hash"] == "0xabc"
        assert e["traded_at"] == datetime.fromtimestamp(1_789_000_000, tz=timezone.utc)
        assert (
            "鏈上轉出" in e["note"]
            and "0xdddd…dddd" in e["note"]
            and "base" in e["note"]
        )
        e_in = sync.transfer_to_entry(tr, 1.0, "en")
        assert e_in["fee_currency"] == "ETH"

    def test_sync_user_skips_dupes_unpriced_and_scam(self, monkeypatch):
        transfers = [
            Transfer(
                chain="base",
                tx_hash="0x1",
                timestamp=1_789_000_000,
                direction="in",
                symbol="USDC",
                amount=25.0,
                native_symbol="ETH",
            ),
            Transfer(
                chain="base",
                tx_hash="0x2",
                timestamp=1_789_000_000,
                direction="out",
                symbol="AERO",
                amount=5.0,
                native_symbol="ETH",
            ),  # 有價
            Transfer(
                chain="base",
                tx_hash="0x3",
                timestamp=1_789_000_000,
                direction="in",
                symbol="JUNKAIRDROP",
                amount=1e9,
                native_symbol="ETH",
            ),  # 無價
            Transfer(
                chain="base",
                tx_hash="0x4",
                timestamp=1_789_000_000,
                direction="in",
                symbol="USDC",
                amount=1.0,
                native_symbol="ETH",
            ),  # 已存在
            Transfer(
                chain="base",
                tx_hash="dup-at-insert",
                timestamp=1_789_000_000,
                direction="in",
                symbol="USDC",
                amount=1.0,
                native_symbol="ETH",
            ),
            Transfer(
                chain="ton",
                tx_hash="scam",
                timestamp=1_789_000_000,
                direction="in",
                symbol="TON",
                amount=1.0,
                native_symbol="TON",
                is_scam=True,
            ),
        ]
        repo = _Repo(existing={("base", "0x4", "USDC", "buy")})
        recorded = {}
        monkeypatch.setattr(
            sync,
            "fetch_wallet_transfers",
            lambda w, since_ts, lookback_days: (transfers, ["n1"]),
        )
        monkeypatch.setattr(
            sync.store,
            "get_status",
            lambda uid: {"enabled": True, "last_synced_at": None},
        )
        monkeypatch.setattr(
            sync.store, "record_result", lambda uid, res, at=None: recorded.update(res)
        )
        monkeypatch.setattr(
            sync, "usd_price", lambda sym, cache: {"USDC": 1.0, "AERO": 0.8}.get(sym)
        )
        result = sync.sync_user(
            "u1",
            now_ts=1_789_100_000,
            language="en",
            repo=repo,
            wallets=[{"chain": "evm", "address": ME, "is_primary": True}],
        )
        assert (
            result["added"] == 2
            and result["duplicates"] == 2
            and result["skipped_scam"] == 1
        )
        assert result["skipped_unpriced"] == ["JUNKAIRDROP"] and result["notes"] == [
            "n1"
        ]
        assert result["lookback_days"] == sync.DEFAULT_FIRST_LOOKBACK_DAYS  # 第一次同步
        assert [(a["symbol"], a["side"], a["price"]) for a in repo.added] == [
            ("USDC", "buy", 1.0),
            ("AERO", "sell", 0.8),
        ]
        assert recorded["added"] == 2 and "entries" not in recorded

    def test_daily_lookback_after_first_sync_and_wallet_failure_isolated(
        self, monkeypatch
    ):
        def fetch(w, since_ts, lookback_days):
            if w["chain"] == "ton":
                raise RuntimeError("tonapi down")
            return [
                Transfer(
                    chain="base",
                    tx_hash="0x9",
                    timestamp=1_789_000_000,
                    direction="in",
                    symbol="USDC",
                    amount=2.0,
                    native_symbol="ETH",
                )
            ], []

        repo = _Repo()
        monkeypatch.setattr(sync, "fetch_wallet_transfers", fetch)
        monkeypatch.setattr(
            sync.store,
            "get_status",
            lambda uid: {"enabled": True, "last_synced_at": datetime.now(timezone.utc)},
        )
        monkeypatch.setattr(sync.store, "record_result", lambda uid, res, at=None: None)
        monkeypatch.setattr(sync, "usd_price", lambda sym, cache: 1.0)
        result = sync.sync_user(
            "u1",
            now_ts=1_789_100_000,
            language="en",
            repo=repo,
            wallets=[
                {"chain": "ton", "address": TON_FRIENDLY},
                {"chain": "evm", "address": ME},
            ],
        )
        assert result["lookback_days"] == sync.DEFAULT_LOOKBACK_DAYS
        assert (
            result["errors"] == 1
            and result["added"] == 1
            and "ton:fetch:RuntimeError" in result["notes"]
        )


# ── holdings ─────────────────────────────────────────────────────────────────


class TestHoldings:
    def test_collect_holdings_totals_and_ordering(self, monkeypatch):
        monkeypatch.setattr(
            holdings,
            "usd_price",
            lambda sym, cache: {"ETH": 2000.0, "USDC": 1.0, "TON": 2.0}.get(
                normalize_symbol(sym)
            ),
        )
        monkeypatch.setattr(
            holdings.evm_source,
            "fetch_evm_balances",
            lambda addr, chain=None: [
                {"symbol": "ETH", "amount": 0.5, "contract": None},
                {"symbol": "USDC", "amount": 100.0, "contract": USDC},
                {"symbol": "ETH", "amount": 0, "contract": None},
            ],
        )
        monkeypatch.setattr(holdings.evm_source, "platform_chain", lambda: "base")
        monkeypatch.setattr(
            holdings.ton_source,
            "fetch_ton_balances",
            lambda addr: [
                {"symbol": "TON", "amount": 10.0, "contract": None},
                {
                    "symbol": "SPAM",
                    "amount": 9e9,
                    "contract": "0:1",
                    "usd": None,
                    "verified": False,
                },
            ],
        )
        with patch(
            "core.onchain.store.list_wallets",
            return_value=[
                {"chain": "evm", "address": ME, "is_primary": True},
                {"chain": "ton", "address": TON_FRIENDLY, "is_primary": False},
            ],
        ):
            data = holdings.collect_holdings("u1")
        assert [w["chain"] for w in data["wallets"]] == ["evm", "ton"]
        evm = data["wallets"][0]
        assert evm["network"] == "base" and evm["short"] == "0x3304…566a"
        assert [a["symbol"] for a in evm["assets"]] == ["ETH", "USDC"] and evm[
            "usd_total"
        ] == 1100.0
        ton = data["wallets"][1]
        assert [a["symbol"] for a in ton["assets"]] == ["TON", "SPAM"] and ton[
            "assets"
        ][1]["usd"] is None
        assert data["usd_total"] == 1120.0
        assert data["totals"][0] == {
            "symbol": "ETH",
            "amount": 0.5,
            "usd": 1000.0,
            "priced": True,
        }
        assert (
            data["totals"][-1]["symbol"] == "SPAM" and data["totals"][-1]["usd"] is None
        )

    def test_wallet_source_failure_marks_not_ok(self, monkeypatch):
        monkeypatch.setattr(
            holdings.ton_source,
            "fetch_ton_balances",
            lambda addr: (_ for _ in ()).throw(RuntimeError("x")),
        )
        w = holdings.collect_wallet_holdings({"chain": "ton", "address": TON_FRIENDLY})
        assert w["ok"] is False and w["assets"] == [] and w["usd_total"] == 0


# ── API ──────────────────────────────────────────────────────────────────────


def _client():
    from api.routers import onchain as mod

    app = FastAPI()
    app.state.limiter = mod.limiter
    app.include_router(mod.router)

    async def fake_user():
        return {"user_id": "u1", "membership_tier": "free"}

    app.dependency_overrides[mod.get_current_user] = fake_user
    return TestClient(app), mod


class TestApi:
    def test_holdings_and_status_and_sync(self):
        client, mod = _client()
        try:
            mod.limiter.reset()
        except Exception:  # noqa: BLE001
            pass
        with patch.object(
            mod,
            "collect_holdings",
            return_value={"wallets": [], "totals": [], "usd_total": 0.0},
        ):
            res = client.get("/api/wallet/holdings")
        assert (
            res.status_code == 200
            and res.json()["success"] is True
            and res.json()["wallets"] == []
        )

        with (
            patch.object(
                mod.store,
                "get_status",
                return_value={
                    "enabled": False,
                    "last_synced_at": datetime(2026, 9, 12, tzinfo=timezone.utc),
                    "last_result": {"added": 1},
                },
            ),
            patch.object(
                mod.store,
                "list_wallets",
                return_value=[{"chain": "evm", "address": ME, "is_primary": True}],
            ),
        ):
            res = client.get("/api/journal/onchain/status")
            assert res.json()["enabled"] is False and res.json()[
                "last_synced_at"
            ].startswith("2026-09-12")
            with patch.object(mod.store, "set_enabled") as se:
                assert (
                    client.put(
                        "/api/journal/onchain/status", json={"enabled": True}
                    ).status_code
                    == 200
                )
            se.assert_called_once_with("u1", True)
            with patch.object(mod, "sync_user", return_value={"added": 2}) as su:
                res = client.post("/api/journal/onchain/sync")
            assert res.status_code == 200 and res.json()["result"] == {"added": 2}
            su.assert_called_once()

    def test_sync_without_wallets_is_400(self):
        client, mod = _client()
        try:
            mod.limiter.reset()
        except Exception:  # noqa: BLE001
            pass
        with patch.object(mod.store, "list_wallets", return_value=[]):
            res = client.post("/api/journal/onchain/sync")
        assert res.status_code == 400 and "Bind a wallet" in res.json()["detail"]


# ── 接線守衛 ─────────────────────────────────────────────────────────────────




def test_cron_isolates_user_failures():
    from scripts import cron_onchain_sync as cron

    with (
        patch("core.onchain.store.list_sync_candidates", return_value=["a", "b", "c"]),
        patch(
            "core.onchain.sync.sync_user",
            side_effect=[{"added": 2}, RuntimeError("x"), {"added": 1}],
        ),
    ):
        summary = cron.run()
    assert summary == {"users": 3, "added": 3, "failed": 1}
