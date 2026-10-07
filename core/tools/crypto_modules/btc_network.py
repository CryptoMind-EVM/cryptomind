"""比特幣網路狀態（mempool.space 公開 API，免金鑰）。

DANNY 2026-09-13 從 public-apis 清單挑的：手續費建議、mempool 壅塞、區塊高度、
難度調整進度、下一次減半（每 210,000 個區塊，直接由高度算）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import httpx
from langchain_core.tools import tool

from .common import get_cached_data, set_cached_data

logger = logging.getLogger(__name__)

BASE = "https://mempool.space/api"
TIMEOUT = 10.0
HALVING_INTERVAL = 210_000
BLOCK_SECONDS = 600
CACHE_KEY = "btc_network_status"
CACHE_TTL = 60


def _get(path: str) -> Any:
    resp = httpx.get(f"{BASE}{path}", timeout=TIMEOUT)
    resp.raise_for_status()
    text = resp.text.strip()
    return resp.json() if text.startswith(("{", "[")) else text


def halving_info(height: int) -> Dict[str, Any]:
    """純函式：目前高度 → 下一次減半高度、剩餘區塊、約幾天、減半後區塊獎勵。"""
    era = height // HALVING_INTERVAL
    next_height = (era + 1) * HALVING_INTERVAL
    remaining = next_height - height
    reward_after = 50 / (2 ** (era + 1))
    return {
        "next_halving_height": next_height,
        "blocks_remaining": remaining,
        "days_remaining_est": round(remaining * BLOCK_SECONDS / 86400, 1),
        "block_reward_after_btc": reward_after,
        "current_block_reward_btc": 50 / (2**era),
    }


def congestion_label(vsize_bytes: int) -> str:
    mb = vsize_bytes / 1_000_000
    if mb < 5:
        return "low"
    if mb < 40:
        return "moderate"
    return "high"


@tool("get_btc_network_status")
def get_btc_network_status() -> Dict:
    """比特幣網路即時狀態：建議手續費（sat/vB，快／半小時／一小時／經濟）、mempool 壅塞程度、
    最新區塊高度、難度調整進度、下一次減半還有幾個區塊。問「BTC 手續費貴不貴／現在轉幣好不好／
    減半什麼時候」用這個。免金鑰，資料來源 mempool.space。"""
    cached = get_cached_data(CACHE_KEY, CACHE_TTL)
    if cached:
        return cached
    try:
        fees = _get("/v1/fees/recommended")
        pool = _get("/mempool")
        height = int(_get("/blocks/tip/height"))
        try:
            diff = _get("/v1/difficulty-adjustment")
        except Exception:  # noqa: BLE001 — 次要資訊
            diff = {}
        vsize = int(pool.get("vsize") or 0)
        result = {
            "fees_sat_per_vb": {
                "fastest": fees.get("fastestFee"),
                "half_hour": fees.get("halfHourFee"),
                "hour": fees.get("hourFee"),
                "economy": fees.get("economyFee"),
                "minimum": fees.get("minimumFee"),
            },
            "mempool": {
                "tx_count": pool.get("count"),
                "vsize_mb": round(vsize / 1_000_000, 2),
                "congestion": congestion_label(vsize),
            },
            "block_height": height,
            "difficulty_adjustment": {
                "progress_percent": round(float(diff.get("progressPercent") or 0), 1),
                "estimated_change_percent": round(
                    float(diff.get("difficultyChange") or 0), 2
                ),
                "remaining_blocks": diff.get("remainingBlocks"),
            }
            if diff
            else None,
            "halving": halving_info(height),
            "source": "mempool.space",
        }
        set_cached_data(CACHE_KEY, result)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.info("[btc_network] failed: %s", type(exc).__name__)
        return {"error": "mempool.space is temporarily unavailable"}
