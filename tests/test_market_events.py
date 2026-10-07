"""市場事件資料源（2026-09-12）：DefiLlama 解鎖索引壓縮、FOMC 解析＋靜態表守衛、
CPI／非農表涵蓋、兩支工具不再回假資料。"""

from __future__ import annotations

import copy
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from core.market_events import macro_calendar, token_unlocks

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


# ── 解鎖 ─────────────────────────────────────────────────────────────────────

_RAW = {
    "data": [
        {
            "protocolSlug": "arbitrum-foundation",
            "name": "Arbitrum",
            "circSupply": 6_936_027_936,
            "tokenPrice": [{"symbol": "ARB", "price": 0.2}],
            "unlockEvents": [
                {
                    "timestamp": 1_789_443_090,  # 2026-09-15
                    "cliffAllocations": [
                        {
                            "recipient": "Team",
                            "category": "insiders",
                            "amount": 56_125_000,
                        },
                        {
                            "recipient": "Investors",
                            "category": "privateSale",
                            "amount": 36_520_833,
                        },
                    ],
                    "linearAllocations": [],
                },
                # 過去太久：丟掉
                {"timestamp": 1_700_000_000, "cliffAllocations": [{"amount": 1}]},
                # 只有線性、沒有 cliff 金額：丟掉
                {
                    "timestamp": 1_789_443_090 + 86400,
                    "cliffAllocations": [],
                    "linearAllocations": [{"amount": 5}],
                },
            ],
        },
        # 同 symbol 的小協議（流通量小）：被大的蓋掉
        {
            "protocolSlug": "arb-wrapped",
            "name": "ARB wrapped",
            "circSupply": 10,
            "tokenPrice": [{"symbol": "ARB", "price": 0.2}],
            "unlockEvents": [],
        },
        # 沒 symbol：跳過
        {
            "protocolSlug": "nameless",
            "circSupply": 1,
            "tokenPrice": [],
            "unlockEvents": [],
        },
    ]
}


class TestUnlocks:
    def test_compact_index_keeps_cliffs_in_window_and_prefers_larger_supply(self):
        idx = token_unlocks.compact_index(_RAW, now_ts=1_789_200_000)
        assert set(idx) == {"ARB"}
        assert idx["ARB"]["slug"] == "arbitrum-foundation"
        assert len(idx["ARB"]["events"]) == 1
        ev = idx["ARB"]["events"][0]
        assert ev["amount"] == 56_125_000 + 36_520_833
        assert ev["categories"] == ["insiders", "privateSale"]
        # 進快取的東西要能 JSON 化
        json.dumps(idx)

    def test_upcoming_unlocks_pct_and_usd(self):
        idx = token_unlocks.compact_index(_RAW, now_ts=1_789_200_000)
        ups = token_unlocks.upcoming_unlocks(
            "arb", today=date(2026, 9, 12), days=30, index=idx
        )
        assert len(ups) == 1
        assert ups[0]["date"] == date(2026, 9, 15)
        assert ups[0]["pct_of_circ"] == 1.34
        assert ups[0]["usd"] == pytest.approx((56_125_000 + 36_520_833) * 0.2)
        assert (
            token_unlocks.upcoming_unlocks("BTC", today=date(2026, 9, 12), index=idx)
            == []
        )

    def test_format_amount_by_language(self):
        assert token_unlocks.format_amount(92_645_833, "zh-TW") == "9265萬"
        assert token_unlocks.format_amount(1.2e8, "zh-CN") == "1.20億"
        assert token_unlocks.format_amount(92_645_833, "en") == "92.6M"
        assert token_unlocks.format_amount(1500, "ru") == "2K"

    def test_index_download_failure_returns_empty(self):
        with (
            patch("core.database.cache.get_cache", return_value=None),
            patch.object(
                token_unlocks, "_download_index", side_effect=RuntimeError("boom")
            ),
        ):
            assert token_unlocks.get_unlock_index() == {}

    def test_cached_index_is_used_without_download(self):
        with (
            patch(
                "core.database.cache.get_cache",
                return_value={"index": {"ARB": {"circ": 1}}},
            ),
            patch.object(token_unlocks, "_download_index") as dl,
        ):
            assert token_unlocks.get_unlock_index() == {"ARB": {"circ": 1}}
        dl.assert_not_called()


