"""
TON jetton 餘額查詢工具

查 TonAPI 取得某錢包地址的「所有 jetton 餘額」（含 USD/TON 估值）。
補齊 get_ton_balance 的缺口——後者只查原生 TON，查不到 USDt/NOT/DOGS
等 jetton。這個工具讓 agent 能回答「我錢包有哪些幣、各值多少錢」。

唯讀、低風險（同 get_ton_balance）：公開鏈上資料，不碰私鑰、不簽章。
資料源：TonAPI /v2/accounts/{addr}/jettons（免 key，與 ton_safety.py 同模式）。

設計：
  - address 可選：省略時自動用登入身份（TON Connect 登入的 user_id 即錢包地址，
    見 api/routers/user.py:251）。非 TON 帳號會被 EQ/UQ/0Q 驗證擋下。
  - 例外降級：TonAPI 斷線/逾時回 {"error": ...}，讓 agent 優雅回答。
  - 回傳結構化 list：symbol / name / balance（人類可讀）/ balance_raw（最小單位）/
    usd_value / ton_value / verification（官方驗證狀態，反詐騙訊號）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from langchain_core.tools import tool

from core.onchain.addresses import is_ton_address

logger = logging.getLogger(__name__)

_TONAPI_BASE = "https://tonapi.io"
# 單筆 jetton 餘額最多回 100 個（TonAPI 分頁上限；一般錢包遠低於此）。
_MAX_JETTONS = 100


@tool
async def get_ton_jetton_balances(address: str = "") -> dict:
    """查詢 TON 錢包持有的所有 jetton 代幣餘額（含 USD/TON 估值）。

    查「我錢包」時可不傳 address——自動用登入身份查詢；查其他錢包才傳
    TON 地址（EQ... 或 UQ... 開頭）。

    回傳每個 jetton 的：symbol（如 USDt）、名稱、餘額（人類可讀 + 最小單位）、
    USD 估值、TON 估值、官方驗證狀態（whitelist=官方驗證）。唯讀，不碰私鑰。

    Args:
        address: TON 地址（EQ... 或 UQ... 開頭）。省略 = 查登入使用者的錢包。
    """
    from core.tools.key_resolver import get_current_user_id

    # 顯式 address 優先；缺省時用登入身份（同 get_ton_balance 模式）。
    addr = (address or "").strip() or (get_current_user_id() or "").strip()
    if not addr:
        return {"error": "No address provided and not logged in; cannot query balances"}
    if not is_ton_address(addr):
        return {"error": "Invalid TON address"}

    url = (
        f"{_TONAPI_BASE}/v2/accounts/{addr}/jettons"
        f"?currencies=usd,ton&limit={_MAX_JETTONS}"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers={"Origin": "https://tonapi.io"})
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except httpx.HTTPError as exc:
        logger.warning("TonAPI jetton balances failed for %s: %s", addr[:20], exc)
        return {"error": "Unable to connect to the TON network (TonAPI unavailable)"}

    if resp.status_code == 400 or resp.status_code == 404:
        return {"error": "Invalid TON address or wallet does not exist"}
    if resp.status_code != 200:
        logger.warning(
            "TonAPI jetton balances returned %s: %s", resp.status_code, resp.text[:200]
        )
        return {"error": f"TON network temporarily unavailable (HTTP {resp.status_code})"}

    try:
        data = resp.json()
        raw_balances = data.get("balances", []) or []
    except (ValueError, AttributeError):
        logger.warning("TonAPI jetton balances bad JSON for %s", addr[:20])
        return {"error": "Unexpected response format from the TON network"}

    balances = [_format_balance(b) for b in raw_balances]
    return {
        "address": addr,
        "count": len(balances),
        "balances": balances,
        "source": "tonapi",
    }


def _format_balance(raw: dict[str, Any]) -> dict[str, Any]:
    """把 TonAPI 的單筆 jetton 餘額轉成結構化 dict。

    TonAPI 回傳結構（實測確認）：
      {
        "balance": "4280000000000",        # 最小單位字串
        "price": {"prices": {"USD": 0, "TON": 0}, ...},
        "jetton": {"name", "symbol", "decimals", "verification", ...},
        "wallet_address": {...},
      }
    """
    jetton = raw.get("jetton", {}) or {}
    price = (raw.get("price", {}) or {}).get("prices", {}) or {}
    decimals = int(jetton.get("decimals") or 0)
    balance_raw = str(raw.get("balance") or "0")
    try:
        balance_num = int(balance_raw)
    except (TypeError, ValueError):
        balance_num = 0

    # 人類可讀餘額：最小單位 / 10^decimals。
    human = balance_num / (10 ** decimals) if decimals > 0 else balance_num
    # 估值：USD / TON（TonAPI 已換算好；0 代表無價格資料）。
    usd = float(price.get("USD") or 0)
    ton = float(price.get("TON") or 0)

    return {
        "symbol": jetton.get("symbol") or "?",
        "name": jetton.get("name") or "",
        "balance": human,
        "balance_raw": balance_raw,
        "decimals": decimals,
        "usd_value": usd,
        "ton_value": ton,
        "verification": jetton.get("verification") or "unknown",
        "is_verified": (jetton.get("verification") or "") == "whitelist",
    }
