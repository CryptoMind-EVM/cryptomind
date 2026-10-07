// ========================================
// social-login.js — Email／Google 登入（Reown 內嵌錢包）開不開（PR-6，2026-09-27）
//
// 一頁只判定一次，AppKit 設定、登入視窗按鈕、Premium「刷卡買 USDC」三處共用同一個答案——
// AppKit 一頁只初始化一次，三處各自判定會出現「按鈕在、彈窗裡卻沒有 Email」。
//
// 開的條件：/api/config 的 reown_social_login === true，且是一般網頁。
//   - Telegram Mini App：Telegram 規定錢包只能 TON Connect
//   - Base App／Farcaster：宿主自己給錢包（被 iframe 或 RN WebView 載入）
//   - Play 版（TWA）：付款與登入走 Play 的規矩
// 任何一步讀不到就當關（fail-closed）。
// ========================================

/** 純函式：只有布林 true 的旗標＋一般網頁才開。 */
export function shouldEnableSocialLogin(ctx) {
    const c = ctx || {};
    return c.flag === true && c.platform === 'web' && !c.framed && !c.telegram;
}

/** 從 window 讀情境。讀 top 丟例外（跨網域 iframe）就當作被框住。 */
export function readSocialLoginContext(win) {
    const w = win || window;
    let platform = 'web';
    try {
        if (w.CMPlatform && typeof w.CMPlatform.get === 'function') platform = w.CMPlatform.get();
    } catch (e) {
        platform = 'unknown';
    }
    let framed;
    try {
        framed = w.self !== w.top;
    } catch (e) {
        framed = true;
    }
    if (w.ReactNativeWebView) framed = true;
    let telegram = false;
    try {
        telegram = !!(w.Telegram && w.Telegram.WebApp && w.Telegram.WebApp.initData);
    } catch (e) {
        telegram = false;
    }
    return { platform, framed, telegram };
}

/** 讀 /api/config（共用快取）後判定；不做記憶，測試用。 */
export async function computeSocialLogin(win) {
    const w = win || window;
    try {
        const api = w.AppAPI;
        if (!api || typeof api.getAppConfig !== 'function') return false;
        const cfg = await api.getAppConfig();
        return shouldEnableSocialLogin({
            flag: !!cfg && cfg.reown_social_login === true,
            ...readSocialLoginContext(w),
        });
    } catch (e) {
        return false;
    }
}

let _resolved = null;

/** 本頁的答案（記憶）。 */
export function resolveSocialLogin() {
    if (!_resolved) _resolved = computeSocialLogin();
    return _resolved;
}

/** 登入視窗的「用 Email 或 Google 繼續」按鈕：只切 hidden。 */
export function applySocialLoginButton(enabled, doc) {
    try {
        const d = doc || document;
        const btn = d.getElementById('social-login-btn');
        if (btn) btn.classList.toggle('hidden', !enabled);
    } catch (e) { /* 按鈕壞掉不能影響其他登入方式 */ }
}
