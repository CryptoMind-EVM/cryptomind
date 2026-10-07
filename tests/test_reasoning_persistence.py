"""思考過程存檔／還原的回歸測試（2026-09-07 DANNY 回報）。

回報：「思考過程一開始會顯示但是 F5 重置網頁後就會不見」。

成因不是渲染壞掉，是根本沒存：串流的 ``type: "reasoning"`` 事件只累積在
前端變數裡，``save_chat_message`` 只寫 role/content/metadata，reasoning
從來沒進過 DB——歷史從 DB 重建時必然是空的。

修法：兩條產生路徑（in-process SSE 與 worker）都把 reasoning 累積起來，
截斷後存進 ``metadata.reasoning``；``chat-history.js`` 重播時用與串流同一個
``renderThinkingBlock`` 補回思考區塊。

本測試守住：截斷函式的邊界、兩條路徑的接線、前端還原的接線。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.database.chat import MAX_STORED_REASONING_CHARS, clip_reasoning

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
ANALYSIS = REPO / "api" / "routers" / "analysis.py"
WORKER = REPO / "scripts" / "analysis_worker.py"
CHAT_HISTORY = REPO / "web" / "js" / "chat-history.js"
CHAT_ANALYSIS = REPO / "web" / "js" / "chat-analysis.js"


class TestClipReasoning:
    """截斷邊界：空值不留鍵、超長要截、截斷標記不綁語言。"""

    @pytest.mark.parametrize("empty", [None, "", "   ", "\n\n"])
    def test_empty_becomes_none(self, empty):
        # metadata 不該多一個空的 reasoning 鍵，否則前端會渲染空的思考區塊
        assert clip_reasoning(empty) is None

    def test_short_text_passes_through_stripped(self):
        assert clip_reasoning("  想了一下  ") == "想了一下"

    def test_long_text_is_truncated_with_marker(self):
        clipped = clip_reasoning("字" * (MAX_STORED_REASONING_CHARS + 500))
        assert clipped is not None
        assert clipped.startswith("字" * 10)
        assert clipped.endswith("[…]")
        # 截斷後長度 = 上限 + 標記，不得整段塞進 metadata
        assert len(clipped) == MAX_STORED_REASONING_CHARS + len("\n\n[…]")

    def test_exact_limit_is_not_truncated(self):
        text = "字" * MAX_STORED_REASONING_CHARS
        assert clip_reasoning(text) == text

    def test_marker_is_language_neutral(self):
        # 同一段歷史會被四種語系的使用者讀到——截斷標記不能綁死中文
        clipped = clip_reasoning("x" * (MAX_STORED_REASONING_CHARS + 1))
        assert not any("一" <= ch <= "鿿" for ch in clipped[-10:])


class TestBackendWiring:
    """兩條產生路徑都要累積 reasoning 並存進 metadata。"""

    def test_in_process_stream_accumulates_and_saves(self):
        src = ANALYSIS.read_text(encoding="utf-8")
        assert "reasoning_parts" in src, "in-process 串流要累積 reasoning"
        assert "reasoning_parts.append(chunk)" in src
        assert 'clip_reasoning("".join(reasoning_parts))' in src
        assert '_meta["reasoning"] = _reasoning' in src

    def test_worker_accumulates_and_saves(self):
        src = WORKER.read_text(encoding="utf-8")
        assert "accumulated_reasoning" in src, "worker 模式要累積 reasoning"
        assert "accumulated_reasoning.append(chunk)" in src
        assert 'clip_reasoning("".join(accumulated_reasoning))' in src
        assert 'meta["reasoning"] = reasoning' in src

    def test_reasoning_never_merged_into_answer(self):
        # 思考內容併進 content 就會變成答案的一部分印在對話裡
        for path in (ANALYSIS, WORKER):
            src = path.read_text(encoding="utf-8")
            assert 'accumulated_content["value"] += chunk' not in src.split(
                "elif event_type == \"reasoning\""
            )[-1].split("publish_event")[0], f"{path.name}: reasoning 不得併進 content"


class TestFrontendWiring:
    """歷史重播要用與串流同一個 renderThinkingBlock 還原思考區塊。"""

    def test_render_thinking_block_is_shared(self):
        src = CHAT_ANALYSIS.read_text(encoding="utf-8")
        assert "window.renderThinkingBlock = renderThinkingBlock;" in src

    def test_history_restores_thinking_block(self):
        src = CHAT_HISTORY.read_text(encoding="utf-8")
        assert "msg.metadata.reasoning" in src
        assert "window.renderThinkingBlock(msg.metadata.reasoning, false)" in src
        # 位置要與串流一致（答案最前面）
        assert "'afterbegin'" in src
