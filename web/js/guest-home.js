// ========================================
// guest-home.js - 訪客聊天首頁（hero）
// 2026-09-27 上市準備 PR-2（docs/plans/2026-09-27-launch-readiness-design.md §2 ①）：
// 未登入訪客 5 秒內看懂這是什麼——標題、副標、三張功能卡、三個入口；問第一題就收起。
// 登入用戶不經過這裡（chat-sessions.js showWelcomeScreen 開頭分流），畫面維持原樣。
// 按鈕一律走 click-delegator 的 data-click（prod CSP 擋 inline handler）。
// ========================================

const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ESC_MAP[c]);
}

// 有翻譯用翻譯，沒有（i18n 還沒好、key 缺）就用英文——英文是主力客群的語言
function translate(key, fallback) {
    if (window.I18n && typeof window.I18n.t === 'function') {
        const value = window.I18n.t(key);
        if (value && value !== key) return value;
    }
    return fallback;
}

function currentLanguage() {
    try {
        return (window.I18n && window.I18n.getLanguage && window.I18n.getLanguage()) || 'en';
    } catch (_e) {
        return 'en';
    }
}

const CHIP_BTC = { label: 'chat.quickBTCAnalysis', labelFallback: 'BTC Analysis', prompt: 'chat.examplePromptBtc', promptFallback: 'BTC technical analysis' };
const CHIP_ETHSOL = { label: 'chat.quickETHSOLAnalysis', labelFallback: 'ETH vs SOL', prompt: 'chat.examplePromptEthSol', promptFallback: 'ETH vs SOL comparison' };
const CHIP_NVDA = { label: 'chat.quickNVDAAnalysis', labelFallback: 'NVDA ahead of earnings', prompt: 'chat.examplePromptNvda', promptFallback: 'NVDA trend and what to watch in the next earnings report' };
const CHIP_TSMC = { label: 'chat.quickTSMCAnalysis', labelFallback: 'TSMC Trend', prompt: 'chat.examplePromptTsmc', promptFallback: 'TSMC latest trend' };

/**
 * 範例問題 chip（對外定位：美股、台股、加密貨幣）。
 * 中文介面一個市場一題：BTC、NVDA、台積電；其他語言維持 BTC、ETH vs SOL、NVDA
 * （英文主力客群是加密貨幣＋美股投資人）。
 * 加密貨幣旗標關閉（Play 版、CRYPTO_TAB_ENABLED=false）時不放加密貨幣的問題，免得首屏推的東西點進去沒有分頁。
 * @param {string} lang
 * @param {{ crypto?: boolean }} [opts]
 */
export function guestChipItems(lang, opts = {}) {
    const zh = String(lang || '').toLowerCase().startsWith('zh');
    if (opts.crypto === false) return [CHIP_NVDA, CHIP_TSMC];
    return zh ? [CHIP_BTC, CHIP_NVDA, CHIP_TSMC] : [CHIP_BTC, CHIP_ETHSOL, CHIP_NVDA];
}

const CTA_BASE =
    'w-full sm:w-auto inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-full ' +
    'text-sm font-semibold whitespace-nowrap transition';

function featureCard(icon, title, desc) {
    return `
        <div class="flex sm:flex-col items-start gap-3 rounded-2xl border border-borderSubtle bg-surface p-3.5 sm:p-4">
            <span class="w-9 h-9 shrink-0 rounded-xl bg-primary/10 text-primary flex items-center justify-center">
                <i data-lucide="${esc(icon)}" class="w-4 h-4"></i>
            </span>
            <div class="min-w-0">
                <p class="text-sm font-semibold text-textMain">${esc(title)}</p>
                <p class="mt-0.5 text-xs leading-relaxed text-textMuted">${esc(desc)}</p>
            </div>
        </div>`;
}

/**
 * 訪客首頁標記（純字串，node 測試可直接跑）。
 * 標題與副標不套 .welcome-title／.welcome-sub 的延遲淡入：這是訪客第一眼要看懂的內容，
 * 晚 0.5 秒才出現只會拖慢首屏（語言切換重畫改由 chat-sessions.js 認 #guest-home）。
 * @param {(key: string, fallback: string) => string} t 翻譯函式
 * @param {string} lang 目前語言（決定範例問題）
 * @param {{ crypto?: boolean }} [opts] crypto:false＝加密貨幣分頁關閉（不放加密貨幣的範例問題）
 */
