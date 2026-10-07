"""已驗證持倉：綁定錢包的鏈上餘額（含 USD 估值）。錢包分頁、帳本卡、agent 工具共用。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from . import evm_source, ton_source
from .addresses import short_address
from .transfers import STABLECOINS, normalize_symbol

logger = logging.getLogger(__name__)

# 一個錢包最多列幾個資產（TON 上垃圾 jetton 很多；未驗證且無估值的排最後）
MAX_ASSETS_PER_WALLET = 12


def usd_price(symbol: str, cache: Dict[str, Optional[float]]) -> Optional[float]:
    """USD 價：stable 1；其他走既有匯率工具；查不到記 None（不重查）。"""
    sym = normalize_symbol(symbol)
    if sym in STABLECOINS:
        return 1.0
    if sym in cache:
        return cache[sym]
    try:
        from core.tools.crypto_modules.exchange_rate import get_exchange_rate

        rate = get_exchange_rate(sym, "USD")
        cache[sym] = float(rate) if rate and rate > 0 else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[holdings] price %s failed: %s", sym, type(exc).__name__)
        cache[sym] = None
    return cache[sym]


def collect_wallet_holdings(
    wallet: Dict[str, Any], *, price_cache: Optional[Dict[str, Optional[float]]] = None
) -> Dict[str, Any]:
    """一個錢包 → {chain, address, short, is_primary, assets[], usd_total, ok}。"""
    price_cache = {} if price_cache is None else price_cache
    chain, address = wallet["chain"], wallet["address"]
    assets: List[Dict[str, Any]] = []
    ok = True
    try:
        if chain == "ton":
            raw = ton_source.fetch_ton_balances(address)
            network = "ton"
        else:
            network = evm_source.platform_chain()
            raw = evm_source.fetch_evm_balances(address, chain=network)
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "[holdings] %s %s failed: %s", chain, address[:10], type(exc).__name__
        )
        raw, ok = [], False
    for item in raw:
        amount = float(item.get("amount") or 0)
        if amount <= 0:
            continue
        usd = item.get("usd")
        if usd is None:
            price = usd_price(item["symbol"], price_cache)
            usd = amount * price if price is not None else None
        assets.append(
            {
                "symbol": item["symbol"],
                "amount": amount,
                "usd": round(usd, 2) if usd is not None else None,
                "contract": item.get("contract"),
                "verified": item.get("verified", True),
            }
        )
    # 有估值的在前、金額大的在前；未估值的墊底並截斷
    assets.sort(key=lambda a: (a["usd"] is None, -(a["usd"] or 0), -a["amount"]))
    assets = assets[:MAX_ASSETS_PER_WALLET]
    return {
        "chain": chain,
        "network": network if ok else chain,
        "address": address,
        "short": short_address(address),
        "is_primary": bool(wallet.get("is_primary")),
        "assets": assets,
        "usd_total": round(sum(a["usd"] or 0 for a in assets), 2),
        "ok": ok,
    }


def collect_holdings(user_id: str) -> Dict[str, Any]:
    """全部綁定錢包的持倉＋合計。沒綁定錢包 → wallets=[]。"""
    from .store import list_wallets

    wallets = list_wallets(user_id)
    cache: Dict[str, Optional[float]] = {}
    rows = [collect_wallet_holdings(w, price_cache=cache) for w in wallets]
    by_symbol: Dict[str, Dict[str, Any]] = {}
    for w in rows:
        for a in w["assets"]:
            slot = by_symbol.setdefault(
                a["symbol"],
                {"symbol": a["symbol"], "amount": 0.0, "usd": 0.0, "priced": False},
            )
            slot["amount"] += a["amount"]
            if a["usd"] is not None:
                slot["usd"] += a["usd"]
                slot["priced"] = True
    totals = sorted(by_symbol.values(), key=lambda s: -s["usd"])
    return {
        "wallets": rows,
        "totals": [
            {**t, "usd": round(t["usd"], 2) if t["priced"] else None} for t in totals
        ],
        "usd_total": round(sum(w["usd_total"] for w in rows), 2),
    }
