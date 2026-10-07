# Multiturn Scenario Suite

CryptoMind agent 的回歸測試問題集 — 用真實 LLM 跑多種情境，驗證 agent 行為
沒被改壞。

## 用途

- **PR review 前**：跑一次確認改動沒退化關鍵行為
- **新模型上線前**：用新模型跑 suite，看遵從性
- **重現 bug**：把 bug 寫成新 scenario 加進 YAML

## 跑法

需要先設定：
- `DATABASE_URL`（Neon 或本地 PG 都行；要有 schema）
- `LANGFUSE_*` keys（trace 會進 Langfuse dashboard）
- NVIDIA 或其他 BYOK key

```bash
# 跑全部（10 個 scenario，~10-15 分鐘）
python tests/scenarios/run_suite.py

# 只跑某類別
python tests/scenarios/run_suite.py --category unknown_ticker

# 只跑某 scenario id
python tests/scenarios/run_suite.py --id S6-tool-failure-tolerance

# 只 validate YAML 結構（不跑 LLM）
python tests/scenarios/run_suite.py --dry-run
```

## 結果

- **console summary**：每個 scenario ✅ / ⚠️ / ❌
- **JSON 詳細結果**：`tests/scenarios/results_<timestamp>.json`（gitignored）
- **Langfuse traces**：每個 scenario 一個 session_id，可在 dashboard 看完整
  LLM generation + tool calls + recovery 過程

## Soft vs Hard fail

YAML 內可標 `soft: true`：

```yaml
- id: "S1-unknown-ticker"
  soft: true  # nemotron 對未知代號不穩定；fail 算 warning
```

- **Hard fail**（無 soft）：exit code 1，擋 PR
- **Soft fail**（soft=true）：算 warning，不擋 PR

用途：某些情境本質上依賴 LLM 遵從性（例如 nemotron 對冷門代號），不該因為
模型本身能力問題卡住開發。但要留下 visibility。

## 加新 scenario

編輯 `multiturn_suite.yaml`，加新 entry：

```yaml
- id: "S11-new-case"
  category: new_category
  language: "zh-TW"
  turns:
    - query: "你的問題"
      expect_tools: ["tool_name"]            # 可選；至少一個
      expect_keywords: ["必須出現的詞"]       # 可選
      expect_not_keywords: ["不該出現的詞"]   # 可選
      expect_nonempty: true                  # 預設 true
      notes: "這題在驗什麼"
```

欄位：
- `id`：唯一，建議 `S<n>-<category>`
- `category`：分類，方便 `--category` 過濾
- `language`：UI 語言
- `turns`：同一個 session 的多輪（測多輪記憶）
- `soft`：選用，true 時 fail 算 warning

## 現有 scenarios

| ID | 類別 | 驗什麼 |
|---|---|---|
| S1 | unknown_ticker | SNXX（nemotron soft；模型遵從性） |
| S2 | unknown_ticker | PEPE 冷門幣 |
| S3 | multiturn_context | 跨主題切換（BTC → 台積電 → 黃金） |
| S4 | multiturn_context | 多輪代名詞（"它們" parser） |
| S5 | language_switch | 中英文切換 |
| S6 | tool_failure | 查無此幣（容忍 + 明確告知） |
| S7 | beginner | 小白口語問法 |
| S8 | complex_reasoning | 多標的比較 + 配置建議 |
| S9 | smalltalk | 閒聊不該硬呼叫 tool |
| S10 | baseline | 繁中基本查詢 |
