"""
Agent V4 — Tool Definitions

All tools use LangChain @tool decorator.
Each agent gets only the tools it should use (set in bootstrap.py).
"""

from langchain_core.tools import tool

# ============================================
# 新聞工具
# ============================================


@tool
def google_news(symbol: str, limit: int = 5) -> list:
    """獲取加密貨幣**近期一般新聞**（從 Google News RSS，過去 7 天內）。

    ⚠️ symbol 為必填參數（無預設值），請務必傳入用戶詢問的具體幣種代號。

    ⚠️ 適用場景：用戶問某幣種「最近新聞」「最新動態」「近期消息」（最近 7 天）。

    ⚠️ 不適用場景（改用 web_search）：
    - 「安全事件」「漏洞」「被駭」「exploit」「hack」→ 用 web_search 搜「{symbol} security hack vulnerability」
    - 「近一年/近半年/歷史」等超過 7 天的回顧 → 用 web_search
    - 特定主題（監管、訴訟、技術升級）→ 用 web_search 搜更具體的關鍵字
    本工具只搜 symbol 名稱，無法依主題/時間範圍過濾，上述場景會搜不到。

    ⚠️ 引用規則（重要！）：
    - published_at 是 Google News 的**收錄時間**（date_semantics="feed_ingestion_time"），
      **不是**原文發表時間——轉載／重新收錄會帶新的時間戳，4 月的舊聞可能帶本週時間戳。
      要把事件寫成「近期／剛發生」前，先用 web_search 對事件本身交叉確認實際日期；
      無法確認就標註「收錄時間為 X，原文日期未確認」，**禁止**把收錄時間當發表日期引用。
    - 已過濾 ticker 撞名的非加密新聞（如 EnerSys NYSE:ENS），但縮寫標的仍須自行確認主體。
    - **禁止**用訓練資料補充任何「具體日期」「KYC deadline」「用戶數」「時程」
    - 標題沒提到的事實不要編造（如：標題說 "Pi Network solves problem"，不要寫成 "KYC 截止 2024/11/30"）

    Args:
        symbol: 幣種代碼（必填，例：BTC, ETH, PI, DOGE）
        limit: 返回新聞數量，預設 5

    Returns:
        list of dict，含 title / url / description / source / published_at（GMT 時間字串）
        / date_semantics（日期語意標注）
    """
    from utils.utils import get_crypto_news_google

    return get_crypto_news_google(symbol=symbol, limit=limit)


@tool
def aggregate_news(symbol: str, limit: int = 5) -> list:
    """從多個來源聚合加密貨幣**近期一般新聞**（Google News + CryptoPanic + NewsAPI 等）。

    ⚠️ symbol 為必填參數（無預設值），請務必傳入用戶詢問的具體幣種代號。

    ⚠️ 適用場景：用戶問某幣種「最近新聞」「最新動態」「市場情緒」（最近 7 天）。
    比 google_news 更完整（多來源），建議優先選此。

    ⚠️ 不適用場景（改用 web_search）：
    - 「安全事件」「漏洞」「被駭」「exploit」「hack」→ 用 web_search 搜「{symbol} security hack vulnerability」
    - 「近一年/近半年/歷史」等超過 7 天的回顧 → 用 web_search
    - 特定主題（監管、訴訟、技術升級）→ 用 web_search 搜更具體的關鍵字
    本工具只搜 symbol 名稱，無法依主題/時間範圍過濾，上述場景會搜不到。

    ⚠️ 引用規則：每則新聞含 published_at 欄位——Google 來源的值是**收錄時間**
    （date_semantics="feed_ingestion_time"），轉載會帶新時間戳，不是原文發表日；
    其餘來源（CryptoCompare 等）為真實發布時間。要把事件稱為「近期」且日期影響
    結論時，先用 web_search 交叉確認；無法確認就標「收錄時間」，**禁止**把
    Google 的收錄時間當發表日期引用。**禁止**用訓練資料補「KYC 進度」「用戶數」
    「具體截止日」「時程規劃」等細節。

    Args:
        symbol: 幣種代碼（必填，例：BTC, ETH, PI, DOGE）
        limit: 返回新聞數量，預設 5
    """
    from utils.utils import get_crypto_news

    return get_crypto_news(symbol=symbol, limit=limit)


# ============================================
# 技術分析工具
# ============================================


@tool
def technical_analysis(symbol: str, interval: str = "1d") -> dict:
    """獲取加密貨幣的技術指標（RSI, MACD, 均線等）。
    ⚠️ symbol 為必填參數（無預設值），請務必傳入用戶詢問的具體幣種代號。
    """
    from core.tools.crypto_tools import technical_analysis_tool

    return technical_analysis_tool.invoke({"symbol": symbol, "interval": interval})


