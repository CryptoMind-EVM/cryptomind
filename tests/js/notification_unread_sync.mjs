// 鈴鐺未讀數字與側欄徽章的同步缺口（2026-10-06，#1058 之後）。看守：
//   1. 鈴鐺數字：伺服器總未讀超過本機載入的 50 筆時，之後本機標已讀／別台已讀不能讓數字突然掉到
//      「只算前 50 筆」；全部已讀要歸零
//      範圍外有未讀時，新通知可能是合併進範圍外那一筆（同 id）：本機分不出來，稍後重抓校正
//   2. 伺服器推 badges_changed（例如對方撤回好友邀請、通知早已讀所以沒有 notifications_read 可推）：
//      重抓側欄徽章，好友頁載入過的話邀請清單也一起更新；連續幾則合併成一次（伺服器在 commit 前就推了）
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.document = { visibilityState: 'visible', addEventListener: () => {}, getElementById: () => null };
globalThis.location = { protocol: 'https:', host: 'example.test' };
globalThis.CustomEvent = class {
    constructor(type, init) {
        this.type = type;
        this.detail = init?.detail;
    }
};
let lastEvent = null;
globalThis.dispatchEvent = (e) => (lastEvent = e);
console.log = () => {};

globalThis.I18n = { t: (k) => k };
globalThis.showToast = () => {};
globalThis.AuthManager = { currentUser: { user_id: 'me' }, isLoggedIn: () => true };

let serverUnread = 70;
let loaded = [];
let gets = 0;
const posts = [];
globalThis.AppAPI = {
    get: async () => (gets++, { success: true, notifications: loaded, unread_count: serverUnread }),
    post: async (url) => (posts.push(url), { success: true }),
};

const nav = [];
globalThis.NavBadges = { schedule: () => nav.push('schedule') };
let friendsRefreshed = 0;
globalThis.refreshFriendsUI = () => friendsRefreshed++;

class FakeWebSocket {
    static OPEN = 1;
    static CONNECTING = 0;
    constructor(url) {
        this.url = url;
        this.readyState = FakeWebSocket.CONNECTING;
        FakeWebSocket.last = this;
    }
    send() {}
    close() {}
}
globalThis.WebSocket = FakeWebSocket;

await import(await loadModuleUrl(new URL('../../web/js/notification-service.js', import.meta.url).pathname));
const NS = window.NotificationService;
NS.isLoggedIn = true;

const sys = (i, read = false) => ({ id: `n${i}`, type: 'system', title: 't', body: 'b', is_read: read, data: {} });

// ── 1) 鈴鐺數字以伺服器總數為準 ──────────────────────────────────────
loaded = Array.from({ length: 50 }, (_, i) => sys(i));
await NS.fetchNotifications();
assert.equal(NS.getUnreadCount(), 70, '初始：伺服器的總未讀（本機只載 50 筆）');

await NS.markAsRead('n0');
assert.equal(NS.getUnreadCount(), 69, '標已讀一筆：70→69（以前會掉到本機 49）');
assert.equal(lastEvent.detail.unreadCount, 69, '發給鈴鐺的數字也是總數');

NS.applyRemoteRead(['n1']);
assert.equal(NS.getUnreadCount(), 68, '別台讀了本機有的一筆');

NS.applyRemoteRead(['not-loaded-here']);
assert.equal(NS.getUnreadCount(), 67, '別台讀了本機沒載入的（超出 50 筆之外的）也要扣');

NS.applyRemoteRead(['n1']);
assert.equal(NS.getUnreadCount(), 67, '本機已經是已讀的不重複扣');

NS.addNotification(sys(100));
assert.equal(NS.getUnreadCount(), 68, '新通知進來 +1');

