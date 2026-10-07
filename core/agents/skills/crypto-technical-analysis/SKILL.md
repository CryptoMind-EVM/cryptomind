---
name: crypto-technical-analysis
description: "Crypto technical analysis — comprehensive read of RSI/MACD/KD/moving averages, support and resistance levels, and trend direction. Use when the user asks about technicals, price action, overbought/oversold, or support/resistance."
applies_to: [crypto]
priority: 5
auto_fire_keywords: [技術分析, 走勢, 超買, 超賣, rsi, macd, kd, 均線, 支撐, 壓力, 指標, trend, overbought, oversold, 交叉, 金叉, 死叉]
recommended_tools: ['technical_analysis', 'get_crypto_price', 'get_futures_data']
---

# Crypto Technical Analysis

## When to Use
When the user asks about a cryptocurrency's technicals, including but not limited to:
- "How do BTC's technicals look?"
- "ETH price action analysis"
- "Is it overbought now?"
- "What's the RSI?"
- "Where are support and resistance?"
- "What's the short-term view?"

## Method

### Step 1: Pull technical indicators
Use the `technical_analysis` tool to fetch:
- RSI (14)
- MACD (12, 26, 9)
- Moving averages (MA 20, 60, 120)

### Step 2: Trend read
Judge the trend from the moving-average alignment:
- **Bullish alignment**: MA20 > MA60 > MA120 → bullish
- **Bearish alignment**: MA20 < MA60 < MA120 → bearish
- **Tangled/crossing**: three lines tangled → consolidation / reversal signal

### Step 3: Momentum analysis
- RSI > 70: overbought zone (high pullback risk)
- RSI < 30: oversold zone (bounce opportunity)
- RSI 30–70: neutral zone
- MACD golden cross (fast line crosses above slow line): bullish signal
- MACD death cross (fast line crosses below slow line): bearish signal

### Step 4: Key price levels
Identify recent highs/lows as support/resistance references; round-number levels (e.g., BTC $100K) should also be considered.

### Step 5: Overall score
Combine the above indicators into a score: strong / neutral-bullish / neutral / neutral-bearish / weak.

## Output Format
```
## <Coin> Technical Analysis

### Indicator Readings
Table columns: indicator (RSI 14 / MACD / moving averages), value, read
— The read column uses one of "overbought", "neutral", "oversold".

### Key Levels
List the actual prices for support and resistance levels.

**Overall score**: one of "strong", "neutral-bullish", "neutral", "neutral-bearish", "weak".

⚠️ The above is technical analysis and does not constitute investment advice.

(All figures must be filled with the actual results returned by tools; for fields that cannot be retrieved, state "unavailable" directly — do not leave placeholder text and do not estimate on your own.)
```

## Conventions
- Use "bullish / bearish / neutral" rather than "buy / sell"
- A disclaimer is required
- If data is insufficient (tool did not return), state explicitly "data temporarily unavailable" rather than guessing
- The default timeframe is the daily chart, unless the user specifies otherwise
