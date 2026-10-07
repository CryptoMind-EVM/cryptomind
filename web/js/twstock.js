/**
 * TW Stock Module
 *
 * Handles the logic and rendering for the top-level "TW Stock" navigation tab.
 * Includes sub-tab switching between "Market Watch" and "AI Pulse".
 */

window.TWStockTab = {
    activeSubTab: 'market', // 'market' | 'pulse'
    lastUpdatedAt: null,
    _pulseCache: {}, // { [symbol]: { data, hasKey, timestamp } }

    getStockName(item) {
        const lang = window.I18n?.getLanguage?.() || 'zh-TW';
        if (lang === 'en') return item.name_en || item.name_zh || item.Name || item.name || '';
        return item.name_zh || item.Name || item.name || '';
    },

    defaultSymbols: [
        '2330', '2317', '2454', '2308',
        '2881', '2412', '2882', '2891', '1301', '2002',
    ],

    // ── 精選標的清單（分組，供 Picker 使用）───────────────────────
    AVAILABLE_SYMBOLS: [
        { symbol: '2330', name: '台積電',   group: '半導體' },
        { symbol: '2454', name: '聯發科',   group: '半導體' },
        { symbol: '2303', name: '聯電',     group: '半導體' },
        { symbol: '2308', name: '台達電',   group: '半導體' },
        { symbol: '2317', name: '鴻海',     group: '電子' },
        { symbol: '2357', name: '華碩',     group: '電子' },
        { symbol: '2382', name: '廣達',     group: '電子' },
        { symbol: '3008', name: '大立光',   group: '電子' },
        { symbol: '2881', name: '富邦金',   group: '金融' },
        { symbol: '2882', name: '國泰金',   group: '金融' },
        { symbol: '2883', name: '開發金',   group: '金融' },
        { symbol: '2884', name: '玉山金',   group: '金融' },
        { symbol: '2891', name: '中信金',   group: '金融' },
        { symbol: '2412', name: '中華電',   group: '電信' },
        { symbol: '3045', name: '台灣大',   group: '電信' },
        { symbol: '1301', name: '台塑',     group: '傳產' },
        { symbol: '1303', name: '南亞',     group: '傳產' },
        { symbol: '2002', name: '中鋼',     group: '傳產' },
        { symbol: '6505', name: '台塑化',   group: '傳產' },
        { symbol: '2603', name: '長榮',     group: '航運' },
        { symbol: '2609', name: '陽明',     group: '航運' },
        { symbol: '2615', name: '萬海',     group: '航運' },
        { symbol: '0050', name: '元大台50', group: 'ETF' },
        { symbol: '0056', name: '元大高股息', group: 'ETF' },
        { symbol: '006208', name: '富邦台50', group: 'ETF' },
    ],

    showPicker() {
        const controlsContainer = document.getElementById('twstock-screener-controls');
        if (!controlsContainer) return;
        const selected = new Set(AppStore.get('twStockSelectedSymbols') || this.defaultSymbols);
        const groups = {};
        this.AVAILABLE_SYMBOLS.forEach(s => {
            if (!groups[s.group]) groups[s.group] = [];
            groups[s.group].push(s);
        });
        controlsContainer.innerHTML = `
            <div>
                <p class="text-xs text-textMuted mb-4">${window.I18n.t('stock.watchlistHint', { market: window.I18n.t('stock.marketNames.tw') })}</p>
                ${Object.entries(groups).map(([group, syms]) => `
                    <div class="mb-4">
                        <div class="text-[10px] uppercase tracking-wider text-textMuted/50 mb-2 pl-1">${window.I18n.t('sectors.' + group, { defaultValue: group })}</div>
                        <div class="grid grid-cols-2 gap-1.5">
                            ${syms.map(s => `
                                <label class="flex items-center gap-2 bg-surface border ${selected.has(s.symbol) ? 'border-primary/40 bg-primary/5' : 'border-borderSubtle'} rounded-xl px-3 py-2 cursor-pointer hover:border-primary/30 transition">
                                    <input type="checkbox" value="${s.symbol}" ${selected.has(s.symbol) ? 'checked' : ''}
                                        class="twstock-sym-check w-3.5 h-3.5 accent-primary">
                                    <div>
                                        <span class="text-xs font-bold text-secondary">${s.symbol}</span>
                                        <span class="text-[10px] text-textMuted ml-1">${escapeHtml(s.name)}</span>
                                    </div>
                                </label>`).join('')}
                        </div>
                    </div>`).join('')}
                <div class="flex gap-2 mt-2 pb-4">
                    <button data-click="TWStockTab.renderWatchlistControls" data-click-after="refreshMarketWatch"
                        class="flex-1 py-2.5 bg-surface border border-borderLight text-textMuted font-bold rounded-xl hover:bg-surfaceHighlight transition text-sm">${window.I18n.t('common.cancel')}</button>
                    <button data-click="TWStockTab._applyPicker"
                        class="flex-1 py-2.5 bg-primary text-background font-bold rounded-xl hover:opacity-90 transition text-sm">${window.I18n.t('stock.applySelection')}</button>
                </div>
            </div>`;
    },

    _applyPicker() {
        const checks = document.querySelectorAll('.twstock-sym-check:checked');
        const selected = Array.from(checks).map(c => c.value);
        if (selected.length === 0) {
            if (typeof showToast === 'function') showToast(window.I18n.t('stock.selectAtLeastOne'), 'error');
            return;
        }
        AppStore.set('twStockSelectedSymbols', selected);
        window.twStockSelectedSymbols = selected;
        this.saveTwStockSelection();
        this.renderWatchlistControls();
        this.refreshMarketWatch();
    },

    initTwStock: function () {
        window.addEventListener('languageChanged', () => {
            if (this.activeSubTab === 'market') this.refreshMarketWatch();
        });
        if (window.MarketStatus && !this._autoRefreshBound) {
            this._autoRefreshBound = true;
            window.MarketStatus.startMarketAutoRefresh(
                'twstock',
                () => this.refreshCurrent(),
                () => this.lastUpdatedAt
            );
        }
        this.loadTwStockSelection();
        this._hydrateServerWatchlist();
        this.renderWatchlistControls();
        this.bindEvents();
        this.refreshCurrent(true);
    },

    loadTwStockSelection: function () {
        try {
            const saved = localStorage.getItem('twStockWatchlist');
            if (saved) {
                AppStore.set('twStockSelectedSymbols', JSON.parse(saved));
                window.twStockSelectedSymbols = AppStore.get('twStockSelectedSymbols');
            } else {
                AppStore.set('twStockSelectedSymbols', [...this.defaultSymbols]);
                window.twStockSelectedSymbols = AppStore.get('twStockSelectedSymbols');
                this.saveTwStockSelection();
            }
        } catch (e) {
            console.warn('[TW Stock] Error loading watchlist from localStorage', e);
            AppStore.set('twStockSelectedSymbols', [...this.defaultSymbols]);
            window.twStockSelectedSymbols = AppStore.get('twStockSelectedSymbols');
        }
    },

    // 登入的人：自選存在伺服器（web/js/watchlist-sync.js）——伺服器有這個市場的清單就用伺服器的
    _hydrateServerWatchlist: function () {
        const ws = window.WatchlistSync;
        if (!ws) return;
        ws.hydrate('tw_stock', AppStore.get('twStockSelectedSymbols'), this.defaultSymbols).then((list) => {
            if (!list || ws.sameList(list, AppStore.get('twStockSelectedSymbols'), 'tw_stock')) return;
            AppStore.set('twStockSelectedSymbols', [...list]);
            window['twStockSelectedSymbols'] = AppStore.get('twStockSelectedSymbols');
            try {
                localStorage.setItem('twStockWatchlist', JSON.stringify(list));
            } catch (e) { /* ignore */ }
            this.renderWatchlistControls();
            this.refreshCurrent(true);
        });
    },

    saveTwStockSelection: function () {
        try {
            if (!AppStore.get('twStockSelectedSymbols') || AppStore.get('twStockSelectedSymbols').length === 0) {
                AppStore.set('twStockSelectedSymbols', [...this.defaultSymbols]);
                window.twStockSelectedSymbols = AppStore.get('twStockSelectedSymbols');
            }
            localStorage.setItem('twStockWatchlist', JSON.stringify(AppStore.get('twStockSelectedSymbols')));
            if (window.WatchlistSync) window.WatchlistSync.push('tw_stock', AppStore.get('twStockSelectedSymbols'), this.defaultSymbols);
        } catch (e) {
            console.error('[TW Stock] Failed to save watchlist', e);
        }
    },

    addTwStock: async function (symbol) {
        if (!symbol) return;
        const sym = symbol.toUpperCase().trim();
        if (sym.length < 2) return;

        const input = document.getElementById('twStockAddInput');
        if (AppStore.get('twStockSelectedSymbols').includes(sym)) {
            if (input) input.value = '';
            if (window.showToast) window.showToast(_t('twstock.alreadyInWatchlist').replace('{sym}', sym), 'info');
            return;
        }

        // Show loading state on button
        const btn = input ? input.nextElementSibling : null;
        let originalIcon = '';
        if (btn) {
            originalIcon = btn.innerHTML;
            btn.innerHTML =
                '<div class="w-4 h-4 border-2 border-primary/50 border-t-primary rounded-full animate-spin"></div>';
            btn.disabled = true;
        }

        try {
            // Validate symbol with backend
            const data = await AppAPI.get(`/api/twstock/market?symbols=${encodeURIComponent(sym)}`);
            if (data.top_performers && data.top_performers.length > 0) {
                // Symbol valid! Add to watchlist
                AppStore.get('twStockSelectedSymbols').unshift(sym);
                window.twStockSelectedSymbols = AppStore.get('twStockSelectedSymbols');
                this.saveTwStockSelection();
                this.refreshMarketWatch();
                this.refreshMarketInfo(); // Update News, Dividend, PE
                if (window.showToast) window.showToast(_t('twstock.addedToWatchlist').replace('{sym}', sym), 'success');
            } else {
                // Invalid symbol or no data returned
                if (window.showToast) {
                    window.showToast(
                        _t('twstock.symbolNotFound').replace('{sym}', sym),
                        'error'
                    );
                } else {
                    alert(_t('twstock.symbolNotFoundShort').replace('{sym}', sym));
                }
            }
        } catch (e) {
            console.error('[TW Stock] Validation error:', e);
            if (window.showToast) window.showToast(_t('twstock.addFailed'), 'error');
        } finally {
            // Restore UI state
            if (btn) {
                btn.innerHTML = originalIcon;
                btn.disabled = false;
            }
            if (input) input.value = '';
        }
    },

    removeTwStock: function (symbol, event) {
        if (event) {
            event.stopPropagation(); // prevent jumping to pulse
        }
        AppStore.set('twStockSelectedSymbols', AppStore.get('twStockSelectedSymbols').filter((s) => s !== symbol));
        window.twStockSelectedSymbols = AppStore.get('twStockSelectedSymbols');
        this.saveTwStockSelection();
        this.refreshMarketWatch();
        this.refreshMarketInfo(); // Update News, Dividend, PE
    },

    renderWatchlistControls: function () {
        const controlsContainer = document.getElementById('twstock-screener-controls');
        if (!controlsContainer) return;

        controlsContainer.innerHTML = `
            <div class="flex items-center gap-2 mb-4">
                <h3 class="font-bold text-secondary flex items-center gap-2 flex-shrink-0">
                    <i data-lucide="star" class="w-4 h-4 text-yellow-500"></i> My TW Stocks
                </h3>
                <button data-click="TWStockTab.showPicker"
                    class="p-1.5 text-textMuted hover:text-primary hover:bg-surfaceHighlight rounded-lg transition" title="${window.I18n.t('common.selectSymbol')}">
                    <i data-lucide="sliders-horizontal" class="w-4 h-4"></i>
                </button>
                <div class="flex-1 min-w-0">
                    <div class="relative">
                        <input type="text" id="twStockAddInput" placeholder="${_t('twstock.searchPlaceholder')}" maxlength="6"
                            data-input-filter="alphanumeric" data-uppercase
                            class="w-full bg-background/50 border border-borderLight rounded-lg pl-3 pr-10 py-1.5 text-sm focus:outline-none focus:border-primary transition-colors text-textMain placeholder-textMuted/50">
                        <button data-click="TWStockTab.addTwStock" data-click-input="twStockAddInput" class="absolute right-1 top-1/2 -translate-y-1/2 p-1 text-textMuted hover:text-primary transition-colors hover:bg-surfaceHighlight rounded">
                            <i data-lucide="plus" class="w-4 h-4"></i>
                        </button>
                    </div>
                </div>
            </div>
        `;
        AppUtils.refreshIcons();

        // Add enter key support
        const input = document.getElementById('twStockAddInput');
        if (input) {
            input.addEventListener('keypress', function (e) {
                if (e.key === 'Enter') {
                    window.TWStockTab.addTwStock(e.target.value);
                }
            });
        }
    },

    switchSubTab: function (tabId) {
        if (this.activeSubTab === tabId) return;

        console.log(`[TW Stock] Switching sub-tab to: ${tabId}`);

        // Update button states
        const marketBtn = document.getElementById('twstock-btn-market');
        const pulseBtn = document.getElementById('twstock-btn-pulse');
        const marketContent = document.getElementById('twstock-market-content');
        const pulseContent = document.getElementById('twstock-pulse-content');

        if (!marketBtn || !pulseBtn || !marketContent || !pulseContent) {
            console.error('[TW Stock] Cannot find DOM elements for tab switching.');
            return;
        }

        const activeClass =
            'twstock-sub-tab flex-1 py-2 px-4 rounded-lg font-bold text-sm transition flex items-center justify-center gap-2 bg-primary text-background shadow-md';
        const inactiveClass =
            'twstock-sub-tab flex-1 py-2 px-4 rounded-lg font-bold text-sm transition flex items-center justify-center gap-2 text-textMuted hover:text-textMain hover:bg-surfaceHighlight';

        // Hide all content panes
        [marketContent, pulseContent].forEach((el) => el && el.classList.add('hidden'));
        [marketBtn, pulseBtn].forEach((el) => el && (el.className = inactiveClass));

        if (tabId === 'market') {
            marketBtn.className = activeClass;
            marketContent.classList.remove('hidden');
        } else if (tabId === 'pulse') {
            pulseBtn.className = activeClass;
            pulseContent.classList.remove('hidden');
        }

        this.activeSubTab = tabId;

        // Fetch new data for the active tab
        this.refreshCurrent(true);
    },

    refreshCurrent: function (isFirstLoadForTab = false) {
        if (this.activeSubTab === 'market') {
            this.refreshMarketWatch();
            // Also load TWSE market info below the watchlist
            this.refreshMarketInfo();
        } else if (this.activeSubTab === 'pulse') {
            // Check if there's a selected symbol in the UI
            const inputEl = document.getElementById('twstockPulseSearchInput');
            const symbol = inputEl ? inputEl.value.trim() : '';
            if (symbol) {
                this.refreshAIPulse(symbol);
            } else {
                // Ignore empty symbol and show a prompt
                const pulseContainer = document.getElementById('twstock-pulse-result');
                if (pulseContainer) {
                    pulseContainer.innerHTML = `<div class="py-20 text-center text-textMuted uppercase tracking-widest text-sm italic opacity-50 flex flex-col items-center"><i data-lucide="search" class="w-8 h-8 mb-3 opacity-50"></i>${_t('marketPage.analysisPlaceholder')}</div>`;
                    pulseContainer.classList.remove('hidden');
                    AppUtils.refreshIcons();
                }
            }
        }
    },

    refreshMarketWatch: async function () {
        const listContainer = document.getElementById('twstock-screener-list');
        const loader = document.getElementById('twstock-market-loader');

        if (!listContainer || !loader) return;

        // 失敗冷卻：上游（yfinance）假日/離峰可 >15s，連續逾時後拉長重試
        // 間隔（30s→2min），避免錯誤洗版（2026-08-22 盤查：139 連發逾時）
        if (this._marketFailAfter && Date.now() < this._marketFailAfter) return;

        if (window.MarketStatus) window.MarketStatus.markSynced('twstock');
        loader.classList.remove('hidden');
        // 不先清空列表——保留舊資料避免每次重試閃白；成功時才整批換新

        try {
            let url = '/api/twstock/market';

            // Append symbols if we have a watchlist
            if (window.twStockSelectedSymbols && window.twStockSelectedSymbols.length > 0) {
                url += `?symbols=${encodeURIComponent(window.twStockSelectedSymbols.join(','))}`;
            }

            const data = await AppAPI.get(url);
            const topPerformers = data.top_performers || [];
            this.lastUpdatedAt = data.last_updated || new Date().toISOString();
            if (window.MarketStatus) {
                window.MarketStatus.markSynced('twstock');
                window.MarketStatus.updateMarketStatusBar('twstock', this.lastUpdatedAt);
            }
            this._marketFailStreak = 0;
            this._marketFailAfter = 0;
            this.renderMarketList(listContainer, topPerformers);
        } catch (error) {
            console.error('[TW Stock] Market API Error:', error);
            this._marketFailStreak = (this._marketFailStreak || 0) + 1;
            // 連續失敗 → 冷卻 2 分鐘；有舊資料時只顯示提示條不覆蓋列表
            this._marketFailAfter = Date.now() + 2 * 60 * 1000;
            if (!listContainer.children.length) {
                listContainer.innerHTML = `<div class="p-4 text-center text-danger bg-danger/10 rounded-xl text-sm">${_t('twstock.marketDataLoadFailed')}${SecurityUtils.escapeHTML(error.message || '')}</div>`;
            }
        } finally {
            loader.classList.add('hidden');
        }
    },

    renderMarketList: function (container, items) {
        if (!items || items.length === 0) {
            container.innerHTML =
                `<p class="text-textMuted text-[10px] italic py-6 text-center opacity-50 uppercase tracking-widest">${_t('marketPage.noData')}</p>`;
            return;
        }

        // Helper to escape HTML to prevent XSS
        const escapeHtml = (unsafe) => {
            if (!unsafe) return '';
            return String(unsafe)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;');
        };

        const fragment = document.createDocumentFragment();
        items.forEach((item) => {
            const sym = escapeHtml(item.Symbol || 'N/A');
            const name = escapeHtml(this.getStockName(item) || sym);
            const exchange = escapeHtml(item.Exchange || _t('twstock.twExchange'));
            const price = item.Close
                ? parseFloat(item.Close).toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                  })
                : '-';
            const change = item.price_change_24h ? parseFloat(item.price_change_24h) : 0;
            const isPos = change > 0;
            const isNeg = change < 0;
            const colorClass = isPos ? 'text-success' : isNeg ? 'text-danger' : 'text-textMuted';
            const sign = isPos ? '+' : '';

            const div = document.createElement('div');
            div.className =
                'group bg-surface/20 hover:bg-surface/40 border border-borderSubtle rounded-2xl p-4 transition-all duration-300 cursor-pointer';
            div.onclick = () => window.TWStockTab.jumpToPulse(sym);
            div.innerHTML = `
                <div class="flex items-start gap-3">
                    <div class="w-10 h-10 rounded-xl bg-background flex items-center justify-center text-xs font-bold text-primary border border-borderSubtle group-hover:scale-110 transition-transform flex-shrink-0 mt-0.5">${sym.substring(0, 2)}</div>
                    <div class="flex-1 min-w-0">
                        <div class="flex items-start justify-between gap-2">
                            <div class="min-w-0">
                                <div class="font-bold text-sm text-secondary leading-tight truncate" title="${name}">${name}</div>
                                <div class="text-[9px] text-textMuted font-bold tracking-wider uppercase opacity-60">${exchange}</div>
                            </div>
                            <div class="text-right flex-shrink-0">
                                <div class="text-sm font-black font-mono ${colorClass}">${sign}${change.toFixed(2)}%</div>
                                <div class="text-[9px] text-textMuted uppercase opacity-40 font-bold">24H</div>
                            </div>
                        </div>
                        <div class="flex items-center justify-between mt-1.5">
                            <div class="text-[11px] text-textMuted font-mono opacity-80">NT$${price}</div>
                            <div class="flex items-center gap-1">
                                <button data-click="TWStockTab.showTwChart" data-click-arg="${encodeURIComponent(sym)}" data-click-event class="w-7 h-7 rounded-lg flex items-center justify-center text-textMuted hover:text-primary hover:bg-primary/10 transition-colors border border-borderSubtle" title="${window.I18n ? window.I18n.t('common.viewChart') : 'View Chart'}">
                                    <i data-lucide="bar-chart-2" class="w-3.5 h-3.5"></i>
                                </button>
                                <button data-click="openAlert" data-click-args="${encodeURIComponent(JSON.stringify([sym, 'tw_stock']))}" data-click-stop class="w-7 h-7 rounded-lg flex items-center justify-center text-yellow-400 hover:text-yellow-300 hover:bg-yellow-400/10 transition-colors border border-borderSubtle" title="${_t('twstock.setPriceAlert')}"><span class="text-xs leading-none">🔔</span></button>
                                <button data-click="TWStockTab.removeTwStock" data-click-arg="${encodeURIComponent(sym)}" data-click-event class="w-7 h-7 rounded-lg flex items-center justify-center text-textMuted hover:text-danger hover:bg-danger/10 transition-colors border border-borderSubtle" title="${window.I18n ? window.I18n.t('common.removeFromWatchlist') : 'Remove from Watchlist'}">
                                    <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            `;
            fragment.appendChild(div);
        });

        container.innerHTML = '';
        container.appendChild(fragment);
        AppUtils.refreshIcons();
    },

    // ── AI 分析結果 localStorage 持久化（per-user per-symbol，保留 7 天）──────
    _aiLocalKey(symbol) {
        const u = window.AuthManager?.currentUser;
        const uid = (u?.user_id || u?.uid || 'anon').replace(/[^a-zA-Z0-9_-]/g, '_');
        return `ai_deep_tw_${uid}_${symbol.toUpperCase()}_${(window.I18n?.getLanguage?.() || localStorage.getItem('selectedLanguage') || 'zh-TW')}`;
    },
    _saveAILocal(symbol, data) {
        try {
            localStorage.setItem(this._aiLocalKey(symbol), JSON.stringify({ data, savedAt: Date.now() }));
        } catch (_) {}
    },
    _loadAILocal(symbol) {
        try {
            const raw = localStorage.getItem(this._aiLocalKey(symbol));
            if (!raw) return null;
            const { data, savedAt } = JSON.parse(raw);
            if (Date.now() - savedAt > 7 * 24 * 60 * 60 * 1000) {
                localStorage.removeItem(this._aiLocalKey(symbol));
                return null;
            }
            return { data, savedAt };
        } catch (_) { return null; }
    },

    refreshAIPulse: async function (symbol, forceRefresh = false) {
        const pulseContainer = document.getElementById('twstock-pulse-result');
        const loader = document.getElementById('twstock-pulse-loader');

        if (!pulseContainer || !loader) return;

        // ── 1) 記憶體快取且非強制刷新 → 直接顯示快取 ──────────────────────
        const cached = this._pulseCache[symbol];
        if (!forceRefresh && cached) {
            this._showCachedPulse(pulseContainer, cached, symbol);
            return;
        }

        // ── 2) localStorage 持久化快取（非強制刷新時使用）──────────────────
        if (!forceRefresh) {
            const _localResult = this._loadAILocal(symbol);
            if (_localResult) {
                const { data: localAI, savedAt: _localSavedAt } = _localResult;
                if (!localAI.cached_at) localAI.cached_at = new Date(_localSavedAt).toISOString();
                const userProvider = await window.APIKeyManager?.getCurrentProvider();
                this._pulseCache[symbol] = { data: localAI, hasKey: !!userProvider, timestamp: _localSavedAt };
                this._showCachedPulse(pulseContainer, this._pulseCache[symbol], symbol);
                return;
            }
        }

        loader.classList.remove('hidden');
        pulseContainer.classList.add('hidden');

        try {
            const userProvider = await window.APIKeyManager?.getCurrentProvider();
            // 永遠先抓基礎數據，不自動跑 AI — 需用戶手動點擊分析
            const url = `/api/twstock/pulse/${encodeURIComponent(symbol)}`;
            const data = await AppAPI.get(url);

            // 存快取
            this._pulseCache[symbol] = { data, hasKey: !!userProvider, timestamp: Date.now() };

            this.renderAIPulse(pulseContainer, data, !!userProvider);
            this._appendRefreshBtn(pulseContainer, symbol);
            pulseContainer.classList.remove('hidden');

            const titleEl = document.getElementById('twstock-pulse-title');
            if (titleEl)
                titleEl.textContent = `Taiwan Stock AI Pulse: ${data.company_name || symbol}`;
        } catch (error) {
            console.error('[TW Stock] Pulse API Error:', error);
            pulseContainer.innerHTML = `<div class="p-4 text-center text-danger bg-danger/10 rounded-xl text-sm">${_t('twstock.pulseLoadFailed').replace('{sym}', SecurityUtils.escapeHTML(symbol || ''))}${SecurityUtils.escapeHTML(error.message || '')}</div>`;
            pulseContainer.classList.remove('hidden');
        } finally {
            loader.classList.add('hidden');
        }
    },

    _showCachedPulse: function (container, cached, symbol) {
        const { data, hasKey, timestamp } = cached;
        this.renderAIPulse(container, data, hasKey);
        this._appendRefreshBtn(container, symbol, timestamp);
        container.classList.remove('hidden');

        const titleEl = document.getElementById('twstock-pulse-title');
        if (titleEl)
            titleEl.textContent = `Taiwan Stock AI Pulse: ${data.company_name || symbol}`;
    },

    _appendRefreshBtn: function (container, symbol, timestamp) {
        const timeStr = timestamp
            ? new Date(timestamp).toLocaleTimeString((window.I18n?.getLanguage?.() || 'zh-TW') === 'en' ? 'en-US' : 'zh-TW', { hour: '2-digit', minute: '2-digit' })
            : '';
        const bar = document.createElement('div');
        bar.className = 'flex items-center justify-between px-4 py-2 mt-2 rounded-xl bg-surfaceHighlight border border-borderLight text-xs text-textMuted';
        bar.innerHTML = `
            <span>${timeStr ? window.I18n.t('stock.cachedWithTime', { time: timeStr }) : window.I18n.t('stock.cached')}</span>
            <button class="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-primary/10 text-primary hover:bg-primary/20 border border-primary/20 transition"
                    data-click="TWStockTab.refreshAIPulse" data-click-args="${encodeURIComponent(JSON.stringify([symbol, true]))}">
                <i data-lucide="refresh-cw" class="w-3 h-3"></i> ${window.I18n.t('stock.updateAnalysis')}
            </button>`;
        container.appendChild(bar);
        AppUtils.refreshIcons();
    },

    runDeepAnalysis: async function (symbol, forceRefresh) {
        const pulseContainer = document.getElementById('twstock-pulse-result');
        const loader = document.getElementById('twstock-pulse-loader');
        if (!pulseContainer || !loader) return;

        // 直接顯示 loading，不做前端 key 檢查（讓後端用 session cookie 取 key）
        loader.classList.remove('hidden');
        pulseContainer.classList.add('hidden');

        try {
            // 取 provider hint（同步，不依賴 API call）；後端有 key 就用，沒有就 fallback
            const providerHint = window.APIKeyManager?.getSelectedProvider?.() || 'openai';
            const _force = forceRefresh ? 'true' : 'false';
            const url = `/api/twstock/pulse/${encodeURIComponent(symbol)}?deep_analysis=true&force_refresh=${_force}&lang=${encodeURIComponent(window.I18n?.getLanguage?.() || localStorage.getItem('selectedLanguage') || 'zh-TW')}`;
            const data = await AppAPI.get(url, { headers: { 'X-User-LLM-Provider': providerHint }, timeout: 120000 });
            // 更新記憶體快取 + localStorage 持久化（7 天）
            this._pulseCache[symbol] = { data, hasKey: true, timestamp: Date.now() };
            this._saveAILocal(symbol, data);
            this.renderAIPulse(pulseContainer, data, true);
            pulseContainer.classList.remove('hidden');
        } catch (error) {
            console.error('[TW Stock] Deep Analysis Error:', error);
            pulseContainer.innerHTML = `<div class="p-4 text-center text-danger bg-danger/10 rounded-xl text-sm">${SecurityUtils.escapeHTML(error.message || _t('twstock.pulseLoadFailed'))}</div>`;
            pulseContainer.classList.remove('hidden');
        } finally {
            loader.classList.add('hidden');
        }
    },

    renderAIPulse: function (container, data, hasKey) {
        const rep = data.report || {};
        const tech = data.technical_indicators || {};
        const fund = data.fundamentals || {};
        const inst = data.institutional || {};

        const isPos = data.change_24h > 0;
        const isNeg = data.change_24h < 0;
        const colorClass = isPos ? 'text-success' : isNeg ? 'text-danger' : 'text-textMuted';
        const bgClass = isPos ? 'bg-success/10' : isNeg ? 'bg-danger/10' : 'bg-surfaceHighlight';
        const sign = isPos ? '+' : '';
        const icon = isPos ? 'trending-up' : isNeg ? 'trending-down' : 'minus';

        // ── Helpers ────────────────────────────────────────────────────────
        const fv = (v, d = 2) => (v != null && !isNaN(Number(v)) ? Number(v).toFixed(d) : 'N/A');
        const fvPct = (v, d = 1) =>
            v != null && !isNaN(Number(v)) ? Number(v).toFixed(d) + '%' : 'N/A';
        const fmtMktCap = (v) => {
            if (!v || isNaN(v)) return 'N/A';
            // 單位交給 Intl 依介面語言換算（ru 的「億」譯成 млрд＝1e9，自己除 1e8 會多 10 倍）
            return new Intl.NumberFormat(window.I18n?.getLanguage?.() || 'zh-TW', {
                notation: 'compact',
                maximumFractionDigits: 2,
            }).format(v);
        };
        const parseInst = (v) => {
            if (!v || v === 'N/A' || v === '') return null;
            const n = parseInt(String(v).replace(/,/g, ''), 10);
            return isNaN(n) ? null : n;
        };
        const instRow = (label, raw) => {
            const n = parseInst(raw);
            if (n === null)
                return `<div class="flex items-center justify-between py-2 border-b border-borderSubtle last:border-0"><span class="text-xs text-textMuted">${label}</span><span class="text-xs text-textMuted font-mono">N/A</span></div>`;
            const c = n > 0 ? 'text-success' : 'text-danger';
            const ic = n > 0 ? 'arrow-up' : 'arrow-down';
            return `<div class="flex items-center justify-between py-2 border-b border-borderSubtle last:border-0"><span class="text-xs text-textMuted">${label}</span><span class="text-xs font-bold font-mono ${c} flex items-center gap-1"><i data-lucide="${ic}" class="w-3 h-3"></i>${n > 0 ? '+' : ''}${n.toLocaleString()}${_t('twstock.unitShares')}</span></div>`;
        };
        const pctRow = (label, raw, isAlready100 = false) => {
            if (raw == null || isNaN(Number(raw)))
                return `<div class="flex items-center justify-between py-2 border-b border-borderSubtle last:border-0"><span class="text-xs text-textMuted">${label}</span><span class="text-xs text-textMuted font-mono">N/A</span></div>`;
            const val = isAlready100 ? Number(raw) : Number(raw) * 100;
            const c = val > 0 ? 'text-success' : val < 0 ? 'text-danger' : 'text-textMuted';
            return `<div class="flex items-center justify-between py-2 border-b border-borderSubtle last:border-0"><span class="text-xs text-textMuted">${label}</span><span class="text-xs font-bold font-mono ${c}">${val > 0 ? '+' : ''}${val.toFixed(1)}%</span></div>`;
        };

        // RSI colour & label
        const rsiVal = tech.rsi_14;
        const rsiColor =
            rsiVal == null
                ? 'text-textMuted'
                : rsiVal < 30
                  ? 'text-success'
                  : rsiVal > 70
                    ? 'text-danger'
                    : 'text-secondary';
        const rsiLabelText =
            rsiVal == null ? '' : rsiVal < 30 ? _t('pulse.oversold') : rsiVal > 70 ? _t('pulse.overbought') : _t('pulse.neutral');
        const rsiLabelStyle =
            rsiVal == null
                ? ''
                : rsiVal < 30
                  ? 'bg-success/20 text-success'
                  : rsiVal > 70
                    ? 'bg-danger/20 text-danger'
                    : 'bg-surfaceHighlight text-textMuted';

        // MACD
        const macdObj = typeof tech.macd === 'object' && tech.macd !== null ? tech.macd : {};
        const macdHist = macdObj.histogram;
        const macdHistColor =
            macdHist == null ? 'text-textMuted' : macdHist > 0 ? 'text-success' : 'text-danger';

        // MA position
        const close = data.current_price || 0;
        const maPosBadge = (maVal) => {
            if (!maVal || !close) return '';
            return close >= maVal
                ? `<span class="text-[9px] ml-1 px-1 rounded bg-success/20 text-success">${_t('twstock.aboveMA')}</span>`
                : `<span class="text-[9px] ml-1 px-1 rounded bg-danger/20 text-danger">${_t('twstock.belowMA')}</span>`;
        };

        // 52W progress bar
        const low52 = fund['52w_low'];
        const high52 = fund['52w_high'];
        let w52Html = '<div class="text-xs text-textMuted">N/A</div>';
        if (low52 && high52 && close && high52 > low52) {
            const pct = Math.min(100, Math.max(0, ((close - low52) / (high52 - low52)) * 100));
            w52Html = `
                <div class="flex justify-between text-[9px] text-textMuted mb-1">
                    <span>${_t('twstock.week52Low')} ${fv(low52)}</span><span>${_t('twstock.currentPriceLabel')} ${pct.toFixed(0)}%</span><span>${_t('twstock.week52High')} ${fv(high52)}</span>
                </div>
                <div class="h-1.5 rounded-full bg-surfaceHighlight overflow-hidden">
                    <div class="h-full rounded-full bg-gradient-to-r from-danger via-yellow-500 to-success" style="width:${pct}%"></div>
                </div>`;
        }

        const maArr = tech.ma || {};

        const html = `
            <!-- Hero Price Section -->
            <div class="relative overflow-hidden bg-surface/80 backdrop-blur-xl rounded-3xl p-6 mb-6 border border-borderLight shadow-2xl shadow-black/20">
                <div class="absolute -top-24 -right-24 w-48 h-48 bg-primary/20 rounded-full blur-3xl opacity-50"></div>
                ${isPos ? '<div class="absolute -bottom-24 -left-24 w-48 h-48 bg-success/10 rounded-full blur-3xl opacity-50"></div>' : ''}
                ${isNeg ? '<div class="absolute -bottom-24 -left-24 w-48 h-48 bg-danger/10 rounded-full blur-3xl opacity-50"></div>' : ''}
                <div class="relative z-10 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                    <div class="flex items-center gap-5">
                        <div class="w-16 h-16 rounded-2xl bg-gradient-to-br from-primary/20 to-primary/5 border border-primary/20 flex items-center justify-center shadow-inner">
                            <i data-lucide="building-2" class="w-8 h-8 text-primary"></i>
                        </div>
                        <div>
                            <div class="flex items-center gap-2 mb-1">
                                <h2 class="text-xl md:text-2xl font-serif text-secondary font-bold">${escapeHtml(data.company_name)}</h2>
                                <span class="px-2 py-0.5 rounded text-xs font-bold tracking-wider uppercase bg-surfaceHighlight text-textMain border border-borderLight">${escapeHtml(data.symbol)}</span>
                            </div>
                            <div class="text-xs text-textMuted flex items-center gap-2">
                                <i data-lucide="map-pin" class="w-3 h-3"></i> Taiwan Stock Exchange (TWSE)
                            </div>
                        </div>
                    </div>
                    <div class="mt-2 md:mt-0 ml-20 md:ml-0 flex flex-col items-start md:items-end">
                        <div class="text-[10px] text-textMuted uppercase tracking-[0.2em] mb-1 font-bold"data-i18n="stock.currentPrice">Current Price</div>
                        <div class="text-4xl font-mono font-black text-secondary tracking-tight mb-2 flex items-center gap-2">
                            <span class="text-xl text-primary font-serif font-medium">$</span>${data.current_price}
                        </div>
                        <div class="inline-flex items-center gap-1.5 text-sm font-bold ${colorClass} ${bgClass} px-3 py-1 rounded-lg border border-borderSubtle backdrop-blur-md shadow-sm">
                            <i data-lucide="${icon}" class="w-4 h-4"></i>
                            <span>${sign}${data.change_24h}% (24h)</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- AI Summary + News -->
            <div class="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
                <div class="lg:col-span-2">
                    <div class="bg-surface/60 backdrop-blur-md border border-primary/20 rounded-2xl p-6 shadow-lg shadow-primary/5 relative overflow-hidden h-full">
                        <div class="absolute top-0 left-0 w-1 h-full bg-gradient-to-b from-primary via-accent to-primary"></div>
                        <h3 class="font-serif text-lg text-primary mb-4 flex items-center gap-3">
                            <div class="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center">
                                <i data-lucide="brain-circuit" class="w-4 h-4 text-primary"></i>
                            </div>
                            ${_t('stock.pulseAiSummary')}
                        </h3>
                        <div class="md:ml-11">
                            ${window.renderAIAnalysisSection({
                                data: data,
                                tabName: 'TWStockTab',
                                hasKey: hasKey,
                                settingsBtn: "switchTab('settings')",
                                symbolArg: data.symbol || '',
                                _t: _t,
                            })}
                        </div>
                    </div>
                </div>
                <div>
                    ${
                        rep.highlights && rep.highlights.length > 0
                            ? `
                        <div class="bg-surface/40 backdrop-blur-sm border border-borderSubtle rounded-2xl p-6 h-full">
                            <h3 class="font-bold text-secondary mb-4 flex items-center gap-2 text-sm uppercase tracking-wider">
                                <i data-lucide="rss" class="w-4 h-4 text-yellow-500"></i> Market Sentiments
                            </h3>
                            <div class="space-y-3">
                                ${rep.highlights
                                    .map(
                                        (h) => `
                                    <a href="${sanitizeUrl(h.url)}" target="_blank" rel="noopener noreferrer"
                                       class="block bg-surfaceHighlight p-3 rounded-lg border border-borderSubtle hover:border-borderLight transition-colors">
                                        <p class="text-xs text-textMain leading-relaxed line-clamp-3 hover:text-primary transition-colors">${escapeHtml(h.title || '')}</p>
                                    </a>
                                `
                                    )
                                    .join('')}
                            </div>
                        </div>
                    `
                            : ''
                    }
                </div>
            </div>

            <!-- Section A: Technical Analysis -->
            <div class="bg-surface/40 backdrop-blur-sm border border-borderSubtle rounded-2xl p-6 mb-6">
                <h3 class="font-bold text-secondary mb-5 flex items-center gap-2 text-sm uppercase tracking-wider">
                    <i data-lucide="activity" class="w-4 h-4 text-accent"></i> Technical Analysis
                </h3>
                <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                    <!-- RSI -->
                    <div class="bg-background/80 rounded-xl p-4 border border-borderSubtle hover:border-borderLight transition-colors">
                        <div class="text-[10px] text-textMuted uppercase tracking-wider mb-2">RSI (14)</div>
                        <div class="flex items-end justify-between mb-2">
                            <span class="text-2xl font-black font-mono ${rsiColor}">${rsiVal != null ? Number(rsiVal).toFixed(1) : 'N/A'}</span>
                            ${rsiLabelText ? `<span class="text-[10px] font-bold px-2 py-0.5 rounded-full ${rsiLabelStyle}">${rsiLabelText}</span>` : ''}
                        </div>
                        ${rsiVal != null ? `<div class="h-1.5 rounded-full bg-surfaceHighlight overflow-hidden"><div class="h-full rounded-full" style="width:${Math.min(100, Number(rsiVal))}%;background:${Number(rsiVal) < 30 ? '#86efac' : Number(rsiVal) > 70 ? '#fda4af' : '#a1a1aa'}"></div></div>` : ''}
                    </div>
                    <!-- MACD -->
                    <div class="bg-background/80 rounded-xl p-4 border border-borderSubtle hover:border-borderLight transition-colors">
                        <div class="text-[10px] text-textMuted uppercase tracking-wider mb-2">MACD (12/26/9)</div>
                        <div class="space-y-1.5">
                            <div class="flex justify-between text-xs"><span class="text-textMuted" data-i18n="stock.macd">MACD</span><span class="font-mono text-secondary">${fv(macdObj.macd, 3)}</span></div>
                            <div class="flex justify-between text-xs"><span class="text-textMuted" data-i18n="stock.signal">Signal</span><span class="font-mono text-secondary">${fv(macdObj.signal, 3)}</span></div>
                            <div class="flex justify-between text-xs"><span class="text-textMuted" data-i18n="stock.histogram">Histogram</span><span class="font-bold font-mono ${macdHistColor}">${fv(macdHist, 3)}</span></div>
                        </div>
                    </div>
                    <!-- KD -->
                    <div class="bg-background/80 rounded-xl p-4 border border-borderSubtle hover:border-borderLight transition-colors">
                        <div class="text-[10px] text-textMuted uppercase tracking-wider mb-2">${_t('twstock.kdIndicator')}</div>
                        <div class="space-y-1.5">
                            <div class="flex justify-between text-xs"><span class="text-textMuted">${_t('twstock.kValue')}</span><span class="font-bold font-mono text-secondary">${fv((tech.kd || {}).k)}</span></div>
                            <div class="flex justify-between text-xs"><span class="text-textMuted">${_t('twstock.dValue')}</span><span class="font-bold font-mono text-secondary">${fv((tech.kd || {}).d)}</span></div>
                        </div>
                    </div>
                    <!-- MA -->
                    <div class="bg-background/80 rounded-xl p-4 border border-borderSubtle hover:border-borderLight transition-colors">
                        <div class="text-[10px] text-textMuted uppercase tracking-wider mb-2">${_t('twstock.movingAverage')}</div>
                        <div class="space-y-1.5">
                            ${[
                                ['MA5', maArr.ma5],
                                ['MA20', maArr.ma20],
                                ['MA60', maArr.ma60],
                            ]
                                .map(
                                    ([l, v]) =>
                                        `<div class="flex justify-between text-xs items-center"><span class="text-textMuted">${l}</span><span class="font-mono text-secondary">${fv(v)}${v ? maPosBadge(v) : ''}</span></div>`
                                )
                                .join('')}
                        </div>
                    </div>
                </div>
            </div>

            <!-- Section B + C: Fundamentals + Institutional -->
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <!-- Section B: Fundamentals -->
                <div class="bg-surface/40 backdrop-blur-sm border border-borderSubtle rounded-2xl p-6">
                    <h3 class="font-bold text-secondary mb-5 flex items-center gap-2 text-sm uppercase tracking-wider">
                        <i data-lucide="bar-chart-2" class="w-4 h-4 text-primary"></i> Fundamental Analysis
                    </h3>
                    <div class="grid grid-cols-2 gap-3 mb-4">
                        <div class="bg-background/60 rounded-lg p-3 border border-borderSubtle">
                            <div class="text-[9px] text-textMuted uppercase mb-1">${_t('marketPage.peRatio')}</div>
                            <div class="font-bold font-mono text-secondary">${fv(fund.pe_ratio)}</div>
                        </div>
                        <div class="bg-background/60 rounded-lg p-3 border border-borderSubtle">
                            <div class="text-[9px] text-textMuted uppercase mb-1">${_t('marketPage.pbRatio')}</div>
                            <div class="font-bold font-mono text-secondary">${fv(fund.pb_ratio)}</div>
                        </div>
                        <div class="bg-background/60 rounded-lg p-3 border border-borderSubtle">
                            <div class="text-[9px] text-textMuted uppercase mb-1">EPS (TTM)</div>
                            <div class="font-bold font-mono text-secondary">${fv(fund.eps_ttm)}</div>
                        </div>
                        <div class="bg-background/60 rounded-lg p-3 border border-borderSubtle">
                            <div class="text-[9px] text-textMuted uppercase mb-1">${_t('marketPage.yield')}</div>
                            <div class="font-bold font-mono ${fund.dividend_yield_pct > 4 ? 'text-success' : 'text-secondary'}">${fund.dividend_yield_pct != null ? fv(fund.dividend_yield_pct) + '%' : 'N/A'}</div>
                        </div>
                        <div class="bg-background/60 rounded-lg p-3 border border-borderSubtle">
                            <div class="text-[9px] text-textMuted uppercase mb-1">${_t('twstock.grossMargin')}</div>
                            <div class="font-bold font-mono text-secondary">${fund.profit_margins != null ? fvPct(fund.profit_margins * 100) : 'N/A'}</div>
                        </div>
                        <div class="bg-background/60 rounded-lg p-3 border border-borderSubtle">
                            <div class="text-[9px] text-textMuted uppercase mb-1">${_t('twstock.marketCap')}</div>
                            <div class="font-bold font-mono text-secondary text-xs">${fmtMktCap(fund.market_cap)}</div>
                        </div>
                    </div>
                    <div class="bg-background/60 rounded-lg p-3 border border-borderSubtle">
                        <div class="text-[9px] text-textMuted uppercase mb-2">${_t('twstock.week52Range')}</div>
                        ${w52Html}
                    </div>
                    ${(() => {
                        const close = data.current_price || 0;
                        const tp = fund.target_price;
                        const upside = tp && close ? ((tp - close) / close * 100) : null;
                        const recLabelMap = {
                            'strong_buy': [window.I18n.t('stock.strongBuy'),'text-success'], 'buy': [window.I18n.t('stock.buy'),'text-success'],
                            'hold': [window.I18n.t('stock.hold'),'text-textMuted'], 'underperform': [window.I18n.t('stock.underperform'),'text-danger'],
                            'sell': [window.I18n.t('stock.sell'),'text-danger'], 'strong_sell': [window.I18n.t('stock.strongSell'),'text-danger'],
                        };
                        const recInfo = recLabelMap[fund.recommendation];
                        const hasSup = tp != null || fund.beta != null || fund.recommendation || fund.sector || fund.revenue_growth != null;
                        if (!hasSup) return '';
                        return `<div class="mt-3 pt-3 border-t border-borderSubtle grid grid-cols-2 gap-2">
                            ${tp != null ? `<div class="bg-background/60 rounded-lg p-3 border border-borderSubtle">
                                <div class="text-[9px] text-textMuted uppercase mb-1">${window.I18n.t('stock.analystTarget')}</div>
                                <div class="font-bold font-mono text-secondary">${tp.toFixed(2)}</div>
                                ${upside != null ? `<div class="text-[9px] font-bold mt-0.5 ${upside >= 0 ? 'text-success' : 'text-danger'}">${upside >= 0 ? '+' : ''}${upside.toFixed(1)}%</div>` : ''}
                            </div>` : ''}
                            ${fund.recommendation ? `<div class="bg-background/60 rounded-lg p-3 border border-borderSubtle">
                                <div class="text-[9px] text-textMuted uppercase mb-1">${window.I18n.t('stock.analystRating')}</div>
                                <div class="font-bold ${recInfo ? recInfo[1] : 'text-secondary'}">${recInfo ? recInfo[0] : fund.recommendation.toUpperCase()}</div>
                            </div>` : ''}
                            ${fund.beta != null ? `<div class="bg-background/60 rounded-lg p-3 border border-borderSubtle">
                                <div class="text-[9px] text-textMuted uppercase mb-1"data-i18n="stock.beta">Beta</div>
                                <div class="font-bold font-mono ${fund.beta > 1.5 ? 'text-danger' : fund.beta < 0.8 ? 'text-success' : 'text-secondary'}">${fund.beta}</div>
                            </div>` : ''}
                            ${fund.revenue_growth != null ? `<div class="bg-background/60 rounded-lg p-3 border border-borderSubtle">
                                <div class="text-[9px] text-textMuted uppercase mb-1">${window.I18n.t('stock.revenueGrowth')}</div>
                                <div class="font-bold font-mono ${fund.revenue_growth >= 0 ? 'text-success' : 'text-danger'}">${fund.revenue_growth >= 0 ? '+' : ''}${fund.revenue_growth}%</div>
                            </div>` : ''}
                        </div>
                        ${fund.sector ? `<div class="mt-2 text-[10px] text-textMuted flex items-center gap-1.5"><i data-lucide="building-2" class="w-3 h-3 shrink-0"></i><span>${fund.sector}${fund.industry ? ' · ' + fund.industry : ''}</span></div>` : ''}`;
                    })()}
                </div>

                <!-- Section C: Institutional -->
                <div class="bg-surface/40 backdrop-blur-sm border border-borderSubtle rounded-2xl p-6">
                    <h3 class="font-bold text-secondary mb-5 flex items-center gap-2 text-sm uppercase tracking-wider">
                        <i data-lucide="users" class="w-4 h-4 text-yellow-500"></i> ${_t('twstock.institutionalChip')}
                    </h3>
                    <div class="space-y-0">
                        ${instRow(_t('twstock.foreignNetBuySell'), inst.foreign_net)}
                        ${instRow(_t('twstock.investTrustNet'), inst.investment_trust)}
                        ${instRow(_t('twstock.dealerNet'), inst.dealer_net)}
                    </div>
                    <div class="mt-3 pt-3 border-t border-borderLight">
                        ${instRow(_t('twstock.totalInstitutional'), inst.total_3party_net)}
                    </div>
                    ${inst.date ? `<div class="mt-3 text-[10px] text-textMuted flex items-center gap-1"><i data-lucide="calendar" class="w-3 h-3"></i> ${_t('twstock.dataDate')}${inst.date}</div>` : ''}
                    ${inst.note ? `<div class="mt-2 text-[10px] text-textMuted/70 leading-relaxed border-t border-borderSubtle pt-2">${inst.note}</div>` : ''}
                </div>
            </div>

            ${
                data.monthly_revenue || data.dividend_info
                    ? `
            <!-- Section D + E: Monthly Revenue + Dividend -->
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-6">
                ${
                    data.monthly_revenue
                        ? `
                <div class="bg-surface/40 backdrop-blur-sm border border-borderSubtle rounded-2xl p-6">
                    <h3 class="font-bold text-secondary mb-5 flex items-center gap-2 text-sm uppercase tracking-wider">
                        <i data-lucide="trending-up" class="w-4 h-4 text-success"></i> ${_t('twstock.monthlyRevenue')}
                        ${data.monthly_revenue.ym ? `<span class="text-[10px] text-textMuted font-normal ml-1">${data.monthly_revenue.ym}</span>` : ''}
                    </h3>
                    <div class="grid grid-cols-2 gap-3">
                        <div class="col-span-2 bg-background/60 rounded-lg p-3 border border-borderSubtle">
                            <div class="text-[9px] text-textMuted uppercase mb-1">${_t('twstock.currentMonthRevenue')}</div>
                            <div class="font-bold font-mono text-secondary text-lg">${data.monthly_revenue.current_revenue ? Number(String(data.monthly_revenue.current_revenue).replace(/,/g, '')).toLocaleString() + _t('twstock.unitThousandNT') : 'N/A'}</div>
                        </div>
                        <div class="bg-background/60 rounded-lg p-3 border border-borderSubtle">
                            <div class="text-[9px] text-textMuted uppercase mb-1">${_t('twstock.mom')}</div>
                            <div class="font-bold font-mono ${Number(data.monthly_revenue.mom_pct) > 0 ? 'text-success' : Number(data.monthly_revenue.mom_pct) < 0 ? 'text-danger' : 'text-secondary'}">${data.monthly_revenue.mom_pct != null ? (Number(data.monthly_revenue.mom_pct) > 0 ? '+' : '') + Number(data.monthly_revenue.mom_pct).toFixed(1) + '%' : 'N/A'}</div>
                        </div>
                        <div class="bg-background/60 rounded-lg p-3 border border-borderSubtle">
                            <div class="text-[9px] text-textMuted uppercase mb-1">${_t('twstock.yoy')}</div>
                            <div class="font-bold font-mono ${Number(data.monthly_revenue.yoy_pct) > 0 ? 'text-success' : Number(data.monthly_revenue.yoy_pct) < 0 ? 'text-danger' : 'text-secondary'}">${data.monthly_revenue.yoy_pct != null ? (Number(data.monthly_revenue.yoy_pct) > 0 ? '+' : '') + Number(data.monthly_revenue.yoy_pct).toFixed(1) + '%' : 'N/A'}</div>
                        </div>
                        <div class="bg-background/60 rounded-lg p-3 border border-borderSubtle">
                            <div class="text-[9px] text-textMuted uppercase mb-1">${_t('twstock.cumulativeRevenue')}</div>
                            <div class="font-bold font-mono text-secondary text-xs">${data.monthly_revenue.ytd_revenue ? Number(String(data.monthly_revenue.ytd_revenue).replace(/,/g, '')).toLocaleString() + _t('twstock.unitThousandNT') : 'N/A'}</div>
                        </div>
                        <div class="bg-background/60 rounded-lg p-3 border border-borderSubtle">
                            <div class="text-[9px] text-textMuted uppercase mb-1">${_t('twstock.cumulativeYoY')}</div>
                            <div class="font-bold font-mono ${Number(data.monthly_revenue.ytd_yoy_pct) > 0 ? 'text-success' : Number(data.monthly_revenue.ytd_yoy_pct) < 0 ? 'text-danger' : 'text-secondary'}">${data.monthly_revenue.ytd_yoy_pct != null ? (Number(data.monthly_revenue.ytd_yoy_pct) > 0 ? '+' : '') + Number(data.monthly_revenue.ytd_yoy_pct).toFixed(1) + '%' : 'N/A'}</div>
                        </div>
                    </div>
                </div>
                `
                        : '<div></div>'
                }

                ${
                    data.dividend_info
                        ? `
                <div class="bg-surface/40 backdrop-blur-sm border border-borderSubtle rounded-2xl p-6">
                    <h3 class="font-bold text-secondary mb-5 flex items-center gap-2 text-sm uppercase tracking-wider">
                        <i data-lucide="gift" class="w-4 h-4 text-yellow-500"></i> ${_t('twstock.dividendInfo')}
                        ${data.dividend_info.year ? `<span class="text-[10px] text-textMuted font-normal ml-1">${data.dividend_info.year}${_t('twstock.yearSuffix')}</span>` : ''}
                    </h3>
                    <div class="grid grid-cols-2 gap-3 mb-4">
                        <div class="bg-background/60 rounded-lg p-3 border border-borderSubtle">
                            <div class="text-[9px] text-textMuted uppercase mb-1">${_t('twstock.cashDividend')}</div>
                            <div class="font-bold font-mono ${Number(data.dividend_info.cash_dividend) > 0 ? 'text-success' : 'text-secondary'}">${data.dividend_info.cash_dividend || 'N/A'}${_t('twstock.unitNTD')}</div>
                        </div>
                        <div class="bg-background/60 rounded-lg p-3 border border-borderSubtle">
                            <div class="text-[9px] text-textMuted uppercase mb-1">${_t('twstock.stockDividend')}</div>
                            <div class="font-bold font-mono text-secondary">${data.dividend_info.stock_dividend || 'N/A'}${_t('twstock.unitNTD')}</div>
                        </div>
                    </div>
                    <div class="space-y-0">
                        ${data.dividend_info.board_date ? `<div class="flex justify-between py-2 border-b border-borderSubtle"><span class="text-xs text-textMuted">${_t('twstock.boardDate')}</span><span class="text-xs font-mono text-secondary">${data.dividend_info.board_date}</span></div>` : ''}
                        ${data.dividend_info.shareholder_mtg ? `<div class="flex justify-between py-2 border-b border-borderSubtle"><span class="text-xs text-textMuted">${_t('twstock.shareholderMeetingDate')}</span><span class="text-xs font-mono text-secondary">${data.dividend_info.shareholder_mtg}</span></div>` : ''}
                        ${data.dividend_info.progress ? `<div class="flex justify-between py-2"><span class="text-xs text-textMuted">${_t('twstock.resolutionProgress')}</span><span class="text-xs text-secondary text-right max-w-[60%]">${data.dividend_info.progress}</span></div>` : ''}
                    </div>
                </div>
                `
                        : '<div></div>'
                }
            </div>
            `
                    : ''
            }
        `;

        container.innerHTML = html;
        AppUtils.refreshIcons();
    },

    jumpToPulse: function (symbol) {
        const inputEl = document.getElementById('twstockPulseSearchInput');
        if (inputEl) inputEl.value = symbol;
        if (this.activeSubTab === 'pulse') {
            this.refreshAIPulse(symbol);
        } else {
            this.switchSubTab('pulse');
        }
    },

    // Chart logic
    twChart: null,
    twCandleSeries: null,
    twVolumeSeries: null,
    twCurrentChartSymbol: null,
    twCurrentChartInterval: '1d',

    showTwChart: async function (symbol, event) {
        if (event) {
            event.stopPropagation();
        }

        const chartSection = document.getElementById('twstock-chart-section');
        const chartContainer = document.getElementById('twstock-chart-container');
        const volumeContainer = document.getElementById('twstock-volume-container');
        const titleEl = document.getElementById('twstock-chart-title');

        if (!chartSection || !chartContainer || typeof LightweightCharts === 'undefined') {
            console.error('[TW Stock] Chart elements or LightweightCharts missing');
            return;
        }

        this.twCurrentChartSymbol = symbol;
        chartSection.classList.remove('hidden');
        AppUtils.refreshIcons();
        if (titleEl)
            titleEl.textContent = `${symbol} (${this.twCurrentChartInterval.toUpperCase()})`;

        // Update active interval button UI
        document.querySelectorAll('.tw-chart-interval-btn').forEach((btn) => {
            if (btn.dataset.interval === this.twCurrentChartInterval) {
                btn.classList.add('bg-surfaceHighlight', 'text-primary');
                btn.classList.remove('text-textMuted');
            } else {
                btn.classList.remove('bg-surfaceHighlight', 'text-primary');
                btn.classList.add('text-textMuted');
            }
        });

        chartContainer.innerHTML =
            '<div class="animate-pulse text-textMuted h-full flex items-center justify-center">' + _t('twstock.loadingHistoricalData') + '</div>';
        if (volumeContainer) volumeContainer.innerHTML = '';

        try {
            const responseData = await AppAPI.get(
                `/api/twstock/klines/${encodeURIComponent(symbol)}?interval=${this.twCurrentChartInterval}&limit=200`
            );

            const data = responseData.data || [];

            if (data.length === 0) {
                chartContainer.innerHTML =
                    '<div class="text-danger h-full flex items-center justify-center">' + _t('twstock.noHistoricalData') + '</div>';
                return;
            }

            // Update timestamp
            const updatedEl = document.getElementById('twstock-chart-updated');
            if (updatedEl) {
                const now = new Date();
                updatedEl.textContent = _t('twstock.loadedAt') + now.toLocaleTimeString(AppUtils.locale(), { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' ';
            }

            chartContainer.innerHTML = '';
            if (this.twChart) {
                this.twChart.remove();
                this.twChart = null;
            }

            // Show volume container as separate panel
            if (volumeContainer) {
                volumeContainer.innerHTML = '';
                volumeContainer.style.display = '';
            }

            this.twChart = LightweightCharts.createChart(chartContainer, {
                layout: {
                    background: { type: 'solid', color: 'transparent' },
                    textColor: '#A0AEC0',
                },
                grid: {
                    vertLines: { color: 'rgba(255,255,255,0.05)' },
                    horzLines: { color: 'rgba(255,255,255,0.05)' },
                },
                crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
                rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)' },
                timeScale: {
                    borderColor: 'rgba(255,255,255,0.1)',
                    timeVisible: true,
                    rightOffset: 5,
                },
                handleScroll: {
                    mouseWheel: true,
                    pressedMouseMove: true,
                    horzTouchDrag: true,
                    vertTouchDrag: false,
                },
                handleScale: {
                    mouseWheel: true,
                    pinchScale: true,
                    axisPressedMouseMove: { time: true, price: false },
                },
            });

            this.twCandleSeries = this.twChart.addCandlestickSeries({
                upColor: '#10B981',
                downColor: '#EF4444',
                borderVisible: false,
                wickUpColor: '#10B981',
                wickDownColor: '#EF4444',
            });

            // Create separate volume chart
            if (this.twVolumeChart) {
                this.twVolumeChart.remove();
                this.twVolumeChart = null;
            }
            this.twVolumeSeries = null;
            if (volumeContainer) {
                this.twVolumeChart = LightweightCharts.createChart(volumeContainer, {
                    layout: {
                        background: { type: 'solid', color: 'transparent' },
                        textColor: '#A0AEC0',
                    },
                    grid: {
                        vertLines: { color: 'rgba(255,255,255,0.05)' },
                        horzLines: { color: 'rgba(255,255,255,0.02)' },
                    },
                    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
                    rightPriceScale: {
                        borderColor: 'rgba(255,255,255,0.1)',
                        scaleMargins: { top: 0.1, bottom: 0.05 },
                    },
                    timeScale: { visible: false },
                    handleScroll: {
                        mouseWheel: false,
                        pressedMouseMove: false,
                        horzTouchDrag: false,
                        vertTouchDrag: false,
                    },
                    handleScale: { mouseWheel: false, pinchScale: false },
                });
                this.twVolumeSeries = this.twVolumeChart.addHistogramSeries({
                    priceFormat: { type: 'volume' },
                });
            }

            // Map data
            const formattedKlines = data.map((k) => ({
                time: k.time,
                open: k.open,
                high: k.high,
                low: k.low,
                close: k.close,
            }));

            const formattedVolume = data.map((k) => ({
                time: k.time,
                value: k.volume,
                color: k.close >= k.open ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)',
            }));

            this.twCandleSeries.setData(formattedKlines);
            if (this.twVolumeSeries) this.twVolumeSeries.setData(formattedVolume);
            this.twChart.timeScale().fitContent();
            if (this.twVolumeChart) {
                this.twVolumeChart.timeScale().fitContent();
                let _syncingRange = false;
                this.twChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
                    if (_syncingRange || !range || !this.twVolumeChart) return;
                    _syncingRange = true;
                    this.twVolumeChart.timeScale().setVisibleLogicalRange(range);
                    _syncingRange = false;
                });
                this.twVolumeChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
                    if (_syncingRange || !range || !this.twChart) return;
                    _syncingRange = true;
                    this.twChart.timeScale().setVisibleLogicalRange(range);
                    _syncingRange = false;
                });
            }

            // Helper functions for formatting
            const formatPrice = (price) => price.toFixed(2);
            const formatVolume = (vol) => {
                if (vol >= 1e9) return (vol / 1e9).toFixed(2) + 'B';
                if (vol >= 1e6) return (vol / 1e6).toFixed(2) + 'M';
                if (vol >= 1e3) return (vol / 1e3).toFixed(2) + 'K';
                return vol.toFixed(2);
            };

            const openEl = document.getElementById('tw-info-open');
            const highEl = document.getElementById('tw-info-high');
            const lowEl = document.getElementById('tw-info-low');
            const closeEl = document.getElementById('tw-info-close');
            const volEl = document.getElementById('tw-info-volume');

            const setHoverData = (kline, volumeData) => {
                if (!kline) return;
                const isUp = kline.close >= kline.open;
                const color = isUp ? 'text-success' : 'text-danger';

                if (openEl) openEl.textContent = formatPrice(kline.open);
                if (highEl) highEl.textContent = formatPrice(kline.high);
                if (lowEl) lowEl.textContent = formatPrice(kline.low);
                if (closeEl) {
                    closeEl.textContent = formatPrice(kline.close);
                    closeEl.className = color + ' ml-0.5';
                }
                if (volEl && volumeData) {
                    // For histogram series, the value is just a number or an object with value
                    const v =
                        typeof volumeData === 'object' && volumeData.value !== undefined
                            ? volumeData.value
                            : volumeData;
                    volEl.textContent = formatVolume(v);
                }
            };

            // Set initial data to the last bar
            if (formattedKlines.length > 0) {
                setHoverData(
                    formattedKlines[formattedKlines.length - 1],
                    formattedVolume[formattedVolume.length - 1]
                );
            }

            // Hover tooltips
            this.twChart.subscribeCrosshairMove((param) => {
                if (
                    param.point === undefined ||
                    !param.time ||
                    param.point.x < 0 ||
                    param.point.x > chartContainer.clientWidth ||
                    param.point.y < 0 ||
                    param.point.y > chartContainer.clientHeight
                ) {
                    // Reset to last candle when mouse leaves chart
                    if (formattedKlines.length > 0) {
                        setHoverData(
                            formattedKlines[formattedKlines.length - 1],
                            formattedVolume[formattedVolume.length - 1]
                        );
                    }
                    if (this.twVolumeChart) this.twVolumeChart.clearCrosshairPosition();
                    return;
                }
                const dataPoint = param.seriesData.get(this.twCandleSeries);
                // Look up volume by time since it's on a separate chart
                const volData = formattedVolume.find((v) => v.time === param.time);
                if (dataPoint) {
                    setHoverData(dataPoint, volData);
                }
                // Sync crosshair position to volume chart
                if (this.twVolumeChart && this.twVolumeSeries && volData) {
                    this.twVolumeChart.setCrosshairPosition(
                        volData.value,
                        param.time,
                        this.twVolumeSeries
                    );
                }
            });

            // Handle resize — remove old handler first to prevent leaks
            window.removeEventListener('resize', this._twChartResizeHandler);
            const onResize = () => {
                if (chartSection && !chartSection.classList.contains('hidden') && this.twChart) {
                    const w = chartContainer.clientWidth;
                    if (!w) return;
                    this.twChart.applyOptions({ width: w });
                    if (this.twVolumeChart && volumeContainer) {
                        this.twVolumeChart.applyOptions({ width: volumeContainer.clientWidth });
                    }
                    // 寬度變更後重新 fitContent，否則蠟燭不撐滿、右側留白
                    this.twChart.timeScale().fitContent();
                }
            };
            this._twChartResizeHandler = window.Utils ? window.Utils.debounce(onResize, 150) : onResize;
            window.addEventListener('resize', this._twChartResizeHandler);

            // Theme 切換時更新圖表配色（文字/格線/邊框跟著 token，避免亮模式留在暗色低對比）
            window.removeEventListener('themeChanged', this._twChartThemeHandler || (() => {}));
            this._twChartThemeHandler = () => {
                if (!this.twChart) return;
                const tokenRgb = (name, fb) => {
                    const r = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
                    return r ? `rgb(${r})` : fb;
                };
                const textColor = tokenRgb('--color-text-muted', '#A0AEC0');
                const gridColor = tokenRgb('--color-surface-deep', 'rgba(255,255,255,0.05)');
                const borderColor = tokenRgb('--color-text-muted', 'rgba(255,255,255,0.1)');
                const opts = {
                    layout: { textColor },
                    grid: { vertLines: { color: gridColor }, horzLines: { color: gridColor } },
                    rightPriceScale: { borderColor },
                    timeScale: { borderColor },
                };
                try {
                    this.twChart.applyOptions(opts);
                    if (this.twVolumeChart) this.twVolumeChart.applyOptions(opts);
                } catch (e) { /* 圖表已銷毀則略過 */ }
            };
            window.addEventListener('themeChanged', this._twChartThemeHandler);

            // ResizeObserver：容器寬度在 createChart 後才定案時 window resize 不會觸發
            if (this._twChartResizeObserver) this._twChartResizeObserver.disconnect();
            if (typeof ResizeObserver !== 'undefined') {
                this._twChartResizeObserver = new ResizeObserver(onResize);
                this._twChartResizeObserver.observe(chartContainer);
            }

            // Trigger initial resize to fit vertically
            setTimeout(onResize, 50);
        } catch (error) {
            console.error('[TW Stock] Chart Data Error:', error);
            chartContainer.innerHTML = `<div class="text-danger h-full flex flex-col items-center justify-center text-sm p-4 text-center">
            <i data-lucide="alert-triangle" class="w-8 h-8 mb-2"></i>
        ${_t('twstock.readFailed')}${SecurityUtils.escapeHTML(error.message || '')}
            </div>`;
            AppUtils.refreshIcons();
        }
    },

    closeTwChart: function () {
        window.removeEventListener('resize', this._twChartResizeHandler);
        this._twChartResizeHandler = null;
        if (this._twChartThemeHandler) {
            window.removeEventListener('themeChanged', this._twChartThemeHandler);
            this._twChartThemeHandler = null;
        }
        const section = document.getElementById('twstock-chart-section');
        if (section) section.classList.add('hidden');
        if (this.twChart) {
            this.twChart.remove();
            this.twChart = null;
        }
        if (this.twVolumeChart) {
            this.twVolumeChart.remove();
            this.twVolumeChart = null;
        }
        this.twVolumeSeries = null;
        this.twCurrentChartSymbol = null;
    },

    changeChartInterval: function (interval) {
        if (!this.twCurrentChartSymbol || this.twCurrentChartInterval === interval) return;
        this.twCurrentChartInterval = interval;
        this.showTwChart(this.twCurrentChartSymbol); // re-fetch and render
    },

    bindEvents: function () {
        const searchBtn = document.getElementById('twstockPulseSearchBtn');
        const inputEl = document.getElementById('twstockPulseSearchInput');

        if (searchBtn && inputEl && !searchBtn.dataset.bound) {
            searchBtn.dataset.bound = 'true';
            searchBtn.addEventListener('click', () => {
                const sym = inputEl.value.trim();
                if (sym) {
                    this.refreshAIPulse(sym, true); // 手動搜尋 → 強制刷新
                }
            });
            inputEl.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    const sym = inputEl.value.trim();
                    if (sym) {
                        this.refreshAIPulse(sym, true); // 手動搜尋 → 強制刷新
                    }
                }
            });
        }
    },

    // ── Market Info Tab ───────────────────────────────────────────────────────

    initSectionToggles: function () {
        try {
            const prefs = JSON.parse(localStorage.getItem('twstock_section_prefs') || '{}');
            const sections = ['pe', 'news', 'dividend', 'foreign'];
            sections.forEach((sec) => {
                const isHidden = prefs[sec] === false; // visible by default
                const bodyEl = document.getElementById(`twstock-section-body-${sec}`);
                const chevronEl = document.getElementById(`twstock-chevron-${sec}`);
                if (bodyEl && chevronEl) {
                    if (isHidden) {
                        bodyEl.classList.add('hidden');
                        chevronEl.classList.add('rotate-180');
                    } else {
                        bodyEl.classList.remove('hidden');
                        chevronEl.classList.remove('rotate-180');
                    }
                }
            });
        } catch (e) {
            console.warn('[TW Stock] Error parsing section prefs:', e);
        }
    },

    toggleSection: function (sectionKey) {
        const bodyEl = document.getElementById(`twstock-section-body-${sectionKey}`);
        const chevronEl = document.getElementById(`twstock-chevron-${sectionKey}`);
        if (!bodyEl || !chevronEl) return;

        const isCurrentlyHidden = bodyEl.classList.contains('hidden');
        if (isCurrentlyHidden) {
            bodyEl.classList.remove('hidden');
            chevronEl.classList.remove('rotate-180');
        } else {
            bodyEl.classList.add('hidden');
            chevronEl.classList.add('rotate-180');
        }

        // Save preference
        try {
            const prefs = JSON.parse(localStorage.getItem('twstock_section_prefs') || '{}');
            prefs[sectionKey] = isCurrentlyHidden; // true if now visible
            localStorage.setItem('twstock_section_prefs', JSON.stringify(prefs));
        } catch (e) {
            console.warn('[TW Stock] Error saving section prefs:', e);
        }
    },

    refreshMarketInfo: async function () {
        this.initSectionToggles();

        // Load all 4 sections in parallel
        await Promise.all([
            this._loadNewsSection(),
            this._loadPESection(),
            this._loadDividendSection(),
            this._loadForeignSection(),
        ]);
    },

    _showLoader: function (loaderId, show) {
        const el = document.getElementById(loaderId);
        if (!el) return;
        el.classList.toggle('hidden', !show);
        el.classList.toggle('flex', show);
    },

    /** 在板塊容器底部加「快取時間 + 刷新」列 */
    _appendSectionRefreshBar: function (container, cacheKey, refreshFn) {
        const timeStr = window.AppCache?.getTimeStr(cacheKey) || '';
        const bar = document.createElement('div');
        bar.className = 'flex items-center justify-between px-1 pt-2 mt-1 border-t border-borderSubtle text-[10px] text-textMuted';
        bar.innerHTML = `
            <span>${timeStr ? window.I18n.t('stock.updatedAtTime', { time: timeStr }) : ''}</span>
            <button class="twstock-section-refresh flex items-center gap-1 px-2 py-1 rounded-lg hover:bg-surfaceHighlight transition text-textMuted hover:text-secondary">
                <i data-lucide="refresh-cw" class="w-3 h-3"></i> ${window.I18n.t('stock.refresh')}
            </button>`;
        container.appendChild(bar);
        const refreshButton = bar.querySelector('.twstock-section-refresh');
        if (refreshButton) refreshButton.addEventListener('click', refreshFn);
        AppUtils.refreshIcons();
    },

    _loadNewsSection: async function (forceRefresh = false) {
        const container = document.getElementById('twstock-info-news');
        if (!container) return;
        const symbols = AppStore.get('twStockSelectedSymbols') || [];
        const cacheKey = 'tw_news_' + symbols.join(',');

        // 有快取且非強制 → 直接用快取
        if (!forceRefresh && window.AppCache?.has(cacheKey)) {
            container.innerHTML = window.AppCache.get(cacheKey);
            this._appendSectionRefreshBar(container, cacheKey,
                () => window.TWStockTab._loadNewsSection(true));
            return;
        }

        this._showLoader('twstock-info-news-loader', true);
        try {
            let url = '/api/twstock/opendata/news?limit=15';
            if (symbols.length > 0) {
                url += `&symbols=${encodeURIComponent(symbols.join(','))}`;
            }

            const json = await AppAPI.get(url);
            const items = json.data || [];
            if (!items.length) {
                container.innerHTML =
                    '<p class="text-textMuted text-xs italic text-center py-6 opacity-50">' + _t('twstock.noAnnouncements') + '</p>';
                return;
            }
            const html = items.length
                ? items.map((item) => `
            <div class="bg-surface/40 border border-yellow-500/10 hover:border-yellow-500/30 rounded-xl p-3 transition-colors cursor-default">
                <div class="flex items-start gap-3">
                    <div class="flex-shrink-0 w-12 text-center">
                        <div class="text-xs font-black text-yellow-400 font-mono">${escapeHtml(item.code || '—')}</div>
                        <div class="text-[10px] text-textMuted opacity-60 truncate">${escapeHtml(item.name || '')}</div>
                    </div>
                    <div class="flex-1 min-w-0">
                        <p class="text-xs text-textMain leading-relaxed line-clamp-2">${escapeHtml(item.subject || _t('twstock.noSubject'))}</p>
                        <div class="flex items-center gap-2 mt-1">
                            <span class="text-[10px] text-textMuted opacity-50">${escapeHtml(item.date || '')} ${escapeHtml(item.time || '')}</span>
                            ${item.rule ? `<span class="text-[9px] bg-yellow-500/10 text-yellow-400 px-1.5 py-0.5 rounded font-mono">${escapeHtml(item.rule)}</span>` : ''}
                        </div>
                    </div>
                </div>
            </div>`).join('')
                : '<p class="text-textMuted text-xs italic text-center py-6 opacity-50">' + _t('twstock.noAnnouncements') + '</p>';
            window.AppCache?.setWithTime(cacheKey, html, 5 * 60 * 1000); // 快取 5 分鐘
            container.innerHTML = html;
            this._appendSectionRefreshBar(container, cacheKey,
                () => window.TWStockTab._loadNewsSection(true));
        } catch (err) {
            console.error('[TW Info] News error:', err);
            container.innerHTML = `<p class="text-danger text-xs text-center py-4">${_t('twstock.newsLoadFailed')}${SecurityUtils.escapeHTML(err.message || '')}</p>`;
        } finally {
            this._showLoader('twstock-info-news-loader', false);
        }
    },

    _loadPESection: async function (forceRefresh = false) {
        const container = document.getElementById('twstock-info-pe');
        if (!container) return;
        const symbols = AppStore.get('twStockSelectedSymbols') || ['2330', '2317', '2454', '2881', '2882'];
        const cacheKey = 'tw_pe_' + symbols.slice(0, 20).join(',');

        if (!forceRefresh && window.AppCache?.has(cacheKey)) {
            container.innerHTML = window.AppCache.get(cacheKey);
            this._appendSectionRefreshBar(container, cacheKey,
                () => window.TWStockTab._loadPESection(true));
            return;
        }

        this._showLoader('twstock-info-pe-loader', true);
        try {
            const targets = symbols.slice(0, 20);
            // 一次查整批（以前每檔各打一個請求，一次 10 個——2026-09-26 量測）；
            // 批次端點失敗才退回逐檔
            let results;
            try {
                const batch = await AppAPI.get(
                    `/api/twstock/opendata/pe_ratio?symbols=${targets.map(encodeURIComponent).join(',')}`
                );
                results = (batch && batch.data) || [];
            } catch (_) {
                results = await Promise.all(
                    targets.map((code) =>
                        AppAPI.get(`/api/twstock/opendata/pe_ratio/${code}`)
                            .then((r) => (!r.error ? r : null))
                            .catch(() => null)
                    )
                );
            }
            const valid = results.filter(Boolean).filter((r) => !r.error);
            const html = valid.length
                ? valid.map((d) => {
                    const pe = parseFloat(d.pe_ratio) || 0;
                    const dy = parseFloat(d.dividend_yield) || 0;
                    const pb = parseFloat(d.pb_ratio) || 0;
                    const peColor = pe <= 0 ? 'text-textMuted' : pe < 15 ? 'text-success' : pe < 25 ? 'text-yellow-400' : 'text-danger';
                    const dyColor = dy <= 0 ? 'text-textMuted' : dy > 4 ? 'text-success' : 'text-secondary';
                    return `
            <div class="relative overflow-hidden rounded-2xl border border-borderSubtle bg-gradient-to-br from-surface to-background hover:border-primary/30 transition-all duration-200 group cursor-default">
                        <div class="absolute top-0 right-0 w-16 h-16 bg-primary/5 rounded-full blur-2xl group-hover:bg-primary/10 transition-all"></div>
                        <div class="relative p-4">
                            <div class="flex items-start justify-between mb-4">
                                <div>
                                    <div class="font-black text-primary font-mono text-base leading-none">${d.code || d.symbol || '—'}</div>
                                    <div class="text-[11px] text-textMuted mt-0.5 truncate max-w-[90px]">${d.name || ''}</div>
                                </div>
                                <div class="text-[9px] text-textMuted/40 font-mono pt-0.5">${d.date || ''}</div>
                            </div>
                            <div class="grid grid-cols-3 gap-1">
                                <div class="bg-background/60 rounded-xl px-1 py-2 text-center min-w-0">
                                    <div class="text-[9px] text-textMuted uppercase tracking-normal whitespace-nowrap mb-1 font-bold">P/E</div>
                                    <div class="font-black text-sm ${peColor} font-mono">${pe > 0 ? pe.toFixed(1) : '—'}</div>
                                </div>
                                <div class="bg-background/60 rounded-xl px-1 py-2 text-center min-w-0">
                                    <div class="text-[9px] text-textMuted uppercase tracking-normal whitespace-nowrap mb-1 font-bold">${_t('marketPage.yield')}</div>
                                    <div class="font-black text-sm ${dyColor} font-mono">${dy > 0 ? dy.toFixed(2) + '%' : '—'}</div>
                                </div>
                                <div class="bg-background/60 rounded-xl px-1 py-2 text-center min-w-0">
                                    <div class="text-[9px] text-textMuted uppercase tracking-normal whitespace-nowrap mb-1 font-bold">P/B</div>
                                    <div class="font-black text-sm text-secondary font-mono">${pb > 0 ? pb.toFixed(2) : '—'}</div>
                                </div>
                            </div>
                        </div>
                    </div>`;
                }).join('')
                : '<p class="text-textMuted text-xs italic text-center py-6 opacity-50 col-span-full">' + _t('twstock.noValuationData') + '</p>';
            window.AppCache?.setWithTime(cacheKey, html, 30 * 60 * 1000); // 快取 30 分鐘
            container.innerHTML = html;
            this._appendSectionRefreshBar(container, cacheKey,
                () => window.TWStockTab._loadPESection(true));
        } catch (err) {
            console.error('[TW Info] PE error:', err);
            container.innerHTML = `<p class="text-danger text-xs text-center py-4 col-span-full">${_t('twstock.peLoadFailed')}${SecurityUtils.escapeHTML(err.message || '')}</p>`;
        } finally {
            this._showLoader('twstock-info-pe-loader', false);
        }
    },

    _loadDividendSection: async function (forceRefresh = false) {
        const container = document.getElementById('twstock-info-dividend');
        if (!container) return;
        const symbols = AppStore.get('twStockSelectedSymbols') || [];
        const cacheKey = 'tw_dividend_' + symbols.join(',');

        if (!forceRefresh && window.AppCache?.has(cacheKey)) {
            container.innerHTML = window.AppCache.get(cacheKey);
            AppUtils.refreshIcons();
            this._appendSectionRefreshBar(container, cacheKey,
                () => window.TWStockTab._loadDividendSection(true));
            return;
        }

        this._showLoader('twstock-info-div-loader', true);
        try {
            let url = '/api/twstock/opendata/dividend?limit=20';
            if (symbols.length > 0) {
                url += `&symbols=${encodeURIComponent(symbols.join(','))}`;
            }

            const json = await AppAPI.get(url);
            const items = (json.data || []).filter(
                (d) => d.cash_dividend && d.cash_dividend !== '' && d.cash_dividend !== '0'
            );
            const html = items.length
                ? items.slice(0, 20).map((d) => `
            <div class="bg-surface/40 border border-success/10 hover:border-success/30 rounded-xl p-3 transition-colors">
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-3">
                        <div class="w-10 h-10 rounded-xl bg-success/10 flex items-center justify-center flex-shrink-0">
                            <span class="text-success font-black text-xs font-mono">${(d.code || '—').substring(0, 4)}</span>
                        </div>
                        <div>
                            <div class="font-bold text-sm text-secondary">${d.name || d.code}</div>
                            <div class="text-[10px] text-textMuted opacity-60">${d.year || ''}${_t('twstock.yearDotSeparator')}${d.progress || ''}</div>
                        </div>
                    </div>
                    <div class="text-right">
                        <div class="font-black text-success text-base">$${d.cash_dividend || '—'}</div>
                        <div class="text-[10px] text-textMuted opacity-60">${_t('twstock.cashDividendPerShare')}</div>
                    </div>
                </div>
                ${d.shareholder_meeting ? `<div class="mt-2 pt-2 border-t border-borderSubtle text-[10px] text-textMuted flex items-center gap-1"><i data-lucide="calendar" class="w-3 h-3"></i> ${_t('twstock.shareholderMeetingLabel')}${d.shareholder_meeting}</div>` : ''}
            </div>`).join('')
                : '<p class="text-textMuted text-xs italic text-center py-6 opacity-50">' + _t('twstock.noDividendData') + '</p>';
            window.AppCache?.setWithTime(cacheKey, html, 30 * 60 * 1000); // 快取 30 分鐘
            container.innerHTML = html;
            AppUtils.refreshIcons();
            this._appendSectionRefreshBar(container, cacheKey,
                () => window.TWStockTab._loadDividendSection(true));
        } catch (err) {
            console.error('[TW Info] Dividend error:', err);
            container.innerHTML = `<p class="text-danger text-xs text-center py-4">${_t('twstock.dividendLoadFailed')}${SecurityUtils.escapeHTML(err.message || '')}</p>`;
        } finally {
            this._showLoader('twstock-info-div-loader', false);
        }
    },

    _loadForeignSection: async function (forceRefresh = false) {
        const container = document.getElementById('twstock-info-foreign');
        if (!container) return;
        const cacheKey = 'tw_foreign_holding';

        if (!forceRefresh && window.AppCache?.has(cacheKey)) {
            container.innerHTML = window.AppCache.get(cacheKey);
            this._appendSectionRefreshBar(container, cacheKey,
                () => window.TWStockTab._loadForeignSection(true));
            return;
        }

        this._showLoader('twstock-info-foreign-loader', true);
        try {
            const json = await AppAPI.get('/api/twstock/opendata/foreign_holding');
            const items = json.data || [];
            const tableHtml = !items.length
                ? '<p class="text-textMuted text-xs italic text-center py-6 opacity-50">' + _t('twstock.noForeignData') + '</p>'
                : `<table class="w-full text-xs">
                    <thead>
                        <tr class="text-textMuted uppercase tracking-wider border-b border-borderLight">
                            <th class="text-left py-2 px-2 font-bold opacity-60">${_t('twstock.rankCol')}</th>
                            <th class="text-left py-2 px-2 font-bold opacity-60">${_t('twstock.symbolCol')}</th>
                            <th class="text-left py-2 px-2 font-bold opacity-60">${_t('twstock.nameCol')}</th>
                            <th class="text-right py-2 px-2 font-bold opacity-60">${_t('twstock.heldPct')}</th>
                            <th class="text-right py-2 px-2 font-bold opacity-60 hidden sm:table-cell">${_t('twstock.availablePct')}</th>
                            <th class="text-right py-2 px-2 font-bold opacity-60 hidden sm:table-cell">${_t('twstock.upperLimitPct')}</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-borderSubtle">
                        ${items.map((d, i) => {
                            const pct = parseFloat(d.held_pct) || 0;
                            const barW = Math.min(100, pct);
                            return `<tr class="hover:bg-surfaceHighlight transition-colors">
                                <td class="py-2 px-2 text-textMuted font-mono">${d.rank || i + 1}</td>
                                <td class="py-2 px-2 font-mono text-primary font-bold">${d.code}</td>
                                <td class="py-2 px-2 text-secondary truncate max-w-[80px]">${d.name}</td>
                                <td class="py-2 px-2 text-right">
                                    <div class="flex items-center justify-end gap-1.5">
                                        <div class="w-12 h-1.5 bg-surfaceHighlight rounded-full overflow-hidden hidden sm:block">
                                            <div class="h-full bg-accent rounded-full" style="width:${barW}%"></div>
                                        </div>
                                        <span class="font-bold text-accent">${pct.toFixed(1)}%</span>
                                    </div>
                                </td>
                                <td class="py-2 px-2 text-right text-textMuted hidden sm:table-cell">${d.available_pct || '—'}%</td>
                                <td class="py-2 px-2 text-right text-textMuted hidden sm:table-cell">${d.upper_limit_pct || '—'}%</td>
                            </tr>`;
                        }).join('')}
                    </tbody>
                </table>`;
            window.AppCache?.setWithTime(cacheKey, tableHtml, 30 * 60 * 1000); // 快取 30 分鐘
            container.innerHTML = tableHtml;
            this._appendSectionRefreshBar(container, cacheKey,
                () => window.TWStockTab._loadForeignSection(true));
        } catch (err) {
            console.error('[TW Info] Foreign holding error:', err);
            container.innerHTML = `<p class="text-danger text-xs text-center py-4">${_t('twstock.foreignLoadFailed')}${SecurityUtils.escapeHTML(err.message || '')}</p>`;
        } finally {
            this._showLoader('twstock-info-foreign-loader', false);
        }
    },
};

async function initTwStock() {
    console.log('[TW Stock] initTwStock called');
    if (window.Components && !window.Components.isInjected('twstock')) {
        await window.Components.inject('twstock');
    }

    const marketBtn = document.getElementById('twstock-btn-market');
    const pulseBtn = document.getElementById('twstock-btn-pulse');

    // Bind click events if not already done
    if (marketBtn && !marketBtn.dataset.bound) {
        marketBtn.addEventListener('click', () => window.TWStockTab.switchSubTab('market'));
        marketBtn.dataset.bound = 'true';
    }

    if (pulseBtn && !pulseBtn.dataset.bound) {
        pulseBtn.addEventListener('click', () => window.TWStockTab.switchSubTab('pulse'));
        pulseBtn.dataset.bound = 'true';
    }

    window.TWStockTab.bindEvents();

    // Refresh current sub-tab data (avoid switchSubTab early-return when already on same tab)
    window.TWStockTab.refreshCurrent(true);
}

// Global exposure
window.initTwStock = initTwStock;
