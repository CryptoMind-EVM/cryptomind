// 好友頁接上群組（social-groups.js 混進 SocialHub，Part C2）。看守：
//   1. 列表：私訊＋群組依最後動態混排、未讀徽章相加；開關關著（404）只有私訊、入口藏起來、不重試
//   2. WS 群組事件：只把開著的那群塞進畫面；被踢就關掉；有人讀了更新「已讀 N」
//   3. 送出：開著群組時走 /api/groups/<id>/messages（帶回覆）
//   4. 從群組點回私訊：群組狀態清乾淨（不然 WS 比對、送出都會走錯邊）
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

function makeEl(id = '') {
    const classes = new Set();
    const attrs = {};
    return {
        id,
        value: '',
        disabled: false,
        innerHTML: '',
        textContent: '',
        className: '',
        style: {},
        dataset: {},
        classList: {
            add: (...c) => c.forEach((x) => classes.add(x)),
            remove: (...c) => c.forEach((x) => classes.delete(x)),
            contains: (c) => classes.has(c),
            toggle: (c, on) => ((on ?? !classes.has(c)) ? classes.add(c) : classes.delete(c)),
            forEach: (fn) => [...classes].forEach(fn),
        },
        setAttribute: (k, v) => (attrs[k] = v),
        getAttribute: (k) => attrs[k],
        removeAttribute: (k) => delete attrs[k],
        toggleAttribute: (k, on) => (on ? (attrs[k] = '') : delete attrs[k]),
        addEventListener() {},
        querySelector: () => null,
        querySelectorAll: () => [],
        focus() {},
        offsetParent: {},
        scrollHeight: 0,
        scrollTop: 0,
        clientHeight: 0,
    };
}
let els = {};
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
globalThis.I18n = { t: (k) => k };
globalThis.AppUtils = { refreshIcons() {} };
globalThis.SecurityUtils = { escapeHTML: (s) => String(s) };
globalThis.AuthManager = { currentUser: { user_id: 'me' } };
globalThis.console.log = () => {};

const api = { gets: [], posts: [], groupsStatus: 200 };
globalThis.AppAPI = {
    get: async (url, opts) => {
        api.gets.push([url, opts]);
        if (url.startsWith('/api/messages/conversations')) {
            return {
                success: true,
                total_unread: 1,
                conversations: [
                    { id: 1, other_user_id: 'bob', other_username: 'bob', last_message: 'hi', unread_count: 1, last_message_at: '2026-10-01T01:00:00Z' },
                ],
            };
        }
        if (url === '/api/groups') {
            if (api.groupsStatus === 404) throw Object.assign(new Error('Not Found'), { status: 404 });
            return {
                success: true,
                groups: [
                    { id: 7, name: '投資閒聊', member_count: 3, unread_count: 2, last_message_at: '2026-10-01T02:00:00Z', last_message: null },
                    { id: 8, name: '舊群', member_count: 2, unread_count: 0, last_message_at: '2026-09-30T00:00:00Z', last_message: null },
                    // 靜音：沒被 @ 的不算進「訊息」徽章；被 @ 的算（同功能選單 core/nav_badges.py）
                    { id: 9, name: '靜音群', member_count: 4, unread_count: 5, muted: true, last_message_at: '2026-09-29T00:00:00Z', last_message: null },
                    { id: 10, name: '靜音但被提及', member_count: 4, unread_count: 1, muted: true, mentioned: true, last_message_at: '2026-09-28T00:00:00Z', last_message: null },
                ],
            };
        }
        if (url === '/api/groups/invites') {
            if (api.groupsStatus === 404) throw Object.assign(new Error('Not Found'), { status: 404 });
            return { success: true, invites: api.invites || [] };
        }
        return { success: true, messages: [], has_more: false };
    },
    post: async (url, body) => (api.posts.push([url, body]), { success: true, message: { id: 99, group_id: 7, from_user_id: 'me', content: body?.content } }),
};

const load = async (rel) => import(await loadModuleUrl(new URL(rel, import.meta.url).pathname));
const { SocialHub } = await load('../../web/js/friends.js');

