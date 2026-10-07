// 舊 TON 身份提醒（2026-09-25）
// TON 登入已拔除：user_id 是 TON 地址的舊帳號只剩 refresh token 撐著（30 天輪替），
// 過期就再也進不來。/api/user/me 對「TON 身份且沒綁 EVM 錢包／Google」回
// login_backup_needed=true；這裡顯示一次性、可關閉的橫幅（#legacy-ton-banner），
// 綁定沿用既有流程：EVM 走 safeEvmBind、Google 走連接頁（按鈕都在 index.html，
// 經 click-delegator 派發）。關掉就記在這台裝置，不再出現。

const DISMISS_KEY = 'legacy_ton_notice_dismissed';
const BANNER_ID = 'legacy-ton-banner';

function isDismissed() {
    try {
        return localStorage.getItem(DISMISS_KEY) === '1';
    } catch (_) {
        return false;
    }
}

const LegacyTonNotice = {
    sync(isLoggedIn, user) {
        const banner = document.getElementById(BANNER_ID);
        if (!banner) return;
        const show = !!isLoggedIn && !!user && user.login_backup_needed === true && !isDismissed();
        banner.classList.toggle('hidden', !show);
        // 伺服器沒開 Google 登入就不給「Link Google」（按了會到一個沒有 Google 的連接頁）
        const googleBtn = document.getElementById('legacy-ton-link-google');
        if (show && googleBtn && window.AppAPI && typeof window.AppAPI.getAppConfig === 'function') {
            window.AppAPI.getAppConfig()
                .then((cfg) => googleBtn.classList.toggle('hidden', !(cfg && cfg.google_client_id)))
                .catch(() => googleBtn.classList.add('hidden'));
        }
    },

    dismiss() {
        try {
            localStorage.setItem(DISMISS_KEY, '1');
        } catch (_) {
            /* 存不了就只收這一次 */
        }
        this.sync(false);
    },

    // 綁好了：當下收起（/api/user/me 下次也會回 false）
    resolved() {
        const AM = window.AuthManager;
        if (AM?.currentUser && typeof AM._mergeCurrentUser === 'function') {
            AM._mergeCurrentUser({ login_backup_needed: false });
        }
        this.sync(false);
    },
};

// auth.js 的 _updateUI 一開頭就發 auth:changed（currentUser 已是最新）
window.addEventListener('auth:changed', (e) => {
    LegacyTonNotice.sync(!!e?.detail?.isLoggedIn, window.AuthManager?.currentUser);
});
window.addEventListener('evm:wallet-bound', () => LegacyTonNotice.resolved());
window.addEventListener('google:linked', () => LegacyTonNotice.resolved());

window.LegacyTonNotice = LegacyTonNotice;
export { LegacyTonNotice };
