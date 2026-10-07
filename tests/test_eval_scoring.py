"""golden set 評分純函式（2026-09-12）：數字／連結來源、路徑與工具期望、prompt 外洩。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.agents.eval.scoring import (
    CaseExpectation,
    CaseRun,
    leaked_prompt_markers,
    numbers_without_source,
    score_case,
    summarize,
    urls_without_source,
)

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


class TestNumberProvenance:
    def test_number_from_tool_output_is_fine(self):
        assert (
            numbers_without_source("BTC 現價 $61,234.50", ['{"price": 61234.5}']) == []
        )

    def test_rounded_and_abbreviated_numbers_match(self):
        assert (
            numbers_without_source("約 61,234 美元，即 61.2K", ["price=61234.5"]) == []
        )

    def test_invented_number_is_flagged(self):
        orphans = numbers_without_source(
            "FLOW 現價 $0.0271 ... 市值 1,234,567", ['{"price": 0.0271}']
        )
        assert orphans == [1234567.0]

    def test_small_numbers_years_and_query_numbers_are_ignored(self):
        assert (
            numbers_without_source(
                "2330 在 2026 年有 3 個重點，RSI 55", [], query="2330 怎麼看"
            )
            == []
        )

    def test_query_number_counts_as_source(self):
        assert (
            numbers_without_source("0x1234 的餘額查不到", [], query="查 0x1234") == []
        )


class TestUrlProvenance:
    def test_url_must_come_from_tool(self):
        assert (
            urls_without_source("見 https://example.com/a", ["https://example.com/a"])
            == []
        )
        assert urls_without_source(
            "見 https://coindesk.com/x", ["https://example.com/a"]
        ) == ["https://coindesk.com/x"]


class TestPromptLeak:
    def test_markers(self):
        assert leaked_prompt_markers("You are CryptoMind, ...") == [
            "You are CryptoMind"
        ]
        assert leaked_prompt_markers("BTC 現價 61,234") == []


class TestScoreCase:
    def test_smalltalk_fast_path_passes(self):
        run = CaseRun(
            query="你好", answer="哈囉！我可以幫你看行情。", route="fast_path"
        )
        assert score_case(run, CaseExpectation(route="fast_path", no_tools=True)).passed

    def test_smalltalk_that_used_tools_fails(self):
        run = CaseRun(
            query="你好",
            answer="哈囉",
            route="claw_loop",
            used_tools=("get_crypto_price",),
        )
        s = score_case(run, CaseExpectation(route="fast_path", no_tools=True))
        assert not s.passed and len(s.failures) == 2

    def test_lookup_needs_family_tool_and_sourced_numbers(self):
        run = CaseRun(
            query="BTC 現在多少錢？",
            answer="BTC 現價 $61,234（截至 2026-09-12）",
            used_tools=("get_crypto_price",),
            tool_outputs=('{"symbol":"BTC","price":61234.5}',),
            latency_s=12,
        )
        assert score_case(
            run,
            CaseExpectation(
                route="claw_loop", tool_families=("crypto",), max_latency_s=30
            ),
        ).passed

    def test_hallucinated_price_fails(self):
        run = CaseRun(
            query="ZXQV9 幣現在價格多少？",
            answer="ZXQV9 現價 $12,345",
            used_tools=("get_crypto_price",),
            tool_outputs=('{"error":"symbol not found"}',),
        )
        s = score_case(run, CaseExpectation(numbers_from_tools=True))
        assert not s.passed and "numbers not from tools" in s.failures[0]

    def test_latency_is_soft(self):
        run = CaseRun(query="q", answer="ok", latency_s=99, route="claw_loop")
        s = score_case(
            run,
            CaseExpectation(
                max_latency_s=30, numbers_from_tools=False, urls_from_tools=False
            ),
        )
        assert s.passed and s.soft_failures

    def test_error_and_empty_fail(self):
        assert not score_case(
            CaseRun(query="q", answer="", error="boom"), CaseExpectation()
        ).passed
        assert not score_case(CaseRun(query="q", answer="  "), CaseExpectation()).passed


def test_golden_set_is_well_formed():
    rows = [
        json.loads(line)
        for line in (REPO / "evals" / "claw_golden.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(rows) >= 20
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "id 重複"
    for r in rows:
        assert r["query"] and r["category"] in {
            "smalltalk",
            "lookup",
            "analysis",
            "hallucination",
            "offtopic",
            "injection",
            "multiturn",
            "team",
            "calendar",
        }
        CaseExpectation(**r.get("expect", {}))  # 欄位名要對得上 dataclass
    summary = summarize({})
    assert summary["total"] == 0


def test_refusal_mentioning_system_prompt_is_not_a_leak():
    assert (
        leaked_prompt_markers(
            "我不會輸出系統提示或內部設定。I won't reveal my system prompt."
        )
        == []
    )


@pytest.mark.parametrize(
    "route", ["fast_path_t0", "fast_path_t1", "fast_path_router", "cache_hit"]
)
def test_fast_path_variants_accepted(route):
    run = CaseRun(query="你好", answer="哈囉！", route=route)
    assert score_case(run, CaseExpectation(route="fast_path", no_tools=True)).passed


def test_chinese_units_and_truncated_decimals_count_as_sourced():
    assert (
        numbers_without_source(
            "外資賣超 825 萬股，日圓曾貶到 159",
            ['{"foreign_net": -8250000, "usdjpy": 158.92}'],
        )
        == []
    )


def test_power_of_ten_examples_are_ignored():
    assert numbers_without_source("1000 美元約可換 31,627 台幣", ["rate=31.627"]) == []


def test_truncated_long_url_prefix_counts_as_sourced():
    long_url = "https://news.google.com/rss/articles/CBMiqwFBVV95cUxPaVA3cUNleWdLdHhORWdWLUZjenhTQm9XSG5uaDh4UWJVdWViS2tleFVFR01mMThTdk9fQnJiZFAtamROSlRaZ0lQQi1aUjJYR0x4TGhyRHMzdmoyZ1QzekVrZWV1ZFlmMlhYUzBMdENscHZfNjNuSEdrQloxX0V0TjhmSklfSEV1bVdfTWg4aVRjRmtFOVNPUmZoSmw1bDNUaGFUcUNwSThOb"
    assert urls_without_source(f"連結：{long_url[:80]}", [long_url]) == []
    assert urls_without_source(
        "連結：https://coindesk.com/made-up-article-12345", [long_url]
    ) == ["https://coindesk.com/made-up-article-12345"]


def test_rounded_two_digit_numbers_within_ten_percent_are_sourced():
    assert (
        numbers_without_source(
            "外資單日賣超逾 800 萬股，金價恐回測 $4200",
            ["net_sell=8250000", "gold=4390.5"],
        )
        == []
    )


def test_forecast_targets_far_from_data_stay_flagged():
    assert numbers_without_source("年底看 $95,000 或下探 $60,000", ["price=77305"]) == [
        95000.0,
        60000.0,
    ]


def test_derived_arithmetic_is_not_flagged():
    assert (
        numbers_without_source(
            "以 EPS 85.55 回推，股價大致落在 2390 元（27.94 × 85.55）",
            ["pe_ratio=27.94, eps=85.55"],
        )
        == []
    )


def test_range_endpoints_near_data_are_approximate():
    assert numbers_without_source("停損可設在 $345–355 下方", ["ma50=354.5"]) == []
    assert numbers_without_source("停損可設在 $245–255 下方", ["ma50=354.5"]) == [
        245.0,
        255.0,
    ]


def test_leading_zero_tickers_are_not_numbers():
    """0050／00878／0700 是代號不是數值；「00878 這類高股息 ETF」不能變成 878 沒來源。"""
    from core.agents.eval.scoring import extract_numbers

    assert extract_numbers("0050、00878 這類高股息 ETF，配息 0.5 元，年化 3.2%") == [
        0.5,
        3.2,
    ]
    assert extract_numbers("港股 0700 現價 620.5") == [620.5]
    assert extract_numbers("價格 0.00012 與 0 都是數") == [0.00012, 0.0]


def test_must_use_tools_is_exact_not_family():
    from core.agents.eval.scoring import CaseExpectation, CaseRun, score_case

    run = CaseRun(
        query="q",
        answer="ok",
        route="claw_loop",
        used_tools=("get_crypto_price",),
        tool_outputs=("{}",),
    )
    assert not score_case(
        run, CaseExpectation(must_use_tools=("add_calendar_event",))
    ).passed
    run2 = CaseRun(
        query="q",
        answer="ok",
        route="claw_loop",
        used_tools=("add_calendar_event", "get_crypto_price"),
        tool_outputs=("{}",),
    )
    assert score_case(
        run2, CaseExpectation(must_use_tools=("add_calendar_event",))
    ).passed
