// ============================================================
// Commodity Market Tab — 大宗商品  [new architecture]
// Special: group-based display (能源/金屬/農產品)
// ============================================================

window.CommodityTab = (() => {
    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function sanitizeUrl(url) {
        if (!url) return '#';
        const s = String(url).trim();
        if (/^(https?:\/\/|\/|mailto:|tel:)/i.test(s)) return s;
        return '#';
    }

    // ── Constants ──────────────────────────────────────────
    const MARKET_KEY  = 'commodity';
    const STORAGE_KEY = 'commodityWatchlist';
    const STORE_KEY   = 'commoditySelectedSymbols';
    const SYNC_MARKET = 'commodity'; // /api/watchlist 的市場代碼
    const DEFAULT_WATCHLIST = ['GC=F', 'CL=F', 'SI=F']; // 預設清單不算使用者選的，不上傳
    const API_BASE    = '/api/commodity';

    // Commodity groups for the info section
    const COMMODITY_GROUPS = {
        energy: {
            label: '能源',
            icon:  'flame',
            symbols: ['CL=F', 'BZ=F', 'NG=F', 'RB=F'],
        },
        metals: {
            label: '金屬',
            icon:  'gem',
            symbols: ['GC=F', 'SI=F', 'PL=F', 'HG=F'],
        },
        agri: {
            label: '農產品',
            icon:  'wheat',
            symbols: ['ZW=F', 'ZC=F', 'ZS=F', 'KC=F'],
        },
    };

    const AVAILABLE_SYMBOLS = [
        { symbol: 'GC=F' }, { symbol: 'SI=F' }, { symbol: 'CL=F' }, { symbol: 'BZ=F' },
        { symbol: 'NG=F' }, { symbol: 'ZW=F' }, { symbol: 'ZC=F' }, { symbol: 'ZS=F' },
        { symbol: 'PL=F' }, { symbol: 'HG=F' }, { symbol: 'KC=F' }, { symbol: 'SB=F' },
    ];

    // symbol → i18n key（commodity.*）——顯示名稱唯一來源。此前名稱寫死中英
    // 混合字串，UI 切英文仍顯示中文開頭（2026-09-10 DANNY 回報）。
    const SYMBOL_I18N_KEYS = {
        'GC=F': 'gold', 'SI=F': 'silver', 'PL=F': 'platinum', 'HG=F': 'copper',
        'CL=F': 'wtiCrude', 'BZ=F': 'brentCrude', 'NG=F': 'naturalGas', 'RB=F': 'gasoline',
        'ZW=F': 'wheat', 'ZC=F': 'corn', 'ZS=F': 'soybean', 'KC=F': 'coffee', 'SB=F': 'sugar',
    };
    const SYMBOL_FALLBACK_NAMES = {
        'GC=F': 'Gold', 'SI=F': 'Silver', 'PL=F': 'Platinum', 'HG=F': 'Copper',
        'CL=F': 'WTI Crude Oil', 'BZ=F': 'Brent Crude Oil', 'NG=F': 'Natural Gas', 'RB=F': 'Gasoline',
        'ZW=F': 'Wheat', 'ZC=F': 'Corn', 'ZS=F': 'Soybean', 'KC=F': 'Coffee', 'SB=F': 'Sugar',
    };

    function localizedName(symbol, fallback) {
        const key = SYMBOL_I18N_KEYS[symbol];
        if (key && window.I18n && typeof window.I18n.t === 'function') {
            const v = window.I18n.t('commodity.' + key);
            if (v && v !== 'commodity.' + key) return v;
        }
        return SYMBOL_FALLBACK_NAMES[symbol] || fallback || symbol;
    }

    // ── State ──────────────────────────────────────────────
    let state = {
        activeSubTab: 'market',
        watchlist: [],
        selectedSymbols: [],
        sectionOpen: { groups: true, news: true },
    };

    // ── Helpers ────────────────────────────────────────────
    function fmt(v, digits = 2) {
        if (v == null || isNaN(v)) return 'N/A';
        return Number(v).toFixed(digits);
    }
    function pctClass(v) {
        if (v > 0) return 'text-success';
        if (v < 0) return 'text-error';
        return 'text-textMuted';
    }
    function pctArrow(v) {
        if (v > 0) return '▲';
        if (v < 0) return '▼';
        return '–';
    }
    function getSelectedProvider() {
        return localStorage.getItem('user_selected_provider')
            || localStorage.getItem('selectedAIProvider')
            || 'openai';
    }

    // ── Watchlist persistence ──────────────────────────────
    function loadWatchlist() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            state.watchlist = raw ? JSON.parse(raw) : [...DEFAULT_WATCHLIST];
        } catch { state.watchlist = [...DEFAULT_WATCHLIST]; }
        try {
            const raw2 = localStorage.getItem(STORE_KEY);
            state.selectedSymbols = raw2 ? JSON.parse(raw2) : [...state.watchlist];
        } catch { state.selectedSymbols = [...state.watchlist]; }
    }
    function saveWatchlist() {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(state.watchlist));
        localStorage.setItem(STORE_KEY, JSON.stringify(state.selectedSymbols));
        if (window.WatchlistSync) window.WatchlistSync.push(SYNC_MARKET, state.selectedSymbols, DEFAULT_WATCHLIST);
    }

    // 登入的人：自選存在伺服器（web/js/watchlist-sync.js）——伺服器有這個市場的清單就用伺服器的
    function hydrateServerWatchlist() {
        const ws = window.WatchlistSync;
        if (!ws) return;
        ws.hydrate(SYNC_MARKET, state.selectedSymbols, DEFAULT_WATCHLIST).then((list) => {
            if (!list || ws.sameList(list, state.selectedSymbols, SYNC_MARKET)) return;
            state.selectedSymbols = [...list];
            list.forEach((s) => {
                if (!state.watchlist.includes(s)) state.watchlist.push(s);
            });
            try {
                localStorage.setItem(STORAGE_KEY, JSON.stringify(state.watchlist));
                localStorage.setItem(STORE_KEY, JSON.stringify(state.selectedSymbols));
            } catch (e) { /* ignore */ }
            renderWatchlistControls();
            refreshMarketWatch();
        });
    }
    function removeStock(symbol) {
        state.watchlist = state.watchlist.filter(s => s !== symbol);
        state.selectedSymbols = state.selectedSymbols.filter(s => s !== symbol);
        saveWatchlist();
    }

    // ── Picker modal ───────────────────────────────────────
    function showPicker() {
        const existing = document.getElementById('commodity-picker-modal');
        if (existing) existing.remove();

        const modal = document.createElement('div');
        modal.id = 'commodity-picker-modal';
        modal.className = 'fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm';
        modal.innerHTML = `
            <div class="bg-surface border border-borderLight rounded-2xl p-6 w-[360px] max-h-[80dvh] flex flex-col gap-4 shadow-2xl">
                <div class="flex items-center justify-between">
                    <h3 class="text-base font-bold text-textPrimary">${window.I18n.t('stock.selectToShow', { market: window.I18n.t('stock.marketNames.commodity') })}</h3>
                    <button data-click="removeById" data-click-arg="commodity-picker-modal" class="w-11 h-11 -m-2 flex items-center justify-center rounded-xl text-textMuted hover:text-textPrimary hover:bg-surfaceHighlight transition" aria-label="Close">
                        <i data-lucide="x" class="w-5 h-5"></i>
                    </button>
                </div>
                <div class="overflow-y-auto flex-1 space-y-1">
                    ${AVAILABLE_SYMBOLS.map(s => `
                        <label class="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-surfaceHighlight cursor-pointer">
                            <input type="checkbox" value="${s.symbol}"
                                ${state.selectedSymbols.includes(s.symbol) ? 'checked' : ''}
                                class="accent-primary w-4 h-4">
                            <span class="text-sm text-textPrimary">${localizedName(s.symbol)}</span>
                            <span class="text-xs text-textMuted ml-auto">${s.symbol}</span>
                        </label>`).join('')}
                </div>
                <button data-click="CommodityTab._applyPicker"
                    class="w-full py-2 rounded-xl bg-primary text-background font-bold text-sm hover:bg-primary/80 transition-colors">
                    ${window.I18n.t('common.confirm')}
                </button>
            </div>`;
        document.body.appendChild(modal);
        if (window.lucide) lucide.createIcons();
    }
    function _applyPicker() {
        const modal = document.getElementById('commodity-picker-modal');
        if (!modal) return;
        const checked = [...modal.querySelectorAll('input[type=checkbox]:checked')].map(el => el.value);
        state.selectedSymbols = checked;
        saveWatchlist();
        modal.remove();
        _renderWatchlist();
    }

    // ── Watchlist controls ─────────────────────────────────
    function renderWatchlistControls() {
        const container = document.getElementById('commodity-screener-controls');
        if (!container) return;
        container.innerHTML = `
            <div class="flex flex-wrap items-center gap-2 mb-3 px-1">
                <button data-click="CommodityTab.showPicker"
                    class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary/10 hover:bg-primary/20 text-primary text-xs font-semibold border border-primary/30 transition-colors">
                    <i data-lucide="settings-2" class="w-3 h-3"></i> ${window.I18n.t('stock.manageWatchlist')}
                </button>
            </div>`;
        if (window.lucide) lucide.createIcons();
    }

    // ── Sub-tab switch ─────────────────────────────────────
    function switchSubTab(tab) {
        state.activeSubTab = tab;
        ['market', 'pulse'].forEach(t => {
            const btn     = document.getElementById(`commodity-btn-${t}`);
            const content = document.getElementById(`commodity-${t}-content`);
            const active  = t === tab;
            if (btn) btn.className = `commodity-sub-tab ${SUB_TAB_BUTTON_BASE_CLASS} ${active ? SUB_TAB_BUTTON_ACTIVE_CLASS : SUB_TAB_BUTTON_INACTIVE_CLASS}`;
            if (content) content.classList.toggle('hidden', !active);
        });
        if (tab === 'market') refreshMarketWatch();
    }

    // ── Refresh ────────────────────────────────────────────
    function refreshCurrent() {
        if (state.activeSubTab === 'market') refreshMarketWatch();
        else refreshAIPulse();
    }
    function refreshMarketWatch() {
        _renderWatchlist();
        refreshMarketInfo();
    }

    // ── Watchlist render ───────────────────────────────────
    async function _renderWatchlist() {
        const list   = document.getElementById('commodity-screener-list');
        const loader = document.getElementById('commodity-market-loader');
        if (!list) return;
        renderWatchlistControls();
        if (!state.selectedSymbols.length) {
            list.innerHTML = '<p class="text-textMuted text-sm px-1">' + window.I18n.t('stock.watchlistEmpty', { market: window.I18n.t('stock.marketNames.commodity'), manage: window.I18n.t('stock.manageWatchlist') }) + '</p>';
            if (loader) loader.classList.add('hidden');
            return;
        }
        if (loader) loader.classList.remove('hidden');
        list.innerHTML = '';
        try {
            const syms = state.selectedSymbols.join(',');
            const res  = await fetch(`${API_BASE}/market?symbols=${encodeURIComponent(syms)}`);
            const data = await res.json();
            if (loader) loader.classList.add('hidden');
            lastUpdatedAt = data.last_updated || new Date().toISOString();
            if (window.MarketStatus) {
                window.MarketStatus.markSynced(MARKET_KEY);
                window.MarketStatus.updateMarketStatusBar(MARKET_KEY, lastUpdatedAt);
            }
            if (!data.quotes || !data.quotes.length) {
                list.innerHTML = '<p class="text-textMuted text-sm px-1">' + window.I18n.t('stock.quotesUnavailable') + '</p>';
                return;
            }
            list.innerHTML = data.quotes.map(q => {
                const chg   = q.changePercent ?? 0;
                const cls   = pctClass(chg);
                const arrow = pctArrow(chg);
                const known = AVAILABLE_SYMBOLS.find(s => s.symbol === q.symbol);
                const label = known ? localizedName(q.symbol) : (q.name || q.symbol);
                const abbr  = q.symbol.replace('=F','').slice(0,4);
                return `
                    <div class="group bg-surface/20 hover:bg-surface/40 border border-borderSubtle rounded-2xl p-4 transition-all duration-300 cursor-pointer"
                         data-click="CommodityTab.jumpToPulse" data-click-arg="${encodeURIComponent(q.symbol)}">
                        <div class="flex items-start gap-3">
                            <div class="w-10 h-10 rounded-xl bg-background flex items-center justify-center text-xs font-bold text-primary border border-borderSubtle group-hover:scale-110 transition-transform flex-shrink-0 mt-0.5">${abbr}</div>
                            <div class="flex-1 min-w-0">
                                <div class="flex items-start justify-between gap-2">
                                    <div class="min-w-0">
                                        <div class="text-sm font-bold text-secondary truncate">${label}</div>
                                        <div class="text-[9px] text-textMuted mt-0.5">${q.symbol}</div>
                                    </div>
                                    <div class="text-right flex-shrink-0">
                                        <div class="text-sm font-black font-mono ${cls}">${arrow} ${Math.abs(chg).toFixed(2)}%</div>
                                        <div class="text-[9px] text-textMuted">24H</div>
                                    </div>
                                </div>
                                <div class="flex items-center justify-between mt-1.5">
                                    <span class="text-[11px] text-textMuted font-mono">$${fmt(q.price)}</span>
                                    <div class="flex items-center gap-1">
                                        <button data-click="openAlert" data-click-args="${encodeURIComponent(JSON.stringify([q.symbol, 'commodity']))}" data-click-stop class="w-7 h-7 rounded-lg flex items-center justify-center text-yellow-400 hover:text-yellow-300 hover:bg-yellow-400/10 transition-colors border border-borderSubtle" title="${window.I18n.t('settings.watchlist.setAlert')}"><span class="text-xs leading-none">🔔</span></button>
                                        <button data-click="CommodityTab.removeStock" data-click-arg="${encodeURIComponent(q.symbol)}" data-click-after="_renderWatchlist" data-click-stop
                                            class="w-7 h-7 rounded-lg flex items-center justify-center text-textMuted hover:text-danger hover:bg-danger/10 transition-colors border border-borderSubtle" title="${window.I18n.t('common.remove')}">
                                            <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
                                        </button>
                                    </div>
                                </div>
                                ${window.MarketUI.quoteExtrasHtml({ ...q, volume: null })}
                            </div>
                        </div>
                    </div>`;
            }).join('');
            if (window.lucide) lucide.createIcons();
        } catch {
            if (loader) loader.classList.add('hidden');
            list.innerHTML = '<p class="text-error text-sm px-1">' + window.I18n.t('stock.loadFailedRetry') + '</p>';
        }
    }

    // ── Jump to Pulse ──────────────────────────────────────
    function jumpToPulse(symbol) {
        switchSubTab('pulse');
        const input = document.getElementById('commodityPulseSearchInput');
        if (input) { input.value = symbol; refreshAIPulse(); }
    }

    // ── Market info sections ───────────────────────────────
    async function refreshMarketInfo() {
        window.MarketUI.mountSource('commodity', 'commodity');
        _loadGroupsSection();
        _loadNewsSection();
    }

    async function _loadGroupsSection() {
        const container = document.getElementById('commodity-info-groups');
        const loader    = document.getElementById('commodity-info-groups-loader');
        if (!container) return;
        if (loader) loader.classList.remove('hidden');
        container.innerHTML = '';

        // Fetch all group symbols in one batch
        const allSymbols = Object.values(COMMODITY_GROUPS).flatMap(g => g.symbols);
        let quoteMap = {};
        try {
            const res  = await fetch(`${API_BASE}/market?symbols=${encodeURIComponent(allSymbols.join(','))}`);
            const data = await res.json();
            if (loader) loader.classList.add('hidden');
            if (data.quotes) {
                data.quotes.forEach(q => { quoteMap[q.symbol] = q; });
            }
        } catch {
            if (loader) loader.classList.add('hidden');
        }

        Object.entries(COMMODITY_GROUPS).forEach(([key, group]) => {
            const groupHtml = `
                <div class="space-y-2">
                    <div class="flex items-center gap-2 text-xs font-bold text-textMuted uppercase tracking-widest px-1">
                        <i data-lucide="${group.icon}" class="w-3 h-3 text-success"></i>
                        <span>${window.I18n.t('sectors.' + group.label, { defaultValue: group.label })}</span>
                    </div>
                    <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
                        ${group.symbols.map(sym => {
                            const q     = quoteMap[sym];
                            const price = q ? fmt(q.price) : '–';
                            const chg   = q ? (q.changePercent ?? 0) : 0;
                            const cls   = pctClass(chg);
                            const arrow = pctArrow(chg);
                            const name  = localizedName(sym, sym);
                            return `
                                <div class="group bg-surface/20 hover:bg-surface/40 border border-borderSubtle rounded-xl p-3 transition-all duration-200 cursor-pointer"
                                     data-click="CommodityTab.jumpToPulse" data-click-arg="${encodeURIComponent(sym)}">
                                    <div class="text-xs text-textMuted truncate">${name}</div>
                                    <div class="flex items-baseline justify-between gap-2 mt-0.5 whitespace-nowrap">
                                        <span class="text-sm font-bold font-mono text-textPrimary tabular-nums">${price}</span>
                                        <span class="text-xs font-bold ${cls} tabular-nums shrink-0">${arrow} ${fmt(Math.abs(chg))}%</span>
                                    </div>
                                </div>`;
                        }).join('')}
                    </div>
                </div>`;
            container.insertAdjacentHTML('beforeend', groupHtml);
        });
        if (window.lucide) lucide.createIcons();
    }

    async function _loadNewsSection() {
        const container = document.getElementById('commodity-info-news');
        const loader    = document.getElementById('commodity-info-news-loader');
        if (!container) return;
        if (loader) loader.classList.remove('hidden');
        try {
            const syms = (state.selectedSymbols.length ? state.selectedSymbols : ['GC=F','SI=F','CL=F','NG=F']).slice(0, 5).join(',');
            const res  = await fetch(`${API_BASE}/news?symbols=${encodeURIComponent(syms)}&limit=15&lang=${encodeURIComponent(window.I18n?.getLanguage?.() || 'zh-TW')}`);
            const json = await res.json();
            if (loader) loader.classList.add('hidden');
            const items = json.data || [];
            if (!items.length) {
                container.innerHTML = '<p class="text-textMuted text-xs italic text-center py-6 opacity-50">' + window.I18n.t('stock.noNews', { market: window.I18n.t('stock.marketNames.commodity') }) + '</p>';
                return;
            }
            container.innerHTML = items.map(item => {
                const tag  = (item.symbol || '').replace('=F', '').slice(0, 5);
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
            if (loader) loader.classList.add('hidden');
            container.innerHTML = '<p class="text-error text-xs text-center py-4">' + window.I18n.t('stock.newsLoadFailedRetry') + '</p>';
        }
    }

    // ── Section toggles ────────────────────────────────────
    function initSectionToggles() {
        Object.keys(state.sectionOpen).forEach(sec => {
            const body    = document.getElementById(`commodity-section-body-${sec}`);
            const chevron = document.getElementById(`commodity-chevron-${sec}`);
            if (body)    body.style.display    = state.sectionOpen[sec] ? '' : 'none';
            if (chevron) chevron.style.transform = state.sectionOpen[sec] ? '' : 'rotate(180deg)';
        });
    }
    function toggleSection(sec) {
        state.sectionOpen[sec] = !state.sectionOpen[sec];
        const body    = document.getElementById(`commodity-section-body-${sec}`);
        const chevron = document.getElementById(`commodity-chevron-${sec}`);
        if (body)    body.style.display    = state.sectionOpen[sec] ? '' : 'none';
        if (chevron) chevron.style.transform = state.sectionOpen[sec] ? '' : 'rotate(180deg)';
    }

    // ── AI Pulse ───────────────────────────────────────────

    // ── AI 分析結果 localStorage 持久化（per-user per-symbol，保留 7 天）──────
    function _aiLocalKey(symbol) {
        const u = window.AuthManager?.currentUser;
        const uid = (u?.user_id || u?.uid || 'anon').replace(/[^a-zA-Z0-9_-]/g, '_');
        return `ai_deep_commodity_${uid}_${symbol.toUpperCase()}_${(window.I18n?.getLanguage?.() || localStorage.getItem('selectedLanguage') || 'zh-TW')}`;
    }
    function _saveAILocal(symbol, data) {
        try { localStorage.setItem(_aiLocalKey(symbol), JSON.stringify({ data, savedAt: Date.now() })); } catch (_) {}
    }
    function _loadAILocal(symbol) {
        try {
            const raw = localStorage.getItem(_aiLocalKey(symbol));
            if (!raw) return null;
            const { data, savedAt } = JSON.parse(raw);
            if (Date.now() - savedAt > 7 * 24 * 60 * 60 * 1000) { localStorage.removeItem(_aiLocalKey(symbol)); return null; }
            return { data, savedAt };
        } catch (_) { return null; }
    }

    function refreshAIPulse() {
        const input = document.getElementById('commodityPulseSearchInput');
        if (!input) return;
        const symbol = input.value.trim().toUpperCase();
        if (!symbol) return;
        const loader   = document.getElementById('commodity-pulse-loader');
        const result   = document.getElementById('commodity-pulse-result');
        const cacheKey = 'commodity_pulse_' + symbol;

        // 有快取 → 直接顯示，不打 API
        const cached = window.AppCache?.get(cacheKey);
        if (cached) {
            if (loader) { loader.classList.add('hidden'); loader.style.display = 'none'; }
            if (result) {
                result.classList.remove('hidden');
                _renderAIPulse(result, cached);
                _appendPulseRefreshBar(result, symbol, cacheKey);
            }
            return;
        }

        // 2) localStorage 持久化快取 → 先顯示上次 AI 分析結果
        const _localResult = _loadAILocal(symbol);
        if (_localResult) {
            const { data: localAI, savedAt: _localSavedAt } = _localResult;
            if (!localAI.cached_at) localAI.cached_at = new Date(_localSavedAt).toISOString();
            if (window.AppCache) window.AppCache.savedAt[cacheKey] = _localSavedAt;
            if (loader) { loader.classList.add('hidden'); loader.style.display = 'none'; }
            if (result) {
                result.classList.remove('hidden');
                _renderAIPulse(result, localAI);
                _appendPulseRefreshBar(result, symbol, cacheKey);
            }
            return;
        }

        // 沒快取 → 抓基礎數據（不含 AI）
        if (loader) { loader.classList.remove('hidden'); loader.style.display = 'flex'; }
        if (result) { result.classList.add('hidden'); result.innerHTML = ''; }

        fetch(`${API_BASE}/pulse/${encodeURIComponent(symbol)}`, { credentials: 'include' })
            .then(async r => {
                // 錯誤回應不能當結果快取（以前 {detail} 會被快取 15 分鐘，重試一直看到壞結果）
                if (!r.ok) {
                    const _err = await r.json().catch(() => ({}));
                    throw new Error(typeof _err.detail === 'string' ? _err.detail : `HTTP ${r.status}`);
                }
                return r.json();
            })
            .then(data => {
                window.AppCache?.setWithTime(cacheKey, data, 15 * 60 * 1000); // 快取 15 分鐘
                if (loader) { loader.classList.add('hidden'); loader.style.display = 'none'; }
                if (result) {
                    result.classList.remove('hidden');
                    _renderAIPulse(result, data);
                    _appendPulseRefreshBar(result, symbol, cacheKey);
                }
            })
            .catch(e => {
                if (loader) { loader.classList.add('hidden'); loader.style.display = 'none'; }
                if (result) {
                    result.classList.remove('hidden');
                    result.innerHTML = `<div class="text-error text-sm">${window.I18n.t('stock.loadFailedMsg', { msg: escapeHtml(e.message) })}</div>`;
                }
            });
    }

    function _appendPulseRefreshBar(container, symbol, cacheKey) {
        const timeStr = window.AppCache?.getTimeStr(cacheKey) || '';
        const bar = document.createElement('div');
        bar.className = 'flex items-center justify-between px-1 pt-2 mt-2 border-t border-borderSubtle text-[10px] text-textMuted';
        bar.innerHTML = `
            <span>${timeStr ? window.I18n.t('stock.cachedAt', { time: timeStr }) : ''}</span>
            <button class="flex items-center gap-1 px-2 py-1 rounded-lg hover:bg-surfaceHighlight transition hover:text-secondary"
                    data-click="marketPulseRetry" data-input-id="commodityPulseSearchInput" data-symbol="${encodeURIComponent(symbol)}" data-cache-prefix="commodity_pulse_" data-target-action="CommodityTab.runDeepAnalysis" data-click-args="${encodeURIComponent(JSON.stringify([true]))}">
                <i data-lucide="zap" class="w-3 h-3 text-primary"></i> ${window.I18n.t('stock.aiDeepAnalysis')}
            </button>`;
        container.appendChild(bar);
        if (window.lucide) lucide.createIcons();
    }

    async function runDeepAnalysis(forceRefresh) {
        const input  = document.getElementById('commodityPulseSearchInput');
        const loader = document.getElementById('commodity-pulse-loader');
        const result = document.getElementById('commodity-pulse-result');
        if (!input) return;
        const symbol = input.value.trim().toUpperCase();
        if (!symbol) return;

        if (loader) { loader.classList.remove('hidden'); loader.style.display = 'flex'; }
        if (result) { result.classList.add('hidden'); result.innerHTML = ''; }

        try {
            const provider = getSelectedProvider();
            const _deepCtrl = new AbortController();
            const _deepTimer = setTimeout(() => _deepCtrl.abort(), 120000);
            const _force = forceRefresh ? 'true' : 'false';
            const res  = await fetch(`${API_BASE}/pulse/${encodeURIComponent(symbol)}?deep_analysis=true&force_refresh=${_force}&lang=${encodeURIComponent(window.I18n?.getLanguage?.() || localStorage.getItem('selectedLanguage') || 'zh-TW')}`, {
                credentials: 'include',
                headers: { 'X-User-LLM-Provider': provider },
                signal: _deepCtrl.signal,
            });
            clearTimeout(_deepTimer);
            // 錯誤回應不能當分析結果存（以前會把 {detail} 寫進 localStorage 留 7 天）
            if (!res.ok) {
                const _err = await res.json().catch(() => ({}));
                throw new Error(typeof _err.detail === 'string' ? _err.detail : `HTTP ${res.status}`);
            }
            const data = await res.json();
            if (data.ai_error) showToast(data.ai_error, 'warning', 8000);
            if (loader) { loader.classList.add('hidden'); loader.style.display = 'none'; }
            // 用送出請求時的代號存快取——等待期間使用者可能已改了輸入框
            window.AppCache?.setWithTime('commodity_pulse_' + symbol, data, 15 * 60 * 1000);
            _saveAILocal(symbol, data);
            if (result) { result.classList.remove('hidden'); _renderAIPulse(result, data); }
        } catch (e) {
            if (loader) { loader.classList.add('hidden'); loader.style.display = 'none'; }
            if (result) {
                result.classList.remove('hidden');
                const _msg = e.name === 'AbortError' ? 'Request timeout (120000ms)' : e.message;
                result.innerHTML = `<div class="text-error text-sm">${window.I18n.t('stock.analysisFailedMsg', { msg: escapeHtml(_msg) })}</div>`;
            }
        }
    }

    function _renderAIPulse(container, data) {
        if (!data || data.error) {
            container.innerHTML = `<div class="text-error text-sm px-2">${data?.error || window.I18n.t('stock.analysisUnavailable')}</div>`;
            return;
        }
        const chg    = data.change_24h ?? 0;
        const isPos  = chg > 0, isNeg = chg < 0;
        const cls    = pctClass(chg);
        const arrow  = pctArrow(chg);
        const tech   = data.technical_indicators || {};
        const news   = data.news || [];
        const cur    = data.unit || data.currency || 'USD';
        const price  = data.current_price;
        const fv     = (v, d = 2) => (v != null && !isNaN(Number(v))) ? Number(v).toFixed(d) : 'N/A';

        // ── 52W 進度條 ────────────────────────────────────────────────────────
        const lo52 = tech['52w_low'], hi52 = tech['52w_high'];
        let w52Html = '';
        if (lo52 != null && hi52 != null && price != null && hi52 > lo52) {
            const pct = Math.min(100, Math.max(0, ((price - lo52) / (hi52 - lo52)) * 100));
            w52Html = `
                <div class="mt-3">
                    <div class="flex justify-between text-[9px] text-textMuted mb-1">
                        <span>${window.I18n.t('stock.low52w')} ${fv(lo52)} ${cur}</span>
                        <span class="text-textSecondary font-semibold">${window.I18n.t('stock.now')} ${pct.toFixed(0)}%</span>
                        <span>${window.I18n.t('stock.high52w')} ${fv(hi52)} ${cur}</span>
                    </div>
                    <div class="h-1.5 rounded-full bg-surfaceHighlight overflow-hidden">
                        <div class="h-full rounded-full bg-gradient-to-r from-danger via-yellow-500 to-success transition-all" style="width:${pct}%"></div>
                    </div>
                </div>`;
        }

        // ── Hero ──────────────────────────────────────────────────────────────
        const colorBg = isPos ? 'bg-success/10' : isNeg ? 'bg-danger/10' : 'bg-surfaceHighlight';
        const heroHtml = `
            <div class="rounded-2xl bg-surface/80 border border-borderLight p-5 mb-3">
                <div class="flex items-start justify-between gap-3">
                    <div>
                        <div class="text-lg font-bold text-secondary leading-tight">${localizedName(data.symbol, data.name || data.symbol)}</div>
                        <div class="text-[11px] text-textMuted mt-0.5 flex items-center gap-2">
                            <span>${data.symbol}</span>
                            <span class="px-1.5 py-0.5 rounded bg-surfaceHighlight text-[9px] font-bold tracking-wider">${window.I18n.t('stock.marketNames.commodity')}</span>
                            <span>${cur}</span>
                        </div>
                    </div>
                    <div class="text-right">
                        <div class="text-2xl font-black font-mono text-secondary">${fv(price)}</div>
                        <div class="inline-flex items-center gap-1 text-sm font-bold ${cls} ${colorBg} px-2.5 py-0.5 rounded-lg mt-0.5">
                            ${arrow} ${fv(Math.abs(chg))}% <span class="text-[9px] font-normal opacity-70">24H</span>
                        </div>
                    </div>
                </div>
                ${w52Html}
            </div>`;

        // ── ${window.I18n.t('stock.technicalIndicators')} grid ─────────────────────────────────────────────────────
        const rsiVal  = tech.rsi;
        const rsiSig  = rsiVal != null ? (rsiVal > 70 ? [window.I18n.t('stock.overbought'), 'bg-danger/20 text-danger'] : rsiVal < 30 ? [window.I18n.t('stock.oversold'), 'bg-success/20 text-success'] : [window.I18n.t('stock.neutral'), 'bg-surfaceHighlight text-textMuted']) : null;
        const macdVal = tech.macd_histogram;
        const macdSig = macdVal != null ? (macdVal > 0 ? [window.I18n.t('stock.bullish'), 'bg-success/20 text-success'] : [window.I18n.t('stock.bearish'), 'bg-danger/20 text-danger']) : null;
        const ma20    = tech.ma20, ma50 = tech.ma50;
        const ma20Pct = (ma20 != null && price != null) ? ((price - ma20) / ma20 * 100) : null;
        const ma50Pct = (ma50 != null && price != null) ? ((price - ma50) / ma50 * 100) : null;

        const indicCell = (label, value, badge) => `
            <div class="bg-surfaceHighlight rounded-xl p-3 flex flex-col gap-1.5">
                <span class="text-[10px] text-textMuted uppercase tracking-wider">${label}</span>
                <span class="text-base font-black font-mono text-secondary">${value}</span>
                ${badge ? `<span class="text-[9px] font-bold px-1.5 py-0.5 rounded-full w-fit ${badge[1]}">${badge[0]}</span>` : ''}
            </div>`;

        const techHtml = `
            <div class="rounded-2xl bg-surfaceHighlight border border-borderLight p-4 mb-3">
                <div class="text-[10px] font-bold text-textMuted uppercase tracking-widest mb-3">${window.I18n.t('stock.technicalIndicators')}</div>
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    ${indicCell('RSI (14)', rsiVal != null ? fv(rsiVal, 1) : 'N/A', rsiSig)}
                    ${indicCell('MACD Hist', macdVal != null ? fv(macdVal) : 'N/A', macdSig)}
                    ${indicCell('MA20', ma20 != null ? fv(ma20) : 'N/A', ma20Pct != null ? [`${ma20Pct >= 0 ? '+' : ''}${ma20Pct.toFixed(1)}%`, ma20Pct >= 0 ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger'] : null)}
                    ${indicCell('MA50', ma50 != null ? fv(ma50) : 'N/A', ma50Pct != null ? [`${ma50Pct >= 0 ? '+' : ''}${ma50Pct.toFixed(1)}%`, ma50Pct >= 0 ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger'] : null)}
                </div>
            </div>`;

        // ── AI 分析摘要 ───────────────────────────────────────────────────────
        const summaryHtml = window.renderAIAnalysisSection({
            data: data,
            tabName: 'CommodityTab',
            _t: _t,
        });

        // ── ${window.I18n.t('stock.recentNews')} ──────────────────────────────────────────────────────────
        let newsHtml = '';
        if (news.length > 0) {
            const newsItems = news.slice(0, 4).map(n => `
                <li class="flex items-start gap-2 py-2 border-b border-borderSubtle last:border-0">
                    <i data-lucide="newspaper" class="w-3 h-3 text-textMuted mt-0.5 shrink-0"></i>
                    <span class="text-xs text-textSecondary leading-relaxed">${n.url
                        ? `<a href="${sanitizeUrl(n.url)}" target="_blank" rel="noopener" class="hover:text-primary transition-colors">${escapeHtml(n.title)}</a>`
                        : n.title}</span>
                </li>`).join('');
            newsHtml = `
                <div class="rounded-2xl bg-surfaceHighlight border border-borderLight p-4 mt-3">
                    <div class="text-[10px] font-bold text-textMuted uppercase tracking-widest mb-2 flex items-center gap-1.5">
                        <i data-lucide="rss" class="w-3 h-3"></i> ${window.I18n.t('stock.recentNews')}
                    </div>
                    <ul>${newsItems}</ul>
                </div>`;
        }

        container.innerHTML = heroHtml + techHtml + summaryHtml + newsHtml;
        if (window.lucide) lucide.createIcons();
    }

    // ── Event binding ──────────────────────────────────────
    function bindEvents() {
        const searchBtn   = document.getElementById('commodityPulseSearchBtn');
        const searchInput = document.getElementById('commodityPulseSearchInput');
        if (searchBtn)   searchBtn.onclick = () => runDeepAnalysis();
        if (searchInput) searchInput.onkeydown = e => { if (e.key === 'Enter') runDeepAnalysis(); };
    }

    // ── Init ───────────────────────────────────────────────
    let lastUpdatedAt = null;

    function init() {
        loadWatchlist();
        hydrateServerWatchlist();
        renderWatchlistControls();
        initSectionToggles();
        if (window.MarketStatus) {
            window.MarketStatus.startMarketAutoRefresh(
                MARKET_KEY,
                () => refreshCurrent(),
                () => lastUpdatedAt
            );
        }
        refreshMarketWatch();
        bindEvents();
    }

    return {
        init,
        loadWatchlist, saveWatchlist,
        removeStock,
        showPicker, _applyPicker,
        renderWatchlistControls,
        switchSubTab, refreshCurrent,
        refreshMarketWatch, _renderWatchlist,
        jumpToPulse, refreshMarketInfo,
        _loadGroupsSection, _loadNewsSection,
        initSectionToggles, toggleSection,
        refreshAIPulse, runDeepAnalysis, _renderAIPulse,
        bindEvents,
    };
})();
