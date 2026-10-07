"""啟動時預載完整 ticker 列表（背景 fire-and-forget）。

從免費公開來源下載各市場的完整 ticker + 公司名列表，攤平成 alias 格式
merge 進 multi_market_resolver 的本地映射表。下載在背景跑，不阻塞啟動。

來源：
- 台股：TWSE/TPEX OpenAPI（含中文名，~2000 檔）
- 美股：rreichel3/US-Stock-Symbols GitHub raw JSON（每晚自動更新，~7000 檔）
- 港股：HKEX（透過 yfinance search 系統或 EODHD，若可用）

設計原則：
- 每個來源獨立 try/except，失敗不阻塞其他市場
- 全程背景跑（asyncio.create_task from lifespan），絕不阻塞 lifespan yield
- 下載結果 merge 進 _LOCAL_ALIASES（透過 add_preloaded_aliases）
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def preload_ticker_lists() -> None:
    """啟動時背景預載完整 ticker 列表（fire-and-forget）。

    若 Redis 已有 preloaded alias（前一個 worker 或上次啟動填的），直接跳過
    下載——這是冷啟動加速的關鍵：多 worker 共享一份、重啟不重新下載。
    """
    # Fast-path：Redis 已有 → 跳過下載（第一個 worker 下載並填 Redis，後續都命中）
    try:
        from core.tools.multi_market_resolver import _alias_redis_exists

        if _alias_redis_exists():
            logger.info(
                "[ticker_preloader] Redis already has preloaded aliases, skipping download"
            )
            return
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        pass  # Redis 檢查失敗不阻塞，照常下載

    logger.info("[ticker_preloader] starting background preload...")
    total = 0

    # 台股（TWSE + TPEX，含中文名）
    total += await _preload_tw()
    # 美股（NASDAQ + NYSE + AMEX，從 GitHub raw）
    total += await _preload_us()
    # 港股（嘗試從 yfinance 抓 hot list）
    total += await _preload_hk()

    logger.info("[ticker_preloader] preload done: %d new aliases added", total)


async def _preload_tw() -> int:
    """台股全部（TWSE 上市 + TPEX 上櫃，含中文名）。"""
    try:
        from api.utils import run_sync
        from core.tools.multi_market_resolver import add_preloaded_aliases
        from core.tools.tw_symbol_resolver import TWSymbolResolver

        resolver = TWSymbolResolver()
        stock_list = await run_sync(resolver._get_stock_list)

        if not stock_list:
            logger.warning("[ticker_preloader] TW stock list empty")
            return 0

        entries = {}
        for item in stock_list:
            # TWSymbolResolver._get_stock_list 回的是攤平後的 code/name/eng，
            # 不是 API 原始欄位名（過去讀「代號/公司代號」永遠是空 → 預載空轉）
            code = str(item.get("code") or "").strip()
            name = str(item.get("name") or "").strip()
            if code and name:
                # 上市 .TW / 上櫃 .TWO（TWSymbolResolver 已區分，這裡統一不加後綴，
                # 因為下游 tw_stock_price 工具直接吃 4 位數字代號）
                entries[name.lower()] = {
                    "symbol": code,
                    "market": "tw",
                    "name": name,
                }
                # 英文簡稱也加入（如果有）
                eng = str(item.get("eng") or "").strip()
                if eng:
                    entries[eng.lower()] = {
                        "symbol": code,
                        "market": "tw",
                        "name": name,
                    }

        added = add_preloaded_aliases(entries)
        logger.info("[ticker_preloader] TW: %d aliases (%d new)", len(entries), added)
        return added
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("[ticker_preloader] TW preload failed: %s", exc)
        return 0


async def _preload_us() -> int:
    """美股全部（NASDAQ + NYSE + AMEX，從 rreichel3 GitHub raw JSON）。"""
    try:
        import httpx

        from core.tools.multi_market_resolver import add_preloaded_aliases

        # rreichel3/US-Stock-Symbols 每晚自動更新的 JSON（按交易所分檔）
        sources = {
            "nasdaq": "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/nasdaq/nasdaq_full_tickers.json",
            "nyse": "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/nyse/nyse_full_tickers.json",
            "amex": "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/amex/amex_full_tickers.json",
        }

        entries = {}
        for exchange, url in sources.items():
            try:
                resp = await asyncio.to_thread(
                    lambda u=url: httpx.get(u, timeout=30, follow_redirects=True)
                )
                if resp.status_code != 200:
                    logger.warning("[ticker_preloader] US %s: HTTP %d", exchange, resp.status_code)
                    continue
                data = resp.json()
                # 格式: [{"symbol": "AAPL", "name": "Apple Inc.", ...}, ...]
                for item in data:
                    symbol = str(item.get("symbol", "")).strip()
                    name = str(item.get("name", "")).strip()
                    if symbol and name:
                        entries[name.lower()] = {
                            "symbol": symbol,
                            "market": "us",
                            "name": name,
                        }
                logger.info("[ticker_preloader] US %s: %d entries", exchange, len(data))
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                logger.warning("[ticker_preloader] US %s failed: %s", exchange, exc)

        added = add_preloaded_aliases(entries)
        logger.info("[ticker_preloader] US: %d aliases (%d new)", len(entries), added)
        return added
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("[ticker_preloader] US preload failed: %s", exc)
        return 0


async def _preload_hk() -> int:
    """港股主要股票（嘗試從 yfinance 抓 constituents，或略過）。"""
    # 港股完整列表免費來源較少，目前略過——港股主要公司已在手動表裡。
    # 未來可接 HKEX 官方 CSV 或 EODHD API。
    return 0
