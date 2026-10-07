"""
Consent Gate — Policy Gate 支柱的核心邏輯（可信 AI 黑客松）

在 agent 真正執行前，掃描可用工具池（零 LLM、純同步），若有 high-risk tool 則：
1. 評估使用者身分信任度（Identity Trust Layer）。
2. 若信任度不足 → 要求 explicit consent（透過既有 HITL interrupt 機制）。
3. 同意/拒絕記進 audit log（事後追溯）。

設計取捨（見 docs/TRUST_DESIGN.md）：
- 用 `_filter_tool_metas` 的 deterministic 工具池，不跑額外「規劃 LLM」
  （違反 CLAW 單迴圈哲學，且免費 tier 對 429 敏感）。
- consent 對象是「可用工具池裡的 high-risk tool」，不是「LLM 預測會用的」。
  對「高風險動作要事前同意」訴求，這是更安全的方向（更早暴露意圖）。
- 接活既有 HITL plumbing（analysis.py 的 __interrupt__ 接收 + 前端 modal），
  不是新造管線。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from api.utils import logger
from core.agents.tool_registry import ToolMetadata
from core.audit import audit_log
from core.identity import (
    IdentityTrustAssessment,
    assess_identity_trust,
)


def scan_high_risk_tools(tool_metas: List[ToolMetadata]) -> List[ToolMetadata]:
    """從可用工具池挑出 high-risk tool。"""
    return [m for m in tool_metas if getattr(m, "risk_level", "low") == "high"]


def assess_user_trust(
    *,
    wallet_verified: bool,
    wallet_first_active: Optional[Any] = None,
    wallet_tx_count: Optional[int] = None,
    verified_stamps: Optional[Dict[str, bool]] = None,
) -> IdentityTrustAssessment:
    """評估使用者身分信任度（包裝 identity.trust，便於測試與未來擴充）。"""
    return assess_identity_trust(
        wallet_verified=wallet_verified,
        wallet_first_active=wallet_first_active,
        wallet_tx_count=wallet_tx_count,
        verified_stamps=verified_stamps,
    )


def should_require_consent(
    high_risk_tools: List[ToolMetadata],
    assessment: IdentityTrustAssessment,
    already_granted: bool,
) -> bool:
    """決定是否需要彈出 consent。

    需要Consent的條件：
    1. 有 high-risk tool 在池中，且
    2. 本次 session 尚未對該高風險範圍同意過（already_granted=False）。

    身分信任分數只作 audit/risk context，不能取代使用者對高風險 action 的明確授權。
    """
    if not high_risk_tools:
        return False
    if already_granted:
        return False
    return True


def build_consent_payload(
    high_risk_tools: List[ToolMetadata],
    assessment: IdentityTrustAssessment,
    language: str,
) -> Dict[str, Any]:
    """建構送給前端的 consent_gate interrupt payload。

    相容於既有前端 chat-hitl.js 的 research_summary 結構（renderPreResearchCard），
    並擴充 tools / trust 欄位供新的 renderConsentCard 使用。
    """
    tool_list = [
        {
            "name": m.name,
            "display_name": getattr(m, "description", m.name),
            "risk_level": "high",
        }
        for m in high_risk_tools
    ]
    # 多語系訊息（前端有對應 i18n key 時用前端，這裡給 fallback）
    msgs = {
        "zh-TW": "Agent 即將使用高風險工具，需要您的同意才能繼續。",
        "zh-CN": "Agent 即将使用高风险工具，需要您的同意才能继续。",
        "en": "The agent is about to use high-risk tools. Your consent is required to proceed.",
        "ru": "Агент собирается использовать инструменты повышенного риска. Требуется ваше согласие.",
    }
    return {
        "type": "consent_gate",
        "message": msgs.get(language, msgs["zh-TW"]),
        "tools": tool_list,
        "trust": assessment.to_audit_metadata(),
        "research_summary": msgs.get(language, msgs["zh-TW"]),
    }


def parse_consent_answer(answer: Any) -> Dict[str, Any]:
    """解析前端送回的 consent 回應。

    前端送 {action: 'consent', approved: true/false}（見 chat-hitl.js submitHITLAnswer）。
    相容於純字串 'yes'/'no' 與既有 {action:'execute'/'cancel'} 格式。
    """
    if isinstance(answer, dict):
        if answer.get("action") == "consent":
            return {"approved": bool(answer.get("approved")), "raw": answer}
        if answer.get("action") == "cancel":
            return {"approved": False, "raw": answer}
        if answer.get("action") == "execute":
            return {"approved": True, "raw": answer}
        if "approved" in answer:
            return {"approved": bool(answer["approved"]), "raw": answer}
    if isinstance(answer, str):
        normalized = answer.strip().lower()
        if normalized in {"yes", "y", "ok", "同意", "同意。", "好", "好的"}:
            return {"approved": True, "raw": answer}
        if normalized in {"no", "n", "cancel", "否", "不要", "取消"}:
            return {"approved": False, "raw": answer}
    # 預設：無法判讀視為拒絕（fail-closed，高風險動作不該被模糊同意）
    return {"approved": False, "raw": answer}


def log_consent_decision(
    *,
    user_id: Optional[str],
    username: Optional[str],
    high_risk_tools: List[ToolMetadata],
    assessment: IdentityTrustAssessment,
    approved: bool,
) -> None:
    """把 consent 決定記進 audit log（Principal + Audit Log 支柱）。"""
    tool_names = ",".join(m.name for m in high_risk_tools)
    audit_log(
        "consent_high_risk_action",
        user_id=user_id,
        username=username,
        resource_type="tool",
        resource_id=tool_names or "high_risk_tools",
        metadata={
            "consent": {
                "granted": approved,
                "tools": tool_names.split(",") if tool_names else [],
                "trust": assessment.to_audit_metadata(),
            }
        },
        endpoint="agent://consent_gate",
        success=approved,
    )
    logger.info(
        "[ConsentGate] user=%s consent=%s for high-risk tools=[%s] trust_score=%d",
        user_id,
        approved,
        tool_names,
        assessment.trust_score,
    )


def log_high_risk_tool_execution(
    *,
    user_id: Optional[str],
    tool_name: str,
    assessment: Optional[IdentityTrustAssessment] = None,
    consent_granted: bool = True,
) -> None:
    """high-risk tool 實際被呼叫時記 audit（事後追溯防線，由 wrap_tool 呼叫）。"""
    audit_log(
        "high_risk_tool_executed",
        user_id=user_id,
        resource_type="tool",
        resource_id=tool_name,
        metadata={
            "consent_granted": consent_granted,
            "trust": assessment.to_audit_metadata() if assessment else None,
        },
        endpoint="agent://high_risk_tool",
    )


async def append_guard_audit_receipt(
    *,
    user_id: Optional[str],
    event_type: str,
    payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Write the same control event to the tamper-evident Guard receipt chain."""
    if not user_id:
        return None
    from core.orm.action_guard_repo import action_guard_repo

    return await action_guard_repo.append_internal_receipt(
        user_id=user_id,
        event_type=event_type,
        payload=payload,
    )


