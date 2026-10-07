// 前端功能盤查（2026-09-25）的行為測試：價格警報清單、私訊載入舊訊息順序、
// 帳本（月曆／條目競態、重複送出、類別合併、-0 標紅、單次事件刪除確認）、
// 工具金鑰移除確認。都是「不噴錯但結果是錯的」那一類，只能跑起來看。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

// ── 最小 DOM ───────────────────────────────────────────────
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function makeEl(id = '') {
    const classes = new Set();
    let text = '';
    const el = {
        id,
        value: '',
        checked: false,
        disabled: false,
        innerHTML: '',
        title: '',
        style: {},
        dataset: {},
        scrollTop: 0,
        scrollHeight: 0,
        classList: {
            add: (...c) => c.forEach((x) => classes.add(x)),
            remove: (...c) => c.forEach((x) => classes.delete(x)),
            contains: (c) => classes.has(c),
            toggle: (c, on) => ((on ?? !classes.has(c)) ? classes.add(c) : classes.delete(c)),
        },
        setAttribute() {},
        addEventListener() {},
        removeEventListener() {},
        appendChild() {},
        insertBefore() {},
        remove() {},
        focus() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
        insertAdjacentHTML(pos, html) {
            if (pos === 'afterbegin') el.innerHTML = html + el.innerHTML;
            else el.innerHTML += html;
        },
    };
    // createElement('div') + textContent → innerHTML 的 escape（tab-journal._esc 的做法）
    Object.defineProperty(el, 'textContent', {
        get: () => text,
        set: (v) => { text = String(v); el.innerHTML = esc(text); },
    });
    return el;
}

let els = {};
const el = (id) => (els[id] ||= makeEl(id));
const resetDom = () => { els = {}; };

globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.document = {
    documentElement: { lang: 'en' },
    getElementById: (id) => el(id),
    createElement: () => makeEl(),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    removeEventListener() {},
};
globalThis.escapeHtml = esc;
globalThis.showToast = () => {};
globalThis.console.log = () => {};
globalThis.console.warn = () => {};
globalThis.console.error = ((orig) => (...a) => { if (String(a[0]).includes(': ok')) orig(...a); })(console.error);
globalThis.AppUtils = { refreshIcons() {} };
const flush = () => new Promise((r) => setImmediate(r));
function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
}
const load = async (rel) => import(await loadModuleUrl(new URL(rel, import.meta.url).pathname));

// ── 1) 價格警報：各分頁只列自己市場、symbol 要 escape、連點只送一次 ─────
{
    resetDom();
    await load('../../web/js/alerts.js');
    window.renderAlertList([
        { id: 'a1', symbol: '2330', market: 'tw_stock', condition: 'above', target: 900, repeat: false },
        { id: 'a2', symbol: 'AAPL', market: 'us_stock', condition: 'below', target: 150, repeat: true },
        { id: 'a3', symbol: '<img src=x>', market: 'us_stock', condition: 'above', target: 1, repeat: false },
        { id: 'a4', symbol: 'BTC', market: 'crypto', condition: 'above', target: 1, repeat: false },
    ]);
    const tw = el('alert-list-twstock').innerHTML;
    const us = el('alert-list-usstock').innerHTML;
    assert.ok(tw.includes('2330') && !tw.includes('AAPL') && !tw.includes('BTC'), `台股清單只列台股警報：${tw}`);
    assert.ok(us.includes('AAPL') && !us.includes('2330') && !us.includes('BTC'), `美股清單只列美股警報：${us}`);
    assert.ok(!us.includes('<img') && us.includes('&lt;img'), 'symbol 要 escape');
    assert.ok(tw.includes('data-click="deleteUserAlert" data-click-arg="a1"'), '每筆要有刪除鈕');
    assert.ok(!el('alert-list-section-twstock').classList.contains('hidden'), '拿到清單後要打開區塊');

    window.renderAlertList([{ id: 'a2', symbol: 'AAPL', market: 'us_stock', condition: 'below', target: 150 }]);
    assert.ok(el('alert-list-twstock').innerHTML.includes('noAlerts'), '台股沒有警報時顯示空狀態');

    // 連點「建立警報」（openAlertModal 會清空目標欄，所以之後才填）
    window.openAlertModal('2330', 'tw_stock');
    el('alert-condition').value = 'above';
    el('alert-target').value = '100';
    const posts = [];
    const d = deferred();
    window.AppAPI = {
        post: (url, body) => { posts.push(body); return d.promise; },
        get: async () => ({ alerts: [] }),
        hasSession: () => true,
    };
    const first = window.submitAlert();
    const second = window.submitAlert();
    await flush();
    assert.equal(posts.length, 1, '送出中再按一次不能再送');
    d.resolve({ success: true });
    await Promise.all([first, second]);
    await window.submitAlert();
    assert.equal(posts.length, 2, '送完之後要能再建立下一筆');
}

