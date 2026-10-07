/**
 * Trust Tab — 可信分數 + EVM 綁定（全面重組 Phase 1，從 Settings 搬出）。
 *
 * 薄包裝層：實際邏輯在 TrustScoreManager（trustScoreManager.js），
 * 這裡只指定 container（trust-tab-body）並提供 tab 專用 refresh。
 * 分層揭露：自己看完整明細；徽章外顯等 forum 開放（軌道 B1）。
 */
window.TrustTab = (function () {
    function init() {
        if (window.TrustScoreManager && typeof window.TrustScoreManager.init === 'function') {
            window.TrustScoreManager.init('trust-tab-body');
        }
    }

    function refresh() {
        if (window.TrustScoreManager && typeof window.TrustScoreManager.load === 'function') {
            window.TrustScoreManager.load();
        }
    }

    return { init: init, refresh: refresh };
})();
