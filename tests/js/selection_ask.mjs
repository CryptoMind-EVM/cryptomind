// 選取句子問 AI（web/js/selection-ask.js、chat-assistant.js 的 quoteForInput）。看守：
//   1. 選取文字整理：壓空白、單字元不算、太長截斷
//   2. 按鈕位置：預設在選取範圍下方（手機原生工具列在上方）、夾在視窗內、下方放不下改放上方
//   3. 附掛：選取要整段落在 eligible 元素裡（跨兩則訊息不算）、enabled=false 不顯示、
//      按下去呼叫 onAsk 並清掉選取、同一個 root 重複 attach 只留一份、detach 乾淨
//   4. quoteForInput：「句子」＋換行；太長截斷、保證留得下問題的字數
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

// ── 最小 DOM ────────────────────────────────────────────────────────────────
function makeEl(tag = 'div', props = {}) {
    const classes = new Set();
    const listeners = {};
    const el = {
        tagName: tag.toUpperCase(),
        nodeType: 1,
        children: [],
        parent: null,
        style: {},
        attrs: {},
        textContent: '',
        offsetWidth: 80,
        offsetHeight: 30,
        classList: {
            add: (...c) => c.forEach((x) => classes.add(x)),
            remove: (...c) => c.forEach((x) => classes.delete(x)),
            contains: (c) => classes.has(c),
        },
        setAttribute(k, v) { this.attrs[k] = v; },
        addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
        fire(type, event = {}) { (listeners[type] || []).forEach((fn) => fn({ preventDefault() { event.prevented = true; }, ...event })); },
        appendChild(child) { child.parent = this; this.children.push(child); return child; },
        contains(other) { for (let n = other; n; n = n.parent) if (n === this) return true; return false; },
        closest(sel) { for (let n = this; n; n = n.parent) if (n.matchesSel?.(sel)) return n; return null; },
        get parentElement() { return this.parent; },
        ...props,
    };
    return el;
}
const docListeners = {};
const winListeners = {};
const bodyEl = makeEl('body');
globalThis.window = globalThis;
globalThis.innerWidth = 400;
globalThis.innerHeight = 800;
globalThis.document = {
    body: bodyEl,
    createElement: (tag) => makeEl(tag),
    addEventListener: (type, fn) => (docListeners[type] ||= new Set()).add(fn),
    removeEventListener: (type, fn) => docListeners[type]?.delete(fn),
};
globalThis.addEventListener = (type, fn) => (winListeners[type] ||= new Set()).add(fn);
globalThis.removeEventListener = (type, fn) => winListeners[type]?.delete(fn);
const listenerCount = () => Object.values(docListeners).reduce((n, s) => n + s.size, 0) + Object.values(winListeners).reduce((n, s) => n + s.size, 0);

let selection = null; // { text, node, rects }
globalThis.getSelection = () => ({
    get rangeCount() { return selection ? 1 : 0; },
    get isCollapsed() { return !selection; },
    getRangeAt: () => ({
        commonAncestorContainer: selection.node,
        getClientRects: () => selection.rects,
        getBoundingClientRect: () => selection.rects[0] || { left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0 },
    }),
    toString: () => selection.text,
    removeAllRanges() { selection = null; },
});

const { normalizeSelection, placeButton, attachSelectionAsk, _registeredCount } = await import(await loadModuleUrl('web/js/selection-ask.js'));

// ── 1) 選取文字整理 ──────────────────────────────────────────────────────────
assert.equal(normalizeSelection('  今天  BTC \n 很強  '), '今天 BTC 很強');
assert.equal(normalizeSelection('a'), '', '單一字元多半是手滑');
assert.equal(normalizeSelection('   '), '');
assert.equal(normalizeSelection(null), '');
const long = normalizeSelection('字'.repeat(500), 300);
assert.equal(long.length, 300);
assert.ok(long.endsWith('…'));

// ── 2) 按鈕位置 ──────────────────────────────────────────────────────────────
const view = { width: 400, height: 800 };
const size = { width: 80, height: 30 };
assert.deepEqual(placeButton({ left: 100, right: 200, top: 300, bottom: 320, width: 100 }, size, view), { left: 110, top: 328 }, '下方、置中於範圍');
assert.equal(placeButton({ left: 0, right: 20, top: 300, bottom: 320, width: 20 }, size, view).left, 8, '左邊夾在視窗內');
assert.equal(placeButton({ left: 380, right: 400, top: 300, bottom: 320, width: 20 }, size, view).left, 400 - 80 - 8, '右邊夾在視窗內');
assert.equal(placeButton({ left: 100, right: 200, top: 770, bottom: 790, width: 100 }, size, view).top, 770 - 8 - 30, '下方放不下就放上方');

