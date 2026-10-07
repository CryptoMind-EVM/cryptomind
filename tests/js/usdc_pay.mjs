// usdc-pay.js：USDC on Base 錢包直付共用模組（premium 訂閱、論壇發文費／打賞）。
// 2026-09-25 論壇從 TON 改 USDC 時從 premium.js 抽出來——送錢前的檢查（付款帳號必須
// 是綁定錢包、錯鏈先切鏈）與領取輪詢只有一份。這裡用假的 window.ethereum 實跑。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
globalThis.console.warn = () => {};

const m = await import(await loadModuleUrl('web/js/usdc-pay.js'));
const { BASE_BUILDER_CODE, erc8021Suffix } = await import(await loadModuleUrl('web/js/builder-code.js'));

const PAYER = '0xAbCdEf0000000000000000000000000000000001';
const OTHER = '0x0000000000000000000000000000000000000002';
const AUTHOR = '0xD00000000000000000000000000000000000000d';
const USDC = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913';
const ORDER = {
    micro: 1000321,
    token_contract: USDC,
    receiving_address: AUTHOR,
    network: 'base',
};

// ---- 1. 付款人檢查 ----
let r = m.resolvePaymentPayer([], PAYER);
assert.equal(r.ok, false);
assert.equal(r.reason, 'no_binding');
assert.equal(m.resolvePaymentPayer([PAYER], PAYER.toUpperCase().replace('0X', '0x')).ok, true);
r = m.resolvePaymentPayer([PAYER], OTHER);
assert.equal(r.reason, 'account_mismatch');
assert.equal(m.shortAddr(PAYER), '0xAbCd…0001');

// ---- 2. walletSendUsdc：calldata、切鏈、付款帳號 ----
function fakeEthereum({ account = PAYER, chain = '0x2105', chainAfterSwitch = '0x2105' } = {}) {
    const calls = [];
    let current = chain;
    return {
        calls,
        request: async ({ method, params }) => {
            calls.push({ method, params });
            if (method === 'eth_accounts') return [account];
            if (method === 'eth_chainId') return current;
            if (method === 'wallet_switchEthereumChain') {
                current = chainAfterSwitch;
                return null;
            }
            if (method === 'eth_sendTransaction') return '0x' + 'ab'.repeat(32);
            throw new Error('unexpected ' + method);
        },
    };
}

globalThis.ethereum = fakeEthereum();
assert.equal(m.hasInjectedWallet(), true);
const hash = await m.walletSendUsdc(ORDER, [PAYER.toLowerCase()]);
assert.equal(hash, '0x' + 'ab'.repeat(32));
const send = globalThis.ethereum.calls.find((c) => c.method === 'eth_sendTransaction');
const tx = send.params[0];
assert.equal(tx.to, USDC, 'transfer 打的是 USDC 合約');
assert.equal(tx.value, '0x0');
assert.equal(tx.from, PAYER);
const expected =
    '0xa9059cbb' +
    AUTHOR.toLowerCase().slice(2).padStart(64, '0') +
    ORDER.micro.toString(16).padStart(64, '0') +
    erc8021Suffix(BASE_BUILDER_CODE);
assert.equal(tx.data, expected, 'transfer(收款人, 含尾數的精確金額) + Builder Code 尾巴');

// 錢包目前帳號不是綁定地址 → 送出前就擋，不發交易
globalThis.ethereum = fakeEthereum({ account: OTHER });
await assert.rejects(() => m.walletSendUsdc(ORDER, [PAYER.toLowerCase()]));
assert.ok(!globalThis.ethereum.calls.some((c) => c.method === 'eth_sendTransaction'));

// 錯鏈 → 先切鏈；切完還是錯 → 丟錯、不送
globalThis.ethereum = fakeEthereum({ chain: '0x1' });
await m.walletSendUsdc(ORDER, [PAYER]);
assert.ok(globalThis.ethereum.calls.some((c) => c.method === 'wallet_switchEthereumChain'));
globalThis.ethereum = fakeEthereum({ chain: '0x1', chainAfterSwitch: '0x1' });
await assert.rejects(() => m.walletSendUsdc(ORDER, [PAYER]));
assert.ok(!globalThis.ethereum.calls.some((c) => c.method === 'eth_sendTransaction'));

