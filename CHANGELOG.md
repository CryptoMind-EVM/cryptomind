# CHANGELOG — 2026-08-20 ~ 2026-08-23

## 記帳系統（統一帳本）— 完整上線

### 功能
- PR #539: 基礎 schema（c032）+ repo + agent 工具
- PR #544: 統一帳本擴充（c033）+ 匯率服務 + REST API
- PR #545: Dashboard UI（4 卡 + 分類 chips + 搜尋 + 手動表單 + 長條圖）
- PR #546: Agent 工具路由指引（四語 prompt）

### 三層斷鏈修復（2026-08-22 深挖）
| 斷點 | 根因 | 修復 |
|---|---|---|
| 寫入不持久化 | INSERT 用 query_all（不 commit），事務關閉即 rollback | `a43b52f` 改走 transaction() |
| Agent 看不到工具 | ledger 工具只加在 "crypto" 類別，cryptomind agent 清單漏了 | `d769d99` 補進 agent 清單 |
| 工具拿不到使用者 | 誤 import 不存在的 core.agents.context → except 吞掉 → 恆 None | `f3e1625` 改用 key_resolver |

### 額外修復
| Bug | 修復 |
|---|---|
| Pulse 子分頁永久空白 | `ff76a09` dynamic import 斷鏈（window 橋接） |
| Dashboard 卡「載入中」 | `e643b4d` + `9a4e6c5` init 無人呼叫 + _TAB_MODULES 缺鍵 |
| 賣出被當加倉（損益全錯） | `5a77173` + `51fbc24` + `d37b6a7` side 欄三連修 |
| 同意卡每 session 只彈一次 | `f804971` _skill_consent_done 永久 flag 改 marker ts 比對 |
| 「花了 0.2 TON」句式漏叫工具 | `abff157` 金額偵測 regex → query 尾注入條件式提示 |
| 訪客記帳幻覺「已記錄」 | `02784e5` + `94db879` guest pipeline 加記帳引導規則 |
| 手動表單三按鈕全無效 | `6bd3f40` Journal 加入 click-delegator 白名單 |
| 同意卡送出後按鈕殘留 | `5c8aef4` Force Clean 補 .consent-card |

### E2E 驗證（全部通過）
支出/收入/投資買賣/多幣種（TWD/USD/TON 凍結匯率）/編輯再核准/拒絕/查帳統計/持倉損益/Dashboard 渲染/手動表單/篩選搜尋刪除

---

## i18n 國際化

| 項目 | 詳情 |
|---|---|
| 156 個死鍵復活 | `04518d2` 扁平含點鍵改巢狀（i18next 拆路徑查找永遠找不到） |
| placeholder 硬編 15 處 | `189e46a` data-i18n-attr="placeholder" |
| Legal 標題四語 | `189e46a` |
| 手機導覽 5 顆 | `04518d2` MOBILE_NAV_VISIBLE 對齊 MAX_ENABLED_ITEMS |
| 記帳加入 NAV_ITEMS | `a59b48f` journal 條目（第 5 位、PREFERENCES_VERSION 21） |
| Navigation Custom 跑版 | `a59b48f` .feature-item-label 加 min-width:0 + line-clamp 2 |

---

## UI/UX 修復

