"""
工具系統資料庫操作
包含：工具目錄管理、Agent 工具權限、用戶工具偏好、使用量追蹤

會員等級：free / premium
- free: 免費用戶
- premium: 付費會員（完整功能）
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .connection import get_connection

logger = logging.getLogger(__name__)


# ============================================================================
# 工具目錄 Seed 資料
# ============================================================================

_TOOLS_SEED: List[Dict[str, Any]] = [
    # ── Crypto 基礎 (Free) ─────────────────────────────────────────────────────────
    {
        "tool_id": "get_crypto_price",
        "display_name": "即時加密貨幣價格",
        "description": "查詢加密貨幣即時價格",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_current_time_taipei",
        "display_name": "目前時間",
        "description": "查詢台灣/UTC+8 目前時間",
        "category": "general",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_fear_and_greed_index",
        "display_name": "恐慌與貪婪指數",
        "description": "查詢全球加密貨幣市場恐慌貪婪指數",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_trending_tokens",
        "display_name": "熱門幣種排行",
        "description": "查詢全網最熱門搜尋的加密貨幣",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "shared_limited",
        "daily_limit_free": 10,
        "daily_limit_plus": 30,
        "daily_limit_prem": None,
    },
    # ── 新增：市值排行 (Free) ─────────────────────────────────────────────────────
    {
        "tool_id": "get_crypto_market_cap",
        "display_name": "加密貨幣市值排行",
        "description": "查詢加密貨幣總市值排行 Top 100",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "shared_limited",
        "daily_limit_free": 5,
        "daily_limit_plus": 20,
        "daily_limit_prem": None,
    },
    # ── 新增：經濟日曆 (Free) ─────────────────────────────────────────────────────
    {
        "tool_id": "get_economic_calendar",
        "display_name": "全球經濟日曆",
        "description": "查詢重要經濟事件與數據發布時間",
        "category": "general",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── Crypto 技術分析 (Free) ─────────────────────────────────────────────────────
    {
        "tool_id": "technical_analysis",
        "display_name": "加密貨幣技術指標",
        "description": "RSI、MACD、均線等技術指標分析",
        "category": "technical",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── 新聞 (Free) ────────────────────────────────────────────────────────────────
    {
        "tool_id": "google_news",
        "display_name": "Google 新聞",
        "description": "從 Google News 抓取相關新聞",
        "category": "news",
        "tier_required": "free",
        "quota_type": "shared_limited",
        "daily_limit_free": 10,
        "daily_limit_plus": 30,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "aggregate_news",
        "display_name": "多來源新聞聚合",
        "description": "從多個來源聚合加密貨幣新聞（設定下方 CryptoPanic / NewsAPI 金鑰可解鎖更多來源）",
        "category": "news",
        "tier_required": "free",
        "quota_type": "shared_limited",
        "daily_limit_free": 10,
        "daily_limit_plus": 30,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "cryptopanic_news_source",
        "display_name": "CryptoPanic 新聞來源",
        "description": "讓 aggregate_news 工具能讀取 CryptoPanic 專業新聞（需自帶 CryptoPanic API 金鑰）",
        "category": "news",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
        "key_provider": "cryptopanic",
        "key_mode": "byok",
    },
    {
        "tool_id": "newsapi_news_source",
        "display_name": "NewsAPI 新聞來源",
        "description": "讓 aggregate_news 工具能讀取 NewsAPI 主流媒體新聞（免費版 100/天，需自帶金鑰）",
        "category": "news",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
        "key_provider": "newsapi",
        "key_mode": "byok",
    },
    {
        "tool_id": "web_search",
        "display_name": "網路搜尋",
        "description": "通用網路搜尋（免費用平台自架的多引擎聚合搜尋：Google／Bing／DuckDuckGo 等；設定 Tavily 金鑰可再升級品質）",
        "category": "general",
        "tier_required": "free",
        "quota_type": "shared_limited",
        "daily_limit_free": 10,
        "daily_limit_plus": 30,
        "daily_limit_prem": None,
        "key_provider": "tavily",
        "key_mode": "byok",
    },
    {
        "tool_id": "fetch_url",
        "display_name": "網頁全文讀取",
        "description": "讀取指定 URL 的完整網頁內容。先用 web_search 找到候選文章，再用此工具讀全文。免費使用 Jina Reader；設定 Tavily 金鑰可升級品質。",
        "category": "general",
        "tier_required": "free",
        "quota_type": "shared_limited",
        "daily_limit_free": 10,
        "daily_limit_plus": 30,
        "daily_limit_prem": None,
        # 重用現有 tavily provider（不新增 provider）；非必填 — 有 key 升級，沒 key 走免費 Jina
        "key_provider": "tavily",
        "key_mode": "byok",
    },
    # ── web_search 的備用搜尋金鑰 ──────────────────────────────────────────
    # 跟 *_news_source 同一個模式：不是獨立工具（bootstrap 不註冊），只是讓
    # 使用者的 Serper / FMP 金鑰在「工具設定」有地方可以填。web_search 的
    # fallback chain 會自己去讀（core/tools/web_search.py::search_web）。
    {
        "tool_id": "serper_search_source",
        "display_name": "Serper 搜尋來源（Google）",
        "description": "讓 web_search 用你自己的 Google 搜尋配額，中文品質最佳（免費 2500 次/月，需自帶金鑰）",
        "category": "general",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
        "key_provider": "serper",
        "key_mode": "byok",
    },
    {
        "tool_id": "fmp_search_source",
        "display_name": "FMP 搜尋來源（股票代號）",
        "description": "讓 web_search 能用公司名查股票代號，支援中文（如「台積電」→ TSM）（免費 250 次/天，需自帶金鑰）",
        "category": "general",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
        "key_provider": "fmp",
        "key_mode": "byok",
    },
    # ── 總經（BYOK：FRED 金鑰）─────────────────────────────────────────────
    {
        "tool_id": "get_central_bank_rates",
        "display_name": "央行利率（FRED）",
        "description": "主要央行利率與總經數據（需自帶 FRED 金鑰）",
        "category": "general",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
        "key_provider": "fred",
        "key_mode": "required",
    },
    # ── Crypto 衍生品 (Premium) ────────────────────────────────────────────
    {
        "tool_id": "get_futures_data",
        "display_name": "合約資金費率",
        "description": "查詢永續合約資金費率與多空情緒",
        "category": "derivatives",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": 0,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── Crypto 鏈上數據 (Premium) ──────────────────────────────────────────
    {
        "tool_id": "get_defillama_tvl",
        "display_name": "DeFi TVL 鎖倉量",
        "description": "從 DefiLlama 查詢協議/公鏈 TVL",
        "category": "onchain",
        "tier_required": "premium",
        "quota_type": "shared_limited",
        "daily_limit_free": 0,
        "daily_limit_plus": 30,
        "daily_limit_prem": 50,
    },
    {
        "tool_id": "get_crypto_categories_and_gainers",
        "display_name": "加密板塊與漲幅排行",
        "description": "CoinGecko 最強板塊與熱點",
        "category": "onchain",
        "tier_required": "premium",
        "quota_type": "shared_limited",
        "daily_limit_free": 0,
        "daily_limit_plus": 20,
        "daily_limit_prem": 30,
    },
    {
        "tool_id": "get_token_supply",
        "display_name": "代幣流通供應量",
        "description": "查詢代幣總發行量、最大供應量與流通量",
        "category": "onchain",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": 0,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── 新增：DEX 交易量 (Premium) ───────────────────────────────────────────────
    {
        "tool_id": "get_dex_volume",
        "display_name": "DEX 交易量排行",
        "description": "查詢去中心化交易所交易量與熱門幣對",
        "category": "onchain",
        "tier_required": "premium",
        "quota_type": "shared_limited",
        "daily_limit_free": 0,
        "daily_limit_plus": 20,
        "daily_limit_prem": 30,
    },
    # ── Crypto 鏈上數據 (Premium) ──────────────────────────────────────────
    {
        "tool_id": "get_token_unlocks",
        "display_name": "代幣解鎖日程",
        "description": "查詢代幣未來解鎖時間與數量",
        "category": "onchain",
        "tier_required": "premium",
        "quota_type": "shared_limited",
        "daily_limit_free": 0,
        "daily_limit_plus": 0,
        "daily_limit_prem": 50,
    },
    # ── 新增：鯨魚追蹤 (Premium) ─────────────────────────────────────────────
    {
        "tool_id": "get_whale_alerts",
        "display_name": "鯨魚追蹤警報",
        "description": "追蹤大額鏈上轉帳與鯨魚動向",
        "category": "onchain",
        "tier_required": "premium",
        "quota_type": "shared_limited",
        "daily_limit_free": 0,
        "daily_limit_plus": 0,
        "daily_limit_prem": 20,
    },
    # ── Crypto 行情（BYOK：CoinMarketCap 金鑰）──────────────────────────────
    {
        "tool_id": "get_cmc_quote",
        "display_name": "CoinMarketCap 行情",
        "description": "加密貨幣即時行情、市值與排名（需自帶 CMC 金鑰）",
        "category": "onchain",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
        "key_provider": "coinmarketcap",
        "key_mode": "required",
    },
    # ── Crypto 鏈上（BYOK：Etherscan 金鑰）──────────────────────────────────
    {
        "tool_id": "get_eth_balance",
        "display_name": "EVM 原生代幣餘額",
        "description": "查詢 EVM 地址原生代幣餘額（Ethereum/BSC/Polygon 等 11 鏈，需自帶 Etherscan 金鑰）",
        "category": "onchain",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
        "key_provider": "etherscan",
        "key_mode": "required",
    },
    {
        "tool_id": "get_erc20_token_balance",
        "display_name": "ERC20 代幣餘額",
        "description": "查詢 EVM 地址的 ERC20 代幣餘額（Ethereum/BSC/Polygon 等 11 鏈，需自帶 Etherscan 金鑰）",
        "category": "onchain",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
        "key_provider": "etherscan",
        "key_mode": "required",
    },
    {
        "tool_id": "get_address_transactions",
        "display_name": "地址交易記錄",
        "description": "查詢 EVM 地址最近交易（Ethereum/BSC/Polygon 等 11 鏈，需自帶 Etherscan 金鑰）",
        "category": "onchain",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
        "key_provider": "etherscan",
        "key_mode": "required",
    },
    # ── Crypto 安全檢測（GoPlus Security）─────────────────────────────────────
    {
        "tool_id": "check_token_security",
        "display_name": "代幣安全檢測",
        "description": "檢測代幣合約風險（蜜罐、rug pull、隱藏權限等，免費免金鑰）",
        "category": "onchain",
        "tier_required": "free",
        "quota_type": "shared_limited",
        "daily_limit_free": 10,
        "daily_limit_plus": 30,
        "daily_limit_prem": None,
        "key_mode": "none",
    },
    {
        "tool_id": "check_address_safety",
        "display_name": "地址安全檢測",
        "description": "檢測地址是否涉及釣魚、洗錢、制裁等惡意行為（平台官方 GoPlus key）",
        "category": "onchain",
        "tier_required": "free",
        "quota_type": "shared_limited",
        "daily_limit_free": 10,
        "daily_limit_plus": 30,
        "daily_limit_prem": None,
        "key_mode": "none",
    },
    # ── TON 安全檢測（路線 B，與 GoPlus 對稱）──────────────────────────────────────
    {
        "tool_id": "assess_jetton_safety",
        "display_name": "TON Jetton 安全檢測",
        "description": "查詢 TON jetton 安全訊號（白名單/持有人/admin，免費免金鑰）",
        "category": "onchain",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── 台股 基礎 (Free) ───────────────────────────────────────────────────────────
    {
        "tool_id": "tw_stock_price",
        "display_name": "台股即時股價",
        "description": "查詢台灣股票即時價格",
        "category": "tw_stock",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "tw_technical_analysis",
        "display_name": "台股技術指標",
        "description": "台股 RSI / MACD / KD / 均線",
        "category": "tw_stock",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "tw_news",
        "display_name": "台股新聞",
        "description": "查詢台股相關最新新聞",
        "category": "tw_stock",
        "tier_required": "free",
        "quota_type": "shared_limited",
        "daily_limit_free": 10,
        "daily_limit_plus": 30,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "tw_major_news",
        "display_name": "台股重大訊息",
        "description": "TWSE 官方重大訊息公告",
        "category": "tw_stock",
        "tier_required": "free",
        "quota_type": "shared_limited",
        "daily_limit_free": 10,
        "daily_limit_plus": 30,
        "daily_limit_prem": None,
    },
    # ── 台股 進階 (Premium) ──────────────────────────────────────────────────
    {
        "tool_id": "tw_fundamentals",
        "display_name": "台股基本面",
        "description": "P/E、EPS、ROE 等基本面資料",
        "category": "tw_stock",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": 0,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "tw_institutional",
        "display_name": "台股法人籌碼",
        "description": "外資、投信、自營商三大法人買賣超",
        "category": "tw_stock",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": 0,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "tw_pe_ratio",
        "display_name": "台股本益比",
        "description": "P/E 比、股息殖利率、P/B 比",
        "category": "tw_stock",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": 0,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "tw_monthly_revenue",
        "display_name": "台股月營收",
        "description": "月營收數據含 MoM、YoY 成長率",
        "category": "tw_stock",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": 0,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "tw_dividend",
        "display_name": "台股股利",
        "description": "現金股利、股票股利、除權息日期",
        "category": "tw_stock",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": 0,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── 台股 進階 (Premium) ──────────────────────────────────────────────────
    {
        "tool_id": "tw_foreign_top20",
        "display_name": "外資持股 Top 20",
        "description": "外資與陸資持股前 20 名排行",
        "category": "tw_stock",
        "tier_required": "premium",
        "quota_type": "shared_limited",
        "daily_limit_free": 0,
        "daily_limit_plus": 0,
        "daily_limit_prem": 30,
    },
    # ── 美股 基礎 (Free) ───────────────────────────────────────────────────────────
    {
        "tool_id": "us_stock_price",
        "display_name": "美股即時股價",
        "description": "美股即時價格（15 分鐘延遲，Yahoo Finance）",
        "category": "us_stock",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "us_technical_analysis",
        "display_name": "美股技術指標",
        "description": "美股 RSI / MACD / 布林帶 / 均線",
        "category": "us_stock",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "us_news",
        "display_name": "美股新聞",
        "description": "美股相關最新新聞",
        "category": "us_stock",
        "tier_required": "free",
        "quota_type": "shared_limited",
        "daily_limit_free": 10,
        "daily_limit_plus": 30,
        "daily_limit_prem": None,
    },
    # ── 美股 進階 (Premium) ──────────────────────────────────────────────────
    {
        "tool_id": "us_fundamentals",
        "display_name": "美股基本面",
        "description": "P/E、EPS、ROE、市值、股息率",
        "category": "us_stock",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": 0,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "us_earnings",
        "display_name": "美股財報",
        "description": "財報數據與財報日曆",
        "category": "us_stock",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": 0,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── 美股 進階 (Premium) ──────────────────────────────────────────────────
    {
        "tool_id": "us_institutional_holders",
        "display_name": "美股機構持倉",
        "description": "機構投資人持倉數據",
        "category": "us_stock",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": 0,
        "daily_limit_plus": 0,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "us_insider_transactions",
        "display_name": "美股內部人交易",
        "description": "公司內部人買賣記錄",
        "category": "us_stock",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": 0,
        "daily_limit_plus": 0,
        "daily_limit_prem": None,
    },
    # ── 普惠金融示範（Trustworthy AI Hackathon — Consent Gate demo）─────────────
    {
        "tool_id": "submit_kyc_application",
        "display_name": "普惠金融開戶申請",
        "description": "移工/新住民以 TON 錢包為數位身分申請數位銀行開戶（KYC）。高風險動作，需使用者同意。",
        "category": "finance",
        "tier_required": "free",
        "quota_type": "shared_limited",
        "daily_limit_free": 3,
        "daily_limit_plus": 10,
        "daily_limit_prem": 20,
    },
    # ── 投資帳本（2026-08-21 design，DANNY Approve）──
    {
        "tool_id": "record_entry",
        "display_name": "統一帳本記錄",
        "description": "在使用者的統一帳本記錄一筆（支出/收入/投資；支援多幣種自動匯率）。",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "query_ledger",
        "display_name": "查詢帳本記錄",
        "description": "查詢使用者的帳本記錄（支援類別/期間/幣別篩選）。",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "delete_ledger_entry",
        "display_name": "刪除帳本單筆記錄",
        "description": "提議刪除帳本中的一筆記錄（軟刪除；需使用者確認卡核准）。",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "update_ledger_entry",
        "display_name": "修改帳本單筆記錄",
        "description": "提議修改帳本中一筆記錄的金額/幣別/類別/備註（需使用者確認卡核准）。",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_portfolio_pnl",
        "display_name": "持倉損益計算",
        "description": "計算使用者全部持倉的損益（加權平均成本，接市場現價）。",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── TON 錢包餘額（get_ton_balance：bootstrap 註冊 + DB seed 雙路徑都要有，
    # 否則 get_allowed_tools SQL JOIN 排除 → LLM 工具池缺失。原與 swap 同區塊，
    # swap 移除後保留本工具）──
    {
        "tool_id": "get_ton_balance",
        "display_name": "TON 錢包餘額",
        "description": "查詢 TON 錢包的 TON 餘額（唯讀，沿用 toncenter API）。",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── TON 錢包查詢（查自己錢包是核心 UX，free）──
    {
        "tool_id": "get_ton_jetton_balances",
        "display_name": "TON Jetton 餘額",
        "description": "查詢 TON 錢包持有的所有 jetton 代幣餘額（含 USD/TON 估值）。",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_my_wallet_overview",
        "display_name": "錢包持倉總覽",
        "description": "查詢綁定錢包的鏈上持倉（EVM：ETH／USDC；TON：TON＋jetton）與 USD 估值。",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── Onchain / DEX（公開鏈上資料，free）──
    {
        "tool_id": "get_gas_fees",
        "display_name": "Gas 費用",
        "description": "查詢當前區塊鏈 Gas 費用（ETH 等）。",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_exchange_flow",
        "display_name": "交易所資金流",
        "description": "查詢交易所資金流入/流出（鯨魚動向）。",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_staking_yield",
        "display_name": "質押收益率",
        "description": "查詢主流代幣的質押收益率。",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_dex_pair_info",
        "display_name": "DEX 交易對",
        "description": "查詢 DEX 交易對資訊（流動性/交易量）。",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_trending_dex_pairs",
        "display_name": "熱門 DEX 交易對",
        "description": "查詢當下熱門的 DEX 交易對。",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_contract_info",
        "display_name": "合約資訊",
        "description": "查詢 EVM 合約地址資訊（Ethereum/BSC/Polygon 等 11 鏈，Etherscan）。",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_eth_price_etherscan",
        "display_name": "ETH 價格",
        "description": "查詢 ETH 當前價格（Etherscan）。",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── 商品（premium 高階分析）──
    {
        "tool_id": "get_commodity_price",
        "display_name": "商品價格",
        "description": "查詢大宗商品（黃金/原油/銅等）現貨價格。",
        "category": "commodity",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_commodity_futures_price",
        "display_name": "商品期貨",
        "description": "查詢大宗商品期貨價格。",
        "category": "commodity",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_all_commodities_prices",
        "display_name": "全商品價格",
        "description": "查詢所有大宗商品價格一覽。",
        "category": "commodity",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_gold_silver_ratio",
        "display_name": "金銀比",
        "description": "查詢黃金/白銀價格比（避險指標）。",
        "category": "commodity",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_oil_price_analysis",
        "display_name": "油價分析",
        "description": "查詢原油價格與分析。",
        "category": "commodity",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── 外匯（premium）──
    {
        "tool_id": "get_forex_rate",
        "display_name": "匯率查詢",
        "description": "查詢外匯匯率（USD/TWD 等）。",
        "category": "forex",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_all_forex_rates",
        "display_name": "全匯率",
        "description": "查詢所有主要外匯匯率。",
        "category": "forex",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_usd_twd_rate",
        "display_name": "台幣匯率",
        "description": "查詢 USD/TWD 匯率。",
        "category": "forex",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── 全球股票（premium）──
    {
        "tool_id": "global_stock_price",
        "display_name": "全球股價",
        "description": "查詢全球股票價格。",
        "category": "global_stock",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "global_stock_technical",
        "display_name": "全球股技術面",
        "description": "全球股票技術分析。",
        "category": "global_stock",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "global_stock_fundamentals",
        "display_name": "全球股基本面",
        "description": "全球股票基本面分析。",
        "category": "global_stock",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "global_stock_news",
        "display_name": "全球股新聞",
        "description": "全球股票相關新聞。",
        "category": "global_stock",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "global_stock_snapshot",
        "display_name": "全球股快照",
        "description": "全球股票綜合快照。",
        "category": "global_stock",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── 總經指標（premium）──
    {
        "tool_id": "get_market_indices",
        "display_name": "市場指數",
        "description": "查詢主要市場指數（S&P500/那斯達克等）。",
        "category": "macro",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_vix_index",
        "display_name": "VIX 指數",
        "description": "查詢 VIX 恐慌指數。",
        "category": "macro",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_sp500_performance",
        "display_name": "S&P500 表現",
        "description": "查詢 S&P500 指數表現。",
        "category": "macro",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_sector_performance",
        "display_name": "板塊表現",
        "description": "查詢美股板塊表現。",
        "category": "macro",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── 台/美股快照（premium）──
    {
        "tool_id": "tw_stock_snapshot",
        "display_name": "台股快照",
        "description": "台股綜合快照。",
        "category": "tw_stock",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "tw_market_index",
        "display_name": "台股大盤指數",
        "description": "查詢台股大盤指數。",
        "category": "tw_stock",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "us_stock_snapshot",
        "display_name": "美股快照",
        "description": "美股綜合快照。",
        "category": "us_stock",
        "tier_required": "premium",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── 雜項（free）──
    {
        "tool_id": "resolve_symbol",
        "display_name": "代碼解析",
        "description": "解析股票/加密貨幣代碼（symbol → 資產識別）。",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── Agent 內建能力（free，無配額限制）──────────────────────────────────────
    # 這些工具在 bootstrap.py 有註冊到 tool_registry，但若沒 seed 進 DB，
    # get_allowed_tools 的 SQL JOIN 會把它們排除 → LLM 工具池缺它們（同 get_ton_balance
    # 之前踩過的 bug）。HITL/記憶/方法管理是核心 UX，必須在正常 DB 路徑也可用。
    {
        "tool_id": "clarify",
        "display_name": "釐清問題",
        "description": "向使用者提出釐清問題（範圍/標的不明時）。",
        "category": "general",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "load_skill",
        "display_name": "載入分析方法",
        "description": "載入分析方法 skill 的完整內容（方法步驟＋輸出格式）。",
        "category": "general",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "load_knowledge",
        "display_name": "查詢個人知識庫",
        "description": "查詢個人知識庫中與主題相關的過去分析（LLM Wiki）。",
        "category": "general",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "remember",
        "display_name": "記住資訊",
        "description": "記住使用者主動說的偏好/背景/持倉（HITL 同意後才寫入）。",
        "category": "general",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "add_calendar_event",
        "display_name": "加入行事曆",
        "description": "把一個日期事件加進使用者行事曆（財報、繳費…），早報前一天與當天提醒。",
        "category": "general",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "list_calendar_events",
        "display_name": "查看行事曆",
        "description": "列出接下來 N 天的行事曆事件（系統財報／月營收＋自訂）。",
        "category": "general",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    # ── public-apis 挑進來的免金鑰工具（2026-09-13）────────────────────────
    {
        "tool_id": "sec_filings",
        "display_name": "SEC 申報文件",
        "description": "美股公司最近的 SEC 申報（8-K、10-Q／10-K、Form 4 內部人、13D／13G），附官方連結。",
        "category": "us_stock",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_btc_network_status",
        "display_name": "比特幣網路狀態",
        "description": "BTC 手續費建議、mempool 壅塞、區塊高度、難度調整、下一次減半（mempool.space）。",
        "category": "crypto_basic",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "get_my_scorecard",
        "display_name": "我的判斷成績",
        "description": "帳本交易到期對答案、扣大盤後的命中率與超額報酬（只回本人）。",
        "category": "general",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "record_call",
        "display_name": "記一筆判斷",
        "description": "使用者明講看多／看空與期限就記一筆，到期用收盤價評分（確認卡核准才寫）。",
        "category": "general",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "list_my_skills_memory",
        "display_name": "列出我的分析方法與記憶",
        "description": "列出使用者自己的分析方法（custom skills）與記憶（唯讀）。",
        "category": "general",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
    {
        "tool_id": "propose_custom_skill",
        "display_name": "提議建立/修改/刪除分析方法",
        "description": "提議建立/修改/刪除個人分析方法（HITL 同意後才寫入）。",
        "category": "general",
        "tier_required": "free",
        "quota_type": "unlimited",
        "daily_limit_free": None,
        "daily_limit_plus": None,
        "daily_limit_prem": None,
    },
]

# ──────────────────────────────────────────────────────────────────────────────
# Tool 風險分級（Consent Gate 用）
# ──────────────────────────────────────────────────────────────────────────────
# low（預設）：純查詢，無 side effect。
# medium：讀外部不可信內容（新聞、網頁、地址交易歷史）— 內容可能含誤導/釣魚。
# high：接觸錢包資產、對外發送、變更狀態 — 執行前需使用者 explicit consent。
#
# 未列出的 tool_id 一律 low。這份 dict 是單一真相來源，_TOOLS_SEED 與
# ToolMetadata 註冊（bootstrap.py）都從這裡讀，確保 DB catalog / runtime / 前端三者一致。
_TOOL_RISK_LEVELS: Dict[str, str] = {
    # ── high：接觸錢包資產 / 變更狀態 / 對外發送 ──
    "get_eth_balance": "high",  # 讀錢包 ETH 餘額（鏈上資產查詢）
    "get_erc20_token_balance": "high",  # 讀錢包代幣餘額（鏈上資產查詢）
    "get_address_transactions": "high",  # 讀錢包完整交易歷史（財務隱私）
    "get_whale_alerts": "high",  # 追蹤大額鏈上轉帳（金融動向）
    "submit_kyc_application": "high",  # 普惠金融開戶（變更金融狀態，需 consent）
    "propose_custom_skill": "high",  # 改使用者分析方法（HITL consent gate，PR #429）
    # ── medium：讀外部不可信內容 ──
    "fetch_url": "medium",  # 抓任意外部網頁全文（不可信內容）
    "web_search": "medium",  # 通用網路搜尋（不可信來源）
    "google_news": "medium",  # 外部新聞
    "aggregate_news": "medium",  # 多來源新聞聚合
    "tw_news": "medium",
    "us_news": "medium",
    "tw_major_news": "medium",
    "check_token_security": "medium",  # 讀外部 GoPlus 報告
    "check_address_safety": "medium",  # 讀外部 GoPlus 報告
    "assess_jetton_safety": "low",  # TON jetton 安全查詢（唯讀，TonAPI 公開端點）
    # 其餘（get_crypto_price / technical_analysis / 台股美股行情與基本面等）= low
}


def get_tool_risk_level(tool_id: str) -> str:
    """取得 tool 的風險等級（low/medium/high）。未列出者回 'low'。"""
    level = _TOOL_RISK_LEVELS.get(tool_id, "low")
    return level if level in {"low", "medium", "high"} else "low"


def register_mcp_risk_overrides(overrides: Dict[str, str]) -> None:
    """把 MCP tool 的風險覆寫合併進 _TOOL_RISK_LEVELS（單一真相來源）。

    MCP tool 預設 low；唯有經此註冊的 high/medium 覆寫才會生效，確保
    bootstrap 的 risk_level 注入（get_tool_risk_level）能正確讀到 MCP tool
    的風險等級，不會把 high-risk MCP tool 誤覆蓋回 low 而繞過 Consent Gate。
    只接受合法值；同名既有 tool 的風險等級不被 MCP 覆寫（既有工具優先）。
    """
    for name, level in overrides.items():
        if level not in {"low", "medium", "high"}:
            continue
        # 既有（自家）tool 的風險等級不被 MCP 覆蓋
        if name in _TOOL_RISK_LEVELS:
            continue
        _TOOL_RISK_LEVELS[name] = level


# Agent 預設工具清單（bootstrap.py 的 fallback）
_AGENT_DEFAULT_TOOLS: Dict[str, List[str]] = {
    "crypto": [
        "get_current_time_taipei",
        "technical_analysis",
        "get_crypto_price",
        "google_news",
        "aggregate_news",
        "web_search",
        "fetch_url",
        "get_fear_and_greed_index",
        "get_trending_tokens",
        "get_crypto_market_cap",
        "get_economic_calendar",
        "get_futures_data",
        "get_defillama_tvl",
        "get_crypto_categories_and_gainers",
        "get_token_unlocks",
        "get_token_supply",
        "get_dex_volume",
        "get_whale_alerts",
        # TON 錢包查詢
        "get_ton_balance",
        "get_ton_jetton_balances",
        "get_my_wallet_overview",
        # Onchain / DEX（公開鏈上資料）
        "get_gas_fees",
        "get_btc_network_status",
        "get_exchange_flow",
        "get_staking_yield",
        "get_dex_pair_info",
        "get_trending_dex_pairs",
        "get_contract_info",
        "get_eth_price_etherscan",
        # BYOK 工具（需使用者自帶金鑰，未設定則被 get_allowed_tools 排除）
        "get_cmc_quote",
        "get_eth_balance",
        "get_erc20_token_balance",
        "get_address_transactions",
        "get_central_bank_rates",
        # 安全檢測（GoPlus EVM + TonAPI TON）
        "check_token_security",
        "check_address_safety",
        "assess_jetton_safety",
        # 普惠金融示範（Trustworthy AI Hackathon — Consent Gate demo）
        "submit_kyc_application",
        # 投資帳本（2026-08-21 design，DANNY Approve）
        "record_entry",
        "query_ledger",
        "get_portfolio_pnl",
        "delete_ledger_entry",
        "update_ledger_entry",
    ],
    "tw_stock": [
        "get_current_time_taipei",
        "tw_stock_price",
        "tw_technical_analysis",
        "tw_fundamentals",
        "tw_institutional",
        "tw_news",
        "tw_major_news",
        "tw_pe_ratio",
        "tw_monthly_revenue",
        "tw_dividend",
        "tw_foreign_top20",
        "web_search",
        "fetch_url",
    ],
    "us_stock": [
        "us_stock_price",
        "us_technical_analysis",
        "us_fundamentals",
        "us_earnings",
        "us_news",
        "us_institutional_holders",
        "us_insider_transactions",
        "sec_filings",
        "get_current_time_taipei",
    ],
    "chat": [
        "get_current_time_taipei",
        "get_crypto_price",
        "tw_stock_price",
        "web_search",
        "fetch_url",
        # BYOK 工具
        "get_cmc_quote",
        "get_central_bank_rates",
    ],
    # cryptomind（CLAW 全能 agent）：get_allowed_tools 對它走 fallback
    # （_get_fallback_tools 用整個 _TOOLS_SEED），所以這裡的清單主要影響
    # 「DB 已 seed 但 agent_tool_permissions 沒 cryptomind 記錄」時的行為。
    # 列出所有工具確保 DB query 路徑也放行。
    "cryptomind": [
        # crypto 全套
        "get_current_time_taipei",
        "technical_analysis",
        "get_crypto_price",
        "google_news",
        "aggregate_news",
        "web_search",
        "fetch_url",
        "get_fear_and_greed_index",
        "get_trending_tokens",
        "get_crypto_market_cap",
        "get_economic_calendar",
        "get_futures_data",
        "get_defillama_tvl",
        "get_crypto_categories_and_gainers",
        "get_token_unlocks",
        "get_token_supply",
        "get_dex_volume",
        "get_whale_alerts",
        "get_cmc_quote",
        "get_eth_balance",
        "get_erc20_token_balance",
        "get_address_transactions",
        "get_central_bank_rates",
        "check_token_security",
        "check_address_safety",
        "assess_jetton_safety",
        "get_ton_balance",
        # TON 錢包查詢（M3 錢包功能）
        "get_ton_jetton_balances",
        "get_my_wallet_overview",
        # Onchain / DEX（公開鏈上資料）
        "get_gas_fees",
        "get_btc_network_status",
        "get_exchange_flow",
        "get_staking_yield",
        "get_dex_pair_info",
        "get_trending_dex_pairs",
        "get_contract_info",
        "get_eth_price_etherscan",
        # Commodity（premium 高階分析）
        "get_commodity_price",
        "get_commodity_futures_price",
        "get_all_commodities_prices",
        "get_gold_silver_ratio",
        "get_oil_price_analysis",
        # Forex（premium）
        "get_forex_rate",
        "get_all_forex_rates",
        "get_usd_twd_rate",
        # Global stock（premium）
        "global_stock_price",
        "global_stock_technical",
        "global_stock_fundamentals",
        "global_stock_news",
        "global_stock_snapshot",
        # Macro 總經（premium）
        "get_market_indices",
        "get_vix_index",
        "get_sp500_performance",
        "get_sector_performance",
        # tw / us / finance
        "tw_stock_price",
        "tw_technical_analysis",
        "tw_fundamentals",
        "tw_institutional",
        "tw_news",
        "tw_major_news",
        "tw_pe_ratio",
        "tw_monthly_revenue",
        "tw_dividend",
        "tw_foreign_top20",
        "tw_stock_snapshot",
        "tw_market_index",
        "us_stock_price",
        "us_technical_analysis",
        "us_news",
        "us_fundamentals",
        "us_earnings",
        "us_institutional_holders",
        "us_insider_transactions",
        "us_stock_snapshot",
        "sec_filings",
        # 免金鑰鏈上狀態（2026-09-13）
        "get_btc_network_status",
        # 工具
        "resolve_symbol",
        "submit_kyc_application",
        # Agent 內建能力（HITL / 記憶 / 方法管理 / 知識庫 / 釐清）
        "clarify",
        "load_skill",
        "load_knowledge",
        "remember",
        "list_my_skills_memory",
        "propose_custom_skill",
        # 行事曆（每日早報 Phase B）
        "add_calendar_event",
        "list_calendar_events",
        # 判斷成績（2026-09-13）＋明確喊單（v2）
        "get_my_scorecard",
        "record_call",
        # 投資帳本（金流 HITL——2026-08-22 盤點：先前只加在 "crypto" 類別，
        # cryptomind agent 的清單漏了 → agent 看不到工具、記帳全走不上）
        "record_entry",
        "query_ledger",
        "get_portfolio_pnl",
        "delete_ledger_entry",
        "update_ledger_entry",
    ],
    # Commodity agent（commodity-analysis skill 對應）
    "commodity": [
        "get_commodity_price",
        "get_commodity_futures_price",
        "get_all_commodities_prices",
        "get_gold_silver_ratio",
        "get_oil_price_analysis",
        "web_search",
        "fetch_url",
    ],
    # Forex agent（forex-macro-analysis skill 對應）
    "forex": [
        "get_forex_rate",
        "get_all_forex_rates",
        "get_usd_twd_rate",
        "get_central_bank_rates",
        "web_search",
        "fetch_url",
    ],
    # Global stock agent（global-stock-analysis skill 對應）
    "global_stock": [
        "global_stock_price",
        "global_stock_technical",
        "global_stock_fundamentals",
        "global_stock_news",
        "global_stock_snapshot",
        "web_search",
        "fetch_url",
    ],
    # Economic agent（macro-economic-indicators skill 對應）
    "economic": [
        "get_market_indices",
        "get_vix_index",
        "get_sp500_performance",
        "get_sector_performance",
        "get_economic_calendar",
        "web_search",
        "fetch_url",
    ],
}


# ============================================================================
# DB 操作函數
# ============================================================================

# 會員等級權限順序
TIER_HIERARCHY = {"free": 0, "premium": 1}

_TIER_ALIASES = {
    "free": "free",
    "premium": "premium",
    "plus": "premium",
    "pro": "premium",
}


def normalize_membership_tier(tier: Optional[str]) -> str:
    """Normalize legacy membership names to tool-system tiers."""
    return _TIER_ALIASES.get((tier or "free").strip().lower(), "free")


def _get_tier_level(tier: str) -> int:
    """取得會員等級數值"""
    return TIER_HIERARCHY.get(normalize_membership_tier(tier), 0)


def seed_tools_catalog():
    """
    首次執行時把所有工具 metadata 寫入 tools_catalog 和 agent_tool_permissions。
    使用 INSERT ... ON CONFLICT DO NOTHING 確保冪等（可重複執行）。
    """
    conn = get_connection()
    c = conn.cursor()
    try:
        # 防禦性：確保 risk_level 欄位存在（reconcile 可能因背景 init 競態未跑完）
        c.execute(
            "ALTER TABLE tools_catalog ADD COLUMN IF NOT EXISTS risk_level TEXT DEFAULT 'low'"
        )
        c.execute(
            "ALTER TABLE tools_catalog ADD COLUMN IF NOT EXISTS key_provider TEXT"
        )
        c.execute(
            "ALTER TABLE tools_catalog ADD COLUMN IF NOT EXISTS key_mode TEXT DEFAULT 'none'"
        )
        c.execute(
            "ALTER TABLE tools_catalog ADD COLUMN IF NOT EXISTS daily_limit_plus INTEGER"
        )
        # 1. Seed tools_catalog
        for t in _TOOLS_SEED:
            c.execute(
                """
                INSERT INTO tools_catalog
                    (tool_id, display_name, description, category,
                     tier_required, quota_type, daily_limit_free, daily_limit_plus, daily_limit_prem,
                     source_type, key_provider, key_mode, risk_level)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'native', %s, %s, %s)
                ON CONFLICT (tool_id) DO UPDATE SET
                    key_provider = EXCLUDED.key_provider,
                    key_mode = EXCLUDED.key_mode,
                    risk_level = EXCLUDED.risk_level
            """,
                (
                    t["tool_id"],
                    t["display_name"],
                    t["description"],
                    t["category"],
                    t["tier_required"],
                    t["quota_type"],
                    t.get("daily_limit_free"),
                    t.get("daily_limit_plus"),
                    t.get("daily_limit_prem"),
                    t.get("key_provider"),
                    t.get("key_mode", "none"),
                    get_tool_risk_level(t["tool_id"]),
                ),
            )

        # 2. Seed agent_tool_permissions
        for agent_id, tools in _AGENT_DEFAULT_TOOLS.items():
            for tool_id in tools:
                c.execute(
                    """
                    INSERT INTO agent_tool_permissions (agent_id, tool_id, is_enabled)
                    VALUES (%s, %s, TRUE)
                    ON CONFLICT (agent_id, tool_id) DO NOTHING
                """,
                    (agent_id, tool_id),
                )

        # 3. Deactivate obsolete tools — any active tool_id no longer in _TOOLS_SEED.
        # Fixes DB residue from commit 1591030 (legacy tools removed from source but not DB).
        seed_ids = tuple(t["tool_id"] for t in _TOOLS_SEED)
        if seed_ids:
            c.execute(
                """
                UPDATE tools_catalog
                SET is_active = FALSE
                WHERE is_active = TRUE
                  AND tool_id NOT IN %s
            """,
                (seed_ids,),
            )
            deactivated = c.rowcount
            if deactivated:
                logger.info(
                    "[seed_tools_catalog] deactivated %d obsolete tool(s) no longer in _TOOLS_SEED",
                    deactivated,
                )

        conn.commit()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"[seed_tools_catalog] error: {e}")
    finally:
        conn.close()


def get_tools_catalog_fallback(user_tier: str = "free") -> List[Dict[str, Any]]:
    """Return a static frontend-safe tool list when DB-backed catalog is unavailable."""
    normalized_tier = normalize_membership_tier(user_tier)
    user_tier_level = _get_tier_level(normalized_tier)

    return [
        {
            "tool_id": tool["tool_id"],
            "display_name": tool["display_name"],
            "description": tool["description"],
            "category": tool["category"],
            "tier_required": tool["tier_required"],
            "quota_type": tool["quota_type"],
            "is_enabled": True,
            "locked": _get_tier_level(tool["tier_required"]) > user_tier_level,
            "key_provider": tool.get("key_provider"),
            "key_mode": tool.get("key_mode", "none"),
            "risk_level": get_tool_risk_level(tool["tool_id"]),
            "key_status": (
                "missing" if tool.get("key_mode") == "byok" else "not_required"
            ),
            "has_user_key": False,
        }
        for tool in _TOOLS_SEED
    ]


def get_allowed_tools(
    agent_id: str, user_tier: str = "free", user_id: Optional[str] = None
) -> List[str]:
    """
    取得某 agent 對特定用戶可用的工具清單。

    過濾邏輯：
    1. tools_catalog.is_active = TRUE
    2. agent_tool_permissions.is_enabled = TRUE
    3. tools_catalog.tier_required <= user_tier
       (premium > free)
    4. （可選）用戶偏好：user_tool_preferences.is_enabled = FALSE 的排除

    若 DB 資料為空（首次啟動前尚未 seed），回傳 hardcode fallback。
    """
    user_tier = normalize_membership_tier(user_tier)
    conn = get_connection()
    c = conn.cursor()
    try:
        # 組合 tier 條件（二級會員）
        user_tier_level = _get_tier_level(user_tier)
        allowed_tiers = [
            tier for tier, level in TIER_HIERARCHY.items() if level <= user_tier_level
        ]

        query = """
            SELECT tc.tool_id, tc.key_mode, tc.key_provider
            FROM tools_catalog tc
            JOIN agent_tool_permissions atp
                ON tc.tool_id = atp.tool_id AND atp.agent_id = %s
            WHERE tc.is_active = TRUE
              AND atp.is_enabled = TRUE
              AND tc.tier_required = ANY(%s)
        """

        # 排除用戶主動關閉的工具（Premium 功能）
        if user_id and user_tier == "premium":
            query = """
                SELECT tc.tool_id, tc.key_mode, tc.key_provider
                FROM tools_catalog tc
                JOIN agent_tool_permissions atp
                    ON tc.tool_id = atp.tool_id AND atp.agent_id = %s
                LEFT JOIN user_tool_preferences utp
                    ON tc.tool_id = utp.tool_id AND utp.user_id = %s
                WHERE tc.is_active = TRUE
                  AND atp.is_enabled = TRUE
                  AND tc.tier_required = ANY(%s)
                  AND (utp.is_enabled IS NULL OR utp.is_enabled = TRUE)
            """
            c.execute(query, (agent_id, user_id, allowed_tiers))
        else:
            c.execute(query, (agent_id, allowed_tiers))

        rows = c.fetchall()

        # 強制金鑰工具（key_mode='required'）：使用者未設定該 provider 金鑰時排除
        required_providers = {
            r[2] for r in rows if (r[1] or "none") == "required" and r[2]
        }
        user_key_providers: set = set()
        if required_providers and user_id:
            c.execute(
                """
                SELECT provider FROM user_api_keys
                WHERE user_id = %s AND key_kind = 'tool'
                """,
                (user_id,),
            )
            user_key_providers = {kr[0] for kr in c.fetchall()}
        rows = [
            r
            for r in rows
            if (r[1] or "none") != "required" or (r[2] and r[2] in user_key_providers)
        ]

        if not rows:
            # 若 catalog / permissions 已存在，但因 tier 或 user preference 篩掉全部，
            # 必須回傳空清單，而不是誤判成未 seed 後放回 fallback 工具。
            c.execute(
                """
                SELECT 1
                FROM agent_tool_permissions atp
                JOIN tools_catalog tc ON tc.tool_id = atp.tool_id
                WHERE atp.agent_id = %s
                LIMIT 1
            """,
                (agent_id,),
            )
            seeded = c.fetchone()
            if seeded:
                return []
            # Fallback：DB 還沒 seed 時用 hardcode 清單
            return _get_fallback_tools(agent_id, user_tier)

        return [row[0] for row in rows]

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"[get_allowed_tools] error: {e}")
        return _get_fallback_tools(agent_id, user_tier)
    finally:
        conn.close()


def _get_fallback_tools(agent_id: str, user_tier: str) -> List[str]:
    """DB 不可用時的 hardcode fallback（與原 bootstrap.py 一致）"""
    user_tier = normalize_membership_tier(user_tier)

    if agent_id == "cryptomind":
        all_tool_ids = [t["tool_id"] for t in _TOOLS_SEED]
    else:
        all_tools = _AGENT_DEFAULT_TOOLS.get(agent_id, [])
        all_tool_ids = all_tools

    user_tier_level = _get_tier_level(user_tier)

    allowed_tools = []
    for t in _TOOLS_SEED:
        tool_tier_level = _get_tier_level(t["tier_required"])
        if tool_tier_level <= user_tier_level and t["tool_id"] in all_tool_ids:
            allowed_tools.append(t["tool_id"])

    return allowed_tools


def check_and_increment_tool_quota(user_id: str, tool_id: str, user_tier: str) -> bool:
    """
    Atomically check tool quota and increment usage in a single transaction.
    Prevents race conditions between check and increment.

    Returns True if the tool can be used (quota available), False if limit reached.
    """
    user_tier = normalize_membership_tier(user_tier)
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT quota_type, daily_limit_free, daily_limit_plus, daily_limit_prem
            FROM tools_catalog WHERE tool_id = %s AND is_active = TRUE
        """,
            (tool_id,),
        )
        row = c.fetchone()

        if not row:
            return True

        quota_type, limit_free, limit_plus, limit_prem = row

        if quota_type == "unlimited":
            return True

        if user_tier == "premium":
            limit = limit_prem if limit_prem is not None else limit_plus
        else:
            limit = limit_free

        if limit is None:
            return True
        if limit == 0:
            return False

        c.execute(
            """
            INSERT INTO tool_usage_log (user_id, tool_id, used_date, call_count)
            VALUES (%s, %s, CURRENT_DATE, 1)
            ON CONFLICT (user_id, tool_id, used_date)
            DO UPDATE SET call_count = tool_usage_log.call_count + 1
            RETURNING call_count
        """,
            (user_id, tool_id),
        )
        usage_row = c.fetchone()
        conn.commit()

        used = usage_row[0] if usage_row else 1
        return used <= limit

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"[check_and_increment_tool_quota] error: {e}")
        conn.rollback()
        return True
    finally:
        conn.close()


