// 社群對話列表的分頁與分類（2026-10-01：以前只抓 20 個私訊、沒有下一頁，第 21 個起從列表消失）。看守：
//   1. 一次 20 個；捲到底多 20；重畫照目前載到的數量抓；超過後端上限 100 分段一起抓
//   2. 還有更舊的私訊沒載時，比已載最舊私訊還舊的群組先不放（不然往下捲時群組插隊到中間）
//   3. 分類：全部／私訊／群組；記在 localStorage；群組邀請不放「私訊」；開關關著固定「全部」
//   4. 同時好幾次在路上：只畫最後發出的那次
//   5. 分類鈕上的未讀數
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

function makeEl(id = '') {
    const classes = new Set();
    const attrs = {};
    return {
        id,
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
        addEventListener() {},
        querySelector: () => null,
        querySelectorAll: () => [],
    };
}
const els = {};
const el = (id) => (els[id] ||= makeEl(id));
// 分類鈕（模板裡的 [data-conv-filter]）：每顆有自己的未讀徽章
const filterBtns = ['all', 'dm', 'group'].map((key) => {
    const btn = makeEl();
    btn.dataset.convFilter = key;
    const badge = key === 'all' ? null : makeEl();
    btn.querySelector = (sel) => (sel === '[data-filter-unread]' ? badge : null);
    btn.badge = badge;
    return btn;
});

const store = {};
globalThis.window = globalThis;
globalThis.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => (store[k] = String(v)),
};
globalThis.addEventListener = () => {};
globalThis.document = {
    documentElement: { lang: 'zh-TW' },
    visibilityState: 'visible',
    getElementById: (id) => el(id),
    createElement: () => makeEl(),
    querySelector: () => null,
    querySelectorAll: (sel) => (sel === '[data-conv-filter]' ? filterBtns : []),
    addEventListener() {},
    removeEventListener() {},
};
globalThis.I18n = { t: (k) => k };
globalThis.AppUtils = { refreshIcons() {} };
globalThis.SecurityUtils = { escapeHTML: (s) => String(s) };
globalThis.AuthManager = { currentUser: { user_id: 'me' } };
globalThis.console.log = () => {};

// 45 個私訊：第 i 個的最後一則在 i 小時前（新的在前）
const BASE = Date.parse('2026-10-01T12:00:00Z');
const hoursAgo = (h) => new Date(BASE - h * 3600e3).toISOString();
const ALL_CONVS = Array.from({ length: 45 }, (_, i) => ({
    id: i + 1,
    other_user_id: `u${i + 1}`,
    other_username: `user${i + 1}`,
    last_message: 'hi',
    unread_count: 0,
    last_message_at: hoursAgo(i + 1),
}));
const api = { convRequests: [], groupsStatus: 200, delay: null };
const groups = [
    { id: 7, name: '新群', member_count: 3, unread_count: 2, last_message_at: hoursAgo(0.5), last_message: null },
    // 比第 20 個私訊（20 小時前）還舊：第一頁不該出現
    { id: 8, name: '老群', member_count: 2, unread_count: 0, last_message_at: hoursAgo(30), last_message: null },
];
globalThis.AppAPI = {
    get: async (url) => {
        if (url.startsWith('/api/messages/conversations')) {
            const q = new URL(url, 'http://x').searchParams;
            const limit = Number(q.get('limit'));
            const offset = Number(q.get('offset'));
            api.convRequests.push([limit, offset]);
            if (api.delay) await api.delay();
            return { success: true, total_unread: 5, conversations: ALL_CONVS.slice(offset, offset + limit) };
        }
        if (url === '/api/groups') {
            if (api.groupsStatus === 404) throw Object.assign(new Error('Not Found'), { status: 404 });
            return { success: true, groups };
        }
        if (url === '/api/groups/invites') {
            return { success: true, invites: [{ invite_id: 1, group_id: 99, group_name: '邀請', member_count: 1, inviter_name: 'x' }] };
        }
        return { success: true };
    },
};

const load = async (rel) => import(await loadModuleUrl(new URL(rel, import.meta.url).pathname));
const { SocialHub } = await load('../../web/js/friends.js');
const list = () => el('social-conv-list').innerHTML;
const dmCount = () => (list().match(/data-conversation-id="\d+"/g) || []).length;

