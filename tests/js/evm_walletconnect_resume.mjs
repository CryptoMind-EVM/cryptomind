import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const moduleUrl = await loadModuleUrl('web/js/evm-walletconnect.js');
const { readEvmConnection } = await import(moduleUrl);

assert.equal(typeof readEvmConnection, 'function');

const provider = { request() {} };
const namespaceCalls = [];
const appKit = {
    getAccount(namespace) {
        namespaceCalls.push(['account', namespace]);
        return {
            address: '0x1111111111111111111111111111111111111111',
            caipAddress: 'eip155:1:0x1111111111111111111111111111111111111111',
            isConnected: true,
        };
    },
    getCaipAddress(namespace) {
        namespaceCalls.push(['caip', namespace]);
        return undefined;
    },
    getProvider(namespace) {
        namespaceCalls.push(['provider', namespace]);
        return namespace === 'eip155' ? provider : undefined;
    },
    // The bug: activeChain-dependent access remains empty during WebView restore.
    getWalletProvider() {
        return null;
    },
};

assert.deepEqual(readEvmConnection(appKit), {
    address: '0x1111111111111111111111111111111111111111',
    provider,
});
assert.deepEqual(namespaceCalls, [
    ['account', 'eip155'],
    ['caip', 'eip155'],
    ['provider', 'eip155'],
]);

const eventProvider = { request() {} };
assert.deepEqual(
    readEvmConnection(
        {
            getAccount: () => ({
                caipAddress: 'eip155:8453:0x2222222222222222222222222222222222222222',
            }),
            getCaipAddress: () => undefined,
            getProvider: () => undefined,
            getWalletProvider: () => null,
        },
        { eip155: eventProvider }
    ),
    {
        address: '0x2222222222222222222222222222222222222222',
        provider: eventProvider,
    }
);

assert.equal(
    readEvmConnection({
        getAccount: () => ({ address: '0x3333333333333333333333333333333333333333' }),
        getCaipAddress: () => undefined,
        getProvider: () => undefined,
        getWalletProvider: () => null,
    }),
    null
);

const legacyProvider = { request() {} };
assert.deepEqual(
    readEvmConnection({
        getAccount: () => ({ address: '0x4444444444444444444444444444444444444444' }),
        getCaipAddress: () => undefined,
        getWalletProvider: () => legacyProvider,
    }),
    {
        address: '0x4444444444444444444444444444444444444444',
        provider: legacyProvider,
    }
);

console.log('evm walletconnect resume tests passed');

// ---- WalletConnect session as ground truth ----
// 手機實測（2026-09-01 13:41）：錢包批准後切回瀏覽器，AppKit 彈窗仍停在
// 「Continue in Trust Wallet」轉圈——代表 AppKit 的 controller state 沒更新。
// 這時 UniversalProvider.session 才是唯一可信的來源。
const { readWcSession, ensureRelayConnected } = await import(moduleUrl);

const universalProvider = {
    request() {},
    session: {
        namespaces: {
            eip155: {
                accounts: ['eip155:1:0x3333333333333333333333333333333333333333'],
            },
        },
    },
};
const deadAppKit = {
    // AppKit 全空——正是手機卡死時的狀態
    getAccount: () => undefined,
    getCaipAddress: () => undefined,
    getProvider: () => undefined,
    getWalletProvider: () => null,
    getUniversalProvider: async () => universalProvider,
};
assert.equal(readEvmConnection(deadAppKit), null);
assert.deepEqual(await readWcSession(deadAppKit), {
    address: '0x3333333333333333333333333333333333333333',
    provider: universalProvider,
});

// 沒有 session 就是沒連上，不能瞎猜
assert.equal(
    await readWcSession({ getUniversalProvider: async () => ({ request() {} }) }),
    null
);
assert.equal(await readWcSession({}), null);

// ---- relay socket 復原 ----
// Android Chrome 會凍結背景分頁，WS 斷掉後 session settle 永遠送不到。
let restarts = 0;
const makeAppKit = (connected) => ({
    getUniversalProvider: async () => ({
        client: {
            core: {
                relayer: {
                    connected,
                    async restartTransport() {
                        restarts += 1;
                    },
                },
            },
        },
    }),
});
await ensureRelayConnected(makeAppKit(false));
assert.equal(restarts, 1, 'relay 斷線時要重開 transport');
await ensureRelayConnected(makeAppKit(true));
assert.equal(restarts, 1, 'relay 還連著就不要多此一舉');
await ensureRelayConnected({}); // 不可拋

