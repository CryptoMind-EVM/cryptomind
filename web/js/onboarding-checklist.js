// ========================================
// onboarding-checklist.js — 登入用戶聊天首頁的新手三步（PR-7，2026-09-27）
// 設計：docs/plans/2026-09-27-launch-readiness-design.md §5
//
// 1. 記第一筆持倉或同步 Base 錢包  2. 打開每日早報（Telegram 一鍵／站內）  3. 記下第一個判斷
// 完成狀態只打一支 GET /api/user/onboarding-status（從既有資料推，不存任何東西）；
// 三步都完成就不顯示；按關閉記在 localStorage（只是這台裝置的便利設定）。
// 按鈕走 click-delegator：data-click="OnboardingChecklist.<方法>"（allowedRoots 已列）。
// ========================================

const DISMISS_KEY = 'cm_onboarding_dismissed_v1';
const CARD_ID = 'onboarding-checklist';
// 開頁時 showWelcomeScreen 會被叫好幾次（auth:ready、語言切換…），60 秒內共用同一份結果
const STATUS_TTL_MS = 60 * 1000;

let _cache = null; // { at, status }
let _inflight = null;

function _t(key, fallback, vars) {
    const value = window.I18n ? window.I18n.t(key, vars) : '';
    return value && value !== key ? value : fallback;
}

function _isDismissed() {
    try {
        return localStorage.getItem(DISMISS_KEY) === '1';
    } catch (_) {
        return false; // 無痕／封鎖儲存：照常顯示
    }
}

function _fetchStatus(force) {
    if (!force && _cache && Date.now() - _cache.at < STATUS_TTL_MS) {
        return Promise.resolve(_cache.status);
    }
    if (_inflight) return _inflight;
    _inflight = AppAPI.get('/api/user/onboarding-status')
        .then((res) => {
            const status = res && res.status ? res.status : null;
            if (status) _cache = { at: Date.now(), status };
            return status;
        })
        .catch(() => null) // 拿不到就不顯示清單，不打擾聊天首頁
        .finally(() => {
            _inflight = null;
        });
    return _inflight;
}

// 條款同意卡（legal-consent.js）顯示中就不畫：它是 fixed 浮層，桌機會蓋住這張清單的下半，
// 兩張卡疊在一起很醜。同意卡是必要的，所以清單讓路，同意後再出現（見檔尾的事件監聽）。
function _legalPending() {
    return window.LegalConsent?.isPending?.() === true;
}

// 歡迎畫面還在才畫（使用者可能已經開始聊天，#chat-messages 換成訊息了）
function _welcomeRoot() {
    const greeting = document.getElementById('welcome-greeting');
    return greeting ? greeting.closest('.market-workspace') : null;
}

const PRIMARY_BTN =
    'px-3 py-1.5 rounded-full bg-primary text-background text-xs font-bold hover:opacity-90 transition';
const SECONDARY_BTN =
    'px-3 py-1.5 rounded-full bg-surfaceHighlight/60 hover:bg-surfaceHighlight text-xs text-textMuted hover:text-secondary transition border border-borderSubtle';

function _step(n, done, title, desc, actions) {
    const badge = done
        ? `<span class="w-6 h-6 rounded-full bg-success/10 text-success flex items-center justify-center shrink-0"><i data-lucide="check" class="w-3 h-3"></i></span>`
        : `<span class="w-6 h-6 rounded-full bg-primary/20 text-primary text-xs font-bold flex items-center justify-center shrink-0">${n}</span>`;
    const body = done
        ? ''
        : `<p class="text-xs text-textMuted mt-1 leading-relaxed">${desc}</p>
           <div class="flex flex-wrap gap-2 mt-2">${actions}</div>`;
    return `
        <li class="flex items-start gap-3">
            ${badge}
            <div class="min-w-0 flex-1">
                <p class="text-sm font-medium ${done ? 'text-textMuted line-through' : 'text-textMain'}">${title}</p>
                ${body}
            </div>
        </li>`;
}

