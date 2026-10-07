"""
加密貨幣工具模組

將大型 crypto_tools.py 拆分為多個功能模組：
- analysis.py: 技術分析、價格、新聞
- sentiment.py: 市場情緒、熱門代幣
- defi.py: DeFi TVL、代幣供應量
- onchain.py: Gas 費用、鯨魚交易
- etherscan.py: 以太坊鏈上數據
- dex.py: DEX 交易對數據
- ton_balance.py: TON 錢包餘額查詢
"""

from .analysis import (
    explain_market_movement_tool,
    get_crypto_price_tool,
    news_analysis_tool,
    technical_analysis_tool,
)
from .btc_network import get_btc_network_status  # noqa: F401
from .defi import (
    extract_crypto_symbols_tool,
    get_crypto_categories_and_gainers,
    get_crypto_market_cap,
    get_defillama_tvl,
    get_dex_volume,
    get_staking_yield,
    get_token_supply,
    get_token_unlocks,
)
from .dex import (
    get_dex_pair_info,
    get_trending_dex_pairs,
    search_dex_pairs,
)
from .etherscan import (
    get_address_transactions,
    get_contract_info,
    get_erc20_token_balance,
    get_eth_balance,
    get_eth_price_from_etherscan,
)
from .exchange_rate import (  # noqa: F401 — re-export for agents/API
    get_exchange_rate,
    infer_category,
)
from .goplus import (
    check_address_safety,
    check_token_security,
)
from .onchain import (
    get_exchange_flow,
    get_gas_fees,
    get_whale_transactions,
)
from .sentiment import (
    get_current_time_taipei,
    get_fear_and_greed_index,
    get_futures_data,
    get_trending_tokens,
)
from .ton_balance import (
    get_ton_balance,
)
from .ton_jetton_balances import (
    get_ton_jetton_balances,
)
from .ton_safety import (
    assess_jetton_safety_tool,
)
from .trade_journal import (  # noqa: F401 — re-export for bootstrap
    get_portfolio_pnl,
    query_ledger,
    record_entry,
)
from .wallet_overview import (
    get_my_wallet_overview,
)

__all__ = [
    # Analysis
    "technical_analysis_tool",
    "news_analysis_tool",
    "get_crypto_price_tool",
    "explain_market_movement_tool",
    # Sentiment
    "get_fear_and_greed_index",
    "get_trending_tokens",
    "get_futures_data",
    "get_current_time_taipei",
    # DeFi
    "get_defillama_tvl",
    "get_crypto_categories_and_gainers",
    "get_crypto_market_cap",
    "get_dex_volume",
    "get_token_unlocks",
    "get_token_supply",
    "extract_crypto_symbols_tool",
    "get_staking_yield",
    # On-chain
    "get_gas_fees",
    "get_btc_network_status",
    "get_whale_transactions",
    "get_exchange_flow",
    # Etherscan
    "get_eth_balance",
    "get_erc20_token_balance",
    "get_address_transactions",
    "get_contract_info",
    "get_eth_price_from_etherscan",
    # GoPlus Security
    "check_token_security",
    "check_address_safety",
    # DEX
    "get_dex_pair_info",
    "get_trending_dex_pairs",
    "search_dex_pairs",
    # TON balance
    "get_ton_balance",
    # TON jetton balances (TonAPI)
    "get_ton_jetton_balances",
    # TON wallet overview (組合視圖)
    "get_my_wallet_overview",
    # TON jetton safety (路線 B，與 GoPlus 對稱)
    "assess_jetton_safety_tool",
]
