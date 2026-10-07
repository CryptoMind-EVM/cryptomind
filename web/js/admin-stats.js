// ========================================
// admin-stats.js - Statistics Dashboard (P2)
// ========================================

const AdminStatsManager = {
    charts: {},
    currentRange: 30,
    // 快取趨勢資料供 sparkline 用（loadXxxChart 抓回來時順手存）
    trendCache: { users: [], forum: [], revenue: [], visitors: [], wallet: [] },

    // i18n helper（與 admin.js inline ternary 同邏輯，省冗長）
    _t(key, fb) {
        return window.I18n ? (window.I18n.t('admin.' + key) || fb) : fb;
    },

    /**
     * SVG sparkline — 在 KPI card 內嵌 mini 走勢線，不靠 Chart.js（輕量、無 canvas）。
     * points: number[]（每日值，舊→新）。回傳 <svg> 字串。
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

    /**
     * 把 sparkline 注入指定的 KPI card（card 由 label 找）。
     * container: cards 容器 element；label: 卡片標題；points: 趨勢值。
     */
    _injectSparkline(container, label, points, opts) {
        if (!container) return;
        // 找到對應 label 的 card（text-xs span 文字相符）
        const cards = container.querySelectorAll('.bg-surface.rounded-2xl');
        let target = null;
        cards.forEach((c) => {
            const labelEl = c.querySelector('.text-xs.text-textMuted');
            if (labelEl && labelEl.textContent.trim() === label) target = c;
        });
        if (!target) return;
        // 避免重複注入
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
                <button data-click="AdminStatsManager.changeRange" data-click-args="%5B7%5D" id="stats-range-7"
                        class="stats-range-btn px-3 py-1.5 rounded-lg text-xs font-medium transition">${this._t('range7', '7 Days')}</button>
                <button data-click="AdminStatsManager.changeRange" data-click-args="%5B30%5D" id="stats-range-30"
                        class="stats-range-btn px-3 py-1.5 rounded-lg text-xs font-medium transition">${this._t('range30', '30 Days')}</button>
                <button data-click="AdminStatsManager.changeRange" data-click-args="%5B90%5D" id="stats-range-90"
                        class="stats-range-btn px-3 py-1.5 rounded-lg text-xs font-medium transition">${this._t('range90', '90 Days')}</button>
            </div>

            <!-- Overview Cards -->
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3" id="stats-overview-cards">
                ${this._renderCardSkeleton(this._t('kpiTotalUsers', 'Total Users'), 'users')}
                ${this._renderCardSkeleton(this._t('kpiActiveToday', 'Active Today'), 'activity')}
                ${this._renderCardSkeleton(this._t('kpiTotalPosts', 'Total Posts'), 'message-square')}
                ${this._renderCardSkeleton(this._t('kpiTotalTips', 'Total Tips (GRAM)'), 'coins')}
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6" id="stats-overview-cards-2">
                ${this._renderCardSkeleton(this._t('kpiChatMessages', 'Chat Messages'), 'message-circle')}
                ${this._renderCardSkeleton(this._t('kpiMemories', 'Memories'), 'brain')}
                ${this._renderCardSkeleton(this._t('kpiCustomSkills', 'Custom Skills'), 'wrench')}
                ${this._renderCardSkeleton(this._t('kpiWalletUsers', 'Wallet Users'), 'wallet')}
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6" id="stats-overview-cards-3">
                ${this._renderCardSkeleton(this._t('kpiConversionRate', 'Conversion'), 'credit-card')}
                ${this._renderCardSkeleton(this._t('kpiRetentionD1', 'D1 Retention'), 'repeat')}
                ${this._renderCardSkeleton(this._t('kpiRetentionD7', 'D7 Retention'), 'calendar')}
                ${this._renderCardSkeleton(this._t('kpiAvgMsgPerUser', 'Avg Msg/User'), 'message-square-dashed')}
            </div>

            <!-- Agent runs + daily brief（tier 3 觀測項，2026-09-12） -->
            <div class="bg-surface rounded-2xl border border-borderSubtle p-4 mb-6" id="stats-agent-runs">
                <h3 class="font-bold text-secondary mb-3 text-sm flex items-center gap-2">
                    <i data-lucide="bot" class="w-4 h-4"></i> ${this._t('agentRunsTitle', 'Agent runs (last 24h)')}
                </h3>
                <div id="stats-agent-runs-body" class="text-sm text-textMuted">${this._t('loading', 'Loading...')}</div>
            </div>

            <!-- Email／Google 登入（Reown 內嵌錢包）：對照 Reown 免費方案每月上限（2026-09-30） -->
            <div class="bg-surface rounded-2xl border border-borderSubtle p-4 mb-6" id="stats-login-methods">
                <h3 class="font-bold text-secondary mb-3 text-sm flex items-center gap-2">
                    <i data-lucide="mail" class="w-4 h-4"></i> ${this._t('loginMethodsTitle', 'Email / Google sign-in (Reown embedded wallet)')}
                </h3>
                <div id="stats-login-methods-body" class="text-sm text-textMuted">${this._t('loading', 'Loading...')}</div>
            </div>

            <!-- 內容檢查服務（moderation 容器）狀態：發文頁顯示「暫時無法使用」時先看這裡（2026-10-01） -->
            <div class="bg-surface rounded-2xl border border-borderSubtle p-4 mb-6 flex items-start gap-2 text-sm" id="stats-moderation">
                <i data-lucide="shield" class="w-4 h-4 mt-0.5 shrink-0"></i>
                <div class="min-w-0"><span class="font-bold text-secondary">${this._t('moderationTitle', 'Content check service')}</span>
                    <span id="stats-moderation-body" class="ml-1 text-textMuted">${this._t('loading', 'Loading...')}</span></div>
            </div>

            <!-- Charts -->
            <div class="space-y-4">
                <div class="bg-surface rounded-2xl border border-borderSubtle p-4">
                    <h3 class="font-bold text-secondary mb-3 text-sm flex items-center gap-2">
                        <i data-lucide="trending-up" class="w-4 h-4"></i> ${this._t('chartUserGrowth', 'User Growth')}
                    </h3>
                    <div style="height: 180px; position: relative;">
                        <canvas id="chart-users"></canvas>
                    </div>
                </div>

                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    <div class="bg-surface rounded-2xl border border-borderSubtle p-4">
                        <h3 class="font-bold text-secondary mb-3 text-sm flex items-center gap-2">
                            <i data-lucide="message-square" class="w-4 h-4"></i> ${this._t('chartForumActivity', 'Forum Activity')}
                        </h3>
                        <div style="height: 168px; position: relative;">
                            <canvas id="chart-forum"></canvas>
                        </div>
                    </div>
                    <div class="bg-surface rounded-2xl border border-borderSubtle p-4">
                        <h3 class="font-bold text-secondary mb-3 text-sm flex items-center gap-2">
                            <i data-lucide="coins" class="w-4 h-4"></i> ${this._t('chartRevenue', 'Revenue')}
                        </h3>
                        <div style="height: 168px; position: relative;">
                            <canvas id="chart-revenue"></canvas>
                        </div>
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
        this.loadUserChart();
        this.loadForumChart();
        this.loadRevenueChart();
    },

    _updateRangeButtons() {
        document.querySelectorAll('.stats-range-btn').forEach((b) => {
            b.classList.remove('bg-primary/20', 'text-primary');
            b.classList.add('text-textMuted', 'hover:bg-surfaceHighlight');
        });
        const active = document.getElementById(`stats-range-${this.currentRange}`);
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
                <div class="text-xl font-bold text-secondary stats-value" data-stat="${label}">--</div>
            </div>
        `;
    },

    async loadAll() {
        this.loadOverview();
        this.loadAgentRuns();
        this.loadLoginMethods();
        this.loadModerationStatus();
        this.loadUserChart();
        this.loadForumChart();
        this.loadRevenueChart();
    },

    _esc(str) {
        return String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
    },

    _kpi(label, value, sub) {
        return `
            <div class="bg-background rounded-xl border border-borderSubtle p-3">
                <div class="text-[11px] text-textMuted">${this._esc(label)}</div>
                <div class="text-lg font-bold text-secondary tabular-nums">${this._esc(value)}</div>
                ${sub ? `<div class="text-[10px] text-textMuted/70 mt-0.5">${this._esc(sub)}</div>` : ''}
            </div>`;
    },

    _pairs(obj, limit = 6) {
        const entries = Object.entries(obj || {}).slice(0, limit);
        if (!entries.length) return `<span class="text-textMuted/60">—</span>`;
        return entries
            .map(([k, v]) => `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-surfaceHighlight text-xs"><span class="font-mono">${this._esc(k)}</span><span class="text-textMuted">${this._esc(v)}</span></span>`)
            .join(' ');
    },

    _dateTime(iso) {
        if (!iso) return '—';
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return '—';
        // 跟著站內語言，不是瀏覽器語系
        const lang = (window.I18n && window.I18n.getLanguage && window.I18n.getLanguage()) || undefined;
        try {
            return d.toLocaleString(lang, { dateStyle: 'short', timeStyle: 'short' });
        } catch (e) {
            return d.toLocaleString();
        }
    },

    // 內容檢查服務狀態（/api/admin/stats/moderation）：正常／載入中或失敗（帶錯誤）／連不到／已關閉
    async loadModerationStatus() {
        const body = document.getElementById('stats-moderation-body');
        if (!body) return;
        try {
            const s = await AppAPI.get('/api/admin/stats/moderation');
            let text;
            let cls;
            if (!s.enabled) {
                [text, cls] = [this._t('moderationDisabled', 'Turned off (MODERATION_ENABLED)'), 'text-textMuted'];
            } else if (s.ok) {
                [text, cls] = [`${this._t('moderationOk', 'Running')} · ${String(s.revision || '').slice(0, 8)}`, 'text-success'];
            } else if (s.reachable) {
                [text, cls] = [`${this._t('moderationLoading', 'Loading or failed to load the model')}${s.error ? ` \u2014 ${s.error}` : ''}`, 'text-amber-600'];
            } else {
                [text, cls] = [`${this._t('moderationUnreachable', 'Unreachable (is the moderation container running?)')}${s.error ? ` \u2014 ${s.error}` : ''}`, 'text-danger'];
            }
            body.textContent = text;
            body.className = `ml-1 break-words ${cls}`;
        } catch (e) {
            body.textContent = this._t('moderationFailed', 'Could not load status');
            body.className = 'ml-1 text-danger';
        }
    },

    // Email／Google 登入人數與名單（/api/admin/stats/login-methods，c064）
    async loadLoginMethods() {
        const body = document.getElementById('stats-login-methods-body');
        if (!body) return;
        try {
            const data = await AppAPI.get('/api/admin/stats/login-methods');
            const limit = Number(data.free_limit) || 500;
            const active = Number(data.month_active) || 0;
            const pct = Math.min(100, Math.round((active / limit) * 100));
            const barColor = pct >= 80 ? 'bg-danger' : pct >= 50 ? 'bg-accent' : 'bg-primary';
            const viaLabel = (v) => this._t('loginVia_' + v, v);
            const providers = {};
            (data.by_provider || []).forEach((p) => {
                providers[viaLabel(p.provider)] = `${p.month_active} / ${p.total}`;
            });
            const rows = (data.recent || [])
                .map((r) => `
                    <tr class="border-t border-borderSubtle">
                        <td class="py-1 pr-3"><div class="text-secondary">${this._esc(r.username || '—')}</div><div class="font-mono text-[10px] text-textMuted/70 break-all">${this._esc(r.user_id)}</div></td>
                        <td class="py-1 pr-3 whitespace-nowrap">${this._esc(viaLabel(r.provider))}</td>
                        <td class="py-1 pr-3 whitespace-nowrap tabular-nums">${this._esc(this._dateTime(r.first_seen_at))}</td>
                        <td class="py-1 pr-3 whitespace-nowrap tabular-nums">${this._esc(this._dateTime(r.last_seen_at))}</td>
                        <td class="py-1 text-right tabular-nums">${this._esc(r.login_count)}</td>
                    </tr>`)
                .join('');
            body.innerHTML = `
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
                    ${this._kpi(this._t('loginMonthActive', 'Active this month'), `${active} / ${limit}`, this._t('loginFreeLimitNote', 'Reown free plan cap — over it, sign-in stops'))}
                    ${this._kpi(this._t('loginMonthNew', 'New this month'), data.month_new ?? 0)}
                    ${this._kpi(this._t('loginTotal', 'All time'), data.total ?? 0)}
                    ${this._kpi(this._t('loginEvmThisMonth', 'All wallet sign-ins this month'), data.evm_logins_this_month ?? 0, this._t('loginEvmNote', 'Includes Trust / MetaMask (not billed by Reown)'))}
                </div>
                <div class="h-2 rounded-full bg-surfaceHighlight overflow-hidden mb-3" role="progressbar" aria-valuemin="0" aria-valuemax="${limit}" aria-valuenow="${active}">
                    <div class="h-full ${barColor}" style="width: ${pct}%"></div>
                </div>
                <div class="text-xs mb-3"><span class="text-textMuted">${this._t('loginByProvider', 'By method (this month / all time)')}:</span> ${this._pairs(providers)}</div>
                <div class="overflow-x-auto">
                    <table class="w-full text-xs">
                        <thead class="text-textMuted">
                            <tr>
                                <th class="text-left font-medium pb-1">${this._t('loginColUser', 'Account')}</th>
                                <th class="text-left font-medium pb-1">${this._t('loginColVia', 'Method')}</th>
                                <th class="text-left font-medium pb-1">${this._t('loginColFirst', 'First sign-in')}</th>
                                <th class="text-left font-medium pb-1">${this._t('loginColLast', 'Last sign-in')}</th>
                                <th class="text-right font-medium pb-1">${this._t('loginColCount', 'Sign-ins')}</th>
                            </tr>
                        </thead>
                        <tbody>${rows || `<tr><td colspan="5" class="py-2 text-textMuted/60">${this._t('loginEmpty', 'No Email / Google sign-ins yet')}</td></tr>`}</tbody>
                    </table>
                </div>
                <div class="text-[10px] text-textMuted/70 mt-2">${this._t('loginNote', 'Counted from 2026-09-30; earlier users appear after their next sign-in. The Reown dashboard is the billing source of truth.')}</div>`;
        } catch (e) {
            body.innerHTML = `<span class="text-danger">${this._t('loginFailed', 'Could not load sign-in stats')}</span>`;
        }
    },

    // Agent 運行指標＋每日早報（/api/admin/stats/agent-runs）
    async loadAgentRuns() {
        const body = document.getElementById('stats-agent-runs-body');
        if (!body) return;
        try {
            const data = await AppAPI.get('/api/admin/stats/agent-runs?hours=24');
            const runs = data.runs || {};
            const brief = data.brief || {};
            const fast = runs.fast_path_rate == null ? '—' : `${Math.round(runs.fast_path_rate * 100)}%`;
            const latencyRows = Object.entries(runs.latency_by_route || {})
                .map(([route, v]) => `
                    <tr class="border-t border-borderSubtle">
                        <td class="py-1 pr-3 font-mono text-xs">${this._esc(route)}</td>
                        <td class="py-1 pr-3 text-right tabular-nums">${this._esc(v.n)}</td>
                        <td class="py-1 pr-3 text-right tabular-nums">${v.p50 == null ? '—' : this._esc(v.p50) + 's'}</td>
                        <td class="py-1 text-right tabular-nums">${v.p95 == null ? '—' : this._esc(v.p95) + 's'}</td>
                    </tr>`)
                .join('');
            body.innerHTML = `
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                    ${this._kpi(this._t('agentRunsRuns', 'Runs'), runs.runs ?? 0, this._t('agentRunsFastPath', 'Fast path') + ` ${fast}`)}
                    ${this._kpi(this._t('agentRunsToolCalls', 'Avg tool calls'), runs.tool_calls_mean ?? '—')}
                    ${this._kpi(this._t('agentRunsTokens', 'Tokens (prompt / completion)'), `${runs.prompt_tokens ?? 0} / ${runs.completion_tokens ?? 0}`)}
                    ${this._kpi(this._t('briefSendsToday', 'Briefs sent today'), brief.sent_today ?? 0, `${this._t('briefSends14d', '14d')}: ${brief.sends_total ?? 0}`)}
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div class="overflow-x-auto">
                        <table class="w-full text-xs">
                            <thead><tr class="text-textMuted">
                                <th class="text-left font-medium pb-1">${this._t('agentRunsRoute', 'Route')}</th>
                                <th class="text-right font-medium pb-1">n</th>
                                <th class="text-right font-medium pb-1">p50</th>
                                <th class="text-right font-medium pb-1">p95</th>
                            </tr></thead>
                            <tbody>${latencyRows || `<tr><td colspan="4" class="py-2 text-textMuted/60">${this._t('agentRunsEmpty', 'No runs in this window')}</td></tr>`}</tbody>
                        </table>
                    </div>
                    <div class="space-y-2 text-xs">
                        <div><span class="text-textMuted">${this._t('agentRunsSources', 'Router source')}:</span> ${this._pairs(runs.sources)}</div>
                        <div><span class="text-textMuted">${this._t('agentRunsDepths', 'Depth')}:</span> ${this._pairs(runs.depths)}</div>
                        <div><span class="text-textMuted">${this._t('agentRunsSkills', 'Top skills')}:</span> ${this._pairs(runs.top_skills)}</div>
                        <div><span class="text-textMuted">${this._t('agentRunsProviders', 'Providers')}:</span> ${this._pairs(runs.providers)}</div>
                        <div><span class="text-textMuted">${this._t('agentRunsFallbacks', 'Router fallbacks')}:</span> ${this._pairs(runs.fallback_reasons)}</div>
                        <div class="pt-2 border-t border-borderSubtle">
                            <span class="text-textMuted">${this._t('briefTitle', 'Daily brief')}:</span>
                            ${this._pairs({
                                [this._t('briefEnabled', 'enabled')]: brief.prefs_enabled ?? 0,
                                [this._t('briefDisabled', 'disabled')]: brief.prefs_disabled ?? 0,
                                [this._t('briefTelegramBound', 'telegram')]: brief.telegram_bound ?? 0,
                                [this._t('briefCalendarEvents', 'calendar')]: `${brief.calendar_user_events ?? 0}+${brief.calendar_system_events ?? 0}`,
                            })}
                        </div>
                    </div>
                </div>`;
            if (window.AppUtils) window.AppUtils.refreshIcons();
        } catch (e) {
            console.error('[AdminStats] agent runs failed:', e);
            body.innerHTML = `<span class="text-danger">${this._t('agentRunsFailed', 'Could not load agent metrics')}</span>`;
        }
    },

    async loadOverview() {
        try {
            const data = await AppAPI.get('/api/admin/stats/overview');

            const cards = document.getElementById('stats-overview-cards');
            if (!cards) return;

            const values = [
                {
                    label: this._t('kpiTotalUsers', 'Total Users'),
                    value: `${data.total_users}`,
                    sub: `+${data.new_users_today} today | ${data.premium_users ?? 0} premium`,
                },
                {
                    label: this._t('kpiActiveToday', 'Active Today'),
                    value: `${data.active_today}`,
                    sub: `${data.pending_reports} pending reports`,
                },
                {
                    label: this._t('kpiTotalPosts', 'Total Posts'),
                    value: `${data.total_posts}`,
                    sub: `${data.total_comments} comments`,
                },
                {
                    label: this._t('kpiTotalTips', 'Total Tips (GRAM)'),
                    value: `${data.total_tips_amount.toFixed(2)}`,
                    sub: `${data.total_tips_count} transactions`,
                },
            ];

            const icons = ['users', 'activity', 'message-square', 'coins'];

            cards.innerHTML = values
                .map(
                    (v, i) => `
                <div class="bg-surface rounded-2xl border border-borderSubtle p-4 transition duration-200 hover:border-primary/30 hover:shadow-[0_4px_20px_rgba(37,99,235,0.08)]">
                    <div class="flex items-center gap-2 mb-2">
                        <span class="w-9 h-9 rounded-xl bg-primary/10 flex items-center justify-center shrink-0">
                            <i data-lucide="${icons[i]}" class="w-4 h-4 text-primary"></i>
                        </span>
                        <span class="text-xs text-textMuted">${v.label}</span>
                    </div>
                    <div class="text-xl font-bold text-secondary tabular-nums tracking-tight">${v.value}</div>
                    <div class="text-[10px] text-textMuted mt-1">${v.sub}</div>
                </div>
            `
                )
                .join('');

            // 第二行：產品使用深度（chat / memory / skill / wallet users）
            const cards2 = document.getElementById('stats-overview-cards-2');
            if (cards2) {
                const values2 = [
                    {
                        label: this._t('kpiChatMessages', 'Chat Messages'),
                        value: `${data.total_chat_messages ?? 0}`,
                        sub: `+${data.chat_messages_today ?? 0} today`,
                    },
                    {
                        label: this._t('kpiMemories', 'Memories'),
                        value: `${data.total_memories ?? 0}`,
                        sub: `${data.users_with_memory ?? 0} users`,
                    },
                    {
                        label: this._t('kpiCustomSkills', 'Custom Skills'),
                        value: `${data.total_custom_skills ?? 0}`,
                        sub: `${data.users_with_custom_skill ?? 0} users`,
                    },
                    {
                        label: this._t('kpiWalletUsers', 'Wallet Users'),
                        value: `${data.total_wallet_users ?? 0}`,
                        sub: 'EVM + bound wallets + legacy TON',
                    },
                ];
                if (data.total_wallet_users === undefined) {
                    values2[3] = { label: this._t('kpiWalletUsers', 'Wallet Users'), value: '—', sub: 'see Visitors tab' };
                }
                const icons2 = ['message-circle', 'brain', 'wrench', 'wallet'];
                cards2.innerHTML = values2
                    .map(
                        (v, i) => `
                    <div class="bg-surface rounded-2xl border border-borderSubtle p-4 transition duration-200 hover:border-primary/30 hover:shadow-[0_4px_20px_rgba(37,99,235,0.08)]">
                        <div class="flex items-center gap-2 mb-2">
                            <span class="w-9 h-9 rounded-xl bg-primary/10 flex items-center justify-center shrink-0">
                                <i data-lucide="${icons2[i]}" class="w-4 h-4 text-primary"></i>
                            </span>
                            <span class="text-xs text-textMuted">${v.label}</span>
                        </div>
                        <div class="text-xl font-bold text-secondary tabular-nums tracking-tight">${v.value}</div>
                        <div class="text-[10px] text-textMuted mt-1">${v.sub}</div>
                    </div>
                `
                    )
                    .join('');
            }

            // 第三行：經營指標（轉換率 / 留存率 / 平均訊息）
            const cards3 = document.getElementById('stats-overview-cards-3');
            if (cards3) {
                const pct = (v) => `${((v || 0) * 100).toFixed(1)}%`;
                const d1 = data.retention_d1_sample || {};
                const d7 = data.retention_d7_sample || {};
                const values3 = [
                    { label: this._t('kpiConversionRate', 'Conversion'), value: pct(data.conversion_rate), sub: `${data.paid_users ?? 0} paid / ${data.total_users ?? 0}` },
                    { label: this._t('kpiRetentionD1', 'D1'), value: pct(data.retention_d1), sub: `${d1.retained ?? 0} / ${d1.eligible ?? 0}` },
                    { label: this._t('kpiRetentionD7', 'D7'), value: pct(data.retention_d7), sub: `${d7.retained ?? 0} / ${d7.eligible ?? 0}` },
                    { label: this._t('kpiAvgMsgPerUser', 'Avg'), value: `${data.avg_messages_per_user ?? 0}`, sub: 'msgs / user' },
                ];
                const icons3 = ['credit-card', 'repeat', 'calendar', 'message-square-dashed'];
                cards3.innerHTML = values3
                    .map(
                        (v, i) => `
                    <div class="bg-surface rounded-2xl border border-borderSubtle p-4 transition duration-200 hover:border-primary/30 hover:shadow-[0_4px_20px_rgba(37,99,235,0.08)]">
                        <div class="flex items-center gap-2 mb-2">
                            <span class="w-9 h-9 rounded-xl bg-primary/10 flex items-center justify-center shrink-0">
                                <i data-lucide="${icons3[i]}" class="w-4 h-4 text-primary"></i>
                            </span>
                            <span class="text-xs text-textMuted">${v.label}</span>
                        </div>
                        <div class="text-xl font-bold text-secondary tabular-nums tracking-tight">${v.value}</div>
                        <div class="text-[10px] text-textMuted mt-1">${v.sub}</div>
                    </div>
                `
                    )
                    .join('');
            }

            AppUtils.refreshIcons();
        } catch (e) {
            console.warn('Failed to load overview stats:', e);
        }
    },

    async loadUserChart() {
        try {
            const data = await AppAPI.get(`/api/admin/stats/users?days=${this.currentRange}`);

            const filled = this._fillMissingDates(data.data, this.currentRange);
            const labels = filled.map((d) => d.date.substring(5)); // MM-DD
            const counts = filled.map((d) => d.count);

            // Cumulative
            let cumulative = [];
            let sum = 0;
            counts.forEach((c) => {
                sum += c;
                cumulative.push(sum);
            });

            // sparkline：Total Users card 顯示「每日新增」走勢
            this.trendCache.users = counts;
            const cards1 = document.getElementById('stats-overview-cards');
            this._injectSparkline(cards1, this._t('kpiTotalUsers', 'Total Users'), counts, { color: 'rgba(37, 99, 235, 0.9)' });

            this._createChart('chart-users', {
                type: 'line',
                data: {
                    labels,
                    datasets: [
                        {
                            label: 'New Users',
                            data: counts,
                            borderColor: 'rgba(37, 99, 235, 1)',
                            backgroundColor: 'rgba(37, 99, 235, 0.1)',
                            fill: true,
                            tension: 0.3,
                            yAxisID: 'y',
                        },
                        {
                            label: 'Cumulative',
                            data: cumulative,
                            borderColor: 'rgba(100, 180, 100, 0.6)',
                            borderDash: [5, 5],
                            tension: 0.3,
                            pointRadius: 0,
                            yAxisID: 'y1',
                        },
                    ],
                },
                options: this._chartOptions({
                    y: { position: 'left', title: { display: true, text: 'New', color: '#888' } },
                    y1: {
                        position: 'right',
                        grid: { drawOnChartArea: false },
                        title: { display: true, text: 'Total', color: '#888' },
                    },
                }),
            });
        } catch (e) {
            console.warn('Failed to load user chart:', e);
        }
    },

    async loadForumChart() {
        try {
            const data = await AppAPI.get(`/api/admin/stats/forum?days=${this.currentRange}`);

            const postsFilled = this._fillMissingDates(data.posts, this.currentRange);
            const commentsFilled = this._fillMissingDates(data.comments, this.currentRange);
            const labels = postsFilled.map((d) => d.date.substring(5));

            // sparkline：Total Posts card 顯示每日貼文數走勢
            const postsCounts = postsFilled.map((d) => d.count);
            this.trendCache.forum = postsCounts;
            const cards1 = document.getElementById('stats-overview-cards');
            this._injectSparkline(cards1, this._t('kpiTotalPosts', 'Total Posts'), postsCounts, { color: 'rgba(37, 99, 235, 0.9)' });

            this._createChart('chart-forum', {
                type: 'bar',
                data: {
                    labels,
                    datasets: [
                        {
                            label: 'Posts',
                            data: postsFilled.map((d) => d.count),
                            backgroundColor: 'rgba(37, 99, 235, 0.7)',
                            borderRadius: 4,
                        },
                        {
                            label: 'Comments',
                            data: commentsFilled.map((d) => d.count),
                            backgroundColor: 'rgba(100, 180, 100, 0.5)',
                            borderRadius: 4,
                        },
                    ],
                },
                options: this._chartOptions(),
            });
        } catch (e) {
            console.warn('Failed to load forum chart:', e);
        }
    },

    async loadRevenueChart() {
        try {
            const data = await AppAPI.get(`/api/admin/stats/revenue?days=${this.currentRange}`);

            const tipsFilled = this._fillMissingDates(
                data.tips.map((d) => ({ date: d.date, count: d.amount })),
                this.currentRange
            );
            const memFilled = this._fillMissingDates(
                data.memberships.map((d) => ({ date: d.date, count: d.amount })),
                this.currentRange
            );

            // sparkline：Total Tips card 顯示每日打賞金額走勢
            const tipsCounts = tipsFilled.map((d) => d.count);
            this.trendCache.revenue = tipsCounts;
            const cards1 = document.getElementById('stats-overview-cards');
            this._injectSparkline(cards1, this._t('kpiTotalTips', 'Total Tips (GRAM)'), tipsCounts, { color: 'rgba(37, 99, 235, 0.9)' });
            const labels = tipsFilled.map((d) => d.date.substring(5));

            this._createChart('chart-revenue', {
                type: 'line',
                data: {
                    labels,
                    datasets: [
                        {
                            label: 'Tips (TON)',
                            data: tipsFilled.map((d) => d.count),
                            borderColor: 'rgba(37, 99, 235, 1)',
                            backgroundColor: 'rgba(37, 99, 235, 0.1)',
                            fill: true,
                            tension: 0.3,
                        },
                        {
                            label: 'Memberships (TON)',
                            data: memFilled.map((d) => d.count),
                            borderColor: 'rgba(130, 100, 200, 0.8)',
                            backgroundColor: 'rgba(130, 100, 200, 0.1)',
                            fill: true,
                            tension: 0.3,
                        },
                    ],
                },
                options: this._chartOptions(),
            });
        } catch (e) {
            console.warn('Failed to load revenue chart:', e);
        }
    },

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
                    ticks: { color: '#666', font: { size: 10 } },
                    grid: { color: 'rgba(255,255,255,0.05)' },
                },
            },
        };

        if (scalesOverride) {
            base.scales = { x: base.scales.x, ...scalesOverride };
            // Ensure common x axis style
            Object.values(base.scales).forEach((s) => {
                if (!s.ticks) s.ticks = {};
                if (!s.ticks.color) s.ticks.color = '#666';
            });
        }

        return base;
    },

    _createChart(canvasId, config) {
        const canvas = document.getElementById(canvasId);
        if (!canvas) return;

        // Destroy existing
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

window.AdminStatsManager = AdminStatsManager;
