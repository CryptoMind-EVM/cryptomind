"""早報組稿（純函式）：段落有無、四語、上限、月結。"""

from __future__ import annotations

from datetime import date

import pytest

from core.daily_brief.compose import (
    MAX_CHARS,
    BriefData,
    compose_brief,
    fmt_money,
    fmt_pct,
)

pytestmark = pytest.mark.unit


def _full(lang="zh-TW"):
    return BriefData(
        language=lang,
        date_local=date(2026, 9, 13),
        display_name="DANNY",
        base_currency="TWD",
        positions=[
            {
                "symbol": "2330",
                "market": "tw_stock",
                "change_pct": 1.8,
                "note": "法人連 3 買",
            },
            {"symbol": "BTC", "market": "crypto", "change_pct": -2.1},
            {"symbol": "0050", "market": "tw_stock", "change_pct": 0.4},
        ],
        alerts=[{"title": "🔔 BTC 價格警報", "body": "BTC < 60,000（昨 23:41）"}],
        events=[
            {"event_date": date(2026, 9, 14), "title": "NVDA 財報", "source": "system"},
            {"event_date": date(2026, 9, 15), "title": "房租", "source": "user"},
        ],
        spend_yesterday=[
            {"category": "餐飲", "total": 640},
            {"category": "交通", "total": 300},
        ],
        spend_mtd=18900,
        spend_prev_same_period=16200,
    )


class TestSections:
    def test_full_brief_has_every_section_in_order(self):
        text = compose_brief(_full())
        assert text is not None
        for marker in (
            "☀️ 9/13 早報｜DANNY",
            "📊 你的持倉（3 檔，帳本 TWD）",
            "🔔",
            "📅",
            "💸 昨天花費 940 TWD",
        ):
            assert marker in text
        assert text.index("📊") < text.index("🔔") < text.index("📅") < text.index("💸")
        assert "明天 NVDA 財報（系統）" in text and "9/15 房租（你標記的）" in text
        assert "+1.8%  ▲" in text and "-2.1%  ▼" in text
        assert "本月累計 18,900 TWD（上月同期 16,200）" in text
        assert text.rstrip().endswith("/brief off 可關閉早報）")

    def test_missing_sections_are_omitted(self):
        d = _full()
        d.alerts = []
        d.events = []
        d.spend_yesterday = []
        text = compose_brief(d)
        assert "🔔" not in text and "📅" not in text and "💸" not in text
        assert "📊" in text

    def test_nothing_at_all_means_no_message(self):
        d = BriefData(language="zh-TW", date_local=date(2026, 9, 13))
        assert compose_brief(d) is None

    def test_watchlist_is_a_core_section(self):
        """2026-09-27：自選是使用者自己挑的，跟持倉一起每天列（不再只是沒內容時的備用）。"""
        d = BriefData(
            language="zh-TW",
            date_local=date(2026, 9, 13),
            watchlist=[
                {
                    "symbol": "ETH",
                    "market": "crypto",
                    "price": 2500.4,
                    "change_pct": 3.2,
                }
            ],
        )
        text = compose_brief(d)
        assert "👀 自選清單" in text and "ETH" in text and "2,500" in text
        full = _full()
        full.watchlist = [
            {"symbol": "AAPL", "market": "us_stock", "price": None, "change_pct": None}
        ]
        text = compose_brief(full)
        assert "👀" in text and "AAPL" in text, "有持倉時自選照樣列"
        assert text.index("👀") > text.index("2330"), "自選排在持倉後面"

    def test_long_watchlist_is_trimmed_before_truncating(self):
        d = _full()
        d.watchlist = [
            {
                "symbol": f"COIN{i}",
                "market": "crypto",
                "price": 1.23 + i,
                "change_pct": 1.0,
            }
            for i in range(10)
        ]
        d.positions = d.positions * 6
        text = compose_brief(d)
        assert len(text) <= 1200
        assert "COIN0" in text

    def test_zero_spend_hides_spend_section(self):
        d = _full()
        d.spend_yesterday = [{"category": "餐飲", "total": 0}]
        assert "💸" not in compose_brief(d)

    def test_monthly_summary_and_insight(self):
        d = _full()
        d.monthly = {
            "trades": 7,
            "expense_total": 40000,
            "expense_prev_total": 32000,
            "top_expense": [("餐飲", 15000), ("房租", 12000)],
        }
        d.insight = "台積電法人續買但 BTC 破位，要不要看一下 BTC 的支撐？"
        text = compose_brief(d)
        assert (
            "📆 上月總結" in text
            and "交易 7 筆" in text
            and "+25%" in text
            and "餐飲 15,000" in text
        )
        assert "🤖 一句話：台積電法人續買" in text

    def test_english_and_no_telegram_hint(self):
        d = _full("en")
        d.telegram_hint = False
        text = compose_brief(d)
        assert text.startswith("☀️ Morning brief 9/13 | DANNY")
        assert "📊 Your positions (3, ledger in TWD)" in text
        assert "Tomorrow NVDA 財報（system）" in text
        assert "/brief off" not in text

    def test_length_cap_trims_positions_first(self):
        d = _full()
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
        assert len(text) <= MAX_CHARS
        assert "…還有" in text or text.endswith("…")


class TestDisclosure:
    """2026-09-28：每個管道都標免責；有「一句話」時標明是哪個 AI 寫的。"""

    def test_disclaimer_always_and_ai_note_only_with_insight(self):
        text = compose_brief(_full())
        assert "不構成投資建議" in text
        assert "AI 模型" not in text, "沒有 AI 寫的段落就不該標 AI"
        d = _full()
        d.insight = "BTC 破位，看一下支撐？"
        d.insight_model = "CryptoMind Lite"
        text = compose_brief(d)
        assert "「一句話」由 CryptoMind Lite（AI 模型）產生" in text
        assert (
            text.index("🤖 一句話：")
            < text.index("CryptoMind Lite")
            < text.index("/brief off")
        )

    def test_email_variant_leaves_disclaimer_to_the_footer(self):
        d = _full("en")
        d.telegram_hint = False
        d.disclaimer = False
        d.insight = "Check BTC support."
        text = compose_brief(d)
        assert "Not investment advice" not in text
        assert "(an AI model)" in text

    def test_truncation_keeps_the_disclosure(self):
        d = _full()
        d.insight = "x"
        d.positions = [
            {
                "symbol": f"SYM{i}",
                "market": "crypto",
                "change_pct": 1.0,
                "note": "y" * 200,
            }
            for i in range(30)
        ]
        d.monthly = {"trades": 1, "expense_total": 1, "top_expense": [("z" * 900, 1)]}
        text = compose_brief(d)
        assert len(text) <= MAX_CHARS
        assert "不構成投資建議" in text and "AI 模型" in text
        assert text.rstrip().endswith("/brief off 可關閉早報）")


def test_formatters():
    assert (
        fmt_money(1234.6) == "1,235"
        and fmt_money(12.5) == "12.5"
        and fmt_money(0.5) == "0.5"
    )
    assert fmt_money(None) == "-"
    assert (
        fmt_pct(1.84) == "+1.8%  ▲"
        and fmt_pct(-0.04) == "-0.0%  ▼"
        and fmt_pct(0) == "+0.0%  ＝"
    )
