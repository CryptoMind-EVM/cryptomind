// 好友邀請通知的前端契約（2026-09-29）。
//
// 鈴鐺裡按「接受」以前呼叫 FriendsUI.handleAcceptRequest——FriendsUI 要切過好友頁才
// 載入，沒進過好友頁就按，什麼都沒送出、通知卻被標已讀，邀請默默卡住。
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
    'notification.friendRequest': '好友邀請',
    'notification.friendRequestBody': '{{name}} 想加你為好友',
    'notification.friendAccepted': '好友邀請已接受',
    'notification.friendAcceptedBody': '{{name}} 接受了你的好友邀請',
    'notification.newMessage': '新訊息',
    'notification.newMessages': '{{count}} 則新訊息',
    'friends.becameFriends': '已成為好友',
    'friends.requestRejected': '已拒絕',
};
globalThis.I18n = {
    t: (k, o = {}) => (T[k] || k).replace(/\{\{(\w+)\}\}/g, (_, n) => o[n]),
};

const posts = [];
globalThis.AppAPI = {
    post: async (url, body) => {
        posts.push({ url, body });
        return { success: true };
    },
    get: async () => ({ notifications: [], unread_count: 0 }),
};
const toasts = [];
globalThis.showToast = (msg, type) => toasts.push({ msg, type });
let friendsRefreshed = 0;
globalThis.refreshFriendsUI = () => friendsRefreshed++;
globalThis.AuthManager = { currentUser: { user_id: 'me' }, isLoggedIn: () => true };

await import(
    await loadModuleUrl(new URL('../../web/js/notification-service.js', import.meta.url).pathname)
);
const NS = window.NotificationService;

// ── 文字走 i18n（後端存的是固定中文）──────────────────────────────────────
const req = { id: 'f1', type: 'friend_request', title: '好友請求', body: 'x', data: { from_user_id: 'alice', from_username: 'Alice' } };
assert.deepEqual(NS.describe(req), { title: '好友邀請', body: 'Alice 想加你為好友' });
const acc = { id: 'f2', type: 'friend_accepted', title: 'x', body: 'x', data: { from_user_id: 'bob', from_username: 'Bob' } };
assert.deepEqual(NS.describe(acc), { title: '好友邀請已接受', body: 'Bob 接受了你的好友邀請' });
const msg = { id: 'm1', type: 'message', title: '新消息', body: 'Carol: hi', data: { count: 3 } };
assert.deepEqual(NS.describe(msg), { title: '3 則新訊息', body: 'Carol: hi' });
const other = { id: 'o1', type: 'announcement', title: '公告', body: '內容' };
assert.deepEqual(NS.describe(other), { title: '公告', body: '內容' });

// ── 收到好友邀請：toast 用 i18n 文字，好友頁（載入過的話）即時更新 ─────────
NS.addNotification(req);
assert.equal(toasts.at(-1).msg, 'Alice 想加你為好友');
assert.equal(friendsRefreshed, 1, '好友頁的邀請清單要跟著更新');

// ── 鈴鐺裡按接受：FriendsUI 沒載入也要真的送出 ────────────────────────────
delete globalThis.FriendsUI;
await import(
    await loadModuleUrl(new URL('../../web/js/components/NotificationPanel.js', import.meta.url).pathname)
);
const panel = Object.create(window.NotificationPanel.prototype);
await panel.handleAction('f1', 'accept', req);
assert.deepEqual(posts.at(-1), { url: '/api/friends/accept', body: { target_user_id: 'alice' } });
assert.equal(toasts.at(-1).msg, '已成為好友');

await panel.handleAction('f1', 'reject', req);
assert.deepEqual(posts.at(-1), { url: '/api/friends/reject', body: { target_user_id: 'alice' } });

// 失敗（例如對方已取消）：要講出來，不能默默吞掉
AppAPI.post = async () => {
    throw new Error('Friend request not found');
};
await panel.handleAction('f1', 'accept', req);
assert.equal(toasts.at(-1).type, 'error');

console.error('friend_notifications: ok');