| 項目 | 詳情 |
|---|---|
| 16 個彈窗溢出 | `04518d2` 25 檔 36 處 vh→dvh（手機動態 viewport） |
| 「點開放手立刻縮回」 | `0f85cf6` 手勢級吞點——pointerdown 開面板後放手 click 落點已變（遮罩 z-55 蓋按鈕 z-40） |
| 側欄歷史對話太小 | `f558572` 導覽 8→5 項 + 38vh 上限 + min-h-0 內滾 |
| 台美股逾時洗版 | `08acdd9` 快取 60s + 失敗冷卻 + 不閃白 |
| CSS 版號快取 | `2a64c8f` tailwind-built.css v13→v14 bump |
| 側欄雙分頁 | 2026-08-23：導覽 + 歷史不再上下硬擠，改「對話歷史 / 功能選單」雙 tab 切換、各佔全高（DANNY 回饋）；手機抽屜選完功能自動收合；「更多分頁」popover 改錨定按鈕 |
| 側欄分頁記憶 | PR #549：sidebarActiveTab localStorage，重造訪回到上次分頁（ChatGPT/Claude 同款） |
| 全站側欄統一 | 2026-08-24：forum×7 / scam-tracker×3 / governance 掛上同款雙分頁側欄（site-sidebar.js classic 元件、inline style + design token 免 Tailwind 依賴、桌機 body 讓位 / 手機抽屜漢堡、跨頁對話歷史經 chat_last_session_id 還原）——設計 docs/plans/2026-08-24-site-sidebar-unification.md |
| messages.html#chat 排版崩壞 | 2026-08-24 當日事故+修復：論壇頁共用模組被 rolldown 併進 SPA main 入口 chunk → 整個 SPA 在論壇頁執行（#chat 改寫 URL、initChat 炸 null、版面摧毀）。修：論壇頁改單一薄 entry（web/js/pages/forum-*.js）+ codeSplitting.groups 強制 shared-core chunk + spa.js/chat-sessions 非 SPA 頁守門 |

---

## 安全

| 項目 | 詳情 |
|---|---|
| Admin 會員授予 bug | `ce66eb6` 固定 tx_hash 撞防重放 → 唯一化 |
| Silent refresh | `713d611` 通知服務不得有全域登出權（真兇：搶先 clearExpiredToken） |
| npm audit 歸零 | `c54472d` 6 個 high 風險修復（brace-expansion/picomatch/postcss/vite 等） |
| API 速率限制 | `1563f6b` 27 個端點補齊（pulse 20/min、klines 30/min、messages 30/min） |
| 資安四項驗證 | IDOR 403 ✓ / 未授權 401 ✓ / admin 權限 ✓ / model-config 不洩 key ✓ |

---

## 記憶治理

| 項目 | 詳情 |
|---|---|
| Part A（後端） | c031 migration + read_facts 過濾 + write_facts supersedes 鏈 |
| Part B（前端） | `595d369` verified/superseded 徽章 + 到期提示 + verify 按鈕 |

---

## 可觀測性

| 項目 | 詳情 |
|---|---|
| JS Error Boundary | `e11a46c` 前端錯誤三路捕捉 → 後端 → admin 面板可見 |
| 串流事件丟失兜底 | `4dd705f` 正常結束但泡泡空/剩 spinner → 從 DB 救回 |

---

## 測試

- 3488 項自動化測試全過
- 9 輪全面盤點（最後 5 輪零新 bug）
- 記帳全矩陣 E2E（含多幣種、槓桿、拒絕、統計）
- SQLi/XSS/IDOR/並發/極端輸入驗證
- 15 分頁 × 雙視窗 × 四語

---

## 統計

- **31 個 commit**
- **~4,500 行新增 / ~2,000 行刪除**
- **14 個產品 bug + 1 個測試修復 + 6 個安全修復**

## 設計決策記錄

| 決策 | 理由 |
|---|---|
| record_entry 只提案（Propose→Confirm→Commit） | 金融業標準 HITL 模式（arXiv 2510.07645）——工具不能自己寫錢 |
| 匯率在提案時凍結 | 確認卡顯示的換算=實際寫入的換算（歷史準確） |
| 確定性引導（regex→hint） | DeepSeek 對小數+代幣句式 tool 意圖辨識弱——prompt 強化不夠，需繞過模型隨機性 |
| marker ts 比對取代永久 flag | _skill_consent_done 永久 True 讓同 session 第二次同意卡永遠不彈 |
| 手勢級吞點 | pointerdown 開面板後放手 click 的 hit-test 落點已變（遮罩/彈窗蓋過按鈕） |
