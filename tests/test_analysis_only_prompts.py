"""平台只做分析、不設計買賣：prompt／skill／工具描述對齊（2026-10）。

產品方針：本平台提供分析（波動度、歷史回撤、關鍵支撐壓力位當資料、情境分析），
**不**產出個人化的進出場點、停損停利點、部位大小或買賣規則設計。使用者問
「我的停損規則是跌 5% 就賣」這類題目時，模型要說明平台只做分析，並主動給可用的分析資料。

本檔守的是「文字檔」：
- risk-assessment-review skill 不再教停損／倉位處方，自動觸發字縮小到風險揭露
- investment-judgment／market-risk-assessment／swap-quote 沒有「建議進場／買點」之類語句
- shared.yaml analysis_not_advice 有「不設計買賣規則」條款（4 語）
- scope_clarification／clarify 工具描述限定「無標的且無財務脈絡」才釐清（M6：個人理財題先直接答）
- 使用者看得到的 skill 目錄描述（web i18n）不再宣稱「覆核停損／部位」
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.agents.prompt_registry import PromptRegistry
from core.agents.skill_loader import get_skill_loader
from core.tools.clarify_tool import clarify

ROOT = Path(__file__).resolve().parent.parent
LANGS = ["zh-TW", "zh-CN", "en", "ru"]


def _skill(name: str):
    skill = get_skill_loader().get_skill(name)
    assert skill is not None, f"skill {name} 不存在"
    return skill


def _matched_names(query: str) -> list[str]:
    return [
        s.name for s in get_skill_loader().match_skills(query=query, max_matches=20)
    ]


# ============================================================================
# risk-assessment-review：純風險揭露審查
# ============================================================================

TRADE_RULE_KEYWORDS = [
    "止損",
    "止蝕",
    "倉位",
    "部位",
    "資金管理",
    "stop loss",
    "position sizing",
]


def test_risk_review_keywords_exclude_trade_rule_terms():
    keywords = [k.lower() for k in _skill("risk-assessment-review").auto_fire_keywords]
    for term in TRADE_RULE_KEYWORDS:
        assert term not in keywords, (
            f"{term!r} 是買賣規則詞，不該自動觸發風險揭露 skill"
        )


def test_risk_review_keeps_risk_disclosure_keywords():
    keywords = [k.lower() for k in _skill("risk-assessment-review").auto_fire_keywords]
    for term in ("風險", "槓桿", "最大回撤", "drawdown", "all in", "重倉", "滿倉"):
        assert term in keywords, f"風險揭露關鍵字 {term!r} 不該被一併移除"


@pytest.mark.parametrize(
    "query",
    [
        "止損設在哪",
        "倉位怎麼分配",
        "我的停損規則是跌 5% 就賣，這樣好嗎",
        "where should I set my stop loss",
        "position sizing for my portfolio",
    ],
)
def test_trade_rule_questions_do_not_autoload_risk_review(query):
    """買賣規則題交給 shared.yaml 的 analysis_not_advice，不靠風控 skill 自動載入。"""
    assert "risk-assessment-review" not in _matched_names(query)


@pytest.mark.parametrize(
    "query", ["這筆風險大嗎", "我該 all in 嗎", "槓桿會不會被強平", "最大回撤多少"]
)
def test_risk_questions_still_autoload_risk_review(query):
    assert "risk-assessment-review" in _matched_names(query)


def test_risk_review_description_does_not_advertise_trade_rules():
    desc = _skill("risk-assessment-review").description.lower()
    for term in ("stop-loss", "stop loss", "position sizing", "position size"):
        assert term not in desc


def test_risk_review_body_has_no_trade_rule_prescriptions():
    body = _skill("risk-assessment-review").body
    for phrase in (
        "Is the stop-loss point explicit",  # 舊：檢查使用者有沒有設停損
        "Stop-loss concept",  # 舊：教停損概念
        "Position-sizing concept",
        "5–20%",  # 舊：單一資產佔比建議
        "Have an exit plan",  # 舊：教使用者設出場計畫
        "General sizing principle",
    ):
        assert phrase not in body, f"risk-assessment-review 不該再包含「{phrase}」"


def test_risk_review_body_redirects_to_analysis_data():
    body = _skill("risk-assessment-review").body.lower()
    assert "analysis only" in body
    for data_point in ("volatility", "drawdown", "support", "scenario"):
        assert data_point in body, f"風控 skill 應引導提供「{data_point}」這類分析資料"


# ============================================================================
# 其他 skill：沒有進出場／買點語句；swap-quote 只做唯讀報價
# ============================================================================


def test_investment_judgment_has_no_entry_instructions():
    body = _skill("investment-judgment").body
    for phrase in (
        "may consider entering",
        "avoid entering",
        "before entering",
        "entry/exit reference",
    ):
        assert phrase not in body, f"investment-judgment 不該包含進出場指示「{phrase}」"
    assert "analysis only" in body.lower()


def test_market_risk_assessment_does_not_call_extreme_fear_a_buy_point():
    body = _skill("market-risk-assessment").body.lower()
    assert "buy point" not in body


@pytest.mark.parametrize(
    "query",
    [
        "台積電值得買嗎",
        "BTC 現在可以買嗎",
        "我該賣掉 ETH 嗎",
        "美股現在適合買嗎",
        "should I trade NVDA",
    ],
)
def test_swap_quote_not_loaded_for_general_finance_questions(query):
    assert "swap-quote" not in _matched_names(query)


@pytest.mark.parametrize(
    "query",
    [
        "TON 換 USDt 能拿多少",
        "swap 100 TON to USDt",
        "100 TON 兌換多少 USDt",
        "把 USDt 換成 TON",
    ],
)
def test_swap_quote_still_loaded_for_swap_quote_questions(query):
    assert "swap-quote" in _matched_names(query)


def test_swap_quote_stays_read_only():
    skill = _skill("swap-quote")
    assert "does not execute" in skill.description.lower()
    assert "尚未執行" in skill.body


# ============================================================================
# shared.yaml：analysis_not_advice「不設計買賣規則」條款
# ============================================================================

# 每語言：平台只做分析、停損例子、要主動提供的三類資料
TRADE_RULE_CLAUSE_MARKERS = {
    "zh-TW": ("不設計買賣規則", "只做分析", "停損", "波動度", "回撤", "支撐"),
    "zh-CN": ("不设计买卖规则", "只做分析", "止损", "波动度", "回撤", "支撑"),
    "en": (
        "Do not design trading rules",
        "analysis only",
        "stop-loss",
        "volatility",
        "drawdown",
        "support",
    ),
    "ru": (
        "Не проектируйте торговые правила",
        "только анализ",
        "стоп-лосс",
        "волатильност",
        "просадк",
        "поддержк",
    ),
}


@pytest.mark.parametrize("lang", LANGS)
def test_analysis_not_advice_has_trade_rule_clause(lang):
    out = PromptRegistry.get("shared", "analysis_not_advice", lang)
    for marker in TRADE_RULE_CLAUSE_MARKERS[lang]:
        assert marker in out, f"analysis_not_advice（{lang}）缺「{marker}」"


@pytest.mark.parametrize("lang", LANGS)
def test_analysis_not_advice_clause_is_helpful_not_a_flat_refusal(lang):
    """條款必須叫模型「主動給資料」，不是生硬拒絕。"""
    out = PromptRegistry.get("shared", "analysis_not_advice", lang)
    helpful = {
        "zh-TW": "不要生硬拒絕",
        "zh-CN": "不要生硬拒绝",
        "en": "not a flat refusal",
        "ru": "без сухого отказа",
    }[lang]
    assert helpful in out


@pytest.mark.parametrize("lang", LANGS)
def test_judgment_questions_points_to_trade_rule_clause(lang):
    """「不下進出場指令」那條要指到新條款，兩處規則不會各說各話。"""
    out = PromptRegistry.get("shared", "judgment_questions", lang)
    assert "analysis_not_advice" in out
    pointer = {
        "zh-TW": "買賣規則",
        "zh-CN": "买卖规则",
        "en": "trading rules",
        "ru": "торговых правил",
    }[lang]
    assert pointer in out


# ============================================================================
# scope_clarification／clarify 工具：僅限「投資標的不明且無財務脈絡」（M6）
# ============================================================================

SCOPE_LIMIT_MARKERS = {
    "zh-TW": ("沒有任何財務脈絡", "個人理財", "還款", "先直接回答"),
    "zh-CN": ("没有任何财务脉络", "个人理财", "还款", "先直接回答"),
    "en": ("no financial context", "personal-finance", "repayment", "answer directly"),
    "ru": (
        "нет финансового контекста",
        "личных финансов",
        "погашени",
        "отвечайте сразу",
    ),
}

# 舊範例選項：強模型會把它原樣套到「我想投資」「月薪六萬想還款又想投資」上
OLD_COPY_PRONE_OPTIONS = {
    "zh-TW": "目前技術面與基本面的整體概況",
    "zh-CN": "目前技术面与基本面的整体概况",
    "en": "an overall call on whether to enter now",
    "ru": "общую оценку — стоит ли входить сейчас",
}


@pytest.mark.parametrize("lang", LANGS)
def test_scope_clarification_limited_to_no_financial_context(lang):
    out = PromptRegistry.get("shared", "scope_clarification", lang)
    for marker in SCOPE_LIMIT_MARKERS[lang]:
        assert marker in out, f"scope_clarification（{lang}）缺限定語「{marker}」"


@pytest.mark.parametrize("lang", LANGS)
def test_scope_clarification_examples_are_neutral(lang):
    out = PromptRegistry.get("shared", "scope_clarification", lang)
    assert OLD_COPY_PRONE_OPTIONS[lang] not in out, "範例 options 仍是舊的偏投資組合"


def test_clarify_tool_description_excludes_personal_finance():
    desc = clarify.description
    for marker in ("個人理財", "預算", "還款", "直接回答"):
        assert marker in desc, f"clarify 工具描述缺「{marker}」"
    # 原本的觸發情境仍在（既有行為不退化）
    assert "台股" in desc and "釐清" in desc


# ============================================================================
# 使用者可見的 skill 目錄描述（web i18n）
# ============================================================================

BANNED_IN_RISK_DESC = (
    "停損",
    "止损",
    "部位",
    "仓位",
    "stop-loss",
    "position",
    "стоп-лосс",
    "позици",
)


@pytest.mark.parametrize("lang", LANGS)
def test_web_catalog_risk_review_description_has_no_trade_rules(lang):
    data = json.loads(
        (ROOT / "web" / "js" / "i18n" / f"{lang}.json").read_text(encoding="utf-8")
    )
    desc = data["skills"]["risk-assessment-review"]["description"].lower()
    for term in BANNED_IN_RISK_DESC:
        assert term not in desc, f"web i18n {lang} 的風控 skill 描述仍含「{term}」"


@pytest.mark.parametrize("lang", ["zh-TW", "zh-CN"])
def test_web_catalog_tw_technical_description_has_no_buy_sell_points(lang):
    data = json.loads(
        (ROOT / "web" / "js" / "i18n" / f"{lang}.json").read_text(encoding="utf-8")
    )
    desc = data["skills"]["tw-stock-technical"]["description"]
    assert "買賣點" not in desc and "买卖点" not in desc
