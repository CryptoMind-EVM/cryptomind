// 「分享這則回答」前端（2026-10-05 任務 D）：按鈕出現條件、對話框流程、內容絕不經 innerHTML、不送答案。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

// ── 最小 DOM：innerHTML 一碰就丟錯（問題與答案是使用者／模型產生的文字）──────────
class FakeNode {
    constructor(tag) {
        this.tagName = tag.toUpperCase();
        this.children = [];
        this.attrs = {};
        this.listeners = {};
        this.className = '';
        this._text = '';
        this.removed = false;
        this.isConnected = true;
    }
    set textContent(v) { this._text = String(v); this.children = []; }
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
    set innerHTML(_v) { if (globalThis.__strictHtml) throw new Error('innerHTML 不能用在使用者／模型文字上'); }
    get innerHTML() { return ''; }
    append(...nodes) { this.children.push(...nodes); }
    appendChild(node) { this.children.push(node); return node; }
    replaceChildren(...nodes) { this._text = ''; this.children = [...nodes]; }
    setAttribute(k, v) { this.attrs[k] = v; }
    getAttribute(k) { return this.attrs[k]; }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    async click() { for (const fn of this.listeners.click || []) await fn({ target: this }); }
    remove() { this.removed = true; }
    focus() {}
    select() {}
    querySelector() { return new FakeNode('span'); }
}
const all = (node, out = []) => { out.push(node); node.children.forEach((c) => all(c, out)); return out; };
const button = (root, text) => all(root).find((n) => n.tagName === 'BUTTON' && n.textContent.trim() === text);

const body = new FakeNode('body');
globalThis.window = globalThis;
globalThis.document = {
    body,
    createElement: (tag) => new FakeNode(tag),
    addEventListener() {},
    removeEventListener() {},
};
globalThis.location = { origin: 'https://app.example', pathname: '/', search: '', hash: '' };
globalThis.history = { state: null, replaceState() {} };
globalThis.I18n = { t: (key) => `[${key}]`, getLanguage: () => 'en' };  // 回傳 [key]：看得出畫面用的是哪個翻譯 key
const toasts = [];
globalThis.showToast = (msg, kind) => toasts.push([msg, kind]);

// ── API 假的：記下每次呼叫 ───────────────────────────────────────────────────
const calls = [];
let previewResult;
let createResult;
let listResult = { items: [] };
let failNext = null;
globalThis.AppAPI = {
    getAppConfig: async () => ({ answer_share: window.__flag }),
    post: async (url, payload) => {
        calls.push(['POST', url, payload]);
        if (failNext) { const e = failNext; failNext = null; throw e; }
        return url.endsWith('/preview') ? previewResult : createResult;
    },
    get: async (url) => { calls.push(['GET', url]); return listResult; },
    delete: async (url) => { calls.push(['DELETE', url]); return { success: true }; },
};

// ── 1. 按鈕出現條件：登入＋後端旗標開＋有 session id ─────────────────────────
const shareMod = await import(await loadModuleUrl(new URL('../../web/js/share-link.js', import.meta.url).pathname));
const answerButtons = async (state) => {
    window.__flag = state.flag;
    window.currentSessionId = state.session;
    window.AuthManager = { isLoggedIn: () => state.loggedIn };
    const host = new FakeNode('div');
    shareMod.appendShareButton(host, 'BTC 現在怎麼看');
    await new Promise((r) => setTimeout(r, 0));
    return all(host).filter((n) => n.tagName === 'BUTTON').length;
};
assert.equal(await answerButtons({ loggedIn: true, flag: true, session: 's1' }), 2, '登入＋旗標開＋有對話 → 兩顆（分享問題、分享回答）');
assert.equal(await answerButtons({ loggedIn: false, flag: true, session: 's1' }), 1, '訪客沒有（答案沒存伺服器，無從驗證）');
assert.equal(await answerButtons({ loggedIn: true, flag: false, session: 's1' }), 1, '旗標關（預設）→ 不顯示');
assert.equal(await answerButtons({ loggedIn: true, flag: undefined, session: 's1' }), 1, '設定檔沒有這個欄位 → 不顯示');
assert.equal(await answerButtons({ loggedIn: true, flag: true, session: '' }), 1, '沒有 session id → 不顯示');

