// Auto-generated from components.js split
// Tab: Taiwan Stock - original lines 246-424
window.Components = window.Components || {};
window.Components.twstock = `
        <div class="${TAB_SHELL_CLASS}">
            <!-- Header with Sub-tab Switcher -->
            <div class="${TAB_HEADER_CLASS}">
                <h2 class="font-serif text-3xl text-secondary" data-i18n="twstock.title">TW Stock</h2>
                <div class="flex items-center gap-2">
                    <button data-click="TWStockTab.refreshCurrent" aria-label="Refresh" data-i18n="common.refresh" data-i18n-attr="aria-label" class="${ICON_ACTION_BUTTON_CLASS}">
                        <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                    </button>
                </div>
            </div>
            <div class="mb-4 flex flex-wrap items-center gap-2 text-xs">
                <span id="twstock-market-status-badge" class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border bg-surfaceHighlight text-textMuted border-borderLight font-bold">Checking schedule...</span>
                <span id="twstock-market-refresh-note" class="text-textMuted/80">Auto refresh policy loading</span>
                <span id="twstock-market-session-note" class="text-textMuted/60">Session info loading</span>
                <span id="twstock-last-updated" class="text-textMuted/60 md:ml-auto whitespace-nowrap">Waiting for first update</span>
            </div>

            <!-- Sub-tab Navigation (2 tabs only) -->
            <div class="${TAB_SWITCHER_CLASS}">
                <button id="twstock-btn-market" data-click="TWStockTab.switchSubTab" data-click-arg="market"
                    class="twstock-sub-tab ${SUB_TAB_BUTTON_BASE_CLASS} ${SUB_TAB_BUTTON_ACTIVE_CLASS}">
                    <i data-lucide="bar-chart-2" class="w-4 h-4"></i>
                    <span data-i18n="nav.market">Market Watch</span>
                </button>
                <button id="twstock-btn-pulse" data-click="TWStockTab.switchSubTab" data-click-arg="pulse"
                    class="twstock-sub-tab ${SUB_TAB_BUTTON_BASE_CLASS} ${SUB_TAB_BUTTON_INACTIVE_CLASS}">
                    <i data-lucide="activity" class="w-4 h-4"></i>
                    <span data-i18n="nav.pulse">AI Pulse</span>
                </button>
            </div>

            <!-- Content Area -->
            <div class="${TAB_CONTENT_AREA_CLASS}">

                <!-- Market Watch Container -->
                <div id="twstock-market-content" ${SHELL_SCROLL_ATTR} class="absolute inset-0 ${SHELL_SCROLLBAR_CLASS}">
                    <div class="space-y-8">

                        <!-- ① Watchlist section -->
                        <section>
                            <div class="${SECTION_HEADER_ROW_CLASS}">
                                <div class="${PRIMARY_DIVIDER_LEFT_CLASS}"></div>
                                <h3 class="${SECTION_TITLE_CLASS} text-primary">
                                    <i data-lucide="star" class="w-3 h-3 text-yellow-400"></i>
                                    <span data-i18n="twstock.watchlist">Watchlist</span>
                                </h3>
                                <div class="${PRIMARY_DIVIDER_RIGHT_CLASS}"></div>
                            </div>
                            <!-- Custom Watchlist Controls Area -->
                            <div id="twstock-screener-controls" class="px-1"></div>
                            <div id="twstock-market-loader" class="${LARGE_LOADER_BLOCK_CLASS}">
                                <div class="${PRIMARY_LARGE_SPINNER_CLASS}"></div>
                            </div>
                            <div id="twstock-screener-list" class="space-y-2 px-1"></div>
                            <!-- 我的警報：alerts.js renderAlertList 填入（鈴鐺建立的警報在這裡看／刪） -->
                            <div id="alert-list-section-twstock" class="hidden mt-4 px-1">
                                <h4 class="text-textMuted text-xs font-bold uppercase tracking-wider mb-2" data-i18n="modals.priceAlert.myAlerts">My Alerts</h4>
                                <div id="alert-list-twstock" class="space-y-1"></div>
                            </div>
                        </section>

                        <!-- ② PE / Valuation section -->
                        <section>
                            <div class="${SECTION_TOGGLE_ROW_CLASS}">
                                <div class="${PRIMARY_DIVIDER_LEFT_CLASS}"></div>
                                <button data-click="TWStockTab.toggleSection" data-click-arg="pe" class="${SECTION_TOGGLE_BUTTON_CLASS}">
                                    <h3 class="${SECTION_TITLE_CLASS} text-primary">
                                        <i data-lucide="percent" class="w-3 h-3"></i>
                                        <span data-i18n="twstock.valuationMetrics">Valuation Metrics (P/E · Yield · P/B)</span>
                                    </h3>
                                    <i id="twstock-chevron-pe" data-lucide="chevron-up" class="${PRIMARY_CHEVRON_CLASS}"></i>
                                </button>
                                <div class="${PRIMARY_DIVIDER_RIGHT_CLASS}"></div>
                            </div>
                            <div id="twstock-section-body-pe">
                                <div id="twstock-info-pe-loader" class="${LOADER_BLOCK_CLASS}">
                                    <div class="${PRIMARY_SPINNER_CLASS}"></div>
                                </div>
                                <div id="twstock-info-pe" class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 px-1"></div>
                            </div>
                        </section>

                        <!-- ③ Major Announcements section -->
                        <section>
                            <div class="${SECTION_TOGGLE_ROW_CLASS}">
                                <div class="${WARNING_DIVIDER_LEFT_CLASS}"></div>
                                <button data-click="TWStockTab.toggleSection" data-click-arg="news" class="${SECTION_TOGGLE_BUTTON_CLASS}">
                                    <h3 class="${SECTION_TITLE_CLASS} text-yellow-400">
                                        <i data-lucide="megaphone" class="w-3 h-3"></i>
                                        <span data-i18n="twstock.majorAnnouncements">Today's Major Announcements</span>
                                    </h3>
                                    <i id="twstock-chevron-news" data-lucide="chevron-up" class="${WARNING_CHEVRON_CLASS}"></i>
                                </button>
                                <div class="${WARNING_DIVIDER_RIGHT_CLASS}"></div>
                            </div>
                            <div id="twstock-section-body-news">
                                <div id="twstock-info-news-loader" class="${LOADER_BLOCK_CLASS}">
                                    <div class="${WARNING_SPINNER_CLASS}"></div>
                                </div>
                                <div id="twstock-info-news" class="space-y-2 px-1"></div>
                            </div>
                        </section>

                        <!-- ④ Dividend Calendar -->
                        <section>
                            <div class="${SECTION_TOGGLE_ROW_CLASS}">
                                <div class="${SUCCESS_DIVIDER_LEFT_CLASS}"></div>
                                <button data-click="TWStockTab.toggleSection" data-click-arg="dividend" class="${SECTION_TOGGLE_BUTTON_CLASS}">
                                    <h3 class="${SECTION_TITLE_CLASS} text-success">
                                        <i data-lucide="calendar-check" class="w-3 h-3"></i>
                                        <span data-i18n="twstock.dividendCalendar">Dividend Calendar</span>
                                    </h3>
                                    <i id="twstock-chevron-dividend" data-lucide="chevron-up" class="${SUCCESS_CHEVRON_CLASS}"></i>
                                </button>
                                <div class="${SUCCESS_DIVIDER_RIGHT_CLASS}"></div>
                            </div>
                            <div id="twstock-section-body-dividend">
                                <div id="twstock-info-div-loader" class="${LOADER_BLOCK_CLASS}">
                                    <div class="${SUCCESS_SPINNER_CLASS}"></div>
                                </div>
                                <div id="twstock-info-dividend" class="grid grid-cols-1 sm:grid-cols-2 gap-2 px-1"></div>
                            </div>
                        </section>

                        <!-- ⑤ Foreign Holding Top 20 -->
                        <section>
                            <div class="${SECTION_TOGGLE_ROW_CLASS}">
                                <div class="${ACCENT_DIVIDER_LEFT_CLASS}"></div>
                                <button data-click="TWStockTab.toggleSection" data-click-arg="foreign" class="${SECTION_TOGGLE_BUTTON_CLASS}">
                                    <h3 class="${SECTION_TITLE_CLASS} text-accent">
                                        <i data-lucide="globe" class="w-3 h-3"></i>
                                        <span data-i18n="twstock.foreignHoldingTop20">Foreign Holdings Top 20</span>
                                    </h3>
                                    <i id="twstock-chevron-foreign" data-lucide="chevron-up" class="${ACCENT_CHEVRON_CLASS}"></i>
                                </button>
                                <div class="${ACCENT_DIVIDER_RIGHT_CLASS}"></div>
                            </div>
                            <div id="twstock-section-body-foreign">
                                <div id="twstock-info-foreign-loader" class="${LOADER_BLOCK_CLASS}">
                                    <div class="${ACCENT_SPINNER_CLASS}"></div>
                                </div>
                                <div id="twstock-info-foreign" class="overflow-x-auto rounded-2xl border border-borderSubtle px-1"></div>
                            </div>
                        </section>

                    </div>
                </div>

                <!-- AI Pulse Container -->
                <div id="twstock-pulse-content" ${SHELL_SCROLL_ATTR} class="absolute inset-0 ${SHELL_SCROLLBAR_CLASS} hidden">
                    <div class="max-w-5xl mx-auto pt-4 px-2">
                        <!-- Premium Search Input for Pulse -->
                        <div class="relative mb-8">
                            <div class="${SEARCH_SHELL_GLOW_CLASS}"></div>
                            <div class="${SEARCH_SHELL_PANEL_CLASS}">
                                <div class="${SEARCH_ICON_WRAP_CLASS}">
                                    <i data-lucide="search" class="w-5 h-5 text-primary/70"></i>
                                </div>
                                <input type="text" id="twstockPulseSearchInput" data-i18n="twstock.searchPlaceholder" data-i18n-attr="placeholder" placeholder="Enter a Taiwan ticker (e.g. 2330)" data-i18n="twstock.searchPlaceholder" data-i18n-attr="placeholder"
                                    class="${SEARCH_INPUT_CLASS}">
                                <button id="twstockPulseSearchBtn" class="${SEARCH_ACTION_BUTTON_CLASS}">
                                    <span class="hidden sm:inline" data-i18n="common.deepAnalysis">Deep Analysis</span>
                                    <i data-lucide="zap" class="w-4 h-4"></i>
                                </button>
                            </div>
                        </div>

                        <div id="twstock-pulse-loader" class="hidden items-center justify-center py-20 flex flex-col">
                            <div class="w-16 h-16 relative">
                                <div class="absolute inset-0 border-4 border-primary/20 rounded-full"></div>
                                <div class="absolute inset-0 border-4 border-primary rounded-full border-t-transparent animate-spin"></div>
                                <i data-lucide="database" class="absolute inset-0 m-auto w-6 h-6 text-primary"></i>
                            </div>
                            <p class="text-[11px] text-primary font-bold tracking-widest uppercase mt-6 animate-pulse" data-i18n="twstock.analyzingChip">Loading market data...</p>
                        </div>

                        <div id="twstock-pulse-result" class="space-y-6 hidden"></div>
                    </div>
                </div>
            </div>
        </div>
    `;

// Side-effect module — assigns to window.Components
export {};
