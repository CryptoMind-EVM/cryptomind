// web/js/watchlist-sync.js：各市場分頁的自選 ↔ 伺服器。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
globalThis.showToast = () => {};
globalThis.I18n = { t: (k) => k };
let loggedIn = false;
globalThis.AuthManager = {
    isLoggedIn: () => loggedIn,
    get currentUser() {
        return { user_id: 'evm_1' };
    },
};
let serverItems = [];
const calls = [];
globalThis.AppAPI = {
    get: async (url) => {
        calls.push(['GET', url]);
        return { items: serverItems };
    },
    put: async (url, body) => {
        calls.push(['PUT', url, body]);
        const market = decodeURIComponent(url.split('/').pop());
        const symbols = body.symbols.map((s) => (market === 'crypto' ? s.replace(/-USDT$/, '') : s));
        return { symbols, truncated: false };
    },
};
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const { hydrate, push, sameList } = await import(await loadModuleUrl('web/js/watchlist-sync.js'));

// ---- sameList：不分順序；加密貨幣交易對與幣種視為同一個 ----
assert.equal(sameList(['A', 'B'], ['B', 'A'], 'us_stock'), true);
assert.equal(sameList(['A'], ['A', 'B'], 'us_stock'), false);
assert.equal(sameList(['BTC-USDT'], ['BTC'], 'crypto'), true);

// ---- 沒登入：什麼都不做 ----
assert.equal(await hydrate('jp_stock', ['7203.T'], ['7203.T']), null);
push('jp_stock', ['6758.T'], ['7203.T']);
await wait(700);
assert.equal(calls.length, 0, '沒登入不打 API');

loggedIn = true;

// ---- 伺服器有這個市場 → 用伺服器的（加密貨幣轉成交易對） ----
serverItems = [
    { market: 'hk_stock', symbol: '0700.HK' },
    { market: 'crypto', symbol: 'BTC' },
];
assert.deepEqual(await hydrate('hk_stock', ['9988.HK'], ['9988.HK']), ['0700.HK']);
assert.deepEqual(await hydrate('crypto', [], []), ['BTC-USDT']);
assert.equal(calls.filter((c) => c[0] === 'GET').length, 1, '5 秒內同一頁只抓一次');

// ---- 伺服器沒有、本機還是預設 → 不上傳（預設不算使用者選的） ----
assert.equal(await hydrate('jp_stock', ['7203.T', '6758.T'], ['6758.T', '7203.T']), null);
assert.equal(calls.filter((c) => c[0] === 'PUT').length, 0);

// ---- 伺服器沒有、本機改過 → 搬上去一次 ----
assert.equal(await hydrate('kr_stock', ['005930.KS', '000660.KS'], ['005930.KS']), null);
assert.deepEqual(calls.at(-1), ['PUT', '/api/watchlist/kr_stock', { symbols: ['005930.KS', '000660.KS'] }]);

// ---- push：沒動過（伺服器沒有、清單＝預設）不存 ----
const before = calls.length;
push('forex', ['EURUSD=X', 'USDJPY=X'], ['USDJPY=X', 'EURUSD=X']);
await wait(700);
assert.equal(calls.length, before, '預設清單不上傳');

// ---- push：改過 → 600ms 內合併成一次 PUT ----
push('forex', ['EURUSD=X'], ['USDJPY=X', 'EURUSD=X']);
push('forex', ['EURUSD=X', 'TWD=X'], ['USDJPY=X', 'EURUSD=X']);
await wait(700);
const puts = calls.filter((c) => c[0] === 'PUT' && c[1] === '/api/watchlist/forex');
assert.equal(puts.length, 1, '連續改動合併成一次');
assert.deepEqual(puts[0][2], { symbols: ['EURUSD=X', 'TWD=X'] });

// ---- 伺服器已經有這個市場：就算改回預設也要存（使用者明確的選擇） ----
push('forex', ['USDJPY=X', 'EURUSD=X'], ['USDJPY=X', 'EURUSD=X']);
await wait(700);
assert.deepEqual(calls.at(-1), ['PUT', '/api/watchlist/forex', { symbols: ['USDJPY=X', 'EURUSD=X'] }]);

// ---- 加密貨幣清空（回 Auto 模式）：伺服器有就清掉 ----
push('crypto', [], []);
await wait(700);
assert.deepEqual(calls.at(-1), ['PUT', '/api/watchlist/crypto', { symbols: [] }]);

console.log('watchlist_sync ok');
