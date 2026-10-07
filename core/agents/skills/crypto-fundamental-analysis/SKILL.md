---
name: crypto-fundamental-analysis
description: "Crypto fundamental analysis — comprehensive assessment of on-chain data, TVL, tokenomics, whale activity, and funding rates. Use when the user asks whether a coin is worth buying, about fundamentals, or about on-chain data."
applies_to: [crypto]
priority: 6
auto_fire_keywords: [基本面, 值得, 可以買嗎, 鏈上, tvl, on-chain, 鯨魚, whale, 資金費率, funding, 代幣經濟, tokenomics, 解鎖, unlock, 整體評估, 完整分析]
recommended_tools: ['get_crypto_price', 'get_token_supply', 'get_token_unlocks', 'aggregate_news', 'get_cmc_quote_tool']
---

# Crypto Fundamental Analysis

## When to Use
When the user wants to understand a cryptocurrency's intrinsic value and market structure:
- "Is BTC worth investing in?"
- "How do ETH's on-chain metrics look?"
- "What are this coin's fundamentals?"
- "Are whales buying or selling?"
- "What does the funding rate indicate?"

## Method

### Step 1: Market structure data
Use tools to fetch:
- `get_crypto_market_cap` — market cap ranking, total market cap
- `get_futures_data` — funding rate, open interest (market sentiment indicators)
- `get_token_supply` — circulating supply, max supply (inflation pressure)

### Step 2: On-chain activity
- `get_defillama_tvl` — TVL locked (ecosystem health)
- `get_dex_volume` — DEX trading volume (user activity)
- `get_whale_alerts` — large whale transfers (smart-money flow)

### Step 3: Market sentiment
- `get_fear_and_greed_index` — fear & greed index
- `get_trending_tokens` — trending searches (capital flow)

### Step 4: Token events
- `get_token_unlocks` — unlock schedule (sell-pressure warning)
- News events — `google_news` / `aggregate_news`

### Step 5: Comprehensive assessment
Score each of the following dimensions (1–5 points each):
| Dimension | Data source |
|------|----------|
| Market position | Market cap ranking |
| Ecosystem health | TVL trend |
| Capital flow | Funding rate + DEX volume |
| Holder structure | Whale activity |
| Supply pressure | Unlock schedule + inflation rate |
| Market sentiment | Fear & greed index |

## Output Format
```
## <Coin> Fundamental Analysis

### Per-Dimension Scores
Table columns: dimension (team / technology / tokenomics / community / real adoption), score, basis
— Scores are integers from 0 to 6; total out of 30.

### Overall Score
Present as "score achieved / 30", with a one-sentence rationale for the score.

⚠️ The above is fundamental analysis and does not constitute investment advice.

(All figures must be filled with the actual results returned by tools; for fields that cannot be retrieved, state "unavailable" directly — do not leave placeholder text and do not estimate on your own.)
```

## Conventions
- TVL and DEX volume should describe the trend (rising/falling), not just report the number
- Whale transfers should distinguish "transferred into an exchange" (likely selling) vs "transferred out of an exchange" (likely buying)
- A positive funding rate = longs pay shorts (bullish sentiment); negative = the reverse
- Unlock events must flag the date and the amount