// ── 2. 對話框：預覽 → 建立 → 連結 → 停止分享 ─────────────────────────────────
globalThis.__strictHtml = true; // 從這裡起，對話框只要碰 innerHTML 就丟錯
const { openAnswerShare } = await import(await loadModuleUrl(new URL('../../web/js/answer-share.js', import.meta.url).pathname));
const evil = '<img src=x onerror=alert(1)>';
previewResult = { success: true, ttl_days: 30, question: 'Q ' + evil, answer: 'A <script>alert(2)</script>', redactions: 2, truncated: true };
createResult = { success: true, id: 7, url: 'https://app.example/s/' + 'T'.repeat(32), expires_at: '2026-11-04T00:00:00+00:00' };
listResult = { items: [{ id: 7, question: 'Q ' + evil, created_at: '2026-10-05T00:00:00+00:00', expires_at: '2026-11-04T00:00:00+00:00' }] };

await openAnswerShare({ sessionId: 'sess-1', question: 'Q ' + evil });
const overlay = body.children.at(-1);
const text = () => overlay.textContent;
assert.deepEqual(calls[0], ['POST', '/api/share/answers/preview', { session_id: 'sess-1', question: 'Q ' + evil }], '只送 session 與問題，不送答案');
assert.ok(text().includes('<script>alert(2)</script>') && text().includes(evil), '內容原樣當文字顯示（跳脫由 textContent 保證）');
assert.ok(text().includes('[answerShare.redacted]'), '顯示遮蔽處數');
assert.ok(text().includes('[answerShare.reviewNotice]'), '提醒確認沒有不想公開的資訊');
assert.ok(text().includes('[answerShare.truncated]'), '被截斷要說');
assert.ok(!calls.some((c) => c[0] === 'POST' && c[1] === '/api/share/answers'), '預覽階段還沒建立任何東西');

await button(overlay, '[answerShare.create]').click();
assert.deepEqual(calls.find((c) => c[0] === 'POST' && c[1] === '/api/share/answers'), ['POST', '/api/share/answers', { session_id: 'sess-1', question: 'Q ' + evil }]);
const link = all(overlay).find((n) => n.tagName === 'INPUT');
assert.equal(link.value, createResult.url, '建立後顯示連結');
assert.ok(button(overlay, '[answerShare.copyLink]'));
assert.ok(text().includes('[answerShare.deleteNote]') && text().includes('[answerShare.previewCacheNote]'), '要說清楚：刪對話不會讓連結失效、停止分享後第三方預覽可能還在');

await button(overlay, '[answerShare.stop]').click();
assert.ok(calls.some((c) => c[0] === 'DELETE' && c[1] === '/api/share/answers/7'), '停止分享 → DELETE 那一筆');
assert.ok(toasts.some(([msg, kind]) => kind === 'success'), '撤銷成功有提示');

// ── 3. 錯誤：預覽 404／429 顯示對應文案，不留下半套畫面 ───────────────────────
for (const [status, key] of [[404, 'answerShare.errNotFound'], [429, 'answerShare.errLimit'], [409, 'answerShare.errTooMany'], [500, 'answerShare.errGeneric']]) {
    failNext = Object.assign(new Error('x'), { status });
    await openAnswerShare({ sessionId: 's', question: 'q' });
    const o = body.children.at(-1);
    assert.ok(o.textContent.includes(key), `${status} → ${key}`);
    assert.equal(button(o, '[answerShare.create]'), undefined, '預覽失敗就沒有「建立」按鈕');
}

console.error('answer_share_ui: ok');
process.exit(0);
