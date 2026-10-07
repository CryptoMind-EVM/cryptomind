// ========================================
// forum.js - 論壇功能核心邏輯
// ========================================

// ============================================
// 論壇價格（USD，USDC on Base 付款；從後端動態獲取）
// 2026-09-25 論壇付款改 USDC on Base：/api/premium/pricing 的 forum.prices
// （create_post＝發文費、tip＝預設打賞、tip_min／tip_max＝打賞範圍）。
// 實際金額由建單端點簽進訂單，這裡只負責顯示。
// ============================================
const _defaultForumPrices = {
    create_post: null, // 完全依賴後端配置
    tip: null,
    tip_min: null,
    tip_max: null,
    loaded: false,
};
// AppStore is optional (not loaded on all pages); always keep window.ForumPrices in sync
window.ForumPrices = _defaultForumPrices;
if (typeof AppStore !== 'undefined') AppStore.set('ForumPrices', _defaultForumPrices);

function _setForumPrices(value) {
    window.ForumPrices = value;
    if (typeof AppStore !== 'undefined') AppStore.set('ForumPrices', value);
}

// 顯示用：0.5 → "0.50 USDC"
function formatUsdc(value) {
    return `${Number(value).toFixed(2)} USDC`;
}

// ============================================
// 論壇限制配置（從後端動態獲取）
// ============================================
const _defaultForumLimits = {
    daily_post_free: null,
    daily_post_premium: null,
    daily_comment_free: null,
    daily_comment_premium: null,
    loaded: false,
};
window.ForumLimits = _defaultForumLimits;
if (typeof AppStore !== 'undefined') AppStore.set('ForumLimits', _defaultForumLimits);

// 從後端載入論壇價格（/api/premium/pricing 的 forum.prices，單位 USD）
async function loadForumPrices() {
    if (window.ForumPrices.loading) return; // Prevent concurrent requests
    _setForumPrices({ ...window.ForumPrices, loading: true });

    try {
        // 跟 premium.js 共用同一個請求（AppAPI.getPremiumPricing）
        const data =
            typeof AppAPI.getPremiumPricing === 'function'
                ? await AppAPI.getPremiumPricing()
                : await AppAPI.get('/api/premium/pricing');
        const forum = (data && data.forum && data.forum.prices) || {};
        const updated = {
            create_post: forum.create_post ?? null,
            tip: forum.tip ?? null,
            tip_min: forum.tip_min ?? null,
            tip_max: forum.tip_max ?? null,
            loaded: true,
            loading: false,
        };
        _setForumPrices(updated);
        // 更新頁面上的價格顯示
        updatePriceDisplays();
        // 通知其他模組價格已更新
        document.dispatchEvent(new Event('forum-prices-updated'));
    } catch (e) {
        console.error(window.I18n.t('forum.loadPricesFailed') || '[Forum] 載入價格配置失敗:', e);
        _setForumPrices({ ...window.ForumPrices, loading: false });
    }
}

// 舊名（spa.js settings 載入器與 forum-app.js 用這個名字 import）
const loadPiPrices = loadForumPrices;

function updatePriceDisplays() {
    // 更新所有帶有 data-price 屬性的元素
    const prices = window.ForumPrices;
    if (!prices || !prices.loaded) {
        console.log(window.I18n.t('forum.pricesNotLoaded') || '[Forum] 價格尚未載入，跳過更新');
        return;
    }

    const priceElements = document.querySelectorAll('[data-price]');
    priceElements.forEach((el) => {
        const priceKey = el.getAttribute('data-price');
        // 訂閱價（premium/premium_yearly）是 USD 錨定、只收 USDC on Base
        // （2026-09-09 統一）——顯示擁有者是 PremiumManager.updatePriceDisplay，
        // 這裡不覆寫同一個 [data-price="premium"]。
        if (priceKey === 'premium' || priceKey === 'premium_yearly') return;
        const price = prices[priceKey];

        if (price !== undefined && price !== null) {
            el.textContent = formatUsdc(price);
        }
    });

    console.log(window.I18n.t('forum.pricesDisplayUpdated') || '[Forum] 價格顯示已更新:', prices);
}

// 從後端載入論壇限制配置
async function loadForumLimits() {
    if (window.ForumLimits.loading) return;
    window.ForumLimits = { ...window.ForumLimits, loading: true };
    if (typeof AppStore !== 'undefined') AppStore.set('ForumLimits', window.ForumLimits);

    try {
        const data = await AppAPI.get('/api/config/limits');
        const updated = { ...data.limits, loaded: true, loading: false };
        window.ForumLimits = updated;
        if (typeof AppStore !== 'undefined') AppStore.set('ForumLimits', updated);
        console.log(window.I18n.t('forum.limitsLoaded') || '[Forum] 論壇限制配置已載入:', updated);
        // 通知其他模組限制已更新
        document.dispatchEvent(new Event('forum-limits-updated'));
    } catch (e) {
        console.error(window.I18n.t('forum.loadLimitsFailed') || '[Forum] 載入論壇限制配置失敗:', e);
        window.ForumLimits = { ...window.ForumLimits, loading: false };
        if (typeof AppStore !== 'undefined') AppStore.set('ForumLimits', window.ForumLimits);
    }
}

// 取得價格的輔助函數（確保有值）
function getPrice(key) {
    const prices = window.ForumPrices;
    if (prices?.loaded && prices[key] !== null) {
        return prices[key];
    }
    console.warn(window.I18n.t('forum.priceNotLoaded', { key }) || `[Forum] 價格 ${key} 尚未載入，請確認 API 連線`);
    return null;
}

// 取得限制的輔助函數（確保有值）
function getLimit(key) {
    const limits = window.ForumLimits;
    if (limits?.loaded && limits[key] !== undefined) {
        return limits[key];
    }
    console.warn(window.I18n.t('forum.limitNotLoaded', { key }) || `[Forum] 限制 ${key} 尚未載入，請確認 API 連線`);
    return null;
}

// Helper to format date
function formatTWDate(dateStr, full = false) {
    if (!dateStr) return '';
    try {
        // Server stores UTC — append 'Z' if no timezone info so JS parses as UTC
        let normalized = dateStr;
        if (
            typeof dateStr === 'string' &&
            !dateStr.endsWith('Z') &&
            !dateStr.includes('+') &&
            !/\d{2}:\d{2}:\d{2}-/.test(dateStr)
        ) {
            normalized = dateStr.replace(' ', 'T') + 'Z';
        }
        const date = new Date(normalized);
        const now = new Date();
        const diff = now - date;

        // Less than 24 hours
        if (diff < 86400000 && !full) {
            if (diff < 3600000) return Math.max(1, Math.floor(diff / 60000)) + (window.I18n ? window.I18n.t('forum.minutesAgo') : 'm ago');
            return Math.floor(diff / 3600000) + (window.I18n ? window.I18n.t('forum.hoursAgo') : 'h ago');
        }

        // Format: MM/DD or YYYY/MM/DD HH:mm
        const year = date.getFullYear();
        const month = (date.getMonth() + 1).toString().padStart(2, '0');
        const day = date.getDate().toString().padStart(2, '0');
        const hours = date.getHours().toString().padStart(2, '0');
        const minutes = date.getMinutes().toString().padStart(2, '0');

        if (full) return `${year}/${month}/${day} ${hours}:${minutes}`;
        return `${month}/${day}`;
    } catch (e) {
        return dateStr;
    }
}

export { loadPiPrices, loadForumPrices, loadForumLimits, getPrice, getLimit, formatTWDate, formatUsdc };
