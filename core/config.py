# ruff: noqa: E402
# ^ E402 ignored because config validation code needs to run before imports
import os
import sys

from dotenv import load_dotenv

from core.feature_flags import env_flag

# Fix Windows console encoding (cp950 cannot handle emoji/unicode)
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Load local defaults from .env, but never override values already provided
# by the shell, process manager, or deployment platform.
load_dotenv()

# === 測試模式配置 ===
# 設為 True 時，跳過登入驗證，自動以測試用戶身份登入
# ⚠️ 安全警告：測試模式會繞過付費、認證等安全檢查
# ⚠️ 預設為 False（安全），開發時需明確設置 TEST_MODE=true
# 可透過環境變數 TEST_MODE=true 來啟用（僅限開發環境）
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

# 🔒 Security: Multi-layer protection for TEST_MODE
if TEST_MODE:
    # Check 1: Environment must NOT be production
    env = os.getenv("ENVIRONMENT", "development").lower()
    if env in ["production", "prod"]:
        raise ValueError(
            "🚨 SECURITY ALERT: TEST_MODE is ENABLED in a PRODUCTION environment!\n"
            "This is NOT allowed. TEST_MODE bypasses security checks.\n"
            "Please set TEST_MODE=false or remove it from environment variables."
        )

    # Check 2: Must explicitly confirm understanding of risks
    confirmation = os.getenv("TEST_MODE_CONFIRMATION", "")
    if confirmation != "I_UNDERSTAND_THE_RISKS":
        raise ValueError(
            "🚨 SECURITY ALERT: TEST_MODE requires explicit confirmation.\n"
            "To enable TEST_MODE in development, set:\n"
            "  TEST_MODE=true\n"
            "  TEST_MODE_CONFIRMATION=I_UNDERSTAND_THE_RISKS"
        )

    # Check 3: IP whitelist (optional but recommended)
    ip_whitelist = os.getenv("TEST_MODE_IP_WHITELIST", "")
    if ip_whitelist:
        # Simple format check (IP should have dots)
        if "." not in ip_whitelist:
            raise ValueError(
                "🚨 SECURITY ALERT: TEST_MODE_IP_WHITELIST must be a valid IP address.\n"
                "Example: TEST_MODE_IP_WHITELIST=127.0.0.1"
            )

    # All checks passed - log warning but allow
    import logging

    logging.warning("⚠️⚠️⚠️ TEST_MODE IS ENABLED - SECURITY CHECKS ARE BYPASSED ⚠️⚠️⚠️")

# 測試用戶資料（TEST_MODE=True 時使用）
TEST_USER = {
    "uid": "test-user-001",
    "username": "TestUser",
}

# === AI 模型配置 ===
# 所有模型名稱統一由 core/model_config.py 管理，在此不再硬寫字串。
from core.model_config import OPENAI_DEFAULT_MODEL

FAST_THINKING_MODEL = OPENAI_DEFAULT_MODEL  # 用於快速分析（分析師）
DEEP_THINKING_MODEL = OPENAI_DEFAULT_MODEL  # 用於深度思考（交易員、風險管理）

# ============================================================================
# [User-Side] 需要用戶 API Key 的功能
# ============================================================================

# 主要 AI 模型配置（統一配置，簡化架構）
# 原多模型辯論架構已簡化為單一模型
PRIMARY_MODEL = {
    "provider": "user_provided",
    "model": OPENAI_DEFAULT_MODEL,
}

# 向後兼容別名（deprecated，將在未來版本移除）
BULL_RESEARCHER_MODEL = PRIMARY_MODEL
BEAR_RESEARCHER_MODEL = PRIMARY_MODEL
TRADER_MODEL = PRIMARY_MODEL
SYNTHESIS_MODEL = PRIMARY_MODEL

# 查詢解析 (用戶付費)
QUERY_PARSER_MODEL_CONFIG = {
    "provider": "user_provided",
    "model": OPENAI_DEFAULT_MODEL,
}


