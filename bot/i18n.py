"""Lightweight i18n for the Telegram bot.

The bot is a standalone service with no access to the web frontend's
i18next bundles, so it keeps its own translation dict here. Keys mirror
the error codes returned by the API (e.g. ``NO_API_KEY``) plus bot-only
strings (welcome, help, etc.).

Language resolution:
1. ``language`` arg passed to ``t()``
2. Fallback: ``DEFAULT_LANGUAGE``
"""

from __future__ import annotations

from typing import Optional

DEFAULT_LANGUAGE = "zh-TW"

MESSAGES: dict[str, dict[str, str]] = {
    "zh-TW": {
        # /start
        "welcome": (
            "👋 歡迎來到 CryptoMind Bot！\n\n"
            "我可以幫您分析加密貨幣、追蹤行情，即時回答您的問題。\n\n"
            "🔗 第一次使用？到網站按「連結 Telegram」，一鍵就綁好；"
            "也可以取得連結 Token 後輸入 /link <token>。\n\n"
            "📌 使用說明：\n"
            "  /link <token> — 綁定平台帳號\n"
            "  /check <地址> — 轉帳前地址健診\n"
            "  /brief on|off — 每日早報開關\n"
            "  /lang — 切換語言\n"
            "  /sessions — 選擇 / 切換對話\n"
            "  /new — 開新對話\n"
            "  /unlink — 解除帳號綁定\n"
            "  /help — 顯示所有指令\n\n"
            "ℹ️ 所有內容為資料分析，不構成投資建議。"
        ),
        # Mini App launch button (web_app)
        "disclaimer": "ℹ️ 以上為資料分析，不構成投資建議。",
        "open_app": "🚀 開啟 CryptoMind",
        # /check 地址健診（design 2026-08-18）
        "check_usage": "🔎 用法：/check <地址>\n貼上 TON（EQ/UQ…）或 EVM（0x…）地址，轉帳前先查一下。",
        "check_high_risk": "🚨 高風險 — 建議不要轉帳",
        "check_caution": "⚠️ 留意 — 資訊不完整或有黃旗",
        "check_no_red_flags": "✅ 未發現紅旗（非保證安全）",
        "check_invalid_address": "❌ 地址格式不正確（需 TON EQ/UQ… 或 EVM 0x…）",
        "check_rate_limited": "⏳ 查詢太頻繁，請稍後再試",
        "check_failed": "❌ 健診暫時無法使用，請稍後再試",
        "check_footer": "判定由社群舉報＋GoPlus＋TonAPI 即時合成，僅供參考。\n🌐 完整報告：/scam-tracker/",
        # /help
        "help": (
            "📖 CryptoMind Bot 指令說明\n\n"
            "  /start — 顯示歡迎訊息\n"
            "  /link <token> — 綁定您的 CryptoMind 帳號\n"
            "  /sessions — 列出並切換對話（與網頁端共用記錄）\n"
            "  /new — 切換到全新的 Telegram 對話\n"
            "  /lang — 切換語言（早報與通知用；跟網站右上角是同一個設定）\n"
            "  /unlink — 解除帳號綁定\n"
            "  /help — 顯示本說明\n\n"
            "直接傳送訊息給我，我會轉交給 AI 分析師回覆！\n"
            "我會用你發問的語言回答；看不出來時才用你設定的語言。"
        ),
        # /sessions & /new
        "sessions_header": "💬 選擇要繼續的對話（✅ 為目前對話）：",
        "sessions_empty": "目前沒有對話記錄。直接傳訊息給我即可開始新對話。",
        # /brief
        "brief_usage": "用法：/brief on 開啟每日早報，/brief off 關閉。",
        "brief_on": "✅ 每日早報已開啟，每天本地 8 點送到這裡。到網站 Settings 可以改時間。",
        "brief_off": "🔕 每日早報已關閉，隨時 /brief on 再開。",
        # /lang
        "lang_choose": (
            "🌐 選擇語言\n\n早報、通知，以及看不出語言的訊息都會用這個語言。"
            "跟網站右上角切換語言是同一個設定。"
        ),
        "lang_set": "✅ 語言已改成：{name}。網站、早報與通知也會一起換。",
        "sessions_failed": "⚠️ 無法載入對話列表，請稍後再試。",
        "sessions_default_label": "🆕 Telegram 預設對話",
        "session_switched": "✅ 已切換到：{title}\n\n之後的訊息都會接續這個對話。",
        "session_new_done": "✅ 已開新對話。之後的訊息會記在全新的 Telegram 對話裡。",
        "SESSION_NOT_FOUND": "找不到該對話，可能已被刪除，請用 /sessions 重新選擇。",
        # /link
        "link_missing_token": (
            "❌ 請提供連結 Token。\n"
            "格式：/link <您的token>\n\n"
            "請到 CryptoMind 網站取得 Token。"
        ),
        "link_success": "✅ 綁定成功！\n\n已將 Telegram 帳號綁定至：@{username}\n\n現在可以直接傳送訊息給我，我會為您分析市場！",
        "link_brief_on": "☀️ 每日早報已開啟：每天本地 {hour} 送到這裡（持倉、行事曆、判斷結果）。回覆 /brief off 可關閉。",
        "link_brief_off": "☀️ 想每天早上收到早報？回覆 /brief on 就開。",
        # 綁定確認（2026-09-27 安全需求：連結可能是別人丟過來的）
        "link_confirm_prompt": "🔗 要把這個 Telegram 綁定到 CryptoMind 帳號：\n{target}",
        "link_confirm_move": "⚠️ 這會把你的 Telegram 從帳號 {current} 移到 {target}。",
        "link_confirm_warning": (
            "⚠️ 只有在你自己的瀏覽器按了「連結 Telegram」才確認。"
            "如果連結是別人傳給你的，請按取消——確認後對方就能在網頁上看到你在這裡的對話。\n"
            "（{minutes} 分鐘內有效）"
        ),
        "link_confirm_btn": "✅ 確認綁定",
        "link_cancel_btn": "✖️ 取消",
        "link_cancelled": "已取消，沒有綁定任何帳號。",
        "LINK_NOT_YOURS": "這個確認不是給你的，只有開啟連結的人能按。",
        "link_failed": "❌ 綁定失敗：{detail}",
        # /unlink
        "unlink_redirect": (
            "ℹ️ 解除綁定需在網站操作。\n\n"
            "請前往 CryptoMind 網站的「個人設定」頁面，"
            "找到 Telegram 綁定設定並解除。"
        ),
        # chat errors (mapped from API error codes)
        "NOT_BOUND": "⚠️ 請先使用 /link <token> 指令綁定您的帳號。",
        "RATE_LIMITED": "⚠️ 操作太頻繁了，請稍後再試。",
        "ALREADY_LINKED": "此 Telegram 帳號已經綁定過了，無法重複綁定。",
        "INVALID_TOKEN": "連結 Token 無效或已過期，請在網站重新取得。",
        "USER_NOT_ACTIVE": "您的帳號狀態異常，請聯繫客服。",
        "NO_API_KEY": (
            "⚠️ 您尚未設定 LLM API Key。\n\n"
            "請先到 CryptoMind 網站的「設定」頁面，配置任一 API Key：\n"
            "  • OpenAI\n  • OpenRouter\n  • Google AI\n\n"
            "設定完成後即可使用 AI 分析功能。"
        ),
        "INVALID_API_KEY": "⚠️ API Key 無效，請到網站設定頁面檢查您的 API Key。",
        "ANALYSIS_FAILED": "⚠️ 分析失敗，請稍後再試。",
        # generic
        "timeout": "⏳ 回覆時間過長，伺服器忙碌中，請稍後再試。",
        "link_timeout": "⚠️ 連線逾時，請稍後重試。",
        "unknown_error": "⚠️ 發生未知錯誤，請稍後重試或聯繫客服。",
        # HITL 卡（記帳同意／釐清）：按鈕過期（API 重啟或重複點）
        "NO_PENDING_HITL": "這張卡已經過期了，請再說一次。",
        "empty_response": "（無法產生回覆，請稍後再試。）",
        "analyzing": "🌀 分析中...",
        "media_unsupported": (
            "📷 抱歉，目前暫不支援圖片／語音／檔案分析。\n"
            "請直接用文字描述您的問題（例如「分析 BTC 走勢」），我馬上為您處理！"
        ),
        "image_default_prompt": "請詳細分析這張圖片的內容。若是 K 線／持倉／市場相關截圖，請從投資角度解讀。",
        "image_too_large": "📸 圖片超過 4MB 上限，請壓縮後再試（直接貼上 Telegram 相片會自動壓縮）。",
        "vision_failed": "⚠️ 圖片分析失敗，請稍後再試或改用文字提問。",
        "VISION_UNSUPPORTED_MODEL": "🚫 此模型不支援圖片分析——請切換支援視覺的模型（如 GPT-4o / Gemini / Claude），或改用文字描述。",
        "VISION_IMAGE_TOO_LARGE": "📸 圖片超過 4MB 上限，請壓縮後再試。",
        "VISION_INVALID_FORMAT": "📸 僅支援 PNG / JPEG / WebP 圖片。",
        "VISION_DISABLED": "📷 圖片分析目前停用中。",
    },
    "en": {
        "welcome": (
            "👋 Welcome to CryptoMind Bot!\n\n"
            "I can help you analyze crypto, track markets, and answer your questions in real time.\n\n"
            '🔗 First time? Tap "Connect Telegram" on the website to link your account in one tap, '
            "or send /link <token> with a token from the website.\n\n"
            "📌 Commands:\n"
            "  /link <token> — Bind platform account\n"
            "  /sessions — Pick / switch conversation\n"
            "  /new — Start a new conversation\n"
            "  /check <address> — Address safety check before transferring\n"
            "  /brief on|off — daily brief on/off\n"
            "  /lang — change language\n"
            "  /unlink — Unbind account\n"
            "  /help — Show all commands\n\n"
            "ℹ️ All content is data analysis, not investment advice."
        ),
        "disclaimer": "ℹ️ Data analysis only, not investment advice.",
        "open_app": "🚀 Open CryptoMind",
        # /check address safety check (design 2026-08-18)
        "check_usage": "🔎 Usage: /check <address>\nPaste a TON (EQ/UQ…) or EVM (0x…) address — check before you transfer.",
        "check_high_risk": "🚨 High risk — do not transfer",
        "check_caution": "⚠️ Caution — incomplete info or yellow flags",
        "check_no_red_flags": "✅ No red flags found (not a guarantee)",
        "check_invalid_address": "❌ Invalid address format (expect TON EQ/UQ… or EVM 0x…)",
        "check_rate_limited": "⏳ Too many requests, please retry later",
        "check_failed": "❌ Check temporarily unavailable, please retry later",
        "check_footer": "Verdict combines community reports + GoPlus + TonAPI live. Reference only.\n🌐 Full report: /scam-tracker/",
        "help": (
            "📖 CryptoMind Bot Commands\n\n"
            "  /start — Show welcome message\n"
            "  /link <token> — Bind your CryptoMind account\n"
            "  /sessions — List & switch conversations (shared with the web)\n"
            "  /new — Switch to a fresh Telegram conversation\n"
            "  /lang — Change language (used for the brief and notifications; same setting as the website)\n"
            "  /unlink — Unbind account\n"
            "  /help — Show this help\n\n"
            "Send me a message and I'll forward it to the AI analyst!\n"
            "I reply in the language you write in; if I can't tell, I use your language setting."
        ),
        "sessions_header": "💬 Pick a conversation to continue (✅ = current):",
        "sessions_empty": "No conversations yet. Just send me a message to start one.",
        # /brief
        "brief_usage": "Usage: /brief on to enable the daily brief, /brief off to disable.",
        "brief_on": "✅ Daily brief is on. It arrives here at 8am local time; change the hour in Settings on the website.",
        "brief_off": "🔕 Daily brief is off. Send /brief on any time to resume.",
        # /lang
        "lang_choose": (
            "🌐 Choose a language\n\nYour brief, notifications, and messages whose language "
            "I can't tell will use it. It's the same setting as the language switch on the website."
        ),
        "lang_set": "✅ Language set to {name}. The website, brief and notifications switch too.",
        "sessions_failed": "⚠️ Couldn't load your conversations. Please try again later.",
        "sessions_default_label": "🆕 Default Telegram chat",
        "session_switched": "✅ Switched to: {title}\n\nYour next messages will continue this conversation.",
        "session_new_done": "✅ New conversation started. Your next messages go into a fresh Telegram chat.",
        "SESSION_NOT_FOUND": "Conversation not found — it may have been deleted. Use /sessions to pick again.",
        "link_missing_token": (
            "❌ Please provide a link token.\n"
            "Format: /link <your-token>\n\n"
            "Get your token from the CryptoMind website."
        ),
        "link_success": "✅ Bound successfully!\n\nTelegram account linked to: @{username}\n\nSend me a message and I'll analyze the market for you!",
        "link_brief_on": "☀️ Your daily brief is on: it arrives here every day at {hour} local time (holdings, calendar, how your calls did). Reply /brief off to stop it.",
        "link_brief_off": "☀️ Want a daily brief every morning? Reply /brief on to turn it on.",
        "link_confirm_prompt": "🔗 Link this Telegram to the CryptoMind account:\n{target}",
        "link_confirm_move": "⚠️ This will move your Telegram from account {current} to {target}.",
        "link_confirm_warning": (
            "⚠️ Only confirm if you started this from your own browser. "
            "If someone sent you this link, tap Cancel: confirming would let them "
            "read your chats here on the website.\n"
            "(Valid for {minutes} min)"
        ),
        "link_confirm_btn": "✅ Confirm",
        "link_cancel_btn": "✖️ Cancel",
        "link_cancelled": "Cancelled. Nothing was linked.",
        "LINK_NOT_YOURS": "This confirmation is not for you. Only the person who opened the link can use it.",
        "link_failed": "❌ Binding failed: {detail}",
        "unlink_redirect": (
            "ℹ️ Unbinding must be done on the website.\n\n"
            "Go to the CryptoMind website → Account Settings → "
            "Telegram binding settings to unbind."
        ),
        "NOT_BOUND": "⚠️ Please bind your account first using /link <token>.",
        "RATE_LIMITED": "⚠️ Too many requests, please try again later.",
        "ALREADY_LINKED": "This Telegram account is already linked and cannot be bound again.",
        "INVALID_TOKEN": "Link token is invalid or expired. Please get a new one from the website.",
        "USER_NOT_ACTIVE": "Your account status is abnormal. Please contact support.",
        "NO_API_KEY": (
            "⚠️ You haven't configured an LLM API Key.\n\n"
            "Please go to the CryptoMind website → Settings and set up any API Key:\n"
            "  • OpenAI\n  • OpenRouter\n  • Google AI\n\n"
            "AI analysis will be available once configured."
        ),
        "INVALID_API_KEY": "⚠️ Invalid API Key. Please check your API Key in the website settings.",
        "ANALYSIS_FAILED": "⚠️ Analysis failed. Please try again later.",
        "timeout": "⏳ Response is taking too long. The server is busy — please try again later.",
        "link_timeout": "⚠️ Connection timed out. Please try again.",
        "unknown_error": "⚠️ An unknown error occurred. Please try again or contact support.",
        "NO_PENDING_HITL": "That card has expired — please say it again.",
        "empty_response": "(Unable to generate a response. Please try again later.)",
        "analyzing": "🌀 Analyzing...",
        "media_unsupported": (
            "📷 Sorry, image / voice / file analysis isn't supported yet.\n"
            "Please describe your question in text (e.g. \"Analyze BTC trend\") "
            "and I'll take it from there!"
        ),
        "image_default_prompt": "Please analyze this image in detail. If it is a chart / position / market-related screenshot, interpret it from an investment perspective.",
        "image_too_large": "📸 Image exceeds the 4MB limit. Please compress it and try again (sending as a Telegram photo compresses automatically).",
        "vision_failed": "⚠️ Image analysis failed. Please try again later or ask in text.",
        "VISION_UNSUPPORTED_MODEL": "🚫 This model doesn't support image analysis — switch to a vision-capable model (e.g. GPT-4o / Gemini / Claude), or describe it in text.",
        "VISION_IMAGE_TOO_LARGE": "📸 Image exceeds the 4MB limit. Please compress and retry.",
        "VISION_INVALID_FORMAT": "📸 Only PNG / JPEG / WebP images are supported.",
        "VISION_DISABLED": "📷 Image analysis is currently disabled.",
    },
}


def t(key: str, language: Optional[str] = None, **kwargs) -> str:
    """Look up a translated message.

    Falls back to ``DEFAULT_LANGUAGE`` if ``language`` is missing or unknown,
    then to the raw ``key`` if the key itself is missing.
    """
    lang = language if language and language in MESSAGES else DEFAULT_LANGUAGE
    text = MESSAGES[lang].get(key, MESSAGES[DEFAULT_LANGUAGE].get(key, key))
    return text.format(**kwargs) if kwargs else text
