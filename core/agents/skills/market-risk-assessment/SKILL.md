---
name: market-risk-assessment
description: "Market risk assessment — cross-market risk analysis, fear & greed index, funding rates, VIX, and correlation analysis. Use when the user asks about market risk, the broad market, the fear index, or market sentiment."
applies_to: [crypto, us_stock, tw_stock, economic]
priority: 8
eager_load: true  # description 不足以讓中等模型聯想到擇時問題，命中時直接注入完整 body
auto_fire_keywords: [風險, 大盤, 市場情緒, 恐慌, 貪婪, vix, fear, greed, 賣壓, 崩盤, 泡沫, risk, market sentiment, 整體市場, 宏觀, 會跌, 會漲, 跌到何時, 漲到何時, 現在適合, 適合買嗎, 可以買嗎, 該買嗎, 要不要買, 現在買, 擇時, 進場, 出場, 抄底, 逃頂, 見底, 見頂, 止跌, 跌完, 見底了嗎, 入場]
recommended_tools: ['get_fear_and_greed_index', 'get_crypto_price', 'technical_analysis', 'get_vix_index_tool']
---

# Market Risk Assessment

## When to Use
When the user asks about overall market risk or sentiment:
- "Is broad-market risk high right now?"
- "Is the market panicking?"
- "Is it a good time to enter?"
- "How is crypto-market sentiment?"
- "Is there a bubble?"

## Method

### Step 1: Fear & greed index
- Crypto: `get_fear_and_greed_index`
  - 0–25: extreme fear (contrarian reading: extreme fear has often coincided with rebounds, but it is not a timing signal)
  - 25–45: fear
  - 45–55: neutral
  - 55–75: greed
  - 75–100: extreme greed (caution warranted)
- Traditional finance: VIX data from the Economic Agent

### Step 2: Capital flow
- Crypto: `get_futures_data` — funding rate
  - Extreme positive rate → crowded long (pullback risk)
  - Extreme negative rate → crowded short (short-squeeze potential)
- Crypto: `get_defillama_tvl` — is capital flowing in or out

### Step 3: Market breadth
- Crypto: `get_crypto_market_cap` — total market-cap change
- Crypto: `get_trending_tokens` — which sectors capital is rotating into

### Step 4: Macro backdrop
- Central bank rates: `get_central_bank_rates`
- Economic calendar: `get_economic_calendar` — near-term major events

### Step 5: Comprehensive risk assessment
| Risk level | Condition |
|----------|------|
| 🟢 Low risk | Fear index < 25, neutral funding, stable TVL |
| 🟡 Medium risk | Fear index 25–55, normal volatility |
| 🟠 High risk | Fear index > 75, extreme funding, fast TVL expansion |
| 🔵 Extreme fear | Fear index < 10, possibly a bottom zone |

## Output Format
```
## Market Risk Assessment

### Fear & Greed Index
List the actual index value (out of 100) and the corresponding sentiment tier

### Derivatives & On-Chain
- Funding rate: positive or negative, with the actual value and interpretation
- TVL change: rising or falling, with the actual magnitude

### Market Cap
List total crypto market cap and the magnitude of change

**Risk level**: one of "low", "medium", "high"

⚠️ The above is risk assessment and does not constitute investment advice.

(All figures must be filled with the actual results returned by tools; for fields that cannot be retrieved, state "unavailable" directly — do not leave placeholder text and do not estimate on your own.)
```

## Conventions
- The fear index should state both the number and its "contrarian" meaning (extreme fear has often preceded rebounds, but it is context, not a timing signal)
- Timing questions ("is it a good time to enter?") get sentiment data and scenarios — analysis only; do not issue entry / exit instructions
- Extreme funding-rate values (> 0.1% or < -0.05%) must always be flagged
- Use emoji for the risk level so the user can see it at a glance
- List only macro events in the next 1–2 weeks; anything farther out has little reference value
