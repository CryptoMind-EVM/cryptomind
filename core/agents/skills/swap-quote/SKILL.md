---
name: swap-quote
description: "TON token swap quote via Omniston liquidity aggregator. Use when the user asks about swapping/exchanging TON or TON jettons (e.g. 'swap 100 TON to USDt', 'how much USDt for 50 TON', 'TON 換 USDt 能拿多少'). Returns best route, rate, slippage, and price impact. Read-only — does NOT execute the swap."
applies_to: [crypto]
priority: 6
# 不收「買／賣／trade」：這些字會讓一般財經問題（「台積電值得買嗎」）誤載入報價 skill。
auto_fire_keywords: [換, 兌換, 兑换, 交換, 交换, swap, convert, 換成, 换成, 能換, 能换, 拿到多少]
recommended_tools: ['get_swap_quote', 'get_ton_balance']
# eager_load：CLAW 全能 agent 走 progressive disclosure，只注入 skill 目錄，
# 靠 LLM 自覺 load_skill。但「換幣查詢」跟「查幣價」用詞高度重疊（100 TON 換 USDt），
# 中等模型看了目錄不會自覺 load swap-quote，反而退化去查幣價/web search
# （線上 run 09bb1df8 觀察到此行為）。eager_load 讱查詢命中時直接注入完整方法，
# 明確指示用 get_swap_quote。與 market-risk-assessment / investment-judgment 同類處理。
eager_load: true
---

# Swap Quote (TON DeFi)

## When to Use
When the user wants to know a swap quote for TON or TON jettons:
- "幫我查用 100 TON 換 USDt 能拿到多少"
- "swap 50 TON to USDt"
- "TON 換 STON 匯率多少"
- "100 顆 TON 能換多少美元穩定幣"
- "我想把 USDt 換回 TON"

**Do NOT use this skill when:** the user wants to actually *execute* the swap (sign a transaction, move funds). Quote is read-only. Execution is a separate flow (requires consent + wallet signing).

## Method

### Step 1: Identify the trade pair and amount
Parse from the user's message:
- **Input asset** (what they pay with): usually native TON → pass `"native"`. If a jetton, use its address.
- **Output asset** (what they receive): a jetton address.
- **Amount**: in TON units; convert to nano for the tool (1 TON = 1,000,000,000 nano).

**Common jetton addresses (use these directly, do NOT ask the user):**
| Symbol | Address | Notes |
|--------|---------|-------|
| USDt | `EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs` | Tether USD, most liquid stable |
| STON | `EQA2kCVNwVsil2EM2mB0SkXytxCqQjS4mttjDpnXmwG9T6bO` | STON.fi governance token |

For any **other** token the user mentions that isn't in this table, ask for its EQ/UQ address (it may be a scam — see Step 3).

### Step 2: Fetch the quote
Call `get_swap_quote` with:
- `input_asset`: `"native"` for TON, or jetton address
- `output_asset`: jetton address
- `input_units`: amount in nano (string)
- `max_slippage_pips`: leave default (5000 = 0.5%)

The tool returns: `output_units` (expected receive), `min_output_amount` (slippage-protected minimum), `price_impact_percent`, `routes`, `source`.

### Step 3: Assess and present risk
Always evaluate the quote's `price_impact_percent` before presenting. This protects the user from thin-liquidity / scam tokens:

| Price impact | Level | What to tell the user |
|--------------|-------|----------------------|
| < 1% | 🟢 Normal | "匯率正常，流動性充足" |
| 1–3% | 🟡 Slightly high | "價格影響略高，注意滑點" |
| 3–5% | 🟠 High | "價格影響偏高，實際成交成本會明顯高於報價" |
| 5–15% | 🔴 Very high | "⚠️ 價格影響很高，這筆交易可能不划算" |
| > 15% | ⚫ Extreme | "⚠️ 價格影響過大，流動性可能不足（可能是低流動性代幣）" |

**Risk red flags — warn the user clearly if:**
- The user provided an unknown token address (not in the table above) → warn it's unverified, suggest checking it first.
- `price_impact_percent > 5%` → the pool is thin; swapping will lose significant value.
- The quote fails or returns 0 → the token may not have liquidity; say no usable route was found, do not present it as a quote.

### Step 4: Present the quote
Format clearly in the user's language:
- 付出多少 → 預計收到多少（最少收到多少，含滑價保護）
- 匯率、價格影響、路由（via which DEX）
- 風險燈號（from Step 3）

**Always clarify this is a quote only, not executed:** "這是報價預覽，尚未執行換幣。"

### Step 5 (optional): Check balance first
If the user asks "我錢包夠不夠換" or mentions their holdings, call `get_ton_balance` with their wallet address first, then fetch the quote.

## Out of scope
- **Advice on whether or when to swap or trade** — this skill only reports the quote and its cost; it does not say whether the swap is a good idea.
- **Executing the swap** (wallet signing, on-chain transaction) — this requires explicit user consent and is handled separately.
- **Cross-chain swaps** (TON ↔ EVM) — not yet supported.
