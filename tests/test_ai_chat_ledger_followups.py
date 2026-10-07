"""AI 問答：帳本類問題與「接著問」不能被快速通道回掉；確認卡按下去之後要回報結果。

2026-10-05 線上（DANNY 手機截圖）：
- 提議記帳、使用者按「確認記入」後，回覆只剩「請確認同意卡，核准後才會寫入你的帳本」，
  沒有任何寫入成功／失敗的說明；
- 使用者接著問「記錄了嗎」→ 被當閒聊，快速通道的小模型（無對話歷史、無工具）回一段開場白；
- 問「幫我查看一下帳本目前有什麼紀錄」→ 同一條路，回「我無法查看您的個人帳本」——其實有 query_ledger 工具。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.agents.manager.claw_loop import _resolve_fast_route
from core.agents.manager.consent_gate import build_consent_outcome_text
from core.agents.triage import (
    DEEP_ANALYSIS,
    SIMPLE_QA,
    classify_query,
    has_finance_signal,
    is_smalltalk,
)

pytestmark = pytest.mark.unit


# ── 帳本／個人紀錄語彙＝有訊號，不能走無工具的快速通道 ─────────────────────────────

LEDGER_QUERIES = [
    "記錄了嗎",
    "记录了吗",
    "幫我查看一下帳本目前有什麼紀錄",
    "幫我記錄一下 我欠了柏均11萬台幣",
    "我這個月支出多少",
    "收入跟支出各多少",
    "did you record it in my ledger",
    "show my expenses",
    "добавь в журнал расходов",
]


@pytest.mark.parametrize("query", LEDGER_QUERIES)
def test_ledger_questions_are_not_smalltalk(query):
    assert has_finance_signal(query) is True
    assert is_smalltalk(query) is False


@pytest.mark.parametrize("query", LEDGER_QUERIES)
async def test_ledger_questions_never_reach_the_t1_classifier(query):
    async def llm_must_not_run(_prompt):
        raise AssertionError("有帳本訊號就該直接走完整路徑，不該再問小模型")

    assert await classify_query(query, llm_must_not_run) == DEEP_ANALYSIS


# ── 有對話歷史時，只有明確寒暄白名單走快速通道 ──────────────────────────────────────


async def _boom(_prompt):
    raise AssertionError("有歷史時不該呼叫 Router／T1")


@pytest.mark.parametrize("router_on", [False, True])
async def test_followup_with_history_goes_full_path(router_on):
    """「那它呢」「為什麼」這類接著問，單獨看像閒聊；有歷史就一律走完整路徑。"""
    with patch("core.agents.manager.claw_loop.router_enabled", return_value=router_on):
        for query in ["那它呢", "為什麼會這樣", "你可以再說清楚一點嗎"]:
            route, decision = await _resolve_fast_route(query, _boom, _boom, has_history=True)
            assert route is None and decision is None


@pytest.mark.parametrize("router_on", [False, True])
async def test_plain_greeting_with_history_keeps_fast_path(router_on):
    with patch("core.agents.manager.claw_loop.router_enabled", return_value=router_on):
        route, decision = await _resolve_fast_route("謝謝", _boom, _boom, has_history=True)
    assert route == "T0" and decision is None


async def test_without_history_t1_still_works():
    """沒有歷史（對話第一句）維持原行為：非金融的 meta 問題走 T1 快速通道。"""

    async def t1(_prompt):
        return SIMPLE_QA

    with patch("core.agents.manager.claw_loop.router_enabled", return_value=False):
        route, _ = await _resolve_fast_route("你可以幫我做什麼", _boom, t1, has_history=False)
    assert route == "T1"


# ── 確認卡之後的結果句 ──────────────────────────────────────────────────────────────

ENTRY = {"kind": "journal_entry", "entry_type": "expense", "amount": 110000, "currency": "TWD", "category": "other"}


def _outcome(**kw):
    base = {"signal": ENTRY, "approved": True, "ok": True, "edited": {}}
    base.update(kw)
    return base


def test_outcome_done_names_what_was_written():
    text = build_consent_outcome_text([_outcome()], "zh-TW")
    assert text.startswith("\n\n")
    assert "已記入帳本" in text and "支出 110000 TWD" in text and "other" in text


def test_outcome_uses_the_edited_values_the_user_saved():
    text = build_consent_outcome_text([_outcome(edited={"amount": 99000, "category": "food"})], "zh-TW")
    assert "99000" in text and "110000" not in text and "food" in text


def test_outcome_failed_says_it_was_not_saved():
    text = build_consent_outcome_text([_outcome(ok=False)], "zh-TW")
    assert "沒有寫入帳本" in text and "✅" not in text
    assert "NOT saved" in build_consent_outcome_text([_outcome(ok=False)], "en")


def test_outcome_declined_says_nothing_was_added():
    text = build_consent_outcome_text([_outcome(approved=False, ok=False)], "zh-TW")
    assert "已取消" in text and "沒有記入帳本" in text
    assert "nothing was added" in build_consent_outcome_text([_outcome(approved=False, ok=False)], "en")


@pytest.mark.parametrize("language", ["zh-TW", "zh-CN", "en", "ru"])
def test_outcome_exists_in_every_language_and_state(language):
    for kwargs in ({}, {"ok": False}, {"approved": False, "ok": False}):
        assert build_consent_outcome_text([_outcome(**kwargs)], language).strip()


def test_outcome_unknown_language_falls_back_to_english():
    assert "ledger" in build_consent_outcome_text([_outcome()], "xx").lower()


def test_outcome_delete_and_update_show_the_entry_id():
    delete = {"kind": "journal_delete", "entry_id": 12}
    update = {"kind": "journal_update", "entry_id": 7}
    text = build_consent_outcome_text(
        [
            {"signal": delete, "approved": True, "ok": True, "edited": {}},
            {"signal": update, "approved": True, "ok": False, "edited": {}},
        ],
        "zh-TW",
    )
    assert "已刪除帳本紀錄 #12" in text and "修改失敗" in text and "#7" in text


def test_outcome_skips_kinds_it_has_no_message_for():
    """沒有結果句的 kind 不能亂報成功（記憶／技能現在有結果句，見下一個測試）。"""
    unknown = {"kind": "some_future_kind", "key": "k"}
    assert build_consent_outcome_text([{"signal": unknown, "approved": True, "ok": True, "edited": {}}], "zh-TW") == ""
    assert build_consent_outcome_text([], "zh-TW") == ""
    assert build_consent_outcome_text(None, "zh-TW") == ""


def test_memory_outcome_reports_only_what_actually_happened():
    """記憶寫入成功才說「已記住」；失敗要明說沒記住；取消要說沒有記。"""
    memory = {"kind": "create_memory", "content": "我偏好長期持有", "category": "preference"}

    def run(approved, ok):
        return build_consent_outcome_text([{"signal": memory, "approved": approved, "ok": ok, "edited": {}}], "zh-TW")

    done, failed, declined = run(True, True), run(True, False), run(False, False)
    assert "✅" in done and "我偏好長期持有" in done
    assert "✅" not in failed and "我偏好長期持有" in failed
    assert "✅" not in declined and "我偏好長期持有" in declined
    assert len({done, failed, declined}) == 3


def test_outcome_multi_card_batch_one_line_each():
    text = build_consent_outcome_text(
        [_outcome(), _outcome(signal={**ENTRY, "entry_type": "income", "amount": 500}, ok=False)], "zh-TW"
    )
    lines = text.strip().splitlines()
    assert len(lines) == 2 and "已記入帳本" in lines[0] and "沒有寫入帳本" in lines[1]
