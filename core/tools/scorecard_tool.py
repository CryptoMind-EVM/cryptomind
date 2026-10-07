"""agent 工具：本人的判斷成績（設計 §6）。只回本人資料，沒登入回錯。"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


@tool("get_my_scorecard")
def get_my_scorecard() -> Dict[str, Any]:
    """查登入使用者自己的「判斷成績」：帳本每筆交易當成判斷，到期（30／90 天）用收盤價對答案、
    扣掉同市場大盤後的命中率與平均超額報酬，分「鏈上驗證／自報」與市場。不滿 10 筆的組只有筆數。
    使用者問「我最近判斷準嗎、我的操作成績、我跟大盤比如何」用這個。只回本人，不回別人。"""
    from core.scorecard.service import scorecard_for_user
    from core.tools.key_resolver import get_current_user_id

    user_id = (get_current_user_id() or "").strip()
    if not user_id:
        return {
            "error": "Not logged in; scorecard is only available for the signed-in user"
        }
    try:
        data = scorecard_for_user(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[get_my_scorecard] failed for %s: %s", user_id[:12], exc)
        return {"error": "Failed to load scorecard"}
    recent = [
        {
            "symbol": r["symbol"],
            "market": r["market"],
            "side": r["side"],
            "horizon_days": r["horizon_days"],
            "entry_date": r["entry_date"],
            "excess_pct": r["excess_pct"],
            "hit": r["hit"],
            "verification": r["verification"],
        }
        for r in data.get("recent", [])[:10]
    ]
    return {
        "headline": data["headline"],
        "groups": data["groups"],
        "counts": data["counts"],
        "min_sample": data["min_sample"],
        "recent": recent,
        "method": (
            "Each ledger trade is a timestamped call (buy=bullish, sell=bearish). At 30/90 days the "
            "close price is compared with entry; the same-period benchmark (0050/SPY/BTC…) is subtracted. "
            "hit = excess return > 0. Groups with fewer than min_sample trades show counts only."
        ),
    }


# ── record_call：明確喊單（v2，設計 §7）──────────────────────────────
# 與 record_entry 同一套 HITL：工具只「提案」（回 __needs_consent__ marker），
# 使用者在卡上核准後 claw_loop 才呼叫 core.scorecard.calls.create_call 寫入。

NEEDS_CONSENT_KEY = "__needs_consent__"


class RecordCallInput(BaseModel):
    symbol: str = Field(description="Ticker: BTC / ETH / 2330.TW / AAPL / 0700.HK")
    side: str = Field(
        description="bullish (expect it to rise) or bearish (expect it to fall)"
    )
    horizon_days: int = Field(
        default=30,
        ge=7,
        le=365,
        description="How many days until the call is judged (7–365)",
    )
    market: str = Field(
        default="",
        description="crypto / tw_stock / us_stock / hk_stock / jp_stock / kr_stock / cn_stock; blank = infer from symbol",
    )
    target_price: Optional[float] = Field(
        default=None, description="Optional target price the user mentioned"
    )
    note: str = Field(
        default="", description="The user's reasoning in one line (optional)"
    )


@tool("record_call", args_schema=RecordCallInput)
def record_call(
    symbol: str,
    side: str,
    horizon_days: int = 30,
    market: str = "",
    target_price: Optional[float] = None,
    note: str = "",
) -> str:
    """Propose to record the user's OWN explicit market call so it can be scored later.

    Use when the user states a directional view with a horizon about a specific asset —
    "I'm bullish on BTC for the next month", "2330 will drop within 3 months",
    "ETH to 5000 by year end". Ask (or infer from wording) the horizon; default 30 days.
    Do NOT record your own analysis as the user's call, and do NOT record vague talk
    without a direction. A confirmation card is shown; nothing is saved until the user
    approves. The entry price is the latest close (the user cannot set it), and after
    24 hours the call cannot be deleted — that is the point: calls are scored against
    the market at expiry and appear in the user's scorecard (get_my_scorecard).
    """
    from core.scorecard import calls
    from core.tools.key_resolver import get_current_user_id

    user_id = (get_current_user_id() or "").strip()
    if not user_id:
        return json.dumps(
            {
                "error": "Not logged in; calls can only be recorded for the signed-in user"
            }
        )
    try:
        sym, mkt = calls.normalize_symbol_market(symbol, market)
        sd = calls.normalize_side(side)
        h = calls.normalize_horizon(horizon_days)
    except calls.CallError as exc:
        return json.dumps({"error": str(exc)})
    marker: Dict[str, Any] = {
        NEEDS_CONSENT_KEY: True,
        "ts": time.time(),
        "kind": "judgment_call",
        "symbol": sym,
        "market": mkt,
        "side": sd,
        "direction": "bullish" if sd == "buy" else "bearish",
        "horizon_days": h,
        "target_price": float(target_price) if target_price else None,
        "note": (note or "")[:500],
    }
    marker["proposal_id"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in marker.items() if k != "ts"},
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()[:12]
    return json.dumps(marker, ensure_ascii=False)
