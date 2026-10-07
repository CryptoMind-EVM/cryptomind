// wallet.js 付款紀錄多資產顯示（2026-09-12 DANNY 截圖：EVM 使用者的紀錄全印成
// 「-1 TON」、admin 授予被當成付款、明細用瀏覽器 alert 連換行都沒處理）。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
globalThis.document = {
    addEventListener() {},
    removeEventListener() {},
    getElementById() { return null; },
    querySelectorAll() { return { length: 0, forEach() {} }; },
    createElement() { return { classList: { add() {}, remove() {} }, style: {} }; },
};
globalThis.console.log = () => {};

const mod = await import(await loadModuleUrl(new URL('../../web/js/wallet.js', import.meta.url).pathname));
const { WalletApp, formatTxAmount, summarizeTotals } = mod;

// 1) 金額照資產別印，不再寫死 TON
assert.equal(formatTxAmount({ type: 'membership_payment', asset: 'USDC', amount: -12 }), '12.00 USDC');
assert.equal(formatTxAmount({ type: 'tip_received', asset: 'TON', amount: 0.5 }), '+0.50 TON');
assert.equal(formatTxAmount({ type: 'admin_grant', asset: null, amount: 0 }), '—', 'admin 授予不印金額');
assert.equal(formatTxAmount({ type: 'post_payment', asset: undefined, amount: -0.1 }), '—', '沒有資產別就不亂印單位');

// 2) 總計依資產分開加總，授予不計
const totals = summarizeTotals([
    { type: 'membership_payment', asset: 'USDC', amount: -12 },
    { type: 'post_payment', asset: 'TON', amount: -0.1 },
    { type: 'tip_sent', asset: 'TON', amount: -0.2 },
    { type: 'tip_received', asset: 'TON', amount: 0.5 },
    { type: 'admin_grant', asset: null, amount: 0 },
]);
assert.equal(totals.out, '12.00 USDC · 0.30 TON');
assert.equal(totals.in, '0.50 TON');
assert.deepEqual(summarizeTotals([]), { out: '0', in: '0' });

// 3) 明細走平台 ContentModal，內容有真正的換行、授予不顯示 TON
let opened = null;
globalThis.ContentModal = { open(opts) { opened = opts; } };
let alerted = false;
globalThis.alert = () => { alerted = true; };
WalletApp.showDetail({
    type: 'admin_grant',
    asset: null,
    amount: 0,
    tx_hash: 'admin_grant_evm_0xabc_evm_0xdef_1789027826',
    created_at: '2026-09-10 08:10:00',
});
assert.ok(opened, 'ContentModal.open 必須被呼叫');
assert.equal(alerted, false, '有 ContentModal 就不准退回 alert');
assert.equal(opened.editable, false);
assert.ok(opened.body.includes('\n'), '明細各欄位要換行');
assert.ok(!opened.body.includes('TON'), 'admin 授予的明細不能出現 TON 金額');
assert.ok(opened.body.includes('admin_grant_evm_0xabc'), '參考編號要保留');

opened = null;
WalletApp.showDetail({ type: 'membership_payment', asset: 'USDC', chain: 'base', amount: -12, tx_hash: '0x' + 'a'.repeat(64), created_at: '2026-09-10 08:10:00' });
assert.ok(opened.body.includes('12.00 USDC'));
assert.ok(opened.body.includes('BASE'), '有鏈就標網路');

// 4) 付款／打賞列的資產別（2026-09-25 論壇改 USDC）：後端有帶就用；舊回應看 tx hash
//    形狀——0x＋64 hex＝USDC on Base，其他＝舊的 TON
const { paymentAsset } = mod;
assert.equal(paymentAsset({ asset: 'USDC', tx_hash: 'x' }), 'USDC');
assert.equal(paymentAsset({ tx_hash: '0x' + 'f'.repeat(64) }), 'USDC');
assert.equal(paymentAsset({ tx_hash: 'te6cckEBAQEA' }), 'TON');
assert.equal(paymentAsset({}), 'TON');

console.error('wallet_history_assets: ok');
