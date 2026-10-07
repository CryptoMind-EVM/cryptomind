// ========================================
// ton-auth.js — Telegram Mini App 原生登入（檔名是 TON 時代沿用）
//
// 2026-08-31 起錢包登入統一走 EVM（evm-auth.js）；2026-09-08 刪掉 TON 錢包登入鏈；
// 2026-09-26 付款全部改成 Base USDC 之後，TON Connect 單例（window.tonConnectUI）
// 也沒有呼叫端了，連同 index.html 的 TON Connect SDK／tonweb 一起移除。
// 這支現在只剩 Telegram 登入（initData）。
// ========================================

// ---- Telegram 原生登入（Mini App initData）----
// 在 Telegram Mini App 內,Telegram 已驗證使用者身分(initData),不需錢包簽章
// 即可登入。這是 Mini App 最可靠的登入方式:免 modal、免錢包、免重載。
function _applyTelegramSession(syncResult) {
    const A = window.AuthManager;
    if (!A || !syncResult || !syncResult.user) return;
    A.currentUser = {
        uid: syncResult.user.user_id,
        user_id: syncResult.user.user_id,
        username: syncResult.user.username,
        accessTokenExpiry: Date.now() + (A.TOKEN_EXPIRY_MS || 86400000),
        authMethod: syncResult.user.auth_method || 'telegram',
        role: syncResult.user.role || 'user',
        membership_tier: syncResult.user.membership_tier || 'free',
        has_wallet: syncResult.user.has_wallet === true,
        wallet_address: null,
    };
    A._saveUserSession();
    if (typeof A.markRecentLoginSuccess === 'function') A.markRecentLoginSuccess();
    if (typeof A.startTokenRefreshTimer === 'function') A.startTokenRefreshTimer();
    window.dispatchEvent(new Event('auth-success'));
}

async function handleTelegramLogin() {
    const WebApp = window.Telegram && window.Telegram.WebApp;
    const initData = WebApp && WebApp.initData;
    if (!initData) {
        return { success: false, reason: 'no-initdata' };
    }
    try {
        const res = await AppAPI.post('/api/user/telegram-login', { init_data: initData });
        if (res && res.success) {
            _applyTelegramSession(res);
            return { success: true, user: window.AuthManager.currentUser };
        }
        return { success: false, reason: (res && res.detail) || 'failed' };
    } catch (e) {
        return { success: false, reason: (e && e.message) || 'error' };
    }
}
window.handleTelegramLogin = handleTelegramLogin;

// ---- 登入狀態小工具 ----
function _isLoggedIn() {
    const A = window.AuthManager;
    return !!(A && typeof A.isLoggedIn === 'function' && A.isLoggedIn());
}

// ---- 登入成功後的共用 UI（目前只剩 Telegram 登入使用）----
// Telegram 自動登入跑在 i18n 字典載完之前，I18n.t 這時只會回 key；
// 有翻譯用翻譯、沒有就用英文，別把 "tonAuth.loginSuccess" 原字印給使用者（2026-09-13 TMA 實測）。
function _t(key, fallback) {
    if (window.I18n && typeof window.I18n.t === 'function') {
        const v = window.I18n.t(key);
        if (v && v !== key) return v;
    }
    return fallback;
}

let _lastLoginSuccessToastAt = 0;
function _onLoginSuccessUI() {
    const modal = document.getElementById('login-modal');
    if (modal) modal.classList.add('hidden');
    if (window.AppStore) AppStore.set('forceGuestLandingTab', false);
    if (window.AuthManager && typeof AuthManager._updateUI === 'function') {
        AuthManager._updateUI(true);
    }
    const now = Date.now();
    if (now - _lastLoginSuccessToastAt > 3000) {
        _lastLoginSuccessToastAt = now;
        if (typeof showToast === 'function')
            showToast(_t('tonAuth.loginSuccess', 'Login successful!'), 'success');
    }
}

// ---- Telegram Mini App 自動登入 ----
// 在 Mini App 內,只要 Telegram 提供了 initData 且使用者尚未登入,就用 Telegram
// 身分自動登入——不開錢包、不彈 modal。這也讓「重載後被登出」能自我修復:任何
// 一次 AuthManager 判定未登入,都會立刻用 initData 重新登入。錢包只在付款時才連。
// 手動 Telegram 登入按鈕(自動登入失敗時的後備),含 loading 狀態。
window.safeTelegramLogin = async function () {
    const btn = document.getElementById('tg-login-btn');
    const original = btn ? btn.innerHTML : '';
    try {
        if (btn) {
            btn.disabled = true;
            btn.classList.add('opacity-70', 'cursor-not-allowed');
        }
        const res = await handleTelegramLogin();
        if (res && res.success) {
            _onLoginSuccessUI();
        } else if (typeof showToast === 'function') {
            showToast(
                _t('tonAuth.loginFailedShort', 'Login failed, please retry'),
                'error'
            );
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.classList.remove('opacity-70', 'cursor-not-allowed');
            btn.innerHTML = original;
        }
    }
};

// 在 Mini App 內顯示「用 Telegram 登入」按鈕(一般瀏覽器維持只有錢包登入)。
function _revealTelegramLoginButton() {
    const WebApp = window.Telegram && window.Telegram.WebApp;
    const inMiniApp = !!(WebApp && WebApp.platform && WebApp.platform !== 'unknown' && WebApp.initData);
    if (!inMiniApp) return;
    const btn = document.getElementById('tg-login-btn');
    if (btn) btn.style.display = '';
}

let _tgAutoLoginTried = false;
async function _tryTelegramAutoLogin(reason) {
    if (_isLoggedIn()) return;
    const WebApp = window.Telegram && window.Telegram.WebApp;
    const inMiniApp = !!(
        WebApp && WebApp.platform && WebApp.platform !== 'unknown' && WebApp.initData
    );
    if (!inMiniApp) return; // 一般瀏覽器 → 走 EVM 登入（evm-auth.js）
    const res = await handleTelegramLogin();
    if (res.success) {
        _onLoginSuccessUI();
    } else if (!_tgAutoLoginTried && res.reason && res.reason !== 'no-initdata') {
        _tgAutoLoginTried = true;
        if (typeof showToast === 'function') {
            showToast(
                _t('tonAuth.loginFailedShort', 'Login failed, please retry'),
                'error'
            );
        }
    }
}
// AuthManager init 完成後若仍未登入 → 試 Telegram 自動登入(含重載後被登出的自我修復)。
window.addEventListener('auth:initialized', (e) => {
    if (e && e.detail && e.detail.isLoggedIn) return;
    _tryTelegramAutoLogin('auth:initialized');
});
// 後備:若 auth:initialized 在監聽掛上前就觸發過,開機時也試一次。
(function bootTelegramAutoLogin() {
    const run = () => {
        _revealTelegramLoginButton();
        // 已有本地 session 就交給 AuthManager 還原;它若失敗登出會再觸發上面的事件。
        if (localStorage.getItem('ton_user')) return;
        _tryTelegramAutoLogin('boot');
    };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', run, { once: true });
    } else {
        run();
    }
})();


// 測試模式區塊（沿用）— 改用共用 getAppConfig()，避免首頁載入時重複 fetch /api/config
if (typeof AppAPI !== 'undefined' && AppAPI.getAppConfig) {
    AppAPI.getAppConfig()
        .then((cfg) => {
            if (cfg && cfg.test_mode) {
                const devArea = document.getElementById('dev-login-area');
                if (devArea) devArea.style.display = 'block';
            }
        })
        .catch(() => {});
}

export {};