// 過期的 session 不能當成可用連線——撿回來只會讓 personal_sign 空等 150 秒
const expiredAppKit = {
    getUniversalProvider: async () => ({
        request() {},
        session: {
            expiry: Math.floor(Date.now() / 1000) - 60,
            namespaces: {
                eip155: { accounts: ['eip155:1:0x4444444444444444444444444444444444444444'] },
            },
        },
    }),
};
assert.equal(await readWcSession(expiredAppKit), null);

const liveAppKit = {
    getUniversalProvider: async () => ({
        request() {},
        session: {
            expiry: Math.floor(Date.now() / 1000) + 3600,
            namespaces: {
                eip155: { accounts: ['eip155:1:0x5555555555555555555555555555555555555555'] },
            },
        },
    }),
};
assert.equal(
    (await readWcSession(liveAppKit)).address,
    '0x5555555555555555555555555555555555555555'
);

console.log('evm walletconnect session/relay tests passed');

// ---- 續登要讓得出路 ----
// 使用者在背景續登輪詢（最長 25s）期間再按一次「連接錢包」時，續登必須立刻
// 收手；不然兩條流程各跑一次 nonce＋personal_sign，錢包會連跳兩次簽名。
const { pollForConnection } = await import(moduleUrl);
const neverConnects = {
    getAccount: () => undefined,
    getCaipAddress: () => undefined,
    getProvider: () => undefined,
    getWalletProvider: () => null,
    getUniversalProvider: async () => null,
};

let polls = 0;
const started = Date.now();
const aborted = await pollForConnection(neverConnects, {
    timeoutMs: 25000,
    intervalMs: 50,
    shouldAbort: () => {
        polls += 1;
        return polls > 2;
    },
});
assert.equal(aborted, null);
assert.ok(Date.now() - started < 2000, '被中止時要立刻回傳，不能空轉 25 秒');

// 沒被中止時照樣走到逾時才放棄
const timedOut = await pollForConnection(neverConnects, {
    timeoutMs: 120,
    intervalMs: 20,
});
assert.equal(timedOut, null);

// 有 session 就回傳，不必等到逾時
const found = await pollForConnection(liveAppKit, { timeoutMs: 25000, intervalMs: 20 });
assert.equal(found.address, '0x5555555555555555555555555555555555555555');

console.log('evm walletconnect abort tests passed');

// ---- 回前景要「無條件」重開 relay ----
// Android 凍結分頁後 WebSocket 常是殭屍：relayer.connected 還報 true，
// 訊息卻永遠收不到。只在 connected===false 才重連，等於什麼都沒做。
let forced = 0;
const zombieRelayAppKit = {
    getUniversalProvider: async () => ({
        client: {
            core: {
                relayer: {
                    connected: true, // 殭屍：旗標是 true，實際已死
                    async restartTransport() {
                        forced += 1;
                    },
                },
            },
        },
    }),
};
await ensureRelayConnected(zombieRelayAppKit);
assert.equal(forced, 0, '平常不要亂踢還活著的連線');
await ensureRelayConnected(zombieRelayAppKit, { force: true });
assert.equal(forced, 1, 'force 時即使 connected=true 也要重開');

console.log('evm walletconnect relay force tests passed');

// ---- 錢包要知道往哪裡跳回來 ----
// 使用者回報「驗證指紋後沒跳轉到網頁，我自己回去」。WalletConnect 錢包是靠
// dapp metadata 的 redirect 決定要把使用者送回哪裡；沒填就只能自己切回來。
const { buildAppKitConfig } = await import(moduleUrl);
const siwxStub = { createMessage() {}, addSession() {} };
const cfg = buildAppKitConfig(
    'https://cryptomind-ton.zeabur.app',
    'af1d',
    [{ id: 1 }],
    siwxStub
);
// siwx 沒傳進去的話 AppKit 就退回舊的兩趟往返，One-Click Auth 等於沒開
assert.equal(cfg.siwx, siwxStub, 'siwx 必須傳給 createAppKit');
assert.equal(cfg.metadata.url, 'https://cryptomind-ton.zeabur.app');
assert.equal(
    cfg.metadata.redirect.universal,
    'https://cryptomind-ton.zeabur.app',
    'metadata.redirect.universal 要指回 dapp，錢包才知道往哪跳回來'
);
assert.equal(cfg.projectId, 'af1d');
assert.ok(cfg.networks.length >= 1);