// ── 2d) 好友頁（SocialHub，私訊聊天室只剩這一個；原本 MessagesPage 的 2／2b／2c／2e 併入這裡）：以前只載最新 50 則、捲上去沒有舊訊息，點引用跳不到
// 舊訊息；快速換對話時，前一個對話較晚回來的第一頁還會蓋掉畫面（2026-09-29）
{
    resetDom();
    globalThis.AuthManager = { currentUser: { user_id: 'me', uid: 'me' }, isLoggedIn: () => true };
    await load('../../web/js/friends.js');
    const SH = window.SocialHub;
    const container = el('social-messages-container');
    const gets = [];
    const pendingByUrl = [];
    window.AppAPI = {
        get: (url) => {
            gets.push(url);
            const hit = pendingByUrl.find((p) => url.includes(p.match));
            if (hit) {
                pendingByUrl.splice(pendingByUrl.indexOf(hit), 1);
                return hit.promise;
            }
            return Promise.resolve({});
        },
        post: async () => ({}),
    };
    SH.renderMessageBubble = (m) => `[${m.id}]`;
    SH.markCurrentAsRead = () => {};
    SH.loadConversations = async () => {};
    const respond = (match) => {
        const d = deferred();
        pendingByUrl.push({ match, promise: d.promise });
        return d;
    };

    // 第一頁：記下游標（最舊那則）與 has_more
    const first = respond('/with/bob');
    const opening = SH.loadMessages('bob');
    first.resolve({ success: true, conversation: { id: 2 }, messages: [{ id: 100 }, { id: 101 }], has_more: true });
    await opening;
    assert.equal(container.innerHTML, '[100][101]');
    assert.equal(SH.oldestMessageId, 100);
    assert.equal(SH.hasMoreMessages, true);

    // 捲上去：用這個對話的游標往前抓，插在最前面，捲動位置不跳
    const older = respond('/conversation/2');
    const loadingMore = SH.loadMoreMessages();
    const again = SH.loadMoreMessages();
    assert.equal(again, loadingMore, '正在載入時再叫一次要回同一個 promise（點引用跳轉會 await 它）');
    older.resolve({ messages: [{ id: 90 }, { id: 91 }], has_more: false });
    await loadingMore;
    assert.ok(gets.at(-1).includes('before_id=100'), `要用最舊那則當游標：${gets.at(-1)}`);
    assert.equal(container.innerHTML, '[90][91][100][101]');
    assert.equal(SH.hasMoreMessages, false);
    assert.equal(await SH.loadMoreMessages(), false, '沒有更多就回 false（跳轉迴圈會停）');

    // 快速換對話：bob 的第一頁比 carol 晚回來，不能蓋掉 carol
    const slowBob = respond('/with/bob');
    const fastCarol = respond('/with/carol');
    const pBob = SH.loadMessages('bob');
    const pCarol = SH.loadMessages('carol');
    fastCarol.resolve({ success: true, conversation: { id: 3 }, messages: [{ id: 300 }], has_more: true });
    await pCarol;
    slowBob.resolve({ success: true, conversation: { id: 2 }, messages: [{ id: 100 }], has_more: true });
    await pBob;
    assert.equal(container.innerHTML, '[300]', `bob 較晚回來的第一頁蓋掉了 carol：${container.innerHTML}`);
    assert.equal(SH.currentConversationId, 3);
    assert.equal(SH.oldestMessageId, 300);

    // 往前抓途中換對話：舊對話的舊訊息不能塞進新對話，也不能卡住載入旗標
    const stale = respond('/conversation/3');
    const pStale = SH.loadMoreMessages();
    const reopen = respond('/with/dave');
    const pDave = SH.loadMessages('dave');
    reopen.resolve({ success: true, conversation: { id: 4 }, messages: [{ id: 400 }], has_more: true });
    await pDave;
    stale.resolve({ messages: [{ id: 290 }], has_more: true });
    await pStale;
    assert.equal(container.innerHTML, '[400]');
    assert.equal(SH.isLoadingMore, false);
}