// ── 1) 列表 ───────────────────────────────────────────
{
    await SocialHub.loadConversations();
    const html = el('social-conv-list').innerHTML;
    const order = ['g-7', 'data-conversation-id="1"', 'g-8'].map((k) => html.indexOf(k));
    assert.ok(order.every((i) => i >= 0), html);
    assert.deepEqual([...order].sort((a, b) => a - b), order, '依最後動態：群 7（02:00）> 私訊（01:00）> 群 8（昨天）');
    assert.equal(el('messages-unread-badge').textContent, 4, '徽章＝私訊 1＋群組 2＋被 @ 的靜音群 1（沒被 @ 的靜音群 5 不算）');
    assert.equal(SocialHub.groupsEnabled, true);
    assert.equal(el('social-group-bar').classList.contains('hidden'), false, '開關開著：顯示「建立群組」');
    assert.equal(api.gets.find(([u]) => u === '/api/groups')[1]?.retries, 0, '404 不重試');

    // 開關關著：只剩私訊、入口藏起來、之後不再問
    api.groupsStatus = 404;
    SocialHub.groupsEnabled = null;
    await SocialHub.loadConversations();
    assert.ok(!el('social-conv-list').innerHTML.includes('g-7'));
    assert.equal(SocialHub.groupsEnabled, false);
    assert.equal(el('social-group-bar').classList.contains('hidden'), true);
    const before = api.gets.filter(([u]) => u === '/api/groups').length;
    await SocialHub.loadConversations();
    assert.equal(api.gets.filter(([u]) => u === '/api/groups').length, before, '知道關著就不再打');
    api.groupsStatus = 200;
    SocialHub.groupsEnabled = null;
}

// ── 2) WS 群組事件 ─────────────────────────────────────
{
    const calls = [];
    const spy = (name) => {
        const original = SocialHub[name];
        SocialHub[name] = (...args) => calls.push([name, ...args]);
        return () => (SocialHub[name] = original);
    };
    const restores = ['appendGroupRow', '_scheduleListReload', 'closeChat', 'refreshGroupReadStatuses', 'markGroupRead', 'reloadGroupInfo'].map(spy);
    SocialHub.currentGroupId = 7;
    SocialHub.currentGroup = { id: 7, members: [{ user_id: 'bob', last_read_message_id: 3, first_visible_message_id: 0 }] };

    SocialHub.onGroupEvent({ type: 'group_message', group_id: 8, message: { id: 5, from_user_id: 'bob' } });
    assert.ok(!calls.some(([n]) => n === 'appendGroupRow'), '別的群的訊息不塞進畫面');
    assert.ok(calls.some(([n]) => n === '_scheduleListReload'), '列表要更新');

    calls.length = 0;
    SocialHub.onGroupEvent({ type: 'group_message', group_id: 7, message: { id: 6, from_user_id: 'bob' } });
    assert.deepEqual(calls.filter(([n]) => n === 'appendGroupRow').map(([, m]) => m.id), [6]);
    assert.ok(calls.some(([n]) => n === 'markGroupRead'), '開著的群收到別人的訊息 → 已讀');

    calls.length = 0;
    SocialHub.onGroupEvent({ type: 'group_read', group_id: 7, user_id: 'bob', last_read_message_id: 9 });
    assert.equal(SocialHub.currentGroup.members[0].last_read_message_id, 9);
    assert.ok(calls.some(([n]) => n === 'refreshGroupReadStatuses'));
    SocialHub.onGroupEvent({ type: 'group_read', group_id: 7, user_id: 'bob', last_read_message_id: 2 });
    assert.equal(SocialHub.currentGroup.members[0].last_read_message_id, 9, '讀取位置不倒退');

    calls.length = 0;
    SocialHub.onGroupEvent({ type: 'group_updated', group_id: 7, kind: 'member_joined' });
    assert.ok(calls.some(([n]) => n === 'reloadGroupInfo'), '成員異動 → 重抓成員');
    SocialHub.onGroupEvent({ type: 'group_updated', group_id: 7, kind: 'removed' });
    assert.ok(calls.some(([n]) => n === 'closeChat'), '被踢 → 關掉');
    // 群主解散（2026-10-01）：正看著的人也要關掉並提示
    calls.length = 0;
    const toastsBefore = (globalThis.__toasts ||= []).length;
    const realToast = globalThis.showToast;
    globalThis.showToast = (msg) => globalThis.__toasts.push(msg);
    SocialHub.currentGroupId = 7;
    SocialHub.onGroupEvent({ type: 'group_updated', group_id: 7, kind: 'dissolved' });
    assert.ok(calls.some(([n]) => n === 'closeChat'), '解散 → 關掉');
    assert.equal(globalThis.__toasts.at(-1), 'groups.dissolvedNotice');
    assert.ok(globalThis.__toasts.length > toastsBefore);
    // 自己按解散的那台：WS 先到，不重複跳「群主已解散」（只留按鈕那邊的「群組已解散」）
    const n = globalThis.__toasts.length;
    SocialHub.currentGroupId = 7;
    SocialHub._dissolvingGroupId = 7;
    SocialHub.onGroupEvent({ type: 'group_updated', group_id: 7, kind: 'dissolved' });
    assert.equal(globalThis.__toasts.length, n, '自己解散的不重複提示');
    SocialHub._dissolvingGroupId = null;
    globalThis.showToast = realToast;

    restores.forEach((r) => r());
}