console.log('evm walletconnect redirect tests passed');

// ---- 過期 session 不得冒充登入 ----
// localStorage 殘影／過期 session 撿回來只會讓 personal_sign 對著殭屍 relay
// 空等 150 秒（#597 真凶的另一半）：readWcSession 必須自己看 session.expiry。
const { readWcSession: readSessionExpiry } = await import(moduleUrl);
const nowSec = () => Math.floor(Date.now() / 1000);
const sessionOf = (expiry) => ({
    request() {},
    session: {
        expiry,
        namespaces: {
            eip155: {
                accounts: ['eip155:1:0x6666666666666666666666666666666666666666'],
            },
        },
    },
});
const expiringAppKit = (expiry) => ({
    getAccount: () => undefined,
    getCaipAddress: () => undefined,
    getProvider: () => undefined,
    getWalletProvider: () => null,
    getUniversalProvider: async () => sessionOf(expiry),
});

assert.equal(
    await readSessionExpiry(expiringAppKit(nowSec() - 1)),
    null,
    '過期 session 必須拒絕，不能拿殘影冒充登入'
);
const fresh = await readSessionExpiry(expiringAppKit(nowSec() + 3600));
assert.equal(
    fresh && fresh.address,
    '0x6666666666666666666666666666666666666666',
    '未過期 session 照常可用'
);

console.log('evm walletconnect session expiry tests passed');

// ---- pairing 診斷摘要 ----
// 2026-09-01 22:13 實測：除錯面板永遠「appkit=0 session=0」無法分辨 proposal
// 死在哪一層。readPairingSummary 要能一行分辨：publish 沒完成（0）、錢包沒回
// （active=false）、settle 迷路（active=true）。
const { readPairingSummary } = await import(moduleUrl);

assert.equal(
    await readPairingSummary({ getUniversalProvider: async () => ({}) }),
    'pairing=0',
    '沒有 pairing 要回 pairing=0（proposal publish 沒完成）'
);
assert.equal(await readPairingSummary(null), 'pairing=?');
assert.equal(
    await readPairingSummary({
        getUniversalProvider: async () => { throw new Error('init pending'); },
    }),
    'pairing=?',
    'provider 還沒就緒時給未知標記，不能丟例外砸掉 probe'
);

const up = (pairings, extra = {}) => ({
    client: { core: { pairing: { getPairings: () => pairings } }, ...extra },
});
const appKitWith = (pairings) => ({ getUniversalProvider: async () => up(pairings) });

assert.equal(await readPairingSummary(appKitWith([])), 'pairing=0');

const nowMs = Date.now();
const summary = await readPairingSummary(
    appKitWith([
        { topic: 'a', active: false, expiry: Math.floor((nowMs - 60_000) / 1000) },
        { topic: 'b', active: true, expiry: Math.floor((nowMs + 3600_000) / 1000) },
    ])
);
// alive 允許 3600：若 nowMs 擷取與讀值落在同一個牆鐘秒內，剩餘壽命正好一小時
assert.match(summary, /^pairing=2\(active=true,alive=(?:3600|359[89])s,uri=\?,wallet=\?\)$/,
    '要取最新一筆 pairing 並標出 active 與剩餘壽命；無 uri／無 ping 時給 ?');

const dead = await readPairingSummary(appKitWith([{ active: false, expiry: Math.floor(nowMs / 1000) }]));
assert.match(dead, /^pairing=1\(active=false,alive=0s,uri=\?,wallet=\?\)$/, '過期 pairing 顯示壽命 0');

const noExpiry = await readPairingSummary(appKitWith([{ active: true }]));
assert.equal(noExpiry, 'pairing=1(active=true,alive=?s,uri=?,wallet=?)');

