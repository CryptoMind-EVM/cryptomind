"""錢包監測：兩次輪詢之間超過 20 筆也不能漏（2026-09-25）。

原本每次只抓最新 20 筆，上次處理到的事件不在這 20 筆裡時，更舊的就被默默跳過；
EVM 路徑甚至沒有游標，每次輪詢都把最新 20 筆重發一次。現在往舊翻頁直到碰到
上次處理到的那筆，翻頁有上限，超過會記 log。

游標按 (用戶, 鏈, 地址) 分開存：同一個錢包被兩個人監測，各自都要收到警示。
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

WALLET = "EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N"
WALLET_RAW = "0:83dfd552e63729b472fcbcc8c45ebcc6691702558b68ec7527e1ba403a0f31a8"
OTHER_RAW = "0:" + "b" * 64


# ── TON（TonAPI events，before_lt／next_from 翻頁） ──────────────────────────


def _ton_history(n):
    """n 筆事件，新的在前：e{n} … e1，lt 越新越大。每筆都是 1 TON 轉入。"""
    return [
        {
            "event_id": f"e{i}",
            "lt": 1000 + i,
            "timestamp": 1_700_000_000 + i,
            "is_scam": False,
            "actions": [
                {
                    "type": "TonTransfer",
                    "TonTransfer": {
                        "amount": 1_000_000_000,
                        "sender": {"address": OTHER_RAW},
                        "recipient": {"address": WALLET_RAW},
                    },
                }
            ],
        }
        for i in range(n, 0, -1)
    ]


def _tonapi_client(history, *, fail_on_call=None):
    """假 TonAPI：依 limit／before_lt 切頁，next_from＝本頁最舊一筆的 lt（沒了回 0）。"""
    calls = []

    async def get(url, params=None):
        params = dict(params or {})
        calls.append(params)
        resp = MagicMock()
        if fail_on_call is not None and len(calls) == fail_on_call:
            resp.status_code = 500
            return resp
        before = params.get("before_lt")
        rows = [e for e in history if before is None or e["lt"] < int(before)]
        page = rows[: int(params["limit"])]
        more = len(rows) > len(page)
        resp.status_code = 200
        resp.json.return_value = {
            "events": page,
            "next_from": page[-1]["lt"] if (page and more) else 0,
        }
        return resp

    client = MagicMock()
    client.get = AsyncMock(side_effect=get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client, calls


async def _fetch_new(history, cursor, **kw):
    from core.wallet_monitor import engine

    client, calls = _tonapi_client(history, **kw)
    with (
        patch("core.wallet_monitor.engine.httpx.AsyncClient", return_value=client),
        patch("core.wallet_monitor.engine.get_json", return_value=cursor),
    ):
        events = await engine.fetch_new_events(WALLET, user_id="u1")
    return events, calls


@pytest.mark.asyncio
async def test_more_than_one_page_of_new_events_are_all_fetched():
    history = _ton_history(50)
    events, calls = await _fetch_new(history, "e5")  # e50…e6 是新的（45 筆）
    assert [e["event_id"] for e in events] == [f"e{i}" for i in range(50, 5, -1)]
    assert len(calls) == 3
    assert [c.get("before_lt") for c in calls] == [None, 1031, 1011]


@pytest.mark.asyncio
async def test_all_new_events_become_alerts_and_cursor_moves_to_newest():
    from core.wallet_monitor.engine import match_rules

    history = _ton_history(50)
    events, _ = await _fetch_new(history, "e5")
    settings = {"alerts": {"incoming": {"enabled": True, "min_amount_ton": 0}}}
    with (
        patch("core.wallet_monitor.engine.get_json", return_value="e5"),
        patch("core.wallet_monitor.engine.set_json") as mark,
    ):
        matched = match_rules(WALLET, events, settings, user_id="u1")
    assert len(matched) == 45
    assert {m.event_type for m in matched} == {"incoming"}
    assert mark.call_args.args[1] == "e50"


@pytest.mark.asyncio
async def test_no_cursor_fetches_only_latest_page():
    """第一次輪詢（或游標過期）沒有「上次」可翻到，行為同以前：只抓最新一頁。"""
    events, calls = await _fetch_new(_ton_history(50), None)
    assert len(calls) == 1
    assert len(events) == 20


@pytest.mark.asyncio
async def test_cursor_on_first_page_needs_one_request():
    events, calls = await _fetch_new(_ton_history(50), "e48")
    assert len(calls) == 1
    assert [e["event_id"] for e in events] == ["e50", "e49"]


@pytest.mark.asyncio
async def test_page_cap_stops_and_logs(caplog):
    from core.wallet_monitor import engine

    history = _ton_history(500)
    with caplog.at_level(logging.WARNING, logger="core.wallet_monitor.engine"):
        events, calls = await _fetch_new(history, "gone")
    assert len(calls) == engine._MAX_EVENT_PAGES
    assert len(events) == engine._MAX_EVENT_PAGES * engine._MAX_EVENTS_PER_FETCH
    assert any("page cap" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_history_exhausted_before_cursor_returns_everything_without_cap_log(
    caplog,
):
    with caplog.at_level(logging.WARNING, logger="core.wallet_monitor.engine"):
        events, calls = await _fetch_new(_ton_history(30), "gone")
    assert len(events) == 30 and len(calls) == 2
    assert not any("page cap" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_later_page_failure_keeps_newer_events_and_logs(caplog):
    """翻到一半失敗：跟碰到翻頁上限一樣，已抓到的較新事件照常處理、記 warning。

    不能回空等下一輪——游標每輪都會刷新，TonAPI 第二頁一直 429 的話這個錢包會
    整個停擺（review 抓到）。"""
    with caplog.at_level(logging.WARNING, logger="core.wallet_monitor.engine"):
        events, calls = await _fetch_new(_ton_history(50), "e5", fail_on_call=2)
    assert [e["event_id"] for e in events] == [f"e{i}" for i in range(50, 30, -1)]
    assert len(calls) == 2
    assert any("older events skipped" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_first_page_failure_returns_nothing():
    events, calls = await _fetch_new(_ton_history(50), "e5", fail_on_call=1)
    assert events == [] and len(calls) == 1


def test_quiet_wallet_cursor_is_refreshed_every_poll():
    """沒有新事件也要重寫游標（刷新 TTL）。

    游標 TTL 30 分鐘、原本只在有新事件時才寫：安靜 30 分鐘的錢包游標過期，下一輪
    沒游標就把最新一頁全當新事件重發一次。以前轉入／轉出規則因為地址格式從沒命中，
    這個洞看不出來。"""
    from core.wallet_monitor.engine import _LAST_EVENT_KEY, _LAST_EVENT_TTL, match_rules

    settings = {"alerts": {"incoming": {"enabled": True, "min_amount_ton": 0}}}
    with (
        patch("core.wallet_monitor.engine.get_json", return_value="e5"),
        patch("core.wallet_monitor.engine.set_json") as mark,
    ):
        assert match_rules(WALLET, [], settings, user_id="u1") == []
    mark.assert_called_once_with(
        _LAST_EVENT_KEY.format(user_id="u1", address=WALLET), "e5", _LAST_EVENT_TTL
    )


@pytest.mark.asyncio
async def test_router_display_fetch_is_still_single_page():
    from core.wallet_monitor import engine

    client, calls = _tonapi_client(_ton_history(50))
    with patch("core.wallet_monitor.engine.httpx.AsyncClient", return_value=client):
        events = await engine.fetch_events(WALLET)
    assert len(events) == 20 and len(calls) == 1


@pytest.mark.asyncio
async def test_cron_uses_cursor_aware_fetch_for_ton():
    from scripts import cron_wallet_monitor as mod

    settings = {"monitored_wallets": [WALLET], "alerts": {}}
    with (
        patch.object(mod, "fetch_new_events", new=AsyncMock(return_value=[])) as fetch,
        patch.object(mod, "match_rules", return_value=[]),
    ):
        checked, alerts = await mod._process_user("u1", settings)
    fetch.assert_awaited_once_with(WALLET, user_id="u1")
    assert (checked, alerts) == (1, 0)


# ── EVM（Etherscan txlist，page 翻頁） ───────────────────────────────────────

EVM = "0x3304e22ddaa22bcdc5fca2269b418046ae7b566a"
PEER = "0x" + "2" * 40


def _evm_history(n):
    return [
        {
            "hash": f"0x{i:064x}",
            "from": PEER,
            "to": EVM,
            "value": str(10**18),
            "timeStamp": str(1_700_000_000 + i),
        }
        for i in range(n, 0, -1)
    ]


def _etherscan(history):
    calls = []

    def get(params, api_key=None):
        calls.append(dict(params))
        page, offset = int(params["page"]), int(params["offset"])
        rows = history[(page - 1) * offset : page * offset]
        if not rows:
            return {"status": "0", "message": "No transactions found", "result": []}
        return {"status": "1", "message": "OK", "result": rows}

    return get, calls


def _evm_fetch(history, cursor):
    from core.wallet_monitor.adapters.evm_adapter import EvmAdapter

    adapter = EvmAdapter(1)
    get, calls = _etherscan(history)
    return adapter, get, calls, cursor


@pytest.mark.asyncio
async def test_evm_pages_until_last_seen_tx():
    adapter, get, calls, _ = _evm_fetch(_evm_history(50), None)
    cursor = f"0x{5:064x}"
    with (
        patch.object(adapter, "_etherscan_get", side_effect=get),
        patch("core.wallet_monitor.adapters.evm_adapter.get_json", return_value=cursor),
    ):
        events = await adapter.fetch_events(EVM, api_key="k", user_id="u1")
    assert len(events) == 45
    assert events[0]["event_id"] == f"0x{50:064x}"
    assert [c["page"] for c in calls] == [1, 2, 3]


@pytest.mark.asyncio
async def test_evm_repoll_without_new_txs_sends_nothing_again():
    """以前 EVM 沒有游標，每 10 分鐘把最新 20 筆重發一次。
    （第一次輪詢只記基準的情況見下面 new_evm_wallet 測試。）"""
    adapter, get, calls, _ = _evm_fetch(_evm_history(50), None)
    settings = {"alerts": {"incoming": {"enabled": True}}}
    store = {adapter._cursor_key("u1", EVM): f"0x{30:064x}"}
    with (
        patch.object(adapter, "_etherscan_get", side_effect=get),
        patch(
            "core.wallet_monitor.adapters.evm_adapter.get_json",
            side_effect=lambda k: store.get(k),
        ),
        patch(
            "core.wallet_monitor.adapters.evm_adapter.set_json",
            side_effect=lambda k, v, ttl=None: store.__setitem__(k, v),
        ),
    ):
        first = adapter.translate_events(
            EVM,
            await adapter.fetch_events(EVM, api_key="k", user_id="u1"),
            settings,
            user_id="u1",
        )
        second = adapter.translate_events(
            EVM,
            await adapter.fetch_events(EVM, api_key="k", user_id="u1"),
            settings,
            user_id="u1",
        )
    assert len(first) == 20  # 游標（第 30 筆）之後的第 31～50 筆
    assert second == []


@pytest.mark.asyncio
async def test_evm_page_cap_logs(caplog):
    from core.wallet_monitor.adapters import evm_adapter

    adapter, get, calls, _ = _evm_fetch(_evm_history(500), None)
    with (
        caplog.at_level(
            logging.WARNING, logger="core.wallet_monitor.adapters.evm_adapter"
        ),
        patch.object(adapter, "_etherscan_get", side_effect=get),
        patch(
            "core.wallet_monitor.adapters.evm_adapter.get_json", return_value="0xgone"
        ),
    ):
        events = await adapter.fetch_events(EVM, api_key="k", user_id="u1")
    assert len(calls) == evm_adapter._MAX_TX_PAGES
    assert len(events) == evm_adapter._MAX_TX_PAGES * evm_adapter._TX_PAGE_SIZE
    assert any("page cap" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_evm_later_page_failure_keeps_newer_txs(caplog):
    adapter, get, calls, _ = _evm_fetch(_evm_history(50), None)

    def flaky(params, api_key=None):
        if int(params["page"]) == 2:
            return None
        return get(params, api_key)

    with (
        caplog.at_level(
            logging.WARNING, logger="core.wallet_monitor.adapters.evm_adapter"
        ),
        patch.object(adapter, "_etherscan_get", side_effect=flaky),
        patch(
            "core.wallet_monitor.adapters.evm_adapter.get_json", return_value="0xgone"
        ),
    ):
        events = await adapter.fetch_events(EVM, api_key="k", user_id="u1")
    assert len(events) == 20
    assert any("older txs skipped" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_evm_first_page_failure_returns_nothing():
    from core.wallet_monitor.adapters.evm_adapter import EvmAdapter

    adapter = EvmAdapter(1)
    with (
        patch.object(adapter, "_etherscan_get", return_value=None),
        patch(
            "core.wallet_monitor.adapters.evm_adapter.get_json", return_value="0xgone"
        ),
    ):
        assert await adapter.fetch_events(EVM, api_key="k", user_id="u1") == []


def test_evm_quiet_wallet_cursor_is_refreshed_every_poll():
    from core.wallet_monitor.adapters.evm_adapter import _LAST_TX_TTL, EvmAdapter

    adapter = EvmAdapter(1)
    with (
        patch(
            "core.wallet_monitor.adapters.evm_adapter.get_json", return_value="0xlast"
        ),
        patch("core.wallet_monitor.adapters.evm_adapter.set_json") as mark,
    ):
        assert adapter.translate_events(EVM, [], {"alerts": {}}, user_id="u1") == []
    mark.assert_called_once_with(adapter._cursor_key("u1", EVM), "0xlast", _LAST_TX_TTL)


def test_evm_cursor_key_is_per_chain_and_per_user():
    """同一個 EVM 地址在 eth 跟 polygon 是兩條鏈上的兩串交易，游標不能共用；
    兩個人監測同一個錢包也不能共用（見下面 two_users 測試）。"""
    from core.wallet_monitor.adapters.evm_adapter import EvmAdapter

    eth = EvmAdapter(1)
    assert eth._cursor_key("u1", EVM) != EvmAdapter(137)._cursor_key("u1", EVM)
    assert eth._cursor_key("u1", EVM) != eth._cursor_key("u2", EVM)
    assert eth._cursor_key("u1", EVM.upper().replace("0X", "0x")) == eth._cursor_key(
        "u1", EVM
    )


# ── 游標按用戶分開：同一個錢包被兩個人監測 ────────────────────────────────────


def _dict_cache(store):
    return (
        lambda k: store.get(k),
        lambda k, v, ttl=None: store.__setitem__(k, v),
    )


async def _poll_everyone(mod, users, settings):
    """cron 對每個用戶跑一次 _process_user；回 {user: [警示的 event_id…]}。"""
    got = {}

    async def dispatch(uid, matched, st):
        got.setdefault(uid, []).extend(m.event_id for m in matched)
        return len(matched)

    with patch.object(
        mod.alert_dispatcher, "dispatch", new=AsyncMock(side_effect=dispatch)
    ):
        for uid in users:
            await mod._process_user(uid, settings)
    return got


@pytest.mark.asyncio
async def test_two_users_same_ton_wallet_both_alerted_once():
    """游標原本只按地址存：先輪到的人把游標推到最新，另一個人就再也收不到那些事件。

    也鎖住上線那一輪：per-user 游標還沒有時讀一次舊的 per-address 游標當起點——
    只警示它之後的事件，不會把最新一頁全當新的重發。"""
    from scripts import cron_wallet_monitor as mod

    users = ["alice", "bob"]
    settings = {
        "monitored_wallets": [WALLET],
        "alerts": {"incoming": {"enabled": True, "min_amount_ton": 0}},
    }
    history = _ton_history(3)
    legacy_key = f"wallet_monitor:last_event:{WALLET}"  # 部署前的舊 key
    store = {legacy_key: "e3"}
    get, put = _dict_cache(store)
    client, _ = _tonapi_client(history)
    with (
        patch("core.wallet_monitor.engine.httpx.AsyncClient", return_value=client),
        patch("core.wallet_monitor.engine.get_json", side_effect=get),
        patch("core.wallet_monitor.engine.set_json", side_effect=put),
        patch("core.tools.key_resolver.resolve_tool_key", return_value=None),
    ):
        history.insert(0, _ton_history(4)[0])  # 新事件 e4
        assert await _poll_everyone(mod, users, settings) == {
            "alice": ["e4"],
            "bob": ["e4"],
        }
        assert await _poll_everyone(mod, users, settings) == {}  # 沒新事件：不重發
        assert store[legacy_key] == "e3"  # 舊 key 不再寫，TTL 到就消失

        del store[legacy_key]  # 舊 key 過期後，各自的游標照常運作
        history.insert(0, _ton_history(5)[0])
        assert await _poll_everyone(mod, users, settings) == {
            "alice": ["e5"],
            "bob": ["e5"],
        }
        assert await _poll_everyone(mod, users, settings) == {}


@pytest.mark.asyncio
async def test_two_users_same_evm_wallet_both_alerted_once():
    from core.wallet_monitor.adapters.evm_adapter import EvmAdapter
    from scripts import cron_wallet_monitor as mod

    users = ["alice", "bob"]
    settings = {
        "monitored_wallets": [{"address": EVM, "chain": "eth"}],
        "alerts": {"incoming": {"enabled": True}},
    }
    history = _evm_history(3)
    etherscan, _ = _etherscan(history)
    legacy_key = f"wallet_monitor:last_tx:1:{EVM}"  # 部署前的舊 key
    store = {legacy_key: f"0x{3:064x}"}
    get, put = _dict_cache(store)
    with (
        patch.object(mod, "WALLET_MONITOR_EVM_ENABLED", True),
        patch.object(EvmAdapter, "_etherscan_get", side_effect=etherscan),
        patch("core.wallet_monitor.adapters.evm_adapter.get_json", side_effect=get),
        patch("core.wallet_monitor.adapters.evm_adapter.set_json", side_effect=put),
        patch("core.tools.key_resolver.resolve_tool_key", return_value="k"),
    ):
        history.insert(0, _evm_history(4)[0])
        assert await _poll_everyone(mod, users, settings) == {
            "alice": [f"0x{4:064x}"],
            "bob": [f"0x{4:064x}"],
        }
        assert await _poll_everyone(mod, users, settings) == {}
        assert store[legacy_key] == f"0x{3:064x}"

        del store[legacy_key]
        history.insert(0, _evm_history(5)[0])
        assert await _poll_everyone(mod, users, settings) == {
            "alice": [f"0x{5:064x}"],
            "bob": [f"0x{5:064x}"],
        }
        assert await _poll_everyone(mod, users, settings) == {}


# ── 新加入的監測：第一次輪詢只記基準、不警示 ─────────────────────────────────


@pytest.mark.asyncio
async def test_new_ton_wallet_first_poll_is_baseline_without_alerts():
    """沒有 per-user 游標也沒有舊 key＝剛加入監測：以前第一次輪詢把最新一頁（最多 20 筆
    舊事件）全當新事件發出去。現在只把最新一筆記成游標，下一輪起才警示更新的。"""
    from core.wallet_monitor import engine
    from scripts import cron_wallet_monitor as mod

    settings = {
        "monitored_wallets": [WALLET],
        "alerts": {"incoming": {"enabled": True, "min_amount_ton": 0}},
    }
    history = _ton_history(30)
    store = {}
    get, put = _dict_cache(store)
    client, _ = _tonapi_client(history)
    with (
        patch("core.wallet_monitor.engine.httpx.AsyncClient", return_value=client),
        patch("core.wallet_monitor.engine.get_json", side_effect=get),
        patch("core.wallet_monitor.engine.set_json", side_effect=put),
        patch("core.tools.key_resolver.resolve_tool_key", return_value=None),
    ):
        assert await _poll_everyone(mod, ["alice"], settings) == {}
        assert store == {
            engine._LAST_EVENT_KEY.format(user_id="alice", address=WALLET): "e30"
        }

        history.insert(0, _ton_history(31)[0])
        assert await _poll_everyone(mod, ["alice"], settings) == {"alice": ["e31"]}
        assert await _poll_everyone(mod, ["alice"], settings) == {}


@pytest.mark.asyncio
async def test_new_ton_wallet_empty_first_fetch_records_no_baseline():
    """第一次抓不到事件（TonAPI 失敗或空錢包）不能記成「空」基準：那樣下一輪沒游標
    可比，整頁舊事件又會全被當新的。先不記，等抓得到的那輪再當基準。"""
    from scripts import cron_wallet_monitor as mod

    settings = {
        "monitored_wallets": [WALLET],
        "alerts": {"incoming": {"enabled": True, "min_amount_ton": 0}},
    }
    history = _ton_history(5)
    store = {}
    get, put = _dict_cache(store)
    client, _ = _tonapi_client(history, fail_on_call=1)
    with (
        patch("core.wallet_monitor.engine.httpx.AsyncClient", return_value=client),
        patch("core.wallet_monitor.engine.get_json", side_effect=get),
        patch("core.wallet_monitor.engine.set_json", side_effect=put),
        patch("core.tools.key_resolver.resolve_tool_key", return_value=None),
    ):
        assert await _poll_everyone(mod, ["alice"], settings) == {}  # 抓失敗
        assert store == {}
        assert await _poll_everyone(mod, ["alice"], settings) == {}  # 這輪才是基準
        assert list(store.values()) == ["e5"]


@pytest.mark.asyncio
async def test_new_evm_wallet_first_poll_is_baseline_without_alerts():
    from core.wallet_monitor.adapters.evm_adapter import EvmAdapter
    from scripts import cron_wallet_monitor as mod

    settings = {
        "monitored_wallets": [{"address": EVM, "chain": "eth"}],
        "alerts": {"incoming": {"enabled": True}},
    }
    history = _evm_history(30)
    etherscan, _ = _etherscan(history)
    store = {}
    get, put = _dict_cache(store)
    with (
        patch.object(mod, "WALLET_MONITOR_EVM_ENABLED", True),
        patch.object(EvmAdapter, "_etherscan_get", side_effect=etherscan),
        patch("core.wallet_monitor.adapters.evm_adapter.get_json", side_effect=get),
        patch("core.wallet_monitor.adapters.evm_adapter.set_json", side_effect=put),
        patch("core.tools.key_resolver.resolve_tool_key", return_value="k"),
    ):
        assert await _poll_everyone(mod, ["alice"], settings) == {}
        assert store == {EvmAdapter(1)._cursor_key("alice", EVM): f"0x{30:064x}"}

        history.insert(0, _evm_history(31)[0])
        assert await _poll_everyone(mod, ["alice"], settings) == {
            "alice": [f"0x{31:064x}"]
        }
        assert await _poll_everyone(mod, ["alice"], settings) == {}


def test_cursor_ttl_survives_an_outage():
    """首輪改成 baseline 後，游標過期＝停機期間的事件全被吞掉（review）。
    游標每輪都會刷新，TTL 只在停機時起作用——要撐過一般停機，恢復後靠翻頁補抓。"""
    from core.wallet_monitor.adapters.evm_adapter import _LAST_TX_TTL
    from core.wallet_monitor.engine import _LAST_EVENT_TTL

    assert _LAST_EVENT_TTL >= 24 * 3600
    assert _LAST_TX_TTL >= 24 * 3600
