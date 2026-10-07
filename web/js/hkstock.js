/**
 * HK Stock Tab — 港股看板
 * New architecture: mirrors usstock.js pattern
 * Component HTML: tab-hkstock.js → window.Components.hkstock
 */

window.HKStockTab = {
    activeSubTab: 'market',
    lastUpdatedAt: null,

    // ── Stock name helper ─────────────────────────────────────────────────────
    getStockName(item) {
        const lang = window.I18n?.getLanguage?.() || 'zh-TW';
        if (lang === 'en') return item.name_en || item.name || '';
        return item.name_zh || item.name || '';
    },

    // ── Symbols ──────────────────────────────────────────────────────────────
    defaultSymbols: ['0700.HK', '9988.HK', '0005.HK', '0388.HK', '3690.HK', '2318.HK', '0939.HK', '2800.HK'],

    AVAILABLE_SYMBOLS: [
        { symbol: '0700.HK', name_zh: '騰訊',         name_en: 'Tencent',          group: '科技/互聯網' },
        { symbol: '9988.HK', name_zh: '阿里巴巴',     name_en: 'Alibaba',          group: '科技/互聯網' },
        { symbol: '3690.HK', name_zh: '美團',         name_en: 'Meituan',          group: '科技/互聯網' },
        { symbol: '9618.HK', name_zh: '京東',         name_en: 'JD.com',           group: '科技/互聯網' },
        { symbol: '0992.HK', name_zh: '聯想',         name_en: 'Lenovo',           group: '科技/互聯網' },
        { symbol: '0005.HK', name_zh: '匯豐',         name_en: 'HSBC',             group: '金融' },
        { symbol: '0939.HK', name_zh: '建設銀行',     name_en: 'CCB',              group: '金融' },
        { symbol: '1398.HK', name_zh: '工商銀行',     name_en: 'ICBC',             group: '金融' },
        { symbol: '3988.HK', name_zh: '中國銀行',     name_en: 'Bank of China',    group: '金融' },
        { symbol: '2318.HK', name_zh: '中國平安',     name_en: 'Ping An',          group: '金融' },
        { symbol: '0016.HK', name_zh: '新鴻基地產',   name_en: 'Sun Hung Kai',     group: '地產/基建' },
        { symbol: '0001.HK', name_zh: '長和',         name_en: 'CK Hutchison',     group: '地產/基建' },
        { symbol: '0002.HK', name_zh: '中電控股',     name_en: 'CLP Holdings',     group: '地產/基建' },
        { symbol: '0291.HK', name_zh: '華潤啤酒',     name_en: 'CR Beer',          group: '消費/娛樂' },
        { symbol: '0027.HK', name_zh: '銀河娛樂',     name_en: 'Galaxy Ent.',      group: '消費/娛樂' },
        { symbol: '1928.HK', name_zh: '金沙中國',     name_en: 'Sands China',      group: '消費/娛樂' },
        { symbol: '1177.HK', name_zh: '中國生物製藥', name_en: 'Sino Biopharm',    group: '醫療/生物' },
        { symbol: '6160.HK', name_zh: '百濟神州',     name_en: 'BeiGene',          group: '醫療/生物' },
        { symbol: '0175.HK', name_zh: '吉利汽車',     name_en: 'Geely Auto',       group: '汽車/新能源' },
        { symbol: '2015.HK', name_zh: '理想汽車',     name_en: 'Li Auto',          group: '汽車/新能源' },
        { symbol: '9866.HK', name_zh: '蔚來',         name_en: 'NIO',              group: '汽車/新能源' },
        { symbol: '0388.HK', name_zh: '港交所',       name_en: 'HKEX',             group: '交易所' },
        { symbol: '2800.HK', name_zh: '盈富基金',     name_en: 'Tracker Fund ETF', group: 'ETF' },
        { symbol: '3032.HK', name_zh: '恒生科技ETF',  name_en: 'HS Tech ETF',      group: 'ETF' },
    ],

    INDEX_SYMBOLS: ['^HSI', '^HSCE', '3032.HK'],
    INDEX_NAMES:   { '^HSI': '恒生指數', '^HSCE': '國企指數', '3032.HK': '恒生科技ETF' },
    INDEX_NAMES_EN: { '^HSI': 'Hang Seng Index', '^HSCE': 'HSCEI', '3032.HK': 'HS Tech ETF' },
    MARKET_KEY:  'hkstock',
    STORAGE_KEY: 'hkStockWatchlist',
    STORE_KEY:   'hkStockSelectedSymbols',
    CURRENCY:    'HKD',

    // ── Init ─────────────────────────────────────────────────────────────────
    init() {
        window.addEventListener('languageChanged', () => {
            if (this.activeSubTab === 'market') this.refreshMarketWatch();
        });
        if (window.MarketStatus && !this._autoRefreshBound) {
            this._autoRefreshBound = true;
            window.MarketStatus.startMarketAutoRefresh(
                this.MARKET_KEY,
                () => this.refreshCurrent(),
                () => this.lastUpdatedAt
            );
        }
        this.loadWatchlist();
        this._hydrateServerWatchlist();
        this.renderWatchlistControls();
        this.bindEvents();
        this.refreshCurrent(true);
    },

    // ── Watchlist persistence ─────────────────────────────────────────────────
    loadWatchlist() {
        try {
            const saved = localStorage.getItem(this.STORAGE_KEY);
            if (saved) {
                AppStore.set(this.STORE_KEY, JSON.parse(saved));
            } else {
                AppStore.set(this.STORE_KEY, [...this.defaultSymbols]);
                this.saveWatchlist();
            }
        } catch (e) {
            AppStore.set(this.STORE_KEY, [...this.defaultSymbols]);
        }
        window[this.STORE_KEY] = AppStore.get(this.STORE_KEY);
    },

    // 登入的人：自選存在伺服器（web/js/watchlist-sync.js）——伺服器有這個市場的清單就用伺服器的
    _hydrateServerWatchlist: function () {
        const ws = window.WatchlistSync;
        if (!ws) return;
        ws.hydrate('hk_stock', AppStore.get(this.STORE_KEY), this.defaultSymbols).then((list) => {
            if (!list || ws.sameList(list, AppStore.get(this.STORE_KEY), 'hk_stock')) return;
            AppStore.set(this.STORE_KEY, [...list]);
            window[this.STORE_KEY] = AppStore.get(this.STORE_KEY);
            try {
                localStorage.setItem(this.STORAGE_KEY, JSON.stringify(list));
            } catch (e) { /* ignore */ }
            this.renderWatchlistControls();
            this.refreshCurrent(true);
        });
    },

    saveWatchlist() {
        try {
            const syms = AppStore.get(this.STORE_KEY) || [...this.defaultSymbols];
            if (!syms.length) AppStore.set(this.STORE_KEY, [...this.defaultSymbols]);
            localStorage.setItem(this.STORAGE_KEY, JSON.stringify(AppStore.get(this.STORE_KEY)));
            if (window.WatchlistSync) window.WatchlistSync.push('hk_stock', AppStore.get(this.STORE_KEY), this.defaultSymbols);
        } catch (e) { /* ignore */ }
    },

    // ── Add / Remove ──────────────────────────────────────────────────────────
    addStock: async function(symbol) {
        if (!symbol) return;
        const sym = symbol.toUpperCase().replace(/[^A-Z0-9.^]/g, '').trim();
        if (!sym) return;
        const input = document.getElementById('hkStockAddInput');
        if (AppStore.get(this.STORE_KEY)?.includes(sym)) {
            if (input) input.value = '';
            if (window.showToast) window.showToast(window.I18n.t('stock.inWatchlist', { sym }), 'info');
            return;
        }
        const btn = input ? input.nextElementSibling : null;
        let origIcon = '';
        if (btn) { origIcon = btn.innerHTML; btn.innerHTML = '<div class="w-4 h-4 border-2 border-primary/50 border-t-primary rounded-full animate-spin"></div>'; btn.disabled = true; }
        try {
            const data = await AppAPI.get(`/api/hkstock/market?symbols=${encodeURIComponent(sym)}`);
            if (data.quotes && data.quotes.length > 0) {
                AppStore.get(this.STORE_KEY).unshift(sym);
                this.saveWatchlist();
                this.refreshMarketWatch();
                if (window.showToast) window.showToast(window.I18n.t('stock.addedSymbol', { sym }), 'success');
            } else {
                if (window.showToast) window.showToast(window.I18n.t('stock.symbolNotFound', { sym }), 'error');
            }
        } catch (e) {
            if (window.showToast) window.showToast(window.I18n.t('stock.queryFailed', { msg: e.message }), 'error');
        } finally {
            if (btn) { btn.innerHTML = origIcon; btn.disabled = false; }
            if (input) input.value = '';
        }
    },

    removeStock(symbol, event) {
        if (event) event.stopPropagation();
        const syms = (AppStore.get(this.STORE_KEY) || []).filter(s => s !== symbol);
        if (!syms.length) { if (window.showToast) window.showToast(window.I18n.t('stock.keepAtLeastOne'), 'error'); return; }
        AppStore.set(this.STORE_KEY, syms);
        this.saveWatchlist();
        this.refreshMarketWatch();
    },

    // ── Picker ────────────────────────────────────────────────────────────────
    showPicker() {
        const container = document.getElementById('hkstock-screener-controls');
        if (!container) return;
        const selected = new Set(AppStore.get(this.STORE_KEY) || this.defaultSymbols);
        const groups = {};
        this.AVAILABLE_SYMBOLS.forEach(s => {
            if (!groups[s.group]) groups[s.group] = [];
            groups[s.group].push(s);
        });
        container.innerHTML = `
            <div>
                <p class="text-xs text-textMuted mb-4">${window.I18n.t('stock.watchlistHintSimple', { market: window.I18n.t('stock.marketNames.hk') })}</p>
                ${Object.entries(groups).map(([group, syms]) => `
                    <div class="mb-4">
                        <div class="text-[10px] uppercase tracking-wider text-textMuted/50 mb-2 pl-1">${window.I18n.t('sectors.' + group, { defaultValue: group })}</div>
                        <div class="grid grid-cols-2 gap-1.5">
                            ${syms.map(s => `
                                <label class="flex items-center gap-2 bg-surface border ${selected.has(s.symbol) ? 'border-primary/40 bg-primary/5' : 'border-borderSubtle'} rounded-xl px-3 py-2 cursor-pointer hover:border-primary/30 transition">
                                    <input type="checkbox" value="${s.symbol}" ${selected.has(s.symbol) ? 'checked' : ''} class="hkstock-sym-check w-3.5 h-3.5 accent-primary">
                                    <div>
                                        <span class="text-xs font-bold text-secondary">${s.symbol.replace('.HK','')}</span>
                                        <span class="text-[10px] text-textMuted ml-1">${escapeHtml(window.I18n?.getLanguage?.() === 'en' ? s.name_en : s.name_zh)}</span>
                                    </div>
                                </label>`).join('')}
                        </div>
                    </div>`).join('')}
                <div class="flex gap-2 mt-2 pb-4">
                    <button data-click="HKStockTab.renderWatchlistControls" data-click-after="refreshMarketWatch"
                        class="flex-1 py-2.5 bg-surface border border-borderLight text-textMuted font-bold rounded-xl hover:bg-surfaceHighlight transition text-sm">${window.I18n.t('common.cancel')}</button>
                    <button data-click="HKStockTab._applyPicker"
                        class="flex-1 py-2.5 bg-primary text-background font-bold rounded-xl hover:opacity-90 transition text-sm">${window.I18n.t('stock.applySelection')}</button>
                </div>
            </div>`;
    },

    _applyPicker() {
        const checks = document.querySelectorAll('.hkstock-sym-check:checked');
        const selected = Array.from(checks).map(c => c.value);
        if (!selected.length) { if (window.showToast) showToast(window.I18n.t('stock.selectAtLeastOne'), 'error'); return; }
        AppStore.set(this.STORE_KEY, selected);
        this.saveWatchlist();
        this.renderWatchlistControls();
        this.refreshMarketWatch();
    },

    // ── Watchlist controls ────────────────────────────────────────────────────
    renderWatchlistControls() {
        const container = document.getElementById('hkstock-screener-controls');
        if (!container) return;
        container.innerHTML = `
            <div class="flex items-center gap-2 mb-4">
                <h3 class="font-bold text-secondary flex items-center gap-2 flex-shrink-0">
                    <i data-lucide="star" class="w-4 h-4 text-yellow-500"></i> My HK Stocks
                </h3>
                <button data-click="HKStockTab.showPicker" class="p-1.5 text-textMuted hover:text-primary hover:bg-surfaceHighlight rounded-lg transition" title="${window.I18n.t('common.selectSymbol')}">
                    <i data-lucide="sliders-horizontal" class="w-4 h-4"></i>
                </button>
                <div class="flex-1 min-w-0">
                    <div class="relative">
                        <input type="text" id="hkStockAddInput" placeholder="${window.I18n.t('hkstock.symbolPlaceholder')}" maxlength="12"
                            data-uppercase
                            class="w-full bg-background/50 border border-borderLight rounded-lg pl-3 pr-10 py-1.5 text-sm focus:outline-none focus:border-primary transition-colors text-textMain placeholder-textMuted/50">
                        <button data-click="HKStockTab.addStock" data-click-input="hkStockAddInput"
                            class="absolute right-1 top-1/2 -translate-y-1/2 p-1 text-textMuted hover:text-primary transition-colors hover:bg-surfaceHighlight rounded">
                            <i data-lucide="plus" class="w-4 h-4"></i>
                        </button>
                    </div>
                </div>
            </div>`;
        AppUtils.refreshIcons();
        const input = document.getElementById('hkStockAddInput');
        if (input) input.addEventListener('keypress', e => { if (e.key === 'Enter') window.HKStockTab.addStock(e.target.value); });
    },

    // ── Sub-tab switching ─────────────────────────────────────────────────────
    switchSubTab(tabId) {
        if (this.activeSubTab === tabId) return;
        const marketBtn = document.getElementById('hkstock-btn-market');
        const pulseBtn  = document.getElementById('hkstock-btn-pulse');
        const marketContent = document.getElementById('hkstock-market-content');
        const pulseContent  = document.getElementById('hkstock-pulse-content');
        if (!marketBtn || !pulseBtn || !marketContent || !pulseContent) return;

        const active   = `hkstock-sub-tab flex-1 py-2 px-4 rounded-lg font-bold text-sm transition flex items-center justify-center gap-2 bg-primary text-background shadow-md`;
        const inactive = `hkstock-sub-tab flex-1 py-2 px-4 rounded-lg font-bold text-sm transition flex items-center justify-center gap-2 text-textMuted hover:text-textMain hover:bg-surfaceHighlight`;
        [marketContent, pulseContent].forEach(el => el.classList.add('hidden'));
        [marketBtn, pulseBtn].forEach(el => el.className = inactive);

        if (tabId === 'market') {
            marketBtn.className = active;
            marketContent.classList.remove('hidden');
        } else {
            pulseBtn.className = active;
            pulseContent.classList.remove('hidden');
        }
        this.activeSubTab = tabId;
        this.refreshCurrent(true);
    },

    refreshCurrent(isFirst = false) {
        if (this.activeSubTab === 'market') {
            this.refreshMarketWatch();
            this.refreshMarketInfo();
        } else {
            const inputEl = document.getElementById('hkstockPulseSearchInput');
            const sym = inputEl ? inputEl.value.trim() : '';
            if (sym) {
                this.refreshAIPulse(sym);
            } else {
                const container = document.getElementById('hkstock-pulse-result');
                if (container) {
                    container.innerHTML = `<div class="py-20 text-center text-textMuted uppercase tracking-widest text-sm italic opacity-50 flex flex-col items-center"><i data-lucide="search" class="w-8 h-8 mb-3 opacity-50"></i>${window.I18n.t('stock.enterSymbolPrompt', { market: window.I18n.t('stock.marketNames.hk') })}</div>`;
                    container.classList.remove('hidden');
                    AppUtils.refreshIcons();
                }
            }
        }
    },

    // ── Market Watch ──────────────────────────────────────────────────────────
    refreshMarketWatch: async function() {
        const listEl   = document.getElementById('hkstock-screener-list');
        const loader   = document.getElementById('hkstock-market-loader');
        if (!listEl || !loader) return;

        if (window.MarketStatus) window.MarketStatus.markSynced(this.MARKET_KEY);
        loader.classList.remove('hidden');
        listEl.innerHTML = '';

        try {
            const syms = AppStore.get(this.STORE_KEY) || this.defaultSymbols;
            const data = await AppAPI.get(`/api/hkstock/market?symbols=${encodeURIComponent(syms.join(','))}`);
            this.lastUpdatedAt = data.last_updated || new Date().toISOString();
            if (window.MarketStatus) {
                window.MarketStatus.markSynced(this.MARKET_KEY);
                window.MarketStatus.updateMarketStatusBar(this.MARKET_KEY, this.lastUpdatedAt);
            }
            this._renderWatchlist(listEl, data.quotes || []);
        } catch (err) {
            listEl.innerHTML = `<div class="p-4 text-center text-danger bg-danger/10 rounded-xl text-sm">${window.I18n.t('stock.loadFailedMsg', { msg: SecurityUtils.escapeHTML(err.message || '') })}</div>`;
        } finally {
            loader.classList.add('hidden');
        }
    },

    _renderWatchlist(container, items) {
        if (!items.length) {
            container.innerHTML = `<p class="text-textMuted text-[10px] italic py-6 text-center opacity-50 uppercase tracking-widest">${window.I18n ? window.I18n.t('common.noData') : 'No data'}</p>`;
            return;
        }
        const frag = document.createDocumentFragment();
        items.forEach(item => {
            const sym  = escapeHtml(item.symbol);
            const name = escapeHtml(this.getStockName(item) || sym);
            const price = item.price != null ? item.price.toLocaleString(undefined, {maximumFractionDigits: 3}) : '-';
            const chg  = item.changePercent != null ? parseFloat(item.changePercent) : 0;
            const isPos = chg > 0, isNeg = chg < 0;
            const color = isPos ? 'text-success' : isNeg ? 'text-danger' : 'text-textMuted';
            const sign  = isPos ? '+' : '';
            const abbr  = sym.replace('.HK','').slice(0,4);
            const div = document.createElement('div');
            div.className = 'group bg-surface/20 hover:bg-surface/40 border border-borderSubtle rounded-2xl p-4 transition-all duration-300 cursor-pointer';
            div.onclick = () => window.HKStockTab.jumpToPulse(sym);
            div.innerHTML = `
                <div class="flex items-start gap-3">
                    <div class="w-10 h-10 rounded-xl bg-background flex items-center justify-center text-xs font-bold text-primary border border-borderSubtle group-hover:scale-110 transition-transform flex-shrink-0 mt-0.5">${abbr}</div>
                    <div class="flex-1 min-w-0">
                        <div class="flex items-start justify-between gap-2">
                            <div class="min-w-0">
                                <div class="font-bold text-sm text-secondary leading-tight">${name}</div>
                                <div class="text-[9px] text-textMuted font-bold tracking-wider uppercase opacity-60">${sym} · ${item.currency || this.CURRENCY}</div>
                            </div>
                            <div class="text-right flex-shrink-0">
                                <div class="text-sm font-black font-mono ${color}">${sign}${chg.toFixed(2)}%</div>
                                <div class="text-[9px] text-textMuted uppercase opacity-40 font-bold">${window.MarketUI.todayLabel()}</div>
                            </div>
                        </div>
                        <div class="flex items-center justify-between mt-1.5">
                            <div class="text-[11px] text-textMuted font-mono opacity-80">${price} ${item.currency || this.CURRENCY}</div>
                            <div class="flex items-center gap-1">
                                <button data-click="openAlert" data-click-args="${encodeURIComponent(JSON.stringify([sym, 'hk_stock']))}" data-click-stop class="w-7 h-7 rounded-lg flex items-center justify-center text-yellow-400 hover:text-yellow-300 hover:bg-yellow-400/10 transition-colors border border-borderSubtle" title="${window.I18n.t('settings.watchlist.setAlert')}"><span class="text-xs leading-none">🔔</span></button>
                                <button data-click="HKStockTab.removeStock" data-click-arg="${encodeURIComponent(sym)}" data-click-event class="w-7 h-7 rounded-lg flex items-center justify-center text-textMuted hover:text-danger hover:bg-danger/10 transition-colors border border-borderSubtle" title="${window.I18n.t('common.remove')}">
                                    <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
                                </button>
                            </div>
                        </div>
                        ${window.MarketUI.quoteExtrasHtml(item)}
                    </div>
                </div>`;
            frag.appendChild(div);
        });
        container.innerHTML = '';
        container.appendChild(frag);
        AppUtils.refreshIcons();
    },

    jumpToPulse(symbol) {
        const inputEl = document.getElementById('hkstockPulseSearchInput');
        if (inputEl) inputEl.value = symbol;
        if (this.activeSubTab === 'pulse') this.refreshAIPulse(symbol);
        else this.switchSubTab('pulse');
    },

    // ── Market Info (indices + reference) ────────────────────────────────────
    refreshMarketInfo: async function() {
        window.MarketUI.mountSource('hkstock', 'hk');
        this.initSectionToggles();
        await Promise.all([this._loadIndicesSection(), this._loadNewsSection()]);
    },

    _showLoader(id, show) {
        const el = document.getElementById(id);
        if (!el) return;
        el.classList.toggle('hidden', !show);
        el.classList.toggle('flex', show);
    },

    _loadIndicesSection: async function() {
        const container = document.getElementById('hkstock-info-indices');
        if (!container) return;
        this._showLoader('hkstock-info-indices-loader', true);
        try {
            const data = await AppAPI.get(`/api/hkstock/market?symbols=${this.INDEX_SYMBOLS.join(',')}`);
            const stocks = data.quotes || [];
            if (!stocks.length) { container.innerHTML = '<p class="text-textMuted text-xs italic text-center py-6 opacity-50 col-span-3">' + window.I18n.t('stock.indicesNoData') + '</p>'; return; }
            window.MarketUI.fitIndexGrid(container, stocks.length);
            container.innerHTML = stocks.map(idx => {
                const chg  = idx.changePercent != null ? parseFloat(idx.changePercent) : 0;
                const color = chg >= 0 ? 'text-success' : 'text-danger';
                const arrow = chg >= 0 ? '↑' : '↓';
                const label = ((window.I18n?.getLanguage?.() || 'zh-TW') === 'en'
                    ? this.INDEX_NAMES_EN[idx.symbol]
                    : this.INDEX_NAMES[idx.symbol]) || this.INDEX_NAMES[idx.symbol] || idx.symbol;
                return `
                <div class="relative overflow-hidden rounded-2xl border border-borderSubtle bg-gradient-to-br from-surface to-background hover:border-primary/30 transition-all duration-200 group cursor-default">
                    <div class="absolute top-0 right-0 w-16 h-16 bg-primary/5 rounded-full blur-2xl group-hover:bg-primary/10 transition-all"></div>
                    <div class="relative p-4">
                        <div class="text-[10px] text-textMuted mb-2 font-bold uppercase tracking-wider">${escapeHtml(label)}</div>
                        <div class="font-black text-secondary text-lg font-mono">${idx.price != null ? idx.price.toLocaleString(undefined,{maximumFractionDigits:2}) : '—'}</div>
                        <div class="text-xs ${color} font-bold mt-1">${arrow} ${Math.abs(chg).toFixed(2)}%</div>
                    </div>
                </div>`;
            }).join('');
        } catch (err) {
            container.innerHTML = `<p class="text-danger text-xs text-center py-4 col-span-3">${window.I18n.t('stock.indicesLoadFailed')}</p>`;
        } finally {
            this._showLoader('hkstock-info-indices-loader', false);
        }
    },

    _loadNewsSection: async function() {
        const container = document.getElementById('hkstock-info-news');
        if (!container) return;
        this._showLoader('hkstock-info-news-loader', true);
        try {
            const syms = (AppStore.get(this.STORE_KEY) || this.defaultSymbols).slice(0, 5).join(',');
            const json = await AppAPI.get(`/api/hkstock/news?symbols=${encodeURIComponent(syms)}&limit=15`);
            this._showLoader('hkstock-info-news-loader', false);
            const items = json.data || [];
            if (!items.length) {
                container.innerHTML = '<p class="text-textMuted text-xs italic text-center py-6 opacity-50">' + window.I18n.t('stock.noNews', { market: window.I18n.t('stock.marketNames.hk') }) + '</p>';
                return;
            }
            container.innerHTML = items.map(item => {
                const tag  = (item.symbol || '').replace(/\.[A-Z]+$/, '').slice(0, 6);
                const pub  = item.publisher ? `<span class="text-[9px] bg-primary/10 text-primary px-1.5 py-0.5 rounded font-mono">${escapeHtml(item.publisher)}</span>` : '';
                const time = item.pub_str  ? `<span class="text-[9px] text-textMuted">${escapeHtml(item.pub_str)}</span>` : '';
                return `
                    <div class="bg-surface/40 border border-borderSubtle hover:border-primary/30 rounded-xl p-3 transition-colors">
                        <div class="flex items-start gap-3">
                            <div class="flex-shrink-0 w-10 text-center pt-0.5">
                                <div class="text-[9px] font-bold text-primary font-mono truncate">${tag}</div>
                            </div>
                            <div class="flex-1 min-w-0">
                                <a href="${sanitizeUrl(item.url)}" target="_blank" rel="noopener noreferrer"
                                   class="text-xs text-textMain leading-relaxed line-clamp-2 hover:text-primary transition-colors block">${escapeHtml(item.title)}</a>
                                <div class="flex items-center gap-2 mt-1">${pub}${time}</div>
                            </div>
                        </div>
                    </div>`;
            }).join('');
        } catch(e) {
            this._showLoader('hkstock-info-news-loader', false);
            container.innerHTML = '<p class="text-error text-xs text-center py-4">' + window.I18n.t('stock.newsLoadFailedRetry') + '</p>';
        }
    },

    // ── Section toggles ───────────────────────────────────────────────────────
    initSectionToggles() {
        try {
            const prefs = JSON.parse(localStorage.getItem('hkstock_section_prefs') || '{}');
            ['indices', 'news'].forEach(sec => {
                const isHidden = prefs[sec] === false;
                const bodyEl    = document.getElementById(`hkstock-section-body-${sec}`);
                const chevronEl = document.getElementById(`hkstock-chevron-${sec}`);
                if (bodyEl && chevronEl) {
                    bodyEl.classList.toggle('hidden', isHidden);
                    chevronEl.classList.toggle('rotate-180', isHidden);
                }
            });
        } catch (e) { /* ignore */ }
    },

    toggleSection(key) {
        const bodyEl    = document.getElementById(`hkstock-section-body-${key}`);
        const chevronEl = document.getElementById(`hkstock-chevron-${key}`);
        if (!bodyEl || !chevronEl) return;
        const wasHidden = bodyEl.classList.contains('hidden');
        bodyEl.classList.toggle('hidden', !wasHidden);
        chevronEl.classList.toggle('rotate-180', !wasHidden);
        try {
            const prefs = JSON.parse(localStorage.getItem('hkstock_section_prefs') || '{}');
            prefs[key] = wasHidden;
            localStorage.setItem('hkstock_section_prefs', JSON.stringify(prefs));
        } catch (e) { /* ignore */ }
    },

    // ── AI 分析結果 localStorage 持久化（per-user per-symbol，保留 7 天）──────
    _aiLocalKey(symbol) {
        const u = window.AuthManager?.currentUser;
        const uid = (u?.user_id || u?.uid || 'anon').replace(/[^a-zA-Z0-9_-]/g, '_');
        return `ai_deep_hk_${uid}_${symbol.toUpperCase()}_${(window.I18n?.getLanguage?.() || localStorage.getItem('selectedLanguage') || 'zh-TW')}`;
    },
    _saveAILocal(symbol, data) {
        try {
            localStorage.setItem(this._aiLocalKey(symbol), JSON.stringify({ data, savedAt: Date.now() }));
        } catch (_) { /* storage full / private mode — silently skip */ }
    },
    _loadAILocal(symbol) {
        try {
            const raw = localStorage.getItem(this._aiLocalKey(symbol));
            if (!raw) return null;
            const { data, savedAt } = JSON.parse(raw);
            // 超過 7 天自動清除
            if (Date.now() - savedAt > 7 * 24 * 60 * 60 * 1000) {
                localStorage.removeItem(this._aiLocalKey(symbol));
                return null;
            }
            return data;
        } catch (_) { return null; }
    },

    // ── AI Pulse ──────────────────────────────────────────────────────────────
    refreshAIPulse: async function(symbol) {
        const container = document.getElementById('hkstock-pulse-result');
        const loader    = document.getElementById('hkstock-pulse-loader');
        if (!container || !loader) return;
        const cacheKey = 'hkstock_pulse_' + symbol.toUpperCase();

        // 1) 記憶體快取 → 直接顯示，不打 API
        const cached = window.AppCache?.get(cacheKey);
        if (cached) {
            loader.classList.add('hidden');
            const userProvider = await window.APIKeyManager?.getCurrentProvider();
            this._renderAIPulse(container, cached, !!userProvider);
            this._appendPulseRefreshBar(container, symbol, cacheKey);
            container.classList.remove('hidden');
            return;
        }

        // 2) localStorage 持久化快取（含 ${window.I18n.t('stock.aiDeepAnalysis')}）→ 先顯示上次結果，同時背景更新基礎數據
        const _localResult = this._loadAILocal(symbol);
        if (_localResult) {
            const { data: localAI, savedAt: _localSavedAt } = _localResult;
            if (!localAI.cached_at) localAI.cached_at = new Date(_localSavedAt).toISOString();
            if (window.AppCache) window.AppCache.savedAt[cacheKey] = _localSavedAt;
            loader.classList.add('hidden');
            const userProvider = await window.APIKeyManager?.getCurrentProvider();
            this._renderAIPulse(container, localAI, !!userProvider);
            this._appendPulseRefreshBar(container, symbol, cacheKey);
            container.classList.remove('hidden');
            return;
        }

        loader.classList.remove('hidden');
        container.classList.add('hidden');
        try {
            // 基礎數據（不含 AI）
            const url  = `/api/hkstock/pulse/${encodeURIComponent(symbol.toUpperCase())}`;
            const data = await AppAPI.get(url);
            window.AppCache?.setWithTime(cacheKey, data, 15 * 60 * 1000); // 快取 15 分鐘
            const userProvider = await window.APIKeyManager?.getCurrentProvider();
            this._renderAIPulse(container, data, !!userProvider);
            this._appendPulseRefreshBar(container, symbol, cacheKey);
            container.classList.remove('hidden');
        } catch (err) {
            container.innerHTML = `<div class="p-4 text-center text-danger bg-danger/10 rounded-xl text-sm">${window.I18n.t('stock.loadFailedMsg', { msg: SecurityUtils.escapeHTML(err.message || '') })}</div>`;
            container.classList.remove('hidden');
        } finally {
            loader.classList.add('hidden');
        }
    },

    _appendPulseRefreshBar: function(container, symbol, cacheKey) {
        const timeStr = window.AppCache?.getTimeStr(cacheKey) || '';
        const safeSym = symbol.replace(/'/g, "\\'");
        const bar = document.createElement('div');
        bar.className = 'flex items-center justify-between px-4 py-2 mt-2 rounded-xl bg-surfaceHighlight border border-borderLight text-xs text-textMuted';
        bar.innerHTML = `
            <span>${timeStr ? window.I18n.t('stock.cachedAt', { time: timeStr }) : window.I18n.t('stock.cachedShort')}</span>
            <button class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary/10 text-primary hover:bg-primary/20 border border-primary/20 transition"
                    data-click="clearCacheAndInvoke" data-cache-key="${encodeURIComponent(cacheKey)}" data-target-action="HKStockTab.runDeepAnalysis" data-click-args="${encodeURIComponent(JSON.stringify([symbol, true]))}">
                <i data-lucide="zap" class="w-3 h-3"></i> ${window.I18n.t('stock.aiDeepAnalysis')}
            </button>`;
        container.appendChild(bar);
        AppUtils.refreshIcons();
    },

    runDeepAnalysis: async function(symbol, forceRefresh) {
        if (!symbol) {
            const inputEl = document.getElementById('hkstockPulseSearchInput');
            symbol = inputEl ? inputEl.value.trim() : '';
        }
        if (!symbol) return;
        const container = document.getElementById('hkstock-pulse-result');
        const loader    = document.getElementById('hkstock-pulse-loader');
        if (!container || !loader) return;
        loader.classList.remove('hidden');
        container.classList.add('hidden');
        try {
            const providerHint = window.APIKeyManager?.getSelectedProvider?.() || 'openai';
            const _force = forceRefresh ? 'true' : 'false';
            const url  = `/api/hkstock/pulse/${encodeURIComponent(symbol.toUpperCase())}?deep_analysis=true&force_refresh=${_force}&lang=${encodeURIComponent(window.I18n?.getLanguage?.() || localStorage.getItem('selectedLanguage') || 'zh-TW')}`;
            const data = await AppAPI.get(url, { headers: { 'X-User-LLM-Provider': providerHint }, timeout: 120000 });
            // 更新記憶體快取（15 分鐘）+ localStorage 持久化（7 天）
            window.AppCache?.setWithTime('hkstock_pulse_' + symbol.toUpperCase(), data, 15 * 60 * 1000);
            this._saveAILocal(symbol, data);
            this._renderAIPulse(container, data, true);
            container.classList.remove('hidden');
        } catch (err) {
            container.innerHTML = `<div class="p-4 text-center text-danger bg-danger/10 rounded-xl text-sm">${SecurityUtils.escapeHTML(err.message || (window.I18n?.t('chat.analysisFailed')) || 'Analysis failed')}</div>`;
            container.classList.remove('hidden');
        } finally {
            loader.classList.add('hidden');
        }
    },

    _renderAIPulse(container, data, hasKey) {
        const rep  = data.report || {};
        const tech = data.technical_indicators || {};
        const fund = data.fundamentals || {};
        const news = data.news || [];
        const chg  = data.change_24h || 0;
        const isPos = chg > 0, isNeg = chg < 0;
        const color = isPos ? 'text-success' : isNeg ? 'text-danger' : 'text-textMuted';
        const bg    = isPos ? 'bg-success/10' : isNeg ? 'bg-danger/10' : 'bg-surfaceHighlight';
        const sign  = isPos ? '+' : '';
        const icon  = isPos ? 'trending-up' : isNeg ? 'trending-down' : 'minus';
        const currency = data.currency || this.CURRENCY;
        const fv = (v, d=2) => (v != null && !isNaN(Number(v))) ? Number(v).toFixed(d) : 'N/A';

        // RSI
        const rsiVal = tech.rsi;
        const rsiSig = rsiVal != null ? (rsiVal > 70 ? 'overbought' : rsiVal < 30 ? 'oversold' : 'neutral') : '';
        const rsiColor = rsiSig === 'oversold' ? 'text-success' : rsiSig === 'overbought' ? 'text-danger' : 'text-secondary';
        const rsiLabelMap = { oversold: [window.I18n.t('stock.oversold'), 'bg-success/20 text-success'], overbought: [window.I18n.t('stock.overbought'), 'bg-danger/20 text-danger'], neutral: [window.I18n.t('stock.neutral'), 'bg-surfaceHighlight text-textMuted'] };
        const [rsiLabelTxt, rsiLabelStyle] = rsiLabelMap[rsiSig] || ['', ''];

        // 52W
        const low52  = tech['52w_low'];
        const high52 = tech['52w_high'];
        const close  = data.current_price || 0;
        let w52Html  = '<div class="text-xs text-textMuted">N/A</div>';
        if (low52 && high52 && close && high52 > low52) {
            const pct = Math.min(100, Math.max(0, ((close - low52) / (high52 - low52)) * 100));
            w52Html = `
                <div class="flex justify-between text-[9px] text-textMuted mb-1">
                    <span>${window.I18n.t('stock.low52w')} ${fv(low52)} ${currency}</span><span>${window.I18n.t('stock.nowPosition')} ${pct.toFixed(0)}%</span><span>${window.I18n.t('stock.high52w')} ${fv(high52)} ${currency}</span>
                </div>
                <div class="h-1.5 rounded-full bg-surfaceHighlight overflow-hidden">
                    <div class="h-full rounded-full bg-gradient-to-r from-danger via-yellow-500 to-success" style="width:${pct}%"></div>
                </div>`;
        }

        container.innerHTML = `
            <!-- Hero -->
            <div class="relative overflow-hidden bg-surface/80 backdrop-blur-xl rounded-3xl p-6 mb-6 border border-borderLight shadow-2xl shadow-black/20">
                <div class="absolute -top-24 -right-24 w-48 h-48 bg-primary/20 rounded-full blur-3xl opacity-50"></div>
                <div class="relative z-10 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                    <div class="flex items-center gap-5">
                        <div class="w-16 h-16 rounded-2xl bg-gradient-to-br from-primary/20 to-primary/5 border border-primary/20 flex items-center justify-center shadow-inner">
                            <i data-lucide="landmark" class="w-8 h-8 text-primary"></i>
                        </div>
                        <div>
                            <div class="flex items-center gap-2 mb-1">
                                <h2 class="text-xl md:text-2xl font-serif text-secondary font-bold">${escapeHtml(data.name || data.symbol)}</h2>
                                <span class="px-2 py-0.5 rounded text-xs font-bold tracking-wider uppercase bg-surfaceHighlight text-textMain border border-borderLight">${escapeHtml(data.symbol)}</span>
                            </div>
                            <div class="text-xs text-textMuted flex items-center gap-2">
                                <i data-lucide="map-pin" class="w-3 h-3"></i> Hong Kong Stock Exchange
                            </div>
                        </div>
                    </div>
                    <div class="mt-2 md:mt-0 ml-20 md:ml-0 flex flex-col items-start md:items-end">
                        <div class="text-[10px] text-textMuted uppercase tracking-[0.2em] mb-1 font-bold"data-i18n="stock.currentPrice">Current Price</div>
                        <div class="text-4xl font-mono font-black text-secondary tracking-tight mb-2">${data.current_price != null ? data.current_price.toLocaleString(undefined,{maximumFractionDigits:3}) : '—'} <span class="text-sm text-textMuted font-serif font-normal">${currency}</span></div>
                        <div class="inline-flex items-center gap-1.5 text-sm font-bold ${color} ${bg} px-3 py-1 rounded-lg border border-borderSubtle backdrop-blur-md shadow-sm">
                            <i data-lucide="${icon}" class="w-4 h-4"></i>
                            <span>${sign}${chg.toFixed(2)}% (24h)</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- AI Summary -->
            <div class="bg-surface/60 backdrop-blur-md border border-primary/20 rounded-2xl p-6 shadow-lg relative overflow-hidden mb-6">
                <div class="absolute top-0 left-0 w-1 h-full bg-gradient-to-b from-primary via-accent to-primary"></div>
                <h3 class="font-serif text-lg text-primary mb-4 flex items-center gap-3 ml-3">
                    <div class="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center">
                        <i data-lucide="brain-circuit" class="w-4 h-4 text-primary"></i>
                    </div>
                    ${_t('stock.pulseAiSummary')}
                </h3>
                <div class="md:ml-11">
                    ${window.renderAIAnalysisSection({
                        data: data,
                        tabName: 'HKStockTab',
                        hasKey: hasKey,
                        settingsBtn: "switchTab('settings')",
                        _t: _t,
                    })}
                </div>
            </div>

            <!-- Technical -->
            <div class="bg-surface/40 backdrop-blur-sm border border-borderSubtle rounded-2xl p-6 mb-6">
                <h3 class="font-bold text-secondary mb-5 flex items-center gap-2 text-sm uppercase tracking-wider">
                    <i data-lucide="activity" class="w-4 h-4 text-accent"></i> Technical Indicators
                </h3>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <!-- RSI -->
                    <div class="bg-background/80 rounded-xl p-4 border border-borderSubtle">
                        <div class="text-[10px] text-textMuted uppercase tracking-wider mb-2">RSI (14)</div>
                        <div class="flex items-end justify-between mb-2">
                            <span class="text-2xl font-black font-mono ${rsiColor}">${rsiVal != null ? Number(rsiVal).toFixed(1) : 'N/A'}</span>
                            ${rsiLabelTxt ? `<span class="text-[10px] font-bold px-2 py-0.5 rounded-full ${rsiLabelStyle}">${rsiLabelTxt}</span>` : ''}
                        </div>
                        ${rsiVal != null ? `<div class="h-1.5 rounded-full bg-surfaceHighlight overflow-hidden"><div class="h-full rounded-full" style="width:${Math.min(100,Number(rsiVal))}%;background:${Number(rsiVal)<30?'#86efac':Number(rsiVal)>70?'#fda4af':'#a1a1aa'}"></div></div>` : ''}
                    </div>
                    <!-- 52W Range -->
                    <div class="bg-background/80 rounded-xl p-4 border border-borderSubtle">
                        <div class="text-[10px] text-textMuted uppercase tracking-wider mb-2">52-Week Range</div>
                        ${w52Html}
                    </div>
                    <!-- MACD -->
                    ${(() => {
                        const macdVal = tech.macd_histogram;
                        if (macdVal == null) return '';
                        const macdSig = macdVal > 0 ? [window.I18n.t('stock.bullish'), 'bg-success/20 text-success'] : [window.I18n.t('stock.bearish'), 'bg-danger/20 text-danger'];
                        return `<div class="bg-background/80 rounded-xl p-4 border border-borderSubtle">
                            <div class="text-[10px] text-textMuted uppercase tracking-wider mb-2"data-i18n="stock.macdHistogram">MACD Histogram</div>
                            <div class="flex items-end justify-between mb-1">
                                <span class="text-2xl font-black font-mono ${macdVal > 0 ? 'text-success' : 'text-danger'}">${fv(macdVal)}</span>
                                <span class="text-[10px] font-bold px-2 py-0.5 rounded-full ${macdSig[1]}">${macdSig[0]}</span>
                            </div>
                        </div>`;
                    })()}
                    <!-- MA20 / MA50 -->
                    ${(() => {
                        const ma20 = tech.ma20, ma50 = tech.ma50;
                        if (ma20 == null && ma50 == null) return '';
                        const pct = (ma) => ma != null && close ? ((close - ma) / ma * 100) : null;
                        const badge = (p) => p != null ? `<span class="text-[9px] font-bold px-1.5 py-0.5 rounded-full ${p >= 0 ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger'}">${p >= 0 ? '+' : ''}${p.toFixed(1)}%</span>` : '';
                        return `<div class="bg-background/80 rounded-xl p-4 border border-borderSubtle col-span-2 sm:col-span-1">
                            <div class="text-[10px] text-textMuted uppercase tracking-wider mb-2">MA20 / MA50</div>
                            <div class="flex flex-col gap-1.5">
                                ${ma20 != null ? `<div class="flex items-center justify-between"><span class="text-xs text-textMuted">MA20</span><div class="flex items-center gap-1.5"><span class="text-sm font-bold font-mono text-secondary">${fv(ma20, 3)}</span>${badge(pct(ma20))}</div></div>` : ''}
                                ${ma50 != null ? `<div class="flex items-center justify-between"><span class="text-xs text-textMuted">MA50</span><div class="flex items-center gap-1.5"><span class="text-sm font-bold font-mono text-secondary">${fv(ma50, 3)}</span>${badge(pct(ma50))}</div></div>` : ''}
                            </div>
                        </div>`;
                    })()}
                </div>
            </div>

            <!-- Fundamentals -->
            ${(() => {
                const hasFund = fund.target_price != null || fund.beta != null ||
                                fund.dividend_yield != null || fund.eps != null ||
                                fund.revenue_growth != null || fund.recommendation;
                if (!hasFund) return '';
                const upside = (fund.target_price != null && close) ? ((fund.target_price - close) / close * 100) : null;
                const recLabelMap = {
                    'strong_buy': [window.I18n.t('stock.strongBuy'),'bg-success/30 text-success'], 'buy': [window.I18n.t('stock.buy'),'bg-success/20 text-success'],
                    'hold': [window.I18n.t('stock.hold'),'bg-surfaceHighlight text-textMuted'], 'underperform': [window.I18n.t('stock.underperform'),'bg-danger/20 text-danger'],
                    'sell': [window.I18n.t('stock.sell'),'bg-danger/20 text-danger'], 'strong_sell': [window.I18n.t('stock.strongSell'),'bg-danger/30 text-danger'],
                };
                const recEntry = recLabelMap[fund.recommendation] || (fund.recommendation ? [fund.recommendation.toUpperCase(),'bg-surfaceHighlight text-textMuted'] : null);
                const betaSig = fund.beta != null ? (fund.beta > 1.5 ? [window.I18n.t('stock.highVolatility'),'bg-danger/20 text-danger'] : fund.beta < 0.8 ? [window.I18n.t('stock.lowVolatility'),'bg-success/20 text-success'] : [window.I18n.t('stock.moderate'),'bg-surfaceHighlight text-textMuted']) : null;
                const cell = (label, val, badge) => `<div class="bg-background/60 rounded-xl p-3 flex flex-col gap-1.5">
                    <span class="text-[10px] text-textMuted uppercase tracking-wider">${label}</span>
                    <span class="text-base font-black font-mono text-secondary">${val}</span>
                    ${badge ? `<span class="text-[9px] font-bold px-1.5 py-0.5 rounded-full w-fit ${badge[1]}">${badge[0]}</span>` : ''}
                </div>`;
                const cells = [
                    fund.target_price != null ? cell(window.I18n.t('stock.analystTarget'), fv(fund.target_price, 3), upside != null ? [`${upside >= 0 ? '+' : ''}${upside.toFixed(1)}%`, upside >= 0 ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger'] : null) : '',
                    recEntry ? cell(window.I18n.t('stock.analystRating'), recEntry[0], null) : '',
                    fund.beta != null ? cell('Beta', String(fund.beta), betaSig) : '',
                    fund.dividend_yield != null ? cell(window.I18n.t('stock.dividendYield'), `${fund.dividend_yield}%`, null) : '',
                    fund.eps != null ? cell('EPS', fv(fund.eps, 3), null) : '',
                    fund.revenue_growth != null ? cell(window.I18n.t('stock.revenueGrowth'), `${fund.revenue_growth >= 0 ? '+' : ''}${fund.revenue_growth}%`, fund.revenue_growth >= 0 ? [window.I18n.t('stock.growth'),'bg-success/20 text-success'] : [window.I18n.t('stock.decline'),'bg-danger/20 text-danger']) : '',
                ].filter(Boolean).join('');
                const sectorLine = fund.sector ? `<div class="text-[10px] text-textMuted mt-2 pt-2 border-t border-borderSubtle flex items-center gap-1.5"><i data-lucide="building-2" class="w-3 h-3 shrink-0"></i><span>${fund.sector}${fund.industry ? ` · ${fund.industry}` : ''}</span></div>` : '';
                return `<div class="bg-surface/40 backdrop-blur-sm border border-borderSubtle rounded-2xl p-6 mb-6">
                    <h3 class="font-bold text-secondary mb-4 flex items-center gap-2 text-sm uppercase tracking-wider">
                        <i data-lucide="bar-chart-2" class="w-4 h-4 text-accent"></i> ${window.I18n.t('stock.fundamentals')}
                    </h3>
                    <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">${cells}</div>
                    ${sectorLine}
                </div>`;
            })()}

            <!-- News -->
            ${news.length > 0 ? `
            <div class="bg-surface/40 backdrop-blur-sm border border-borderSubtle rounded-2xl p-6 mb-6">
                <h3 class="font-bold text-secondary mb-4 flex items-center gap-2 text-sm uppercase tracking-wider">
                    <i data-lucide="rss" class="w-4 h-4 text-accent"></i> ${window.I18n.t('stock.recentNews')}
                </h3>
                <ul class="space-y-0">${news.slice(0, 4).map(n => `
                    <li class="flex items-start gap-2 py-2.5 border-b border-borderSubtle last:border-0">
                        <i data-lucide="newspaper" class="w-3 h-3 text-textMuted mt-0.5 shrink-0"></i>
                        <span class="text-xs text-textSecondary leading-relaxed">${n.url
                            ? `<a href="${sanitizeUrl(n.url)}" target="_blank" rel="noopener" class="hover:text-primary transition-colors">${escapeHtml(n.title)}</a>`
                            : escapeHtml(n.title)}</span>
                    </li>`).join('')}
                </ul>
            </div>` : ''}`;

        AppUtils.refreshIcons();
    },

    // ── Events ────────────────────────────────────────────────────────────────
    bindEvents() {
        const btn   = document.getElementById('hkstockPulseSearchBtn');
        const input = document.getElementById('hkstockPulseSearchInput');
        if (btn && input && !btn.dataset.bound) {
            btn.dataset.bound = 'true';
            btn.addEventListener('click', () => { const s = input.value.trim(); if (s) this.refreshAIPulse(s); });
            input.addEventListener('keypress', e => { if (e.key === 'Enter') { const s = input.value.trim(); if (s) this.refreshAIPulse(s); } });
        }
    },
};
