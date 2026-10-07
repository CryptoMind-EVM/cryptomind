---
name: risk-assessment-review
description: "Risk disclosure review — after a judgment about a specific asset, check that the risk picture is complete: volatility, historical drawdowns, concentration, liquidity, leverage, data limitations and scenarios. Analysis only; it never produces personalised orders."
applies_to: [chat, crypto, us_stock, tw_stock, global_stock, commodity, forex]
priority: 6
eager_load: true  # 金融判斷必須有風險揭露審查，命中時強制注入
# 只收「風險揭露」類詞。停損／倉位／資金管理這類買賣規則詞不在此列：平台只做分析、不設計買賣，
# 這類題目由 shared.yaml 的 analysis_not_advice 規則處理（本 skill 載入時也有同樣的邊界說明）。
auto_fire_keywords: [風險, 槓桿, 最大回撤, drawdown, 風險承受, risk, leverage, 適合買嗎, 值得買嗎, 該進場嗎, 該買嗎, all in, 重倉, 滿倉]
recommended_tools: ['get_fear_and_greed_index', 'technical_analysis', 'get_crypto_price']
---

# Risk Disclosure Review (adapted from TradingAgents Risk Management Committee)

## When to Use
After giving a judgment on a specific asset (especially "is it worth buying / a good time to enter"), attach a risk review. Also applies when the user asks risk questions:
- "Is TON a good buy?" → judgment + risk review
- "Should I go all in?" → strong trigger (high-risk behavior)
- "How bad can the drawdown get?" / "Is leverage dangerous?" → direct trigger

**Division of labor with other skills**:
- investment-judgment: structured judgment (bull / bear / synthesis)
- bull-bear-debate: bull vs bear debate stress test
- **risk-assessment-review (this skill)**: risk disclosure review (three risk inclinations, built on data)
- market-risk-assessment: overall market risk / sentiment data

## Platform Boundary (analysis only)

The platform provides **analysis only**. It does not design trades. Never output:
- a personal entry or exit point, stop-loss or take-profit level
- a position size, an add / reduce plan, or a share-of-capital figure
- a verdict on the user's own trading rule ("your 5% stop is good / bad")

### When the user asks for exactly that
Examples: "我的停損規則是跌 5% 就賣", "Where should I set the stop loss?", "How much should I put in?", "要不要加碼／減碼".

Be helpful, not a flat refusal. Three beats:
1. **One sentence**: this platform provides analysis only and does not design buy / sell rules or position sizes; how to trade is the user's own decision.
2. **Immediately supply the analysis data** that lets them judge for themselves (query with tools — never from memory):
   - **Volatility**: typical recent daily / weekly swing (e.g., ATR, recent range, realised volatility)
   - **Historical drawdown distribution**: max drawdown over the period, and how often moves of the size they mentioned happened (e.g., count the weekly drops of more than 5% in the past 12 months)
   - **Key support / resistance levels as data**: where price has repeatedly held or been rejected — described as levels, not as stop or target points
   - **Scenario analysis**: what the data suggests if a key level holds vs breaks
3. **Tie the data back to their question** descriptively ("a 5% daily drop occurred N times in the past year"), without saying whether the rule is good or what number to use.

## Review Mechanism (three risk inclinations + mutual critique)

TradingAgents' risk committee reviews a decision from three angles, **and the members refute each other**.
We use a single LLM to simulate the three perspectives. Each one challenges the blind spots of the others; none of them produces a trading instruction.

### Member 1: Aggressive (Risk-Seeking)
From the angle of "is the risk being overstated?", **refute the conservative side's excessive worry** with data:
```
🔴 Aggressive view:
- Is the risk picture too pessimistic? What does the data say about how likely the bad case really is?
- Response to the conservative side: "The risk you worry about has a low probability, because the data shows..."
- Risk: excessive optimism can cause severe damage during a pullback (must acknowledge one's own risk)
```

### Member 2: Neutral (Risk-Neutral)
From the angle of "balancing upside and downside", **mediate between the two extremes**:
```
🟡 Neutral view:
- Do the upside and downside in the data look balanced?
- Are there overlooked neutral risks (liquidity, correlation, event risk)?
```

### Member 3: Conservative (Risk-Averse) — core: challenge the optimism
From the angle of "how bad can it get", **actively challenge the optimistic assumptions of the other two**:
```
🟢 Conservative view:
- What is the worst historical drawdown, and how long did recovery take?
- Are there undisclosed tail risks (black swan, liquidity dry-up, token unlocks, regulation)?
- Response to the aggressive side: "You focus on the bullish signal but ignore the risks,
  if the risk materializes, the loss would be significant — is that tolerable?"
- For beginners / small-capital users, is the volatility itself already too high?
```

### Risk Resolution
After the three members review, summarise the risk picture:
```
🛡️ Risk Resolution:
- Risk level: low, medium, or high — with a one-line rationale
- Strongest risk fact: the single data point that matters most (volatility, drawdown, liquidity, concentration)
- Special warning: if there are high-risk signs (all-in, heavy concentration, leverage), warn strongly
```

## Output Format (appended after the judgment)

```
🛡️ Risk Review
Volatility: recent typical swing, with the actual numbers from tools
Historical drawdown: max drawdown over the period, with the actual number
Levels that would change the risk picture: key support / resistance as data, with rationale
Data limitations: what could not be retrieved or has too short a history
Risk level: low, medium, or high

💡 Risk reminder: [the single most important risk reminder — e.g., "high volatility; losses can exceed what you expect, so only use money you can afford to lose entirely"]

⚠️ The above is analysis and does not constitute investment advice. Investing carries risk; decide based on your own situation.
```

(All figures must come from tools; for anything that cannot be retrieved, say "unavailable" — do not leave placeholders and do not estimate.)

## Strong Warnings for Special Cases

When the following high-risk behaviors are detected, **a strong warning is mandatory** (not just listing risks, but explicitly opposing):
- "All-in / full position / bet everything" → strongly warn about concentration risk; show the single asset's historical drawdown as the evidence
- "Mortgaging the house / borrowing to invest" → strongly oppose
- "High leverage" → warn about liquidation risk (a small adverse move can wipe out the position)
- "Copy-trading / buying on tips" → warn about information-source risk

These warnings are risk disclosure, not trade design: explain the danger with data, then leave the decision to the user.
