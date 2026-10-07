/**
 * Navigation Configuration Module
 * Defines all available navigation items and default states
 */

const NAV_ITEMS = [
    {
        id: 'chat',
        icon: 'message-circle',
        label: 'Chat',
        i18nKey: 'nav.chat',
        defaultEnabled: true,
        // 2026-08-24 DANNY 確認鎖定：chat 是核心 UX（訪客模式也只有 chat），
        // 停用後「新對話」等入口會跳不到聊天面板。locked 項每次載入自動
        // 修復回 enabledItems（見 loadPreferences），先前停用的使用者自動恢復。
        locked: true,
    },
    {
        id: 'sample',
        icon: 'briefcase',
        label: 'Sample portfolio',
        i18nKey: 'nav.sample',
        defaultEnabled: false,
        // 2026-09-27 上市準備 PR-3：訪客專用的示範頁（靜態資料）。hidden＝登入用戶的導覽與
        // Customize 都不列、偏好殘留也會被清掉；訪客選單由 getGuestItems 固定列出。
        // 登入用戶 deep link #sample 由 spa.js switchTab 導到 journal。
        hidden: true,
        guestOnly: true,
        guestAllowed: true,
    },
    {
        id: 'market',
        icon: 'trending-up',
        label: 'Markets',
        i18nKey: 'nav.marketHub',
        defaultEnabled: true,
        // 2026-10-05 導覽整理成 AI 助理／市場／社群：各市場分頁（MARKET_TABS）併成這一項，
        // 內部用子分頁列切換。它不是真的面板——spa.js switchTab('market') 會換成上次用的子分頁。
        // 同日稍晚港股、日股、韓股、A 股、商品、外匯也併進來（DANNY：「統一放在一個地方」）；
        // 印股仍是暫停開放的獨立隱藏項。
        guestAllowed: true,
    },
    {
        id: 'crypto',
        icon: 'zap',
        label: 'Crypto',
        i18nKey: 'nav.crypto',
        defaultEnabled: false,
        guestAllowed: true,
        // 以下三個市場分頁是「市場」的子分頁：hidden＝不再各佔一個導覽項／Customize 不列，
        // 深連結（#crypto…）與 switchTab 照舊直達。
        hidden: true,
        group: 'market',
        // 旗標：關閉時（Play 版、CRYPTO_TAB_ENABLED=false）導覽、子分頁列、深連結、訪客選單都進不去。
        // 值來自 /api/config 的 capabilities.crypto_tab（NavPreferences.setCapabilities）
        flag: 'crypto_tab',
    },
    {
        id: 'twstock',
        icon: 'bar-chart',
        label: 'TW Stock',
        i18nKey: 'nav.twstock',
        defaultEnabled: false,
        guestAllowed: true,  // 訪客模式 Tier 1：唯讀市場數據（2026-08-27 設計）
        hidden: true,
        group: 'market',
    },
    {
        id: 'usstock',
        icon: 'trending-up',
        label: 'US Stock',
        i18nKey: 'nav.usstock',
        defaultEnabled: false,
        guestAllowed: true,  // 訪客模式 Tier 1：唯讀市場數據（2026-08-27 設計）
        hidden: true,
        group: 'market',
    },
    {
        id: 'scamcheck',
        icon: 'shield-alert',
        label: 'Scam Check',
        i18nKey: 'nav.scamcheck',
        // 2026-09-27：詐騙檢查從獨立頁 /scam-tracker/ 搬進 SPA（訪客固定選單第五項）。
        // 登入用戶不佔預設格、Customize 可開——不 bump PREFERENCES_VERSION（會重置所有人的自訂）。
        // guestAlways：公開查詢不是唯讀市場數據，GUEST_DATA_ACCESS 關掉時訪客照樣進得去
        //（以前是獨立頁，一直公開）。
        defaultEnabled: false,
        guestAllowed: true,
        guestAlways: true,
    },
    {
        id: 'journal',
        icon: 'book-open',
        label: 'Ledger',
        i18nKey: 'nav.journal',
        defaultEnabled: true,
    },
    {
        id: 'wallet',
        icon: 'credit-card',
        label: 'Wallet',
        i18nKey: 'nav.wallet',
        defaultEnabled: false,
    },
    { id: 'commodity', icon: 'bar-chart-2',      label: 'Commodity', i18nKey: 'nav.commodity', defaultEnabled: false, guestAllowed: true, hidden: true, group: 'market' },
    {
        id: 'wallet-monitor',
        icon: 'radar',
        label: 'Wallet Monitor',
        i18nKey: 'nav.walletMonitor',
        defaultEnabled: false,
        // 2026-09-12 DANNY：還沒想好怎麼給使用者用的先下架（含 Customize 選單）。
        // prod 旗標本來就關；之後以 Telegram 推播＋帳本「已驗證持倉」卡回歸，不當分頁。
        // 見 docs/plans/2026-09-12-product-inventory.md §13
        hidden: true,
    },
    { id: 'forex',     icon: 'arrow-left-right', label: 'Forex',     i18nKey: 'nav.forex',     defaultEnabled: false, guestAllowed: true, hidden: true, group: 'market' },
    {
        id: 'trust',
        icon: 'shield-check',
        label: 'Trust',
        i18nKey: 'nav.trust',
        defaultEnabled: false,
        // 2026-09-12 DANNY：同上。信任分數之後以論壇／個人頁徽章＋風控 agent 工具回歸。
        hidden: true,
    },
    { id: 'hkstock',   icon: 'landmark',         label: 'HK Stock',   i18nKey: 'nav.hkstock',   defaultEnabled: false, guestAllowed: true, hidden: true, group: 'market' },
    { id: 'astock',    icon: 'building-2',       label: 'A Share',    i18nKey: 'nav.astock',    defaultEnabled: false, guestAllowed: true, hidden: true, group: 'market' },
    { id: 'jpstock',   icon: 'sun',              label: 'JP Stock',   i18nKey: 'nav.jpstock',   defaultEnabled: false, guestAllowed: true, hidden: true, group: 'market' },
    { id: 'instock',   icon: 'flame',            label: 'India Stock', i18nKey: 'nav.instock',   defaultEnabled: false, hidden: true },  // 2026-08: 印度股暫停開放（資料源不穩 + 使用率低），隱藏保留可復原
    { id: 'krstock',   icon: 'flag',             label: 'Korea Stock', i18nKey: 'nav.krstock',   defaultEnabled: false, guestAllowed: true, hidden: true, group: 'market' },
    {
        id: 'friends',
        icon: 'users',
        label: 'Friends',
        i18nKey: 'nav.friends',
        // 2026-09-26 開放：不佔預設格，Customize 可開；主要入口是論壇作者的個人頁
        defaultEnabled: false,
    },
    {
        id: 'forum',
        icon: 'messages-square',
        label: 'Forum',
        i18nKey: 'nav.forum',
        defaultEnabled: true,  // 2026-09-26 DANNY：論壇開放並預設顯示（取代美股的預設位置）
        // 2026-09-27 DANNY：訪客選單先不放論壇（還沒有真人內容），不設 guestAllowed；
        // 登入用戶的預設顯示不變。真人文章累積約 10 篇再開放訪客看標題。
    },
    {
        id: 'discover',
        icon: 'compass',
        label: 'Discover',
        i18nKey: 'nav.discover',
        defaultEnabled: true,
        hidden: true,
        // 2026-09-02 DANNY：Manifund 相關板塊 Phase 2 自製贊助平台上線前下架。
        // hidden = 不顯示於 nav/Customize；disabled = 連 deep-link／switchTab
        // 都導回 chat（同 instock/friends 模式可復原——拿掉 disabled 即回歸）。
        disabled: true,
    },
    {
        id: 'studio',
        icon: 'pencil-ruler',
        label: 'Studio',
        i18nKey: 'nav.studio',
        defaultEnabled: true,
        hidden: true,
        disabled: true,  // 2026-09-02 DANNY：提案工作台（贊助計劃）Phase 2 前下架，語意同上
    },
    {
        id: 'ai-studio',
        icon: 'bot',
        label: 'AI Studio',
        i18nKey: 'nav.aiStudio',
        // 2026-09-02 DANNY：Settings 摘要卡入口移除，改為一般可自訂 tab
        //（defaultEnabled: false——預設不佔底部導覽 5 格，Customize 可開）
        defaultEnabled: false,
    },
    {
        id: 'connections',
        icon: 'link',
        label: 'Connections',
        i18nKey: 'nav.connections',
        // defaultEnabled: false —— 新板塊預設不去動既有使用者已經排好的選單；主要入口是 Settings 的
        // 引導卡，想放進導覽列的人再從 Customize 勾。同 ai-studio 的處理。
        // （因為預設不啟用，這裡不需要 bump PREFERENCES_VERSION —— bump 會把
        //   所有人的自訂導覽重置掉。）
        defaultEnabled: false,
        // 2026-09-12 盤點 §13：不當分頁——導覽列與 Customize 都不列；入口是 Settings
        // 的引導卡（GlobalNav.navigateToTab），deep link #connections 仍可到。
        hidden: true,
    },
    {
        id: 'admin',
        icon: 'shield',
        label: 'Admin',
        i18nKey: 'nav.admin',
        defaultEnabled: true,
        locked: true,
        adminOnly: true,
    },
    {
        id: 'settings',
        icon: 'settings-2',
        label: 'Settings',
        i18nKey: 'nav.settings',
        defaultEnabled: true,
        locked: true,
    },
];

