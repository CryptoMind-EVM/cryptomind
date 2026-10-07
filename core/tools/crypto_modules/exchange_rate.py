"""Exchange Rate Service — 統一帳本的多幣種匯率。

設計：docs/plans/2026-08-21-investment-journal-design.md（DANNY Approve）

匯率來源（2026-09-13 起都是「一串免金鑰來源＋靜態表墊底」，以前法幣對只要兩邊都在
靜態表就永遠用寫死的 31.5，加密貨幣只認 14 個幣）：
- 加密貨幣 → USD：CoinGecko（已知 id）→ OKX ticker → Binance ticker → Coinpaprika 搜尋
- 法幣 → 法幣：ExchangeRate-API → Currency-api（fawazahmed0，150+ 幣含 TWD）→ Frankfurter（ECB）→ 靜態表
- TON：ton_price.py（已有）

匯率凍結：record 時呼叫 get_exchange_rate()，結果存入 trade_journal
的 exchange_rate / converted_amount 欄位——歷史記錄不受之後匯率波動影響。

快取：60 秒（同一 process/Redis 內共用），避免每筆記錄都打 API。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# 快取 TTL（秒）
_RATE_CACHE_TTL = 60
_rate_cache: Dict[str, Tuple[float, datetime]] = {}

# 支援的法幣對（相對 USD）
_FIAT_RATES = {
    "TWD": 31.5,  # fallback 預設
    "USD": 1.0,
    "HKD": 7.8,
    "JPY": 150.0,
    "CNY": 7.25,
    "EUR": 0.92,
}

# 帳本「計價幣別」下拉提供的加密貨幣（UI 清單；估值本身現在任何交易所有掛牌的幣都查得到）
_CRYPTO_SYMBOLS = {
    "BTC",
    "ETH",
    "TON",
    "SOL",
    "BNB",
    "XRP",
    "DOGE",
    "USDT",
    "USDC",
    "ADA",
    "AVAX",
    "MATIC",
    "DOT",
    "LINK",
}
# 已知法幣代碼：在這裡的走外匯路徑，其他代碼一律先當加密貨幣查（查不到再試外匯）
_FIAT_CODES = {
    "USD",
    "TWD",
    "HKD",
    "JPY",
    "CNY",
    "EUR",
    "GBP",
    "KRW",
    "SGD",
    "AUD",
    "CAD",
    "CHF",
    "THB",
    "MYR",
    "INR",
    "IDR",
    "PHP",
    "VND",
    "NZD",
    "SEK",
    "NOK",
    "DKK",
    "PLN",
    "CZK",
    "HUF",
    "TRY",
    "ZAR",
    "BRL",
    "MXN",
    "ILS",
    "AED",
    "SAR",
    "RUB",
}
# Frankfurter（ECB 參考匯率）只有這些幣，沒有 TWD
_FRANKFURTER_CODES = {
    "USD",
    "EUR",
    "JPY",
    "GBP",
    "CHF",
    "CNY",
    "HKD",
    "KRW",
    "SGD",
    "AUD",
    "CAD",
    "NZD",
    "INR",
    "IDR",
    "MYR",
    "PHP",
    "THB",
    "ILS",
    "ZAR",
    "BRL",
    "MXN",
    "TRY",
    "NOK",
    "SEK",
    "DKK",
    "PLN",
    "CZK",
    "HUF",
    "RON",
    "BGN",
    "ISK",
}
_HTTP_TIMEOUT = 6.0

# Lucide icon for each category
CATEGORY_ICONS = {
    "food": "utensils",
    "transport": "car",
    "housing": "home",
    "shopping": "shopping-bag",
    "entertainment": "film",
    "medical": "heart-pulse",
    "education": "book-open",
    "investment": "trending-up",
    "income": "banknote",
    "other": "package",
}

# 類別的 i18n key prefix
CATEGORY_I18N_PREFIX = "journal.category"


def get_exchange_rate(
    currency: str,
    base_currency: str = "TWD",
) -> Optional[float]:
    """取得 currency → base_currency 的匯率（1 unit = ? base）。

    Returns:
        匯率浮點數，或 None（無法取得）
    """
    currency = currency.strip().upper()
    base = base_currency.strip().upper()

    if currency == base:
        return 1.0

    cache_key = f"{currency}:{base}"
    cached = _rate_cache.get(cache_key)
    if (
        cached
        and (datetime.now(timezone.utc) - cached[1]).total_seconds() < _RATE_CACHE_TTL
    ):
        return cached[0]

    rate = _fetch_rate(currency, base)
    if rate and rate > 0:
        _rate_cache[cache_key] = (rate, datetime.now(timezone.utc))
    return rate


def _fetch_rate(currency: str, base: str) -> Optional[float]:
    """依幣種類型取得匯率：已知法幣走外匯，其他先當加密貨幣，查不到再試外匯。"""
    if currency in _FIAT_CODES:
        return _fiat_pair(currency, base)
    usd_rate = _get_crypto_usd(currency)
    if usd_rate:
        if base == "USD":
            return usd_rate
        base_rate = _get_usd_to_fiat(base)
        return usd_rate * base_rate if base_rate else None
    return _fiat_pair(currency, base)


def _fiat_pair(from_cur: str, to_cur: str) -> Optional[float]:
    """法幣對：即時來源優先，全掛才用靜態表。"""
    if from_cur == to_cur:
        return 1.0
    live = _fetch_forex_rate(from_cur, to_cur)
    if live:
        return live
    if from_cur in _FIAT_RATES and to_cur in _FIAT_RATES:
        return _FIAT_RATES[to_cur] / _FIAT_RATES[from_cur]
    return None


def _get_crypto_usd(symbol: str) -> Optional[float]:
    """加密貨幣 → USD：CoinGecko（已知 id）→ OKX → Binance → Coinpaprika，誰先回就用誰。"""
    for source in (
        _crypto_usd_coingecko,
        _crypto_usd_okx,
        _crypto_usd_binance,
        _crypto_usd_coinpaprika,
    ):
        try:
            price = source(symbol)
        except Exception as exc:  # noqa: BLE001 — 單一來源掛掉換下一個
            logger.debug(
                "[ExchangeRate] %s %s failed: %s", source.__name__, symbol, exc
            )
            continue
        if price and price > 0:
            return float(price)
    return None


def _crypto_usd_coingecko(symbol: str) -> Optional[float]:
    if symbol.upper() not in _COINGECKO_IDS:
        return None  # 不猜 id（symbol.lower() 幾乎都不是 CoinGecko id）
    import httpx  # noqa: PLC0415

    resp = httpx.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": _COINGECKO_IDS[symbol.upper()], "vs_currencies": "usd"},
        timeout=_HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        return None
    for row in resp.json().values():
        if "usd" in row:
            return float(row["usd"])
    return None


def _crypto_usd_okx(symbol: str) -> Optional[float]:
    """OKX 現貨 ticker（USDT 計價 ≈ USD）。"""
    import httpx  # noqa: PLC0415

    resp = httpx.get(
        "https://www.okx.com/api/v5/market/ticker",
        params={"instId": f"{symbol.upper()}-USDT"},
        timeout=_HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        return None
    rows = resp.json().get("data") or []
    return float(rows[0]["last"]) if rows and rows[0].get("last") else None


def _crypto_usd_binance(symbol: str) -> Optional[float]:
    import httpx  # noqa: PLC0415

    resp = httpx.get(
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": f"{symbol.upper()}USDT"},
        timeout=_HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        return None
    price = resp.json().get("price")
    return float(price) if price else None


def _crypto_usd_coinpaprika(symbol: str) -> Optional[float]:
    """Coinpaprika 免金鑰：search（注意路徑要有尾斜線）→ tickers/{id}。"""
    import httpx  # noqa: PLC0415

    resp = httpx.get(
        "https://api.coinpaprika.com/v1/search/",
        params={"q": symbol.upper(), "c": "currencies", "limit": 10},
        timeout=_HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        return None
    coins = [
        c
        for c in resp.json().get("currencies") or []
        if str(c.get("symbol", "")).upper() == symbol.upper()
    ]
    if not coins:
        return None
    coins.sort(key=lambda c: c.get("rank") or 10**9)
    ticker = httpx.get(
        f"https://api.coinpaprika.com/v1/tickers/{coins[0]['id']}",
        timeout=_HTTP_TIMEOUT,
    )
    if ticker.status_code != 200:
        return None
    price = ((ticker.json().get("quotes") or {}).get("USD") or {}).get("price")
    return float(price) if price else None


def _get_usd_to_fiat(fiat: str) -> Optional[float]:
    """USD → 法幣（即時優先，靜態表墊底）。"""
    if fiat == "USD":
        return 1.0
    return _fiat_pair("USD", fiat)


def _fetch_forex_rate(from_cur: str, to_cur: str) -> Optional[float]:
    """即時外匯：ExchangeRate-API → Currency-api → Frankfurter；全部失敗回 None（呼叫端再退靜態表）。"""
    for source in (_forex_exchangerate_api, _forex_currency_api, _forex_frankfurter):
        try:
            rate = source(from_cur, to_cur)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[ExchangeRate] %s %s→%s failed: %s",
                source.__name__,
                from_cur,
                to_cur,
                exc,
            )
            continue
        if rate and rate > 0:
            return float(rate)
    return None


def _forex_exchangerate_api(from_cur: str, to_cur: str) -> Optional[float]:
    import httpx  # noqa: PLC0415

    resp = httpx.get(
        f"https://api.exchangerate-api.com/v4/latest/{from_cur}", timeout=_HTTP_TIMEOUT
    )
    if resp.status_code != 200:
        return None
    rates = resp.json().get("rates") or {}
    return float(rates[to_cur]) if to_cur in rates else None


_CURRENCY_API_HOSTS = (
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies",
    "https://latest.currency-api.pages.dev/v1/currencies",
)


def _forex_currency_api(from_cur: str, to_cur: str) -> Optional[float]:
    """fawazahmed0/currency-api：每日更新、150+ 法幣含 TWD、免金鑰；兩個主機互為備援。"""
    import httpx  # noqa: PLC0415

    key_from, key_to = from_cur.lower(), to_cur.lower()
    for host in _CURRENCY_API_HOSTS:
        try:
            resp = httpx.get(
                f"{host}/{key_from}.json", timeout=_HTTP_TIMEOUT, follow_redirects=True
            )
        except Exception:  # noqa: BLE001
            continue
        if resp.status_code != 200:
            continue
        rates = resp.json().get(key_from) or {}
        if key_to in rates:
            return float(rates[key_to])
        return None
    return None


def _forex_frankfurter(from_cur: str, to_cur: str) -> Optional[float]:
    """Frankfurter（ECB）：只有 30 幾種幣，沒有 TWD；當前兩個都掛時的備援。"""
    if from_cur not in _FRANKFURTER_CODES or to_cur not in _FRANKFURTER_CODES:
        return None
    import httpx  # noqa: PLC0415

    resp = httpx.get(
        "https://api.frankfurter.app/latest",
        params={"from": from_cur, "to": to_cur},
        timeout=_HTTP_TIMEOUT,
        follow_redirects=True,
    )
    if resp.status_code != 200:
        return None
    rates = resp.json().get("rates") or {}
    return float(rates[to_cur]) if to_cur in rates else None


_COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "TON": "the-open-network",
    "GRAM": "the-open-network",  # Toncoin 2026-06 改名 Gram，CoinGecko id 沒變
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "DOGE": "dogecoin",
    "USDT": "tether",
    "USDC": "usd-coin",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "MATIC": "matic-network",
    "POL": "polygon-ecosystem-token",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "ARB": "arbitrum",
    "OP": "optimism",
    "SUI": "sui",
    "APT": "aptos",
    "TRX": "tron",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "ATOM": "cosmos",
    "UNI": "uniswap",
    "AAVE": "aave",
    "PEPE": "pepe",
    "SHIB": "shiba-inu",
    "NEAR": "near",
    "TIA": "celestia",
    "STRK": "starknet",
    "ZRO": "layerzero",
    "AERO": "aerodrome-finance",
    "WBTC": "wrapped-bitcoin",
    "WETH": "weth",
    "DAI": "dai",
}


def _coingecko_id(symbol: str) -> str:
    """symbol → CoinGecko id（舊呼叫端相容；查不到回原 symbol 小寫）。"""
    return _COINGECKO_IDS.get(symbol.upper(), symbol.lower())


def infer_category(text: str, entry_type: str = "expense") -> str:
    """從使用者輸入推斷類別。

    Args:
        text: 使用者的原始輸入（如「午餐 250」「加油 1500」）
        entry_type: trade/expense/income
    Returns:
        類別 id（expense: food/transport/.../other；
        income: salary/bonus/investment_income/other；trade: investment）
    """
    if entry_type == "trade":
        return "investment"
    if entry_type == "income":
        # UX 第二輪：收入類別關鍵詞映射（此前 hardcode "income"）
        text_lower = text.lower()
        income_rules = [
            ("salary", ["薪水", "薪資", "工资", "月薪", "salary", "payroll"]),
            ("bonus", ["獎金", "奖金", "年終", "年终", "bonus", "紅利", "红利"]),
            (
                "investment_income",
                [
                    "股利",
                    "配息",
                    "利息",
                    "投資收益",
                    "投资收益",
                    "dividend",
                    "interest",
                    "staking",
                ],
            ),
        ]
        for cat, keywords in income_rules:
            if any(k in text_lower for k in keywords):
                return cat
        return "other"

    text_lower = text.lower()

    # 關鍵詞 → 類別映射（按比對優先序）
    rules = [
        (
            "food",
            [
                "午餐",
                "晚餐",
                "早餐",
                "吃",
                "餐",
                "咖啡",
                "饮料",
                "喝",
                "lunch",
                "dinner",
                "breakfast",
                "coffee",
                "food",
                "eat",
                "午",
                "晚",
                "饭",
                "餐廳",
                "餐厅",
            ],
        ),
        (
            "transport",
            [
                "加油",
                "油錢",
                "停車",
                "捷運",
                "公車",
                "計程車",
                " uber",
                "gas",
                "parking",
                "metro",
                "bus",
                "taxi",
                "uber",
                "transport",
                "油",
                "车",
                "車",
            ],
        ),
        (
            "housing",
            [
                "房租",
                "水電",
                "電費",
                "瓦斯",
                "網路",
                "管理費",
                "rent",
                "utility",
                "utilities",
                "internet",
                "mortgage",
                "房",
                "住",
            ],
        ),
        (
            "shopping",
            [
                "買",
                "購物",
                "網購",
                "衣服",
                "3c",
                "電子",
                "buy",
                "shop",
                "clothes",
                "electronics",
                "online",
            ],
        ),
        (
            "entertainment",
            [
                "電影",
                "遊戲",
                "旅遊",
                "唱歌",
                "看",
                "玩",
                "movie",
                "game",
                "travel",
                "entertainment",
                "fun",
            ],
        ),
        (
            "medical",
            [
                "看診",
                "藥",
                "醫院",
                "感冒",
                "doctor",
                "medicine",
                "hospital",
                "medical",
                "pharmacy",
            ],
        ),
        (
            "education",
            [
                "課程",
                "書",
                "學",
                "補習",
                "course",
                "book",
                "study",
                "education",
                "tuition",
            ],
        ),
    ]

    for category, keywords in rules:
        for kw in keywords:
            if kw in text_lower:
                return category

    return "other"


def format_converted(
    amount: float,
    currency: str,
    base_currency: str = "TWD",
    exchange_rate: Optional[float] = None,
) -> str:
    """格式化換算結果（顯示用）。"""
    rate = exchange_rate or get_exchange_rate(currency, base_currency)
    if not rate:
        return f"{amount:g} {currency}"
    converted = amount * rate
    symbols = {
        "TWD": "NT$",
        "USD": "$",
        "HKD": "HK$",
        "JPY": "¥",
        "CNY": "¥",
        "EUR": "€",
    }
    sym = symbols.get(base_currency, base_currency)
    return f"{amount:g} {currency} (~{sym}{converted:,.0f})"
