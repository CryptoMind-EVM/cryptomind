"""Bot（Telegram／LINE）端的 HITL 橋：interrupt payload → 純文字＋按鈕 → resume 答案。

背景（2026-09-14）：網頁版的同意卡（記帳／記憶／skill／多卡／釐清／loop_fork）
靠 SSE ``hitl_question`` 事件＋前端卡片；bot 路徑 ``run_bot_chat`` 只讀
``final_response``，interrupt 一發生就回「（無法產生回覆）」——在 Telegram
打「午餐 180」永遠記不進帳本。這裡把 interrupt payload 消化成 bot 能用的
形狀，兩個 bot 共用：

- :func:`render_hitl` → ``{"type", "text", "buttons": [{label, decision, index?}]}``
- :func:`build_resume_answer` → 送進 ``Command(resume=...)`` 的物件，形狀對齊
  各 parser（consent_gate.parse_consent_answer／parse_skill_memory_consent_answer／
  claw_loop.parse_multi_consent_answers／clarify 純字串／loop_fork 純字串）。
- :func:`text_decision` → 使用者直接打「好／不要」時的文字 fallback（LINE 沒
  inline 按鈕；Telegram 使用者也可能直接回字）。

decision 值：``approve`` / ``deny`` / ``option``（帶 index）/ ``text``（帶 text）/
``wrap``（loop_fork 收尾）。fail-closed：認不得的一律當 deny。
"""

from __future__ import annotations

from typing import Any, Optional

# 同意類 payload type（一顆同意、一顆取消）
_CONSENT_TYPES = {
    "consent_gate",
    "journal_consent",
    "skill_create_consent",
    "memory_consent",
    "multi_consent",
}

_T = {
    "approve": {
        "zh-TW": "✅ 同意",
        "zh-CN": "✅ 同意",
        "en": "✅ Approve",
        "ru": "✅ Да",
    },
    "deny": {"zh-TW": "❌ 取消", "zh-CN": "❌ 取消", "en": "❌ Cancel", "ru": "❌ Нет"},
    "approve_all": {
        "zh-TW": "✅ 全部同意",
        "zh-CN": "✅ 全部同意",
        "en": "✅ Approve all",
        "ru": "✅ Всё одобрить",
    },
    "deny_all": {
        "zh-TW": "❌ 全部取消",
        "zh-CN": "❌ 全部取消",
        "en": "❌ Cancel all",
        "ru": "❌ Всё отменить",
    },
    "wrap": {
        "zh-TW": "🏁 先收尾",
        "zh-CN": "🏁 先收尾",
        "en": "🏁 Wrap up",
        "ru": "🏁 Завершить",
    },
    "record": {
        "zh-TW": "✅ 記下來",
        "zh-CN": "✅ 记下来",
        "en": "✅ Save it",
        "ru": "✅ Записать",
    },
    "skip": {
        "zh-TW": "❌ 不用",
        "zh-CN": "❌ 不用",
        "en": "❌ Skip",
        "ru": "❌ Не надо",
    },
    "tools": {"zh-TW": "工具", "zh-CN": "工具", "en": "Tools", "ru": "Инструменты"},
    "category": {"zh-TW": "分類", "zh-CN": "分类", "en": "Category", "ru": "Категория"},
    "note": {"zh-TW": "備註", "zh-CN": "备注", "en": "Note", "ru": "Заметка"},
    "memory": {"zh-TW": "記憶", "zh-CN": "记忆", "en": "Memory", "ru": "Память"},
    "skill": {"zh-TW": "技能", "zh-CN": "技能", "en": "Skill", "ru": "Навык"},
    "reply_hint": {
        "zh-TW": "也可以直接回「好」或「不要」。",
        "zh-CN": "也可以直接回「好」或「不要」。",
        "en": "You can also just reply “yes” or “no”.",
        "ru": "Можно просто ответить «да» или «нет».",
    },
    "type_hint": {
        "zh-TW": "直接回覆文字即可。",
        "zh-CN": "直接回复文字即可。",
        "en": "Just reply with text.",
        "ru": "Просто ответьте текстом.",
    },
    "fork_steps": {
        "zh-TW": "已跑 {n} 步。回覆補充提示讓它繼續，或先收尾。",
        "zh-CN": "已跑 {n} 步。回复补充提示让它继续，或先收尾。",
        "en": "{n} steps so far. Reply with a hint to continue, or wrap up.",
        "ru": "Пройдено {n} шагов. Ответьте подсказкой, чтобы продолжить, или завершите.",
    },
}

