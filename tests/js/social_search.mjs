// 社群搜尋（2026-10-01：找人、找群組、找訊息）。看守：
//   1. 標記關鍵字：不分大小寫、先切再轉義（不會切到 &amp;）、正規式特殊字元照字面
//   2. 打字停 300ms 才打 API；結果三區（聯絡人／群組／訊息）；沒結果有空狀態
//   3. 搜尋中來訊重畫列表不能洗掉結果；清掉搜尋回到列表
//   4. 點訊息：打開對話、載完往前翻（一次 100 則）跳到那則（桌機、手機都在好友頁開）
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

function makeEl(id = '') {
    const classes = new Set();
    const attrs = {};
    return {
        id,
        value: '',
        innerHTML: '',
        textContent: '',
        dataset: {},
        scrollTop: 0,
        classList: {
            add: (...c) => c.forEach((x) => classes.add(x)),
            remove: (...c) => c.forEach((x) => classes.delete(x)),
            contains: (c) => classes.has(c),
            toggle: (c, on) => ((on ?? !classes.has(c)) ? classes.add(c) : classes.delete(c)),
        },
        setAttribute: (k, v) => (attrs[k] = String(v)),
        getAttribute: (k) => attrs[k],
        hasAttribute: (k) => k in attrs,
        toggleAttribute: (k, on) => (on ? (attrs[k] = '') : delete attrs[k]),
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
globalThis.innerWidth = 1200;
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
globalThis.I18n = { t: (k) => k }; // 只回 key：social-search.js 會改用英文備用字
globalThis.AppUtils = { refreshIcons() {} };
globalThis.SecurityUtils = { escapeHTML: (s) => String(s) };
globalThis.AuthManager = { currentUser: { user_id: 'me' } };
globalThis.sessionStorage = { setItem() {} };
globalThis.console.log = () => {};
const toasts = [];
globalThis.showToast = (text) => toasts.push(text);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const searches = [];
globalThis.AppAPI = {
    get: async (url) => {
        if (url.startsWith('/api/chat-search')) {
            const q = new URL(url, 'http://x').searchParams.get('q');
            searches.push(q);
            if (q === 'none') return { success: true, contacts: [], groups: [], messages: [] };
            return {
                success: true,
                contacts: [{ user_id: 'bob', username: 'bob_1', display_name: '鮑伯 Bob', conversation_id: 3 }],
                groups: [{ id: 7, name: 'Bob 的群', member_count: 4 }],
                messages: [
                    { kind: 'dm', message_id: 55, conversation_id: 3, group_id: null, other_user_id: 'bob', chat_name: '鮑伯 Bob', from_user_id: 'bob', from_name: '鮑伯 Bob', snippet: 'hi BOB & co', created_at: '2026-10-01T10:00:00Z' },
                    { kind: 'dm', message_id: 56, conversation_id: 3, group_id: null, other_user_id: 'bob', chat_name: '鮑伯 Bob', from_user_id: 'me', from_name: '我', snippet: 'bob?', created_at: '2026-10-01T09:00:00Z' },
                    { kind: 'group', message_id: 90, conversation_id: null, group_id: 7, other_user_id: null, chat_name: 'Bob 的群', from_user_id: 'amy', from_name: 'Amy', snippet: 'ask bob', created_at: '2026-10-01T08:00:00Z' },
                ],
            };
        }
        if (url.startsWith('/api/messages/conversations')) {
            return { success: true, total_unread: 0, conversations: [{ id: 3, other_user_id: 'bob', other_username: 'bob_1', last_message_at: '2026-10-01T10:00:00Z', pin_position: null }] };
        }
        return { success: true, groups: [], invites: [] };
    },
};

const load = async (rel) => import(await loadModuleUrl(new URL(rel, import.meta.url).pathname));
const { highlight, JUMP_PAGE_SIZE } = await load('../../web/js/social-search.js');
const { SocialHub } = await load('../../web/js/friends.js');
const list = () => el('social-conv-list').innerHTML;

// ── 1) 標記 ─────────────────────────────────────────────
assert.equal(highlight('Hi BOB & bob', 'bob'), 'Hi <mark class="bg-primary/20 text-inherit rounded-sm px-0.5">BOB</mark> &amp; <mark class="bg-primary/20 text-inherit rounded-sm px-0.5">bob</mark>');
assert.equal(highlight('a<b>', 'x'), 'a&lt;b&gt;', '沒命中也要轉義');
assert.ok(highlight('1+1=2 (ok)', '1+1').includes('<mark class="bg-primary/20 text-inherit rounded-sm px-0.5">1+1</mark>'), '正規式特殊字元照字面');
assert.ok(!highlight('a&amp;b', 'amp').includes('&<mark'), '先切再轉義');

// ── 2) 打字 → 300ms 後搜尋 ───────────────────────────────
await SocialHub.loadConversations();
assert.ok(list().includes('data-conversation-id="3"'));
SocialHub.onSearchInput('b');
SocialHub.onSearchInput('bo');
SocialHub.onSearchInput('bob');
assert.equal(searches.length, 0, '還在打字就不打 API');
assert.equal(el('social-conv-sidebar').hasAttribute('data-searching'), true, '搜尋中分類列收起來');
await sleep(350);
assert.deepEqual(searches, ['bob'], '只打最後一次');
assert.ok(list().includes('People · 1') && list().includes('Groups · 1') && list().includes('Messages · 3'), '三區都有、帶筆數');
assert.ok(list().includes('data-user-id="bob"') && list().includes('data-click="SocialHub.openGroup" data-click-arg="7"'));
assert.ok(list().includes('&amp;'), '片段要轉義');
assert.ok(list().includes('You: '), '自己說的標「你」');
assert.ok(list().includes('Amy: '), '群組訊息寫誰說的');

// ── 3) 搜尋中來訊：列表重畫不能洗掉結果 ─────────────────────
await SocialHub.loadConversations();
assert.ok(list().includes('Messages · 3'), '搜尋結果還在');
SocialHub.clearSearch();
assert.ok(list().includes('data-conversation-id="3"') && !list().includes('Messages · 3'), '清掉回到列表');
assert.equal(el('social-conv-sidebar').hasAttribute('data-searching'), false);

SocialHub.onSearchInput('none');
await sleep(350);
assert.ok(list().includes('No results'), '沒結果有空狀態');
SocialHub.onSearchInput('   ');
assert.ok(list().includes('data-conversation-id="3"'), '刪光（只剩空白）回到列表');

// ── 4) 點訊息 ───────────────────────────────────────────
{
    const opened = [];
    const realOpen = SocialHub.openConversation;
    SocialHub.openConversation = (userId, name) => opened.push([userId, name]);
    SocialHub.openSearchMessage('dm', 'bob', 55, '鮑伯 Bob');
    assert.deepEqual(opened, [['bob', '鮑伯 Bob']]);
    assert.deepEqual(SocialHub._pendingJump, { key: 'dm:bob', messageId: 55 });

    // 第一頁沒有那則：提示、往前翻（一次 100 則）直到出現，捲過去
    const row = { getBoundingClientRect: () => ({ top: 900 }), querySelector: () => ({ classList: { add() {}, remove() {} } }) };
    let loaded = 0;
    const msgs = {
        scrollTop: 0,
        clientHeight: 600,
        getBoundingClientRect: () => ({ top: 100 }),
        querySelector: (sel) => (sel === '[data-message-id="55"]' && loaded >= 2 ? row : null),
    };
    els['social-messages-container'] = msgs;
    const pages = [];
    SocialHub.loadMoreMessages = async (size) => (pages.push(size), (loaded += 1), true);
    SocialHub.currentConversationId = 3;
    SocialHub.runPendingJump('dm:other');
    assert.ok(SocialHub._pendingJump, '別的對話載完不能把它用掉');
    SocialHub.runPendingJump('dm:bob');
    assert.equal(toasts.at(-1), 'Finding that message…');
    await sleep(10);
    assert.deepEqual(pages, [JUMP_PAGE_SIZE, JUMP_PAGE_SIZE]);
    assert.equal(JUMP_PAGE_SIZE, 100);
    assert.equal(msgs.scrollTop, 900 - 100 - 200, '捲到那則（離頂三分之一）');
    assert.equal(SocialHub._pendingJump, null);
    SocialHub.openConversation = realOpen;

    // 群組：打開群組、等 loadGroup 叫
    const groups = [];
    SocialHub.openGroup = (id) => groups.push(id);
    SocialHub.openSearchMessage('group', 7, 90);
    assert.deepEqual(groups, [7]);
    assert.deepEqual(SocialHub._pendingJump, { key: 'group:7', messageId: 90 });

    // 手機：跟桌機一樣在好友頁開、載完跳到那則（私訊聊天室統一成同一個，不再跳另一頁，2026-10-05）
    globalThis.innerWidth = 375;
    const navs = [];
    globalThis.smoothNavigate = (url) => navs.push(url);
    const mobileOpened = [];
    SocialHub.openConversation = (id, name) => mobileOpened.push([id, name]);
    SocialHub.openSearchMessage('dm', 'bob', 55, '鮑伯 Bob');
    assert.deepEqual(navs, [], '不能再跳去別的頁');
    assert.deepEqual(mobileOpened, [['bob', '鮑伯 Bob']]);
    assert.deepEqual(SocialHub._pendingJump, { key: 'dm:bob', messageId: 55 });
}

console.error('social_search: ok');
