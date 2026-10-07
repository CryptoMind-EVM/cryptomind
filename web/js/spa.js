// ========================================
// spa.js - Single Page Application Core
// ========================================

// AppStore is the single source of truth for active tab
AppStore.set('activeTab', 'chat');

// Helper to get activeTab from AppStore (source of truth) with localStorage fallback
function getActiveTab() {
    return AppStore.get('activeTab') || localStorage.getItem('activeTab') || 'chat';
}

var VALID_TABS = [
    'chat', 'crypto', 'twstock', 'usstock',
    'journal', 'commodity', 'forex', 'hkstock', 'astock', 'jpstock', 'instock', 'krstock',
    'wallet', 'wallet-monitor', 'trust', 'friends', 'forum', 'settings', 'admin',
    'ai-studio', 'discover', 'studio', 'connections', 'sample', 'scamcheck',
    // 導覽的「市場」群組（美股／台股／加密貨幣的子分頁列）：不是真的面板，switchTab 會換成上次用的子分頁
    'market',
];

// 功能 tab（ai-studio/discover）：不在底部導覽，但可經 deep-link 到達。
// 2026-08-24 起 customize 閘門整體移除（見 executeTabSwitch）——此集合
// 僅作文件說明保留，不再參與導覽判斷。
const NON_NAV_TABS = new Set(['ai-studio', 'discover']);

// Normalize removed/unknown routes before asynchronous app initialization begins.
// This prevents a dormant feature hash (for example an old bookmark) from
// remaining visible while authentication, i18n, and navigation preferences load.
const _requestedInitialTab = window.location.hash.replace('#', '');
// 2026-08-24 事故守門：spa.js 誤在非 SPA 頁執行（如 chunk graph 異常把 main
// 入口拉進論壇頁）時，replaceState '#chat' 會改寫論壇頁 URL、boot 流程會
// 操弄不存在 的 SPA 容器。#chat-tab 是 SPA 的根容器——不存在即整個模組靜默。
const _IS_SPA_PAGE = !!document.getElementById('chat-tab');
if (_IS_SPA_PAGE && _requestedInitialTab && !VALID_TABS.includes(_requestedInitialTab)) {
    history.replaceState({ tab: 'chat' }, '', '#chat');
}

// ========================================
// Lazy module loading (code-splitting)
// ========================================
// 首屏只載入核心 + chat。其餘 tab 的模組在切到該 tab 時才 dynamic import，
// 讓 Vite 切成獨立 chunk，首屏 bundle 大幅縮小。SPA 架構本來就是
// 「import 時註冊 window.X、切 tab 時才 init」，且跨模組呼叫全用
// typeof guard（click-delegator.js），所以延遲載入不會破壞功能——
// 唯一要確保的是 tab init 執行前模組已 await 完成。
//
// _TAB_MODULES: tabId → 該 tab 需要的模組清單（dynamic import factory）。
// 載入過的 tab Vite/瀏覽器會自動快取，重複切換不會重新下載。
const _TAB_MODULES = {
    // hidden:true（Phase 2 未開放，使用者根本看不到）
    friends: () => Promise.all([
        import('./friends.js'),
        import('./messages.js'),
        import('./components/tab-friends.js'),
    ]),
    // journal（統一帳本 Dashboard）：骨架在 index.html，模組只需 tab-journal。
    // 2026-08-22 盤點：此前缺此鍵 → tab-journal.js 從未被載入 → JournalTab
    // undefined → Dashboard 永遠卡「載入中」。
    journal: () => Promise.all([
        import('./components/tab-journal.js'),
    ]),
    forum: () => Promise.all([
        import('./forum-api.js'),
        import('./forum-app.js'),
        import('./components/tab-forum.js'),
    ]),
    // defaultEnabled:false（使用者要 Customize 勾選才出現）
    commodity: () => Promise.all([
        import('./commodity.js'),
        import('./alerts.js'), // 🔔 價格警報
        import('./components/tab-commodity.js'),
    ]),
    forex: () => Promise.all([
        import('./forex.js'),
        import('./alerts.js'), // 🔔 價格警報
        import('./components/tab-forex.js'),
    ]),
    hkstock: () => Promise.all([
        import('./hkstock.js'),
        import('./alerts.js'), // 🔔 價格警報
        import('./components/tab-hkstock.js'),
    ]),
    astock: () => Promise.all([
        import('./astock.js'),
        import('./alerts.js'), // 🔔 價格警報
        import('./components/tab-astock.js'),
    ]),
    jpstock: () => Promise.all([
        import('./jpstock.js'),
        import('./alerts.js'), // 🔔 價格警報
        import('./components/tab-jpstock.js'),
    ]),
    instock: () => Promise.all([
        import('./instock.js'),
        import('./alerts.js'), // 🔔 價格警報
        import('./components/tab-instock.js'),
    ]),
    krstock: () => Promise.all([
        import('./krstock.js'),
        import('./alerts.js'), // 🔔 價格警報
        import('./components/tab-krstock.js'),
    ]),
    // 預設啟用但非首屏（chat 為首屏預設 tab）
    twstock: () => Promise.all([
        import('./twstock.js'),
        import('./alerts.js'),            // loadUserAlerts 由 twstock/usstock 用
        import('./components/tab-twstock.js'),
    ]),
    usstock: () => Promise.all([
        import('./usstock.js'),
        import('./alerts.js'),
        import('./components/tab-usstock.js'),
    ]),
    wallet: () => Promise.all([
        // wallet.js 的交易紀錄靠 ForumAPI 拉資料；forum-api 原本只在 forum
        // loader 載入，沒開過 Forum 就進 Wallet →「ForumAPI library not
        // loaded」載入失敗（2026-09-11 盤查）
        import('./forum-api.js'),
        import('./wallet.js'),
        // wallet tab 無獨立 tab-*.js 模板
    ]),
    'wallet-monitor': () => Promise.all([
        import('./walletMonitorTab.js'),
        import('./components/tab-wallet-monitor.js'),
    ]),
    trust: () => Promise.all([
        import('./trustTab.js'),
        import('./trustScoreManager.js'),
        import('./components/tab-trust.js'),
    ]),
    // Phase 1/3：AI Studio + Discover（lazy chunk；詳 design.md §7/§10）
    'ai-studio': () => Promise.all([
        import('./ai-studio.js'),
        import('./components/tab-ai-studio.js'),
    ]),
    discover: () => Promise.all([
        import('./discover.js'),
        import('./components/tab-discover.js'),
    ]),
    // 提案工作台（募資者模式，design 2026-08-16）
    studio: () => Promise.all([
        import('./studio.js'),
        import('./components/tab-studio.js'),
    ]),
    admin: () => Promise.all([
        import('./admin.js'),
        import('./admin-stats.js'),
        import('./admin-visitors.js'),
        import('./admin-audit.js'),
        import('./admin-wallet-monitor.js'),
        import('./admin-settings.js'),
        import('./components/tab-admin.js'),
    ]),
    // crypto tab（market-screener/chart/ws/pulse 互相依賴，整組一起載入）
    crypto: () => Promise.all([
        import('./market-screener.js'),
        import('./market-chart.js'),
        import('./market-ws.js'),
        import('./pulse.js'),
        import('./components/tab-crypto.js'),
    ]),
    // Sample portfolio（2026-09-27 上市準備 PR-3）：訪客專用示範頁，資料是前端靜態 fixture
    sample: () => Promise.all([
        import('./sample-portfolio.js'),
        import('./components/tab-sample.js'),
    ]),
    // 詐騙檢查（2026-09-27 自獨立頁 /scam-tracker/ 搬進來）：公開免登入的地址健診
    scamcheck: () => Promise.all([
        import('./scam-check.js'),
        import('./components/tab-scamcheck.js'),
    ]),
    // Connections（連結）：Telegram／LINE 等外部連接（2026-09-08 自 Settings 遷出）。
    // telegram-link.js 一併改成 lazy——它只服務這個分頁，沒必要留在首屏 bundle。
    connections: () => Promise.all([
        import('./connections.js'),
        import('./telegram-link.js'),
        import('./line-link.js'),
        import('./components/tab-connections.js'),
    ]),
    // settings tab（init 呼叫 PremiumManager/loadPremiumStatus/updatePriceDisplays，
    // 故 premium + forum-config 需在 settings init 前載入）
    settings: () =>
        Promise.all([
            import('./forum-config.js'),
            import('./premium.js'),
            import('./brief-settings.js'),
            import('./alerts.js'), // 我的自選的 🔔 開價格警報視窗
            import('./connections-settings.js'),
            import('./components/tab-settings.js'),
        ]).then(([forumConfig]) => {
            // Settings 升級鈕的價格是 data-price="premium" 佔位（spinner），只有
            // 載到 /api/premium/pricing 才會填充；SPA 裡原本只有 forum tab 會觸發
            // loadPiPrices → 直達 Settings 時永遠停在 Loading。這裡冪等補載
            // （loadForumPrices 內建 loading 防併發），成功路徑會自行呼叫
            // updatePriceDisplays 填充已注入的 markup。
            if (!window.ForumPrices || !window.ForumPrices.loaded) {
                forumConfig.loadPiPrices();
            }
        }),
};

