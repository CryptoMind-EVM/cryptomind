---
name: tw-stock-technical
description: "TW stock technical analysis — candlestick patterns, moving-average alignment, KD/RSI/MACD indicators, volume-price relationship, and a comprehensive read of margin trading. Use when the user asks about TW-stock technicals, price action, or key support and resistance levels."
applies_to: [tw_stock]
priority: 5
auto_fire_keywords: [台股技術, 技術面, 走勢, k線, 均線, kd, rsi, macd, 量價, 融資, 融券, 支撐, 壓力, 買點, 賣點, 破底, 破頭, 黃金交叉, 死亡交叉, 帶量, 缺口]
recommended_tools: ['tw_technical', 'tw_price', 'tw_stock_snapshot_tool']
---

# TW Stock Technical Analysis

## When to Use
When the user asks about a TW stock's technicals:
- "How do 2330's technicals look?"
- "Is TSMC's technical picture strong or weak right now?"
- "Does the broad market have support?"
- "Is this stock about to weaken?"
- "How to read the margin data?"

## Method

### Step 1: Get technical data
Use tools to fetch:
- Candlestick patterns (daily / weekly)
- Moving averages: MA5 (weekly), MA20 (monthly), MA60 (quarterly), MA240 (yearly)
- Indicators: KD (9,3,3), RSI (6/12), MACD (12,26,9)
- Volume: volume vs the 5-day / 20-day average volume
- Margin balance changes

### Step 2: Trend read
Judge bull/bear from the moving-average alignment:
- **Bullish alignment**: MA5 > MA20 > MA60 > MA240 → medium-to-long term bullish
- **Bearish alignment**: MA5 < MA20 < MA60 < MA240 → medium-to-long term bearish
- **Tangled**: MAs intertwined → trend-reversal signal
- **Golden cross**: monthly line (MA20) crosses above quarterly line (MA60) → bullish
- **Death cross**: monthly line crosses below quarterly line → bearish

### Step 3: Momentum and overbought/oversold
- KD > 80: overbought, short-term pullback risk
- KD < 20: oversold, short-term bounce potential
- KD high-plateau overbought (sustained > 80): a strong-stock trait; overbought alone is not a weakness signal
- RSI > 70 hot; RSI < 30 cold
- MACD histogram turning from negative to positive: bullish turn; positive to negative: bearish turn

### Step 4: Volume-price relationship
- **Price up + volume up**: healthy bull; aggressive buying
- **Price up + volume down**: weak rally; chase-high risk
- **Price down + volume up**: selling pressure surging; decline accelerates
- **Price down + volume down**: selling pressure abating; may be near a bottom
- **Long green with volume**: strong breakout signal
- **Long black with volume**: breakdown signal

### Step 5: Margin trading (TW-specific)
- **Margin (融資) increasing**: retail bullish (often optimistic but frequently trapped)
- **Margin decreasing**: retail stopping out (chips rotating to institutions)
- **Short (融券) increasing**: retail bearish (short-squeeze possible)
- **Both margin and short decreasing**: market wait-and-see

### Step 6: Key price levels
- Recent highs/lows, round-number levels (e.g., 1000 for 2330)
- Gap positions (breakaway gaps often become support or resistance)
- Prior high-volume zones (chip-dense zones)

## Output Format
```
## <Stock name and code> Technical Analysis

### Indicator Readings
Table columns: indicator (KD 9,3,3 / RSI / MACD / moving averages), value, read
— The KD and RSI reads use one of "overbought", "neutral", "oversold"

### Margin Trading
List the actual values for margin and short, each with its increase or decrease

### Key Levels
List the actual prices for support and resistance levels

**Technical assessment**: one of "bullish", "neutral", "bearish"

⚠️ The above is technical analysis and does not constitute investment advice.

(All figures must be filled with the actual results returned by tools; for fields that cannot be retrieved, state "unavailable" directly — do not leave placeholder text and do not estimate on your own.)
```

## Conventions
- TW stocks are priced in TWD; the daily price limit is ±10% (ETFs ±10%)
- The weekly line = MA5 (TW convention uses 5 days as a week), not the Western WMA
- KD high-plateau overbought is common in strong TW stocks and should not always be treated as overbought
- Present support / resistance and recent highs / lows as data points. Do not turn them into entry, stop-loss or take-profit points; "buy point / sell point" questions get the key levels and the bullish / bearish conditions, not an instruction
- A disclaimer is required