@tool
def get_crypto_price(symbol: str) -> dict:
    """
    查詢加密貨幣即時價格。

    ⚠️ symbol 為必填參數（無預設值），不可省略。請務必傳入用戶詢問的具體幣種代號。

    優先查 Binance/OKX 等主流交易所，若找不到會自動 fallback 到 CoinGecko
    （涵蓋 10000+ 幣種，含冷門幣與未上主流所的幣種如 PI、PEPE 等）。

    Args:
        symbol: 幣種代號（必填，例：BTC, ETH, PI, PEPE, DOGE）
    """
    from core.tools.crypto_tools import get_crypto_price_tool

    result = get_crypto_price_tool.invoke({"symbol": symbol})
    return {"price_info": result}


# ============================================
# 專業加密貨幣市場數據 API
# ============================================


@tool
def get_fear_and_greed_index() -> str:
    """獲取加密貨幣市場全域的恐慌與貪婪指數"""
    from core.tools.crypto_tools import get_fear_and_greed_index

    return get_fear_and_greed_index.invoke({})


@tool
def get_trending_tokens() -> str:
    """獲取目前全網最熱門搜尋的加密貨幣"""
    from core.tools.crypto_tools import get_trending_tokens

    return get_trending_tokens.invoke({})


@tool
def get_futures_data(symbol: str) -> str:
    """獲取加密貨幣永續合約的資金費率與多空情緒。
    ⚠️ symbol 為必填參數（無預設值），請務必傳入用戶詢問的具體幣種代號。
    """
    from core.tools.crypto_tools import get_futures_data

    return get_futures_data.invoke({"symbol": symbol})


@tool
def get_current_time_taipei() -> str:
    """獲取目前台灣/UTC+8的精準時間與日期"""
    from core.tools.crypto_tools import get_current_time_taipei

    return get_current_time_taipei.invoke({})


@tool
def get_defillama_tvl(protocol_name: str) -> str:
    """從 DefiLlama 獲取特定協議或公鏈的 TVL (總鎖倉價值)"""
    from core.tools.crypto_tools import get_defillama_tvl

    return get_defillama_tvl.invoke({"protocol_name": protocol_name})


@tool
def get_crypto_categories_and_gainers() -> str:
    """獲取 CoinGecko 上表現最佳的加密貨幣板塊"""
    from core.tools.crypto_tools import get_crypto_categories_and_gainers

    return get_crypto_categories_and_gainers.invoke({})


@tool
def get_token_unlocks(symbol: str) -> str:
    """獲取代幣未來的解鎖日程與數量 (Token Unlocks)"""
    from core.tools.crypto_tools import get_token_unlocks

    return get_token_unlocks.invoke({"symbol": symbol})


@tool
def get_token_supply(symbol: str) -> str:
    """獲取代幣的發行量、目前市場流通量與最大供應量上限"""
    from core.tools.crypto_tools import get_token_supply

    return get_token_supply.invoke({"symbol": symbol})


# ============================================
# Skill 載入工具（OpenClaw 式 progressive disclosure）
# ============================================


@tool
def load_skill(skill_name: str) -> str:
    """載入一個分析方法 skill 的完整內容（方法步驟＋輸出格式＋慣例）。

    system prompt 的「可用分析方法（Skills）」目錄只有名稱和描述。
    當用戶的問題屬於某個 skill 的範疇（例如要求比較多個標的、基本面
    評估、技術分析）時，先用此工具取得完整方法再照著執行。

    Args:
        skill_name: skill 名稱（目錄中的粗體名，例：comparison-analysis）
    """
    from core.agents.skill_loader import get_skill_loader

    loader = get_skill_loader()
    skill = loader.get_skill(skill_name.strip())
    if skill is None:
        available = ", ".join(s.name for s in loader.list_all())
        return (
            f"Skill '{skill_name}' not found. Available skills: {available or 'none'}"
        )
    # per-user 攔截：使用者關閉的 skill 不可由模型自主載入
    from core.tools.key_resolver import get_current_user_id

    uid = get_current_user_id()
    if uid and uid != "default":
        try:
            from core.database.skill_preferences import SkillPreferenceStore

            disabled = SkillPreferenceStore(user_id=uid).get_disabled_skills()
            if skill.name in disabled:
                return f"Skill '{skill.name}' 已被使用者關閉，不可載入。"
        except Exception:  # noqa: BLE001 — DB 失敗不阻塞工具
            pass
    from core.agents.skill_metrics import record_skill_load

    record_skill_load(skill.name)
    return f'<skill name="{skill.name}">\n{skill.body.strip()}\n</skill>'


