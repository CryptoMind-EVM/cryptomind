"""每日早報 Phase C（警報建議）與 Phase D（記憶可見）。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from core.daily_brief.compose import BriefData, compose_brief

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


class TestAlertSuggestion:
    def _data(self, **kw):
        d = BriefData(
            language="zh-TW",
            date_local=date(2026, 9, 13),
            positions=[{"symbol": "2330", "market": "tw_stock", "change_pct": 1.0}],
        )
        for k, v in kw.items():
            setattr(d, k, v)
        return d

    def test_suggestion_line_when_positions_lack_alerts(self):
        text = compose_brief(self._data(alert_suggestions=["2330", "BTC", "ETH"]))
        assert "💡 2330、BTC 還沒設價格警報" in text, "最多列兩檔"
        # 建議在一句話之後、footer 之前
        assert text.index("💡") < text.index("（回覆這則訊息")

    def test_no_suggestion_without_positions_or_candidates(self):
        assert "💡" not in compose_brief(self._data(alert_suggestions=[]))
        d = self._data(alert_suggestions=["2330"])
        d.positions = []
        d.alerts = [{"title": "x", "body": "BTC < 60,000"}]
        assert "💡" not in compose_brief(d)

    def test_suggestion_survives_length_trim(self):
        d = self._data(alert_suggestions=["2330"])
        d.positions = [
            {
                "symbol": f"SYM{i}",
                "market": "crypto",
                "change_pct": 1.0,
                "note": "x" * 60,
            }
            for i in range(30)
        ]
        text = compose_brief(d)
        assert "💡 2330" in text

    def test_english(self):
        d = self._data(alert_suggestions=["NVDA"])
        d.language = "en"
        assert "💡 No price alert yet for NVDA" in compose_brief(d)


class TestCollectAlertSuggestions:
    async def test_symbols_without_alerts_are_suggested(self):
        from unittest.mock import AsyncMock, patch

        from core.daily_brief.collect import collect_brief_data
        from core.daily_brief.schedule import effective_prefs

        prefs = effective_prefs(
            {
                "user_id": "u1",
                "language": "zh-TW",
                "telegram_id": 1,
                "prefs_user_id": None,
            }
        )

        class _Repo:
            base_currency = "TWD"

            def get_positions(self):
                return [
                    {"symbol": "2330", "market": "tw_stock", "quantity": 1000},
                    {"symbol": "BTC", "market": "crypto", "quantity": 0.5},
                    {
                        "symbol": "USD",
                        "market": "cash",
                        "quantity": 1,
                    },  # 不可設警報的市場（2026-09-27 起商品等 10 個市場都能設，現金仍不行）
                ]

            def get_category_summary(self, **kw):
                return []

        with (
            patch("core.orm.trade_journal_repo.get_journal_repo", return_value=_Repo()),
            patch(
                "core.daily_brief.collect._fetch_price_safe",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "core.database.price_alerts.get_user_alerts",
                return_value=[{"symbol": "btc"}],
            ),
            patch("core.daily_brief.store.recent_alert_notifications", return_value=[]),
            patch("core.daily_brief.store.list_events_between", return_value=[]),
        ):
            data = await collect_brief_data(
                prefs, datetime(2026, 9, 13, 0, 5, tzinfo=timezone.utc)
            )
        assert data.alert_suggestions == ["2330"], (
            "BTC 已有警報（不分大小寫）、現金市場不支援"
        )


class TestCollectEventWindow:
    async def test_long_lead_reminder_for_one_off_event_is_collected(self):
        """單次事件 20 天後、提前 25 天提醒 → 今天就要出現在早報。

        回歸鎖：撈事件只撈 7 天，remind_days_before 上限卻是 30——
        提前 8～30 天的提醒從 SQL 那層就被濾掉，Python 的提醒窗根本看不到。
        """
        from datetime import timedelta
        from unittest.mock import AsyncMock, patch

        from core.daily_brief.collect import collect_brief_data
        from core.daily_brief.schedule import effective_prefs

        prefs = effective_prefs(
            {
                "user_id": "u1",
                "language": "zh-TW",
                "telegram_id": 1,
                "prefs_user_id": None,
            }
        )
        today = date(2026, 9, 13)
        rows = [
            {"id": 1, "event_date": today + timedelta(days=20), "title": "long",
             "remind_days_before": 25},
            {"id": 2, "event_date": today + timedelta(days=20), "title": "short",
             "remind_days_before": 1},
        ]

        def _fake_between(user_id, start, end):
            # 跟 SQL 一樣：單次事件只回區間內的
            return [r for r in rows if start <= r["event_date"] <= end]

        class _Repo:
            base_currency = "TWD"

            def get_positions(self):
                return []

            def get_category_summary(self, **kw):
                return []

        with (
            patch("core.orm.trade_journal_repo.get_journal_repo", return_value=_Repo()),
            patch(
                "core.daily_brief.collect._fetch_price_safe",
                new=AsyncMock(return_value=None),
            ),
            patch("core.database.trading.get_watchlist", return_value=[]),
            patch("core.daily_brief.store.recent_alert_notifications", return_value=[]),
            patch("core.daily_brief.store.list_events_between", _fake_between),
        ):
            data = await collect_brief_data(
                prefs, datetime(2026, 9, 13, 0, 5, tzinfo=timezone.utc)
            )
        assert [e["title"] for e in data.events] == ["long"]

    async def test_zero_lead_means_on_the_day_only(self):
        """remind_days_before=0＝當天才提醒；以前 `or 1` 把 0 吃成 1，提前一天就出現。
        欄位缺漏（None）才退回預設 1 天。"""
        from datetime import timedelta
        from unittest.mock import AsyncMock, patch

        from core.daily_brief.collect import collect_brief_data
        from core.daily_brief.schedule import effective_prefs

        prefs = effective_prefs(
            {
                "user_id": "u1",
                "language": "zh-TW",
                "telegram_id": 1,
                "prefs_user_id": None,
            }
        )
        today = date(2026, 9, 13)
        tomorrow = today + timedelta(days=1)
        rows = [
            {
                "id": 1,
                "event_date": tomorrow,
                "title": "zero-tomorrow",
                "remind_days_before": 0,
            },
            {
                "id": 2,
                "event_date": today,
                "title": "zero-today",
                "remind_days_before": 0,
            },
            {
                "id": 3,
                "event_date": tomorrow,
                "title": "default-tomorrow",
                "remind_days_before": None,
            },
        ]

        class _Repo:
            base_currency = "TWD"

            def get_positions(self):
                return []

            def get_category_summary(self, **kw):
                return []

        with (
            patch("core.orm.trade_journal_repo.get_journal_repo", return_value=_Repo()),
            patch(
                "core.daily_brief.collect._fetch_price_safe",
                new=AsyncMock(return_value=None),
            ),
            patch("core.database.trading.get_watchlist", return_value=[]),
            patch("core.daily_brief.store.recent_alert_notifications", return_value=[]),
            patch("core.daily_brief.store.list_events_between", return_value=rows),
        ):
            data = await collect_brief_data(
                prefs, datetime(2026, 9, 13, 0, 5, tzinfo=timezone.utc)
            )
        assert sorted(e["title"] for e in data.events) == [
            "default-tomorrow",
            "zero-today",
        ]


class TestMemoryCitation:
    def test_prompt_rule_exists_in_four_languages_and_is_injected_twice(self):
        from core.agents.prompt_registry import PromptRegistry

        PromptRegistry.load()
        for lang in ("zh-TW", "zh-CN", "en", "ru"):
            text = PromptRegistry.get("shared", "memory_citation", lang)
            assert text and "[YYYY-MM-DD]" in text, lang
        # shared 規則要改兩處（base 與 cryptomind 覆寫）——[[agent-prompt-injection-trap]]
        base = (REPO / "core" / "agents" / "base_react_agent.py").read_text(
            encoding="utf-8"
        )
        crypto = (
            REPO / "core" / "agents" / "agents" / "cryptomind_agent.py"
        ).read_text(encoding="utf-8")
        assert '"memory_citation",' in base
        assert 'PromptRegistry.get("shared", "memory_citation", language)' in crypto

    def test_facts_to_text_carries_recorded_date(self):
        from core.database.memory import MemoryStore

        store = MemoryStore.__new__(MemoryStore)
        store.read_facts = lambda: {
            "pref": {
                "value": "偏好技術面",
                "agent_id": None,
                "updated_at": datetime(2026, 8, 3, 1, 2),
            },
            "hold": {
                "value": "持有 2330 1000 股",
                "agent_id": "finance_markets",
                "updated_at": "2026-09-03T08:00:00",
            },
            "nodate": {"value": "新手", "agent_id": None},
        }
        text = store.facts_to_text()
        assert "- 偏好技術面 [2026-08-03]" in text
        assert "- 持有 2330 1000 股 [2026-09-03]" in text
        assert "- 新手\n" in text + "\n" or text.endswith("- 新手"), "沒日期就不加括號"
