// 私訊檢舉對話框的「同時封鎖」（2026-10-01 DANNY：沒勾封鎖，對方卻被封鎖了）。看守：
//   1. 沒勾 → 送出 block:false；勾了 → block:true（送出的就是畫面上的狀態）
//   2. 只有勾選框＋字能點：label 不能是滿寬的列（手機點空白收鍵盤會默默勾上）
//   3. 勾了，送出鈕寫「檢舉並封鎖」；取消勾選換回「送出檢舉」
//   4. 群組（allowBlock:false）沒有勾選框，也不送 block
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

// ── 極簡 DOM：對話框只用到這些 ─────────────────────────────
class FakeEl {
    constructor(tag) {
        this.tagName = tag.toUpperCase();
        this.children = [];
        this.parent = null;
        this.listeners = {};
        this.className = '';
        this.textContent = '';
        this.type = '';
        this.checked = false;
        this.disabled = false;
        this.value = '';
        const classes = () => new Set(this.className.split(/\s+/).filter(Boolean));
        this.classList = {
            add: (...c) => (this.className = [...new Set([...classes(), ...c])].join(' ')),
            remove: (...c) => (this.className = [...classes()].filter((x) => !c.includes(x)).join(' ')),
            contains: (c) => classes().has(c),
        };
    }
    append(...nodes) {
        for (const n of nodes) {
            n.parent = this;
            this.children.push(n);
        }
    }
    appendChild(n) {
        this.append(n);
        return n;
    }
    remove() {
        if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
    }
    setAttribute() {}
    focus() {}
    addEventListener(type, fn) {
        (this.listeners[type] ||= []).push(fn);
    }
    fire(type) {
        for (const fn of this.listeners[type] || []) fn({ target: this });
    }
    *walk() {
        yield this;
        for (const c of this.children) yield* c.walk();
    }
    all(pred) {
        return [...this.walk()].filter(pred);
    }
    querySelector(sel) {
        if (sel === 'input:checked') return this.all((n) => n.tagName === 'INPUT' && n.checked)[0] || null;
        if (sel === 'input') return this.all((n) => n.tagName === 'INPUT')[0] || null;
        throw new Error(`fake DOM 沒實作 selector: ${sel}`);
    }
}

const body = new FakeEl('body');
globalThis.window = globalThis;
globalThis.document = {
    body,
    createElement: (tag) => new FakeEl(tag),
    addEventListener() {},
    removeEventListener() {},
    getElementById: () => null,
};
globalThis.I18n = { t: (k) => k };
const toasts = [];
globalThis.showToast = (text) => toasts.push(text);
const posts = [];
globalThis.AppAPI = {
    post: async (url, payload) => {
        posts.push({ url, payload });
        return { success: true, blocked: !!payload.block };
    },
};

const { reportDmMessage } = await import(
    await loadModuleUrl(new URL('../../web/js/dm-message-actions.js', import.meta.url).pathname)
);

/** 開對話框，回傳裡面的元件；done 是 reportDmMessage 的 promise */
function open(opts = {}) {
    const blockedCalls = [];
    const done = reportDmMessage({ id: 7, text: 'hello' }, { onBlocked: () => blockedCalls.push(1), ...opts });
    const overlay = body.children[body.children.length - 1];
    const inputs = overlay.all((n) => n.tagName === 'INPUT');
    const buttons = overlay.all((n) => n.tagName === 'BUTTON');
    return {
        done,
        blockedCalls,
        radio: inputs.find((n) => n.type === 'radio'),
        box: inputs.find((n) => n.type === 'checkbox'),
        submit: buttons[buttons.length - 1],
    };
}
const flush = () => new Promise((r) => setTimeout(r, 0));

// ── 1) 沒勾：block:false、不關對話、提示「已收到檢舉」 ─────────
{
    const d = open();
    assert.equal(d.box.checked, false, '預設不勾');
    d.radio.checked = true;
    d.submit.fire('click');
    await d.done;
    assert.equal(posts.at(-1).url, '/api/messages/7/report');
    assert.equal(posts.at(-1).payload.block, false);
    assert.equal(d.blockedCalls.length, 0, '沒封鎖就不收掉對話');
    assert.equal(toasts.at(-1), 'messages.report.done');
}

// ── 2) 勾選框所在的 label 不能撐滿整列 ───────────────────────
{
    const d = open();
    const label = d.box.parent;
    assert.equal(label.tagName, 'LABEL');
    assert.ok(label.className.split(/\s+/).includes('inline-flex'), label.className);
    assert.ok(!label.className.split(/\s+/).includes('flex'), `label 是滿寬的 flex 列：${label.className}`);
    assert.ok(!/\bw-full\b/.test(label.className));

    // ── 3) 勾了 → 按鈕字變「檢舉並封鎖」，送出 block:true ──────
    assert.equal(d.submit.textContent, 'messages.report.submit');
    d.box.checked = true;
    d.box.fire('change');
    assert.equal(d.submit.textContent, 'messages.report.submitAndBlock');
    d.box.checked = false;
    d.box.fire('change');
    assert.equal(d.submit.textContent, 'messages.report.submit', '取消勾選要換回來');
    d.box.checked = true;
    d.box.fire('change');
    d.radio.checked = true;
    d.submit.fire('click');
    await d.done;
    await flush();
    assert.equal(posts.at(-1).payload.block, true);
    assert.equal(d.blockedCalls.length, 1);
    assert.equal(toasts.at(-1), 'messages.report.doneBlocked');
}

// ── 4) 群組：沒有勾選框、不送 block ──────────────────────────
{
    const d = open({ url: '/api/groups/messages/7/report', allowBlock: false });
    assert.equal(d.box, undefined, '群組不該有「同時封鎖」');
    d.radio.checked = true;
    d.submit.fire('click');
    await d.done;
    assert.equal(posts.at(-1).url, '/api/groups/messages/7/report');
    assert.ok(!('block' in posts.at(-1).payload));
}

console.log('dm_report_dialog: ok');
