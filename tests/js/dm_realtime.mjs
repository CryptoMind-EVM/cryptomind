// 私訊即時／通知中心的前端契約（2026-09-29）。
//
// 根因：桌機好友頁沒連 WS；WS 斷線重試 5 次就放棄、重連後不補抓；通知 WS 沒送心跳
// （Cloudflare 閒置 100 秒切斷）；通知初次讀取碰到登入寬限期直接 return 且沒人再抓。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

// ── 假環境 ────────────────────────────────────────────────────────────────
const timers = [];
globalThis.setTimeout = (fn, ms) => {
    timers.push({ fn, ms });
    return timers.length;
};
globalThis.clearTimeout = () => {};
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};
function runTimers() {
    const pending = timers.splice(0);
    pending.forEach((t) => t.fn());
}

const listeners = {};
globalThis.window = globalThis;
globalThis.addEventListener = (type, fn) => ((listeners[type] ||= []).push(fn));
globalThis.document = {
    visibilityState: 'visible',
    addEventListener: (type, fn) => ((listeners[type] ||= []).push(fn)),
    getElementById: () => null,
};
globalThis.CustomEvent = class {
    constructor(type, init) {
        this.type = type;
        this.detail = init?.detail;
    }
};
globalThis.dispatchEvent = () => {};
globalThis.location = { protocol: 'https:', host: 'example.test' };
console.log = () => {};

const sockets = [];
class FakeWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 3;
    constructor(url) {
        this.url = url;
        this.readyState = FakeWebSocket.CONNECTING;
        this.sent = [];
        sockets.push(this);
    }
    send(data) {
        this.sent.push(JSON.parse(data));
    }
    close() {
        this.readyState = FakeWebSocket.CLOSED;
        this.onclose?.();
    }
    open() {
        this.readyState = FakeWebSocket.OPEN;
        this.onopen?.();
    }
    receive(data) {
        this.onmessage?.({ data: JSON.stringify(data) });
    }
}
globalThis.WebSocket = FakeWebSocket;

let loginInFlight = false;
let inGracePeriod = false;
const apiCalls = [];
globalThis.AuthManager = {
    currentUser: { user_id: 'me' },
    isLoggedIn: () => true,
    isLoginInFlight: () => loginInFlight,
    shouldDeferExpiredSessionCleanup: () => loginInFlight || inGracePeriod,
    isTokenExpired: () => false,
};
globalThis.AppAPI = {
    get: async (url) => {
        apiCalls.push(url);
        return { notifications: [{ id: 'n1', type: 'message', is_read: false }], unread_count: 1 };
    },
};
const toasts = [];
globalThis.showToast = (msg) => toasts.push(msg);

const { messageInsertIndex, quotaState } = await import(
    await loadModuleUrl(new URL('../../web/js/messages.js', import.meta.url).pathname)
);
await import(
    await loadModuleUrl(new URL('../../web/js/notification-service.js', import.meta.url).pathname)
);
const WS = window.MessagesWebSocket;
const NS = window.NotificationService;

// ── 1. 私訊 WS：斷線永遠會重連，不會試 5 次就放棄 ─────────────────────────
WS.connect();
const first = sockets.at(-1);
first.open();
assert.deepEqual(first.sent[0], { action: 'auth', user_id: 'me' });
let resyncs = 0;
WS.onResync(() => resyncs++);
first.receive({ type: 'authenticated' });
assert.equal(resyncs, 0, '第一次連上不用補抓');

for (let i = 0; i < 8; i++) {
    sockets.at(-1).close();
    runTimers();
}
assert.equal(sockets.length, 9, '斷 8 次就要重連 8 次');
sockets.at(-1).open();
sockets.at(-1).receive({ type: 'authenticated' });
assert.equal(resyncs, 1, '重連成功要叫頁面補抓漏掉的訊息');
assert.equal(WS.reconnectAttempts, 0, '連上後退避歸零');

// ── 2. 心跳沒回 pong：當死線關掉重建 ──────────────────────────────────────
const live = sockets.at(-1);
WS._ping();
assert.deepEqual(live.sent.at(-1), { action: 'ping' });
runTimers(); // pong 逾時 → close → 排重連
runTimers();
assert.notEqual(sockets.at(-1), live, '沒回 pong 要換一條新連線');
sockets.at(-1).open();
sockets.at(-1).receive({ type: 'authenticated' });

// ── 3. 回到前景：連著就補抓 ─────────────────────────────────────────────
const before = resyncs;
listeners.visibilitychange.forEach((fn) => fn());
assert.equal(resyncs, before + 1, '回到前景要補抓（手機背景時可能漏）');

