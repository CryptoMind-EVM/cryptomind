# cryptomind-safety-kernel

**可驗證的 AI-agent 風險閘控規則**（純規則、零依賴）。

這是 CryptoMind 的「審查核心」：把「碰風險的動作必須經過人類同意」這類聲明寫成可逐行核對的程式碼。
零網路 I/O：不查幣價、不查代幣、不碰資料庫。

> **現況說明**：換幣（swap）功能已從產品移除，`risk_tiers`、`swap_limits`、`consent_gate` 保留為
> 參考實作與測試，**目前正式站不使用**。仍在使用的是 `safety_rules`（TON 代幣風險訊號，供詐騙檢查呼叫）。

## 為什麼存在

操作型流程（碰資金）不該讓 LLM 自由組合工具，而是走固定流程 + 人類同意閘。
**「同意閘不可跳過」這個聲明必須可以被驗證**——本套件就是把這份聲明變成可核對的程式碼。

## 涵蓋的規則

| 模組 | 規則 |
|---|---|
| `risk_tiers` | price impact 色階：<1% green / 1-3% yellow / 3-5% orange（同意）/ 5-15% red（同意）/ ≥15% block（硬擋，無 expert 後門） |
| `safety_rules` | jetton 安全訊號：官方驗證（whitelist）、持有者 ≥1000、admin 權限 |
| `swap_limits` | 單筆 USD 限額（灰度期 50 USD），**fail-closed**：查不到幣價就拒絕，不放行未知金額 |
| `consent_gate` | 需要同意的等級（orange/red）+ 同意足跡的 canonical SHA-256 |

### 設計原則

- **金額不論大小都要確認**：執行 swap 必須經過使用者顯式確認（主平台 `/confirm`）。
- **限額是防 bug 後備保險**：與同意卡疊加——使用者同意也無法一次賭超過上限。
- **同意足跡不可否認**：每次 confirm 記錄 `consent_hash`（`consent_payload_hash`），
  內容是「使用者同意的事實」（quote/數量/最低收到/風險等級/價格影響/kernel 版本）
  的 canonical SHA-256。
