// 點通知打開全文（2026-09-29）。
//
// 以前只有公告會開全文；早報、價格提醒、錢包監控點下去只是關掉面板，列表又只顯示兩行，
// 等於內容看不到。Base App 早報推播點開是 /?brief=日期，前端也沒接。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

// ── 最小 DOM：只做 showNotificationDetail 用到的部分 ─────────────────────────
class Node {
    constructor(tag) {
        this.tagName = tag.toUpperCase();
        this.children = [];
        this.parent = null;
        this.dataset = {};
        this.attrs = {};
        this.listeners = {};
        this.className = '';
        this.id = '';
        this.textContent = '';
    }
    setAttribute(k, v) {
        this.attrs[k] = String(v);
    }
    append(...nodes) {
        for (const n of nodes) {
            n.parent = this;
            this.children.push(n);
        }
    }
    appendChild(n) {
        this.append(n);
        return n;
    }
    remove() {
        if (!this.parent) return;
        this.parent.children = this.parent.children.filter((c) => c !== this);
        this.parent = null;
    }
    addEventListener(type, fn) {
        (this.listeners[type] ||= []).push(fn);
    }
    click() {
        for (const fn of this.listeners.click || []) fn({});
    }
    *walk() {
        yield this;
        for (const c of this.children) yield* c.walk();
    }
}
const body = new Node('body');
const docListeners = {};
globalThis.window = globalThis;
globalThis.document = {
    body,
    visibilityState: 'visible',
    createElement: (tag) => new Node(tag),
    getElementById: (id) => [...body.walk()].find((n) => n.id === id) || null,
    addEventListener: (type, fn) => (docListeners[type] ||= []).push(fn),
    removeEventListener: (type, fn) => {
        docListeners[type] = (docListeners[type] || []).filter((f) => f !== fn);
    },
};
globalThis.addEventListener = () => {};
globalThis.dispatchEvent = () => {};
globalThis.CustomEvent = class {
    constructor(type, init) {
        this.detail = init?.detail;
    }
};
console.log = () => {};

const T = {
    'notification.kind.dailyBrief': '每日早報',
    'notification.action.briefSettings': '早報設定',
    'notification.action.openMarket': '查看行情',
    'notification.action.walletMonitor': '前往錢包監控',
    'notification.briefNotFound': '找不到這份早報',
    'notifications.announcement': '公告',
    'notification.title': '通知',
    'common.close': '關閉',
};
globalThis.I18n = { t: (k) => T[k] || k };
const tabs = [];
globalThis.switchTab = (tab) => tabs.push(tab);
const toasts = [];
globalThis.showToast = (msg, type) => toasts.push({ msg, type });
globalThis.AuthManager = { currentUser: { user_id: 'me' }, isLoggedIn: () => true };
globalThis.AppAPI = { get: async () => ({ notifications: [], unread_count: 0 }), post: async () => ({}) };
globalThis.SecurityUtils = { escapeHTML: (s) => s };
globalThis.AppUtils = { refreshIcons: () => {} };

