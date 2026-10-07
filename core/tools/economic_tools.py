"""
Economic Data Tools - 經濟數據工具

使用免費數據源：
- FRED API: 美國聯邦儲備經濟數據（免費，需申請 API key）
- yfinance: 市場指數

支援數據：
- GDP、CPI、失業率、利率等經濟指標
- VIX 恐慌指數
- 市場指數（S&P 500、道瓊、那斯達克）
"""

import asyncio

from langchain_core.tools import tool

# 市場指數代碼
MARKET_INDICES = {
    "SP500": "^GSPC",  # S&P 500
    "DOW": "^DJI",  # 道瓊工業指數
    "NASDAQ": "^IXIC",  # 那斯達克
    "VIX": "^VIX",  # 恐慌指數
    "RUSSELL2000": "^RUT",  # 羅素2000小盤股
    "PHLX_SEMICONDUCTOR": "^SOX",  # 費城半導體
}

INDEX_NAMES = {
    "^GSPC": "S&P 500 Index",
    "^DJI": "Dow Jones Industrial Average",
    "^IXIC": "NASDAQ Composite",
    "^VIX": "VIX Volatility Index",
    "^RUT": "Russell 2000",
    "^SOX": "PHLX Semiconductor Index",
}


@tool
def get_market_indices() -> dict:
    """獲取美股主要市場指數即時價格。

    包含：S&P 500、道瓊、那斯達克、VIX恐慌指數、費城半導體。
    """
    from datetime import datetime

    import pytz
    import yfinance as yf

    results = {}
    errors = []

    # 檢查是否為美股交易時間（美東時間 9:30-16:00）
    try:
        et_tz = pytz.timezone("America/New_York")
        now_et = datetime.now(et_tz)
        is_trading_hours = (
            now_et.weekday() < 5  # 週一到週五
            and 9 <= now_et.hour < 16  # 9:00 - 16:00
        )
        market_status = (
            "Open (trading)" if is_trading_hours else "Closed / not trading hours"
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        market_status = "Unknown"

    for name, symbol in MARKET_INDICES.items():
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            current = getattr(info, "last_price", None)
            prev = getattr(info, "previous_close", None)

            # 如果無法獲取當前價格，嘗試使用歷史數據
            if current is None:
                hist = ticker.history(period="5d")
                if not hist.empty:
                    current = float(hist["Close"].iloc[-1])
                    prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else None

            change_pct = None
            if current and prev and prev != 0:
                change_pct = round((current - prev) / prev * 100, 2)

            if current:
                results[name] = {
                    "name": INDEX_NAMES.get(symbol, symbol),
                    "symbol": symbol,
                    "price": round(current, 2),
                    "change_pct": change_pct,
                }
            else:
                errors.append(f"{name}: could not fetch price data")

        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            errors.append(f"{name}: {str(e)}")

    response = {
        "market_indices": results,
        "source": "yfinance",
        "market_status": market_status,
    }

    if errors:
        response["errors"] = errors
        response["note"] = (
            "Some indices could not be fetched, possibly outside trading hours"
        )

    return response


@tool
def get_vix_index() -> dict:
    """獲取 VIX 恐慌指數詳細資訊。

    VIX 是衡量市場預期波動率的指標：
    - VIX < 15: 市場平穩
    - VIX 15-25: 正常波動
    - VIX > 25: 市場恐慌
    - VIX > 40: 極度恐慌
    """
    try:
        import yfinance as yf

        ticker = yf.Ticker("^VIX")
        info = ticker.fast_info
        hist = ticker.history(period="30d")

        current = getattr(info, "last_price", None)
        prev = getattr(info, "previous_close", None)

        # 計算變化
        change_pct = None
        if current and prev and prev != 0:
            change_pct = round((current - prev) / prev * 100, 2)

        # 判斷市場情緒
        if current:
            if current < 15:
                sentiment = "🟢 Market Calm (low volatility)"
                interpretation = "Investor sentiment is optimistic; markets are expected to remain stable"
            elif current < 20:
                sentiment = "🟡 Normal Volatility"
                interpretation = "Markets are in a normal state"
            elif current < 25:
                sentiment = "🟠 Elevated Volatility"
                interpretation = "Markets face uncertainty; worth monitoring"
            elif current < 40:
                sentiment = "🔴 Market Fear"
                interpretation = "Investor concern is rising"
            else:
                sentiment = "🩸 Extreme Fear"
                interpretation = "Markets could see significant selling"
        else:
            sentiment = "Unknown"
            interpretation = ""

        # 計算30天高點低點
        high_30d = float(hist["High"].max()) if not hist.empty else None
        low_30d = float(hist["Low"].min()) if not hist.empty else None

        return {
            "name": "VIX Volatility Index",
            "symbol": "^VIX",
            "current": round(current, 2) if current else None,
            "change_pct": change_pct,
            "high_30d": round(high_30d, 2) if high_30d else None,
            "low_30d": round(low_30d, 2) if low_30d else None,
            "sentiment": sentiment,
            "interpretation": interpretation,
            "source": "yfinance",
        }

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": f"Failed to fetch VIX index: {str(e)}"}


@tool
def get_sp500_performance() -> dict:
    """獲取 S&P 500 指數詳細表現。

    包含價格、漲跌幅、技術位等信息。
    """
    try:
        import yfinance as yf

        ticker = yf.Ticker("^GSPC")
        info = ticker.fast_info
        hist = ticker.history(period="1y")

        current = getattr(info, "last_price", None)
        prev = getattr(info, "previous_close", None)

        # 計算各期間報酬
        returns = {}
        if not hist.empty:
            # 日報酬
            if prev and prev != 0:
                returns["1d"] = round((current - prev) / prev * 100, 2)

            # 週報酬
            if len(hist) >= 5:
                week_ago = hist["Close"].iloc[-5]
                returns["1w"] = round((current - week_ago) / week_ago * 100, 2)

            # 月報酬
            if len(hist) >= 20:
                month_ago = hist["Close"].iloc[-20]
                returns["1m"] = round((current - month_ago) / month_ago * 100, 2)

            # 年報酬
            if len(hist) >= 252:
                year_ago = hist["Close"].iloc[0]
                returns["1y"] = round((current - year_ago) / year_ago * 100, 2)

            # 高低點
            returns["52w_high"] = round(float(hist["High"].max()), 2)
            returns["52w_low"] = round(float(hist["Low"].min()), 2)

        return {
            "name": "S&P 500 Index",
            "symbol": "^GSPC",
            "current": round(current, 2) if current else None,
            "returns": returns,
            "source": "yfinance",
        }

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return {"error": f"Failed to fetch S&P 500 data: {str(e)}"}


@tool
def get_us_sector_performance() -> dict:
    """獲取美股 11 大板塊表現。

    使用 SPDR Sector ETF 查詢各板塊漲跌：
    - XLF: 金融
    - XLK: 科技
    - XLV: 醫療
    - XLE: 能源
    - XLY: 非必需消費
    - XLP: 必需消費
    - XLI: 工業
    - XLB: 原物料
    - XLRE: 房地产
    - XLC: 通訊
    - XLU: 公用事業
    """
    import yfinance as yf

    sectors = {
        "XLF": "Financials",
        "XLK": "Technology",
        "XLV": "Healthcare",
        "XLE": "Energy",
        "XLY": "Consumer Discretionary",
        "XLP": "Consumer Staples",
        "XLI": "Industrials",
        "XLB": "Materials",
        "XLRE": "Real Estate",
        "XLC": "Communication Services",
        "XLU": "Utilities",
    }

    results = []

    for symbol, name in sectors.items():
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            current = getattr(info, "last_price", None)
            prev = getattr(info, "previous_close", None)

            change_pct = None
            if current and prev and prev != 0:
                change_pct = round((current - prev) / prev * 100, 2)

            results.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "price": round(current, 2) if current else None,
                    "change_pct": change_pct,
                }
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            results.append(
                {"symbol": symbol, "name": name, "error": "Unable to fetch data"}
            )

    # 按漲跌幅排序
    results = sorted(results, key=lambda x: x.get("change_pct") or -999, reverse=True)

    return {
        "sector_performance": results,
        "source": "yfinance (SPDR Sector ETFs)",
        "note": "Sector performance is based on ETF prices",
    }


