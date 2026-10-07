// 導覽整理成 AI 助理／市場／社群（2026-10-05，docs/plans/2026-10-04-growth-handoff.md 任務 B）：
// 加密貨幣、美股、台股併成「市場」一個導覽項（內部子分頁），加密貨幣可用旗標關閉。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
const store = new Map();
globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
};

const { NAV_ITEMS, NavPreferences, MARKET_TABS } = await import(await loadModuleUrl('web/js/nav-config.js'));
const byId = Object.fromEntries(NAV_ITEMS.map((i) => [i.id, i]));
const reset = (prefs) => {
    NavPreferences._cache = null;
    NavPreferences.setCapabilities({});
    delete globalThis.CMPlatform;
    if (prefs) store.set(NavPreferences.STORAGE_KEY, JSON.stringify(prefs));
    else store.delete(NavPreferences.STORAGE_KEY);
};

// ── 1. 設定本身 ─────────────────────────────────────────────────────────────
assert.deepEqual(
    MARKET_TABS,
    ['usstock', 'twstock', 'crypto', 'hkstock', 'jpstock', 'krstock', 'astock', 'commodity', 'forex'],
    '子分頁順序：美股、台股、加密貨幣在前（對外定位），港日韓陸股、商品、外匯接在後面'
);
assert.equal(byId.market.i18nKey, 'nav.marketHub', '導覽用 marketHub；nav.market 是各市場面板內的「行情」子標籤');
assert.equal(byId.market.defaultEnabled, true);
assert.equal(byId.market.guestAllowed, true);
for (const id of MARKET_TABS) {
    assert.equal(byId[id].hidden, true, `${id} 不再是獨立導覽項（併進市場）`);
    assert.equal(byId[id].group, 'market');
    assert.equal(byId[id].guestAllowed, true, `${id} 深連結／子分頁訪客照舊唯讀可看`);
}
assert.equal(byId.crypto.flag, 'crypto_tab');
// 2026-10-05 稍晚併進來的六個市場：同樣不佔導覽項、深連結照舊、訪客唯讀可看
for (const id of ['hkstock', 'jpstock', 'krstock', 'astock', 'commodity', 'forex']) {
    assert.equal(byId[id].hidden, true, `${id} 併進市場，不再是獨立導覽項／Customize 選項`);
    assert.equal(byId[id].group, 'market');
    assert.equal(byId[id].guestAllowed, true);
    assert.equal(byId[id].defaultEnabled, false);
}
assert.equal(byId.instock.hidden, true, '印股仍是暫停開放的隱藏項');
assert.equal(byId.instock.group, undefined, '印股不併入市場');
assert.equal(byId.chat.i18nKey, 'nav.chat');
assert.equal(NavPreferences.PREFERENCES_VERSION, 22, '不 bump 偏好版本（會重置所有人的自訂）');

// ── 2. 新使用者預設：AI 助理、市場、帳本、社群、設定 ─────────────────────────
reset(null);
assert.deepEqual(
    NavPreferences.getEnabledItems()
        .filter((i) => !i.adminOnly)
        .map((i) => i.id),
    ['chat', 'market', 'journal', 'forum', 'settings']
);

// ── 3. 舊偏好遷移：有任一市場分頁 → 換成 market；不重置、不 bump、可重複執行 ─
reset({ version: 22, enabledItems: ['chat', 'crypto', 'twstock', 'journal', 'settings'], order: ['chat', 'journal', 'twstock', 'crypto'] });
let prefs = NavPreferences.loadPreferences();
assert.deepEqual(
    prefs.enabledItems.filter((id) => id !== 'admin').sort(), // admin 是 locked 項，載入時一律補上
    ['chat', 'journal', 'market', 'settings']
);
assert.deepEqual(prefs.order, ['chat', 'journal', 'market'], '排序裡第一個市場分頁的位置換成 market、其餘市場分頁拿掉');
assert.equal(prefs.version, 22);
NavPreferences._cache = null;
prefs = NavPreferences.loadPreferences();
assert.equal(prefs.enabledItems.filter((id) => id === 'market').length, 1, '重複載入不會加兩次');