/** 按需載入該 tab 的模組（若已列在 _TAB_MODULES）。未列的 tab 表示模組仍在首屏。 */
async function _ensureTabModules(tabId) {
    const loader = _TAB_MODULES[tabId];
    if (!loader) return;
    try {
        await loader();
    } catch (e) {
        console.error(`[spa] dynamic import failed for tab "${tabId}":`, e);
    }
}

// ========================================
// Navigation Logic (Updated with Smooth Transitions)
// ========================================

/**
 * 把 /api/config 的 capabilities（例如 crypto_tab）告訴導覽：重畫側欄；
 * 如果使用者此刻正停在剛被關掉的分頁（旗標比畫面晚到），退回 chat。取不到設定就維持原狀。
 */
async function _applyFeatureFlags() {
    try {
        const cfg = await window.AppAPI.getAppConfig();
        if (!cfg || !window.NavPreferences) return;
        window.NavPreferences.setCapabilities(cfg.capabilities, true);
        if (window.GlobalNav) window.GlobalNav.renderSidebarNav();
        // 旗標比畫面晚到：目前停在市場分頁的話，子分頁列要跟著收掉被關的那顆
        const visible = (document.querySelector('.tab-content:not(.hidden)')?.id || '').replace(/-tab$/, '');
        if (window.MarketHub && visible) window.MarketHub.show(visible);
        const current = getActiveTab();
        const item = (window.NAV_ITEMS || []).find((i) => i.id === current);
        if (item && window.NavPreferences.isUnavailable(item) && document.getElementById('chat-tab')) {
            await window.switchTab('chat');
        }
    } catch (e) {
        console.warn('[spa] feature flags unavailable, keeping defaults:', e);
    }
}

/**
 * Switch to a different tab with smooth transition
 * @param {string} tabId - The tab ID to switch to
 * @param {boolean} fromPopState - Whether this is triggered by browser back/forward
 * @returns {Promise<void>}
 */
var _switchTabTimer = null;

