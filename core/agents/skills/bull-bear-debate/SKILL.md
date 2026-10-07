---
name: bull-bear-debate
description: "Bull-bear debate stress test — adapted from TradingAgents' bull/bear researcher mechanism. When the user challenges a judgment, asks for deeper weighing of trade-offs, or asks 'really? / are you sure? / what are the risks?', force a simulated bull vs bear debate before reaching a conclusion, to avoid single-perspective bias."
applies_to: [chat, crypto, us_stock, tw_stock, global_stock, commodity, forex]
priority: 6
eager_load: true  # 中等模型不會自己模擬辯論，命中時強制注入完整 body
auto_fire_keywords: [你確定嗎, 你確定, 真的適合, 真的值得, 真的嗎, 有什麼風險, 風險是什麼, 反方意見, 反駁, 挑戰, 質疑, 深入分析, 深入探討, 全面評估, 客觀分析, 正反兩面, 兩面看, 公正, are you sure, really worth, what about the risks, counter-argument, devil's advocate, play devil]
recommended_tools: ['get_crypto_price', 'technical_analysis', 'aggregate_news', 'get_fear_and_greed_index', 'get_cmc_quote_tool']
---

# Bull-Bear Debate Stress Test (adapted from TradingAgents Bull/Bear Researcher)

## When to Use
When the user challenges a judgment or asks for deeper/more objective analysis:
- "Are you sure TON is worth buying?"
- "Is it really worth investing in? What are the risks?"
- "Counter your previous point."
- "Give me an objective analysis."
- "Make the case both ways."

Also applies to high-stakes judgments (large size, long horizon), even without an explicit challenge:
- "I'm going all in on BTC, what do you think?"
- "Is it worth mortgaging the house to buy NVDA?"

**Division of labor with investment-judgment**:
- investment-judgment: the **structured format** of a judgment (bull → bear → synthesis)
- bull-bear-debate (this skill): the **stress test** of a judgment (bull vs bear debate → verdict)
- Both can trigger together: first list the factors in a structured way, then run the debate stress test.

## Debate Mechanism (adapted from TradingAgents, single-LLM simulation)

TradingAgents uses separate bull/bear researchers debating for n rounds, then a facilitator renders a verdict.
We **simulate this process with a single LLM** (no multi-agent architecture needed).

**Pre-step: gather evidence in parallel** (adapted from TradingAgents analysts gathering in parallel):
Before the debate, **call multiple tools in parallel within the same round** to collect the evidence both sides will use (price + sentiment + news + technicals). Do not query serially. Start the debate only after the evidence is complete.

**Key point (adapted from TradingAgents' Bear Counterpoints + Engagement)**:
A debate is not "the bull lists bull points, the bear lists bear points" talking past each other — it is **dynamic mutual refutation**. Each side must directly respond to the opponent's arguments and challenge them with data, not merely list its own evidence.

### Round 1: Bull Case
Argue, from a **bullish** standpoint with data, why the bullish case holds / why the price could rise. Focus on:
- **Growth Potential**: market opportunity, growth momentum, scaling potential
- **Competitive Advantages**: unique product / brand / market position
- **Positive Indicators**: healthy financials, industry trends, positive news (with specific data)
```
🐂 Bull Case:
Based on [data A + data B], [asset] has the conditions to rise:
1. [bull point 1 + specific data] → impact: ...
2. [bull point 2 + specific data] → impact: ...
3. [bull point 3 + specific data] → impact: ...
Bull conclusion: [one-line directional judgment]
```

### Round 2: Bear Rebuttal — core: directly refute the bull
Argue from a **bearish** standpoint. **Do not merely list your own bear points** — you must:
1. **Refute each bull point**: for every argument the bull made, challenge its validity with data
   - "The bull says the trend will continue, but [counter-data] shows this judgment is flawed, because..."
2. **Present the bear's own evidence**: risks the bull ignored
3. **Engagement**: clash directly in a conversational tone, not a cold bullet list
```
🐻 Bear Rebuttal:
On the bull's point 1: this judgment is flawed, because the counter-data shows...
On the bull's point 2: the data shows this tailwind is already priced in / unsustainable...
Risks the bull ignored:
1. [bear point 1 + data] → this is fatal because...
2. [bear point 2 + data] → ...
Bear conclusion: [one line, directly addressing why the bull's core logic does not hold]
```

### Round 3 (optional): Bull responds once
If the bear's rebuttal is forceful, the bull may respond once (keep it brief).

### Verdict (Facilitator Synthesis)
Weigh both sides' arguments objectively and render a final judgment:
```
⚖️ Debate Verdict:
Bull's strongest point(s): [pick the 1-2 most forceful]
Bear's strongest point(s): [pick the 1-2 most forceful]
Weighing: note which side's arguments have stronger data support, and which side relies on sentiment or speculation.
Final judgment: [direction] — [one-line rationale]
Confidence: high, medium, or low, based on the data strength of both sides' arguments.
```

## Output Format

```
## [Asset] Bull-Bear Debate

🐂 Bull Case: ... (2-4 bull points + data)

🐻 Bear Rebuttal: ... (refute the bull + 2-4 bear points + data)

⚖️ Debate Verdict:
- Bull's strongest: ...
- Bear's strongest: ...
- Final judgment: [direction] — [rationale]
- Confidence: high / medium / low

💡 Key insight: the single most important takeaway the debate surfaced — e.g., "the real risk is different from the obvious one"

⚠️ The above is analysis based on current data and does not constitute investment advice.
```

## Debate Quality Requirements

1. **Both sides must use data** — never just say "it might fall" or "it should rise"; attach data retrieved from tools.
2. **The bear must genuinely refute the bull**, not merely list its own bear points — challenge the bull's arguments directly.
3. **The verdict must explain which side it trusts and why** — not split the difference; pick a side based on data strength.
4. **Confidence must be honest** — if both sides' data are weak, mark "low confidence"; do not feign certainty.

## When Tools Return No Data

Same fallback principles as investment-judgment:
- State explicitly what data is missing
- Compensate with the aspects you can retrieve
- The debate must still proceed (debate with the limited data, flagging uncertainty)
- Do not skip the debate structure just because data is missing
