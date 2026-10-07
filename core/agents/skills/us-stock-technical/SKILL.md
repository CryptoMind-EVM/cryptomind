---
name: us-stock-technical
description: "US stock technical analysis — RSI/MACD/Bollinger Bands/moving averages, 52-week highs/lows, and technical-signal interpretation. Use when the user asks about US-stock technicals, price action, or charts."
applies_to: [us_stock]
priority: 6
auto_fire_keywords: [技術, 走勢, 圖表, chart, rsi, macd, bollinger, 布林, 均線, moving average, 突破, breakout, 支撐, 壓力, 52週]
recommended_tools: ['us_stock_price', 'us_technical_analysis', 'us_stock_snapshot_tool']
---

# US Stock Technical Analysis

## When to Use
When the user asks about a US stock's technicals or price action:
- "How do AAPL's technicals look?"
- "TSLA chart analysis"
- "NVDA support and resistance"
- "Has this stock broken out?"

## Method

### Step 1: Technical indicators
Use the `us_technical_analysis` tool to fetch:
- RSI (14)
- MACD
- Bollinger Bands (BB20): upper / middle / lower band
- Moving averages (MA 20/50/200)

### Step 2: Price position
- 52-week high/low position (`week52Range`)
- Bollinger position: breaking the upper band (overbought) / breaking the lower band (oversold) / within the bands (neutral)
- Relative position to the moving averages (above/below)

### Step 3: Volume analysis
- Today's volume vs 20-day average volume
- Expanding volume = trend confirmation; contracting volume = weakening trend

### Step 4: Combined signals
| Signal | Read |
|------|------|
| RSI > 70 + breaking upper band | Overbought; pullback risk |
| RSI < 30 + breaking lower band | Oversold; bounce opportunity |
| MACD golden cross + bullish MA alignment | Bullish signal |
| MACD death cross + bearish MA alignment | Bearish signal |
| Breakout with volume | Valid breakout |
| Breakout on weak volume | Possibly a false breakout |

## Output Format
```
## <Ticker> Technical Analysis

**Trend**: one of "bullish", "bearish", "consolidating"

### Indicator Readings
Table columns: indicator (RSI 14 / MACD / moving averages), value, read

### Price Range and Volume
- 52-week range: list the actual high/low and state the current position
- Volume: compare today's volume to the 20-day average; state expansion or contraction

**Overall score**: one of "bullish", "neutral", "bearish"

⚠️ The above is technical analysis and does not constitute investment advice.

(All figures must be filled with the actual results returned by tools; for fields that cannot be retrieved, state "unavailable" directly — do not leave placeholder text and do not estimate on your own.)
```

## Conventions
- The Bollinger position should be specific (near the upper band / near the middle band / near the lower band)
- Volume comparison should use a multiple of "today vs 20-day average"
- Do not give buy/sell advice; use "bullish/bearish"
- The 52-week position should be described as a percentage (e.g., "near 95% of the 52-week high")
