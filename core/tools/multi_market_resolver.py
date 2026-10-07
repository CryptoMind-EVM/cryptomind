"""Multi-market symbol resolver — 處理一個 ticker 在多個市場並存的情況。

動機：使用者問「AKE 現在值得買嗎」，AKE 可能是：
- Akedo (crypto memecoin, CoinGecko #516)
- Arkema (Euronext Paris 法股, AKE.PA) — 本平台不支援
- Allkem (ASX:AKE) — 已 delisted（合併成 Arcadium Lithium）

既有 ``resolve_symbol`` 一次只能查一個 market，且 ``SymbolNormalizer.normalize`` 也只回
單一 ``NormalizedSymbol``。當 ticker 真的存在多個 market 時，這個訊息結構上會丟失。

本模組對同一個 ticker **並行查所有支援的市場** + 對「不在 ``SUPPORTED_MARKETS`` 的市場」
用 ``web_search`` 偵測，回傳完整候選清單。

設計：
- 重用既有 ``SymbolNormalizer``（不重複實作 API 呼叫邏輯）
- 每個 market 跑在 ``asyncio.gather`` 內，加 3s 超時（避免單一市場慢拖累整體）
- 未支援市場的候選標 ``supported: false`` + 人類可讀原因（商業產品誠實告知使用者）
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from core.providers.base_provider import SUPPORTED_MARKETS

logger = logging.getLogger(__name__)


# ============================================================================
# 候選 markets（含 crypto + 本平台支援的股票市場）
# ============================================================================

#: 本工具會並行探測的所有市場。
#: crypto + SUPPORTED_MARKETS（us/tw/hk/jp/kr/in/cn）= 8 個 market
_PROBED_MARKETS: tuple[str, ...] = ("crypto", *SUPPORTED_MARKETS)

#: 每個 market 查詢超時（秒）。SymbolNormalizer 內部 API 通常 <2s，
#: 給 3s 緩衝；超時的 market 視為查無（不阻塞其他 market）。
_PER_MARKET_TIMEOUT_SEC = 3.0

#: 「本平台不支援」的常見市場（web_search 可能會提到的）。
#: 商業誠實原則：明確告知使用者為什麼不能查。
_UNSUPPORTED_MARKET_INFO: dict[str, dict[str, str]] = {
    "fr": {
        "name_zh": "法股（Euronext Paris）",
        "name_en": "French stock (Euronext Paris)",
        "note_zh": "本平台目前不支援法股市場",
        "note_en": "French market is not supported by this platform",
    },
    "de": {
        "name_zh": "德股（XETRA/Frankfurt）",
        "name_en": "German stock (XETRA/Frankfurt)",
        "note_zh": "本平台目前不支援德股市場",
        "note_en": "German market is not supported by this platform",
    },
    "au": {
        "name_zh": "澳股（ASX）",
        "name_en": "Australian stock (ASX)",
        "note_zh": "本平台目前不支援澳股市場",
        "note_en": "Australian market is not supported by this platform",
    },
    "uk": {
        "name_zh": "英股（LSE）",
        "name_en": "UK stock (LSE)",
        "note_zh": "本平台目前不支援英股市場",
        "note_en": "UK market is not supported by this platform",
    },
}


# ============================================================================
# 候選結果資料結構
# ============================================================================


@dataclass
class MarketCandidate:
    """單一 market 對某 ticker 的查詢結果。"""

    market: str
    symbol: Optional[str]  # 標準 ticker；查無時 None
    name: Optional[str] = None  # 資產名稱（如 "Akedo", "Arkema"）
    source: Optional[str] = None  # coingecko / yfinance / web_search / llm_confident
    verified: bool = False  # True = API 確認
    supported: bool = True  # 本平台是否支援此 market
    status: Optional[str] = None  # "active" / "delisted" / "merged"
    note: Optional[str] = None  # 人類可讀註解（不支援原因等）
    extra: Dict[str, Any] = field(default_factory=dict)  # 額外資訊（mcap_rank 等）
    error: Optional[str] = None  # 查詢失敗原因


# ============================================================================
# 核心：對單一 market 查詢
# ============================================================================


def _probe_single_market(
    query: str, market: str
) -> MarketCandidate:
    """對單一 market 跑一次 SymbolNormalizer。

    Sync 函數（SymbolNormalizer 是 sync），由 ``run_in_executor`` 包起來跑。
    """
    from core.agents.models import ExtractedEntity
    from core.agents.tools import _get_shared_normalizer

    candidate = MarketCandidate(market=market, symbol=None)

    try:
        is_ascii = query.strip().isascii()
        entity = ExtractedEntity(
            market=market,
            asset_name=query.strip(),
            candidate_symbol=query.strip().upper() if is_ascii else None,
            confidence=0.8,
            reasoning="invoked via resolve_symbol_all_markets",
        )
        normalizer = _get_shared_normalizer()
        result = normalizer.normalize(entity, enable_web_fallback=False)
        if result is not None:
            candidate.symbol = result.symbol
            candidate.source = result.source
            candidate.verified = result.verified
            candidate.name = result.original_name if result.original_name != query.strip() else None
            candidate.status = "active"
        else:
            candidate.error = "not_found"
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        candidate.error = f"probe_failed: {type(e).__name__}: {e}"
        logger.debug("[multi_market_resolver] %s probe %s failed: %s", query, market, e)

    return candidate


async def _probe_single_market_async(query: str, market: str) -> MarketCandidate:
    """Async wrapper：把 sync probe 丟到 executor + 加超時。"""
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _probe_single_market, query, market),
            timeout=_PER_MARKET_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        return MarketCandidate(
            market=market, symbol=None, error=f"timeout_{_PER_MARKET_TIMEOUT_SEC}s"
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return MarketCandidate(
            market=market, symbol=None, error=f"async_failed: {type(e).__name__}"
        )


# ============================================================================
# web_search 偵測未支援市場的候選
# ============================================================================


async def _detect_unsupported_market_candidates(query: str) -> List[MarketCandidate]:
    """用 web_search 偵測 ticker 在「本平台不支援的市場」的候選。

    例如 AKE 在 fr (Euronext Paris) 是 Arkema，本平台不支援法股；
    但使用者應該被告知這個事實（商業誠實原則）。
    """
    candidates: List[MarketCandidate] = []

    try:
        from core.tools.web_search import web_search_tool

        # 用英文 query 增加命中率；只取前幾筆避免 token 浪費
        search_query = f'"{query.upper()}" stock ticker exchange'
        results_json = web_search_tool.invoke({"query": search_query, "purpose": "general"})

        # web_search_tool 回 JSON 字串，解析它
        try:
            results = json.loads(results_json) if isinstance(results_json, str) else results_json
        except (ValueError, TypeError):
            results = []

        if not isinstance(results, list):
            return candidates

        # 對每個未支援 market 的關鍵字掃描搜尋結果
        text_blob = json.dumps(results, ensure_ascii=False).lower()
        for market_code, info in _UNSUPPORTED_MARKET_INFO.items():
            # 簡單關鍵字匹配（exchange name / market code）
            triggers = [
                market_code,
                info["name_en"].lower().split("(")[0].strip(),
                info["name_en"].lower().split("(")[1].rstrip(")").lower()
                if "(" in info["name_en"]
                else "",
            ]
            triggers = [t for t in triggers if t and len(t) >= 2]
            if any(t in text_blob for t in triggers):
                candidates.append(
                    MarketCandidate(
                        market=market_code,
                        symbol=None,  # 沒有 verified symbol
                        source="web_search",
                        verified=False,
                        supported=False,
                        status="detected_via_web_search",
                        note=info["note_en"],
                        extra={"market_name_en": info["name_en"]},
                    )
                )
                logger.info(
                    "[multi_market_resolver] %s detected in unsupported market: %s",
                    query,
                    market_code,
                )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.debug(
            "[multi_market_resolver] web_search detection failed for %s: %s", query, e
        )

    return candidates


# ============================================================================
# 本地映射表查詢（零 API call、最高優先級）+ 動態學習 + 啟動預載
# ============================================================================
# 三層別名來源，全部攤平進 _LOCAL_ALIASES：
# 1. company_aliases.json（curated 手動表，進版控）
# 2. learned_aliases.json（動態學習 + 啟動預載，runtime 產物，不進版控）
# 3. 啟動預載（TWSE/美股/港股完整列表，背景 fire-and-forget 寫進 learned）

_LOCAL_ALIASES: Optional[Dict[str, Dict]] = None
_ALIASES_LOCK = threading.Lock()
_LEARNED_PATH = None  # lazy 計算
_LAST_LEARN_WRITE: float = 0.0
_LEARN_DEBOUNCE_SEC = 300  # 5 分鐘內不重複寫檔


# ============================================================================
# Preloaded alias 的 Redis L2（跨 worker 共享，避免每個 worker 各自下載+記憶）
# ============================================================================
# preloaded alias（~11000 個 TW/US 公司名）改存 Redis HASH，讓多 worker 共享
# 一份、重啟不丟。curated/learned（~250）仍留 in-memory（有 substring scan 消費者）。
# 設計對齊 core/shared_cache.py：lazy client、Redis 不可用時優雅降級（no-op）。
_ALIAS_REDIS_KEY = "ticker:preloaded_aliases"
_ALIAS_REDIS_TTL_SEC = 86400  # 24h；TTL 到期由 preloader 重建
_alias_redis_client: Optional[Any] = None
_alias_redis_checked: bool = False


def _get_alias_redis_client() -> Optional[Any]:
    """回傳可用的 sync Redis client；不可用回 None（只檢查一次）。

    對齊 shared_cache._get_client 模式：lazy 連線、memory:// 不啟用、連不上 no-op。
    """
    global _alias_redis_client, _alias_redis_checked
    if _alias_redis_checked:
        return _alias_redis_client

    _alias_redis_checked = True
    try:
        from core.redis_url import resolve_redis_url

        redis_url, _source = resolve_redis_url()
    except Exception:
        return None
    if not redis_url or redis_url.startswith("memory://"):
        return None

    try:
        import redis as _redis

        client = _redis.from_url(
            redis_url,
            decode_responses=False,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        _alias_redis_client = client
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.debug("[multi_market_resolver] alias Redis unavailable: %s", exc)
        _alias_redis_client = None
    return _alias_redis_client


def _reset_alias_redis_client() -> None:
    """重置 Redis client 狀態（主要給測試用）。"""
    global _alias_redis_client, _alias_redis_checked
    _alias_redis_client = None
    _alias_redis_checked = False


def _alias_redis_get(key: str) -> Optional[Dict]:
    """從 Redis HASH 查單一 alias；miss 或不可用回 None。"""
    client = _get_alias_redis_client()
    if not client:
        return None
    try:
        import orjson

        raw = client.hget(_ALIAS_REDIS_KEY, key)
        if raw is not None:
            return orjson.loads(raw)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.debug("[multi_market_resolver] alias Redis HGET failed: %s", exc)
    return None


def _alias_redis_set_batch(entries: Dict[str, Dict]) -> None:
    """批次寫 alias 進 Redis HASH（pipeline）+ 設 24h TTL。

    Redis 不可用時為 no-op。
    """
    if not entries:
        return
    client = _get_alias_redis_client()
    if not client:
        return
    try:
        import orjson

        mapping = {k: orjson.dumps(v) for k, v in entries.items()}
        pipe = client.pipeline()
        pipe.hset(_ALIAS_REDIS_KEY, mapping=mapping)
        pipe.expire(_ALIAS_REDIS_KEY, _ALIAS_REDIS_TTL_SEC)
        pipe.execute()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.debug("[multi_market_resolver] alias Redis HSET batch failed: %s", exc)


def _alias_redis_exists() -> bool:
    """Redis HASH 是否已有 preloaded alias（給 preloader fast-path 用）。

    不可用時回 False（會觸發下載）。
    """
    client = _get_alias_redis_client()
    if not client:
        return False
    try:
        return client.hlen(_ALIAS_REDIS_KEY) > 0
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.debug("[multi_market_resolver] alias Redis HLEN failed: %s", exc)
        return False


def _get_learned_path():
    """lazy 計算 learned_aliases.json 的路徑。"""
    global _LEARNED_PATH
    if _LEARNED_PATH is None:
        from pathlib import Path

        _LEARNED_PATH = Path(__file__).parent.parent.parent / "data" / "learned_aliases.json"
    return _LEARNED_PATH


def _flatten_aliases(raw: dict) -> Dict[str, Dict]:
    """把 JSON 結構攤平成 {alias_lower: {symbol, market, name}}。"""
    flat: Dict[str, Dict] = {}
    for key, val in raw.items():
        if key.startswith("_"):
            continue
        if isinstance(val, dict) and "symbol" in val:
            flat[key.lower()] = val
        elif isinstance(val, dict):
            for alias, entry in val.items():
                if isinstance(entry, dict) and "symbol" in entry:
                    flat[alias.lower()] = entry
    return flat


def _load_local_aliases() -> Dict[str, Dict]:
    """載入本地別名表（lazy load + cache）。

    合併兩個來源：company_aliases.json（curated）+ learned_aliases.json（動態學習）。
    """
    global _LOCAL_ALIASES
    if _LOCAL_ALIASES is not None:
        return _LOCAL_ALIASES

    import json
    from pathlib import Path

    base_path = Path(__file__).parent.parent.parent / "data"
    flat: Dict[str, Dict] = {}

    # 來源 1：curated 手動表
    try:
        curated = json.loads((base_path / "company_aliases.json").read_text(encoding="utf-8"))
        flat.update(_flatten_aliases(curated))
    except Exception as exc:
        logger.warning("[multi_market_resolver] curated aliases load failed: %s", exc)

    # 來源 2：動態學習 + 啟動預載
    try:
        learned_path = base_path / "learned_aliases.json"
        if learned_path.exists():
            learned = json.loads(learned_path.read_text(encoding="utf-8"))
            flat.update(_flatten_aliases(learned))
    except Exception as exc:
        logger.warning("[multi_market_resolver] learned aliases load failed: %s", exc)

    with _ALIASES_LOCK:
        if _LOCAL_ALIASES is None:
            _LOCAL_ALIASES = flat
        else:
            # 另一條 thread 已經先載了 → merge（不覆蓋已有的）
            for k, v in flat.items():
                _LOCAL_ALIASES.setdefault(k, v)

    logger.debug("[multi_market_resolver] loaded %d local aliases", len(_LOCAL_ALIASES))
    return _LOCAL_ALIASES


def _learn_alias(query: str, symbol: str, market: str, name: str = "") -> None:
    """動態學習：把查到的新 alias 寫進記憶體 + 持久化到 learned_aliases.json。

    下次同一查詢直接命中本地表，不再走 API。
    簡單 debounce：5 分鐘內不重複寫檔（避免頻繁 I/O）。
    """
    import time

    key = query.strip().lower()
    if not key or not symbol:
        return

    entry = {"symbol": symbol, "market": market, "name": name or query}

    with _ALIASES_LOCK:
        aliases = _load_local_aliases()
        if key in aliases:
            return  # 已存在，不重複學習
        aliases[key] = entry

    # debounce：5 分鐘內不重複寫檔
    now = time.time()
    global _LAST_LEARN_WRITE
    if now - _LAST_LEARN_WRITE < _LEARN_DEBOUNCE_SEC:
        return

    _LAST_LEARN_WRITE = now
    try:
        import json

        learned_path = _get_learned_path()
        # 讀現有 → 加入新 entry → 寫回
        existing = {}
        if learned_path.exists():
            existing = json.loads(learned_path.read_text(encoding="utf-8"))
        with _ALIASES_LOCK:
            # 把記憶體裡所有 learned alias 同步寫回（不只這一筆）
            for k, v in _LOCAL_ALIASES.items():
                if v.get("source") != "curated":
                    existing[k] = v
            existing[key] = entry
        learned_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        logger.debug("[multi_market_resolver] learned alias: %s → %s", key, symbol)
    except Exception as exc:
        logger.warning("[multi_market_resolver] learn alias persist failed: %s", exc)


def add_preloaded_aliases(entries: Dict[str, Dict]) -> int:
    """啟動預載用：批次加入 alias 到記憶體表 + Redis（啟動時調用）。

    Args:
        entries: {alias_lower: {symbol, market, name}}

    Returns:
        新增的條目數（in-memory 部分；Redis HSET 不影響計數）

    除了 merge 進 in-memory dict（保留 Redis 不可用時的降級路徑），同時
    批次寫進 Redis HASH（跨 worker 共享 + 重啟不丟）。Redis 不可用時
    只寫 in-memory，行為等同改動前。
    """
    added = 0
    # 先載入再進臨界區：_load_local_aliases() 自己會取 _ALIASES_LOCK，而
    # _ALIASES_LOCK 是 threading.Lock（不可重入）。過去在持鎖狀態下呼叫它，
    # 冷啟動（_LOCAL_ALIASES 還是 None、不會 early return）就同一條 thread
    # 取兩次鎖 → 永久 deadlock。這段跑在 event loop 上（lifespan 的
    # asyncio.create_task(preload_ticker_lists())），所以整個 worker 會凍住，
    # 連 /health 都不回 → gunicorn timeout → SIGABRT → 重啟 → 再 deadlock。
    aliases = _load_local_aliases()
    new_entries: Dict[str, Dict] = {}
    with _ALIASES_LOCK:
        for key, entry in entries.items():
            if key not in aliases:
                aliases[key] = entry
                added += 1
                new_entries[key] = entry
    if added:
        logger.info("[multi_market_resolver] preloaded %d new aliases", added)
    # 同步寫進 Redis（全量 entries，不只 new；讓 Redis 成為完整副本供其他 worker）
    _alias_redis_set_batch(entries)
    return added


def _lookup_local_alias(query: str) -> Optional[MarketCandidate]:
    """查本地映射表。命中 → 回 MarketCandidate（零 API call）。

    支援大小寫不敏感 + 去空白。

    查詢順序：
    1. Redis HASH（preloaded ~11000 alias，跨 worker 共享）
    2. in-memory dict（curated + learned ~250，含 substring scan 消費者）
    Redis miss 或不可用時 fallback 到 in-memory，行為等同改動前。
    """
    key = query.strip().lower()

    # Layer 0a: Redis（preloaded bulk）
    entry = _alias_redis_get(key)
    # Layer 0b: in-memory（curated + learned），Redis miss/不可用時的 fallback
    if entry is None:
        aliases = _load_local_aliases()
        if not aliases:
            return None
        entry = aliases.get(key)
    if not entry:
        return None

    return MarketCandidate(
        market=entry.get("market", "us"),
        symbol=entry.get("symbol", ""),
        name=entry.get("name", ""),
        source="local_alias",
        verified=True,
        supported=True,
        status="local_alias_match",
        note=f'Local alias: "{query}" → {entry.get("symbol")} ({entry.get("name")})',
    )


# ============================================================================
# 全球符號主檔（第 1 層）— 從 FinanceDatabase 匯出的輕量 JSON
# ============================================================================
# 動機：company_aliases.json 只覆蓋中文暱稱（250 個）。全球英文用戶問
# "NVDA"/"MSFT"/"JPM" 等英文 ticker 時本地表查不到，每次要走外部 API（慢、
# 可能失敗）。global_symbols.json 補上全球大型股（5778 支，從 FinanceDatabase
# 匯出），本地毫秒級查詢。詳見 scripts/export_global_symbols.py。
_global_symbols_cache: Optional[Dict[str, dict]] = None


def _load_global_symbols() -> Dict[str, dict]:
    """載入 data/global_symbols.json（lazy cache，11ms 載入）。

    graceful：載入失敗回空 dict（fallback 到外部 API）。
    """
    global _global_symbols_cache
    if _global_symbols_cache is not None:
        return _global_symbols_cache
    try:
        import json
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent.parent / "data" / "global_symbols.json"
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        symbols = data.get("symbols", {}) if isinstance(data, dict) else {}
        _global_symbols_cache = symbols if isinstance(symbols, dict) else {}
        return _global_symbols_cache
    except Exception as e:
        logger.debug(f"[multi_market_resolver] failed to load global_symbols.json: {e}")
        _global_symbols_cache = {}
        return _global_symbols_cache


def _lookup_global_symbol(query: str) -> Optional[MarketCandidate]:
    """查全球符號主檔（第 1 層）。命中 → 回 MarketCandidate（零 API call）。

    覆蓋英文 ticker（NVDA/MSFT/JPM 等全球大型股），補 company_aliases.json
    （中文暱稱為主）的英文缺口。大小寫不敏感。
    """
    symbols = _load_global_symbols()
    if not symbols:
        return None

    key = query.strip().upper()  # ticker 慣例大寫
    entry = symbols.get(key)
    if not entry:
        return None

    return MarketCandidate(
        market=entry.get("market", "us"),
        symbol=key,
        name=entry.get("name", ""),
        source="global_symbols",
        verified=True,
        supported=True,
        status="global_symbol_match",
        note=f'Global symbol registry: {key} ({entry.get("name")}) — {entry.get("country", "")}',
    )


# ============================================================================
# 中文暱稱 → 英文翻譯（web_search 輔助）
# ============================================================================
# 動機：yfinance / CoinGecko 對中文暱稱查不到（如「閃迪」→ 應為 Sandisk SNDK）。
# 但 DuckDuckGo 搜「閃迪 stock」能找到含 SNDK 的結果（如 PTT「[標的] SNDK閃迪」）。
# 此函式在 all_markets 候選為空 + query 含非 ASCII 時觸發，用 web_search 翻譯
# 並抽出英文 ticker，再用 yfinance 驗證後加進候選。


async def _web_search_translate_retry(
    query: str, existing_candidates: List["MarketCandidate"]
) -> List["MarketCandidate"]:
    """候選為空 + query 含非 ASCII 時，用 web_search 翻譯並重試。

    流程：
    1. search_duckduckgo("{query} stock ticker") 搜尋
    2. 從結果的 title/snippet 用 regex 抽 [A-Z]{2,5} ticker（過濾常見誤抓詞）
    3. 每個 ticker 用 yfinance Ticker 驗證存在
    4. 驗證通過 → 加進 candidates（source=web_search_translate, verified=True）
    """
    if not query or query.strip().isascii():
        return existing_candidates  # 只對非 ASCII（中文等）觸發

    try:
        from core.tools.web_search import search_web
    except Exception:
        return existing_candidates

    try:
        results = search_web(f"{query} stock ticker", max_results=5)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.debug("[multi_market_resolver] translate web_search failed: %s", exc)
        return existing_candidates

    if not results:
        return existing_candidates

    import re

    # 合併 title + snippet，抽 [A-Z]{2,5} 候選 ticker
    combined = " | ".join(
        f"{r.get('title', '')} {r.get('snippet', '')}" for r in results
    )
    # 排除常見誤抓詞
    _FALSE_POSITIVES = {
        "RSI", "KD", "MACD", "ETF", "CEO", "CFO", "USA", "USD",
        "TW", "HK", "JP", "KR", "EU", "APAC", "EMEA", "PDF", "HTML",
        "Q", "IPO", "SPAC", "NYSE", "NASDAQ", "OTC",
        "AI", "IT", "PR", "IR", "ESG", "GDP", "CPI", "FED", "SEC",
        "PC", "TV", "APP", "API", "GPU", "CPU", "LED", "USB",
    }

    # 策略：優先抽「緊鄰非 ASCII 字元」的 ticker（如「SNDK閃迪」「超微AMD」），
    # 這代表搜尋引擎認為 ticker 跟使用者問的中文 query 相關。
    # 若找不到緊鄰的，退而抽所有 [A-Z]{2,5}（較寬鬆，靠 yfinance 驗證把關）。
    raw_tickers = re.findall(r"\b([A-Z]{2,5})(?=[^\x00-\x7F])", combined)
    if not raw_tickers:
        # 退而求其次：ticker 前面緊鄰非 ASCII（如「閃迪 SNDK」）
        raw_tickers = re.findall(r"(?<=[^\x00-\x7F])\s*([A-Z]{2,5})\b", combined)
    if not raw_tickers:
        # 最後 fallback：所有 [A-Z]{2,5}（靠 yfinance 驗證把關）
        raw_tickers = re.findall(r"\b([A-Z]{2,5})\b", combined)

    tickers = []
    seen = set()
    for t in raw_tickers:
        if t not in _FALSE_POSITIVES and t not in seen:
            seen.add(t)
            tickers.append(t)
        if len(tickers) >= 5:
            break

    if not tickers:
        return existing_candidates

    # 用 yfinance 驗證每個 ticker（確認真的存在）
    from core.tools.symbol_normalizer import SymbolNormalizer

    # 找每個 ticker 在搜尋結果裡的脈絡 snippet（給 LLM 判斷用）
    def _snippet_for(ticker: str) -> str:
        for r in results:
            text = f"{r.get('title', '')} {r.get('snippet', '')}"
            if ticker in text:
                return text[:120]
        return ""

    normalizer = SymbolNormalizer()
    new_candidates: List[MarketCandidate] = list(existing_candidates)
    for ticker in tickers:
        try:
            verified = normalizer._yfinance_exists(ticker)
            if verified:
                snippet = _snippet_for(ticker)
                new_candidates.append(
                    MarketCandidate(
                        market="us",
                        symbol=ticker,
                        source="web_search_translate",
                        verified=True,
                        supported=True,
                        status="translated_from_non_ascii_query",
                        note=f'web_search translation: "{query}" → {ticker} (verified via yfinance)',
                        extra={"search_snippet": snippet} if snippet else {},
                    )
                )
                # 不 break：回傳所有驗證通過的候選，
                # 若有多個 → ambiguous: true → LLM 根據 snippet 判斷哪個才對
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            continue

    # 動態學習：如果只找到一個候選（非 ambiguous），自動學習這個 alias
    # 下次同一查詢直接命中本地表，不再走 web_search
    learned = [c for c in new_candidates if c not in existing_candidates]
    if len(learned) == 1:
        c = learned[0]
        _learn_alias(query, c.symbol, c.market, c.name or query)

    return new_candidates


# ============================================================================
# 主入口：resolve_symbol_all_markets
# ============================================================================


async def resolve_symbol_all_markets_async(query: str) -> Dict[str, Any]:
    """對單一 ticker 並行查所有市場，回傳候選清單。

    Args:
        query: 使用者輸入的 ticker 或資產名稱（例如 "AKE", "BTC", "2330"）。

    Returns:
        Dict 含 ``query`` / ``candidates`` / ``ambiguous`` / ``recommendation``。
    """
    if not query or not query.strip():
        return {
            "query": query,
            "candidates": [],
            "ambiguous": False,
            "error": "empty query",
        }

    query = query.strip()

    # ── 第 0 層：本地中文映射表（最快路徑、零 API call、零延遲）──
    # 高頻中文暱稱（閃迪/輝達/台積電/比特幣等）直接命中，不需走 API。
    local = _lookup_local_alias(query)
    if local:
        return {
            "query": query,
            "candidates": [asdict(local)],
            "ambiguous": False,
            "valid_count": 1,
            "unsupported_count": 0,
            "recommendation": "local alias match — proceed with the matched symbol",
        }

    # ── 第 1 層：全球符號主檔（英文 ticker 本地查詢、零 API call）──
    # 補 company_aliases.json 的英文缺口：全球用戶問 NVDA/MSFT/JPM 等英文
    # ticker 時，這裡本地秒查（5778 支大型股），不用走外部 API。
    global_sym = _lookup_global_symbol(query)
    if global_sym:
        return {
            "query": query,
            "candidates": [asdict(global_sym)],
            "ambiguous": False,
            "valid_count": 1,
            "unsupported_count": 0,
            "recommendation": "global symbol match — proceed with the matched symbol",
        }

    # ── 第 2 層：並行查所有支援的 market + web_search 偵測未支援 market ──
    probe_tasks = [
        _probe_single_market_async(query, market) for market in _PROBED_MARKETS
    ]
    web_task = _detect_unsupported_market_candidates(query)

    all_results = await asyncio.gather(*probe_tasks, web_task, return_exceptions=False)

    # 拆出：前 N 個是 market probe，最後一個是 web_search 偵測
    market_candidates: List[MarketCandidate] = [
        r for r in all_results[:-1] if isinstance(r, MarketCandidate)
    ]
    web_candidates: List[MarketCandidate] = (
        all_results[-1] if isinstance(all_results[-1], list) else []
    )

    # 合併 + 過濾掉「明確查無」的（保留有 symbol 或不支援的）
    filtered: List[MarketCandidate] = []
    for c in market_candidates:
        if c.symbol is not None or not c.supported:
            filtered.append(c)
    filtered.extend(web_candidates)

    # 中文暱稱翻譯重試：候選為空 + query 含非 ASCII（中文等）時，
    # 用 web_search 翻譯成英文 ticker 再驗證（如「閃迪」→ SNDK）
    if not filtered and not query.strip().isascii():
        filtered = await _web_search_translate_retry(query, filtered)

    # 判定 ambiguous：有 ≥2 個有效候選（有 symbol 或不支援市場被偵測到）
    valid_count = sum(1 for c in filtered if c.symbol is not None)
    unsupported_count = sum(1 for c in filtered if not c.supported)
    is_ambiguous = (valid_count + unsupported_count) >= 2

    # 組 recommendation
    if is_ambiguous:
        recommendation = (
            "ambiguous: list all candidates and ask user to pick "
            "(or 'all' to query all supported ones in parallel)"
        )
    elif valid_count == 1 and unsupported_count == 0:
        recommendation = "single match — proceed with the matched symbol"
    elif valid_count == 0 and unsupported_count >= 1:
        recommendation = (
            "only unsupported markets matched — inform user this ticker exists "
            "but the platform doesn't support it; suggest web_search for reference"
        )
    else:
        recommendation = "no candidates found — fall back to existing resolve_symbol flow"

    return {
        "query": query,
        "candidates": [asdict(c) for c in filtered],
        "ambiguous": is_ambiguous,
        "valid_count": valid_count,
        "unsupported_count": unsupported_count,
        "recommendation": recommendation,
    }


def resolve_symbol_all_markets_sync(query: str) -> str:
    """Sync wrapper：給 LangChain @tool 用。

    LangChain tool 可以是 sync 或 async；用 sync 版簡化 agent 呼叫，
    內部用 ``asyncio.run`` 跑 async 實作。

    注意：被呼叫時若已在 event loop 內（例如 claw_loop Phase C 直接呼叫），
    不能用 ``asyncio.run``（會炸 RuntimeError）。偵測到就新建 loop 在另一個 thread 跑。
    """
    try:
        try:
            asyncio.get_running_loop()
            # 已在 event loop 內（如 claw_loop 呼叫）→ 不能 asyncio.run
            # 開新 thread + 新 loop 跑
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(_run_in_fresh_loop, query)
                result = future.result(timeout=_PER_MARKET_TIMEOUT_SEC * len(_PROBED_MARKETS) + 5)
        except RuntimeError:
            # 沒有 event loop → 直接 asyncio.run
            result = asyncio.run(resolve_symbol_all_markets_async(query))
        return json.dumps(result, ensure_ascii=False)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("[multi_market_resolver] failed for %r: %s", query, e)
        return json.dumps(
            {
                "query": query,
                "candidates": [],
                "ambiguous": False,
                "error": f"resolver_failed: {type(e).__name__}: {e}",
            },
            ensure_ascii=False,
        )


def _run_in_fresh_loop(query: str) -> Dict[str, Any]:
    """在新 thread 的新 event loop 內跑 async resolver（避開巢狀 loop 限制）。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(resolve_symbol_all_markets_async(query))
    finally:
        loop.close()