// 範圍外有未讀時，不認得的 id 可能是「合併進範圍外那一筆」（本機會多算 1）：稍後重抓，以伺服器為準
NS._resyncUnreadDelayMs = 10;
const getsBeforeMerge = gets;
serverUnread = 67; // 合併：伺服器端總數沒變（67），本機因為把它當新的多算了 1
NS.addNotification({ ...sys(7), id: 'merged-from-outside' });
assert.equal(NS.getUnreadCount(), 69, '先照新通知 +1');
await new Promise((r) => setTimeout(r, 40));
assert.equal(gets, getsBeforeMerge + 1, '重抓一次');
assert.equal(NS.getUnreadCount(), 67, '校正回伺服器的總數');
loaded = Array.from({ length: 50 }, (_, i) => sys(i, i === 0 || i === 1)); // 後面的案例用：伺服器清單與總數
serverUnread = 67;
await NS.fetchNotifications();
assert.equal(NS.getUnreadCount(), 67);

await NS.markAllAsRead();
assert.equal(NS.getUnreadCount(), 0, '全部已讀：歸零（超出本機的那些也一起）');
assert.ok(posts.some((u) => u.startsWith('/api/notifications/read-all')));

// 未讀都在載入範圍內（沒有範圍外）：新通知不必重抓
loaded = [sys(1), sys(2)];
serverUnread = 2;
await NS.fetchNotifications();
const getsInRange = gets;
NS.addNotification(sys(300));
await new Promise((r) => setTimeout(r, 40));
assert.equal(gets, getsInRange, '沒有範圍外的未讀就不必重抓');
assert.equal(NS.getUnreadCount(), 3);

// 未讀不超過載入範圍（一般情況）：行為跟以前一樣
loaded = [sys(1), sys(2), sys(3, true)];
serverUnread = 2;
await NS.fetchNotifications();
assert.equal(NS.getUnreadCount(), 2);
await NS.markAsRead('n1');
assert.equal(NS.getUnreadCount(), 1);

// 重抓失敗：不殘留上一輪的「範圍外」數字
loaded = [sys(1)];
serverUnread = 40;
await NS.fetchNotifications();
assert.equal(NS.getUnreadCount(), 40);
const realGet = AppAPI.get;
AppAPI.get = async () => {
    throw Object.assign(new Error('boom'), { status: 500 });
};
const realError = console.error;
console.error = () => {};
await NS.fetchNotifications();
console.error = realError;
assert.equal(NS.getUnreadCount(), 0, '拿不到資料：清空歸零');
AppAPI.get = realGet;
loaded = [sys(1)];
serverUnread = 1;
await NS.fetchNotifications();
assert.equal(NS.getUnreadCount(), 1, '之後恢復：不帶著上一輪的範圍外數字');

// ── 2) badges_changed ──────────────────────────────────────────────
NS.connectWebSocket();
const ws = FakeWebSocket.last;
assert.ok(ws.url.endsWith('/ws/notifications'));
NS._badgesChangedDelayMs = 10;
nav.length = 0;
friendsRefreshed = 0;
const push = () => ws.onmessage({ data: JSON.stringify({ type: 'badges_changed' }) });
push();
push(); // 連續幾則合併成一次（伺服器在 commit 前就推，馬上重抓可能讀到舊資料；動作本人的分頁也會收到）
assert.equal(nav.length, 0, '先等一下再重抓');
await new Promise((r) => setTimeout(r, 40));
assert.equal(nav.length, 1, 'badges_changed → 側欄徽章重抓（合併成一次）');
assert.equal(friendsRefreshed, 1, '好友頁載入過的話邀請清單一起更新（對方撤回邀請，清單上的「接受」會 400）');

// 好友頁沒載入過（沒有 refreshFriendsUI）：只重抓徽章，不能丟錯
delete globalThis.refreshFriendsUI;
push();
await new Promise((r) => setTimeout(r, 40));
assert.equal(nav.length, 2);

// 沒有 NavBadges（頁面沒載）：安靜略過
delete globalThis.NavBadges;
push();
await new Promise((r) => setTimeout(r, 40));

console.error('notification_unread_sync: ok');