# ── Skill / Memory 自主管理 consent（docs/plans/2026-08-10-agent-self-managed-skills-memory-design.md）──
# 與 consent_gate（工具池 high-risk 預先同意）不同：這組是「工具執行後、寫入前」
# 的同意，由 claw_loop 偵測 __needs_consent__ marker 後觸發。兩條路獨立、互不影響。

_CONSENT_TYPE_MAP = {
    "create_skill": "skill_create_consent",
    "custom_skill": "skill_create_consent",  # propose_custom_skill 的 kind
    "create_memory": "memory_consent",
    "delete_memory": "memory_consent",  # remember mode=delete
    "journal_entry": "journal_consent",  # record_entry 的 kind（金流 HITL）
    "judgment_call": "journal_consent",  # record_call 的 kind（明確喊單，走 multi 卡）
}

_MSGS_SKILL = {
    "zh-TW": "Agent 提議{verb}一個個人分析方法，需要您同意才會儲存。",
    "zh-CN": "Agent 提议{verb}一个个人分析方法，需要您同意才会储存。",
    "en": "The agent proposes to {verb} a personal analysis method. Your approval is required to save it.",
    "ru": "Агент предлагает {verb} персональный метод анализа. Требуется ваше подтверждение.",
}
_VERB = {
    "create": {"zh-TW": "新建", "zh-CN": "新建", "en": "create", "ru": "создать"},
    "update": {"zh-TW": "修改", "zh-CN": "修改", "en": "update", "ru": "изменить"},
    "delete": {"zh-TW": "刪除", "zh-CN": "删除", "en": "delete", "ru": "удалить"},
}
_MSGS_MEMORY_CREATE = {
    "zh-TW": "Agent 提議記住一則資訊，需要您同意才會儲存。",
    "zh-CN": "Agent 提议记住一则信息，需要您同意才会储存。",
    "en": "The agent proposes to remember a piece of information. Your approval is required to save it.",
    "ru": "Агент предлагает запомнить информацию. Требуется ваше подтверждение.",
}