@tool
def load_knowledge(topic: str) -> str:
    """查詢個人知識庫中與 topic 相關的過去分析(LLM Wiki)。

    當用戶問的問題你之前分析過類似主題時,先用此工具檢索過去的分析結果,
    可避免重複從零合成、並建立在之前的結論上。知識庫會隨使用累積
    (有價值的分析會自動存入;使用者也可手動收藏)。

    注意:知識庫內容可能過時(例如舊價格),使用前應交叉驗證關鍵數字。

    Args:
        topic: 要查詢的主題/問題關鍵字(如「BTC 技術分析」「台積電基本面」)
    """
    from core.database.knowledge import KnowledgeStore
    from core.tools.key_resolver import get_current_user_id

    user_id = get_current_user_id()
    if not user_id:
        return "知識庫為空或未登入。"
    pages = KnowledgeStore().retrieve_relevant(user_id, topic, limit=2)
    if not pages:
        return f"知識庫中無與「{topic}」相關的過去分析。"
    return KnowledgeStore().format_for_prompt(pages)


# ============================================
# 通用網絡搜尋工具
# ============================================


@tool
def web_search(query: str, purpose: str = "general") -> str:
    """通用網絡搜索 (DuckDuckGo)，用於獲取即時資訊。可自由組合搜尋關鍵字。

    ⚠️ 觸發條件：
    1. 需要最新/即時資訊，且其他專用工具無法滿足時
    2. 用戶問「宏觀市場」「整體經濟」「全球事件」等超出單一幣種範圍的問題
    3. **安全事件、漏洞、被駭、exploit、歷史回顧（超過7天）、特定主題新聞**
       → 優先使用此工具，組合精確關鍵字搜尋
       （例：「FLOW blockchain security vulnerability hack」）
       google_news/aggregate_news 只搜 symbol 近 7 天，無法依主題過濾。

    Args:
        query: 搜索關鍵字，如 "Bitcoin ETF approval news 2024"
        purpose: 搜索目的說明
    """
    # 工具呼叫防護：去重（完全相同/相似 query）+ 每 turn 輪數上限。
    # 防 reasoning 模型反覆搜尋同一主題跑滿 timeout（線上特定個股案例）。
    from core.agents.tool_guard import check_tool_guard

    should_block, message = check_tool_guard("web_search", query=query)
    if should_block:
        return message

    from core.tools.web_search import web_search_tool

    return web_search_tool.invoke({"query": query, "purpose": purpose})


@tool
def fetch_url(url: str, purpose: str = "general") -> str:
    """讀取指定網址的網頁全文。先用 web_search 找到候選文章後，若 snippet 不夠，
    用此工具讀完整內容。

    ⚠️ 觸發條件：
    1. web_search 找到的文章，但 snippet（1-2 句摘要）不足以回答問題時
    2. 用戶直接貼一個 URL 要你讀內容
    3. 需要引述原文細節、數據、步驟時

    安全限制：
    - 只接受 http/https
    - 內網 / localhost / 雲端 metadata IP 會被擋（SSRF 防護）
    - query 帶 api_key / token 等的 credential URL 會被擋

    Args:
        url: 完整的 http(s) 網址，如 "https://example.com/article"
        purpose: 為何要讀這頁（供 logging）
    """
    # 工具呼叫防護：去重（同一 URL）+ 每 turn 輪數上限。
    # 防 reasoning 模型反覆 fetch 同一頁或抓太多頁跑滿 timeout（線上個股頁案例：
    # 連續 fetch 兩頁各 16000 字，context 爆增讓 LLM 處理到超時）。
    from core.agents.tool_guard import check_tool_guard

    should_block, message = check_tool_guard("fetch_url", url=url)
    if should_block:
        return message

    from core.tools.url_fetch import fetch_url_tool

    return fetch_url_tool.invoke({"url": url, "purpose": purpose})


# ============================================
# 所有工具列表（供 Manager prompt 用）
# ============================================

# ============================================
# 台股工具 (TW Stock Tools)
# ============================================


@tool
def tw_price(ticker: str) -> dict:
    """獲取台股即時價格（yfinance）"""
    from core.tools.tw_stock_tools import tw_stock_price

    return tw_stock_price.invoke({"ticker": ticker})


@tool
def tw_technical(ticker: str) -> dict:
    """計算台股技術指標 RSI/MACD/KD/MA"""
    from core.tools.tw_stock_tools import tw_technical_analysis

    return tw_technical_analysis.invoke({"ticker": ticker})


