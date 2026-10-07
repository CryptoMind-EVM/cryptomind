// legal 三頁（terms-of-service／privacy-policy／community-guidelines）共用的
// 語言切換與返回鈕。2026-09-25 自三頁各一份的 inline <script> 移出——正式站
// CSP script-src 不再放行 'unsafe-inline'；privacy-policy 那份是舊版（切回
// zh-TW 不還原連結），一併統一成這份。
// 四語屬性系統（2026-08-20 遷移）。屬性四語數量一致性由
// tests/test_legal_i18n_parity.py 看守。
var LEGAL_LANG_ATTR = {
    'zh-TW': 'data-zh',
    'zh-CN': 'data-zh-cn',
    'en': 'data-en',
    'ru': 'data-ru'
};

function detectLegalLang() {
    try {
        var saved = localStorage.getItem('selectedLanguage');
        if (saved && LEGAL_LANG_ATTR[saved]) return saved;
    } catch (e) { /* ignore */ }
    var nav = (navigator.language || 'en');
    if (nav.indexOf('zh') === 0) {
        return /CN|Hans|SG/.test(nav) ? 'zh-CN' : 'zh-TW';
    }
    if (nav.indexOf('ru') === 0) return 'ru';
    return 'en';
}

// 內含子元素（strong/a 等強調或連結）的節點，第一次套語言前先存原始 HTML。
// 其餘語言以純文字顯示譯文（textContent 會把內嵌標籤洗掉），切回 zh-TW 時要還原——
// 原本只是「zh-TW 且有子元素就跳過」，但洗過一次之後子元素已經沒了，連結就再也回不來。
var LEGAL_ORIGINAL_HTML = null;

function applyLegalLang(lang) {
    if (!LEGAL_LANG_ATTR[lang]) lang = 'zh-TW';
    var attr = LEGAL_LANG_ATTR[lang];
    document.documentElement.lang = lang;
    if (!LEGAL_ORIGINAL_HTML) {
        LEGAL_ORIGINAL_HTML = new Map();
        document.querySelectorAll('[data-zh]').forEach(function (el) {
            if (el.children.length > 0) LEGAL_ORIGINAL_HTML.set(el, el.innerHTML);
        });
    }
    document.querySelectorAll('[data-zh]').forEach(function (el) {
        if (lang === 'zh-TW' && LEGAL_ORIGINAL_HTML.has(el)) {
            el.innerHTML = LEGAL_ORIGINAL_HTML.get(el);
            return;
        }
        var text = el.getAttribute(attr) || el.getAttribute('data-zh') || '';
        if (text) el.textContent = text;
    });
    var select = document.getElementById('legal-lang-select');
    if (select && select.value !== lang) select.value = lang;
}

// 語言直選（2026-09-20：原循環鈕 zh-TW→en→zh-CN→ru 要按好幾次才到目標語言，
// 按鈕上又只顯示「下一語言」，改成 <select> 一次點到）
function selectLegalLang(lang) {
    if (!LEGAL_LANG_ATTR[lang]) return;
    try { localStorage.setItem('selectedLanguage', lang); } catch (e) { /* ignore */ }
    applyLegalLang(lang);
}

document.addEventListener('DOMContentLoaded', function () {
    // CSP 禁止 inline onclick（legal 頁沒有全域 click delegator，f43d55a 的
    // 死鍵問題改在這裡綁定，而非 inline handler）
    var select = document.getElementById('legal-lang-select');
    if (select) select.addEventListener('change', function () { selectLegalLang(select.value); });
    // 返回鈕：7/10 從 inline onclick 改成 data-click="goHome"，但 legal 頁沒載
    // click-delegator → 之後一直點不動。這裡直接綁（行為同原 onclick）。
    document.querySelectorAll('[data-click="goHome"]').forEach(function (btn) {
        btn.addEventListener('click', function () { window.location.href = '/static/index.html'; });
    });
    applyLegalLang(detectLegalLang());
});

window.addEventListener('languageChanged', function (e) {
    var lang = (e && e.detail && e.detail.language) || detectLegalLang();
    applyLegalLang(lang);
});
