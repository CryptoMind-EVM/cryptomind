---
name: us-stock-earnings
description: "US stock earnings analysis — EPS/revenue/guidance, institutional holdings, and insider-transaction interpretation. Use when the user asks about earnings, EPS, or institutional dynamics."
applies_to: [us_stock]
priority: 5
auto_fire_keywords: [財報, earnings, eps, 營收, revenue, 指引, guidance, 機構, institutional, 內部人, insider, 法人, beat, miss, 超預期, 不如預期]
recommended_tools: ['us_earnings', 'us_fundamentals', 'us_insider_transactions', 'us_institutional_holders']
---

# US Stock Earnings Analysis

## When to Use
When the user asks about a US stock's financial performance or institutional dynamics:
- "How were AAPL's earnings?"
- "TSLA's EPS last quarter?"
- "How do institutions view NVDA?"
- "Are insiders selling?"

## Method

### Step 1: Core earnings data
Use the `us_fundamentals` tool to fetch:
- EPS (earnings per share) vs market consensus (Beat / Miss / In-line)
- Revenue vs consensus
- Net margin, gross margin
- P/E

### Step 2: Growth indicators
- `revenueGrowth` — revenue YoY
- `earningsGrowth` — earnings YoY
- Read: >20% high growth, 10–20% solid, <10% mature

### Step 3: Institutional activity
- `us_institutional_holders` — institutional holdings changes
- `us_insider_transactions` — insider buys/sells
- Key point: institutional adding = bullish; insider selling = a warning sign

### Step 4: Analyst ratings
- `targetPrice` — consensus target price
- `analystCount` — number of covering institutions
- Rating distribution (strong buy / buy / hold / sell)

### Step 5: Earnings calendar
- `us_earnings` — upcoming earnings release dates
- If a release is imminent, remind the user

## Output Format
```
## <Ticker> Earnings Analysis

### This Quarter's Results
- EPS: actual value and market consensus; state whether it beat or missed
- Revenue: actual value and market consensus; state whether it beat or missed
- YoY growth: actual YoY for revenue and earnings

### Growth Assessment
Use one of "high growth", "solid", "mature", with a one-sentence read

### Outlook
List the key points of the company's guidance

⚠️ The above is earnings-data analysis and does not constitute investment advice.

(All figures must be filled with the actual results returned by tools; for fields that cannot be retrieved, state "unavailable" directly — do not leave placeholder text and do not estimate on your own.)
```

## Conventions
- EPS/revenue must always be compared to consensus (Beat/Miss); don't just report numbers
- Institutional holdings changes should describe the direction (adding/reducing), not just the holdings number
- "Heavy insider selling" should be flagged as a risk signal
- If earnings are imminent (<2 weeks), proactively remind