_YES_WORDS = {
    "yes",
    "y",
    "ok",
    "okay",
    "sure",
    "approve",
    "好",
    "好的",
    "好啊",
    "同意",
    "確認",
    "确认",
    "記",
    "记",
    "可以",
    "是",
    "對",
    "对",
    "да",
    "ок",
    "хорошо",
}
_NO_WORDS = {
    "no",
    "n",
    "cancel",
    "deny",
    "skip",
    "不要",
    "不用",
    "取消",
    "否",
    "不",
    "算了",
    "不記",
    "不记",
    "нет",
    "отмена",
}


def _t(key: str, language: str) -> str:
    table = _T[key]
    return table.get(language) or table["en"]


def _fmt(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v or "")
    return f"{f:,.0f}" if f == int(f) else f"{f:,.2f}"


def _journal_lines(p: dict, language: str) -> list[str]:
    from core.agents.manager.consent_gate import _JOURNAL_ENTRY_LABELS

    entry_type = p.get("entry_type", "expense")
    label = _JOURNAL_ENTRY_LABELS.get(entry_type, _JOURNAL_ENTRY_LABELS["expense"])
    head = f"{label.get(language, label['en'])} · {_fmt(p.get('amount'))} {p.get('currency', '')}"
    if entry_type == "trade" and p.get("symbol"):
        head += f" · {p['symbol']} × {_fmt(p.get('quantity'))} ({p.get('side', 'buy')})"
    lines = [head]
    if p.get("category"):
        lines.append(f"{_t('category', language)}: {p['category']}")
    if p.get("note"):
        lines.append(f"{_t('note', language)}: {p['note']}")
    if p.get("converted_amount") and p.get("base_currency") not in (
        None,
        p.get("currency"),
    ):
        lines.append(f"≈ {_fmt(p['converted_amount'])} {p.get('base_currency', '')}")
    return lines


def render_hitl(payload: Any, language: str = "zh-TW") -> dict:
    """把 interrupt payload 轉成 ``{"type", "text", "buttons"}``。

    不認得的 type 也給一個能回答的形狀（同意／取消），絕不讓 bot 端沒東西可送。
    """
    p = payload if isinstance(payload, dict) else {}
    ptype = str(p.get("type") or "clarify")
    message = str(p.get("message") or p.get("question") or "").strip()
    lines: list[str] = [message] if message else []
    buttons: list[dict] = []

    if ptype == "journal_consent":
        lines += _journal_lines(p, language)
        buttons = [
            {"label": _t("record", language), "decision": "approve"},
            {"label": _t("skip", language), "decision": "deny"},
        ]
    elif ptype == "multi_consent":
        for card in p.get("cards") or []:
            if not isinstance(card, dict):
                continue
            lines.append(f"{card.get('icon', '•')} {card.get('title', '')}")
            lines += [f"  {ln}" for ln in card.get("lines") or []]
        buttons = [
            {"label": _t("approve_all", language), "decision": "approve"},
            {"label": _t("deny_all", language), "decision": "deny"},
        ]
    elif ptype == "consent_gate":
        names = [
            str(t.get("display_name") or t.get("name") or "")
            for t in p.get("tools") or []
            if isinstance(t, dict)
        ]
        if names:
            lines.append(
                f"{_t('tools', language)}: " + ", ".join(n for n in names if n)
            )
        buttons = [
            {"label": _t("approve", language), "decision": "approve"},
            {"label": _t("deny", language), "decision": "deny"},
        ]
    elif ptype == "memory_consent":
        lines.append(
            f"{_t('memory', language)}: [{p.get('category', 'fact')}] {p.get('content', '')}"
        )
        buttons = [
            {"label": _t("approve", language), "decision": "approve"},
            {"label": _t("deny", language), "decision": "deny"},
        ]
    elif ptype == "skill_create_consent":
        lines.append(
            f"{_t('skill', language)}: {p.get('mode', 'create')} {p.get('skill_name', '')}"
        )
        if p.get("description"):
            lines.append(str(p["description"]))
        buttons = [
            {"label": _t("approve", language), "decision": "approve"},
            {"label": _t("deny", language), "decision": "deny"},
        ]
    elif ptype == "loop_fork":
        lines.append(_t("fork_steps", language).format(n=p.get("steps", "?")))
        buttons = [{"label": _t("wrap", language), "decision": "wrap"}]
    else:  # clarify（含未知 type）：選項按鈕＋自由文字
        options = [o for o in (p.get("options") or []) if isinstance(o, dict)]
        for i, o in enumerate(options):
            label = str(o.get("label") or o.get("hint") or "").strip()
            if label:
                buttons.append({"label": label, "decision": "option", "index": i})
        lines.append(_t("type_hint", language))

    if ptype in _CONSENT_TYPES:
        lines.append(_t("reply_hint", language))
    return {
        "type": ptype,
        "text": "\n".join(ln for ln in lines if ln),
        "buttons": buttons,
    }