# ============================================================================

# [Server-Side] 由平台提供的免費功能 (後台運行)

# ============================================================================


# 市場脈動分析器 (平台付費 - 用於生成公共報告)

MARKET_PULSE_MODEL = {
    "provider": "openai_server",  # 使用 SERVER_OPENAI_API_KEY，不影響 BYOK
    "model": OPENAI_DEFAULT_MODEL,
}


# 向後兼容：保留模型名稱字符串（供直接使用模型名稱的代碼使用）
QUERY_PARSER_MODEL = QUERY_PARSER_MODEL_CONFIG["model"]

# 支持的交易所列表，按優先級排序。
# 先 OKX，OKX 抓不到的標的(例如 TON)再 fallback 到 Binance(有 TONUSDT)。
# get_klines 會依序試，OKX 有的標的行為不變、不會多打 Binance。
SUPPORTED_EXCHANGES = ["okx", "binance"]

# 合約市場分析的默認槓桿
DEFAULT_FUTURES_LEVERAGE = 5

# 並行分析的最大工作線程數
MAX_ANALYSIS_WORKERS = 2

# Plan Reflection: max number of re-planning attempts allowed when reflection rejects the plan
PLAN_REFLECTION_MAX_RETRIES = 3

# Gradio 介面的預設值
DEFAULT_INTERVAL = "1d"
DEFAULT_KLINES_LIMIT = 200  # 業界標準：200 天，確保統計有效性

# 新聞抓取數量限制 (每個來源)
NEWS_FETCH_LIMIT = 10  # 每個來源嘗試抓取 10 條新聞

# 加密貨幣篩選器的預設值
SCREENER_DEFAULT_LIMIT = 30
SCREENER_DEFAULT_INTERVAL = "1d"

# === 自動篩選器/市場掃描配置 ===
# 指定要每天自動分析的重點幣種 (減少數量以提升效能，建議 3-5 個)
# 注意：僅包含 OKX 交易所實際存在的幣種
SCREENER_TARGET_SYMBOLS = ["BTC", "ETH", "SOL"]

# 自動更新間隔 (分鐘)
SCREENER_UPDATE_INTERVAL_MINUTES = int(
    os.getenv("SCREENER_UPDATE_INTERVAL_MINUTES", "5")
)

# 資金費率自動更新間隔 (秒)
FUNDING_RATE_UPDATE_INTERVAL = int(os.getenv("FUNDING_RATE_UPDATE_INTERVAL", "300"))

# === 市場脈動 (Market Pulse) 配置 ===
# 固定監控的幣種列表 (優先級最高)
# 注意：僅包含 OKX 交易所實際存在的幣種
MARKET_PULSE_TARGETS = ["BTC", "ETH", "SOL"]

# 自動排名的幣種數量 (已停用 - 改為全市場掃描)
# MARKET_PULSE_BATCH_SIZE = 20

# 市場脈動更新頻率 (秒) - 4小時
MARKET_PULSE_UPDATE_INTERVAL = 14400

# === 交易限制配置 ===
MINIMUM_INVESTMENT_USD = 20.0  # 最低投資金額 (USDT)
MAXIMUM_INVESTMENT_USD = 30.0  # 最高投資金額 (USDT)
EXCHANGE_MINIMUM_ORDER_USD = 1.0  # 交易所最低下單金額 (USDT)

# === 交易類型選擇 ===
# 控制是否執行現貨交易和合約交易
# True: 啟用該類型的交易 / False: 停用該類型的交易
ENABLE_SPOT_TRADING = False  # 是否執行現貨交易
ENABLE_FUTURES_TRADING = True  # 是否執行合約交易

# === 加密貨幣分析配置 ===
# 預設要分析的加密貨幣列表。
# 用戶可以在此處修改此列表，以選擇要分析的加密貨幣。
CRYPTO_CURRENCIES_TO_ANALYZE = ["PIUSDT"]