// 舊偏好裡開的是港股／日股／商品之類的獨立分頁 → 一樣換成 market（不會憑空多一格，也不會留下殘影）
reset({ version: 22, enabledItems: ['chat', 'jpstock', 'commodity', 'journal', 'settings'], order: ['chat', 'journal', 'jpstock', 'commodity'] });
prefs = NavPreferences.loadPreferences();
assert.deepEqual(
    prefs.enabledItems.filter((id) => id !== 'admin').sort(),
    ['chat', 'journal', 'market', 'settings']
);
assert.deepEqual(prefs.order, ['chat', 'journal', 'market']);

// 使用者本來就關掉所有市場分頁 → 不替他加回來
reset({ version: 22, enabledItems: ['chat', 'journal', 'forum', 'settings'] });
assert.ok(!NavPreferences.loadPreferences().enabledItems.includes('market'));

// 使用者關掉 market 之後，不會被再次遷移加回（市場分頁已從偏好移除）
reset({ version: 22, enabledItems: ['chat', 'usstock', 'journal', 'settings'] });
NavPreferences.loadPreferences();
NavPreferences.setItemEnabled('market', false);
NavPreferences._cache = null;
assert.ok(!NavPreferences.loadPreferences().enabledItems.includes('market'));

// ── 4. 訪客固定選單 ─────────────────────────────────────────────────────────
reset(null);
assert.deepEqual(NavPreferences.getGuestItems().map((i) => i.id), ['chat', 'sample', 'market', 'scamcheck']);

// ── 5. 市場子分頁與群組 ─────────────────────────────────────────────────────
assert.deepEqual(NavPreferences.marketMembers().map((i) => i.id), MARKET_TABS);
assert.equal(NavPreferences.groupOf('crypto'), 'market');
assert.equal(NavPreferences.groupOf('twstock'), 'market');
assert.equal(NavPreferences.groupOf('chat'), null);
for (const id of ['hkstock', 'jpstock', 'krstock', 'astock', 'commodity', 'forex']) {
    assert.equal(NavPreferences.groupOf(id), 'market', `${id} 也併進市場子分頁`);
}
assert.equal(NavPreferences.groupOf('instock'), null, '印股暫停開放，不併入');
assert.equal(NavPreferences.resolveMarketTab(), 'usstock', '沒記錄過 → 第一個子分頁');
assert.equal(NavPreferences.resolveMarketTab('crypto'), 'crypto', '記得上次用的子分頁');
assert.equal(NavPreferences.resolveMarketTab('chat'), 'usstock', '亂寫的值退回第一個');

// ── 6. 旗標關閉加密貨幣：每個入口都沒有 ─────────────────────────────────────
reset(null);
NavPreferences.setCapabilities({ crypto_tab: false, wallet_login: true });
assert.equal(NavPreferences.isFlagOff('crypto_tab'), true);
assert.equal(NavPreferences.isFlagOff('wallet_login'), false);
assert.equal(NavPreferences.isUnavailable(byId.crypto), true, '深連結／switchTab 要擋');
assert.equal(NavPreferences.isUnavailable(byId.twstock), false, '其餘市場照常');
assert.deepEqual(
    NavPreferences.marketMembers().map((i) => i.id),
    MARKET_TABS.filter((id) => id !== 'crypto'),
    '子分頁列沒有加密貨幣，其餘照舊'
);
assert.equal(NavPreferences.resolveMarketTab('crypto'), 'usstock', '上次停在加密貨幣、旗標關了 → 退回第一個可用子分頁');
for (const item of [...NavPreferences.getEnabledItems(), ...NavPreferences.getGuestItems(), ...NavPreferences.getGuestMoreItems()]) {
    assert.notEqual(item.id, 'crypto', '導覽、訪客選單、更多分頁都不能出現 crypto');
}
NavPreferences.setCapabilities({ crypto_tab: true });
assert.equal(NavPreferences.isUnavailable(byId.crypto), false, '旗標重新打開即復原');

// Play 版：設定檔回來前就同步關（不閃一下）
reset(null);
globalThis.CMPlatform = { isPlay: () => true };
assert.equal(NavPreferences.isUnavailable(byId.crypto), true);
assert.ok(!NavPreferences.marketMembers().some((i) => i.id === 'crypto'));
assert.equal(NavPreferences.marketMembers()[0].id, 'usstock');
delete globalThis.CMPlatform;

