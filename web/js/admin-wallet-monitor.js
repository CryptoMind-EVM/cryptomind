// ========================================
// admin-wallet-monitor.js — 錢包監測總覽（跨使用者聚合）
// 資料來源：/api/admin/stats/wallet-monitor
// ========================================

const AdminWalletMonitorManager = {
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
            <!-- KPI -->
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
                <div class="bg-surface rounded-2xl border border-borderSubtle p-4">
                    <div class="text-xs text-textMuted mb-1">${this._t('walletUsersMonitoring', 'Users Monitoring')}</div>
                    <div class="text-xl font-bold text-secondary" id="wm-users">--</div>
                </div>
                <div class="bg-surface rounded-2xl border border-borderSubtle p-4">
                    <div class="text-xs text-textMuted mb-1">${this._t('walletMonitoredCount', 'Monitored Wallets')}</div>
                    <div class="text-xl font-bold text-secondary" id="wm-total">--</div>
                </div>
                <div class="bg-surface rounded-2xl border border-borderSubtle p-4">
                    <div class="text-xs text-textMuted mb-1"data-i18n="admin.scamAlertsOn">Scam Alerts On</div>
                    <div class="text-xl font-bold text-secondary" id="wm-scam">--</div>
                </div>
                <div class="bg-surface rounded-2xl border border-borderSubtle p-4">
                    <div class="text-xs text-textMuted mb-1"data-i18n="admin.largeOutflowAlerts">Large Outflow Alerts</div>
                    <div class="text-xl font-bold text-secondary" id="wm-large">--</div>
                </div>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <!-- Chain Distribution -->
                <div class="bg-surface rounded-2xl border border-borderSubtle p-5">
                    <h3 class="font-bold text-secondary mb-3 text-sm flex items-center gap-2">
                        <i data-lucide="link-2" class="w-4 h-4"></i> ${this._t('walletChainDist', 'Chain Distribution')}
                    </h3>
                    <div id="wm-chains" class="space-y-2">
                        <div class="text-center text-textMuted py-6 text-sm">${this._t('loading', 'Loading...')}</div>
                    </div>
                </div>

                <!-- Alert Adoption -->
                <div class="bg-surface rounded-2xl border border-borderSubtle p-5">
                    <h3 class="font-bold text-secondary mb-3 text-sm flex items-center gap-2">
                        <i data-lucide="bell" class="w-4 h-4"></i> Alert Adoption
                    </h3>
                    <div id="wm-alerts" class="space-y-2">
                        <div class="text-center text-textMuted py-6 text-sm">${this._t('loading', 'Loading...')}</div>
                    </div>
                </div>
            </div>

            <div class="mt-4 text-xs text-textMuted italic">
                ⓘ Alert-triggered count requires a new <code class="font-mono">wallet_alert_events</code> table (follow-up).
            </div>
        `;

        AppUtils.refreshIcons();
        this.load();
    },

    async load() {
        try {
            const data = await AppAPI.get('/api/admin/stats/wallet-monitor');

            const setText = (id, v) => {
                const el = document.getElementById(id);
                if (el) el.textContent = v;
            };
            setText('wm-users', data.users_monitoring ?? 0);
            setText('wm-total', data.total_monitored_wallets ?? 0);
            const adoption = data.alert_adoption || {};
            setText('wm-scam', adoption.scam ?? 0);
            setText('wm-large', adoption.large_out ?? 0);

            // Chain distribution
            const chainsEl = document.getElementById('wm-chains');
            const chains = data.chain_distribution || [];
            if (!chains.length) {
                chainsEl.innerHTML = `<div class="text-center text-textMuted py-6 text-sm">${this._t('noData', 'No data')}</div>`;
            } else {
                const max = chains[0].count || 1;
                const chainNames = { ton: 'TON', eth: 'Ethereum', bsc: 'BNB Chain', polygon: 'Polygon', arbitrum: 'Arbitrum' };
                chainsEl.innerHTML = chains
                    .map(
                        (ch) => `
                        <div class="flex items-center gap-3 py-1.5">
                            <span class="text-sm text-secondary w-24">${chainNames[ch.chain] || this._esc(ch.chain)}</span>
                            <div class="flex-1 h-2 bg-surfaceHighlight rounded-full overflow-hidden">
                                <div class="h-full bg-primary/60 rounded-full" style="width: ${(ch.count / max) * 100}%"></div>
                            </div>
                            <span class="text-xs text-textMuted w-8 text-right">${ch.count}</span>
                        </div>
                    `
                    )
                    .join('');
            }

            // Alert adoption
            const alertsEl = document.getElementById('wm-alerts');
            const alertTypes = [
                { key: 'incoming', label: 'Incoming transfers', icon: 'arrow-down-circle', color: 'text-success' },
                { key: 'outgoing', label: 'Outgoing transfers', icon: 'arrow-up-circle', color: 'text-amber-600' },
                { key: 'scam', label: 'Scam detection', icon: 'shield-alert', color: 'text-danger' },
                { key: 'large_out', label: 'Large outflow', icon: 'alert-triangle', color: 'text-amber-600' },
            ];
            alertsEl.innerHTML = alertTypes
                .map(
                    (a) => `
                    <div class="flex items-center gap-2 py-1.5 px-2 rounded-lg hover:bg-surfaceHighlight/30">
                        <i data-lucide="${a.icon}" class="w-4 h-4 ${a.color}"></i>
                        <span class="text-sm text-secondary flex-1">${a.label}</span>
                        <span class="text-sm font-medium text-secondary">${adoption[a.key] ?? 0}</span>
                        <span class="text-xs text-textMuted">users</span>
                    </div>
                `
                )
                .join('');
            AppUtils.refreshIcons();
        } catch (e) {
            console.warn('Failed to load wallet monitor:', e);
        }
    },
};

window.AdminWalletMonitorManager = AdminWalletMonitorManager;
