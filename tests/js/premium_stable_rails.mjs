// Premium 付款穩定幣優先測試（2026-09-02 DANNY：付款不要以 TON 為主，
// 走跨 EVM 穩定幣）。釘死三件事：
//   1. rail 選擇器順序：evm_usdc 最前（TON 兩軌 2026-09-25 移除，當未知 rail 排後面）
//   2. 方案卡顯示 USD 錨定價（$12.00），不再是 TON 浮動計價
//   3. pricing 未載入前顯示 loading，不出錯
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

// premium.js 載入時會 new PremiumManager() → document.addEventListener，
// 底層也會掛 window.*——先備好最小 stub。
const captured = { priceHtml: [] };
globalThis.window = globalThis;
globalThis.document = {
    addEventListener() {},
    removeEventListener() {},
    querySelectorAll() {
        const els = [];
        return {
            length: els.length,
            forEach(cb) {
                els.forEach(cb);
            },
        };
    },
    getElementById() {
        return null;
    },
    createElement() {
        return { style: {}, classList: { add() {}, toggle() {} } };
    },
    body: { appendChild() {} },
};

const moduleUrl = await loadModuleUrl('web/js/premium.js');
const { PremiumManager, sortRailsStableFirst } = await import(moduleUrl);

// ---- 1. rail 排序：穩定幣優先、EVM 先行、TON 動態匯率殿後 ----
const shuffled = sortRailsStableFirst([
    { rail: 'ton_native', amounts: {} },
    { rail: 'evm_usdc', amounts: {} },
]);
assert.deepEqual(
    shuffled.map((r) => r.rail),
    ['evm_usdc', 'ton_native'],
    'rails 順序必須是 evm_usdc → ton_native'
);
// TON 兩軌 2026-09-25 移除：ton_usdt、ton_native 都不是已知 rail——一樣排最後、彼此維持原順序
const withRemoved = sortRailsStableFirst([{ rail: 'ton_usdt' }, { rail: 'ton_native' }, { rail: 'evm_usdc' }]);
assert.deepEqual(
    withRemoved.map((r) => r.rail),
    ['evm_usdc', 'ton_usdt', 'ton_native'],
    'TON 兩軌已移除，要當未知 rail 排在 evm_usdc 後面'
);
// 未知 rail 排最後，不噴例外
const withUnknown = sortRailsStableFirst([{ rail: 'future_rail' }, { rail: 'evm_usdc' }]);
assert.equal(withUnknown[0].rail, 'evm_usdc', '已知 rail 要排在未知 rail 前');

// ---- 2. 方案卡顯示 USD 錨定價 ----
const manager = new PremiumManager();
manager._usdPricing = { monthly: 12, yearly: 108 };
assert.equal(manager._priceForPlan('premium_monthly'), 12, '月費 USD 錨定價');
assert.equal(manager._priceForPlan('premium_yearly'), 108, '年費 USD 錨定價');
assert.equal(
    manager._priceForPlan('premium_monthly'),
    12,
    '不是 TON 浮動價（舊版會隨匯率變）'
);

// updatePriceDisplay 渲染 $ 金額
let lastHtml = null;
manager.selectedPlan = 'premium_monthly';
document.querySelectorAll = () => ({
    length: 1,
    forEach(cb) {
        // lazy 模組 readyState guard 會在建構時立即 init（initUpgradeButtons
        // / initPlanToggle 綁事件）——假元素要有 addEventListener
        cb({
            addEventListener() {},
            set innerHTML(v) { lastHtml = v; },
            get innerHTML() { return lastHtml; },
        });
    },
});
manager.updatePriceDisplay();
assert.equal(lastHtml, '$12.00', `方案卡要顯示 USD 金額，實際：${lastHtml}`);

manager.selectedPlan = 'premium_yearly';
manager.updatePriceDisplay();
assert.equal(lastHtml, '$108.00', `年費要顯示 USD 金額，實際：${lastHtml}`);

// ---- 3. pricing 未載入：顯示 loading、不出錯 ----
const bare = new PremiumManager();
assert.equal(bare._priceForPlan('premium_monthly'), null, 'pricing 未載入要回 null');
bare.updatePriceDisplay();
assert.ok(typeof lastHtml === 'string' && lastHtml.includes('animate-pulse'), '未載入時要顯示 loading');

// ---- 4. 2026-09-09 訂閱統一：單一 rail 直通、無可用不再 fallback TON ----
const unified = new PremiumManager();
unified._usdPricing = { monthly: 12, yearly: 108 }; // 跳過 _ensurePricing 的 AppAPI
unified._rails = [{ rail: 'evm_usdc', available: true, amounts: {} }];
assert.equal(
    await unified.chooseRail(),
    'evm_usdc',
    '唯一可用 rail 要直通回傳，不跳選擇器'
);
unified._rails = [{ rail: 'evm_usdc', available: false, amounts: {} }];
assert.equal(
    await unified.chooseRail(),
    null,
    '無可用 rail 要回 null（不得 fallback ton_native）'
);
// 死碼釘子：TON Connect 升級流程已移除，不得復活（比對定義語法，
// 允許說明用的歷史名稱出現在註解裡）
const src = await (await import('node:fs/promises')).readFile(
    new URL('../../web/js/premium.js', import.meta.url),
    'utf-8'
);
assert.ok(!/async startTonUpgrade\(/.test(src), 'TON Connect 升級流程不得殘留');
assert.ok(!/async executeTonPayment\(/.test(src), 'TON Connect 送單不得殘留');
assert.ok(!/async showUpgradeConfirmation\(/.test(src), 'TON 確認對話框不得殘留');
assert.ok(!src.includes('tonConnectUI'), 'premium.js 不得再耦合 TON Connect');
assert.ok(!src.includes('ton-order'), '不得再呼叫已退場的 ton-order');

console.log('premium stable rails tests passed');