@tool
def tw_fundamentals_tool(ticker: str) -> dict:
    """獲取台股基本面資料"""
    from core.tools.tw_stock_tools import tw_fundamentals

    return tw_fundamentals.invoke({"ticker": ticker})


@tool
def tw_institutional_tool(ticker: str) -> dict:
    """獲取台股三大法人籌碼資料"""
    from core.tools.tw_stock_tools import tw_institutional

    return tw_institutional.invoke({"ticker": ticker})


@tool
def tw_news_tool(ticker: str, company_name: str = "") -> list:
    """獲取台股新聞"""
    from core.tools.tw_stock_tools import tw_news

    return tw_news.invoke({"ticker": ticker, "company_name": company_name})


@tool
def tw_major_news_tool(limit: int = 10) -> list:
    """獲取台股上市公司今日重大訊息（TW Stock major announcements）"""
    from core.tools.tw_stock_tools import tw_major_news

    return tw_major_news.invoke({"limit": limit})


@tool
def tw_pe_ratio_tool(code: str) -> dict:
    """獲取台股個股本益比(P/E)、殖利率(Dividend Yield)、股價淨值比(P/B)。
    code: 股票代號"""
    from core.tools.tw_stock_tools import tw_pe_ratio

    return tw_pe_ratio.invoke({"code": code})


@tool
def tw_monthly_revenue_tool(code: str = "") -> list:
    """獲取台股月營收資料，含月增率與年增率。code 為股票代號，空白為全市場前30筆"""
    from core.tools.tw_stock_tools import tw_monthly_revenue

    return tw_monthly_revenue.invoke({"code": code})


@tool
def tw_dividend_tool(code: str = "") -> list:
    """獲取台股股利分派（現金股利、配股、除息日）。code 為股票代號，空白為近期全市場"""
    from core.tools.tw_stock_tools import tw_dividend_info

    return tw_dividend_info.invoke({"code": code})


@tool
def tw_foreign_top20_tool() -> list:
    """獲取外資及陸資持股台股前20名，含持股比率與可投資上限"""
    from core.tools.tw_stock_tools import tw_foreign_holding_top20

    return tw_foreign_holding_top20.invoke({})


@tool
def tw_stock_snapshot_tool(ticker: str) -> dict:
    """一次取得台股完整快照：即時價格 + 技術指標(RSI/MACD/KD/均線) + 基本面(PE/EPS/ROE) +
    三大法人籌碼 + 最新新聞(5則)。
    適合需要全面分析時使用，比分別呼叫各工具更高效。
    接受股票代號（如 2330）或公司名稱（如台積電）。
    """
    from core.tools.tw_stock_tools import tw_stock_snapshot

    return tw_stock_snapshot.invoke({"ticker": ticker})


# ============================================
# DexScreener DEX 數據工具
# ============================================


@tool
def us_stock_snapshot_tool(symbol: str) -> dict:
    """一次取得美股完整快照：即時價格 + 技術指標(RSI/MACD/MA/布林帶) + 基本面(PE/EPS/ROE/市值) +
    財報數據 + 機構持倉 + 最新新聞(5則)。
    適合需要全面分析時使用，比分別呼叫各工具更高效。
    接受美股代號（如 AAPL、TSLA、NVDA）或公司名稱。
    """
    from core.tools.us_stock_tools import us_stock_snapshot

    return us_stock_snapshot.invoke({"symbol": symbol})


@tool
def get_dex_pair_info_tool(token_address: str) -> dict:
    """獲取 DEX 代幣對的詳細資訊（價格、流動性、交易量）"""
    from core.tools.crypto_tools import get_dex_pair_info

    return get_dex_pair_info.invoke({"token_address": token_address})


@tool
def get_trending_dex_pairs_tool(query: str) -> list:
    """搜索熱門 DEX 交易對"""
    from core.tools.crypto_tools import get_trending_dex_pairs

    return get_trending_dex_pairs.invoke({"query": query})


# ============================================
# Etherscan 鏈上數據工具
# ============================================


@tool
def get_eth_balance_tool(address: str, chain_id: int = 1) -> dict:
    """查詢 EVM 地址的原生代幣餘額（多鏈：chain_id 預設 1=Ethereum, 56=BSC, 137=Polygon...）"""
    from core.tools.crypto_tools import get_eth_balance

    return get_eth_balance.invoke({"address": address, "chain_id": chain_id})


