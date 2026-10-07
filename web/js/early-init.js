// early-init.js — must load before all other scripts
// Moved from inline <script> in index.html for CSP compliance
(function () {
    // ── Theme（防 FOUC：在任何 CSS 渲染前決定深淺）─────────────────────
    // 必須同步執行，否則會出現「先閃深色再轉淺色」的閃爍。
    // 預設淺色（top.co 風格）；使用者可經 ThemeSwitcher 覆寫並存 localStorage。
    try {
        var saved = localStorage.getItem('selectedTheme'); // 'light' | 'dark' | 'system' | null
        var theme = saved || 'light';
        if (theme === 'system') {
            theme = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
        }
        var root = document.documentElement;
        if (theme === 'dark') {
            root.classList.add('dark');
        } else {
            root.classList.remove('dark');
        }
    } catch (_e) {
        // localStorage 被禁用（隱私模式）時保底深色（既有使用者預期）
        document.documentElement.classList.add('dark');
    }

    // ── 桌機側欄收合（Teams 式圖示列）：第一次繪製前套用，免得先寬後窄閃一下 ──
    // 由 GlobalNav.setSidebarCollapsed（SPA）／site-sidebar.js（獨立頁）寫入；CSS 只在 md+ 生效
    try {
        if (localStorage.getItem('sidebarCollapsed') === '1') {
            document.documentElement.classList.add('sidebar-collapsed');
        }
    } catch (_e) {
        /* localStorage 不可用：維持展開 */
    }

    // ── 既有：登入態判定（隱藏 login modal / nav）──────────────────────
    var u = localStorage.getItem('ton_user');
    if (u) {
        try {
            JSON.parse(u);
            var modal = document.getElementById('login-modal');
            if (modal) modal.classList.add('hidden');
        } catch (e) {}
    }
})();
