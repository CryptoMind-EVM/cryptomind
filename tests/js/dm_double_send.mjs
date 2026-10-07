// 私訊「送一次、出現兩則」（2026-09-29 DANNY 回報：中文訊息每則都重複）。
// 私訊聊天室（好友頁的 SocialHub；2026-10-05 起獨立的 messages.html 已併入）同樣兩個洞：
//   1. Enter 沒擋輸入法——注音／拼音按 Enter 選字時就送出了，使用者再按一次
//      Enter 送出（chat-analysis.js 早就擋 isComposing，這兩處漏了）；
//      Safari 是 compositionend 後才發 keydown，isComposing=false、keyCode=229。
//   2. 送出中只 disable 按鈕，Enter 路徑不看按鈕——回應回來清空輸入框之前
//      再按 Enter 會用同一段文字再送一次。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

// ── 最小 DOM ───────────────────────────────────────────────
function makeEl(id = '') {
    const classes = new Set();
    return {
        id,
        value: '',
        disabled: false,
        innerHTML: '',
        textContent: '',
        style: {},
        dataset: {},
        maxLength: 500,
        scrollTop: 0,
        scrollHeight: 0,
        classList: {
            add: (...c) => c.forEach((x) => classes.add(x)),
            remove: (...c) => c.forEach((x) => classes.delete(x)),
            contains: (c) => classes.has(c),
            toggle: (c, on) => ((on ?? !classes.has(c)) ? classes.add(c) : classes.delete(c)),
        },
        addEventListener() {},
        focus() {},
        scrollIntoView() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
        insertAdjacentHTML(pos, html) { this.innerHTML += html; },
    };
}

let els = {};
const el = (id) => (els[id] ||= makeEl(id));
const resetDom = () => { els = {}; };

globalThis.window = globalThis;
globalThis.innerWidth = 1200;
globalThis.addEventListener = () => {};
globalThis.document = {
    documentElement: { lang: 'en' },
    getElementById: (id) => el(id),
    createElement: () => makeEl(),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
};
globalThis.requestAnimationFrame = (fn) => fn();
globalThis.showToast = () => {};
globalThis.I18n = { t: (k) => k };
globalThis.AppUtils = { refreshIcons() {} };
globalThis.SecurityUtils = { escapeHTML: (s) => String(s) };
globalThis.console.log = () => {};
globalThis.console.warn = () => {};

const flush = () => new Promise((r) => setImmediate(r));
function deferred() {
    let resolve;
    const promise = new Promise((res) => { resolve = res; });
    return { promise, resolve };
}
const load = async (rel) => import(await loadModuleUrl(new URL(rel, import.meta.url).pathname));

const enter = (extra = {}) => {
    const ev = { key: 'Enter', shiftKey: false, isComposing: false, keyCode: 13, prevented: false, ...extra };
    ev.preventDefault = () => { ev.prevented = true; };
    return ev;
};

// 共用情境：inputId 裡打好字 → 各種 Enter → 數 POST 次數
async function assertNoDoubleSend(label, { inputId, handler, setPost }) {
    // 1) 輸入法選字中的 Enter：不送、也不能 preventDefault（會吃掉選字）
    el(inputId).value = '啥';
    let posts = 0;
    setPost(async () => { posts++; return { success: true, message: { id: posts, content: '啥' } }; });
    const composing = enter({ isComposing: true, keyCode: 229 });
    handler(composing);
    await flush();
    assert.equal(posts, 0, `${label}：選字中的 Enter 不能送出`);
    assert.equal(composing.prevented, false, `${label}：選字中的 Enter 要交給輸入法`);

    // 2) Safari：compositionend 後才來的 keydown，isComposing=false 但 keyCode=229
    handler(enter({ keyCode: 229 }));
    await flush();
    assert.equal(posts, 0, `${label}：Safari 選字確認的 Enter（keyCode 229）不能送出`);

    // 3) 送出中再按 Enter：回應回來之前只能有一次 POST
    const d = deferred();
    posts = 0;
    setPost(() => { posts++; return d.promise; });
    handler(enter());
    handler(enter());
    await flush();
    assert.equal(posts, 1, `${label}：送出中再按 Enter 不能再送一次`);
    d.resolve({ success: true, message: { id: 1, content: '啥' } });
    await flush();
    await flush();
    assert.equal(el(inputId).value, '', `${label}：送完要清空輸入框`);

    // 4) 送完之後要能送下一則（旗標有放掉）
    el(inputId).value = '下一則';
    setPost(async () => { posts++; return { success: true, message: { id: 2, content: '下一則' } }; });
    handler(enter());
    await flush();
    await flush();
    assert.equal(posts, 2, `${label}：送完之後要能再送下一則`);
}

// ── 好友頁：SocialHub ───────────────────────────────────────
{
    resetDom();
    const { SocialHub } = await load('../../web/js/friends.js');
    globalThis.AuthManager = { currentUser: { user_id: 'alice' } }; // friends.js 內部 FriendsAPI._getUserId 讀這個
    globalThis.AppAPI = {};
    SocialHub.currentChatUserId = 'bob';
    SocialHub.loadConversations = async () => {};
    await assertNoDoubleSend('SocialHub', {
        inputId: 'social-msg-input',
        handler: (e) => SocialHub.handleInputKeydown(e),
        setPost: (fn) => { AppAPI.post = fn; },
    });
}

console.error('dm_double_send: ok');
