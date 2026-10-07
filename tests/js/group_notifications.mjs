// 群組通知（Part C3）。看守：
//   1. 文字走 i18n（後端存固定中文）：邀請寫誰邀你進哪群、群組訊息標題是群名（多則加則數）、被移出
//   2. 正看著那個群組：新訊息不跳 toast（data.group_id 是字串，要轉數字比對）
//   3. 鈴鐺裡直接接受／拒絕邀請：真的打 API、標已讀、列表更新；接受失敗（群滿）要講出來
//   4. 點群組訊息通知：切到好友頁並打開那個群組
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.document = { visibilityState: 'visible', addEventListener: () => {}, getElementById: () => null };
globalThis.CustomEvent = class {
    constructor(type, init) {
        this.detail = init?.detail;
    }
};
globalThis.dispatchEvent = () => {};
console.log = () => {};

const T = {
    'notification.groupInvite': '群組邀請',
    'notification.groupInviteBody': '{{name}} 邀請你加入「{{group}}」',
    'notification.groupMessages': '{{group}}（{{count}} 則新訊息）',
    'notification.groupMentioned': '有人在「{{group}}」提及你',
    'notification.groupRemoved': '已被移出群組',
    'notification.groupRemovedBody': '你已被移出「{{group}}」',
    'notification.messageRecalledBody': '{{name}} 收回了一則訊息',
    'notification.groupJoined': '已加入「{{group}}」',
    'notification.groupDeclined': '已拒絕邀請',
    'groups.errors.group_full': '群組人數已滿',
    'notification.groupDissolved': '群組已解散',
    'notification.groupDissolvedBody': '{{name}} 解散了「{{group}}」',
};
globalThis.I18n = { t: (k, o = {}) => (T[k] || k).replace(/\{\{(\w+)\}\}/g, (_, n) => o[n]) };

const posts = [];
let failNext = null;
globalThis.AppAPI = {
    post: async (url, body) => {
        posts.push({ url, body });
        if (failNext) {
            const e = Object.assign(new Error(failNext), { status: 409 });
            failNext = null;
            throw e;
        }
        return { success: true, group_id: 7 };
    },
    get: async () => ({ notifications: [], unread_count: 0 }),
};
const toasts = [];
globalThis.showToast = (msg, type) => toasts.push({ msg, type });
globalThis.AuthManager = { currentUser: { user_id: 'me' }, isLoggedIn: () => true };
const opened = [];
globalThis.SocialHub = { loadConversations: () => opened.push('list'), openGroup: (id) => opened.push(['open', id]) };
const switched = [];
globalThis.switchTab = (tab) => switched.push(tab);

await import(await loadModuleUrl(new URL('../../web/js/notification-service.js', import.meta.url).pathname));
const NS = window.NotificationService;

// ── 1) 文字 ─────────────────────────────────────────────
const invite = {
    id: 'g1',
    type: 'group_invite',
    title: '群組邀請',
    body: 'x',
    data: { invite_id: 11, group_id: 7, group_name: '投資閒聊', inviter_id: 'a', inviter_name: '小明' },
};
assert.deepEqual(NS.describe(invite), { title: '群組邀請', body: '小明 邀請你加入「投資閒聊」' });
const gm = { id: 'g2', type: 'group_message', title: '投資閒聊', body: '小明: 早安', data: { group_id: '7', group_name: '投資閒聊', count: 1, from_username: '小明' } };
assert.deepEqual(NS.describe(gm), { title: '投資閒聊', body: '小明: 早安' });
assert.deepEqual(NS.describe({ ...gm, data: { ...gm.data, count: 3 } }), { title: '投資閒聊（3 則新訊息）', body: '小明: 早安' });
assert.equal(NS.describe({ ...gm, data: { ...gm.data, recalled: true } }).body, '小明 收回了一則訊息');
// @提及：標題換成「有人在 X 提及你」（合併多則也一樣）
assert.deepEqual(NS.describe({ ...gm, data: { ...gm.data, count: 3, mentioned: true } }), { title: '有人在「投資閒聊」提及你', body: '小明: 早安' });
const removed = { id: 'g3', type: 'group_removed', title: 'x', body: 'x', data: { group_id: 7, group_name: '投資閒聊' } };
assert.deepEqual(NS.describe(removed), { title: '已被移出群組', body: '你已被移出「投資閒聊」' });
// 群主解散（2026-10-01）：誰解散了哪群
const dissolved = { id: 'g5', type: 'group_dissolved', title: 'x', body: 'x', data: { group_id: 7, group_name: '投資閒聊', by_name: '老王' } };
assert.deepEqual(NS.describe(dissolved), { title: '群組已解散', body: '老王 解散了「投資閒聊」' });

// ── 2) 正看著那群不跳 toast ─────────────────────────────
window.MessagesWebSocket = { isViewing: () => false, isViewingGroup: (gid) => gid === 7 };
NS.addNotification(gm);
assert.equal(toasts.length, 0, '正看著的群組不跳 toast');
assert.equal(NS.notifications[0].is_read, true);
NS.addNotification({ ...gm, id: 'g4', data: { ...gm.data, group_id: '8' } });
assert.equal(toasts.at(-1).msg, '小明: 早安', '別的群照跳');

// ── 3) 鈴鐺裡接受／拒絕 ─────────────────────────────────
await import(await loadModuleUrl(new URL('../../web/js/components/NotificationPanel.js', import.meta.url).pathname));
const panel = Object.create(window.NotificationPanel.prototype);
await panel.handleAction('g1', 'group_accept', invite);
assert.deepEqual(posts.at(-1), { url: '/api/groups/invites/11/accept', body: undefined });
assert.equal(toasts.at(-1).msg, '已加入「投資閒聊」');
assert.ok(opened.includes('list'), '好友頁列表更新');
await panel.handleAction('g1', 'group_decline', invite);
assert.equal(posts.at(-1).url, '/api/groups/invites/11/decline');
failNext = 'group_full';
await panel.handleAction('g1', 'group_accept', invite);
assert.deepEqual(toasts.at(-1), { msg: '群組人數已滿', type: 'error' }, '失敗要講出來（在地化）');

// ── 4) 點群組訊息通知 → 打開那個群組 ────────────────────
panel.hide = () => {};
panel.handleNotificationClick(gm);
assert.deepEqual(switched.at(-1), 'friends');
await new Promise((r) => setTimeout(r, 350));
assert.deepEqual(opened.at(-1), ['open', 7], 'group_id 字串轉成數字');

// ── 5) 論壇頁（沒有 SocialHub／switchTab）點群組通知 → 帶去好友頁開群 ──────
{
    delete globalThis.SocialHub;
    delete globalThis.switchTab;
    globalThis.location = { href: '/static/forum/index.html' };
    panel.handleNotificationClick(gm);
    assert.equal(globalThis.location.href, '/?group=7#friends');
}

console.error('group_notifications: ok');
