// AuthManager.init() 每次頁面載入只跑一次（2026-09-25 盤查）。
//
// api-client 的 auth gate 讓每支 AppAPI 請求都先 await AuthManager.init()。init 原本
// 在 finally 把 _initPromise 清掉，只擋得住「同時」的呼叫——之後每支請求都重跑整套
// 初始化：/api/user/me＋/api/user/refresh 各打一次、_updateUI、再發一次
// auth:initialized（NotificationService／toolSettings／Telegram 自動登入跟著重跑）。
//
// 這裡用 node 實跑 api-client.js＋auth.js（TMA 情境再加 ton-auth.js），stub
// fetch／window／storage，數網路呼叫次數與事件次數。
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const SOURCES = {
    api: await readFile('web/js/api-client.js', 'utf8'),
    auth: await readFile('web/js/auth.js', 'utf8'),
    ton: await readFile('web/js/ton-auth.js', 'utf8'),
};

// 每個情境要全新的模組狀態（AuthManager 單例、refresh 單飛、config 快取）；
// data: URL 內容不同才會被當成不同模組，所以尾巴補一行編號註解。
let instance = 0;
function freshUrl(src) {
    instance += 1;
    const body = `${src}\n// instance ${instance}\n`;
    return `data:text/javascript;base64,${Buffer.from(body).toString('base64')}`;
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

const STORED_USER = { user_id: 'u1', uid: 'u1', username: 'alice', authMethod: 'evm_wallet' };

const DEFAULT_ROUTES = {
    '/api/user/me': () =>
        json(200, { user: { user_id: 'u1', username: 'alice', auth_method: 'evm_wallet' } }),
    '/api/user/refresh': () => json(200, { success: true }),
    '/api/config': () => json(200, { test_mode: false }),
    '/api/user/logout': () => json(200, { success: true }),
    '/api/user/telegram-login': () =>
        json(200, { success: true, user: { user_id: 'tg1', username: 'tg', auth_method: 'telegram' } }),
};

// 瀏覽器裡 window 就是全域物件：bare 的 AppAPI／AuthManager 與 window.* 是同一個。
async function boot({ storedUser = null, routes = {}, telegram = false } = {}) {
    // 每個情境一個新的 EventTarget——舊情境模組掛的監聽不會串到新情境
    const target = new EventTarget();
    setGlobal('window', globalThis);
    setGlobal('addEventListener', target.addEventListener.bind(target));
    setGlobal('removeEventListener', target.removeEventListener.bind(target));
    setGlobal('dispatchEvent', target.dispatchEvent.bind(target));
    setGlobal('localStorage', memoryStorage());
    setGlobal('sessionStorage', memoryStorage());
    const location = {
        search: '',
        pathname: '/',
        origin: 'https://example.test',
        reloads: 0,
        reload() {
            this.reloads += 1;
        },
    };
    setGlobal('location', location);
    setGlobal('history', { state: null, replaceState() {} });
    setGlobal('document', {
        readyState: 'complete',
        visibilityState: 'visible',
        getElementById: () => null,
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
        removeEventListener() {},
    });
    setGlobal('APP_CONFIG', { DEBUG_MODE: false });
    setGlobal('AppUtils', { refreshIcons() {} });
    setGlobal('showToast', () => {});
    // 30 分鐘的 refresh 定時器會讓 node 不退出——這裡不測它
    setGlobal('setInterval', () => 0);
    setGlobal('clearInterval', () => {});
    setGlobal('Telegram', telegram ? { WebApp: { platform: 'ios', initData: 'query_id=x' } } : undefined);
    setGlobal('TON_CONNECT_UI', { TonConnectUI: class {} });

    const calls = [];
    const table = { ...DEFAULT_ROUTES, ...routes };
    setGlobal('fetch', async (url) => {
        calls.push(url);
        const nth = calls.filter((u) => u === url).length;
        const handler = table[url] || (() => json(200, { ok: true }));
        return handler(nth);
    });

    const initialized = [];
    const changed = [];
    target.addEventListener('auth:initialized', (e) => initialized.push(e.detail.isLoggedIn));
    target.addEventListener('auth:changed', (e) => changed.push(e.detail.isLoggedIn));

    if (storedUser) localStorage.setItem('ton_user', JSON.stringify(storedUser));

    await import(freshUrl(SOURCES.api));
    await import(freshUrl(SOURCES.auth));
    if (telegram) await import(freshUrl(SOURCES.ton));

    return {
        AM: globalThis.AuthManager,
        api: globalThis.AppAPI,
        location,
        initialized,
        changed,
        count: (url) => calls.filter((u) => u === url).length,
        route(url, handler) {
            table[url] = handler;
        },
    };
}

async function settle(done, label) {
    for (let i = 0; i < 200; i++) {
        if (done()) return;
        await new Promise((r) => setImmediate(r));
    }
    assert.fail(`${label}：等不到狀態收斂`);
}

// ---- 已登入：之後每支請求都不重跑 init ----
{
    const s = await boot({ storedUser: STORED_USER });
    await s.api.get('/api/a');
    await s.api.get('/api/b');
    await s.api.get('/api/c');
    assert.equal(await s.AM.init(), true, 'init 結果要記住（已登入）');
    assert.equal(s.count('/api/user/me'), 1, '一次頁面載入只打一次 /api/user/me');
    assert.equal(s.count('/api/user/refresh'), 1, '一次頁面載入只打一次 /api/user/refresh');
    assert.deepEqual(s.initialized, [true], 'auth:initialized 只發一次');
}

// ---- 同時的呼叫者共用同一個 promise ----
{
    const s = await boot({ storedUser: STORED_USER });
    const results = await Promise.all([
        s.AM.init(),
        s.AM.init(),
        s.api.get('/api/a'),
        s.api.get('/api/b'),
    ]);
    assert.equal(results[0], true);
    assert.equal(results[1], true);
    assert.equal(s.count('/api/user/me'), 1, '並發呼叫只跑一次初始化');
    assert.deepEqual(s.initialized, [true]);
}

// ---- 訪客：判定一次未登入就記住，請求不再重跑（也不再重發 auth:initialized）----
{
    const s = await boot();
    await s.api.get('/api/a');
    await s.api.get('/api/b');
    assert.equal(await s.AM.init(), false, '訪客 init 結果是 false');
    assert.equal(s.count('/api/user/me'), 0, '沒有本地 session 不打 /me');
    assert.deepEqual(s.initialized, [false], '訪客的 auth:initialized 也只發一次');

    // 訪客打到受保護端點 401（refresh 也失敗）→ 不能因此重跑 init 或重載
    s.route('/api/protected', () => json(401, { detail: 'Not authenticated' }));
    await assert.rejects(s.api.get('/api/protected'), (e) => e.status === 401);
    await s.api.get('/api/c');
    assert.deepEqual(s.initialized, [false], '訪客 401 不觸發重跑 init');
    assert.equal(s.location.reloads, 0, '訪客 401 不重載頁面');
}

// ---- 登入（evm-auth._applyEvmSession 的形狀：只寫 currentUser＋發 auth-success，
//      不呼叫 _updateUI）→ 重跑一次 init，之後又記住 ----
{
    const s = await boot();
    assert.equal(await s.AM.init(), false);
    s.AM.currentUser = { ...STORED_USER, accessTokenExpiry: Date.now() + 86400000 };
    s.AM._saveUserSession();
    s.AM.markRecentLoginSuccess();
    window.dispatchEvent(new Event('auth-success'));
    await s.api.get('/api/a');
    await s.api.get('/api/b');
    assert.equal(await s.AM.init(), true, '登入後 init 結果要換成已登入');
    assert.equal(s.count('/api/user/me'), 1, '登入後只重跑一次 init（/me 同步身分）');
    assert.equal(s.count('/api/user/refresh'), 1);
    assert.deepEqual(s.initialized, [false, true], '登入後要再發一次 auth:initialized（已登入）');
    assert.equal(s.changed.at(-1), true, '登入後 UI 要切到登入態（_updateUI(true)）');
}

// ---- 401 → refresh → 重試：照常運作，且不重跑 init ----
{
    const s = await boot({ storedUser: STORED_USER });
    await s.AM.init();
    s.route('/api/protected', (nth) =>
        nth === 1 ? json(401, { detail: 'expired' }) : json(200, { ok: 'retried' })
    );
    assert.deepEqual(await s.api.get('/api/protected'), { ok: 'retried' }, '401 後 refresh 再重試要成功');
    assert.equal(s.count('/api/user/refresh'), 2, '開機一次＋401 一次');
    assert.equal(s.count('/api/user/me'), 1, '401 自動恢復不需要重跑 init');
}

// ---- 頁面開著時 session 被撤銷（例如另一個分頁登出）：401 且 refresh 失敗後，
//      下一支請求要重驗 → 清掉 session 並重載成訪客 ----
{
    const s = await boot({ storedUser: STORED_USER });
    await s.AM.init();
    // 開機那次 refresh 會蓋「剛登入」時間戳（15 秒內暫緩清 session）——撥到寬限期外
    sessionStorage.setItem('ton_login_success_at', String(Date.now() - 60000));
    s.route('/api/user/me', () => json(401, { detail: 'expired' }));
    s.route('/api/user/refresh', () => json(401, { detail: 'expired' }));
    s.route('/api/a', () => json(401, { detail: 'expired' }));
    await assert.rejects(s.api.get('/api/a'), (e) => e.status === 401);
    await s.api.get('/api/b');
    assert.equal(s.count('/api/user/me'), 2, 'session 疑似失效後要重跑一次 init 驗證');
    assert.equal(s.AM.currentUser, null, '確認失效要清掉 currentUser');
    assert.equal(localStorage.getItem('ton_user'), null, '確認失效要清掉本地 session');
    assert.equal(s.location.reloads, 1, '確認失效要重載成訪客');
}

// ---- 登入過渡期（clearExpiredToken 暫緩）的結果不記住：下一個呼叫者重驗 ----
{
    const s = await boot({
        storedUser: STORED_USER,
        routes: {
            '/api/user/me': (nth) =>
                nth === 1
                    ? json(403, { detail: 'blip' })
                    : json(200, { user: { user_id: 'u1', username: 'alice', auth_method: 'evm_wallet' } }),
        },
    });
    s.AM.markRecentLoginSuccess();
    assert.equal(await s.AM.init(), false, '/me 失敗且在寬限期內 → 先回 false');
    assert.equal(s.location.reloads, 0, '寬限期內不重載');
    await s.api.get('/api/a');
    assert.equal(await s.AM.init(), true, '下一次要重驗並成功');
    assert.equal(s.count('/api/user/me'), 2, '暫緩後只重驗一次，成功就記住');
}

// ---- Telegram Mini App：沒有本地 session → 自動用 initData 登入 → 登入後重跑一次
//      init，之後的請求不再重跑 ----
{
    const s = await boot({ telegram: true });
    await s.AM.init();
    await settle(() => s.initialized.at(-1) === true, 'TMA 自動登入');
    assert.ok(s.count('/api/user/telegram-login') >= 1, '要打 telegram-login');
    assert.equal(s.AM.isLoggedIn(), true, 'TMA 自動登入後是已登入');
    assert.equal(await s.AM.init(), true);
    const meAfterLogin = s.count('/api/user/me');
    const refreshAfterLogin = s.count('/api/user/refresh');
    await s.api.get('/api/a');
    await s.api.get('/api/b');
    assert.equal(s.count('/api/user/me'), meAfterLogin, '登入完成後的請求不再重跑 init');
    assert.equal(s.count('/api/user/refresh'), refreshAfterLogin);
}

console.error('auth_init_once: ok');
