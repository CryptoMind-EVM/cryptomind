---
name: tw-stock-fundamentals
description: "TW stock fundamental analysis — P/E, dividend yield, P/B, dividend policy, major announcements, and valuation assessment. Use when the user asks about TW-stock fundamentals, whether it's worth buying, dividends, or P/E."
applies_to: [tw_stock]
priority: 6
auto_fire_keywords: [基本面, 本益比, p/e, 殖利率, 股利, 淨值, p/b, 估值, 體質, 值得, 可以買嗎, 重大訊息, dividend, valuation]
recommended_tools: ['tw_fundamentals_tool', 'tw_pe_ratio_tool', 'tw_monthly_revenue_tool', 'tw_dividend_tool']
---

# TW Stock Fundamental Analysis

## When to Use
When the user asks about a TW stock's fundamentals or long-term investment value:
- "How are 2330's fundamentals?"
- "Is TSMC worth holding long-term?"
- "What's this stock's dividend?"
- "Is the P/E reasonable?"

## Method

### Step 1: The valuation trio
Use the `tw_pe_ratio` tool to fetch:
- **P/E**: judge whether it's expensive
  - P/E < 12: relatively cheap
  - P/E 12–20: reasonable range
  - P/E > 25: expensive (unless high growth)
- **Dividend yield**: cash return rate
  - > 5%: high yield
  - 3–5%: above average
  - < 2%: low
- **P/B**: market price vs book value
  - P/B < 1: discount
  - P/B 1–2: reasonable
  - P/B > 3: premium

### Step 2: Profitability
Use `tw_fundamentals`:
- Gross margin (trend matters more than absolute level)
- ROE (return on equity)
- EPS (earnings per share)

### Step 3: Dividend policy
Use `tw_dividend`:
- Cash dividend
- Stock dividend
- Ex-dividend date
- Multi-year payout stability

### Step 4: Major announcements
Use `tw_major_news`:
- Investor-day content
- Product / order changes
- Management changes
- Accounting changes

### Step 5: Monthly revenue trend
Use `tw_monthly_revenue`:
- Recent 3-month YoY trend
- Cumulative YoY growth

## Output Format
```
## <Stock name and code> Fundamental Analysis

### Valuation
Table columns: indicator (P/E / dividend yield / P/B), value, read
— The P/E read uses one of "cheap", "reasonable", "expensive"
— The P/B read uses one of "discount", "reasonable", "premium"

### Profitability
List the actual values of gross margin, ROE, EPS; for gross margin, include the rising or falling trend

### Dividend
List the actual values of cash dividend, stock dividend, and yield
Payout stability uses one of "stable", "unstable"

### Revenue Momentum
List the actual YoY values for the recent three months
The trend uses one of "growth", "flat", "decline"

**Fundamental assessment**: one of "excellent", "good", "fair", "weak"

⚠️ The above is fundamental analysis and does not constitute investment advice.

(All figures must be filled with the actual results returned by tools; for fields that cannot be retrieved, state "unavailable" directly — do not leave placeholder text and do not estimate on your own.)
```

## Conventions
- The three valuation indicators must always be read together (P/E + Yield + P/B); never report just one
- Gross margin should describe the trend (rising/falling for several quarters)
- Dividend yield is what TW-stock investors care about most; highlight it
- Major announcements should list only the recent 2–3; too many dilutes focus
