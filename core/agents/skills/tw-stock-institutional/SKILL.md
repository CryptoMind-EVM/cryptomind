---
name: tw-stock-institutional
description: "TW stock institutional flow analysis — net buy/sell by the three major institutional investors (foreign / investment trust / dealer), foreign holdings, and monthly revenue analysis. Use when the user asks about TW-stock institutional flow, chip dynamics, or foreign-capital movement."
applies_to: [tw_stock]
priority: 5
auto_fire_keywords: [法人, 籌碼, 外資, 投信, 自營商, 買超, 賣超, 持股, institutional, foreign, 月營收, 營收]
recommended_tools: ['tw_institutional_tool', 'tw_foreign_top20_tool', 'tw_price']
---

# TW Stock Institutional Flow Analysis

## When to Use
When the user asks about a TW stock's institutional activity or chip dynamics:
- "How do foreigners view TSMC?"
- "Did institutions buy or sell 2330 today?"
- "Three-major-institutional dynamics"
- "Foreign holdings percentage"

## Method

### Step 1: Net buy/sell by the three major institutions
Use the `tw_institutional` tool to fetch:
- Foreign net buy/sell (most important)
- Investment-trust net buy/sell
- Dealer net buy/sell
- Three-institution total

Read rules:
- Foreign net buying for 3+ consecutive days → medium-term bullish
- Foreign net selling for 3+ consecutive days → medium-term bearish
- Investment-trust net buying → bullish on the mid-to-long term
- Dealers → short-term trading reference

### Step 2: Foreign holdings
Use `tw_foreign_top20` or a fundamentals tool:
- Foreign holdings percentage
- Holdings-change trend (MoM increase/decrease)
- Upper-limit percentage (QE quota)

### Step 3: Monthly revenue
Use `tw_monthly_revenue`:
- Current-month revenue
- MoM growth
- YoY growth
- Cumulative YoY growth

Read:
- YoY > 10% → strong growth momentum
- MoM turning from negative to positive → inflection signal
- Stable cumulative YoY → healthy fundamentals

### Step 4: Valuation indicators
Use `tw_pe_ratio`:
- P/E
- Dividend yield
- P/B

## Output Format
```
## <Stock name and code> Institutional Flow Analysis

### Three-Major-Institution Net Buy/Sell
Table columns: institution (foreign / investment trust / dealer / total), net buy/sell (shares), trend
— Shares should be filled with the actual number returned by the tool, converted to an intuitive unit
— The trend column states the actual number of consecutive net-buy or net-sell days

### Foreign Holdings
List the actual holdings percentage and state whether rising or falling

### Monthly Revenue
List the actual values for current-month revenue, MoM, and YoY

### Valuation
List the actual values for P/E and dividend yield

**Chip assessment**: one of "bullish", "neutral", "bearish"

⚠️ The above is chip-data analysis and does not constitute investment advice.

(All figures must be filled with the actual results returned by tools; for fields that cannot be retrieved, state "unavailable" directly — do not leave placeholder text and do not estimate on your own.)
```

## Conventions
- Foreign data is the most important; place it first
- Net buy/sell should describe continuity ("3 consecutive days of net buying" is more meaningful than a single-day number)
- Monthly revenue YoY is the core metric; highlight it
- Share counts should be converted to an intuitive unit ("10k shares" rather than "10000 shares")