// ── 3) 送出走群組 API ──────────────────────────────────
{
    SocialHub.currentGroupId = 7;
    SocialHub.currentGroup = { id: 7, members: [] };
    SocialHub.appendGroupRow = () => {};
    el('social-msg-input').value = '早安';
    await SocialHub.sendMessage({ preventDefault() {} });
    const [url, body] = api.posts.at(-1);
    assert.equal(url, '/api/groups/7/messages');
    assert.equal(body.content, '早安');
    assert.ok(!api.posts.some(([u]) => u.startsWith('/api/messages/send')), '不能走私訊 API');
}

// ── 4) 點回私訊：群組狀態清掉 ───────────────────────────
{
    SocialHub.currentGroupId = 7;
    SocialHub.currentGroup = { id: 7, members: [] };
    SocialHub.openConversation('bob', 'bob');
    assert.equal(SocialHub.currentGroupId, null);
    assert.equal(SocialHub.currentGroup, null);
    assert.equal(el('social-group-info-btn').classList.contains('hidden'), true, '資訊鈕藏起來');
    assert.equal(el('social-chat-profile-link').getAttribute('data-click'), undefined, '標題列恢復連到個人頁');
}

// ── 5) Pro 到期唯讀：送出被擋後，字數更新不能把送出鈕又打開（review HIGH） ─────
{
    SocialHub.currentGroupId = 7;
    SocialHub.quotaLeft = null;
    el('social-msg-input').value = '還想再傳';
    SocialHub._setReadOnly(true);
    SocialHub.updateCharCount();
    assert.equal(el('social-send-btn').disabled, true, '唯讀時送出鈕要一直鎖著');
    SocialHub._setReadOnly(false);
    SocialHub.updateCharCount();
    assert.equal(el('social-send-btn').disabled, false, '恢復後照常');
}

// ── 6) 深連結 /?group=<id>#friends：開那個群、把參數從網址拿掉 ──────────
{
    const opened = [];
    const original = SocialHub.openGroup;
    SocialHub.openGroup = (id) => opened.push(id);
    const replaced = [];
    globalThis.location = { search: '?group=12&x=1', pathname: '/', hash: '#friends' };
    globalThis.history = { state: null, replaceState: (_s, _t, url) => replaced.push(url) };
    SocialHub.openGroupFromUrl();
    assert.deepEqual(opened, [12]);
    assert.deepEqual(replaced, ['/?x=1#friends'], '參數拿掉，重新整理不會再開一次');
    globalThis.location = { search: '', pathname: '/', hash: '#friends' };
    SocialHub.openGroupFromUrl();
    assert.deepEqual(opened, [12], '沒有參數什麼都不做');
    SocialHub.openGroup = original;
}

