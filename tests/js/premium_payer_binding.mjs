// premium.js 付款人檢查純函式（2026-09-11 盤查）：後端只認已綁定地址送出的
// USDC，前端要在建單前／錢包送款前就判，別讓錢先轉出去。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

// premium.js 載入時會 new PremiumManager() → document.addEventListener——先備好最小 stub
globalThis.window = globalThis;
globalThis.document = {
    addEventListener() {},
    removeEventListener() {},
    querySelectorAll() { return { length: 0, forEach() {} }; },
    getElementById() { return null; },
    createElement() { return { style: {}, classList: { add() {}, toggle() {} } }; },
    body: { appendChild() {} },
};

const moduleUrl = await loadModuleUrl('web/js/premium.js');
const { resolvePaymentPayer } = await import(moduleUrl);

const BOUND = ['0xAbCdEf0000000000000000000000000000000001'];

// 沒綁定 → 不能付（要先引導綁定）
let r = resolvePaymentPayer([], '0xabcdef0000000000000000000000000000000001');
assert.equal(r.ok, false); assert.equal(r.reason, 'no_binding');
r = resolvePaymentPayer(null, null);
assert.equal(r.ok, false, 'null 清單視同沒綁定');

// 有綁定、手動轉帳（還沒有帳號可比）→ 放行
r = resolvePaymentPayer(BOUND, null);
assert.equal(r.ok, true);

// 錢包目前帳號＝綁定地址（大小寫不同）→ 放行
r = resolvePaymentPayer(BOUND, '0xABCDEF0000000000000000000000000000000001');
assert.equal(r.ok, true, '地址比對必須不分大小寫');

// 錢包切到別的帳號 → 擋，並回傳綁定清單供文案顯示
r = resolvePaymentPayer(BOUND, '0x0000000000000000000000000000000000000002');
assert.equal(r.ok, false); assert.equal(r.reason, 'account_mismatch');
assert.deepEqual(r.bound, [BOUND[0].toLowerCase()]);

console.log('all assertions passed');
