// ========================================
// admin-audit.js — 稽核日誌總覽（可疑活動 / 熱門動作 / 總覽）
// 資料來源：/api/admin/audit/{stats,suspicious}
// ========================================

const AdminAuditManager = {
    currentRange: 30,

    _t(key, fb) {
        return window.I18n ? (window.I18n.t('admin.' + key) || fb) : fb;
    },

    _esc(s) {
        if (s === null || s === undefined) return '';
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    },

    render() {
        const container = document.getElementById('admin-subpage-content');
        if (!container) return;

        container.innerHTML = `
            <!-- Time Range -->
            <div class="flex gap-2 mb-5">
                <button data-click="AdminAuditManager.changeRange" data-click-args="%5B7%5D" id="audit-range-7"
                        class="audit-range-btn px-3 py-1.5 rounded-lg text-xs font-medium transition">${this._t('range7', '7 Days')}</button>
                <button data-click="AdminAuditManager.changeRange" data-click-args="%5B30%5D" id="audit-range-30"
                        class="audit-range-btn px-3 py-1.5 rounded-lg text-xs font-medium transition">${this._t('range30', '30 Days')}</button>
                <button data-click="AdminAuditManager.changeRange" data-click-args="%5B90%5D" id="audit-range-90"
                        class="audit-range-btn px-3 py-1.5 rounded-lg text-xs font-medium transition">${this._t('range90', '90 Days')}</button>
            </div>

            <!-- Overview KPI -->
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6" id="audit-overview-cards">
                <div class="bg-surface rounded-2xl border border-borderSubtle p-4">
                    <div class="text-xs text-textMuted mb-1">${this._t('auditOverall', 'Total Requests')}</div>
                    <div class="text-xl font-bold text-secondary" id="audit-total">--</div>
                </div>
                <div class="bg-surface rounded-2xl border border-borderSubtle p-4">
                    <div class="text-xs text-textMuted mb-1"data-i18n="admin.kpiFailed">Failed</div>
                    <div class="text-xl font-bold text-danger" id="audit-failed">--</div>
                </div>
                <div class="bg-surface rounded-2xl border border-borderSubtle p-4">
                    <div class="text-xs text-textMuted mb-1"data-i18n="admin.kpiUniqueUsers">Unique Users</div>
                    <div class="text-xl font-bold text-secondary" id="audit-users">--</div>
                </div>
                <div class="bg-surface rounded-2xl border border-borderSubtle p-4">
                    <div class="text-xs text-textMuted mb-1"data-i18n="admin.kpiUniqueIPs">Unique IPs</div>
                    <div class="text-xl font-bold text-secondary" id="audit-ips">--</div>
                </div>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <!-- Top Actions -->
                <div class="bg-surface rounded-2xl border border-borderSubtle p-5">
                    <h3 class="font-bold text-secondary mb-3 text-sm flex items-center gap-2">
                        <i data-lucide="activity" class="w-4 h-4"></i> ${this._t('auditTopActions', 'Top Actions')}
                    </h3>
                    <div id="audit-top-actions" class="space-y-1 max-h-72 overflow-y-auto">
                        <div class="text-center text-textMuted py-6 text-sm">${this._t('loading', 'Loading...')}</div>
                    </div>
                </div>

                <!-- Suspicious -->
                <div class="bg-surface rounded-2xl border border-borderSubtle p-5">
                    <h3 class="font-bold text-secondary mb-3 text-sm flex items-center gap-2">
                        <i data-lucide="shield-alert" class="w-4 h-4"></i> ${this._t('auditSuspicious', 'Suspicious Activity')}
                    </h3>
                    <div id="audit-suspicious" class="space-y-2 max-h-72 overflow-y-auto">
                        <div class="text-center text-textMuted py-6 text-sm">${this._t('loading', 'Loading...')}</div>
                    </div>
                </div>
            </div>
        `;

        AppUtils.refreshIcons();
        this._updateRangeButtons();
        this.loadAll();
    },

    changeRange(days) {
        this.currentRange = days;
        this._updateRangeButtons();
        this.loadAll();
    },

    _updateRangeButtons() {
        document.querySelectorAll('.audit-range-btn').forEach((b) => {
            b.classList.remove('bg-primary/20', 'text-primary');
            b.classList.add('text-textMuted', 'hover:bg-surfaceHighlight');
        });
        const active = document.getElementById(`audit-range-${this.currentRange}`);
        if (active) {
            active.classList.add('bg-primary/20', 'text-primary');
            active.classList.remove('text-textMuted', 'hover:bg-surfaceHighlight');
        }
    },

    async loadAll() {
        this.loadStats();
        this.loadSuspicious();
    },

    async loadStats() {
        try {
            const data = await AppAPI.get(`/api/admin/audit/stats?days=${this.currentRange}`);
            const o = data.overall || {};
            const setText = (id, v) => {
                const el = document.getElementById(id);
                if (el) el.textContent = v;
            };
            setText('audit-total', o.total_requests ?? 0);
            setText('audit-failed', o.failed_requests ?? 0);
            setText('audit-users', o.unique_users ?? 0);
            setText('audit-ips', o.unique_ips ?? 0);

            // Top actions
            const taEl = document.getElementById('audit-top-actions');
            const actions = data.top_actions || [];
            if (!actions.length) {
                taEl.innerHTML = `<div class="text-center text-textMuted py-6 text-sm">${this._t('noData', 'No data')}</div>`;
            } else {
                const max = actions[0].count || 1;
                taEl.innerHTML = actions
                    .map(
                        (a) => `
                        <div class="flex items-center gap-2 py-1.5 px-2 rounded-lg hover:bg-surfaceHighlight/30">
                            <span class="text-xs text-secondary font-mono flex-1 truncate">${this._esc(a.action)}</span>
                            <div class="w-24 h-1.5 bg-surfaceHighlight rounded-full overflow-hidden">
                                <div class="h-full bg-primary/60 rounded-full" style="width: ${(a.count / max) * 100}%"></div>
                            </div>
                            <span class="text-xs text-textMuted w-10 text-right">${a.count}</span>
                        </div>
                    `
                    )
                    .join('');
            }
        } catch (e) {
            console.warn('Failed to load audit stats:', e);
        }
    },

    async loadSuspicious() {
        try {
            const data = await AppAPI.get(`/api/admin/audit/suspicious?days=${this.currentRange}`);
            const el = document.getElementById('audit-suspicious');
            const logins = data.failed_logins || [];
            const payments = data.failed_payments || [];
            const alertCount = data.alert_count || 0;

            if (!logins.length && !payments.length) {
                el.innerHTML = `
                    <div class="text-center py-6">
                        <i data-lucide="check-circle" class="w-8 h-8 text-success mx-auto mb-2"></i>
                        <div class="text-sm text-textMuted">No suspicious activity in ${this.currentRange} days</div>
                    </div>`;
                AppUtils.refreshIcons();
                return;
            }

            let html = '';
            if (logins.length) {
                html += `<div class="text-xs text-textMuted mb-1" data-i18n="admin.failedLogins">Failed logins</div>`;
                html += logins
                    .map(
                        (l) => `
                        <div class="flex items-center gap-2 text-xs py-1.5 px-2 rounded-lg bg-red-500/5">
                            <i data-lucide="x-circle" class="w-3 h-3 text-danger"></i>
                            <span class="text-secondary flex-1 truncate">${this._esc(l.username || l.user_id)}</span>
                            <span class="text-textMuted font-mono">${this._esc(l.ip_address || '')}</span>
                            <span class="text-danger font-medium">${l.failure_count}×</span>
                        </div>`
                    )
                    .join('');
            }
            if (payments.length) {
                html += `<div class="text-xs text-textMuted mt-3 mb-1" data-i18n="admin.failedPayments">Failed payments</div>`;
                html += payments
                    .map(
                        (p) => `
                        <div class="flex items-center gap-2 text-xs py-1.5 px-2 rounded-lg bg-amber-500/5">
                            <i data-lucide="credit-card" class="w-3 h-3 text-amber-600"></i>
                            <span class="text-secondary flex-1 truncate">${this._esc(p.username || p.user_id)}</span>
                            <span class="text-amber-600 font-medium">${p.failure_count}×</span>
                        </div>`
                    )
                    .join('');
            }
            el.innerHTML = html;
            AppUtils.refreshIcons();
        } catch (e) {
            console.warn('Failed to load suspicious:', e);
        }
    },
};

window.AdminAuditManager = AdminAuditManager;
