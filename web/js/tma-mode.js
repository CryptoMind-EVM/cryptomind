// ========================================
// tma-mode.js — Telegram Mini App「TON 模式」（2026-09-13）
// Telegram 規定 Mini App 內的加密功能只能是 TON、錢包只能 TON Connect。
// 在 Telegram 裡開啟時給 <html> 加 .tma，帶 data-tma-hide 的元素（EVM 登入／綁定、
// USDC 付款入口）用 CSS 藏起來；資料顯示不受影響。一般瀏覽器什麼都不做。
// ========================================
(function () {
    'use strict';
    function isTelegramMiniApp() {
        try {
            var wa = window.Telegram && window.Telegram.WebApp;
            return !!(wa && wa.initData);
        } catch (e) {
            return false;
        }
    }
    function apply() {
        if (isTelegramMiniApp()) document.documentElement.classList.add('tma');
    }
    apply();
    // telegram-web-app.js（defer）晚於本檔執行，但一定在 DOMContentLoaded 之前跑完
    document.addEventListener(
        'DOMContentLoaded',
        function () {
            apply();
            // 一般瀏覽器：telegram-web-app.js 照樣會建立 window.Telegram（initData 是空的）。
            // Reown AppKit 只看 window.Telegram 存不存在就判定「在 Telegram 裡」
            //（CoreHelperUtil.isTelegram）——2026-09-27 實測：iPhone Safari 因此被拿掉 Google 登入
            //（OptionsUtil.filterSocialsByPlatform），Android 的 WalletConnect 連結也會多編碼一次。
            // 不是 Mini App 就清掉；真的在 Telegram App 內建瀏覽器時 AppKit 另有
            // TelegramWebviewProxy 可判定，不受影響。
            if (!isTelegramMiniApp()) {
                try {
                    delete window.Telegram;
                } catch (e) {
                    /* 不可刪就改成 undefined */
                }
                if (window.Telegram) window.Telegram = undefined;
            }
        },
        { once: true }
    );
    window.isTelegramMiniAppMode = function () { return document.documentElement.classList.contains('tma'); };
})();
