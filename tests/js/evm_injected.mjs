// evm-injected — 注入錢包偵測（EIP-6963 + 傳統 window.ethereum）純函式測試。
// 背景：偵測只看 window.ethereum 時，只走 EIP-6963 廣播的錢包（Bitget／
// 幣安 Web3 等）在自家內建瀏覽器裡會被判成「沒有錢包」，引導面板不出現
// 「直接連接」列，使用者只剩跳去別家錢包的清單 → 鬼打牆連不上
// （2026-09-02 回報）。這裡釘死廣播收集與挑選優先序。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const moduleUrl = await loadModuleUrl('web/js/evm-injected.js');
const {
    startInjectedDiscovery,
    discoverInjectedProviders,
    listInjectedProviders,
    pickInjectedProvider,
    injectedWalletName,
    _resetInjectedDiscoveryForTests,
} = await import(moduleUrl);

// 最小 window 替身：只實作 addEventListener / dispatchEvent / setTimeout。
function makeWin(wallets) {
    const listeners = {};
    const win = {
        addEventListener(type, fn) {
            (listeners[type] = listeners[type] || []).push(fn);
        },
        dispatchEvent(evt) {
            (listeners[evt.type] || []).forEach((fn) => fn(evt));
            return true;
        },
        setTimeout: (fn, ms) => setTimeout(fn, ms),
        Event: class {
            constructor(type) {
                this.type = type;
            }
        },
    };
    // 錢包端：收到 requestProvider 就廣播自己（EIP-6963 規範行為）
    win.addEventListener('eip6963:requestProvider', () => {
        wallets.forEach((w) => {
            win.dispatchEvent({ type: 'eip6963:announceProvider', detail: w });
        });
    });
    return win;
}

const trust = {
    info: { uuid: 'u-1', rdns: 'com.trustwallet.app', name: 'Trust Wallet' },
    provider: { request: async () => ['0xaaa'] },
};
const bitget = {
    info: { uuid: 'u-2', rdns: 'com.bitget.web3', name: 'Bitget Wallet' },
    provider: { request: async () => ['0xbbb'] },
};

// --- 廣播收集 ---
_resetInjectedDiscoveryForTests();
const win = makeWin([trust, bitget]);
startInjectedDiscovery(win);
assert.equal(listInjectedProviders().length, 2, '兩顆錢包的廣播都要收到');

// 重複廣播同一顆（rdns/uuid 相同）不能變成兩筆
win.dispatchEvent({ type: 'eip6963:announceProvider', detail: trust });
assert.equal(listInjectedProviders().length, 2, '同一顆錢包重複廣播不重複計數');

// discoverInjectedProviders 會再要求一次廣播，等待窗過後回傳清單
const found = await discoverInjectedProviders({ win, waitMs: 5 });
assert.equal(found.length, 2, 'discover 要回傳已廣播的錢包');

// --- 挑選優先序 ---
// 1. EIP-6963 廣播優先於 window.ethereum
const legacyEthereum = { request: async () => ['0xccc'] };
let picked = pickInjectedProvider(found, legacyEthereum);
assert.equal(picked.source, 'eip6963', '有 6963 廣播時不該退回 window.ethereum');
assert.equal(picked.provider, trust.provider);
assert.equal(injectedWalletName(picked), 'Trust Wallet');

// 2. 沒有廣播時退回 window.ethereum.providers[]（舊版多錢包共存陣列）
picked = pickInjectedProvider([], { providers: [{}, legacyEthereum] });
assert.equal(picked.source, 'legacy-multi', '無廣播時要挑 providers[] 裡能發請求的那顆');
assert.equal(picked.provider, legacyEthereum);

// 3. 再退回 window.ethereum 本身
picked = pickInjectedProvider([], legacyEthereum);
assert.equal(picked.source, 'legacy');
assert.equal(injectedWalletName(picked, 'Wallet'), 'Wallet', '傳統注入沒有名稱，用 fallback');

// 3b. Base App／Coinbase Wallet 自家瀏覽器（provider 帶 isCoinbaseWallet）：不論 6963 廣播
//     或 legacy，都要被認成 wallet-browser 直接連（2026-09-13 Base App 實測要跳兩次才登得進）
const cbw = { request: async () => ['0xcb'], isCoinbaseWallet: true };
picked = pickInjectedProvider(found, cbw);
assert.equal(picked.source, 'wallet-browser', '錢包自家瀏覽器優先於一般 6963 廣播');
assert.equal(picked.provider, cbw);
assert.equal(injectedWalletName(picked), 'Base App');
picked = pickInjectedProvider([], { providers: [legacyEthereum, cbw] });
assert.equal(picked.source, 'wallet-browser', 'providers[] 裡有 Coinbase 也要挑到');
picked = pickInjectedProvider([{ provider: cbw, info: { name: 'Coinbase Wallet' } }], undefined);
assert.equal(picked.source, 'wallet-browser');

// 4. 都沒有 → null（引導面板據此不顯示「直接連接」列）
assert.equal(pickInjectedProvider([], undefined), null);

// 5. 殘缺 stub（沒有 request()）不算錢包——當成錢包會讓 eth_requestAccounts 直接爆
assert.equal(
    pickInjectedProvider([{ info: { uuid: 'x', name: 'Stub' }, provider: {} }], undefined),
    null,
    '沒有 request() 的注入物件不能當錢包'
);

// --- 不會爆的邊界 ---
_resetInjectedDiscoveryForTests();
assert.equal(startInjectedDiscovery(null), false, '沒有 window 時安靜返回 false');
assert.deepEqual(pickInjectedProvider(null, null), null, '清單為 null 也不能丟例外');

console.log('evm_injected.mjs: all assertions passed');
