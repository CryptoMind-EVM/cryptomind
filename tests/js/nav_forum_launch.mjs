// 論壇／好友開放（DANNY 2026-09-26）：論壇預設顯示並取代美股的預設位置，好友只放 Customize。
// 上限 6 格原本就被預設項占滿，v22 遷移若只是把論壇接在最後，載入時會被上限修剪掉——
// 這裡鎖定舊使用者真的看得到論壇，且不動他們自己的選擇。
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

function load(version, enabledItems) {
    store.set(NavPreferences.STORAGE_KEY, JSON.stringify({ version, enabledItems }));
    NavPreferences._cache = null;
    return NavPreferences.getEnabledItems().map((i) => i.id);
}
const visibleCount = (ids) => NavPreferences.countVisible(ids);

// ---- 設定本身 ----
assert.notEqual(byId.forum.hidden, true, '論壇要開放');
assert.equal(byId.forum.defaultEnabled, true, '論壇預設顯示');
assert.notEqual(byId.friends.hidden, true, '好友要出現在 Customize');
assert.equal(byId.friends.defaultEnabled, false, '好友不佔預設格');
assert.equal(byId.usstock.defaultEnabled, false, '美股併進市場，不再佔獨立的預設位置');

// ---- 新使用者（沒有存過偏好）----
store.clear();
NavPreferences._cache = null;
const fresh = NavPreferences.getEnabledItems().map((i) => i.id);
assert.ok(fresh.includes('forum'), '新使用者看得到論壇');
assert.ok(!fresh.includes('usstock'));
assert.ok(visibleCount(fresh) <= NavPreferences.MAX_ENABLED_ITEMS);

// ---- v21 預設使用者：論壇加進來；美股／台股／加密貨幣併成一格「市場」（2026-10-05）----
const v21Default = ['chat', 'crypto', 'twstock', 'usstock', 'journal', 'settings', 'admin'];
let ids = load(21, v21Default);
assert.ok(ids.includes('forum'), `預設使用者要看得到論壇（實際 ${ids}）`);
assert.ok(!ids.includes('usstock'), '美股不再是獨立導覽項');
for (const kept of ['chat', 'market', 'journal', 'settings']) {
    assert.ok(ids.includes(kept), `${kept} 不受影響`);
}
assert.equal(visibleCount(ids), 5);

// ---- v21 未滿 6 格：直接加論壇，美股保留，自己關掉的預設項不被加回來 ----
ids = load(21, ['chat', 'crypto', 'usstock', 'settings']);
assert.ok(ids.includes('forum'));
assert.ok(ids.includes('market'), '有市場分頁的人保留市場');
assert.ok(!ids.includes('twstock') && !ids.includes('journal'), '不可把使用者關掉的項目加回來');

// ---- v21 已滿 6 格但沒有美股：保留使用者的選擇，論壇留在 Customize ----
const custom = ['chat', 'crypto', 'twstock', 'journal', 'ai-studio', 'wallet', 'settings', 'admin']; // 商品已併進市場，改用 ai-studio 湊滿 6 格
ids = load(21, custom);
assert.ok(!ids.includes('forum'));
assert.deepEqual(
    [...ids].sort(),
    ['chat', 'market', 'journal', 'ai-studio', 'wallet', 'settings', 'admin'].sort()
);

// ---- 遷移只做一次：v22 使用者關掉論壇後不會被加回來 ----
ids = load(22, ['chat', 'crypto', 'twstock', 'usstock', 'settings']);
assert.ok(!ids.includes('forum'));

// ---- 好友可從 Customize 開 ----
ids = load(22, ['chat', 'crypto', 'forum', 'settings']);
assert.equal(NavPreferences.setItemEnabled('friends', true), true);
assert.ok(NavPreferences.isItemEnabled('friends'));

console.log('nav forum launch tests passed');
