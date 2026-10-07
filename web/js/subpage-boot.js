/* 子頁 i18n boot（2026-08-20，建議 #7）——script 型子頁的標準 i18n 啟動程序。
 *
 * 背景：子頁的 i18n 啟動曾被各自發明（governance 曾整頁停在英文預設文字——
 * #500 修），每加一種頁面型態就重新踩一次坑。本模組把標準程序封裝：
 *   1. I18n.init()（i18n.js 於 init 完成後自行 updatePageContent 翻譯 data-i18n）
 *   2. 右上 EN/中 語言鈕（與 ThemeToggle 同款圓鈕、排其左側）
 *   3. languageChanged → i18n.js 自動重渲染靜態節點（本模組不重複處理）
 *
 * 頁面需求：載入 i18next CDN（SRI）＋ classic-compat（含 i18n.js）後，行內呼叫
 *   <script>SubpageBoot.init();</script>
 * 可選參數 { langButton: false } 關閉語言鈕（頁面自帶者）。
 *
 * 注意：legal 三頁的 data-zh/... 屬性系統是**內容本地化的刻意設計**
 * （不進主 JSON、避免法律長文灌爆），不適用本模組——見
 * tests/test_legal_i18n_parity.py。*/
import './i18n.js';

window.SubpageBoot = {
    init(opts) {
        const options = opts || {};

        document.addEventListener('DOMContentLoaded', async () => {
            try {
                if (window.I18n) await window.I18n.init();
            } catch (e) {
                console.error('[subpage-boot] i18n init failed:', e);
            }
        });

        if (options.langButton === false) return;
        const btn = document.createElement('button');
        btn.id = 'subpage-lang-btn';
        btn.type = 'button';
        btn.setAttribute('aria-label', 'Language');
        btn.setAttribute('style', [
            'position:fixed',
            'top:1rem',
            'right:4rem',
            'z-index:9999',
            'width:2.5rem',
            'height:2.5rem',
            'border-radius:9999px',
            'border:1px solid rgba(128,128,128,0.25)',
            'background:rgba(128,128,128,0.12)',
            'color:inherit',
            'cursor:pointer',
            'font-size:0.75rem',
            'font-weight:600',
            'transition:background .2s, opacity .2s',
            '-webkit-backdrop-filter:blur(8px)',
            'backdrop-filter:blur(8px)'
        ].join(';'));
        const setLabel = () => {
            const cur =
                window.I18n && window.I18n.getLanguage
                    ? window.I18n.getLanguage()
                    : 'en';
            btn.textContent = cur === 'zh-TW' ? 'EN' : (window.I18n ? window.I18n.t('safety.languageToggle') : '中');
        };
        btn.addEventListener('click', async () => {
            if (!window.I18n || !window.I18n.changeLanguage) return;
            const cur = window.I18n.getLanguage();
            await window.I18n.changeLanguage(cur === 'zh-TW' ? 'en' : 'zh-TW');
        });
        window.addEventListener('languageChanged', setLabel);
        setLabel();
        document.body.appendChild(btn);
    },
};

// 自動啟動：external module 執行時 document.currentScript 為 null，
// 頁面無法可靠地在行內呼叫 init（載入順序 race）——預設直接啟動。
// 不需要語言鈕的頁面改用 data 屬性停用：<script ... src="subpage-boot.js" data-no-lang-btn>
(function autoInit() {
    const noBtn =
        document.querySelector('script[src*="subpage-boot"][data-no-lang-btn]') !==
        null;
    const boot = () => window.SubpageBoot.init({ langButton: !noBtn });
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot, { once: true });
    } else {
        boot();
    }
})();
