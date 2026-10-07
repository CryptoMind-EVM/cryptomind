"""Unit tests for skill 驅動輸出格式（取代寫死模板）。

背景（DANNY 決策）：輸出格式不該寫死在 manager.yaml 模板＋關鍵詞 if/else，
應該由 skill 系統驅動（每個 SKILL.md 自帶 Method + Output Format）。

調查發現兩個潛伏 bug：
1. match_skills 用 applies_to 過濾，但轉成 CLAW 單 agent（name=cryptomind）後
   沒有任何 skill 的 applies_to 包含 cryptomind → skill 系統全滅、零注入。
2. priority 加分無條件生效 → 拿掉 applies_to 過濾後，任何問題都會
   硬塞 3 個不相關 skill。

DANNY 後續指正：Hermes/OpenClaw 的 skill 是模型自主載入（progressive
disclosure：prompt 只放目錄，agent 自己呼叫 load_skill），不是 harness
關鍵詞匹配硬塞。claw agent 已改為此模式；關鍵詞匹配僅保留給 legacy agents。

Covers:
- cryptomind 舊行為（applies_to 過濾全滅）的記錄
- claw agent：目錄注入＋load_skill 工具（模型自主），不硬塞完整 body
- priority 只當 tiebreaker：不相關 query 不匹配任何 skill（legacy 匹配器）
- comparison-analysis skill 取代寫死的 response_format_compare 模板
- citation_rules 共用段落注入 agent prompt
- claw 輕量清理不破壞 skill 驅動的輸出段落
"""

from unittest.mock import MagicMock

import pytest

from core.agents.agents.cryptomind_agent import CryptoMindAgent
from core.agents.manager.claw_loop import _clean_claw_response
from core.agents.models import SubTask
from core.agents.prompt_registry import PromptRegistry
from core.agents.skill_loader import SkillLoader

pytestmark = pytest.mark.unit


def _loader() -> SkillLoader:
    loader = SkillLoader()
    loader.load_all()
    return loader


# ---------------------------------------------------------------------------
# Skill matching for the CLAW agent
# ---------------------------------------------------------------------------


def test_cryptomind_name_filter_kills_all_skills():
    """記錄舊 bug：用 cryptomind 過濾 applies_to 會全滅（沒 skill 列它）。"""
    matched = _loader().match_skills("FLOW 基本面 值得買嗎", agent_name="cryptomind")
    assert matched == []


def test_no_filter_matches_crypto_fundamental():
    matched = _loader().match_skills("FLOW 基本面 值得買嗎", agent_name=None)
    names = [s.name for s in matched]
    assert "crypto-fundamental-analysis" in names


def test_cryptomind_agent_uses_model_driven_skills():
    assert CryptoMindAgent.match_all_skills is True


def test_cryptomind_injects_full_skill_for_timing_question():
    """Regression：線上 bug — 中等模型問「美股會跌到何時」不自覺 load_skill →
    空回覆。market-risk-assessment 標記 eager_load，cryptomind 對命中它的
    query 直接注入完整 body（不只目錄），讓中等模型即使不自覺 load_skill
    也有方法引導。學 Hermes eager-load flag（issue #14405）。
    """
    agent = CryptoMindAgent(llm_client=MagicMock(), tool_registry=None)
    task = SubTask(
        step=0,
        description="你認為美股會跌到什麼時候",
        agent="cryptomind",
        context={"original_query": "你認為美股會跌到什麼時候"},
    )
    result = agent._inject_skill_instructions("BASE", task)

    # 目錄注入（所有 match_all_skills agent 都有）
    assert "可用分析方法" in result
    # eager-load：擇時問題命中 market-risk-assessment → 直接注入完整 body。
    # body 已英文化（#355）——用英文標題當 marker。
    assert "Market Risk Assessment" in result, (
        "擇時問題應 eager-load market-risk-assessment 完整方法論"
    )
    # 完整方法論含 Step（方法步驟），目錄只有名稱+描述
    assert "Step" in result or "恐慌貪婪" in result, "應注入 skill 方法步驟"


