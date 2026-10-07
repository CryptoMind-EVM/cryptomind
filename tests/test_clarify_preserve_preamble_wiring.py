"""clarify 卡片保留已串流前言的接線回歸（2026-09-07「答案顯示完又消失」）。

背景：model-driven clarify（模型跑一輪後自行呼叫 clarify 工具）觸發時，
前端把 botMsgDiv.innerHTML 整個換成三選一卡片——模型在呼叫前串流的
「看起來像答案」的前言被蓋掉，使用者以為回答消失。修法：前言（＋思考
區塊）渲染保留在卡片上方；卡片渲染前 cancelStreamRender 防待處理 rAF
回蓋。

本檔守住三件事：前言保留、cancelStreamRender 前置、卡片本體不回退。

2026-09-24 起卡片分派抽成 renderHitlQuestion（斷線續傳共用同一份），
前言由呼叫端算好傳入——守的三件事不變，只是位置分成「呼叫端」與「函式本體」。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
JS = REPO / "web" / "js" / "chat-analysis.js"


def _seg(js: str, start: str, end: str) -> str:
    s = js.index(start)
    return js[s : js.index(end, s)]


def _live_hitl_branch(js: str) -> str:
    """sendMessage 串流迴圈裡的 hitl_question 分支（到 waiting 判斷為止）。"""
    return _seg(js, "if (data.type === 'hitl_question') {", "if (data.waiting)")


class TestClarifyPreservesPreamble:
    def test_card_renders_after_prior_content(self):
        js = JS.read_text(encoding="utf-8")
        live = _live_hitl_branch(js)
        assert "renderThinkingBlock(reasoningContent, false)" in live, "思考區塊要保留"
        assert "renderStoredBotMessage(fullContent, false)" in live, "前言內容要保留"
        assert "renderHitlQuestion(" in live, "前言要交給卡片渲染"
        fn = _seg(js, "function renderHitlQuestion(", "\n}\n")
        assert "botMsgDiv.innerHTML = (priorHtml || '') +" in fn, (
            "卡片接在前言之後，不是取代"
        )

    def test_cancel_stream_render_before_cards(self):
        """hitl_question 進場就要取消待處理渲染——否則 rAF 在卡片之後跑，
        整張卡被蓋回串流畫面。"""
        js = JS.read_text(encoding="utf-8")
        seg = _seg(js, "if (data.type === 'hitl_question') {", "renderHitlQuestion(")
        assert "cancelStreamRender()" in seg

    def test_clarify_card_still_has_options(self):
        js = JS.read_text(encoding="utf-8")
        # 選項按鈕的構建在保留前言注解之前——段要涵蓋整個 clarify fallback
        seg = _seg(
            js,
            "const options = _hitlContext.clarifyOptions;",
            "botMsgDiv.innerHTML = (priorHtml || '')",
        )
        assert "clarify-option-btn" in seg, "三選一按鈕不可回退"
        assert "data-clarify-answer" in seg
