// 聊天側欄「對話歷史」分頁（2026-10-01：以前只抓 API 預設 20 筆、沒有下一頁，第 21 個起從清單消失）。看守：
//   1. 第一次 20 筆、清單底有「載入中」；多載一頁照目前數量一次抓；沒有更多就不再打
//   2. 超過 API 上限 100：分段一起抓
//   3. 重畫不跳回頂端
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

class FakeEl {
    constructor(tag = 'div') {
        this.tagName = tag.toUpperCase();
        this.children = [];
        this.dataset = {};
        this.className = '';
        this.style = {};
        this._html = '';
        this._scrollTop = 0;
    }
    set innerHTML(v) {
        this._html = v;
        this.children = [];
        this._starred = String(v).includes('starred-list') ? new FakeEl() : null;
        this._scrollTop = 0; // 清空內容會把捲動位置歸零（瀏覽器排版時才會，這裡直接模擬最壞情況）
    }
    get innerHTML() {
        return this._html;
    }
    get scrollTop() {
        return this._scrollTop;
    }
    set scrollTop(v) {
        this._scrollTop = v;
    }
    appendChild(c) {
        this.children.push(c);
        return c;
    }
    querySelector(sel) {
        if (sel === '.starred-list') return this._starred || new FakeEl();
        if (sel === '[data-sessions-more]') return this.children.find((c) => 'sessionsMore' in c.dataset) || null;
        return null;
    }
    querySelectorAll() {
        return [];
    }
    addEventListener() {}
    scrollIntoView() {}
}
const list = new FakeEl();
globalThis.window = globalThis;
globalThis.innerWidth = 1200;
globalThis.addEventListener = () => {};
globalThis.document = {
    getElementById: (id) => (id === 'chat-session-list' ? list : null),
    createElement: (tag) => new FakeEl(tag),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
};
globalThis.I18n = { t: (k) => k };
globalThis.AppStore = { set() {}, get: () => null };
globalThis.createIconsIn = () => {};
globalThis.SecurityUtils = { escapeHTML: (s) => String(s) };
globalThis.AuthManager = { isLoggedIn: () => true, currentUser: { user_id: 'me' } };
globalThis.console.log = () => {};

const ALL = Array.from({ length: 130 }, (_, i) => ({ id: `s${i}`, title: `chat ${i}`, is_pinned: 0, updated_at: '' }));
const requests = [];
const puts = [];
globalThis.AppAPI = {
    put: async (url, body) => (puts.push([url, body]), { success: true }),
    get: async (url) => {
        const q = new URL(url, 'http://x').searchParams;
        const limit = Number(q.get('limit') ?? 20);
        const offset = Number(q.get('offset') ?? 0);
        requests.push([limit, offset]);
        return { success: true, sessions: ALL.slice(offset, offset + limit) };
    },
};

const mod = await import(await loadModuleUrl(new URL('../../web/js/chat-sessions.js', import.meta.url).pathname));
const { loadSessions, loadMoreSessions, startStarredReorder, finishStarredReorder, saveStarredOrder } = mod;
const rows = () => list.children.filter((c) => c.dataset.sessionId).length;
const hasMore = () => list.children.some((c) => 'sessionsMore' in c.dataset);

// ── 1) 第一頁 ───────────────────────────────────────────
await loadSessions();
assert.deepEqual(requests.at(-1), [20, 0], '要明確帶 limit/offset');
assert.equal(rows(), 20);
assert.ok(hasMore(), '還有下一頁：清單底要有「載入中」');

// ── 3) 多載一頁：照目前數量一次抓、不跳回頂端 ──────────────
list.scrollTop = 640;
await loadMoreSessions();
assert.deepEqual(requests.at(-1), [40, 0]);
assert.equal(rows(), 40);
assert.equal(list.scrollTop, 640, '重畫後要回到原本的捲動位置');

// ── 2) 超過 100：分段一起抓 ─────────────────────────────
for (let i = 0; i < 4; i += 1) await loadMoreSessions();
assert.deepEqual(requests.slice(-2), [
    [100, 0],
    [20, 100],
], '120 筆 = 100 + 20');
assert.equal(rows(), 120);
await loadMoreSessions();
assert.equal(rows(), 130);
assert.ok(!hasMore(), '全部載完：沒有「載入中」');
const n = requests.length;
await loadMoreSessions();
assert.equal(requests.length, n, '沒有更多就不再打');

// ── 4) 收藏排序（2026-10-01）：收藏 2 個以上才有「調整順序」；排序模式只列收藏、有把手；拖完送新順序 ──
{
    ALL.slice(0, 3).forEach((x) => (x.is_pinned = 1));
    await loadSessions();
    const starredSection = () => list.children.find((c) => String(c.innerHTML).includes('starred-list'));
    assert.ok(starredSection().innerHTML.includes('data-click="startStarredReorder"'));
    assert.equal(starredSection()._starred.children.length, 3);
    assert.ok(starredSection()._starred.children.every((c) => c.dataset.sessionId), '一般模式：可以點開的列');

    startStarredReorder();
    await loadSessions({ shared: true });
    const starred = starredSection()._starred.children;
    assert.deepEqual(starred.map((c) => c.dataset.reorderItem), ['s0', 's1', 's2']);
    assert.ok(starred.every((c) => c.innerHTML.includes('data-reorder-handle')), '排序模式每列有把手');
    assert.ok(starredSection().innerHTML.includes('data-click="finishStarredReorder"'));
    assert.equal(rows(), 0, '排序模式先收起一般對話');
    assert.ok(!hasMore(), '排序模式不往下載');

    await saveStarredOrder(['s2', 's0', 's1']);
    assert.deepEqual(puts.at(-1), ['/api/chat/sessions/pin-order', { session_ids: ['s2', 's0', 's1'] }]);

    finishStarredReorder();
    await loadSessions({ shared: true });
    assert.ok(rows() > 0, '完成後一般對話回來');

    // 只剩 1 個收藏：沒有「調整順序」
    ALL[1].is_pinned = 0;
    ALL[2].is_pinned = 0;
    await loadSessions();
    assert.ok(!starredSection().innerHTML.includes('startStarredReorder'));
}

console.error('chat_sessions_paging: ok');
