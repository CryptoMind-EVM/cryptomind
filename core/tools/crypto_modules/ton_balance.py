"""
TON 錢包餘額查詢工具

查詢 TON 區塊鏈上某地址的 TON 餘額（與活躍狀態）。唯讀、低風險——
TON 地址與其餘額是公開鏈上資料，查詢不涉及隱私邊界（不同於 ETH 私鑰
操作）。沿用既有 toncenter API（與登入驗證、付款驗證同一個 endpoint）。

設計決策：
  - 接受 address 參數（與既有 get_eth_balance 模式一致），讓 agent 能
    回答「我錢包有多少 TON」（使用者貼自己地址）或「這個地址有多少」。
  - 標 low-risk：純讀取公開鏈上資料，不碰私鑰、不簽章、不轉帳。
  - M3 的 swap 執行會另外處理「只查自己綁定地址」（需 session context）。
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from langchain_core.tools import tool

from core.config import TONCENTER_API_BASE, TONCENTER_API_KEY
from core.onchain.addresses import is_ton_address

logger = logging.getLogger(__name__)

# 1 TON = 1e9 nanoTON（TON 的最小單位）。
_NANO_PER_TON = 1_000_000_000


@tool
async def get_ton_balance(address: str = "") -> dict:
    """查詢 TON 錢包地址的 TON 餘額與鏈上狀態。

    查「我錢包」時可不傳 address——自動以登入身份查詢（TON Connect 登入的
    user_id 即錢包地址，見 api/routers/user.py:251 ton_uid = body.address）。
    也可顯式傳 TON 地址查其他錢包。

    回傳餘額（TON 與 nanoTON）、地址狀態（active / uninitialized / frozen）。
    唯讀，不碰私鑰。

    Args:
        address: TON 地址（EQ... 或 UQ... 開頭）。省略 = 查登入使用者的錢包。
    """
    from core.tools.key_resolver import get_current_user_id

    # 顯式 address 優先；缺省時用登入身份。兩者都必須是合法 TON 地址
    # （friendly 或 raw 皆可）——登入身份若非 TON 錢包（如測試帳號），會被下方驗證擋下。
    addr = (address or "").strip() or (get_current_user_id() or "").strip()
    if not addr:
        return {"error": "No address provided and not logged in; cannot query balance"}
    if not is_ton_address(addr):
        return {"error": "Invalid TON address"}

    # 金鑰走 header：放 query string 會被 httpx 的 INFO log 連 URL 一起記下來
    headers = {"X-API-Key": TONCENTER_API_KEY} if TONCENTER_API_KEY else {}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{TONCENTER_API_BASE}/getAddressInformation",
                params={"address": addr},
                headers=headers,
            )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except httpx.HTTPError as exc:
        logger.error("toncenter getAddressInformation failed: %s", exc)
        return {"error": "Unable to connect to the TON network"}

    if resp.status_code != 200:
        logger.error("toncenter returned %s: %s", resp.status_code, resp.text[:200])
        return {"error": f"TON network error (HTTP {resp.status_code})"}

    body = resp.json()
    if not body.get("ok"):
        return {"error": "Unexpected response from the TON network"}

    result = body.get("result", {})
    balance_nano = int(result.get("balance", 0))
    balance_ton = balance_nano / _NANO_PER_TON

    return {
        "address": addr,
        "balance_ton": round(balance_ton, 4),
        "balance_nano": str(balance_nano),
        "state": result.get("state", "unknown"),  # active / uninitialized / frozen
        "source": "toncenter",
    }