@tool
def get_erc20_token_balance_tool(
    address: str, contract_address: str, chain_id: int = 1
) -> dict:
    """查詢 EVM 地址的 ERC20 代幣餘額（多鏈：chain_id 預設 1=Ethereum, 56=BSC, 137=Polygon...）"""
    from core.tools.crypto_tools import get_erc20_token_balance

    return get_erc20_token_balance.invoke(
        {"address": address, "contract_address": contract_address, "chain_id": chain_id}
    )


@tool
def get_address_transactions_tool(
    address: str, limit: int = 10, chain_id: int = 1
) -> dict:
    """查詢 EVM 地址的最近交易記錄（多鏈：chain_id 預設 1=Ethereum, 56=BSC, 137=Polygon...）"""
    from core.tools.crypto_tools import get_address_transactions

    return get_address_transactions.invoke(
        {"address": address, "limit": limit, "chain_id": chain_id}
    )


@tool
def get_contract_info_tool(contract_address: str, chain_id: int = 1) -> dict:
    """查詢 EVM 智能合約的基本資訊（多鏈：chain_id 預設 1=Ethereum, 56=BSC, 137=Polygon...）"""
    from core.tools.crypto_tools import get_contract_info

    return get_contract_info.invoke(
        {"contract_address": contract_address, "chain_id": chain_id}
    )


@tool
def get_eth_price_etherscan_tool() -> dict:
    """從 Etherscan 獲取即時價格"""
    from core.tools.crypto_tools import get_eth_price_from_etherscan

    return get_eth_price_from_etherscan.invoke({})


@tool
def check_token_security_tool(contract_address: str, chain_id: int = 1) -> dict:
    """檢測代幣合約的安全風險（蜜罐、rug pull、隱藏權限等，免費免金鑰）"""
    from core.tools.crypto_tools import check_token_security

    return check_token_security.invoke(
        {"contract_address": contract_address, "chain_id": chain_id}
    )


@tool
def check_address_safety_tool(address: str, chain_id: int = 1) -> dict:
    """檢測地址是否涉及惡意行為（釣魚、洗錢、制裁等，需 GoPlus 金鑰）"""
    from core.tools.crypto_tools import check_address_safety

    return check_address_safety.invoke({"address": address, "chain_id": chain_id})


@tool
def get_cmc_quote_tool(symbol: str, convert: str = "USD") -> dict:
    """使用 CoinMarketCap 查詢加密貨幣行情（需自帶 CMC 金鑰）"""
    from core.tools.coinmarketcap_tools import get_cmc_quote

    return get_cmc_quote.invoke({"symbol": symbol, "convert": convert})


# ============================================
# 大宗商品工具 (Commodity Tools)
# ============================================


@tool
def get_commodity_price_tool(commodity: str) -> dict:
    """查詢大宗商品即時價格（黃金、白銀、石油、天然氣、銅等）"""
    from core.tools.commodity_tools import get_commodity_price

    return get_commodity_price.invoke({"commodity": commodity})


@tool
def get_commodity_futures_tool(futures_type: str) -> dict:
    """查詢商品期貨價格（原油、黃金、白銀、天然氣期貨）"""
    from core.tools.commodity_tools import get_commodity_futures_price

    return get_commodity_futures_price.invoke({"futures_type": futures_type})


@tool
def get_all_commodities_prices_tool() -> dict:
    """獲取所有主要大宗商品價格一覽表"""
    from core.tools.commodity_tools import get_all_commodities_prices

    return get_all_commodities_prices.invoke({})


@tool
def get_gold_silver_ratio_tool() -> dict:
    """獲取金銀比（重要的市場情緒指標）"""
    from core.tools.commodity_tools import get_gold_silver_ratio

    return get_gold_silver_ratio.invoke({})


@tool
def get_oil_analysis_tool() -> dict:
    """獲取原油價格綜合分析（WTI vs 布蘭特）"""
    from core.tools.commodity_tools import get_oil_price_analysis

    return get_oil_price_analysis.invoke({})


# ============================================
# 外匯工具 (Forex Tools)
# ============================================


@tool
def get_forex_rate_tool(pair: str) -> dict:
    """查詢外匯即時匯率（USD/TWD、USD/JPY、EUR/USD等）"""
    from core.tools.forex_tools import get_forex_rate

    return get_forex_rate.invoke({"pair": pair})


@tool
def get_all_forex_rates_tool() -> dict:
    """獲取所有主要貨幣對的即時匯率一覽表"""
    from core.tools.forex_tools import get_all_forex_rates

    return get_all_forex_rates.invoke({})


@tool
def get_usd_twd_rate_tool() -> dict:
    """查詢美元/台幣即時匯率"""
    from core.tools.forex_tools import get_usd_twd_rate

    return get_usd_twd_rate.invoke({})


