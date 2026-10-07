"""Vision（圖片辨識）服務——Tier 1 描述式架構（docs/plans/2026-08-27-vision-image-analysis-design.md）。

流程：使用者附圖（data URL）→ 驗證（mime 白名單 + 大小上限 + flag）
→ 以使用者 BYOK 模型做一次多模態描述 → 描述文字由路由層併入 query
→ 既有 agent 管線（零內部改動）。

設計決策：
- 描述式而非多模態直連：`_llm_invoke(prompt: str)` 架構下直連需改所有
  訊息建構點（v2 再評估）；描述式全 provider 一致、Telegram 同鏈、可測性好。
- 僅登入用戶：免費線（訪客）成本考量，明確不開。
- data URL 不進 log、不落 DB（描述文字才併入 query）。
"""

from __future__ import annotations

import base64
import os
import re
from typing import Any, Optional

from langchain_core.messages import HumanMessage

from api.utils import logger
from utils.user_client_factory import create_user_llm_client

_DATA_URL_RE = re.compile(
    r"^data:image/(?P<mime>png|jpeg|webp);base64,(?P<b64>[A-Za-z0-9+/=\s]+)$"
)

_ALLOWED_MIMES = {"png", "jpeg", "webp"}


class VisionError(Exception):
    """附圖處理失敗。code 供前端映射 i18n（4 語）。"""

    def __init__(self, code: str, status_code: int = 400):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def vision_enabled() -> bool:
    from core.feature_flags import env_flag

    return env_flag("VISION_ENABLED", "true")


def _max_image_bytes() -> int:
    raw = os.getenv("VISION_MAX_IMAGE_BYTES", "").strip()
    try:
        n = int(raw) if raw else 4 * 1024 * 1024
    except ValueError:
        n = 4 * 1024 * 1024
    return max(64 * 1024, n)


def validate_image_data_url(data_url: str) -> str:
    """驗證並正規化附圖 data URL；不符即 raise VisionError。

    回傳正規化後（移除空白）的 data URL。
    """
    if not vision_enabled():
        raise VisionError("VISION_DISABLED", status_code=403)
    if not isinstance(data_url, str) or not data_url.strip():
        raise VisionError("VISION_INVALID_FORMAT")
    m = _DATA_URL_RE.match(data_url.strip())
    if not m:
        raise VisionError("VISION_INVALID_FORMAT")
    mime = m.group("mime")
    if mime not in _ALLOWED_MIMES:
        raise VisionError("VISION_INVALID_FORMAT")
    b64 = m.group("b64")
    # 大小以「解碼後位元組」計；base64 約 4/3 倍，先用長度粗篩再精算
    approx = len(b64) * 3 // 4
    if approx > _max_image_bytes():
        raise VisionError("VISION_IMAGE_TOO_LARGE")
    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception as exc:  # base64.binascii 錯誤
        raise VisionError("VISION_INVALID_FORMAT") from exc
    if len(raw) > _max_image_bytes():
        raise VisionError("VISION_IMAGE_TOO_LARGE")
    return data_url.strip()


def vision_supported(provider: str, model: Optional[str]) -> bool:
    """啟發式：provider/model 是否支援圖片輸入（2026-08-28 目錄校準）。

    未知名單時回 True——交由實際呼叫失敗映射 VISION_UNSUPPORTED_MODEL，
    避免把新視覺模型誤判為不支援。
    """
    p = (provider or "").lower()
    m = (model or "").lower()
    if p == "google_gemini":
        return not m.startswith("gemini-1.0")
    if p == "openai":
        if any(k in m for k in ("gpt-3.5", "gpt-4-turbo-preview", "o1-mini")):
            return False
        return True
    if p == "anthropic":
        return not m.startswith("claude-2")
    if p == "deepseek":
        # deepseek-flash（= V4.1 Flash，2026-09-10 起原生多模態）與舊的
        # deepseek-v4-flash-vision-exp；deepseek-v4-pro / 退役的 v4-flash 純文字
        return m == "deepseek-flash" or "v4.1" in m or "vision" in m
    if p == "zhipu":
        # GLM-5.3-Flash 為 GLM-5 系列首個原生多模態；glm-5.1/5.3 純文字
        return "flash" in m or "vision" in m
    if p == "moonshot":
        return "k3" in m or "k2.5" in m
    if p == "openrouter":
        blocked = (
            "gpt-3.5",
            "deepseek-chat",
            "deepseek-r1",
            "qwen2.5",
            "llama-3.1",
            "deepseek-v4-pro",
            "deepseek-v4-flash:",  # :free/別名純文字；vision 請用 -vision-exp
            "nemotron-3-ultra",  # 實測上游拒收圖片（2026-08-28 E2E）
            "nemotron-3.5-lightning",
        )
        return not any(k in m for k in blocked)
    # siliconflow / volcengine / nvidia 等文字線：保守視為不支援
    return False


def _image_content_blocks(data_url: str, prompt: str) -> list[dict[str, Any]]:
    """LangChain 標準多模態 content blocks（init_chat_model 各 provider 通用）。"""
    return [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]


