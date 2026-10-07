"""早報留存（PR-7）：判斷結果進早報、會空時補市場概況、只勾 Base App 也送得出去。

不打 DB、不打行情 API。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from core.daily_brief.compose import BriefData, compose_brief
from core.daily_brief.schedule import effective_prefs, is_deliverable, is_due

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)  # 台北 08:05
TODAY = date(2026, 9, 28)


def _row(**kw):
    base = {
        "user_id": "u1",
        "language": "zh-TW",
        "display_name": "D",
        "membership_tier": "free",
        "telegram_id": 111,
        "prefs_user_id": None,
        # 測試裡給了 prefs_user_id 的列都當成使用者自己設的（c063）；自動建的列另外測
        "user_set": True,
        "enabled": None,
        "send_hour": None,
        "timezone": None,
        "channels": None,
        "include_spend": None,
        "include_macro": None,
        "last_sent_on": None,
        # 預設是新帳號（2 天前開）：空早報補市場概況只給新用戶與自己開過早報的人
        "account_created_at": NOW - timedelta(days=2),
    }
    base.update(kw)
    return base


def _call(symbol="BTC", side="buy", hit=True, raw=4.2, bench=None, excess=None, **kw):
    row = {
        "kind": "call",
        "symbol": symbol,
        "market": "crypto",
        "side": side,
        "horizon_days": 30,
        "raw_pct": raw,
        "bench_symbol": bench,
        "bench_pct": None if bench is None else 1.0,
        "excess_pct": raw if excess is None else excess,
        "hit": hit,
    }
    row.update(kw)
    return row


def _overview():
    return {
        "quotes": [
            {"label": "BTC", "price": 65432.1, "change_pct": 1.2},
            {"label": "ETH", "price": 3210.5, "change_pct": -0.8},
            {"label": "SPY", "price": 571.3, "change_pct": 0.3},
        ],
        "next_event": {"date": date(2026, 10, 3), "title": "美國非農就業報告"},
    }


# ── 送得到的管道 ────────────────────────────────────────────────────────────


class TestDeliverable:
    def test_baseapp_only_is_deliverable_and_due(self):
        """以前只認 in-app／Telegram：只勾 Base App 的人永遠不會到點。"""
        p = effective_prefs(
            _row(
                prefs_user_id="u1", enabled=True, channels=["baseapp"], telegram_id=None
            )
        )
        assert is_deliverable(p) is True
        assert is_due(p, NOW) is True

    def test_telegram_without_binding_is_not_deliverable(self):
        p = effective_prefs(
            _row(
                prefs_user_id="u1",
                enabled=True,
                channels=["telegram"],
                telegram_id=None,
            )
        )
        assert is_deliverable(p) is False

    async def test_baseapp_channel_also_writes_the_inapp_copy(self):
        """Base App 推播只是敲門，內容在站內通知——勾了 baseapp 就要寫站內那份。"""
        from scripts.cron_daily_brief import send_one

        prefs = effective_prefs(
            _row(
                prefs_user_id="u1", enabled=True, channels=["baseapp"], telegram_id=None
            )
        )
        data = BriefData(
            language="zh-TW", date_local=TODAY, market_overview=_overview()
        )
        with (
            patch(
                "core.daily_brief.collect.collect_brief_data",
                new=AsyncMock(return_value=data),
            ),
            patch(
                "core.daily_brief.insight.generate_insight",
                new=AsyncMock(return_value=None),
            ),
            patch("core.daily_brief.send.send_telegram_text", new=AsyncMock()) as tg,
            patch("core.daily_brief.send.send_inapp", return_value=True) as inapp,
            patch("core.daily_brief.send.send_baseapp", return_value=True) as push,
            patch("core.daily_brief.store.claim_day", return_value=True),
            patch("core.daily_brief.store.mark_sent"),
        ):
            assert await send_one(prefs, NOW) == "sent"
        tg.assert_not_called()
        inapp.assert_called_once()
        push.assert_called_once()


# ── 判斷結果段 ──────────────────────────────────────────────────────────────


class TestCallsSection:
    def test_hit_and_miss_rows(self):
        data = BriefData(
            language="en",
            date_local=TODAY,
            scored_calls=[
                _call("BTC", "buy", hit=True, raw=4.2),
                _call("AAPL", "sell", hit=False, raw=-1.5, bench="SPY", excess=-2.0),
            ],
        )
        text = compose_brief(data)
        assert text is not None
        assert "🎯" in text
        assert "BTC bullish 30d: ✅ hit +4.2%" in text
        assert "AAPL bearish 30d: ❌ missed -1.5%" in text
        assert "-2.0% vs SPY" in text

    def test_calls_alone_are_worth_sending(self):
        """只有判斷結果也照送（不落到市場概況 fallback）。"""
        data = BriefData(
            language="zh-TW",
            date_local=TODAY,
            scored_calls=[_call()],
            market_overview=_overview(),
        )
        text = compose_brief(data)
        assert "🎯" in text and "看多" in text and "✅" in text
        assert "🌍" not in text

    def test_rows_are_capped_with_more_line(self):
        data = BriefData(
            language="en",
            date_local=TODAY,
            scored_calls=[_call(f"C{i}") for i in range(8)],
        )
        text = compose_brief(data)
        assert "C4" in text and "C5" not in text
        assert "3 more" in text

    def test_backfilled_and_order_are_left_to_the_store(self):
        from core.scorecard import store

        with patch.object(store.DatabaseBase, "query_all", return_value=[]) as q:
            store.scored_since("u1", NOW - timedelta(hours=24), limit=50)
        sql, params = q.call_args.args
        assert params == ("u1", NOW - timedelta(hours=24), 50)
        assert "u1" not in sql
        assert "status = 'scored'" in sql and "scored_at >= %s" in sql
        assert "backfilled" in sql

    @pytest.mark.parametrize("lang", ["zh-TW", "zh-CN", "en", "ru"])
    def test_every_language_renders_without_raw_keys(self, lang):
        data = BriefData(
            language=lang,
            date_local=TODAY,
            scored_calls=[_call(), _call("ETH", "sell", hit=False, raw=-3.0)],
        )
        text = compose_brief(data)
        assert "daily_brief." not in text


# ── 會空的時候補市場概況 ────────────────────────────────────────────────────


class TestMarketOverview:
    def test_empty_brief_gets_market_overview(self):
        data = BriefData(language="en", date_local=TODAY, market_overview=_overview())
        text = compose_brief(data)
        assert text is not None
        assert "🌍" in text
        assert "BTC" in text and "+1.2%" in text and "SPY" in text
        assert "10/3" in text
        assert "Investment Journal" in text  # 引導加第一筆持倉

    def test_personal_content_wins_over_overview(self):
        data = BriefData(
            language="en",
            date_local=TODAY,
            positions=[{"symbol": "NVDA", "market": "us_stock", "change_pct": 2.0}],
            market_overview=_overview(),
        )
        assert "🌍" not in compose_brief(data)

    def test_nothing_at_all_is_still_none(self):
        assert compose_brief(BriefData(language="en", date_local=TODAY)) is None
        empty = {"quotes": [], "next_event": None}
        assert (
            compose_brief(
                BriefData(language="en", date_local=TODAY, market_overview=empty)
            )
            is None
        )

    @pytest.mark.parametrize("lang", ["zh-TW", "zh-CN", "en", "ru"])
    def test_every_language_renders_without_raw_keys(self, lang):
        text = compose_brief(
            BriefData(language=lang, date_local=TODAY, market_overview=_overview())
        )
        assert text and "daily_brief." not in text


# ── collect：什麼時候抓什麼 ──────────────────────────────────────────────────


class _Repo:
    base_currency = "USD"

    def __init__(self, positions=None):
        self._positions = positions or []

    def get_positions(self):
        return self._positions

    def get_category_summary(self, **_):
        return []

    def list_trades(self, **_):
        return []


def _collect_patches(repo, scored=None, price=(100.0, 99.0)):
    return (
        patch("core.orm.trade_journal_repo.get_journal_repo", return_value=repo),
        patch("core.daily_brief.store.recent_alert_notifications", return_value=[]),
        patch("core.daily_brief.store.list_events_between", return_value=[]),
        patch("core.database.trading.get_watchlist", return_value=[]),
        patch("core.database.price_alerts.get_user_alerts", return_value=[]),
        patch("core.scorecard.store.scored_since", return_value=scored or []),
        patch(
            "core.daily_brief.collect._fetch_price_safe",
            new=AsyncMock(return_value=price),
        ),
    )


class TestCollect:
    @pytest.fixture(autouse=True)
    def _fresh_overview_cache(self):
        from core.daily_brief import collect

        collect._overview_cache.clear()
        yield
        collect._overview_cache.clear()

    async def test_new_user_gets_overview(self):
        from core.daily_brief.collect import collect_brief_data

        prefs = effective_prefs(_row(language="en"))
        ps = _collect_patches(_Repo())
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6] as price:
            data = await collect_brief_data(prefs, NOW)
        labels = [q["label"] for q in data.market_overview["quotes"]]
        assert labels == ["BTC", "ETH", "SPY"]
        assert data.market_overview["next_event"] is not None
        assert price.await_count == 3
        assert compose_brief(data) is not None

    async def test_dormant_default_on_user_gets_no_overview(self):
        """很久以前綁了 Telegram（早報預設開）、從沒記帳的人：內容空就照舊不送，
        不因為補市場概況而突然開始每天收到訊息（首次主動推播要 DANNY 拍板）。"""
        from core.daily_brief.collect import collect_brief_data

        prefs = effective_prefs(
            _row(language="en", account_created_at=NOW - timedelta(days=60))
        )
        ps = _collect_patches(_Repo())
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6] as price:
            data = await collect_brief_data(prefs, NOW)
        assert data.market_overview is None
        assert price.await_count == 0
        assert compose_brief(data) is None

    async def test_old_user_who_turned_brief_on_gets_overview(self):
        from core.daily_brief.collect import collect_brief_data

        prefs = effective_prefs(
            _row(
                language="en",
                account_created_at=NOW - timedelta(days=60),
                prefs_user_id="u1",
                enabled=True,
            )
        )
        ps = _collect_patches(_Repo())
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
            data = await collect_brief_data(prefs, NOW)
        assert data.market_overview is not None

    async def test_dormant_user_with_auto_created_row_still_gets_no_overview(self):
        """c063：上面那條限制以前只擋得了一天——第一次空早報 mark_sent 就替他建一列，
        隔天起「有列」＝「自己設過」，每天補市場概況。自動建的列不算設過。"""
        from core.daily_brief.collect import collect_brief_data

        prefs = effective_prefs(
            _row(
                language="en",
                account_created_at=NOW - timedelta(days=60),
                prefs_user_id="u1",
                user_set=False,
                enabled=True,
                last_sent_on=date(2026, 9, 27),
            )
        )
        ps = _collect_patches(_Repo())
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6] as price:
            data = await collect_brief_data(prefs, NOW)
        assert data.market_overview is None
        assert price.await_count == 0

    async def test_missing_created_at_is_treated_as_not_new(self):
        from core.daily_brief.collect import collect_brief_data

        prefs = effective_prefs(_row(language="en", account_created_at=None))
        ps = _collect_patches(_Repo())
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
            data = await collect_brief_data(prefs, NOW)
        assert data.market_overview is None

    async def test_overview_quotes_are_shared_across_users(self):
        from core.daily_brief.collect import collect_brief_data

        prefs = effective_prefs(_row(language="en"))
        ps = _collect_patches(_Repo())
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6] as price:
            await collect_brief_data(prefs, NOW)
            await collect_brief_data(prefs, NOW)
        assert price.await_count == 3  # 第二個人吃快取

    async def test_macro_off_skips_next_event(self):
        from core.daily_brief.collect import collect_brief_data

        prefs = effective_prefs(
            _row(prefs_user_id="u1", enabled=True, include_macro=False, language="en")
        )
        ps = _collect_patches(_Repo())
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
            data = await collect_brief_data(prefs, NOW)
        assert data.market_overview["next_event"] is None

    async def test_user_with_holdings_does_not_fetch_overview(self):
        from core.daily_brief.collect import collect_brief_data

        prefs = effective_prefs(_row(language="en"))
        repo = _Repo(positions=[{"symbol": "BTC", "market": "crypto", "quantity": 1}])
        ps = _collect_patches(repo)
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6] as price:
            data = await collect_brief_data(prefs, NOW)
        assert data.market_overview is None
        assert price.await_count == 1  # 只有持倉那一檔

    async def test_scored_calls_are_attached_and_skip_overview(self):
        from core.daily_brief.collect import collect_brief_data

        prefs = effective_prefs(_row(language="en"))
        ps = _collect_patches(_Repo(), scored=[_call()])
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5] as scored, ps[6] as price:
            data = await collect_brief_data(prefs, NOW)
        assert data.scored_calls == [_call()]
        assert data.market_overview is None
        price.assert_not_awaited()
        assert scored.call_args.args[0] == "u1"

    async def test_scores_lookup_failure_does_not_break_the_brief(self):
        from core.daily_brief.collect import collect_brief_data

        prefs = effective_prefs(_row(language="en"))
        ps = _collect_patches(_Repo())
        with (
            ps[0],
            ps[1],
            ps[2],
            ps[3],
            ps[4],
            patch("core.scorecard.store.scored_since", side_effect=RuntimeError("db")),
            ps[6],
        ):
            data = await collect_brief_data(prefs, NOW)
        assert data.scored_calls == []
        assert compose_brief(data) is not None


class TestScoresSince:
    def test_first_brief_looks_back_24h(self):
        from core.daily_brief.collect import scores_since

        prefs = effective_prefs(_row())
        assert scores_since(prefs, NOW) == NOW - timedelta(hours=24)

    def test_since_previous_send_time(self):
        """上次 9/27 台北 08:00 送 → 從 9/27 00:00 UTC 起算（00:50 UTC 評的分會在 9/28 那份）。"""
        from core.daily_brief.collect import scores_since

        prefs = effective_prefs(
            _row(prefs_user_id="u1", enabled=True, last_sent_on=date(2026, 9, 27))
        )
        assert scores_since(prefs, NOW) == datetime(
            2026, 9, 27, 0, 0, tzinfo=timezone.utc
        )

    def test_long_gap_is_capped_at_7_days(self):
        from core.daily_brief.collect import scores_since

        prefs = effective_prefs(
            _row(prefs_user_id="u1", enabled=True, last_sent_on=date(2026, 8, 1))
        )
        assert scores_since(prefs, NOW) == NOW - timedelta(days=7)
