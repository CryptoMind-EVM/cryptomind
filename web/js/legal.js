// ========================================
// legal.js - 法律與條款頁面 Modal 邏輯
// ========================================

const LEGAL_PAGE_MAP = {
    terms: 'terms-of-service.html',
    privacy: 'privacy-policy.html',
    guidelines: 'community-guidelines.html',
};

// 載入失敗的在地化文案（以前寫死 'Failed to load: ' + e.message、'Content not found.'）
function _legalLoadFailed() {
    return window.I18n ? window.I18n.t('common.loadFailed') : 'Load failed';
}

async function showLegalPage(type) {
    const filename = LEGAL_PAGE_MAP[type];
    if (!filename) return;

    const modal = document.getElementById('legal-modal');
    const contentArea = document.querySelector('#legal-content > div');
    const titleEl = document.getElementById('legal-title');
    const backEl = document.getElementById('legal-back-text');

    // Show modal with loading
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    contentArea.innerHTML =
        '<div class="flex items-center justify-center py-20"><div class="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div></div>';

    const lang = window.I18n ? window.I18n.getLanguage() : 'en';
    backEl.textContent = window.I18n ? window.I18n.t('legal.back') : (lang === 'zh-TW' ? 'Back' : 'Back');

    try {
        const res = await fetch(`/static/legal/${filename}`);
        const html = await res.text();
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');

        // 三頁都是單一 #content＋四語屬性（舊的 content-zh／content-en 雙區塊版型只分中英，已不用）
        const singleContent = doc.getElementById('content');

        if (singleContent) {
            contentArea.innerHTML = '';
            const clone = singleContent.cloneNode(true);
            clone.id = 'legal-content-single';
            contentArea.appendChild(clone);
            _applyDataLang(contentArea, lang);
        } else {
            contentArea.innerHTML = `<p class="text-center text-textMuted py-20">${_legalLoadFailed()}</p>`;
            return;
        }

        titleEl.textContent = _legalTitle(type, lang) || (doc.getElementById('nav-title')?.textContent || '');
        modal.dataset.type = type;
    } catch (e) {
        // e.message 是 'Failed to fetch' 這類英文——只顯示在地化的「載入失敗」，原文留在 console
        console.warn('[legal] load failed', e);
        contentArea.innerHTML = `<p class="text-center text-danger py-20">${_legalLoadFailed()}</p>`;
    }

    if (window.AppUtils) window.AppUtils.refreshIcons();
}

// web/legal/ 三頁每段都有四語屬性（同那幾頁自己的 applyLegalLang）；
// 以前這裡只分 zh-TW／其他，zh-CN 與 ru 使用者看到英文。不支援的語言退回英文。
const LEGAL_LANG_ATTR = {
    'zh-TW': 'data-zh',
    'zh-CN': 'data-zh-cn',
    en: 'data-en',
    ru: 'data-ru',
};
// 原文（zh-TW）內含 <strong>／<a> 的節點：其他語言只有純文字譯文（textContent
// 會把內嵌標籤洗掉），第一次套語言前先存原始 HTML，切回 zh-TW 時還原
const _legalOriginalHtml = new WeakMap();

function _applyDataLang(container, lang) {
    if (!container) return;
    const attr = LEGAL_LANG_ATTR[lang] || 'data-en';
    container.querySelectorAll('[data-zh]').forEach((el) => {
        if (el.children.length > 0 && !_legalOriginalHtml.has(el)) {
            _legalOriginalHtml.set(el, el.innerHTML);
        }
        if (attr === 'data-zh' && _legalOriginalHtml.has(el)) {
            el.innerHTML = _legalOriginalHtml.get(el);
            return;
        }
        const text = el.getAttribute(attr) || el.getAttribute('data-en');
        if (text) el.textContent = text;
    });
}

function _legalTitle(type, lang) {
    const titles = {
        terms: { 'zh-TW': '服務條款', 'zh-CN': '服务条款', en: 'Terms of Service', ru: 'Условия обслуживания' },
        privacy: { 'zh-TW': '隱私權政策', 'zh-CN': '隐私政策', en: 'Privacy Policy', ru: 'Политика конфиденциальности' },
        guidelines: { 'zh-TW': '社群守則', 'zh-CN': '社群守则', en: 'Community Guidelines', ru: 'Правила сообщества' },
    };
    const t = titles[type];
    return t ? t[lang] || t.en : '';
}

function _updateLegalLanguage(lang) {
    const single = document.getElementById('legal-content-single');
    if (single) _applyDataLang(single.parentElement, lang);

    const backEl = document.getElementById('legal-back-text');
    if (backEl) backEl.textContent = window.I18n ? window.I18n.t('legal.back') : (lang === 'zh-TW' ? 'Back' : 'Back');

    const titleEl = document.getElementById('legal-title');
    const modal = document.getElementById('legal-modal');
    const type = modal?.dataset?.type;
    if (titleEl && type) {
        titleEl.textContent = _legalTitle(type, lang);
    }
}

function closeLegalModal() {
    const modal = document.getElementById('legal-modal');
    modal.classList.add('hidden');
    modal.classList.remove('flex');
    const contentArea = document.querySelector('#legal-content > div');
    if (contentArea) contentArea.innerHTML = '';
    delete modal.dataset.type;
}

// Re-render legal content on language change
window.addEventListener('languageChanged', e => {
    const modal = document.getElementById('legal-modal');
    if (modal && !modal.classList.contains('hidden')) {
        const lang = e.detail?.language || (window.I18n ? window.I18n.getLanguage() : 'en');
        _updateLegalLanguage(lang);
    }
});

window.showLegalPage = showLegalPage;
window.closeLegalModal = closeLegalModal;

export { LEGAL_PAGE_MAP, showLegalPage, closeLegalModal };
