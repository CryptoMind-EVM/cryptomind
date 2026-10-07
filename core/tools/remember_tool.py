"""remember tool — Hermes-style proactive memory (the agent decides what to remember).

Design (adapted from Hermes write_memory):
- **Proactive**: when the model judges "this user information is worth remembering",
  it calls this tool without the user asking.
- **Categorized**: category distinguishes preference/holding/fact/context;
  preference/holding are injected into the prompt first.
- **Compliant**: only records information the user volunteered; never records PII
  (ID number / password / private key); holdings are never auto-scraped from the
  wallet (TON Connect is a trust signal, not a memory source).
- **HITL-gated** (docs/plans/2026-08-10-agent-self-managed-skills-memory-design.md):
  the tool does NOT write directly. It returns a ``__needs_consent__`` marker; the
  claw_loop node intercepts it, ``interrupt()``s to show a consent card, and only
  writes after the user approves. This mirrors the ``clarify`` tool's marker
  pattern (tool-level ``interrupt()`` is infeasible because the inner agent has no
  checkpointer).

user_id source: via a contextvar (get_current_user_id), same mechanism as
key_resolver. base_react_agent sets up the current user context before the agent runs.
"""
import json
import logging
import re
import time as _time
from typing import Optional

from langchain_core.tools import tool

from .schemas import RememberInput

logger = logging.getLogger(__name__)

# Consent marker key — claw_loop._extract_consent_signal scans tool output for this.
_CLARIFY_SIGNAL_KEY = "__needs_clarify__"  # legacy, kept for clarify tool
NEEDS_CONSENT_KEY = "__needs_consent__"

# PII filter: never record this kind of sensitive information (compliance baseline)
_PII_PATTERNS = [
    re.compile(r"\b[A-Z]\d{9}\b"),  # Taiwan national ID
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN
    re.compile(r"0x[0-9a-fA-F]{32,}"),  # private key / wallet seed
    re.compile(r"-----BEGIN.*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),  # API keys
]
_PII_HINTS = ["身分證", "身份证", "密碼", "密码", "私鑰", "私钥", "seed phrase", "secret key"]


def _contains_pii(text: str) -> bool:
    """Detect PII (compliance: never record sensitive personal data)."""
    if not text:
        return False
    lower = text.lower()
    if any(hint in lower or hint in text for hint in _PII_HINTS):
        return True
    return any(p.search(text) for p in _PII_PATTERNS)


def _make_key(content: str, category: str) -> str:
    """Generate a snake_case key from the content (the key column of user_facts)."""
    # Simple normalization: take an md5 digest of the content as the key, prefixed
    # with the category to avoid collisions.
    import hashlib

    digest = hashlib.md5(content.encode("utf-8")).hexdigest()[:8]
    return f"{category}_{digest}"


@tool(args_schema=RememberInput)
def remember(content: str = "", category: str = "fact", mode: str = "create", key: str = "") -> str:
    """Remember important information about the user, or delete a previously remembered fact.

    mode='create' (default): remember new info. It is brought into the next
    conversation automatically to make answers more personalized.
    mode='delete': remove a previously remembered fact. Pass its 'key' (obtained
    from list_my_skills_memory, which shows each fact as '[key] (category) value').

    When to proactively call this tool with mode='create' (to remember):
    - The user explicitly states an investment preference: "I always look at technicals", "I prefer conservative investing"
    - The user's investment background: "I mainly trade crypto", "I'm a long-term holder"
    - The user's holdings (volunteered): "I hold 2.3 BTC", "I have TSLA"
    - A recurring pattern: the user asks about TW stocks several times in a row → remember they focus on TW stocks

    When to call with mode='delete':
    - The user explicitly asks to forget/remove/clear a remembered fact.
    - First call list_my_skills_memory to get the [key], then call this with mode='delete' and that key.

    When NOT to remember (just answer directly, do not call this tool):
    - Answers to one-off questions ("what's BTC right now" is not worth remembering)
    - Market facts that are easy to look up again (what NVDA does, today's index level)
    - Sensitive personal data (ID number, password, private key, seed phrase) — NEVER record these

    The user must approve before it is actually stored/removed — you are *proposing*.

    Args:
      content: the information to remember (concise, specific). Required for mode='create'.
      category: preference / holding / context / fact (mode='create' only).
      mode: 'create' (default) or 'delete'.
      key: the memory key to delete (mode='delete' only; from list_my_skills_memory).
    """
    mode = (mode or "create").strip().lower()

    # Get user_id (contextvar). Without a logged-in user there is nobody to
    # write for / delete for.
    from core.tools.key_resolver import get_current_user_id

    user_id = get_current_user_id()
    if not user_id:
        return "(No logged-in user; memory not stored. It will be remembered only after login.)"

    # ── delete mode ──
    if mode == "delete":
        if not key or not key.strip():
            return ("⚠️ mode='delete' requires 'key'. Call list_my_skills_memory first "
                    "to get the [key] of the memory you want to remove.")
        # Verify the fact exists + capture before-snapshot for the consent card / audit.
        before: Optional[dict] = None
        try:
            from core.database.memory import MemoryStore
            from core.tools.key_resolver import get_current_agent_id

            # agent 視角：共享＋自身私有（否則 agent 刪不到自己剛記的私有記憶）
            facts = MemoryStore(
                user_id=user_id, agent_id=get_current_agent_id()
            ).read_facts()
            fdata = facts.get(key.strip())
            if fdata is None:
                return f"⚠️ No memory with key '{key}'. Call list_my_skills_memory to see current memories."
            before = {"key": key.strip(), "data": fdata}
        except Exception as exc:  # graceful — never crash the agent
            logger.debug("[remember] delete pre-check failed: %s", exc)

        marker = {
            NEEDS_CONSENT_KEY: True,
            "ts": _time.time(),
            "kind": "delete_memory",
            "mode": "delete",
            "key": key.strip(),
            "before": before,
        }
        logger.info(
            "[remember] proposed memory DELETE (pending consent) user=%s key=%s",
            user_id, key,
        )
        return json.dumps(marker, ensure_ascii=False)

    # ── create mode (original behavior) ──
    # PII filter (compliance baseline) — block before even proposing
    if _contains_pii(content):
        return "⚠️ Sensitive information detected; for security it was not remembered. Please do not share ID numbers, passwords, private keys, etc."

    # Normalize category
    category = category.strip().lower() if category else "fact"
    if category not in ("preference", "holding", "context", "fact"):
        category = "fact"

    # Return consent marker — claw_loop intercepts and interrupt()s.
    # The actual MemoryStore.write_facts happens only after user approval.
    # user_id is read inside claw_loop (from self.user_id, not from this
    # contextvar) so a spoofed contextvar can never write to another user.
    marker = {
        NEEDS_CONSENT_KEY: True,
            "ts": _time.time(),
        "kind": "create_memory",
        "content": content.strip(),
        "category": category,
        "key": _make_key(content, category),
    }
    logger.info(
        "[remember] proposed memory (pending consent) user=%s category=%s: %s",
        user_id, category, content[:50],
    )
    return json.dumps(marker, ensure_ascii=False)
