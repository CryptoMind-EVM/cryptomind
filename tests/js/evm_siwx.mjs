// One-Click Auth（SIWX）前端設定的行為測試。
// 重點：訊息格式必須跟後端 verify_siwe_wallet_message 的解析器對得起來，
// 且 AppKit 在 One-Click Auth 路徑會用「空的 accountAddress」來要訊息。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const moduleUrl = await loadModuleUrl('web/js/evm-siwx.js');
const { buildErc4361Message, createSiwxConfig } = await import(moduleUrl);

const ADDR = '0x1111111111111111111111111111111111111111';

// ---- 訊息格式 ----
const msg = buildErc4361Message({
    domain: 'cryptomind-ton.zeabur.app',
    accountAddress: ADDR,
    statement: 'Sign in to CryptoMind',
    uri: 'https://cryptomind-ton.zeabur.app',
    version: '1',
    chainId: 'eip155:1',
    nonce: 'abcdef0123456789',
    issuedAt: '2026-09-01T12:00:00.000Z',
});
const lines = msg.split('\n');
assert.equal(lines[0], 'cryptomind-ton.zeabur.app wants you to sign in with your Ethereum account:');
assert.equal(lines[1], ADDR);
assert.ok(msg.includes('\nURI: https://cryptomind-ton.zeabur.app'));
assert.ok(msg.includes('\nVersion: 1'));
assert.ok(msg.includes('\nChain ID: 1'), 'Chain ID 要是數字，不是 caip 全名');
assert.ok(msg.includes('\nNonce: abcdef0123456789'));
assert.ok(msg.includes('\nIssued At: 2026-09-01T12:00:00.000Z'));

// nonce 不可含點——ERC-4361 的 ABNF 不允許
assert.ok(/\nNonce: [A-Za-z0-9]{8,}\n?/.test(msg + '\n'));

// ---- createMessage：AppKit 的 One-Click Auth 會傳空地址 ----
let nonceCalls = 0;
let lastNonceAddress = 'unset';
const deps = () => ({
    origin: 'https://cryptomind-ton.zeabur.app',
    async fetchNonce(address) {
        nonceCalls += 1;
        lastNonceAddress = address;
        return { nonce_token: 'aaaabbbbccccdddd1111222233334444' };
    },
    async submitLogin(body) {
        submitted.push(body);
        return { success: true, user: { wallet_address: body.address } };
    },
    storage: new Map(),
});
let submitted = [];

const cfg = createSiwxConfig(deps());
const oneClick = await cfg.createMessage({ chainId: 'eip155:1', accountAddress: '' });
assert.equal(nonceCalls, 1);
assert.equal(lastNonceAddress, '', '空地址要照樣拿得到 nonce，不能丟例外');
assert.equal(oneClick.nonce, 'aaaabbbbccccdddd1111222233334444');
assert.equal(typeof oneClick.toString, 'function');
assert.ok(oneClick.toString().includes('Nonce: aaaabbbbccccdddd1111222233334444'));

// 有地址時照樣可用（wallet 不支援 One-Click Auth 的 fallback 路徑）
const withAddr = await cfg.createMessage({ chainId: 'eip155:1', accountAddress: ADDR });
assert.equal(withAddr.toString().split('\n')[1], ADDR);

// ---- addSession：把錢包組的訊息送去後端驗 ----
submitted = [];
const cfg2 = createSiwxConfig(deps());
const walletMessage = buildErc4361Message({
    domain: 'cryptomind-ton.zeabur.app',
    accountAddress: ADDR,
    statement: 'Sign in to CryptoMind',
    uri: 'https://cryptomind-ton.zeabur.app',
    version: '1',
    chainId: 'eip155:1',
    nonce: 'aaaabbbbccccdddd1111222233334444',
    issuedAt: '2026-09-01T12:00:00.000Z',
});
await cfg2.addSession({
    data: { accountAddress: ADDR, chainId: 'eip155:1' },
    message: walletMessage,
    signature: '0xsig',
});
assert.equal(submitted.length, 1);
assert.deepEqual(submitted[0], {
    address: ADDR,
    message: walletMessage,
    signature: '0xsig',
});

