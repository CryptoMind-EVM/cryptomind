"""
Human Passport (Gitcoin Passport) Scorer API 客戶端——Phase A1 scaffold

申請 Scorer ID + API key：
    https://docs.passport.human.tech/building-with-passport/stamps/passport-api/getting-access

Phase A0：本模組存在但未啟用（scoring.py 的 collect_passport_stamps 在 key 未設時
回空 stamps）。
Phase A1：把 HUMAN_PASSPORT_SCORER_ID / HUMAN_PASSPORT_API_KEY 設好即自動啟用。

設計：
    - 純函式 async client，無 class，易 mock（測試 patch fetch_passport_score）。
    - graceful：API 失敗回 None，上層當「未驗證」處理（不中斷 scoring）。
    - 不快取——Scorer API 每次回最新分數，快取由 caller（cron 週期）控制。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from core.config import HUMAN_PASSPORT_API_KEY, HUMAN_PASSPORT_SCORER_ID

logger = logging.getLogger(__name__)

_SCORER_BASE = "https://api.scorer.gitcoin.co/registry"
_TIMEOUT = 15.0


async def fetch_passport_score(wallet_address: str) -> Optional[float]:
    """查詢某錢包的 Human Passport Unique Humanity Score。

    Args:
        wallet_address: EVM 地址（0x...）。註：Passport 目前主支援 EVM，
            TON 地址需用戶另外綁 EVM 地址才能計分——Phase A1 上線時要決定綁定流程。

    Returns:
        分數（float，通常 0-100）或 None（未設 / API 失敗）。
    """
    if not (HUMAN_PASSPORT_SCORER_ID and HUMAN_PASSPORT_API_KEY):
        logger.debug("[passport] not configured, skip")
        return None

    headers = {"Authorization": f"Bearer {HUMAN_PASSPORT_API_KEY}"}
    url = f"{_SCORER_BASE}/score/{HUMAN_PASSPORT_SCORER_ID}/{wallet_address}"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except httpx.HTTPError as exc:
        logger.warning("[passport] fetch failed for %s: %s", wallet_address, exc)
        return None

    if resp.status_code == 404:
        return None  # 用戶還沒護照
    if resp.status_code != 200:
        logger.warning("[passport] %s -> HTTP %s", wallet_address, resp.status_code)
        return None

    try:
        body = resp.json()
        # Scorer API 回傳 { "score": "12.34", "status": "DONE", ... }（score 是字串）
        raw = body.get("score")
        return float(raw) if raw is not None else None
    except (ValueError, TypeError) as exc:
        logger.warning("[passport] parse failed for %s: %s", wallet_address, exc)
        return None


__all__ = ["fetch_passport_score"]
