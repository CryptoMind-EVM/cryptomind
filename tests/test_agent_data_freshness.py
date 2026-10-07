"""Unit tests for agent data-freshness awareness.

背景（DANNY 回報）：聊天 agent 用超舊資訊回答行情問題（例：FLOW
「近期」$0.55–$0.65），因為：
1. system prompt 沒注入今天日期 → 模型無從判斷資訊新舊
2. web_search 結果不帶發布日期 → 舊文章看起來像新聞
3. 沒有「行情必須用即時工具、禁止訓練記憶」的規則

Covers:
- CryptoMindAgent system prompt 含當前日期（UTC+8）
- shared.yaml 的 data_freshness 規則存在且會被 append
- Tavily 結果攜帶 published_date；web_search_tool 輸出含搜尋時間戳
- PromptRegistry 時間變數用台灣時間（修正原本 UTC 標成 UTC+8 的潛伏 bug）
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from core.agents.agents.cryptomind_agent import CryptoMindAgent
from core.agents.prompt_registry import PromptRegistry

TAIPEI_TZ = timezone(timedelta(hours=8))


def _today_taipei() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")


def _agent() -> CryptoMindAgent:
    return CryptoMindAgent(llm_client=None, tool_registry=None)


# ---------------------------------------------------------------------------
# System prompt: current date injection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_system_prompt_contains_current_date_zh_tw():
    prompt = _agent()._get_system_prompt("zh-TW")
    assert _today_taipei() in prompt
    assert "現在時間" in prompt


@pytest.mark.unit
def test_system_prompt_contains_current_date_en():
    prompt = _agent()._get_system_prompt("en")
    assert _today_taipei() in prompt
    assert "Current time" in prompt


@pytest.mark.unit
def test_system_prompt_contains_current_date_zh_cn():
    prompt = _agent()._get_system_prompt("zh-CN")
    assert _today_taipei() in prompt


# ---------------------------------------------------------------------------
# data_freshness shared prompt section
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_shared_yaml_has_data_freshness_section():
    zh = PromptRegistry.get("shared", "data_freshness", "zh-TW")
    en = PromptRegistry.get("shared", "data_freshness", "en")
    assert "即時工具" in zh
    assert "訓練記憶" in zh
    assert "real-time" in en.lower()


@pytest.mark.unit
def test_inject_appends_data_freshness_rules():
    """data_freshness 規則應在 CryptoMindAgent system prompt 中。

    2026-07-19 G1 變更：CryptoMindAgent 覆寫 _inject_tool_retry_instructions
    為 no-op（避免 protocol 被注入兩次）。data_freshness 改從 _get_system_prompt
    注入，所以本測試改測 _get_system_prompt 的輸出。
    """
    out = _agent()._get_system_prompt("zh-TW")
    assert "資料時效" in out


# ---------------------------------------------------------------------------
# analysis_not_advice shared prompt section（2026-09-14 合規口吻）
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("language", ["zh-TW", "zh-CN", "en", "ru"])
def test_shared_yaml_has_analysis_not_advice_section(language):
    out = PromptRegistry.get("shared", "analysis_not_advice", language)
    assert out.strip(), language
    # 四語都要講到「不代理交易／不保管」
    assert any(
        k in out for k in ("不代理交易", "does not custody", "не совершает сделки")
    )


@pytest.mark.unit
def test_cryptomind_prompt_includes_analysis_not_advice():
    """兩條注入路徑都要有：CryptoMindAgent 走 _get_system_prompt，
    其他 ReAct agent 走 base 的 _inject_tool_retry_instructions。"""
    from core.agents.base_react_agent import BaseReActAgent

    assert "分析口吻" in _agent()._get_system_prompt("zh-TW")
    base = BaseReActAgent.__new__(BaseReActAgent)
    assert "分析口吻" in base._inject_tool_retry_instructions("", "zh-TW")


# ---------------------------------------------------------------------------
# web_search: dates on results + searched-at timestamp
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.mark.unit
def test_search_tavily_carries_published_date():
    from core.tools import web_search

    payload = {
        "results": [
            {
                "title": "FLOW price analysis",
                "url": "https://example.com/a",
                "content": "FLOW trades around $0.60",
                "published_date": "2024-03-01",
            }
        ]
    }
    with patch.object(web_search.httpx, "post", return_value=_FakeResp(payload)):
        results = web_search.search_tavily("flow price", "tvly-key")

    assert results[0]["published_date"] == "2024-03-01"


@pytest.mark.unit
def test_web_search_tool_output_has_dates_and_search_time():
    from core.tools import web_search

    fake_results = [
        {
            "title": "Old article",
            "link": "https://example.com/a",
            "snippet": "FLOW trades around $0.60",
            "published_date": "2024-03-01",
        },
        {
            "title": "No date article",
            "link": "https://example.com/b",
            "snippet": "something",
        },
    ]
    with patch.object(web_search, "search_web", return_value=fake_results):
        out = web_search.web_search_tool.invoke(
            {"query": "flow price", "purpose": "test"}
        )

    # 搜尋時間戳讓模型能比對文章日期 vs 現在
    assert _today_taipei() in out
    # 有日期的結果要顯示發布日期
    assert "2024-03-01" in out


# ---------------------------------------------------------------------------
# PromptRegistry time variables use Taiwan time (UTC+8)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prompt_registry_time_vars_are_taipei():
    # 比對到「分鐘」等級才能抓到「UTC 時間被標成台灣時間」的 8 小時偏移
    PromptRegistry.load()
    PromptRegistry._prompts["_test_freshness"] = {
        "probe": {"zh-TW": "{current_datetime_tw}"}
    }
    try:
        out = PromptRegistry.render("_test_freshness", "probe", language="zh-TW")
        rendered = datetime.strptime(out, "%Y-%m-%d %H:%M:%S")
        now_taipei = datetime.now(TAIPEI_TZ).replace(tzinfo=None)
        drift = abs((now_taipei - rendered).total_seconds())
        assert drift < 300, f"time vars drift {drift}s from Taipei time: {out}"
    finally:
        PromptRegistry._prompts.pop("_test_freshness", None)
