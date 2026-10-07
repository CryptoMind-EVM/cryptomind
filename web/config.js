// ========================================
// config.js - 前端配置文件
// ========================================

// DEBUG_MODE 由 hostname 推導，fail-closed（認不出是本機就當正式站）。
//
// 2026-08-25：原本寫死 `DEBUG_MODE: false`，而且這支根本沒被主 SPA 載入
// （只有 forum×7 / scam-tracker×3 有載）。兩個後果：
//   1. 主 SPA 的 window.APP_CONFIG 恆為 undefined，所有
//      `APP_CONFIG && APP_CONFIG.DEBUG_MODE !== true` 形式的守衛都會短路
//      ——layout-debug 面板外洩到正式站就是這樣來的。
//   2. logger.js 也沒載，141 處 console.log 在正式站全部照噴。
// 改成由 hostname 推導後，正式站靜音、本機開發保有完整 log。
//
// 判斷邏輯與 web/js/layout-debug.js 的 isLocalHost 對齊，
// tests/js/layout_debug_gate.mjs 會比對兩邊行為一致。
(function () {
    var PRIVATE_IPV4 = new RegExp(
        '^(?:' +
        '10\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}' + '|' +
        '192\\.168\\.\\d{1,3}\\.\\d{1,3}' + '|' +
        '172\\.(?:1[6-9]|2\\d|3[01])\\.\\d{1,3}\\.\\d{1,3}' +
        ')$'
    );

    function isPrivateIPv4(host) {
        if (!PRIVATE_IPV4.test(host)) return false;
        return host.split('.').every(function (o) { return Number(o) <= 255; });
    }

    function isLocalHost() {
        var host = (window.location.hostname || '').toLowerCase();
        return (
            host === 'localhost' ||
            host === '127.0.0.1' ||
            host === '::1' ||
            host === '[::1]' ||
            host === '0.0.0.0' ||
            host.endsWith('.local') ||
            host.endsWith('.localhost') ||
            // 整串比對——只錨定開頭的話 `192.168.1.5.evil.com` 也會通過
            isPrivateIPv4(host)
        );
    }

    window.APP_CONFIG = {
        // 本機開發全開；正式站靜音（logger.js 會把 console.log/debug/info 換成 noop）
        DEBUG_MODE: isLocalHost(),

        // 钱包连接超时时间（毫秒）
        WALLET_CONNECT_TIMEOUT: 10000,

        // API请求超时时间（毫秒）
        API_REQUEST_TIMEOUT: 15000,

        // 重试次数
        MAX_RETRY_ATTEMPTS: 3
    };

    // 设置全局调试模式标志
    window.DEBUG_MODE = window.APP_CONFIG.DEBUG_MODE;
})();