_MSGS_MEMORY_DELETE = {
    "zh-TW": "Agent 提議刪除一則記憶，需要您同意才會執行。",
    "zh-CN": "Agent 提议删除一则记忆，需要您同意才会执行。",
    "en": "The agent proposes to delete a memory. Your approval is required to proceed.",
    "ru": "Агент предлагает удалить воспоминание. Требуется ваше подтверждение.",
}

# 金流 HITL（統一帳本）：Propose → Confirm → Commit。
# 訊息帶 {entry} 佔位（支出/收入/投資交易），4 語由前端 i18n 呈現細節。
_MSGS_JOURNAL = {
    "zh-TW": "Agent 提議記一筆{entry}，確認後才會寫入帳本。",
    "zh-CN": "Agent 提议记一笔{entry}，确认后才会写入帐本。",
    "en": "The agent proposes to record {entry}. Nothing is saved until you confirm.",
    "ru": "Агент предлагает записать {entry}. Ничего не будет сохранено без вашего подтверждения.",
}
_JOURNAL_ENTRY_LABELS = {
    "expense": {"zh-TW": "支出", "zh-CN": "支出", "en": "an expense", "ru": "расход"},
    "income": {"zh-TW": "收入", "zh-CN": "收入", "en": "an income", "ru": "доход"},
    "trade": {
        "zh-TW": "投資交易",
        "zh-CN": "投资交易",
        "en": "an investment trade",
        "ru": "инвестиционную сделку",
    },
}


def build_journal_consent_payload(
    signal: Dict[str, Any], language: str
) -> Dict[str, Any]:
    """建構統一帳本記帳確認卡 payload（expense 紅 / income 綠 / trade 藍）。

    ``signal`` 是 record_entry 工具回傳的 ``__needs_consent__`` marker，
    含提案時凍結的匯率（exchange_rate/converted_amount）。
    """
    entry_type = signal.get("entry_type", "expense")
    labels = _JOURNAL_ENTRY_LABELS.get(entry_type, _JOURNAL_ENTRY_LABELS["expense"])
    label = labels.get(language, labels["en"])
    msg = _MSGS_JOURNAL.get(language, _MSGS_JOURNAL["en"]).format(entry=label)
    return {
        "type": "journal_consent",
        "message": msg,
        "entry_type": entry_type,
        "symbol": signal.get("symbol", ""),
        "market": signal.get("market", ""),
        "amount": signal.get("amount", 0),
        "currency": signal.get("currency", "TWD"),
        "quantity": signal.get("quantity", 1),
        "side": signal.get("side", "buy"),
        "instrument_type": signal.get("instrument_type", "spot"),
        "direction": signal.get("direction", "long"),
        "leverage": signal.get("leverage", 1),
        "category": signal.get("category", "other"),
        "fee": signal.get("fee", 0),
        "note": signal.get("note", ""),
        "exchange_rate": signal.get("exchange_rate"),
        "converted_amount": signal.get("converted_amount"),
        "base_currency": signal.get("base_currency", "TWD"),
    }


def build_skill_consent_payload(
    signal: Dict[str, Any], language: str
) -> Dict[str, Any]:
    """建構 skill create/update/delete 的同意卡 payload。

    ``signal`` 是工具回傳的 ``__needs_consent__`` marker dict。
    payload type 為 ``skill_create_consent``，前端 ``renderSkillConsentCard`` 處理。
    """
    mode = signal.get("mode", "create")
    verb = _VERB.get(mode, _VERB["create"]).get(language, _VERB[mode]["zh-TW"])
    msg = _MSGS_SKILL.get(language, _MSGS_SKILL["zh-TW"]).format(verb=verb)
    return {
        "type": "skill_create_consent",
        "mode": mode,
        "message": msg,
        "skill_name": signal.get("skill_name", ""),
        "description": signal.get("description", ""),
        "trigger_keywords": signal.get("trigger_keywords", ""),
        "body": signal.get("body", ""),
        "body_preview": signal.get("body_preview") or signal.get("body", ""),
        "reason": signal.get("reason", ""),
    }


# ── 多卡批次同意（multi_consent）───────────────────────────────────────────
# 一輪多個提案（記帳＋刪記憶＋增記憶…）只彈第一張卡的問題（2026-08-25）：
# 後端把全部 pending 提案包成一張 multi_consent 卡，前端逐一 ✅/❌ 後一次
# 送回答案陣列，graph 只 resume 一次（不必每張卡重跑一次 agent）。
# 每張子卡是自描述的顯示結構（kind/icon/title/lines/danger），前端零業務邏輯。