async function switchTab(tabId, fromPopState = false) {
    if (!VALID_TABS.includes(tabId)) {
        console.warn(`Invalid tab '${tabId}', falling back to 'chat'`);
        tabId = 'chat';
    }

    // 「市場」群組（#market、導覽點市場）→ 上次用的子分頁；沒有可用子分頁就回 chat
    if (tabId === 'market') {
        const resolved = (window.MarketHub && window.MarketHub.target()) || 'chat';
        if (_IS_SPA_PAGE && window.location.hash === '#market') {
            history.replaceState({ tab: resolved }, '', `#${resolved}`);
        }
        tabId = resolved;
    }

    // 2026-09-02 DANNY：下架板塊（nav-config disabled: true，如 discover/studio）
    // 連 deep-link／殘留 localStorage 也不可達——一律導回 chat 並清掉 hash。
    // 注意語意分離：hidden 僅代表不顯示於導覽（如 ai-studio 仍可從設定頁
    // switchTab 進入），disabled 才是「下架不可達」。
    // 2026-10-05：旗標關閉的分頁（加密貨幣：Play 版、CRYPTO_TAB_ENABLED=false）同樣不可達。
    const _offNavItem = (window.NAV_ITEMS || []).find((i) => i.id === tabId);
    if (_offNavItem && window.NavPreferences && window.NavPreferences.isUnavailable(_offNavItem)) {
        console.warn(`Tab '${tabId}' is disabled, falling back to 'chat'`);
        if (_IS_SPA_PAGE && window.location.hash === `#${tabId}`) {
            history.replaceState({ tab: 'chat' }, '', '#chat');
        }
        tabId = 'chat';
    }

    // 訪客專用分頁（guestOnly，如 sample 示範頁）：登入用戶改去真的帳本
    if (_offNavItem && _offNavItem.guestOnly && window.AuthManager && window.AuthManager.isLoggedIn()) {
        if (_IS_SPA_PAGE && window.location.hash === `#${tabId}`) {
            history.replaceState({ tab: 'journal' }, '', '#journal');
        }
        tabId = 'journal';
    }

    // [Security] Strict Login Check
    // 訪客模式（2026-08-19 設計）：chat tab 開放訪客（guest AI 免登入體驗），
    // 其他 tab 仍需登入 —— 彈登入 modal 並退回 chat。
    if (window.AuthManager && !window.AuthManager.isLoggedIn()) {
        // 訪客模式 Tier 1（2026-08-27 設計）：chat + 唯讀市場數據頁開放訪客，
        // 邊界與 global-nav._isGuestAllowed 同款（資料源：nav-config guestAllowed）。
        // 後端 GUEST_DATA_ACCESS=off 時（quota 帶 data_access:false）收回 chat-only。
        const navItem = (window.NAV_ITEMS || []).find((i) => i.id === tabId);
        const flagOff =
            window.AuthEnvironment && window.AuthEnvironment.guestDataAccess === false;
        // guestOnly（示範頁）是前端靜態資料、guestAlways（詐騙檢查）是公開查詢，
        // 都不受唯讀市場數據開關影響
        const guestOk =
            tabId === 'chat' ||
            navItem?.guestOnly === true ||
            navItem?.guestAlways === true ||
            (navItem?.guestAllowed === true && !flagOff);
        if (!guestOk) {
            const modal = document.getElementById('login-modal');
            if (modal && modal.classList.contains('hidden')) {
                console.warn('⚠️ Access denied: User not logged in. Showing login modal.');
                modal.classList.remove('hidden');
            }
            tabId = 'chat';
        }
    }

    if (_switchTabTimer) {
        clearTimeout(_switchTabTimer);
        _switchTabTimer = null;
    }

    const currentTab = document.querySelector('.tab-content:not(.hidden)');

    if (currentTab && currentTab.id !== tabId + '-tab') {
        currentTab.style.opacity = '0';
        currentTab.style.transform = 'translateY(-5px)';
        currentTab.style.transition = 'all 0.2s ease-in';

        return new Promise((resolve) => {
            _switchTabTimer = setTimeout(async () => {
                _switchTabTimer = null;
                await executeTabSwitch(tabId, fromPopState);
                resolve();
            }, 150);
        });
    } else {
        return await executeTabSwitch(tabId, fromPopState);
    }
}
window.switchTab = switchTab;

// 監聽瀏覽器返回/前進按鈕
window.addEventListener('popstate', (event) => {
    let targetTab = 'chat';

    if (event.state && event.state.tab) {
        targetTab = event.state.tab;
    } else if (window.location.hash) {
        const hashTab = window.location.hash.replace('#', '');
        if (VALID_TABS.includes(hashTab)) {
            targetTab = hashTab;
        }
    }

    // 使用 fromPopState=true 避免再次 pushState
    switchTab(targetTab, true);
});

/**
 * Navigate to forum (save current tab for return)
 */
function navigateToForum() {
    // 保存當前 tab 到 sessionStorage
    const currentTab = getActiveTab();
    sessionStorage.setItem('returnToTab', currentTab);
    smoothNavigate('/static/index.html#forum');
}
window.navigateToForum = navigateToForum;

/**
 * Execute the actual tab switching logic
 * @param {string} tabId - The tab ID to switch to
 * @param {boolean} fromPopState - Whether this is triggered by browser back/forward
 * @returns {Promise<void>}
 */
// 「我的 AI」摘要卡已移除（2026-09-02）：loadMyAISummaryCounts 連同四個
// 計數 API 呼叫一併移除，Settings 載入不再多打四個請求。

// 旗標關閉／下架的分頁進不去。switchTab 開頭已擋一次；這裡再擋是因為旗標可能在
// 150ms 淡出計時或模組載入（await）期間才到，到時 switchTab 的檢查早就過了。
function _isTabUnavailable(tabId) {
    const item = (window.NAV_ITEMS || []).find((i) => i.id === tabId);
    return !!(item && window.NavPreferences && window.NavPreferences.isUnavailable(item));
}

