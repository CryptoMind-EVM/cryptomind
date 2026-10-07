// 舊 TON 身份提醒橫幅＋錢包卡的身份地址（2026-09-25）。
//
// 1. legacy-ton-notice.js：只有 /api/user/me 回 login_backup_needed=true（TON 身份、
//    還沒綁 EVM／Google）才顯示；關掉就不再出現；綁好（evm:wallet-bound／google:linked）
//    當下收起。
// 2. auth.js：錢包卡不再拿 user_id 當地址（Telegram 帳號會顯示「tg_12…3456」）、
//    不再預設 auth_method=ton_wallet。
//
// node 實跑 web/js 原始碼，stub window／document／storage／fetch。
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const SOURCES = {
    notice: await readFile('web/js/legacy-ton-notice.js', 'utf8'),
    api: await readFile('web/js/api-client.js', 'utf8'),
    auth: await readFile('web/js/auth.js', 'utf8'),
};

let instance = 0;
function freshUrl(src) {
    instance += 1;
    return `data:text/javascript;base64,${Buffer.from(`${src}\n// instance ${instance}\n`).toString('base64')}`;
}

function memoryStorage() {
    const store = new Map();
    return {
        getItem: (k) => (store.has(k) ? store.get(k) : null),
        setItem: (k, v) => store.set(k, String(v)),
        removeItem: (k) => store.delete(k),
    };
}

function setGlobal(name, value) {
    Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
}

function json(status, body) {
    return new Response(JSON.stringify(body), {
        status,
        headers: { 'content-type': 'application/json' },
    });
}

function fakeElement() {
    const classes = new Set(['hidden']);
    return {
        textContent: '',
        dataset: {},
        classList: {
            add: (...c) => c.forEach((x) => classes.add(x)),
            remove: (...c) => c.forEach((x) => classes.delete(x)),
            toggle: (c, force) => {
                const on = force === undefined ? !classes.has(c) : !!force;
                if (on) classes.add(c);
                else classes.delete(c);
                return on;
            },
            contains: (c) => classes.has(c),
        },
        getAttribute: () => null,
        setAttribute() {},
        removeAttribute() {},
        addEventListener() {},
        querySelector: () => null,
        get hidden() {
            return classes.has('hidden');
        },
    };
}

async function boot({ routes = {}, storage = memoryStorage() } = {}) {
    const target = new EventTarget();
    const elements = { 'legacy-ton-banner': fakeElement(), 'profile-method': fakeElement() };
    setGlobal('window', globalThis);
    setGlobal('addEventListener', target.addEventListener.bind(target));
    setGlobal('removeEventListener', target.removeEventListener.bind(target));
    setGlobal('dispatchEvent', target.dispatchEvent.bind(target));
    setGlobal('localStorage', storage);
    setGlobal('sessionStorage', memoryStorage());
    setGlobal('location', { search: '', pathname: '/', origin: 'https://example.test', reload() {} });
    setGlobal('history', { state: null, replaceState() {} });
    setGlobal('document', {
        readyState: 'complete',
        visibilityState: 'visible',
        getElementById: (id) => elements[id] || null,
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
        removeEventListener() {},
    });
    setGlobal('APP_CONFIG', { DEBUG_MODE: false });
    setGlobal('AppUtils', { refreshIcons() {} });
    setGlobal('showToast', () => {});
    setGlobal('I18n', { t: (k) => k });
    setGlobal('setInterval', () => 0);
    setGlobal('clearInterval', () => {});
    setGlobal('fetch', async (url) => (routes[url] ? routes[url]() : json(200, { ok: true })));

    await import(freshUrl(SOURCES.notice));
    await import(freshUrl(SOURCES.api));
    await import(freshUrl(SOURCES.auth));
    return { elements, AM: globalThis.AuthManager, notice: globalThis.LegacyTonNotice };
}

const TON = 'EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N';
const EVM = '0x3304e22ddaa22bcdc5fca2269b418046ae7b566a';

// ---- 橫幅：只給 TON 身份且沒有備用登入的人 ----
{
    const s = await boot();
    const banner = s.elements['legacy-ton-banner'];

    s.AM.currentUser = { user_id: TON, auth_method: 'ton_wallet', login_backup_needed: true };
    s.AM._updateUI(true);
    assert.equal(banner.hidden, false, 'TON 身份、沒備用登入 → 顯示');

    s.AM.currentUser = { user_id: `evm_${EVM}`, auth_method: 'evm_wallet', login_backup_needed: false };
    s.AM._updateUI(true);
    assert.equal(banner.hidden, true, 'EVM 使用者 → 不顯示');

    s.AM.currentUser = { user_id: 'tg_42', auth_method: 'telegram' };
    s.AM._updateUI(true);
    assert.equal(banner.hidden, true, '沒有旗標（Telegram）→ 不顯示');

    s.AM.currentUser = { user_id: TON, auth_method: 'ton_wallet', login_backup_needed: true };
    s.AM._updateUI(false);
    assert.equal(banner.hidden, true, '登出 → 不顯示');
}

