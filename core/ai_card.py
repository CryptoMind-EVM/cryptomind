"""AI 分析卡片（message_type = 'ai_card'，2026-10-04 DANNY）。

聊天室 AI 助理的回答「分享到聊天室」不再塞進輸入框（受單則 500 字限制、表格被壓成文字），
而是送出一則卡片訊息：內容是 AI 回答的 markdown 原文，各個聊天介面自己渲染成卡片。

- 內容一律由伺服器依 turn id 從 chat_assistant_history 取（呼叫端傳不進任何文字），
  所以卡片一定是「這個人真的問過 AI 得到的回答」，不能拿來偽造「AI 說的」。
- 長度上限 CARD_MAX_LENGTH（回答本身受 ANSWER_MAX_TOKENS 限制，這裡只是保險），
  不受 limit_message_max_length（一般訊息 500 字）管。
- 列表預覽、通知、回覆引用都只拿 card_preview 的一行，不把整篇送出去。
"""

from __future__ import annotations

import re

CARD_TYPE = "ai_card"
CARD_MAX_LENGTH = 6000

_BR = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_LEAD = re.compile(r"^\s*(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)+")
_MARKS = re.compile(r"[*_`~|]+")


def card_preview(content: str | None, limit: int = 50) -> str:
    """卡片 → 一行預覽（列表、通知、回覆引用用）：去掉 HTML／markdown 符號，取第一行有字的。
    加 ✨ 開頭讓人一眼看出是 AI 分析（不放文字：伺服器端沒有使用者語言）。"""
    for raw in _BR.sub("\n", content or "").split("\n"):
        if _TABLE_RULE.match(raw):
            continue
        line = _MARKS.sub(" ", _LEAD.sub("", _TAG.sub("", raw)))
        line = " ".join(line.split())
        if line:
            break
    else:
        line = ""
    text = f"✨ {line}".rstrip()
    return text[: limit - 1] + "…" if len(text) > limit else text
