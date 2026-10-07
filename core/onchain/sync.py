"""綁定錢包的鏈上轉帳 → 帳本條目。

對應規則（portfolio tracker 慣例）：轉入＝買、轉出＝賣，數量＝鏈上數量，
價格＝同步當下市價（stable 幣 1），gas 進 fee；``source='onchain'``＋``chain``＋``tx_hash``
讓每天重跑冪等。只收查得到價格的資產（Base 上的空投垃圾幣沒價就跳過，記在結果裡）。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from core.i18n import t as _t

from . import evm_source, store, ton_source
from .addresses import short_address
from .holdings import usd_price
from .transfers import Transfer

logger = logging.getLogger(__name__)

DEFAULT_FIRST_LOOKBACK_DAYS = 3.0  # 第一次同步（RPC logs 路徑每天約 22 次 getLogs×2）
DEFAULT_LOOKBACK_DAYS = 1.0  # 之後每天 cron


def first_lookback_days() -> float:
    return float(
        os.getenv("ONCHAIN_SYNC_FIRST_LOOKBACK_DAYS", DEFAULT_FIRST_LOOKBACK_DAYS)
    )


def daily_lookback_days() -> float:
    return float(os.getenv("ONCHAIN_SYNC_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS))


def transfer_to_entry(
    transfer: Transfer, price_usd: float, language: str
) -> Dict[str, Any]:
    """Transfer → ``TradeJournalRepo.add_entry`` 的參數（純函式）。"""
    from datetime import datetime, timezone

    key = (
        "ui_messages.onchain.note_in"
        if transfer.direction == "in"
        else "ui_messages.onchain.note_out"
    )
    return {
        "entry_type": "trade",
        "symbol": transfer.symbol,
        "market": "crypto",
        "side": "buy" if transfer.direction == "in" else "sell",
        "quantity": float(transfer.amount),
        "price": float(price_usd),
        "currency": "USD",
        "fee": float(transfer.fee_native or 0),
        "fee_currency": transfer.native_symbol if transfer.fee_native else "",
        "traded_at": datetime.fromtimestamp(int(transfer.timestamp), tz=timezone.utc),
        "source": "onchain",
        "note": _t(
            key,
            language,
            chain=transfer.chain,
            counterparty=short_address(transfer.counterparty),
        ),
        "chain": transfer.chain,
        "tx_hash": transfer.tx_hash,
    }


def fetch_wallet_transfers(
    wallet: Dict[str, Any], *, since_ts: int, lookback_days: float
) -> tuple[List[Transfer], List[str]]:
    if wallet["chain"] == "ton":
        return ton_source.fetch_ton_transfers(wallet["address"], since_ts=since_ts), []
    return evm_source.fetch_evm_transfers(
        wallet["address"], since_ts=since_ts, lookback_days=lookback_days
    )


def sync_user(
    user_id: str,
    *,
    now_ts: Optional[int] = None,
    language: Optional[str] = None,
    repo=None,
    wallets: Optional[List[Dict[str, Any]]] = None,
    lookback_days: Optional[float] = None,
) -> Dict[str, Any]:
    """抓綁定錢包的轉帳、寫進帳本、記錄結果。單一錢包失敗不中斷其他。"""
    from core.orm.trade_journal_repo import get_journal_repo

    now_ts = int(now_ts or time.time())
    wallets = store.list_wallets(user_id) if wallets is None else wallets
    status = store.get_status(user_id)
    if lookback_days is None:
        lookback_days = (
            daily_lookback_days()
            if status.get("last_synced_at")
            else first_lookback_days()
        )
    since_ts = now_ts - int(lookback_days * 86400)
    language = language or store.user_language(user_id)
    repo = repo or get_journal_repo(user_id)
    result: Dict[str, Any] = {
        "wallets": len(wallets),
        "added": 0,
        "duplicates": 0,
        "skipped_unpriced": [],
        "skipped_scam": 0,
        "errors": 0,
        "notes": [],
        "lookback_days": lookback_days,
        "entries": [],
    }
    price_cache: Dict[str, Optional[float]] = {}
    for wallet in wallets:
        try:
            transfers, notes = fetch_wallet_transfers(
                wallet, since_ts=since_ts, lookback_days=lookback_days
            )
        except Exception as exc:  # noqa: BLE001 — 單一錢包來源掛掉不影響其他
            logger.info(
                "[onchain] fetch failed %s %s: %s",
                wallet["chain"],
                wallet["address"][:10],
                type(exc).__name__,
            )
            result["notes"].append(f"{wallet['chain']}:fetch:{type(exc).__name__}")
            result["errors"] += 1
            continue
        result["notes"].extend(notes)
        for tr in transfers:
            if tr.is_scam:
                result["skipped_scam"] += 1
                continue
            side = "buy" if tr.direction == "in" else "sell"
            if repo.has_onchain_entry(tr.chain, tr.tx_hash, tr.symbol, side):
                result["duplicates"] += 1
                continue
            price = usd_price(tr.symbol, price_cache)
            if price is None:
                if tr.symbol not in result["skipped_unpriced"]:
                    result["skipped_unpriced"].append(tr.symbol)
                continue
            res = repo.add_entry(**transfer_to_entry(tr, price, language))
            if res.get("ok"):
                result["added"] += 1
                result["entries"].append(
                    {
                        "symbol": tr.symbol,
                        "side": side,
                        "amount": tr.amount,
                        "chain": tr.chain,
                        "tx_hash": tr.tx_hash,
                    }
                )
            elif res.get("duplicate"):
                result["duplicates"] += 1
            else:
                result["errors"] += 1
    result["entries"] = result["entries"][:50]
    try:
        store.record_result(
            user_id, {k: v for k, v in result.items() if k != "entries"}
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[onchain] record result failed %s: %s", user_id, exc)
    return result