// ── 1) 第一頁 20 個、有「載入中」；捲到底多 20；最後一頁沒有「載入中」 ──
{
    await SocialHub.loadConversations();
    assert.deepEqual(api.convRequests.at(-1), [20, 0]);
    assert.equal(dmCount(), 20);
    assert.ok(list().includes('data-conv-more'), '還有下一頁要有「載入中」');
    assert.ok(list().includes('g-7') && !list().includes('g-8'), '比已載最舊私訊還舊的群組先不放');

    await SocialHub.loadMoreConversations();
    assert.deepEqual(api.convRequests.at(-1), [40, 0], '重畫照目前載到的數量一次抓');
    assert.equal(dmCount(), 40);

    await SocialHub.loadMoreConversations();
    assert.deepEqual(api.convRequests.at(-1), [60, 0]);
    assert.equal(dmCount(), 45);
    assert.ok(!list().includes('data-conv-more'), '沒有更多了');
    assert.ok(list().includes('g-8'), '全部載完，舊群組出現');
    const n = api.convRequests.length;
    await SocialHub.loadMoreConversations();
    assert.equal(api.convRequests.length, n, '沒有更多就不再打');

    // 來訊重畫：數量不縮回 20
    await SocialHub.loadConversations();
    assert.equal(dmCount(), 45);
}

// ── 1b) 超過後端上限 100：分段一起抓 ──────────────────
{
    SocialHub.convLimit = 120;
    await SocialHub.loadConversations();
    assert.deepEqual(api.convRequests.slice(-2), [
        [100, 0],
        [20, 100],
    ]);
    SocialHub.convLimit = 20;
    await SocialHub.loadConversations();
}

// ── 3) 分類 ──────────────────────────────────────────
{
    const active = () => filterBtns.filter((b) => b.getAttribute('aria-selected') === 'true').map((b) => b.dataset.convFilter);
    assert.deepEqual(active(), ['all']);
    assert.ok(list().includes('data-invite-id="1"'), '「全部」有群組邀請');

    const reqs = api.convRequests.length;
    SocialHub.setConvFilter('dm');
    assert.equal(api.convRequests.length, reqs, '換分類不重抓');
    assert.deepEqual(active(), ['dm']);
    assert.equal(store.socialConvFilter, 'dm');
    assert.ok(!list().includes('g-7') && !list().includes('data-invite-id'), '「私訊」沒有群組、也沒有群組邀請');
    assert.equal(dmCount(), 20);
    assert.ok(list().includes('data-conv-more'));

    SocialHub.setConvFilter('group');
    assert.equal(dmCount(), 0);
    assert.ok(list().includes('g-7') && list().includes('g-8'), '「群組」不分頁，舊群組也在');
    assert.ok(list().includes('data-invite-id="1"'));
    assert.ok(!list().includes('data-conv-more'), '「群組」不需要往下載私訊');

    SocialHub.setConvFilter('bogus');
    assert.equal(SocialHub.convFilter, 'group', '不認得的分類不理');

    // 群組開關關著：只有私訊，分類失效（固定全部）
    api.groupsStatus = 404;
    SocialHub.groupsEnabled = null;
    await SocialHub.loadConversations();
    assert.equal(dmCount(), 20, '開關關著時就算記著「群組」也照樣列私訊');
    api.groupsStatus = 200;
    SocialHub.groupsEnabled = null;
    SocialHub.setConvFilter('all');
}

// ── 5) 分類鈕的未讀數：私訊用 total_unread、群組加總 ─────
{
    await SocialHub.loadConversations();
    const [, dm, group] = filterBtns;
    assert.equal(dm.badge.textContent, '5');
    assert.equal(dm.badge.classList.contains('hidden'), false);
    assert.equal(group.badge.textContent, '2');
}

// ── 4) 先發出的那次比較晚回來：不能蓋掉後面那次 ───────────
{
    let release;
    api.delay = () => new Promise((r) => (release = r));
    const slow = SocialHub.loadConversations(); // 第一次：卡住
    await new Promise((r) => setTimeout(r, 0));
    const releaseSlow = release;
    api.delay = null;
    SocialHub.convLimit = 40;
    await SocialHub.loadConversations(); // 第二次：40 個，先回來
    assert.equal(dmCount(), 40);
    releaseSlow();
    await slow;
    assert.equal(dmCount(), 40, '舊的那次（20 個）回來了也不能畫');
}

console.error('social_list_paging: ok');
