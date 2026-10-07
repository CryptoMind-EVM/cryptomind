// ========================================
// admin-visitors.js - Chat Visitors & Wallet Connection Tracking
// 資料來源：audit_logs（chat_page_visited / wallet_connected）
// 後台 DANNY 用：看誰進站、誰真的連接錢包。
// ========================================

const AdminVisitorsManager = {
    charts: {},
    currentRange: 30,

    _t(key, fb) {
        return window.I18n ? (window.I18n.t('admin.' + key) || fb) : fb;
    },

    /**
     * SVG sparkline — mini 走勢線（鏡射 admin-stats.js，避免跨檔依賴）。
     */
    _sparkline(points, opts) {
        opts = opts || {};
        const w = opts.width || 80;
        const h = opts.height || 24;
        const stroke = opts.color || 'rgba(37, 99, 235, 0.9)';
        const fill = opts.fill || 'rgba(37, 99, 235, 0.12)';
        if (!points || points.length < 2) {
            return `<svg width="${w}" height="${h}" class="inline-block opacity-30"><text x="2" y="${h - 4}" font-size="9" fill="#888">—</text></svg>`;
        }
        const max = Math.max(...points, 1);
        const min = Math.min(...points, 0);
        const range = max - min || 1;
        const stepX = w / (points.length - 1);
        const coords = points.map((p, i) => {
            const x = i * stepX;
            const y = h - ((p - min) / range) * (h - 4) - 2;
            return `${x.toFixed(1)},${y.toFixed(1)}`;
        });
        const linePath = `M${coords.join(' L')}`;
        const areaPath = `${linePath} L${w},${h} L0,${h} Z`;
        const lastIdx = points.length - 1;
        const lastX = (lastIdx * stepX).toFixed(1);
        const lastY = (h - ((points[lastIdx] - min) / range) * (h - 4) - 2).toFixed(1);
        const trendUp = points[lastIdx] >= points[0];
        const dotColor = trendUp ? 'rgba(100, 180, 100, 0.9)' : 'rgba(220, 100, 100, 0.9)';
        return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" class="inline-block align-middle">
            <path d="${areaPath}" fill="${fill}" stroke="none"/>
            <path d="${linePath}" fill="none" stroke="${stroke}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            <circle cx="${lastX}" cy="${lastY}" r="2" fill="${dotColor}"/>
        </svg>`;
    },

    _injectSparkline(container, label, points, opts) {
        if (!container) return;
        const cards = container.querySelectorAll('.bg-surface.rounded-2xl');
        let target = null;
        cards.forEach((c) => {
            const labelEl = c.querySelector('.text-xs.text-textMuted');
            if (labelEl && labelEl.textContent.trim() === label) target = c;
        });
        if (!target) return;
        const existing = target.querySelector('.kpi-sparkline');
        if (existing) existing.remove();
        const wrap = document.createElement('div');
        wrap.className = 'kpi-sparkline mt-1';
        wrap.innerHTML = this._sparkline(points, opts);
        target.appendChild(wrap);
    },

    render() {
        const container = document.getElementById('admin-subpage-content');
        if (!container) return;

        container.innerHTML = `
            <!-- Time Range Selector -->
            <div class="flex gap-2 mb-5">
                <button data-click="AdminVisitorsManager.changeRange" data-click-args="%5B7%5D" id="visitors-range-7"
                        class="visitors-range-btn px-3 py-1.5 rounded-lg text-xs font-medium transition">${this._t('range7', '7 Days')}</button>
                <button data-click="AdminVisitorsManager.changeRange" data-click-args="%5B30%5D" id="visitors-range-30"
                        class="visitors-range-btn px-3 py-1.5 rounded-lg text-xs font-medium transition">${this._t('range30', '30 Days')}</button>
                <button data-click="AdminVisitorsManager.changeRange" data-click-args="%5B90%5D" id="visitors-range-90"
                        class="visitors-range-btn px-3 py-1.5 rounded-lg text-xs font-medium transition">${this._t('range90', '90 Days')}</button>
            </div>

            <!-- KPI Cards -->
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6" id="visitors-summary-cards">
                ${this._renderCardSkeleton(this._t('kpiChatVisitorsToday', 'Chat Visitors Today'), 'eye')}
                ${this._renderCardSkeleton(this._t('kpiWalletConnectsToday', 'Wallet Connects Today'), 'wallet')}
                ${this._renderCardSkeleton(this._t('kpiWalletUsers', 'Total Wallet Users'), 'link-2')}
                ${this._renderCardSkeleton(this._t('kpiWalletVisitorRatio', 'Wallet Visitor % (7d)'), 'percent')}
            </div>

            <!-- Conversion Funnel (PR-5): fixed 7d / 30d windows, independent of the range buttons -->
            <div class="bg-surface rounded-2xl border border-borderSubtle p-5 mb-6">
                <h3 class="font-bold text-secondary text-sm flex items-center gap-2 mb-3">
                    <i data-lucide="filter" class="w-4 h-4"></i> ${this._t('funnelTitle', 'Conversion Funnel')}
                </h3>
                <div class="overflow-x-auto">
                    <table class="w-full text-sm">
                        <thead>
                            <tr class="text-left text-textMuted border-b border-borderSubtle">
                                <th class="py-2 px-2 font-medium">${this._t('funnelStep', 'Step')}</th>
                                <th class="py-2 px-2 font-medium text-right">${this._t('range7', '7 Days')}</th>
                                <th class="py-2 px-2 font-medium text-right">${this._t('range30', '30 Days')}</th>
                            </tr>
                        </thead>
                        <tbody id="visitors-funnel-body">
                            <tr><td colspan="3" class="text-center text-textMuted py-6">${this._t('loading', 'Loading...')}</td></tr>
                        </tbody>
                    </table>
                </div>
                <p class="text-[10px] text-textMuted mt-2">${this._t('funnelNote', 'Guest steps use guests first seen in the window as the base; account steps use accounts created in the window.')}</p>
            </div>

            <!-- Trend Chart -->
            <div class="space-y-4">
                <div class="bg-surface rounded-2xl border border-borderSubtle p-4">
                    <h3 class="font-bold text-secondary mb-3 text-sm flex items-center gap-2">
                        <i data-lucide="trending-up" class="w-4 h-4"></i> ${this._t('chartVisitors', 'Chat Visitors & Wallet Connects')}
                    </h3>
                    <div style="height: 180px; position: relative;">
                        <canvas id="chart-visitors"></canvas>
                    </div>
                </div>

                <!-- Visitors Table -->
                <div class="bg-surface rounded-2xl border border-borderSubtle p-5">
                    <div class="flex items-center justify-between mb-3">
                        <h3 class="font-bold text-secondary text-sm flex items-center gap-2">
                            <i data-lucide="users" class="w-4 h-4"></i> ${this._t('recentVisitors', 'Recent Visitors')}
                        </h3>
                        <span class="text-xs text-textMuted" id="visitors-table-meta"></span>
                    </div>
                    <div class="overflow-x-auto">
                        <table class="w-full text-sm">
                            <thead>
                                <tr class="text-left text-textMuted border-b border-borderSubtle">
                                    <th class="py-2 px-2 font-medium">${this._t('colUser', 'User')}</th>
                                    <th class="py-2 px-2 font-medium">${this._t('colWallet', 'Wallet')}</th>
                                    <th class="py-2 px-2 font-medium">${this._t('colTier', 'Tier')}</th>
                                    <th class="py-2 px-2 font-medium">${this._t('colLastVisit', 'Last Chat Visit')}</th>
                                    <th class="py-2 px-2 font-medium">${this._t('colJoined', 'Joined')}</th>
                                </tr>
                            </thead>
                            <tbody id="visitors-table-body">
                                <tr><td colspan="5" class="text-center text-textMuted py-6">${this._t('loading', 'Loading...')}</td></tr>
                            </tbody>
                        </table>
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
        this.destroyCharts();
        this.loadVisitorChart();
        this.loadVisitorList();
    },

    _updateRangeButtons() {
        document.querySelectorAll('.visitors-range-btn').forEach((b) => {
            b.classList.remove('bg-primary/20', 'text-primary');
            b.classList.add('text-textMuted', 'hover:bg-surfaceHighlight');
        });
        const active = document.getElementById(`visitors-range-${this.currentRange}`);
        if (active) {
            active.classList.add('bg-primary/20', 'text-primary');
            active.classList.remove('text-textMuted', 'hover:bg-surfaceHighlight');
        }
    },

    _renderCardSkeleton(label, icon) {
        return `
            <div class="bg-surface rounded-2xl border border-borderSubtle p-4">
                <div class="flex items-center gap-2 mb-2">
                    <i data-lucide="${icon}" class="w-4 h-4 text-textMuted"></i>
                    <span class="text-xs text-textMuted">${label}</span>
                </div>
                <div class="text-xl font-bold text-secondary">--</div>
            </div>
        `;
    },

    async loadAll() {
        this.loadSummary();
        this.loadFunnel();
        this.loadVisitorChart();
        this.loadVisitorList();
    },

    _esc(s) {
        const text = String(s ?? '');
        if (typeof window.escapeHtml === 'function') return window.escapeHtml(text);
        return text.replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`);
    },

    /**
     * 轉換漏斗（PR-5）：訪客步驟以新訪客為基數、帳號步驟以新帳號為基數，
     * 「其中先問過問題」以登入的訪客為基數。
     */
    async loadFunnel() {
        const tbody = document.getElementById('visitors-funnel-body');
        if (!tbody) return;
        const steps = [
            { key: 'guests_first_seen', label: this._t('funnelGuestsFirstSeen', 'New guests') },
            { key: 'guests_asked_1', label: this._t('funnelGuestsAsked1', 'Asked 1+ questions'), base: 'guests_first_seen' },
            { key: 'guests_asked_2', label: this._t('funnelGuestsAsked2', 'Asked 2+ questions'), base: 'guests_first_seen' },
            { key: 'guests_converted', label: this._t('funnelGuestsConverted', 'Signed in (from guest)'), base: 'guests_first_seen' },
            { key: 'guests_converted_after_question', label: this._t('funnelConvertedAfterQuestion', 'of whom asked first'), base: 'guests_converted', sub: true },
            { key: 'new_accounts', label: this._t('funnelNewAccounts', 'New accounts'), divider: true },
            { key: 'journal_first_entry', label: this._t('funnelJournalFirst', 'First ledger entry'), base: 'new_accounts' },
            { key: 'brief_enabled', label: this._t('funnelBriefEnabled', 'Daily brief on'), base: 'new_accounts' },
            { key: 'returned_7d', label: this._t('funnelReturned7d', 'Returned within 7 days'), base: 'new_accounts' },
        ];
        try {
            const data = await AppAPI.get('/api/admin/stats/funnel');
            const windows = (data && data.windows) || [];
            const cell = (w, step) => {
                const n = Number(w && w[step.key]) || 0;
                let pct = '';
                if (step.base) {
                    const base = Number(w && w[step.base]) || 0;
                    const ratio = base ? ((n / base) * 100).toFixed(0) : '0';
                    pct = ` <span class="text-[10px] text-textMuted">${ratio}%</span>`;
                }
                return `<td class="py-2 px-2 text-right tabular-nums text-secondary whitespace-nowrap">${n}${pct}</td>`;
            };
            tbody.innerHTML = steps
                .map((step) => {
                    const rowCls = step.divider
                        ? 'border-t border-borderSubtle'
                        : 'border-b border-borderSubtle/50';
                    const labelCls = step.sub ? 'text-xs text-textMuted' : 'text-secondary';
                    const label = (step.sub ? '↳ ' : '') + this._esc(step.label);
                    return `
                    <tr class="${rowCls}">
                        <td class="py-2 px-2 ${labelCls}">${label}</td>
                        ${windows.slice(0, 2).map((w) => cell(w, step)).join('')}
                    </tr>`;
                })
                .join('');
        } catch (e) {
            console.warn('Failed to load funnel:', e);
            tbody.innerHTML = `<tr><td colspan="3" class="text-center text-red-400 py-6">${this._esc(this._t('funnelLoadFailed', 'Failed to load funnel data'))}</td></tr>`;
        }
    },

    async loadSummary() {
        try {
            const data = await AppAPI.get('/api/admin/stats/visitors/summary');
            const cards = document.getElementById('visitors-summary-cards');
            if (!cards) return;

            const ratio7d = (data.wallet_visitor_ratio_7d || 0) * 100;
            const values = [
                { label: this._t('kpiChatVisitorsToday', 'Chat Visitors Today'), value: `${data.chat_visitors_today ?? 0}`, icon: 'eye', sub: `${data.total_visitors_7d ?? 0} in 7d` },
                { label: this._t('kpiWalletConnectsToday', 'Wallet Connects Today'), value: `${data.wallet_connects_today ?? 0}`, icon: 'wallet', sub: (window.I18n ? window.I18n.t('admin.tonProofVerified') : 'ton-proof verified') },
                { label: this._t('kpiWalletUsers', 'Total Wallet Users'), value: `${data.total_wallet_users ?? 0}`, icon: 'link-2', sub: 'EVM + bound wallets + legacy TON' },
                { label: this._t('kpiWalletVisitorRatio', 'Wallet Visitor % (7d)'), value: `${ratio7d.toFixed(1)}%`, icon: 'percent', sub: `${data.wallet_visitors_7d ?? 0} / ${data.total_visitors_7d ?? 0}` },
            ];

            cards.innerHTML = values
                .map(
                    (v) => `
                <div class="bg-surface rounded-2xl border border-borderSubtle p-4 transition duration-200 hover:border-primary/30 hover:shadow-[0_4px_20px_rgba(37,99,235,0.08)]">
                    <div class="flex items-center gap-2 mb-2">
                        <span class="w-9 h-9 rounded-xl bg-primary/10 flex items-center justify-center shrink-0">
                            <i data-lucide="${v.icon}" class="w-4 h-4 text-primary"></i>
                        </span>
                        <span class="text-xs text-textMuted">${v.label}</span>
                    </div>
                    <div class="text-xl font-bold text-secondary tabular-nums tracking-tight">${v.value}</div>
                    <div class="text-[10px] text-textMuted mt-1">${v.sub}</div>
                </div>
            `
                )
                .join('');

            AppUtils.refreshIcons();
        } catch (e) {
            console.warn('Failed to load visitors summary:', e);
        }
    },

    async loadVisitorChart() {
        try {
            const data = await AppAPI.get(
                `/api/admin/stats/visitors/trend?days=${this.currentRange}`
            );

            const visitsFilled = this._fillMissingDates(data.chat_visits, this.currentRange);
            const walletFilled = this._fillMissingDates(data.wallet_connects, this.currentRange);
            const labels = visitsFilled.map((d) => d.date.substring(5)); // MM-DD

            // sparkline：兩張 KPI 卡各顯示對應趨勢
            const visitCounts = visitsFilled.map((d) => d.count);
            const walletCounts = walletFilled.map((d) => d.count);
            const cards = document.getElementById('visitors-summary-cards');
            this._injectSparkline(cards, this._t('kpiChatVisitorsToday', 'Chat Visitors Today'), visitCounts, { color: 'rgba(37, 99, 235, 0.9)' });
            this._injectSparkline(cards, this._t('kpiWalletConnectsToday', 'Wallet Connects Today'), walletCounts, { color: 'rgba(100, 180, 100, 0.9)' });

            this._createChart('chart-visitors', {
                type: 'bar',
                data: {
                    labels,
                    datasets: [
                        {
                            label: 'Chat Visitors (unique)',
                            data: visitsFilled.map((d) => d.count),
                            backgroundColor: 'rgba(37, 99, 235, 0.7)',
                            borderRadius: 4,
                            yAxisID: 'y',
                        },
                        {
                            label: (window.I18n ? window.I18n.t('admin.walletConnects') : 'Wallet Connects'),
                            type: 'line',
                            data: walletFilled.map((d) => d.count),
                            borderColor: 'rgba(100, 180, 100, 0.9)',
                            backgroundColor: 'rgba(100, 180, 100, 0.1)',
                            fill: true,
                            tension: 0.3,
                            yAxisID: 'y',
                        },
                    ],
                },
                options: this._chartOptions(),
            });
        } catch (e) {
            console.warn('Failed to load visitors chart:', e);
        }
    },

    async loadVisitorList() {
        const tbody = document.getElementById('visitors-table-body');
        const meta = document.getElementById('visitors-table-meta');
        if (!tbody) return;
        try {
            // 造訪清單看近 7 天即可（避免大窗口聚合太慢）
            const data = await AppAPI.get(
                `/api/admin/stats/visitors?days=7&limit=50`
            );
            const visitors = data.visitors || [];
            if (meta) meta.textContent = `${visitors.length} unique visitors (last 7 days)`;

            if (visitors.length === 0) {
                tbody.innerHTML =
                    '<tr><td colspan="5" class="text-center text-textMuted py-6">No chat visits recorded yet.</td></tr>';
                return;
            }

            tbody.innerHTML = visitors
                .map((v) => {
                    const walletBadge = v.has_wallet
                        ? `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-500/10 text-green-600 text-xs"><i data-lucide="check-circle" class="w-3 h-3"></i>wallet</span>`
                        : `<span class="text-textMuted text-xs">—</span>`;
                    const justConnected =
                        v.connected_wallet_in_window && v.has_wallet
                            ? ' <span class="text-[10px] text-green-600">🆕</span>'
                            : '';
                    const tier = v.membership_tier || 'free';
                    const lastVisit = v.last_chat_visit
                        ? new Date(v.last_chat_visit).toLocaleString()
                        : '—';
                    const joined = v.user_created_at
                        ? new Date(v.user_created_at).toLocaleDateString()
                        : '—';
                    const username = window.escapeHtml
                        ? window.escapeHtml(v.username || v.user_id.slice(0, 12))
                        : (v.username || v.user_id.slice(0, 12));
                    return `
                    <tr class="border-b border-borderSubtle/50 hover:bg-surfaceHighlight/30">
                        <td class="py-2 px-2">
                            <div class="font-medium text-secondary">${username}${justConnected}</div>
                            <div class="text-[10px] text-textMuted font-mono">${v.user_id.slice(0, 16)}…</div>
                        </td>
                        <td class="py-2 px-2">${walletBadge}</td>
                        <td class="py-2 px-2"><span class="text-xs px-2 py-0.5 rounded-full bg-surfaceHighlight text-secondary">${tier}</span></td>
                        <td class="py-2 px-2 text-xs text-textMuted">${lastVisit}</td>
                        <td class="py-2 px-2 text-xs text-textMuted">${joined}</td>
                    </tr>
                `;
                })
                .join('');

            AppUtils.refreshIcons();
        } catch (e) {
            console.warn('Failed to load visitors list:', e);
            tbody.innerHTML =
                '<tr><td colspan="5" class="text-center text-red-400 py-6">Failed to load visitors.</td></tr>';
        }
    },

    // ── chart helpers（鏡射 admin-stats.js，避免跨模組依賴）──

    _chartOptions(scalesOverride) {
        const base = {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                    labels: { color: '#888', boxWidth: 12, font: { size: 10 } },
                },
            },
            scales: {
                x: {
                    ticks: { color: '#666', font: { size: 9 }, maxRotation: 0 },
                    grid: { color: 'rgba(255,255,255,0.03)' },
                },
                y: {
                    beginAtZero: true,
                    ticks: { color: '#666', font: { size: 10 }, precision: 0 },
                    grid: { color: 'rgba(255,255,255,0.05)' },
                },
            },
        };
        if (scalesOverride) {
            base.scales = { x: base.scales.x, ...scalesOverride };
        }
        return base;
    },

    _createChart(canvasId, config) {
        const canvas = document.getElementById(canvasId);
        if (!canvas) return;
        if (this.charts[canvasId]) {
            this.charts[canvasId].destroy();
            delete this.charts[canvasId];
        }
        this.charts[canvasId] = new Chart(canvas, config);
    },

    destroyCharts() {
        Object.keys(this.charts).forEach((key) => {
            if (this.charts[key]) {
                this.charts[key].destroy();
                delete this.charts[key];
            }
        });
    },

    _fillMissingDates(data, days) {
        const dateMap = {};
        (data || []).forEach((d) => {
            dateMap[d.date] = d.count || 0;
        });
        const result = [];
        const now = new Date();
        for (let i = days - 1; i >= 0; i--) {
            const d = new Date(now);
            d.setDate(d.getDate() - i);
            const key = d.toISOString().split('T')[0];
            result.push({ date: key, count: dateMap[key] || 0 });
        }
        return result;
    },
};

window.AdminVisitorsManager = AdminVisitorsManager;