_MULTI_KIND_TITLES = {
    "journal_entry": {
        "zh-TW": "記一筆帳本記錄",
        "zh-CN": "记一笔帐本记录",
        "en": "Record a ledger entry",
        "ru": "Запись в журнал",
    },
    "journal_delete": {
        "zh-TW": "刪除帳本記錄",
        "zh-CN": "删除帐本记录",
        "en": "Delete a ledger entry",
        "ru": "Удалить запись журнала",
    },
    "journal_update": {
        "zh-TW": "修改帳本記錄",
        "zh-CN": "修改帐本记录",
        "en": "Edit a ledger entry",
        "ru": "Изменить запись журнала",
    },
    "create_memory": {
        "zh-TW": "新增記憶",
        "zh-CN": "新增记忆",
        "en": "Remember information",
        "ru": "Запомнить информацию",
    },
    "delete_memory": {
        "zh-TW": "刪除記憶",
        "zh-CN": "删除记忆",
        "en": "Forget a memory",
        "ru": "Удалить память",
    },
    "custom_skill": {
        "zh-TW": "自訂 Skill 異動",
        "zh-CN": "自定义 Skill 变更",
        "en": "Custom skill change",
        "ru": "Изменение skill",
    },
    "judgment_call": {
        "zh-TW": "記一筆判斷（到期評分）",
        "zh-CN": "记一笔判断（到期评分）",
        "en": "Record a market call (scored at expiry)",
        "ru": "Записать прогноз (оценка по сроку)",
    },
}

_CALL_DIRECTION = {
    "buy": {"zh-TW": "看多", "zh-CN": "看多", "en": "bullish", "ru": "рост"},
    "sell": {"zh-TW": "看空", "zh-CN": "看空", "en": "bearish", "ru": "падение"},
}
_CALL_HORIZON = {
    "zh-TW": "{d} 天後對答案",
    "zh-CN": "{d} 天后对答案",
    "en": "judged in {d} days",
    "ru": "оценка через {d} дн.",
}
_CALL_TARGET = {
    "zh-TW": "目標價 {p}",
    "zh-CN": "目标价 {p}",
    "en": "target {p}",
    "ru": "цель {p}",
}
_CALL_NOTICE = {
    "zh-TW": "進場價取最近收盤；24 小時後不能刪",
    "zh-CN": "进场价取最近收盘；24 小时后不能删",
    "en": "Entry = latest close; cannot be deleted after 24h",
    "ru": "Вход = последнее закрытие; через 24 ч удалить нельзя",
}

_MULTI_KIND_ICONS = {
    "journal_entry": "📝",
    "journal_delete": "🗑️",
    "journal_update": "✏️",
    "create_memory": "🧠",
    "delete_memory": "🧹",
    "custom_skill": "🛠️",
    "judgment_call": "🎯",
}

_MULTI_MESSAGES = {
    "zh-TW": "Agent 這輪提出了 {n} 個動作，請逐一確認（未核准的不會執行）：",
    "zh-CN": "Agent 这轮提出了 {n} 个动作，请逐一确认（未核准的不会执行）：",
    "en": "The agent proposes {n} actions this turn. Confirm each — unapproved ones won't run:",
    "ru": "Агент предлагает {n} действий. Подтвердите каждое — неподтверждённые не выполнятся:",
}


def _fmt_num(v: Any) -> str:
    try:
        f = float(v)
        return f"{f:g}"
    except (TypeError, ValueError):
        return str(v or "")


_MULTI_KIND_CARD_TYPES = {
    "journal_entry": "journal_consent",
    "journal_delete": "journal_consent",
    "journal_update": "journal_consent",
    "create_memory": "memory_consent",
    "delete_memory": "memory_consent",
    "custom_skill": "skill_create_consent",
    "judgment_call": "journal_consent",
}


