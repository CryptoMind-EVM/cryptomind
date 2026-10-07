// userFacingMessage 的呼叫端行為（2026-10-06）：api-client 的逾時旗標、google-auth／trustScoreManager／
// evm-auth 的「在地化文案優先於錯誤原文」與「錢包取消不是錯誤」。helper 本身見 error_message.mjs。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { loadModuleUrl } from './_load.mjs';

const dicts = Object.fromEntries(
    ['zh-TW', 'en'].map((l) => [l, JSON.parse(readFileSync(new URL(`../../web/js/i18n/${l}.json`, import.meta.url), 'utf8'))])
);
const resolve = (dict, key) =>
    key.split('.').reduce((cur, part) => (cur && typeof cur === 'object' ? cur[part] : undefined), dict);
let lang = 'zh-TW';
const T = (key) => resolve(dicts[lang], key);

globalThis.window = globalThis;
globalThis.I18n = {
    t: (key) => {
        const v = resolve(dicts[lang], key);
        return typeof v === 'string' ? v : key;
    },
    getLanguage: () => lang,
};
globalThis.console.error = () => {};
globalThis.console.warn = () => {};
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.dispatchEvent = () => true;
globalThis.CustomEvent = class CustomEvent {
    constructor(type, init) {
        this.type = type;
        this.detail = init && init.detail;
    }
};

const http = (status, message) => Object.assign(new Error(message), { status });
const el = () => ({
    textContent: '',
    innerHTML: '',
    disabled: false,
    clientWidth: 300,
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    setAttribute() {},
    addEventListener() {},
});

const { userFacingMessage } = await import(
    await loadModuleUrl(new URL('../../web/js/error-message.js', import.meta.url).pathname)
);

// ───────────────────────── api-client：逾時旗標，.message 與原生錯誤原樣 ─────────────────────────
{
    globalThis.document = { getElementById: () => null };
    globalThis.AuthManager = undefined;
    let mode = 'hang';
    globalThis.fetch = (url, opts) =>
        new Promise((resolveFetch, reject) => {
            if (mode === 'hang') {
                opts.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
            } else if (mode === 'typeerror') {
                reject(new TypeError('Failed to fetch'));
            } else if (mode === 'html502') {
                resolveFetch({
                    ok: false,
                    status: 502,
                    statusText: '',
                    text: async () => '<html>Bad Gateway</html>',
                    headers: { get: () => 'text/html' },
                });
            }
        });
    // AbortSignal 的 addEventListener 在 node 有；fetch stub 需要
    const apiUrl = await loadModuleUrl(new URL('../../web/js/api-client.js', import.meta.url).pathname);
    const { AppAPI } = await import(apiUrl);
    const opts = { timeout: 20, retries: 0, _skipAuthGate: true };

    let err = await AppAPI.get('/api/x', opts).catch((e) => e);
    assert.equal(err.message, 'Request timeout (20ms)', '.message 不改（有地方靠它比對）');
    assert.equal(err.status, 0);
    assert.equal(err.timeout, true);
    assert.equal(userFacingMessage(err), T('error.timeout'));

    mode = 'typeerror';
    err = await AppAPI.get('/api/x', opts).catch((e) => e);
    assert.ok(err instanceof TypeError, '原生 TypeError 照樣丟出（filter.js 比對 Failed to fetch）');
    assert.equal(err.message, 'Failed to fetch');
    assert.equal(err.status, undefined);
    assert.equal(userFacingMessage(err), T('common.networkUnstable'));

    mode = 'html502';
    err = await AppAPI.get('/api/x', opts).catch((e) => e);
    assert.equal(err.message, 'Status 502: ', '.message 維持原樣');
    assert.equal(err.status, 502);
    assert.equal(userFacingMessage(err), T('error.serverError'));
}

