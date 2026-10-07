// 私訊收回的前端契約（2026-09-29）。
//
// 收回原本只改 DB 類型、不推事件：對方開著聊天室會一直看到原文；鈴鐺裡的預覽也還在；
// 確認框是原生 confirm（「getcryptomind.com 說」）。現在：24 小時內才出「收回」、
// message_recalled 即時換掉那一列、notification_updated 原地換掉通知、確認框走官方樣式。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

// ── 假環境 ────────────────────────────────────────────────────────────────
globalThis.setTimeout = () => 0;
globalThis.clearTimeout = () => {};
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.document = {
    visibilityState: 'visible',
    addEventListener: () => {},
    getElementById: () => null,
};
globalThis.dispatchEvent = () => {};
globalThis.location = { protocol: 'https:', host: 'example.test' };
console.log = () => {};
globalThis.I18n = { t: (k, o) => (o ? `${k}:${JSON.stringify(o)}` : k) };

const sockets = [];
class FakeWebSocket {
    static OPEN = 1;
    constructor() {
        this.readyState = 0;
        this.sent = [];
        sockets.push(this);
    }
    send(data) {
        this.sent.push(JSON.parse(data));
    }
    close() {}
    open() {
        this.readyState = FakeWebSocket.OPEN;
        this.onopen?.();
    }
    receive(data) {
        this.onmessage?.({ data: JSON.stringify(data) });
    }
}
globalThis.WebSocket = FakeWebSocket;
globalThis.AuthManager = {
    currentUser: { user_id: 'me' },
    isLoggedIn: () => true,
    isLoginInFlight: () => false,
    shouldDeferExpiredSessionCleanup: () => false,
    isTokenExpired: () => false,
};
globalThis.AppAPI = { get: async () => ({ notifications: [], unread_count: 0 }) };
const toasts = [];
globalThis.showToast = (msg) => toasts.push(msg);

const { canRecallMessage, markMessageRowRecalled, confirmDmAction, recalledPreviewText, recallDmMessage } =
    await import(await loadModuleUrl(new URL('../../web/js/messages.js', import.meta.url).pathname));
await import(await loadModuleUrl(new URL('../../web/js/notification-service.js', import.meta.url).pathname));
const WS = window.MessagesWebSocket;
const NS = window.NotificationService;

// ── 1. 24 小時內才能收回；已收回的不能再收 ─────────────────────────────────
const now = Date.parse('2026-09-29T12:00:00Z');
const ago = (ms) => new Date(now - ms).toISOString();
const H = 60 * 60 * 1000;
assert.equal(canRecallMessage({ message_type: 'text', created_at: ago(24 * H - 60000) }, now), true);
assert.equal(canRecallMessage({ message_type: 'text', created_at: ago(24 * H + 60000) }, now), false);
assert.equal(canRecallMessage({ message_type: 'recalled', created_at: ago(1000) }, now), false);
assert.equal(canRecallMessage({ message_type: 'text', created_at: 'garbage' }, now), false);

// ── 2. message_recalled：私訊 WS 交給頁面 ─────────────────────────────────
WS.connect();
const sock = sockets.at(-1);
sock.open();
sock.receive({ type: 'authenticated' });
const recalled = [];
WS.onRecalled((messageId, conversationId) => recalled.push([messageId, conversationId]));
sock.receive({ type: 'message_recalled', message_id: 5, conversation_id: 9 });
assert.deepEqual(recalled, [[5, 9]]);

// ── 3. 畫面上那一列換成「已收回」樣式，保留發送者與時間 ─────────────────────
let replaced = null;
const row = {
    dataset: { from: 'other', ts: '2026-09-29T11:00:00Z' },
    set outerHTML(html) {
        replaced = html;
    },
};
const container = {
    querySelector: (sel) => (sel === '[data-message-id="5"]' ? row : null),
    querySelectorAll: () => [],
};
let rendered = null;
const ok = markMessageRowRecalled(container, 5, (msg) => {
    rendered = msg;
    return '<div>recalled</div>';
});
assert.equal(ok, true);
assert.deepEqual(rendered, {
    id: 5,
    from_user_id: 'other',
    created_at: '2026-09-29T11:00:00Z',
    message_type: 'recalled',
});
assert.equal(replaced, '<div>recalled</div>');
assert.equal(markMessageRowRecalled(container, 6, () => ''), false, '不在畫面上就不動');

// ── 4. 對話列表預覽：最後一則被收回顯示「已收回」，不是空白 ─────────────────
assert.equal(recalledPreviewText({ last_message: '', last_message_type: 'recalled' }), 'messages.recalledPreview');
assert.equal(recalledPreviewText({ last_message: 'hi', last_message_type: 'text' }), null);

// ── 5. 確認框：有官方 dialog 就用它（danger），沒有才退回原生 ───────────────
let dialogOpts = null;
window.showConfirmDialog = async (opts) => {
    dialogOpts = opts;
    return true;
};
let nativeCalled = false;
globalThis.confirm = () => {
    nativeCalled = true;
    return false;
};
assert.equal(await confirmDmAction({ title: 'T', message: 'M', confirmText: 'C', danger: true }), true);
assert.deepEqual(dialogOpts, { title: 'T', message: 'M', confirmText: 'C', danger: true });
assert.equal(nativeCalled, false, '有官方 dialog 就不能跳原生 confirm');
delete window.showConfirmDialog;
assert.equal(await confirmDmAction({ title: 'T', message: 'M' }), false);
assert.equal(nativeCalled, true);

// ── 6. 通知被改寫：原地換掉、不跳 toast；不認得的 id 不新增 ────────────────
NS.notifications = [
    { id: 'n1', type: 'message', body: 'A: 秘密', is_read: false, data: { from_username: 'A' } },
    { id: 'n2', type: 'system', body: 'x', is_read: true },
];
const toastCount = toasts.length;
NS.applyRemoteUpdate({
    id: 'n1',
    type: 'message',
    body: 'A: 訊息已收回',
    is_read: false,
    data: { from_username: 'A', recalled: true },
});
assert.equal(NS.notifications[0].body, 'A: 訊息已收回');
assert.equal(NS.notifications.length, 2);
assert.equal(toasts.length, toastCount, '改寫不是新通知，不跳 toast');
NS.applyRemoteUpdate({ id: 'zzz', type: 'message', body: 'q' });
assert.equal(NS.notifications.length, 2);

// ── 7. 已收回通知的內文走 i18n ────────────────────────────────────────────
const d = NS.describe({ type: 'message', body: 'A: 訊息已收回', data: { from_username: 'A', recalled: true } });
assert.equal(d.body, 'notification.messageRecalledBody:{"name":"A"}');

// ── 8. 別的裝置先收回了（409 already_recalled）：當成功，畫面跟上，不跳錯誤 ──────
window.showConfirmDialog = async () => true;
globalThis.AppAPI.delete = async () => {
    throw new Error('already_recalled');
};
let marked = null;
const row2 = {
    dataset: { from: 'me', ts: '2026-09-29T11:00:00Z' },
    set outerHTML(html) {
        marked = html;
    },
};
const container2 = { querySelector: () => row2, querySelectorAll: () => [] };
const errorToasts = toasts.length;
window.showToast = (msg, type) => toasts.push(`${type}:${msg}`);
assert.equal(await recallDmMessage(7, { container: container2, renderBubble: () => '<div>r</div>' }), true);
assert.equal(marked, '<div>r</div>');
assert.equal(toasts.length, errorToasts, 'already_recalled 不是錯誤，不跳 toast');
