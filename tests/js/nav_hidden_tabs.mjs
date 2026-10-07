// nav 下架板塊測試（2026-09-02 DANNY：Manifund 相關 Discover 與提案工作台
// Studio 在 Phase 2 自製贊助平台上線前要「看不到也不能選」）。
// 語意分離（spa.js switchTab 深防依據 disabled，不是 hidden）：
//   hidden:   不顯示於 nav/Customize，但程式/deep-link 仍可達（如 ai-studio，
//             入口在設定頁「我的 AI」卡）
//   disabled: 下架——連 deep-link／switchTab 都導回 chat（discover/studio）
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

// nav-config.js 底層會掛 window.*——Node 環境先備好
globalThis.window = globalThis;

const moduleUrl = await loadModuleUrl('web/js/nav-config.js');
const { NAV_ITEMS, NavPreferences } = await import(moduleUrl);

const byId = Object.fromEntries(NAV_ITEMS.map((i) => [i.id, i]));

// ---- discover / studio 必須下架（hidden + disabled）----
assert.equal(byId.discover?.hidden, true, 'Discover 板塊要隱藏（hidden）');
assert.equal(byId.discover?.disabled, true, 'Discover 板塊要下架（disabled）');
assert.equal(byId.studio?.hidden, true, 'Studio（提案工作台）板塊要隱藏（hidden）');
assert.equal(byId.studio?.disabled, true, 'Studio 板塊要下架（disabled）');

// ---- ai-studio 改為一般可自訂 tab（2026-09-02：Settings 摘要卡入口移除；
//      曾因 switchTab 誤攔 hidden 跳回 chat，現語意分離後不得下架）----
assert.notEqual(byId['ai-studio']?.hidden, true, 'ai-studio 要出現在 Customize 自訂清單');
assert.notEqual(byId['ai-studio']?.disabled, true, 'ai-studio 不得被下架');
assert.equal(byId['ai-studio']?.defaultEnabled, false, 'ai-studio 預設不佔底部導覽');

// ---- getEnabledItems 永遠濾掉 hidden，即使偏好殘留 ----
const store = new Map();
globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
};
store.set(
    'userNavPreferences',
    JSON.stringify({
        version: NavPreferences.PREFERENCES_VERSION,
        enabledItems: [
            'chat', 'crypto', 'twstock', 'usstock', 'journal',
            'settings', 'admin', 'discover', 'studio',
        ],
    })
);
NavPreferences._cache = null;

const enabledIds = NavPreferences.getEnabledItems().map((i) => i.id);
assert.ok(!enabledIds.includes('discover'), 'getEnabledItems 不得包含 discover');
assert.ok(!enabledIds.includes('studio'), 'getEnabledItems 不得包含 studio');

// ---- 舊偏好殘留會被自動清掉（loadPreferences 清洗）----
const cleaned = NavPreferences.loadPreferences().enabledItems;
assert.ok(!cleaned.includes('discover'), '殘留偏好要被清洗掉：discover');
assert.ok(!cleaned.includes('studio'), '殘留偏好要被清洗掉：studio');

// ---- 核心 tab 不受影響 ----
for (const core of ['chat', 'settings']) {
    assert.ok(enabledIds.includes(core), `核心 tab ${core} 要照常顯示`);
}

console.log('nav hidden tabs tests passed');
