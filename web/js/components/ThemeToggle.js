// ========================================
// ThemeToggle.js - 子頁專用極簡主題切換鈕
// ========================================
// 與主站 ThemeSwitcher 不同：這是極簡版，給 forum/scam-tracker/legal/governance
// 等子頁用。一個 fixed 右上角圓鈕，點擊直接 light↔dark 切換。
//
// 與主站共用 localStorage key 'selectedTheme'，所以主站切了子頁會跟著切。
// 需搭配 early-init.js（防 FOUC + 預設淺色）。
//
// 設計：純 vanilla JS、自帶 inline SVG icon（子頁不一定載 lucide）、
// 不依賴 main.js / AppUtils，可當 <script src> 直接載。

(function () {
    // 避免重複注入
    if (window.__themeToggleMounted) return;

    function getEffectiveTheme() {
        return document.documentElement.classList.contains('dark') ? 'dark' : 'light';
    }

    // SVG icons（inline，不依賴 lucide）
    var SUN_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>';
    var MOON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>';

    function createToggle() {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.setAttribute('aria-label', 'Toggle theme');
        // 樣式：右上角圓鈕，用 CSS var 配合主站 token（若頁面有載 styles.css）；
        // 若沒載（legal/governance），fallback 到內聯樣式。
        btn.setAttribute('style', [
            'position:fixed',
            'top:1rem',
            'right:1rem',
            'z-index:9999',
            'width:2.5rem',
            'height:2.5rem',
            'border-radius:9999px',
            'border:1px solid rgba(128,128,128,0.25)',
            'background:rgba(128,128,128,0.12)',
            'color:inherit',
            'cursor:pointer',
            'display:flex',
            'align-items:center',
            'justify-content:center',
            'padding:0',
            'line-height:0',
            'transition:background .2s, opacity .2s',
            '-webkit-backdrop-filter:blur(8px)',
            'backdrop-filter:blur(8px)'
        ].join(';'));
        btn.title = (window.I18n ? window.I18n.t('theme.toggleTitle') : 'Toggle light/dark theme');

        function render() {
            var isDark = getEffectiveTheme() === 'dark';
            btn.innerHTML = isDark ? SUN_SVG : MOON_SVG;
            // 淺色時顯示月亮（提示可切到深）、深色時顯示太陽（提示可切到淺）
        }

        btn.addEventListener('click', function () {
            var current = getEffectiveTheme();
            var next = current === 'dark' ? 'light' : 'dark';
            document.documentElement.classList.toggle('dark', next === 'dark');
            try { localStorage.setItem('selectedTheme', next); } catch (e) {}
            // 通知主站模組（若有）
            window.dispatchEvent(new CustomEvent('themeChanged', {
                detail: { theme: next, effective: next }
            }));
            render();
        });

        render();
        // 開機後若主站或其他邏輯改了 theme，同步更新 icon
        window.addEventListener('themeChanged', render);

        document.body.appendChild(btn);
        window.__themeToggleMounted = true;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', createToggle);
    } else {
        createToggle();
    }
})();
