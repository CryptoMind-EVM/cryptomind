// PR-6：Reown Email／Google 登入（REOWN_SOCIAL_LOGIN_ENABLED，預設關）的行為閘門。
// 由 tests/test_reown_social_login.py 包進 pytest。
//
// 守的是四件事：
// 1. 旗標關 → AppKit 設定跟上線前逐字相同（email／socials 關、不動帳戶類型）
// 2. 旗標開 → 只開 Email＋Google，內嵌錢包固定 EOA（AppKit 預設 smartAccount；
//    EOA 走零 RPC 的 ecrecover，智慧帳戶那條要打鏈上 RPC 且沒實測過）
// 3. Telegram／Base App／Farcaster／Play／被框住的頁面 → 永遠不開
// 4. 內嵌錢包的 provider 不收 eth_requestAccounts：連線結果要標記出來，登出要真的斷
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const social = await import(await loadModuleUrl('web/js/social-login.js'));
const {
    shouldEnableSocialLogin,
    readSocialLoginContext,
    computeSocialLogin,
    applySocialLoginButton,
} = social;

// ---- 1. 情境判定：只有「旗標開＋一般網頁」才開 ----
const plain = { flag: true, platform: 'web', framed: false, telegram: false };
assert.equal(shouldEnableSocialLogin(plain), true, '一般網頁＋旗標開 → 開');
assert.equal(shouldEnableSocialLogin({ ...plain, flag: false }), false, '旗標關 → 不開');
assert.equal(shouldEnableSocialLogin({ ...plain, flag: 'true' }), false, '只認布林 true');
assert.equal(shouldEnableSocialLogin({ ...plain, flag: undefined }), false);
for (const platform of ['tma', 'baseapp', 'play']) {
    assert.equal(shouldEnableSocialLogin({ ...plain, platform }), false, `${platform} 一律不開`);
}
assert.equal(shouldEnableSocialLogin({ ...plain, framed: true }), false, 'iframe 內（mini app 宿主）不開');
assert.equal(shouldEnableSocialLogin({ ...plain, telegram: true }), false, 'Telegram Mini App 不開');
assert.equal(shouldEnableSocialLogin(null), false);

// ---- 情境讀取：從 window 推得 ----
const top = {};
const winWeb = { CMPlatform: { get: () => 'web' }, Telegram: { WebApp: { initData: '' } } };
winWeb.self = winWeb;
winWeb.top = winWeb;
assert.deepEqual(readSocialLoginContext(winWeb), { platform: 'web', framed: false, telegram: false });

const winFramed = { CMPlatform: { get: () => 'web' }, self: {}, top };
assert.equal(readSocialLoginContext(winFramed).framed, true, 'self !== top → 被框住');

const winCrossOrigin = { CMPlatform: { get: () => 'web' } };
winCrossOrigin.self = winCrossOrigin;
Object.defineProperty(winCrossOrigin, 'top', {
    get() {
        throw new Error('cross-origin');
    },
});
assert.equal(readSocialLoginContext(winCrossOrigin).framed, true, '讀 top 丟例外 → 當作被框住（fail-closed）');

const winRn = { ReactNativeWebView: {}, CMPlatform: { get: () => 'web' } };
winRn.self = winRn;
winRn.top = winRn;
assert.equal(readSocialLoginContext(winRn).framed, true, 'Base App 的 RN WebView 視同宿主');

const winTg = { Telegram: { WebApp: { initData: 'query_id=1' } } };
winTg.self = winTg;
winTg.top = winTg;
assert.equal(readSocialLoginContext(winTg).telegram, true);
assert.equal(readSocialLoginContext(winTg).platform, 'web', '沒有 CMPlatform 時預設 web（其他條件照擋）');

const winTma = { CMPlatform: { get: () => 'tma' } };
winTma.self = winTma;
winTma.top = winTma;
assert.equal(readSocialLoginContext(winTma).platform, 'tma');

// ---- /api/config → 決策（fail-closed）----
const withConfig = (cfg, base) => ({
    ...(base || winWeb),
    AppAPI: { getAppConfig: async () => cfg },
});
const w1 = withConfig({ reown_social_login: true });
w1.self = w1;
w1.top = w1;
assert.equal(await computeSocialLogin(w1), true);
const w2 = withConfig({ reown_social_login: false });
w2.self = w2;
w2.top = w2;
assert.equal(await computeSocialLogin(w2), false);
const w3 = withConfig({});
w3.self = w3;
w3.top = w3;
assert.equal(await computeSocialLogin(w3), false, '舊後端沒有這個欄位 → 關');
const w4 = { ...winWeb, AppAPI: { getAppConfig: async () => { throw new Error('offline'); } } };
w4.self = w4;
w4.top = w4;
assert.equal(await computeSocialLogin(w4), false, '/api/config 失敗 → 關');
const w5 = { ...winWeb };
w5.self = w5;
w5.top = w5;
assert.equal(await computeSocialLogin(w5), false, '沒有 AppAPI → 關');
const w6 = withConfig({ reown_social_login: true }, winTma);
w6.self = w6;
w6.top = w6;
assert.equal(await computeSocialLogin(w6), false, 'Telegram 內旗標開也不開');

