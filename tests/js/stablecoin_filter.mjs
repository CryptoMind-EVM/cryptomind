// 熱門榜排除穩定幣（2026-09-27 上市準備 PR-2）：
// 市場頁在使用者沒選幣時會把熱門榜前 5 名自動釘成選擇清單，USDC-USDT 成交量第一就被釘上去；
// 使用者之後在篩選器按套用，清單連同 USDC 存進 localStorage。前端要自己濾掉，
// 讓已經存了穩定幣的瀏覽器下次載入就恢復正常（後端修好之前的快取也擋得住）。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
globalThis.document = { addEventListener() {}, getElementById: () => null };
const state = new Map();
globalThis.AppStore = { get: (k) => state.get(k), set: (k, v) => state.set(k, v) };
globalThis.I18n = { t: () => '' };
const store = new Map();
globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
};

const moduleUrl = await loadModuleUrl('web/js/filter.js');
const { SymbolSanitizer } = await import(moduleUrl);

// ---- 判斷 ----
for (const s of ['USDC-USDT', 'usdc-usdt', 'DAI-USDT', 'FDUSD-USDT', 'USDE-USDT', 'PYUSD-USDT', 'EURC-USDT', 'USDT-USDC', 'TUSD']) {
    assert.equal(SymbolSanitizer.isStablecoin(s), true, `${s} 是穩定幣`);
}
for (const s of ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'PAXG-USDT', 'USUAL-USDT']) {
    assert.equal(SymbolSanitizer.isStablecoin(s), false, `${s} 不是穩定幣`);
}
assert.deepEqual(
    SymbolSanitizer.withoutStablecoins(['USDC-USDT', 'BTC-USDT', 'DAI-USDT', 'ETH-USDT']),
    ['BTC-USDT', 'ETH-USDT']
);

// ---- 已存清單載入時清掉穩定幣並回寫 ----
store.set('marketWatchSymbols', JSON.stringify(['USDC-USDT', 'BTC-USDT', 'ETH-USDT']));
window.loadSavedSymbolSelection();
assert.deepEqual(state.get('globalSelectedSymbols'), ['BTC-USDT', 'ETH-USDT']);
assert.deepEqual(JSON.parse(store.get('marketWatchSymbols')), ['BTC-USDT', 'ETH-USDT'], '要回寫 localStorage');

// 只剩穩定幣 → 回到自動模式（空清單），不會卡在空的已存清單
store.set('marketWatchSymbols', JSON.stringify(['USDC-USDT']));
window.loadSavedSymbolSelection();
assert.deepEqual(state.get('globalSelectedSymbols'), []);

console.log('stablecoin filter tests passed');