// uri 與最新 pairing topic 的比對：錢包訂閱在 uri topic 上等 proposal，
// 若 uri 落後就不是同一個頻道——面板要一眼看出（uri≠topic!）。
const uriAppKit = (uri, pairings) => ({
    getUniversalProvider: async () => ({ uri, ...up(pairings) }),
});
const topicB = 'b'.repeat(66);
assert.equal(
    await readPairingSummary(uriAppKit(`wc:${topicB}@2?symKey=abc`, [{ topic: topicB, active: true }])),
    'pairing=1(active=true,alive=?s,uri=match,wallet=?)',
    'uri topic 等於最新 pairing topic → match'
);
assert.equal(
    await readPairingSummary(uriAppKit(`wc:${'c'.repeat(66)}@2?symKey=abc`, [{ topic: topicB, active: true }])),
    'pairing=1(active=true,alive=?s,uri≠topic!,wallet=?)',
    'uri topic 落後於最新 pairing → 要喊 uri≠topic!'
);
assert.equal(
    await readPairingSummary(uriAppKit('https://example.com', [{ topic: topicB, active: true }])),
    'pairing=1(active=true,alive=?s,uri=?,wallet=?)',
    '非 wc: 開頭的 uri 不當 topic 解析'
);

// pairing ping 活性：online＝錢包在 topic 上活著（proposal 丟了，dapp 可修）；
// offline＝錢包從未連上（deep link 沒被消化／錢包端網路問題）。
const { _resetPingProbeForTests } = await import(moduleUrl);

_resetPingProbeForTests();
const pongAppKit = {
    getUniversalProvider: async () =>
        up([{ topic: topicB, active: false }], { ping: async () => undefined }),
};
assert.equal(
    await readPairingSummary(pongAppKit),
    'pairing=1(active=false,alive=?s,uri=?,wallet=online)',
    'ping 有回 → wallet=online（錢包在線，proposal 丟了）'
);

_resetPingProbeForTests();
const silentAppKit = {
    getUniversalProvider: async () =>
        up([{ topic: topicB, active: false }], { ping: () => Promise.reject(new Error('timeout')) }),
};
assert.equal(
    await readPairingSummary(silentAppKit),
    'pairing=1(active=false,alive=?s,uri=?,wallet=offline)',
    'ping 逾時 → wallet=offline（錢包沒連上 topic）'
);

console.log('evm walletconnect pairing summary tests passed');

// ---- 除錯面板不得在正式站自動現形 ----
// wc-debug 面板把 relay/session/pairing 技術狀態整塊疊在畫面上，
// 2026-09-02 DANNY 回報正式站「監測資料跑出來」。enableWcDebug 只准在
// APP_CONFIG.DEBUG_MODE（hostname 推導、fail-closed）下自動展開；
// 正式站取證走 ?wc-debug=1，不受此閘影響。
const { enableWcDebug: forceWcDebug } = await import(moduleUrl);

// 沒有 window（Node）＝視同正式站：不准自動展開，也不能噴例外
assert.equal(forceWcDebug(), false, '沒有 DEBUG_MODE 不能自動展開除錯面板');

// 本機開發（DEBUG_MODE=true）才允許自動展開
globalThis.window = { APP_CONFIG: { DEBUG_MODE: true } };
assert.equal(forceWcDebug(), true, '本機開發要能自動展開');
delete globalThis.window;

// ?wc-debug=1 在正式站也不准現形（2026-09-22 DANNY：旗標被記住後面板一直黏著）；
// 只有本機開發或 localStorage.cm_wc_debug='1' 的支援人員看得到
{
    const { _wcDebugAllowedByUrl: allowed } = await import(moduleUrl);
    const store = new Map();
    globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null) };
    globalThis.location = { search: '?wc-debug=1' };
    globalThis.window = { APP_CONFIG: { DEBUG_MODE: false } };
    assert.equal(allowed(), false, '正式站 ?wc-debug=1 不准現形');
    store.set('cm_wc_debug', '1');
    assert.equal(allowed(), true, '支援人員 localStorage 明確開啟才看得到');
    store.clear();
    globalThis.window = { APP_CONFIG: { DEBUG_MODE: true } };
    assert.equal(allowed(), true, '本機開發 ?wc-debug=1 照舊');
    globalThis.location = { search: '' };
    assert.equal(allowed(), false, '沒有旗標就沒有面板');
    delete globalThis.window; delete globalThis.location; delete globalThis.localStorage;
}

console.log('evm walletconnect debug gate tests passed');
