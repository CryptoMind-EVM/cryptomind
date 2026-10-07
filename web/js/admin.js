// ========================================
// admin.js - Admin Panel Module
// ========================================

import { userFacingMessage } from './error-message.js';

const AdminPanel = {
    currentSubPage: 'broadcast',
    initialized: false,

    init() {
        const container = document.getElementById('admin-content');
        if (!container) return;

        // Render shell with sub-nav
        container.innerHTML = `
            <!-- Page Header -->
            <div class="mb-5">
                <h1 class="text-lg font-semibold text-textMain tracking-tight font-serif">
                    ${window.I18n ? window.I18n.t('admin.title') || 'Admin' : 'Admin'}
                </h1>
                <p class="text-xs text-textMuted mt-0.5">${window.I18n ? window.I18n.t('admin.subtitle') || 'Manage platform, users, stats & monitoring' : 'Manage platform, users, stats & monitoring'}</p>
            </div>
            <!-- Sub Navigation -->
            <div class="flex flex-wrap gap-1.5 mb-6 border-b border-borderSubtle pb-3">
                <button id="admin-subnav-broadcast" data-click="AdminPanel.switchSubPage" data-click-arg="broadcast"
                        class="admin-subnav-btn px-3 py-1.5 rounded-lg text-[13px] font-medium transition flex items-center gap-1.5 text-textMuted hover:bg-surfaceHighlight hover:text-textSecondary active:scale-[0.98]">
                    <i data-lucide="megaphone" class="w-4 h-4"></i>
                    <span>${window.I18n ? window.I18n.t('admin.broadcast') || 'Broadcast' : 'Broadcast'}</span>
                </button>
                <button id="admin-subnav-users" data-click="AdminPanel.switchSubPage" data-click-arg="users"
                        class="admin-subnav-btn px-3 py-1.5 rounded-lg text-[13px] font-medium transition flex items-center gap-1.5 text-textMuted hover:bg-surfaceHighlight hover:text-textSecondary active:scale-[0.98]">
                    <i data-lucide="users" class="w-4 h-4"></i>
                    <span>${window.I18n ? window.I18n.t('admin.users') || 'Users' : 'Users'}</span>
                </button>
                <button id="admin-subnav-forum" data-click="AdminPanel.switchSubPage" data-click-arg="forum"
                        class="admin-subnav-btn px-3 py-1.5 rounded-lg text-[13px] font-medium transition flex items-center gap-1.5 text-textMuted hover:bg-surfaceHighlight hover:text-textSecondary active:scale-[0.98]">
                    <i data-lucide="message-square" class="w-4 h-4"></i>
                    <span>${window.I18n ? window.I18n.t('admin.posts') || 'Forum' : 'Forum'}</span>
                </button>
                <button id="admin-subnav-config" data-click="AdminPanel.switchSubPage" data-click-arg="config"
                        class="admin-subnav-btn px-3 py-1.5 rounded-lg text-[13px] font-medium transition flex items-center gap-1.5 text-textMuted hover:bg-surfaceHighlight hover:text-textSecondary active:scale-[0.98]">
                    <i data-lucide="sliders" class="w-4 h-4"></i>
                    <span>${window.I18n ? window.I18n.t('admin.config') || 'Config' : 'Config'}</span>
                </button>
                <button id="admin-subnav-settings" data-click="AdminPanel.switchSubPage" data-click-arg="settings"
                        class="admin-subnav-btn px-3 py-1.5 rounded-lg text-[13px] font-medium transition flex items-center gap-1.5 text-textMuted hover:bg-surfaceHighlight hover:text-textSecondary active:scale-[0.98]">
                    <i data-lucide="toggle-right" class="w-4 h-4"></i>
                    <span>${window.I18n ? window.I18n.t('admin.settingsCenter.nav') || 'Settings' : 'Settings'}</span>
                </button>
                <button id="admin-subnav-stats" data-click="AdminPanel.switchSubPage" data-click-arg="stats"
                        class="admin-subnav-btn px-3 py-1.5 rounded-lg text-[13px] font-medium transition flex items-center gap-1.5 text-textMuted hover:bg-surfaceHighlight hover:text-textSecondary active:scale-[0.98]">
                    <i data-lucide="bar-chart-3" class="w-4 h-4"></i>
                    <span>${window.I18n ? window.I18n.t('admin.stats') || 'Stats' : 'Stats'}</span>
                </button>
                <button id="admin-subnav-visitors" data-click="AdminPanel.switchSubPage" data-click-arg="visitors"
                        class="admin-subnav-btn px-3 py-1.5 rounded-lg text-[13px] font-medium transition flex items-center gap-1.5 text-textMuted hover:bg-surfaceHighlight hover:text-textSecondary active:scale-[0.98]">
                    <i data-lucide="eye" class="w-4 h-4"></i>
                    <span>${window.I18n ? window.I18n.t('admin.visitors') || 'Visitors' : 'Visitors'}</span>
                </button>
                <button id="admin-subnav-audit" data-click="AdminPanel.switchSubPage" data-click-arg="audit"
                        class="admin-subnav-btn px-3 py-1.5 rounded-lg text-[13px] font-medium transition flex items-center gap-1.5 text-textMuted hover:bg-surfaceHighlight hover:text-textSecondary active:scale-[0.98]">
                    <i data-lucide="shield-alert" class="w-4 h-4"></i>
                    <span>${window.I18n ? window.I18n.t('admin.audit') || 'Audit' : 'Audit'}</span>
                </button>
                <button id="admin-subnav-wallet-monitor" data-click="AdminPanel.switchSubPage" data-click-arg="wallet-monitor"
                        class="admin-subnav-btn px-3 py-1.5 rounded-lg text-[13px] font-medium transition flex items-center gap-1.5 text-textMuted hover:bg-surfaceHighlight hover:text-textSecondary active:scale-[0.98]">
                    <i data-lucide="wallet" class="w-4 h-4"></i>
                    <span>${window.I18n ? window.I18n.t('admin.walletMonitor') || 'Wallet Monitor' : 'Wallet Monitor'}</span>
                </button>
            </div>
            <!-- Sub Page Content -->
            <div id="admin-subpage-content"></div>
        `;

        AppUtils.refreshIcons();
        this.switchSubPage(this.currentSubPage);
        this.initialized = true;
    },

    switchSubPage(page) {
        this.currentSubPage = page;

        // Update sub-nav active state
        document.querySelectorAll('.admin-subnav-btn').forEach((btn) => {
            btn.classList.remove('bg-primary/10', 'text-primary', 'border-primary/30');
            btn.classList.add('text-textMuted', 'hover:bg-surfaceHighlight', 'hover:text-textSecondary');
        });
        const activeBtn = document.getElementById(`admin-subnav-${page}`);
        if (activeBtn) {
            activeBtn.classList.add('bg-primary/10', 'text-primary');
            activeBtn.classList.remove('text-textMuted', 'hover:bg-surfaceHighlight', 'hover:text-textSecondary');
        }

        // Render sub page
        switch (page) {
            case 'broadcast':
                this.BroadcastManager.render();
                break;
            case 'users':
                this.UserManager.render();
                break;
            case 'forum':
                this.ForumManager.render();
                break;
            case 'config':
                this.ConfigManager.render();
                break;
            case 'settings':
                if (window.AdminSettingsCenter) AdminSettingsCenter.render();
                else
                    document.getElementById('admin-subpage-content').innerHTML =
                        '<div class="text-center text-textMuted py-12">Settings module loading...</div>';
                break;
            case 'stats':
                if (window.AdminStatsManager) AdminStatsManager.render();
                else
                    document.getElementById('admin-subpage-content').innerHTML =
                        '<div class="text-center text-textMuted py-12">Stats module loading...</div>';
                break;
            case 'visitors':
                if (window.AdminVisitorsManager) AdminVisitorsManager.render();
                else
                    document.getElementById('admin-subpage-content').innerHTML =
                        '<div class="text-center text-textMuted py-12">Visitors module loading...</div>';
                break;
            case 'audit':
                if (window.AdminAuditManager) AdminAuditManager.render();
                else
                    document.getElementById('admin-subpage-content').innerHTML =
                        '<div class="text-center text-textMuted py-12">Audit module loading...</div>';
                break;
            case 'wallet-monitor':
                if (window.AdminWalletMonitorManager) AdminWalletMonitorManager.render();
                else
                    document.getElementById('admin-subpage-content').innerHTML =
                        '<div class="text-center text-textMuted py-12">Wallet Monitor module loading...</div>';
                break;
        }
    },

    // ========================================
    // Broadcast Manager
    // ========================================
    BroadcastManager: {
        render() {
            const container = document.getElementById('admin-subpage-content');
            if (!container) return;

            container.innerHTML = `
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    <!-- Send Form -->
                    <div class="bg-surface rounded-2xl border border-borderSubtle p-5">
                        <h3 class="font-bold text-secondary mb-4 flex items-center gap-2">
                            <i data-lucide="send" class="w-4 h-4"></i> Send Broadcast
                        </h3>
                        <div class="space-y-4">
                            <div>
                                <label class="text-xs text-textMuted mb-1 block" data-i18n="admin.notifyType">Type</label>
                                <select id="broadcast-type" class="w-full bg-background border border-borderLight rounded-xl px-3 py-2 text-sm text-secondary">
                                    <option value="announcement" data-i18n="admin.notifyAnnouncement">Announcement</option>
                                    <option value="system_update" data-i18n="admin.notifySystemUpdate">System Update</option>
                                </select>
                            </div>
                            <div>
                                <label class="text-xs text-textMuted mb-1 block" data-i18n="admin.notifyTitle">Title</label>
                                <input id="broadcast-title" type="text" maxlength="200" placeholder="${window.I18n ? window.I18n.t('admin.notifTitlePlaceholder') : 'Notification title...'}"
                                       class="w-full bg-background border border-borderLight rounded-xl px-3 py-2 text-sm text-secondary placeholder:text-textMuted/50 focus:border-primary/50 focus:outline-none">
                            </div>
                            <div>
                                <label class="text-xs text-textMuted mb-1 block" data-i18n="admin.notifyBody">Body</label>
                                <textarea id="broadcast-body" rows="4" maxlength="1000" placeholder="${window.I18n ? window.I18n.t('admin.notifContentPlaceholder') : 'Notification content...'}"
                                          class="w-full bg-background border border-borderLight rounded-xl px-3 py-2 text-sm text-secondary placeholder:text-textMuted/50 focus:border-primary/50 focus:outline-none resize-none"></textarea>
                            </div>
                            <!-- Preview -->
                            <div id="broadcast-preview" class="hidden bg-background/50 rounded-xl p-3 border border-borderSubtle">
                                <div class="text-[10px] text-textMuted mb-1 uppercase tracking-wider"data-i18n="admin.notifyPreview">Preview</div>
                                <div id="broadcast-preview-title" class="text-sm font-medium text-secondary"></div>
                                <div id="broadcast-preview-body" class="text-xs text-textMuted mt-1"></div>
                            </div>
                            <button data-click="AdminPanel.BroadcastManager.send"
                                    id="broadcast-send-btn"
                                    class="w-full py-2.5 bg-primary hover:bg-primary/80 text-background rounded-xl text-sm font-bold transition flex items-center justify-center gap-2">
                                <i data-lucide="send" class="w-4 h-4"></i> ${window.I18n ? window.I18n.t('admin.sendToAll') : 'Send to All Users'}
                            </button>
                        </div>
                    </div>

                    <!-- History -->
                    <div class="bg-surface rounded-2xl border border-borderSubtle p-5">
                        <h3 class="font-bold text-secondary mb-4 flex items-center gap-2">
                            <i data-lucide="history" class="w-4 h-4"></i> Broadcast History
                        </h3>
                        <div id="broadcast-history" class="space-y-3">
                            <div class="text-center text-textMuted text-sm py-8">Loading...</div>
                        </div>
                    </div>
                </div>
            `;

            AppUtils.refreshIcons();
            this._attachPreviewListeners();
            this.loadHistory();
        },

        _attachPreviewListeners() {
            const title = document.getElementById('broadcast-title');
            const body = document.getElementById('broadcast-body');
            const preview = document.getElementById('broadcast-preview');

            const update = () => {
                const t = title?.value?.trim();
                const b = body?.value?.trim();
                if (t || b) {
                    preview.classList.remove('hidden');
                    document.getElementById('broadcast-preview-title').textContent =
                        t || '(no title)';
                    document.getElementById('broadcast-preview-body').textContent =
                        b || '(no body)';
                } else {
                    preview.classList.add('hidden');
                }
            };

            if (title) title.addEventListener('input', update);
            if (body) body.addEventListener('input', update);
        },

        async send() {
            const title = document.getElementById('broadcast-title')?.value?.trim();
            const body = document.getElementById('broadcast-body')?.value?.trim();
            const type = document.getElementById('broadcast-type')?.value || 'announcement';
            const btn = document.getElementById('broadcast-send-btn');

            if (!title || !body) {
                if (typeof showToast === 'function')
                    showToast(window.I18n ? window.I18n.t('admin.fillTitleBody') : 'Please fill in title and body', 'error');
                return;
            }

            btn.disabled = true;
            btn.innerHTML =
                '<div class="w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin"></div> ' + (window.I18n ? window.I18n.t('admin.sending') : 'Sending...');

            try {
                const data = await AppAPI.post('/api/admin/notifications/broadcast', { title, body, type });
                if (typeof showToast === 'function') {
                    showToast(
                        window.I18n ? window.I18n.t('admin.sentSummary', { sent: data.sent_count, online: data.online_count }) : `Sent to ${data.sent_count} users (${data.online_count} online)`,
                        'success'
                    );
                }

                // Clear form
                document.getElementById('broadcast-title').value = '';
                document.getElementById('broadcast-body').value = '';
                document.getElementById('broadcast-preview').classList.add('hidden');

                // Reload history
                this.loadHistory();
            } catch (e) {
                if (typeof showToast === 'function') showToast(e.message, 'error');
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<i data-lucide="send" class="w-4 h-4"></i> ' + (window.I18n ? window.I18n.t('admin.sendToAll') : 'Send to All Users');
                AppUtils.refreshIcons();
            }
        },

        async loadHistory() {
            const container = document.getElementById('broadcast-history');
            if (!container) return;

            try {
                const data = await AppAPI.get('/api/admin/notifications/history?limit=20');
                if (!data.broadcasts || data.broadcasts.length === 0) {
                    container.innerHTML =
                        '<div class="text-center text-textMuted text-sm py-8" data-i18n="admin.noBroadcasts">No broadcasts yet</div>';
                    return;
                }

                container.innerHTML = data.broadcasts
                    .map((b) => {
                        const typeLabel =
                            b.type === 'system_update' ? 'System Update' : 'Announcement';
                        const typeColor =
                            b.type === 'system_update' ? 'text-success' : 'text-accent';
                        const time = b.created_at ? new Date(b.created_at).toLocaleString() : '';

                        return `
                        <div class="p-3 bg-background/50 rounded-xl border border-borderSubtle">
                            <div class="flex items-center justify-between mb-1">
                                <span class="text-xs font-medium ${typeColor}">${typeLabel}</span>
                                <span class="text-[10px] text-textMuted">${time}</span>
                            </div>
                            <div class="text-sm font-medium text-secondary">${this._escapeHtml(b.title)}</div>
                            <div class="text-xs text-textMuted mt-0.5 line-clamp-2">${this._escapeHtml(b.body)}</div>
                            <div class="text-[10px] text-textMuted/60 mt-1">${b.recipient_count} recipients</div>
                        </div>
                    `;
                    })
                    .join('');
            } catch (e) {
                container.innerHTML = `<div class="text-center text-danger text-sm py-4" data-i18n="admin.loadHistoryFailed">Failed to load history</div>`;
            }
        },

        _escapeHtml(str) {
            if (!str) return '';
            return str
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
        },
    },

    // ========================================
    // User Manager
    // ========================================
    UserManager: {
        currentPage: 1,
        searchQuery: '',

        render() {
            const container = document.getElementById('admin-subpage-content');
            if (!container) return;

            container.innerHTML = `
                <div class="bg-surface rounded-2xl border border-borderSubtle p-5">
                    <!-- Search Bar -->
                    <div class="flex gap-3 mb-4">
                        <div class="flex-1 relative">
                            <i data-lucide="search" class="w-4 h-4 text-textMuted absolute left-3 top-1/2 -translate-y-1/2"></i>
                            <input id="admin-user-search" type="text" placeholder="Search by username or user ID..." data-i18n="admin.userSearchPlaceholder" data-i18n-attr="placeholder"
                                   value="${this._escapeHtml(this.searchQuery)}"
                                   class="w-full bg-background border border-borderLight rounded-xl pl-10 pr-3 py-2 text-sm text-secondary placeholder:text-textMuted/50 focus:border-primary/50 focus:outline-none">
                        </div>
                        <button data-click="AdminPanel.UserManager.doSearch"
                                class="px-4 py-2 bg-primary/20 hover:bg-primary/30 text-primary rounded-xl text-sm font-medium transition">
                            Search
                        </button>
                    </div>

                    <!-- User List -->
                    <div id="admin-user-list">
                        <div class="text-center text-textMuted text-sm py-8">Loading...</div>
                    </div>

                    <!-- Pagination -->
                    <div id="admin-user-pagination" class="flex items-center justify-between mt-4 hidden">
                        <button data-click="AdminPanel.UserManager.prevPage" id="admin-users-prev"
                                class="px-3 py-1.5 bg-surfaceHighlight hover:bg-surfaceHighlight rounded-lg text-xs text-textMuted transition">
                            Previous
                        </button>
                        <span id="admin-users-page-info" class="text-xs text-textMuted"></span>
                        <button data-click="AdminPanel.UserManager.nextPage" id="admin-users-next"
                                class="px-3 py-1.5 bg-surfaceHighlight hover:bg-surfaceHighlight rounded-lg text-xs text-textMuted transition">
                            Next
                        </button>
                    </div>
                </div>

                <!-- User Action Modal -->
                <!-- 手機上內容比視窗高：標題列（含叉叉）固定在頂端不隨內文捲動，
                     否則往下捲去看 Actions 時叉叉就不見了（2026-09-11 DANNY 回報）。 -->
                <div id="admin-user-modal" class="fixed inset-0 bg-black/60 z-[70] hidden flex items-center justify-center p-4">
                    <div class="bg-surface rounded-2xl border border-borderLight w-full max-w-md max-h-[85dvh] flex flex-col overflow-hidden" id="admin-user-modal-content">
                    </div>
                </div>
            `;

            AppUtils.refreshIcons();

            // Enter key search
            const searchInput = document.getElementById('admin-user-search');
            if (searchInput) {
                searchInput.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') this.doSearch();
                });
            }

            this.loadUsers();
        },

        doSearch() {
            this.searchQuery = document.getElementById('admin-user-search')?.value?.trim() || '';
            this.currentPage = 1;
            this.loadUsers();
        },

        prevPage() {
            if (this.currentPage > 1) {
                this.currentPage--;
                this.loadUsers();
            }
        },

        nextPage() {
            this.currentPage++;
            this.loadUsers();
        },

        async loadUsers() {
            const listEl = document.getElementById('admin-user-list');
            if (!listEl) return;

            listEl.innerHTML =
                '<div class="text-center text-textMuted text-sm py-8"><div class="w-5 h-5 border-2 border-primary/30 border-t-primary rounded-full animate-spin mx-auto mb-2"></div>Loading...</div>';

            try {
                let url = `/api/admin/users?page=${this.currentPage}&limit=20`;
                if (this.searchQuery) url += `&search=${encodeURIComponent(this.searchQuery)}`;

                const data = await AppAPI.get(url);
                const users = data.users || [];
                const total = data.total || 0;
                const totalPages = Math.ceil(total / 20);

                if (users.length === 0) {
                    listEl.innerHTML =
                        '<div class="text-center text-textMuted text-sm py-8" data-i18n="admin.noUsers">No users found</div>';
                    document.getElementById('admin-user-pagination')?.classList.add('hidden');
                    return;
                }

                listEl.innerHTML = `
                    <div class="text-xs text-textMuted mb-3">${total} users total</div>
                    <div class="space-y-2">
                        ${users.map((u) => this._renderUserRow(u)).join('')}
                    </div>
                `;

                // Pagination
                const pagEl = document.getElementById('admin-user-pagination');
                if (pagEl && totalPages > 1) {
                    pagEl.classList.remove('hidden');
                    document.getElementById('admin-users-page-info').textContent =
                        `Page ${this.currentPage} / ${totalPages}`;
                    document.getElementById('admin-users-prev').disabled = this.currentPage <= 1;
                    document.getElementById('admin-users-next').disabled =
                        this.currentPage >= totalPages;
                } else if (pagEl) {
                    pagEl.classList.add('hidden');
                }

                AppUtils.refreshIcons();
            } catch (e) {
                listEl.innerHTML = `<div class="text-center text-danger text-sm py-4">${SecurityUtils.escapeHTML(`${window.I18n ? window.I18n.t('admin.loadUsersFailed') : 'Failed to load users'}: ${userFacingMessage(e)}`)}</div>`;
            }
        },

        _renderUserRow(user) {
            const roleBadge =
                user.role === 'admin'
                    ? '<span class="text-[10px] px-1.5 py-0.5 bg-danger/20 text-danger rounded-full font-bold"data-i18n="admin.roleAdmin">ADMIN</span>'
                    : '';
            const normalizedTier = (user.membership_tier || 'free').toLowerCase();
            const premiumBadge =
                ['premium', 'pro', 'plus'].includes(normalizedTier)
                    ? '<span class="text-[10px] px-1.5 py-0.5 bg-accent/20 text-accent rounded-full font-bold"data-i18n="admin.membershipPremium">PREMIUM</span>'
                    : '';
            const statusDot = user.is_active
                ? '<div class="w-2 h-2 rounded-full bg-success shrink-0" title="' + (window.I18n ? window.I18n.t('admin.statusActive') : 'Active') + '"></div>'
                : '<div class="w-2 h-2 rounded-full bg-danger shrink-0" title="' + (window.I18n ? window.I18n.t('admin.statusSuspended') : 'Suspended') + '"></div>';
            const time = user.created_at ? new Date(user.created_at).toLocaleDateString() : '';

            return `
                <div class="flex items-center gap-3 p-3 bg-background/50 rounded-xl border border-borderSubtle hover:border-borderLight transition cursor-pointer"
                     data-click="AdminPanel.UserManager.openUserModal" data-click-arg="${encodeURIComponent(user.user_id)}">
                    ${statusDot}
                    <div class="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center text-primary text-sm font-bold shrink-0">
                        ${(user.username || '?')[0].toUpperCase()}
                    </div>
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2">
                            <span class="text-sm font-medium text-secondary truncate">${this._escapeHtml(user.username)}</span>
                            ${roleBadge}${premiumBadge}
                        </div>
                        <div class="text-[10px] text-textMuted truncate">${user.user_id}</div>
                    </div>
                    <div class="text-[10px] text-textMuted shrink-0">${time}</div>
                    <i data-lucide="chevron-right" class="w-4 h-4 text-textMuted/50 shrink-0"></i>
                </div>
            `;
        },

        // refresh=true：操作完成後就地更新——不先清成轉圈、保留捲動位置。
        // 2026-09-26 DANNY 回報「點會員沒反應」：原本每次操作後整個視窗清成轉圈、
        // 重畫後跳回頂端，剛按的按鈕已捲出畫面，看起來像沒反應，再按一次反而改回去。
        async openUserModal(userId, refresh = false) {
            const modal = document.getElementById('admin-user-modal');
            const content = document.getElementById('admin-user-modal-content');
            if (!modal || !content) return;

            const inPlace = refresh === true && !modal.classList.contains('hidden');
            const prevBody = document.getElementById('admin-user-modal-body');
            const prevScroll = inPlace && prevBody ? prevBody.scrollTop : 0;
            modal.classList.remove('hidden');
            if (!inPlace) {
                content.innerHTML = `
                <div class="flex items-center px-5 pt-5 pb-4 shrink-0">${this._closeButtonHtml()}</div>
                <div class="p-8 text-center"><div class="w-6 h-6 border-2 border-primary/30 border-t-primary rounded-full animate-spin mx-auto"></div></div>`;
                AppUtils.refreshIcons();
            }

            // Close on backdrop click
            modal.onclick = (e) => {
                if (e.target === modal) modal.classList.add('hidden');
            };

            try {
                const data = await AppAPI.get(`/api/admin/users/${userId}`);
                const u = data.user;

                const isActive = u.is_active;
                const normalizedTier = (u.membership_tier || 'free').toLowerCase();
                const isPro = ['premium', 'pro', 'plus'].includes(normalizedTier);
                const displayTier = isPro ? 'premium' : 'free';
                const isAdmin = u.role === 'admin';
                const tt = (key, fallback) => (window.I18n ? window.I18n.t(key) || fallback : fallback);

                content.innerHTML = `
                    <!-- Header（固定，不隨內文捲動） -->
                    <div class="flex items-center gap-3 px-5 pt-5 pb-4 border-b border-borderSubtle shrink-0">
                        <div class="w-12 h-12 rounded-full bg-primary/20 flex items-center justify-center text-primary text-lg font-bold shrink-0">
                            ${(u.username || '?')[0].toUpperCase()}
                        </div>
                        <div class="min-w-0">
                            <div class="font-bold text-secondary truncate">${this._escapeHtml(u.username)}</div>
                            <div class="text-xs text-textMuted truncate">${u.user_id}</div>
                        </div>
                        ${this._closeButtonHtml()}
                    </div>

                    <div id="admin-user-modal-body" class="p-5 overflow-y-auto min-h-0">
                        <!-- Info Grid -->
                        <div class="grid grid-cols-2 gap-3 mb-5 text-xs">
                            <div class="bg-background/50 rounded-xl p-3">
                                <div class="text-textMuted mb-0.5" data-i18n="admin.colRole">Role</div>
                                <div class="text-secondary font-medium">${u.role || 'user'}</div>
                            </div>
                            <div class="bg-background/50 rounded-xl p-3">
                                <div class="text-textMuted mb-0.5" data-i18n="admin.colMembership">Membership</div>
                                <div class="text-secondary font-medium">${displayTier}${u.membership_expires_at ? ' (expires ' + new Date(u.membership_expires_at).toLocaleDateString() + ')' : ''}</div>
                            </div>
                            <div class="bg-background/50 rounded-xl p-3">
                                <div class="text-textMuted mb-0.5" data-i18n="admin.colStatus">Status</div>
                                <div class="${isActive ? 'text-success' : 'text-danger'} font-medium">${isActive ? 'Active' : 'Suspended'}</div>
                            </div>
                            <div class="bg-background/50 rounded-xl p-3">
                                <div class="text-textMuted mb-0.5" data-i18n="admin.colJoined">Joined</div>
                                <div class="text-secondary font-medium">${u.created_at ? new Date(u.created_at).toLocaleDateString() : 'N/A'}</div>
                            </div>
                        </div>

                        <!-- Usage Depth（P1-4）-->
                        <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5 text-xs">
                            <div class="bg-background/50 rounded-xl p-3">
                                <div class="text-textMuted mb-0.5">${window.I18n ? window.I18n.t('admin.userDetailChat') || 'Chat' : 'Chat'}</div>
                                <div class="text-secondary font-medium">${u.chat_message_count ?? 0} msgs</div>
                            </div>
                            <div class="bg-background/50 rounded-xl p-3">
                                <div class="text-textMuted mb-0.5">${window.I18n ? window.I18n.t('admin.userDetailMemory') || 'Memory' : 'Memory'}</div>
                                <div class="text-secondary font-medium">${u.memory_count ?? 0}</div>
                            </div>
                            <div class="bg-background/50 rounded-xl p-3">
                                <div class="text-textMuted mb-0.5">${window.I18n ? window.I18n.t('admin.userDetailSkill') || 'Skills' : 'Skills'}</div>
                                <div class="text-secondary font-medium">${u.custom_skill_count ?? 0}</div>
                            </div>
                            <div class="bg-background/50 rounded-xl p-3">
                                <div class="text-textMuted mb-0.5">${window.I18n ? window.I18n.t('admin.userDetailWallet') || 'Wallet' : 'Wallet'}</div>
                                <div class="text-secondary font-mono text-[10px] break-all">${u.wallet_address ? u.wallet_address.slice(0, 12) + '…' : '—'}</div>
                            </div>
                        </div>

                        <!-- Recent Activity（P1-4）-->
                        ${(u.recent_audit && u.recent_audit.length) ? `
                        <div class="mb-5">
                            <div class="text-textMuted text-xs mb-2">${window.I18n ? window.I18n.t('admin.userDetailRecentActivity') || 'Recent Activity' : 'Recent Activity'}</div>
                            <div class="space-y-1 max-h-32 overflow-y-auto">
                                ${u.recent_audit.map((a) => `
                                    <div class="flex items-center gap-2 text-[11px] py-1 px-2 rounded-lg bg-background/30">
                                        <span class="${a.success ? 'text-success' : 'text-danger'}">${a.success ? '✓' : '✗'}</span>
                                        <span class="text-secondary font-mono">${a.action || '?'}</span>
                                        <span class="text-textMuted ml-auto">${a.at ? new Date(a.at).toLocaleString() : ''}</span>
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                        ` : ''}

                        <!-- Actions -->
                        <div class="space-y-2">
                            <h4 class="text-xs text-textMuted font-medium uppercase tracking-wider mb-2"data-i18n="admin.actions">Actions</h4>

                            <!-- Role Toggle -->
                            <button data-click="AdminPanel.UserManager.setRole" data-click-element data-click-args="${encodeURIComponent(JSON.stringify([u.user_id, isAdmin ? 'user' : 'admin']))}"
                                    class="w-full flex items-center gap-3 p-3 rounded-xl border border-borderSubtle hover:bg-surfaceHighlight transition text-left">
                                <i data-lucide="${isAdmin ? 'shield-off' : 'shield'}" class="w-4 h-4 ${isAdmin ? 'text-textMuted' : 'text-danger'}"></i>
                                <div>
                                    <div class="text-sm text-secondary">${isAdmin ? 'Remove Admin' : 'Make Admin'}</div>
                                    <div class="text-[10px] text-textMuted">${isAdmin ? (window.I18n ? window.I18n.t('admin.demote') : 'Demote to regular user') : (window.I18n ? window.I18n.t('admin.grantAdmin') : 'Grant admin privileges')}</div>
                                </div>
                            </button>

                            <!-- Membership Toggle -->
                            <button data-click="AdminPanel.UserManager.setMembership" data-click-element data-click-args="${encodeURIComponent(JSON.stringify([u.user_id, isPro ? 'free' : 'pro']))}"
                                    class="w-full flex items-center gap-3 p-3 rounded-xl border border-borderSubtle hover:bg-surfaceHighlight transition text-left">
                                <i data-lucide="${isPro ? 'star-off' : 'star'}" class="w-4 h-4 ${isPro ? 'text-textMuted' : 'text-accent'}"></i>
                                <div>
                                    <div class="text-sm text-secondary">${isPro ? 'Remove Pro' : 'Grant Pro (1 month)'}</div>
                                    <div class="text-[10px] text-textMuted">${isPro ? (window.I18n ? window.I18n.t('admin.downgrade') : 'Downgrade to free tier') : (window.I18n ? window.I18n.t('admin.upgradePro') : 'Upgrade to pro membership')}</div>
                                </div>
                            </button>

                            <!-- Status Toggle -->
                            <button data-click="AdminPanel.UserManager.toggleStatus" data-click-element data-click-args="${encodeURIComponent(JSON.stringify([u.user_id, !isActive]))}"
                                    class="w-full flex items-center gap-3 p-3 rounded-xl border ${isActive ? 'border-danger/20 hover:bg-danger/5' : 'border-success/20 hover:bg-success/5'} transition text-left">
                                <i data-lucide="${isActive ? 'ban' : 'check-circle'}" class="w-4 h-4 ${isActive ? 'text-danger' : 'text-success'}"></i>
                                <div>
                                    <div class="text-sm ${isActive ? 'text-danger' : 'text-success'}">${isActive ? 'Suspend Account' : 'Reactivate Account'}</div>
                                    <div class="text-[10px] text-textMuted">${isActive ? (window.I18n ? window.I18n.t('admin.blockUser') : 'Block user from accessing the platform') : (window.I18n ? window.I18n.t('admin.restoreUser') : 'Restore user access')}</div>
                                </div>
                            </button>

                            <!-- Manual USDC settle（2026-09-12）：自動驗證對不上的付款由 admin 核銷 -->
                            <div class="p-3 rounded-xl border border-borderSubtle space-y-2">
                                <div class="text-sm text-secondary">${tt('admin.settleUsdc', 'Settle USDC payment manually')}</div>
                                <div class="text-[10px] text-textMuted">${tt('admin.settleUsdcHint', 'For transfers the automatic verifier cannot match (expired order, unbound wallet, exchange withdrawal). The transfer must have reached our receiving address.')}</div>
                                <input id="admin-settle-tx" type="text" autocomplete="off" spellcheck="false" placeholder="0x… transaction hash"
                                       class="w-full bg-background border border-borderLight rounded-xl px-3 py-2 text-xs font-mono text-secondary placeholder:text-textMuted/50 focus:border-primary/50 focus:outline-none">
                                <div class="flex gap-2">
                                    <select id="admin-settle-plan" class="flex-1 min-w-0 bg-background border border-borderLight rounded-xl px-2 py-2 text-xs text-secondary">
                                        <option value="premium_monthly">${tt('admin.settlePlanMonthly', 'Monthly (1 month)')}</option>
                                        <option value="premium_yearly">${tt('admin.settlePlanYearly', 'Yearly (12 months)')}</option>
                                    </select>
                                    <button data-click="AdminPanel.UserManager.settleUsdc" data-click-element data-click-arg="${encodeURIComponent(u.user_id)}"
                                            class="px-4 py-2 bg-primary/20 hover:bg-primary/30 text-primary rounded-xl text-xs font-medium transition shrink-0">
                                        ${tt('admin.settleUsdcButton', 'Settle')}
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                `;

                AppUtils.refreshIcons();
                if (prevScroll) {
                    const body = document.getElementById('admin-user-modal-body');
                    if (body) body.scrollTop = prevScroll;
                }
            } catch (e) {
                content.innerHTML = `
                    <div class="flex items-center px-5 pt-5 pb-4 shrink-0">${this._closeButtonHtml()}</div>
                    <div class="p-8 text-center text-danger text-sm">${SecurityUtils.escapeHTML(`${window.I18n ? window.I18n.t('admin.loadUserFailed') : 'Failed to load user'}: ${userFacingMessage(e)}`)}</div>`;
                AppUtils.refreshIcons();
            }
        },

        // 關閉鈕：44px 觸控目標（原本 p-2 + 16px 圖示只有 32px，手機上很難按準）
        _closeButtonHtml() {
            return `
                <button data-click="closeModal" data-click-arg="admin-user-modal" aria-label="Close"
                        class="ml-auto -mr-2 w-11 h-11 flex items-center justify-center rounded-xl hover:bg-surfaceHighlight transition shrink-0">
                    <i data-lucide="x" class="w-5 h-5 text-textMuted"></i>
                </button>`;
        },

        // 手動核銷：讀彈窗內的 tx hash＋方案，打 /settle-usdc
        // 送出期間鎖住按鈕並顯示轉圈：立刻有回饋，也擋掉連點（連點會把升級又改回降級）。
        // 回傳 false 表示這顆按鈕已在處理中。
        _setBusy(btn, busy) {
            if (!btn) return true;
            if (busy && btn.getAttribute('aria-busy') === 'true') return false;
            btn.setAttribute('aria-busy', busy ? 'true' : 'false');
            btn.disabled = busy;
            btn.classList.toggle('opacity-60', busy);
            const spin = btn.querySelector('.admin-action-spinner');
            if (busy && !spin) {
                btn.insertAdjacentHTML('beforeend', '<span class="admin-action-spinner ml-auto w-4 h-4 border-2 border-primary/30 border-t-primary rounded-full animate-spin shrink-0"></span>');
            } else if (!busy && spin) {
                spin.remove();
            }
            return true;
        },

        async settleUsdc(userId, btn) {
            const tt = (key, fallback) => (window.I18n ? window.I18n.t(key) || fallback : fallback);
            const txEl = document.getElementById('admin-settle-tx');
            const planEl = document.getElementById('admin-settle-plan');
            const txHash = ((txEl && txEl.value) || '').trim();
            if (!/^0x[0-9a-fA-F]{64}$/.test(txHash)) {
                if (typeof showToast === 'function')
                    showToast(tt('admin.settleTxInvalid', 'Enter a valid transaction hash (0x + 64 hex characters)'), 'warning');
                return;
            }
            if (!this._setBusy(btn, true)) return;
            try {
                const res = await AppAPI.post(`/api/admin/users/${userId}/settle-usdc`, {
                    tx_hash: txHash,
                    plan: planEl ? planEl.value : 'premium_monthly',
                });
                if (typeof showToast === 'function')
                    showToast(tt('admin.settleDone', 'Payment settled — membership extended') + (res && res.months ? ` (+${res.months}m)` : ''), 'success');
                await this.openUserModal(userId, true);
                this.loadUsers();
            } catch (e) {
                this._setBusy(btn, false);
                if (typeof showToast === 'function') showToast(e.message || tt('admin.settleFailed', 'Settle failed'), 'error');
            }
        },

        async setRole(userId, newRole, btn) {
            if (!this._setBusy(btn, true)) return;
            try {
                await AppAPI.put(`/api/admin/users/${userId}/role`, { role: newRole });
                if (typeof showToast === 'function')
                    showToast(window.I18n ? window.I18n.t('admin.roleUpdated', { role: newRole }) : `Role updated to ${newRole}`, 'success');
                await this.openUserModal(userId, true); // 就地刷新
                this.loadUsers(); // Refresh list
            } catch (e) {
                this._setBusy(btn, false);
                if (typeof showToast === 'function') showToast(e.message, 'error');
            }
        },

        async setMembership(userId, tier, btn) {
            if (!this._setBusy(btn, true)) return;
            try {
                const body = { tier };
                if (tier === 'pro') body.months = 1;

                await AppAPI.put(`/api/admin/users/${userId}/membership`, body);
                if (typeof showToast === 'function')
                    showToast(window.I18n ? window.I18n.t('admin.membershipSet', { tier: tier }) : `Membership set to ${tier}`, 'success');
                await this.openUserModal(userId, true);
                this.loadUsers();
            } catch (e) {
                this._setBusy(btn, false);
                if (typeof showToast === 'function') showToast(e.message, 'error');
            }
        },

        async toggleStatus(userId, active, btn) {
            if (btn && btn.getAttribute('aria-busy') === 'true') return;
            const reason = active ? null : prompt('Suspension reason (optional):');
            if (!this._setBusy(btn, true)) return;
            try {
                await AppAPI.put(`/api/admin/users/${userId}/status`, { active, reason });
                if (typeof showToast === 'function')
                    showToast(active ? 'Account reactivated' : 'Account suspended', 'success');
                await this.openUserModal(userId, true);
                this.loadUsers();
            } catch (e) {
                this._setBusy(btn, false);
                if (typeof showToast === 'function') showToast(e.message, 'error');
            }
        },

        _escapeHtml(str) {
            if (!str) return '';
            return str
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
        },
    },

    // ========================================
    // Forum Manager (P1)
    // ========================================
    ForumManager: {
        currentView: 'posts', // posts | reports
        currentPage: 1,
        searchQuery: '',
        statusFilter: 'all',

        render() {
            const container = document.getElementById('admin-subpage-content');
            if (!container) return;

            container.innerHTML = `
                <div class="bg-surface rounded-2xl border border-borderSubtle p-5">
                    <!-- View Toggle -->
                    <div class="flex gap-2 mb-4">
                        <button data-click="AdminPanel.ForumManager.switchView" data-click-arg="posts" id="forum-view-posts"
                                class="forum-view-btn px-3 py-1.5 rounded-lg text-xs font-medium transition"data-i18n="admin.tabPosts">Posts</button>
                        <button data-click="AdminPanel.ForumManager.switchView" data-click-arg="reports" id="forum-view-reports"
                                class="forum-view-btn px-3 py-1.5 rounded-lg text-xs font-medium transition">
                            Reports <span id="forum-report-badge" class="hidden ml-1 px-1.5 py-0.5 bg-danger/20 text-danger rounded-full text-[10px]"></span>
                        </button>
                        <button data-click="AdminPanel.ForumManager.switchView" data-click-arg="dm-reports" id="forum-view-dm-reports"
                                class="forum-view-btn px-3 py-1.5 rounded-lg text-xs font-medium transition">
                            DM reports <span id="dm-report-badge" class="hidden ml-1 px-1.5 py-0.5 bg-danger/20 text-danger rounded-full text-[10px]"></span>
                        </button>
                        <button data-click="AdminPanel.ForumManager.switchView" data-click-arg="group-reports" id="forum-view-group-reports"
                                class="forum-view-btn px-3 py-1.5 rounded-lg text-xs font-medium transition">
                            Group reports <span id="group-report-badge" class="hidden ml-1 px-1.5 py-0.5 bg-danger/20 text-danger rounded-full text-[10px]"></span>
                        </button>
                    </div>
                    <div id="forum-content"></div>
                </div>
            `;
            this.switchView(this.currentView);
        },

        switchView(view) {
            this.currentView = view;
            document.querySelectorAll('.forum-view-btn').forEach((b) => {
                b.classList.remove('bg-primary/20', 'text-primary');
                b.classList.add('text-textMuted', 'hover:bg-surfaceHighlight');
            });
            const active = document.getElementById(`forum-view-${view}`);
            if (active) {
                active.classList.add('bg-primary/20', 'text-primary');
                active.classList.remove('text-textMuted', 'hover:bg-surfaceHighlight');
            }
            if (view === 'posts') this.loadPosts();
            else if (view === 'dm-reports') this.loadDmReports();
            else if (view === 'group-reports') this.loadDmReports('group');
            else this.loadReports();
        },

        // 私訊檢舉（2026-09-29）：快照是檢舉當下被檢舉那則＋前 10 則，只有管理員看得到。
        // 群組檢舉（c066）共用：kind='group' 走 /api/admin/group-reports，多顯示群名、快照每個發言人
        async loadDmReports(kind = 'dm') {
            const el = document.getElementById('forum-content');
            if (!el) return;
            const label = kind === 'group' ? 'group' : 'DM';
            el.innerHTML = `<div class="text-center text-textMuted text-sm py-8">Loading ${label} reports...</div>`;
            try {
                const data = await AppAPI.get(`/api/admin/${kind}-reports?status=pending&limit=50`);
                if (this.currentView !== `${kind}-reports`) return; // 等回應期間切走了：別蓋掉新分頁
                const reports = data.reports || [];
                const badge = document.getElementById(`${kind}-report-badge`);
                if (badge) {
                    badge.textContent = data.total || '';
                    badge.classList.toggle('hidden', !data.total);
                }
                if (reports.length === 0) {
                    el.innerHTML = `<div class="text-center text-textMuted text-sm py-8">No pending ${label} reports</div>`;
                    return;
                }
                el.innerHTML = `
                    <div class="text-xs text-textMuted mb-2">${Number(data.total)} pending ${label} reports</div>
                    <div class="space-y-3">${reports.map((r) => this._renderDmReportRow(r, kind)).join('')}</div>
                `;
            } catch (e) {
                el.innerHTML = `<div class="text-danger text-sm py-4 text-center">${SecurityUtils.escapeHTML(e.message || '')}</div>`;
            }
        },

        /**
         * 檢舉的風險分數（c065，內容檢查的模型／規則打的）：紅＝高風險、橘＝可疑、灰＝低。
         * 還沒分數（檢查服務不在）就不顯示；列表已經照分數排好了。
         */
        _riskBadge(r) {
            if (r.risk_score === null || r.risk_score === undefined) return '';
            const t = (k, fb) => (window.I18n ? window.I18n.t(k) : fb);
            const score = Number(r.risk_score);
            // 跟 core/moderation/service.py 的門檻一致：≥0.8 會被擋、≥0.5 會送審
            const level = score >= 0.8 ? 'high' : score >= 0.5 ? 'suspicious' : 'low';
            const color = { high: 'bg-danger/10 text-danger', suspicious: 'bg-amber-500/10 text-amber-600', low: 'bg-surfaceHighlight text-textMuted' }[level];
            const label = t(`admin.risk.${level}`, level);
            // 2026-10-02 前的舊資料才有類別：規則理由代碼（asks_secret…）或舊模型的詐騙類別（investment_scam…）
            const RULE_CODES = ['leaked_secret', 'asks_secret', 'doubling', 'asks_payment'];
            const key = r.risk_category
                ? `forum.moderation.${RULE_CODES.includes(r.risk_category) ? 'reason' : 'category'}.${r.risk_category}`
                : '';
            const cat = key ? t(key, '') : '';
            const catText = cat && cat !== key ? ` \u00b7 ${this._esc(cat)}` : '';
            return `<span class="text-[10px] px-1.5 py-0.5 rounded-full font-medium ${color}" title="${this._esc(t('admin.risk.hint', 'Content check score'))}">${this._esc(label)} ${score.toFixed(2)}${catText}</span>`;
        },

        _renderDmReportRow(r, kind = 'dm') {
            const reasonColors = { scam: 'text-danger', harassment: 'text-danger', spam: 'text-amber-600', other: 'text-textMuted' };
            const time = r.created_at ? new Date(r.created_at).toLocaleString() : '';
            const name = (id, username) => this._esc(username || id);
            // 群組快照有好幾個人講話：用後端給的 names 標每一則是誰；私訊只有雙方
            const speaker = (m, mine) =>
                kind === 'group'
                    ? name(m.from_user_id, r.names?.[m.from_user_id])
                    : mine
                      ? name(r.reported_user_id, r.reported_username)
                      : name(r.reporter_user_id, r.reporter_username);
            // 被檢舉的人那幾則標紅，被檢舉那一則加框
            const lines = (r.snapshot || [])
                .map((m) => {
                    const mine = m.from_user_id === r.reported_user_id;
                    const target = m.id === r.message_id;
                    const text =
                        m.message_type === 'recalled' && !m.content
                            ? '<span class="italic text-textMuted">(recalled before the report)</span>'
                            : this._esc(m.content);
                    return `<div class="px-2 py-1 rounded ${target ? 'ring-1 ring-danger/60 bg-danger/5' : ''}">
                        <span class="text-[10px] font-medium ${mine ? 'text-danger' : 'text-textMuted'}">${speaker(m, mine)}</span>
                        <span class="text-[10px] text-textMuted/70 ml-1">${m.created_at ? new Date(m.created_at).toLocaleString() : ''}</span>
                        <div class="text-xs text-secondary whitespace-pre-wrap break-words">${text}</div>
                    </div>`;
                })
                .join('');
            const args = (status) => encodeURIComponent(JSON.stringify([r.id, status, kind]));
            return `
                <div class="p-3 bg-background/50 rounded-xl border border-borderSubtle">
                    <div class="flex items-center justify-between mb-2">
                        <span class="text-[10px] px-1.5 py-0.5 bg-surfaceHighlight rounded-full ${reasonColors[r.reason] || 'text-textMuted'} font-medium">${this._esc(r.reason)}</span>
                        ${this._riskBadge(r)}
                        <span class="text-[10px] text-textMuted">${time}</span>
                    </div>
                    <div class="text-[10px] text-textMuted mb-2">${name(r.reporter_user_id, r.reporter_username)} reported ${name(r.reported_user_id, r.reported_username)}${kind === 'group' ? ` in group “${this._esc(r.group_name || `#${r.group_id}`)}”` : ''}</div>
                    ${r.note ? `<div class="text-xs text-textMuted/80 mb-2 italic">"${this._esc(r.note)}"</div>` : ''}
                    <div class="space-y-0.5 mb-3 max-h-72 overflow-y-auto border border-borderSubtle rounded-lg p-1">${lines}</div>
                    <div class="flex gap-2">
                        <button data-click="AdminPanel.ForumManager.resolveDmReport" data-click-args="${args('resolved')}"
                                class="px-3 py-1 bg-danger/20 hover:bg-danger/30 text-danger rounded-lg text-xs transition">Resolved (action taken)</button>
                        <button data-click="AdminPanel.ForumManager.resolveDmReport" data-click-args="${args('dismissed')}"
                                class="px-3 py-1 bg-surfaceHighlight text-textMuted rounded-lg text-xs transition">Dismiss</button>
                    </div>
                </div>
            `;
        },

        async resolveDmReport(reportId, status, kind = 'dm') {
            const k = kind === 'group' ? 'group' : 'dm'; // 只認這兩種，網址不吃外部字串
            try {
                await AppAPI.post(`/api/admin/${k}-reports/${Number(reportId)}/resolve`, { status });
                if (typeof showToast === 'function') showToast(`${k === 'group' ? 'Group' : 'DM'} report ${status}`, 'success');
                this.loadDmReports(k);
            } catch (e) {
                if (typeof showToast === 'function') showToast(e.message, 'error');
            }
        },

        async loadPosts() {
            const el = document.getElementById('forum-content');
            if (!el) return;

            el.innerHTML = `
                <div class="flex gap-3 mb-4">
                    <div class="flex-1 relative">
                        <input id="forum-search" type="text" placeholder="Search posts..." data-i18n="admin.postSearchPlaceholder" data-i18n-attr="placeholder"
                               value="${this._esc(this.searchQuery)}"
                               class="w-full bg-background border border-borderLight rounded-xl pl-3 pr-3 py-2 text-sm text-secondary placeholder:text-textMuted/50 focus:border-primary/50 focus:outline-none">
                    </div>
                    <select id="forum-status-filter" data-change-action="adminForumStatus"
                            class="bg-background border border-borderLight rounded-xl px-3 py-2 text-sm text-secondary">
                        <option value="all" ${this.statusFilter === 'all' ? 'selected' : ''} data-i18n="admin.filterAll">All</option>
                        <option value="hidden" ${this.statusFilter === 'hidden' ? 'selected' : ''} data-i18n="admin.filterHidden">Hidden</option>
                        <option value="pinned" ${this.statusFilter === 'pinned' ? 'selected' : ''} data-i18n="admin.filterPinned">Pinned</option>
                    </select>
                    <button data-click="AdminPanel.ForumManager.doSearch"
                            class="px-4 py-2 bg-primary/20 hover:bg-primary/30 text-primary rounded-xl text-sm font-medium transition"data-i18n="common.search">Search</button>
                </div>
                <div id="forum-posts-list"><div class="text-center text-textMuted text-sm py-8">Loading...</div></div>
            `;

            const searchInput = document.getElementById('forum-search');
            if (searchInput)
                searchInput.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') this.doSearch();
                });

            try {
                let url = `/api/admin/forum/posts?page=${this.currentPage}&limit=20&status=${this.statusFilter}`;
                if (this.searchQuery) url += `&search=${encodeURIComponent(this.searchQuery)}`;

                const data = await AppAPI.get(url);
                const posts = data.posts || [];

                // 等回應期間切到檢舉分頁了：容器已經不在，別畫（以前直接對 null 設 innerHTML 噴錯）
                const listEl = document.getElementById('forum-posts-list');
                if (!listEl || this.currentView !== 'posts') return;
                if (posts.length === 0) {
                    listEl.innerHTML =
                        '<div class="text-center text-textMuted text-sm py-8" data-i18n="admin.noPosts">No posts found</div>';
                    return;
                }

                listEl.innerHTML = `
                    <div class="text-xs text-textMuted mb-2">${data.total} posts</div>
                    <div class="space-y-2">${posts.map((p) => this._renderPostRow(p)).join('')}</div>
                `;
                AppUtils.refreshIcons();
            } catch (e) {
                const listEl = document.getElementById('forum-posts-list');
                if (listEl && this.currentView === 'posts') {
                    listEl.innerHTML = `<div class="text-danger text-sm py-4 text-center">${SecurityUtils.escapeHTML(e.message || '')}</div>`;
                }
            }
        },

        _renderPostRow(p) {
            const hiddenBadge = p.is_hidden
                ? '<span class="text-[10px] px-1.5 py-0.5 bg-danger/20 text-danger rounded-full"data-i18n="admin.statusHidden">HIDDEN</span>'
                : '';
            const pinnedBadge = p.is_pinned
                ? '<span class="text-[10px] px-1.5 py-0.5 bg-accent/20 text-accent rounded-full"data-i18n="admin.statusPinned">PINNED</span>'
                : '';
            const time = p.created_at ? new Date(p.created_at).toLocaleDateString() : '';

            return `
                <div class="flex items-center gap-3 p-3 bg-background/50 rounded-xl border border-borderSubtle">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 flex-wrap">
                            <span class="text-sm font-medium text-secondary truncate">${this._esc(p.title)}</span>
                            ${hiddenBadge}${pinnedBadge}
                        </div>
                        <div class="text-[10px] text-textMuted mt-0.5">
                            by ${this._esc(p.username)} | ${p.category || ''} | ${p.view_count} views | ${p.comment_count} comments | ${time}
                        </div>
                    </div>
                    <div class="flex gap-1 shrink-0">
                        <button data-click="AdminPanel.ForumManager.toggleVisibility" data-click-args="${encodeURIComponent(JSON.stringify([p.id, p.is_hidden]))}"
                                class="p-1.5 rounded-lg hover:bg-surfaceHighlight transition" title="${p.is_hidden ? (window.I18n ? window.I18n.t('admin.actionShow') : 'Show') : (window.I18n ? window.I18n.t('admin.actionHide') : 'Hide')}">
                            <i data-lucide="${p.is_hidden ? 'eye' : 'eye-off'}" class="w-4 h-4 ${p.is_hidden ? 'text-success' : 'text-danger'}"></i>
                        </button>
                        <button data-click="AdminPanel.ForumManager.togglePin" data-click-args="${encodeURIComponent(JSON.stringify([p.id, p.is_pinned]))}"
                                class="p-1.5 rounded-lg hover:bg-surfaceHighlight transition" title="${p.is_pinned ? (window.I18n ? window.I18n.t('admin.actionUnpin') : 'Unpin') : (window.I18n ? window.I18n.t('admin.actionPin') : 'Pin')}">
                            <i data-lucide="pin" class="w-4 h-4 ${p.is_pinned ? 'text-accent' : 'text-textMuted'}"></i>
                        </button>
                    </div>
                </div>
            `;
        },

        doSearch() {
            this.searchQuery = document.getElementById('forum-search')?.value?.trim() || '';
            this.currentPage = 1;
            this.loadPosts();
        },

        filterByStatus(status) {
            this.statusFilter = status;
            this.currentPage = 1;
            this.loadPosts();
        },

        async toggleVisibility(postId, currentlyHidden) {
            try {
                await AppAPI.patch(`/api/admin/forum/posts/${postId}/visibility`, { is_hidden: !currentlyHidden });
                if (typeof showToast === 'function')
                    showToast(currentlyHidden ? 'Post shown' : 'Post hidden', 'success');
                this.loadPosts();
            } catch (e) {
                if (typeof showToast === 'function') showToast(e.message, 'error');
            }
        },

        async togglePin(postId, currentlyPinned) {
            try {
                await AppAPI.patch(`/api/admin/forum/posts/${postId}/pin`, { is_pinned: !currentlyPinned });
                if (typeof showToast === 'function')
                    showToast(currentlyPinned ? 'Post unpinned' : 'Post pinned', 'success');
                this.loadPosts();
            } catch (e) {
                if (typeof showToast === 'function') showToast(e.message, 'error');
            }
        },

        async loadReports() {
            const el = document.getElementById('forum-content');
            if (!el) return;

            el.innerHTML =
                '<div class="text-center text-textMuted text-sm py-8">Loading reports...</div>';

            try {
                const data = await AppAPI.get('/api/admin/forum/reports?status=pending&limit=50');
                if (this.currentView !== 'reports') return; // 等回應期間切走了：別蓋掉新分頁
                const reports = data.reports || [];

                // Update badge
                const badge = document.getElementById('forum-report-badge');
                if (badge && data.total > 0) {
                    badge.textContent = data.total;
                    badge.classList.remove('hidden');
                }

                if (reports.length === 0) {
                    el.innerHTML =
                        '<div class="text-center text-textMuted text-sm py-8" data-i18n="admin.noPendingReports">No pending reports</div>';
                    return;
                }

                el.innerHTML = `
                    <div class="text-xs text-textMuted mb-2">${data.total} pending reports</div>
                    <div class="space-y-3">${reports.map((r) => this._renderReportRow(r)).join('')}</div>
                `;
                AppUtils.refreshIcons();
            } catch (e) {
                el.innerHTML = `<div class="text-danger text-sm py-4 text-center">${SecurityUtils.escapeHTML(e.message || '')}</div>`;
            }
        },

        _renderReportRow(r) {
            const typeColors = {
                spam: 'text-amber-600',
                harassment: 'text-danger',
                misinformation: 'text-accent',
                scam: 'text-danger',
                other: 'text-textMuted',
            };
            const color = typeColors[r.report_type] || 'text-textMuted';
            const time = r.created_at ? new Date(r.created_at).toLocaleDateString() : '';

            return `
                <div class="p-3 bg-background/50 rounded-xl border border-borderSubtle">
                    <div class="flex items-center justify-between mb-2">
                        <div class="flex items-center gap-2">
                            <span class="text-[10px] px-1.5 py-0.5 bg-surfaceHighlight rounded-full ${color} font-medium">${r.report_type}</span>
                            <span class="text-[10px] text-textMuted">${r.content_type} #${r.content_id}</span>
                            ${this._riskBadge(r)}
                        </div>
                        <span class="text-[10px] text-textMuted">${time}</span>
                    </div>
                    ${r.content_preview ? `<div class="text-xs text-secondary mb-1 line-clamp-1">"${this._esc(r.content_preview)}"</div>` : ''}
                    <div class="text-[10px] text-textMuted mb-2">Reported by ${this._esc(r.reporter_username)} | Votes: ${r.approve_count} approve / ${r.reject_count} reject</div>
                    ${r.description ? `<div class="text-[10px] text-textMuted/80 mb-2 italic">${this._esc(r.description)}</div>` : ''}
                    <div class="flex gap-2">
                        <button data-click="AdminPanel.ForumManager.resolveReport" data-click-args="${encodeURIComponent(JSON.stringify([r.id, 'approved']))}"
                                class="px-3 py-1 bg-danger/20 hover:bg-danger/30 text-danger rounded-lg text-xs transition">
                            Approve (Hide Content)
                        </button>
                        <button data-click="AdminPanel.ForumManager.resolveReport" data-click-args="${encodeURIComponent(JSON.stringify([r.id, 'rejected']))}"
                                class="px-3 py-1 bg-surfaceHighlight hover:bg-surfaceHighlight text-textMuted rounded-lg text-xs transition">
                            Reject
                        </button>
                    </div>
                </div>
            `;
        },

        async resolveReport(reportId, decision) {
            try {
                const body = { decision };
                if (decision === 'approved') body.violation_level = 'mild';

                await AppAPI.post(`/api/admin/forum/reports/${reportId}/resolve`, body);
                if (typeof showToast === 'function') showToast(window.I18n ? window.I18n.t(decision === 'approved' ? 'admin.reportApproved' : 'admin.reportRejected') : `Report ${decision}`, 'success');
                this.loadReports();
            } catch (e) {
                if (typeof showToast === 'function') showToast(e.message, 'error');
            }
        },

        _esc(str) {
            if (!str) return '';
            return str
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
        },
    },

    // ========================================
    // Config Manager (P1)
    // ========================================
    ConfigManager: {
        configs: {},
        editingKey: null,

        render() {
            const container = document.getElementById('admin-subpage-content');
            if (!container) return;

            container.innerHTML = `
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    <div class="lg:col-span-2">
                        <div id="config-groups">
                            <div class="text-center text-textMuted text-sm py-8">Loading configs...</div>
                        </div>
                    </div>
                    <div>
                        <div class="bg-surface rounded-2xl border border-borderSubtle p-5">
                            <h3 class="font-bold text-secondary mb-3 flex items-center gap-2 text-sm">
                                <i data-lucide="history" class="w-4 h-4"></i> Recent Changes
                            </h3>
                            <div id="config-audit-log" class="space-y-2">Loading...</div>
                        </div>
                    </div>
                </div>
            `;
            AppUtils.refreshIcons();
            this.loadConfigs();
            this.loadAuditLog();
        },

        async loadConfigs() {
            const el = document.getElementById('config-groups');
            if (!el) return;

            try {
                const data = await AppAPI.get('/api/admin/config/all');
                this.configs = data.configs_by_category || {};

                // 功能開關排第一：以前落在最後一張、標題是原始代碼 features，找不到群組開關
                const categoryLabels = {
                    features: { icon: 'toggle-right', label: 'Features' },
                    pricing: { icon: 'coins', label: 'Pricing' },
                    limits: { icon: 'gauge', label: 'Limits' },
                    general: { icon: 'settings', label: 'General' },
                    scam_tracker: { icon: 'shield-alert', label: 'Scam Tracker' },
                };

                const order = ['features', 'pricing', 'limits', 'general', 'scam_tracker'];
                const categories = [
                    ...order.filter((k) => this.configs[k]),
                    ...Object.keys(this.configs).filter((k) => !order.includes(k)),
                ];

                el.innerHTML = categories
                    .map((cat) => {
                        const info = categoryLabels[cat] || { icon: 'folder', label: cat };
                        const items = this.configs[cat] || [];
                        return `
                        <div class="bg-surface rounded-2xl border border-borderSubtle p-5 mb-4">
                            <h3 class="font-bold text-secondary mb-3 flex items-center gap-2 text-sm">
                                <i data-lucide="${info.icon}" class="w-4 h-4"></i> ${info.label}
                                <span class="text-[10px] text-textMuted font-normal">(${items.length})</span>
                            </h3>
                            <div class="space-y-2">
                                ${items.map((cfg) => this._renderConfigRow(cfg)).join('')}
                            </div>
                        </div>
                    `;
                    })
                    .join('');

                AppUtils.refreshIcons();
            } catch (e) {
                el.innerHTML = `<div class="text-danger text-sm py-4 text-center">${SecurityUtils.escapeHTML(e.message || '')}</div>`;
            }
        },

        _renderConfigRow(cfg) {
            const isEditing = this.editingKey === cfg.key;
            const val = cfg.value !== null && cfg.value !== undefined ? String(cfg.value) : '';
            const desc = cfg.description || '';
            const typeBadge = `<span class="text-[9px] px-1 py-0.5 bg-surfaceHighlight rounded text-textMuted/60">${cfg.value_type || 'string'}</span>`;

            if (isEditing) {
                return `
                    <div class="p-3 bg-primary/5 rounded-xl border border-primary/20">
                        <div class="flex items-center gap-2 mb-2">
                            <span class="text-xs font-medium text-secondary">${this._esc(cfg.key)}</span>
                            ${typeBadge}
                        </div>
                        <div class="flex gap-2">
                            <input id="config-edit-input" type="text" value="${this._esc(val)}"
                                   class="flex-1 bg-background border border-borderLight rounded-lg px-3 py-1.5 text-sm text-secondary focus:border-primary/50 focus:outline-none">
                            <button data-click="AdminPanel.ConfigManager.saveConfig" data-click-arg="${encodeURIComponent(cfg.key)}"
                                    class="px-3 py-1.5 bg-primary hover:bg-primary/80 text-background rounded-lg text-xs font-medium transition"data-i18n="common.save">Save</button>
                            <button data-click="AdminPanel.ConfigManager.cancelEdit"
                                    class="px-3 py-1.5 bg-surfaceHighlight hover:bg-surfaceHighlight text-textMuted rounded-lg text-xs transition"data-i18n="common.cancel">Cancel</button>
                        </div>
                        ${desc ? `<div class="text-[10px] text-textMuted mt-1">${this._esc(desc)}</div>` : ''}
                    </div>
                `;
            }

            return `
                <div class="flex items-center gap-3 p-3 bg-background/50 rounded-xl border border-borderSubtle hover:border-borderLight transition group">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2">
                            <span class="text-xs font-medium text-secondary">${this._esc(cfg.key)}</span>
                            ${typeBadge}
                        </div>
                        <div class="text-sm text-primary font-mono mt-0.5">${this._esc(val) || '<em class="text-textMuted">null</em>'}</div>
                        ${desc ? `<div class="text-[10px] text-textMuted mt-0.5">${this._esc(desc)}</div>` : ''}
                    </div>
                    <button data-click="AdminPanel.ConfigManager.startEdit" data-click-arg="${encodeURIComponent(cfg.key)}"
                            class="px-2 py-1 bg-surfaceHighlight hover:bg-surfaceHighlight rounded-lg text-xs text-textMuted transition shrink-0">
                        Edit
                    </button>
                </div>
            `;
        },

        startEdit(key) {
            this.editingKey = key;
            this.loadConfigs(); // Re-render with edit mode
        },

        cancelEdit() {
            this.editingKey = null;
            this.loadConfigs();
        },

        async saveConfig(key) {
            const input = document.getElementById('config-edit-input');
            if (!input) return;

            try {
                await AppAPI.put(`/api/admin/config/${encodeURIComponent(key)}`, { value: input.value });
                if (typeof showToast === 'function')
                    showToast(window.I18n ? window.I18n.t('admin.configUpdated', { key: key }) : `Config "${key}" updated`, 'success');
                this.editingKey = null;
                this.loadConfigs();
                this.loadAuditLog();
            } catch (e) {
                if (typeof showToast === 'function') showToast(e.message, 'error');
            }
        },

        async loadAuditLog() {
            const el = document.getElementById('config-audit-log');
            if (!el) return;

            try {
                const data = await AppAPI.get('/api/admin/config/audit?limit=30');
                const logs = data.logs || [];

                if (logs.length === 0) {
                    el.innerHTML =
                        '<div class="text-xs text-textMuted text-center py-4" data-i18n="admin.noChanges">No changes yet</div>';
                    return;
                }

                el.innerHTML = logs
                    .map((l) => {
                        const time = l.changed_at ? new Date(l.changed_at).toLocaleString() : '';
                        return `
                        <div class="text-[10px] p-2 bg-background/50 rounded-lg border border-borderSubtle">
                            <div class="flex justify-between mb-0.5">
                                <span class="text-secondary font-medium truncate">${this._esc(l.key)}</span>
                                <span class="text-textMuted/60 shrink-0 ml-2">${time}</span>
                            </div>
                            <div class="text-textMuted">
                                ${l.old_value ? `<span class="text-danger line-through">${this._esc(String(l.old_value).substring(0, 40))}</span> → ` : ''}
                                <span class="text-success">${this._esc(String(l.new_value || '').substring(0, 40))}</span>
                            </div>
                            <div class="text-textMuted/50 mt-0.5">by ${this._esc(l.changed_by)}</div>
                        </div>
                    `;
                    })
                    .join('');
            } catch (e) {
                el.innerHTML =
                    '<div class="text-xs text-danger text-center py-4" data-i18n="admin.loadFailed">Failed to load</div>';
            }
        },

        _esc(str) {
            if (!str) return '';
            return str
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
        },
    },
};

window.AdminPanel = AdminPanel;

export { AdminPanel };
