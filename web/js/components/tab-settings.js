// Auto-generated from components.js split
// Tab: Settings - original lines 733-1100
window.Components = window.Components || {};
window.Components.settings = `
    <div class="max-w-2xl mx-auto px-1 sm:px-0">
             <h2 class="font-serif text-3xl text-secondary mb-8" data-i18n="settings.title">Settings</h2>

             <div class="space-y-10">
                <!-- User Profile Section -->
                <div id="settings-profile-card" class="${SECTION_CARD_PRIMARY}">
                    <div class="flex flex-wrap items-start justify-between gap-3 mb-6" style="display:flex;flex-wrap:wrap;align-items:flex-start;gap:0.75rem">
                        <div class="flex items-center gap-4 min-w-0 flex-1" style="display:flex;align-items:center;gap:1rem;min-width:0;flex:1 1 260px">
                            <div class="w-16 h-16 rounded-full bg-gradient-to-br from-primary to-accent flex items-center justify-center text-background font-bold text-2xl shadow-lg shadow-primary/20 shrink-0" style="flex:0 0 auto" id="profile-avatar">
                                U
                            </div>
                            <div class="min-w-0" style="min-width:0;flex:1 1 auto">
                                <div class="flex items-center gap-2">
                                    <h3 class="text-xl font-serif text-secondary truncate" id="profile-username">User</h3>
                                    <button id="btn-edit-display-name" data-click="toggleEditDisplayName"
                                        class="text-textMuted/50 hover:text-primary transition shrink-0"
                                        title="Edit nickname" data-i18n="settings.profile.editNickname" data-i18n-attr="title">
                                        <i data-lucide="pencil" class="w-3.5 h-3.5"></i>
                                    </button>
                                </div>
                                <!-- 暱稱編輯列（預設隱藏，點鉛筆後顯示） -->
                                <div id="display-name-editor" class="hidden mt-2 flex items-center gap-2">
                                    <input type="text" id="display-name-input" maxlength="20"
                                        class="flex-1 min-w-0 bg-background border border-borderLight rounded-lg px-3 py-1.5 text-sm text-secondary focus:border-primary focus:outline-none"
                                        placeholder="Your nickname (1-20 chars)" data-i18n="settings.profile.nicknamePlaceholder" data-i18n-attr="placeholder">
                                    <button id="btn-save-display-name" data-click="saveDisplayName"
                                        class="px-3 py-1.5 bg-primary text-background text-xs font-bold rounded-lg hover:opacity-90 transition shrink-0"
                                        data-i18n="settings.profile.save">Save</button>
                                </div>
                                <p id="display-name-cooldown" class="hidden mt-1 text-[10px] text-amber-600/80"></p>
                                <p class="text-xs text-textMuted font-mono break-all" style="min-width:0;overflow-wrap:anywhere" id="profile-uid">UID: --</p>
                                <div class="mt-1 flex items-center gap-2">
                                    <span class="px-2 py-0.5 rounded-md bg-surfaceHighlight text-[10px] text-textMuted border border-borderSubtle uppercase" id="profile-method">PASSWORD</span>
                                </div>
                            </div>
                        </div>
                    <div id="premium-status-badge" class="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-surfaceHighlight text-textMuted shrink-0" style="flex:0 0 auto;max-width:100%">
                        <i data-lucide="loader" class="w-3 h-3 animate-spin"></i>
                        <span data-i18n="settings.wallet.loading">Loading</span>
                    </div>
                    </div>

                    <!-- TEST MODE: Multi-User Switcher (預設隱藏，由 auth.js 根據 API 控制) -->
                    <div id="dev-user-switcher" class="mt-4 pt-4 border-t border-borderSubtle hidden">
                        <p class="text-[10px] text-textMuted uppercase tracking-wider mb-2 font-bold opacity-50" data-i18n="settings.profile.devSwitchUser">Dev: Switch User</p>
                        <div class="grid grid-cols-2 gap-2">
                            <button data-click="handleDevSwitchUser" data-click-arg="test-user-001" class="py-2 bg-surfaceHighlight hover:bg-primary/20 hover:text-primary rounded-lg text-xs font-mono transition border border-borderSubtle">
                                User 001
                            </button>
                            <button data-click="handleDevSwitchUser" data-click-arg="test-user-002" class="py-2 bg-surfaceHighlight hover:bg-accent/20 hover:text-accent rounded-lg text-xs font-mono transition border border-borderSubtle">
                                User 002
                            </button>
                            <button data-click="handleDevSwitchUser" data-click-arg="test-user-003" class="py-2 bg-surfaceHighlight hover:bg-success/20 hover:text-success rounded-lg text-xs font-mono transition border border-borderSubtle">
                                User 003 (PREMIUM)
                            </button>
                            <button data-click="handleDevSwitchUser" data-click-arg="test-user-004" class="py-2 bg-surfaceHighlight hover:bg-amber-500/20 hover:text-amber-600 rounded-lg text-xs font-mono transition border border-borderSubtle">
                                User 004 (PREMIUM)
                            </button>
                        </div>
                    </div>

                    <!-- TEST MODE: Tier Switcher (僅測試模式顯示) -->
                    <div id="test-tier-switcher" class="mt-4 pt-4 border-t border-borderSubtle hidden">
                        <div class="flex items-center justify-between mb-3">
                            <p class="text-[10px] text-primary uppercase tracking-wider font-bold" data-i18n="settings.testModeSwitchTier">TEST MODE: Switch Membership Tier</p>
                            <span id="current-test-tier" class="px-2 py-0.5 rounded-md bg-primary/20 text-primary text-[10px] font-mono font-bold">PREMIUM</span>
                        </div>
                        <p class="text-[10px] text-textMuted mb-3" data-i18n="settings.testModeTierDesc">Test different membership tier permissions (no charges)</p>
                        <div class="grid grid-cols-2 gap-2">
                            <button data-click="handleSwitchTestTier" data-click-arg="free" class="test-tier-btn py-2 bg-surfaceHighlight hover:bg-textMuted/10 rounded-lg text-xs font-mono transition border border-borderSubtle" data-tier="free">
                                FREE
                            </button>
                            <button data-click="handleSwitchTestTier" data-click-arg="premium" class="test-tier-btn py-2 bg-surfaceHighlight hover:bg-primary/20 hover:text-primary rounded-lg text-xs font-mono transition border border-primary/20 text-primary" data-tier="premium">
                                PREMIUM
                            </button>
                        </div>
                    </div>

                    <button data-click="handleLogout" class="w-full py-3 bg-surfaceHighlight hover:bg-danger/10 text-textMuted hover:text-danger border border-borderSubtle hover:border-danger/20 font-bold rounded-xl transition flex items-center justify-center gap-2 mt-4">
                        <i data-lucide="log-out" class="w-4 h-4"></i>
                        <span data-i18n="settings.profile.logout">Logout</span>
                    </button>
                </div>

                <!-- Daily Brief Section（2026-09-12 留存核心第 1 項；web/js/brief-settings.js） -->
                <div id="settings-brief-card" class="${SECTION_CARD_PRIMARY}">
                    <div class="flex items-center justify-between mb-6">
                        <div class="${CARD_HEADER_ROW_CLASS}">
                            <div class="${HERO_ICON_BOX_CLASS} text-primary">
                                <i data-lucide="sunrise" class="w-5 h-5"></i>
                            </div>
                            <div>
                                <h3 class="text-lg font-serif text-primary" data-i18n="settings.brief.title">Daily brief</h3>
                                <p class="text-xs text-textMuted" data-i18n="settings.brief.description">Your positions, watchlist, alerts, calendar and yesterday's spending, every morning</p>
                            </div>
                        </div>
                    </div>

                    <div class="space-y-4">
                        <label class="flex items-center justify-between gap-3 min-h-11 cursor-pointer">
                            <span class="text-sm text-textMain" data-i18n="settings.brief.enabled">Send me a daily brief</span>
                            <input id="brief-enabled" type="checkbox" class="w-5 h-5 accent-primary">
                        </label>
                        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                            <label class="block">
                                <span class="text-xs text-textMuted" data-i18n="settings.brief.hour">Time (local)</span>
                                <select id="brief-hour" class="mt-1 w-full bg-background border border-borderLight rounded-lg px-3 py-2 text-sm text-secondary"></select>
                            </label>
                            <label class="block">
                                <span class="text-xs text-textMuted" data-i18n="settings.brief.timezone">Timezone</span>
                                <select id="brief-timezone" class="mt-1 w-full bg-background border border-borderLight rounded-lg px-3 py-2 text-sm text-secondary"></select>
                            </label>
                        </div>
                        <div class="space-y-1">
                            <p class="text-xs text-textMuted" data-i18n="settings.brief.channels">Deliver to</p>
                            <label class="flex items-center gap-2 min-h-11 cursor-pointer">
                                <input id="brief-channel-telegram" type="checkbox" class="w-4 h-4 accent-primary">
                                <span class="text-sm text-textMain" data-i18n="settings.brief.channelTelegram">Telegram</span>
                            </label>
                            <button id="brief-telegram-hint" type="button" data-click="SettingsConnections.open" data-click-arg="telegram" class="hidden min-h-11 pl-6 text-left text-xs text-primary hover:underline">
                                <span data-i18n="settings.brief.telegramLink">Not linked · Link Telegram →</span>
                            </button>
                            <label class="flex items-center gap-2 min-h-11 cursor-pointer">
                                <input id="brief-channel-inapp" type="checkbox" class="w-4 h-4 accent-primary">
                                <span class="text-sm text-textMain" data-i18n="settings.brief.channelInapp">In-app notification</span>
                            </label>
                            <label id="brief-channel-baseapp-row" class="hidden flex items-center gap-2 min-h-11 cursor-pointer">
                                <input id="brief-channel-baseapp" type="checkbox" class="w-4 h-4 accent-primary">
                                <span class="text-sm text-textMain" data-i18n="settings.brief.channelBaseapp">Base App notification</span>
                            </label>
                            <label id="brief-channel-email-row" class="hidden flex items-center gap-2 min-h-11 cursor-pointer">
                                <input id="brief-channel-email" type="checkbox" class="w-4 h-4 accent-primary">
                                <span class="text-sm text-textMain" data-i18n="settings.emailBrief.channelEmail">Email</span>
                            </label>
                            <!-- Email 的設定在 Connections（email-brief-settings.js）；這裡只有勾選與「去設定」 -->
                            <button id="brief-email-hint" type="button" data-click="SettingsConnections.open" data-click-arg="email" class="hidden min-h-11 pl-6 text-left text-xs text-primary hover:underline">
                                <span data-i18n="settings.brief.emailLink">Not set up · Add your email →</span>
                            </button>
                        </div>
                        <!-- 我的自選（web/js/watchlist-settings.js，/api/watchlist）：早報每天列價格、每一檔都能設價格警報 -->
                        <div id="brief-watchlist-section" class="space-y-2 pt-4 border-t border-borderSubtle">
                            <div class="flex items-center justify-between gap-2">
                                <p class="text-sm text-textMain" data-i18n="settings.watchlist.title">My watchlist</p>
                                <span id="watchlist-count" class="text-[11px] text-textMuted font-mono"></span>
                            </div>
                            <p class="text-xs text-textMuted" data-i18n="settings.watchlist.description">Your daily brief lists the price of each one every morning. Tap 🔔 to set a price alert.</p>
                            <div class="flex flex-col sm:flex-row gap-2">
                                <label class="block sm:w-32 shrink-0">
                                    <span class="sr-only" data-i18n="settings.watchlist.market">Market</span>
                                    <select id="watchlist-market" class="w-full min-h-11 bg-background border border-borderLight rounded-lg px-3 py-2 text-sm text-secondary">
                                        <option value="crypto" data-i18n="settings.watchlist.marketCrypto">Crypto</option>
                                        <option value="tw_stock" data-i18n="settings.watchlist.marketTw">TW stock</option>
                                        <option value="us_stock" data-i18n="settings.watchlist.marketUs">US stock</option>
                                        <option value="hk_stock" data-i18n="settings.watchlist.marketHk">HK stock</option>
                                        <option value="jp_stock" data-i18n="settings.watchlist.marketJp">JP stock</option>
                                        <option value="kr_stock" data-i18n="settings.watchlist.marketKr">KR stock</option>
                                        <option value="cn_stock" data-i18n="settings.watchlist.marketCn">China A-share</option>
                                        <option value="in_stock" data-i18n="settings.watchlist.marketIn">India stock</option>
                                        <option value="commodity" data-i18n="settings.watchlist.marketCommodity">Commodity</option>
                                        <option value="forex" data-i18n="settings.watchlist.marketForex">Forex</option>
                                    </select>
                                </label>
                                <label class="block flex-1 min-w-0">
                                    <span class="sr-only" data-i18n="settings.watchlist.symbol">Symbol</span>
                                    <input id="watchlist-symbol" type="text" maxlength="24" autocomplete="off" autocapitalize="characters" placeholder="BTC / 2330 / AAPL / 0700 / GC=F / EURUSD" data-enter="WatchlistSettings.add" class="w-full min-h-11 bg-background border border-borderLight rounded-lg px-3 py-2 text-sm text-secondary font-mono">
                                </label>
                                <button id="watchlist-add" data-click="WatchlistSettings.add" class="px-4 py-3 min-h-11 bg-primary/10 hover:bg-primary/20 text-primary rounded-xl text-sm transition disabled:opacity-60">
                                    <span data-i18n="settings.watchlist.add">Add</span>
                                </button>
                            </div>
                            <p id="watchlist-status" class="text-xs text-textMuted" aria-live="polite"></p>
                            <ul id="watchlist-items" class="space-y-1.5"></ul>
                            <!-- 持倉（投資日誌）會自動列進早報；每一檔都能關掉「📰 早報」（c057，不動帳本） -->
                            <div id="brief-positions-block" class="hidden space-y-1.5 pt-2">
                                <p class="text-xs text-textMuted" data-i18n="settings.watchlist.positionsTitle">Holdings from your journal (added to the brief automatically)</p>
                                <ul id="brief-position-items" class="space-y-1.5"></ul>
                            </div>
                        </div>
                        <label class="flex items-center justify-between gap-3 min-h-11 cursor-pointer">
                            <span class="text-sm text-textMain" data-i18n="settings.brief.includeSpend">Include yesterday's spending from the ledger</span>
                            <input id="brief-include-spend" type="checkbox" class="w-5 h-5 accent-primary">
                        </label>
                        <label class="flex items-center justify-between gap-3 min-h-11 cursor-pointer">
                            <span class="text-sm text-textMain" data-i18n="settings.brief.includeMacro">Include FOMC / CPI / jobs report dates in the calendar</span>
                            <input id="brief-include-macro" type="checkbox" class="w-5 h-5 accent-primary">
                        </label>
                        <div class="flex flex-wrap gap-2">
                            <button data-click="saveBriefPrefs" class="px-4 py-3 min-h-11 bg-primary hover:bg-primary/90 text-background font-bold rounded-xl text-sm transition">
                                <span data-i18n="settings.brief.save">Save</span>
                            </button>
                            <button data-click="previewBrief" class="px-4 py-3 min-h-11 bg-primary/10 hover:bg-primary/20 text-primary rounded-xl text-sm transition">
                                <span data-i18n="settings.brief.preview">Preview today's brief</span>
                            </button>
                        </div>
                        <p id="brief-status" class="text-xs mt-2 text-textMuted"></p>
                        <pre id="brief-preview" class="hidden whitespace-pre-wrap break-words text-sm bg-background border border-borderLight rounded-xl p-3 text-secondary font-sans"></pre>
                    </div>
                </div>

                <!-- TON Wallet Section -->
                <div id="settings-wallet-card" class="${SECTION_CARD_PRIMARY}">
                    <div class="flex items-center justify-between mb-6">
                        <div class="${CARD_HEADER_ROW_CLASS}">
                            <div id="settings-wallet-icon" class="${HERO_ICON_BOX_CLASS} text-primary">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="w-5 h-5">
                                    <path d="M12 2L21 12L12 22L3 12L12 2Z" stroke-linejoin="round"/>
                                </svg>
                            </div>
                            <div>
                                <h3 class="text-lg font-serif text-primary" data-i18n="settings.wallet.title">Wallet</h3>
                                <p class="text-xs text-textMuted" data-i18n="settings.wallet.description">Connect your wallet for payments, membership and tips</p>
                            </div>
                        </div>
                        <div id="settings-wallet-status-badge" class="${STATUS_BADGE_MUTED_CLASS}">
                            <i data-lucide="loader" class="w-3 h-3 animate-spin"></i>
                            <span data-i18n="settings.wallet.loading">Loading</span>
                        </div>
                    </div>

                    <div id="settings-wallet-content" class="space-y-4">
                        <div id="wallet-not-linked" class="hidden">
                            <div class="${INFO_PANEL_CLASS}">
                                <div class="flex items-start gap-3">
                                    <i data-lucide="info" class="w-4 h-4 text-primary/60 mt-0.5 flex-shrink-0"></i>
                                    <div>
                                        <p class="text-sm text-textMuted leading-relaxed mb-1" data-i18n="settings.wallet.bindHint">No EVM wallet is bound yet. Bind the wallet you will pay from — USDC subscriptions are only credited when sent from a bound address.</p>
                                        <p class="text-xs text-textMuted/60" data-i18n="settings.wallet.reloginDesc">After re-logging in, you can make payments, upgrade membership, and receive tips.</p>
                                    </div>
                                </div>
                                <!-- 付款驗證只認 user_wallets 的綁定（2026-09-11 盤查）：這裡是
                                     Telegram 登入者唯一的綁定入口，Trust 分頁那顆寫的是護照欄位 -->
                                <button data-click="safeEvmBind" data-tma-hide class="w-full mt-4 py-3 bg-primary hover:bg-primary/90 text-background font-bold rounded-xl transition flex items-center justify-center gap-2">
                                    <i data-lucide="wallet" class="w-4 h-4"></i>
                                    <span data-i18n="settings.wallet.bindButton">Bind EVM wallet</span>
                                </button>
                                <button data-click="handleLogout" class="w-full mt-2 py-3 bg-primary/10 hover:bg-primary/20 text-primary border border-primary/20 font-bold rounded-xl transition flex items-center justify-center gap-2">
                                    <i data-lucide="log-in" class="w-4 h-4"></i>
                                    <span data-i18n="settings.wallet.reloginButton">Re-login to Link Wallet</span>
                                </button>
                            </div>
                        </div>

                        <div id="wallet-linked" class="hidden">
                            <div class="bg-success/5 rounded-xl p-4 border border-success/10">
                                <div class="flex flex-wrap items-center gap-3">
                                    <div class="w-10 h-10 shrink-0 rounded-full bg-success/20 flex items-center justify-center">
                                        <i data-lucide="check-circle" class="w-5 h-5 text-success"></i>
                                    </div>
                                    <div>
                                        <p class="text-sm font-bold text-success" data-i18n="settings.wallet.connected">Wallet connected</p>
                                        <p id="settings-wallet-username" class="text-xs text-textMuted font-mono">@username</p>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                 <!-- LLM Configuration -->
                <!-- AI Intelligence（金鑰綁定／模型清單）已於 2026-09-04 搬到
                     AI Studio →「模型」分頁。ID 全部沿用，apiKeyManager.js 不動；
                     理由：Skills／Memory／Tools 早已搬過去，金鑰是最後一個還留在
                     Settings 的 AI 設定，留著才是不一致的那個。
                     摘要卡刻意不放——2026-09-02 DANNY 已決定 AI Studio 走獨立入口。 -->

                <!-- 我的 AI 卡片已移除（2026-09-02 DANNY）：AI Studio 是獨立板塊，
                     入口改走導覽自訂（Customize）開啟，不再放 Settings 摘要卡 -->



                <!-- Telegram 卡已遷至 Connections（連結）板塊（2026-09-08）：外部連接入口集中一處， -->
                <!-- LINE 落地時直接進那裡，不再讓 Settings 繼續膨脹。 -->
                <!-- design：docs/plans/2026-09-08-connections-tab-design.md -->
                <!-- 註：多行 HTML 註解的續行會被中文棘輪計數（見 4cd5e26e），故逐行獨立成一則。 -->
                <div id="settings-connections-card" class="${SECTION_CARD_SECONDARY}">
                    <!-- 走 GlobalNav.navigateToTab 而非 ConnectionsTab.*：後者是 connections 分頁的 lazy chunk， -->
                    <!-- 在 Settings 上還沒載入，點了會被 click-delegator 靜默吞掉（typeof fn !== 'function' 就 return）。 -->
                    <!-- GlobalNav 在首屏，隨時可用。 -->
                    <button data-click="GlobalNav.navigateToTab" data-click-arg="connections"
                        class="w-full flex items-center justify-between gap-3 text-left">
                        <div class="flex items-center gap-3 min-w-0">
                            <div class="w-10 h-10 rounded-xl bg-surfaceHighlight flex items-center justify-center shrink-0">
                                <i data-lucide="link" class="w-5 h-5 text-primary"></i>
                            </div>
                            <div class="min-w-0">
                                <h3 class="text-base font-serif text-secondary" data-i18n="connections.title">Connections</h3>
                                <p class="text-xs text-textMuted" data-i18n="settings.connectionsGuide">Manage Telegram and other linked apps</p>
                                <!-- 2026-09-12 盤點 §13：分頁下架（導覽隱藏），入口就是這張卡；徽章由 connections-settings.js 填 -->
                                <div class="flex flex-wrap gap-1.5 mt-1.5">
                                    <span id="settings-connections-telegram" class="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] bg-surfaceHighlight text-textMuted">Telegram …</span>
                                    <span id="settings-connections-email" class="hidden inline-flex items-center px-2 py-0.5 rounded-full text-[11px] bg-surfaceHighlight text-textMuted">Email …</span>
                                    <span id="settings-connections-line" class="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] bg-surfaceHighlight text-textMuted">LINE …</span>
                                    <span id="settings-connections-google" class="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] bg-surfaceHighlight text-textMuted">Google …</span>
                                </div>
                            </div>
                        </div>
                        <i data-lucide="chevron-right" class="w-4 h-4 text-textMuted shrink-0"></i>
                    </button>
                </div>

                <!-- Navigation Customization -->
                <div class="${SECTION_CARD_SECONDARY}">
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center gap-3 min-w-0 flex-1">
                            <div class="${HERO_ICON_BOX_CLASS}">
                                <i data-lucide="layout-grid" class="w-5 h-5 text-primary"></i>
                            </div>
                            <div class="min-w-0">
                                <h3 class="text-lg font-serif text-primary" data-i18n="settings.navigation.title">Navigation Customization</h3>
                                <p class="text-xs text-textMuted" data-i18n="settings.navigation.description">Customize your bottom navigation bar</p>
                            </div>
                        </div>
                        <button data-click="featureMenuOpen" class="shrink-0 whitespace-nowrap px-4 py-2 bg-primary/10 hover:bg-primary/20 text-primary rounded-xl transition font-bold text-sm flex items-center gap-2">
                            <i data-lucide="settings-2" class="w-4 h-4"></i>
                            <span data-i18n="settings.navigation.customize">Customize</span>
                        </button>
                    </div>
                    <div class="mt-4 ${INFO_PANEL_CLASS}">
                        <p class="text-sm text-textMuted leading-relaxed">
                            <i data-lucide="info" class="w-4 h-4 inline-block mr-1 opacity-60"></i>
                            <span data-i18n="settings.navigation.info">Choose which features appear in the sidebar menu: 3 to 6 items, including the fixed Chat and Settings.</span>
                        </p>
                    </div>
                </div>


                <!-- Premium Membership -->
                <div class="${SECTION_CARD_DEFAULT}">
                    <div class="flex flex-wrap items-center gap-3 mb-6">
                        <div class="w-10 h-10 shrink-0 rounded-xl bg-primary flex items-center justify-center">
                            <i data-lucide="star" class="w-5 h-5 text-background"></i>
                        </div>
                        <div class="min-w-0 flex-1">
                            <h3 class="text-lg font-serif text-primary" data-i18n="settings.premium.title">Premium Membership</h3>
                            <p class="text-xs text-textMuted" data-i18n="settings.premium.description">Unlock advanced features</p>
                        </div>
                    </div>

                    <div class="space-y-4">
                        <div class="${INFO_PANEL_CLASS}">
                            <p class="text-sm text-textMuted leading-relaxed">
                                <i data-lucide="crown" class="w-4 h-4 inline-block mr-1 text-yellow-400"></i>
                                <span data-i18n="settings.premium.benefits">Premium members enjoy:</span>
                            </p>
                            <ul class="text-xs text-textMuted mt-2 space-y-1 ml-5">
                                <li data-i18n="settings.premium.benefit1">Unlimited posting</li>
                                <li data-i18n="settings.premium.benefit2">Unlimited replies</li>
                                <li data-i18n="settings.premium.benefit3">Early access to new features</li>
                                <li data-i18n="settings.premium.benefit4">Exclusive premium badge</li>
                            </ul>
                        </div>

                        <!-- 月／年方案切換（與論壇 premium 頁同一組 data-plan-toggle，
                             premium.js 以 document 委派綁定——2026-09-10 DANNY：
                             方案選擇只在論壇頁出現，SPA 付款入口沒得選） -->
                        <div class="grid grid-cols-2 gap-2" style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem" role="group" aria-label="Plan" data-i18n="premium.planToggle" data-i18n-attr="aria-label">
                            <button type="button" data-plan-toggle="premium_monthly" aria-pressed="true"
                                class="w-full py-3 px-3 rounded-2xl border border-borderSubtle bg-background/50 font-bold text-sm transition ring-2 ring-primary text-primary bg-primary/10">
                                <span data-i18n="premium.planMonthly">30 Days</span>
                            </button>
                            <button type="button" data-plan-toggle="premium_yearly" aria-pressed="false"
                                class="w-full py-3 px-3 rounded-2xl border border-borderSubtle bg-background/50 text-textMuted font-bold text-sm transition relative">
                                <span data-i18n="premium.planYearly">365 Days</span>
                                <span class="block text-[10px] font-normal text-success mt-0.5" data-i18n="premium.planYearlySave">Save 3 months</span>
                            </button>
                        </div>

                        <button data-click="handleUpgradeToPremium" class="w-full px-4 py-3.5 bg-primary hover:bg-primary/90 text-background font-bold rounded-xl transition flex items-center justify-center gap-2 upgrade-premium-btn">
                            <i data-lucide="zap" class="w-4 h-4"></i>
                            <span><span data-i18n="settings.premium.upgradeButton">Upgrade to Premium -</span> <span data-price="premium"><i data-lucide="loader" class="w-3 h-3 animate-spin"></i></span></span>
                        </button>

                        <p class="text-[10px] text-textMuted/60 text-center" data-plan-note>Monthly plan (30 days). Renew manually before expiry.</p>
                        <!-- 2026-09-14 合規口吻：非託管、不代理交易、會員費非儲值 -->
                        <p class="text-[10px] text-textMuted/50 text-center leading-snug" data-i18n="settings.premium.nonCustodialNotice">This service does not custody assets or trade on your behalf. Payments go straight from your wallet; membership fees are not a balance and cannot be withdrawn.</p>
                    </div>
                </div>

                <!-- Feedback -->
                <div class="${SECTION_CARD_SECONDARY}">
                    <div class="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
                        <div class="${CARD_HEADER_ROW_CLASS}">
                            <div class="${HERO_ICON_BOX_CLASS}">
                                <i data-lucide="message-square-heart" class="w-5 h-5 text-primary"></i>
                            </div>
                            <div>
                                <h3 class="text-lg font-serif text-primary" data-i18n="settings.feedback.title">Feedback</h3>
                                <p class="text-xs text-textMuted" data-i18n="settings.feedback.description">Share your thoughts, suggestions, and product experience</p>
                            </div>
                        </div>
                        <button data-click="openFeedbackModal" class="px-4 py-3 bg-primary/10 hover:bg-primary/20 text-primary rounded-2xl transition font-bold text-sm flex items-center justify-center gap-2 sm:min-w-[180px]">
                            <i data-lucide="message-square-plus" class="w-4 h-4"></i>
                            <span data-i18n="settings.feedback.button">Open Feedback</span>
                        </button>
                    </div>
                    <div class="mt-4 ${INFO_PANEL_CLASS}">
                        <p class="text-sm text-textMuted leading-relaxed">
                            <i data-lucide="sparkles" class="w-4 h-4 inline-block mr-1 opacity-60"></i>
                            <span data-i18n="settings.feedback.hint">Tell us what is working well, what feels confusing, or what you want to see next.</span>
                        </p>
                    </div>
                </div>

                <!-- About & Legal -->
                <div class="${SECTION_CARD_SECONDARY}">
                    <div class="${CARD_HEADER_ROW_CLASS} mb-6">
                        <div class="${HERO_ICON_BOX_CLASS}">
                            <i data-lucide="info" class="w-5 h-5 text-primary"></i>
                        </div>
                        <div>
                            <h3 class="text-lg font-serif text-primary" data-i18n="settings.legal.title">About & Legal</h3>
                            <p class="text-xs text-textMuted" data-i18n="settings.legal.description">Terms, Privacy, and Community Guidelines</p>
                        </div>
                    </div>

                    <div class="space-y-3">
                        <button type="button" data-click="showLegalPage" data-click-arg="terms" class="block w-full text-left p-4 bg-background/50 hover:bg-background rounded-xl border border-borderSubtle hover:border-primary/20 transition group cursor-pointer">
                            <div class="flex items-center justify-between">
                                <div class="flex flex-wrap items-center gap-3">
                                    <i data-lucide="file-text" class="w-4 h-4 shrink-0 text-textMuted group-hover:text-primary transition"></i>
                                    <div>
                                        <p class="text-sm font-medium text-secondary" data-i18n="settings.legal.termsTitle">Terms of Service</p>
                                        <p class="text-xs text-textMuted" data-i18n="settings.legal.termsDesc">Usage rules and policies</p>
                                    </div>
                                </div>
                                <i data-lucide="chevron-right" class="w-4 h-4 shrink-0 text-textMuted group-hover:text-primary transition"></i>
                            </div>
                        </button>

                        <button type="button" data-click="showLegalPage" data-click-arg="privacy" class="block w-full text-left p-4 bg-background/50 hover:bg-background rounded-xl border border-borderSubtle hover:border-primary/20 transition group cursor-pointer">
                            <div class="flex items-center justify-between">
                                <div class="flex flex-wrap items-center gap-3">
                                    <i data-lucide="shield" class="w-4 h-4 shrink-0 text-textMuted group-hover:text-primary transition"></i>
                                    <div>
                                        <p class="text-sm font-medium text-secondary" data-i18n="settings.legal.privacyTitle">Privacy Policy</p>
                                        <p class="text-xs text-textMuted" data-i18n="settings.legal.privacyDesc">Data protection and privacy</p>
                                    </div>
                                </div>
                                <i data-lucide="chevron-right" class="w-4 h-4 shrink-0 text-textMuted group-hover:text-primary transition"></i>
                            </div>
                        </button>

                        <button type="button" data-click="showLegalPage" data-click-arg="guidelines" class="block w-full text-left p-4 bg-background/50 hover:bg-background rounded-xl border border-borderSubtle hover:border-primary/20 transition group cursor-pointer">
                            <div class="flex items-center justify-between">
                                <div class="flex flex-wrap items-center gap-3">
                                    <i data-lucide="users" class="w-4 h-4 shrink-0 text-textMuted group-hover:text-primary transition"></i>
                                    <div>
                                        <p class="text-sm font-medium text-secondary" data-i18n="settings.legal.guidelinesTitle">Community Guidelines</p>
                                        <p class="text-xs text-textMuted" data-i18n="settings.legal.guidelinesDesc">Governance and moderation rules</p>
                                    </div>
                                </div>
                                <i data-lucide="chevron-right" class="w-4 h-4 shrink-0 text-textMuted group-hover:text-primary transition"></i>
                            </div>
                        </button>
                    </div>
                </div>

                <div class="text-center pt-8 opacity-20 text-[10px] font-mono tracking-widest uppercase">
                    CryptoMind v2.0.0-TON
                </div>
             </div>
        </div>
    `;

// Side-effect module — assigns to window.Components
export {};
