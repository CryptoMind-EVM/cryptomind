import asyncio
import concurrent.futures
import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import numpy as np
import pandas as pd
import requests
from cachetools import TTLCache, cached

# LangChain Imports
from langchain_core.messages import HumanMessage

from api.utils import logger
from utils.llm_client import extract_json_from_response

# Cache for CryptoPanic API calls, 5-minute TTL (reduced from 1 hour for real-time)
cryptopanic_cache = TTLCache(maxsize=100, ttl=300)
RSS_DEFAULT_NEWS_SOURCES = ["google"]
# 新聞來源改走 BYOK：在 get_crypto_news() 內依 has_tool_key() 動態決定，
# 不再用 PREMIUM_GATED_NEWS_SOURCES 硬擋。保留常數名給潛在舊 import 用。
PREMIUM_GATED_NEWS_SOURCES = set()


class DataFrameEncoder(json.JSONEncoder):
    """
    Custom JSON encoder to handle pandas DataFrame, Timestamps, and NumPy types.
    It converts a DataFrame to a list of dictionaries and NumPy types to native Python types.
    """

    def default(self, obj):
        if isinstance(obj, pd.DataFrame):
            # Create a copy to avoid modifying the original DataFrame in place
            df_copy = obj.copy()
            # Convert all datetime-like columns to ISO 8601 strings.
            for col in df_copy.select_dtypes(
                include=["datetime64[ns]", "datetimetz"]
            ).columns:
                df_copy[col] = df_copy[col].dt.isoformat()
            return df_copy.to_dict(orient="records")
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        if isinstance(obj, (np.bool_, bool)):
            return bool(obj)
        if isinstance(obj, (np.integer, int)):
            return int(obj)
        if isinstance(obj, (np.floating, float)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def safe_float(value, default=0.0):
    """
    Safely converts a value to a float.

    Args:
        value: The value to convert.
        default: The default value to return if conversion fails.

    Returns:
        The float value or the default.
    """
    try:
        result = float(value)
    except (ValueError, TypeError, SystemError):
        return default
    # NaN/inf 通過 float() 不會 raise，但會毒化下游（LLM prompt 顯示 "nan"、
    # JSON 序列化無效、格式化金額變 "$nan"）。一律降級為 default。
    if math.isnan(result) or math.isinf(result):
        return default
    return result


@cached(cryptopanic_cache)
def get_crypto_news_cryptopanic(symbol: str = "BTC", limit: int = 5) -> List[Dict]:
    """
    從 CryptoPanic 獲取指定幣種的最新新聞 (有1小時快取)
    """
    # 增加延遲以符合 API Rate Limit
    time.sleep(0.5)

    # BYOK：優先讀使用者自帶金鑰，沒有則嘗試官方 env fallback
    from core.tools.key_resolver import resolve_tool_key

    API_KEY = resolve_tool_key("cryptopanic", official_env="CRYPTOPANIC_API_KEY")

    if not API_KEY:
        return []

    logger.debug(f">> 正在從 CryptoPanic API 撈取 {symbol} 的真實新聞...")

    url = "https://cryptopanic.com/api/developer/v2/posts/"
    params = {
        "auth_token": API_KEY,
        "currencies": symbol,
        "kind": "news",
        "public": "true",
    }

    retries = 3
    delay = 5  # seconds
    for i in range(retries):
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            news_list = []
            if "results" in data:
                for item in data["results"][:limit]:
                    # 加入情緒標籤 (如果有)
                    sentiment = "中性"
                    if "votes" in item:
                        if item["votes"]["positive"] > item["votes"]["negative"]:
                            sentiment = "看漲"
                        elif item["votes"]["negative"] > item["votes"]["positive"]:
                            sentiment = "看跌"

                    news_list.append(
                        {
                            "title": item.get("title", "No Title"),
                            "description": item.get(
                                "title", ""
                            ),  # CryptoPanic often has empty description, use title as fallback
                            "published_at": item.get("published_at", "N/A"),
                            "sentiment": sentiment,
                            "url": item.get("url", ""),  # Extract URL
                            "source": item.get("domain", "CryptoPanic"),
                        }
                    )

            if not news_list:
                logger.debug(">> CryptoPanic: 未找到相關新聞")

            return news_list

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429 and i < retries - 1:
                logger.warning(
                    f">> CryptoPanic API rate limit hit. Retrying in {delay} seconds..."
                )
                time.sleep(delay)
                delay *= 2  # Exponential backoff
                continue
            else:
                logger.error(f">> CryptoPanic 獲取新聞失敗: {str(e)}")
                return []
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.error(f">> CryptoPanic 獲取新聞失敗: {str(e)}")
            return []

    return []


def get_crypto_news_newsapi(symbol: str = "BTC", limit: int = 5) -> List[Dict]:
    """
    從 NewsAPI 獲取加密貨幣相關新聞
    申請免費 API Key: https://newsapi.org/
    免費版: 100 請求/天
    """
    from core.tools.key_resolver import resolve_tool_key

    API_KEY = resolve_tool_key("newsapi", official_env="NEWSAPI_KEY")

    if not API_KEY:
        logger.debug(">> NewsAPI Key not set, skipping...")
        return []

    logger.debug(f">> 正在從 NewsAPI 撈取 {symbol} 相關新聞...")

    # 常見加密貨幣名稱映射
    crypto_names = {
        "BTC": "Bitcoin",
        "ETH": "Ethereum",
        "XRP": "Ripple",
        "BNB": "Binance",
        "SOL": "Solana",
        "ADA": "Cardano",
        "DOGE": "Dogecoin",
        "PI": "Pi Network",
        "MATIC": "Polygon",
    }

    search_term = crypto_names.get(symbol.upper(), symbol)

    url = "https://newsapi.org/v2/everything"
    params = {
        "apiKey": API_KEY,
        "q": f"{search_term} OR {symbol}",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": limit,
        "from": (datetime.now(timezone.utc) - timedelta(days=7)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        ),
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        news_list = []
        if data.get("status") == "ok" and "articles" in data:
            for article in data["articles"][:limit]:
                news_list.append(
                    {
                        "title": article.get("title", "No Title"),
                        "description": article.get("description", ""),
                        "published_at": article.get("publishedAt", "N/A"),
                        "sentiment": "中性",  # NewsAPI 不提供情緒分析
                        "source": f"NewsAPI ({article.get('source', {}).get('name', 'Unknown')})",
                        "url": article.get("url", ""),  # Extract URL
                    }
                )

        return news_list

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        print(f">> NewsAPI 獲取失敗: {str(e)}")
        return []


# 2026-09-14 根因修復（eth.limo 舊聞被當近期事件）：
# 1. Google News RSS 的 pubDate 是「條目進 feed 的時間」——轉載／重新收錄會蓋掉
#    原文日期，不可當原文發表時間引用。每筆結果帶 date_semantics 標注語意。
# 2. 關鍵字查詢（q={symbol}+crypto）會混入同 ticker 的非加密公司
#    （EnerSys NYSE:ENS 等），依加密訊號詞過濾。
_GOOGLE_NEWS_DATE_SEMANTICS = "feed_ingestion_time"
_CRYPTO_SIGNAL_RE = re.compile(
    r"crypto|cryptocurrency|blockchain|web3|defi|nft|dao\b|token|altcoin|stablecoin|"
    r"bitcoin|btc\b|ethereum|ether\b|eth\b|solana|binance|coinbase|wallet|onchain|"
    r"on-chain|\.eth\b|name service|白皮書|區塊鏈|加密|代幣|鏈上|虛擬貨幣",
    re.IGNORECASE,
)


def _looks_crypto_related(title: str, description: str) -> bool:
    """標題／摘要有任一加密訊號詞才算相關——擋掉 ticker 撞名的傳產新聞。"""
    return bool(
        _CRYPTO_SIGNAL_RE.search(title or "")
        or _CRYPTO_SIGNAL_RE.search(description or "")
    )


def get_crypto_news_google(symbol: str = "BTC", limit: int = 5) -> List[Dict]:
    """
    從 Google News RSS 獲取最新新聞（無限量、無 API Key 限制）

    ⚠️ published_at 為 Google News 的「收錄時間」（date_semantics=
    feed_ingestion_time），不是原文發表時間；引用事件日期前需另行查證。
    """
    import xml.etree.ElementTree as ET

    logger.debug(f">> 正在從 Google News 撈取 {symbol} 的即時新聞...")

    url = f"https://news.google.com/rss/search?q={symbol}+crypto+when:7d&hl=en-US&gl=US&ceid=US:en"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        root = ET.fromstring(response.content)

        news_list = []
        for item in root.findall(".//item"):
            title = (
                item.find("title").text
                if item.find("title") is not None
                else "No Title"
            )
            link = item.find("link").text if item.find("link") is not None else ""
            source_name = (
                item.find("source").text
                if item.find("source") is not None
                else "Google News"
            )
            description = (
                item.find("description").text
                if item.find("description") is not None
                else ""
            )

            # Clean up HTML tags from description if present
            description = re.sub("<[^<]+?>", "", description)

            # ticker 撞名過濾（如 EnerSys NYSE:ENS ≠ Ethereum Name Service）
            if not _looks_crypto_related(title, description):
                continue

            news_list.append(
                {
                    "title": title,
                    "description": description or title,
                    "published_at": item.find("pubDate").text
                    if item.find("pubDate") is not None
                    else "N/A",
                    "date_semantics": _GOOGLE_NEWS_DATE_SEMANTICS,
                    "sentiment": "中性",
                    "source": f"Google ({source_name})",
                    "url": link,
                }
            )
            if len(news_list) >= limit:
                break
        return news_list
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f">> Google News 獲取失敗: {str(e)}")
        return []


def get_crypto_news_cryptocompare(symbol: str = "BTC", limit: int = 5) -> List[Dict]:
    """
    從 CryptoCompare 獲取專業新聞（免費額度極高）
    """
    logger.debug(f">> 正在從 CryptoCompare 撈取 {symbol} 的專業報導...")
    url = "https://min-api.cryptocompare.com/data/v2/news/"
    params = {"categories": symbol, "excludeCategories": "Sponsored", "lang": "EN"}

    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        news_list = []
        if data.get("Data"):
            for item in data["Data"][:limit]:
                news_list.append(
                    {
                        "title": item.get("title", "No Title"),
                        "description": item.get("body", ""),
                        "published_at": datetime.fromtimestamp(
                            item.get("published_on", 0)
                        ).isoformat(),
                        "sentiment": "中性",
                        "source": f"CryptoCompare ({item.get('source', 'Unknown')})",
                        "url": item.get("url", ""),
                    }
                )
        return news_list
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        print(f">> CryptoCompare 獲取失敗: {str(e)}")
        return []


def audit_crypto_news(symbol: str, news_list: List[Dict]) -> List[Dict]:
    """
    📰 新聞審查員 (News Auditor)
    使用 LLM 批量審查新聞清單，過濾掉不相關、廣告或低質量的資訊。
    """
    if not news_list:
        return []

    from core.config import MARKET_PULSE_MODEL
    from utils.llm_client import create_llm_client_from_config
    from utils.settings import Settings

    # 只用 SERVER_OPENAI_API_KEY，不觸碰用戶 BYOK key
    api_key = Settings.SERVER_OPENAI_API_KEY or os.getenv("SERVER_OPENAI_API_KEY", "")
    if not api_key or not api_key.startswith("sk-") or len(api_key) < 20:
        if not getattr(audit_crypto_news, "_has_warned", False):
            logger.debug(
                ">> ℹ️ 新聞審查員跳過：未設定 SERVER_OPENAI_API_KEY（BYOK 模式）"
            )
            audit_crypto_news._has_warned = True
        return news_list

    try:
        client, model = create_llm_client_from_config(MARKET_PULSE_MODEL)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        if not getattr(audit_crypto_news, "_has_warned", False):
            logger.warning(">> ⚠️ 無法啟動新聞審查員 (未配置 API Key)，跳過審查。")
            audit_crypto_news._has_warned = True
        return news_list

    logger.debug(
        f">> 🛡️ 新聞審查員正在啟動 (對象: {symbol}, 待審核: {len(news_list)} 條)..."
    )

    # 準備審核文本
    audit_data = []
    for i, n in enumerate(news_list):
        audit_data.append(
            {"id": i, "title": n.get("title", ""), "source": n.get("source", "")}
        )

    prompt = f"""
你是一位專業的加密貨幣新聞審查員。你的任務是從以下新聞清單中，篩選出與「{symbol}」真正相關且具備參考價值的內容。

**篩選準則**:
1. **高度相關**: 新聞必須直接提及 {symbol} 或其生態系統、技術更新、重大合作或價格波動原因。
2. **品質優先**: 過濾掉明顯的點擊誘餌 (Clickbait)、純廣告、無意義的短訊或與加密貨幣完全無關的內容。
3. **去除重複**: 如果多條新聞內容幾乎相同，請只保留最重要或來源最可靠的一條。

**待審核清單**:
{json.dumps(audit_data, ensure_ascii=False, indent=2)}

**輸出要求**:
請僅輸出一個 JSON 陣列，包含通過審核的新聞 ID。
範例: [0, 2, 5]
不要包含任何解釋、標記或 Markdown 格式。
"""

    try:
        # LangChain Invoke
        response = client.invoke([HumanMessage(content=prompt)])
        content = response.content.strip()

        passed_ids = extract_json_from_response(content)

        if not isinstance(passed_ids, list):
            logger.warning(">> ⚠️ 審查員回傳格式錯誤，跳過篩選。")
            return news_list

        filtered_news = [news_list[i] for i in passed_ids if i < len(news_list)]
        logger.debug(
            f">> ✅ 審查完成: 保留 {len(filtered_news)}/{len(news_list)} 條高價值新聞"
        )
        return filtered_news

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f">> ⚠️ 審查過程跳過: {e}")
        return news_list