// ---- 登入視窗按鈕：只切 hidden ----
const makeBtn = () => {
    const classes = new Set(['hidden']);
    return {
        classes,
        classList: {
            toggle(name, force) {
                if (force) classes.add(name);
                else classes.delete(name);
            },
        },
    };
};
const btn = makeBtn();
const doc = { getElementById: (id) => (id === 'social-login-btn' ? btn : null) };
applySocialLoginButton(true, doc);
assert.equal(btn.classes.has('hidden'), false, '開 → 顯示');
applySocialLoginButton(false, doc);
assert.equal(btn.classes.has('hidden'), true, '關 → 藏');
applySocialLoginButton(true, { getElementById: () => null }); // 沒有按鈕的頁面不能炸

console.log('social-login context: ok');

// ---- 2. AppKit 設定 ----
const wc = await import(await loadModuleUrl('web/js/evm-walletconnect.js'));
const { buildAppKitConfig } = wc;

const off = buildAppKitConfig('https://getcryptomind.com', 'pid', [{ id: 1 }]);
assert.deepEqual(
    off.features,
    { analytics: false, email: false, socials: false },
    '旗標關：features 跟上線前逐字相同'
);
assert.equal('defaultAccountTypes' in off, false, '旗標關：不動帳戶類型');
assert.equal('termsConditionsUrl' in off, false, '旗標關：彈窗不多條款列');
assert.deepEqual(
    buildAppKitConfig('https://getcryptomind.com', 'pid', [{ id: 1 }], undefined, { socialLogin: false }),
    off,
    '明確傳 false 也一樣'
);

const on = buildAppKitConfig('https://getcryptomind.com', 'pid', [{ id: 1 }], undefined, {
    socialLogin: true,
});
assert.equal(on.features.email, true);
assert.deepEqual(on.features.socials, ['google'], '只開 Google');
assert.equal(on.features.emailShowWallets, true, '錢包使用者仍看得到錢包清單');
assert.equal(on.features.analytics, false);
assert.equal(on.features.onramp, true, '刷卡買幣要用');
assert.equal(on.features.swaps, false, '不做兌換（合規：不代理交易）');
assert.deepEqual(on.defaultAccountTypes, { eip155: 'eoa' }, '內嵌錢包固定 EOA');
assert.equal(on.metadata.url, 'https://getcryptomind.com', '其餘設定不變');
assert.equal(on.termsConditionsUrl, 'https://getcryptomind.com/legal/terms-of-service.html');
assert.equal(on.privacyPolicyUrl, 'https://getcryptomind.com/legal/privacy-policy.html');

console.log('appkit config: ok');

// ---- 3. 內嵌錢包的連線要被標記 ----
const { readEvmConnection } = wc;
const frameProvider = { request() {} };
const embeddedAppKit = {
    getAccount: () => ({
        address: '0x7777777777777777777777777777777777777777',
        isConnected: true,
        embeddedWalletInfo: {
            authProvider: 'google',
            accountType: 'eoa',
            user: { email: 'someone@gmail.com', username: 'Someone' },
        },
    }),
    getCaipAddress: () => undefined,
    getProvider: () => frameProvider,
};
// authProvider 給後台統計（2026-09-30）；Email 本身不能跟著帶出去
assert.deepEqual(readEvmConnection(embeddedAppKit), {
    address: '0x7777777777777777777777777777777777777777',
    provider: frameProvider,
    embeddedWallet: true,
    authProvider: 'google',
});
const noProvider = { ...embeddedAppKit, getAccount: () => ({ ...embeddedAppKit.getAccount(), embeddedWalletInfo: {} }) };
assert.equal(readEvmConnection(noProvider).authProvider, 'email', 'AppKit 沒給 authProvider＝Email 登入');