@tool
def get_central_bank_rates_tool() -> dict:
    """獲取主要央行利率（Fed、ECB、BOJ、台灣央行）"""
    from core.tools.forex_tools import get_central_bank_rates

    return get_central_bank_rates.invoke({})


# ============================================
# 經濟數據工具 (Economic Tools)
# ============================================


@tool
def get_market_indices_tool() -> dict:
    """獲取美股主要市場指數（S&P 500、道瓊、那斯達克、VIX）"""
    from core.tools.economic_tools import get_market_indices

    return get_market_indices.invoke({})


@tool
def get_vix_index_tool() -> dict:
    """獲取 VIX 恐慌指數詳細資訊和市場情緒判讀"""
    from core.tools.economic_tools import get_vix_index

    return get_vix_index.invoke({})


@tool
def get_sp500_performance_tool() -> dict:
    """獲取 S&P 500 指數詳細表現和各期間報酬"""
    from core.tools.economic_tools import get_sp500_performance

    return get_sp500_performance.invoke({})


@tool
def get_sector_performance_tool() -> dict:
    """獲取美股 11 大板塊表現（科技、金融、能源等）"""
    from core.tools.economic_tools import get_us_sector_performance

    return get_us_sector_performance.invoke({})


@tool
def get_economic_calendar_tool() -> dict:
    """獲取近期重要經濟事件行事曆"""
    from core.tools.economic_tools import get_economic_calendar

    return get_economic_calendar.invoke({})


# ============================================
# 全球股市工具 (Global Stock Tools — HK/JP/KR/IN)
# ============================================


@tool
def global_stock_price_tool(symbol: str, market: str = "hk") -> dict:
    """查詢全球股市即時價格（港股/日股/韓股/印股）。
    market: hk=港股, jp=日股, kr=韓股, in=印股
    symbol 可帶後綴（0700.HK）或純代號（0700）。
    """
    from core.tools.global_stock_tools import global_stock_price

    return global_stock_price.invoke({"symbol": symbol, "market": market})


@tool
def global_stock_technical_tool(symbol: str, market: str = "hk") -> dict:
    """計算全球股市技術指標（港股/日股/韓股/印股）。
    返回 RSI(14), MACD histogram, MA20, MA50, 52週高低點。
    """
    from core.tools.global_stock_tools import global_stock_technical

    return global_stock_technical.invoke({"symbol": symbol, "market": market})


@tool
def global_stock_fundamentals_tool(symbol: str, market: str = "hk") -> dict:
    """取得全球股市基本面（港股/日股/韓股/印股）。
    返回 PE/PB/Beta/股息率/EPS/營收成長/獲利成長/分析師目標/建議/產業。
    """
    from core.tools.global_stock_tools import global_stock_fundamentals

    return global_stock_fundamentals.invoke({"symbol": symbol, "market": market})


@tool
def global_stock_news_tool(symbol: str, market: str = "hk", limit: int = 5) -> list:
    """取得全球股市相關新聞（港股/日股/韓股/印股）。"""
    from core.tools.global_stock_tools import global_stock_news

    return global_stock_news.invoke(
        {"symbol": symbol, "market": market, "limit": limit}
    )


@tool
def global_stock_snapshot_tool(symbol: str, market: str = "hk") -> dict:
    """一次取得全球股市完整快照（港股/日股/韓股/印股）。
    包含：即時價格 + 技術指標 + 基本面 + 近期新聞（3則）。
    適合需要全面分析時使用，比分別呼叫各工具更高效。
    market: hk=港股, jp=日股, kr=韓股, in=印股
    """
    from core.tools.global_stock_tools import global_stock_snapshot

    return global_stock_snapshot.invoke({"symbol": symbol, "market": market})


# ============================================
# 通用 Symbol 解析工具
# ============================================


