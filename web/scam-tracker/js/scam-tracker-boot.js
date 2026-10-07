// scam-tracker 子頁（detail／submit）的啟動腳本。2026-09-25 自各頁的 inline <script>
// 移出（正式站 CSP script-src 不再放行 'unsafe-inline'）；只差呼叫哪個 init，
// 由 <script data-page="detail|submit"> 決定。列表頁 2026-09-27 改成 SPA 分頁 #scamcheck。
(function () {
    var INIT = { detail: 'initDetailPage', submit: 'initSubmitPage' };
    var script = document.currentScript;
    var method = INIT[script && script.getAttribute('data-page')];

    document.addEventListener('DOMContentLoaded', async () => {
        if (typeof initializeAuth === 'function') initializeAuth();
        if (window.I18n) await window.I18n.init();
        if (typeof ScamTrackerApp !== 'undefined' && method) await ScamTrackerApp[method]();
        lucide.createIcons();
    });
})();