def build_resume_answer(
    payload: Any,
    decision: str,
    *,
    option_index: Optional[int] = None,
    text: Optional[str] = None,
) -> Any:
    """依 pending payload 的 type 把 bot 端 decision 轉成各 parser 認得的答案。"""
    p = payload if isinstance(payload, dict) else {}
    ptype = str(p.get("type") or "clarify")
    approved = decision == "approve"

    if ptype == "multi_consent":
        n = max(1, len(p.get("cards") or []))
        return [{"action": "approve" if approved else "deny"} for _ in range(n)]
    if ptype in _CONSENT_TYPES:
        # parse_consent_answer 看 action=consent；parse_skill_memory_consent_answer
        # 看 approved——兩個鍵都給，兩邊都吃得下。
        return {"action": "consent", "approved": approved}
    if ptype == "loop_fork":
        # 空字串＝收尾；非空＝補充提示繼續跑
        return "" if decision in ("wrap", "deny", "approve") else str(text or "")
    # clarify：選項送 hint（網頁 chat-hitl.js 同樣送 hint），自由文字原樣送
    if decision == "option" and option_index is not None:
        options = [o for o in (p.get("options") or []) if isinstance(o, dict)]
        if 0 <= option_index < len(options):
            o = options[option_index]
            return str(o.get("hint") or o.get("label") or "")
        return ""
    return str(text or "")


def text_decision(payload: Any, text: str) -> Optional[dict]:
    """使用者對 pending interrupt 直接打字時，判斷這句是不是在回答它。

    回傳 ``{"decision": ..., "option_index"?, "text"?}``；回 None 表示
    「這是新問題，不是在回卡」——呼叫端照一般聊天處理。
    """
    p = payload if isinstance(payload, dict) else {}
    ptype = str(p.get("type") or "clarify")
    norm = (text or "").strip().rstrip("。.!！").lower()
    if not norm:
        return None
    if ptype in _CONSENT_TYPES:
        if norm in _YES_WORDS:
            return {"decision": "approve"}
        if norm in _NO_WORDS:
            return {"decision": "deny"}
        return None
    if ptype == "loop_fork":
        if norm in _NO_WORDS or norm in _YES_WORDS:
            return {"decision": "wrap"}
        return {"decision": "text", "text": text.strip()}
    # clarify：純數字選第幾個選項；其他文字就是答案
    options = [o for o in (p.get("options") or []) if isinstance(o, dict)]
    if norm.isdigit() and options and 1 <= int(norm) <= len(options):
        return {"decision": "option", "option_index": int(norm) - 1}
    return {"decision": "text", "text": text.strip()}
