// 社群對話置頂與排序（2026-10-01）。看守：
//   1. 拖拉落點：拿被拖那列的中心點跟其他列比
//   2. 只排一部分（在「私訊」分類裡排）：那幾個填回原本的位置，群組的位置不動
//   3. 列表：置頂的照自己排的順序放最上面（就算比較舊）；2 個以上才有「調整順序」；排序模式置頂／其他各一區、有把手
//   4. 置頂／取消：打 API、用回傳的清單更新畫面；超過上限提示
//   5. 拖完送出的是完整順序
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
globalThis.window = globalThis;
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

const load = async (rel) => import(await loadModuleUrl(new URL(rel, import.meta.url).pathname));

// ── 1) 拖拉落點 ──────────────────────────────────────────
{
    const { targetIndex } = await load('../../web/js/drag-reorder.js');
    const rects = [0, 60, 120, 180].map((top) => ({ top, height: 60 }));
    assert.equal(targetIndex(rects, 0, 30), 0, '沒動');
    assert.equal(targetIndex(rects, 0, 95), 1, '中心點過了下一列的中線');
    assert.equal(targetIndex(rects, 0, 205), 2, '還沒過最後一列的中線（210）');
    assert.equal(targetIndex(rects, 0, 215), 3);
    assert.equal(targetIndex(rects, 3, 85), 1, '往上拖');
    assert.equal(targetIndex(rects, 3, 10), 0);
    assert.equal(targetIndex(rects, 2, 145), 2, '還沒過中線就不換');
    // 拖到頂被夾住：中心點剛好在第一列中線上也要換（2026-10-01 實測拖不到第一格）
    assert.equal(targetIndex(rects, 1, 30), 0);
    assert.equal(targetIndex(rects, 2, 210), 3, '拖到底同理');
}

// ── 2) 只排一部分 ────────────────────────────────────────
const pins = await load('../../web/js/chat-pins.js');
{
    const all = ['group:1', 'dm:5', 'group:2', 'dm:9'];
    assert.deepEqual(pins.mergeSubsetOrder(all, ['dm:9', 'dm:5']), ['group:1', 'dm:9', 'group:2', 'dm:5']);
    assert.deepEqual(pins.mergeSubsetOrder(all, all.slice().reverse()), all.slice().reverse());
    assert.deepEqual(pins.parsePinKey('group:7'), { kind: 'group', id: 7 });
    assert.equal(pins.pinKey('dm', '12'), 'dm:12');
}

// ── 3～5) 列表 ───────────────────────────────────────────
const BASE = Date.parse('2026-10-01T12:00:00Z');
const hoursAgo = (h) => new Date(BASE - h * 3600e3).toISOString();
const state = {
    convs: [
        // 後端讓置頂私訊排最前面（不管時間）
        { id: 9, other_user_id: 'old', other_username: '老朋友', last_message_at: hoursAgo(50), pin_position: 1 },
        { id: 1, other_user_id: 'a', other_username: 'A', last_message_at: hoursAgo(1), pin_position: null },
        { id: 2, other_user_id: 'b', other_username: 'B', last_message_at: hoursAgo(2), pin_position: null },
    ],
    groups: [{ id: 7, name: '置頂群', member_count: 3, unread_count: 0, last_message_at: hoursAgo(40), last_message: null, pin_position: 0 }],
};
const calls = [];
let pinError = null;
globalThis.AppAPI = {
    get: async (url) => {
        // 回傳複本：跟真的 API 一樣，畫面手上的資料跟「伺服器」不是同一份物件
        if (url.startsWith('/api/messages/conversations')) return { success: true, total_unread: 0, conversations: structuredClone(state.convs) };
        if (url === '/api/groups') return { success: true, groups: structuredClone(state.groups) };
        if (url === '/api/groups/invites') return { success: true, invites: [] };
        return { success: true };
    },
    put: async (url, body) => {
        calls.push(['PUT', url, body]);
        if (pinError) throw new Error(pinError);
        if (url === '/api/chat-pins/order') {
            return { success: true, pins: body.items.map((p, i) => ({ ...p, position: i })) };
        }
        // 置頂 dm:1 → 排在最後
        return { success: true, pins: [{ kind: 'group', id: 7, position: 0 }, { kind: 'dm', id: 9, position: 1 }, { kind: 'dm', id: 1, position: 2 }] };
    },
    delete: async (url) => {
        calls.push(['DELETE', url]);
        return { success: true, pins: [{ kind: 'dm', id: 9, position: 0 }] };
    },
};

const { SocialHub } = await load('../../web/js/friends.js');
const list = () => el('social-conv-list').innerHTML;
const order = () => [...list().matchAll(/data-pin-key="([^"]+)"/g)].map((m) => m[1]);
const reorderOrder = () => [...list().matchAll(/data-reorder-item="([^"]+)"/g)].map((m) => m[1]);