// 旗標快取：設定檔還沒回來（或取不到）時先照上次的，不會露出被關掉的分頁
reset(null);
NavPreferences.setCapabilities({ crypto_tab: false }, true);
assert.deepEqual(JSON.parse(store.get(NavPreferences.CAPS_STORAGE_KEY)), { crypto_tab: false });
NavPreferences.setCapabilities({}); // 模擬重新開站：記憶體旗標清空
assert.equal(NavPreferences.isFlagOff('crypto_tab'), false);
NavPreferences.loadCachedCapabilities();
assert.equal(NavPreferences.isFlagOff('crypto_tab'), true, '開站先照上次記下的旗標');
store.set(NavPreferences.CAPS_STORAGE_KEY, '{壞掉');
NavPreferences.setCapabilities({});
NavPreferences.loadCachedCapabilities(); // 壞掉的快取當作沒有，不丟錯
assert.equal(NavPreferences.isFlagOff('crypto_tab'), false);
store.delete(NavPreferences.CAPS_STORAGE_KEY);

// 回應不是物件（錯誤形狀、undefined）→ 忽略，不把已經設好的旗標清掉；非布林值不收
NavPreferences.setCapabilities({ crypto_tab: false });
NavPreferences.setCapabilities(undefined);
NavPreferences.setCapabilities('oops');
assert.equal(NavPreferences.isFlagOff('crypto_tab'), true);
NavPreferences.setCapabilities({ crypto_tab: 'no', other: 0 });
assert.equal(NavPreferences.isFlagOff('crypto_tab'), false, '只認布林 false');
NavPreferences.setCapabilities({});

// 既有的 disabled 下架機制照舊（discover／studio）
assert.equal(NavPreferences.isUnavailable(byId.discover), true);
assert.equal(NavPreferences.isUnavailable(byId.chat), false);

// ── 7. 子分頁列 HTML（market-hub.js 純函式）──────────────────────────────────
{
    const { renderMarketBar } = await import(await loadModuleUrl('web/js/market-hub.js'));
    const t = (key, fallback) => (key === 'nav.twstock' ? '台股<script>' : fallback);
    NavPreferences.setCapabilities({});
    const full = renderMarketBar('twstock', NavPreferences.marketMembers(), t);
    for (const id of MARKET_TABS) {
        assert.ok(full.includes(`data-market-tab="${id}"`), `子分頁列要有 ${id}`);
        assert.ok(full.includes(`data-click="switchTab" data-click-arg="${id}"`), `${id} 走 switchTab`);
    }
    const positions = MARKET_TABS.map((id) => full.indexOf(`data-market-tab="${id}"`));
    assert.deepEqual(positions, [...positions].sort((a, b) => a - b), '順序照 MARKET_TABS：美股、台股、加密貨幣、其餘市場');
    // 九個子分頁在手機放不下：整列可橫向捲動、按鈕不折行不縮
    assert.ok(/data-market-subtabs[^>]*overflow-x-auto/.test(full), '子分頁列要能橫向捲動');
    assert.equal((full.match(/whitespace-nowrap/g) || []).length, MARKET_TABS.length, '每顆按鈕不折行');
    assert.ok(!/class="flex-1 /.test(full), '不能用 flex-1：basis 0 會被全站 button min-width 36px 壓扁');
    assert.equal((full.match(/shrink-0/g) || []).length, MARKET_TABS.length, '每顆按鈕不縮');
    assert.equal((full.match(/aria-selected="true"/g) || []).length, 1, '只有目前這個高亮');
    assert.ok(/data-market-tab="twstock"[^>]*>|aria-selected="true" data-market-tab="twstock"/.test(full));
    assert.ok(!full.includes('<script>'), '標籤要跳脫');
    assert.ok(full.includes('台股&lt;script&gt;'));

    NavPreferences.setCapabilities({ crypto_tab: false });
    const noCrypto = renderMarketBar('usstock', NavPreferences.marketMembers(), t);
    assert.ok(!noCrypto.includes('crypto'), '旗標關閉：子分頁列沒有加密貨幣');
    assert.ok(noCrypto.includes('data-market-tab="usstock"') && noCrypto.includes('data-market-tab="twstock"'));
    assert.ok(noCrypto.includes('data-market-tab="jpstock"'), '其他市場不受加密貨幣旗標影響');
}

console.error('nav_market: ok');
process.exit(0);
