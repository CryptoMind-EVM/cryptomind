// Tab: Connections（連結）— 外部連接的專屬板塊
// design：docs/plans/2026-09-08-connections-tab-design.md
//
// Telegram 卡自 Settings 整卡搬入，內部 DOM-ID 一律沿用
// （telegram-status-badge / telegram-link-content），TelegramLinkApp 零改動。
// 2026-09-28：分兩區——「收早報與通知」（Telegram／Email／LINE）與「登入方式」（Google），
// 不然 Email（收信）跟 Google（登入）看起來像同一件事。
window.Components = window.Components || {};
window.Components.connections = `
    <div class="max-w-2xl mx-auto px-1 sm:px-0">
        <div class="flex items-center gap-2 mb-2">
            <button data-click="ConnectionsTab.goBack" class="${ICON_ACTION_BUTTON_CLASS} shrink-0 -ml-2"
                aria-label="back" data-i18n="connections.back" data-i18n-attr="aria-label">
                <i data-lucide="arrow-left" class="w-4 h-4"></i>
            </button>
            <h2 class="font-serif text-3xl text-secondary" data-i18n="connections.title">Connections</h2>
        </div>
        <p class="text-sm text-textMuted mb-8 pl-1" data-i18n="connections.subtitle">
            Link CryptoMind to the apps you already chat in. Your conversations and history stay shared across every entry point.
        </p>

        <div class="space-y-12">
            <!-- 2026-09-28：依用途分兩區，Email（收早報的信箱）跟 Google（登入方式）不再混在同一串 -->
            <section class="space-y-6">
                <div class="pl-1">
                    <h3 class="text-sm font-bold text-secondary" data-i18n="connections.notifyTitle">Get your brief and alerts</h3>
                    <p class="text-xs text-textMuted mt-0.5" data-i18n="connections.notifyDesc">Where we send your daily brief and notifications.</p>
                </div>

                <!-- Telegram（自 Settings 搬入 2026-09-08；ID 沿用，邏輯不動） -->
                <div id="connections-telegram-card" class="${SECTION_CARD_DEFAULT}">
                    <div class="flex items-center justify-between mb-6">
                        <div class="flex items-center gap-3 min-w-0">
                            <div class="w-10 h-10 rounded-xl flex items-center justify-center shrink-0" style="background: rgba(0,152,234,0.1);">
                                <svg viewBox="0 0 24 24" fill="none" class="w-5 h-5">
                                    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2z" fill="#229ED9"/>
                                    <path d="M12 2c-2.5 0-5 2-5 5 0 1.5.5 2.5 1 3.5.5 1 1.5 2 2 2s1-.5 1.5-1c.5-.5 1-1 2-1s1.5.5 2 1c.5.5.5 1.5.5 2 0 1-.5 2-1 3-.5 1-2 2-3 2s-2-.5-3-1c-1-.5-2-1.5-3-3s-1-4 0-5 2-2 3-2 2 1 3 2c1 1 1.5 2 2 2" fill="white"/>
                                </svg>
                            </div>
                            <div>
                                <h3 class="text-lg font-serif text-primary" data-i18n="telegram.title">Telegram</h3>
                                <p class="text-xs text-textMuted" data-i18n="telegram.not_bound_desc">Link your Telegram account for notifications</p>
                            </div>
                        </div>
                        <div id="telegram-status-badge" class="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium shrink-0 whitespace-nowrap bg-surfaceHighlight text-textMuted">
                            <i data-lucide="loader" class="w-3 h-3 animate-spin"></i>
                            <span data-i18n="common.loading">Loading</span>
                        </div>
                    </div>
                    <div id="telegram-link-content">
                        <!-- Dynamically rendered by TelegramLinkApp -->
                    </div>
                </div>

                <!-- Email 早報（PR-8；web/js/email-brief-settings.js）：2026-09-28 自 Settings 早報卡搬來，
                     所有綁定集中在這頁。/api/config 的 email_brief_enabled 為 true 且已登入才顯示 -->
                <div id="connections-email-card" class="hidden ${SECTION_CARD_DEFAULT}">
                    <div class="flex items-center justify-between mb-6">
                        <div class="flex items-center gap-3 min-w-0">
                            <div class="w-10 h-10 rounded-xl flex items-center justify-center shrink-0 bg-primary/10">
                                <i data-lucide="inbox" class="w-5 h-5 text-primary"></i>
                            </div>
                            <div>
                                <h3 class="text-lg font-serif text-primary" data-i18n="connections.emailTitle">Email brief</h3>
                                <p class="text-xs text-textMuted" data-i18n="connections.emailDesc">We email your daily brief every morning. Any address works.</p>
                            </div>
                        </div>
                        <div id="email-status-badge" class="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium shrink-0 whitespace-nowrap bg-surfaceHighlight text-textMuted">
                            <i data-lucide="loader" class="w-3 h-3 animate-spin"></i>
                            <span data-i18n="common.loading">Loading</span>
                        </div>
                    </div>
                    <div class="space-y-2">
                        <div class="flex flex-col sm:flex-row gap-2">
                            <label class="block flex-1 min-w-0">
                                <span class="sr-only" data-i18n="settings.emailBrief.label">Email address</span>
                                <input id="brief-email-input" type="email" inputmode="email" autocomplete="email" maxlength="254" placeholder="you@example.com" data-enter="EmailBriefSettings.save" class="w-full min-h-11 bg-background border border-borderLight rounded-lg px-3 py-2 text-sm text-secondary">
                            </label>
                            <button id="brief-email-send" data-click="saveBriefEmail" class="px-4 py-3 min-h-11 bg-primary/10 hover:bg-primary/20 text-primary rounded-xl text-sm transition">
                                <span data-i18n="settings.emailBrief.send">Send confirmation</span>
                            </button>
                        </div>
                        <p id="brief-email-status" class="text-xs break-all text-textMuted" aria-live="polite"></p>
                        <button id="brief-email-remove" data-click="removeBriefEmail" class="hidden self-start min-h-11 text-xs text-textMuted hover:text-danger underline transition">
                            <span data-i18n="settings.emailBrief.remove">Remove email</span>
                        </button>
                    </div>
                </div>

                <!-- LINE（2026-09-08 上線）。後端 LINE_CHANNEL_SECRET 未設時 -->
                <!-- /api/line/status 回 404，LineLinkApp 會渲染「尚未開放」而不是錯誤。 -->
                <div id="connections-line-card" class="${SECTION_CARD_DEFAULT}">
                    <div class="flex items-center justify-between mb-6">
                        <div class="flex items-center gap-3 min-w-0">
                            <div class="w-10 h-10 rounded-xl flex items-center justify-center shrink-0" style="background: rgba(6,199,85,0.1);">
                                <i data-lucide="message-circle" class="w-5 h-5" style="color:#06C755;"></i>
                            </div>
                            <div>
                                <h3 class="text-lg font-serif text-primary">LINE</h3>
                                <p class="text-xs text-textMuted" data-i18n="line.notBoundDesc">Link LINE to chat with CryptoMind there</p>
                                <p class="text-xs text-textMuted mt-1" data-i18n="line.noBrief">The daily brief isn't available on LINE yet. Use Telegram, Email or in-app notifications.</p>
                            </div>
                        </div>
                        <div id="line-status-badge" class="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium shrink-0 whitespace-nowrap bg-surfaceHighlight text-textMuted">
                            <i data-lucide="loader" class="w-3 h-3 animate-spin"></i>
                            <span data-i18n="common.loading">Loading</span>
                        </div>
                    </div>
                    <div id="line-link-content">
                        <!-- Dynamically rendered by LineLinkApp -->
                    </div>
                </div>
            </section>

            <section id="connections-signin-section" class="hidden space-y-6">
                <div class="pl-1">
                    <h3 class="text-sm font-bold text-secondary" data-i18n="connections.moreWaysTitle">Ways to sign in</h3>
                    <p class="text-xs text-textMuted mt-0.5" data-i18n="connections.moreWaysDesc">Another way to sign in to this same account. It has nothing to do with where your brief is sent.</p>
                </div>

                <!-- Google（2026-09-13 Google Play 版 Phase 2）：綁定後可用 Google 登入同一個帳號。
                     後端沒設 GOOGLE_CLIENT_ID 時 GoogleLinkApp 把整個「登入方式」區塊藏起來 -->
                <div id="connections-google-card" class="${SECTION_CARD_DEFAULT}">
                    <div class="flex items-center justify-between mb-6">
                        <div class="flex items-center gap-3 min-w-0">
                            <div class="w-10 h-10 rounded-xl flex items-center justify-center shrink-0 bg-surfaceHighlight">
                                <svg viewBox="0 0 48 48" class="w-5 h-5" aria-hidden="true">
                                    <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/>
                                    <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/>
                                    <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z"/>
                                    <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C36.9 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/>
                                </svg>
                            </div>
                            <div>
                                <h3 class="text-lg font-serif text-primary" data-i18n="connections.googleTitle">Google sign-in</h3>
                                <p class="text-xs text-textMuted" data-i18n="googleAuth.linkDesc">Link Google to sign in to this account without a wallet</p>
                            </div>
                        </div>
                        <div id="google-status-badge" class="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium shrink-0 whitespace-nowrap bg-surfaceHighlight text-textMuted">
                            <i data-lucide="loader" class="w-3 h-3 animate-spin"></i>
                            <span data-i18n="common.loading">Loading</span>
                        </div>
                    </div>
                    <div id="google-link-content">
                        <!-- Dynamically rendered by GoogleLinkApp -->
                    </div>
                </div>
            </section>
        </div>
    </div>
`;

// Side-effect module — Components 掛在 window 上供 Components.inject 取用。
export {};
