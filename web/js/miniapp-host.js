// ========================================
// miniapp-host.js — Base App／Farcaster mini app 宿主偵測（2026-09-13）
// 在 Base App 或 Farcaster 客戶端裡開啟時：載 SDK、通知宿主 ready（不叫會永遠轉圈）、
// 把宿主提供的 EIP-1193 provider 交給 evm-auth（window.__miniAppEthereumProvider）。
// 一般瀏覽器：什麼都不做，零成本。
// ========================================
(function () {
    'use strict';
    // 宿主把我們放在 iframe 或 WebView 裡；一般瀏覽器直接開的 top-level 頁面不可能是 mini app
    var framed = false;
    try { framed = window.parent !== window; } catch (e) { framed = true; }
    var webview = !!window.ReactNativeWebView;
    if (!framed && !webview) return;

    var SDK_URL = 'https://cdn.jsdelivr.net/npm/@farcaster/miniapp-sdk@0.1/+esm';
    var state = { active: false, provider: null, context: null };
    window.MiniAppHost = state;

    function markActive() {
        state.active = true;
        document.documentElement.classList.add('miniapp');
        try { document.dispatchEvent(new CustomEvent('miniapp:ready', { detail: state })); } catch (e) { /* ignore */ }
    }

    // ---- 通知 token 回報：把宿主給的 fid＋notificationDetails 綁到登入中的帳號 ----
    // webhook 事件只有 fid，沒這一步後端不知道 fid 是誰；登入完成再送（未登入時等 auth 事件）。
    var registeredKey = null;
    function setupNotificationRegistration(ctx) {
        var user = ctx && ctx.user;
        if (!user || !user.fid) return;
        var details = ctx.client && ctx.client.notificationDetails;
        var key = String(user.fid) + ':' + (details && details.token ? details.token : '');
        function attempt() {
            if (registeredKey === key) return;
            var logged = !!(window.AuthManager && typeof window.AuthManager.isLoggedIn === 'function' && window.AuthManager.isLoggedIn());
            if (!logged) return;
            registeredKey = key;
            var body = { fid: user.fid };
            if (details && details.url && details.token) { body.url = details.url; body.token = details.token; }
            fetch('/api/miniapp/notifications/register', {
                method: 'POST', credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            }).catch(function (e) {
                registeredKey = null;
                if (window.console && console.warn) console.warn('[miniapp-host] register failed:', e && e.message);
            });
        }
        attempt();
        ['auth:ready', 'auth-success', 'auth:initialized'].forEach(function (ev) {
            window.addEventListener(ev, attempt);
        });
    }

    import(SDK_URL).then(function (mod) {
        var sdk = mod.sdk || (mod.default && mod.default.sdk) || mod.default;
        if (!sdk || !sdk.actions) return;
        var check = typeof sdk.isInMiniApp === 'function' ? sdk.isInMiniApp() : Promise.resolve(true);
        return Promise.resolve(check).then(function (inside) {
            if (!inside) return;
            // ready 越早越好：宿主在這之前顯示 splash
            var whenReady = document.readyState === 'loading'
                ? new Promise(function (r) { document.addEventListener('DOMContentLoaded', r, { once: true }); })
                : Promise.resolve();
            return whenReady.then(function () {
                return sdk.actions.ready({ disableNativeGestures: false });
            }).then(function () {
                markActive();
                try { return sdk.context; } catch (e) { return null; }
            }).then(function (ctx) {
                state.context = ctx || null;
                state.sdk = sdk;
                setupNotificationRegistration(ctx);
                if (sdk.wallet && typeof sdk.wallet.getEthereumProvider === 'function') {
                    return Promise.resolve(sdk.wallet.getEthereumProvider()).then(function (p) {
                        if (p && typeof p.request === 'function') {
                            state.provider = p;
                            window.__miniAppEthereumProvider = p;
                        }
                    });
                }
                return null;
            });
        });
    }).catch(function (e) {
        if (window.console && console.warn) console.warn('[miniapp-host] sdk unavailable:', e && e.message);
    });
})();