def build_multi_consent_cards(signals: list, language: str) -> list:
    """把多個 pending consent marker 轉成自描述子卡陣列（給前端批次渲染）。

    內部 marker 欄位（__needs_consent__ / before 快照 key 結構等）在此消化，
    前端只拿到顯示用的 title / lines。
    """
    cards = []
    for idx, signal in enumerate(signals):
        kind = signal.get("kind", "")
        titles = _MULTI_KIND_TITLES.get(kind, _MULTI_KIND_TITLES["custom_skill"])
        lines = []

        if kind in ("journal_entry",):
            lines.append(
                f"{_JOURNAL_ENTRY_LABELS.get(signal.get('entry_type', 'expense'), _JOURNAL_ENTRY_LABELS['expense']).get(language, '')}"
                f" · {_fmt_num(signal.get('amount'))} {signal.get('currency', '')}"
            )
            if signal.get("entry_type") == "trade" and signal.get("symbol"):
                lines.append(
                    f"{signal.get('symbol', '')} × {_fmt_num(signal.get('quantity'))}"
                    f" ({signal.get('side', 'buy')}"
                    + (f" {signal.get('direction')}" if signal.get("direction") else "")
                    + (
                        f" {signal.get('leverage')}x"
                        if float(signal.get("leverage") or 1) > 1
                        else ""
                    )
                    + ")"
                )
            if signal.get("category"):
                lines.append(f"category: {signal['category']}")
            if signal.get("note"):
                lines.append(f"note: {signal['note']}")
            converted = signal.get("converted_amount")
            if converted:
                lines.append(
                    f"≈ {_fmt_num(converted)} {signal.get('base_currency', 'TWD')}"
                )
        elif kind in ("journal_delete", "journal_update"):
            before = (
                signal.get("before") if isinstance(signal.get("before"), dict) else {}
            )
            lines.append(
                f"#{signal.get('entry_id')} · {before.get('symbol', '')} · "
                f"{_fmt_num(before.get('amount'))} {before.get('currency', '')}"
                + (
                    f" × {_fmt_num(before.get('quantity'))}"
                    if before.get("quantity")
                    else ""
                )
            )
            if before.get("note"):
                lines.append(f"note: {before['note']}")
            if kind == "journal_update":
                updates = signal.get("updates") or {}
                lines.append(
                    " → ".join(f"{k}: {_fmt_num(v)}" for k, v in updates.items())
                )
        elif kind == "judgment_call":
            side = signal.get("side", "buy")
            lines.append(
                f"{signal.get('symbol', '')} · "
                f"{_CALL_DIRECTION.get(side, _CALL_DIRECTION['buy']).get(language, side)} · "
                f"{_CALL_HORIZON.get(language, _CALL_HORIZON['en']).format(d=signal.get('horizon_days', 30))}"
            )
            if signal.get("target_price"):
                lines.append(
                    _CALL_TARGET.get(language, _CALL_TARGET["en"]).format(
                        p=_fmt_num(signal.get("target_price"))
                    )
                )
            if signal.get("note"):
                lines.append(f"note: {signal['note']}")
            lines.append(_CALL_NOTICE.get(language, _CALL_NOTICE["en"]))
        elif kind == "delete_memory":
            before = (
                signal.get("before") if isinstance(signal.get("before"), dict) else {}
            )
            before_data = (
                before.get("data") if isinstance(before.get("data"), dict) else {}
            )
            content = before_data.get("value") or signal.get("content", "")
            lines.append(f"[{signal.get('key', '')}] {content}")
        elif kind == "create_memory":
            lines.append(
                f"[{signal.get('category', 'fact')}] {signal.get('content', '')}"
            )
        else:  # custom_skill 等
            lines.append(
                f"{signal.get('mode', 'create')}: {signal.get('skill_name', '')}"
            )
            if signal.get("description"):
                lines.append(signal["description"])

        cards.append(
            {
                "idx": idx,
                "kind": kind,
                "type": _MULTI_KIND_CARD_TYPES.get(kind, "skill_create_consent"),
                "icon": _MULTI_KIND_ICONS.get(kind, "🔧"),
                "title": titles.get(language, titles.get("en", "")),
                "lines": lines,
                "danger": kind in ("journal_delete", "delete_memory"),
            }
        )
    return cards


def build_multi_consent_message(n: int, language: str) -> str:
    msg = _MULTI_MESSAGES.get(language, _MULTI_MESSAGES["en"])
    return msg.format(n=n)


def build_memory_consent_payload(
    signal: Dict[str, Any], language: str
) -> Dict[str, Any]:
    """建構 memory 記憶的同意卡 payload（create 藍色 / delete 紅色警告）。"""
    is_delete = signal.get("kind") == "delete_memory"
    if is_delete:
        msg = _MSGS_MEMORY_DELETE.get(language, _MSGS_MEMORY_DELETE["zh-TW"])
        # before snapshot 從 marker 帶入（remember delete 時撈的）。
        before = signal.get("before") or {}
        before_data = before.get("data") if isinstance(before, dict) else None
        before_val = ""
        before_cat = "fact"
        if isinstance(before_data, dict):
            before_val = before_data.get("value", "")
            before_cat = before_data.get("category", "fact")
        return {
            "type": "memory_consent",
            "mode": "delete",
            "message": msg,
            "content": before_val,
            "category": before_cat,
            "key": signal.get("key", ""),
        }
    return {
        "type": "memory_consent",
        "mode": "create",
        "message": _MSGS_MEMORY_CREATE.get(language, _MSGS_MEMORY_CREATE["zh-TW"]),
        "content": signal.get("content", ""),
        "category": signal.get("category", "fact"),
        "key": signal.get("key", ""),
    }