@tool
def get_economic_calendar() -> dict:
    """未來 60 天的美國總經事件真日期：FOMC 利率決議、CPI、非農（NFP）。

    來源：Fed 官網行事曆＋BLS 公告排程（設 FRED_SERVICE_API_KEY 時改用 FRED）。
    回每筆的日期、代碼、名稱、距今天數；沒有的事件不要憑常識補。
    """
    from datetime import datetime, timedelta, timezone

    from core.market_events import macro_calendar

    names = {
        "fomc": "FOMC interest rate decision",
        "cpi": "US CPI (Consumer Price Index) release",
        "nfp": "US Employment Situation (Non-Farm Payrolls)",
    }
    today = datetime.now(timezone.utc).date()
    horizon_days = 60
    try:
        events = macro_calendar.upcoming_macro_events(today=today, days=horizon_days)
        known = macro_calendar.schedule_known_through(today=today)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"calendar source unavailable: {type(exc).__name__}"}
    result = {
        "as_of": today.isoformat(),
        "horizon_days": horizon_days,
        "events": [
            {
                "date": ev["date"].isoformat(),
                "code": ev["code"],
                "event": names.get(ev["code"], ev["code"]),
                "days_until": (ev["date"] - today).days,
                "importance": "Very High" if ev["code"] == "fomc" else "High",
            }
            for ev in events
        ],
        "data_source": "Federal Reserve calendar + BLS release schedule (FRED when configured)",
        "schedule_known_through": {code: d.isoformat() for code, d in known.items()},
    }
    # 查詢範圍超過官方已公布日期：明講「之後還沒公布」，否則 AI 會把缺口講成「沒有發布」
    gaps = macro_calendar.coverage_gaps(
        known, horizon_end=today + timedelta(days=horizon_days)
    )
    if gaps:
        result["coverage_note"] = (
            "Official release dates are only published through: "
            + "; ".join(f"{names.get(c, c)} until {known[c].isoformat()}" for c in gaps)
            + ". Events after those dates are not announced yet — a missing release "
            "does NOT mean there is none. Tell the user the schedule is not published yet."
        )
    return result
