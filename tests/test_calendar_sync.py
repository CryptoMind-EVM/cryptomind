"""系統事件同步（Phase B＋解鎖／總經）：台股規則日期、美股財報日、解鎖門檻、總經偏好、去重鍵、單標的失敗不中斷。"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from core.daily_brief.calendar_sync import (
    earnings_symbol,
    next_cn_report_deadline,
    next_tw_quarterly_deadline,
    plan_macro_events,
    plan_system_events,
    plan_unlock_events,
    sync_user_events,
    tw_monthly_revenue_date,
    watchlist_positions,
)

pytestmark = pytest.mark.unit


class TestTwRules:
    def test_monthly_revenue_is_10th_this_or_next_month(self):
        assert tw_monthly_revenue_date(date(2026, 9, 3)) == date(2026, 9, 10)
        assert tw_monthly_revenue_date(date(2026, 9, 10)) == date(2026, 9, 10)
        assert tw_monthly_revenue_date(date(2026, 9, 11)) == date(2026, 10, 10)
        assert tw_monthly_revenue_date(date(2026, 12, 20)) == date(2027, 1, 10)

    def test_quarterly_deadline_rolls_forward(self):
        assert next_tw_quarterly_deadline(date(2026, 9, 12)) == date(2026, 11, 14)
        assert next_tw_quarterly_deadline(date(2026, 11, 14)) == date(2026, 11, 14)
        assert next_tw_quarterly_deadline(date(2026, 11, 15)) == date(2027, 3, 31)


class TestPlan:
    def test_us_earnings_and_tw_rules(self):
        positions = [
            {"symbol": "NVDA", "market": "us_stock"},
            {"symbol": "2330", "market": "tw_stock"},
            {"symbol": "BTC", "market": "crypto"},
        ]
        events = plan_system_events(
            positions=positions,
            today=date(2026, 9, 12),
            language="zh-TW",
            earnings_lookup=lambda s: date(2026, 9, 14) if s == "NVDA" else None,
        )
        keys = {e["dedupe_key"]: e for e in events}
        assert "system:earnings:NVDA:2026-09-14" in keys
        assert keys["system:earnings:NVDA:2026-09-14"]["title"] == "NVDA 財報"
        assert "system:revenue:tw:2026-10-10" in keys
        assert "system:report:tw:2026-11-14" in keys
        assert all(e["dedupe_key"].startswith("system:") for e in events)
        # crypto 第一版不產事件
        assert not [e for e in events if e.get("symbol") == "BTC"]

    def test_earnings_outside_horizon_or_past_are_skipped(self):
        far = plan_system_events(
            positions=[{"symbol": "AAPL", "market": "us_stock"}],
            today=date(2026, 9, 12),
            language="en",
            earnings_lookup=lambda s: date(2027, 3, 1),
        )
        past = plan_system_events(
            positions=[{"symbol": "AAPL", "market": "us_stock"}],
            today=date(2026, 9, 12),
            language="en",
            earnings_lookup=lambda s: date(2026, 9, 1),
        )
        assert far == [] and past == []

    def test_lookup_failure_does_not_break_others(self):
        def lookup(sym):
            if sym == "BAD":
                raise RuntimeError("provider down")
            return date(2026, 10, 1)

        events = plan_system_events(
            positions=[
                {"symbol": "BAD", "market": "us_stock"},
                {"symbol": "MSFT", "market": "us_stock"},
            ],
            today=date(2026, 9, 12),
            language="en",
            earnings_lookup=lookup,
        )
        assert [e["symbol"] for e in events] == ["MSFT"]
        assert events[0]["title"] == "MSFT earnings"

    def test_no_tw_positions_no_tw_events(self):
        events = plan_system_events(
            positions=[{"symbol": "NVDA", "market": "us_stock"}],
            today=date(2026, 9, 12),
            language="zh-TW",
            earnings_lookup=lambda s: None,
        )
        assert events == []


def test_sync_upserts_planned_events_with_dedupe_keys():
    class _Repo:
        def get_positions(self):
            return [
                {"symbol": "2330", "market": "tw_stock", "quantity": 1000},
                {"symbol": "2317", "market": "tw_stock", "quantity": 0},  # 清倉不算
            ]

    written = []
    with (
        patch("core.orm.trade_journal_repo.get_journal_repo", return_value=_Repo()),
        patch(
            "core.daily_brief.store.add_event",
            side_effect=lambda uid, **kw: written.append((uid, kw)) or {"id": 1},
        ),
    ):
        n = sync_user_events(
            "u1",
            date(2026, 9, 12),
            "zh-TW",
            include_macro=False,
            earnings_lookup=lambda s: None,
        )
    assert n == 2
    assert {kw["dedupe_key"] for _, kw in written} == {
        "system:revenue:tw:2026-10-10",
        "system:report:tw:2026-11-14",
    }
    assert all(
        kw["source"] == "system" and kw["remind_days_before"] == 1 for _, kw in written
    )


# ── 2026-09-12：代幣解鎖＋總經 ──────────────────────────────────────────

_ARB_TS = 1789443090  # 2026-09-15 UTC
_UNLOCK_INDEX = {
    "ARB": {
        "slug": "arbitrum-foundation",
        "name": "Arbitrum",
        "circ": 6_936_027_936.0,
        "price": 0.2,
        "events": [
            {"ts": _ARB_TS, "amount": 56_125_000.0, "recipients": ["Team"], "categories": ["insiders"]},
            {"ts": _ARB_TS + 60, "amount": 36_520_833.0, "recipients": ["Investors"], "categories": ["privateSale"]},
            # 61 天後：超出 30 天視窗
            {"ts": _ARB_TS + 61 * 86400, "amount": 56_125_000.0, "recipients": ["Team"], "categories": ["insiders"]},
        ],
    },
    # 小額線性釋放：0.01% 流通量，不該進行事曆
    "TIA": {
        "slug": "celestia",
        "name": "Celestia",
        "circ": 1_093_972_232.0,
        "price": 1.0,
        "events": [{"ts": _ARB_TS, "amount": 100_000.0, "recipients": ["R&D"], "categories": ["ecosystem"]}],
    },
}


class TestUnlockAndMacro:
    def test_unlock_events_merge_same_day_and_apply_threshold(self):
        positions = [
            {"symbol": "ARB", "market": "crypto", "quantity": 100},
            {"symbol": "arb", "market": "crypto", "quantity": 5},  # 大小寫合併
            {"symbol": "TIA", "market": "crypto", "quantity": 10},
            {"symbol": "NVDA", "market": "us_stock", "quantity": 1},
        ]
        events = plan_unlock_events(
            positions=positions,
            today=date(2026, 9, 12),
            language="zh-TW",
            unlock_index=_UNLOCK_INDEX,
        )
        assert [e["dedupe_key"] for e in events] == ["system:unlock:ARB:2026-09-15"]
        ev = events[0]
        assert ev["kind"] == "unlock" and ev["market"] == "crypto"
        assert "ARB" in ev["title"] and "9265萬" in ev["title"] and "1.34%" in ev["title"]

    def test_unlock_needs_crypto_position_and_index(self):
        assert (
            plan_unlock_events(
                positions=[{"symbol": "2330", "market": "tw_stock", "quantity": 1}],
                today=date(2026, 9, 12),
                language="en",
                unlock_index=_UNLOCK_INDEX,
            )
            == []
        )

    def test_macro_events_are_platform_wide_keys(self):
        macro = [
            {"code": "fomc", "date": date(2026, 9, 16)},
            {"code": "cpi", "date": date(2026, 10, 14)},
            {"code": "nfp", "date": date(2026, 10, 2)},
        ]
        events = plan_macro_events(today=date(2026, 9, 12), language="en", macro_events=macro)
        assert [e["dedupe_key"] for e in events] == [
            "system:macro:fomc:2026-09-16",
            "system:macro:cpi:2026-10-14",
            "system:macro:nfp:2026-10-02",
        ]
        assert events[0]["title"] == "FOMC rate decision"
        assert all(e["kind"] == "macro" and e["symbol"] is None for e in events)

    def test_plan_system_events_includes_new_kinds_only_when_given(self):
        positions = [{"symbol": "ARB", "market": "crypto", "quantity": 1}]
        base = plan_system_events(
            positions=positions,
            today=date(2026, 9, 12),
            language="en",
            earnings_lookup=lambda s: None,
        )
        assert base == []  # 沒給索引／總經 → 舊行為
        full = plan_system_events(
            positions=positions,
            today=date(2026, 9, 12),
            language="en",
            earnings_lookup=lambda s: None,
            unlock_index=_UNLOCK_INDEX,
            macro_events=[{"code": "cpi", "date": date(2026, 10, 14)}],
        )
        assert {e["kind"] for e in full} == {"unlock", "macro"}

    def test_sync_respects_include_macro_and_shares_sources(self):
        class _Repo:
            def get_positions(self):
                return [{"symbol": "ARB", "market": "crypto", "quantity": 1}]

        written = []
        with (
            patch("core.orm.trade_journal_repo.get_journal_repo", return_value=_Repo()),
            patch(
                "core.daily_brief.store.add_event",
                side_effect=lambda uid, **kw: written.append(kw) or {"id": 1},
            ),
            patch("core.daily_brief.calendar_sync.default_unlock_index") as dui,
            patch("core.daily_brief.calendar_sync.default_macro_events") as dme,
        ):
            n = sync_user_events(
                "u1",
                date(2026, 9, 12),
                "en",
                include_macro=False,
                earnings_lookup=lambda s: None,
                unlock_index=_UNLOCK_INDEX,
                macro_events=[{"code": "cpi", "date": date(2026, 10, 14)}],
            )
        # 傳進來的索引直接用，不重抓；include_macro=False 時總經整批不寫
        dui.assert_not_called()
        dme.assert_not_called()
        assert n == 1 and written[0]["kind"] == "unlock"


# ── 2026-09-12：港日韓財報（yfinance）、A 股規則、自選清單 ──────────────────


class TestGlobalMarkets:
    def test_earnings_symbol_adds_exchange_suffix_once(self):
        assert earnings_symbol("0700", "hk_stock") == "0700.HK"
        assert earnings_symbol("0700.HK", "hk_stock") == "0700.HK"
        assert earnings_symbol("7203", "jp_stock") == "7203.T"
        assert earnings_symbol("005930", "kr_stock") == "005930.KS"
        assert earnings_symbol("NVDA", "us_stock") == "NVDA"
        assert earnings_symbol("nvda.tw", "us_stock") == "NVDA"  # 錯後綴拿掉
        assert earnings_symbol("2330", "tw_stock") is None  # 台股走規則不查財報日
        assert earnings_symbol("600519", "cn_stock") is None  # A 股 yfinance 沒日期

    def test_hk_jp_kr_earnings_and_cn_rule(self):
        seen = []

        def lookup(query):
            seen.append(query)
            return {"0700.HK": date(2026, 11, 12), "7203.T": date(2026, 11, 5), "005930.KS": date(2026, 10, 28)}.get(query)

        events = plan_system_events(
            positions=[
                {"symbol": "0700", "market": "hk_stock", "quantity": 100},
                {"symbol": "0700.HK", "market": "hk_stock", "quantity": 50},  # 同一家只查一次
                {"symbol": "7203", "market": "jp_stock", "quantity": 100},
                {"symbol": "005930", "market": "kr_stock", "quantity": 1},
                {"symbol": "600519", "market": "cn_stock", "quantity": 1},
            ],
            today=date(2026, 9, 12),
            language="en",
            earnings_lookup=lookup,
        )
        assert sorted(seen) == ["005930.KS", "0700.HK", "7203.T"]
        keys = {e["dedupe_key"] for e in events}
        assert keys == {
            "system:earnings:0700.HK:2026-11-12",
            "system:earnings:7203.T:2026-11-05",
            "system:earnings:005930.KS:2026-10-28",
            "system:report:cn:2026-10-31",
        }
        by = {e["dedupe_key"]: e for e in events}
        assert by["system:earnings:0700.HK:2026-11-12"]["market"] == "hk_stock"
        assert by["system:earnings:0700.HK:2026-11-12"]["symbol"] == "0700"
        assert by["system:report:cn:2026-10-31"]["title"] == "China A-shares: periodic report deadline"

    def test_cn_deadline_rolls_forward(self):
        assert next_cn_report_deadline(date(2026, 4, 30)) == date(2026, 4, 30)
        assert next_cn_report_deadline(date(2026, 5, 1)) == date(2026, 8, 31)
        assert next_cn_report_deadline(date(2026, 11, 1)) == date(2027, 4, 30)

    def test_watchlist_positions_infer_market_and_strip_suffix(self):
        rows = watchlist_positions(["0700.HK", "nvda", "2330", "600519.SS", "005930.KS", "!!"])
        assert [(r["symbol"], r["market"]) for r in rows] == [
            ("0700", "hk_stock"),
            ("NVDA", "us_stock"),
            ("2330", "tw_stock"),
            ("600519", "cn_stock"),
            ("005930", "kr_stock"),
        ]
        assert all(r["watch"] and r["quantity"] == 0 for r in rows)

    def test_sync_includes_watchlist(self):
        class _Repo:
            def get_positions(self):
                return []

        written = []
        with (
            patch("core.orm.trade_journal_repo.get_journal_repo", return_value=_Repo()),
            patch("core.database.trading.get_watchlist", return_value=["0700.HK", "2330"]),
            patch(
                "core.daily_brief.store.add_event",
                side_effect=lambda uid, **kw: written.append(kw) or {"id": 1},
            ),
        ):
            n = sync_user_events(
                "u1",
                date(2026, 9, 12),
                "en",
                include_macro=False,
                earnings_lookup=lambda q: date(2026, 11, 12) if q == "0700.HK" else None,
            )
        assert n == 3  # 騰訊財報＋台股月營收＋台股季報
        assert {w["dedupe_key"] for w in written} == {
            "system:earnings:0700.HK:2026-11-12",
            "system:revenue:tw:2026-10-10",
            "system:report:tw:2026-11-14",
        }
