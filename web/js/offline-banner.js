// ========================================
// offline-banner.js — 離線偵測與提示
// 2026-08-23 Phase C：SW 快取 app shell 但 API 全炸→白屏。
// 加一個優雅的「離線中」banner，連線恢復自動消失。
// ========================================

(function () {
    'use strict';

    var bannerId = 'offline-banner';
    var style = document.createElement('style');
    style.textContent = `
        #${bannerId} {
            position: fixed;
            top: 0; left: 0; right: 0;
            z-index: 9999;
            background: rgb(245, 158, 11);
            color: rgb(30, 30, 35);
            font-size: 13px;
            font-weight: 600;
            text-align: center;
            padding: 8px 16px;
            transform: translateY(-100%);
            transition: transform 0.3s ease;
            pointer-events: none;
        }
        #${bannerId}.visible {
            transform: translateY(0);
        }
        #${bannerId} .dot {
            display: inline-block;
            width: 7px; height: 7px;
            border-radius: 50%;
            background: currentColor;
            margin-right: 6px;
            animation: ${bannerId}-pulse 1.2s ease-in-out infinite;
        }
        @keyframes ${bannerId}-pulse {
            0%, 100% { opacity: 0.4; }
            50% { opacity: 1; }
        }
    `;
    document.head.appendChild(style);

    var banner = document.createElement('div');
    banner.id = bannerId;
    banner.innerHTML = '<span class="dot"></span><span data-i18n="common.offline">Offline — will resume when the connection is back</span>';
    banner.setAttribute('data-i18n', 'common.offline');
    document.body.appendChild(banner);

    function update() {
        var offline = !navigator.onLine;
        banner.classList.toggle('visible', offline);
        document.documentElement.style.setProperty('--offline-banner-h', offline ? '36px' : '0px');
    }

    window.addEventListener('online', function () {
        update();
        // 連線恢復：如果有 toast 通知機制就顯示
        if (typeof window.showToast === 'function') {
            var t = window.I18n ? window.I18n.t('common.backOnline') : 'Back online';
            window.showToast(t, 'success');
        }
    });
    window.addEventListener('offline', update);

    /* 回到前景時一定要重新檢查一次（2026-08-25 DANNY 回報：手機切到其他 App
       再切回來就卡在「離線中」）。

       手機把背景分頁凍結／收進 bfcache，凍結期間不跑 JS——網路恢復所送出的
       online 事件因此收不到，或送達時頁面根本沒在聽。只靠 online/offline
       事件的話，橫幅會一直留在畫面上，即使 navigator.onLine 早就是 true。
       改成回到前景就照 navigator.onLine 重新校正，不依賴事件有沒有送到。

       update() 只是讀一個 boolean、切一個 class，成本近乎零，所以不用
       document.hidden 過濾——那等於多押一個「行動瀏覽器會正確回報可見性」
       的假設，而這正是出問題的那類環境。無條件校正最穩。 */
    window.addEventListener('pageshow', update);
    document.addEventListener('visibilitychange', update);

    update();
})();
