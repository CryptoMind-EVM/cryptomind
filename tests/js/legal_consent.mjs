// 條款改版後的同意提示卡（web/js/legal-consent.js）：
// legal.accepted=false 才顯示；按同意 → POST 目前版本 → 成功才收起；409 顯示「請重新整理」；
// 剛登入（currentUser 沒有 legal 欄位）→ 自己查一次 /me，同一個使用者只查一次。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

function makeEl() {
    const classes = new Set();
    return {
        textContent: '',
        disabled: false,
        classList: {
            add: (c) => classes.add(c),
            remove: (c) => classes.delete(c),
            contains: (c) => classes.has(c),
            toggle: (c, force) => {
                const on = force === undefined ? !classes.has(c) : !!force;
                if (on) classes.add(c);
                else classes.delete(c);
                return on;
            },
        },
    };
}

const els = {
    'legal-consent-card': makeEl(),
    'legal-consent-error': makeEl(),
    'legal-consent-accept': makeEl(),
};
els['legal-consent-card'].classList.add('hidden');
els['legal-consent-error'].classList.add('hidden');

const listeners = {};
globalThis.window = globalThis;
globalThis.addEventListener = (type, fn) => (listeners[type] ||= []).push(fn);
globalThis.document = { getElementById: (id) => els[id] || null };
globalThis.I18n = { t: (k) => 'T:' + k };

const AM = {
    currentUser: null,
    _mergeCurrentUser(patch) {
        this.currentUser = { ...(this.currentUser || {}), ...(patch || {}) };
        return this.currentUser;
    },
};
globalThis.AuthManager = AM;

const posts = [];
let meCalls = 0;
let meLegal = { version: '2026-09-27', accepted: false };
let postError = null;
globalThis.AppAPI = {
    get: async (url) => {
        assert.equal(url, '/api/user/me');
        meCalls += 1;
        return { user: { legal: meLegal } };
    },
    post: async (url, body) => {
        posts.push({ url, body });
        if (postError) throw postError;
        return { success: true };
    },
};

const { LegalConsent } = await import(await loadModuleUrl('web/js/legal-consent.js'));
const card = els['legal-consent-card'];
const fire = (isLoggedIn) =>
    (listeners['auth:changed'] || []).forEach((fn) => fn({ detail: { isLoggedIn } }));
const flush = () => new Promise((r) => setTimeout(r, 0));

// ---- 未登入：不顯示 ----
fire(false);
assert.ok(card.classList.contains('hidden'));

// ---- 已同意：不顯示；不查 /me ----
AM.currentUser = { user_id: 'evm_1', legal: { version: '2026-09-27', accepted: true } };
fire(true);
assert.ok(card.classList.contains('hidden'));
// legal=null（/me 查不到）：不顯示、也不重查
AM.currentUser = { user_id: 'evm_1', legal: null };
fire(true);
assert.ok(card.classList.contains('hidden'));
assert.equal(meCalls, 0);

// ---- 未同意：顯示 ----
AM.currentUser = { user_id: 'evm_1', legal: { version: '2026-09-27', accepted: false } };
fire(true);
assert.ok(!card.classList.contains('hidden'), '未同意要顯示提示卡');

// 409（條款剛又改版）：卡片留著、顯示請重新整理
postError = Object.assign(new Error('conflict'), { status: 409 });
await LegalConsent.accept();
assert.equal(posts.at(-1).body.version, '2026-09-27');
assert.ok(!card.classList.contains('hidden'), '失敗不能收起');
assert.equal(els['legal-consent-error'].textContent, 'T:auth.legalConsent.outdated');
assert.equal(els['legal-consent-accept'].disabled, false, '失敗後按鈕要能再按');

// 其他錯誤：一般失敗訊息
postError = Object.assign(new Error('boom'), { status: 500 });
await LegalConsent.accept();
assert.equal(els['legal-consent-error'].textContent, 'T:auth.legalConsent.failed');

// 成功：收起、currentUser 記成已同意、錯誤訊息清掉
postError = null;
await LegalConsent.accept();
assert.equal(posts.at(-1).url, '/api/user/legal/accept');
assert.ok(card.classList.contains('hidden'), '同意後收起');
assert.equal(AM.currentUser.legal.accepted, true);
assert.ok(els['legal-consent-error'].classList.contains('hidden'));
fire(true);
assert.ok(card.classList.contains('hidden'), '之後的 auth:changed 不會再跳出來');

// ---- 剛登入（沒有 legal 欄位）：自己查 /me 一次 ----
AM.currentUser = { user_id: 'evm_2' };
meLegal = { version: '2026-09-27', accepted: false };
fire(true);
fire(true); // 同一頁多次 auth:changed 只查一次
await flush();
await flush();
assert.equal(meCalls, 1, '同一個使用者只查一次 /me');
assert.deepEqual(AM.currentUser.legal, meLegal);
assert.ok(!card.classList.contains('hidden'), '查到未同意 → 顯示');

// 查詢期間換了帳號：舊結果不能套到新帳號
AM.currentUser = { user_id: 'evm_3' };
meLegal = { version: '2026-09-27', accepted: false };
fire(true);
AM.currentUser = { user_id: 'evm_4', legal: { version: '2026-09-27', accepted: true } };
await flush();
await flush();
assert.equal(AM.currentUser.user_id, 'evm_4');
assert.equal(AM.currentUser.legal.accepted, true, '不能被 evm_3 的結果蓋掉');

console.log('legal_consent ok');