def parse_skill_memory_consent_answer(answer: Any) -> Dict[str, Any]:
    """解析前端 skill/memory/journal 同意卡的回應。

    前端送 ``{action: 'skill_consent'|'memory_consent'|'journal_entry',
    approved: bool, edited_fields?: {...}}``。
    fail-closed：無法判讀視為拒絕。
    """
    if isinstance(answer, dict):
        if answer.get("action") in (
            "skill_consent",
            "memory_consent",
            "journal_entry",
            "journal_consent",
        ):
            return {
                "approved": bool(answer.get("approved")),
                "edited_fields": answer.get("edited_fields") or {},
                "raw": answer,
            }
        if answer.get("action") == "cancel":
            return {"approved": False, "edited_fields": {}, "raw": answer}
        if "approved" in answer:
            return {
                "approved": bool(answer["approved"]),
                "edited_fields": answer.get("edited_fields") or {},
                "raw": answer,
            }
    if isinstance(answer, str):
        normalized = answer.strip().lower()
        # 同意/拒絕詞集涵蓋四語（2026-09-21）：同意卡 fallback 會引導使用者
        # 直接回「同意／取消」，英文 approve、俄文 да/нет 也要認得——
        # 否則照卡片指引回覆卻被 fail-closed 誤判，核可路徑直接失效。
        if normalized in {
            "yes", "y", "ok", "approve", "同意", "同意。", "好", "好的", "да",
        }:
            return {"approved": True, "edited_fields": {}, "raw": answer}
        if normalized in {
            "no", "n", "cancel", "decline", "reject",
            "否", "不要", "取消", "нет",
        }:
            return {"approved": False, "edited_fields": {}, "raw": answer}
    return {"approved": False, "edited_fields": {}, "raw": answer}  # fail-closed


def log_skill_memory_decision(
    *,
    user_id: Optional[str],
    username: Optional[str],
    kind: str,
    mode: str,
    approved: bool,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
) -> None:
    """把 skill/memory 提議的同意決定記進 audit log，含 before 快照（改/刪可追溯）。"""
    audit_log(
        "consent_high_risk_action",
        user_id=user_id,
        username=username,
        resource_type="skill_memory",
        resource_id=f"{kind}:{mode}",
        metadata={
            "consent": {
                "granted": approved,
                "kind": kind,
                "mode": mode,
                "before": before,  # 改/刪前的完整內容（None for create）
                "after": after,  # 核准後的最終內容（可能含使用者編輯）
            }
        },
        endpoint="agent://skill_memory_consent",
        success=approved,
    )
    logger.info(
        "[SkillMemoryConsent] user=%s kind=%s mode=%s approved=%s",
        user_id,
        kind,
        mode,
        approved,
    )


