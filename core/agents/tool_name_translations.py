"""Tool display name translations — i18n for SSE progress / HITL tool names.

背景（Bug#1 修復）
-----------------
``_TOOLS_SEED`` (``core/database/tools.py``) 的 ``display_name`` / ``description``
是單語（繁中），經 ``tool_name_registry`` 直接送進 SSE progress 事件的
``task_name``，前端無翻譯層直顯 → 英文 UI 卻顯示「正在查詢：TON 錢包總覽」。

修復策略
--------
**不動 ``_TOOLS_SEED``**（它是 DB seed 來源，結構變更會牽動 catalog/seed 流程），
改在本檔維護一份獨立的 ``{tool_id: {lang: {"name": ..., "desc": ...}}}`` 翻譯表。
``get_tool_display_name(tool_id, language)`` 優先查本表，缺譯 fallback 到
``_TOOLS_SEED`` 的繁中 display_name（向下相容，不破壞現有行為）。

語言涵蓋
--------
官方支援 4 語：``zh-TW``（_TOOLS_SEED 原文）、``en``、``zh-CN``、``ru``。
與 ``shared.yaml`` / ``LanguageAwareLLM._INSTRUCTIONS`` / 前端 ``web/i18n/*.json``
一致的 4 語集合。

翻譯品質
--------
- en：金融/加密貨幣業界標準譯名（如 "Fear & Greed Index" / "Funding Rate" / "TVL"）
- zh-CN：繁簡轉換 + 少數在地化用語（如「法人」→「机构」、「殖利率」→「收益率」）
- ru：機翻草稿（對齊 shared.yaml ru 區 `# TODO: native speaker review` 現狀），
  待俄文母語者校審。

新增工具時
----------
1. 在 ``_TOOLS_SEED`` 加 entry（既有流程）
2. 在本檔 ``_TRANSLATIONS`` 補 4 語（至少補 en；zh-TW 由 _TOOLS_SEED fallback）
3. 跑 ``tests/test_tool_name_registry.py`` 確認覆蓋
"""

from __future__ import annotations

# 官方支援語言（與 shared.yaml / LanguageAwareLLM / 前端 i18n 一致）
SUPPORTED_LANGUAGES = ("zh-TW", "zh-CN", "en", "ru")
DEFAULT_LANGUAGE = "zh-TW"