def get_crypto_news(
    symbol: str = "BTC", limit: int = 5, enabled_sources: List[str] = None
) -> List[Dict]:
    """
    多來源新聞聚合器

    支援來源 ID: ['google', 'cryptocompare', 'cryptopanic', 'newsapi']
    - google / cryptocompare：免費、不需金鑰，預設啟用
    - cryptopanic / newsapi：BYOK，使用者必須在 tool settings 設定金鑰後才會被加入
    """
    logger.debug(f"\n>> 啟動多來源新聞聚合系統 (目標: {symbol})...")

    FREE_SOURCES = ["google"]
    BYOK_SOURCES = {"cryptocompare", "cryptopanic", "newsapi"}

    if not enabled_sources:
        enabled_sources = list(FREE_SOURCES)
    else:
        enabled_sources = [src.strip().lower() for src in enabled_sources if src]

    try:
        from core.tools.key_resolver import has_tool_key

        unlocked = [s for s in BYOK_SOURCES if has_tool_key(s)]
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        unlocked = []
    if unlocked:
        for src in unlocked:
            if src not in enabled_sources:
                enabled_sources.append(src)
        logger.info(">> 已啟用 BYOK 新聞來源: %s", ", ".join(unlocked))

    all_news = []
    source_map = {
        "google": (get_crypto_news_google, "Google News (無限)"),
        "cryptocompare": (get_crypto_news_cryptocompare, "CryptoCompare (高額度)"),
        "cryptopanic": (get_crypto_news_cryptopanic, "CryptoPanic (專業)"),
        "newsapi": (get_crypto_news_newsapi, "NewsAPI (主流)"),
    }

    # 使用並行處理
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(enabled_sources)
    ) as executor:
        futures = {}
        for src_id in enabled_sources:
            if src_id in source_map:
                func, name = source_map[src_id]
                futures[executor.submit(func, symbol, limit)] = name

        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                news = future.result()
                if news:
                    all_news.extend(news)
                    logger.debug(f">> {name}: 獲取 {len(news)} 條新聞")
                else:
                    logger.debug(f">> {name}: 無新聞")
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.error(f">> {name} 發生錯誤: {e}")

    # 去重（根據標題相似度）
    unique_news = []
    seen_titles = set()

    for news_item in all_news:
        title_lower = news_item["title"].lower()[:50]
        if title_lower not in seen_titles:
            seen_titles.add(title_lower)
            unique_news.append(news_item)

    # 排序
    try:
        unique_news.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        logger.debug("Failed to sort news by published_at")

    # 🚀 調用 LLM 審查員進行最後篩選

    audited_news = audit_crypto_news(symbol, unique_news[: limit * 3])

    result = audited_news[: limit * 3]

    logger.debug(f"\n>> 聚合與審查完成: 總共獲取 {len(result)} 條優質新聞\n")

    return result
