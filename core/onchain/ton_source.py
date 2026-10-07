"""TON 轉帳與餘額來源（TonAPI 公開端點，同步 httpx）。

TonAPI 事件裡的 sender／recipient 是 raw 格式（``0:hex``），登入身份是 friendly
（EQ…／UQ…），比對前先轉 raw（``addresses.ton_friendly_to_raw``）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from .addresses import ton_friendly_to_raw
from .transfers import Transfer

logger = logging.getLogger(__name__)

TONAPI = "https://tonapi.io/v2"
TIMEOUT = 15.0
EVENTS_LIMIT = 50
NANO = 10**9


def _get(path: str, params: Optional[dict] = None) -> Optional[dict]:
    try:
        resp = httpx.get(
            f"{TONAPI}{path}",
            params=params,
            timeout=TIMEOUT,
            headers={"Origin": "https://tonapi.io"},
        )
        if resp.status_code != 200:
            logger.info("[ton] %s -> HTTP %s", path, resp.status_code)
            return None
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.info("[ton] %s failed: %s", path, type(exc).__name__)
        return None


def fetch_ton_balances(address: str) -> List[Dict[str, Any]]:
    """[{symbol, amount, contract, usd}]：TON 原生＋jetton（TonAPI 已附 USD 估值）。"""
    out: List[Dict[str, Any]] = []
    acct = _get(f"/accounts/{address}")
    if acct:
        out.append(
            {
                "symbol": "TON",
                "amount": int(acct.get("balance") or 0) / NANO,
                "contract": None,
            }
        )
    jettons = _get(f"/accounts/{address}/jettons", {"currencies": "usd", "limit": 50})
    for raw in (jettons or {}).get("balances") or []:
        jet = raw.get("jetton") or {}
        symbol = str(jet.get("symbol") or "").strip()
        decimals = int(jet.get("decimals") or 9)
        try:
            amount = int(str(raw.get("balance") or "0")) / 10**decimals
        except ValueError:
            continue
        if not symbol or amount <= 0:
            continue
        usd = float(((raw.get("price") or {}).get("prices") or {}).get("USD") or 0)
        out.append(
            {
                "symbol": symbol,
                "amount": amount,
                "contract": jet.get("address"),
                "usd": usd * amount if usd else None,
                "verified": (jet.get("verification") or "") == "whitelist",
            }
        )
    return out


def fetch_ton_transfers(address: str, *, since_ts: int) -> List[Transfer]:
    """最近 EVENTS_LIMIT 個事件裡 ≥ since_ts 的 TonTransfer／JettonTransfer。

    跳過：進行中的事件、TonAPI 標記 scam 的事件、自己轉自己。
    手續費：事件的 ``extra``（負值＝為手續費付出的 nanoTON）只在轉出時計。
    """
    me = ton_friendly_to_raw(address)
    if not me:
        return []
    body = _get(f"/accounts/{address}/events", {"limit": EVENTS_LIMIT})
    out: List[Transfer] = []
    for ev in (body or {}).get("events") or []:
        ts = int(ev.get("timestamp") or 0)
        if ts < since_ts or ev.get("in_progress"):
            continue
        scam = bool(ev.get("is_scam"))
        extra = int(ev.get("extra") or 0)
        fee = (-extra / NANO) if extra < 0 else 0.0
        for idx, action in enumerate(ev.get("actions") or []):
            atype = action.get("type")
            if atype not in ("TonTransfer", "JettonTransfer"):
                continue
            payload = action.get(atype) or {}
            sender = ton_friendly_to_raw(
                (payload.get("sender") or {}).get("address") or ""
            )
            recipient = ton_friendly_to_raw(
                (payload.get("recipient") or {}).get("address") or ""
            )
            if sender == me and recipient == me:
                continue
            if me not in (sender, recipient):
                continue
            direction = "out" if sender == me else "in"
            if atype == "TonTransfer":
                symbol, contract = "TON", None
                amount = int(payload.get("amount") or 0) / NANO
            else:
                jet = payload.get("jetton") or {}
                symbol = str(jet.get("symbol") or "").strip()
                contract = jet.get("address")
                decimals = int(jet.get("decimals") or 9)
                amount = int(payload.get("amount") or 0) / 10**decimals
                if (jet.get("verification") or "") == "blacklist":
                    scam = True
            if not symbol or amount <= 0:
                continue
            out.append(
                Transfer(
                    chain="ton",
                    tx_hash=f"{ev.get('event_id')}:{idx}"
                    if idx
                    else str(ev.get("event_id") or ""),
                    timestamp=ts,
                    direction=direction,
                    symbol=symbol,
                    amount=amount,
                    counterparty=(recipient if direction == "out" else sender) or "",
                    contract=contract,
                    fee_native=fee if direction == "out" else 0.0,
                    native_symbol="TON",
                    is_scam=scam,
                    meta={"comment": payload.get("comment") or ""},
                )
            )
    return out