// 訂單缺欄位或地址不是完整 0x＋40 hex → 不送（padStart 會把壞地址補成別的地址）
for (const bad of [
    { micro: undefined },
    { receiving_address: 'author.eth' },
    { receiving_address: '0x1234' },
    { token_contract: '' },
]) {
    globalThis.ethereum = fakeEthereum();
    await assert.rejects(() => m.walletSendUsdc({ ...ORDER, ...bad }, [PAYER]));
    assert.ok(!globalThis.ethereum.calls.some((c) => c.method === 'eth_sendTransaction'), JSON.stringify(bad));
}

delete globalThis.ethereum;
assert.equal(m.hasInjectedWallet(), false);

// ---- 3. 哪些領取錯誤要重試 ----
const err = (status, message) => Object.assign(new Error(message), { status });
assert.equal(m.isRetryableClaimError(err(400, 'Transaction not confirmed on Base yet — please wait')), true);
assert.equal(m.isRetryableClaimError(err(400, 'Payment seen but awaiting confirmations (3/12)')), true);
assert.equal(m.isRetryableClaimError(err(400, 'No matching payment found yet — please wait for confirmation')), true);
assert.equal(m.isRetryableClaimError(err(502, 'Unable to reach EVM network')), true);
assert.equal(m.isRetryableClaimError(err(0, 'Request timeout (30000ms)')), true);
assert.equal(m.isRetryableClaimError(new TypeError('Failed to fetch')), true);
assert.equal(m.isRetryableClaimError(err(429, 'Status 429: Too Many Requests')), true);
assert.equal(m.isRetryableClaimError(err(429, 'Daily post limit reached (3)')), false, '每日額度用完不是限流');
assert.equal(m.isRetryableClaimError(err(400, 'Payment amount mismatch — please create a new order')), false);
assert.equal(m.isRetryableClaimError(err(400, 'Payment seen but not from your bound wallet')), false);
assert.equal(m.isRetryableClaimError(err(400, 'Order has expired')), false);
assert.equal(m.isRetryableClaimError(err(409, 'Payment has already been used')), false);
assert.equal(m.isRetryableClaimError(err(402, 'Free members must complete payment before posting')), false);

// ---- 4. pollUsdcClaim ----
const noSleep = async () => {};
let n = 0;
const progress = [];
const ok = await m.pollUsdcClaim(
    async () => {
        n += 1;
        if (n < 3) throw err(400, 'Transaction not confirmed on Base yet — please wait');
        return { success: true, post_id: 9 };
    },
    { total: 5, sleep: noSleep, onProgress: (i, t) => progress.push(`${i}/${t}`) }
);
assert.deepEqual(ok, { success: true, post_id: 9 });
assert.deepEqual(progress, ['1/5', '2/5', '3/5']);

// 確定性錯誤：只打一次就丟
n = 0;
await assert.rejects(
    () =>
        m.pollUsdcClaim(
            async () => {
                n += 1;
                throw err(400, 'Payment amount mismatch — please create a new order');
            },
            { total: 5, sleep: noSleep }
        ),
    /mismatch/
);
assert.equal(n, 1);

// 取消（面板關了）：靜默中止，帶 cancelled 哨兵
await assert.rejects(
    () => m.pollUsdcClaim(async () => ({ success: true }), { isAlive: () => false, sleep: noSleep }),
    (e) => e.cancelled === true
);

// 次數用完：丟最後一個錯
n = 0;
await assert.rejects(
    () =>
        m.pollUsdcClaim(
            async () => {
                n += 1;
                throw err(400, 'No matching payment found yet — please wait for confirmation');
            },
            { total: 3, sleep: noSleep }
        ),
    /No matching payment/
);
assert.equal(n, 3);

console.log('all assertions passed');
