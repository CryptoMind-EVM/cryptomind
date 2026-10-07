"""啟動後先把台股／美股頁一打開就要的行情快取暖好（2026-09-26）。

每次部署或 gunicorn worker recycle 後，程序內快取是空的，第一個開台股／美股頁的人要等
外部 API 1.5–2 秒（正式站量測）。worker 起來一陣子後，在背景用「跟前端預設清單相同的參數」
直接呼叫那幾個端點函式，把同一份快取填好；之後由 _ttl_cache.swr 的背景更新維持新鮮。

只在 ENVIRONMENT=production 跑（測試與本機開發不去打外部 API）。每一項各自 try，
任何一項失敗只記 log。前端預設清單與這裡用的常數由 tests/test_market_warmup.py 守住。
"""

from __future__ import annotations

import asyncio
import os

from api.utils import logger

WARMUP_DELAY_SECONDS = 20


def warmup_enabled() -> bool:
    return os.getenv("ENVIRONMENT", "").lower() == "production" and not os.getenv(
        "PYTEST_CURRENT_TEST"
    )


def _jobs():
    from api.routers import twstock, usstock

    tw = ",".join(twstock.DEFAULT_TW_SYMBOLS)
    us = ",".join(usstock.DEFAULT_US_SYMBOLS)
    us_news = ",".join(usstock.DEFAULT_US_SYMBOLS[:5])
    # 參數要跟前端一模一樣，快取 key 才對得上（web/js/twstock.js、usstock.js）。
    # symbols=None 要明寫：端點的預設值是 FastAPI 的 Query 物件，直接呼叫會被當成有值。
    return [
        ("twstock market", lambda: twstock.get_tw_market(symbols=tw)),
        ("twstock pe_ratio", lambda: twstock.get_tw_pe_ratio_batch(symbols=tw)),
        ("twstock news", lambda: twstock.get_tw_major_news(limit=15, symbols=None)),
        ("twstock dividend", lambda: twstock.get_tw_dividend(limit=20, symbols=None)),
        ("twstock foreign_holding", lambda: twstock.get_tw_foreign_holding()),
        ("usstock indices", lambda: usstock.get_us_indices()),
        ("usstock market", lambda: usstock.get_us_market(symbols=us)),
        ("usstock news", lambda: usstock.get_us_news(symbols=us_news, limit=15)),
    ]


async def warm_market_caches(delay: float = WARMUP_DELAY_SECONDS) -> dict:
    await asyncio.sleep(delay)
    results = {}
    for name, run in _jobs():
        try:
            await run()
            results[name] = "ok"
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:  # 外部 API 掛了不影響啟動；使用者來了照常同步抓
            results[name] = f"failed: {e}"
            logger.warning(f"[warmup] {name} failed: {e}")
    logger.info(f"[warmup] market caches: {results}")
    return results
