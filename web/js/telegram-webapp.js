// ========================================
// telegram-webapp.js — Telegram Mini App integration
// ========================================
// Loaded as a plain (non-module) script in <head>, AFTER the official
// telegram-web-app.js SDK and BEFORE the app's ES modules.
//
// Without this, the app is just a website: opened inside Telegram it does not
// identify as a Mini App, does not expand, and — critically — TON Connect has
// no way to return the user to the Mini App after a wallet action, so the
// wallet bounces back to an EXTERNAL browser. That breaks the flow and is a
// common Telegram Apps Center rejection reason.

(function () {
    'use strict';

    var WebApp = window.Telegram && window.Telegram.WebApp;
    // Outside Telegram the SDK still loads but reports platform "unknown".
    var inTelegram = !!(WebApp && WebApp.platform && WebApp.platform !== 'unknown');
    window.IS_TELEGRAM_MINIAPP = inTelegram;

    // TON Connect return URL for the Telegram Mini App environment. After the
    // user approves in their wallet, TON Connect uses this to come back INTO
    // Telegram instead of an external browser. Points at the named Mini App
    // registered in BotFather (/newapp, short name "cmind").
    window.TWA_RETURN_URL = 'https://t.me/getcryptomind_bot/cmind';

    if (WebApp) {
        // Tell Telegram the Mini App is ready and expand to full height.
        try { WebApp.ready(); } catch (e) { /* noop */ }
        try { WebApp.expand(); } catch (e) { /* noop */ }
    }

    // Open external URLs the right way inside Telegram. A plain target="_blank"
    // / window.open can break or escape the Mini App webview.
    window.openExternalLink = function (url) {
        if (!url) return;
        if (inTelegram && WebApp && typeof WebApp.openLink === 'function') {
            WebApp.openLink(url);
        } else {
            window.open(url, '_blank', 'noopener');
        }
    };

    // Route every target="_blank" anchor (news links, etc.) through openLink
    // when inside Telegram. Capture-phase so it runs before the default nav.
    // No-op in a normal browser.
    if (inTelegram) {
        document.addEventListener(
            'click',
            function (e) {
                var a = e.target && e.target.closest && e.target.closest('a[target="_blank"]');
                if (a && a.href) {
                    e.preventDefault();
                    window.openExternalLink(a.href);
                }
            },
            true
        );
    }
})();
