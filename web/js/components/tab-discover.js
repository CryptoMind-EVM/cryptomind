// Auto-generated from components.js split
// Tab: Discover — People & Projects 探索（Phase 3，flag: PEOPLE_PROJECTS_DISCOVER_ENABLED）
// 規格見 docs/plans/2026-08-14-general-agent-platform-ai-studio-manifund-design.md §10
// 出資者旅程 P0-2/P0-3（2026-08-15 design）：為我推薦＋專案詳情＋AI 出資者視角評估
window.Components = window.Components || {};
window.Components.discover = `
        <div class="${TAB_SHELL_CLASS}">
            <!-- Header -->
            <div class="${TAB_HEADER_CLASS}">
                <h2 class="font-serif text-3xl text-secondary" data-i18n="discover.title">Discover</h2>
                <div class="flex items-center gap-2">
                    <button data-click="DiscoverTab.refresh" class="${ICON_ACTION_BUTTON_CLASS}" aria-label="refresh" data-i18n="common.refresh" data-i18n-attr="aria-label">
                        <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                    </button>
                </div>
            </div>

            <!-- Content Area -->
            <div class="${TAB_CONTENT_AREA_CLASS}">
                <div id="discover-tab-body" ${SHELL_SCROLL_ATTR} class="absolute inset-0 ${SHELL_SCROLLBAR_CLASS}">
                    <div class="max-w-4xl mx-auto pb-10">

                        <!-- 資料外傳提示（首次啟用，design §15.2） -->
                        <div id="discover-disclosure" class="hidden mb-4 p-4 rounded-xl border border-danger/40 bg-danger/10 text-sm">
                            <p class="font-medium mb-1" data-i18n="discover.disclosureTitle">資料外傳說明</p>
                            <p class="text-textMuted" data-i18n="discover.disclosureBody">
                                搜尋文字、興趣與預算會送往 Manifund 以取得專案資料；不會傳送您的 API 金鑰、錢包簽章或私人記憶。
                            </p>
                            <button data-click="DiscoverTab.ackDisclosure" class="mt-2 px-3 py-1.5 rounded-lg bg-surfaceHighlight text-sm" data-i18n="discover.disclosureAck">我了解了</button>
                        </div>

                        <!-- 降級橫幅（MCP 不可用，design §10.10） -->
                        <div id="discover-degraded" class="hidden mb-4 p-3 rounded-xl border border-destructive/40 bg-destructive/10 text-sm flex items-center justify-between gap-3">
                            <span class="flex items-center gap-2">
                                <i data-lucide="cloud-off" class="w-4 h-4"></i>
                                <span data-i18n="discover.degraded">Manifund 資料暫時無法取得</span>
                            </span>
                            <button data-click="DiscoverTab.refresh" class="px-3 py-1 rounded-lg bg-surfaceHighlight text-sm" data-i18n="discover.retry">稍後再試</button>
                        </div>

                        <!-- 搜尋列 -->
                        <div class="flex gap-2 mb-4">
                            <div class="flex-1 relative">
                                <i data-lucide="search" class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-textMuted"></i>
                                <input id="discover-search-input" type="text"
                                    class="w-full pl-9 pr-3 py-2.5 rounded-xl bg-surface border border-borderSubtle/10 text-sm"
                                    data-i18n="discover.searchPlaceholder" data-i18n-attr="placeholder"
                                    data-i18n="discover.searchPlaceholder" data-i18n-attr="placeholder" placeholder="搜尋專案、人物、Cause…">
                            </div>
                            <button data-click="DiscoverTab.search" class="px-4 py-2.5 rounded-xl bg-primary text-background text-sm font-medium whitespace-nowrap" data-i18n="discover.search">搜尋</button>
                        </div>

                        <!-- 為我推薦（出資者旅程 P0-2，design 2026-08-15） -->
                        <div class="mb-4 p-4 rounded-xl border border-borderSubtle/10 bg-surface">
                            <div class="flex items-center gap-2 mb-3">
                                <i data-lucide="sparkles" class="w-4 h-4 text-primary"></i>
                                <h3 class="text-sm font-medium text-secondary" data-i18n="discover.recommend.title">為我推薦</h3>
                            </div>
                            <div class="flex flex-col sm:flex-row gap-2">
                                <input id="discover-recommend-interests" type="text"
                                    class="flex-1 px-3 py-2 rounded-lg bg-surfaceHighlight border border-borderSubtle/10 text-sm"
                                    data-i18n="discover.recommend.interestsPlaceholder" data-i18n-attr="placeholder"
                                    data-i18n="discover.recommend.interestsPlaceholder" data-i18n-attr="placeholder" placeholder="興趣，例如：AI safety、預測市場…">
                                <input id="discover-recommend-budget" type="number" min="0" step="1"
                                    class="sm:w-36 px-3 py-2 rounded-lg bg-surfaceHighlight border border-borderSubtle/10 text-sm"
                                    data-i18n="discover.recommend.budgetPlaceholder" data-i18n-attr="placeholder"
                                    data-i18n="discover.recommend.budgetPlaceholder" data-i18n-attr="placeholder" placeholder="預算 USD（選填）">
                                <button data-click="DiscoverTab.recommend"
                                    class="px-4 py-2 rounded-lg bg-primary text-background text-sm font-medium whitespace-nowrap"
                                    data-i18n="discover.recommend.button">推薦專案</button>
                            </div>
                            <p class="mt-2 text-xs text-textMuted" data-i18n="discover.recommend.hint">留空則依你的收藏推薦。</p>
                            <div id="discover-recommend-status" class="hidden mt-3 text-sm text-textMuted"></div>
                        </div>

                        <!-- 補助機會表（curated；無 API 平台涵蓋網，design 2026-08-17 §Phase 0） -->
                        <div id="discover-opportunities-section" class="mb-4 p-4 rounded-xl border border-borderSubtle/10 bg-surface hidden">
                            <div class="flex items-center gap-2 mb-1">
                                <i data-lucide="landmark" class="w-4 h-4 text-danger"></i>
                                <h3 class="text-sm font-medium text-secondary" data-i18n="discover.opportunities.title">補助機會</h3>
                            </div>
                            <p class="text-xs text-textMuted mb-3" data-i18n="discover.opportunities.subtitle">
                                沒有 API 的募資/補助平台整理（EA Funds、SFF、ACX、TON 生態）——常態維護，死線過期自動標記。
                            </p>
                            <div id="discover-opportunities" class="grid grid-cols-1 md:grid-cols-2 gap-3"></div>
                        </div>

                        <!-- 結果區 -->
                        <div id="discover-status" class="hidden text-center py-10 text-sm text-textMuted"></div>
                        <div id="discover-recommend-results" class="hidden grid grid-cols-1 md:grid-cols-2 gap-4"></div>
                        <div id="discover-search-hint" class="hidden mb-3 text-xs text-textMuted bg-surfaceHighlight/50 border border-borderSubtle/10 rounded-lg px-3 py-2" data-i18n="discover.searchWeakMatch">沒有精確符合的專案——以下為相關度最高的結果。</div>
                        <div id="discover-results" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>

                        <!-- 收藏列表（Priority 1 輕量標記，design §10.8） -->
                        <div id="discover-favorites-section" class="mt-8 hidden">
                            <div class="flex items-center justify-between mb-3">
                                <h3 class="text-lg font-medium flex items-center gap-2">
                                    <i data-lucide="star" class="w-4 h-4"></i>
                                    <span data-i18n="discover.favorites">收藏</span>
                                </h3>
                            </div>
                            <div id="discover-favorites" class="grid grid-cols-1 md:grid-cols-2 gap-3"></div>
                        </div>

                        <p class="mt-8 text-xs text-textMuted text-center" data-i18n="discover.sourceNote">
                            專案資料來自 Manifund 公開平台；支持與留言請前往官方專案頁。
                        </p>
                    </div>
                </div>
            </div>
        </div>

        <!-- 選擇草稿 Modal（Discover→Studio 參考動線，design 2026-08-17 §Phase 2） -->
        <div id="discover-ref-modal" class="hidden fixed inset-0 z-[70] bg-background/80 backdrop-blur-sm p-4 flex items-center justify-center" data-click="DiscoverTab._closeRefPicker">
            <div class="max-w-md w-full rounded-2xl border border-borderSubtle/10 bg-surface shadow-xl p-5" role="dialog" aria-modal="true" data-click="DiscoverTab._noop">
                <div class="flex items-center justify-between mb-3">
                    <h3 class="font-serif text-lg text-secondary" data-i18n="discover.ref.pickTitle">加到哪份草稿？</h3>
                    <button data-click="DiscoverTab._closeRefPicker" class="p-2 -mr-2 -mt-1 rounded-full hover:bg-surfaceHighlight text-textMuted" aria-label="close" data-i18n="common.close" data-i18n-attr="aria-label">
                        <i data-lucide="x" class="w-5 h-5"></i>
                    </button>
                </div>
                <div id="discover-ref-list" class="max-h-72 overflow-y-auto"></div>
            </div>
        </div>

        <!-- 專案詳情 Modal（出資者旅程 P0-3；平台樣式 overlay，非原生對話框） -->
        <div id="discover-detail-modal" class="hidden fixed inset-0 z-[70] bg-background/80 backdrop-blur-sm p-4 flex items-center justify-center" data-click="DiscoverTab.closeDetail">
            <div class="max-w-2xl w-full max-h-[90dvh] overflow-y-auto rounded-2xl border border-borderSubtle/10 bg-surface shadow-xl" role="dialog" aria-modal="true" data-click="DiscoverTab._noop">
                <div class="flex items-start justify-between gap-3 p-5 border-b border-borderSubtle/10">
                    <div class="min-w-0">
                        <div id="discover-detail-tags" class="flex flex-wrap gap-1.5 mb-1"></div>
                        <h3 id="discover-detail-title" class="font-serif text-xl text-secondary leading-snug"></h3>
                    </div>
                    <button data-click="DiscoverTab.closeDetail" class="p-2 -mr-2 -mt-1 rounded-full hover:bg-surfaceHighlight text-textMuted" aria-label="close" data-i18n="common.close" data-i18n-attr="aria-label">
                        <i data-lucide="x" class="w-5 h-5"></i>
                    </button>
                </div>
                <div class="p-5 space-y-5">
                    <div id="discover-detail-progress" class="hidden">
                        <div class="h-1.5 w-full bg-surfaceHighlight rounded-full overflow-hidden">
                            <div id="discover-detail-progress-bar" class="h-full bg-primary"></div>
                        </div>
                        <p id="discover-detail-progress-text" class="text-xs text-textMuted mt-1"></p>
                    </div>
                    <p id="discover-detail-desc" class="text-sm text-textMuted whitespace-pre-line"></p>
                    <div class="flex flex-wrap gap-3">
                        <button data-click="DiscoverTab.requestAiReview"
                            class="flex-1 min-w-[180px] px-4 py-2.5 rounded-lg bg-primary text-background text-sm font-medium inline-flex items-center justify-center gap-2 whitespace-nowrap">
                            <i data-lucide="scan-search" class="w-4 h-4"></i>
                            <span data-i18n="discover.aiReview.button">AI 出資者視角評估</span>
                        </button>
                        <a id="discover-detail-link" href="#" target="_blank" rel="noopener noreferrer"
                            class="flex-1 min-w-[140px] px-4 py-2.5 rounded-lg bg-surfaceHighlight border border-borderSubtle/40 text-sm inline-flex items-center justify-center gap-2 whitespace-nowrap">
                            <span data-i18n="discover.goManifund">前往 Manifund</span>
                            <i data-lucide="external-link" class="w-3 h-3"></i>
                        </a>
                    </div>
                    <div id="discover-ai-review" class="hidden rounded-xl border border-borderSubtle/10 bg-background/50 p-4 space-y-4"></div>
                    <!-- 深掘問答（design 2026-08-17 §1） -->
                    <div class="rounded-xl border border-borderSubtle/10 bg-surface p-4">
                        <div class="flex items-center gap-2 mb-1">
                            <i data-lucide="messages-square" class="w-4 h-4 text-primary"></i>
                            <h4 class="text-sm font-medium text-secondary" data-i18n="discover.ask.title">深入提問</h4>
                        </div>
                        <p class="text-xs text-textMuted mb-3" data-i18n="discover.ask.note">針對這個專案提問——AI 只依據專案資料與留言回答，協助你理解，不替你決定。</p>
                        <div id="discover-qa-history" class="space-y-2 mb-3 max-h-64 overflow-y-auto"></div>
                        <div class="flex gap-2">
                            <textarea id="discover-qa-input" rows="2" maxlength="1000"
                                class="flex-1 px-3 py-2 rounded-lg bg-surfaceHighlight border border-borderSubtle/10 text-sm resize-none"
                                data-i18n="discover.ask.placeholder" data-i18n-attr="placeholder"
                                data-i18n="discover.ask.placeholder" data-i18n-attr="placeholder" placeholder="例如：這個團隊過往有什麼紀錄？"></textarea>
                            <button data-click="DiscoverTab.sendQuestion"
                                class="px-4 py-2.5 rounded-lg bg-primary text-background text-sm font-medium shrink-0 whitespace-nowrap" data-i18n="discover.ask.send">提問</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
`;