// ── 3) 帳本 ─────────────────────────────────────────────────
resetDom();
const routes = [];
const apiCalls = { get: [], post: [], delete: [] };
window.AppAPI = {
    get: (url) => {
        apiCalls.get.push(url);
        const hit = routes.find((r) => url.startsWith(r.prefix));
        if (hit) {
            if (hit.queue) return hit.queue.shift().promise;
            return Promise.resolve(hit.body);
        }
        return Promise.resolve({});
    },
    post: (url, body) => { apiCalls.post.push({ url, body }); return (window.__postImpl || (async () => ({ ok: true })))(url, body); },
    put: async () => ({}),
    delete: async (url) => { apiCalls.delete.push(url); return {}; },
};
const route = (prefix, body) => {
    const i = routes.findIndex((r) => r.prefix === prefix);
    if (i >= 0) routes.splice(i, 1);
    routes.push({ prefix, body });
};
const routeQueue = (prefix, ds) => {
    const i = routes.findIndex((r) => r.prefix === prefix);
    if (i >= 0) routes.splice(i, 1);
    routes.push({ prefix, queue: ds });
};
await load('../../web/js/components/tab-journal.js');
const J = window.JournalTab;

// 3a) 類別彙總：後端依 category+幣別分組 → 同類別要合併成一條，最大類別也要算對
{
    route('/api/journal/entries', { entries: [] });
    route('/api/journal/summary', {
        summary: [
            { category: 'food', currency: 'TWD', base_currency: 'TWD', total: '150', count: 2 },
            { category: 'food', currency: 'USD', base_currency: 'TWD', total: '150', count: 1 },
            { category: 'transport', currency: 'TWD', base_currency: 'TWD', total: '200', count: 1 },
        ],
    });
    await J.refresh();
    const bars = el('journal-category-breakdown').innerHTML;
    assert.equal((bars.match(/>Food</g) || []).length, 1, `同一類別只能有一條：${bars}`);
    assert.ok(/NT\$300/.test(bars), `food 150+150 合併成 300：${bars}`);
    assert.ok(el('journal-summary').innerHTML.includes('Food'), '最大類別是合併後的 Food（300 > 200）');
}

// 3b) 淨流量 -0.3 不能顯示成紅色的「-NT$0」
{
    route('/api/journal/entries', {
        entries: [
            { id: 1, entry_type: 'income', category: 'salary', price: '100', converted_amount: '100', currency: 'TWD' },
            { id: 2, entry_type: 'expense', category: 'food', price: '100.3', converted_amount: '100.3', currency: 'TWD' },
        ],
    });
    route('/api/journal/summary', { summary: [] });
    await J.refresh();
    const cards = el('journal-summary').innerHTML;
    assert.ok(!cards.includes('-NT$0'), `淨流量 -0.3 不該顯示 -NT$0：${cards}`);
}

// 3b2) 總支出／總收入要算篩選下的全部條目：列表只回前 100 筆，以前拿它加總就少算
//      ——改用後端一起回的 totals（整個篩選範圍的 SQL 合計）
{
    route('/api/journal/entries', {
        entries: [{ id: 1, entry_type: 'expense', category: 'food', price: '100', converted_amount: '100', currency: 'TWD' }],
        totals: { expense: 25000, income: 3000.4 },
    });
    route('/api/journal/summary', { summary: [] });
    await J.refresh();
    const cards = el('journal-summary').innerHTML;
    assert.ok(cards.includes('NT$25,000'), `總支出要用後端的全範圍合計，不是列表那 1 筆：${cards}`);
    assert.ok(cards.includes('NT$3,000'), `總收入要用後端的全範圍合計：${cards}`);
    assert.ok(cards.includes('-NT$22,000'), `淨流量 3000.4-25000 → -22,000：${cards}`);
}

// 3c) 條目競態：先發的慢回應晚到，不能蓋掉後發的結果
{
    const slow = deferred();
    const fast = deferred();
    routeQueue('/api/journal/entries', [slow, fast]);
    route('/api/journal/summary', { summary: [] });
    const r1 = J.refresh();
    const r2 = J.refresh();
    fast.resolve({ entries: [{ id: 2, entry_type: 'expense', category: 'food', price: '1', currency: 'TWD', symbol: 'NEWER' }] });
    await r2;
    slow.resolve({ entries: [{ id: 1, entry_type: 'expense', category: 'food', price: '1', currency: 'TWD', symbol: 'STALE' }] });
    await r1;
    const list = el('journal-list').innerHTML;
    assert.ok(list.includes('NEWER') && !list.includes('STALE'), `舊回應蓋掉新結果：${list}`);
}

// 3d) 重複送出新增條目：送出中再按不能再 POST
{
    route('/api/journal/entries', { entries: [] });
    el('journal-entry-type').value = 'expense';
    el('journal-entry-amount').value = '120';
    el('journal-entry-currency').value = 'TWD';
    el('journal-entry-category').value = 'food';
    const d = deferred();
    window.__postImpl = () => d.promise;
    const before = apiCalls.post.length;
    const first = J.submitForm();
    const second = J.submitForm();
    await flush();
    assert.equal(apiCalls.post.length - before, 1, '送出中再按一次不能再送');
    d.resolve({ ok: true });
    await Promise.all([first, second]);
    window.__postImpl = null;
    el('journal-entry-amount').value = '50';
    await J.submitForm();
    assert.equal(apiCalls.post.length - before, 2, '送完之後要能再記下一筆');
}

