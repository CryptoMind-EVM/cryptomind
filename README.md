[繁體中文](README_CN.md)

# CryptoMind

**AI market analyst and money ledger for crypto, US stocks and Taiwan stocks.**
Analysis, not financial advice. Non-custodial: CryptoMind never holds user funds and never executes trades.

🌐 Live app: <https://getcryptomind.com> · 🤖 Telegram: [@getcryptomind_bot](https://t.me/getcryptomind_bot)

> This repository is a **sanitized public snapshot** of the production codebase. It is meant for reading,
> local demos and reuse under Apache-2.0. Deployment/ops files, internal planning documents and the
> production anti-abuse rule sets are intentionally not included (see [What is not in this repo](#what-is-not-in-this-repo)).

---

## What it does

- **Tool-using AI agent** – asks in plain language; the agent pulls live prices, technicals, news, on-chain data and
  company filings, and shows its reasoning. Crypto, US and Taiwan stocks; the market pages also cover Hong Kong,
  Japan, Korea, A-shares, commodities and forex.
- **Unified ledger** – log expenses, income and trades by chatting. Nothing is saved until the user approves a
  confirmation card. Holdings and P&L for stocks and crypto; binding a Base wallet syncs transfers as verified holdings.
- **Money calendar** – earnings, Taiwan monthly revenue, token unlocks, FOMC / CPI / jobs reports, personal reminders,
  and an optional 08:00 daily brief (Telegram, Base App or in-app).
- **Judgment scoring** – record your own market calls; they are graded against actual closing prices (private to you).
- **Scam checker** – free, no login: EVM address risk flags plus community reports.
- **Friends and chat** – one-to-one chat with real-time delivery, typing indicators, replies, reactions and unsend;
  an optional in-chat AI assistant can summarize the conversation or look things up together with you.
- **Bring your own key** – 13 LLM providers, several saved models per provider, keys encrypted at rest. Guests get a few free questions per day.
- **Memory and skills** – the agent proposes things worth remembering and stores them only after you approve;
  built-in skills can be toggled and you can write up to 10 custom skills.
- **Everywhere** – installable web app (PWA), Telegram bot / Mini App, LINE bot, Base mini app; UI in English,
  Traditional Chinese, Simplified Chinese and Russian.

Sign-in is an EVM wallet (Sign-In with Ethereum, including smart-contract wallets via EIP-1271/6492) or Telegram.
Premium is paid in USDC on Base by a wallet the user has bound, verified on-chain.

## Trust model for the agent

The agent acts on behalf of a verified principal and is wrapped by authorization boundaries:

- **Tier RBAC × risk level** limits which tools the agent may call.
- **Policy gate** – high-risk actions (wallet access, financial verdicts) need explicit human consent, fail-closed.
- **Audit log and revocation** – consent decisions and executions are traceable; users can revoke at any time.

Details: [`docs/TRUST_DESIGN.md`](docs/TRUST_DESIGN.md).

## Architecture

| Layer | Technology |
|---|---|
| Backend | FastAPI, SQLAlchemy, Alembic, PostgreSQL, Redis |
| Agent | LangGraph + LangChain, MCP tool loader, skill system |
| Frontend | Vanilla JS (ES modules), Tailwind CSS, Vite, Lightweight Charts |
| Auth / payments | SIWE (EVM wallets), USDC on Base verified through on-chain logs |
| Channels | Telegram bot, LINE bot, PWA, Base mini app |
| Tests | pytest, Playwright |

Entry points: `api_server.py` (API + static web), `bot/telegram_bot.py` (bot), `core/agents/` (agent runtime),
`core/tools/` (data tools), `api/routers/` (HTTP API), `web/` (frontend). More in [`docs/architecture.md`](docs/architecture.md).

## Run it locally

Requirements: Docker, or Python 3.13 + Node 20+ with PostgreSQL and Redis.

```bash
cp .env.example .env
# set at least: JWT_SECRET_KEY, API_KEY_ENCRYPTION_SECRET (openssl rand -hex 32 each),
# and DATABASE_URL / REDIS_URL if you are not using docker compose
docker compose up --build app db redis   # http://localhost:8000
```

Without Docker:

```bash
pip install -r requirements.txt && npm install
npm run build            # Tailwind + Vite bundle
python api_server.py     # http://localhost:8080
```

`.env.example` documents every variable. Add an LLM key (OpenAI, Anthropic, Gemini, DeepSeek, OpenRouter, …) in the
app's settings to enable AI answers; market data tools work without extra keys where free sources exist.
For a local demo without a wallet see the `TEST_MODE` notes in `.env.example` (never enable it in production).

```bash
pytest -m "not e2e"      # unit / integration tests
ruff check .
```

## What is not in this repo

- Deployment and operations files (infrastructure layout, backup/restore automation, CI deploy pipelines).
- Internal planning documents.
- The production rule sets used against abuse: the moderation phrase list here is a reduced demonstration set,
  and real thresholds are supplied through environment variables / admin settings, not source code.
- Secrets of any kind. Git history is not carried over; this snapshot starts from a clean history.

## Team

Built by five independent developers covering AI/LLM architecture, data retrieval, backend and infrastructure,
API and deployment, and web feature testing.

## Security

Please report vulnerabilities privately, see [SECURITY.md](SECURITY.md). Do not open public issues for them.

## License

[Apache License 2.0](LICENSE). CryptoMind is an independent open-source project and is not affiliated with,
endorsed by, or sponsored by any blockchain project, cryptocurrency project or financial institution.