// ───────────────────────── google-auth：在地化文案優先於原文 ─────────────────────────
{
    const status = el();
    const linkStatus = el();
    const container = el();
    const wrap = el();
    const elements = {
        'google-login-status': status,
        'google-link-status': linkStatus,
        'google-login-container': container,
        'google-login-wrap': wrap,
    };
    globalThis.document = {
        getElementById: (id) => elements[id] || null,
        documentElement: { classList: { contains: () => false } },
    };
    let credentialCb = null;
    globalThis.google = { accounts: { id: { initialize: (cfg) => (credentialCb = cfg.callback), renderButton() {} } } };
    let postImpl;
    globalThis.AppAPI = {
        get: async () => ({ google_client_id: 'cid' }),
        post: async (...args) => postImpl(...args),
    };
    const { setupGoogleLogin, GoogleLinkApp } = await import(
        await loadModuleUrl(new URL('../../web/js/google-auth.js', import.meta.url).pathname)
    );
    await setupGoogleLogin();
    assert.equal(typeof credentialCb, 'function');

    const loginWith = async (impl) => {
        postImpl = impl;
        await credentialCb({ credential: 'tok' });
        return status.textContent;
    };
    lang = 'zh-TW';
    assert.equal(await loginWith(async () => { throw http(401, 'Invalid Google token'); }), T('googleAuth.loginFailed'));
    assert.equal(await loginWith(async () => { throw new TypeError('Failed to fetch'); }), T('common.networkUnstable'));
    assert.equal(await loginWith(async () => ({ success: false, detail: 'login failed' })), T('googleAuth.loginFailed'));
    assert.equal(await loginWith(async () => ({ success: false })), T('googleAuth.loginFailed'));
    assert.equal(await loginWith(async () => { throw http(502, 'Status 502: Bad Gateway'); }), T('error.serverError'));
    // 後端本來就給人看的中文 detail 照顯示
    assert.equal(await loginWith(async () => { throw http(403, '這個 Google 帳號已被停用'); }), '這個 Google 帳號已被停用');
    // 英文介面：英文 detail 本來就是介面語言
    lang = 'en';
    assert.equal(await loginWith(async () => { throw http(401, 'Invalid Google token'); }), 'Invalid Google token');
    lang = 'zh-TW';

    // 綁定／解綁（Connections 分頁）
    postImpl = async () => { throw http(409, 'Google account already linked'); };
    await GoogleLinkApp.bind('cred');
    assert.equal(linkStatus.textContent, T('googleAuth.linkFailed'));
    // 後端可據以行動的英文 detail 有對照表，不退化成「綁定失敗」
    postImpl = async () => { throw http(409, 'This Google account is already linked to another CryptoMind account'); };
    await GoogleLinkApp.bind('cred');
    assert.equal(linkStatus.textContent, T('error.details.googleAlreadyLinked'));
    postImpl = async () => { throw new TypeError('Load failed'); };
    await GoogleLinkApp.bind('cred');
    assert.equal(linkStatus.textContent, T('common.networkUnstable'));
    globalThis.showConfirmDialog = async () => true;
    postImpl = async () => { throw http(500, 'Internal Server Error'); };
    await GoogleLinkApp.unlink();
    assert.equal(linkStatus.textContent, T('error.serverError'));
    delete globalThis.showConfirmDialog;
}

