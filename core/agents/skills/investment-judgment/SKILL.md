---
name: investment-judgment
description: "Structured investment judgment — when the user asks whether a specific asset is worth buying or whether it's a good time to enter, force a 'bull → bear → synthesis' structure (adapted from FinRobot Financial CoT). Ensures a judgment has both bull and bear evidence; no hasty answer before research is done."
applies_to: [chat, crypto, us_stock, tw_stock, global_stock, commodity, forex]
priority: 7
eager_load: true  # 中等模型不會自己結構化判斷，命中時直接注入完整 body 強制走流程
auto_fire_keywords: [適合買嗎, 值不值得, 值得買嗎, 該不該買, 該進場嗎, 該買嗎, 現在買, 可以買嗎, 要不要買, 值得投資嗎, 值得進場嗎, 現在適合, 適合投資, 該投資嗎, 該賣嗎, 該出場嗎, should I buy, worth buying, worth it, good time to buy, good time to enter, buy or wait, hold or sell]
recommended_tools: ['get_crypto_price', 'get_us_stock_price', 'get_tw_stock_price', 'technical_analysis', 'aggregate_news', 'get_fear_and_greed_index', 'get_cmc_quote_tool']
---

# Structured Investment Judgment (adapted from FinRobot Financial Chain-of-Thought)

## When to Use
When the user raises a buy/sell judgment question about a **specific asset** (a named coin/stock/commodity):
- "Is TON a good buy now?"
- "Is NVDA worth investing in?"
- "Is it time to enter TSMC?"
- "Should I sell BTC?"
- "Should I buy ETH now?"

**Not applicable** (use another skill or answer directly):
- "Are TW stocks worth buying?" (no named asset → use scope_clarification to clarify)
- "Compare A and B" (→ comparison-analysis skill)
- "Is market risk high?" (overall market → market-risk-assessment skill)

## Platform Boundary (analysis only)

This skill delivers **analysis only**. Answer "should I buy / sell / enter" by laying out the bull and bear evidence, the risks, and the scenarios that would change the picture, then leave the decision to the user. Never output a personal entry or exit point, stop-loss or take-profit level, position size, or an instruction to buy / sell / add / reduce.

If the user asks you to design or grade a trading rule (stop-loss, take-profit, entry / exit, sizing), say once that the platform provides analysis only, then give the data that helps them judge it themselves: recent volatility, historical drawdown distribution, key support / resistance levels (as data, not as stop or target points), and scenarios. Be helpful, not a flat refusal.

## Structured Judgment Flow (must complete in full, do not skip)

Faced with a judgment question like "is it a good buy / worth it", you **must** answer with the following structure.
Do not just conclude after pulling data — complete the bull/bear analysis before synthesizing.

**Key (adapted from FinRobot CoT + CFI stepwise rationalization)**:
Every conclusion must be backed by preceding data — never jump to a conclusion without showing the reasoning.
"The core of CoT is ensuring each step of logic is rationalized before reaching the conclusion" — this naturally reduces hallucination.

### Step 1: Gather current situation (query at least 2 aspects in parallel)
Choose available tools by asset type (adapted from TradingAgents explicitly listing resource aspects):
- **Technicals**: indicators (RSI/MACD/moving averages), support/resistance, volume
- **Fundamentals**: financials, EPS, revenue (stocks) / TVL, funding rate, tokenomics (crypto)
- **Market sentiment**: fear & greed index, VIX, capital flow
- **Recent news**: major events, policy, partnerships

**Parallel querying (adapted from TradingAgents analysts in parallel)**:
Within the **same round**, call multiple tools at once to query different aspects — do not query serially (waiting for one before issuing the next).
For example, for "is TON a good buy", issue at once:
- `get_crypto_price(TON)` + `get_fear_and_greed_index()` + `aggregate_news(TON)`
Let the tools run in parallel, speeding up the response and simulating multiple analysts gathering in parallel.

When gathering, **show the data source**: "Per [tool name], [data]" — so the user can verify.

### Step 2: [Bull factors] (list 2–4, each with data evidence)
From Step 1's data, identify factors supporting buying / bullishness. Each factor must carry specific evidence:
```
[Bull factors]
1. RSI 32 (oversold zone); historically, bounces often follow oversold readings
2. Fear & greed index at 25 (extreme fear); as a contrarian indicator, hints the bottom is near
3. Recent institutional-buying news (source: aggregate_news)
```

### Step 3: Bear factors / risks (list 2–4, each with data evidence)
From Step 1's data, identify factors against buying / bearishness. **Do not skip this step** —
presenting only bull factors is dangerous. Even if bullish, list the risks:
```
Bear factors / risks
1. Broke below a prior-low support level — technical breakdown
2. Funding rate is negative; short sentiment is heavy
3. Recent regulatory-bearish news
```

### Step 4: [Synthesis]
Weigh bulls vs bears and give a directional judgment:
```
[Synthesis]
Direction: bearish / neutral (short term), but could turn bullish mid-to-long term if it reclaims the broken support level
Rationale: although RSI oversold + extreme fear index (bullish), the break of key support +
           bearish funding rate (bears are stronger). The picture stays mixed until the broken level is reclaimed.
Key observation points (scenarios, not instructions):
- If it reclaims and holds the broken level → the bullish case strengthens
- If it breaks the next support → the bearish case strengthens
```

## When Tools Return No Data (key fallback — do not abandon the structure)

When data for an aspect cannot be retrieved (e.g., the technicals tool does not support the asset):
1. **State it**: "Currently unable to retrieve the requested technical indicator data"
2. **Compensate with other aspects**: no technicals → use sentiment + fundamentals + news
3. **Still complete bull and bear**: use the data you can retrieve to list bulls and bears; do not skip just because data is missing
4. **Give alternative observation points**: "Technicals unavailable, but changes in the sentiment index are another gauge of the market backdrop"

❌ **Don't**: "Cannot retrieve technical indicators" → end directly, giving no judgment at all.
✅ **Do**: when data is missing, compensate with other aspects and still deliver a structured judgment.

## Output Format (follow this template so the user sees both sides at a glance)

```
## [Asset] Investment Judgment

📊 Current: a one-line summary of the current price and data state

🟢 Bull factors:
1. ...
2. ...

🔴 Bear factors / risks:
1. ...
2. ...

⚖️ Synthesis: [direction] — [one-line rationale]
Key observation points: state the price levels or conditions that would flip the view bullish or bearish

⚠️ The above is analysis based on current data and does not constitute investment advice.
```

## Multi-Market Differences

- **Crypto**: emphasize on-chain data (TVL / funding rate / whales) + 24h sentiment
- **US stocks**: emphasize earnings + technicals + institutional holdings
- **TW stocks**: emphasize institutional net buy/sell + technicals + margin
- **Commodities / Forex**: emphasize macro + supply-demand + USD trend
