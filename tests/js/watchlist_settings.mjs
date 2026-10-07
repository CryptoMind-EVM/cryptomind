// 設定頁「我的自選」（web/js/watchlist-settings.js）：載入、加入（錯誤訊息）、移除、每一檔的警報。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const els = {};
const mk = (extra = {}) => ({ innerHTML: '', textContent: '', className: '', value: '', disabled: false, ...extra });
for (const id of ['brief-watchlist-section', 'watchlist-items', 'watchlist-count', 'watchlist-status', 'watchlist-add']) {
    els[id] = mk();
}
els['watchlist-market'] = mk({ value: 'crypto' });
els['watchlist-symbol'] = mk({ value: '' });

const listeners = {};
globalThis.window = globalThis;
globalThis.addEventListener = (type, fn) => (listeners[type] ||= []).push(fn);
globalThis.document = { getElementById: (id) => els[id] || null };
globalThis.I18n = { t: (k, p) => (p && p.symbol ? `T:${k}:${p.symbol}` : p && p.max ? `T:${k}:${p.max}` : `T:${k}`) };

let items = [{ market: 'tw_stock', symbol: '2330' }];
let alerts = [
    { id: 7, symbol: '2330.TW', market: 'tw_stock', condition: 'above', target: 1100, repeat: false },
    { id: 8, symbol: 'AAPL', market: 'us_stock', condition: 'below', target: 150, repeat: false },
];
let addError = null;
const posts = [];
globalThis.AppAPI = {
    get: async (url) => (url === '/api/watchlist' ? { items, max: 20 } : { alerts }),
    post: async (url, body) => {
        posts.push([url, body]);
        if (url === '/api/watchlist/add') {
            if (addError) throw addError;
            return { item: { market: body.market, symbol: body.symbol.toUpperCase() } };
        }
        return { removed: true };
    },
};

const { loadWatchlist, addWatchlistItem, removeWatchlistItem } = await import(
    await loadModuleUrl('web/js/watchlist-settings.js')
);

// ---- 載入：列出自選、數量、這一檔的警報（代號後綴 .TW 也算同一檔） ----
await loadWatchlist();
let html = els['watchlist-items'].innerHTML;
assert.ok(html.includes('2330'));
assert.ok(html.includes('T:settings.watchlist.marketTw'));
assert.equal(els['watchlist-count'].textContent, '1/20');
assert.ok(html.includes('data-click="openAlert"'), '每一檔都要有 🔔');
assert.ok(html.includes('1100'), '這一檔的警報要列出來');
assert.ok(html.includes('data-click="deleteUserAlert"'), '警報可以刪');
assert.ok(!html.includes('150'), '別檔的警報不列在這一檔下面');

// ---- 加入：空白不送；成功會清空輸入框並列出來 ----
els['watchlist-symbol'].value = '  ';
assert.equal(await addWatchlistItem(), false);
assert.equal(posts.length, 0);
els['watchlist-market'].value = 'crypto';
els['watchlist-symbol'].value = 'btc';
assert.equal(await addWatchlistItem(), true);
assert.deepEqual(posts.at(-1), ['/api/watchlist/add', { market: 'crypto', symbol: 'btc' }]);
assert.equal(els['watchlist-symbol'].value, '');
assert.ok(els['watchlist-items'].innerHTML.includes('BTC'));
assert.equal(els['watchlist-count'].textContent, '2/20');
assert.ok(els['watchlist-status'].textContent.includes('BTC'));

// ---- 錯誤訊息依狀態碼 ----
for (const [status, key] of [
    [404, 'T:settings.watchlist.notFound'],
    [422, 'T:settings.watchlist.invalid'],
    [400, 'T:settings.watchlist.full:20'],
    [500, 'T:settings.watchlist.failed'],
]) {
    addError = Object.assign(new Error('x'), { status });
    els['watchlist-symbol'].value = 'zzz';
    assert.equal(await addWatchlistItem(), false);
    assert.equal(els['watchlist-status'].textContent, key);
    assert.ok(els['watchlist-status'].className.includes('text-danger'));
    assert.equal(els['watchlist-add'].disabled, false, '失敗後按鈕要恢復');
}
addError = null;

// ---- 警報變動（alerts.js 發 alerts:changed）要重畫 ----
alerts = [];
listeners['alerts:changed'].forEach((fn) => fn({ detail: { alerts: [] } }));
assert.ok(!els['watchlist-items'].innerHTML.includes('1100'));

// ---- 移除 ----
assert.equal(await removeWatchlistItem('tw_stock', '2330'), true);
assert.deepEqual(posts.at(-1), ['/api/watchlist/remove', { market: 'tw_stock', symbol: '2330' }]);
assert.ok(!els['watchlist-items'].innerHTML.includes('2330'));
assert.equal(els['watchlist-count'].textContent, '1/20');

// ---- 清空後顯示提示 ----
await removeWatchlistItem('crypto', 'BTC');
assert.ok(els['watchlist-items'].innerHTML.includes('T:settings.watchlist.empty'));

console.log('watchlist_settings ok');
