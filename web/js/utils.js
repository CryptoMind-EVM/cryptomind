/**
 * Shared Utility Functions
 *
 * Single source of truth for common helpers used across all modules.
 * This file MUST be loaded before any other JS file in index.html.
 */
import './lucide-guard.js'; // lucide 防整頁重畫（refreshIcons／createIconsIn 都受惠，見檔頭）
const AppUtils = {
    /**
     * Escape HTML special characters to prevent XSS.
     * Safe for use in both text content and HTML attribute values.
     * @param {string} str
     * @returns {string}
     */
    escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    },

    /**
     * Sanitize a URL to prevent javascript:/data:/vbscript: injection.
     * @param {string} url
     * @returns {string}
     */
    sanitizeUrl(url) {
        if (!url) return '#';
        var trimmed = String(url).trim();
        if (
            trimmed.startsWith('javascript:') ||
            trimmed.startsWith('data:') ||
            trimmed.startsWith('vbscript:')
        ) {
            return '#';
        }
        return trimmed;
    },

    /**
     * i18n translation helper — wraps the verbose inline fallback pattern.
     * Returns the translated string or falls back to the key itself.
     * @param {string} key - i18next key
     * @param {object} [options] - i18next interpolation options
     * @returns {string}
     */
    t(key, options) {
        if (window.I18n && typeof window.I18n.t === 'function') {
            return window.I18n.t(key, options);
        }
        return key;
    },

    /**
     * Refresh Lucide icons within a specific container (scoped).
     * Falls back to global scan if no container is provided.
     * @param {HTMLElement} [container] - scope to specific element
     */
    refreshIcons(container) {
        if (!window.lucide || typeof window.lucide.createIcons !== 'function') return;
        if (container) {
            window.lucide.createIcons({ nodes: [container] });
        } else {
            window.lucide.createIcons();
        }
    },

    /**
     * BCP-47 locale matching the current app language, for Date.toLocale*()
     * and chart libraries. Prevents the browser locale (e.g. Chinese 下午 /
     * 日) from leaking into the English UI.
     * @returns {string}
     */
    locale() {
        return (window.I18n?.getLanguage?.() || 'zh-TW') === 'en' ? 'en-US' : 'zh-TW';
    },
};

window.AppUtils = AppUtils;
window.escapeHtml = AppUtils.escapeHtml;
window.sanitizeUrl = AppUtils.sanitizeUrl;
window._t = AppUtils.t;
window.t = AppUtils.t;

// ── AppCache：共用 TTL 記憶體快取 ──────────────────────────────────────────
// 用法：AppCache.set(key, data, ttlMs)  AppCache.get(key)  AppCache.clear(key)
const AppCache = {
    _store: {},
    /** 儲存資料，ttlMs 預設 5 分鐘 */
    set(key, data, ttlMs = 5 * 60 * 1000) {
        this._store[key] = { data, expires: Date.now() + ttlMs };
    },
    /** 取得資料；已過期或不存在回傳 null */
    get(key) {
        const entry = this._store[key];
        if (!entry) return null;
        if (Date.now() > entry.expires) { delete this._store[key]; return null; }
        return entry.data;
    },
    /** 是否有未過期的快取 */
    has(key) { return this.get(key) !== null; },
    /** 清除特定 key（或不傳清全部） */
    clear(key) {
        if (key) delete this._store[key];
        else this._store = {};
    },
    /** 格式化快取存入時間（用於顯示 "上次更新 HH:MM"） */
    timestamp(key) {
        const entry = this._store[key];
        if (!entry) return '';
        const saved = new Date(entry.expires - (entry.expires - Date.now()) - 0);
        // entry.expires - ttl = saved time，但我們只需顯示「現在 - 剩餘」
        // 直接記錄存入時間更準確；此處用 expires 推算（ttl 未知則顯示剩餘秒）
        return '';
    },
    /** 直接記錄存入時間，供顯示用 */
    savedAt: {},
    setWithTime(key, data, ttlMs = 5 * 60 * 1000) {
        this.set(key, data, ttlMs);
        this.savedAt[key] = Date.now();
    },
    getTimeStr(key) {
        const t = this.savedAt[key];
        if (!t) return '';
        return new Date(t).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' });
    },
};

/**
 * 站內圖片 lightbox（取代 window.open(dataURL) 的裸白分頁）。
 * 主題一致（bg-black/90 背景板）、點背景或按 Esc 關閉、圖片最大 92vw/88vh 置中。
 */
function showImageLightbox(src) {
    if (!src) return;
    const old = document.getElementById('app-image-lightbox');
    if (old) old.remove();
    const overlay = document.createElement('div');
    overlay.id = 'app-image-lightbox';
    overlay.className =
        'fixed inset-0 z-[90] bg-black/90 flex items-center justify-center cursor-zoom-out';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-label', 'image preview');
    const img = document.createElement('img');
    img.src = src;
    img.alt = 'preview';
    img.className = 'max-w-[92vw] max-h-[88vh] rounded-xl object-contain shadow-2xl';
    overlay.appendChild(img);
    overlay.addEventListener('click', () => overlay.remove());
    document.body.appendChild(overlay);
    const onKey = (e) => {
        if (e.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', onKey); }
    };
    document.addEventListener('keydown', onKey);
}
window.showImageLightbox = showImageLightbox;

window.AppCache = AppCache;

export { AppUtils, AppCache };