async def describe_image(
    data_url: str,
    credentials: dict,
    language: str = "en",
) -> str:
    """以使用者 BYOK 模型對附圖產生結構化描述（給 agent 管線當 query 上下文）。

    Raises:
        VisionError: VISION_UNSUPPORTED_MODEL / VISION_DESCRIPTION_FAILED
    """
    provider = credentials.get("provider", "")
    api_key = credentials.get("api_key", "")
    model = credentials.get("model")

    if not vision_supported(provider, model):
        raise VisionError("VISION_UNSUPPORTED_MODEL")

    prompt = (
        "Describe this image in detail for a financial analyst. "
        "If it is a candlestick/price chart, report: asset & timeframe if shown, "
        "trend structure, key support/resistance levels, indicators visible, "
        "and any notable patterns. If it is a position/portfolio screenshot, "
        "report every symbol, size, entry/price and PnL exactly as shown. "
        "If it is a chat message screenshot, transcribe the key content. "
        "Be factual and exhaustive; do not give advice. "
        f"Respond in language code: {language}."
    )

    llm = create_user_llm_client(provider, api_key, model)
    message = HumanMessage(content=_image_content_blocks(data_url, prompt))
    try:
        response = await llm.ainvoke([message])
    except Exception as exc:
        lowered = str(exc).lower()
        if "429" in lowered or "rate-limited" in lowered or "rate limit" in lowered:
            # 免費線上游共享池限流（如 :free 模型）——稍後重試或換模型
            logger.warning("Vision: upstream rate-limited (%s/%s)", provider, model)
            raise VisionError("VISION_RATE_LIMITED", status_code=429) from exc
        if any(
            k in lowered
            for k in (
                "image",
                "vision",
                "multi-modal",
                "multimodal",
                "content type",
                "invalid content",
                "does not support",
            )
        ):
            logger.info(
                "Vision: provider/model rejected image input (%s/%s)",
                provider,
                model,
            )
            raise VisionError("VISION_UNSUPPORTED_MODEL") from exc
        # 其餘（網路/金鑰/配額/未知）→ 仍以 VISION_* 碼呈現給前端（可 i18n），
        # 原始例外保留在 log 供診斷——避免落入泛用 500 無法引導使用者
        logger.error("Vision describe failed (%s/%s): %s", provider, model, exc)
        raise VisionError("VISION_DESCRIPTION_FAILED", status_code=502) from exc

    content = response.content
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    text = (content or "").strip()
    if not text:
        raise VisionError("VISION_DESCRIPTION_FAILED", status_code=502)
    logger.info(
        "Vision: described image for %s/%s (%d chars)", provider, model, len(text)
    )
    return text


def augment_query_with_image(message: str, description: str) -> str:
    """把附圖描述併入使用者訊息（query 前綴式，歷史保存同格式）。"""
    return f"{message}\n\n[Attached image — content description]\n{description}"


# ---------------------------------------------------------------------------
# 圖片存儲（vision Phase 2，docs/plans/2026-08-30-vision-image-storage-design.md）
# 壓縮圖 bytea 存 chat_attachments（30 天保留、隨對話刪除、privacy v2.3）。
# ---------------------------------------------------------------------------


def image_storage_enabled() -> bool:
    """回滾開關：VISION_STORAGE_ENABLED=false 停寫入（讀取保留），表可留。"""
    from core.feature_flags import env_flag

    return env_flag("VISION_STORAGE_ENABLED", "true")


def decode_image_data_url(data_url: str) -> tuple[bytes, str]:
    """把已通過 validate_image_data_url 的 data URL 還原為 (位元組, mime)。

    再驗一次大小上限（呼叫端可能與驗證點不同時空轉發）。
    """
    m = _DATA_URL_RE.match(data_url.strip())
    if not m:
        raise VisionError("VISION_INVALID_FORMAT")
    raw = base64.b64decode(m.group("b64"), validate=False)
    if len(raw) > _max_image_bytes():
        raise VisionError("VISION_IMAGE_TOO_LARGE")
    return raw, m.group("mime")


async def store_image_attachment(
    data_url: str, user_id: str, session_id: str
) -> Optional[int]:
    """壓縮圖存入 chat_attachments，回傳 attachment_id（flag off 或失敗回 None）。

    Best-effort：附圖描述已完成、分析主流程不應因存檔失敗而中斷，
    因此僅捕捉 SQLAlchemyError（表未建/連線抖動）並記 log，不往上拋。
    """
    if not image_storage_enabled():
        return None
    raw, mime = decode_image_data_url(data_url)
    from sqlalchemy.exc import SQLAlchemyError

    from core.orm.chat_attachments_repo import chat_attachments_repo
    from core.orm.session import using_session

    try:
        async with using_session() as db:
            attachment = await chat_attachments_repo.create(
                db,
                user_id=user_id,
                session_id=session_id,
                mime=mime,
                data=raw,
            )
        return attachment.id
    except SQLAlchemyError as exc:
        logger.warning(
            "Vision: attachment store failed (user=%s session=%s): %s",
            user_id,
            session_id,
            exc,
        )
        return None
