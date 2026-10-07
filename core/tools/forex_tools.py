"""
Forex Tools - 外匯數據工具

使用免費數據源：
- yfinance: 貨幣對價格
- FRED API: 央行利率（免費）

支援貨幣對：
- USD/TWD 美元/台幣
- USD/JPY 美元/日圓
- EUR/USD 歐元/美元
- GBP/USD 英鎊/美元
- USD/CNY 美元/人民幣
"""

import asyncio

from langchain_core.tools import tool

# 主要貨幣對代碼
CURRENCY_PAIRS = {
    "USD_TWD": "TWD=X",  # 美元/台幣
    "USD_JPY": "JPY=X",  # 美元/日圓
    "EUR_USD": "EURUSD=X",  # 歐元/美元
    "GBP_USD": "GBPUSD=X",  # 英鎊/美元
    "USD_CNY": "CNY=X",  # 美元/人民幣
    "USD_KRW": "KRW=X",  # 美元/韓元
    "AUD_USD": "AUDUSD=X",  # 澳幣/美元
    "USD_SGD": "SGD=X",  # 美元/新加坡幣
}

CURRENCY_NAMES = {
    "TWD=X": "US Dollar / New Taiwan Dollar (USD/TWD)",
    "JPY=X": "US Dollar / Japanese Yen (USD/JPY)",
    "EURUSD=X": "Euro / US Dollar (EUR/USD)",
    "GBPUSD=X": "British Pound / US Dollar (GBP/USD)",
    "CNY=X": "US Dollar / Chinese Yuan (USD/CNY)",
    "KRW=X": "US Dollar / South Korean Won (USD/KRW)",
    "AUDUSD=X": "Australian Dollar / US Dollar (AUD/USD)",
    "SGD=X": "US Dollar / Singapore Dollar (USD/SGD)",
}


@tool
def get_forex_rate(pair: str) -> dict:
    """查詢外匯即時匯率。

    支援的貨幣對：
    - USD/TWD: 美元/台幣
    - USD/JPY: 美元/日圓
    - EUR/USD: 歐元/美元
    - GBP/USD: 英鎊/美元
    - USD/CNY: 美元/人民幣
    - USD/KRW: 美元/韓元
    - AUD/USD: 澳幣/美元
    - USD/SGD: 美元/新加坡幣

    資料來源：yfinance（免費）

    Args:
        pair: 貨幣對（如 USD/TWD、EUR/USD）
    """
    try:
        import yfinance as yf

        # 轉換格式：接受 USD/TWD、USD_TWD、USDTWD 等常見寫法
        pair_key = pair.upper().replace("/", "_")
        if pair_key in CURRENCY_PAIRS:
            symbol = CURRENCY_PAIRS[pair_key]
        elif pair.upper() in CURRENCY_PAIRS.values():
            symbol = pair.upper()
        elif len(pair_key) == 6 and pair_key[:3] + "_" + pair_key[3:] in CURRENCY_PAIRS:
            pair_key = pair_key[:3] + "_" + pair_key[3:]
            symbol = CURRENCY_PAIRS[pair_key]
        else:
            available = ", ".join(CURRENCY_PAIRS.keys())
            return {"error": f"Unsupported currency pair '{pair}'. Supported pairs: {available}"}

        ticker = yf.Ticker(symbol)
        info = ticker.fast_info

        current_rate = getattr(info, "last_price", None)
        prev_close = getattr(info, "previous_close", None)

        change_pct = None
        if current_rate and prev_close and prev_close != 0:
            change_pct = round((current_rate - prev_close) / prev_close * 100, 4)

        return {
            "pair": pair.upper(),
            "name": CURRENCY_NAMES.get(symbol, symbol),
            "symbol": symbol,
            "current_rate": round(current_rate, 4) if current_rate else None,
            "previous_close": round(prev_close, 4) if prev_close else None,
            "change_pct": change_pct,
            "source": "yfinance",
        }

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": f"Failed to fetch the exchange rate: {str(e)}"}


@tool
def get_all_forex_rates() -> dict:
    """獲取所有主要貨幣對的即時匯率一覽表。

    包含：美元/台幣、美元/日圓、歐元/美元、英鎊/美元等。
    """
    import yfinance as yf

    results = {}

    for pair_name, symbol in CURRENCY_PAIRS.items():
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            current = getattr(info, "last_price", None)
            prev = getattr(info, "previous_close", None)

            change_pct = None
            if current and prev and prev != 0:
                change_pct = round((current - prev) / prev * 100, 4)

            results[pair_name] = {
                "name": CURRENCY_NAMES.get(symbol, symbol),
                "rate": round(current, 4) if current else None,
                "change_pct": change_pct,
            }
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            results[pair_name] = {"error": "Unable to fetch data"}

    return {"forex_rates": results, "source": "yfinance", "note": "Rates may be slightly delayed"}


@tool
def get_usd_twd_rate() -> dict:
    """查詢美元/台幣即時匯率。

    專門用於快速查詢台幣匯率。
    """
    import yfinance as yf

    symbol = "TWD=X"
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info

        current_rate = getattr(info, "last_price", None)
        prev_close = getattr(info, "previous_close", None)

        change_pct = None
        if current_rate and prev_close and prev_close != 0:
            change_pct = round((current_rate - prev_close) / prev_close * 100, 4)

        return {
            "pair": "USD/TWD",
            "name": CURRENCY_NAMES.get(symbol, symbol),
            "symbol": symbol,
            "current_rate": round(current_rate, 4) if current_rate else None,
            "previous_close": round(prev_close, 4) if prev_close else None,
            "change_pct": change_pct,
            "source": "yfinance",
        }

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": f"Failed to fetch the USD/TWD rate: {str(e)}"}


@tool
def get_central_bank_rates() -> dict:
    """獲取主要央行利率。

    包含：美國聯準會(Fed)、歐洲央行(ECB)、日本央行(BOJ)、台灣央行。

    注意：此功能需使用者自帶 FRED API 金鑰（BYOK）。
    若未設定金鑰，將返回提示訊息。
    """
    from core.tools.key_resolver import resolve_tool_key

    # BYOK：使用者自帶 FRED 金鑰（不提供官方 fallback）
    fred_api_key = resolve_tool_key("fred", official_env=None)

    if not fred_api_key:
        return {
            "error": "This feature requires your own FRED API key.",
            "hint": "Add your FRED key in the tool settings (free sign-up: https://fredaccount.stlouisfed.org/apikeys)",
            "alternative": "You can also use the web_search tool to search 'Federal Reserve interest rate' for the latest rate information",
        }

    try:
        import httpx

        # 使用 FRED API 獲取聯邦基金利率
        url = f"https://api.stlouisfed.org/fred/series/observations?series_id=DFEDTARU&api_key={fred_api_key}&file_type=json&observation_start=2024-01-01"
        resp = httpx.get(url, timeout=10)

        results = {}

        if resp.status_code == 200:
            data = resp.json()
            observations = data.get("observations", [])
            if observations:
                latest = observations[-1]
                results["US Fed"] = {
                    "rate": f"{float(latest.get('value', 0)):.2f}%",
                    "date": latest.get("date", ""),
                    "note": "Upper bound of the federal funds rate target range",
                }

        # 其他央行利率（這些需要其他 API 或數據源）
        results["Notes"] = {
            "note": "ECB, BOJ, and Taiwan central bank rates require other data sources",
            "suggestion": "Use the web_search tool to look these up",
        }

        return {"central_bank_rates": results, "source": "FRED API"}

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": f"Failed to fetch central bank rates: {str(e)}"}
