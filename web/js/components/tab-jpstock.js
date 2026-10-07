// Tab: JP Stock 日股
window.Components = window.Components || {};
window.Components.jpstock = `
        <div class="${TAB_SHELL_CLASS}">
            <div class="${TAB_HEADER_CLASS}">
                <h2 class="font-serif text-3xl text-secondary" data-i18n="nav.jpstock">JP Stock</h2>
                <div class="flex items-center gap-2">
                    <button data-click="JPStockTab.refreshCurrent" aria-label="Refresh" data-i18n="common.refresh" data-i18n-attr="aria-label" class="${ICON_ACTION_BUTTON_CLASS}">
                        <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                    </button>
                </div>
            </div>
            <div class="mb-4 flex flex-wrap items-center gap-2 text-xs">
                <span id="jpstock-market-status-badge" class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border bg-surfaceHighlight text-textMuted border-borderLight font-bold">Checking schedule...</span>
                <span id="jpstock-market-refresh-note" class="text-textMuted/80">Auto refresh policy loading</span>
                <span id="jpstock-market-session-note" class="text-textMuted/60">Session info loading</span>
                <span id="jpstock-last-updated" class="text-textMuted/60 md:ml-auto whitespace-nowrap">Waiting for first update</span>
            </div>

            <div class="${TAB_SWITCHER_CLASS}">
                <button id="jpstock-btn-market" data-click="JPStockTab.switchSubTab" data-click-arg="market"
                    class="jpstock-sub-tab ${SUB_TAB_BUTTON_BASE_CLASS} ${SUB_TAB_BUTTON_ACTIVE_CLASS}">
                    <i data-lucide="bar-chart-2" class="w-4 h-4"></i>
                    <span data-i18n="nav.market">Market Watch</span>
                </button>
                <button id="jpstock-btn-pulse" data-click="JPStockTab.switchSubTab" data-click-arg="pulse"
                    class="jpstock-sub-tab ${SUB_TAB_BUTTON_BASE_CLASS} ${SUB_TAB_BUTTON_INACTIVE_CLASS}">
                    <i data-lucide="activity" class="w-4 h-4"></i>
                    <span data-i18n="nav.pulse">AI Pulse</span>
                </button>
            </div>

            <div class="${TAB_CONTENT_AREA_CLASS}">
                <div id="jpstock-market-content" ${SHELL_SCROLL_ATTR} class="absolute inset-0 ${SHELL_SCROLLBAR_CLASS}">
                    <div class="space-y-8">
                        <section>
                            <div class="${SECTION_HEADER_ROW_CLASS}">
                                <div class="${PRIMARY_DIVIDER_LEFT_CLASS}"></div>
                                <h3 class="${SECTION_TITLE_CLASS} text-primary">
                                    <i data-lucide="star" class="w-3 h-3 text-yellow-400"></i>
                                    <span data-i18n="jpstock.watchlist">Japan watchlist</span>
                                </h3>
                                <div class="${PRIMARY_DIVIDER_RIGHT_CLASS}"></div>
                            </div>
                            <div id="jpstock-screener-controls" class="px-1"></div>
                            <div id="jpstock-market-loader" class="${LARGE_LOADER_BLOCK_CLASS}">
                                <div class="${PRIMARY_RING_SPINNER_CLASS}"></div>
                            </div>
                            <div id="jpstock-screener-list" class="space-y-2 px-1"></div>
                        </section>

                        <section>
                            <div class="${SECTION_TOGGLE_ROW_CLASS}">
                                <div class="${PRIMARY_DIVIDER_LEFT_CLASS}"></div>
                                <button data-click="JPStockTab.toggleSection" data-click-arg="indices" class="${SECTION_TOGGLE_BUTTON_CLASS}">
                                    <h3 class="${SECTION_TITLE_CLASS} text-primary">
                                        <i data-lucide="trending-up" class="w-3 h-3"></i>
                                        <span data-i18n="jpstock.indices">Japan indices</span>
                                    </h3>
                                    <i id="jpstock-chevron-indices" data-lucide="chevron-up" class="${PRIMARY_CHEVRON_CLASS}"></i>
                                </button>
                                <div class="${PRIMARY_DIVIDER_RIGHT_CLASS}"></div>
                            </div>
                            <div id="jpstock-section-body-indices">
                                <div id="jpstock-info-indices-loader" class="${LOADER_BLOCK_CLASS}">
                                    <div class="${PRIMARY_RING_SPINNER_CLASS}"></div>
                                </div>
                                <div id="jpstock-info-indices" class="grid grid-cols-1 sm:grid-cols-3 gap-3 px-1">
                                    <div class="${LOADING_PLACEHOLDER_CLASS}" data-i18n="common.loading">Loading...</div>
                                </div>
                            </div>
                        </section>

                        <section>
                            <div class="${SECTION_TOGGLE_ROW_CLASS}">
                                <div class="${WARNING_DIVIDER_LEFT_CLASS}"></div>
                                <button data-click="JPStockTab.toggleSection" data-click-arg="news" class="${SECTION_TOGGLE_BUTTON_CLASS}">
                                    <h3 class="${SECTION_TITLE_CLASS} text-yellow-400">
                                        <i data-lucide="newspaper" class="w-3 h-3"></i>
                                        <span data-i18n="jpstock.news">Japan stock news</span>
                                    </h3>
                                    <i id="jpstock-chevron-news" data-lucide="chevron-up" class="${WARNING_CHEVRON_CLASS}"></i>
                                </button>
                                <div class="${WARNING_DIVIDER_RIGHT_CLASS}"></div>
                            </div>
                            <div id="jpstock-section-body-news">
                                <div id="jpstock-info-news-loader" class="${LOADER_BLOCK_CLASS}">
                                    <div class="${WARNING_RING_SPINNER_CLASS}"></div>
                                </div>
                                <div id="jpstock-info-news" class="space-y-2 px-1">
                                    <div class="${LOADING_PLACEHOLDER_CLASS}" data-i18n="common.loading">Loading...</div>
                                </div>
                            </div>
                        </section>
                    </div>
                </div>

                <div id="jpstock-pulse-content" ${SHELL_SCROLL_ATTR} class="absolute inset-0 ${SHELL_SCROLLBAR_CLASS} hidden">
                    <div class="max-w-5xl mx-auto pt-4 px-2">
                        <div class="relative mb-8">
                            <div class="${SEARCH_SHELL_GLOW_CLASS}"></div>
                            <div class="${SEARCH_SHELL_PANEL_CLASS}">
                                <div class="${SEARCH_ICON_WRAP_CLASS}">
                                    <i data-lucide="search" class="w-5 h-5 text-primary/70"></i>
                                </div>
                                <input type="text" id="jpstockPulseSearchInput" data-i18n="jpstock.searchPlaceholder" data-i18n-attr="placeholder" placeholder="Enter a Japan ticker (e.g. 7203.T)" data-i18n="jpstock.searchPlaceholder" data-i18n-attr="placeholder"
                                    class="${SEARCH_INPUT_CLASS}">
                                <button id="jpstockPulseSearchBtn" class="${SEARCH_ACTION_BUTTON_CLASS}">
                                    <span class="hidden sm:inline" data-i18n="common.deepAnalysis">Deep Analysis</span>
                                    <i data-lucide="zap" class="w-4 h-4"></i>
                                </button>
                            </div>
                        </div>

                        <div id="jpstock-pulse-loader" class="hidden items-center justify-center py-20 flex flex-col">
                            <div class="w-16 h-16 relative">
                                <div class="absolute inset-0 border-4 border-primary/20 rounded-full"></div>
                                <div class="absolute inset-0 border-4 border-primary rounded-full border-t-transparent animate-spin"></div>
                                <i data-lucide="database" class="absolute inset-0 m-auto w-6 h-6 text-primary"></i>
                            </div>
                            <p class="text-[11px] text-primary font-bold tracking-widest uppercase mt-6 animate-pulse" data-i18n="twstock.analyzingChip">Loading market data...</p>
                        </div>

                        <div id="jpstock-pulse-result" class="space-y-6 hidden"></div>
                    </div>
                </div>
            </div>
        </div>
    `;

export {};