{
    await SocialHub.loadConversations();
    assert.deepEqual(order(), ['group:7', 'dm:9', 'dm:1', 'dm:2'], '置頂（群 7、老朋友）在最上面、照置頂順序；其他照時間');
    assert.ok(list().includes('SocialHub.startConvReorder'), '置頂 2 個以上：有「調整順序」');
    assert.ok(list().includes('data-pinned="1"') && list().includes('lucide="pin"'), '置頂列有圖釘');
    assert.ok(!list().includes('data-reorder-handle'), '一般模式沒有把手');

    // 排序模式：置頂、其他對話各一區（2026-10-01 起一般對話也能排），每列有把手、有「完成」
    SocialHub.startConvReorder();
    assert.deepEqual(reorderOrder(), ['group:7', 'dm:9', 'dm:1', 'dm:2']);
    const pinnedPart = list().split('data-other-list')[0];
    assert.ok(pinnedPart.includes('data-reorder-item="group:7"') && !pinnedPart.includes('data-reorder-item="dm:1"'), '置頂一區、其他一區');
    assert.equal((list().match(/data-reorder-handle/g) || []).length, 4);
    assert.ok(list().includes('SocialHub.finishConvReorder'));
    assert.ok(!list().includes('data-pin-key="dm:1"'), '排序模式不畫一般列（只有把手列）');

    // 拖完：送完整順序、用回傳更新
    await SocialHub.saveConvOrder(['dm:9', 'group:7']);
    assert.deepEqual(calls.at(-1), ['PUT', '/api/chat-pins/order', { items: [{ kind: 'dm', id: 9 }, { kind: 'group', id: 7 }] }]);
    SocialHub.finishConvReorder();
    assert.deepEqual(order().slice(0, 2), ['dm:9', 'group:7']);

    // 在「私訊」分類裡排：群組的位置不動
    SocialHub.groupsEnabled = true;
    SocialHub.setConvFilter('dm');
    state.convs[0].pin_position = 1; // 回到 group:7=0、dm:9=1
    SocialHub.applyPins([{ kind: 'group', id: 7, position: 0 }, { kind: 'dm', id: 9, position: 1 }, { kind: 'dm', id: 1, position: 2 }]);
    await SocialHub.saveConvOrder(['dm:1', 'dm:9']);
    assert.deepEqual(calls.at(-1)[2].items, [{ kind: 'group', id: 7 }, { kind: 'dm', id: 1 }, { kind: 'dm', id: 9 }]);
    SocialHub.setConvFilter('all');

    // 取消置頂：打 DELETE、畫面跟著回傳更新；剩 1 個就沒有「調整順序」
    await SocialHub.togglePinFromList('dm:1');
    assert.deepEqual(calls.at(-1), ['DELETE', '/api/chat-pins/dm/1']);
    assert.ok(!list().includes('SocialHub.startConvReorder'), '只剩 1 個置頂就不需要排');

    // 置頂：打 PUT
    await SocialHub.togglePinFromList('dm:2');
    assert.deepEqual(calls.at(-1).slice(0, 2), ['PUT', '/api/chat-pins/dm/2']);

    // 存順序時還在路上的列表請求（舊順序）：回來了不能把畫面蓋回去
    {
        let release;
        const realGet = AppAPI.get;
        AppAPI.get = async (url) => {
            if (url.startsWith('/api/messages/conversations')) await new Promise((r) => (release = r));
            return realGet(url);
        };
        state.convs[0].pin_position = 0; // 伺服器那時候的舊順序：dm:9 在前
        state.groups[0].pin_position = 1;
        const stale = SocialHub.loadConversations();
        await new Promise((r) => setTimeout(r, 0));
        SocialHub.applyPins([{ kind: 'group', id: 7, position: 0 }, { kind: 'dm', id: 9, position: 1 }]);
        await SocialHub.saveConvOrder(['group:7', 'dm:9']);
        SocialHub.renderConvList();
        release();
        await stale;
        assert.deepEqual(order().slice(0, 2), ['group:7', 'dm:9'], '舊的列表回應不能把剛排好的順序蓋掉');
        AppAPI.get = realGet;
    }

    // 超過上限：提示
    pinError = 'pin_limit_reached';
    await SocialHub.togglePinFromList('dm:5');
    assert.deepEqual(toasts.at(-1), ['You can pin up to 10 chats', 'error'], '上限有專屬提示（測試的 I18n 只回 key，走英文備用字）');
    pinError = null;
}

// ── 6) 搜尋框旁的排序鈕一直看得到：置頂不到 2 個時說明怎麼置頂，不進空的排序模式（DANNY：找不到排序模式） ──
{
    // 置頂只有 1 個，但一般對話夠多：照樣進排序模式（一般對話也能排）
    SocialHub.convReorder = false;
    SocialHub.applyPins([{ kind: 'dm', id: 9, position: 0 }]);
    SocialHub.startConvReorder();
    assert.equal(SocialHub.convReorder, true);
    SocialHub.finishConvReorder();
    // 能拖的東西不到 2 個（只有一段對話）：說明怎麼用，不進空的排序模式
    const saved = SocialHub._convData;
    SocialHub._convData = { conversations: [{ id: 1, pin_position: null }], groups: [], hasMore: false };
    SocialHub.startConvReorder();
    assert.equal(SocialHub.convReorder, false);
    assert.ok(String(toasts.at(-1)[0]).startsWith('Long-press a chat'), toasts.at(-1));
    SocialHub._convData = saved;
}

console.error('chat_pins: ok');
