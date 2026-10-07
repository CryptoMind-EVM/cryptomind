// 訪客固定選單（2026-09-27 上市準備 PR-2／PR-3，design §3）：
// 訪客沒有偏好可言，localStorage 殘留的舊設定（例如把論壇帶回來）一律不讀；
// 論壇對訪客隱藏，但登入用戶的預設（2026-09-26 論壇預設顯示）與偏好版本都不動；
// Sample portfolio 只給訪客，登入用戶的導覽與 Customize 都看不到。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
const store = new Map();
globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
};

const moduleUrl = await loadModuleUrl('web/js/nav-config.js');
const { NAV_ITEMS, NavPreferences } = await import(moduleUrl);
const byId = Object.fromEntries(NAV_ITEMS.map((i) => [i.id, i]));

// ---- 設定本身 ----
assert.ok(byId.sample, 'NAV_ITEMS 要有 sample');
assert.equal(byId.sample.hidden, true, 'sample 對登入用戶的導覽／Customize 隱藏');
assert.equal(byId.sample.guestOnly, true, 'sample 只給訪客');
assert.notEqual(byId.sample.disabled, true, 'sample 不能 disabled（訪客要到得了）');
assert.notEqual(byId.forum.guestAllowed, true, '論壇對訪客隱藏');
assert.equal(byId.forum.defaultEnabled, true, '登入用戶的論壇預設不變');
assert.equal(NavPreferences.PREFERENCES_VERSION, 22, '不 bump 偏好版本（會重置所有人的自訂）');

// ---- 訪客固定順序（Scam check 2026-09-27 起是 SPA 分頁，不再是外部連結） ----
const guestIds = () => NavPreferences.getGuestItems().map((i) => i.id);
assert.deepEqual(guestIds(), ['chat', 'sample', 'market', 'scamcheck']);
for (const item of NavPreferences.getGuestItems()) {
    assert.equal(item.href, undefined, `${item.id} 不能是外部連結`);
    assert.ok(byId[item.id], `${item.id} 要是 NAV_ITEMS 裡的分頁`);
}
assert.equal(byId.scamcheck.i18nKey, 'nav.scamcheck');
assert.equal(byId.scamcheck.guestAllowed, true);
assert.equal(byId.scamcheck.guestAlways, true, '公開查詢不受 GUEST_DATA_ACCESS 關閉影響');
assert.notEqual(byId.scamcheck.hidden, true, '登入用戶的 Customize 看得到');
assert.equal(byId.scamcheck.defaultEnabled, false, '登入用戶預設不佔導覽格');

// localStorage 殘留什麼都不影響訪客選單
store.set(
    NavPreferences.STORAGE_KEY,
    JSON.stringify({ version: 22, enabledItems: ['chat', 'forum', 'journal', 'settings'] })
);
NavPreferences._cache = null;
assert.deepEqual(guestIds(), ['chat', 'sample', 'market', 'scamcheck']);

// ---- 「更多分頁」：其餘開放訪客的分頁，不含固定項、論壇、隱藏項 ----
const moreIds = NavPreferences.getGuestMoreItems().map((i) => i.id);
assert.ok(!moreIds.includes('twstock'), '台股併進市場子分頁，不在更多分頁');
for (const id of ['hkstock', 'jpstock', 'krstock', 'astock', 'commodity', 'forex']) {
    assert.ok(!moreIds.includes(id), `${id} 併進市場子分頁，不在更多分頁`);
}
// 這些市場訪客照舊唯讀可看：只是入口換成市場的子分頁列
for (const id of ['hkstock', 'jpstock', 'krstock', 'astock', 'commodity', 'forex']) {
    assert.equal(byId[id].guestAllowed, true, `${id} 訪客可看`);
    assert.equal(NavPreferences.groupOf(id), 'market');
}
for (const id of ['chat', 'sample', 'market', 'crypto', 'usstock', 'twstock', 'scamcheck', 'forum', 'journal', 'settings', 'instock']) {
    assert.ok(!moreIds.includes(id), `更多分頁不該有 ${id}（實際 ${moreIds}）`);
}
for (const id of moreIds) {
    assert.equal(byId[id].guestAllowed, true, `${id} 要開放訪客才放更多分頁`);
}

// ---- 登入用戶：sample 永遠看不到，預設照舊 ----
store.clear();
NavPreferences._cache = null;
const fresh = NavPreferences.getEnabledItems().map((i) => i.id);
assert.deepEqual(fresh, ['chat', 'market', 'journal', 'forum', 'admin', 'settings']);

store.set(
    NavPreferences.STORAGE_KEY,
    JSON.stringify({ version: 22, enabledItems: ['chat', 'sample', 'crypto', 'forum', 'settings'] })
);
NavPreferences._cache = null;
const withSample = NavPreferences.getEnabledItems().map((i) => i.id);
assert.ok(!withSample.includes('sample'), '偏好殘留 sample 也不能出現在登入導覽');
assert.equal(NavPreferences.setItemEnabled('sample', true), false, 'Customize 不能開 sample');

// scamcheck：登入用戶可以從 Customize 開，開了就出現在導覽
store.set(
    NavPreferences.STORAGE_KEY,
    JSON.stringify({ version: 22, enabledItems: ['chat', 'crypto', 'forum', 'settings'] })
);
NavPreferences._cache = null;
assert.equal(NavPreferences.setItemEnabled('scamcheck', true), true, 'Customize 可以開 scamcheck');
assert.ok(NavPreferences.getEnabledItems().some((i) => i.id === 'scamcheck'));

console.log('guest nav tests passed');
