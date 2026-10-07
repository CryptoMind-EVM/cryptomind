"""
Commodity Tools - 大宗商品數據工具

使用免費數據源：
- yfinance: 黃金、石油、白銀 ETF
- FRED API: 聯邦儲備經濟數據（免費）

支援商品：
- 黃金 (Gold): GLD ETF
- 白銀 (Silver): SLV ETF
- 石油 (Oil): USO ETF / CL 原油期貨
- 天然氣 (Natural Gas): UNG ETF
- 銅 (Copper): CPER ETF
"""

import asyncio

from langchain_core.tools import tool

# ============================================
# 商品 ETF 代碼對照表 (yfinance)
# ============================================
COMMODITY_ETFS = {
    "gold": {"symbol": "GLD", "name": "Gold ETF", "description": "SPDR Gold Shares"},
    "silver": {
        "symbol": "SLV",
        "name": "Silver ETF",
        "description": "iShares Silver Trust",
    },
    "oil": {
        "symbol": "USO",
        "name": "Crude Oil ETF",
        "description": "United States Oil Fund",
    },
    "natural_gas": {
        "symbol": "UNG",
        "name": "Natural Gas ETF",
        "description": "United States Natural Gas Fund",
    },
    "copper": {"symbol": "CPER", "name": "Copper ETF", "description": "Copper Path ETN"},
    "wheat": {
        "symbol": "WEAT",
        "name": "Wheat ETF",
        "description": "Teucrium Wheat Fund",
    },
    "corn": {"symbol": "CORN", "name": "Corn ETF", "description": "Teucrium Corn Fund"},
    "soybean": {
        "symbol": "SOYB",
        "name": "Soybean ETF",
        "description": "Teucrium Soybean Fund",
    },
}

# 期貨代碼
FUTURES_SYMBOLS = {
    "crude_oil": "CL=F",  # WTI 原油期貨
    "brent_oil": "BZ=F",  # 布蘭特原油期貨
    "gold_futures": "GC=F",  # 黃金期貨
    "silver_futures": "SI=F",  # 白銀期貨
    "natural_gas_futures": "NG=F",  # 天然氣期貨
    "copper_futures": "HG=F",  # 銅期貨
}


@tool
def get_commodity_price(commodity: str) -> dict:
    """查詢大宗商品的即時價格。

    支援的商品：
    - gold: 黃金
    - silver: 白銀
    - oil: 石油/原油
    - natural_gas: 天然氣
    - copper: 銅
    - wheat: 小麥
    - corn: 玉米
    - soybean: 黃豆

    資料來源：yfinance（ETF 價格）

    Args:
        commodity: 商品名稱（英文）
    """
    commodity = commodity.lower().strip()

    if commodity not in COMMODITY_ETFS:
        available = ", ".join(COMMODITY_ETFS.keys())
        return {"error": f"Unsupported commodity '{commodity}'. Supported commodities: {available}"}

    try:
        import yfinance as yf

        etf_info = COMMODITY_ETFS[commodity]
        symbol = etf_info["symbol"]

        ticker = yf.Ticker(symbol)
        info = ticker.fast_info

        current_price = getattr(info, "last_price", None)
        prev_close = getattr(info, "previous_close", None)

        change_pct = None
        if current_price and prev_close and prev_close != 0:
            change_pct = round((current_price - prev_close) / prev_close * 100, 2)

        return {
            "commodity": commodity,
            "name": etf_info["name"],
            "description": etf_info["description"],
            "etf_symbol": symbol,
            "current_price": round(current_price, 2) if current_price else None,
            "previous_close": round(prev_close, 2) if prev_close else None,
            "change_pct": change_pct,
            "currency": "USD",
            "source": "yfinance ETF",
        }

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": f"Failed to fetch commodity price: {str(e)}"}


@tool
def get_commodity_futures_price(futures_type: str) -> dict:
    """查詢商品期貨的即時價格。

    支援的期貨：
    - crude_oil: WTI 原油期貨
    - brent_oil: 布蘭特原油期貨
    - gold: 黃金期貨
    - silver: 白銀期貨
    - natural_gas: 天然氣期貨
    - copper: 銅期貨

    資料來源：yfinance（期貨價格）

    Args:
        futures_type: 期貨類型
    """
    futures_type = futures_type.lower().strip()

    # 處理別名
    aliases = {
        "gold": "gold_futures",
        "silver": "silver_futures",
        "oil": "crude_oil",
        "crude": "crude_oil",
        "wti": "crude_oil",
        "brent": "brent_oil",
        "natgas": "natural_gas_futures",
        "natural_gas": "natural_gas_futures",
    }
    futures_type = aliases.get(futures_type, futures_type)

    if futures_type not in FUTURES_SYMBOLS:
        available = ", ".join(FUTURES_SYMBOLS.keys())
        return {"error": f"Unsupported futures type '{futures_type}'. Supported types: {available}"}

    try:
        import yfinance as yf

        symbol = FUTURES_SYMBOLS[futures_type]
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info

        current_price = getattr(info, "last_price", None)
        prev_close = getattr(info, "previous_close", None)

        change_pct = None
        if current_price and prev_close and prev_close != 0:
            change_pct = round((current_price - prev_close) / prev_close * 100, 2)

        # 期貨名稱對照
        futures_names = {
            "crude_oil": "WTI Crude Oil Futures",
            "brent_oil": "Brent Crude Oil Futures",
            "gold_futures": "Gold Futures",
            "silver_futures": "Silver Futures",
            "natural_gas_futures": "Natural Gas Futures",
            "copper_futures": "Copper Futures",
        }

        return {
            "futures_type": futures_type,
            "name": futures_names.get(futures_type, futures_type),
            "symbol": symbol,
            "current_price": round(current_price, 2) if current_price else None,
            "previous_close": round(prev_close, 2) if prev_close else None,
            "change_pct": change_pct,
            "currency": "USD",
            "source": "yfinance Futures",
        }

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": f"Failed to fetch futures price: {str(e)}"}