function _cardHtml(status) {
    const doneCount = [status.holding, status.brief, status.call].filter(Boolean).length;
    const briefActions = status.telegram_bound
        ? `<button data-click="OnboardingChecklist.openBriefSettings" class="${PRIMARY_BTN}">${_t('onboarding.briefTurnOn', 'Turn it on in Settings')}</button>`
        : `<button data-click="OnboardingChecklist.connectTelegram" class="${PRIMARY_BTN}">${_t('onboarding.briefConnectTelegram', 'Connect Telegram')}</button>
           <button data-click="OnboardingChecklist.openBriefSettings" class="${SECONDARY_BTN}">${_t('onboarding.briefInApp', 'Get it in-app instead')}</button>`;
    return `
    <div id="${CARD_ID}" class="w-full max-w-md mt-4 rounded-2xl border border-borderSubtle bg-surface px-4 py-3 text-left">
        <div class="flex items-center justify-between gap-3 mb-3">
            <p class="text-sm font-bold text-textMain">${_t('onboarding.title', 'Get set up in 3 steps')}
                <span class="text-xs font-normal text-textMuted whitespace-nowrap">${_t('onboarding.progress', `${doneCount}/3 done`, { done: doneCount })}</span>
            </p>
            <button data-click="OnboardingChecklist.dismiss" class="w-7 h-7 rounded-lg hover:bg-surfaceHighlight flex items-center justify-center transition shrink-0"
                aria-label="${_t('onboarding.dismiss', 'Hide setup steps')}" title="${_t('onboarding.dismiss', 'Hide setup steps')}">
                <i data-lucide="x" class="w-4 h-4 text-textMuted"></i>
            </button>
        </div>
        <ol class="space-y-3">
            ${_step(
                1,
                status.holding,
                _t('onboarding.holdingTitle', 'Add your first holding or sync a Base wallet'),
                _t('onboarding.holdingDesc', 'Your morning brief and the AI answers are built around what you hold.'),
                `<button data-click="OnboardingChecklist.openJournal" class="${PRIMARY_BTN}">${_t('onboarding.holdingAction', 'Open Investment Journal')}</button>`
            )}
            ${_step(
                2,
                status.brief,
                _t('onboarding.briefTitle', 'Turn on your morning brief'),
                _t('onboarding.briefDesc', 'Every morning: your holdings, upcoming events and how your calls did. One tap on Telegram, or read it in-app.'),
                briefActions
            )}
            ${_step(
                3,
                status.call,
                _t('onboarding.callTitle', 'Record your first market call'),
                _t('onboarding.callDesc', 'Tell the AI your view, e.g. “I’m bullish on BTC for the next 30 days, record my call”. You confirm it on a card; it’s scored against the market when it expires.'),
                `<button data-click="fillChatExample" data-click-arg="onboarding.callExamplePrompt" data-fallback="I'm bullish on BTC for the next 30 days, record my call" class="${SECONDARY_BTN}">${_t('onboarding.callAction', 'Try this example')}</button>`
            )}
        </ol>
    </div>`;
}

function _render(status) {
    const existing = document.getElementById(CARD_ID);
    if (existing) existing.remove();
    if (!status || _isDismissed() || _legalPending()) return;
    if (status.holding && status.brief && status.call) return; // 全部完成就消失
    const root = _welcomeRoot();
    if (!root) return;
    root.insertAdjacentHTML('beforeend', _cardHtml(status));
    const card = document.getElementById(CARD_ID);
    if (card && window.AppUtils) window.AppUtils.refreshIcons(card);
}

export async function mountOnboardingChecklist() {
    if (_isDismissed() || !window.AuthManager?.isLoggedIn()) return;
    const status = await _fetchStatus(false);
    _render(status);
}

const OnboardingChecklist = {
    dismiss() {
        try {
            localStorage.setItem(DISMISS_KEY, '1');
        } catch (_) {
            // 存不進去就只收這一次
        }
        const card = document.getElementById(CARD_ID);
        if (card) card.remove();
    },

    openJournal() {
        _cache = null; // 回來時重抓（可能已經記了一筆）
        if (typeof window.switchTab === 'function') window.switchTab('journal');
    },

    openBriefSettings() {
        _cache = null;
        if (typeof window.switchTab === 'function') window.switchTab('settings');
        // Settings 分頁是 lazy 注入，等一下再捲到早報卡
        setTimeout(() => {
            const card = document.getElementById('settings-brief-card');
            if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 400);
    },

    async connectTelegram() {
        try {
            const mod = await import('./telegram-link.js');
            await mod.TelegramLinkApp.handleConnect();
        } catch (e) {
            console.warn('[Onboarding] connect telegram failed', e);
            if (typeof window.showToast === 'function') {
                window.showToast(_t('telegram.connect_failed', 'Could not create a link'), 'error');
            }
        }
    },

    async refresh() {
        const status = await _fetchStatus(true);
        _render(status);
    },
};

// 在 Telegram 按「開始」綁好 → 早報那步可能已經完成，重抓一次
window.addEventListener('telegram:bound', () => OnboardingChecklist.refresh());

// 同意卡出現 → 把已經畫出的清單收起；同意後 → 再畫回來（按過關閉的 _render 自己會擋）
window.addEventListener('legal:consent-pending', (e) => {
    if (e.detail && e.detail.pending) {
        const card = document.getElementById(CARD_ID);
        if (card) card.remove();
    } else {
        mountOnboardingChecklist();
    }
});

window.OnboardingChecklist = OnboardingChecklist;
export { OnboardingChecklist };