// 訪客固定選單（2026-09-27 上市準備 design §3）：Chat → Sample portfolio → Market
// → Scam check（2026-10-05 起 Crypto／US stocks 併進 Market），其餘開放訪客的市場頁放
// 「更多分頁」。訪客沒有偏好可言，不讀 localStorage（殘留的舊設定會把論壇帶回來）。
const GUEST_NAV_ORDER = ['chat', 'sample', 'market', 'scamcheck'];

// 「市場」的子分頁，順序＝子分頁列順序：前三個是對外定位的「美股、台股、加密貨幣」，
// 其餘市場接在後面（子分頁列放不下時橫向捲動）。印股（instock）暫停開放，不在內。
const MARKET_TABS = [
    'usstock',
    'twstock',
    'crypto',
    'hkstock',
    'jpstock',
    'krstock',
    'astock',
    'commodity',
    'forex',
];

// 這些旗標在 Play 版同步就關（/api/config 回來前導覽已經畫出來，不能閃一下）。
// 對應後端 core/platform.py 能力表；伺服端旗標（CRYPTO_TAB_ENABLED）走 setCapabilities。
const PLAY_CLOSED_FLAGS = new Set(['crypto_tab']);

/**
 * Navigation Preferences Manager
 * Handles user's navigation customization preferences
 */