# === OKX API 配置 ===
# 從 .env 檔案或環境變數讀取 OKX API 資訊
OKX_API_KEY = os.getenv("OKX_API_KEY", "")
OKX_API_SECRET = os.getenv("OKX_API_SECRET", "")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")

# === 第三方 API Keys（已停用） ===
# ⚠️ 以下 API Keys 已不再使用，因為：
# 1. 公開平台不適合共享 API Key（配額限制問題）
# 2. 改為引導用戶到專業網站查詢
# 3. 保留配置供未來可能的平台統一 Key 方案使用

# Etherscan API（已停用 - 改為引導到 etherscan.io）
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")

# Whale Alert API（已停用 - 改為引導到 whale-alert.io）
WHALE_ALERT_API_KEY = os.getenv("WHALE_ALERT_API_KEY", "")

# 是否使用模擬盤 (Paper Trading)
# True: 使用模擬盤 / False: 使用真實帳戶
PAPER_TRADING = False

# === 平台網路模式（EVM RPC／USDC 合約／SIWE chain id／付款網路標籤共用）===
# "testnet"（Base Sepolia）或 "mainnet"（Base）。NETWORK 優先；沒設沿用舊的
# TON_NETWORK（正式站目前只設這個）；都沒設＝testnet。
NETWORK = (
    (os.getenv("NETWORK") or "").strip() or (os.getenv("TON_NETWORK") or "testnet").strip()
).lower()
IS_TESTNET = NETWORK != "mainnet"

# === TON Network 配置（只剩 TON 專用工具在用：toncenter、TON 收款地址）===
# 舊名保留成別名，跟 NETWORK 同源。
TON_NETWORK = NETWORK
TON_IS_TESTNET = IS_TESTNET

# 收款錢包（同一個帳戶，不同網路顯示不同地址）
TON_RECEIVING_ADDRESS_TESTNET = os.getenv(
    "TON_RECEIVING_ADDRESS_TESTNET",
    "0QDvjDhEZ128EktbSBrK4CWrw1xTTbx4ojlZ-rF2OOS2HxtR",
)
TON_RECEIVING_ADDRESS_MAINNET = os.getenv(
    "TON_RECEIVING_ADDRESS_MAINNET",
    "UQDvjDhEZ128EktbSBrK4CWrw1xTTbx4ojlZ-rF2OOS2H6Db",
)
TON_RECEIVING_ADDRESS = (
    TON_RECEIVING_ADDRESS_TESTNET if TON_IS_TESTNET else TON_RECEIVING_ADDRESS_MAINNET
)

# toncenter API（驗證鏈上交易用）
TONCENTER_API_BASE = (
    "https://testnet.toncenter.com/api/v2"
    if TON_IS_TESTNET
    else "https://toncenter.com/api/v2"
)
TONCENTER_API_KEY = os.getenv("TONCENTER_API_KEY", "")

# TON Connect manifest 對外網址（須為部署後的 HTTPS URL）
# 正式站 2026-09-17 起自架在 getcryptomind.com；舊的 *.zeabur.app 網域已下線（404）。
TON_MANIFEST_URL = os.getenv("TON_MANIFEST_URL", "https://getcryptomind.com")
# 站台對外網址（EVM 登入網域、robots／sitemap、manifest 對照）。SITE_URL 優先；
# 沒設沿用 TON_MANIFEST_URL（正式站目前設的是這個），再沒有就 getcryptomind.com。
SITE_URL = (
    os.getenv("SITE_URL", "").strip() or TON_MANIFEST_URL or "https://getcryptomind.com"
)