// 登入時回報的 login_via（evm-auth 送進 /api/user/evm-login）
const { loginViaFor } = await import(
    await loadModuleUrl(new URL('../../web/js/evm-login-feedback.js', import.meta.url).pathname)
);
assert.equal(loginViaFor(undefined), 'wallet', '注入錢包路徑不帶 opts');
assert.equal(loginViaFor({ viaWalletConnect: true }), 'wallet');
assert.equal(loginViaFor({ embeddedWallet: true, authProvider: 'google' }), 'google');
assert.equal(loginViaFor({ embeddedWallet: true, authProvider: 'GOOGLE' }), 'google');
assert.equal(loginViaFor({ embeddedWallet: true, authProvider: 'email' }), 'email');
assert.equal(loginViaFor({ embeddedWallet: true }), 'email');
assert.equal(loginViaFor({ embeddedWallet: true, authProvider: 'apple' }), 'social', '後端只收 wallet／email／google／social');
const externalAppKit = {
    getAccount: () => ({ address: '0x8888888888888888888888888888888888888888', isConnected: true }),
    getCaipAddress: () => undefined,
    getProvider: () => frameProvider,
};
assert.equal(
    'embeddedWallet' in readEvmConnection(externalAppKit),
    false,
    '一般錢包的連線結果形狀不變'
);

console.log('embedded connection flag: ok');

// ---- 4. 刷卡買 USDC（AppKit onramp）----
const { openOnrampWith, openEmbeddedWalletWith } = wc;
const opened = [];
const connectedAppKit = (account) => ({
    getAccount: () => account,
    getCaipAddress: () => undefined,
    getProvider: () => frameProvider,
    open: async (o) => {
        opened.push(o);
    },
});
const payer = '0x7777777777777777777777777777777777777777';
const acct = { address: payer, isConnected: true, embeddedWalletInfo: {} };

assert.deepEqual(
    await openOnrampWith(connectedAppKit(acct), { enabled: false, isPayer: () => true }),
    { ok: false, reason: 'disabled' }
);
assert.deepEqual(
    await openOnrampWith(connectedAppKit(undefined), { enabled: true, isPayer: () => true }),
    { ok: false, reason: 'not_connected' }
);
assert.deepEqual(
    await openOnrampWith(connectedAppKit(acct), { enabled: true, isPayer: () => false }),
    { ok: false, reason: 'not_payer', address: payer },
    '買到的 USDC 會進 AppKit 連著的地址——不是綁定付款錢包就不開'
);
assert.equal(opened.length, 0, '以上都不能打開 onramp');
assert.deepEqual(
    await openOnrampWith(connectedAppKit(acct), { enabled: true, isPayer: (a) => a === payer }),
    { ok: true, address: payer }
);
assert.deepEqual(opened.pop(), { view: 'OnRampProviders' });

// 內嵌錢包的「開啟我的錢包」（Send／Receive）：只給內嵌錢包
assert.deepEqual(
    await openEmbeddedWalletWith(connectedAppKit({ address: payer, isConnected: true }), { enabled: true }),
    { ok: false, reason: 'not_embedded' }
);
assert.deepEqual(
    await openEmbeddedWalletWith(connectedAppKit(acct), { enabled: false }),
    { ok: false, reason: 'disabled' }
);
assert.deepEqual(await openEmbeddedWalletWith(connectedAppKit(acct), { enabled: true }), {
    ok: true,
    address: payer,
});
assert.deepEqual(opened.pop(), { view: 'Account' });

console.log('onramp: ok');

// ---- 5. 登出：內嵌錢包沒有 WC session，但要等恢復完再判斷 ----
const { readEmbeddedState, waitEmbeddedSettled } = wc;
assert.equal(readEmbeddedState(connectedAppKit(acct)), 'connected');
assert.equal(readEmbeddedState(connectedAppKit({ address: payer, isConnected: true })), 'none');
assert.equal(readEmbeddedState(connectedAppKit({ status: 'connecting' })), 'pending');
assert.equal(readEmbeddedState(connectedAppKit(undefined)), 'none');
assert.equal(
    readEmbeddedState({
        getAccount() {
            throw new Error('not ready');
        },
    }),
    'none'
);

// 頁面剛載入：AppKit 還在跟 iframe 恢復內嵌錢包（status=connecting），幾拍後才連上
let calls = 0;
const restoring = {
    getAccount: () => {
        calls += 1;
        return calls < 3 ? { status: 'connecting' } : acct;
    },
};
assert.equal(await waitEmbeddedSettled(restoring, { timeoutMs: 1000, intervalMs: 5 }), 'connected');
const stuck = { getAccount: () => ({ status: 'connecting' }) };
assert.equal(
    await waitEmbeddedSettled(stuck, { timeoutMs: 30, intervalMs: 5 }),
    'pending',
    '逾時還在恢復 → pending（呼叫端保留待斷線旗標，下次再收）'
);

console.log('embedded logout: ok');
console.log('reown_social_login: ok');
