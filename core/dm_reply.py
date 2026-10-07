"""私訊回覆引用的預覽（ORM 與舊 psycopg2 兩條讀取路徑共用）。

預覽只帶前 100 字：引用區塊只顯示一行，不把原訊息全文再送一次。
原訊息被收回時原文已清空（messages_repo.recall_message），這裡只標 recalled。
"""

from __future__ import annotations

from typing import Optional

from core.ai_card import CARD_TYPE, card_preview

REPLY_SNIPPET_LEN = 100


def build_reply_preview(
    message_id: Optional[int],
    from_user_id: Optional[str],
    display_name: Optional[str],
    username: Optional[str],
    content: Optional[str],
    message_type: Optional[str],
) -> Optional[dict]:
    if message_id is None:
        return None
    recalled = message_type == "recalled"
    if message_type == CARD_TYPE:
        snippet = card_preview(content, REPLY_SNIPPET_LEN)
    else:
        snippet = "" if recalled else (content or "")[:REPLY_SNIPPET_LEN]
    return {
        "id": message_id,
        "from_user_id": from_user_id,
        # 暱稱優先，沒設才用帳號名（同 #946 的顯示規則）
        "from_display_name": display_name or username or from_user_id,
        "snippet": snippet,
        "recalled": recalled,
    }