# === TON 支付價格配置（只剩舊資料在讀）===
# 訂閱 2026-09-09、論壇 2026-09-25 起都改收 USDC on Base（見 PREMIUM_USD_PRICES、
# FORUM_*_USD）。create_post 只剩「舊 TON 發文紀錄」的顯示金額在讀；其餘是
# system_config 種子值。
TON_PAYMENT_PRICES = {
    "premium_monthly": float(os.getenv("TON_PRICE_PREMIUM_MONTHLY", "6.0")),
    "premium_yearly": float(os.getenv("TON_PRICE_PREMIUM_YEARLY", "54.0")),
    "create_post": float(os.getenv("TON_PRICE_CREATE_POST", "0.1")),
    "tip": float(os.getenv("TON_PRICE_TIP", "0.1")),
}
# Premium 訂閱價（單位 USD；USDC on Base 按這個收）。PREMIUM_*_USD 優先，
# 沒設沿用舊的 TON_PREMIUM_*_USD（正式站如有設就是這組）。
PREMIUM_USD_PRICES = {
    "premium_monthly": float(
        os.getenv("PREMIUM_MONTHLY_USD") or os.getenv("TON_PREMIUM_MONTHLY_USD", "12.0")
    ),
    "premium_yearly": float(
        os.getenv("PREMIUM_YEARLY_USD") or os.getenv("TON_PREMIUM_YEARLY_USD", "108.0")
    ),
}
# 舊名（同一個 dict）
TON_PREMIUM_USD_ANCHOR = PREMIUM_USD_PRICES
# 論壇付款（單位 USD，USDC on Base 按這個收；2026-09-25 DANNY 從 TON 改過來）。
# 發文費付給平台（免費會員；Premium 免費），打賞直接付給作者的 EVM 錢包。
# 2026-09-25 DANNY：冷啟動期間發文免費（預設 0；0 以下＝免費會員不用付款，每日篇數上限照舊）。
# 要恢復收費就設 FORUM_POST_FEE_USD=0.5。
FORUM_POST_FEE_USD = float(os.getenv("FORUM_POST_FEE_USD", "0"))
FORUM_TIP_MIN_USD = float(os.getenv("FORUM_TIP_MIN_USD", "0.1"))
FORUM_TIP_MAX_USD = float(os.getenv("FORUM_TIP_MAX_USD", "100"))
FORUM_TIP_DEFAULT_USD = 1.0  # 沒指定金額時；會夾在 MIN～MAX 之間
# 金額容差（網路費/精度），允許實付略多
TON_AMOUNT_TOLERANCE = float(os.getenv("TON_AMOUNT_TOLERANCE", "0.001"))

# === 多鏈身份與付款（c041，docs/plans/2026-08-30-multichain-identity-payments-design.md）===
# 總開關（rollback 用）：關閉時 EVM 登入/綁定/付款 endpoints 回 503、前端隱藏 EVM tab。
# TON / Telegram 既有路徑完全不受影響。
MULTICHAIN_ENABLED = env_flag("MULTICHAIN_ENABLED", "true")