@tool
def get_all_commodities_prices() -> dict:
    """獲取所有主要大宗商品的即時價格一覽表。

    包含：黃金、白銀、原油、天然氣、銅
    """
    import yfinance as yf

    results = {}

    # 主要商品期貨
    main_futures = ["GC=F", "SI=F", "CL=F", "NG=F", "HG=F"]
    futures_names = {
        "GC=F": "Gold",
        "SI=F": "Silver",
        "CL=F": "WTI Crude",
        "NG=F": "Natural Gas",
        "HG=F": "Copper",
    }
    futures_units = {
        "GC=F": "USD/oz",
        "SI=F": "USD/oz",
        "CL=F": "USD/barrel",
        "NG=F": "USD/MMBtu",
        "HG=F": "USD/lb",
    }

    for symbol in main_futures:
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            current = getattr(info, "last_price", None)
            prev = getattr(info, "previous_close", None)

            change_pct = None
            if current and prev and prev != 0:
                change_pct = round((current - prev) / prev * 100, 2)

            results[futures_names[symbol]] = {
                "symbol": symbol,
                "price": round(current, 2) if current else None,
                "change_pct": change_pct,
                "unit": futures_units[symbol],
            }
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            results[futures_names[symbol]] = {"error": "Unable to fetch data"}

    return {"commodities": results, "source": "yfinance", "note": "Futures prices may be delayed"}


@tool
def get_gold_silver_ratio() -> dict:
    """獲取金銀比（Gold-Silver Ratio）。

    金銀比 = 黃金價格 / 白銀價格
    是重要的市場情緒指標：
    - 比率高（>80）：黃金相對昂貴，可能預示經濟不確定性
    - 比率低（<60）：白銀相對強勢，可能預示經濟好轉
    """
    import yfinance as yf

    try:
        gold_ticker = yf.Ticker("GC=F")
        silver_ticker = yf.Ticker("SI=F")

        gold_price = getattr(gold_ticker.fast_info, "last_price", None)
        silver_price = getattr(silver_ticker.fast_info, "last_price", None)

        if not gold_price or not silver_price:
            return {"error": "Unable to fetch gold/silver prices"}

        ratio = gold_price / silver_price

        # 判斷市場情緒
        if ratio > 80:
            sentiment = "Elevated (strong risk-off sentiment)"
            interpretation = "Gold is relatively expensive; investors lean toward safe havens"
        elif ratio < 60:
            sentiment = "Low (rising risk appetite)"
            interpretation = "Silver is relatively strong; industrial demand may be rising"
        else:
            sentiment = "Normal range"
            interpretation = "The gold/silver relationship is relatively balanced"

        return {
            "gold_price": round(gold_price, 2),
            "silver_price": round(silver_price, 2),
            "ratio": round(ratio, 2),
            "sentiment": sentiment,
            "interpretation": interpretation,
            "historical_range": "Typically ranges between 60-80",
            "source": "yfinance",
        }

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": f"Failed to calculate gold/silver ratio: {str(e)}"}


@tool
def get_oil_price_analysis() -> dict:
    """獲取原油價格綜合分析。

    包含 WTI 和布蘭特原油的價格比較。
    """
    import yfinance as yf

    try:
        wti_ticker = yf.Ticker("CL=F")
        brent_ticker = yf.Ticker("BZ=F")

        wti_price = getattr(wti_ticker.fast_info, "last_price", None)
        brent_price = getattr(brent_ticker.fast_info, "last_price", None)

        wti_prev = getattr(wti_ticker.fast_info, "previous_close", None)
        brent_prev = getattr(brent_ticker.fast_info, "previous_close", None)

        wti_change = None
        brent_change = None
        if wti_price and wti_prev:
            wti_change = round((wti_price - wti_prev) / wti_prev * 100, 2)
        if brent_price and brent_prev:
            brent_change = round((brent_price - brent_prev) / brent_prev * 100, 2)

        spread = None
        if wti_price and brent_price:
            spread = round(brent_price - wti_price, 2)

        return {
            "wti_crude": {
                "name": "WTI Crude Oil (US)",
                "price": round(wti_price, 2) if wti_price else None,
                "change_pct": wti_change,
                "unit": "USD/barrel",
            },
            "brent_crude": {
                "name": "Brent Crude Oil (North Sea)",
                "price": round(brent_price, 2) if brent_price else None,
                "change_pct": brent_change,
                "unit": "USD/barrel",
            },
            "spread": spread,
            "spread_note": "Brent is usually more expensive than WTI, reflecting transport and quality differences",
            "source": "yfinance",
        }

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": f"Failed to fetch oil prices: {str(e)}"}