async function executeTabSwitch(tabId, fromPopState = false) {
    if (_isTabUnavailable(tabId)) {
        if (_IS_SPA_PAGE && window.location.hash === `#${tabId}`) {
            history.replaceState({ tab: 'chat' }, '', '#chat');
        }
        tabId = 'chat';
    }
    // AI Studio 返回鈕：記錄進入前的分頁（回不去就 fallback chat）
    if (tabId === 'ai-studio' && window.AppStore) {
        const prevTabEl = document.querySelector('.tab-content:not(.hidden)');
        const prevTab = prevTabEl && prevTabEl.id.replace(/-tab$/, '');
        if (prevTab && prevTab !== 'ai-studio') {
            AppStore.set('aiStudioReturnTab', prevTab);
        }
    }

    // [customize 閘門移除——2026-08-24 DANNY 回報「前往板塊沒反應」]
    // 此前：目標 tab 若未在 Customize 啟用，靜默改跳第一個啟用 tab（通常
    // = 目前的 chat）→ 訊息裡的「前往 X 板塊」chip、#hash deep-link、
    // 「新對話」（chat 被停用時）全部表面無反應。
    // 修正：Customize 只控制「底部導覽列顯示哪些按鈕」，不是存取控制——
    // 任何 VALID_TABS 都可到達；導覽列無對應按鈕時僅不高亮（下方
    // activeBtn 已有 null 防護）。

    localStorage.setItem('activeTab', tabId);
    AppStore.set('activeTab', tabId); // source of truth

    // 更新瀏覽器歷史記錄（只有非 popstate 觸發時才 push）
    if (!fromPopState && window.location.hash !== '#' + tabId) {
        history.pushState({ tab: tabId }, '', '#' + tabId);
    }

    // Close any open stock chart overlays (they are fixed-position, not inside tab DOM)
    if (window.TWStockTab && typeof window.TWStockTab.closeTwChart === 'function')
        window.TWStockTab.closeTwChart();
    if (window.USStockTab && typeof window.USStockTab.closeChart === 'function')
        window.USStockTab.closeChart();
    // Crypto K-line chart (#chart-section) is also a fixed-position overlay → close it too
    if (typeof window.closeChart === 'function') window.closeChart();
    // Close price alert modal if open
    if (typeof window.closeAlertModal === 'function') window.closeAlertModal();
    // Remove any leftover symbol-picker overlays (commodity/forex/astock/jpstock/instock/krstock
    // append `<market>-picker-modal` to <body> with fixed inset-0 z-50; they only self-remove on
    // X/confirm, so switching tabs while one is open would leave it floating on top).
    document.querySelectorAll('[id$="-picker-modal"]').forEach((el) => el.remove());

    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach((el) => {
        el.classList.add('hidden');
        el.style.opacity = '';
        el.style.transform = '';
        el.style.transition = '';
    });

    // Dynamic Component Injection (Lazy Loading)
    // 先按需 dynamic import 該 tab 的模組（tab 模板 + 業務邏輯），
    // 完成後 Components.inject 才拿得到 window.Components[tabId] 模板。
    await _ensureTabModules(tabId);
    // 模組載入期間旗標才到：改去 chat（另一個 executeTabSwitch 可能已經在顯示 chat，重複進來是冪等的）
    if (_isTabUnavailable(tabId)) return executeTabSwitch('chat', true);
    // 注入與否由資料決定，不由白名單決定：模板（components/tab-*.js 掛上的
    // 字串）存在就注入；骨架內建於 index.html 的 tab（chat/journal/wallet）
    // 沒有模板，自然跳過。此前這裡是一份 19 項人工白名單——#702 新增
    // connections 時漏列，分頁整頁空白且無任何錯誤（2026-09-09 線上回報）。
    // 白名單每多一天就多一次漏列的機會；拿掉它，新 tab 只要照 _TAB_MODULES
    // 載入模板就自動獲得注入（測試鎖死白名單不得復活）。
    if (
        window.Components &&
        typeof window.Components.inject === 'function' &&
        typeof window.Components[tabId] === 'string'
    ) {
        await window.Components.inject(tabId);
    }

    // 市場分頁：記住這次用的子分頁、把共用的子分頁列放進面板（模板注入之後、顯示之前）
    if (window.MarketHub) window.MarketHub.show(tabId);

    // [Sidebar Visibility]
    // 2026-08-21：sidebar 現在包含主要導覽（journal/crypto/twstock…），
    // 不再只屬於 chat——所有分頁都必須顯示，否則使用者無法切換。
    // 原本 tabsWithSidebar=['chat'] 是「sidebar=對話歷史」時代的邏輯。
    const globalSidebar = document.getElementById('chat-sidebar');
    const sidebarBackdrop = document.getElementById('sidebar-backdrop');
    if (globalSidebar) {
        globalSidebar.style.display = '';
        globalSidebar.classList.remove('hidden');
        // backdrop 只在手機 drawer 打開時顯示（toggleSidebar 控制），這裡不動
    }

    // Show target tab
    const target = document.getElementById(tabId + '-tab');
    if (target) {
        target.classList.remove('hidden');
    }

    // 功能選單高亮由 GlobalNav.refreshSidebarActive 處理（switchTab 包裝，global-nav.js）

    // Trigger tab-specific initialization
    if (tabId === 'crypto') {
        if (typeof connectTickerWebSocket === 'function') connectTickerWebSocket();
        if (typeof initCrypto === 'function') {
            await initCrypto();
        }
    }
    if (tabId === 'twstock') {
        if (window.TWStockTab && typeof window.TWStockTab.initTwStock === 'function') {
            window.TWStockTab.initTwStock();
        }
        if (typeof window.loadUserAlerts === 'function') window.loadUserAlerts();
    }
    if (tabId === 'usstock') {
        if (window.USStockTab && typeof window.USStockTab.init === 'function') {
            window.USStockTab.init();
        }
        if (typeof window.loadUserAlerts === 'function') window.loadUserAlerts();
    }
    if (tabId === 'commodity' && typeof CommodityTab !== 'undefined') CommodityTab.init();
    if (tabId === 'forex' && typeof ForexTab !== 'undefined') ForexTab.init();
    if (tabId === 'hkstock' && typeof HKStockTab !== 'undefined') HKStockTab.init();
    if (tabId === 'astock'  && typeof AStockTab  !== 'undefined') AStockTab.init();
    if (tabId === 'jpstock' && typeof JPStockTab !== 'undefined') JPStockTab.init();
    if (tabId === 'instock' && typeof INStockTab !== 'undefined') INStockTab.init();
    if (tabId === 'krstock' && typeof KRStockTab !== 'undefined') KRStockTab.init();
    if (tabId === 'wallet') {
        if (window.WalletApp) window.WalletApp.init();
        // Swap History（全面重組 Phase 2：從 Settings 搬入 Wallet tab）
        if (typeof window.SwapHistoryManager !== 'undefined' &&
            typeof window.SwapHistoryManager.init === 'function') {
            window.SwapHistoryManager.init();
        }
        // Swap Limit（使用者自訂單筆上限，與 Swap History 同區塊）
        if (typeof initSwapLimitSettings === 'function') {
            initSwapLimitSettings();
        }
    }
    if (tabId === 'wallet-monitor' && typeof WalletMonitorTab !== 'undefined') WalletMonitorTab.init();
    if (tabId === 'trust' && typeof TrustTab !== 'undefined') TrustTab.init();
    if (tabId === 'ai-studio' && typeof AIStudioTab !== 'undefined') AIStudioTab.init();
    if (tabId === 'connections' && window.ConnectionsTab) window.ConnectionsTab.init();
    if (tabId === 'sample' && window.SampleTab) window.SampleTab.init();
    if (tabId === 'scamcheck' && window.ScamCheckTab) window.ScamCheckTab.init();
    if (tabId === 'discover' && typeof DiscoverTab !== 'undefined') DiscoverTab.init();
    if (tabId === 'studio' && typeof StudioTab !== 'undefined') StudioTab.init();
    // journal：骨架內建於 index.html（非 Components 模板型），init 負責拉資料渲染
    // Dashboard——先前只列進 inject 清單（inject 必然 template=false 失敗）而
    // 沒人呼叫 init，Dashboard 永遠卡「載入中...」（2026-08-22 盤點）。
    if (tabId === 'journal' && window.JournalTab) window.JournalTab.init();
    if (tabId === 'friends') {
        if (window.SocialHub) window.SocialHub.init();
    }
    if (tabId === 'forum') {
        if (window.ForumApp) window.ForumApp.init();
    }
    if (tabId === 'admin') {
        if (window.AdminPanel) AdminPanel.init();
    }
    if (tabId === 'settings') {
        // Settings 內容是動態注入，需在注入後重新同步已登入身份顯示（username / UID）
        if (window.AuthManager && typeof window.AuthManager._updateUI === 'function') {
            window.AuthManager._updateUI(window.AuthManager.isLoggedIn());
        }
        // ✅ 效能優化：並行執行所有 settings 初始化，而非依序等待
        const settingsInits = [];
        if (typeof loadSettingsWalletStatus === 'function')
            settingsInits.push(Promise.resolve(loadSettingsWalletStatus()));
        if (typeof loadPremiumStatus === 'function')
            settingsInits.push(Promise.resolve(loadPremiumStatus({ shared: true })));
        if (typeof window.loadBriefPrefs === 'function')
            settingsInits.push(Promise.resolve(window.loadBriefPrefs()));
        if (typeof window.loadConnectionsSummary === 'function')
            settingsInits.push(Promise.resolve(window.loadConnectionsSummary()));
        if (typeof updateLLMStatusUI === 'function')
            settingsInits.push(Promise.resolve(updateLLMStatusUI()));
        // Settings DOM 為動態注入：進入分頁時重抓綁定資料並渲染「已綁定的模型」清單
        // （auth:ready 時的那次 render 常發生在容器還不存在的時候）
        if (typeof window.loadSavedApiKeys === 'function')
            settingsInits.push(Promise.resolve(window.loadSavedApiKeys()));
                if (!AppStore.get('settingsHeavyInitAt')) AppStore.set('settingsHeavyInitAt', 0);
                const now = Date.now();
                if (now - AppStore.get('settingsHeavyInitAt') > 15000) {
                    AppStore.set('settingsHeavyInitAt', now);
            if (typeof window.initTestMode === 'function') {
                settingsInits.push(Promise.resolve(window.initTestMode()));
            }
        }
        if (typeof updatePriceDisplays === 'function') updatePriceDisplays();
        if (
            window.PremiumManager &&
            typeof window.PremiumManager.updatePriceDisplay === 'function'
        ) {
            window.PremiumManager.updatePriceDisplay();
        }
        if (typeof window.updateAvailableModels === 'function') window.updateAvailableModels();
        // TelegramLinkApp 的 init 移到 connections 分頁（2026-09-08 遷出 Settings）
        // 並行發出所有 API 請求
        Promise.allSettled(settingsInits).catch((e) => console.warn('Settings init error:', e));
    }
    if (tabId === 'chat') {
        if (typeof initChat === 'function') initChat();
        // ✅ 效能優化：checkApiKeyStatus 加 TTL 快取，避免每次切換都打後端 API
        const now = Date.now();
        if (!AppStore.get('lastApiKeyCheck') || now - AppStore.get('lastApiKeyCheck') > 30000) {
            AppStore.set('lastApiKeyCheck', now);
            // 確保 APIKeyManager 已初始化
            if (typeof checkApiKeyStatus === 'function' && window.APIKeyManager) {
                checkApiKeyStatus();
            }
        }
    }

    if (typeof onTabSwitch === 'function') onTabSwitch(tabId);
}
window.executeTabSwitch = executeTabSwitch;

