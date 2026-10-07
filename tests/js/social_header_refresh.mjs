// 社群頁標題列（2026-10-01：DANNY 手機上看到重整鈕旁一顆沒有說明的紅色「0」，按重整也像卡住）。看守：
//   1. 標題列沒有那顆不知道在數什麼的數字；待回覆的好友邀請數掛在「好友」分頁鈕上，0 不顯示
//   2. 在訊息分頁一打開就抓邀請數（不用先點進好友分頁才知道有人加你）
//   3. 按重整：轉圈＋鎖住按鈕直到資料回來；連按不重複打；抓失敗也要停止轉圈
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { loadModuleUrl } from './_load.mjs';

function makeEl(id = '') {
    const classes = new Set();
    const attrs = {};
    const el = {
        id,
        innerHTML: '',
        textContent: '',
        dataset: {},
        disabled: false,
        scrollTop: 0,
        classList: {
            add: (...c) => c.forEach((x) => classes.add(x)),
            remove: (...c) => c.forEach((x) => classes.delete(x)),
            contains: (c) => classes.has(c),
            toggle: (c, on) => ((on ?? !classes.has(c)) ? classes.add(c) : classes.delete(c)),
        },
        setAttribute: (k, v) => (attrs[k] = String(v)),
        removeAttribute: (k) => delete attrs[k],
        getAttribute: (k) => attrs[k],
        addEventListener() {},
        blur() {},
        querySelector: () => null,
        querySelectorAll: () => [],
    };
    return el;
}
const els = {};
const el = (id) => (els[id] ||= makeEl(id));

globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem() {} };
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
globalThis.AuthManager = { currentUser: { user_id: 'me' }, isLoggedIn: () => true };
globalThis.FriendsUI = undefined;
globalThis.console.log = () => {};

const api = { requests: [], hits: {}, gate: null, fail: false };
globalThis.AppAPI = {
    get: async (url) => {
        const path = url.split('?')[0];
        api.hits[path] = (api.hits[path] || 0) + 1;
        if (path === '/api/messages/conversations') {
            if (api.gate) await api.gate;
            if (api.fail) throw new Error('boom');
            return { success: true, total_unread: 0, conversations: [] };
        }
        if (path === '/api/friends/requests/received')
            return { success: true, requests: api.requests };
        if (path === '/api/friends/list' || path === '/api/friends')
            return { success: true, friends: [] };
        if (path.startsWith('/api/friends/blocked')) return { success: true, blocked_users: [] };
        if (path === '/api/groups') return { success: true, groups: [] };
        if (path === '/api/groups/invites') return { success: true, invites: [] };
        return { success: true };
    },
};

const load = async (rel) => import(await loadModuleUrl(new URL(rel, import.meta.url).pathname));
const { SocialHub, loadFriendsTabData, loadRequestBadge } = await load('../../web/js/friends.js');
const flush = () => new Promise((r) => setTimeout(r, 0));

// ── 1) 標題列沒有無說明的數字；邀請數在「好友」分頁鈕上 ──
{
    const tpl = readFileSync(
        new URL('../../web/js/components/tab-friends.js', import.meta.url),
        'utf8'
    );
    assert.ok(!tpl.includes('friends-badge-total'), '標題列那顆沒標示的數字要拿掉');
    assert.match(
        tpl,
        /id="social-tab-friends"[\s\S]*?id="friends-request-badge"/,
        '邀請數掛在好友分頁鈕上'
    );

    const badge = el('friends-request-badge');
    api.requests = [];
    await loadFriendsTabData();
    assert.ok(badge.classList.contains('hidden'), '沒有邀請：不顯示 0');

    api.requests = [{ user_id: 'a' }, { user_id: 'b' }];
    await loadFriendsTabData();
    assert.ok(!badge.classList.contains('hidden'));
    assert.equal(String(badge.textContent), '2');

    api.requests = [];
    await loadFriendsTabData();
    assert.ok(badge.classList.contains('hidden'), '處理完變 0 要收起來');
}

// ── 2) 在訊息分頁也抓得到邀請數 ──
{
    const badge = el('friends-request-badge');
    api.requests = [{ user_id: 'c' }];
    await loadRequestBadge();
    assert.ok(!badge.classList.contains('hidden'));
    assert.equal(String(badge.textContent), '1');
}

// ── 3) 重整：轉圈、鎖按鈕、連按不重打、失敗也停 ──
{
    const btn = makeEl('refresh');
    const icon = makeEl();
    btn.querySelector = (sel) => (sel === 'svg, i' ? icon : null);
    SocialHub.activeSubTab = 'messages';
    SocialHub.currentGroupId = null;
    SocialHub.currentChatUserId = null;

    let release;
    api.gate = new Promise((r) => (release = r));
    const before = api.hits['/api/messages/conversations'] || 0;
    const p = SocialHub.refresh(btn);
    await flush();
    assert.ok(icon.classList.contains('animate-spin'), '載入中要轉圈');
    assert.equal(btn.disabled, true, '載入中鎖住');
    assert.equal(btn.getAttribute('aria-busy'), 'true');

    SocialHub.refresh(btn); // 連按
    await flush();
    assert.equal(api.hits['/api/messages/conversations'] - before, 1, '轉圈中再按不重打');

    release();
    await p;
    assert.ok(!icon.classList.contains('animate-spin'), '資料回來停止轉圈');
    assert.equal(btn.disabled, false);
    assert.equal(btn.getAttribute('aria-busy'), undefined);

    api.gate = null;
    api.fail = true;
    const err = console.error;
    console.error = () => {}; // 列表抓失敗會記 log（堆疊很長）
    await SocialHub.refresh(btn);
    console.error = err;
    assert.ok(!icon.classList.contains('animate-spin'), '抓失敗也要停');
    assert.equal(btn.disabled, false);
    api.fail = false;

    // 好友分頁按重整：重抓好友資料
    SocialHub.activeSubTab = 'friends';
    const f = api.hits['/api/friends/requests/received'];
    await SocialHub.refresh(btn);
    assert.equal(api.hits['/api/friends/requests/received'], f + 1);
}

console.error('social_header_refresh: ok');