# ── 決定之後的處理結果 ───────────────────────────────────────────────────────
# 使用者按了確認卡之後，回覆原本什麼都不會說（2026-10-05 線上：按「確認記入」後只剩「請確認同意卡」，
# 使用者不知道有沒有記進去，還回頭問「記錄了嗎」）。結果句附在回覆最後。
# 只涵蓋能確認成敗的 kind（寫入函式回 DB id）：記憶／技能的寫入沒有回傳值，不亂報。
_OUTCOME_MSGS: Dict[str, Dict[str, Dict[str, str]]] = {
    "journal_entry": {
        "zh-TW": {
            "done": "✅ 已記入帳本：{detail}",
            "failed": "⚠️ 這筆沒有寫入帳本（系統暫時出錯），請稍後再試，或到帳本頁手動新增：{detail}",
            "declined": "已取消，這筆沒有記入帳本：{detail}",
        },
        "zh-CN": {
            "done": "✅ 已记入帐本：{detail}",
            "failed": "⚠️ 这笔没有写入帐本（系统暂时出错），请稍后再试，或到帐本页手动新增：{detail}",
            "declined": "已取消，这笔没有记入帐本：{detail}",
        },
        "en": {
            "done": "✅ Added to your ledger: {detail}",
            "failed": "⚠️ This entry was NOT saved (temporary error). Please try again, or add it on the Ledger page: {detail}",
            "declined": "Cancelled — nothing was added to your ledger: {detail}",
        },
        "ru": {
            "done": "✅ Добавлено в журнал: {detail}",
            "failed": "⚠️ Запись НЕ сохранена (временная ошибка). Повторите позже или добавьте вручную на странице журнала: {detail}",
            "declined": "Отменено — в журнал ничего не добавлено: {detail}",
        },
    },
    "journal_delete": {
        "zh-TW": {
            "done": "✅ 已刪除帳本紀錄 {detail}",
            "failed": "⚠️ 刪除失敗，帳本紀錄沒有變動 {detail}",
            "declined": "已取消，沒有刪除帳本紀錄 {detail}",
        },
        "zh-CN": {
            "done": "✅ 已删除帐本记录 {detail}",
            "failed": "⚠️ 删除失败，帐本记录没有变动 {detail}",
            "declined": "已取消，没有删除帐本记录 {detail}",
        },
        "en": {
            "done": "✅ Ledger entry deleted {detail}",
            "failed": "⚠️ Delete failed — your ledger is unchanged {detail}",
            "declined": "Cancelled — nothing was deleted {detail}",
        },
        "ru": {
            "done": "✅ Запись журнала удалена {detail}",
            "failed": "⚠️ Не удалось удалить — журнал не изменён {detail}",
            "declined": "Отменено — ничего не удалено {detail}",
        },
    },
    # 記憶／技能（2026-10-06 審查：原本核准後完全沒有回饋，使用者不知道有沒有存到，只好再講一次）
    "create_memory": {
        "zh-TW": {
            "done": "✅ 已記住：{detail}",
            "failed": "⚠️ 這則資訊沒有存進記憶（系統暫時出錯），請稍後再說一次：{detail}",
            "declined": "已取消，沒有記住：{detail}",
        },
        "zh-CN": {
            "done": "✅ 已记住：{detail}",
            "failed": "⚠️ 这则信息没有存进记忆（系统暂时出错），请稍后再说一次：{detail}",
            "declined": "已取消，没有记住：{detail}",
        },
        "en": {
            "done": "✅ Remembered: {detail}",
            "failed": "⚠️ This was NOT saved to memory (temporary error) — please tell me again later: {detail}",
            "declined": "Cancelled — nothing was remembered: {detail}",
        },
        "ru": {
            "done": "✅ Запомнено: {detail}",
            "failed": "⚠️ Не сохранено в памяти (временная ошибка) — повторите позже: {detail}",
            "declined": "Отменено — ничего не запомнено: {detail}",
        },
    },
    "delete_memory": {
        "zh-TW": {
            "done": "✅ 已忘記這則記憶：{detail}",
            "failed": "⚠️ 刪除記憶失敗，記憶沒有變動：{detail}",
            "declined": "已取消，記憶沒有變動：{detail}",
        },
        "zh-CN": {
            "done": "✅ 已忘记这则记忆：{detail}",
            "failed": "⚠️ 删除记忆失败，记忆没有变动：{detail}",
            "declined": "已取消，记忆没有变动：{detail}",
        },
        "en": {
            "done": "✅ Forgot this memory: {detail}",
            "failed": "⚠️ Could not delete the memory — nothing changed: {detail}",
            "declined": "Cancelled — your memory is unchanged: {detail}",
        },
        "ru": {
            "done": "✅ Эта запись памяти удалена: {detail}",
            "failed": "⚠️ Не удалось удалить — память не изменена: {detail}",
            "declined": "Отменено — память не изменена: {detail}",
        },
    },
    "custom_skill": {
        "zh-TW": {
            "done": "✅ 技能設定已套用：{detail}",
            "failed": "⚠️ 技能設定沒有套用（系統暫時出錯）：{detail}",
            "declined": "已取消，技能設定沒有變動：{detail}",
        },
        "zh-CN": {
            "done": "✅ 技能设置已应用：{detail}",
            "failed": "⚠️ 技能设置没有应用（系统暂时出错）：{detail}",
            "declined": "已取消，技能设置没有变动：{detail}",
        },
        "en": {
            "done": "✅ Skill change applied: {detail}",
            "failed": "⚠️ The skill change was NOT applied (temporary error): {detail}",
            "declined": "Cancelled — your skills are unchanged: {detail}",
        },
        "ru": {
            "done": "✅ Изменение навыка применено: {detail}",
            "failed": "⚠️ Изменение навыка НЕ применено (временная ошибка): {detail}",
            "declined": "Отменено — навыки не изменены: {detail}",
        },
    },
    "journal_update": {
        "zh-TW": {
            "done": "✅ 已修改帳本紀錄 {detail}",
            "failed": "⚠️ 修改失敗，帳本紀錄沒有變動 {detail}",
            "declined": "已取消，沒有修改帳本紀錄 {detail}",
        },
        "zh-CN": {
            "done": "✅ 已修改帐本记录 {detail}",
            "failed": "⚠️ 修改失败，帐本记录没有变动 {detail}",
            "declined": "已取消，没有修改帐本记录 {detail}",
        },
        "en": {
            "done": "✅ Ledger entry updated {detail}",
            "failed": "⚠️ Update failed — your ledger is unchanged {detail}",
            "declined": "Cancelled — nothing was changed {detail}",
        },
        "ru": {
            "done": "✅ Запись журнала изменена {detail}",
            "failed": "⚠️ Не удалось изменить — журнал не изменён {detail}",
            "declined": "Отменено — ничего не изменено {detail}",
        },
    },
}


