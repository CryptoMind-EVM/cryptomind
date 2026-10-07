/**
 * 分享連結：把一個問題變成 `/?ask=<問題>` 的連結。
 * 對方點開後問題會預填進輸入框（不自動送出，避免連結被拿來刷訪客額度），
 * 訪客模式即可直接按送出體驗。不存任何資料。
 */
const ASK_MAX_LEN = 200;

const t = (key, fallback) => (window.I18n ? window.I18n.t(key) : fallback);

export function cleanAsk(raw) {
    return Array.from(
        String(raw || '')
            // eslint-disable-next-line no-control-regex
            .replace(/[\u0000-\u001f\u007f]/g, ' ')
            .replace(/\s+/g, ' ')
            .trim()
    )
        .slice(0, ASK_MAX_LEN)
        .join('');
}

// Telegram Mini App 直連（2026-10-05）：t.me/<bot>/<short_name>?startapp=ask_<base64url(問題)>。
// startapp 只收 A-Za-z0-9_-、最長 64 字；問題太長編不下就退回網頁連結（不截斷，截了就不是原本的問題）。
const START_PREFIX = 'ask_';
const START_MAX_LEN = 64;
const TG_NAME = /^[A-Za-z0-9_]+$/;

export function encodeStartParam(question) {
    const q = cleanAsk(question);
    if (!q) return null;
    let bin = '';
    new TextEncoder().encode(q).forEach((byte) => {
        bin += String.fromCharCode(byte);
    });
    const param = START_PREFIX + btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    return param.length <= START_MAX_LEN ? param : null;
}

