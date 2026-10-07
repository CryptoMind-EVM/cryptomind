// sw-register.js — service worker 註冊（只快取 app shell）
// 放在 publicDir（web/public/），build 後原樣到 dist/static/sw-register.js，
// 不被 Vite 處理，Dockerfile 刪檔規則（只刪 web/js/*.js）不影響本檔。
//
// 策略：自家 JS/CSS chunk 由 Workbox precache／CacheFirst；HTML 一律走網路（不快取）；
// 所有 /api/* 與 WebSocket 一律 network-only（金融平台絕不快取即時資料）。
// SW 檔在 /sw.js（後端路由，送 Service-Worker-Allowed: /），註冊時 scope:'/'。
(function () {
    // SW 需要 secure context：只在 https 或 localhost 註冊（vite dev 的非 secure
    // IP 測試、或 file:// 不註冊，避免錯誤刷屏）
    if (!('serviceWorker' in navigator)) return;
    if (location.protocol !== 'https:' && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1') {
        return;
    }

    // 頁面載入時有沒有舊 SW 在控制：第一次安裝（新訪客）clientsClaim 也會觸發
    // controllerchange，但那不是換版，不能拿來整頁重整（2026-09-27 SW 修好能裝之後才會遇到）
    var hadController = !!navigator.serviceWorker.controller;

    window.addEventListener('load', function () {
        navigator.serviceWorker
            .register('/sw.js', { scope: '/' })
            .then(function (reg) {
                console.debug('[sw] registered, scope:', reg.scope);
            })
            .catch(function (err) {
                // 註冊失敗不阻塞 app（SW 只是加速重複造訪，非核心功能）
                console.debug('[sw] registration skipped:', err.message);
            });

        // 新版 SW 接管時：不自動重整（2026-09-27）。#904 起 HTML 一律走網路、不進 precache，
        // 開頁拿到的就是新版；以前「開頁 20 秒內靜默重整」（#790，HTML 還在快取的年代）
        // 反而會在部署後打斷進行中的流程——Google 登入去 Google 分頁的那幾秒原頁被重整，
        // 回來 AppKit 收不到登入結果（DANNY 錄影）。只有開著一陣子的頁面（可能還是舊版）才提示。
        let notified = false;
        navigator.serviceWorker.addEventListener('controllerchange', function () {
            // 第一次安裝：頁面本來就是網路抓的最新版
            if (!hadController) {
                hadController = true;
                return;
            }
            if (notified) return;
            console.debug('[sw] controller changed — new version active');
            var justOpened = (performance && performance.now ? performance.now() : 99999) < 20000;
            if (justOpened) return; // 剛開的頁面 HTML 是網路抓的，已經是新版
            notified = true;
            if (typeof window.showToast === 'function' && window.I18n && window.I18n.t) {
                var msg = window.I18n.t('common.newVersionReady');
                window.showToast(msg && msg !== 'common.newVersionReady' ? msg : 'A new version is ready — reload to update', 'info');
            }
        });
        // 每次開頁都問一次有沒有新 SW（瀏覽器預設 24h 才檢查）
        // update() 回的是 Promise：sw.js 暫時抓不到（部署換容器的空窗、離線、VPN 吃掉請求）會 reject
        // "Failed to update a ServiceWorker … Not found"。以前只包 try/catch 接不到非同步拒絕，
        // 掉進全域 unhandledrejection → 使用者看到紅色錯誤 toast（2026-10-06 部署後實際發生）。
        // 更新檢查失敗無妨：下次開頁會再問一次。
        navigator.serviceWorker.ready
            .then(function (reg) {
                return reg.update();
            })
            .catch(function (err) {
                console.debug('[sw] update check skipped:', err && err.message);
            });
    });
})();