def check_tool_quota(user_id: str, tool_id: str, user_tier: str) -> bool:
    """
    檢查用戶今日對某工具是否還有額度。
    回傳 True = 可以使用；False = 已達上限。

    支援二級會員：free / premium
    """
    user_tier = normalize_membership_tier(user_tier)
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            SELECT quota_type, daily_limit_free, daily_limit_plus, daily_limit_prem
            FROM tools_catalog WHERE tool_id = %s AND is_active = TRUE
        """,
            (tool_id,),
        )
        row = c.fetchone()

        if not row:
            return True  # 找不到 → 不限制

        quota_type, limit_free, limit_plus, limit_prem = row

        if quota_type == "unlimited":
            return True

        # Premium 沿用 premium 欄位；若舊資料只填了 plus 欄位，仍視為 premium 額度。
        if user_tier == "premium":
            limit = limit_prem if limit_prem is not None else limit_plus
        else:
            limit = limit_free

        if limit is None:
            return True  # NULL = 無限
        if limit == 0:
            return False  # 0 = 完全不開放

        # 查今日使用量
        c.execute(
            """
            SELECT call_count FROM tool_usage_log
            WHERE user_id = %s AND tool_id = %s AND used_date = CURRENT_DATE
        """,
            (user_id, tool_id),
        )
        usage_row = c.fetchone()
        used = usage_row[0] if usage_row else 0

        return used < limit

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"[check_tool_quota] error: {e}")
        return True  # 錯誤時放行，不影響用戶體驗
    finally:
        conn.close()


def increment_tool_usage(user_id: str, tool_id: str):
    """記錄工具呼叫一次（upsert）"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            INSERT INTO tool_usage_log (user_id, tool_id, used_date, call_count)
            VALUES (%s, %s, CURRENT_DATE, 1)
            ON CONFLICT (user_id, tool_id, used_date)
            DO UPDATE SET call_count = tool_usage_log.call_count + 1
        """,
            (user_id, tool_id),
        )
        conn.commit()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"[increment_tool_usage] error: {e}")
        conn.rollback()
    finally:
        conn.close()


def get_tools_for_frontend(user_tier: str, user_id: Optional[str] = None) -> List[Dict]:
    """
    回傳前端設定頁需要的工具清單。
    包含每個工具的 display_name、category、tier_required、
    以及用戶當前的 is_enabled 狀態（Premium 才有個人偏好）。
    """
    user_tier = normalize_membership_tier(user_tier)
    conn = get_connection()
    c = conn.cursor()
    try:
        if user_id and user_tier == "premium":
            c.execute(
                """
                SELECT tc.tool_id, tc.display_name, tc.description, tc.category,
                       tc.tier_required, tc.quota_type,
                       COALESCE(utp.is_enabled, TRUE) AS is_enabled
                FROM tools_catalog tc
                LEFT JOIN user_tool_preferences utp
                    ON tc.tool_id = utp.tool_id AND utp.user_id = %s
                WHERE tc.is_active = TRUE
                ORDER BY tc.category, tc.tier_required, tc.tool_id
            """,
                (user_id,),
            )
        else:
            c.execute("""
                SELECT tool_id, display_name, description, category,
                       tier_required, quota_type, TRUE AS is_enabled
                FROM tools_catalog
                WHERE is_active = TRUE
                ORDER BY category, tier_required, tool_id
            """)

        rows = c.fetchall()
        user_tier_level = _get_tier_level(user_tier)

        return [
            {
                "tool_id": r[0],
                "display_name": r[1],
                "description": r[2],
                "category": r[3],
                "tier_required": r[4],
                "quota_type": r[5],
                "is_enabled": r[6],
                "locked": _get_tier_level(r[4]) > user_tier_level,
            }
            for r in rows
        ]
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"[get_tools_for_frontend] error: {e}")
        return []
    finally:
        conn.close()


def update_user_tool_preference(user_id: str, tool_id: str, is_enabled: bool):
    """更新用戶對某工具的個人偏好（僅 Premium 可用）"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute(
            """
            INSERT INTO user_tool_preferences (user_id, tool_id, is_enabled, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (user_id, tool_id)
            DO UPDATE SET is_enabled = EXCLUDED.is_enabled, updated_at = NOW()
        """,
            (user_id, tool_id, is_enabled),
        )
        conn.commit()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"[update_user_tool_preference] error: {e}")
        conn.rollback()
    finally:
        conn.close()
