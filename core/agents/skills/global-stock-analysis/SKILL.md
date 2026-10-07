---
name: global-stock-analysis
description: "Global stock market analysis — comprehensive assessment of market structure, foreign-capital flow, sector rotation, and valuation comparison for HK / Japan / Korea / India / A-share indices. Use when the user asks about HK stocks, Japan stocks, Korea stocks, Hang Seng, Nikkei, or KOSPI."
applies_to: [global_stock]
priority: 5
auto_fire_keywords: [港股, 恆生, hang seng, hsi, 騰訊, 阿里, 美團, 日股, 日經, nikkei, tokyo, 東證, 韓股, kospi, samsung, LG, 印股, 印度, nifty, sensex, a股, 上證, 滬深, 外資, southbound, northbound]
recommended_tools: ['global_stock_price_tool', 'global_stock_technical_tool', 'global_stock_fundamentals_tool', 'global_stock_news_tool']
---

# Global Stock Market Analysis

## When to Use
When the user asks about Asia-Pacific markets (HK / Japan / Korea / India / A-shares):
- "What's the outlook for HK stocks?"
- "Will the Nikkei keep rising?"
- "Foreign-capital dynamics in the Korean market"
- "Hang Seng Index analysis"
- "India Nifty trend"

## Method

### Step 1: Identify the market
- HK stocks: Hang Seng Index (HSI), Hang Seng Tech Index (HSTECH)
- Japan stocks: Nikkei 225, TOPIX
- Korea stocks: KOSPI, KOSDAQ
- India stocks: NIFTY 50, SENSEX
- A-shares: Shanghai Composite, CSI 300, Shenzhen Component

### Step 2: Foreign-capital flow (core driver)
Asia-Pacific markets are highly dependent on foreign capital:
- **Net foreign buying** → market strengthens
- **Net foreign selling** → market under pressure
- HK stocks: Southbound flows (Stock Connect), foreign big-bank ratings
- Japan stocks: overseas investors account for 70%+; JPY trend has a direct impact
- Korea stocks: foreigners hold 30%+; foreign flow is a short-term bellwether
- A-shares: Northbound flows (Shanghai/Shenzhen Connect) are a foreign-capital indicator

### Step 3: Sector structure and rotation
Each market has dominant sectors:
- HK stocks: financials + tech (Tencent / Alibaba / Meituan) + property
- Japan stocks: export manufacturing (Toyota / Sony) + semiconductor equipment
- Korea stocks: semiconductors (Samsung / SK Hynix) + batteries + chemicals
- India stocks: IT services + financials + consumer
- A-shares: new energy + semiconductors + consumer

Judgment: are the dominant sectors in a favorable cycle?

### Step 4: Valuation comparison
- Compare the market index's P/E vs its historical average
- The valuation premium/discount to US stocks (S&P 500)
- The appeal of high-yield markets (India / Indonesia)

### Step 5: FX and policy
- Japan stocks: weak JPY → strong export-corp earnings → stock support
- Korea stocks: KRW trend affects foreigners' willingness to inflow
- A-shares: RMB policy + regulatory policy (property / tech regulation)

## Output Format
```
## <Market or Stock> Analysis

**Foreign-capital flow**: state net buying or net selling, with the actual amount

### Driver Summary
Table columns: aspect (foreign capital / dominant sector / valuation / FX), current situation, direction of impact
— The valuation column lists the P/E and its comparison with the historical range

**Overall score**: one of "bullish", "neutral", "bearish"

⚠️ The above is market analysis and does not constitute investment advice.

(All figures must be filled with the actual results returned by tools; for fields that cannot be retrieved, state "unavailable" directly — do not leave placeholder text and do not estimate on your own.)
```

## Conventions
- HK stocks must mention Southbound flows and ADR regulatory risk
- Japan stocks must mention JPY exchange rate and BOJ policy
- A-shares should watch regulatory policy (property / tech / capital controls)
- A disclaimer is required
