"""Agent self-managed skills & memory tools (HITL-gated).

docs/plans/2026-08-10-agent-self-managed-skills-memory-design.md

Two tools:
- ``list_my_skills_memory`` (low risk, read-only, NO HITL): returns the user's own
  custom skills + remembered facts so the agent can answer "what do I have?".
- ``propose_custom_skill`` (high risk, HITL): returns a ``__needs_consent__`` marker
  for create/update/delete a custom skill. claw_loop intercepts the marker,
  ``interrupt()``s for consent, and writes only after the user approves.

The third write path — ``remember`` (memory) — lives in ``remember_tool.py`` and
uses the same marker pattern.

Key safety invariant: these tools NEVER take ``user_id`` as a parameter. The user
is read from the contextvar inside claw_loop at write time, so a prompt-injected
tool call cannot target another user's data. The tools only *propose*; the node
*writes*.
"""
import json
import logging
import time as _time

from langchain_core.tools import tool

from .remember_tool import _contains_pii
from .schemas import ListMySkillsMemoryInput, ProposeCustomSkillInput

logger = logging.getLogger(__name__)

# Reuse the same marker key as remember_tool so claw_loop has one detector.
from .remember_tool import NEEDS_CONSENT_KEY  # noqa: E402

# Mirrors SkillPreferenceStore limits (kept here for a pre-write guard before consent).
_MAX_BODY = 3000
_MAX_NAME = 50
_MAX_TRIGGER = 200
_NAME_RE_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _valid_skill_name(name: str) -> bool:
    """Same rule as SkillPreferenceStore._NAME_RE: [a-zA-Z0-9_-]+."""
    if not name or len(name) > _MAX_NAME:
        return False
    return all(c in _NAME_RE_OK for c in name)


def _preview(text: str, limit: int = 200) -> str:
    """Truncate a long body for display on the consent card."""
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


@tool(args_schema=ListMySkillsMemoryInput)
def list_my_skills_memory(kind: str = "all") -> str:
    """List all analysis skills (official + custom) and remembered facts.

    Use this when the user asks "what skills/methods do I have?", wants to review
    their skills, or before proposing a change so you can show them what exists.
    Returns BOTH official skills (read-only templates shared by all users) AND the
    user's own custom skills, plus remembered facts. This is read-only and safe.

    Args:
      kind: 'skill' = official + custom skills, 'memory' = remembered facts only,
            'all' = both (default).
    """
    from core.tools.key_resolver import get_current_user_id

    user_id = get_current_user_id()
    if not user_id:
        return "(No logged-in user; cannot list skills/memory.)"

    kind = (kind or "all").strip().lower()
    if kind not in ("skill", "memory", "all"):
        kind = "all"

    sections = []

    if kind in ("skill", "all"):
        # 官方 skill（唯讀範本，所有使用者共用）
        try:
            from core.agents.skill_loader import get_skill_loader

            official = get_skill_loader().load_all()
            if official:
                off_lines = [
                    f"  • {s.name}: {s.description}" for s in official.values()
                ]
                sections.append(
                    "Official analysis skills (read-only templates):\n"
                    + "\n".join(off_lines)
                )
            else:
                sections.append("Official analysis skills: (none loaded)")
        except Exception as exc:  # graceful — never crash the agent
            logger.debug("[list_my_skills_memory] official skill read failed: %s", exc)
            sections.append("Official analysis skills: (unable to read)")

        # 使用者自訂 skill（可建立/修改/刪除）
        try:
            from core.database.skill_preferences import SkillPreferenceStore

            skills = SkillPreferenceStore(user_id=user_id).get_custom_skills()
            if skills:
                lines = [f"  • {s.get('skill_name', '?')}: {s.get('description', '')}"
                         for s in skills]
                sections.append("Your custom analysis skills:\n" + "\n".join(lines))
            else:
                sections.append("Your custom analysis skills: (none yet)")
        except Exception as exc:  # graceful — never crash the agent
            logger.debug("[list_my_skills_memory] skill read failed: %s", exc)
            sections.append("Your custom analysis skills: (unable to read)")

    if kind in ("memory", "all"):
        try:
            from core.database.memory import MemoryStore
            from core.tools.key_resolver import get_current_agent_id

            # agent 視角：共享＋自身私有（Mixer Step 4——agent 看得到自己
            # 記了什麼，才能列出 key 供 remember delete 使用）
            facts = MemoryStore(
                user_id=user_id, agent_id=get_current_agent_id()
            ).read_facts()
            if facts:
                # Group by category for readability. Show key so the agent/user
                # can reference a specific memory for deletion (remember mode=delete).
                by_cat: dict[str, list[str]] = {}
                for fkey, fdata in facts.items():
                    cat = (fdata.get("category", "fact") if isinstance(fdata, dict) else "fact")
                    val = (fdata.get("value", "") if isinstance(fdata, dict) else str(fdata))
                    # Mixer Step 4：私有記憶標示（agent 自己的專屬觀察）
                    private_mark = (
                        " [private]"
                        if isinstance(fdata, dict) and fdata.get("agent_id")
                        else ""
                    )
                    by_cat.setdefault(cat, []).append(
                        f"  • [{fkey}] ({cat}){private_mark} {val[:80]}"
                    )
                mem_lines = []
                for cat in ("preference", "holding", "context", "fact"):
                    if cat in by_cat:
                        mem_lines.extend(by_cat[cat])
                sections.append(
                    "Your remembered facts (the [key] in brackets is needed to delete one):\n"
                    + "\n".join(mem_lines)
                )
            else:
                sections.append("Your remembered facts: (none yet)")
        except Exception as exc:  # graceful
            logger.debug("[list_my_skills_memory] memory read failed: %s", exc)
            sections.append("Your remembered facts: (unable to read)")

    return "\n\n".join(sections)