@tool
def resolve_symbol(
    query: str, market: str = "auto", use_web_search: bool = False
) -> str:
    """
    將資產名稱解析為標準交易代號。委派到 SymbolNormalizer。

    使用時機：
    - 你不確定資產的標準代號時
    - 用戶用暱稱或全名：「Pi Network」「Bitcoin」「Apple」「Toyota」

    ⚠️ Query 語言建議：
    - tw 市場：**支援中文**（如「台積電」），會走 TWSE OpenAPI
    - 其他市場（crypto/us/hk/jp/kr/in）：**請用英文 query**
      （如「Bitcoin」「Tencent」「Toyota」「Samsung」）
    - 如果用戶用中文（如「騰訊」「豐田」），你應先用訓練資料翻成英文（"Tencent"/"Toyota"）再叫此工具

    各市場 API 路徑：
    - crypto → CoinGecko search
    - tw → TWSE OpenAPI（支援中文模糊匹配）
    - us / hk / jp / kr / in / cn → yfinance Search（接受英文公司名）
    - auto → 預設 crypto（CoinGecko 涵蓋最廣）

    Args:
        query: 資產名稱（tw 可中文；其他市場建議英文）
        market: "crypto" / "tw" / "us" / "hk" / "jp" / "kr" / "in" / "cn" / "auto"
        use_web_search: 當所有 API 都查不到時，是否用 web_search 兜底（較慢但能救援冷門股）。
            預設 False；當第一次叫此工具失敗後，第二次叫請設 True。

    Returns:
        JSON 字串：{symbol, market, source, verified}；找不到時 symbol=null
        source 可能是: twse_api / coingecko / yfinance / yfinance_search / web_search / llm_confident
    """
    import json

    from core.agents.models import ExtractedEntity

    cache_key = f"resolve:{market}:{query.lower().strip()}"
    cached = _resolve_cache_get(cache_key)
    if cached is not None:
        return cached

    # market="auto" 時統一用 crypto（CoinGecko search 涵蓋廣）
    target_market = market if market != "auto" else "crypto"

    # candidate_symbol 暫時帶 query 本身（中文也行，SymbolNormalizer 內部會 fallback 用 asset_name）
    entity = ExtractedEntity(
        market=target_market,
        asset_name=query.strip(),
        candidate_symbol=query.strip().upper() if query.strip().isascii() else None,
        confidence=0.8,
        reasoning="invoked via resolve_symbol tool",
    )

    normalizer = _get_shared_normalizer()
    result = normalizer.normalize(entity, enable_web_fallback=use_web_search)

    if result is None:
        payload = json.dumps(
            {
                "symbol": None,
                "market": target_market,
                "error": f"找不到 '{query}'，請確認名稱",
            },
            ensure_ascii=False,
        )
        # 短 TTL 失敗也要快取（避免重複打爆 API）
        _resolve_cache_set(cache_key, payload, ttl=300)
        return payload

    payload = json.dumps(
        {
            "symbol": result.symbol,
            "market": result.market,
            "source": result.source,
            "verified": result.verified,
            "original_name": result.original_name,
        },
        ensure_ascii=False,
    )
    _resolve_cache_set(cache_key, payload, ttl=3600)
    return payload


# 共用 SymbolNormalizer 實例（避免每次 resolve_symbol 都重建）
_shared_normalizer = None


def _get_shared_normalizer():
    global _shared_normalizer
    if _shared_normalizer is None:
        from core.tools.symbol_normalizer import SymbolNormalizer

        _shared_normalizer = SymbolNormalizer()
    return _shared_normalizer


# In-memory cache for resolve_symbol
_resolve_mem_cache: dict = {}


def _resolve_cache_get(key: str):
    import time as _time

    entry = _resolve_mem_cache.get(key)
    if entry is None:
        return None
    value, expires_at = entry
    if _time.time() > expires_at:
        _resolve_mem_cache.pop(key, None)
        return None
    return value


def _resolve_cache_set(key: str, value: str, ttl: int = 3600) -> None:
    import time as _time

    _resolve_mem_cache[key] = (value, _time.time() + ttl)


# ============================================
# 多市場並行 Symbol 解析（ambiguous ticker）
# ============================================


@tool
def resolve_symbol_all_markets(query: str) -> str:
    """Probe a single ticker across ALL supported markets + detect unsupported markets via web_search.

    Use this when a ticker might be ambiguous (e.g., "AKE" could be a crypto memecoin,
    a French stock, or a delisted ASX stock). Returns a candidate list with each
    market's symbol, source, verification status, and platform support flag.

    **When to use:**
    - User asks about a short uppercase ticker (2-6 letters) that could exist in multiple markets
    - First resolve_symbol call returned null but the ticker "feels" legit
    - Before fabricating data — ALWAYS probe first if unsure

    **When NOT to use:**
    - User explicitly named the market ("BTC 加密" / "AAPL 美股")
    - Ticker is clearly a known crypto (BTC/ETH/USDT) or major US stock (AAPL/TSLA)

    Returns JSON: {query, candidates: [{market, symbol, verified, supported, ...}],
                   ambiguous: bool, recommendation: str}

    If ``ambiguous: true``, list candidates to the user and ask which they meant.
    Candidates with ``supported: false`` should be reported honestly (e.g.,
    "Arkema AKE.PA is a French stock — this platform doesn't support French market").
    """
    from core.tools.multi_market_resolver import (
        resolve_symbol_all_markets_sync,
    )

    return resolve_symbol_all_markets_sync(query)


