// ========================================
// google-auth.js — Google 登入（2026-09-13，Google Play 版 Phase 2）
// /api/config 有 google_client_id 才載 Google Identity Services，把官方按鈕渲染進登入視窗；
// 使用者選帳號後拿到 ID token → POST /api/user/google-login → 套用 session（同 Telegram 登入）。
// Telegram 內不顯示（Telegram 原生登入）；Play 版與網頁版都顯示。
// ========================================

import { userFacingMessage } from './error-message.js';

const GIS_SRC = 'https://accounts.google.com/gsi/client';
let _initialized = false;
let _loading = null;

function _t(key, fallback) {
    try {
        const v = window.I18n && window.I18n.t ? window.I18n.t(key) : null;
        return v && v !== key ? v : fallback;
    } catch (e) { return fallback; }
}

function _setStatus(text, isError) {
    const el = document.getElementById('google-login-status');
    if (!el) return;
    el.textContent = text || '';
    el.classList.toggle('hidden', !text);
    el.classList.toggle('text-danger', !!isError);
    el.classList.toggle('text-textMuted', !isError);
}

function _applySession(result) {
    const A = window.AuthManager;
    if (!A || !result || !result.user) return false;
    A.currentUser = {
        uid: result.user.user_id,
        user_id: result.user.user_id,
        username: result.user.username,
        accessTokenExpiry: Date.now() + (A.TOKEN_EXPIRY_MS || 86400000),
        authMethod: result.user.auth_method || 'google',
        role: result.user.role || 'user',
        membership_tier: result.user.membership_tier || 'free',
        has_wallet: result.user.has_wallet === true,
        wallet_address: null,
    };
    A._saveUserSession();
    if (typeof A.markRecentLoginSuccess === 'function') A.markRecentLoginSuccess();
    if (typeof A.startTokenRefreshTimer === 'function') A.startTokenRefreshTimer();
    if (typeof A._updateUI === 'function') A._updateUI(true);
    window.dispatchEvent(new Event('auth-success'));
    return true;
}

function _loadGis() {
    if (window.google && window.google.accounts && window.google.accounts.id) return Promise.resolve();
    if (_loading) return _loading;
    _loading = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = GIS_SRC;
        s.async = true;
        s.defer = true;
        s.onload = () => resolve();
        s.onerror = () => { _loading = null; reject(new Error('GIS load failed')); };
        document.head.appendChild(s);
    });
    return _loading;
}

async function _onCredential(response) {
    const credential = response && response.credential;
    if (!credential) return;
    _setStatus(_t('googleAuth.signingIn', 'Signing in with Google…'));
    try {
        const result = await window.AppAPI.post('/api/user/google-login', { credential });
        if (!result || !result.success) throw new Error((result && result.detail) || 'login failed');
        if (!_applySession(result)) throw new Error('session manager not ready');
        _setStatus('');
        const modal = document.getElementById('login-modal');
        if (modal) modal.classList.add('hidden');
        if (typeof window.showToast === 'function') {
            window.showToast(_t('googleAuth.loginSuccess', 'Signed in with Google'), 'success');
        }
    } catch (e) {
        // 在地化文案優先於錯誤原文：'Invalid Google token'／'login failed'／'Failed to fetch' 不直接露給使用者
        _setStatus(
            userFacingMessage(e, { fallbackKey: 'googleAuth.loginFailed', fallback: 'Google sign-in failed, please try again' }),
            true
        );
    }
}

function _renderButton(clientId) {
    const container = document.getElementById('google-login-container');
    const wrap = document.getElementById('google-login-wrap');
    if (!container || !wrap) return;
    window.google.accounts.id.initialize({
        client_id: clientId,
        callback: _onCredential,
        ux_mode: 'popup',
        auto_select: false,
        itp_support: true,
    });
    container.innerHTML = '';
    const dark = document.documentElement.classList.contains('dark');
    window.google.accounts.id.renderButton(container, {
        type: 'standard',
        theme: dark ? 'filled_black' : 'outline',
        size: 'large',
        text: 'signin_with',
        shape: 'pill',
        width: Math.min(360, Math.max(240, container.clientWidth || 300)),
        locale: (window.I18n && window.I18n.getLanguage && window.I18n.getLanguage()) || undefined,
    });
    wrap.classList.remove('hidden');
}

/** 登入視窗打開時呼叫（冪等）。沒 client id／Telegram 內／GIS 載不到都靜默。 */
export async function setupGoogleLogin() {
    if (_initialized) return;
    if (document.documentElement.classList.contains('tma')) return;
    let clientId = '';
    try {
        const cfg = await window.AppAPI.get('/api/config');
        clientId = (cfg && cfg.google_client_id) || '';
    } catch (e) { return; }
    if (!clientId) return;
    try {
        await _loadGis();
        _renderButton(clientId);
        _initialized = true;
    } catch (e) {
        if (window.console && console.warn) console.warn('[google-auth]', e && e.message);
    }
}

// 登入視窗一出現就渲染（modal 是既有的 #login-modal，用 class 切 hidden）
(function watchLoginModal() {
    const modal = document.getElementById('login-modal');
    if (!modal) return;
    const tryInit = () => { if (!modal.classList.contains('hidden')) setupGoogleLogin(); };
    tryInit();
    new MutationObserver(tryInit).observe(modal, { attributes: true, attributeFilter: ['class'] });
})();


