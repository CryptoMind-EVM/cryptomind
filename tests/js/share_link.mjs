// `/?ask=` 分享連結（web/js/share-link.js）：問題清理、連結組裝、落地預填、送出後提示消失。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

// ── 最小 DOM stub ─────────────────────────────────────────────────────────
const listeners = { input: {}, send: {} };
const note = { removed: false, remove() { this.removed = true; } };
const input = {
    value: '',
    dataset: {},
    disabled: false,
    closest: () => ({ prepend: (n) => { input._prepended = n; } }),
    dispatchEvent: () => {},
    focus: () => {},
    addEventListener: (type, fn) => { listeners.input[type] = fn; },
};
const sendBtn = { addEventListener: (type, fn) => { listeners.send[type] = fn; } };
let replaced = null;

globalThis.window = globalThis;
globalThis.Event = class { constructor(type) { this.type = type; } };
globalThis.I18n = { t: (key) => key };
globalThis.location = { origin: 'https://app.example', pathname: '/', search: '?ask=BTC%20%E6%80%8E%E9%BA%BC%E7%9C%8B&utm_source=share&x=1', hash: '#chat' };
globalThis.history = { state: null, replaceState: (_s, _t, url) => { replaced = url; } };
globalThis.document = {
    getElementById: (id) => (id === 'user-input' ? input : id === 'send-btn' ? sendBtn : null),
    createElement: () => note,
};

const mod = await import(
    await loadModuleUrl(new URL('../../web/js/share-link.js', import.meta.url).pathname)
);

// ── 1. cleanAsk：控制字元／空白壓成一格、以 code point 截斷 ─────────────────
assert.equal(mod.cleanAsk('  BTC\n\t現在  怎麼看\u0000 '), 'BTC 現在 怎麼看');
assert.equal(mod.cleanAsk(null), '');
assert.equal(Array.from(mod.cleanAsk('😀'.repeat(300))).length, 200);

// ── 2. buildAskLink：編碼問題、帶 utm_source；空問題退回首頁 ─────────────────
assert.equal(
    mod.buildAskLink('BTC 現在怎麼看'),
    'https://app.example/?ask=BTC%20%E7%8F%BE%E5%9C%A8%E6%80%8E%E9%BA%BC%E7%9C%8B&utm_source=share'
);
assert.equal(mod.buildAskLink('   '), 'https://app.example/');

// ── 3. 落地：預填、網址只留其他參數、不自動送出 ─────────────────────────────
await new Promise((resolve) => setTimeout(resolve, 450));
assert.equal(input.value, 'BTC 怎麼看', '輸入框要預填問題');
assert.equal(replaced, '/?x=1#chat', 'ask 與 utm_source 要從網址移除，其他參數保留');
assert.equal(input._prepended, note, '要顯示朋友分享的說明');

// ── 4. 送出後說明消失（Enter 與點送出鈕兩條路）─────────────────────────────
listeners.input.keydown({ key: 'a' });
assert.equal(note.removed, false, '一般按鍵不該移除說明');
listeners.input.keydown({ key: 'Enter' });
assert.equal(note.removed, true, 'Enter 送出後要移除說明');
note.removed = false;
listeners.send.click();
assert.equal(note.removed, true, '點送出鈕後要移除說明');

console.error('share_link: ok');
process.exit(0);