def _outcome_detail(signal: Dict[str, Any], edited: Dict[str, Any], language: str) -> str:
    """結果句裡的內容摘要：記帳＝「支出 110000 TWD（類別）」；刪改＝「#編號」。"""
    final = {**signal, **(edited or {})}
    kind = signal.get("kind", "")
    if kind == "journal_entry":
        label = _JOURNAL_ENTRY_LABELS.get(
            final.get("entry_type", "expense"), _JOURNAL_ENTRY_LABELS["expense"]
        )
        # 英文／俄文標籤是受詞形式（"an expense"），摘要要的是名詞——只有中文直接用
        text = label.get(language) if language in ("zh-TW", "zh-CN") else ""
        amount = f"{_fmt_num(final.get('amount'))} {final.get('currency', '')}".strip()
        text = f"{text} {amount}".strip() if text else amount
        if final.get("category"):
            text += f"（{final['category']}）"
        return text
    if kind in ("create_memory", "delete_memory"):
        content = str(final.get("content") or final.get("key") or "").strip()
        content = content if len(content) <= 60 else content[:59] + "…"
        category = final.get("category")
        suffix = f"（{category}）" if category and kind == "create_memory" else ""
        return f"「{content}」{suffix}" if content else ""
    if kind == "custom_skill":
        return str(final.get("skill_name") or "").strip()
    entry_id = signal.get("entry_id")
    return f"#{entry_id}" if entry_id is not None else ""


def build_consent_outcome_text(outcomes: list, language: str) -> str:
    """把確認卡的處理結果組成附在回覆最後的文字（沒有可報的結果回空字串）。

    outcomes：``[{"signal": 提案, "approved": bool, "ok": bool, "edited": dict}]``。
    ok 由各 kind 的寫入函式回報（記帳回 DB id；記憶／技能回是否建立成功）；沒有結果句的 kind 不報。
    """
    lines = []
    for item in outcomes or []:
        signal = item.get("signal") or {}
        by_lang = _OUTCOME_MSGS.get(signal.get("kind", ""))
        if not by_lang:
            continue
        msgs = by_lang.get(language) or by_lang["en"]
        state = (
            "declined"
            if not item.get("approved")
            else ("done" if item.get("ok") else "failed")
        )
        lines.append(
            msgs[state]
            .format(detail=_outcome_detail(signal, item.get("edited") or {}, language))
            .rstrip(" ：:")
        )
    return ("\n\n" + "\n".join(lines)) if lines else ""


def log_journal_entry_decision(
    *,
    user_id: Optional[str],
    username: Optional[str],
    approved: bool,
    signal: Dict[str, Any],
    edited: Optional[Dict[str, Any]] = None,
    entry_id: Optional[int] = None,
) -> None:
    """把記帳確認卡的決定記進 audit log（金流治理：提案→核准→寫入全留痕）。

    ``signal`` 是提案內容（含凍結匯率），``edited`` 是使用者在卡上改過的欄位，
    ``entry_id`` 是核准寫入後的 DB id（拒絕時為 None）。
    """
    final = {**signal, **(edited or {})}
    audit_log(
        "consent_high_risk_action",
        user_id=user_id,
        username=username,
        resource_type="journal_entry",
        resource_id=str(entry_id) if entry_id is not None else "proposed",
        metadata={
            "consent": {
                "granted": approved,
                "kind": "journal_entry",
                "entry_type": final.get("entry_type"),
                "amount": final.get("amount"),
                "currency": final.get("currency"),
                "category": final.get("category"),
                "exchange_rate": final.get("exchange_rate"),
                "rate_source": final.get("rate_source"),
                "edited_fields": edited or {},
                "entry_id": entry_id,
            }
        },
        endpoint="agent://journal_consent",
        success=approved,
    )
    logger.info(
        "[JournalConsent] user=%s approved=%s type=%s amount=%s %s entry_id=%s",
        user_id,
        approved,
        final.get("entry_type"),
        final.get("amount"),
        final.get("currency"),
        entry_id,
    )


__all__ = [
    "scan_high_risk_tools",
    "assess_user_trust",
    "should_require_consent",
    "build_consent_payload",
    "parse_consent_answer",
    "log_consent_decision",
    "log_high_risk_tool_execution",
    "append_guard_audit_receipt",
    # skill / memory self-manage consent
    "build_skill_consent_payload",
    "build_memory_consent_payload",
    "parse_skill_memory_consent_answer",
    "log_skill_memory_decision",
    # journal (unified ledger) consent
    "build_journal_consent_payload",
    "log_journal_entry_decision",
]
