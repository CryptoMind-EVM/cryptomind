// Auto-generated from components.js split
// Tab: Trust — 可信分數 + EVM 綁定（從 Settings 搬出，全面重組 Phase 1）
window.Components = window.Components || {};
window.Components.trust = `
        <div class="${TAB_SHELL_CLASS}">
            <!-- Header -->
            <div class="${TAB_HEADER_CLASS}">
                <h2 class="font-serif text-3xl text-secondary" data-i18n="trust.title">Trust</h2>
                <div class="flex items-center gap-2">
                    <button data-click="TrustTab.refresh" aria-label="Refresh" data-i18n="common.refresh" data-i18n-attr="aria-label" class="${ICON_ACTION_BUTTON_CLASS}">
                        <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                    </button>
                </div>
            </div>

            <!-- Content Area -->
            <div class="${TAB_CONTENT_AREA_CLASS}">
                <div id="trust-tab-body" ${SHELL_SCROLL_ATTR} class="absolute inset-0 ${SHELL_SCROLLBAR_CLASS}">
                    <!-- 內容由 trustScoreManager 渲染（含外層 max-w 容器，對齊 settings/wallet 版面） -->
                    <p class="text-sm text-textMuted text-center py-8" data-i18n="trust.loading">Loading...</p>
                </div>
            </div>
        </div>
`;
