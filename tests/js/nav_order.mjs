// 功能選單自訂順序（2026-10-01 DANNY：想把「好友」挪到「聊天」旁邊）。看守：
//   1. 沒排過的人：照 NAV_ITEMS 的預設順序（上線不洗牌）
//   2. 排過：照 order；之後才開的接在後面（照預設順序）；關掉的不出現
//   3. setItemOrder 只收認得的 id、去重；存進 localStorage、重新載入還在
//   4. order 壞掉（不是陣列）：丟掉、回預設順序，其他偏好不重置
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
const store = new Map();
globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
};

const { NAV_ITEMS, NavPreferences } = await import(await loadModuleUrl('web/js/nav-config.js'));
const reload = () => {
    NavPreferences._cache = null;
    return NavPreferences.getEnabledItems().map((i) => i.id);
};
const save = (prefs) => {
    store.set(NavPreferences.STORAGE_KEY, JSON.stringify({ version: NavPreferences.PREFERENCES_VERSION, ...prefs }));
    return reload();
};
const defaultOrder = (ids) => NAV_ITEMS.map((i) => i.id).filter((id) => ids.includes(id));
const storedEnabled = () => JSON.parse(store.get(NavPreferences.STORAGE_KEY)).enabledItems;
// 固定項（設定、後台）每次載入都會補回去，沒排到的接在最後
const lockedTail = () => defaultOrder(storedEnabled()).filter((id) => NAV_ITEMS.find((i) => i.id === id).locked || NAV_ITEMS.find((i) => i.id === id).adminOnly).filter((id) => id !== 'chat');

// ── 1) 沒排過：預設順序（enabledItems 的存放順序不影響） ──
const enabled = ['forum', 'chat', 'friends', 'market'];
assert.deepEqual(save({ enabledItems: enabled }), defaultOrder(storedEnabled()));

// ── 2～3) 排過 ──
assert.equal(NavPreferences.setItemOrder(['friends', 'chat', 'friends', 'bogus', 'market', 'forum']), true);
assert.deepEqual(JSON.parse(store.get(NavPreferences.STORAGE_KEY)).order, ['friends', 'chat', 'market', 'forum'], '去重、丟掉不認得的');
assert.deepEqual(reload(), ['friends', 'chat', 'market', 'forum', ...lockedTail()], '重新載入照自訂順序，沒排到的固定項接在後面');

// 之後才開的：接在後面
assert.equal(NavPreferences.setItemEnabled('journal', true), true);
assert.deepEqual(reload().filter((id) => !lockedTail().includes(id)), ['friends', 'chat', 'market', 'forum', 'journal'], '之後才開的接在後面');
// 關掉的不出現、其他順序不變
assert.equal(NavPreferences.setItemEnabled('market', false), true);
assert.deepEqual(reload().filter((id) => !lockedTail().includes(id)), ['friends', 'chat', 'forum', 'journal']);

// ── 4) order 壞掉 ──
assert.deepEqual(save({ enabledItems: enabled, order: 'friends,chat' }), defaultOrder(storedEnabled()), '壞掉的 order 丟掉、回預設順序');
assert.ok(enabled.every((id) => storedEnabled().includes(id)), '啟用項不重置');
assert.equal(JSON.parse(store.get(NavPreferences.STORAGE_KEY)).order, undefined);

// applyOrder 本身：空 order 原樣
const items = NAV_ITEMS.filter((i) => enabled.includes(i.id));
assert.equal(NavPreferences.applyOrder(items, []), items);

console.log('nav_order: OK');
