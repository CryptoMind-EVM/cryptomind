"""app 端的發文內容檢查：自己微調的模型（detector.py）判斷該不該擋 → pass／block／unavailable。

2026-10-02 起只用一顆模型（DANNY）：舊的詐騙模型和寫死的關鍵字規則拿掉了；2026-10-03 換成 v7
CryptoMind-Guard-0.6B（微調 YuFeng-XGuard-Reason-0.6B），門檻 0.8 是 DANNY 選的。
- 分數 ≥ MODERATION_BLOCK_SCORE（0.8）→ block
- 0.5 ≤ 分數 < 0.8 → 照常發文，flagged 送管理員看（後台 audit：action=content_moderation）
- 貼出自己的助記詞／私鑰 → block（不是判斷內容好壞，是防止貼文的人把資產交出去）
- moderation 容器連不到／還在載模型 → unavailable（照常讓人發文）
字元間穿插符號（助＠記＠詞）：原文跟還原後的文字各問一次模型取高的（rules.normalize）。
同一段內容 10 分鐘內只問一次模型（發文頁即時檢查過，按發文時伺服器重檢不用再跑一次）。
v7 在 v6 考題 @0.8（Q8_0）：涉及未成年的性內容擋 93.4%、論壇有害 97.0%、論壇正常文誤擋 0.8%、提到兒少的正常文誤擋 5.5%。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from typing import Dict, Optional

import httpx

from core.feature_flags import moderation_enabled
from core.moderation.rules import leaked_secret, normalize, obfuscated

logger = logging.getLogger(__name__)

_CACHE_TTL = 600
_CACHE_MAX = 512
_cache: Dict[str, tuple] = {}


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _block_score() -> float:
    return _float_env("MODERATION_BLOCK_SCORE", 0.8)


def _flag_score() -> float:
    return _float_env("MODERATION_FLAG_SCORE", 0.5)


def _service_url() -> str:
    return os.getenv("MODERATION_URL", "http://moderation:8000").rstrip("/")


async def _classify(text: str) -> Optional[dict]:
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    try:
        # v7 在 2 核 CPU 中位數 0.4 秒、長文（頭尾兩段）約 1.2 秒；同時有人發文時 llama-server 會排隊，留餘裕
        async with httpx.AsyncClient(timeout=_float_env("MODERATION_TIMEOUT", 8.0)) as client:
            resp = await client.post(f"{_service_url()}/classify", json={"text": text})
        if resp.status_code != 200:
            return None
        result = resp.json()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — 檢查服務出任何事都不能讓發文 500，當作不在
        logger.warning("[moderation] service unavailable: %s", exc)
        return None
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic(), result)
    return result


async def _score(text: str) -> Optional[float]:
    """模型給的「該擋」機率；有穿插符號時還原後再問一次取高的。服務不在回 None。"""
    model = await _classify(text)
    score = model.get("block") if model else None
    if obfuscated(text):
        again = await _classify(normalize(text))
        if again and again.get("block") is not None:
            score = max(score or 0.0, again["block"])
    return score


async def check_post(title: str, content: str) -> dict:
    """回傳 {status, flagged, score, category, reasons, signals}；status 是 pass／block／unavailable。
    category／signals 是舊模型留下的欄位（前端、audit 還在讀），現在固定空的。"""
    text = f"{(title or '').strip()}\n{(content or '').strip()}".strip()
    verdict = {"status": "pass", "flagged": False, "score": None, "category": None, "reasons": [], "signals": []}
    if not moderation_enabled():
        return verdict
    if leaked_secret(text):
        verdict["reasons"].append("leaked_secret")
    score = await _score(text) if text else None
    verdict["score"] = score
    if score is not None and score >= _block_score():
        verdict["reasons"].append("harmful_model")
    if verdict["reasons"]:
        verdict["status"] = "block"
    elif score is None:
        verdict["status"] = "unavailable"
    else:
        verdict["flagged"] = score >= _flag_score()
    return verdict


def record(user_id: str, verdict: dict, source: str, resource_type: str, resource_id=None) -> None:
    """擋下、可疑（flagged）、檢查服務不在的才記；後台 audit 頁用 action=content_moderation 查。"""
    if verdict["status"] == "pass" and not verdict["flagged"]:
        return
    from core.audit import audit_log

    audit_log(
        action="content_moderation",
        user_id=user_id,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        metadata={
            "source": source,
            "status": verdict["status"],
            "flagged": verdict["flagged"],
            "score": verdict["score"],
            "category": verdict["category"],
            "reasons": verdict["reasons"],
        },
    )


async def risk_of(text: str) -> Optional[dict]:
    """被檢舉的內容有多危險（給後台排序，不擋任何東西）：模型的「該擋」機率。
    檢查服務不在、內容空回 None（那筆排在有分數的後面）。"""
    text = (text or "").strip()
    if not text:
        return None
    score = await _score(text)
    return None if score is None else {"score": float(score), "category": None}


async def service_health() -> dict:
    """後台看 moderation 容器狀態（不用進 VM）：連不到／載入中或載入失敗（帶錯誤訊息）／正常。"""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{_service_url()}/health")
        body = resp.json() if resp.content else {}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        return {"enabled": moderation_enabled(), "reachable": False, "ok": False, "error": str(exc)[:300]}
    return {
        "enabled": moderation_enabled(),
        "reachable": True,
        "ok": resp.status_code == 200 and bool(body.get("ok")),
        "error": (body.get("error") or None) if resp.status_code != 200 else None,
        "revision": body.get("revision"),
    }