// ── 3) 附掛 ──────────────────────────────────────────────────────────────────
const root = makeEl('div');
const bubbleA = makeEl('div', { matchesSel: (s) => s === '.msg-bubble' });
const bubbleB = makeEl('div', { matchesSel: (s) => s === '.msg-bubble' });
root.appendChild(bubbleA);
root.appendChild(bubbleB);
const textA = { nodeType: 3, parent: bubbleA, get parentElement() { return this.parent; } };
const outside = { nodeType: 3, parent: makeEl('div'), get parentElement() { return this.parent; } };
const asked = [];
let enabled = true;
const opts = {
    root,
    eligible: '.msg-bubble',
    label: () => '問 AI',
    enabled: () => enabled,
    onAsk: (hit) => asked.push(hit),
};
const before = listenerCount();
const detach = attachSelectionAsk(opts);
assert.equal(_registeredCount(), 1);
assert.ok(listenerCount() > before, '附掛後有監聽 selectionchange 等事件');
const detach2 = attachSelectionAsk({ ...opts, onAsk: opts.onAsk }); // 同一個 root 再 attach：只留一份
assert.equal(_registeredCount(), 1);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const trigger = async () => { (docListeners.selectionchange || []).forEach((fn) => fn()); await sleep(170); };
const btn = () => bodyEl.children.find((c) => c.attrs['data-selection-ask'] !== undefined);

// 沒有選取：沒有按鈕
await trigger();
assert.ok(!btn() || btn().classList.contains('hidden'));

// 選取落在 eligible 元素裡：浮出按鈕、文字整理過、位置在下方
selection = { text: '  那我先\n不追  ', node: textA, rects: [{ left: 100, right: 180, top: 300, bottom: 320, width: 80, height: 20 }] };
await trigger();
assert.ok(btn() && !btn().classList.contains('hidden') && btn().classList.contains('flex'), '浮出按鈕');
assert.equal(btn().textContent, '✨ 問 AI');
assert.equal(btn().style.top, '328px');
assert.equal(btn().style.zIndex, '10001', '要高過 AI 助理抽屜（10000）');

// 按下去：mousedown 不能清掉選取；click 呼叫 onAsk、清選取、收按鈕
const down = {};
btn().fire('mousedown', down);
assert.equal(down.prevented, true, '按下時不能讓選取消失');
btn().fire('click');
assert.equal(asked.length, 1);
assert.equal(asked[0].text, '那我先 不追');
assert.equal(asked[0].target, bubbleA);
assert.equal(selection, null, '送出後清掉選取');
assert.ok(btn().classList.contains('hidden'));

// 選取在容器外／不在 eligible 元素裡／AI 助理沒開：不顯示
selection = { text: '容器外的字', node: outside, rects: [{ left: 1, right: 50, top: 10, bottom: 20, width: 49, height: 10 }] };
await trigger();
assert.ok(btn().classList.contains('hidden'), '容器外不算');
selection = { text: '跨兩則訊息', node: root, rects: [{ left: 1, right: 50, top: 10, bottom: 20, width: 49, height: 10 }] };
await trigger();
assert.ok(btn().classList.contains('hidden'), '共同祖先是容器本身（跨兩則）不算');
enabled = false;
selection = { text: '助理沒開', node: textA, rects: [{ left: 1, right: 50, top: 10, bottom: 20, width: 49, height: 10 }] };
await trigger();
assert.ok(btn().classList.contains('hidden'), 'enabled=false 不顯示');
enabled = true;

// 看不見的選取（rect 全 0）不顯示；Esc 收起來
selection = { text: '零大小', node: textA, rects: [{ left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0 }] };
await trigger();
assert.ok(btn().classList.contains('hidden'));
selection = { text: '要被 Esc 收掉', node: textA, rects: [{ left: 100, right: 180, top: 300, bottom: 320, width: 80, height: 20 }] };
await trigger();
assert.ok(!btn().classList.contains('hidden'));
(docListeners.keydown || []).forEach((fn) => fn({ key: 'Escape' }));
assert.ok(btn().classList.contains('hidden'), 'Esc 收起來');

// 捲動收起來
await trigger();
assert.ok(!btn().classList.contains('hidden'));
(winListeners.scroll || []).forEach((fn) => fn());
assert.ok(btn().classList.contains('hidden'), '捲動時收起來');

// detach：只有最後一個 root 拿掉才拆監聽
detach();
assert.equal(_registeredCount(), 0);
assert.equal(listenerCount(), before, 'detach 之後監聽乾乾淨淨');
detach2(); // 重複 detach 不丟錯
assert.equal(_registeredCount(), 0);

// 沒給必要參數：什麼都不做
assert.equal(typeof attachSelectionAsk({ root: null, eligible: '.x', onAsk() {} }), 'function');
assert.equal(_registeredCount(), 0);

// ── 4) quoteForInput（chat-assistant.js）──────────────────────────────────────
globalThis.I18n = { t: (k) => k };
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
const { quoteForInput } = await import(await loadModuleUrl('web/js/chat-assistant.js'));
assert.equal(quoteForInput('那我先不追'), '「那我先不追」\n');
assert.equal(quoteForInput('  多  行\n文字 '), '「多 行 文字」\n', '空白壓成一個');
assert.equal(quoteForInput(''), '');
assert.equal(quoteForInput(null), '');
const q = quoteForInput('字'.repeat(900));
assert.ok(q.length <= 500 - 80, `引用要留 80 字給問題：${q.length}`);
assert.ok(q.startsWith('「') && q.endsWith('」\n') && q.includes('…'));

console.error('selection_ask: ok');
