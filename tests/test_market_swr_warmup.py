"""行情快取：過期先回舊的、背景更新（swr）＋啟動預熱（2026-09-26）。

TTL 只有 60 秒～5 分鐘，以前過期後下一個人要同步等外部 API 0.8–2 秒；部署／worker recycle
後的第一個人也一樣。
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from api.routers._ttl_cache import TTLCache, swr

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent


def _expire(cache: TTLCache, key: str, seconds_ago: float) -> None:
    data, _ = cache.entry(key)
    cache._data[key] = (data, datetime.now(timezone.utc) - timedelta(seconds=seconds_ago))


def test_swr_fresh_hit_does_not_compute():
    async def main():
        cache = TTLCache(default_ttl=60)
        calls = []

        async def compute():
            calls.append(1)
            return {"v": len(calls)}

        assert await swr(cache, "k", compute) == {"v": 1}
        assert await swr(cache, "k", compute) == {"v": 1}
        assert len(calls) == 1

    asyncio.run(main())


def test_swr_stale_returns_old_and_refreshes_once_in_background():
    async def main():
        cache = TTLCache(default_ttl=60)
        calls = []
        gate = asyncio.Event()

        async def compute():
            calls.append(1)
            if len(calls) > 1:
                await gate.wait()
            return {"v": len(calls)}

        await swr(cache, "k", compute)
        _expire(cache, "k", 30)
        # 過期但未滿 max_stale：立刻回舊值，而且併發的呼叫只排一次背景更新
        results = await asyncio.gather(*[swr(cache, "k", compute, max_stale=600) for _ in range(5)])
        assert results == [{"v": 1}] * 5
        await asyncio.sleep(0)
        assert len(calls) == 2
        gate.set()
        await asyncio.sleep(0.01)
        assert await swr(cache, "k", compute) == {"v": 2}  # 背景更新完成後拿到新值

    asyncio.run(main())


def test_swr_too_stale_computes_synchronously():
    async def main():
        cache = TTLCache(default_ttl=60)
        n = {"i": 0}

        async def compute():
            n["i"] += 1
            return n["i"]

        await swr(cache, "k", compute)
        _expire(cache, "k", 700)
        assert await swr(cache, "k", compute, max_stale=600) == 2  # 太舊：不拿舊值頂

    asyncio.run(main())


def test_swr_background_failure_keeps_stale_value():
    async def main():
        cache = TTLCache(default_ttl=60)
        state = {"fail": False}

        async def compute():
            if state["fail"]:
                raise RuntimeError("upstream down")
            return "old"

        await swr(cache, "k", compute)
        _expire(cache, "k", 30)
        state["fail"] = True
        assert await swr(cache, "k", compute) == "old"
        await asyncio.sleep(0.01)  # 背景更新失敗只記 log
        assert await swr(cache, "k", compute) == "old"

    asyncio.run(main())


def test_twse_fetch_serves_stale_table_and_refreshes_in_background(monkeypatch):
    from api.routers import twstock

    async def main():
        calls = []

        async def fake_now(url, params, ck):
            calls.append(ck)
            twstock._twse_cache[ck] = ([{"Code": str(len(calls))}], datetime.now(timezone.utc) + timedelta(seconds=300))
            return twstock._twse_cache[ck][0]

        monkeypatch.setattr(twstock, "_fetch_twse_now", fake_now)
        twstock._twse_cache.pop("t_swr", None)
        assert await twstock._fetch_twse("u", cache_key="t_swr") == [{"Code": "1"}]
        data, _ = twstock._twse_cache["t_swr"]
        twstock._twse_cache["t_swr"] = (data, datetime.now(timezone.utc) - timedelta(hours=1))
        assert await twstock._fetch_twse("u", cache_key="t_swr") == [{"Code": "1"}]  # 舊的先頂
        await asyncio.sleep(0.01)
        assert calls == ["t_swr", "t_swr"]
        assert await twstock._fetch_twse("u", cache_key="t_swr") == [{"Code": "2"}]
        twstock._twse_cache.pop("t_swr", None)

    asyncio.run(main())


def _js_default_symbols(rel: str) -> list:
    src = (ROOT / rel).read_text(encoding="utf-8")
    block = re.search(r"defaultSymbols:\s*\[(.*?)\]", src, re.S).group(1)
    return re.findall(r"'([^']+)'", block)


def test_warmup_uses_the_same_default_symbols_as_the_frontend():
    """快取 key 含股票清單：預熱用的清單跟前端預設不同，就暖到沒人用的 key。"""
    from api.routers import twstock, usstock

    assert twstock.DEFAULT_TW_SYMBOLS == _js_default_symbols("web/js/twstock.js")
    assert usstock.DEFAULT_US_SYMBOLS == _js_default_symbols("web/js/usstock.js")


def test_warmup_calls_each_endpoint_once_and_survives_failures(monkeypatch):
    from api import market_warmup
    from api.routers import twstock, usstock

    seen = []

    def rec(name, fail=False):
        async def fn(**kwargs):
            seen.append((name, kwargs))
            if fail:
                raise RuntimeError("boom")
            return {}

        return fn

    monkeypatch.setattr(twstock, "get_tw_market", rec("tw_market", fail=True))
    monkeypatch.setattr(twstock, "get_tw_pe_ratio_batch", rec("tw_pe"))
    monkeypatch.setattr(twstock, "get_tw_major_news", rec("tw_news"))
    monkeypatch.setattr(twstock, "get_tw_dividend", rec("tw_div"))
    monkeypatch.setattr(twstock, "get_tw_foreign_holding", rec("tw_foreign"))
    monkeypatch.setattr(usstock, "get_us_indices", rec("us_idx"))
    monkeypatch.setattr(usstock, "get_us_market", rec("us_market"))
    monkeypatch.setattr(usstock, "get_us_news", rec("us_news"))

    results = asyncio.run(market_warmup.warm_market_caches(delay=0))
    assert [s[0] for s in seen] == ["tw_market", "tw_pe", "tw_news", "tw_div", "tw_foreign", "us_idx", "us_market", "us_news"]
    assert results["twstock market"].startswith("failed")  # 一項失敗不影響後面
    assert all(v == "ok" for k, v in results.items() if k != "twstock market")
    kw = dict(seen)
    assert kw["tw_market"]["symbols"] == ",".join(twstock.DEFAULT_TW_SYMBOLS)
    assert kw["tw_news"] == {"limit": 15, "symbols": None}  # 預設值是 Query 物件，要明寫 None
    assert kw["us_news"] == {"symbols": ",".join(usstock.DEFAULT_US_SYMBOLS[:5]), "limit": 15}


def test_warmup_only_in_production(monkeypatch):
    from api.market_warmup import warmup_enabled

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert not warmup_enabled()
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert warmup_enabled()