// 3e) 喊單／行事曆新增同樣不能重複送出
{
    el('journal-call-symbol').value = 'AAPL';
    const d = deferred();
    window.__postImpl = () => d.promise;
    const before = apiCalls.post.length;
    const first = J.submitCall();
    const second = J.submitCall();
    await flush();
    assert.equal(apiCalls.post.length - before, 1, '喊單送出中再按一次不能再送');
    d.resolve({});
    await Promise.all([first, second]);

    el('journal-calendar-date').value = '2030-01-15';
    el('journal-calendar-title').value = 'FOMC';
    const d2 = deferred();
    window.__postImpl = () => d2.promise;
    const before2 = apiCalls.post.length;
    const add = J.addCalendarEvent();
    const add2 = J.addCalendarEvent();
    await flush();
    assert.equal(apiCalls.post.length - before2, 1, '行事曆新增送出中再按一次不能再送');
    d2.resolve({});
    await Promise.all([add, add2]);
    window.__postImpl = null;
}

// 3f) 月曆快速翻頁：前一個月的慢回應晚到，不能把別月的事件蓋進目前月曆
{
    route('/api/journal/calendar', { events: [] });
    await J.calendarSelect('2026-09-15');
    const oct = deferred();
    const nov = deferred();
    routeQueue('/api/journal/calendar', [oct, nov]);
    const p1 = J.calendarNext(); // → 2026-10（選 10/01）
    const p2 = J.calendarNext(); // → 2026-11（選 11/01）
    nov.resolve({ events: [{ id: 2, event_date: '2026-11-01', title: 'NOV-EVENT', source: 'user' }] });
    await p2;
    // 十月格子涵蓋到 11/07，舊回應裡的 11/01 事件若被收下會出現在目前選取日
    oct.resolve({ events: [{ id: 1, event_date: '2026-11-01', title: 'STALE-OCT', source: 'user' }] });
    await p1;
    const day = el('journal-calendar-list').innerHTML;
    assert.ok(day.includes('NOV-EVENT') && !day.includes('STALE-OCT'), `舊月份回應蓋掉目前月曆：${day}`);
}

// 3g) 單次事件刪除也要確認；取消就不打 API
{
    route('/api/journal/calendar', { events: [{ id: 7, event_date: '2026-11-01', title: 'ONE-OFF', source: 'user', recurrence: 'none' }] });
    await J.calendarToday();
    await J.calendarSelect('2026-11-01');
    const asked = [];
    window.showConfirmDialog = async (opts) => { asked.push(opts); return false; };
    const before = apiCalls.delete.length;
    await J.deleteCalendarEvent(7);
    assert.equal(asked.length, 1, '單次事件刪除要先確認');
    assert.equal(apiCalls.delete.length, before, '取消就不能刪');
    window.showConfirmDialog = async () => true;
    await J.deleteCalendarEvent(7);
    assert.equal(apiCalls.delete.length, before + 1, '確認後才刪');
}

// 3h) 投資視圖：已實現 -0.3 不能顯示成紅色的「≈ -0」
{
    route('/api/journal/positions', { positions: [{ symbol: 'X', market: 'us_stock', quantity: '0', realized_pnl: '-0.3', trade_count: 1 }] });
    route('/api/journal/entries', { entries: [] });
    await J.switchView('invest');
    const sum = el('journal-invest-summary').innerHTML;
    assert.ok(!sum.includes('≈ -0'), `-0.3 不該顯示成 ≈ -0：${sum}`);
    assert.ok(sum.includes('≈ +0'), `取整後是 0：${sum}`);
}

// ── 4) 工具金鑰移除要確認 ─────────────────────────────────────
{
    resetDom();
    window.I18n = { t: (k, o) => (o && o.defaultValue) || k };
    await load('../../web/js/toolSettings.js');
    const before = apiCalls.delete.length;
    window.showConfirmDialog = async () => false;
    await window.deleteToolKey('tavily');
    assert.equal(apiCalls.delete.length, before, '取消就不能移除金鑰');
    window.showConfirmDialog = async () => true;
    await window.deleteToolKey('tavily');
    assert.equal(apiCalls.delete.length, before + 1, '確認後才移除');
    assert.ok(apiCalls.delete.at(-1).endsWith('/api/user/api-keys/tavily'));
}

console.error('frontend_feature_bugs: ok');
