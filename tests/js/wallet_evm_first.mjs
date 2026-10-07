// wallet.js 錢包分頁 EVM 優先（2026-09-12）：綁定錢包卡（EVM 在前、主要徽章、鏈上餘額）、
// 沒綁定引導、鏈上同步卡的結果摘要與 429／400 文案。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

// 最小 DOM：只記 innerHTML／textContent／checked／disabled 的元素表
const els = {};
function el(id) {
    if (!els[id]) els[id] = { id, innerHTML: '', textContent: '', checked: false, disabled: false, classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } } };
    return els[id];
}
globalThis.window = globalThis;
globalThis.document = {
    addEventListener() {},
    removeEventListener() {},
    getElementById(id) { return el(id); },
    querySelectorAll() { return { length: 0, forEach() {} }; },
    createElement() { return { classList: { add() {}, remove() {} }, style: {} }; },
};
globalThis.console.log = () => {};
globalThis.console.warn = () => {};
globalThis.AppUtils = { refreshIcons() {} };

const mod = await import(await loadModuleUrl(new URL('../../web/js/wallet.js', import.meta.url).pathname));
const { WalletApp, formatUsd, formatAssetAmount, chainLabel, sortWallets, summarizeSyncResult } = mod;

// 1) 純函式
assert.equal(formatUsd(1234.5), '$1,234.50');
assert.equal(formatUsd(null), '—');
assert.equal(formatAssetAmount(0), '0');
assert.equal(formatAssetAmount(48125.777), '48,125.78');
assert.equal(formatAssetAmount(0.5), '0.5');
assert.equal(formatAssetAmount(0.000123), '0.000123');
assert.equal(chainLabel('base'), 'Base');
assert.equal(chainLabel('ton'), 'TON');
assert.equal(chainLabel('zksync'), 'ZKSYNC');
assert.deepEqual(
    sortWallets([{ chain: 'ton', address: 'EQ1' }, { chain: 'evm', address: '0xb', is_primary: false }, { chain: 'evm', address: '0xa', is_primary: true }]).map((w) => w.address),
    ['0xa', '0xb', 'EQ1'],
    'EVM 在前、主要錢包先、TON 最後'
);
const t = (k, f) => f;
assert.equal(summarizeSyncResult({ added: 2, duplicates: 4 }, t), 'Added 2 entries · 4 already recorded');
assert.equal(
    summarizeSyncResult({ added: 0, skipped_unpriced: ['JUNK', 'SPAM'], errors: 1, notes: ['rpc_logs_only:native_transfers_not_included'] }, t),
    'Added 0 entries · skipped (no price): JUNK, SPAM · 1 wallets failed · ERC-20 only on this network (native ETH transfers need an Etherscan key)'
);
assert.equal(summarizeSyncResult(null, t), '');

// 2) 綁定錢包卡：EVM 先、主要徽章、餘額與 USD、TON 未驗證標記、複製鈕帶地址
WalletApp.renderWallets({
    usd_total: 1120,
    wallets: [
        { chain: 'ton', network: 'ton', address: 'EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N', short: 'EQCD39…qB2N', is_primary: false, ok: true, usd_total: 20, assets: [{ symbol: 'TON', amount: 10, usd: 20 }, { symbol: 'SPAM', amount: 9e9, usd: null, verified: false }] },
        { chain: 'evm', network: 'base', address: '0x3304e22ddaa22bcdc5fca2269b418046ae7b566a', short: '0x3304…566a', is_primary: true, ok: true, usd_total: 1100, assets: [{ symbol: 'ETH', amount: 0.5, usd: 1000 }, { symbol: 'USDC', amount: 100, usd: 100 }] },
        { chain: 'evm', network: 'base', address: '0xdead', short: '0xdead', is_primary: false, ok: false, assets: [], usd_total: 0 },
    ],
});
const html = el('wallet-bound-list').innerHTML;
assert.ok(html.indexOf('0x3304…566a') < html.indexOf('EQCD39…qB2N'), 'EVM 錢包要排在 TON 前面');
assert.ok(html.includes('>Base<') && html.includes('>TON<'), '鏈名徽章');
assert.ok(html.includes('primary'), '主要錢包徽章');
assert.ok(html.includes('$1,000.00') && html.includes('$1,100.00'), 'USD 估值');
assert.ok(html.includes('unverified'), 'TON 未驗證 jetton 要標');
assert.ok(html.includes('balances unavailable'), '來源失敗的錢包要說查不到，不能假裝 0');
assert.ok(html.includes('data-click="walletCopyAddress" data-click-arg="0x3304e22ddaa22bcdc5fca2269b418046ae7b566a"'));
assert.equal(el('wallet-holdings-total').textContent, '$1,120.00');
assert.ok(!html.includes('<script'), '不得有 script');

// 3) 沒綁定：引導去 Settings；總資產顯示 —
WalletApp.renderWallets({ usd_total: 0, wallets: [] });
assert.ok(el('wallet-bound-list').innerHTML.includes('data-click="GlobalNav.navigateToTab" data-click-arg="settings"'));
assert.equal(el('wallet-holdings-total').textContent, '—');

// 4) 同步狀態：開關、上次同步、結果
WalletApp.renderSyncStatus({ enabled: false, last_synced_at: '2026-09-12T10:00:00+00:00', last_result: { added: 3 } });
assert.equal(el('wallet-sync-enabled').checked, false);
assert.ok(el('wallet-sync-last').textContent.startsWith('Last sync'));
assert.equal(el('wallet-sync-result').textContent, 'Added 3 entries');
WalletApp.renderSyncStatus({ enabled: true, last_synced_at: null });
assert.equal(el('wallet-sync-last').textContent, 'Not synced yet');

// 5) 立即同步：429 → 等一分鐘；400 → 沒綁定文案；成功 → 結果摘要＋帳本刷新
let calls = [];
globalThis.AppAPI = {
    async post(url) { calls.push(url); const err = new Error('x'); err.status = 429; throw err; },
    async put() { return { enabled: true }; },
    async get() { return {}; },
};
await WalletApp.syncNow();
assert.equal(el('wallet-sync-result').textContent, 'Please wait a minute before syncing again');
assert.equal(el('wallet-sync-now').disabled, false, '結束要解鎖按鈕');
globalThis.AppAPI.post = async () => { const err = new Error('x'); err.status = 400; throw err; };
await WalletApp.syncNow();
assert.ok(el('wallet-sync-result').textContent.startsWith('No wallet bound'));
let refreshed = false;
globalThis.JournalTab = { async refresh() { refreshed = true; } };
globalThis.AppAPI.post = async () => ({ enabled: true, last_synced_at: '2026-09-12T10:00:00+00:00', result: { added: 1, duplicates: 2 } });
await WalletApp.syncNow();
assert.equal(el('wallet-sync-result').textContent, 'Added 1 entries · 2 already recorded');
assert.equal(refreshed, true, '同步成功要刷新帳本');

// 6) 開關失敗要把 checkbox 彈回去
const box = el('wallet-sync-enabled');
box.checked = true;
globalThis.AppAPI.put = async () => { throw new Error('down'); };
await WalletApp.toggleSync(box);
assert.equal(box.checked, false);

console.error('wallet_evm_first: ok');
