---
name: macro-economic-indicators
description: "Macro-economic indicator interpretation — data reading and cross-market impact assessment for CPI/PPI/NFP/GDP/Fed decisions. Use when the user asks about inflation, rate hikes, the Fed, economic data, nonfarm payrolls, or GDP."
applies_to: [economic]
priority: 5
auto_fire_keywords: [通膨, cpi, ppi, 升息, 降息, fed, 聯準會, 利率, 非農, nonfarm, gdp, 經濟成長, 失業率, 薪資, 景氣, 衰退, recession, 量化緊縮, qt, 點陣圖]
recommended_tools: ['get_market_indices_tool', 'get_vix_index_tool', 'get_economic_calendar_tool', 'get_sector_performance_tool']
---

# Macro-Economic Indicator Interpretation

## When to Use
When the user asks macro-economic or policy-related questions:
- "CPI came out — how to read it?"
- "Will the Fed hike?"
- "Nonfarm impact"
- "Will the economy go into recession?"
- "GDP growth rate"

## Method

### Step 1: Identify the indicator category
- **Inflation indicators**: CPI, Core CPI, PCE, PPI
- **Employment indicators**: nonfarm payrolls (NFP), unemployment rate, wage growth
- **Growth indicators**: GDP, PMI, retail sales
- **Policy indicators**: Fed rate decisions, dot plot, FOMC statement

### Step 2: Expectation vs actual
- Get the market consensus
- Compare the actual print to the expectation (the "surprise")
- **Higher-than-expected** inflation → hike expectations rise → USD strengthens, stocks and bonds under pressure
- **Lower-than-expected** inflation → cut expectations rise → risk assets benefit

### Step 3: Cross-market transmission
For each data result, trace three transmission chains:
- **USD**: strong data → USD strengthens; weak data → USD weakens
- **Stocks/bonds**: cooling inflation → both stocks and bonds rise; heating inflation → both fall
- **Crypto**: Risk-on (cut expectations) → BTC bullish; Risk-off (hike expectations) → BTC bearish

### Step 4: Historical comparison
- Compare with the prior print (MoM / YoY direction)
- Compare with the long-term trend (is it deviating from the norm?)
- Use a 3-month moving average to smooth short-term volatility

### Step 5: Policy implications
- How the data affects the Fed's rate path
- Reference FedWatch (rate probabilities implied by the futures market)
- Divergence between the dot plot and market expectations

## Output Format
```
## <Indicator> Interpretation

**Data**: list the actual value, market consensus, and prior print, and state whether it beat or missed

### Cross-Asset Impact
Table columns: asset (USD / US stocks / US bonds / crypto), direction, reason
— The direction column states the actual call; the reason column is one sentence

**Fed policy implication**: one of "hawkish", "dovish", "neutral", with a one-sentence read

⚠️ The above is macro-data interpretation and does not constitute investment advice.

(All figures must be filled with the actual results returned by tools; for fields that cannot be retrieved, state "unavailable" directly — do not leave placeholder text and do not estimate on your own.)
```

## Conventions
- Core CPI (excluding food and energy) is watched more closely by the Fed than headline CPI
- Nonfarm should also look at wage growth (average hourly earnings)
- Volatility is high around data releases; remind about short-term risk
- A disclaimer is required