export function guestWelcomeHtml(t, lang, opts = {}) {
    const chips = guestChipItems(lang, opts)
        .map(
            (c) => `
            <button type="button" data-click="fillChatExample" data-click-arg="${esc(c.prompt)}" data-fallback="${esc(c.promptFallback)}"
                class="px-3 py-1.5 rounded-full bg-surfaceHighlight/60 hover:bg-surfaceHighlight text-xs text-textMuted hover:text-secondary transition border border-borderSubtle">
                ${esc(t(c.label, c.labelFallback))}
            </button>`
        )
        .join('');

    return `
    <div id="guest-home" class="w-full max-w-3xl mx-auto pt-2 pb-6 md:px-4 md:py-8 md:min-h-[72dvh] md:justify-center flex flex-col items-center text-center gap-6 md:gap-8">
        <div class="flex flex-col items-center gap-4">
            <div class="flex items-center gap-2">
                <img src="/static/img/title_icon.png" alt="" width="28" height="28" class="w-7 h-7 rounded-lg object-cover">
                <span class="brand-mark text-sm font-semibold tracking-tight text-textMain">CryptoMind</span>
            </div>
            <h1 class="h1 text-textMain max-w-2xl text-balance">${esc(t('guestHome.title', 'The AI analyst that knows your portfolio.'))}</h1>
            <p class="text-[15px] md:text-base leading-relaxed text-textMuted max-w-xl">${esc(t('guestHome.subtitle', 'Track crypto & US stocks in one ledger. Every morning, get a brief on what matters to your positions — earnings, token unlocks, macro. Make calls, see how right you were.'))}</p>
        </div>

        <div class="w-full sm:w-auto flex flex-col sm:flex-row items-stretch sm:items-center justify-center gap-2">
            <button type="button" data-click="focusChatInput"
                class="${CTA_BASE} bg-primary text-background hover:brightness-110 shadow-[0_8px_24px_rgba(37,99,235,.25)]">
                <i data-lucide="message-circle" class="w-4 h-4"></i>
                <span>${esc(t('guestHome.ctaAsk', 'Ask the AI'))}</span>
            </button>
            <button type="button" data-click="switchTab" data-click-arg="sample"
                class="${CTA_BASE} bg-primary/10 text-primary border border-primary/20 hover:bg-primary/15">
                <i data-lucide="briefcase" class="w-4 h-4"></i>
                <span>${esc(t('guestHome.ctaSample', 'See a sample portfolio'))}</span>
            </button>
            <button type="button" data-click="openLoginModal"
                class="${CTA_BASE} border border-borderLight text-textMain hover:bg-surfaceHighlight">
                <span>${esc(t('guestHome.ctaSignIn', 'Sign in'))}</span>
            </button>
        </div>

        <div class="welcome-actions flex flex-col items-center gap-2">
            <p class="text-xs text-textMuted">${esc(t('guestHome.tryAsking', 'Try asking'))}</p>
            <div class="flex flex-wrap items-center justify-center gap-2">${chips}</div>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-3 gap-2.5 sm:gap-3 w-full text-left">
            ${featureCard('sunrise', t('guestHome.featureBriefTitle', 'Morning brief'), t('guestHome.featureBriefDesc', 'Earnings, token unlocks and macro events that hit your positions, every morning.'))}
            ${featureCard('wallet', t('guestHome.featureLedgerTitle', 'One ledger + Base wallet sync'), t('guestHome.featureLedgerDesc', 'Crypto and US stocks side by side. Base holdings sync on-chain.'))}
            ${featureCard('target', t('guestHome.featureCallsTitle', 'Call scorecard'), t('guestHome.featureCallsDesc', 'Log a call, and it gets scored against the real close.'))}
        </div>
    </div>`;
}

/** 新加入的節點是不是使用者送出的訊息列（chat-state.js buildMessageRow）。 */
export function isUserMessageNode(node) {
    return !!(node && node.nodeType === 1 && node.classList && node.classList.contains('chat-row-user'));
}

// 訪客送出第一題就收起 hero：看 #chat-messages 出現使用者訊息列。
// 不改 chat-analysis.js 的送出流程——訪客聊天的請求端另有改動，這裡只觀察結果。
// 只看直接子節點：appendMessage（chat-state.js）把訊息列直接掛在 #chat-messages 底下。
let _observer = null;
function collapseOnFirstMessage(container) {
    if (_observer) _observer.disconnect();
    _observer = null;
    if (typeof MutationObserver !== 'function') return;
    _observer = new MutationObserver((mutations) => {
        const hero = document.getElementById('guest-home');
        const sent = mutations.some((m) => Array.from(m.addedNodes).some(isUserMessageNode));
        if (hero && sent) {
            hero.remove();
            // hero 畫出來時放掉了貼底（見 renderGuestWelcome）；開始對話就接回來，讓回答跟著捲
            if (typeof window.resetChatStickToBottom === 'function') window.resetChatStickToBottom();
            if (typeof window.stickChatToBottom === 'function') window.stickChatToBottom(true);
        }
        if (!hero || sent) {
            _observer.disconnect();
            _observer = null;
        }
    });
    _observer.observe(container, { childList: true });
}

/** 把訪客首頁畫進 #chat-messages（chat-sessions.js showWelcomeScreen 呼叫）。 */
export function renderGuestWelcome(container) {
    const cryptoOff = !!(window.NavPreferences && window.NavPreferences.isFlagOff('crypto_tab'));
    container.innerHTML = guestWelcomeHtml(translate, currentLanguage(), { crypto: !cryptoOff });
    // hero 比一屏高：chat-state.js 的貼底錨定會在內容變動（含圖示渲染）時把捲動拉到最底，
    // 訪客第一眼只看到功能卡、看不到標題。先放掉貼底再捲回頂端；送出第一題時接回來。
    if (typeof window.releaseChatStickToBottom === 'function') window.releaseChatStickToBottom();
    container.scrollTop = 0;
    if (typeof window.createIconsIn === 'function') window.createIconsIn(container);
    collapseOnFirstMessage(container);
}

// 登入後 hero 不能留著（上面寫著 Sign in）：還停在訪客首頁、沒開始對話的話，走登入用戶的
// 聊天初始化（還原上次的對話或顯示登入版歡迎畫面）。initChat 自己有重入守衛。
function onAuthChanged() {
    if (!document.getElementById('guest-home')) return;
    if (!(window.AuthManager && window.AuthManager.isLoggedIn())) return;
    if (typeof window.initChat === 'function') window.initChat();
}

if (typeof window.addEventListener === 'function') {
    window.addEventListener('auth:changed', onAuthChanged);
}

window.renderGuestWelcome = renderGuestWelcome;
