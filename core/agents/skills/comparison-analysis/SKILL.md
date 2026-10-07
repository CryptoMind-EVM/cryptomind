---
name: comparison-analysis
description: "Multi-asset comparison analysis — data collection method and table output format for when the user wants to compare two or more assets (coins, stocks, commodities). Use when the user asks 'A vs B, which is better', 'compare A and B', or 'A vs B'."
applies_to: [chat, crypto, us_stock, tw_stock, global_stock, commodity, forex]
priority: 5
auto_fire_keywords: [比較, 對比, " vs ", "vs.", versus, 差異, 哪個好, 哪個比較, 哪一個好, 比一比, compare, 差別]
recommended_tools: ['get_crypto_price', 'get_us_stock_price', 'get_tw_stock_price', 'technical_analysis', 'aggregate_news']
---

# Multi-Asset Comparison Analysis

## When to Use
When the user wants to choose between or understand the differences among two or more assets:
- "BTC vs ETH — which is better?"
- "Compare TSMC and MediaTek"
- "SOL vs AVAX"
- "Gold or Bitcoin as an inflation hedge?"

## Method

### Step 1: Fetch same-dimension data for each asset
For **every** asset, call the same combination of tools to ensure a consistent comparison baseline:
- Real-time price (the price tool for each market)
- Technicals (`technical_analysis`: trend, RSI)
- Fundamentals / market structure (market cap, supply, TVL, etc., by asset type)
- Recent news (`aggregate_news` / `google_news`)

### Step 2: Only compare dimensions backed by tool data
If a dimension has data for only one of the assets → mark that cell "no data";
**never** fabricate the other asset's number from training memory.

### Step 3: Give a contextualized conclusion
Don't just say "each has its pros and cons" — explain **which goal / risk profile suits which asset**.

## Output Format
```
### Asset Comparison
| Item | Asset A | Asset B |
|------|-------|-------|
| Current price | ... | ... |
| 24h / recent performance | ... | ... |
| Technicals | ... | ... |
| Fundamentals highlights | ... | ... |
| Risk level | ... | ... |

### Analysis Conclusion
[Contextual reading: which situation suits A, which suits B — descriptive only]

⚠️ The above is data-driven comparison analysis and does not constitute investment advice.
```

## Conventions
- Every number in the table must come from this tool call; if data is missing, write "no data"
- When the two assets are in different markets (e.g., coin vs TW stock), still use one table, but annotate market differences (trading hours, pricing currency)
- When comparing more than 3 assets, transpose the table (assets as rows, dimensions as columns) to avoid it being too wide
- The conclusion should not give specific buy/sell signals, allocation percentages or position sizes; give fit-for-context readings instead