// ── Connections 分頁的 Google 綁定卡 ─────────────────────────────
const GoogleLinkApp = {
    _clientId: '',
    async init() {
        const content = document.getElementById('google-link-content');
        if (!content) return;
        if (typeof window.AuthManager === 'undefined' || !window.AuthManager.isLoggedIn()) {
            content.innerHTML = '';
            return;
        }
        await this.loadStatus();
    },
    _badge(bound, text) {
        const badge = document.getElementById('google-status-badge');
        if (!badge) return;
        badge.className = 'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium shrink-0 whitespace-nowrap ' +
            (bound ? 'bg-success/10 text-success' : 'bg-surfaceHighlight text-textMuted');
        badge.textContent = text;
    },
    async loadStatus() {
        const content = document.getElementById('google-link-content');
        if (!content) return;
        let status = null;
        try { status = await window.AppAPI.get('/api/google/status'); } catch (e) { status = null; }
        // 伺服器沒開 Google 登入（或查不到）→ 整個「登入方式」區塊藏起來，不擺一張用不了的卡
        const section = document.getElementById('connections-signin-section');
        const enabled = !!status && status.enabled !== false;
        if (section) section.classList.toggle('hidden', !enabled);
        if (!enabled) {
            this._badge(false, _t('googleAuth.notAvailable', 'Not available yet'));
            content.innerHTML = `<p class="text-sm text-textMuted">${_t('googleAuth.notAvailableHint', 'Google sign-in is not enabled on this server yet.')}</p>`;
            return;
        }
        if (status.bound) {
            this._badge(true, _t('googleAuth.linked', 'Linked'));
            const email = status.email ? `<span class="font-mono text-xs">${String(status.email).replace(/[<>&]/g, '')}</span>` : '';
            content.innerHTML = `
                <div class="flex items-center justify-between gap-3 flex-wrap">
                    <div class="text-sm text-textMain">${email}</div>
                    <button id="google-unlink-btn" class="min-h-11 px-4 rounded-xl border border-borderSubtle text-sm text-textMuted hover:text-danger">${_t('googleAuth.unlink', 'Unlink')}</button>
                </div>
                <p id="google-link-status" class="hidden mt-2 text-xs text-textMuted"></p>`;
            content.querySelector('#google-unlink-btn').addEventListener('click', () => this.unlink());
            return;
        }
        this._badge(false, _t('googleAuth.notLinked', 'Not linked'));
        content.innerHTML = `
            <p class="text-sm text-textMuted mb-3">${_t('googleAuth.linkHint', 'After linking, you can sign in to this same account with Google, no wallet needed.')}</p>
            <div id="google-bind-container" class="flex justify-start min-h-11"></div>
            <p id="google-link-status" class="hidden mt-2 text-xs text-textMuted"></p>`;
        try {
            const cfg = await window.AppAPI.get('/api/config');
            this._clientId = (cfg && cfg.google_client_id) || '';
            if (!this._clientId) return;
            await _loadGis();
            const container = document.getElementById('google-bind-container');
            if (!container) return;
            window.google.accounts.id.initialize({
                client_id: this._clientId,
                callback: (resp) => this.bind(resp && resp.credential),
                ux_mode: 'popup',
                auto_select: false,
            });
            container.innerHTML = '';
            window.google.accounts.id.renderButton(container, {
                type: 'standard', theme: 'outline', size: 'large', text: 'continue_with', shape: 'pill',
                width: Math.min(320, Math.max(220, container.clientWidth || 280)),
            });
        } catch (e) {
            if (window.console && console.warn) console.warn('[google-auth] bind button', e && e.message);
        }
    },
    _status(text, isError) {
        const el = document.getElementById('google-link-status');
        if (!el) return;
        el.textContent = text || '';
        el.classList.toggle('hidden', !text);
        el.classList.toggle('text-danger', !!isError);
    },
    async bind(credential) {
        if (!credential) return;
        this._status(_t('googleAuth.linking', 'Linking…'));
        try {
            await window.AppAPI.post('/api/google/bind', { credential });
            this._status('');
            // 舊 TON 身份的綁定提醒聽這個收起（legacy-ton-notice.js）
            window.dispatchEvent(new CustomEvent('google:linked'));
            await this.loadStatus();
            if (typeof window.showToast === 'function') window.showToast(_t('googleAuth.linkedToast', 'Google account linked'), 'success');
        } catch (e) {
            this._status(userFacingMessage(e, { fallbackKey: 'googleAuth.linkFailed', fallback: 'Could not link Google account' }), true);
        }
    },
    async unlink() {
        const ok = window.showConfirmDialog
            ? await window.showConfirmDialog({
                  title: _t('googleAuth.unlink', 'Unlink'),
                  message: _t(
                      'connections.googleUnlinkConfirm',
                      "Unlink Google? You won't be able to sign in to this account with Google until you link it again."
                  ),
                  danger: true,
              })
            : true;
        if (!ok) return;
        try {
            await window.AppAPI.post('/api/google/unlink', {});
            await this.loadStatus();
        } catch (e) {
            this._status(userFacingMessage(e, { fallbackKey: 'googleAuth.unlinkFailed', fallback: 'Could not unlink' }), true);
        }
    },
};

window.setupGoogleLogin = setupGoogleLogin;
window.GoogleLinkApp = GoogleLinkApp;
export { GoogleLinkApp };
