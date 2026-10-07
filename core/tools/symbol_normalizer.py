"""
Symbol Normalizer — API-based ticker 標準化工具

接受 LLM ExtractedEntity（market, asset_name, candidate_symbol），
用 API 驗證並回傳標準 ticker。

設計原則：
- LLM 做 NLU（識別市場 + 資產名稱）
- API 做 fact lookup（確認標準代號）
- 複用現有 TWSymbolResolver（TWSE/TPEX）+ CoinGecko search + yfinance

業界依據：
- LangChain tool layer: "LLM reasons, tools verify"
- 分離 NLU 與 fact lookup 是 standard pattern
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import httpx

from ..providers.base_provider import normalize_symbol
from .tw_symbol_resolver import TWSymbolResolver

# TYPE_CHECKING-only import 避免循環依賴：
#   core.agents.models → core.agents.__init__ → bootstrap → manager → _main
#   → 又要 import SymbolNormalizer。lazy import 在函數內處理。
if TYPE_CHECKING:
    from ..agents.models import ExtractedEntity

logger = logging.getLogger(__name__)


# yfinance Search 結果過濾用的交易所後綴（事實性的交易所後綴，不是 market hint）。
# 補後綴走 base_provider.normalize_symbol（港股補零、A 股依代碼分上海／深圳）。
_YF_SUFFIXES: Dict[str, Tuple[str, ...]] = {
    "hk": (".HK",),
    "jp": (".T",),
    "kr": (".KS",),
    "in": (".NS",),
    "cn": (".SS", ".SZ"),  # 上海 / 深圳
}

# Cache TTL（防止永久 cache 過期資料）
_YFINANCE_CACHE_TTL = 3600  # 1 小時 — 股票上下市不會這麼頻繁
_COINGECKO_CACHE_TTL = 1800  # 30 分鐘 — 新幣上市較頻繁但仍夠用


@dataclass
class NormalizedSymbol:
    """API 驗證後的標準化結果。"""

    market: str
    symbol: str  # 標準 ticker（如 "2330.TW", "BTC", "AAPL"）
    source: str  # 驗證來源（twse_api, coingecko, yfinance, llm_confident）
    verified: bool  # True = API 確認, False = LLM 高信心但未驗證
    original_name: str  # 用戶原始輸入


class SymbolNormalizer:
    """
    API-based symbol normalization.

    每個市場的驗證策略：
    - tw: TWSymbolResolver（TWSE + TPEX OpenAPI，fuzzy match 中文名）
    - crypto: CoinGecko search API（直接用 asset_name 查詢，不需翻譯表）
    - us: yfinance Ticker.info（確認 ticker 存在）
    - hk/jp/kr/in/cn: yfinance + 後綴（確認 ticker + suffix 存在）
    - forex/commodity: 不需 normalizer，直接用 asset_name
    """

    def __init__(self) -> None:
        self._tw_resolver = TWSymbolResolver()
        # Cache 改為 (value, expires_at_epoch) 雙層 — 避免 stale forever
        self._coingecko_cache: Dict[str, Tuple[Optional[str], float]] = {}
        self._yfinance_cache: Dict[str, Tuple[bool, float]] = {}

    def normalize(
        self,
        entity: ExtractedEntity,
        enable_web_fallback: bool = False,
    ) -> Optional[NormalizedSymbol]:
        """
        驗證單一 ExtractedEntity，回傳標準 ticker 或 None。

        Args:
            entity: LLM 抽取的資產實體
            enable_web_fallback: 當所有 API 都查不到時，是否用 web_search 兜底。
                預設 False（保持快速）；Manager 在 retry / 第二次嘗試時可開啟。

        Returns:
            NormalizedSymbol 或 None（無法確認時）
        """
        market = entity.market
        asset_name = entity.asset_name
        candidate = entity.candidate_symbol
        confidence = entity.confidence

        dispatch = {
            "tw": self._normalize_tw,
            "crypto": self._normalize_crypto,
            "us": self._normalize_us,
            "hk": self._normalize_yfinance_suffix,
            "jp": self._normalize_yfinance_suffix,
            "kr": self._normalize_yfinance_suffix,
            "in": self._normalize_yfinance_suffix,
            "cn": self._normalize_yfinance_suffix,
        }

        normalizer = dispatch.get(market)
        if normalizer:
            result = normalizer(entity)
            if result:
                return result

        if market in ("forex", "commodity") and confidence >= 0.7 and candidate:
            return NormalizedSymbol(
                market=market,
                symbol=candidate.upper(),
                source="llm_confident",
                verified=False,
                original_name=asset_name,
            )

        # 🌐 最後一道 fallback：web_search（opt-in，預設關）
        if enable_web_fallback and market in dispatch:
            return self._web_search_fallback(entity)

        return None

    def normalize_many(
        self,
        entities: List[ExtractedEntity],
        enable_web_fallback: bool = False,
    ) -> List[NormalizedSymbol]:
        """批量驗證，回傳所有成功結果。"""
        results: List[NormalizedSymbol] = []
        for entity in entities:
            result = self.normalize(entity, enable_web_fallback=enable_web_fallback)
            if result:
                results.append(result)
        return results

    # ── TW: TWSymbolResolver（TWSE + TPEX OpenAPI）──────────────────────

    def _normalize_tw(self, entity: ExtractedEntity) -> Optional[NormalizedSymbol]:
        """
        用 TWSE/TPEX OpenAPI 驗證台股。
        支援：中文公司名、代號、英文簡稱 → 標準 .TW/.TWO ticker。
        """
        probe = entity.candidate_symbol or entity.asset_name
        if not probe:
            return None

        result = self._tw_resolver.resolve_with_metadata(probe)
        if result:
            return NormalizedSymbol(
                market="tw",
                symbol=result["ticker"],
                source="twse_api",
                verified=True,
                original_name=entity.asset_name,
            )

        if entity.asset_name != probe:
            result = self._tw_resolver.resolve_with_metadata(entity.asset_name)
            if result:
                return NormalizedSymbol(
                    market="tw",
                    symbol=result["ticker"],
                    source="twse_api",
                    verified=True,
                    original_name=entity.asset_name,
                )

        if entity.confidence >= 0.85 and entity.candidate_symbol:
            ticker = entity.candidate_symbol
            if not ticker.endswith((".TW", ".TWO")):
                ticker = f"{ticker}.TW"
            return NormalizedSymbol(
                market="tw",
                symbol=ticker,
                source="llm_confident",
                verified=False,
                original_name=entity.asset_name,
            )

        return None

    # ── Crypto: CoinGecko Search API ────────────────────────────────────

    def _normalize_crypto(self, entity: ExtractedEntity) -> Optional[NormalizedSymbol]:
        """
        用 CoinGecko search API 驗證加密貨幣。
        CoinGecko 接受英文查詢；LLM 已經識別出 asset_name，
        對於主流幣 LLM 給的名稱通常就是英文（BTC, ETH, Bitcoin, Pi Network）。
        """
        probe = entity.candidate_symbol or entity.asset_name
        if not probe:
            return None

        symbol = self._coingecko_search(probe)
        if symbol:
            return NormalizedSymbol(
                market="crypto",
                symbol=symbol,
                source="coingecko",
                verified=True,
                original_name=entity.asset_name,
            )

        if entity.asset_name != probe:
            symbol = self._coingecko_search(entity.asset_name)
            if symbol:
                return NormalizedSymbol(
                    market="crypto",
                    symbol=symbol,
                    source="coingecko",
                    verified=True,
                    original_name=entity.asset_name,
                )

        if entity.confidence >= 0.9 and entity.candidate_symbol:
            return NormalizedSymbol(
                market="crypto",
                symbol=entity.candidate_symbol.upper(),
                source="llm_confident",
                verified=False,
                original_name=entity.asset_name,
            )

        return None

    def _coingecko_search(self, query: str) -> Optional[str]:
        """
        CoinGecko search API: query → standard symbol.

        不需要 _ZH_TO_EN_HINT 翻譯表，因為：
        1. LLM 已把中文名識別為 asset_name（如「比特幣」）
        2. candidate_symbol 是英文（如 BTC）
        3. 如果 LLM 給中文 asset_name 當 candidate，CoinGecko API
           對主流幣（bitcoin, ethereum）也接受英文名查詢
        """
        cache_key = query.lower().strip()
        cached = self._cache_get(self._coingecko_cache, cache_key)
        if cached is not None:
            return cached[0]  # 用 1-tuple wrapper 區分「快取 miss」vs「快取 None」

        try:
            resp = httpx.get(
                "https://api.coingecko.com/api/v3/search",
                params={"query": cache_key},
                timeout=10,
            )
            if resp.status_code != 200:
                logger.debug(
                    f"[symbol_normalizer] CoinGecko HTTP {resp.status_code} for '{query}'"
                )
                self._cache_set(
                    self._coingecko_cache, cache_key, None, _COINGECKO_CACHE_TTL
                )
                return None

            coins = resp.json().get("coins", [])
            if not coins:
                self._cache_set(
                    self._coingecko_cache, cache_key, None, _COINGECKO_CACHE_TTL
                )
                return None

            coins.sort(key=lambda c: c.get("market_cap_rank") or 999999)
            symbol = coins[0].get("symbol", "").upper()
            result = symbol or None
            self._cache_set(
                self._coingecko_cache, cache_key, result, _COINGECKO_CACHE_TTL
            )
            return result

        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.debug(f"[symbol_normalizer] CoinGecko error for '{query}': {e}")
            self._cache_set(
                self._coingecko_cache, cache_key, None, _COINGECKO_CACHE_TTL
            )
            return None

    # ── US: yfinance 直接確認 ──────────────────────────────────────────

    def _normalize_us(self, entity: ExtractedEntity) -> Optional[NormalizedSymbol]:
        """
        美股：LLM 給的 candidate_symbol 通常就是正確 ticker（AAPL, TSLA）。
        用 yfinance 快速確認存在；沒 candidate 時用 yfinance Search 接受公司名。
        """
        candidate = entity.candidate_symbol

        # Path 1: 有 candidate 直接驗證
        if candidate:
            symbol = candidate.upper()
            if symbol.isalpha() and 1 <= len(symbol) <= 5:
                if self._yfinance_exists(symbol):
                    return NormalizedSymbol(
                        market="us",
                        symbol=symbol,
                        source="yfinance",
                        verified=True,
                        original_name=entity.asset_name,
                    )

        # Path 2: 沒 candidate 或驗證失敗 → 用 yfinance Search 接受公司名（含中文）
        searched = self._yfinance_search(entity.asset_name)
        if searched:
            return NormalizedSymbol(
                market="us",
                symbol=searched,
                source="yfinance_search",
                verified=True,
                original_name=entity.asset_name,
            )

        # Path 3: LLM 高信心 fallback
        if candidate and entity.confidence >= 0.85:
            return NormalizedSymbol(
                market="us",
                symbol=candidate.upper(),
                source="llm_confident",
                verified=False,
                original_name=entity.asset_name,
            )

        return None

    # ── HK/JP/KR/IN/CN: yfinance + 後綴 ────────────────────────────────

    def _normalize_yfinance_suffix(
        self, entity: ExtractedEntity
    ) -> Optional[NormalizedSymbol]:
        """
        全球市場（hk/jp/kr/in/cn）：LLM 已識別 market + candidate_symbol，
        補上 yfinance 後綴後驗證；沒 candidate 時 fallback 用 yfinance Search。
        """
        market = entity.market
        candidate = entity.candidate_symbol
        suffix = _YF_SUFFIXES.get(market, ())

        # Path 1: 有 candidate → 補後綴後驗證
        if candidate:
            symbol = normalize_symbol(candidate, market)

            if self._yfinance_exists(symbol):
                return NormalizedSymbol(
                    market=market,
                    symbol=symbol,
                    source="yfinance",
                    verified=True,
                    original_name=entity.asset_name,
                )

        # Path 2: 沒 candidate 或驗證失敗 → yfinance Search 接受公司名（含中文）
        searched = self._yfinance_search(entity.asset_name, market_suffix=suffix)
        if searched:
            return NormalizedSymbol(
                market=market,
                symbol=searched,
                source="yfinance_search",
                verified=True,
                original_name=entity.asset_name,
            )

        # Path 3: LLM 高信心 fallback
        if candidate and entity.confidence >= 0.85:
            symbol = normalize_symbol(candidate, market)
            return NormalizedSymbol(
                market=market,
                symbol=symbol,
                source="llm_confident",
                verified=False,
                original_name=entity.asset_name,
            )

        return None

    # ── yfinance 驗證 helper ─────────────────────────────────────────────

    def _yfinance_exists(self, symbol: str) -> bool:
        """用 yfinance Ticker.info 確認 ticker 存在（有 TTL cache 1 小時）。"""
        cached = self._cache_get(self._yfinance_cache, symbol)
        if cached is not None:
            return cached[0]

        try:
            import yfinance as yf

            ticker = yf.Ticker(symbol)
            info = ticker.info
            exists = bool(info and info.get("regularMarketPrice") is not None)
            self._cache_set(self._yfinance_cache, symbol, exists, _YFINANCE_CACHE_TTL)
            return exists
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.debug(
                f"[symbol_normalizer] yfinance check failed for '{symbol}': {e}"
            )
            # 網路／限流錯誤不是「查無此代號」，不快取——否則合法代號會被報查無 5 分鐘
            return False

    def _yfinance_search(
        self, query: str, market_suffix: Tuple[str, ...] = ()
    ) -> Optional[str]:
        """
        用 yfinance Search API 接受公司名/中文查詢，回標準 ticker。

        - 支援中英文公司名（如「Apple」「豐田」「Tencent」）
        - market_suffix 過濾結果（例：(".HK",) 只取港股；空=any）
        - Cache 12 小時（搜尋結果穩定）
        """
        if not query or not query.strip():
            return None

        cache_key = f"yfsearch:{','.join(market_suffix)}:{query.lower().strip()}"
        cached = self._cache_get(self._yfinance_cache, cache_key)
        if cached is not None:
            # cached 是 (value,) wrapper；yfsearch 的 value 是 str/None
            return cached[0] if cached[0] else None

        try:
            import yfinance as yf

            # yfinance Search 接受任何字串，回 quotes list
            results = yf.Search(query, max_results=10).quotes
            if not results:
                self._cache_set(
                    self._yfinance_cache, cache_key, None, _YFINANCE_CACHE_TTL
                )
                return None

            # 過濾市場：若指定 suffix，只取符合的
            matched = None
            for r in results:
                sym = r.get("symbol", "")
                if not sym:
                    continue
                if market_suffix:
                    if sym.endswith(market_suffix):
                        matched = sym
                        break
                else:
                    # 美股：取第一個無 suffix 的（純字母）
                    if "." not in sym and sym.isalpha():
                        matched = sym
                        break

            if matched is None:
                # 後綴沒匹配，但結果存在 → 退而求其次取第一個
                matched = results[0].get("symbol")

            self._cache_set(
                self._yfinance_cache, cache_key, matched, _YFINANCE_CACHE_TTL
            )
            return matched
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.debug(
                f"[symbol_normalizer] yfinance Search failed for '{query}': {e}"
            )
            # 同 _yfinance_exists：網路錯誤不快取成「查無」
            return None

    # ── Web Search Fallback（最後一道）──────────────────────────────────

    # 各市場的 ticker pattern（從 web_search 結果裡 regex 抽取）
    _WEB_TICKER_PATTERNS: Dict[str, List[str]] = {
        "tw": [
            r"\b(\d{4,6})\.TWO?\b",  # "2330.TW" / "5483.TWO"
            r"(?:代號|股票代號|證券代號)[:\s]*(\d{4,6})\b",  # "代號: 2330"
            r"(?:TWSE|TPEx)[:\s]+(\d{4,6})",
        ],
        "us": [
            r"\(\s*(?:NYSE|NASDAQ|NYSE Arca|AMEX)\s*:\s*([A-Z]{1,5})\s*\)",  # "(NYSE: AAPL)"
            r"\b(?:ticker|symbol)[:\s]+([A-Z]{1,5})\b",
            r"\$([A-Z]{2,5})\b",  # "$AAPL"
        ],
        "crypto": [
            r"\b\$([A-Z]{2,10})\b",  # "$BTC"
            r"(?:symbol|ticker)[:\s]+([A-Z]{2,10})\b",
            r"\(([A-Z]{2,10})\)\s*(?:price|coin|token|cryptocurrency)",
        ],
        "hk": [
            r"\b(\d{4,5})\.HK\b",
            r"(?:港交所|HKEX|HKG)[:\s]*(\d{4,5})\b",
        ],
        "jp": [
            r"\b(\d{4})\.T\b",
            r"(?:TYO|Tokyo)[:\s]+(\d{4})\b",
        ],
        "kr": [
            r"\b(\d{6})\.KS\b",
            r"(?:KRX|KOSPI)[:\s]+(\d{6})\b",
        ],
        "in": [
            r"\b([A-Z][A-Z0-9]{2,10})\.NS\b",
            r"(?:NSE|BSE)[:\s]+([A-Z][A-Z0-9]{2,10})\b",
        ],
        "cn": [
            r"\b(\d{6})\.(?:SS|SZ)\b",
            r"(?:SSE|SZSE|上交所|深交所)[:\s]*(\d{6})\b",
        ],
    }

    def _web_search_fallback(
        self, entity: "ExtractedEntity"
    ) -> Optional[NormalizedSymbol]:
        """
        最後一道防線：web_search 找標準 ticker。

        流程：
        1. 用 DuckDuckGo 搜尋 "{asset_name} {market} stock ticker symbol"
        2. 用 market-specific regex 從 title + snippet 抽 ticker
        3. 回 verified=False（搜尋抽取的，可能不準）

        什麼時候用：
        - LLM 給的 candidate 跟 yfinance Search 都失敗
        - 通常是冷門新股、剛上市、或 LLM 訓練後才出現的標的
        """
        market = entity.market
        patterns = self._WEB_TICKER_PATTERNS.get(market)
        if not patterns:
            return None

        # 組搜尋 query（依市場提示語）
        market_hints = {
            "tw": "台股 代號",
            "us": "stock ticker NYSE NASDAQ",
            "crypto": "cryptocurrency symbol",
            "hk": "Hong Kong stock HKEX",
            "jp": "Japan stock TYO",
            "kr": "Korea stock KOSPI",
            "in": "India stock NSE",
            "cn": "China A-share stock",
        }
        hint = market_hints.get(market, "stock ticker")
        query = f"{entity.asset_name} {hint}"

        cache_key = f"websearch:{market}:{entity.asset_name.lower()}"
        cached = self._cache_get(self._yfinance_cache, cache_key)
        if cached is not None:
            cached_val = cached[0]
            if cached_val:
                return NormalizedSymbol(
                    market=market,
                    symbol=cached_val,
                    source="web_search",
                    verified=False,
                    original_name=entity.asset_name,
                )
            return None

        try:
            from core.tools.web_search import search_duckduckgo

            results = search_duckduckgo(query, max_results=5)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.debug(f"[symbol_normalizer] web_search failed: {e}")
            return None

        if not results:
            self._cache_set(self._yfinance_cache, cache_key, None, 300)
            return None

        # 合併所有 result 的 title + snippet
        combined_text = " | ".join(
            f"{r.get('title', '')} {r.get('snippet', '')}" for r in results
        )

        import re as _re

        # 收集所有 pattern 抽到的 candidate（去重 + 保留出現順序）
        candidates: List[str] = []
        seen_candidates = set()
        for pattern in patterns:
            for match in _re.finditer(pattern, combined_text):
                ticker_raw = next((g for g in match.groups() if g), None)
                if not ticker_raw:
                    continue
                ticker = ticker_raw.upper().strip()
                if ticker and ticker not in seen_candidates:
                    seen_candidates.add(ticker)
                    candidates.append(ticker)

        if not candidates:
            self._cache_set(self._yfinance_cache, cache_key, None, 300)
            return None

        # ⚠️ web_search 抽到的 candidate 可能是「相關標的」而非用戶問的（例如搜
        # 「波克夏 巴菲特」會抽到 OXY 因為是巴菲特持股，不是 Berkshire 本身）。
        # 所以每個 candidate 都要再過一次 yfinance/CoinGecko 確認 name 匹配。
        for ticker in candidates[:5]:  # 最多驗 5 個避免噴 API
            full_symbol = (
                normalize_symbol(ticker, market) if market in _YF_SUFFIXES else ticker
            )

            if self._verify_ticker_matches_name(full_symbol, entity.asset_name, market):
                self._cache_set(
                    self._yfinance_cache, cache_key, full_symbol, _YFINANCE_CACHE_TTL
                )
                logger.info(
                    f"[symbol_normalizer] web_search + verify found "
                    f"'{entity.asset_name}' → {full_symbol}"
                )
                return NormalizedSymbol(
                    market=market,
                    symbol=full_symbol,
                    source="web_search_verified",
                    verified=True,
                    original_name=entity.asset_name,
                )

        # 驗證全失敗 → 不要回 false positive
        self._cache_set(self._yfinance_cache, cache_key, None, 300)
        logger.debug(
            f"[symbol_normalizer] web_search candidates {candidates} "
            f"none verified match '{entity.asset_name}'"
        )
        return None

    def _verify_ticker_matches_name(
        self, full_symbol: str, asset_name: str, market: str
    ) -> bool:
        """
        確認 ticker 對應的標的真的跟 asset_name 相關。

        策略：取 ticker.info 的 longName/shortName，
        跟 asset_name 做關鍵字交集（簡單但夠用）。
        """
        try:
            if market == "crypto":
                # crypto: 用 CoinGecko search 反查 ticker → 看 name 是否含 asset_name 關鍵字
                resp = httpx.get(
                    "https://api.coingecko.com/api/v3/search",
                    params={"query": full_symbol},
                    timeout=8,
                )
                if resp.status_code != 200:
                    return False
                coins = resp.json().get("coins", [])
                for c in coins[:3]:
                    if c.get("symbol", "").upper() == full_symbol.upper():
                        name = c.get("name", "").lower()
                        return self._name_overlap(name, asset_name.lower())
                return False
            else:
                # 股票：yfinance.Ticker.info → longName / shortName
                import yfinance as yf

                ticker = yf.Ticker(full_symbol)
                info = ticker.info or {}
                # 必須有實際行情才算存在
                if info.get("regularMarketPrice") is None:
                    return False
                long_name = (
                    info.get("longName") or info.get("shortName") or ""
                ).lower()
                if not long_name:
                    return False
                return self._name_overlap(long_name, asset_name.lower())
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.debug(f"[symbol_normalizer] verify {full_symbol} failed: {e}")
            return False

    @staticmethod
    def _name_overlap(name_from_api: str, asset_name_lower: str) -> bool:
        """
        簡單的關鍵字匹配：API name 跟 asset_name 是否有 token 重疊？

        例：
        - asset_name="波克夏海瑟威" + name="Berkshire Hathaway Inc" → True (波克夏=Berkshire)
        - asset_name="波克夏" + name="Occidental Petroleum" → False
        """
        # 移除常見公司後綴 (Inc/Corp/Ltd/Co/Inc.)
        import re as _re

        cleaned = _re.sub(
            r"\b(inc|corp|corporation|ltd|limited|co|company|llc|plc)\b\.?",
            "",
            name_from_api,
            flags=_re.IGNORECASE,
        )

        # 把 asset_name 拆成關鍵字（中文按字、英文按 token）
        asset_tokens = set()
        # 英文 token
        for tok in _re.findall(r"[a-z]{3,}", asset_name_lower):
            asset_tokens.add(tok)
        # 中文 2-grams
        chinese = _re.sub(r"[a-z0-9\s]+", "", asset_name_lower)
        for i in range(len(chinese) - 1):
            asset_tokens.add(chinese[i : i + 2])

        if not asset_tokens:
            return False

        # 計算交集（token 在 API name 出現的比例）
        cleaned_lower = cleaned.lower()
        hits = sum(1 for t in asset_tokens if t in cleaned_lower)
        # 至少要命中 ⅓ 的 token
        return hits >= max(1, len(asset_tokens) // 3)

    # ── Cache helpers (with TTL) ─────────────────────────────────────────
    @staticmethod
    def _cache_get(cache: Dict[str, Tuple[Any, float]], key: str):
        """回傳 (value,) 表示快取命中（即使 value 是 None）；回傳 None 表示 miss/過期。"""
        entry = cache.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.time() > expires_at:
            cache.pop(key, None)
            return None
        return (value,)

    @staticmethod
    def _cache_set(
        cache: Dict[str, Tuple[Any, float]], key: str, value: Any, ttl: int
    ) -> None:
        cache[key] = (value, time.time() + ttl)