# EVM 網路隨 NETWORK 切換（同一設定管理整個平台的網路模式）：
# testnet → Base Sepolia；mainnet → Base mainnet（design 決策點 1：Base + USDC 先行）
if IS_TESTNET:
    EVM_RPC_URL = os.getenv("EVM_RPC_URL", "https://sepolia.base.org")
    # Circle 官方 USDC 合約（Base Sepolia）
    EVM_USDC_CONTRACT = os.getenv(
        "EVM_USDC_CONTRACT", "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    )
else:
    EVM_RPC_URL = os.getenv("EVM_RPC_URL", "https://mainnet.base.org")
    # Circle 官方 USDC 合約（Base mainnet）
    EVM_USDC_CONTRACT = os.getenv(
        "EVM_USDC_CONTRACT", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    )
# EVM 收款地址（決策點 3：DANNY 提供；空＝rail 未配置 → payment endpoints fail-closed 503）。
# 防呆：只接受 0x + 40 hex；格式錯誤視同未設定並記錄錯誤（收款地址錯＝資金進錯地方）。
_EVM_RECV_RAW = os.getenv("EVM_USDC_RECEIVING_ADDRESS", "").strip()
if _EVM_RECV_RAW:
    import re as _re

    if _re.fullmatch(r"0x[0-9a-fA-F]{40}", _EVM_RECV_RAW):
        EVM_USDC_RECEIVING_ADDRESS = _EVM_RECV_RAW.lower()
    else:
        # intentional print: config load happens before logging is wired
        print(
            "ERROR: EVM_USDC_RECEIVING_ADDRESS format invalid "
            "(expected 0x + 40 hex) — evm_usdc rail disabled",
            flush=True,
        )
        EVM_USDC_RECEIVING_ADDRESS = ""
else:
    EVM_USDC_RECEIVING_ADDRESS = ""
# 鏈重組防護：收款確認數門檻（Base ~2 分鐘觀察窗）
EVM_CONFIRMATIONS = int(os.getenv("EVM_CONFIRMATIONS", "12"))
# 穩定幣精度（USDC on Base 為 6 decimals）
STABLE_DECIMALS = 6

# 環境判定（原定義於已移除的 Omniston 區塊——多處 trust/wallet monitor 預設值使用）
_ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()

# === Agent 自主管理 Skills / Memory（docs/plans/2026-08-10-agent-self-managed-skills-memory-design.md）===
# 讓 agent 在聊天中提議新建/修改/刪除自己的 custom skill 與 memory，
# 寫入前必過 HITL consent gate（核准才寫、取消不寫，審查見 docs/plans/2026-10-06-chat-latency-domain-gate.md §E）。
# 2026-10-06 DANNY 拍板改為預設「開」：false/0/no/off 一鍵關閉，回到「只有 remember＋唯讀清單」。
AGENT_SELF_MANAGE_ENABLED = env_flag("AGENT_SELF_MANAGE_ENABLED", "true")

# 免費每日 AI 聊天上限（2026-08 定價 v2，DANNY 核准）— 免費 5 則/日，Premium 不限
FREE_DAILY_CHAT_LIMIT = int(os.getenv("FREE_DAILY_CHAT_LIMIT", "5"))

# CoinGecko TON/USD 快取 TTL（秒）— ton_price.py 共用取價（wallet overview 等）。
# 原名 SWAP_TON_PRICE_CACHE_TTL（swap 已移除，改名中性化；env 未設過，無相容問題）。
TON_USD_PRICE_CACHE_TTL = int(os.getenv("TON_USD_PRICE_CACHE_TTL", "60"))

# === Trust Score（可信分數）配置 ===
# 詳見 docs/plans/2026-08-08-trust-score-retention-design.md
# Phase A0：把分散的信任訊號統整成 0-100 的 trust_score，補 TRUST_DESIGN.md 的
# baseline-30 缺口（assess_identity_trust 計算器已存在於 core/identity/trust.py，
# 只是沒人餵真實訊號）。
# 分數權重/閾值為 v1 判斷題（design doc 待 DANNY 拍板），皆可調、不寫死。
TRUST_SCORE_ENABLED = env_flag(
    "TRUST_SCORE_ENABLED", "true" if _ENVIRONMENT != "production" else "false"
)
# Human Passport (Gitcoin Passport) Scorer API — Phase A1 才接，這裡先留設定點。
# 申請：https://docs.passport.human.tech/building-with-passport/stamps/passport-api/getting-access
HUMAN_PASSPORT_SCORER_ID = os.getenv("HUMAN_PASSPORT_SCORER_ID", "")
HUMAN_PASSPORT_API_KEY = os.getenv("HUMAN_PASSPORT_API_KEY", "")
HUMAN_PASSPORT_SCORER_THRESHOLD = float(os.getenv("HUMAN_PASSPORT_SCORER_THRESHOLD", "20"))
# 活動度衰退：超過此天數未活躍，activity 訊號線性衰減（學 Discourse TL3）。
TRUST_INACTIVITY_DECAY_DAYS = int(os.getenv("TRUST_INACTIVITY_DECAY_DAYS", "90"))
# cron 重算頻率外的「事件觸發重算」開關——錢包被詐騙 DB 命中時立即重算。
TRUST_EVENT_RECOMPUTE_ENABLED = env_flag("TRUST_EVENT_RECOMPUTE_ENABLED", "true")
# EVM 地址綁定（讓 TON 用戶綁 EVM 地址以啟用 Human Passport 訊號）。
# 詳見 docs/plans/2026-08-08-evm-address-binding-design.md
TRUST_EVM_BINDING_ENABLED = env_flag(
    "TRUST_EVM_BINDING_ENABLED", "true" if _ENVIRONMENT != "production" else "false"
)
# EVM 簽章訊息的 domain separator（防跨站重放，與 ton_proof 同哲學）。
TRUST_EVM_BIND_DOMAIN = os.getenv("TRUST_EVM_BIND_DOMAIN", "getcryptomind.com")
# nonce 有效期（秒），學 ton_proof 的 15 分鐘。
TRUST_EVM_BIND_PAYLOAD_TTL = int(os.getenv("TRUST_EVM_BIND_PAYLOAD_TTL", str(15 * 60)))

# === 錢包監測 Dashboard 配置 ===
# 詳見 docs/plans/2026-08-08-wallet-monitor-dashboard-design.md
WALLET_MONITOR_ENABLED = env_flag(
    "WALLET_MONITOR_ENABLED", "true" if _ENVIRONMENT != "production" else "false"
)
# cron 輪詢間隔（分鐘）。太頻繁會打爆 TonAPI rate limit。
WALLET_MONITOR_INTERVAL_MINUTES = int(os.getenv("WALLET_MONITOR_INTERVAL_MINUTES", "10"))
# Entitlement gate：開啟後 cron 只處理 active premium 用戶的排程監測。
# 預設 false（漸進上線；design 2026-08-13 §9.7 / §14.2）。關閉=既有行為。
# 既有 Free 用戶遷移走決策 #11 = B（通知後停止）：先跑通知腳本再開此 flag。
WALLET_MONITOR_PREMIUM_GATE_ENABLED = env_flag(
    "WALLET_MONITOR_PREMIUM_GATE_ENABLED", "false"
)

# === 錢包監測多鏈（路線 C，docs/plans/2026-08-11-wallet-monitor-multichain-design.md）===
# EVM 監測用服務級 Etherscan key（背景 cron，非 BYOK）。
ETHERSCAN_SERVICE_API_KEY = os.getenv("ETHERSCAN_SERVICE_API_KEY", "")
# EVM 監測開關（無服務 key 時自動關閉 EVM 路徑，TON 不受影響）。
# ⚠️ 這個旗標有隱藏前提：沒有 ETHERSCAN_SERVICE_API_KEY 時一律 false，
#    即使 env 設了 true 也一樣（無聲關閉，log 不會說）。
WALLET_MONITOR_EVM_ENABLED = (
    env_flag("WALLET_MONITOR_EVM_ENABLED", "true")
    and bool(ETHERSCAN_SERVICE_API_KEY)
)
# 支援鏈白名單（免費 Etherscan key 實測支援：eth + polygon + arbitrum；bsc/optimism/base 需付費 plan）
WALLET_MONITOR_SUPPORTED_CHAINS = os.getenv(
    "WALLET_MONITOR_SUPPORTED_CHAINS", "ton,eth,polygon,arbitrum"
).split(",")

# === 論壇會員限制配置 ===
# None 表示無限制
FORUM_LIMITS = {
    "daily_post_free": 3,  # 一般會員每日發文上限
    "daily_post_premium": None,  # Premium 會員每日發文上限 (None = 無限)
    "daily_comment_free": 20,  # 一般會員每日回覆上限
    "daily_comment_premium": None,  # Premium 會員每日回覆上限 (None = 無限)
}