# ============================================
# 所有工具列表（供 Manager prompt 用）
# ============================================

ALL_TOOLS = [
    google_news,
    aggregate_news,
    technical_analysis,
    get_crypto_price,
    get_fear_and_greed_index,
    get_trending_tokens,
    get_futures_data,
    get_current_time_taipei,
    get_defillama_tvl,
    get_crypto_categories_and_gainers,
    get_token_unlocks,
    web_search,
    tw_price,
    tw_technical,
    tw_fundamentals_tool,
    tw_institutional_tool,
    tw_news_tool,
    tw_stock_snapshot_tool,
    # TWSE OpenAPI tools
    tw_major_news_tool,
    tw_pe_ratio_tool,
    tw_monthly_revenue_tool,
    tw_dividend_tool,
    tw_foreign_top20_tool,
    # US Stock snapshot
    us_stock_snapshot_tool,
    # DexScreener tools
    get_dex_pair_info_tool,
    get_trending_dex_pairs_tool,
    # Etherscan tools
    get_eth_balance_tool,
    get_erc20_token_balance_tool,
    get_address_transactions_tool,
    get_contract_info_tool,
    get_eth_price_etherscan_tool,
    # GoPlus Security (token=free, address=BYOK)
    check_token_security_tool,
    check_address_safety_tool,
    # CoinMarketCap (BYOK)
    get_cmc_quote_tool,
    # Commodity tools
    get_commodity_price_tool,
    get_commodity_futures_tool,
    get_all_commodities_prices_tool,
    get_gold_silver_ratio_tool,
    get_oil_analysis_tool,
    # Forex tools
    get_forex_rate_tool,
    get_all_forex_rates_tool,
    get_usd_twd_rate_tool,
    get_central_bank_rates_tool,
    # Economic tools
    get_market_indices_tool,
    get_vix_index_tool,
    get_sp500_performance_tool,
    get_sector_performance_tool,
    get_economic_calendar_tool,
    # Global stock tools (HK/JP/KR/IN)
    global_stock_price_tool,
    global_stock_technical_tool,
    global_stock_fundamentals_tool,
    global_stock_news_tool,
    global_stock_snapshot_tool,
    # 通用 symbol 解析（中文/暱稱 → 標準代號）
    resolve_symbol,
    # 多市場並行解析（處理 ambiguous ticker，如 AKE 可能是 crypto/法股/澳股）
    resolve_symbol_all_markets,
]


@tool
def tw_market_index_tool() -> dict:
    """獲取台股大盤指數：加權指數（TAIEX）與櫃買指數（OTC）。

    用戶問整體市場（「台股今天如何」「大盤漲跌」）時使用，個股請用 tw_price。
    """
    from core.tools.tw_stock_tools import tw_market_index

    return tw_market_index.invoke({})


# ============================================
# 普惠金融示範工具（Trustworthy AI Hackathon — Consent Gate demo）
# ============================================
# 這是 demo 用的 mock tool，不碰真實 KYC/銀行系統。用途：讓 Consent Gate 在
# 「移工申請開戶」場景自然觸發 — agent 呼叫此 tool 前需使用者 explicit consent，
# 同意記進 audit log。體現 Policy Gate + Audit Log + Principal 三大信任支柱。
# 真實場景會串接銀行 KYC API；此處回結構化 mock 結果供 demo。


@tool
def submit_kyc_application(applicant_wallet: str, id_type: str = "ton_wallet") -> dict:
    """提交普惠金融開戶（KYC）申請（示範用 mock）。

    適用場景：移工/新住民以 TON 錢包為數位身分申請數位銀行開戶。
    ⚠️ 此為高風險動作（變更金融狀態），Agent 呼叫前需使用者同意。

    Args:
        applicant_wallet: 申請人的 TON 錢包地址（數位身分）。
        id_type: 身分類型（預設 ton_wallet）。
    """
    import time

    application_id = f"KYC-{int(time.time())}-{applicant_wallet[-6:]}"
    return {
        "success": True,
        "application_id": application_id,
        "status": "submitted",
        "message": (
            f"KYC 申請已提交（示範 mock）。申請人錢包：{applicant_wallet}，"
            f"身分類型：{id_type}。審核結果將透過 Agent 通知。"
        ),
        "note": "This is a demo mock for Trustworthy AI Hackathon — no real KYC backend.",
    }