# ── 總經 ─────────────────────────────────────────────────────────────────────

_FOMC_HTML = """
<div class="panel panel-default"><div class="panel-heading"><h4><a id="1">2026 FOMC Meetings</a></h4></div>
<div class="row fomc-meeting">
<div class="fomc-meeting__month col-xs-5"><strong>January</strong></div>
<div class="fomc-meeting__date col-xs-4">27-28</div>
</div>
<div class="row fomc-meeting">
<div class="fomc-meeting__month col-xs-5"><strong>March</strong></div>
<div class="fomc-meeting__date col-xs-4">17-18*</div>
</div>
<div class="row fomc-meeting">
<div class="fomc-meeting__month col-xs-5"><strong>January</strong></div>
<div class="fomc-meeting__date col-xs-4">31-Feb 1</div>
</div>
</div>
<div class="panel panel-default"><div class="panel-heading"><h4>2027 FOMC Meetings</h4></div>
<div class="fomc-meeting__month"><strong>June</strong></div>
<div class="fomc-meeting__date">8-9*</div>
</div>
"""


class TestMacro:
    def test_parse_fomc_html_takes_decision_day_and_cross_month(self):
        parsed = macro_calendar.parse_fomc_html(_FOMC_HTML)
        assert parsed[2026] == [date(2026, 1, 28), date(2026, 2, 1), date(2026, 3, 18)]
        assert parsed[2027] == [date(2027, 6, 9)]

    def test_static_tables_cover_the_near_future(self):
        """靜態表到期會紅：補下一年的 FOMC／CPI／非農（怎麼補寫在失敗訊息裡）。"""
        today = macro_calendar.now_utc_date()
        need = today + timedelta(days=30)
        ends = {
            "CPI_STATIC": max(macro_calendar._static_dates(macro_calendar.CPI_STATIC)),
            "NFP_STATIC": max(macro_calendar._static_dates(macro_calendar.NFP_STATIC)),
            "FOMC_STATIC": max(macro_calendar._static_fomc()),
        }
        short = ", ".join(f"{k} 只到 {v}" for k, v in ends.items() if v < need)
        assert macro_calendar.static_coverage_end() >= need, (
            f"core/market_events/macro_calendar.py 的靜態表涵蓋不到 {need}（今天＋30 天）：{short}。\n"
            "CPI_STATIC／NFP_STATIC：加下一年的鍵，12 個 'YYYY-MM-DD'，照抄 BLS 公告排程——\n"
            "  https://www.bls.gov/schedule/news_release/cpi.htm 與 empsit.htm（curl 會 403，用瀏覽器開），\n"
            "  或 FRED 鏡像 https://fred.stlouisfed.org/releases/calendar?rid=10&view=year"
            "&vs=YYYY-01-01&ve=YYYY-12-31（非農 rid=50）。\n"
            "FOMC_STATIC：加 8 組 (月, 決議日)，照抄 https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm。\n"
            "官方還沒公布就不要推算日期，這題就讓它紅到公布為止；線上設了 FRED_SERVICE_API_KEY "
            "會自動拿到新排程，但靜態表是 FRED 掛掉時的退路，一樣要補。"
        )
        for table in (macro_calendar.CPI_STATIC, macro_calendar.NFP_STATIC):
            for year, days in table.items():
                assert len(days) == 12 and all(s.startswith(str(year)) for s in days)
        for year, pairs in macro_calendar.FOMC_STATIC.items():
            assert len(pairs) == 8, f"FOMC {year} 應有 8 次會議"

    def test_fomc_falls_back_to_static_when_site_unreachable(self):
        with (
            patch("core.database.cache.get_cache", return_value=None),
            patch("core.database.cache.set_cache") as sc,
            patch(
                "core.market_events.macro_calendar.httpx.get",
                side_effect=RuntimeError("down"),
            ),
        ):
            dates = macro_calendar.fomc_decision_dates()
        assert date(2026, 9, 16) in dates and date(2027, 12, 8) in dates
        sc.assert_not_called()  # 靜態退路不寫快取，下次還會再試官網

    def test_upcoming_macro_events_window_and_order(self, monkeypatch):
        monkeypatch.delenv("FRED_SERVICE_API_KEY", raising=False)
        with patch.object(
            macro_calendar,
            "fomc_decision_dates",
            return_value=[date(2026, 9, 16), date(2026, 12, 9)],
        ):
            events = macro_calendar.upcoming_macro_events(
                today=date(2026, 9, 12), days=45
            )
        assert [(e["code"], e["date"].isoformat()) for e in events] == [
            ("fomc", "2026-09-16"),
            ("nfp", "2026-10-02"),
            ("cpi", "2026-10-14"),
        ]

    def test_fred_override_when_key_set(self, monkeypatch):
        monkeypatch.setenv("FRED_SERVICE_API_KEY", "k")

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"release_dates": [{"release_id": 10, "date": "2026-10-15"}]}

        with patch("core.market_events.macro_calendar.httpx.get", return_value=_Resp()):
            assert macro_calendar.release_dates("cpi", today=date(2026, 9, 12)) == [
                date(2026, 10, 15)
            ]
        # FRED 掛了退回靜態表
        with patch(
            "core.market_events.macro_calendar.httpx.get", side_effect=RuntimeError("x")
        ):
            assert date(2026, 10, 14) in macro_calendar.release_dates(
                "cpi", today=date(2026, 9, 12)
            )

    def test_schedule_known_through_reports_last_published_date(self, monkeypatch):
        """官方排程目前拿得到的最晚日期——超過這天查不到＝還沒公布，不是沒有發布。"""
        monkeypatch.delenv("FRED_SERVICE_API_KEY", raising=False)
        with patch.object(
            macro_calendar, "fomc_decision_dates", return_value=[date(2027, 12, 8)]
        ):
            known = macro_calendar.schedule_known_through(today=date(2026, 9, 24))
        assert known["fomc"] == date(2027, 12, 8)
        assert known["cpi"] == max(
            macro_calendar._static_dates(macro_calendar.CPI_STATIC)
        )
        assert known["nfp"] == max(
            macro_calendar._static_dates(macro_calendar.NFP_STATIC)
        )

    def test_coverage_gaps_only_when_horizon_passes_known_date(self):
        known = {
            "fomc": date(2027, 12, 8),
            "cpi": date(2026, 12, 10),
            "nfp": date(2026, 12, 4),
        }
        assert macro_calendar.coverage_gaps(known, horizon_end=date(2026, 11, 30)) == []
        assert macro_calendar.coverage_gaps(known, horizon_end=date(2027, 1, 10)) == [
            "cpi",
            "nfp",
        ]