// ---- 關掉就不再出現（同一台裝置）----
{
    const storage = memoryStorage();
    const s = await boot({ storage });
    const banner = s.elements['legacy-ton-banner'];
    s.AM.currentUser = { user_id: TON, auth_method: 'ton_wallet', login_backup_needed: true };
    s.AM._updateUI(true);
    assert.equal(banner.hidden, false);
    s.notice.dismiss();
    assert.equal(banner.hidden, true, '按關閉 → 收起');
    s.AM._updateUI(true);
    assert.equal(banner.hidden, true, '關過就不再顯示');

    const again = await boot({ storage });
    again.AM.currentUser = { user_id: TON, auth_method: 'ton_wallet', login_backup_needed: true };
    again.AM._updateUI(true);
    assert.equal(again.elements['legacy-ton-banner'].hidden, true, '重新載入頁面也不再顯示');
}

// ---- 綁好 EVM／Google → 當下收起 ----
for (const evt of ['evm:wallet-bound', 'google:linked']) {
    const s = await boot();
    const banner = s.elements['legacy-ton-banner'];
    s.AM.currentUser = { user_id: TON, auth_method: 'ton_wallet', login_backup_needed: true };
    s.AM._updateUI(true);
    assert.equal(banner.hidden, false);
    window.dispatchEvent(new Event(evt));
    assert.equal(banner.hidden, true, `${evt} → 收起`);
    assert.equal(s.AM.currentUser.login_backup_needed, false, `${evt} → 旗標清掉`);
}

// ---- /api/user/me 帶回的旗標要進 currentUser ----
{
    const s = await boot();
    s.AM.currentUser = { user_id: TON, auth_method: 'ton_wallet' };
    s.AM._applyBackendSessionUser({ user_id: TON, auth_method: 'ton_wallet', login_backup_needed: true });
    assert.equal(s.AM.currentUser.login_backup_needed, true);
}

// ---- 錢包卡：不拿 user_id 當地址、不預設 ton_wallet ----
{
    const s = await boot({
        routes: {
            '/api/user/wallet-status': () => json(200, { success: true, has_wallet: false, auth_method: 'telegram', wallet_address: null }),
        },
    });
    s.AM.currentUser = { user_id: 'tg_42', uid: 'tg_42', auth_method: 'telegram', has_wallet: false };
    const st = await window.getWalletStatus();
    assert.equal(st.has_wallet, false);
    assert.equal(st.wallet_address, null, 'Telegram 帳號沒有錢包地址（不是 tg_42）');
    assert.equal(st.auth_method, 'telegram');
}
{
    const s = await boot({
        routes: {
            '/api/user/wallet-status': () => json(200, { success: true, has_wallet: true, auth_method: 'telegram', wallet_address: EVM }),
        },
    });
    s.AM.currentUser = { user_id: 'tg_42', uid: 'tg_42', auth_method: 'telegram', has_wallet: true };
    const st = await window.getWalletStatus();
    assert.equal(st.wallet_address, EVM, '綁了 EVM 錢包 → 顯示綁定地址');
}
{
    const s = await boot({
        routes: { '/api/user/wallet-status': () => json(500, { detail: 'boom' }) },
    });
    s.AM.currentUser = { user_id: `evm_${EVM}`, uid: `evm_${EVM}`, auth_method: 'evm_wallet' };
    const st = await window.getWalletStatus();
    assert.equal(st.has_wallet, true, 'EVM 身份本身就是錢包');
    assert.equal(st.wallet_address, EVM, '從 evm_ 身份還原地址');
}
{
    const s = await boot({
        routes: { '/api/user/wallet-status': () => json(200, { success: true, has_wallet: false }) },
    });
    s.AM.currentUser = { user_id: 'g_1', uid: 'g_1' };
    const st = await window.getWalletStatus();
    assert.notEqual(st.auth_method, 'ton_wallet', '後端沒給 auth_method 不可預設成 ton_wallet');
    assert.equal(st.wallet_address, null);
}
{
    // 登入方式徽章：currentUser 沒有 auth_method 時不可顯示成 TON 錢包
    const s = await boot();
    s.AM.currentUser = { user_id: 'g_1', uid: 'g_1', username: 'g' };
    s.AM._updateUI(true);
    assert.notEqual(s.elements['profile-method'].textContent, 'TON_WALLET');
    assert.ok(!/ton/i.test(s.elements['profile-method'].textContent));
}

console.error('legacy_ton_notice: ok');