// ── 7) 待回覆的群組邀請：列表最上面、只在可能變了才重抓（2026-10-01：以前只進通知中心，找不到） ──
{
    const listeners = {};
    globalThis.addEventListener = (type, fn) => ((listeners[type] ||= []).push(fn));
    SocialHub._inviteWatchBound = false;
    SocialHub._invitesStale = true;
    SocialHub._inviteSig = null;
    SocialHub.groupsEnabled = null;
    api.invites = [{ invite_id: 5, group_id: 30, group_name: '新群<b>', member_count: 4, inviter_id: 'bob', inviter_name: '鮑伯' }];
    const invitesGets = () => api.gets.filter(([u]) => u === '/api/groups/invites').length;
    const start = invitesGets();
    await SocialHub.loadConversations();
    let html = el('social-conv-list').innerHTML;
    assert.ok(html.indexOf('id="group-invites"') >= 0 && html.indexOf('id="group-invites"') < html.indexOf('g-7'), '邀請在最上面');
    assert.ok(html.includes('data-invite-id="5"') && html.includes('新群&lt;b&gt;'), '群名要轉義');
    assert.equal(invitesGets(), start + 1);

    // 來訊、已讀之類的重畫：不重抓邀請
    await SocialHub.loadConversations();
    assert.equal(invitesGets(), start + 1, '列表重畫不該每次都打邀請 API');

    // 通知中心的群組邀請有增減 → 重抓
    api.invites = [];
    const fire = (notifications) => (listeners.notificationsUpdated || []).forEach((fn) => fn({ detail: { notifications } }));
    fire([{ id: 'n1', type: 'group_invite', is_read: false }]);
    await new Promise((r) => setTimeout(r, 0));
    await SocialHub.loadConversations();
    assert.equal(invitesGets(), start + 2, '收到新的群組邀請通知要重抓');
    assert.ok(!el('social-conv-list').innerHTML.includes('id="group-invites"'), '沒有邀請就不畫那區');
    const n = invitesGets();
    fire([{ id: 'n1', type: 'group_invite', is_read: false }, { id: 'm1', type: 'message', is_read: false }]);
    await SocialHub.loadConversations();
    assert.equal(invitesGets(), n, '群組邀請沒變（只是多了私訊通知）就不重抓');

    // 開關關著：連邀請都不打
    api.groupsStatus = 404;
    SocialHub.groupsEnabled = null;
    SocialHub._invitesStale = true;
    const m = invitesGets();
    await SocialHub.loadConversations();
    assert.equal(invitesGets(), m, '開關關著不打邀請 API（群組那支 404 就知道了）');
    api.groupsStatus = 200;
    SocialHub.groupsEnabled = null;
    globalThis.addEventListener = () => {};
}

// ── 8) 回到前景（resync）：開著群組時只補新訊息，不能整片換成轉圈再重畫（2026-10-02 切回視窗會閃一下） ──
{
    const calls = [];
    const spy = (name) => {
        const original = SocialHub[name];
        SocialHub[name] = (...args) => (calls.push([name, ...args]), undefined);
        return () => (SocialHub[name] = original);
    };
    const restores = ['appendGroupRow', 'loadGroup', 'loadConversations', 'reloadGroupInfo', 'markGroupRead'].map(spy);
    const origGet = AppAPI.get;
    let switchAway = false;
    AppAPI.get = async (url, opts) => {
        if (url.startsWith('/api/groups/7/messages')) {
            if (switchAway) SocialHub.currentGroupId = 8; // 抓的途中使用者換到別的群
            return { success: true, has_more: false, messages: [{ id: 41, group_id: 7, from_user_id: 'bob' }, { id: 42, group_id: 7, from_user_id: 'bob' }] };
        }
        return origGet(url, opts);
    };
    SocialHub.currentGroupId = 7;
    SocialHub.currentGroup = { id: 7, members: [] };
    SocialHub.currentConversationId = null;
    el('social-messages-container').innerHTML = '<div>舊訊息</div>';

    await SocialHub.resync();
    assert.ok(!calls.some(([n]) => n === 'loadGroup'), '不整片重載（會先塞轉圈、再全部重畫、捲到底）');
    assert.equal(el('social-messages-container').innerHTML, '<div>舊訊息</div>', '畫面上的訊息不動');
    assert.deepEqual(calls.filter(([n]) => n === 'appendGroupRow').map(([, m]) => m.id), [41, 42], '最新一頁照順序塞（appendGroupRow 自己去重）');
    assert.ok(calls.some(([n]) => n === 'reloadGroupInfo'), '離開期間的已讀、成員異動要補');
    assert.ok(calls.some(([n]) => n === 'markGroupRead'));
    assert.ok(calls.some(([n]) => n === 'loadConversations'));

    calls.length = 0;
    switchAway = true;
    await SocialHub.resync();
    assert.ok(!calls.some(([n]) => n === 'appendGroupRow'), '途中換群：抓回來的不能塞進別的群');

    AppAPI.get = origGet;
    restores.forEach((r) => r());
    SocialHub.currentGroupId = null;
    SocialHub.currentGroup = null;
}