// ── 4. 輸入中：3 秒節流；沒送過 start 就不送 stop ─────────────────────────
const sock = sockets.at(-1);
const typingSent = () => sock.sent.filter((m) => m.action === 'typing');
WS.sendTypingStop(7);
assert.equal(typingSent().length, 0);
WS.sendTyping(7);
WS.sendTyping(7);
assert.deepEqual(typingSent(), [{ action: 'typing', conversation_id: 7 }]);
WS.sendTypingStop(7);
assert.deepEqual(typingSent().at(-1), { action: 'typing', conversation_id: 7, state: 'stop' });
let typingEvents = [];
WS.onTyping((d) => typingEvents.push(d));
sock.receive({ type: 'typing', conversation_id: 7, from_username: 'A', state: 'start' });
assert.equal(typingEvents.length, 1);

// ── 5. 通知：登入成功後的寬限期照抓；登入進行中擋掉的要自己補抓 ─────────────
inGracePeriod = true; // 剛續登成功（token 過期隔天打開）
await NS.fetchNotifications();
assert.equal(apiCalls.length, 1, '登入成功後的寬限期不用等，直接抓');
inGracePeriod = false;

timers.length = 0;
loginInFlight = true;
await NS.fetchNotifications();
assert.equal(apiCalls.length, 1, '登入進行中先不抓');
loginInFlight = false;
runTimers();
await new Promise((r) => setImmediate(r));
assert.equal(apiCalls.length, 2, '登入完成要補抓，不能就此空著');
assert.equal(NS.notifications.length, 1);

// ── 6. 通知：合併通知取代舊的；正看著的對話不跳 toast；遠端已讀同步 ────────
NS.notifications = [];
NS.addNotification({ id: 'm1', type: 'message', body: 'A: hi', data: { conversation_id: '7', count: 1 } });
NS.addNotification({ id: 'm1', type: 'message', body: 'A: yo', data: { conversation_id: '7', count: 2 } });
assert.equal(NS.notifications.length, 1, '同 id 取代，不重複');
assert.equal(NS.notifications[0].body, 'A: yo');
assert.equal(toasts.length, 2);

WS.isViewing = (id) => String(id) === '8';
NS.addNotification({ id: 'm2', type: 'message', body: 'B: hey', data: { conversation_id: '8', count: 1 } });
assert.equal(toasts.length, 2, '正看著那個對話不跳 toast');
assert.equal(NS.notifications[0].is_read, true);

NS.applyRemoteRead(['m1']);
assert.equal(NS.unreadCount, 0, '別的分頁讀掉了，這邊同步變已讀');

// ── 7. 插入位置：補抓回來的漏訊息可能比剛到的即時訊息還舊，要插回中間不能丟 ──
assert.equal(messageInsertIndex([], 5), 0, '空的：放第一列');
assert.equal(messageInsertIndex([10, 11, 14], 15), 3, '最新的接在最後');
assert.equal(messageInsertIndex([10, 11, 14], 12), 2, '斷線期間漏的（比最後一則舊）插回中間');
assert.equal(messageInsertIndex([10, 11, 14], 11), -1, '已經在畫面上：不重複');
assert.equal(messageInsertIndex([10, 11, 14], 3), -1, '比載入範圍還舊：屬於往上翻的分頁，不插');

// ── 8. 通知：token 過期又續不回來時，重試要有上限，不能每 2 秒打到天荒地老 ──
timers.length = 0;
const callsBefore = apiCalls.length;
let refreshes = 0;
AuthManager.isTokenExpired = () => true;
AuthManager.backendTokenRefresh = async () => {
    refreshes++;
    throw new Error('offline');
};
await NS.fetchNotifications();
for (let i = 0; i < 30 && timers.length; i++) {
    runTimers();
    await new Promise((r) => setImmediate(r));
}
assert.equal(timers.length, 0, '重試要停下來');
assert.ok(refreshes <= 8, `續登嘗試要有上限，實際 ${refreshes} 次`);
assert.equal(apiCalls.length, callsBefore, 'token 過期期間不打通知 API');
AuthManager.isTokenExpired = () => false;

// ── 9. 每日額度提示：一般會員顯示剩幾則，Pro／無限不顯示 ──────────────────────
assert.deepEqual(quotaState({ limit: 20, used: 5 }, false), { hidden: false, left: 15, limit: 20, low: false });
assert.deepEqual(quotaState({ limit: 20, used: 18 }, false), { hidden: false, left: 2, limit: 20, low: true });
assert.deepEqual(quotaState({ limit: 20, used: 25 }, false), { hidden: false, left: 0, limit: 20, low: true });
assert.equal(quotaState({ limit: 20, used: 5 }, true).hidden, true, 'Pro 不顯示');
assert.equal(quotaState({ limit: -1, used: -1 }, false).hidden, true, '無上限（-1）不顯示');
assert.equal(quotaState(null, false).hidden, true, '還沒拿到額度不顯示');

console.error('dm_realtime: ok');