function restoreUiStateAfterResume() {
    const savedTab = getActiveTab();
    // 2026-09-09 合一根治：這裡原本是 VALID_TABS 的第二份手工副本，而且
    // 已經漂過一次——trust／wallet-monitor／ai-studio／discover／studio 不
    // 在副本裡，那些分頁恢復（resume）時直接 return，UI 不還原。副本與
    // 正本合一後單一事實來源，不可能再漂；switchTab 本身仍保有 disabled
    // deep-link 導回 chat 等全部守衛，這裡放行不會繞過任何檢查。
    if (!VALID_TABS.includes(savedTab)) {
        return;
    }

    const hasVisibleTab = Array.from(document.querySelectorAll('.tab-content')).some(
        (element) => !element.classList.contains('hidden')
    );

    if (!hasVisibleTab || AppStore.get('activeTab') !== savedTab) {
        switchTab(savedTab, true).catch((error) => {
            console.warn('Resume UI restore failed:', error);
        });
    }
}

document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        restoreUiStateAfterResume();
    }
});

window.addEventListener('pageshow', restoreUiStateAfterResume);

// ========================================
// Navigation Rendering & Feature Menu
// ========================================

/**
 * Render navigation buttons based on user preferences
 */
function renderNavButtons() {
    if (window.GlobalNav && typeof window.GlobalNav.renderNavButtons === 'function') {
        window.GlobalNav.renderNavButtons();
        return;
    }
}
window.renderNavButtons = renderNavButtons;

/**
 * Feature Menu Manager
 * Handles the navigation customization modal
 */