# ── 工具：不再有假資料 ────────────────────────────────────────────────────────


class TestTools:
    def test_get_token_unlocks_has_no_mock_data(self):
        src = (REPO / "core" / "tools" / "crypto_modules" / "defi.py").read_text(
            encoding="utf-8"
        )
        assert "mock_data" not in src and "Next Wednesday" not in src
        from core.tools.crypto_modules.defi import get_token_unlocks

        # 工具用真實時鐘算「今天」、只列未來 90 天，fixture 的解鎖日要跟著今天走。
        # 原本寫死 2026-09-15（_RAW 的時間戳），過了那天這題就永遠紅。
        raw = copy.deepcopy(_RAW)
        unlock_ts = int(time.time()) + 3 * 86400
        raw["data"][0]["unlockEvents"][0]["timestamp"] = unlock_ts
        unlock_day = datetime.fromtimestamp(unlock_ts, tz=timezone.utc).date()
        idx = token_unlocks.compact_index(raw)
        with patch(
            "core.market_events.token_unlocks.get_unlock_index", return_value=idx
        ):
            out = get_token_unlocks.invoke({"symbol": "arb"})
            assert unlock_day.isoformat() in out
            assert "1.34%" in out and "DefiLlama" in out
            missing = get_token_unlocks.invoke({"symbol": "XYZ"})
            assert "no unlock schedule" in missing and "Do not assume" in missing
        with patch(
            "core.market_events.token_unlocks.get_unlock_index", return_value={}
        ):
            assert "unavailable" in get_token_unlocks.invoke({"symbol": "ARB"})

    def test_get_economic_calendar_returns_real_dates(self):
        src = (REPO / "core" / "tools" / "economic_tools.py").read_text(
            encoding="utf-8"
        )
        assert "Static calendar reminder" not in src
        from core.tools.economic_tools import get_economic_calendar

        with patch(
            "core.market_events.macro_calendar.upcoming_macro_events",
            return_value=[{"code": "fomc", "date": date(2026, 9, 16)}],
        ):
            out = get_economic_calendar.invoke({})
        assert out["events"][0]["date"] == "2026-09-16"
        assert out["events"][0]["code"] == "fomc"
        assert re.match(r"\d{4}-\d{2}-\d{2}", out["as_of"])

    def test_get_economic_calendar_flags_unpublished_schedule(self):
        """表只到某天、查詢範圍超過時要明講「之後還沒公布」——否則 AI 會說成「1 月沒有 CPI」。"""
        from core.tools.economic_tools import get_economic_calendar

        stale = {
            "fomc": date(2099, 1, 1),
            "cpi": date(2000, 1, 1),
            "nfp": date(2000, 1, 1),
        }
        with (
            patch(
                "core.market_events.macro_calendar.upcoming_macro_events",
                return_value=[],
            ),
            patch(
                "core.market_events.macro_calendar.schedule_known_through",
                return_value=stale,
            ),
        ):
            out = get_economic_calendar.invoke({})
        assert out["schedule_known_through"]["cpi"] == "2000-01-01"
        note = out["coverage_note"]
        assert "not announced yet" in note and "does NOT mean" in note
        assert "CPI" in note and "Non-Farm" in note and "FOMC" not in note

        fresh = {k: date(2099, 1, 1) for k in ("fomc", "cpi", "nfp")}
        with (
            patch(
                "core.market_events.macro_calendar.upcoming_macro_events",
                return_value=[],
            ),
            patch(
                "core.market_events.macro_calendar.schedule_known_through",
                return_value=fresh,
            ),
        ):
            out = get_economic_calendar.invoke({})
        assert "coverage_note" not in out


