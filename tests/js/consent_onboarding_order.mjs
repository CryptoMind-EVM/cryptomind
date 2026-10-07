// 同意卡（legal-consent.js，fixed 浮在輸入列上方）與新手三步清單（onboarding-checklist.js，行內卡片）
// 在桌機會落在同一塊區域，同意卡蓋住清單下半（2026-10-06 截圖）。同意卡是必要的（要留同意紀錄），
// 所以清單讓路：同意前不顯示、先畫了就收起，同意後再出現；使用者按過關閉的不復活。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

function makeClassList() {
    const classes = new Set();
    return {
        add: (c) => classes.add(c),
        remove: (c) => classes.delete(c),
        contains: (c) => classes.has(c),
        toggle: (c, force) => {
            const on = force === undefined ? !classes.has(c) : !!force;
            if (on) classes.add(c);
            else classes.delete(c);
            return on;
        },
    };
}

// ---- 假 DOM ----
const registry = {};
const consentCard = { classList: makeClassList(), textContent: '' };
consentCard.classList.add('hidden');
registry['legal-consent-card'] = consentCard;
registry['legal-consent-error'] = { classList: makeClassList(), textContent: '' };
registry['legal-consent-accept'] = { classList: makeClassList(), disabled: false };

const welcomeRoot = {
    insertAdjacentHTML(_pos, html) {
        assert.ok(html.includes('id="onboarding-checklist"'));
        registry['onboarding-checklist'] = { remove: () => delete registry['onboarding-checklist'] };
    },
};
registry['welcome-greeting'] = { closest: () => welcomeRoot };

const listeners = {};
globalThis.window = globalThis;
globalThis.addEventListener = (type, fn) => (listeners[type] ||= []).push(fn);
globalThis.dispatchEvent = (ev) => {
    (listeners[ev.type] || []).forEach((fn) => fn(ev));
    return true;
};
globalThis.document = { getElementById: (id) => registry[id] || null };
globalThis.I18n = { t: (k) => k };
globalThis.AppUtils = { refreshIcons() {} };
const store = new Map();
globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
};

const AM = {
    currentUser: null,
    isLoggedIn: () => true,
    _mergeCurrentUser(patch) {
        this.currentUser = { ...(this.currentUser || {}), ...(patch || {}) };
        return this.currentUser;
    },
};
globalThis.AuthManager = AM;
globalThis.AppAPI = {
    get: async (url) => {
        if (url === '/api/user/onboarding-status') {
            return { status: { holding: false, brief: false, call: false, telegram_bound: false } };
        }
        throw new Error('unexpected GET ' + url);
    },
    post: async () => ({ success: true }),
};

const { LegalConsent } = await import(await loadModuleUrl('web/js/legal-consent.js'));
const { mountOnboardingChecklist, OnboardingChecklist } = await import(
    await loadModuleUrl('web/js/onboarding-checklist.js')
);
const fire = (isLoggedIn) =>
    (listeners['auth:changed'] || []).forEach((fn) => fn({ detail: { isLoggedIn } }));
const flush = () => new Promise((r) => setTimeout(r, 0));
const hasChecklist = () => !!registry['onboarding-checklist'];

// 1. 未同意：同意卡顯示時，清單不畫（不重疊）
AM.currentUser = { user_id: 'evm_1', legal: { version: 'v1', accepted: false } };
fire(true);
assert.equal(LegalConsent.isPending(), true);
await mountOnboardingChecklist();
assert.equal(hasChecklist(), false, '同意卡還在，清單要讓路');

// 2. 按同意：同意卡收起 → 清單出現
await LegalConsent.accept();
await flush();
await flush();
assert.equal(LegalConsent.isPending(), false);
assert.equal(hasChecklist(), true, '同意後清單要出現');

// 3. 清單先畫了（還不知道要不要同意），之後才發現未同意 → 清單收起
delete registry['onboarding-checklist'];
AM.currentUser = { user_id: 'evm_2' }; // 剛登入：沒有 legal 欄位
await mountOnboardingChecklist();
assert.equal(hasChecklist(), true);
AM._mergeCurrentUser({ legal: { version: 'v1', accepted: false } });
fire(true);
assert.equal(hasChecklist(), false, '發現要同意時，已畫出的清單要收起來');

// 4. 使用者按過關閉：同意後也不復活
OnboardingChecklist.dismiss();
await LegalConsent.accept();
await flush();
await flush();
assert.equal(hasChecklist(), false, '已關閉的清單不能因為同意而復活');

console.log('consent_onboarding_order ok');