# tool_id → {lang → {"name": str, "desc": str}}
# zh-TW 不在此重複（由 _TOOLS_SEED 提供，避免雙重維護）；這裡只放 en / zh-CN / ru。
# 缺譯時 get_tool_display_name 會 fallback 到 _TOOLS_SEED 的繁中 display_name。
_TRANSLATIONS: dict[str, dict[str, dict[str, str]]] = {
    # ── Crypto 基本面 ──
    "get_crypto_price": {
        "en": {
            "name": "Real-time Crypto Price",
            "desc": "Query real-time cryptocurrency prices",
        },
        "zh-CN": {"name": "即时加密货币价格", "desc": "查询加密货币即时价格"},
        "ru": {
            "name": "Актуальная цена криптовалюты",
            "desc": "Запрос актуальной цены криптовалюты",
        },
    },
    "get_current_time_taipei": {
        "en": {"name": "Current Time", "desc": "Query current time in Taiwan/UTC+8"},
        "zh-CN": {"name": "目前时间", "desc": "查询台湾/UTC+8 目前时间"},
        "ru": {"name": "Текущее время", "desc": "Узнать текущее время в Тайване/UTC+8"},
    },
    "get_fear_and_greed_index": {
        "en": {
            "name": "Fear & Greed Index",
            "desc": "Query global crypto market fear & greed index",
        },
        "zh-CN": {"name": "恐慌与贪婪指数", "desc": "查询全球加密货币市场恐慌贪婪指数"},
        "ru": {
            "name": "Индекс страха и жадности",
            "desc": "Индекс страха и жадности крипторынка",
        },
    },
    "get_trending_tokens": {
        "en": {
            "name": "Trending Tokens",
            "desc": "Query the most searched cryptocurrencies",
        },
        "zh-CN": {"name": "热门币种排行", "desc": "查询全网最热门搜索的加密货币"},
        "ru": {"name": "Популярные токены", "desc": "Самые запрашиваемые криптовалюты"},
    },
    "get_crypto_market_cap": {
        "en": {
            "name": "Crypto Market Cap",
            "desc": "Query top 100 crypto by market cap",
        },
        "zh-CN": {"name": "加密货币市值排行", "desc": "查询加密货币总市值排行 Top 100"},
        "ru": {
            "name": "Капитализация криптовалют",
            "desc": "Топ-100 криптовалют по капитализации",
        },
    },
    "get_economic_calendar": {
        "en": {
            "name": "Economic Calendar",
            "desc": "Query major economic events and data releases",
        },
        "zh-CN": {"name": "全球经济日历", "desc": "查询重要经济事件与数据发布时间"},
        "ru": {
            "name": "Экономический календарь",
            "desc": "Важные экономические события и данные",
        },
    },
    "technical_analysis": {
        "en": {
            "name": "Crypto Technical Indicators",
            "desc": "RSI, MACD, moving averages and other indicators",
        },
        "zh-CN": {"name": "加密货币技术指标", "desc": "RSI、MACD、均线等技术指标分析"},
        "ru": {
            "name": "Технические индикаторы криптовалюты",
            "desc": "RSI, MACD, скользящие средние",
        },
    },
    # ── 新聞 ──
    "google_news": {
        "en": {"name": "Google News", "desc": "Fetch relevant news from Google News"},
        "zh-CN": {"name": "Google 新闻", "desc": "从 Google News 抓取相关新闻"},
        "ru": {"name": "Google Новости", "desc": "Получить новости из Google News"},
    },
    "aggregate_news": {
        "en": {
            "name": "Multi-source News Aggregator",
            "desc": "Aggregate crypto news from multiple sources (set CryptoPanic/NewsAPI keys to unlock more)",
        },
        "zh-CN": {
            "name": "多来源新闻聚合",
            "desc": "从多个来源聚合加密货币新闻（设定下方 CryptoPanic / NewsAPI 金钥可解锁更多来源）",
        },
        "ru": {
            "name": "Агрегатор новостей",
            "desc": "Новости криптовалют из нескольких источников",
        },
    },
    "cryptopanic_news_source": {
        "en": {
            "name": "CryptoPanic News Source",
            "desc": "Enable aggregate_news to read CryptoPanic pro news (requires CryptoPanic API key)",
        },
        "zh-CN": {
            "name": "CryptoPanic 新闻来源",
            "desc": "让 aggregate_news 工具能读取 CryptoPanic 专业新闻（需自带 CryptoPanic API 金钥）",
        },
        "ru": {
            "name": "Источник CryptoPanic",
            "desc": "Профессиональные новости CryptoPanic (нужен API-ключ)",
        },
    },
    "newsapi_news_source": {
        "en": {
            "name": "NewsAPI News Source",
            "desc": "Enable aggregate_news to read NewsAPI mainstream media (free tier 100/day, requires key)",
        },
        "zh-CN": {
            "name": "NewsAPI 新闻来源",
            "desc": "让 aggregate_news 工具能读取 NewsAPI 主流媒体新闻（免费版 100/天，需自带金钥）",
        },
        "ru": {
            "name": "Источник NewsAPI",
            "desc": "Новости ведущих СМИ через NewsAPI (бесплатно 100/день)",
        },
    },
    # ── 網路 ──
    "web_search": {
        "en": {
            "name": "Web Search",
            "desc": "General web search (free via the platform's self-hosted multi-engine aggregator: Google / Bing / DuckDuckGo; set a Tavily key to upgrade quality)",
        },
        "zh-CN": {
            "name": "网络搜索",
            "desc": "通用网络搜索（免费用平台自架的多引擎聚合搜索：Google／Bing／DuckDuckGo 等；设定 Tavily 金钥可再升级品质）",
        },
        "ru": {
            "name": "Веб-поиск",
            "desc": "Веб-поиск (бесплатно через собственный агрегатор платформы: Google / Bing / DuckDuckGo; Tavily повышает качество)",
        },
    },
    "serper_search_source": {
        "en": {
            "name": "Serper Search Source (Google)",
            "desc": "Let web_search use your own Google search quota — best quality for Chinese (free 2500/month, requires key)",
        },
        "zh-CN": {
            "name": "Serper 搜索来源（Google）",
            "desc": "让 web_search 用你自己的 Google 搜索配额，中文品质最佳（免费 2500 次/月，需自带金钥）",
        },
        "ru": {
            "name": "Источник Serper (Google)",
            "desc": "web_search через вашу квоту Google (бесплатно 2500/мес, нужен ключ)",
        },
    },
    "fmp_search_source": {
        "en": {
            "name": "FMP Search Source (Tickers)",
            "desc": "Let web_search resolve company names to tickers, including non-English names (free 250/day, requires key)",
        },
        "zh-CN": {
            "name": "FMP 搜索来源（股票代号）",
            "desc": "让 web_search 能用公司名查股票代号，支持中文（如「台积电」→ TSM）（免费 250 次/天，需自带金钥）",
        },
        "ru": {
            "name": "Источник FMP (тикеры)",
            "desc": "web_search сопоставляет названия компаний с тикерами (бесплатно 250/день, нужен ключ)",
        },
    },
    "fetch_url": {
        "en": {
            "name": "Webpage Full-text Reader",
            "desc": "Read full content of a URL. Use web_search first, then read. Free via Jina Reader; Tavily key upgrades quality.",
        },
        "zh-CN": {
            "name": "网页全文读取",
            "desc": "读取指定 URL 的完整网页内容。先用 web_search 找到候选文章，再用此工具读全文。免费使用 Jina Reader；设定 Tavily 金钥可升级品质。",
        },
        "ru": {
            "name": "Чтение веб-страницы",
            "desc": "Полный текст страницы. Сначала web_search, затем чтение. Бесплатно через Jina Reader.",
        },
    },
    # ── 總經 / 合約 ──
    "get_central_bank_rates": {
        "en": {
            "name": "Central Bank Rates (FRED)",
            "desc": "Major central bank rates and macro data (requires FRED key)",
        },
        "zh-CN": {
            "name": "央行利率（FRED）",
            "desc": "主要央行利率与总经数据（需自带 FRED 金钥）",
        },
        "ru": {
            "name": "Ставки ЦБ (FRED)",
            "desc": "Ставки центральных банков и макроданные (нужен ключ FRED)",
        },
    },
    "get_futures_data": {
        "en": {
            "name": "Futures Funding Rate",
            "desc": "Query perpetual futures funding rate and long/short sentiment",
        },
        "zh-CN": {"name": "合约资金费率", "desc": "查询永续合约资金费率与多空情绪"},
        "ru": {
            "name": "Ставка финансирования фьючерсов",
            "desc": "Ставка финансирования бессрочных фьючерсов и настроения",
        },
    },
    # ── DeFi / 鏈上 ──
    "get_defillama_tvl": {
        "en": {"name": "DeFi TVL", "desc": "Query protocol/chain TVL from DefiLlama"},
        "zh-CN": {"name": "DeFi TVL 锁仓量", "desc": "从 DefiLlama 查询协议/公链 TVL"},
        "ru": {"name": "DeFi TVL", "desc": "TVL протоколов и блокчейнов (DefiLlama)"},
    },
    "get_crypto_categories_and_gainers": {
        "en": {
            "name": "Crypto Categories & Gainers",
            "desc": "Strongest categories and hotspots on CoinGecko",
        },
        "zh-CN": {"name": "加密板块与涨幅排行", "desc": "CoinGecko 最强板块与热点"},
        "ru": {
            "name": "Категории и лидеры криптовалют",
            "desc": "Сильнейшие категории и горячие точки (CoinGecko)",
        },
    },
    "get_token_supply": {
        "en": {
            "name": "Token Circulating Supply",
            "desc": "Query total supply, max supply and circulating supply",
        },
        "zh-CN": {
            "name": "代币流通供应量",
            "desc": "查询代币总发行量、最大供应量与流通量",
        },
        "ru": {
            "name": "Обращающееся предложение токена",
            "desc": "Общее, максимальное и обращающееся предложение",
        },
    },
    "get_dex_volume": {
        "en": {
            "name": "DEX Volume Ranking",
            "desc": "Query DEX trading volume and popular pairs",
        },
        "zh-CN": {
            "name": "DEX 交易量排行",
            "desc": "查询去中心化交易所交易量与热门币对",
        },
        "ru": {"name": "Объём DEX", "desc": "Объёмы торгов на DEX и популярные пары"},
    },
    "get_token_unlocks": {
        "en": {
            "name": "Token Unlock Schedule",
            "desc": "Query future token unlock dates and amounts",
        },
        "zh-CN": {"name": "代币解锁日程", "desc": "查询代币未来解锁时间与数量"},
        "ru": {
            "name": "График разблокировки токенов",
            "desc": "Даты и объёмы будущих разблокировок токенов",
        },
    },
    "get_whale_alerts": {
        "en": {
            "name": "Whale Alerts",
            "desc": "Track large on-chain transfers and whale activity",
        },
        "zh-CN": {"name": "鲸鱼追踪警报", "desc": "追踪大额链上转帐与鲸鱼动向"},
        "ru": {
            "name": "Оповещения о китах",
            "desc": "Крупные трансферы в блокчейне и активность китов",
        },
    },
    "get_cmc_quote": {
        "en": {
            "name": "CoinMarketCap Quote",
            "desc": "Real-time crypto quotes, market cap and ranking (requires CMC key)",
        },
        "zh-CN": {
            "name": "CoinMarketCap 行情",
            "desc": "加密货币即时行情、市值与排名（需自带 CMC 金钥）",
        },
        "ru": {
            "name": "Котировки CoinMarketCap",
            "desc": "Котировки, капитализация и рейтинг криптовалют (нужен ключ CMC)",
        },
    },
    # ── Ethereum 鏈上 ──
    "get_eth_balance": {
        "en": {
            "name": "ETH Balance",
            "desc": "Query ETH balance of an address (requires Etherscan key)",
        },
        "zh-CN": {
            "name": "ETH 余额查询",
            "desc": "查询以太坊地址 ETH 余额（需自带 Etherscan 金钥）",
        },
        "ru": {
            "name": "Баланс ETH",
            "desc": "Баланс ETH адреса (нужен ключ Etherscan)",
        },
    },
    "get_erc20_token_balance": {
        "en": {
            "name": "ERC20 Token Balance",
            "desc": "Query ERC20 token balance of an address (requires Etherscan key)",
        },
        "zh-CN": {
            "name": "ERC20 代币余额",
            "desc": "查询地址的 ERC20 代币余额（需自带 Etherscan 金钥）",
        },
        "ru": {
            "name": "Баланс ERC20 токенов",
            "desc": "Баланс ERC20 токенов адреса (нужен ключ Etherscan)",
        },
    },
    "get_address_transactions": {
        "en": {
            "name": "Address Transactions",
            "desc": "Query recent transactions of an ETH address (requires Etherscan key)",
        },
        "zh-CN": {
            "name": "地址交易记录",
            "desc": "查询以太坊地址最近交易（需自带 Etherscan 金钥）",
        },
        "ru": {
            "name": "Транзакции адреса",
            "desc": "Недавние транзакции ETH-адреса (нужен ключ Etherscan)",
        },
    },
    "check_token_security": {
        "en": {
            "name": "Token Security Check",
            "desc": "Detect token contract risks (honeypot, rug pull, hidden privileges — free, no key)",
        },
        "zh-CN": {
            "name": "代币安全检测",
            "desc": "检测代币合约风险（蜜罐、rug pull、隐藏权限等，免费免金钥）",
        },
        "ru": {
            "name": "Проверка безопасности токена",
            "desc": "Риски контракта: honeypot, rug pull, скрытые привилегии (бесплатно)",
        },
    },
    "check_address_safety": {
        "en": {
            "name": "Address Safety Check",
            "desc": "Check if an address is linked to phishing, money laundering, sanctions (platform GoPlus key)",
        },
        "zh-CN": {
            "name": "地址安全检测",
            "desc": "检测地址是否涉及钓鱼、洗钱、制裁等恶意行为（平台官方 GoPlus key）",
        },
        "ru": {
            "name": "Проверка безопасности адреса",
            "desc": "Связь адреса с фишингом, отмыванием, санкциями (GoPlus платформы)",
        },
    },
    "assess_jetton_safety": {
        "en": {
            "name": "TON Jetton Safety Check",
            "desc": "Check TON jetton safety signals (whitelist, holders, admin — free, no key)",
        },
        "zh-CN": {
            "name": "TON Jetton 安全检测",
            "desc": "查询 TON jetton 安全信号（白名单/持有人/admin，免费免金钥）",
        },
        "ru": {
            "name": "Проверка TON Jetton",
            "desc": "Сигналы безопасности TON jetton (whitelist/holders/admin, бесплатно)",
        },
    },
    # ── 台股 ──
    "tw_stock_price": {
        "en": {"name": "TW Stock Price", "desc": "Query real-time Taiwan stock prices"},
        "zh-CN": {"name": "台股即时股价", "desc": "查询台湾股票即时价格"},
        "ru": {
            "name": "Цена акций Тайваня",
            "desc": "Актуальные цены тайваньских акций",
        },
    },
    "tw_technical_analysis": {
        "en": {
            "name": "TW Stock Technical",
            "desc": "TW stock RSI / MACD / KD / moving averages",
        },
        "zh-CN": {"name": "台股技术指标", "desc": "台股 RSI / MACD / KD / 均线"},
        "ru": {
            "name": "Теханализ акций Тайваня",
            "desc": "RSI / MACD / KD / скользящие средние",
        },
    },
    "tw_news": {
        "en": {"name": "TW Stock News", "desc": "Latest Taiwan stock news"},
        "zh-CN": {"name": "台股新闻", "desc": "查询台股相关最新新闻"},
        "ru": {
            "name": "Новости акций Тайваня",
            "desc": "Последние новости тайваньских акций",
        },
    },
    "tw_major_news": {
        "en": {
            "name": "TW Major Announcements",
            "desc": "TWSE official major announcements",
        },
        "zh-CN": {"name": "台股重大讯息", "desc": "TWSE 官方重大讯息公告"},
        "ru": {
            "name": "Важные сообщения TWSE",
            "desc": "Официальные объявления Тайваньской биржи",
        },
    },
    "tw_fundamentals": {
        "en": {
            "name": "TW Fundamentals",
            "desc": "P/E, EPS, ROE and other fundamentals",
        },
        "zh-CN": {"name": "台股基本面", "desc": "P/E、EPS、ROE 等基本面资料"},
        "ru": {
            "name": "Фундаментал акций Тайваня",
            "desc": "P/E, EPS, ROE и другие показатели",
        },
    },
    "tw_institutional": {
        "en": {
            "name": "TW Institutional Flows",
            "desc": "Buy/sell by foreign, investment trust, and proprietary traders",
        },
        "zh-CN": {"name": "台股法人筹码", "desc": "外资、投信、自营商三大法人买卖超"},
        "ru": {
            "name": "Потоки институционалов Тайваня",
            "desc": "Сделки иностранных, доверительных и проприетарных трейдеров",
        },
    },
    "tw_pe_ratio": {
        "en": {"name": "TW P/E Ratio", "desc": "P/E ratio, dividend yield, P/B ratio"},
        "zh-CN": {"name": "台股本益比", "desc": "P/E 比、股息殖利率、P/B 比"},
        "ru": {"name": "P/E акций Тайваня", "desc": "P/E, дивидендная доходность, P/B"},
    },
    "tw_monthly_revenue": {
        "en": {
            "name": "TW Monthly Revenue",
            "desc": "Monthly revenue with MoM and YoY growth",
        },
        "zh-CN": {"name": "台股月营收", "desc": "月营收数据含 MoM、YoY 成长率"},
        "ru": {
            "name": "Месячная выручка (Тайвань)",
            "desc": "Месячная выручка с ростом MoM и YoY",
        },
    },
    "tw_dividend": {
        "en": {
            "name": "TW Dividend",
            "desc": "Cash/stock dividend and ex-dividend dates",
        },
        "zh-CN": {"name": "台股股利", "desc": "现金股利、股票股利、除权息日期"},
        "ru": {
            "name": "Дивиденды (Тайвань)",
            "desc": "Денежные/акционерные дивиденды и даты",
        },
    },
    "tw_foreign_top20": {
        "en": {
            "name": "Foreign Holdings Top 20",
            "desc": "Top 20 foreign and mainland holdings",
        },
        "zh-CN": {"name": "外资持股 Top 20", "desc": "外资与陆资持股前 20 名排行"},
        "ru": {
            "name": "Топ-20 иностранных позиций",
            "desc": "Топ-20 позиций иностранных и материковых инвесторов",
        },
    },
    # ── 美股 ──
    "us_stock_price": {
        "en": {
            "name": "US Stock Price",
            "desc": "Real-time US stock prices (15-min delay, Yahoo Finance)",
        },
        "zh-CN": {
            "name": "美股即时股价",
            "desc": "美股即时价格（15 分钟延迟，Yahoo Finance）",
        },
        "ru": {
            "name": "Цена акций США",
            "desc": "Актуальные цены акций США (задержка 15 мин, Yahoo Finance)",
        },
    },
    "us_technical_analysis": {
        "en": {
            "name": "US Stock Technical",
            "desc": "US stock RSI / MACD / Bollinger / moving averages",
        },
        "zh-CN": {"name": "美股技术指标", "desc": "美股 RSI / MACD / 布林带 / 均线"},
        "ru": {
            "name": "Теханализ акций США",
            "desc": "RSI / MACD / Боллинджер / скользящие средние",
        },
    },
    "us_news": {
        "en": {"name": "US Stock News", "desc": "Latest US stock news"},
        "zh-CN": {"name": "美股新闻", "desc": "美股相关最新新闻"},
        "ru": {"name": "Новости акций США", "desc": "Последние новости акций США"},
    },
    "us_fundamentals": {
        "en": {
            "name": "US Fundamentals",
            "desc": "P/E, EPS, ROE, market cap, dividend yield",
        },
        "zh-CN": {"name": "美股基本面", "desc": "P/E、EPS、ROE、市值、股息率"},
        "ru": {
            "name": "Фундаментал акций США",
            "desc": "P/E, EPS, ROE, капитализация, дивидендная доходность",
        },
    },
    "us_earnings": {
        "en": {"name": "US Earnings", "desc": "Earnings data and earnings calendar"},
        "zh-CN": {"name": "美股财报", "desc": "财报数据与财报日历"},
        "ru": {
            "name": "Отчёты компаний США",
            "desc": "Данные отчётности и календарь отчётов",
        },
    },
    "us_institutional_holders": {
        "en": {
            "name": "US Institutional Holders",
            "desc": "Institutional investor holdings data",
        },
        "zh-CN": {"name": "美股机构持仓", "desc": "机构投资人持仓数据"},
        "ru": {
            "name": "Институциональные держатели США",
            "desc": "Позиции институциональных инвесторов",
        },
    },
    "us_insider_transactions": {
        "en": {
            "name": "US Insider Transactions",
            "desc": "Corporate insider buy/sell records",
        },
        "zh-CN": {"name": "美股内部人交易", "desc": "公司内部人买卖记录"},
        "ru": {
            "name": "Сделки инсайдеров США",
            "desc": "Сделки покупки/продажи корпоративных инсайдеров",
        },
    },
    # ── KYC / TON 錢包 ──
    "submit_kyc_application": {
        "en": {
            "name": "Inclusive Finance Account Application",
            "desc": "Migrant workers/new immigrants apply for digital bank account (KYC) using TON wallet as digital ID. High-risk action, requires user consent.",
        },
        "zh-CN": {
            "name": "普惠金融开户申请",
            "desc": "移工/新住民以 TON 钱包为数位身分申请数位银行开户（KYC）。高风险动作，需使用者同意。",
        },
        "ru": {
            "name": "Заявка на счёт (инклюзивные финансы)",
            "desc": "Мигранты подают заявку на цифровой банковский счёт (KYC) через TON-кошелёк. Высокий риск, требуется согласие.",
        },
    },
    "record_entry": {
        "en": {
            "name": "Record Trade",
            "desc": "Record an entry in the user's unified ledger (expense/income/trade) (crypto, stocks, forex, commodities)",
        },
        "zh-CN": {
            "name": "统一帐本记录",
            "desc": "在使用者的统一帐本记录一笔交易（加密货币/台股/美股/外汇/商品）",
        },
        "ru": {
            "name": "Запись в журнал",
            "desc": "Записать сделку в инвестиционный журнал (крипто, акции, форекс, сырьё)",
        },
    },
    "add_calendar_event": {
        "en": {
            "name": "Add to Calendar",
            "desc": "Add a dated reminder to the user's calendar; the daily brief reminds the day before and on the day",
        },
        "zh-CN": {
            "name": "加入日历",
            "desc": "把一个日期事件加进用户日历，早报前一天与当天提醒",
        },
        "ru": {
            "name": "Добавить в календарь",
            "desc": "Добавить событие с датой в календарь; сводка напомнит накануне и в день события",
        },
    },
    "sec_filings": {
        "en": {
            "name": "SEC Filings",
            "desc": "Recent SEC EDGAR filings for a US company (8-K, 10-Q/10-K, Form 4 insiders, 13D/13G) with official links",
        },
        "zh-CN": {
            "name": "SEC 申报文件",
            "desc": "美股公司最近的 SEC 申报（8-K、10-Q／10-K、Form 4 内部人、13D／13G），附官方链接",
        },
        "ru": {
            "name": "Отчётность SEC",
            "desc": "Последние документы компании в SEC EDGAR (8-K, 10-Q/10-K, Form 4, 13D/13G) со ссылками",
        },
    },
    "get_btc_network_status": {
        "en": {
            "name": "Bitcoin Network Status",
            "desc": "BTC fee estimates, mempool congestion, block height, difficulty adjustment and next halving (mempool.space)",
        },
        "zh-CN": {
            "name": "比特币网络状态",
            "desc": "BTC 手续费建议、mempool 拥堵、区块高度、难度调整、下一次减半（mempool.space）",
        },
        "ru": {
            "name": "Состояние сети Bitcoin",
            "desc": "Комиссии BTC, загрузка mempool, высота блока, корректировка сложности и следующий халвинг (mempool.space)",
        },
    },
    "get_my_scorecard": {
        "en": {
            "name": "My Scorecard",
            "desc": "Hit rate and excess return of your own ledger trades, scored against the market at 30/90 days",
        },
        "zh-CN": {
            "name": "我的判断成绩",
            "desc": "账本交易到期对答案、扣大盘后的命中率与超额回报（只回本人）",
        },
        "ru": {
            "name": "Мои результаты",
            "desc": "Точность и избыточная доходность ваших сделок из журнала, оценённые рынком через 30/90 дней",
        },
    },
    "record_call": {
        "en": {
            "name": "Record a Call",
            "desc": "Record your own bullish/bearish call with a horizon; scored against the market at expiry (confirmation card)",
        },
        "zh-CN": {
            "name": "记一笔判断",
            "desc": "记下你自己的看多／看空判断与期限，到期用收盘价评分（确认卡核准才写）",
        },
        "ru": {
            "name": "Записать прогноз",
            "desc": "Запишите свой прогноз (рост/падение) со сроком; оценка по рынку по истечении (карточка подтверждения)",
        },
    },
    "list_calendar_events": {
        "en": {
            "name": "View Calendar",
            "desc": "List upcoming calendar events (system earnings/revenue dates and the user's own)",
        },
        "zh-CN": {
            "name": "查看日历",
            "desc": "列出接下来 N 天的日历事件（系统财报／月营收＋自定义）",
        },
        "ru": {
            "name": "Календарь",
            "desc": "Показать ближайшие события календаря (отчёты, выручка и собственные напоминания)",
        },
    },
    "query_ledger": {
        "en": {
            "name": "Query Trades",
            "desc": "Query the user's ledger from their investment journal",
        },
        "zh-CN": {"name": "查询帐本记录", "desc": "查询使用者的投资交易历史"},
        "ru": {
            "name": "Запрос журнала",
            "desc": "Запрос журнала из инвестиционного журнала",
        },
    },
    "delete_ledger_entry": {
        "en": {
            "name": "Delete Ledger Entry",
            "desc": "Propose deleting one entry from the user's ledger (needs approval)",
        },
        "zh-CN": {
            "name": "删除帐本单笔记录",
            "desc": "提议删除帐本中的一笔记录（需使用者核准）",
        },
        "ru": {
            "name": "Удалить запись журнала",
            "desc": "Предложить удаление записи из журнала (требуется подтверждение)",
        },
    },
    "update_ledger_entry": {
        "en": {
            "name": "Edit Ledger Entry",
            "desc": "Propose editing one entry in the user's ledger (needs approval)",
        },
        "zh-CN": {
            "name": "修改帐本单笔记录",
            "desc": "提议修改帐本中一笔记录（需使用者核准）",
        },
        "ru": {
            "name": "Изменить запись журнала",
            "desc": "Предложить изменение записи журнала (требуется подтверждение)",
        },
    },
    "get_portfolio_pnl": {
        "en": {
            "name": "Portfolio PnL",
            "desc": "Calculate the user's portfolio profit/loss with current market prices",
        },
        "zh-CN": {
            "name": "持仓损益计算",
            "desc": "计算使用者全部持仓的损益（加权平均成本，接市场现价）",
        },
        "ru": {
            "name": "Прибыль портфеля",
            "desc": "Расчёт прибыли/убытка портфеля с текущими ценами",
        },
    },
    "get_ton_balance": {
        "en": {
            "name": "TON Wallet Balance",
            "desc": "Query TON balance of a wallet (read-only, via toncenter API)",
        },
        "zh-CN": {
            "name": "TON 钱包余额",
            "desc": "查询 TON 钱包的 TON 余额（唯读，沿用 toncenter API）",
        },
        "ru": {
            "name": "Баланс TON-кошелька",
            "desc": "Баланс TON кошелька (только чтение, toncenter API)",
        },
    },
    "get_ton_jetton_balances": {
        "en": {
            "name": "TON Jetton Balances",
            "desc": "Query all jetton balances held by a TON wallet (with USD/TON valuation)",
        },
        "zh-CN": {
            "name": "TON Jetton 余额",
            "desc": "查询 TON 钱包持有的所有 jetton 代币余额（含 USD/TON 估值）",
        },
        "ru": {
            "name": "Балансы jetton TON",
            "desc": "Все балансы jetton в TON-кошельке (с оценкой USD/TON)",
        },
    },
    "get_my_wallet_overview": {
        "en": {
            "name": "Wallet Holdings Overview",
            "desc": "On-chain holdings of bound wallets (EVM: ETH/USDC; TON: TON + jettons) with USD valuation",
        },
        "zh-CN": {
            "name": "钱包持仓总览",
            "desc": "查询绑定钱包的链上持仓（EVM：ETH／USDC；TON：TON＋jetton）与 USD 估值",
        },
        "ru": {
            "name": "Обзор активов кошельков",
            "desc": "Ончейн-активы привязанных кошельков (EVM: ETH/USDC; TON: TON + jettons) с оценкой в USD",
        },
    },
    # ── Gas / 鏈上數據 ──
    "get_gas_fees": {
        "en": {
            "name": "Gas Fees",
            "desc": "Query current blockchain gas fees (ETH etc.)",
        },
        "zh-CN": {"name": "Gas 费用", "desc": "查询当前区块链 Gas 费用（ETH 等）"},
        "ru": {
            "name": "Комиссии Gas",
            "desc": "Текущие комиссии gas блокчейна (ETH и др.)",
        },
    },
    "get_exchange_flow": {
        "en": {
            "name": "Exchange Fund Flow",
            "desc": "Query exchange inflow/outflow (whale activity)",
        },
        "zh-CN": {
            "name": "交易所资金流",
            "desc": "查询交易所资金流入/流出（鲸鱼动向）",
        },
        "ru": {
            "name": "Потоки средств бирж",
            "desc": "Приток/отток средств на биржах (активность китов)",
        },
    },
    "get_staking_yield": {
        "en": {"name": "Staking Yield", "desc": "Query staking yield of major tokens"},
        "zh-CN": {"name": "质押收益率", "desc": "查询主流代币的质押收益率"},
        "ru": {
            "name": "Доходность стейкинга",
            "desc": "Доходность стейкинга основных токенов",
        },
    },
    "get_dex_pair_info": {
        "en": {
            "name": "DEX Pair Info",
            "desc": "Query DEX pair info (liquidity/volume)",
        },
        "zh-CN": {"name": "DEX 交易对", "desc": "查询 DEX 交易对资讯（流动性/交易量）"},
        "ru": {
            "name": "Информация о паре DEX",
            "desc": "Информация о торговой паре DEX (ликвидность/объём)",
        },
    },
    "get_trending_dex_pairs": {
        "en": {
            "name": "Trending DEX Pairs",
            "desc": "Query currently trending DEX pairs",
        },
        "zh-CN": {"name": "热门 DEX 交易对", "desc": "查询当下热门的 DEX 交易对"},
        "ru": {"name": "Популярные пары DEX", "desc": "Актуальные популярные пары DEX"},
    },
    "get_contract_info": {
        "en": {
            "name": "Contract Info",
            "desc": "Query contract address info (Etherscan)",
        },
        "zh-CN": {"name": "合约资讯", "desc": "查询合约地址资讯（Etherscan）"},
        "ru": {
            "name": "Информация о контракте",
            "desc": "Информация об адресе контракта (Etherscan)",
        },
    },
    "get_eth_price_etherscan": {
        "en": {"name": "ETH Price", "desc": "Query current ETH price (Etherscan)"},
        "zh-CN": {"name": "ETH 价格", "desc": "查询 ETH 当前价格（Etherscan）"},
        "ru": {"name": "Цена ETH", "desc": "Текущая цена ETH (Etherscan)"},
    },
    # ── 商品 ──
    "get_commodity_price": {
        "en": {
            "name": "Commodity Price",
            "desc": "Query spot prices of commodities (gold/oil/copper etc.)",
        },
        "zh-CN": {"name": "商品价格", "desc": "查询大宗商品（黄金/原油/铜等）现货价格"},
        "ru": {
            "name": "Цены на сырьё",
            "desc": "Спотовые цены на сырьё (золото/нефть/медь и др.)",
        },
    },
    "get_commodity_futures_price": {
        "en": {"name": "Commodity Futures", "desc": "Query commodity futures prices"},
        "zh-CN": {"name": "商品期货", "desc": "查询大宗商品期货价格"},
        "ru": {"name": "Фьючерсы на сырьё", "desc": "Цены фьючерсов на сырьё"},
    },
    "get_all_commodities_prices": {
        "en": {
            "name": "All Commodities Prices",
            "desc": "Query all commodity prices at a glance",
        },
        "zh-CN": {"name": "全商品价格", "desc": "查询所有大宗商品价格一览"},
        "ru": {"name": "Все цены на сырьё", "desc": "Все цены на сырьё"},
    },
    "get_gold_silver_ratio": {
        "en": {
            "name": "Gold/Silver Ratio",
            "desc": "Query gold/silver price ratio (safe-haven indicator)",
        },
        "zh-CN": {"name": "金银比", "desc": "查询黄金/白银价格比（避险指标）"},
        "ru": {
            "name": "Золото/серебро",
            "desc": "Отношение цен золота/серебра (индикатор безопасности)",
        },
    },
    "get_oil_price_analysis": {
        "en": {
            "name": "Oil Price Analysis",
            "desc": "Query crude oil price and analysis",
        },
        "zh-CN": {"name": "油价分析", "desc": "查询原油价格与分析"},
        "ru": {"name": "Анализ цен на нефть", "desc": "Цены на сырую нефть и анализ"},
    },
    # ── 外匯 ──
    "get_forex_rate": {
        "en": {"name": "Forex Rate", "desc": "Query forex rates (USD/TWD etc.)"},
        "zh-CN": {"name": "汇率查询", "desc": "查询外汇汇率（USD/TWD 等）"},
        "ru": {"name": "Курс валют", "desc": "Курсы валют (USD/TWD и др.)"},
    },
    "get_all_forex_rates": {
        "en": {"name": "All Forex Rates", "desc": "Query all major forex rates"},
        "zh-CN": {"name": "全汇率", "desc": "查询所有主要外汇汇率"},
        "ru": {"name": "Все курсы валют", "desc": "Все основные курсы валют"},
    },
    "get_usd_twd_rate": {
        "en": {"name": "USD/TWD Rate", "desc": "Query USD/TWD exchange rate"},
        "zh-CN": {"name": "台币汇率", "desc": "查询 USD/TWD 汇率"},
        "ru": {"name": "Курс USD/TWD", "desc": "Курс доллара к тайваньскому доллару"},
    },
    # ── 全球股市 ──
    "global_stock_price": {
        "en": {"name": "Global Stock Price", "desc": "Query global stock prices"},
        "zh-CN": {"name": "全球股价", "desc": "查询全球股票价格"},
        "ru": {"name": "Цены мировых акций", "desc": "Цены акций по всему миру"},
    },
    "global_stock_technical": {
        "en": {
            "name": "Global Stock Technical",
            "desc": "Global stock technical analysis",
        },
        "zh-CN": {"name": "全球股技术指标", "desc": "全球股技术分析"},
        "ru": {
            "name": "Теханализ мировых акций",
            "desc": "Технический анализ мировых акций",
        },
    },
    "global_stock_fundamentals": {
        "en": {
            "name": "Global Stock Fundamentals",
            "desc": "Global stock fundamentals",
        },
        "zh-CN": {"name": "全球股基本面", "desc": "全球股基本面资料"},
        "ru": {
            "name": "Фундаментал мировых акций",
            "desc": "Фундаментальные показатели мировых акций",
        },
    },
    "global_stock_news": {
        "en": {"name": "Global Stock News", "desc": "Global stock related news"},
        "zh-CN": {"name": "全球股新闻", "desc": "全球股相关新闻"},
        "ru": {"name": "Новости мировых акций", "desc": "Новости мировых акций"},
    },
    "global_stock_snapshot": {
        "en": {"name": "Global Stock Snapshot", "desc": "Global stock market snapshot"},
        "zh-CN": {"name": "全球股快照", "desc": "全球股市场快照"},
        "ru": {"name": "Снимок мировых акций", "desc": "Снимок рынка мировых акций"},
    },
    # ── 市場指數 ──
    "get_market_indices": {
        "en": {"name": "Market Indices", "desc": "Query major market indices"},
        "zh-CN": {"name": "市场指数", "desc": "查询主要市场指数"},
        "ru": {"name": "Биржевые индексы", "desc": "Основные биржевые индексы"},
    },
    "get_vix_index": {
        "en": {"name": "VIX Index", "desc": "Query VIX volatility index"},
        "zh-CN": {"name": "VIX 指数", "desc": "查询 VIX 波动率指数"},
        "ru": {"name": "Индекс VIX", "desc": "Индекс волатильности VIX"},
    },
    "get_sp500_performance": {
        "en": {"name": "S&P 500 Performance", "desc": "Query S&P 500 performance"},
        "zh-CN": {"name": "标普 500 表现", "desc": "查询标普 500 表现"},
        "ru": {"name": "Динамика S&P 500", "desc": "Динамика индекса S&P 500"},
    },
    "get_sector_performance": {
        "en": {"name": "Sector Performance", "desc": "Query sector performance"},
        "zh-CN": {"name": "板块表现", "desc": "查询板块表现"},
        "ru": {"name": "Динамика секторов", "desc": "Динамика по секторам"},
    },
    # ── 快照（台/美）──
    "tw_stock_snapshot": {
        "en": {"name": "TW Stock Snapshot", "desc": "Taiwan stock market snapshot"},
        "zh-CN": {"name": "台股快照", "desc": "台股市场快照"},
        "ru": {
            "name": "Снимок акций Тайваня",
            "desc": "Снимок рынка тайваньских акций",
        },
    },
    "tw_market_index": {
        "en": {"name": "TW Market Index", "desc": "Query Taiwan market index"},
        "zh-CN": {"name": "台股指数", "desc": "查询台湾市场指数"},
        "ru": {"name": "Индекс рынка Тайваня", "desc": "Индекс тайваньского рынка"},
    },
    "us_stock_snapshot": {
        "en": {"name": "US Stock Snapshot", "desc": "US stock market snapshot"},
        "zh-CN": {"name": "美股快照", "desc": "美股市场快照"},
        "ru": {"name": "Снимок акций США", "desc": "Снимок рынка акций США"},
    },
    # ── Symbol resolver ──
    "resolve_symbol": {
        "en": {
            "name": "Symbol Resolver",
            "desc": "Resolve asset name to standard ticker symbol",
        },
        "zh-CN": {"name": "代号解析器", "desc": "将资产名称解析为标准代号"},
        "ru": {
            "name": "Разрешение символов",
            "desc": "Преобразовать название актива в тикер",
        },
    },
    # ── Agent 內建能力（HITL / 記憶 / 方法管理 / 知識庫 / 釐清）──
    "clarify": {
        "en": {
            "name": "Clarify Question",
            "desc": "Ask the user a clarifying question when scope/target is unclear",
        },
        "zh-CN": {"name": "澄清问题", "desc": "向用户提出澄清问题（范围/标的不明时）"},
        "ru": {
            "name": "Уточнение вопроса",
            "desc": "Задать уточняющий вопрос при неясной области/активе",
        },
    },
    "load_skill": {
        "en": {
            "name": "Load Analysis Skill",
            "desc": "Load the full content of an analysis skill (steps + output format)",
        },
        "zh-CN": {
            "name": "加载分析方法",
            "desc": "加载分析方法 skill 的完整内容（步骤＋输出格式）",
        },
        "ru": {
            "name": "Загрузить метод",
            "desc": "Загрузить полное содержание метода анализа (шаги + формат вывода)",
        },
    },
    "load_knowledge": {
        "en": {
            "name": "Query Personal Knowledge",
            "desc": "Query past analyses in personal knowledge base (LLM Wiki)",
        },
        "zh-CN": {
            "name": "查询个人知识库",
            "desc": "查询个人知识库中相关的过去分析（LLM Wiki）",
        },
        "ru": {
            "name": "Личная база знаний",
            "desc": "Найти прошлые анализы в личной базе знаний (LLM Wiki)",
        },
    },
    "remember": {
        "en": {
            "name": "Remember Info",
            "desc": "Remember user-volunteered preferences/background/holdings (writes only after HITL approval)",
        },
        "zh-CN": {
            "name": "记住信息",
            "desc": "记住用户主动说的偏好/背景/持仓（HITL 同意后才写入）",
        },
        "ru": {
            "name": "Запомнить",
            "desc": "Запомнить предпочтения/опыт/активы пользователя (только после согласия HITL)",
        },
    },
    "list_my_skills_memory": {
        "en": {
            "name": "List My Skills & Memory",
            "desc": "List the user's own custom skills and remembered facts (read-only)",
        },
        "zh-CN": {
            "name": "列出我的分析方法与记忆",
            "desc": "列出用户自己的分析方法与记忆（只读）",
        },
        "ru": {
            "name": "Мои методы и память",
            "desc": "Показать личные методы и факты пользователя (только чтение)",
        },
    },
    "propose_custom_skill": {
        "en": {
            "name": "Propose Custom Skill",
            "desc": "Propose to create/update/delete a personal analysis method (writes only after HITL approval)",
        },
        "zh-CN": {
            "name": "提议个人分析方法",
            "desc": "提议建立/修改/删除个人分析方法（HITL 同意后才写入）",
        },
        "ru": {
            "name": "Предложить метод",
            "desc": "Предложить создать/изменить/удалить личный метод (только после согласия HITL)",
        },
    },
    # ── MCP 工具（crypto-trader + Manifund，動態載入、不在 _TOOLS_SEED）──
    # MCP 工具沒有 _TOOLS_SEED 繁中 fallback，故這裡連 zh-TW 一起放 4 語。
    # 工具名與線上 MCP server 實際暴露名一致（2026-08-15 production 實查）。
    # 註：crypto-trader 的 get_crypto_price 與 seed 工具同名，走上方 seed 條目（語意相同）。
    "get_crypto_market_data": {
        "zh-TW": {"name": "加密貨幣市場數據", "desc": "查詢加密貨幣市場數據"},
        "en": {
            "name": "Crypto Market Data",
            "desc": "Query cryptocurrency market data",
        },
        "zh-CN": {"name": "加密货币市场数据", "desc": "查询加密货币市场数据"},
        "ru": {
            "name": "Данные крипторынка",
            "desc": "Запросить данные криптовалютного рынка",
        },
    },
    "get_crypto_historical_data": {
        "zh-TW": {"name": "加密貨幣歷史數據", "desc": "查詢加密貨幣歷史價格數據"},
        "en": {
            "name": "Crypto Historical Data",
            "desc": "Query cryptocurrency historical price data",
        },
        "zh-CN": {"name": "加密货币历史数据", "desc": "查询加密货币历史价格数据"},
        "ru": {
            "name": "История цен криптовалют",
            "desc": "Запросить исторические данные цен криптовалют",
        },
    },
    "search_crypto": {
        "zh-TW": {"name": "加密貨幣搜尋", "desc": "以關鍵字搜尋加密貨幣"},
        "en": {"name": "Crypto Search", "desc": "Search cryptocurrencies by keyword"},
        "zh-CN": {"name": "加密货币搜索", "desc": "以关键字搜索加密货币"},
        "ru": {
            "name": "Поиск криптовалют",
            "desc": "Поиск криптовалют по ключевому слову",
        },
    },
    "get_trending_crypto": {
        "zh-TW": {"name": "熱門加密貨幣", "desc": "查詢目前熱門的加密貨幣"},
        "en": {
            "name": "Trending Crypto",
            "desc": "Query currently trending cryptocurrencies",
        },
        "zh-CN": {"name": "热门加密货币", "desc": "查询当前热门的加密货币"},
        "ru": {
            "name": "Популярные криптовалюты",
            "desc": "Запросить популярные сейчас криптовалюты",
        },
    },
    "get_global_crypto_data": {
        "zh-TW": {"name": "全球市場總覽", "desc": "查詢全球加密貨幣市場整體數據"},
        "en": {
            "name": "Global Crypto Overview",
            "desc": "Query global cryptocurrency market overview",
        },
        "zh-CN": {"name": "全球市场总览", "desc": "查询全球加密货币市场整体数据"},
        "ru": {
            "name": "Обзор криптовалютного рынка",
            "desc": "Запросить общий обзор мирового крипторынка",
        },
    },
    "search_projects": {
        "zh-TW": {"name": "Manifund 專案搜尋", "desc": "搜尋 Manifund 上的公益專案"},
        "en": {
            "name": "Manifund Project Search",
            "desc": "Search philanthropic projects on Manifund",
        },
        "zh-CN": {"name": "Manifund 项目搜索", "desc": "搜索 Manifund 上的公益项目"},
        "ru": {
            "name": "Поиск проектов Manifund",
            "desc": "Поиск благотворительных проектов на Manifund",
        },
    },
    "get_project": {
        "zh-TW": {"name": "專案詳情", "desc": "查詢 Manifund 專案詳細資訊"},
        "en": {"name": "Project Detail", "desc": "Query Manifund project details"},
        "zh-CN": {"name": "项目详情", "desc": "查询 Manifund 项目详细信息"},
        "ru": {"name": "Детали проекта", "desc": "Запросить детали проекта Manifund"},
    },
    "get_comments": {
        "zh-TW": {"name": "專案留言", "desc": "查看 Manifund 專案的留言討論"},
        "en": {
            "name": "Project Comments",
            "desc": "View comments on a Manifund project",
        },
        "zh-CN": {"name": "项目留言", "desc": "查看 Manifund 项目的留言讨论"},
        "ru": {
            "name": "Комментарии проекта",
            "desc": "Просмотреть комментарии к проекту Manifund",
        },
    },
    "recommend_projects": {
        "zh-TW": {"name": "專案推薦", "desc": "依條件推薦 Manifund 專案"},
        "en": {
            "name": "Project Recommendations",
            "desc": "Get recommended Manifund projects",
        },
        "zh-CN": {"name": "项目推荐", "desc": "按条件推荐 Manifund 项目"},
        "ru": {
            "name": "Рекомендации проектов",
            "desc": "Получить рекомендованные проекты Manifund",
        },
    },
    "search_users": {
        "zh-TW": {"name": "用戶搜尋", "desc": "搜尋 Manifund 上的用戶"},
        "en": {"name": "User Search", "desc": "Search users on Manifund"},
        "zh-CN": {"name": "用户搜索", "desc": "搜索 Manifund 上的用户"},
        "ru": {
            "name": "Поиск пользователей",
            "desc": "Поиск пользователей на Manifund",
        },
    },
    "get_user": {
        "zh-TW": {"name": "用戶詳情", "desc": "查詢 Manifund 用戶詳細資訊"},
        "en": {"name": "User Detail", "desc": "Query Manifund user details"},
        "zh-CN": {"name": "用户详情", "desc": "查询 Manifund 用户详细信息"},
        "ru": {
            "name": "Профиль пользователя",
            "desc": "Запросить профиль пользователя Manifund",
        },
    },
    "get_txns": {
        "zh-TW": {"name": "交易紀錄", "desc": "查詢 Manifund 專案/用戶的交易紀錄"},
        "en": {
            "name": "Transactions",
            "desc": "Query Manifund project/user transactions",
        },
        "zh-CN": {"name": "交易记录", "desc": "查询 Manifund 项目/用户的交易记录"},
        "ru": {
            "name": "Транзакции",
            "desc": "Запросить транзакции проекта/пользователя Manifund",
        },
    },
    "get_user_balances": {
        "zh-TW": {"name": "用戶餘額", "desc": "查詢 Manifund 用戶的餘額"},
        "en": {"name": "User Balances", "desc": "Query a Manifund user's balances"},
        "zh-CN": {"name": "用户余额", "desc": "查询 Manifund 用户的余额"},
        "ru": {
            "name": "Балансы пользователя",
            "desc": "Запросить балансы пользователя Manifund",
        },
    },
    "list_causes": {
        "zh-TW": {"name": "公益類別列表", "desc": "列出 Manifund 的公益類別"},
        "en": {"name": "Causes List", "desc": "List Manifund philanthropic causes"},
        "zh-CN": {"name": "公益类别列表", "desc": "列出 Manifund 的公益类别"},
        "ru": {
            "name": "Список категорий",
            "desc": "Показать благотворительные категории Manifund",
        },
    },
}


def get_translation(tool_id: str, language: str, field: str = "name") -> str | None:
    """查單一工具的單一語言翻譯。

    Args:
        tool_id: 工具 ID
        language: 語言代碼（zh-TW / zh-CN / en / ru）
        field: "name" 或 "desc"

    Returns:
        翻譯字串；缺譯（工具或語言不存在）回 None。
    """
    entry = _TRANSLATIONS.get(tool_id)
    if not entry:
        return None
    lang_entry = entry.get(language)
    if not lang_entry:
        return None
    return lang_entry.get(field)


def get_supported_languages() -> tuple[str, ...]:
    """回傳官方支援語言清單。"""
    return SUPPORTED_LANGUAGES