// ───────────────────────── trustScoreManager：取消不是錯誤、訊息不外露英文 ─────────────────────────
{
    const btn = el();
    globalThis.document = {
        getElementById: (id) => (id === 'btn-evm-bind' ? btn : null),
    };
    const toasts = [];
    const dialogs = [];
    globalThis.showToast = (msg, type) => toasts.push({ msg, type });
    globalThis.showInfoDialog = async (d) => dialogs.push(d);
    globalThis.alert = () => assert.fail('不該走 alert');
    let signImpl;
    globalThis.ethereum = {};
    globalThis.ethers = {
        providers: {
            Web3Provider: class {
                async send() {
                    return ['0xabc'];
                }
                getSigner() {
                    return { signMessage: (m) => signImpl(m) };
                }
            },
        },
    };
    const response = (status, body) => ({
        ok: status < 400,
        status,
        json: async () => JSON.parse(body),
        text: async () => body,
    });
    let fetchImpl;
    globalThis.fetch = (url, init) => fetchImpl(url, init);
    const okFlow = (url) =>
        url.includes('/nonce') ? response(200, JSON.stringify({ message: 'm', payload: 'p' })) : response(200, '{}');

    await import(await loadModuleUrl(new URL('../../web/js/trustScoreManager.js', import.meta.url).pathname));
    const { bindEvm, unbindEvm } = window.TrustScoreManager;

    const reset = () => {
        toasts.length = 0;
        dialogs.length = 0;
        btn.disabled = true;
    };

    // 取消：ethers v5.7 的 ACTION_REJECTED、MetaMask 舊版只有 'User denied message signature'、imToken 的 'cancel'
    for (const rejection of [
        Object.assign(new Error('user rejected signing (action="signMessage", code=ACTION_REJECTED)'), { code: 'ACTION_REJECTED' }),
        new Error('MetaMask Message Signature: User denied message signature.'),
        Object.assign(new Error('x'), { code: 4001 }),
        new Error('cancel'),
    ]) {
        reset();
        fetchImpl = async (url) => okFlow(url);
        signImpl = async () => { throw rejection; };
        await bindEvm();
        assert.equal(dialogs.length, 0, `取消不彈錯誤對話框：${rejection.message}`);
        assert.deepEqual(toasts, [{ msg: T('trust.bindCancelled'), type: 'info' }]);
        assert.equal(btn.disabled, false, '按鈕要還原');
    }

    // 網路層失敗：不顯示 'Failed to fetch'
    reset();
    fetchImpl = async () => { throw new TypeError('Failed to fetch'); };
    await bindEvm();
    assert.equal(dialogs.length, 1);
    assert.equal(dialogs[0].title, T('trust.bindFailed'));
    assert.equal(dialogs[0].message, T('common.networkUnstable'));
    assert.equal(dialogs[0].tone, 'error');

    // nonce 502（以前是 'nonce HTTP 502'）
    reset();
    fetchImpl = async () => response(502, '<html>Bad Gateway</html>');
    await bindEvm();
    assert.equal(dialogs[0].message, T('error.serverError'));

    // 後端 403／409（以前是英文寫死的 'Signature verification failed'）
    for (const [status, key] of [[403, 'trust.bindSignatureFailed'], [409, 'trust.bindAlreadyBound']]) {
        reset();
        signImpl = async () => '0xsig';
        fetchImpl = async (url, init) => (init && init.method === 'POST' ? response(status, '{}') : okFlow(url));
        await bindEvm();
        assert.equal(dialogs[0].message, T(key));
    }
    // 其餘 bind 失敗（500）
    reset();
    fetchImpl = async (url, init) => (init && init.method === 'POST' ? response(500, '{"detail":"boom"}') : okFlow(url));
    await bindEvm();
    assert.equal(dialogs[0].message, T('error.serverError'));
    // 錢包 SDK 的英文 plain error
    reset();
    fetchImpl = async (url) => okFlow(url);
    signImpl = async () => { throw new Error('Internal JSON-RPC error.'); };
    await bindEvm();
    assert.equal(dialogs[0].message, T('app.unexpectedError'));

    // 解綁失敗
    reset();
    globalThis.showConfirmDialog = async () => true;
    fetchImpl = async () => response(500, 'oops');
    await unbindEvm();
    assert.equal(dialogs[0].title, T('trust.unbindFailed'));
    assert.equal(dialogs[0].message, T('error.serverError'));
    reset();
    fetchImpl = async () => { throw new TypeError('NetworkError when attempting to fetch resource.'); };
    await unbindEvm();
    assert.equal(dialogs[0].message, T('common.networkUnstable'));
    delete globalThis.showConfirmDialog;
}

