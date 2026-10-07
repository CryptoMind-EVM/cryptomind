---
name: forex-macro-analysis
description: "Forex macro analysis — comprehensive assessment of interest-rate differentials, central bank policy, safe-haven flows, and technical structure for major currency pairs (USD/EUR/JPY/GBP/CNY). Use when the user asks about exchange rates, forex, currency pairs, or the dollar's trend."
applies_to: [forex]
priority: 5
auto_fire_keywords: [匯率, 外匯, forex, 美元, dollar, dxy, 歐元, eur, 日圓, jpy, yen, 英鎊, gbp, 人民幣, cny, cnh, 利差, 央行, 利率, 避險, 貨幣對, carry trade]
recommended_tools: ['get_forex_rate_tool', 'get_all_forex_rates_tool', 'get_usd_twd_rate_tool', 'get_central_bank_rates_tool']
---

# Forex Macro Analysis

## When to Use
When the user asks forex- or currency-related questions:
- "Will the dollar stay strong?"
- "USD/JPY trend"
- "What's the read on the euro?"
- "RMB exchange rate"
- "Carry trade"

## Method

### Step 1: Identify the currency pair
Map the user query to a specific currency pair:
- Major pairs: EUR/USD, USD/JPY, GBP/USD, USD/CHF
- Commodity currencies: AUD/USD, USD/CAD, NZD/USD
- Emerging markets: USD/CNH (offshore RMB), USD/TWD

### Step 2: Interest-rate differential analysis (core driver)
- Compare the two central banks' policy rates (Fed, ECB, BOJ, BOE, PBOC)
- **Widening differential**: the higher-yielding currency tends to appreciate
- **Narrowing differential**: depreciation pressure on the low-yield currency eases
- Market expectations of the future rate path (FedWatch, OIS)

### Step 3: Central bank policy direction
- **Hawkish** (leaning toward hikes / balance-sheet taper) → currency strengthens
- **Dovish** (leaning toward cuts / balance-sheet expansion) → currency weakens
- Watch recent central bank meeting minutes and official speeches

### Step 4: Safe-haven flows
- Risk-on: capital flows to high-yield / commodity currencies (AUD, CAD)
- Risk-off: capital flows to safe-haven currencies (USD, JPY, CHF)
- VIX index as a risk-sentiment reference

### Step 5: Technicals
- DXY (dollar index) trend direction
- Key support/resistance levels for the currency pair
- 200-day moving average to judge the long-term trend

## Output Format
```
## <Currency Pair> Macro Analysis

**Rate differential**: list both countries' policy rates and the direction of the differential

### Driver Summary
Table columns: aspect (rate differential / central bank stance / safe-haven sentiment / technicals), current situation, impact on the exchange rate
— Central bank stance uses one of "hawkish", "dovish", "neutral"
— The impact column uses one of "bullish", "bearish", "neutral"

**Overall score**: one of "bullish", "neutral", "bearish"

⚠️ The above is macro analysis and does not constitute investment advice.

(All figures must be filled with the actual results returned by tools; for fields that cannot be retrieved, state "unavailable" directly — do not leave placeholder text and do not estimate on your own.)
```

## Conventions
- JPY analysis must mention BOJ intervention risk (150/160 levels)
- RMB must distinguish onshore (CNY) from offshore (CNH)
- A disclaimer is required