// 後端說不行就要丟例外，AppKit 才知道登入失敗
const failing = createSiwxConfig({
    ...deps(),
    async submitLogin() {
        return { success: false, detail: 'nope' };
    },
});
await assert.rejects(
    () => failing.addSession({ data: { accountAddress: ADDR }, message: walletMessage, signature: '0xs' }),
    /nope/
);

// data.accountAddress 空的時候要從訊息第二行認地址（One-Click Auth 會這樣）
submitted = [];
const cfg3 = createSiwxConfig(deps());
await cfg3.addSession({
    data: { accountAddress: '', chainId: 'eip155:1' },
    message: walletMessage,
    signature: '0xsig',
});
assert.equal(submitted[0].address, ADDR);

// ---- session 存取 ----
// getSessions 的真相是「我們後端有沒有這個位址的登入」，所以測試要一併給
// isLoggedInAs（詳見下一段的說明）。
let loggedIn = false;
const cfg4 = createSiwxConfig({
    ...deps(),
    isLoggedInAs: (addr) => loggedIn && addr.toLowerCase() === ADDR.toLowerCase(),
});
assert.deepEqual(await cfg4.getSessions('eip155:1', ADDR), []);
await cfg4.addSession({
    data: { accountAddress: ADDR, chainId: 'eip155:1' },
    message: walletMessage,
    signature: '0xsig',
});
loggedIn = true; // addSession 走完＝後端已發 session
const sessions = await cfg4.getSessions('eip155:1', ADDR);
assert.equal(sessions.length, 1);
assert.equal(sessions[0].data.accountAddress, ADDR);
// 換個地址就不算數
assert.deepEqual(
    await cfg4.getSessions('eip155:1', '0x2222222222222222222222222222222222222222'),
    []
);
await cfg4.revokeSession('eip155:1', ADDR);
assert.deepEqual(await cfg4.getSessions('eip155:1', ADDR), []);

console.log('evm siwx tests passed');

// ---- getSessions 必須反映「我們後端的登入狀態」 ----
// AppKit 的 initializeIfEnabled：getSessions 回非空就 `return`，完全不發簽名
// 請求（SIWXUtil.js:45-51）。把 localStorage 的殘影當成已登入，會讓錢包打開
// 什麼都不跳、addSession 永遠不被呼叫、connect promise 永遠等下去——手機上
// 看到的無限轉圈就是這個（2026-09-01 本機重現）。
const storedSession = {
    data: { accountAddress: ADDR, chainId: 'eip155:1' },
    message: walletMessage,
    signature: '0xsig',
};

// 有殘影、但我們其實沒登入 → 必須回空，讓 AppKit 重新要簽名
const staleOnly = createSiwxConfig({
    ...deps(),
    storage: new Map([['evmSiwxSession', JSON.stringify([storedSession])]]),
    isLoggedInAs: () => false,
});
assert.deepEqual(
    await staleOnly.getSessions('eip155:1', ADDR),
    [],
    '沒登入就不能拿舊紀錄冒充——AppKit 會因此跳過簽名'
);

// 真的登入了才算數（避免每次重整都要重簽）
const reallyLoggedIn = createSiwxConfig({
    ...deps(),
    storage: new Map([['evmSiwxSession', JSON.stringify([storedSession])]]),
    isLoggedInAs: (addr) => addr.toLowerCase() === ADDR.toLowerCase(),
});
assert.equal((await reallyLoggedIn.getSessions('eip155:1', ADDR)).length, 1);

// 登入的是別的位址也不算
assert.deepEqual(
    await reallyLoggedIn.getSessions(
        'eip155:1',
        '0x9999999999999999999999999999999999999999'
    ),
    []
);

// 沒給 isLoggedInAs 時保守處理：一律回空（寧可多簽一次，也不要卡死）
const noChecker = createSiwxConfig({
    ...deps(),
    storage: new Map([['evmSiwxSession', JSON.stringify([storedSession])]]),
});
assert.deepEqual(await noChecker.getSessions('eip155:1', ADDR), []);

console.log('evm siwx session-truth tests passed');
