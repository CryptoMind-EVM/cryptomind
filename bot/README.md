# CryptoMind Telegram Bot

An aiogram3-based Telegram bot service that integrates with the CryptoMind platform.
It forwards user messages to the LangGraph agent via the bot-facing API endpoints.

## Features

- `/start` — Welcome message with usage instructions
- `/start link_<code>` — One-tap binding from the website's deep link (`t.me/<bot>?start=link_<code>`)
- `/link <token>` — Bind a Telegram account to a CryptoMind account (manual fallback)
- `/unlink` — Guide user to unbind via the web UI
- `/help` — Show all available commands
- Plain text messages — Forwarded to `POST /api/telegram/chat` for AI analysis

## Architecture

```
Telegram User
     │
     ▼
┌─────────────┐         X-Bot-Secret header
│ aiogram3    │ ──────►  API Server
│ Bot Service  │          (FastAPI)
│ (polling /   │          └─► LangGraph Agent
│  webhook)    │               └─► LLM
└─────────────┘
```

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

> `aiogram>=3.4.0` will be added automatically.

### 2. Configure environment variables

Copy `.env.example` to `.env` and add the following variables:

```bash
# Required — Telegram Bot Token from @BotFather
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz

# Required — shared secret between bot and API (set same value in API .env)
BOT_INTERNAL_SECRET=your_random_secret_here

# Optional — defaults to http://localhost:8080
API_BASE_URL=https://your-api-domain.com

# Optional — defaults to "polling". Use "webhook" for production.
TELEGRAM_BOT_MODE=polling

# Required only for webhook mode
TELEGRAM_WEBHOOK_HOST=0.0.0.0
TELEGRAM_WEBHOOK_PATH=/bot/webhook
```

### 3. Set `BOT_INTERNAL_SECRET` in the API server

The API server must have the **same** `BOT_INTERNAL_SECRET` value to authenticate
bot requests.

### 4. Start the bot

**Development (polling mode):**

```bash
python -m bot.telegram_bot
```

**Production (webhook mode):**

```bash
TELEGRAM_BOT_MODE=webhook \
TELEGRAM_WEBHOOK_HOST=0.0.0.0 \
TELEGRAM_WEBHOOK_PATH=/bot/webhook \
python -m bot.telegram_bot
```

Or with gunicorn (webhook recommended for production):

```bash
gunicorn bot.telegram_bot:main \
  --bind 0.0.0.0:8443 \
  --worker-class aiohttp.GunicornWebWorker \
  --threads 1 \
  --log-level info
```

## User Flow

1. User taps "Connect Telegram" on the web → the site opens `t.me/<bot>?start=link_<code>`
2. User taps START → bot receives `/start link_<code>` and calls `POST /api/telegram/link-preview`.
   **Nothing is bound yet**: the bot shows which account it will bind to (display name + masked id),
   a warning ("Only confirm if you started this from your own browser"), and — if this Telegram is
   already bound elsewhere — "This will move your Telegram from account A to account B",
   with [Confirm] [Cancel] buttons (callback data `tgl:ok:<16 hex>` / `tgl:no:<16 hex>`)
3. Confirm → `POST /api/telegram/verify-link` with the pending id; the server only accepts it from
   the same Telegram user who opened the link, then consumes the code (single-use, 5-minute TTL).
   Cancel → `POST /api/telegram/link-cancel` invalidates the code and token
4. Fallback: the web dialog also shows `/link <token>`; it goes through the same confirmation
5. On success the bot says which account was linked and whether the daily brief is on
   (no explicit brief settings + bound Telegram = on at 08:00 local); the user can chat right away

Why the confirmation: an attacker could send a victim a link for the attacker's own account;
binding it would put the victim's bot chats into the attacker's account (web and Telegram share
the same chat session).

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | Bot token from @BotFather |
| `BOT_INTERNAL_SECRET` | ✅ | — | Shared secret for X-Bot-Secret header |
| `API_BASE_URL` | ❌ | `http://localhost:8080` | CryptoMind API base URL |
| `TELEGRAM_BOT_MODE` | ❌ | `polling` | `polling` or `webhook` |
| `TELEGRAM_WEBHOOK_HOST` | webhook only | — | Host to bind webhook server |
| `TELEGRAM_WEBHOOK_PATH` | webhook only | `/bot/webhook` | Webhook URL path |

## Error Handling

| Scenario | Bot response |
|----------|--------------|
| Not linked (`/api/telegram/chat` returns 403) | Prompt to `/link` |
| Invalid/expired token (400) | Tell user to get new token from web |
| Already linked (409) | Tell user account is already bound |
| Rate limited (429) | Tell user to slow down |
| API timeout | Generic retry message |
| Unexpected error | Generic error + log details |