/** start param → 問題。不是 ask_ 開頭（別的功能用）或壞掉的一律回空字串。 */
export function decodeStartParam(param) {
    if (typeof param !== 'string' || !param.startsWith(START_PREFIX)) return '';
    const body = param.slice(START_PREFIX.length);
    if (!/^[A-Za-z0-9_-]+$/.test(body)) return '';
    try {
        const bin = atob(body.replace(/-/g, '+').replace(/_/g, '/'));
        const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
        return cleanAsk(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
    } catch (_err) {
        return '';
    }
}

/** Telegram 開 Mini App 帶的 start param：優先讀 SDK，沒有才從網址 hash 的 tgWebAppStartParam 讀。只讀，不碰登入。 */
export function readStartParam() {
    const fromSdk = window.Telegram?.WebApp?.initDataUnsafe?.start_param;
    if (fromSdk) return String(fromSdk);
    try {
        return new URLSearchParams(window.location.hash.replace(/^#/, '')).get('tgWebAppStartParam') || '';
    } catch (_err) {
        return '';
    }
}

/**
 * 問題的分享連結。opts.telegram（/api/config 的 telegram_miniapp）有給、編得下才用 t.me 直連，
 * 其餘一律網頁連結。
 */
export function buildAskLink(question, opts = {}) {
    const q = cleanAsk(question);
    if (!q) return window.location.origin + '/';
    const tg = opts.telegram;
    if (tg && TG_NAME.test(tg.bot_username || '') && TG_NAME.test(tg.short_name || '')) {
        const param = encodeStartParam(q);
        if (param) return `https://t.me/${tg.bot_username}/${tg.short_name}?startapp=${param}`;
    }
    return `${window.location.origin}/?ask=${encodeURIComponent(q)}&utm_source=share`;
}

// 只有在 Telegram 裡分享才用 t.me 直連：貼到 X、LINE 的 t.me 連結會把沒有 Telegram 的人帶到安裝頁
async function telegramShareConfig() {
    if (window.CMPlatform?.get?.() !== 'tma') return null;
    try {
        const cfg = await window.AppAPI.getAppConfig();
        return cfg?.telegram_miniapp || null;
    } catch (_err) {
        return null;
    }
}

export async function shareAsk(question) {
    const url = buildAskLink(question, { telegram: await telegramShareConfig() });
    const title = 'CryptoMind';
    const text = cleanAsk(question);
    try {
        if (navigator.share) {
            await navigator.share({ title, text, url });
            return;
        }
    } catch (err) {
        if (err && err.name === 'AbortError') return; // 使用者自己取消
    }
    try {
        await navigator.clipboard.writeText(url);
        window.showToast?.(t('chat.shareCopied', 'Link copied'), 'success');
    } catch (_err) {
        window.prompt(t('chat.shareCopyManual', 'Copy this link'), url);
    }
}

/** 在一則 AI 回答底下加「分享」按鈕。 */
export function appendShareButton(el, question) {
    if (!el || !cleanAsk(question)) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className =
        'mt-3 inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border border-borderSubtle text-textMuted hover:text-primary hover:border-primary/40 transition';
    btn.innerHTML = `<i data-lucide="share-2" class="w-3.5 h-3.5"></i><span></span>`;
    btn.querySelector('span').textContent = t('chat.share', 'Share this question');
    btn.addEventListener('click', () => shareAsk(question));
    el.appendChild(btn);
    if (window.lucide) window.lucide.createIcons({ nodes: [btn] });
    addAnswerShareButton(el, question);
}

/**
 * 「分享這則回答」（AI 回答快照，旗標 CONVERSATION_SHARE_ENABLED，預設關）：只有登入用戶、後端旗標開、
 * 而且這段對話存在伺服器上（有 session id）才出現。內容由伺服器依 session＋問題取，不從這裡送答案。
 */
async function addAnswerShareButton(el, question) {
    const sessionId = window.currentSessionId;
    if (!sessionId || !cleanAsk(question)) return;
    if (!(window.AuthManager && window.AuthManager.isLoggedIn && window.AuthManager.isLoggedIn())) return;
    try {
        const cfg = await window.AppAPI.getAppConfig();
        if (!cfg || cfg.answer_share !== true || !el.isConnected) return;
    } catch (_err) {
        return;
    }
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className =
        'mt-3 ml-2 inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border border-borderSubtle text-textMuted hover:text-primary hover:border-primary/40 transition';
    btn.innerHTML = `<i data-lucide="link" class="w-3.5 h-3.5"></i><span></span>`;
    btn.querySelector('span').textContent = t('answerShare.button', 'Share this answer');
    btn.addEventListener('click', async () => {
        const { openAnswerShare } = await import('./answer-share.js');
        openAnswerShare({ sessionId, question: String(question) }); // 原文：伺服器用同一個清理規則比對
    });
    el.appendChild(btn);
    if (window.lucide) window.lucide.createIcons({ nodes: [btn] });
}

/** 落地：讀 ?ask=（或 Telegram 的 start param），預填輸入框並顯示一行說明。 */
export function initAskDeepLink() {
    const params = new URLSearchParams(window.location.search);
    const q = cleanAsk(params.get('ask')) || decodeStartParam(readStartParam());
    if (!q) return;
    if (params.has('ask')) {
        // 網址只留乾淨版本，重新整理不會重複預填（start param 是 Telegram 自己的，不動網址）
        params.delete('ask');
        params.delete('utm_source');
        const rest = params.toString();
        history.replaceState(
            history.state,
            '',
            window.location.pathname + (rest ? `?${rest}` : '') + window.location.hash
        );
    }

    let tries = 0;
    const timer = setInterval(() => {
        tries += 1;
        const input = document.getElementById('user-input');
        if (input && !input.dataset.askPrefilled) {
            input.dataset.askPrefilled = '1';
            input.value = q;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            const note = document.createElement('div');
            note.className = 'text-xs text-primary mb-2 px-1';
            note.textContent = t(
                'chat.sharedQuestionNotice',
                'A friend shared this question. Press send to see the AI answer.'
            );
            const row = input.closest('.chat-input-row');
            if (row) row.prepend(note);
            // 送出之後問題已經在對話裡，這行提示就沒意義了
            const dismiss = () => note.remove();
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') dismiss();
            });
            document.getElementById('send-btn')?.addEventListener('click', dismiss);
            if (!input.disabled) input.focus();
            clearInterval(timer);
        } else if (tries > 50) {
            clearInterval(timer);
        }
    }, 200);
}

initAskDeepLink();
