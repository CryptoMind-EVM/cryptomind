"""E2E guards: 鍵盤從開到收時，串流內容要重新貼底（防Thinking...被蓋住）。

問題場景：手機上使用者送出訊息 → 鍵盤開著 → Thinking... 進度列插入並貼底
→ input.blur() 觸發 focusout → chat-state.js 80ms 後呼叫 syncMobileChatViewport
→ --chat-keyboard-offset 歸零、chat-keyboard-open class 移除 → 輸入框從貼齊鍵盤
上緣掉回視窗底部。這個「輸入框瞬間下移數百像素」的過程中，剛插入的進度列若沒有
跟著重新貼底，就會被輸入框蓋住 —— 使用者送出後只看到輸入框，看不到正在思考。

桌面瀏覽器無法重現（visualViewport 永遠等於 viewport、沒有鍵盤佔用），所以這個
守衛用靜態接線檢查：確認 syncMobileChatViewport 在「從 keyboard-open 收回」的
分支有主動觸發 reanchor（呼叫 stickChatToBottom 或等價機制）。否則只靠 ui-shell
的 ResizeObserver 接手，在實機漸進動畫期間會有時序競態漏接。
"""

from __future__ import annotations

import re
from pathlib import Path

WEB_JS = Path(__file__).resolve().parents[2] / "web" / "js"


def test_sync_mobile_chat_viewport_reanchors_on_keyboard_close():
    """接線檢查：syncMobileChatViewport 收鍵盤（offset 歸零）時要觸發 reanchor。

    這是問題 1 「送出當下 Thinking... 被蓋住」的根因兜底：鍵盤收的瞬間輸入框
    下移，必須主動把捲動區重新貼底。只靠 ui-shell ResizeObserver 在實機漸進
    鍵盤動畫期間會漏接。
    """
    source = (WEB_JS / "chat-state.js").read_text(encoding="utf-8")

    # 抓出 syncMobileChatViewport 函式本體
    match = re.search(
        r"function\s+syncMobileChatViewport\s*\([^)]*\)\s*\{",
        source,
    )
    assert match, "找不到 syncMobileChatViewport —— 測試需要更新"

    # 從 match 的 { 開始往後抓到對應的 }（用括號配對）
    start = source.index("{", match.end() - 1)
    depth = 0
    end = start
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    body = source[start:end]

    # 找到「鍵盤收」的 else 分支（offset 歸零、移除 class）
    has_close_branch = (
        "removeProperty" in body
        or ("--chat-keyboard-offset" in body and "'0px'" in body)
        or "classList.remove('chat-keyboard-open')" in body
    )
    assert has_close_branch, "找不到鍵盤收回（offset 歸零）的邏輯分支 —— 測試需要更新"

    # 這個分支必須觸發 reanchor：呼叫 stickChatToBottom 或呼叫 UIShell.syncLayout，
    # 或發出某個會讓 chat-messages 捲到底的事件
    reanchor_patterns = [
        r"stickChatToBottom\s*\(",
        r"resetChatStickToBottom\s*\(",
        r"UIShell\s*\.\s*syncLayout\s*\(",
        r"window\s*\.\s*stickChatToBottom\s*\(",
    ]
    found = any(re.search(p, body) for p in reanchor_patterns)
    assert found, (
        "syncMobileChatViewport 的鍵盤收路徑沒有觸發 reanchor："
        "實機上鍵盤收起瞬間輸入框下移數百像素，串流的 Thinking... 進度列"
        "會被蓋住（送出當下看不到正在思考）"
    )
