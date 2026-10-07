"""思考串流的端到端契約：後端事件型別 ↔ 前端處理。

這條路徑上有一個很容易犯、而且不會噴錯的錯：前端主串流迴圈的收尾是
``if (data.content) { fullContent += data.content }``——**任何**帶 content 的
幀都會被累加進答案。思考幀也帶 content，所以只要 reasoning 分支沒有擋在它
前面（或漏了 continue），模型的自言自語就會被直接寫進回覆正文，而且測試、
lint、CI 全綠。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHAT_JS = ROOT / "web/js/chat-analysis.js"
ANALYSIS_PY = ROOT / "api/routers/analysis.py"
CLAW_PY = ROOT / "core/agents/manager/claw_loop.py"
AGENT_PY = ROOT / "core/agents/base_react_agent.py"


@pytest.fixture(scope="module")
def chat_js() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


# ── 後端：事件確實會被發出 ──────────────────────────────────────────────


def test_claw_loop_emits_reasoning_event():
    src = CLAW_PY.read_text(encoding="utf-8")
    assert 'type="reasoning"' in src
    assert "on_reasoning=_on_reasoning" in src, "回呼沒接上，事件永遠不會發"


def test_reasoning_uses_its_own_sanitizer_buffer():
    """跟正文共用 sanitizer 會把兩條流的半個 token 接在一起。"""
    src = CLAW_PY.read_text(encoding="utf-8")
    assert "reasoning_sanitizer" in src
    assert "reasoning_sanitizer.feed" in src


def test_streaming_agent_forwards_reasoning_content():
    src = AGENT_PY.read_text(encoding="utf-8")
    assert "on_reasoning" in src
    assert "extract_reasoning" in src, "只傳「有動靜」不傳內容的話前端沒東西可顯示"


def test_every_token_branch_has_a_reasoning_sibling():
    """analysis.py 有三處 token 分支（worker／in-process 兩條）。

    漏掉任何一處，該條路徑的思考事件會掉進 else 變成 progress 幀，前端靜默丟棄。
    """
    src = ANALYSIS_PY.read_text(encoding="utf-8")
    token_branches = len(re.findall(r"""==\s*["']token["']""", src))
    reasoning_branches = len(re.findall(r"""==\s*["']reasoning["']""", src))
    assert token_branches >= 3
    assert reasoning_branches == token_branches, (
        f"token 分支 {token_branches} 處，reasoning 只有 {reasoning_branches} 處"
    )


def test_reasoning_is_not_accumulated_into_run_content():
    """run["content"] 是答案本身，被寫進去就會存進 DB 也回放給使用者。"""
    src = ANALYSIS_PY.read_text(encoding="utf-8")
    pattern = r"""elif\s+(?:data\.get\("type"\)|event_type)\s*==\s*["']reasoning["']"""
    found = 0
    for m in re.finditer(pattern, src):
        # 只看到下一個 elif/else 為止——再往後是別的分支，會誤判
        rest = src[m.end() : m.end() + 600]
        cut = min(
            (i for i in (rest.find("\n                        else:"),
                         rest.find("\n                            else:"),
                         rest.find("\n            else:")) if i != -1),
            default=len(rest),
        )
        # 只看程式碼：這裡的註解本身就寫著 run["content"]，不剝掉會誤判
        block = "\n".join(
            line.split("#", 1)[0] for line in rest[:cut].splitlines()
        )
        found += 1
        assert 'run["content"]' not in block, "reasoning 分支不該碰 run[\"content\"]"
    assert found >= 3, f"只找到 {found} 個 reasoning 分支"


# ── 前端：思考不能混進答案 ──────────────────────────────────────────────


def test_frontend_handles_reasoning_before_generic_content(chat_js: str):
    """reasoning 分支必須出現在 `if (data.content)` 之前，否則會被當答案累加。"""
    reasoning_at = chat_js.find("data.type === 'reasoning'")
    generic_at = chat_js.find("if (data.content) {")
    assert reasoning_at != -1, "前端沒有處理 reasoning 幀"
    assert generic_at != -1
    assert reasoning_at < generic_at, "reasoning 分支排在通用 content 分支之後 = 會混進答案"


def _strip_js_comments(src: str) -> str:
    """剝掉 // 與 /* */ ——不剝的話註解掉的程式碼仍會滿足守衛。

    實測踩過：把 ``continue;`` 改成 ``/*continue;*/`` 這個守衛照樣綠。
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(line.split("//", 1)[0] for line in src.splitlines())


def test_reasoning_branch_short_circuits(chat_js: str):
    """分支裡沒有 continue，執行會繼續往下掉到 data.content 被當成答案累加。"""
    start = chat_js.find("data.type === 'reasoning'")
    block = _strip_js_comments(chat_js[start : start + 320])
    assert "continue;" in block


def test_reasoning_never_appended_to_full_content(chat_js: str):
    start = chat_js.find("data.type === 'reasoning'")
    block = _strip_js_comments(chat_js[start : start + 320])
    assert "fullContent" not in block


def test_thinking_block_escapes_model_output(chat_js: str):
    """思考內容是模型自由生成的文字，直接插 innerHTML 等於開 XSS。"""
    start = chat_js.find("function renderThinkingBlock")
    assert start != -1
    block = _strip_js_comments(
        chat_js[start : chat_js.find("function toggleThinkingState")]
    )
    assert "escapeHtml(reasoningText)" in block


def test_thinking_block_defaults_to_collapsed(chat_js: str):
    """思考通常又長又是自言自語，預設攤開會把答案擠到看不見。"""
    start = chat_js.find("function renderThinkingBlock")
    block = chat_js[start : chat_js.find("function toggleThinkingState")]
    assert "AppStore.get('lastThinkingOpenState') === true" in block, (
        "應該是「明確存過 true 才展開」，不是 truthy 判斷"
    )


def test_thinking_block_survives_stream_rerender(chat_js: str):
    """renderStoredBotMessage 會整個換掉 innerHTML，只插一次會被洗掉。"""
    start = chat_js.find("const flushStreamRender")
    block = chat_js[start : start + 700]
    assert "renderThinkingBlock" in block


def test_toggle_is_registered_in_click_delegator():
    """嚴格 CSP 下沒進白名單的 data-click 會靜默失效（點了沒反應）。"""
    src = (ROOT / "web/js/click-delegator.js").read_text(encoding="utf-8")
    assert "'toggleThinkingState'" in src


def test_i18n_keys_exist_in_every_locale():
    import json

    for lang in ("zh-TW", "zh-CN", "en", "ru"):
        data = json.loads((ROOT / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8"))
        flat = json.dumps(data, ensure_ascii=False)
        assert '"thinkingProcess"' in flat, f"{lang} 缺 thinkingProcess"
        assert '"thinkingChars"' in flat, f"{lang} 缺 thinkingChars"


def test_thinking_styles_exist():
    css = (ROOT / "web/styles.css").read_text(encoding="utf-8")
    for cls in (".thinking-container", ".thinking-body", ".thinking-chevron"):
        assert cls in css, f"缺 {cls} 樣式"
