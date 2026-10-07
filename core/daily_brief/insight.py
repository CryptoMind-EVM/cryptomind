"""早報的「一句話」：平台本地模型（CryptoMind Lite）跑一次短呼叫；本地模型不可用就省略，早報照發。

護欄（沿用 eval 的 provenance 規則）：一句話裡不得出現早報本文沒有的數字——
有就整句丟掉、記 info。另外禁止買賣指令式的字眼（合規：不是投資建議）。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

INSIGHT_TIMEOUT_S = 20.0
MAX_INSIGHT_CHARS = 140

_SYSTEM = {
    "zh-TW": (
        "你是 CryptoMind 的早報助理。根據下面這則早報，用一句話（60 字內）點出今天最值得使用者注意的一件事，"
        "並以一個邀請追問的問句結尾。只能引用早報裡已有的數字，不可自行加數字；不得叫使用者買或賣；不加前綴、不加表情符號。"
    ),
    "zh-CN": (
        "你是 CryptoMind 的早报助理。根据下面这则早报，用一句话（60 字内）点出今天最值得用户注意的一件事，"
        "并以一个邀请追问的问句结尾。只能引用早报里已有的数字，不可自行加数字；不得叫用户买或卖；不加前缀、不加表情符号。"
    ),
    "en": (
        "You are CryptoMind's morning-brief assistant. From the brief below, write ONE sentence (max 30 words) "
        "on the single most noteworthy thing for this user today, ending with a short follow-up question. "
        "Use only numbers already present in the brief; never tell the user to buy or sell; no prefix, no emoji."
    ),
    "ru": (
        "Вы помощник утренней сводки CryptoMind. По сводке ниже напишите ОДНО предложение (до 30 слов) о самом "
        "важном для пользователя сегодня и закончите коротким уточняющим вопросом. Используйте только числа из "
        "сводки; не советуйте покупать или продавать; без префиксов и эмодзи."
    ),
}

_FORBIDDEN = re.compile(
    r"(全倉|梭哈|all[- ]?in|買進|買入|賣出|賣掉|加倉|減倉|buy now|sell now|должны купить|должны продать)",
    re.IGNORECASE,
)


def _system_prompt(language: str) -> str:
    return _SYSTEM.get(language) or _SYSTEM["zh-TW"]


def sanitize_insight(text: Optional[str], brief_text: str) -> Optional[str]:
    """純函式：來源檢查＋合規字眼＋長度。不通過回 None（早報照發、少那一句）。"""
    if not text:
        return None
    from core.agents.eval.scoring import numbers_without_source

    cleaned = " ".join(str(text).split()).strip().strip('"「」')
    if not cleaned:
        return None
    if _FORBIDDEN.search(cleaned):
        logger.info("[daily_brief] insight dropped: forbidden wording")
        return None
    orphans = numbers_without_source(cleaned, [brief_text])
    if orphans:
        logger.info(
            "[daily_brief] insight dropped: numbers without source %s", orphans[:3]
        )
        return None
    if len(cleaned) > MAX_INSIGHT_CHARS:
        cleaned = cleaned[: MAX_INSIGHT_CHARS - 1].rstrip() + "…"
    return cleaned


async def generate_insight(
    user_id: str, membership_tier: str, language: str, brief_text: str
) -> Optional[str]:
    """只走平台本地模型；任何失敗都回 None，絕不擋早報。

    2026-09-27 DANNY：早報一律用本地模型，不花雲端 token——連使用者自己的 BYOK
    金鑰也不用（原本會打到使用者綁的 DeepSeek 等）。本地模型沒啟用或健康探測
    失敗就省略這句；鏈上若設了雲端備援也不用。
    """
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from core.agents.fallback import fallback_credentials
        from utils.user_client_factory import create_user_llm_client

        creds = fallback_credentials(membership_tier or "free")
        if not creds or not creds.get("local") or not creds.get("api_key"):
            return None
        client = create_user_llm_client(
            provider=creds["provider"],
            api_key=creds["api_key"],
            model=creds.get("model"),
        )
        resp = await asyncio.wait_for(
            client.ainvoke(
                [
                    SystemMessage(content=_system_prompt(language)),
                    HumanMessage(content=brief_text),
                ]
            ),
            timeout=INSIGHT_TIMEOUT_S,
        )
        content = getattr(resp, "content", resp)
        if isinstance(content, list):  # Anthropic 風格 content blocks
            content = " ".join(
                str(b.get("text", "")) if isinstance(b, dict) else str(b)
                for b in content
            )
        return sanitize_insight(str(content), brief_text)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "[daily_brief] insight skipped user=%s: %s", user_id, type(exc).__name__
        )
        return None
