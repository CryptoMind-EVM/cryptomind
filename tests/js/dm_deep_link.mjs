// 私訊聊天室統一（2026-10-05）：個人頁「發訊息」、社群搜尋、舊的 messages.html 網址，全部進好友頁
// （SocialHub）那一個聊天室。看守 /?chat=<userId>[&msg=<id>]#friends 深連結：
//   1. 開對話、msg 帶了就等著跳到那則；參數用完從網址拿掉（重新整理不會再開一次）
//   2. 名字先留空、查到再補標題列（對話列表有就用，沒聊過的好友查公開資料）；中途換了對話不能補錯人
//   3. openChat／openConversation 不分寬度：手機也在本頁開，不再跳去別的頁
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

function makeEl(id = '') {
    const classes = new Set();
    return {
        id,
        value: '',
        innerHTML: '',
        textContent: '',
        href: '',
        dataset: {},
        scrollTop: 0,
        classList: {
            add: (...c) => c.forEach((x) => classes.add(x)),
            remove: (...c) => c.forEach((x) => classes.delete(x)),
            contains: (c) => classes.has(c),
            toggle: (c, on) => ((on ?? !classes.has(c)) ? classes.add(c) : classes.delete(c)),
        },
        setAttribute() {},
        getAttribute: () => null,
        hasAttribute: () => false,
        addEventListener() {},
        querySelector: () => null,
        querySelectorAll: () => [],
        blur() {},
        focus() {},
    };
}
const els = {};
const el = (id) => (els[id] ||= makeEl(id));
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.document = {
    documentElement: { lang: 'zh-TW' },
    visibilityState: 'visible',
    getElementById: (id) => el(id),
    createElement: () => makeEl(),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    removeEventListener() {},
};
globalThis.I18n = { t: (k) => k };
globalThis.AppUtils = { refreshIcons() {} };
globalThis.SecurityUtils = { escapeHTML: (s) => String(s) };
globalThis.AuthManager = { currentUser: { user_id: 'me', uid: 'me' } };
globalThis.sessionStorage = { setItem() {}, getItem: () => null, removeItem() {} };
globalThis.console.log = () => {};
globalThis.AppAPI = { get: async () => ({ success: true, conversations: [], groups: [], invites: [] }), post: async () => ({ success: true }) };

const load = async (rel) => import(await loadModuleUrl(new URL(rel, import.meta.url).pathname));
const { SocialHub } = await load('../../web/js/friends.js');

const navs = [];
globalThis.smoothNavigate = (url) => navs.push(url);

const setUrl = (search, hash = '#friends') => {
    globalThis.location = { pathname: '/', search, hash, href: `/${search}${hash}` };
    const replaced = [];
    globalThis.history = { state: { tab: 'friends' }, replaceState: (_s, _t, url) => replaced.push(url) };
    return replaced;
};

const opened = [];
const subTabs = [];
SocialHub.openConversation = (id, name) => opened.push([id, name]);
SocialHub.switchSubTab = (tab) => subTabs.push(tab);

// ── 1) 沒有 chat 參數：什麼都不做 ───────────────────────────────────────
let replaced = setUrl('?group=9');
SocialHub.openChatFromUrl();
assert.deepEqual(opened, []);
assert.deepEqual(replaced, [], '沒有 chat 參數不能動網址（group 深連結要留給 openGroupFromUrl）');

// ── 2) /?chat=bob&msg=55：開對話、等著跳到那則、參數用完拿掉 ────────────
SocialHub.activeSubTab = 'friends';
replaced = setUrl('?chat=bob&msg=55&utm=x');
SocialHub.openChatFromUrl();
assert.deepEqual(subTabs, ['messages'], '不在訊息分頁就切過去');
assert.deepEqual(opened, [['bob', '']], '名字先留空，等伺服器回應補');
assert.deepEqual(SocialHub._pendingJump, { key: 'dm:bob', messageId: 55 });
assert.deepEqual(replaced, ['/?utm=x#friends'], '只拿掉 chat／msg，其他參數與 hash 留著');

// ── 3) 使用者 id 要 encode（跟對話列表的 data-user-id 同一種寫法）、沒有 msg 就不排跳轉 ──
opened.length = 0;
SocialHub._pendingJump = null;
replaced = setUrl('?chat=tg%3A123%20x');
SocialHub.openChatFromUrl();
assert.deepEqual(opened, [['tg%3A123%20x', '']]);
assert.equal(SocialHub._pendingJump, null);
assert.deepEqual(replaced, ['/#friends']);

