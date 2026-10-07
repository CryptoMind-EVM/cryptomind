---
name: commodity-analysis
description: "Commodity analysis — comprehensive assessment of supply-demand structure, inventory data, seasonal patterns, and USD correlation for gold / oil / silver. Use when the user asks about commodities, gold, oil, silver, or raw materials."
applies_to: [commodity]
priority: 5
auto_fire_keywords: [黃金, gold, 原油, 石油, crude, oil, 白銀, silver, 原物料, commodity, 大宗商品, 銅, copper, 天然氣, 庫存, inventory, opec, 供需]
recommended_tools: ['get_commodity_price_tool', 'get_gold_silver_ratio_tool', 'get_oil_analysis_tool', 'get_all_commodities_prices_tool']
---

# Commodity Analysis

## When to Use
When the user asks commodity-related questions:
- "Will gold keep rising?"
- "Oil price trend"
- "Is silver worth investing in?"
- "What's the read on copper?"
- "Natural gas supply and demand"

## Method

### Step 1: Identify the commodity category
Map the user query to a specific commodity:
- Precious metals: gold (XAU), silver (XAG), platinum, palladium
- Energy: crude oil (WTI/Brent), natural gas
- Industrial metals: copper, aluminum, zinc
- Agriculture: corn, soybeans, wheat

### Step 2: Supply-demand fundamentals
- **Supply side**: OPEC+ production cut/increase decisions, output of major producers, inventory data (EIA/API)
- **Demand side**: global growth expectations, industrial demand (copper), safe-haven demand (gold)
- **Inventory**: compare recent inventory vs the 5-year average to judge oversupply or shortage

### Step 3: USD correlation
Commodities are priced in USD; analyze the dollar index (DXY):
- Stronger DXY → downward pressure on commodity prices
- Weaker DXY → support for commodity prices
- Exception: supply shocks can break the negative correlation

### Step 4: Seasonal patterns
Identify the commodity's seasonal patterns:
- Gold: Indian wedding season (Oct–Dec) and Chinese New Year typically lift demand
- Crude oil: Northern hemisphere winter heating demand, summer driving season
- Natural gas: highest volatility during the winter heating peak

### Step 5: Technical analysis (supplementary)
Combine price action to judge:
- Key support/resistance levels
- 200-day moving average (a long-term trendline institutions watch)
- Breakout or breakdown of prior highs/lows

## Output Format
```
## <Commodity> Analysis

### Price and Supply-Demand
Table columns: aspect (supply / demand / inventory / seasonality), current situation, direction of impact on price
— The supply column should list actual production output or cut decisions; the demand column should list actual data.

**Supply-demand call**: one of "oversupplied", "balanced", or "short", with a one-sentence basis.
**Seasonality**: state whether currently in peak or off-season, with basis.
**Overall rating**: one of "bullish", "neutral", or "bearish".

⚠️ The above is data analysis and does not constitute investment advice.

(All figures must be filled with the actual results returned by tools; for fields that cannot be retrieved, state "unavailable" directly — do not leave placeholder text and do not estimate on your own.)
```

## Conventions
- Crude oil must distinguish WTI from Brent
- Gold analysis must mention the real yield (TIPS) direction
- Inventory data must cite its source (EIA Wednesday / API Tuesday)
- A disclaimer is required
