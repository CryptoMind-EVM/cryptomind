// Auto-generated from components.js split
// Tab: Wallet Monitor — 錢包監測 dashboard（警示設定 + 事件時間軸 + 持倉總覽）
window.Components = window.Components || {};
// 用連字號 key（與 nav id 'wallet-monitor' 一致）——inject 用 this[id] 查詢，
// camelCase 'walletMonitor' 會查不到（inject failed: template = false）。
window.Components['wallet-monitor'] = `
        <div class="${TAB_SHELL_CLASS}">
            <!-- Header -->
            <div class="${TAB_HEADER_CLASS}">
                <h2 class="font-serif text-3xl text-secondary" data-i18n="walletMonitor.title">Wallet Monitor</h2>
                <div class="flex items-center gap-2">
                    <button data-click="WalletMonitorTab.refresh" aria-label="Refresh" data-i18n="common.refresh" data-i18n-attr="aria-label" class="${ICON_ACTION_BUTTON_CLASS}">
                        <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                    </button>
                </div>
            </div>

            <!-- Content Area -->
            <div class="${TAB_CONTENT_AREA_CLASS}">
                <div id="wallet-monitor-body" ${SHELL_SCROLL_ATTR} class="absolute inset-0 ${SHELL_SCROLLBAR_CLASS}">
                    <p class="text-sm text-textMuted text-center py-8">Loading...</p>
                </div>
            </div>
        </div>
`;
