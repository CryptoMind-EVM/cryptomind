// 條款／隱私／守則 modal（web/js/legal.js）要跟著目前語言走四語（2026-09-25 盤查）。
// 以前只分 zh-TW 與「其他」：zh-CN 與 ru 使用者看到的是英文，雖然
// web/legal/*.html 每段都有 data-zh / data-zh-cn / data-en / data-ru 四語屬性。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function makeClassList() {
    const set = new Set();
    return {
        add: (...c) => c.forEach((x) => set.add(x)),
        remove: (...c) => c.forEach((x) => set.delete(x)),
        contains: (c) => set.has(c),
    };
}

// 最小選擇器：只認 [attr][attr] 這種「屬性存在」條件（legal.js 用的就是這種）
const matches = (node, selector) =>
    [...selector.matchAll(/\[([a-z-]+)\]/g)].every((m) => node.getAttribute(m[1]) !== null);

function makeSpan(attrs, html) {
    let inner = html;
    return {
        getAttribute: (name) => (name in attrs ? attrs[name] : null),
        get innerHTML() { return inner; },
        set innerHTML(v) { inner = String(v); },
        get textContent() { return inner.replace(/<[^>]+>/g, ''); },
        set textContent(v) { inner = esc(v); },
        get children() { return { length: (inner.match(/<[a-z]/gi) || []).length }; },
    };
}

const ALL = {
    'data-zh': '重要通知',
    'data-zh-cn': '重要通知（简）',
    'data-en': 'IMPORTANT NOTICE',
    'data-ru': 'ВАЖНОЕ УВЕДОМЛЕНИЕ',
};
let spans;
function freshSpans() {
    spans = {
        plain: makeSpan(ALL, '重要通知'),
        // 原文（zh-TW）內含 <strong>／<a>：其他語言只有純文字譯文，切回 zh-TW 要還原
        rich: makeSpan(
            { 'data-zh': '歡迎使用 CryptoMind', 'data-zh-cn': '欢迎使用 CryptoMind', 'data-en': 'Welcome to CryptoMind', 'data-ru': 'Добро пожаловать' },
            '歡迎使用 <strong>CryptoMind</strong>',
        ),
        // 缺 data-ru 的節點：退回英文
        noRu: makeSpan({ 'data-zh': '返回', 'data-zh-cn': '返回', 'data-en': 'Back' }, '返回'),
    };
    return Object.values(spans);
}

function makeContentNode() {
    const kids = freshSpans();
    const node = {
        id: 'content',
        parentElement: null,
        cloneNode: () => node,
        querySelectorAll: (sel) => kids.filter((k) => matches(k, sel)),
    };
    return node;
}

// ── DOM／網路替身 ────────────────────────────────────────────
let appended = [];
const contentArea = {
    set innerHTML(v) { appended = []; },
    get innerHTML() { return ''; },
    appendChild(n) { n.parentElement = contentArea; appended.push(n); },
    querySelectorAll: (sel) => appended.flatMap((n) => n.querySelectorAll(sel)),
};
const modal = { classList: makeClassList(), dataset: {} };
modal.classList.add('hidden');
const titleEl = { textContent: '' };
const backEl = { textContent: '' };

let lang = 'en';
const listeners = {};
globalThis.window = globalThis;
globalThis.addEventListener = (type, fn) => { listeners[type] = fn; };
globalThis.I18n = { getLanguage: () => lang, t: (k) => k };
globalThis.document = {
    getElementById: (id) => {
        if (id === 'legal-modal') return modal;
        if (id === 'legal-title') return titleEl;
        if (id === 'legal-back-text') return backEl;
        return appended.find((n) => n.id === id) || null;
    },
    querySelector: (sel) => (sel === '#legal-content > div' ? contentArea : null),
};
globalThis.fetch = async () => ({ text: async () => '<html></html>' });
globalThis.DOMParser = class {
    parseFromString() {
        const content = makeContentNode();
        return {
            getElementById: (id) => (id === 'content' ? content : id === 'nav-title' ? { textContent: '服務條款' } : null),
        };
    }
};

await import(await loadModuleUrl(new URL('../../web/js/legal.js', import.meta.url).pathname));

const cases = [
    ['zh-CN', '重要通知（简）', '服务条款', '返回'],
    ['ru', 'ВАЖНОЕ УВЕДОМЛЕНИЕ', 'Условия обслуживания', 'Back'],
    ['en', 'IMPORTANT NOTICE', 'Terms of Service', 'Back'],
    ['ja', 'IMPORTANT NOTICE', 'Terms of Service', 'Back'], // 不支援的語言退回英文
];
for (const [l, plain, title, noRu] of cases) {
    lang = l;
    await window.showLegalPage('terms');
    assert.equal(spans.plain.textContent, plain, `${l}：內文語言不對（${spans.plain.textContent}）`);
    assert.equal(spans.noRu.textContent, noRu, `${l}：缺該語言譯文時要退回英文`);
    assert.equal(titleEl.textContent, title, `${l}：標題語言不對（${titleEl.textContent}）`);
}

// 開著 modal 切語言：en → zh-TW 要還原含 <strong> 的原文，再切 ru 要換成俄文
lang = 'en';
await window.showLegalPage('terms');
assert.equal(spans.rich.textContent, 'Welcome to CryptoMind');
listeners.languageChanged({ detail: { language: 'zh-TW' } });
assert.equal(spans.rich.innerHTML, '歡迎使用 <strong>CryptoMind</strong>', `切回 zh-TW 要還原原文的強調／連結：${spans.rich.innerHTML}`);
listeners.languageChanged({ detail: { language: 'ru' } });
assert.equal(spans.rich.textContent, 'Добро пожаловать');
assert.equal(titleEl.textContent, 'Условия обслуживания');

console.error('legal_modal_i18n: ok');