let url = { pathname: '/', search: '', hash: '#chat' };
globalThis.location = new Proxy({}, { get: (_, k) => url[k] });
const replaced = [];
globalThis.history = {
    state: null,
    replaceState: (_s, _t, next) => {
        replaced.push(next);
        const m = next.match(/^([^?#]*)(\?[^#]*)?(#.*)?$/);
        url = { pathname: m[1], search: m[2] || '', hash: m[3] || '' };
    },
};

const detail = await import(
    await loadModuleUrl(new URL('../../web/js/notification-detail.js', import.meta.url).pathname)
);
await import(await loadModuleUrl(new URL('../../web/js/notification-service.js', import.meta.url).pathname));
await import(
    await loadModuleUrl(new URL('../../web/js/components/NotificationPanel.js', import.meta.url).pathname)
);
const NS = window.NotificationService;
const read = [];
NS.markAsRead = async (id) => read.push(id);
const modal = () => document.getElementById('notif-detail-modal');
const byId = (id) => document.getElementById(id);

// ── 純函式：去處對應 ─────────────────────────────────────────────────────────
const { detailAction, briefDateFromSearch, findBrief } = detail;
assert.deepEqual(detailAction({ type: 'daily_brief' }), {
    labelKey: 'notification.action.briefSettings',
    tab: 'settings',
    scrollTo: 'settings-brief-card',
});
assert.equal(detailAction({ type: 'price_alert', data: { market: 'tw_stock' } }).tab, 'twstock');
assert.equal(detailAction({ type: 'price_alert', data: { market: 'cn_stock' } }).tab, 'astock', 'A 股分頁叫 astock');
assert.equal(detailAction({ type: 'price_alert', data: { market: 'mars' } }), null, '不認得的市場不給按鈕');
assert.equal(detailAction({ type: 'wallet_alert' }).tab, 'wallet-monitor');
assert.equal(detailAction({ type: 'announcement' }), null);
// 旗標關閉的分頁（加密貨幣）不放「去相關頁面」——進不去的按鈕點了沒反應
assert.equal(detailAction({ type: 'price_alert', data: { market: 'crypto' } }).tab, 'crypto');
{
    const off = { isUnavailable: (item) => item.id === 'crypto' };
    globalThis.NAV_ITEMS = [{ id: 'crypto' }, { id: 'twstock' }];
    globalThis.NavPreferences = off;
    assert.equal(detailAction({ type: 'price_alert', data: { market: 'crypto' } }), null, '加密貨幣分頁關閉：不給按鈕');
    assert.equal(detailAction({ type: 'price_alert', data: { market: 'tw_stock' } }).tab, 'twstock', '其餘市場照常');
    delete globalThis.NAV_ITEMS;
    delete globalThis.NavPreferences;
}

assert.equal(briefDateFromSearch('?brief=2026-09-27'), '2026-09-27');
assert.equal(briefDateFromSearch('?x=1&brief=2026-09-27'), '2026-09-27');
assert.equal(briefDateFromSearch('?brief=2026-09-27<svg>'), null, '日期格式以外一律不收');
assert.equal(briefDateFromSearch('?brief='), null);
assert.equal(briefDateFromSearch(''), null);

const brief = {
    id: 'b1',
    type: 'daily_brief',
    title: '☀️ 每日早報',
    body: '持倉\nBTC +1.2%\n\n警報\nETH 跌破 3000',
    data: { date: '2026-09-27' },
    created_at: '2026-09-27T00:05:00Z',
};
const olderBrief = { ...brief, id: 'b0', data: { date: '2026-09-26' } };
assert.equal(findBrief([olderBrief, brief], '2026-09-27'), brief);
assert.equal(findBrief([{ ...brief, type: 'announcement' }], '2026-09-27'), null);

// ── 點早報：開全文（不是只關面板），有「早報設定」鈕 ─────────────────────────
const panel = Object.create(window.NotificationPanel.prototype);
panel.handleNotificationClick(brief);
assert.ok(modal(), '點早報要打開全文');
assert.equal(modal().dataset.type, 'daily_brief');
assert.equal(byId('notif-modal-body').textContent, brief.body, '全文，不是列表那兩行');
assert.equal(byId('notif-modal-action').textContent, '早報設定');
const heading = [...modal().walk()].find((n) => n.tagName === 'H3').textContent;
assert.ok(heading.includes('2026') && heading.includes('27'), `早報標題改顯示日期（標頭已經寫早報）：${heading}`);
byId('notif-modal-action').click();
assert.equal(modal(), null, '按了就關掉視窗');
assert.deepEqual(tabs, ['settings']);

// Esc 關閉，且不留 keydown 監聽
panel.handleNotificationClick(brief);
assert.equal((docListeners.keydown || []).length, 1);
for (const fn of [...docListeners.keydown]) fn({ key: 'Escape' });
assert.equal(modal(), null);
assert.equal((docListeners.keydown || []).length, 0);

// 再開一次不會疊兩個，前一個的 keydown 監聽也要一起拿掉（review：換開另一則會殘留）
panel.handleNotificationClick(brief);
panel.handleNotificationClick(brief);
assert.equal([...body.walk()].filter((n) => n.id === 'notif-detail-modal').length, 1);
assert.equal((docListeners.keydown || []).length, 1, '只剩目前這個視窗的監聽');
byId('notif-modal-close').click();
assert.equal(modal(), null);
assert.equal((docListeners.keydown || []).length, 0);

// 價格提醒：市場認得才有按鈕；公告：沒有按鈕
panel.handleNotificationClick({ id: 'p1', type: 'price_alert', title: 'BTC', body: 'x', data: { market: 'mars' } });
assert.ok(modal());
assert.equal(byId('notif-modal-action'), null);
panel.handleNotificationClick({ id: 'p2', type: 'price_alert', title: 'BTC', body: 'x', data: { market: 'crypto' } });
byId('notif-modal-action').click();
assert.equal(tabs.at(-1), 'crypto');
panel.handleNotificationClick({ id: 'a1', type: 'announcement', title: '維護', body: '今晚維護' });
assert.equal(byId('notif-modal-body').textContent, '今晚維護');
assert.equal([...modal().walk()].find((n) => n.tagName === 'H3').textContent, '維護', '其他類型照原標題');
assert.equal(byId('notif-modal-action'), null);
byId('notif-modal-close').click();

// 有專屬去處的照舊跳頁，不開全文
panel.handleNotificationClick({ id: 'm1', type: 'membership_expiring', title: 'x', body: 'x' });
assert.equal(modal(), null);
assert.equal(tabs.at(-1), 'settings');

// ── ?brief= 深連結 ───────────────────────────────────────────────────────────
url = { pathname: '/', search: '?brief=2026-09-27&ref=base', hash: '#chat' };
assert.equal(await detail.consumeBriefDeepLink([olderBrief, brief]), true);
assert.equal(replaced.at(-1), '/?ref=base#chat', '只拿掉 brief，其他參數與 hash 留著');
assert.equal(byId('notif-modal-body').textContent, brief.body);
assert.deepEqual(read, ['b1'], '打開就算讀了');
byId('notif-modal-close').click();

// 參數用過就沒了：同一頁再拿到清單不會再跳
assert.equal(await detail.consumeBriefDeepLink([brief]), false);
assert.equal(modal(), null);

// 找不到（太舊被清掉）：講一聲，網址一樣清掉
url = { pathname: '/', search: '?brief=2026-01-01', hash: '' };
assert.equal(await detail.consumeBriefDeepLink([brief]), false);
assert.equal(toasts.at(-1).msg, '找不到這份早報');
assert.equal(replaced.at(-1), '/');
assert.equal(modal(), null);

// 格式不對：同樣清掉、不開
url = { pathname: '/', search: '?brief=<svg>', hash: '' };
assert.equal(await detail.consumeBriefDeepLink([brief]), false);
assert.equal(url.search, '');

// ── 清單只有最新 50 則：往後翻頁找（review：推播晚點才點開、中間來了很多通知）──────
const filler = (n, from = 0) => Array.from({ length: n }, (_, i) => ({ id: `f${from + i}`, type: 'message', data: {} }));
const pages = [];
url = { pathname: '/', search: '?brief=2026-09-27', hash: '' };
assert.equal(
    await detail.consumeBriefDeepLink(filler(50), {
        hasMore: true,
        fetchPage: async (offset, size) => {
            pages.push([offset, size]);
            return offset === 50 ? [...filler(99, 50), brief] : [];
        },
    }),
    true
);
assert.deepEqual(pages, [[50, 100]]);
assert.equal(byId('notif-modal-body').textContent, brief.body);
byId('notif-modal-close').click();

// 翻頁有上限，找不到還是講一聲；翻頁失敗也一樣
pages.length = 0;
url = { pathname: '/', search: '?brief=2026-09-27', hash: '' };
const before = toasts.length;
await detail.consumeBriefDeepLink(filler(50), {
    hasMore: true,
    fetchPage: async (offset, size) => (pages.push([offset, size]), filler(size, offset)),
});
assert.deepEqual(pages.map((p) => p[0]), [50, 150, 250], '最多往後翻 3 頁');
assert.equal(toasts.length, before + 1);
url = { pathname: '/', search: '?brief=2026-09-27', hash: '' };
await detail.consumeBriefDeepLink(filler(50), {
    hasMore: true,
    fetchPage: async () => {
        throw new Error('network');
    },
});
assert.equal(toasts.length, before + 2);
assert.equal(modal(), null);

// 清單沒滿（hasMore=false）就不翻頁
pages.length = 0;
url = { pathname: '/', search: '?brief=2026-01-01', hash: '' };
await detail.consumeBriefDeepLink(filler(3), { hasMore: false, fetchPage: async () => (pages.push(1), []) });
assert.equal(pages.length, 0);

// ── 接在 fetchNotifications 後面：登入後第一次拿到清單就打開 ─────────────────
url = { pathname: '/', search: '?brief=2026-09-27', hash: '' };
AppAPI.get = async () => ({ notifications: [brief], unread_count: 1 });
NS._isTokenExpired = () => false;
await NS.fetchNotifications();
await new Promise((r) => setTimeout(r, 0));
assert.ok(modal(), 'fetch 之後要自己打開');
assert.equal(url.search, '');
byId('notif-modal-close').click();

console.error('notification_detail: ok');
