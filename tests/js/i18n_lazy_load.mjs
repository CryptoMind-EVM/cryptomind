// i18n.js：只下載用得到的語言檔、切換時才補抓；登入事件只查一次 /api/user/me（2026-09-26）。
// 以前一開頁就抓四種語言（壓縮後約 230 KB，每頁重抓），一頁還會為了語言偏好打 3 次 /api/user/me。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

// ---- 瀏覽器環境替身 ----
const bus = new EventTarget();
globalThis.window = globalThis;
globalThis.addEventListener = bus.addEventListener.bind(bus);
globalThis.dispatchEvent = bus.dispatchEvent.bind(bus);
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-TW' }, configurable: true });
const store = {};
globalThis.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
};
globalThis.document = { readyState: 'complete', querySelectorAll: () => [], documentElement: {} };

const fetched = [];
globalThis.fetch = async (url) => {
    fetched.push(url.replace(/\?.*$/, '').replace('/static/js/i18n/', ''));
    return { json: async () => ({ greeting: url }) };
};

// 最小的 i18next 替身：只實作 i18n.js 用到的部分
const bundles = {};
const listeners = {};
globalThis.i18next = {
    language: null,
    async init({ lng, resources }) {
        Object.entries(resources).forEach(([l, r]) => { bundles[l] = r.translation; });
        this.language = lng;
    },
    t: (k) => k,
    on(ev, cb) { (listeners[ev] = listeners[ev] || []).push(cb); },
    hasResourceBundle: (l) => l in bundles,
    addResourceBundle: (l, _ns, res) => { bundles[l] = res; },
    async changeLanguage(l) {
        this.language = l;
        (listeners.languageChanged || []).forEach((cb) => cb(l));
    },
};

let meCalls = 0;
let meResponse = async () => ({ user: { language: 'zh-TW' } });
globalThis.AppAPI = {
    get: async (url) => { assert.equal(url, '/api/user/me'); meCalls += 1; return meResponse(); },
    put: async () => ({}),
};
globalThis.AuthManager = { currentUser: null };

await import(await loadModuleUrl('web/js/i18n.js'));
const tick = () => new Promise((r) => setTimeout(r, 20));

// 1. 初始化只抓目前語言（zh-TW）＋後備 en
await window.I18n.init();
assert.deepEqual(fetched.sort(), ['en.json', 'zh-TW.json']);
assert.equal(window.I18n.getLanguage(), 'zh-TW');

// 2. 切到還沒載入的語言才補抓，而且只抓一次
await window.I18n.changeLanguage('ru');
assert.equal(window.I18n.getLanguage(), 'ru');
assert.ok(fetched.includes('ru.json'));
await window.I18n.changeLanguage('zh-TW');
await window.I18n.changeLanguage('ru');
assert.equal(fetched.filter((f) => f === 'ru.json').length, 1, 'ru.json 只能抓一次');
assert.equal(fetched.length, 3);
await window.I18n.changeLanguage('zh-TW');

// 3. 同一個使用者：auth:ready ×2 ＋ auth:initialized 只查一次 /api/user/me
window.AuthManager.currentUser = { user_id: 'u1' };
['auth:ready', 'auth:ready', 'auth:initialized'].forEach((ev) => dispatchEvent(new Event(ev)));
await tick();
assert.equal(meCalls, 1, `同一頁應該只打 1 次 /api/user/me，實際 ${meCalls} 次`);

// 4. 換帳號要重查；伺服器語言是還沒載入的 zh-CN → 先補抓再切
meResponse = async () => ({ user: { language: 'zh-CN' } });
window.AuthManager.currentUser = { user_id: 'u2' };
dispatchEvent(new Event('auth-success'));
await tick();
assert.equal(meCalls, 2);
assert.ok(fetched.includes('zh-CN.json'));
assert.equal(window.I18n.getLanguage(), 'zh-CN');
assert.equal(store.selectedLanguage, 'zh-CN');

// 5. 查詢失敗不能卡死：下一個事件要能重試
meResponse = async () => { throw new Error('network down'); };
window.AuthManager.currentUser = { user_id: 'u3' };
dispatchEvent(new Event('auth:ready'));
await tick();
assert.equal(meCalls, 3);
meResponse = async () => ({ user: { language: 'zh-CN' } });
dispatchEvent(new Event('auth:ready'));
await tick();
assert.equal(meCalls, 4, '失敗後下一個事件要重試');
dispatchEvent(new Event('auth:ready'));
await tick();
assert.equal(meCalls, 4, '成功後就不再重查');

// 6. auth 還原 session 時已經拿到 /api/user/me：直接用那份，不再打第二次
window.AuthManager.currentUser = { user_id: 'u5' };
window.AuthManager._sessionUserFromBackend = { user_id: 'u5', language: 'en' };
dispatchEvent(new Event('auth:ready'));
await tick();
assert.equal(meCalls, 4, 'auth 已經查過同一個使用者，i18n 不該再打 /api/user/me');
assert.equal(window.I18n.getLanguage(), 'en');
// 資料是別的使用者的（換帳號）→ 照樣自己查
window.AuthManager.currentUser = { user_id: 'u6' };
dispatchEvent(new Event('auth-success'));
await tick();
assert.equal(meCalls, 5);

// 7. 未登入不打 /api/user/me
window.AuthManager.currentUser = null;
dispatchEvent(new Event('auth:ready'));
await tick();
assert.equal(meCalls, 5);

console.log('i18n lazy-load tests passed');