// 壞掉的 msg（非數字）當作沒有
opened.length = 0;
setUrl('?chat=bob&msg=abc');
SocialHub.openChatFromUrl();
assert.equal(SocialHub._pendingJump, null);

// ── 4) openChat 不分寬度：手機也在本頁開，不能跳去別的頁 ─────────────────────
for (const width of [375, 767, 768, 1280]) {
    globalThis.innerWidth = width;
    opened.length = 0;
    subTabs.length = 0;
    globalThis.SocialHub = SocialHub; // friends.js 的 FriendsUI.openChat 靠全域名稱找
    window.FriendsUI.openChat('bob', 'Bob');
    assert.deepEqual(opened, [['bob', 'Bob']], `寬度 ${width}：在好友頁開`);
    assert.deepEqual(subTabs, ['messages']);
}
assert.deepEqual(navs, [], '任何寬度都不能再導去別的頁');

// ── 5) 名字：對話列表有就用；沒有（還沒聊過的好友）查公開資料；都沒有就留 id ──────────
const shown = [];
SocialHub.showChatContent = (id, name) => shown.push([id, name]);
const profileCalls = [];
globalThis.FriendsAPI.getProfile = async (id) => {
    profileCalls.push(id);
    if (id === 'ghost') throw new Error('404');
    return { success: true, profile: { user_id: id, username: `${id}_1`, display_name: id === 'newbie' ? '新朋友' : null } };
};

SocialHub._convData = { conversations: [{ other_user_id: 'bob', other_username: 'bob_1', other_display_name: '鮑伯 Bob' }] };
assert.equal(await SocialHub._resolveChatName('bob'), '鮑伯 Bob', '對話列表有就用（優先顯示名稱）');
assert.deepEqual(profileCalls, [], '對話列表有就不用多打 API');
assert.equal(await SocialHub._resolveChatName('newbie'), '新朋友', '沒聊過：查公開資料');
assert.equal(await SocialHub._resolveChatName('amy'), 'amy_1', '沒有顯示名稱就用 username');
assert.equal(await SocialHub._resolveChatName('ghost'), '', '查不到（404／網路）回空字串，不丟錯');

// 深連結開對話後，名字查到就補標題列；中途換了對話不能把名字塞錯人
opened.length = 0;
SocialHub.currentChatUserId = null;
SocialHub.currentChatUsername = null;
SocialHub.openConversation = (id, name) => {
    opened.push([id, name]);
    SocialHub.currentChatUserId = id; // 真的 openConversation 會做的事
    SocialHub.currentChatUsername = name;
};
setUrl('?chat=newbie');
SocialHub.openChatFromUrl();
await new Promise((r) => setTimeout(r, 10));
assert.deepEqual(opened, [['newbie', '']]);
assert.deepEqual(shown, [['newbie', '新朋友']], '名字查到後補上標題列');
assert.equal(SocialHub.currentChatUsername, '新朋友');

shown.length = 0;
SocialHub.currentChatUserId = null;
SocialHub.currentChatUsername = null;
setUrl('?chat=bob');
SocialHub.openChatFromUrl();
SocialHub.currentChatUserId = 'carol'; // 名字還在查的時候使用者點了別的對話
SocialHub.currentChatUsername = 'Carol';
await new Promise((r) => setTimeout(r, 10));
assert.deepEqual(shown, [], '已經換對話就不能蓋標題列');

// ── 6) 一次一欄時進到聊天就收起上面的「社群」標題與分頁列，回清單再放回來 ───────────
{
    const panes = el('social-content-messages');
    const tops = [makeEl('top1'), makeEl('top2')];
    const realQSA = document.querySelectorAll;
    document.querySelectorAll = (sel) => (sel === '[data-social-top]' ? tops : []);
    SocialHub.setPane('chat');
    assert.equal(panes.dataset.pane, 'chat');
    assert.ok(tops.every((t) => t.classList.contains('max-lg:hidden')), '聊天中：標題與分頁列只在窄螢幕收起（max-lg）');
    SocialHub.setPane('list');
    assert.equal(panes.dataset.pane, 'list');
    assert.ok(tops.every((t) => !t.classList.contains('max-lg:hidden')), '回清單：放回來，才切得了分頁');
    document.querySelectorAll = realQSA;
}

console.error('dm_deep_link: ok');