const FeatureMenu = {
    _tempPreferences: null,
    _modal: null,

    /**
     * Open the feature menu modal
     *
     * featureMenu 是獨立 lazy component；開啟時才載入，避免首屏增加體積。
     */
    async open() {
        if (!window.NavPreferences) {
            console.error('NavPreferences not loaded');
            return;
        }

        // Inject feature menu component if not already in DOM
        if (!document.getElementById('feature-menu-modal')) {
            if (!window.Components || !window.Components.featureMenu) {
                try {
                    await import('./components/feature-menu.js');
                } catch (e) {
                    console.error('Failed to load feature-menu.js', e);
                    return;
                }
            }
            if (window.Components && window.Components.featureMenu) {
                const container = document.createElement('div');
                container.innerHTML = window.Components.featureMenu;
                document.body.appendChild(container.firstElementChild);
            } else {
                console.error('Feature menu component not available');
                return;
            }
        }

        this._modal = document.getElementById('feature-menu-modal');
        const itemsContainer = document.getElementById('feature-menu-items');
        const warningBanner = document.getElementById('feature-menu-warning');

        // Store current preferences for temp state
        const currentEnabled = NavPreferences.loadPreferences().enabledItems;
        this._tempPreferences = new Set(currentEnabled);

        // Clear and render items
        itemsContainer.innerHTML = '';

        window.NAV_ITEMS.forEach((item) => {
            if (item.locked) return;
            if (item.hidden) return;

            const isEnabled = this._tempPreferences.has(item.id);
            const itemEl = document.createElement('div');
            itemEl.className = `feature-menu-item ${!isEnabled ? 'disabled' : ''}`;
            itemEl.dataset.itemId = item.id;

            // Use i18n key if available, otherwise fall back to label
            const labelText =
                item.i18nKey && window.I18n ? window.I18n.t(item.i18nKey) : item.label;

            itemEl.innerHTML = `
                <div class="feature-item-icon">
                    <i data-lucide="${item.icon}"></i>
                </div>
                <span class="feature-item-label">${labelText}</span>
                <div class="feature-toggle ${isEnabled ? 'enabled' : ''}"></div>
            `;

            itemEl.addEventListener('click', () => this.toggleItem(item.id, itemEl));
            itemsContainer.appendChild(itemEl);
        });

        // Initialize Lucide icons
        AppUtils.refreshIcons();

        // Show modal with animation
        this._modal.classList.remove('hidden');
        requestAnimationFrame(() => {
            this._modal
                .querySelector('.feature-menu-content')
                .classList.add('modal-content-active');
        });
    },

    /**
     * Toggle a navigation item on/off
     */
    toggleItem(itemId, element) {
        // Locked items cannot be toggled
        const navItem = window.NAV_ITEMS.find((i) => i.id === itemId);
        if (navItem && navItem.locked) return;

        const isCurrentlyEnabled = this._tempPreferences.has(itemId);
        const reason = NavPreferences.blockReason(
            itemId,
            !isCurrentlyEnabled,
            Array.from(this._tempPreferences)
        );
        if (reason) {
            const warningBanner = document.getElementById('feature-menu-warning');
            this._showWarning(warningBanner, reason);
            element.classList.add('shake');
            setTimeout(() => element.classList.remove('shake'), 400);
            return;
        }
        if (isCurrentlyEnabled) {
            this._tempPreferences.delete(itemId);
        } else {
            this._tempPreferences.add(itemId);
        }

        // Update UI
        const toggle = element.querySelector('.feature-toggle');
        if (this._tempPreferences.has(itemId)) {
            toggle.classList.add('enabled');
            element.classList.remove('disabled');
        } else {
            toggle.classList.remove('enabled');
            element.classList.add('disabled');
        }

        document.getElementById('feature-menu-warning')?.classList.add('hidden');
    },

    // kind：NavPreferences.blockReason 的 'min'｜'max'（設定頁不列固定項，不會是 'locked'）
    _showWarning(banner, kind) {
        if (!banner) return;
        const p = banner.querySelector('p');
        if (window.I18n) {
            p.textContent =
                kind === 'max'
                    ? window.I18n.t('featureMenu.maxWarning', { max: NavPreferences.MAX_ENABLED_ITEMS })
                    : window.I18n.t('featureMenu.minWarning', { min: NavPreferences.MIN_ENABLED_ITEMS });
        }
        banner.classList.remove('hidden');
        clearTimeout(this._warningTimer);
        this._warningTimer = setTimeout(() => banner.classList.add('hidden'), 3500);
    },

    /**
     * Save changes and close modal
     */
    save() {
        if (this._tempPreferences.size < NavPreferences.MIN_ENABLED_ITEMS) {
            if (typeof showToast === 'function') {
                showToast(
                    `At least ${NavPreferences.MIN_ENABLED_ITEMS} items must be enabled`,
                    'warning'
                );
            }
            return;
        }

        // Save preferences
        const preferences = {
            version: NavPreferences.PREFERENCES_VERSION,
            enabledItems: Array.from(this._tempPreferences),
        };
        NavPreferences.savePreferences(preferences);

        // Re-render navigation
        renderNavButtons();

        // Close modal
        this.close();

        // Show success feedback
        this.showToast(window.I18n ? window.I18n.t('nav.preferencesSaved') : 'Navigation preferences saved');
    },

    /**
     * Reset to defaults
     */
    resetToDefaults() {
        if (typeof showConfirm === 'function') {
            showConfirm({
                title: window.I18n?.t('settings.navigation.reset') || 'Reset to Default',
                message: window.I18n ? window.I18n.t('nav.resetConfirm') : 'Reset all navigation items to default?',
                confirmText: window.I18n?.t('common.confirm') || 'Confirm',
                cancelText: window.I18n?.t('common.cancel') || 'Cancel',
            }).then((confirmed) => {
                if (confirmed) {
                    NavPreferences.resetToDefaults();
                    renderNavButtons();
                    this.close();
                    this.showToast(window.I18n ? window.I18n.t('nav.resetToDefaults') : 'Navigation reset to defaults');
                }
            });
        } else {
            NavPreferences.resetToDefaults();
            renderNavButtons();
            this.close();
            this.showToast(window.I18n ? window.I18n.t('nav.resetToDefaults') : 'Navigation reset to defaults');
        }
    },

    /**
     * Close the modal without saving
     */
    close() {
        if (this._modal) {
            const content = this._modal.querySelector('.feature-menu-content');
            if (content) content.classList.remove('modal-content-active');

            setTimeout(() => {
                if (this._modal) this._modal.classList.add('hidden');
                this._tempPreferences = null;
            }, 200);
        }
    },

    /**
     * Show a toast notification (delegates to global showToast)
     */
    showToast(message) {
        if (typeof window.showToast === 'function') {
            window.showToast(message, 'success');
        }
    },
};

// Expose FeatureMenu globally
window.FeatureMenu = FeatureMenu;

// ========================================
// Application Initialization
// ========================================

// Initialize Greeting Time
const hour = new Date().getHours();
const greeting = hour < 12 ? 'morning' : hour < 18 ? 'afternoon' : 'evening';
const greetingTimeEl = document.getElementById('greeting-time');
if (greetingTimeEl) {
    greetingTimeEl.innerText = greeting;
}

