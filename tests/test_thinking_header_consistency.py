"""思考區塊標頭格式一致性的接線回歸（2026-09-07「之前是字數、完成後
顯示不太一樣」）。

renderThinkingBlock 的字數後綴原本只在 isStreaming 時顯示——串流中是
「思考過程 · N 字」、完成後變成只剩「思考過程」，使用者看到兩種長相。
修法：字數常駐，串流與完成態只差 spinner→腦圖示。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
JS = REPO / "web" / "js" / "chat-analysis.js"


def test_char_suffix_always_shown():
    js = JS.read_text(encoding="utf-8")
    seg = js[js.index("function renderThinkingBlock") : js.index("function toggleThinkingState")]
    assert "isStreaming" not in js[seg.index("const suffix") : seg.index("const icon")], (
        "字數後綴不得以 isStreaming 條件化——完成態也要顯示"
    )
    assert "charCount" in seg.split("const suffix")[1].split(";")[0]
    # spinner/腦圖示的切換仍然依 isStreaming（這是唯一該差的的地方）
    assert 'isStreaming' in seg.split("const icon")[1].split(";")[0]