def test_claw_agent_gets_catalog_not_forced_injection():
    """OpenClaw 式：claw agent 只拿到 skill 目錄（名稱＋描述）＋ load_skill
    指引，不做 harness 端關鍵詞匹配硬塞——選擇權在模型。"""
    agent = CryptoMindAgent(llm_client=MagicMock(), tool_registry=None)
    task = SubTask(
        step=0,
        description="比較 BTC 和 ETH 哪個好",
        agent="cryptomind",
        context={"original_query": "比較 BTC 和 ETH 哪個好"},
    )
    prompt = agent._inject_skill_instructions("BASE", task)
    # 目錄條目在（名稱＋描述層級）
    assert "comparison-analysis" in prompt
    assert "可用分析方法" in prompt
    assert "load_skill" in prompt
    # 完整 skill body 不在（那要靠模型自己 load）
    assert "<skill" not in prompt
    assert "Output Format" not in prompt


def test_load_skill_tool_returns_full_body():
    from core.agents.tools import load_skill

    result = load_skill.invoke({"skill_name": "comparison-analysis"})
    assert '<skill name="comparison-analysis">' in result
    # Output Format 的表格在完整內容裡（body 英文化 #355 → Asset Comparison）
    assert "Asset Comparison" in result


def test_load_skill_tool_unknown_lists_available():
    from core.agents.tools import load_skill

    result = load_skill.invoke({"skill_name": "not-a-skill"})
    assert "not found" in result
    assert "comparison-analysis" in result


# ---------------------------------------------------------------------------
# priority 只當 tiebreaker（不相關 query 不硬塞 skill）
# ---------------------------------------------------------------------------


def test_unrelated_query_matches_no_skills():
    assert _loader().match_skills("你好", agent_name=None) == []


def test_priority_breaks_ties_between_matched_skills():
    loader = _loader()
    matched = loader.match_skills("BTC 技術分析 基本面", agent_name=None)
    assert matched, "相關 query 應有匹配"
    # 全部都是有關鍵詞命中的 skill，且不超過 max_matches
    assert len(matched) <= 3


# ---------------------------------------------------------------------------
# comparison-analysis skill（取代寫死的 response_format_compare 模板）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    ["比較 BTC 和 ETH", "SOL vs AVAX 哪個好", "台積電跟聯發科哪個比較值得"],
)
def test_comparison_skill_fires(query):
    matched = _loader().match_skills(query, agent_name=None)
    assert "comparison-analysis" in [s.name for s in matched]


def test_comparison_skill_carries_output_format():
    skill = _loader().get_skill("comparison-analysis")
    assert skill is not None
    assert "Asset Comparison" in skill.body  # Output Format 表格標題（#355 英文化）
    assert "Output Format" in skill.body


# ---------------------------------------------------------------------------
# citation_rules 共用段落
# ---------------------------------------------------------------------------


def test_citation_rules_section_exists():
    zh = PromptRegistry.get("shared", "citation_rules", "zh-TW")
    en = PromptRegistry.get("shared", "citation_rules", "en")
    assert "引用與來源規則" in zh
    assert "工具實際回傳" in zh
    assert "Citation" in en


def test_citation_rules_injected_into_agent_prompt():
    """citation_rules 應在 CryptoMindAgent 的完整 system prompt 中。

    2026-07-19 G1 變更：CryptoMindAgent 覆寫 _inject_tool_retry_instructions 為
    no-op（避免 protocol 被注入兩次 + 蓋過 F1 model-aware 邏輯）。所有 protocol
    組裝集中在 _get_system_prompt。因此本測試改測 _get_system_prompt 的輸出。
    """
    agent = CryptoMindAgent(llm_client=MagicMock(), tool_registry=None)
    # CryptoMindAgent 覆寫後，_inject_tool_retry_instructions 是 no-op
    prompt = agent._get_system_prompt("zh-TW")
    assert "引用與來源規則" in prompt
    assert "資料時效規則" in prompt  # 既有 data_freshness 仍在
    # 順帶驗證覆寫有生效（_inject 不再重複注入）
    assert agent._inject_tool_retry_instructions(prompt, "zh-TW") == prompt


# ---------------------------------------------------------------------------
# claw 輕量清理
# ---------------------------------------------------------------------------


def test_clean_preserves_skill_driven_comparison_section():
    text = "### 標的比較\n| 項目 | BTC | ETH |\n\n### 分析結論\n各有適配情境"
    assert _clean_claw_response(text) == text


def test_clean_strips_fake_urls_and_empty_links():
    text = "詳見 https://example.com/fake-report 以及 [報告]()"
    cleaned = _clean_claw_response(text)
    assert "example.com" not in cleaned
    assert "[報告]()" not in cleaned
    assert "報告" in cleaned


def test_clean_strips_agent_tags():
    assert _clean_claw_response("[cryptomind] FLOW 現價 $0.0271") == "FLOW 現價 $0.0271"