// ───────────────────────── evm-auth：取消與失敗訊息 ─────────────────────────
{
    const store = new Map();
    const memoryStorage = {
        getItem: (k) => (store.has(k) ? store.get(k) : null),
        setItem: (k, v) => store.set(k, String(v)),
        removeItem: (k) => store.delete(k),
    };
    globalThis.localStorage = memoryStorage;
    globalThis.sessionStorage = memoryStorage;
    globalThis.document = {
        readyState: 'complete',
        visibilityState: 'visible',
        getElementById: () => null,
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
        documentElement: { classList: { contains: () => false } },
    };
    const toasts = [];
    globalThis.showToast = (msg, type) => toasts.push({ msg, type });
    let nonceImpl;
    let postImpl;
    globalThis.AppAPI = {
        get: async (url) => nonceImpl(url),
        post: async (url, body) => {
            if (url.includes('wc-connect-event')) return {};
            return postImpl(url, body);
        },
    };
    let requestImpl;
    globalThis.__miniAppEthereumProvider = { request: async (args) => requestImpl(args) };
    await import(await loadModuleUrl(new URL('../../web/js/evm-auth.js', import.meta.url).pathname));

    const challenge = { success: true, message: 'Sign in to CryptoMind', nonce_token: 'n' };
    const wallet = (signError) => async ({ method }) => {
        if (method === 'eth_requestAccounts') return ['0x1111111111111111111111111111111111111111'];
        if (method === 'personal_sign') throw signError;
        throw new Error(`unexpected ${method}`);
    };
    const run = async (fn) => {
        toasts.length = 0;
        await fn();
        return toasts.slice();
    };
    const login = () => window.safeEvmLogin();
    const bind = () => window.safeEvmBind();

    // ── 登入：錢包取消只給中性提示，不是紅色錯誤 ──
    for (const rejection of [
        new Error('User denied message signature'), // MetaMask／Coinbase：以前只認 4001 或 'user rejected'，這句會變紅色 toast
        new Error('cancel'), // imToken
        new Error('User canceled'),
        Object.assign(new Error('User rejected.'), { code: 5000 }), // WalletConnect v2
        Object.assign(new Error('x'), { code: 4001 }),
    ]) {
        nonceImpl = async () => challenge;
        requestImpl = wallet(rejection);
        const out = await run(login);
        const errors = out.filter((t) => t.type === 'error');
        assert.deepEqual(errors, [], `取消不該有紅色 toast：${rejection.message}`);
        assert.ok(out.some((t) => t.type === 'info' && t.msg === T('evmAuth.loginCancelled')), '中性的「已取消登入」');
    }

    // ── 登入：網路層失敗不貼 'Failed to fetch' ──
    nonceImpl = async () => { throw new TypeError('Failed to fetch'); };
    requestImpl = wallet(new Error('unused'));
    let out = await run(login);
    let red = out.filter((t) => t.type === 'error');
    assert.equal(red.length, 1);
    assert.equal(red[0].msg, T('common.networkUnstable'));
    assert.ok(!red[0].msg.includes('fetch'));

    // ── 登入：錢包 SDK 的英文訊息在非英文介面不附在括號裡；英文介面照附（方便回報） ──
    nonceImpl = async () => challenge;
    requestImpl = wallet(new Error('Connection request reset. Please try again.'));
    out = await run(login);
    red = out.filter((t) => t.type === 'error');
    assert.equal(red.length, 1);
    assert.equal(red[0].msg, T('evmAuth.verifyFailed'));
    lang = 'en';
    out = await run(login);
    red = out.filter((t) => t.type === 'error');
    assert.equal(red[0].msg, `${T('evmAuth.verifyFailed')}（Connection request reset. Please try again. [personal_sign]）`);
    lang = 'zh-TW';
    // 程式自己丟的在地化訊息（簽名逾時等）照附
    nonceImpl = async () => { throw new Error('錢包連線逾時——請再按一次 EVM 登入'); };
    out = await run(login);
    red = out.filter((t) => t.type === 'error');
    assert.equal(red[0].msg, `${T('evmAuth.verifyFailed')}（錢包連線逾時——請再按一次 EVM 登入）`);
    // 後端 HTTP 錯誤（有 status）不附技術訊息
    nonceImpl = async () => { throw http(502, 'Status 502: Bad Gateway'); };
    out = await run(login);
    red = out.filter((t) => t.type === 'error');
    assert.equal(red[0].msg, T('evmAuth.verifyFailed'));

    // ── 綁定：取消／網路層／SDK 英文 ──
    nonceImpl = async () => challenge;
    requestImpl = wallet(new Error('User denied message signature'));
    out = await run(bind);
    assert.deepEqual(out.filter((t) => t.type === 'error'), []);
    assert.ok(out.some((t) => t.type === 'info' && t.msg === T('evmAuth.loginCancelled')));

    nonceImpl = async () => { throw new TypeError('Load failed'); };
    out = await run(bind);
    red = out.filter((t) => t.type === 'error');
    assert.equal(red[0].msg, T('common.networkUnstable'));

    nonceImpl = async () => challenge;
    requestImpl = async ({ method }) => {
        if (method === 'eth_requestAccounts') throw new Error('Connection request reset. Please try again.');
        throw new Error('unreachable');
    };
    out = await run(bind);
    red = out.filter((t) => t.type === 'error');
    assert.equal(red[0].msg, T('evmAuth.bindFailed'));

    requestImpl = wallet(null);
    requestImpl = async ({ method }) =>
        method === 'eth_requestAccounts' ? ['0x1111111111111111111111111111111111111111'] : '0xsig';
    postImpl = async () => { throw http(409, 'Address already bound'); };
    out = await run(bind);
    red = out.filter((t) => t.type === 'error');
    assert.equal(red[0].msg, T('evmAuth.bindConflict'));
}

console.log('ok');
process.exit(0); // evm-auth 的續登輪詢／_withTimeout 計時器不讓 node 自己結束