document.addEventListener('DOMContentLoaded', async () => {
    // 非 SPA 頁守門（2026-08-24 messages.html#chat 事故）：boot 流程會動
    // body opacity、切 tab、注入 chat UI——在論壇頁跑等於直接摧毀版面。
    if (!document.getElementById('chat-tab')) {
        console.warn('[spa] SPA container (#chat-tab) missing — skip SPA boot (non-SPA page)');
        return;
    }
    window.APP_CONFIG?.DEBUG_MODE &&
        console.log('DOM fully loaded, starting controlled initialization...');

    // 不在這裡把 body 設透明再淡入（2026-09-27 拿掉）：defer 腳本跑完才到 DOMContentLoaded，
    // 首屏早就畫好了——設透明＝整頁閃一下，淡入期間的元素 LCP 也不算，訪客首屏被往後推。

    // 等待核心組件就緒的輔助函式
    const waitForGlobal = (key, timeout = 3000) => {
        return new Promise((resolve) => {
            if (window[key]) return resolve(window[key]);
            const start = Date.now();
            // ✅ 效能優化：polling 間隔從 100ms 降到 10ms，加快啟動速度
            const interval = setInterval(() => {
                if (window[key] || Date.now() - start > timeout) {
                    clearInterval(interval);
                    resolve(window[key]);
                }
            }, 10);
        });
    };

    // 確保核心腳本都已載入
    window.APP_CONFIG?.DEBUG_MODE && console.log('Waiting for core systems...');
    await Promise.all([
        waitForGlobal('Components').then(
            (v) => window.APP_CONFIG?.DEBUG_MODE && console.log('Components ready:', !!v)
        ),
        waitForGlobal('initializeAuth').then(
            (v) => window.APP_CONFIG?.DEBUG_MODE && console.log('Auth ready:', !!v)
        ),
        waitForGlobal('initializeUIStatus').then(
            (v) => window.APP_CONFIG?.DEBUG_MODE && console.log('UI ready:', !!v)
        ),
    ]);

    window.APP_CONFIG?.DEBUG_MODE &&
        console.log('Core systems ready status:', {
            Components: !!window.Components,
            Auth: !!window.initializeAuth,
            UI: !!window.initializeUIStatus,
        });

    // 0. 初始化 i18n（必須在 renderNavButtons 之前完成）
    if (window.I18n) {
        try {
            await window.I18n.init();
            window.APP_CONFIG?.DEBUG_MODE && console.log('i18n ready');
        } catch (e) {
            console.error('i18n Init Error:', e);
        }
    }

    // 1. 初始化認證系統（支援測試模式自動登入）
    if (typeof initializeAuth === 'function') {
        try {
            await initializeAuth();
        } catch (e) {
            console.error('Auth Init Error:', e);
        }
    }

    // 1.5 載入已保存的 API Key 狀態（必須在 Auth 完成後）
    if (typeof window.loadSavedApiKeys === 'function') {
        try {
            await window.loadSavedApiKeys();
        } catch (e) {
            console.error('Load API Keys Error:', e);
        }
    }

    // 2. Render navigation buttons based on user preferences
    renderNavButtons();

    // 2.5 初始化語系切換器
    if (window.LanguageSwitcher) {
        new LanguageSwitcher('.lang-switcher-container');
        // 登入 modal 內的獨立切換器：未登入的外國使用者也能先切語言
        if (document.querySelector('.login-lang-switcher')) {
            new LanguageSwitcher('.login-lang-switcher');
        }
        // 手機版頂欄：底欄 switcher 在手機被 CSS 隱藏，語言改從頂欄切
        if (document.querySelector('.header-lang-switcher')) {
            new LanguageSwitcher('.header-lang-switcher');
        }
    }

    // 2.6 初始化主題切換器（深淺）
    if (window.ThemeSwitcher) {
        new ThemeSwitcher('.theme-switcher-container');
        if (document.querySelector('.login-theme-switcher')) {
            new ThemeSwitcher('.login-theme-switcher');
        }
        // 手機版頂欄：同語言切換器，手機的主題改從頂欄切
        if (document.querySelector('.header-theme-switcher')) {
            new ThemeSwitcher('.header-theme-switcher');
        }
    }

    // 2.6 初始化通知組件（手機版 + 桌面版）
    if (window.NotificationBell && window.NotificationService) {
        const mobileBell = document.getElementById('notification-bell-mobile');
        const desktopBell = document.getElementById('notification-bell-desktop');
        if (mobileBell) {
            window.notificationBell = new NotificationBell(mobileBell);
        }
        if (desktopBell) {
            window.notificationBellDesktop = new NotificationBell(desktopBell);
        }
        window.APP_CONFIG?.DEBUG_MODE && console.log('NotificationBell initialized (global)');
    }

    // 監聽語言切換事件（i18n.js 一次切換只派發一次）
    window.addEventListener('languageChanged', () => {
        // 1. 導覽列標籤
        renderNavButtons();

        // 2. 重新渲染「目前正在看的分頁」，讓 JS 以 innerHTML 動態產生、
        //    沒有 data-i18n 屬性的內容也套用新語言（靜態 data-i18n 由
        //    i18n.js 的 updatePageContent 處理，動態內容則需重跑分頁 init）。
        //    重跑 executeTabSwitch 等同「切走再切回」，分頁 init 本就需可重入。
        //    - chat 由 chat-sessions.js 的歡迎畫面監聽器另行處理，避免干擾進行中的對話。

        // 已自行監聽 languageChanged 的分頁不重跑，避免重複渲染；
        // 且這些分頁的 init 會無條件再註冊 languageChanged，重跑會造成監聽器洩漏。
        // chat 由 chat-sessions.js 的歡迎畫面監聽器處理。
        // ai-studio 的 init 有一次性 _initialized 守衛，重跑 executeTabSwitch
        // 會被它直接 return（動態字串永遠停在初次語言）——改由它自己監聽
        // languageChanged 以快取重畫，這裡就不必再空跑一次分頁切換。
        const SELF_HANDLED = new Set(['chat', 'twstock', 'usstock',
    'journal', 'hkstock', 'ai-studio', 'scamcheck']);
        const active = getActiveTab();
        const loggedIn = !window.AuthManager || window.AuthManager.isLoggedIn();
        if (active && !SELF_HANDLED.has(active) && loggedIn && typeof executeTabSwitch === 'function') {
            // fromPopState=true：不污染瀏覽歷史
            Promise.resolve(executeTabSwitch(active, true)).catch((err) =>
                console.warn('Re-render active tab on language change failed:', err)
            );
        }
    });

    // 3. 載入預設分頁（優先順序：sessionStorage returnToTab > URL hash > localStorage）
    const returnToTab = sessionStorage.getItem('returnToTab');
    const hashTab = window.location.hash.replace('#', '');
    const savedTab = getActiveTab();
    const normalizedSavedTab = VALID_TABS.includes(savedTab) ? savedTab : 'chat';

    // 優先使用 returnToTab（從論壇返回），其次 hash，最後 localStorage
    let initialTab;
    // 群組深連結 /?group=<id>#friends（論壇頁的通知）、私訊深連結 /?chat=<userId>#friends（個人頁
    // 「發訊息」、舊的 messages.html 網址）：一定進好友頁，不讓 returnToTab（從論壇回來時記的分頁）搶走；
    // 開哪個由 SocialHub.openGroupFromUrl／openChatFromUrl 處理
    const _deepLinkParams = new URLSearchParams(window.location.search);
    const groupDeepLink = _deepLinkParams.get('group') || _deepLinkParams.get('chat');
    if (groupDeepLink && VALID_TABS.includes('friends')) {
        initialTab = 'friends';
        sessionStorage.removeItem('returnToTab');
    } else if (hashTab && !VALID_TABS.includes(hashTab)) {
        initialTab = 'chat';
        history.replaceState({ tab: 'chat' }, '', '#chat');
    } else if (returnToTab && VALID_TABS.includes(returnToTab)) {
        initialTab = returnToTab;
        sessionStorage.removeItem('returnToTab'); // 使用後清除
    } else if (VALID_TABS.includes(hashTab)) {
        initialTab = hashTab;
    } else {
        initialTab = normalizedSavedTab;
    }

    const loginModal = document.getElementById('login-modal');
    const shouldLockGuestLanding =
        AppStore.get('forceGuestLandingTab') === true ||
        (!!window.AuthManager &&
            !window.AuthManager.isLoggedIn() &&
            loginModal &&
            !loginModal.classList.contains('hidden'));
    if (shouldLockGuestLanding) {
        initialTab = 'chat';
    }

    // 2026-10-05 旗標（capabilities.crypto_tab）：只有第一個分頁屬於市場群組時才等 /api/config
    //（最多 3 秒），#crypto 深連結在旗標關閉時才不會先閃出加密貨幣；落地其他分頁不擋首屏、背景套用。
    // 上次的旗標在 nav-config 載入時已先套過（localStorage），等的通常只有第一次造訪或旗標剛變動的人。
    const _flagsReady = _applyFeatureFlags();
    if (initialTab === 'market' || (window.NavPreferences && window.NavPreferences.groupOf(initialTab) === 'market')) {
        await Promise.race([_flagsReady, new Promise((resolve) => setTimeout(resolve, 3000))]);
    }

    window.APP_CONFIG?.DEBUG_MODE &&
        console.log(
            'Initial tab switching to:',
            initialTab,
            '(returnTo:',
            returnToTab,
            ', hash:',
            hashTab,
            ', saved:',
            savedTab,
            ')'
        );

    // 清除 hash 並設置正確的初始歷史狀態
    history.replaceState({ tab: initialTab }, '', '#' + initialTab);

    try {
        await switchTab(initialTab, true); // fromPopState=true 避免重複 pushState
    } catch (e) {
        console.error('Initial Tab Error:', e);
    }

    // 3. 更新 UI 狀態
    if (typeof initializeUIStatus === 'function') {
        try {
            initializeUIStatus();
        } catch (e) {
            console.error('UI Init Error:', e);
        }
    }

    // 4. 延遲啟動 Ticker WebSocket（僅在 market/commodity/forex tab 時立即啟動）
    setTimeout(() => {
        const currentTab = getActiveTab();
        const marketTabs = ['market', 'crypto', 'twstock', 'usstock',
    'journal', 'commodity', 'forex'];
        if (marketTabs.includes(currentTab)) {
            if (typeof connectTickerWebSocket === 'function') connectTickerWebSocket();
        }
    }, 1000);

    // 5. 預加載 Market 和 Pulse 數據（僅在 market 相關 tab 時執行）
    setTimeout(async () => {
        const currentTab = getActiveTab();
        const marketTabs = ['market', 'crypto', 'twstock', 'usstock',
    'journal', 'commodity', 'forex'];
        if (!marketTabs.includes(currentTab)) return;

        window.APP_CONFIG?.DEBUG_MODE && console.log('Preloading market data...');
        // 動態載入的模組只能走 window（bare 識別字在模組作用域恆 undefined，
        // 2026-08-22 修：這裡曾因 bare typeof 靜默跳過導致 Pulse 預載從未執行）
        if (typeof window.initMarket === 'function') {
            await window.initMarket();
        }
        if (typeof window.initPulse === 'function') {
            await window.initPulse();
        }
        window.APP_CONFIG?.DEBUG_MODE && console.log('Market data preloaded');
    }, 2000);
});

