// 側欄功能選單顯示項數：至少 3 項、最多 6 項，固定的「對話」「設定」也算在內
// （DANNY 2026-09-25：太多會亂）。固定項關不掉時要回報 'locked'，不能冒充「至少 3 個」。
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
const visible = () =>
    NavPreferences.getEnabledItems().filter((i) => !i.adminOnly).length;

assert.equal(NavPreferences.MIN_ENABLED_ITEMS, 3);
assert.equal(NavPreferences.MAX_ENABLED_ITEMS, 6);

NavPreferences.resetToDefaults();
assert.ok(visible() <= 6, `預設不能超過上限（實際 ${visible()}）`);

// 1. 點固定項（設定）要關 → 'locked'，不是 'min'
assert.equal(NavPreferences.blockReason('settings', false), 'locked');
assert.equal(NavPreferences.setItemEnabled('settings', false), false);
assert.ok(NavPreferences.isItemEnabled('settings'));

// 2. 開到 6 項後第 7 項被擋 → 'max'
const selectable = NAV_ITEMS.filter((i) => !i.hidden && !i.locked && !i.adminOnly);
for (const item of selectable) {
    if (visible() >= 6) break;
    assert.equal(NavPreferences.setItemEnabled(item.id, true), true, `開 ${item.id}`);
}
assert.equal(visible(), 6);
const extra = selectable.find((i) => !NavPreferences.isItemEnabled(i.id));
assert.ok(extra, '要有一個還沒開的項目才測得出上限');
assert.equal(NavPreferences.blockReason(extra.id, true), 'max');
assert.equal(NavPreferences.setItemEnabled(extra.id, true), false);
assert.equal(visible(), 6);

// 3. 關到剩 3 項後再關 → 'min'
for (const item of selectable) {
    if (visible() <= 3) break;
    if (NavPreferences.isItemEnabled(item.id)) NavPreferences.setItemEnabled(item.id, false);
}
assert.equal(visible(), 3);
const last = NavPreferences.getEnabledItems().find((i) => !i.locked && !i.adminOnly);
assert.equal(NavPreferences.blockReason(last.id, false), 'min');
assert.equal(NavPreferences.setItemEnabled(last.id, false), false);

// 4. 已存的偏好超過上限（09-24~25 曾無上限）→ 載入時修剪：固定項保留、其餘照原順序留到上限
const tooMany = ['chat', 'settings', ...selectable.map((i) => i.id)];
store.set(
    NavPreferences.STORAGE_KEY,
    JSON.stringify({ version: NavPreferences.PREFERENCES_VERSION, enabledItems: tooMany })
);
NavPreferences._cache = null;
assert.equal(visible(), 6);
assert.ok(NavPreferences.isItemEnabled('chat') && NavPreferences.isItemEnabled('settings'));
assert.ok(NavPreferences.isItemEnabled(selectable[0].id), '照原順序保留前面的');

console.log('nav limits tests passed');
