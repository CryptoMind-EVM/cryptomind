"""串流關閉 fallback 的接線回歸（2026-09-07「回答完思考還一直轉圈」）。

根因：SSE 串流正常關閉但沒收到 done 幀（proxy／提早斷線）時，讀取迴圈
安靜退出——done 路徑的最終渲染（思考轉圈→靜止腦圖示、串流樣式收尾）
沒有任何人執行，最後一次串流渲染永久殘留：答案看似完整、思考標頭的
spinner 卻轉不停。

修法：迴圈退出後若 isAnalyzing 仍為 true（done/error 幀都會翻成
false，所以只攔「漏接 done」），用與 done 路徑相同的收尾渲染。

本檔守住 fallback 不被移除、且它與 done 路徑的核心收尾對齊。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
JS = REPO / "web" / "js" / "chat-analysis.js"


def _seg(js: str, start_marker: str, end_marker: str) -> str:
    s = js.index(start_marker)
    return js[s : js.index(end_marker, s)]


class TestStreamCloseFallback:
    def test_fallback_exists_and_gated_by_is_analyzing(self):
        js = JS.read_text(encoding="utf-8")
        seg = _seg(js, "Stream-close fallback", "} catch (err) {")
        # 閘門看「發起的那個對話」的旗標，不是 isAnalyzing（＝目前對話）；
        # 且 HITL 暫停不算漏接 done——否則重繪會把剛畫好的同意卡蓋掉（2026-09-25）
        assert "if (!hitlPaused && isSessionAnalyzing(sessionIdAtStart))" in seg, (
            "閘門不可移除——否則會與 done 路徑重複渲染、或蓋掉 HITL 卡"
        )
        assert "renderStoredBotMessage(fullContent, false," in seg, "要以非串流態做最終渲染"
        assert "renderThinkingBlock(reasoningContent, false)" in seg, (
            "思考區塊必須以非串流態重渲染——這正是轉圈殘留的修復本體"
        )
        assert "cancelStreamRender()" in seg, "要先取消待處理的 rAF，避免被舊渲染覆蓋"

    def test_done_path_still_renders_thinking_static(self):
        js = JS.read_text(encoding="utf-8")
        done_seg = _seg(js, "if (data.done) {", "if (data.error) {")
        assert "renderThinkingBlock(reasoningContent, false)" in done_seg
