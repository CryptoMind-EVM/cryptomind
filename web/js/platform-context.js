// ========================================
// platform-context.js — 平台情境（2026-09-13，Google Play 版 TWA）
// TWA 的 start_url 帶 ?platform=play；記進 localStorage 後，之後每次開（沒帶 query）也還是 Play。
// <html class="play">：帶 data-play-hide 的元素藏起來（USDC／TON 付款入口）。
// window.CMPlatform.get() 回 web／tma／baseapp／play，api-client 每個請求帶 X-Platform，
// 後端能力表（core/platform.py）依此擋付款軌道——前端藏只是體驗，後端擋才是規定。
// Telegram（tma）與 Base App（baseapp）由各自的偵測腳本加 class，這裡只讀。
// ========================================
(function () {
    'use strict';
    var KEY = 'cm:platform';
    var VALID = { web: 1, play: 1 };
    var platform = null;
    try {
        var q = new URLSearchParams(window.location.search).get('platform');
        if (q && VALID[q]) {
            platform = q;
            try { localStorage.setItem(KEY, q); } catch (e) { /* ignore */ }
        } else {
            try { platform = localStorage.getItem(KEY) || null; } catch (e) { platform = null; }
            if (platform && !VALID[platform]) platform = null;
        }
    } catch (e) { platform = null; }
    if (platform === 'play') document.documentElement.classList.add('play');

    window.CMPlatform = {
        get: function () {
            var html = document.documentElement.classList;
            if (html.contains('tma')) return 'tma';
            if (html.contains('miniapp')) return 'baseapp';
            if (html.contains('play')) return 'play';
            return 'web';
        },
        isPlay: function () { return document.documentElement.classList.contains('play'); }
    };
})();