@tool(args_schema=ProposeCustomSkillInput)
def propose_custom_skill(
    mode: str = "create",
    skill_name: str = "",
    description: str = "",
    trigger_keywords: str = "",
    body: str = "",
    reason: str = "",
) -> str:
    """Propose to create, update, or delete one of the user's custom analysis skills.

    The user must approve the proposal before anything is stored — you are
    *proposing*, and a consent card will be shown. Always tell the user what you are
    proposing and why in your reply too.

    When to call:
    - The user explicitly asks to create/edit/remove a personal analysis method.
    - The user repeats the same analysis need several times and would benefit from a
      reusable method (then *propose* it — do not create silently).

    When NOT to call:
    - The user only wants a one-off answer.
    - The user wants to change an OFFICIAL skill (those are read-only templates;
      instead propose a new personal skill that customizes the approach).

    Args:
      mode: 'create' (default) / 'update' / 'delete'.
      skill_name: identifier (letters/digits/underscore/hyphen, <=50 chars).
      description: one-line summary (required for create/update).
      trigger_keywords: comma-separated auto-trigger words.
      body: full method steps (<=3000 chars; required for create/update).
      reason: why you propose this, in the user's language (shown on consent card).
    """
    mode = (mode or "create").strip().lower()
    if mode not in ("create", "update", "delete"):
        return "⚠️ Invalid mode; use 'create', 'update', or 'delete'."

    # 1. Validate name (all modes need a target).
    if not _valid_skill_name(skill_name):
        return (f"⚠️ Invalid skill_name '{skill_name}'. "
                f"Use { _MAX_NAME } chars max, only letters/digits/underscore/hyphen.")

    # 2. PII guard — never let PII sneak into a skill body/description.
    for field_val in (description, body, reason):
        if _contains_pii(field_val):
            return ("⚠️ Sensitive information detected in the proposal; "
                    "it was blocked for security.")

    # 3. Length guards for create/update.
    if mode in ("create", "update"):
        if not description.strip():
            return "⚠️ 'description' is required for create/update."
        if not body.strip():
            return "⚠️ 'body' is required for create/update."
        if len(body) > _MAX_BODY:
            return f"⚠️ body is too long ({len(body)} > {_MAX_BODY} chars)."
        if len(trigger_keywords) > _MAX_TRIGGER:
            return f"⚠️ trigger_keywords too long (>{_MAX_TRIGGER} chars)."

    # 4. Return consent marker — claw_loop intercepts and interrupt()s.
    #    user_id is NOT read here; it is read inside claw_loop at write time,
    #    so a spoofed contextvar cannot write to another user's skills.
    marker = {
        NEEDS_CONSENT_KEY: True,
            "ts": _time.time(),
        "kind": "custom_skill",
        "mode": mode,
        "skill_name": skill_name.strip(),
        "description": description.strip(),
        "trigger_keywords": trigger_keywords.strip(),
        "body": body,
        "body_preview": _preview(body),
        "reason": reason.strip(),
    }
    logger.info(
        "[propose_custom_skill] proposed mode=%s name=%s (pending consent)",
        mode, skill_name,
    )
    return json.dumps(marker, ensure_ascii=False)
