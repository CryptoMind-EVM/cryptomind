// 社群一般對話自訂順序（2026-10-01 DANNY：沒置頂也要能自己排；置頂只是一定在最上面）。看守：
//   1. 依最新訊息（預設）：照時間；自訂順序：還沒排過的（新對話）在上照時間，其他照自己排的位置；置頂永遠在最上面
//   2. 自訂順序時後端照位置分頁：請求帶 order=custom；還有更多時，排在已載最後一個私訊後面的群組先不放
//   3. 排序模式：置頂、其他對話各一區；拖「其他對話」→ 存 /api/chat-order、依最新訊息時自動改成自訂順序
//   4. 換排序方式記在 localStorage、重抓
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

function makeEl(id = '') {
    const classes = new Set();
    const attrs = {};
    return {
        id,
        innerHTML: '',
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
const store = {};
globalThis.window = globalThis;
globalThis.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => (store[k] = String(v)) };
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
const toasts = [];
globalThis.showToast = (text, type) => toasts.push([text, type]);

const BASE = Date.parse('2026-10-01T12:00:00Z');
const hoursAgo = (h) => new Date(BASE - h * 3600e3).toISOString();
const conv = (id, h, extra = {}) => ({ id, other_user_id: `u${id}`, other_username: `u${id}`, last_message_at: hoursAgo(h), pin_position: null, order_position: null, ...extra });
const state = {
    // 後端照「置頂 → 沒排過的照時間 → 排過的照位置」給（order=custom 時）
    convs: [conv(9, 30, { pin_position: 0 }), conv(5, 0.5), conv(1, 10, { order_position: 0 }), conv(2, 1, { order_position: 1 }), conv(3, 2, { order_position: 3 })],
    groups: [{ id: 7, name: '群', member_count: 3, unread_count: 0, last_message_at: hoursAgo(0.1), last_message: null, pin_position: null, order_position: 2 }],
    hasMoreLimit: 999,
};
const gets = [];
const puts = [];
globalThis.AppAPI = {
    get: async (url) => {
        gets.push(url);
        if (url.startsWith('/api/messages/conversations')) {
            const limit = Number(new URL(url, 'http://x').searchParams.get('limit'));
            return { success: true, total_unread: 0, conversations: structuredClone(state.convs).slice(0, Math.min(limit, state.hasMoreLimit)) };
        }
        if (url === '/api/groups') return { success: true, groups: structuredClone(state.groups) };
        if (url === '/api/groups/invites') return { success: true, invites: [] };
        return { success: true };
    },
    put: async (url, body) => (puts.push([url, body]), { success: true }),
};

const { SocialHub } = await load('../../web/js/friends.js');
async function load(rel) {
    return import(await loadModuleUrl(new URL(rel, import.meta.url).pathname));
}
const list = () => el('social-conv-list').innerHTML;
const convGets = () => gets.filter((u) => u.startsWith('/api/messages/conversations'));
const order = () => [...list().matchAll(/data-pin-key="([^"]+)"/g)].map((m) => m[1]);
const reorderRows = () => [...list().matchAll(/data-reorder-item="([^"]+)"/g)].map((m) => m[1]);

// ── 1) 依最新訊息（預設） ─────────────────────────────────
assert.equal(SocialHub.convSort, 'recent');
await SocialHub.loadConversations();
assert.ok(convGets().at(-1).includes('order=recent'));
assert.deepEqual(order(), ['dm:9', 'group:7', 'dm:5', 'dm:2', 'dm:3', 'dm:1'], '置頂在上；其他照時間（群 0.1h、5 0.5h、2 1h、3 2h、1 10h）');

// ── 4) 換成自訂順序：記住、重抓（請求帶 order=custom） ─────────
SocialHub.setConvSort('custom');
await new Promise((r) => setTimeout(r, 0));
assert.equal(store.socialConvSort, 'custom');
assert.ok(convGets().at(-1).includes('order=custom'), '後端照自訂位置分頁');
await SocialHub.loadConversations();
// 自訂：置頂 9 → 沒排過的 5 → 位置 0（1）、1（2）、2（群 7）、3（3）——群雖然最新也不往上跳
assert.deepEqual(order(), ['dm:9', 'dm:5', 'dm:1', 'dm:2', 'group:7', 'dm:3']);

// ── 2) 自訂順序＋還有更多：排在已載最後一個私訊後面的群組先不放 ──
state.groups[0].order_position = 5;
state.hasMoreLimit = 3; // 只回 9、5、1（limit 20 卻只回 3 個 → 這裡模擬「還有更多」要讓 hasMore 為真）
SocialHub.convLimit = 3;
await SocialHub.loadConversations();
assert.ok(!order().includes('group:7'), '群 7 位置 5 排在已載最後一個私訊（位置 0）後面：先不放');
state.hasMoreLimit = 999;
SocialHub.convLimit = 20;
state.groups[0].order_position = 2;
await SocialHub.loadConversations();

// ── 3) 排序模式：兩區、拖其他對話存 /api/chat-order ───────────
SocialHub.startConvReorder();
assert.ok(list().includes('data-pinned-list') && list().includes('data-other-list'));
assert.deepEqual(reorderRows(), ['dm:9', 'dm:5', 'dm:1', 'dm:2', 'group:7', 'dm:3']);
await SocialHub.saveCustomOrder(['dm:3', 'dm:5', 'dm:1', 'group:7', 'dm:2']);
assert.deepEqual(puts.at(-1), ['/api/chat-order', { items: [{ kind: 'dm', id: 3 }, { kind: 'dm', id: 5 }, { kind: 'dm', id: 1 }, { kind: 'group', id: 7 }, { kind: 'dm', id: 2 }] }]);
SocialHub.finishConvReorder();

// 依最新訊息時拖其他對話：自動改成自訂順序、提示
SocialHub.setConvSort('recent', { reload: false });
await SocialHub.saveCustomOrder(['dm:2', 'dm:1']);
assert.equal(SocialHub.convSort, 'custom');
assert.equal(store.socialConvSort, 'custom');
assert.deepEqual(toasts.at(-1), ['Switched to custom order', 'info']);

// 「依最新訊息」時排序模式的其他對話區有一句提示
SocialHub.setConvSort('recent', { reload: false });
SocialHub.renderConvList();
SocialHub.startConvReorder();
assert.ok(list().includes('Dragging here switches to custom order'));
SocialHub.finishConvReorder();

console.error('chat_order: ok');