const NavPreferences = {
    STORAGE_KEY: 'userNavPreferences',
    // v21: 記帳（journal）正式加入 NAV_ITEMS——先前條目遺失導致功能選單看不到（DANNY 2026-08-22 回報）；bump 重置為含 journal 的預設
    // v22: 論壇開放並預設顯示，取代美股的預設位置（DANNY 2026-09-26）；遷移見 loadPreferences
    PREFERENCES_VERSION: 22,
    // 側欄選單顯示幾項（含固定的「對話」「設定」，admin 專用項不算）：至少 3、最多 6。
    // 2026-09-24 拔掉底部列時曾改成無上限，DANNY 09-25：太多會亂，上限回到 6。
    MIN_ENABLED_ITEMS: 3,
    MAX_ENABLED_ITEMS: 6,
    _cache: null,
    _flagsOff: new Set(),
    CAPS_STORAGE_KEY: 'cm:capabilities',

    /**
     * /api/config 的 capabilities：值為 false 的旗標＝這個入口關閉。可重複呼叫，後一次覆蓋前一次。
     * 不是物件（錯誤回應、undefined）就忽略，不要把已經設好的旗標清掉。
     * persist：把這次的結果記在 localStorage——下次開站（設定檔還沒回來、或取不到）就先照上次的，
     * 不會因為 /api/config 慢或失敗而露出被關掉的分頁。
     */
    setCapabilities(caps, persist = false) {
        if (!caps || typeof caps !== 'object') return;
        const flat = {};
        Object.keys(caps).forEach((key) => {
            if (typeof caps[key] === 'boolean') flat[key] = caps[key];
        });
        this._flagsOff = new Set(Object.keys(flat).filter((key) => flat[key] === false));
        if (!persist) return;
        try {
            localStorage.setItem(this.CAPS_STORAGE_KEY, JSON.stringify(flat));
        } catch (_e) {
            /* 隱私模式等：不記憶，下次照預設＋等設定檔 */
        }
    },

    /** 開站時先套上次記下的旗標（只收布林值）。 */
    loadCachedCapabilities() {
        try {
            const raw = localStorage.getItem(this.CAPS_STORAGE_KEY);
            if (raw) this.setCapabilities(JSON.parse(raw));
        } catch (_e) {
            /* 壞掉的快取當作沒有 */
        }
    },

    isFlagOff(flag) {
        if (!flag) return false;
        if (this._flagsOff.has(flag)) return true;
        return PLAY_CLOSED_FLAGS.has(flag) && !!window.CMPlatform?.isPlay?.();
    },

    /** 進不去的分頁：下架（disabled）或旗標關閉。switchTab、深連結、導覽都用同一個判斷。 */
    isUnavailable(item) {
        return !!item && (item.disabled === true || this.isFlagOff(item.flag));
    },

    /** 「市場」子分頁列：MARKET_TABS 裡沒被旗標關掉的分頁（順序固定）。 */
    marketMembers() {
        return MARKET_TABS.map((id) => NAV_ITEMS.find((i) => i.id === id)).filter(
            (item) => item && !this.isUnavailable(item)
        );
    },

    /** 分頁屬於哪個導覽群組（目前只有 'market'）；不屬於任何群組回 null。 */
    groupOf(tabId) {
        return NAV_ITEMS.find((i) => i.id === tabId)?.group || null;
    },

    /** 點「市場」要去哪個子分頁：上次用的（還可用的話），否則第一個可用的。 */
    resolveMarketTab(preferred) {
        const ids = this.marketMembers().map((i) => i.id);
        return ids.includes(preferred) ? preferred : ids[0] || null;
    },

    /**
     * Get all enabled navigation items
     * @returns {Array} Array of enabled NAV_ITEMS
     */
    getEnabledItems() {
        const preferences = this.loadPreferences();
        const items = NAV_ITEMS.filter(
            (item) =>
                preferences.enabledItems.includes(item.id) && !item.hidden && !this.isUnavailable(item)
        );
        return this.applyOrder(items, preferences.order);
    },

    /**
     * 自訂順序（側欄功能選單「調整順序」拖過才有 preferences.order）：排過的照 order，
     * 之後才開的接在後面、照預設順序。沒排過的人維持 NAV_ITEMS 的順序（不會因為上線就洗牌）
     */
    applyOrder(items, order) {
        if (!Array.isArray(order) || order.length === 0) return items;
        const rank = new Map(order.map((id, i) => [id, i]));
        const key = (id, i) => (rank.has(id) ? rank.get(id) : order.length + i);
        return items
            .map((item, i) => ({ item, k: key(item.id, i) }))
            .sort((a, b) => a.k - b.k)
            .map((x) => x.item);
    },

    /** 拖完存新順序（只收認得的 id、去重） */
    setItemOrder(ids) {
        const preferences = this.loadPreferences();
        const valid = new Set(NAV_ITEMS.map((i) => i.id));
        preferences.order = [...new Set(ids)].filter((id) => valid.has(id));
        return this.savePreferences(preferences);
    },

    /** 訪客選單：固定順序，不讀偏好。 */
    getGuestItems() {
        return GUEST_NAV_ORDER.map((id) => NAV_ITEMS.find((i) => i.id === id)).filter(
            (item) => item && !this.isUnavailable(item)
        );
    },

    /** 訪客「更多分頁」：其餘開放訪客、沒隱藏的分頁（其他市場）。 */
    getGuestMoreItems() {
        return NAV_ITEMS.filter(
            (item) =>
                item.guestAllowed === true &&
                !item.hidden &&
                !GUEST_NAV_ORDER.includes(item.id) &&
                !this.isUnavailable(item)
        );
    },

    /**
     * Check if a specific item is enabled
     * @param {string} itemId - The item ID to check
     * @returns {boolean}
     */
    isItemEnabled(itemId) {
        const item = NAV_ITEMS.find((i) => i.id === itemId);
        if (item?.hidden) return false;
        const preferences = this.loadPreferences();
        return preferences.enabledItems.includes(itemId);
    },

    /**
     * Enable or disable a navigation item
     * @param {string} itemId - The item ID to update
     * @param {boolean} enabled - Whether to enable or disable
     * @returns {boolean} Success status
     */
    setItemEnabled(itemId, enabled) {
        const preferences = this.loadPreferences();
        const item = NAV_ITEMS.find((i) => i.id === itemId);

        if (!item) {
            console.warn(`Navigation item '${itemId}' does not exist`);
            return false;
        }
        if (item.hidden) {
            console.warn(`Navigation item '${itemId}' is hidden (not launched), cannot toggle`);
            return false;
        }

        const reason = this.blockReason(itemId, enabled);
        if (reason) {
            console.warn(`Cannot ${enabled ? 'enable' : 'disable'} '${itemId}': ${reason}`);
            return false;
        }
        if (enabled) {
            if (!preferences.enabledItems.includes(itemId)) {
                preferences.enabledItems.push(itemId);
            }
        } else {
            preferences.enabledItems = preferences.enabledItems.filter((id) => id !== itemId);
        }

        this.savePreferences(preferences);
        return true;
    },

    /**
     * Check if an item can be disabled (ensures minimum items)
     * @param {string} itemId - The item to check
     * @returns {boolean}
     */
    canDisableItem(itemId) {
        return this.blockReason(itemId, false) === null;
    },

    /** 側欄實際會顯示的項數：hidden 與 admin 專用項不算，固定項（對話、設定）算。 */
    countVisible(ids) {
        return ids.filter((id) => {
            const item = NAV_ITEMS.find((i) => i.id === id);
            return item && !item.hidden && !item.adminOnly;
        }).length;
    },

    /**
     * 切換為什麼會被擋：'locked'（固定項關不掉）｜'min'｜'max'｜null（可以切）。
     * UI 依原因給對的提示——之前固定項也回報成「至少 3 個」。
     * @param {string[]} [enabledIds] 設定頁的暫存狀態；省略＝目前已存的偏好
     */
    blockReason(itemId, enabled, enabledIds) {
        const ids = enabledIds || this.loadPreferences().enabledItems;
        const item = NAV_ITEMS.find((i) => i.id === itemId);
        if (!item || ids.includes(itemId) === enabled) return null;
        if (!enabled && item.locked) return 'locked';
        const next = enabled ? [...ids, itemId] : ids.filter((id) => id !== itemId);
        if (!enabled && this.countVisible(next) < this.MIN_ENABLED_ITEMS) return 'min';
        if (enabled && this.countVisible(next) > this.MAX_ENABLED_ITEMS) return 'max';
        return null;
    },

    /**
     * Reset all items to default enabled state
     */
    resetToDefaults() {
        const defaultPreferences = {
            version: this.PREFERENCES_VERSION,
            enabledItems: NAV_ITEMS.filter((item) => item.defaultEnabled).map((item) => item.id),
        };
        this.savePreferences(defaultPreferences);
    },

    /**
     * Validate preferences object
     * @param {Object} preferences - Preferences to validate
     * @returns {Object} { valid: boolean, errors: Array }
     */
    validate(preferences) {
        const errors = [];

        if (!preferences.version || typeof preferences.version !== 'number') {
            errors.push('Invalid or missing version');
        }

        if (!Array.isArray(preferences.enabledItems)) {
            errors.push('enabledItems must be an array');
        } else {
            if (preferences.enabledItems.length < this.MIN_ENABLED_ITEMS) {
                errors.push(`At least ${this.MIN_ENABLED_ITEMS} items must be enabled`);
            }

            const validIds = NAV_ITEMS.map((item) => item.id);
            const invalidIds = preferences.enabledItems.filter((id) => !validIds.includes(id));
            if (invalidIds.length > 0) {
                errors.push(`Invalid item IDs: ${invalidIds.join(', ')}`);
            }
        }

        return {
            valid: errors.length === 0,
            errors,
        };
    },

    /**
     * Load preferences from localStorage
     * @returns {Object} Preferences object
     */
    loadPreferences() {
        if (this._cache) return this._cache;
        try {
            const stored = localStorage.getItem(this.STORAGE_KEY);
            if (stored) {
                const preferences = JSON.parse(stored);

                // Validate loaded preferences
                const validation = this.validate(preferences);
                if (!validation.valid) {
                    console.warn(
                        'Invalid preferences loaded, resetting to defaults:',
                        validation.errors
                    );
                    return this._getDefaultPreferences();
                }

                let changed = false;

                // 導覽整理（2026-10-05）：各市場分頁（美股／台股／加密貨幣／港日韓陸股／商品／外匯）併成「市場」一個導覽項。
                // 偏好裡有任一個就換成 market（排序也一併換位置）；本來就全關的不替他加回來
                // （版本遷移裡「補回該版本的預設項」那段不在此限，跟舊行為一樣）。
                // 放在版本遷移之前：之後的「滿幾格」判斷才會把市場算成一格。
                // 不 bump 版本（會重置所有人的自訂）：成員在這裡就拿掉了，之後不會再觸發，可重複執行。
                if (preferences.enabledItems.some((id) => MARKET_TABS.includes(id))) {
                    preferences.enabledItems = preferences.enabledItems.filter(
                        (id) => !MARKET_TABS.includes(id)
                    );
                    if (!preferences.enabledItems.includes('market')) {
                        preferences.enabledItems.push('market');
                    }
                    changed = true;
                }
                if (Array.isArray(preferences.order)) {
                    const at = preferences.order.findIndex((id) => MARKET_TABS.includes(id));
                    if (at !== -1) {
                        const next = preferences.order.filter(
                            (id) => !MARKET_TABS.includes(id) && id !== 'market'
                        );
                        next.splice(at, 0, 'market');
                        preferences.order = next;
                        changed = true;
                    }
                }

                // Version migration
                if (!preferences.version || preferences.version < this.PREFERENCES_VERSION) {
                    if (preferences.version < 14) {
                        // v14: 舊版預設「全開」(13+ 項) 為設計失誤，一次性重置為新預設(≤5)，
                        // 移除殘留的次要市場項目。使用者之後可在設定自行調整。
                        preferences.enabledItems = NAV_ITEMS
                            .filter((i) => i.defaultEnabled)
                            .map((i) => i.id);
                    } else {
                        if (preferences.version < 21) {
                            NAV_ITEMS.filter((i) => i.defaultEnabled && i.id !== 'forum').forEach((item) => {
                                if (!preferences.enabledItems.includes(item.id)) {
                                    preferences.enabledItems.push(item.id);
                                }
                            });
                        }
                        // v22：論壇開放。只補論壇，不把使用者自己關掉的預設項加回來；
                        // 已滿 6 格時拿掉美股讓位（預設使用者＝美股換成論壇），
                        // 沒有美股又已滿的人保留原本的選擇，論壇可在 Customize 開
                        if (!preferences.enabledItems.includes('forum')) {
                            const fits = () =>
                                this.countVisible([...preferences.enabledItems, 'forum']) <=
                                this.MAX_ENABLED_ITEMS;
                            if (!fits()) {
                                preferences.enabledItems = preferences.enabledItems.filter(
                                    (id) => id !== 'usstock'
                                );
                            }
                            if (fits()) preferences.enabledItems.push('forum');
                        }
                    }
                    preferences.version = this.PREFERENCES_VERSION;
                    changed = true;
                }

                // 自訂順序壞掉（手改 localStorage 之類）：丟掉就好，回預設順序，不用整份重置
                if (preferences.order !== undefined && !Array.isArray(preferences.order)) {
                    delete preferences.order;
                    changed = true;
                }

                // 已暫停或尚未推出的項目不可殘留在舊版使用者偏好中。
                const visibleItemIds = new Set(
                    NAV_ITEMS.filter((item) => !item.hidden).map((item) => item.id)
                );
                const visibleEnabledItems = preferences.enabledItems.filter((id) =>
                    visibleItemIds.has(id)
                );
                if (visibleEnabledItems.length !== preferences.enabledItems.length) {
                    preferences.enabledItems = visibleEnabledItems;
                    changed = true;
                }

                // Ensure locked items are always included
                NAV_ITEMS.filter((i) => i.locked).forEach((item) => {
                    if (!preferences.enabledItems.includes(item.id)) {
                        preferences.enabledItems.push(item.id);
                        changed = true;
                    }
                });

                // 超過上限（09-24~25 曾無上限、版本遷移也可能補進預設項）：
                // 固定項與 admin 專用項照留，其餘照原順序留到上限
                if (this.countVisible(preferences.enabledItems) > this.MAX_ENABLED_ITEMS) {
                    const fixed = preferences.enabledItems.filter((id) =>
                        NAV_ITEMS.some((i) => i.id === id && (i.locked || i.adminOnly))
                    );
                    let room = this.MAX_ENABLED_ITEMS - this.countVisible(fixed);
                    preferences.enabledItems = preferences.enabledItems.filter((id) => {
                        if (fixed.includes(id)) return true;
                        if (room <= 0) return false;
                        room -= 1;
                        return true;
                    });
                    changed = true;
                }

                if (changed) {
                    this.savePreferences(preferences);
                }

                this._cache = preferences;
                return preferences;
            }
        } catch (error) {
            console.error('Error loading navigation preferences:', error);
        }

        const defaults = this._getDefaultPreferences();
        this._cache = defaults;
        return defaults;
    },

    /**
     * Save preferences to localStorage
     * @param {Object} preferences - Preferences to save
     * @returns {boolean} Success status
     */
    savePreferences(preferences) {
        const validation = this.validate(preferences);
        if (!validation.valid) {
            console.error('Invalid preferences:', validation.errors);
            return false;
        }

        try {
            localStorage.setItem(this.STORAGE_KEY, JSON.stringify(preferences));
            this._cache = preferences; // 更新 cache，避免下次重新解析
            return true;
        } catch (error) {
            console.error('Error saving navigation preferences:', error);
            return false;
        }
    },

    /**
     * Export preferences for backup/transfer
     * @returns {string} JSON string of preferences
     */
    exportPreferences() {
        const preferences = this.loadPreferences();
        return JSON.stringify(preferences, null, 2);
    },

    /**
     * Import preferences from JSON string
     * @param {string} jsonString - JSON string to import
     * @returns {boolean} Success status
     */
    importPreferences(jsonString) {
        try {
            const preferences = JSON.parse(jsonString);
            const validation = this.validate(preferences);

            if (!validation.valid) {
                console.error('Invalid preferences to import:', validation.errors);
                return false;
            }

            this.savePreferences(preferences);
            return true;
        } catch (error) {
            console.error('Error importing navigation preferences:', error);
            return false;
        }
    },

    /**
     * Get default preferences
     * @returns {Object} Default preferences object
     * @private
     */
    _getDefaultPreferences() {
        return {
            version: this.PREFERENCES_VERSION,
            enabledItems: NAV_ITEMS.filter((item) => item.defaultEnabled).map((item) => item.id),
        };
    },
};

// Make available on window for cross-script access
window.NAV_ITEMS = NAV_ITEMS;
window.NavPreferences = NavPreferences;
NavPreferences.loadCachedCapabilities();

export { NAV_ITEMS, NavPreferences, MARKET_TABS };
