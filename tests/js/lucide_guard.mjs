// lucide.createIcons 防重畫（web/js/lucide-guard.js，2026-10-02 切回視窗會閃、操作不順）。看守：
//   1. 畫好的 <svg> 不再被砍掉重畫（原版每次呼叫整頁 300+ 個圖示全部重來）
//   2. 直接改 svg 的 data-lucide（ThemeSwitcher 換太陽／月亮）照樣會換，換回來也會
//   3. { nodes: [容器] }（全站 refreshIcons(container)／createIconsIn 的寫法；0.577 根本沒這個選項）只畫容器裡的
//   4. 裝這層之前就畫好的 svg 不重畫；暫存屬性不殘留
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

class El {
    constructor(tag, attrs = {}) {
        this.tagName = tag;
        this.attrs = new Map(Object.entries(attrs));
        this.children = [];
        this.parent = null;
    }
    getAttribute(k) {
        return this.attrs.has(k) ? this.attrs.get(k) : null;
    }
    setAttribute(k, v) {
        this.attrs.set(k, String(v));
    }
    removeAttribute(k) {
        this.attrs.delete(k);
    }
    get classList() {
        const list = (this.getAttribute('class') || '').split(/\s+/).filter(Boolean);
        return { contains: (c) => list.includes(c) };
    }
    append(...kids) {
        kids.forEach((k) => {
            k.parent = this;
            this.children.push(k);
        });
        return this;
    }
    replaceWith(node) {
        const i = this.parent.children.indexOf(this);
        node.parent = this.parent;
        this.parent.children[i] = node;
        this.parent = null;
    }
    querySelectorAll(sel) {
        const m = /^\[([\w-]+)\]$/.exec(sel);
        assert.ok(m, `fake DOM 只支援 [attr]：${sel}`);
        const out = [];
        const walk = (n) => n.children.forEach((c) => (c.attrs.has(m[1]) && out.push(c), walk(c)));
        walk(this);
        return out;
    }
}

// 仿 lucide 0.577 createIcons／replaceElement：svg 帶 data-lucide＋原元素的屬性與 class（所以下次還會被掃到）
let replaced = 0;
const fakeLucide = {
    createIcons({ nameAttr = 'data-lucide', root = document, attrs = {} } = {}) {
        root.querySelectorAll(`[${nameAttr}]`).forEach((el) => {
            const name = el.getAttribute(nameAttr);
            const elementAttrs = Object.fromEntries(el.attrs);
            const cls = ['lucide', `lucide-${name}`, ...(elementAttrs.class || '').split(/\s+/)].filter(Boolean);
            const svg = new El('svg', { 'data-lucide': name, ...attrs, ...elementAttrs, class: [...new Set(cls)].join(' ') });
            el.replaceWith(svg);
            replaced++;
        });
    },
};

const body = new El('body');
globalThis.window = globalThis;
globalThis.document = body;
globalThis.addEventListener = () => {};
globalThis.lucide = fakeLucide;

const { guardLucide } = await import(await loadModuleUrl(new URL('../../web/js/lucide-guard.js', import.meta.url).pathname));
assert.ok(fakeLucide.createIcons.__guarded, '載入就裝上（lucide 已在）');
guardLucide(fakeLucide); // 重複裝不疊兩層

const a = new El('div');
const b = new El('div');
a.append(new El('i', { 'data-lucide': 'sun', class: 'w-4 h-4' }));
b.append(new El('i', { 'data-lucide': 'x' }));
body.append(a, b);
const leftovers = () =>
    body.querySelectorAll('[data-lucide]').filter((n) => n.attrs.has('data-lucide-pending')).length;

// 3) { nodes } 當範圍
replaced = 0;
lucide.createIcons({ nodes: [a] });
assert.equal(replaced, 1, '只畫 a 裡的');
assert.equal(a.children[0].tagName, 'svg');
assert.equal(b.children[0].tagName, 'i', 'b 沒被碰');
lucide.createIcons({ root: b });
assert.equal(b.children[0].tagName, 'svg');

// 1) 全頁再叫：畫好的不動
const sunSvg = a.children[0];
replaced = 0;
lucide.createIcons();
lucide.createIcons();
assert.equal(replaced, 0, '已經畫好的不重畫');
assert.equal(a.children[0], sunSvg, '同一個節點（沒被換掉）');
assert.equal(sunSvg.getAttribute('class').includes('w-4'), true, '原本的 class 留著');
assert.equal(leftovers(), 0, '暫存屬性清掉');

// 2) 直接改 svg 的名字：要換；換回來也要換（class 會殘留舊的 lucide-xxx，不能拿 class 判斷）
sunSvg.setAttribute('data-lucide', 'moon');
replaced = 0;
lucide.createIcons();
assert.equal(replaced, 1);
const moonSvg = a.children[0];
assert.equal(moonSvg.getAttribute('data-lucide-rendered'), 'moon');
moonSvg.setAttribute('data-lucide', 'sun');
replaced = 0;
lucide.createIcons();
assert.equal(replaced, 1, '換回太陽也要重畫');
assert.equal(a.children[0].getAttribute('data-lucide-rendered'), 'sun');

// 4) 裝這層之前畫好的 svg（沒有 rendered 標記，但 class 對得上）：不重畫
const old = new El('svg', { 'data-lucide': 'bell', class: 'lucide lucide-bell' });
body.append(old);
replaced = 0;
lucide.createIcons();
assert.equal(replaced, 0, '舊的已畫好的不重來');
assert.equal(leftovers(), 0);

console.error('lucide_guard: ok');
