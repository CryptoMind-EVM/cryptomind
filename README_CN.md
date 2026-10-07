[English](README.md)

# CryptoMind

**加密貨幣、美股、台股的 AI 行情分析與記帳助手。**
提供分析，不構成投資建議。非託管：CryptoMind 不持有使用者資金，也不代為交易。

🌐 正式站：<https://getcryptomind.com> · 🤖 Telegram：[@getcryptomind_bot](https://t.me/getcryptomind_bot)

> 這個 repo 是正式站程式碼的**公開精簡快照**，供閱讀、本機 demo，以及依 Apache-2.0 重用。
> 部署與維運檔案、內部規劃文件、以及正式站的防濫用規則集都刻意不放（見[這個 repo 沒有的東西](#這個-repo-沒有的東西)）。

---

## 功能

- **會用工具的 AI agent**：用日常語言提問，agent 會抓取即時價格、技術指標、新聞、鏈上資料與公司財報並呈現推理過程。
  涵蓋加密貨幣、美股、台股；市場頁另含港股、日股、韓股、A 股、商品與外匯。
- **統一帳本**：用對話記錄支出、收入與交易，使用者按下確認卡片前不會儲存任何資料。可看股票與加密貨幣的持倉與損益；
  綁定 Base 錢包後，轉帳會同步成已驗證持倉。
- **錢的行事曆**：財報、台股月營收、代幣解鎖、FOMC／CPI／非農、個人提醒，可選 08:00 每日早報（Telegram、Base App 或站內）。
- **判斷評分**：記下自己的市場判斷，到期後用實際收盤價評分，只有本人看得到。
- **詐騙檢查**：免費、不用登入；EVM 地址風險標記加上社群回報。
- **自帶金鑰**：支援 13 家 LLM 供應商，金鑰加密儲存；訪客每天有少量免費提問。
- **多通路**：可安裝的網頁版（PWA）、Telegram bot／Mini App、LINE bot、Base mini app；介面支援英文、繁中、簡中、俄文。

登入使用 EVM 錢包（Sign-In with Ethereum，含智慧合約錢包 EIP-1271/6492）或 Telegram。
Premium 由使用者已綁定的錢包在 Base 上支付 USDC，並在鏈上驗證。

## Agent 的信任模型

Agent 代表已驗證的使用者行動，並被授權邊界包住：

- **等級 RBAC × 風險等級**限制 agent 能呼叫哪些工具。
- **政策閘**：高風險動作（錢包存取、財務結論）必須經使用者明確同意，預設拒絕（fail-closed）。
- **稽核與撤銷**：同意與執行都可追溯，使用者隨時可撤銷。

詳見 [`docs/TRUST_DESIGN.md`](docs/TRUST_DESIGN.md)。

## 架構

| 層 | 技術 |
|---|---|
| 後端 | FastAPI、SQLAlchemy、Alembic、PostgreSQL、Redis |
| Agent | LangGraph + LangChain、MCP 工具載入、skill 系統 |
| 前端 | Vanilla JS（ES modules）、Tailwind CSS、Vite、Lightweight Charts |
| 登入／付款 | SIWE（EVM 錢包）、Base 上 USDC（鏈上 log 驗證） |
| 通路 | Telegram bot、LINE bot、PWA、Base mini app |
| 測試 | pytest、Playwright |

入口：`api_server.py`（API 與靜態網頁）、`bot/telegram_bot.py`（bot）、`core/agents/`（agent runtime）、
`core/tools/`（資料工具）、`api/routers/`（HTTP API）、`web/`（前端）。更多見 [`docs/architecture.md`](docs/architecture.md)。

## 本機執行

需求：Docker，或 Python 3.13 + Node 20+ 搭配 PostgreSQL 與 Redis。

```bash
cp .env.example .env
# 至少設定：JWT_SECRET_KEY、API_KEY_ENCRYPTION_SECRET（各用 openssl rand -hex 32 產生），
# 不用 docker compose 的話還要設 DATABASE_URL / REDIS_URL
docker compose up --build app db redis   # http://localhost:8000
```

不用 Docker：

```bash
pip install -r requirements.txt && npm install
npm run build            # Tailwind + Vite 打包
python api_server.py     # http://localhost:8080
```

`.env.example` 有每個變數的說明。在 app 設定裡加入任一家 LLM 金鑰（OpenAI、Anthropic、Gemini、DeepSeek、OpenRouter…）
即可啟用 AI 回答；有免費來源的行情工具不需額外金鑰。沒有錢包也想本機試用，請看 `.env.example` 裡 `TEST_MODE` 的說明
（正式環境絕對不要開）。

```bash
pytest -m "not e2e"      # 單元／整合測試
ruff check .
```

## 這個 repo 沒有的東西

- 部署與維運檔案（基礎設施配置、備份還原自動化、CI 部署流程）。
- 內部規劃文件。
- 正式站對抗濫用的規則集：這裡的內容審核詞句只是精簡示範版，真正的門檻由環境變數／管理設定提供，不在原始碼裡。
- 任何金鑰與密碼。Git 歷史沒有帶過來，這份快照從乾淨的歷史開始。

## 資安

請私下回報漏洞，見 [SECURITY.md](SECURITY.md)，不要開公開 issue。

## 授權

[Apache License 2.0](LICENSE)。CryptoMind 是獨立的開源專案，與任何區塊鏈專案、加密貨幣專案或金融機構無隸屬、背書或贊助關係。