// ========================================
// WebSocket Status Debugging
// ========================================

/**
 * Check and display WebSocket connection status
 */
function checkWebSocketStatus() {
    window.APP_CONFIG?.DEBUG_MODE && console.log('=== WebSocket Status ===');
    window.APP_CONFIG?.DEBUG_MODE &&
        console.log('Ticker WS Connected:', window.marketWsConnected || false);
    window.APP_CONFIG?.DEBUG_MODE &&
        console.log('K-line WS Connected:', window.wsConnected || false);
    window.APP_CONFIG?.DEBUG_MODE &&
        console.log('Ticker WS Object:', window.marketWebSocket ? 'Exists' : 'Not Found');
    window.APP_CONFIG?.DEBUG_MODE &&
        console.log('K-line WS Object:', window.klineWebSocket ? 'Exists' : 'Not Found');
    window.APP_CONFIG?.DEBUG_MODE &&
        console.log('Auto-refresh Enabled:', window.autoRefreshEnabled || false);
    window.APP_CONFIG?.DEBUG_MODE &&
        console.log('Current Chart Symbol:', window.currentChartSymbol || 'None');
    window.APP_CONFIG?.DEBUG_MODE &&
        console.log('Subscribed Ticker Symbols:', Array.from(window.subscribedTickerSymbols || []));
    window.APP_CONFIG?.DEBUG_MODE &&
        console.log('Pending Ticker Symbols:', Array.from(window.pendingTickerSymbols || []));
}
window.checkWebSocketStatus = checkWebSocketStatus;

export {
    switchTab,
    executeTabSwitch,
    navigateToForum,
    renderNavButtons,
    FeatureMenu,
    checkWebSocketStatus,
};
