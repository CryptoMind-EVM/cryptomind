// 發文前的內容檢查（2026-10-01）：綠燈（或檢查服務暫時不在）才能按發文。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

class El {
    constructor() {
        this.value = '';
        this.disabled = false;
        this.dataset = {};
        this.className = '';
        this.children = [];
        this.listeners = {};
        this.textContent = '';
        const classes = new Set();
        this.classList = {
            toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
            contains: (c) => classes.has(c),
        };
    }
    setAttribute(k, v) {
        this[k] = v;
    }
    addEventListener(type, fn) {
        (this.listeners[type] ||= []).push(fn);
    }
    replaceChildren() {
        this.children = [];
    }
    append(...nodes) {
        this.children.push(...nodes);
    }
    get text() {
        return this.children.map((c) => c.textContent).join('');
    }
}
globalThis.document = { createElement: () => new El() };

const { moderationView, blockReasonText, blockedMessage, createModerationGate } = await import(
    await loadModuleUrl(new URL('../../web/js/forum-moderation.js', import.meta.url).pathname)
);
const T = {
    'forum.moderation.block': '未通過',
    'forum.moderation.reason.leaked_secret': '含有助記詞',
    'forum.moderation.reason.harmful_model': '疑似違規內容',
};
const t = (k, vars = {}) => (T[k] || k).replace(/\{\{(\w+)\}\}/g, (_, n) => vars[n]);

// ── 純函式 ────────────────────────────────────────────────────────────────
assert.deepEqual(moderationView(null), { state: 'idle', canSubmit: false });
assert.deepEqual(moderationView({ status: 'pass' }), { state: 'pass', canSubmit: true });
assert.deepEqual(moderationView({ status: 'block' }), { state: 'block', canSubmit: false });
assert.deepEqual(moderationView({ status: 'unavailable' }), { state: 'unavailable', canSubmit: true }, '服務不在照常發');
assert.equal(blockReasonText({ status: 'block', reasons: ['harmful_model'] }, t), '疑似違規內容');
assert.equal(
    blockReasonText({ status: 'block', reasons: ['leaked_secret', 'harmful_model'] }, t),
    '含有助記詞；疑似違規內容',
    '兩個原因都講'
);
assert.equal(blockReasonText({ status: 'pass' }, t), '');

// ── 閘門 ─────────────────────────────────────────────────────────────────
const tick = () => new Promise((r) => setTimeout(r, 5));
function setup() {
    const calls = [];
    const env = {
        titleEl: new El(),
        contentEl: new El(),
        statusEl: new El(),
        submitBtn: new El(),
        calls,
        // check 回一個可以手動 resolve 的 promise
        check: (title, content) => new Promise((resolve, reject) => calls.push({ title, content, resolve, reject })),
    };
    env.gate = createModerationGate({ ...env, t, debounceMs: 1 });
    env.type = (title, content) => {
        env.titleEl.value = title;
        env.contentEl.value = content;
        env.contentEl.listeners.input.forEach((fn) => fn());
    };
    return env;
}

{
    const e = setup();
    assert.equal(e.statusEl.dataset.state, 'idle');
    assert.equal(e.submitBtn.disabled, true, '還沒檢查不能發');

    e.type('BTC 看法', '短線偏空');
    assert.equal(e.statusEl.dataset.state, 'stale');
    assert.equal(e.submitBtn.disabled, true);
    await tick();
    assert.equal(e.calls.length, 1, '停下來才問一次');
    assert.equal(e.statusEl.dataset.state, 'checking');
    e.calls[0].resolve({ status: 'pass' });
    await tick();
    assert.equal(e.statusEl.dataset.state, 'pass');
    assert.equal(e.submitBtn.disabled, false, '綠燈才能按');
    assert.equal(e.submitBtn.classList.contains('opacity-50'), false);

    // 同樣內容按發文：不用再問
    assert.deepEqual(await e.gate.ensureChecked(), { state: 'pass', canSubmit: true });
    assert.equal(e.calls.length, 1);

    // 改字：馬上變回不能按；按發文時立刻重查
    e.type('BTC 看法', '請提供你的助記詞');
    assert.equal(e.submitBtn.disabled, true);
    const pending = e.gate.ensureChecked();
    await tick();
    e.calls.at(-1).resolve({ status: 'block', reasons: ['harmful_model'] });
    assert.deepEqual(await pending, { state: 'block', canSubmit: false });
    assert.equal(e.statusEl.dataset.state, 'block');
    assert.ok(e.statusEl.text.includes('疑似違規內容'), e.statusEl.text);
    assert.equal(e.submitBtn.disabled, true);
}

{
    // 晚回來的舊結果不能蓋掉新的
    const e = setup();
    e.type('t', 'A');
    e.gate.ensureChecked();
    await tick();
    e.type('t', 'B');
    e.gate.ensureChecked();
    await tick();
    const [a, b] = e.calls;
    b.resolve({ status: 'pass' });
    await tick();
    a.resolve({ status: 'block', reasons: ['harmful_model'] });
    await tick();
    assert.equal(e.statusEl.dataset.state, 'pass', 'A 的結果比較晚到也不能蓋掉 B');
    assert.equal(e.submitBtn.disabled, false);
}

{
    // 檢查請求失敗（限流、斷線）：照常讓人發
    const e = setup();
    e.type('t', 'c');
    const p = e.gate.ensureChecked();
    await tick();
    e.calls[0].reject(new Error('Status 429'));
    assert.deepEqual(await p, { state: 'unavailable', canSubmit: true });
    assert.equal(e.submitBtn.disabled, false);
    // 「暫時不在」不沿用：按發文（付款前）會再問一次，服務回來了就照實檢查
    const again = e.gate.ensureChecked();
    await tick();
    assert.equal(e.calls.length, 2, 'unavailable 要重問');
    e.calls[1].resolve({ status: 'block', reasons: ['harmful_model'] });
    assert.deepEqual(await again, { state: 'block', canSubmit: false });
}

{
    // 發文中（busy）：改字也不能把按鈕打開；結束後交回檢查決定
    const e = setup();
    e.type('t', 'c');
    await tick();
    e.calls[0].resolve({ status: 'pass' });
    await tick();
    e.gate.setBusy(true);
    e.submitBtn.disabled = true; // 發文流程自己關的
    e.type('t', 'c'); // 同內容（例如 IME 補一個 input 事件）
    assert.equal(e.submitBtn.disabled, true, '發文中不能被重新打開');
    e.gate.setBusy(false);
    assert.equal(e.submitBtn.disabled, false);

    // 伺服器說被擋：recheck 丟掉舊結果重問
    e.gate.recheck();
    await tick();
    assert.equal(e.calls.length, 2);
}

{
    // 清空：回到初始、不能發
    const e = setup();
    e.type('', '   ');
    assert.equal(e.statusEl.dataset.state, 'idle');
    assert.equal(e.submitBtn.disabled, true);
    assert.deepEqual(await e.gate.ensureChecked(), { state: 'idle', canSubmit: false });
    assert.equal(e.calls.length, 0, '空的不用問');
}

// ── 留言被擋：問 /check 拿原因（伺服器剛檢查過有快取）；拿不到就給通用訊息 ─────
assert.equal(
    await blockedMessage(async () => ({ status: 'block', reasons: ['harmful_model'] }), '', 'x', t),
    '未通過\uFF1A疑似違規內容'
);
assert.equal(
    await blockedMessage(async () => {
        throw new Error('down');
    }, '', 'x', t),
    'forum.moderation.blockedToast'
);

console.error('forum_moderation: ok');