// ── 9) 側欄數字與列表要一起動（2026-10-06）：
//      自己在別台裝置讀了群組（WS group_read 會廣播給自己）、鈴鐺把「提及我」標已讀 ──
{
    const calls = [];
    const spy = (name) => {
        const original = SocialHub[name];
        SocialHub[name] = (...args) => (calls.push([name, ...args]), undefined);
        return () => (SocialHub[name] = original);
    };
    const restores = ['_scheduleListReload', 'loadConversations', 'refreshGroupReadStatuses'].map(spy);
    const nav = [];
    globalThis.NavBadges = { schedule: () => nav.push('schedule') };
    const count = (name) => calls.filter(([n]) => n === name).length;

    // 自己的 group_read、這台沒開著那個群：列表的未讀與側欄徽章都要跟著少
    SocialHub.currentGroupId = null;
    SocialHub.currentGroup = null;
    SocialHub.onGroupEvent({ type: 'group_read', group_id: 7, user_id: 'me', last_read_message_id: 9 });
    assert.equal(count('_scheduleListReload'), 1, '別台裝置讀了這個群：這台的列表未讀要歸零');
    assert.equal(nav.length, 1, '側欄「社群」徽章也要重抓');

    // 別人讀了、這台沒開著：跟我的數字無關，不動
    calls.length = 0;
    nav.length = 0;
    SocialHub.onGroupEvent({ type: 'group_read', group_id: 7, user_id: 'bob', last_read_message_id: 9 });
    assert.equal(count('_scheduleListReload'), 0);
    assert.equal(nav.length, 0);

    // 開著這個群時自己的 group_read（本機自己標已讀廣播回來）：列表與徽章也一起重抓，已讀名單照舊更新
    SocialHub.currentGroupId = 7;
    SocialHub.currentGroup = { id: 7, members: [{ user_id: 'me', last_read_message_id: 3 }] };
    SocialHub.onGroupEvent({ type: 'group_read', group_id: 7, user_id: 'me', last_read_message_id: 9 });
    assert.equal(SocialHub.currentGroup.members[0].last_read_message_id, 9);
    assert.equal(count('refreshGroupReadStatuses'), 1);
    assert.equal(count('_scheduleListReload'), 1);
    SocialHub.currentGroupId = null;
    SocialHub.currentGroup = null;

    // 鈴鐺：「有人提及你」標已讀會改變靜音群算不算進數字，列表的徽章與提及標記要跟著重抓
    const listeners = {};
    globalThis.addEventListener = (type, fn) => ((listeners[type] ||= []).push(fn));
    SocialHub._inviteWatchBound = false;
    SocialHub._inviteSig = '';
    SocialHub._mentionSig = '';
    SocialHub._invitesStale = false;
    SocialHub.bindInviteList(el('social-conv-list'));
    const fire = (notifications) => (listeners.notificationsUpdated || []).forEach((fn) => fn({ detail: { notifications } }));
    const mention = { id: 'g1', type: 'group_message', is_read: false, data: { group_id: '9', mentioned: true } };
    const plain = { id: 'g2', type: 'group_message', is_read: false, data: { group_id: '9' } };
    calls.length = 0;

    fire([plain]);
    assert.equal(count('loadConversations'), 0, '一般群組訊息通知不影響靜音群算不算：不重抓');
    fire([mention, plain]);
    assert.equal(count('loadConversations'), 1, '新的未讀提及 → 重抓');
    fire([mention, plain]);
    assert.equal(count('loadConversations'), 1, '沒變就不重抓');
    assert.equal(SocialHub._invitesStale, false, '只有提及變了：不用重抓邀請');
    fire([{ ...mention, is_read: true }, plain]);
    assert.equal(count('loadConversations'), 2, '鈴鐺把提及標已讀 → 重抓（靜音群不再算進徽章、「有人提及你」拿掉）');

    globalThis.addEventListener = () => {};
    delete globalThis.NavBadges;
    restores.forEach((r) => r());
}

console.error('social_groups: ok');