# ── 接線守衛 ─────────────────────────────────────────────────────────────────


def test_include_macro_is_wired_end_to_end():
    schema = (REPO / "core" / "database" / "schema.py").read_text(encoding="utf-8")
    assert "include_macro  BOOLEAN NOT NULL DEFAULT TRUE" in schema
    assert (
        'ALTER TABLE user_brief_prefs ADD COLUMN IF NOT EXISTS "\n                "include_macro'
        in schema
    )
    mig = (REPO / "alembic" / "versions" / "c046_brief_include_macro.py").read_text(
        encoding="utf-8"
    )
    assert 'revision = "c046"' in mig and 'down_revision = "c045"' in mig
    store = (REPO / "core" / "daily_brief" / "store.py").read_text(encoding="utf-8")
    assert (
        "p.include_macro" in store and "include_macro = EXCLUDED.include_macro" in store
    )
    settings = (REPO / "web" / "js" / "components" / "tab-settings.js").read_text(
        encoding="utf-8"
    )
    assert 'id="brief-include-macro"' in settings
    js = (REPO / "web" / "js" / "brief-settings.js").read_text(encoding="utf-8")
    assert "include_macro" in js
    cron = (REPO / "scripts" / "cron_calendar_sync.py").read_text(encoding="utf-8")
    assert (
        "default_unlock_index()" in cron and "include_macro=prefs.include_macro" in cron
    )
    env = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "FRED_SERVICE_API_KEY" in env
    for lang in ("en", "zh-TW", "zh-CN", "ru"):
        d = json.loads(
            (REPO / "web" / "js" / "i18n" / f"{lang}.json").read_text(encoding="utf-8")
        )
        assert "includeMacro" in d["settings"]["brief"], lang
        assert (
            "kindUnlock" in d["journal"]["calendar"]
            and "kindMacro" in d["journal"]["calendar"]
        ), lang
    ui = json.loads(
        (REPO / "core" / "i18n" / "ui_messages.json").read_text(encoding="utf-8")
    )
    for lang in ("zh-TW", "zh-CN", "en", "ru"):
        for key in ("sys_unlock", "sys_macro_fomc", "sys_macro_cpi", "sys_macro_nfp"):
            assert f"daily_brief.{key}" in ui[lang], f"{lang} 缺 daily_brief.{key}"
