// ============================================================
// India Stock Market Tab — 印度股市  [new architecture]
// Mirrors hkstock.js / krstock.js pattern
// ============================================================

window.INStockTab = (() => {
    // ── Constants ──────────────────────────────────────────
    const MARKET_KEY   = 'instock';
    const STORAGE_KEY  = 'inStockWatchlist';
    const STORE_KEY    = 'inStockSelectedSymbols';
    const SYNC_MARKET = 'in_stock'; // /api/watchlist 的市場代碼
    const DEFAULT_WATCHLIST = ['RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS']; // 預設清單不算使用者選的，不上傳
    const CURRENCY     = 'INR';
    const API_BASE     = '/api/instock';

    const INDEX_SYMBOLS = ['^BSESN', '^NSEI'];
    const INDEX_NAMES   = {
        '^BSESN': 'BSE Sensex',
        '^NSEI':  'NSE Nifty 50',
    };

    const AVAILABLE_SYMBOLS = [
        { symbol: 'RELIANCE.NS',  name: 'Reliance Industries' },
        { symbol: 'TCS.NS',       name: 'Tata Consultancy' },
        { symbol: 'HDFCBANK.NS',  name: 'HDFC Bank' },
        { symbol: 'INFY.NS',      name: 'Infosys' },
        { symbol: 'ICICIBANK.NS', name: 'ICICI Bank' },
        { symbol: 'HINDUNILVR.NS',name: 'Hindustan Unilever' },
        { symbol: 'ITC.NS',       name: 'ITC Limited' },
        { symbol: 'KOTAKBANK.NS', name: 'Kotak Mahindra Bank' },
        { symbol: 'LT.NS',        name: 'Larsen & Toubro' },
        { symbol: 'SBIN.NS',      name: 'State Bank of India' },
        { symbol: 'WIPRO.NS',     name: 'Wipro' },
        { symbol: 'TATAMOTORS.NS',name: 'Tata Motors' },
    ];

    // ── State ──────────────────────────────────────────────
    let state = {
        activeSubTab: 'market',
        watchlist: [],
        selectedSymbols: [],
        sectionOpen: { indices: true, news: true },
    };

    // ── Helpers ────────────────────────────────────────────
    function fmt(v, digits = 2) {
        if (v == null || isNaN(v)) return 'N/A';
        // 千分位（2026-09-12 擬真行情走查：₩1234567.00 難讀，港股已是 98,765.4）
        return Number(v).toLocaleString('en', { minimumFractionDigits: digits, maximumFractionDigits: digits });
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
    function addStock(symbol, name) {
        symbol = symbol.toUpperCase().trim();
        if (!symbol) return;
        if (!state.watchlist.includes(symbol)) {
            state.watchlist.push(symbol);
            state.selectedSymbols.push(symbol);
            saveWatchlist();
        }
    }
    function removeStock(symbol) {
        state.watchlist = state.watchlist.filter(s => s !== symbol);
        state.selectedSymbols = state.selectedSymbols.filter(s => s !== symbol);
        saveWatchlist();
    }

    // ── Picker modal ───────────────────────────────────────
    function showPicker() {
        const existing = document.getElementById('instock-picker-modal');
        if (existing) existing.remove();

        const modal = document.createElement('div');
        modal.id = 'instock-picker-modal';
        modal.className = 'fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm';
        modal.innerHTML = `
            <div class="bg-surface border border-borderLight rounded-2xl p-6 w-[340px] max-h-[80dvh] flex flex-col gap-4 shadow-2xl">
                <div class="flex items-center justify-between">
                    <h3 class="text-base font-bold text-textPrimary">${window.I18n.t('stock.selectToShow', { market: window.I18n.t('stock.marketNames.in') })}</h3>
                    <button data-click="removeById" data-click-arg="instock-picker-modal" class="w-11 h-11 -m-2 flex items-center justify-center rounded-xl text-textMuted hover:text-textPrimary hover:bg-surfaceHighlight transition" aria-label="Close">
                        <i data-lucide="x" class="w-5 h-5"></i>
                    </button>
                </div>
                <div class="overflow-y-auto flex-1 space-y-1">
                    ${AVAILABLE_SYMBOLS.map(s => `
                        <label class="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-surfaceHighlight cursor-pointer">
                            <input type="checkbox" value="${s.symbol}"
                                ${state.selectedSymbols.includes(s.symbol) ? 'checked' : ''}
                                class="accent-primary w-4 h-4">
                            <span class="text-sm text-textPrimary">${s.name}</span>
                            <span class="text-xs text-textMuted ml-auto">${s.symbol}</span>
                        </label>`).join('')}
                </div>
                <button data-click="INStockTab._applyPicker"
                    class="w-full py-2 rounded-xl bg-primary text-background font-bold text-sm hover:bg-primary/80 transition-colors">
                    ${window.I18n.t('common.confirm')}
                </button>
            </div>`;
        document.body.appendChild(modal);
        if (window.lucide) lucide.createIcons();
    }
    function _applyPicker() {
        const modal = document.getElementById('instock-picker-modal');
        if (!modal) return;
        const checked = [...modal.querySelectorAll('input[type=checkbox]:checked')].map(el => el.value);
        state.selectedSymbols = checked;
        saveWatchlist();
        modal.remove();
        _renderWatchlist();
    }

    // ── Watchlist controls ─────────────────────────────────
    function renderWatchlistControls() {
        const container = document.getElementById('instock-screener-controls');
        if (!container) return;
        container.innerHTML = `
            <div class="flex flex-wrap items-center gap-2 mb-3 px-1">
                <button data-click="INStockTab.showPicker"
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
            const btn     = document.getElementById(`instock-btn-${t}`);
            const content = document.getElementById(`instock-${t}-content`);
            const active  = t === tab;
            if (btn) btn.className = `instock-sub-tab ${SUB_TAB_BUTTON_BASE_CLASS} ${active ? SUB_TAB_BUTTON_ACTIVE_CLASS : SUB_TAB_BUTTON_INACTIVE_CLASS}`;
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
        const list   = document.getElementById('instock-screener-list');
        const loader = document.getElementById('instock-market-loader');
        if (!list) return;
        renderWatchlistControls();
        if (!state.selectedSymbols.length) {
            list.innerHTML = '<p class="text-textMuted text-sm px-1">' + window.I18n.t('stock.watchlistEmpty', { market: window.I18n.t('stock.marketNames.in'), manage: window.I18n.t('stock.manageWatchlist') }) + '</p>';
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
                const label = known ? known.name : (q.name || q.symbol);
                const abbr  = q.symbol.replace(/\.(NS)/g,'').slice(0,4);
                return `
                    <div class="group bg-surface/20 hover:bg-surface/40 border border-borderSubtle rounded-2xl p-4 transition-all duration-300 cursor-pointer"
                         data-click="INStockTab.jumpToPulse" data-click-arg="${encodeURIComponent(q.symbol)}">
                        <div class="flex items-start gap-3">
                            <div class="w-10 h-10 rounded-xl bg-background flex items-center justify-center text-xs font-bold text-primary border border-borderSubtle group-hover:scale-110 transition-transform flex-shrink-0 mt-0.5">${abbr}</div>
                            <div class="flex-1 min-w-0">
                                <div class="flex items-start justify-between gap-2">
                                    <div class="min-w-0">
                                        <div class="text-sm font-bold text-secondary truncate">${label}</div>
                                        <div class="text-[9px] text-textMuted mt-0.5">${q.symbol} · INR</div>
                                    </div>
                                    <div class="text-right flex-shrink-0">
                                        <div class="text-sm font-black font-mono ${cls}">${arrow} ${Math.abs(chg).toFixed(2)}%</div>
                                        <div class="text-[9px] text-textMuted">24H</div>
                                    </div>
                                </div>
                                <div class="flex items-center justify-between mt-1.5">
                                    <span class="text-[11px] text-textMuted font-mono">₹${fmt(q.price)}</span>
                                    <button data-click="openAlert" data-click-args="${encodeURIComponent(JSON.stringify([q.symbol, 'in_stock']))}" data-click-stop class="w-7 h-7 rounded-lg flex items-center justify-center text-yellow-400 hover:text-yellow-300 hover:bg-yellow-400/10 transition-colors border border-borderSubtle" title="${window.I18n.t('settings.watchlist.setAlert')}"><span class="text-xs leading-none">🔔</span></button>
                                    <button data-click="INStockTab.removeStock" data-click-arg="${encodeURIComponent(q.symbol)}" data-click-after="_renderWatchlist" data-click-stop
                                        class="text-textMuted hover:text-danger transition-colors p-1">
                                        <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
                                    </button>
                                </div>
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
        const input = document.getElementById('instockPulseSearchInput');
        if (input) { input.value = symbol; refreshAIPulse(); }
    }

    // ── Market info sections ───────────────────────────────
    async function refreshMarketInfo() {
        _loadIndicesSection();
        _loadNewsSection();
    }

    async function _loadIndicesSection() {
        const container = document.getElementById('instock-info-indices');
        const loader    = document.getElementById('instock-info-indices-loader');
        if (!container) return;
        if (loader) loader.classList.remove('hidden');
        try {
            const syms = INDEX_SYMBOLS.join(',');
            const res  = await fetch(`${API_BASE}/market?symbols=${encodeURIComponent(syms)}`);
            const data = await res.json();
            if (loader) loader.classList.add('hidden');
            if (!data.quotes || !data.quotes.length) {
                container.innerHTML = '<p class="text-textMuted text-sm col-span-3">' + window.I18n.t('stock.indicesUnavailable') + '</p>';
                return;
            }
            container.innerHTML = data.quotes.map(q => {
                const chg   = q.changePercent ?? 0;
                const cls   = pctClass(chg);
                const arrow = pctArrow(chg);
                const name  = INDEX_NAMES[q.symbol] || q.symbol;
                return `
                    <div class="relative overflow-hidden rounded-2xl border border-borderSubtle bg-gradient-to-br from-surface to-background hover:border-primary/30 transition-all duration-200 group cursor-default">
                        <div class="absolute top-0 right-0 w-16 h-16 bg-primary/5 rounded-full blur-2xl group-hover:bg-primary/10 transition-all"></div>
                        <div class="relative p-4">
                            <div class="text-[10px] text-textMuted mb-2 font-bold uppercase tracking-wider">${name}</div>
                            <div class="font-black text-secondary text-lg font-mono">${fmt(q.price)}</div>
                            <div class="text-xs ${cls} font-bold mt-1">${arrow} ${Math.abs(chg).toFixed(2)}%</div>
                        </div>
                    </div>`;
            }).join('');
        } catch {
            if (loader) loader.classList.add('hidden');
            container.innerHTML = '<p class="text-error text-sm col-span-3">' + window.I18n.t('stock.indicesLoadFailed') + '</p>';
        }
    }

    async function _loadNewsSection() {
        const container = document.getElementById('instock-info-news');
        const loader    = document.getElementById('instock-info-news-loader');
        if (!container) return;
        if (loader) loader.classList.remove('hidden');
        try {
            const syms = (state.selectedSymbols.length ? state.selectedSymbols : INDEX_SYMBOLS).slice(0, 5).join(',');
            const res  = await fetch(`${API_BASE}/news?symbols=${encodeURIComponent(syms)}&limit=15`);
            const json = await res.json();
            if (loader) loader.classList.add('hidden');
            const items = json.data || [];
            if (!items.length) {
                container.innerHTML = '<p class="text-textMuted text-xs italic text-center py-6 opacity-50">' + window.I18n.t('stock.noNews', { market: window.I18n.t('stock.marketNames.in') }) + '</p>';
                return;
            }
            container.innerHTML = items.map(item => {
                const tag = (item.symbol || '').replace(/\.[A-Z]+$/, '').slice(0, 8);
                const pub = item.publisher ? `<span class="text-[9px] bg-primary/10 text-primary px-1.5 py-0.5 rounded font-mono">${escapeHtml(item.publisher)}</span>` : '';
                const time = item.pub_str ? `<span class="text-[9px] text-textMuted">${escapeHtml(item.pub_str)}</span>` : '';
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
            const body    = document.getElementById(`instock-section-body-${sec}`);
            const chevron = document.getElementById(`instock-chevron-${sec}`);
            if (body)    body.style.display    = state.sectionOpen[sec] ? '' : 'none';
            if (chevron) chevron.style.transform = state.sectionOpen[sec] ? '' : 'rotate(180deg)';
        });
    }
    function toggleSection(sec) {
        state.sectionOpen[sec] = !state.sectionOpen[sec];
        const body    = document.getElementById(`instock-section-body-${sec}`);
        const chevron = document.getElementById(`instock-chevron-${sec}`);
        if (body)    body.style.display    = state.sectionOpen[sec] ? '' : 'none';
        if (chevron) chevron.style.transform = state.sectionOpen[sec] ? '' : 'rotate(180deg)';
    }

    // ── AI Pulse ───────────────────────────────────────────

    // ── AI 分析結果 localStorage 持久化（per-user per-symbol，保留 7 天）──────
    function _aiLocalKey(symbol) {
        const u = window.AuthManager?.currentUser;
        const uid = (u?.user_id || u?.uid || 'anon').replace(/[^a-zA-Z0-9_-]/g, '_');
        return `ai_deep_in__${uid}_${symbol.toUpperCase()}_${(window.I18n?.getLanguage?.() || localStorage.getItem('selectedLanguage') || 'zh-TW')}`;
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
        const input = document.getElementById('instockPulseSearchInput');
        if (!input) return;
        const symbol = input.value.trim().toUpperCase();
        if (!symbol) return;
        const loader   = document.getElementById('instock-pulse-loader');
        const result   = document.getElementById('instock-pulse-result');
        const cacheKey = 'instock_pulse_' + symbol;

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
                    data-click="marketPulseRetry" data-input-id="instockPulseSearchInput" data-symbol="${encodeURIComponent(symbol)}" data-cache-prefix="instock_pulse_" data-target-action="INStockTab.runDeepAnalysis" data-click-args="${encodeURIComponent(JSON.stringify([true]))}">
                <i data-lucide="zap" class="w-3 h-3 text-primary"></i> ${window.I18n.t('stock.aiDeepAnalysis')}
            </button>`;
        container.appendChild(bar);
        if (window.lucide) lucide.createIcons();
    }

    async function runDeepAnalysis(forceRefresh) {
        const input  = document.getElementById('instockPulseSearchInput');
        const loader = document.getElementById('instock-pulse-loader');
        const result = document.getElementById('instock-pulse-result');
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
            window.AppCache?.setWithTime('instock_pulse_' + symbol, data, 15 * 60 * 1000);
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
        const fund   = data.fundamentals || {};
        const news   = data.news || [];
        const cur    = data.currency || CURRENCY;
        const fv     = (v, d = 2) => (v != null && !isNaN(Number(v))) ? Number(v).toFixed(d) : 'N/A';

        // ── 52W 進度條 ────────────────────────────────────────────────────────
        const lo52 = tech['52w_low'], hi52 = tech['52w_high'], price = data.current_price;
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

        // ── 市值格式化 ────────────────────────────────────────────────────────
        const mc = fund.market_cap;
        let mcStr = 'N/A';
        if (mc) {
            // 單位交給 Intl 依介面語言換算（zh 兆/億、en T/B、ru трлн/млрд）。
            // 原本非英文一律除以 1e8 再接「億」的譯文，ru 的譯文是 млрд（1e9）→ 市值多 10 倍
            mcStr = new Intl.NumberFormat(window.I18n?.getLanguage?.() || 'zh-TW', {
                notation: 'compact',
                maximumFractionDigits: 2,
            }).format(mc);
        }

        // ── Hero ──────────────────────────────────────────────────────────────
        const colorBg = isPos ? 'bg-success/10' : isNeg ? 'bg-danger/10' : 'bg-surfaceHighlight';
        const heroHtml = `
            <div class="rounded-2xl bg-surface/80 border border-borderLight p-5 mb-3">
                <div class="flex items-start justify-between gap-3">
                    <div>
                        <div class="text-lg font-bold text-secondary leading-tight">${data.name || data.symbol}</div>
                        <div class="text-[11px] text-textMuted mt-0.5 flex items-center gap-2">
                            <span>${data.symbol}</span>
                            <span class="px-1.5 py-0.5 rounded bg-surfaceHighlight text-[9px] font-bold tracking-wider">${window.I18n.t('stock.marketNames.in')}</span>
                            <span>${cur}</span>
                        </div>
                        ${mcStr !== 'N/A' ? `<div class="text-[10px] text-textMuted mt-1">${window.I18n.t('stock.marketCap')} <span class="text-textSecondary font-semibold">${mcStr} ${cur}</span>${fund.pe_ratio != null ? `　PE <span class="text-textSecondary font-semibold">${fund.pe_ratio}x</span>` : ''}</div>` : ''}
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

        // ── ${window.I18n.t('stock.fundamentals')} panel ──────────────────────────────────────────────────────
        let fundCardHtml = '';
        const hasFundData = fund.target_price != null || fund.beta != null ||
                            fund.dividend_yield != null || fund.eps != null ||
                            fund.revenue_growth != null || fund.recommendation;
        if (hasFundData) {
            const upside = (fund.target_price != null && price != null)
                ? ((fund.target_price - price) / price * 100) : null;
            const recLabelMap = {
                'strong_buy':   [window.I18n.t('stock.strongBuy'), 'bg-success/30 text-success'],
                'buy':          [window.I18n.t('stock.buy'),   'bg-success/20 text-success'],
                'hold':         [window.I18n.t('stock.hold'),   'bg-surfaceHighlight text-textMuted'],
                'underperform': [window.I18n.t('stock.underperform'),'bg-danger/20 text-danger'],
                'sell':         [window.I18n.t('stock.sell'),   'bg-danger/20 text-danger'],
                'strong_sell':  [window.I18n.t('stock.strongSell'), 'bg-danger/30 text-danger'],
            };
            const recEntry = recLabelMap[fund.recommendation] ||
                (fund.recommendation ? [fund.recommendation.toUpperCase(), 'bg-surfaceHighlight text-textMuted'] : null);
            const betaSig = fund.beta != null ? (
                fund.beta > 1.5  ? [window.I18n.t('stock.highVolatility'), 'bg-danger/20 text-danger']  :
                fund.beta < 0.8  ? [window.I18n.t('stock.lowVolatility'), 'bg-success/20 text-success'] :
                                   [window.I18n.t('stock.moderate'),   'bg-surfaceHighlight text-textMuted']
            ) : null;
            const fundCells = [
                fund.target_price != null ? indicCell(
                    window.I18n.t('stock.analystTarget'), fv(fund.target_price),
                    upside != null ? [`${upside >= 0 ? '+' : ''}${upside.toFixed(1)}%`, upside >= 0 ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger'] : null
                ) : null,
                recEntry ? indicCell(window.I18n.t('stock.analystRating'), recEntry[0], null) : null,
                fund.beta != null ? indicCell('Beta', String(fund.beta), betaSig) : null,
                fund.dividend_yield != null ? indicCell(window.I18n.t('stock.dividendYield'), `${fund.dividend_yield}%`, null) : null,
                fund.eps != null ? indicCell('EPS', fv(fund.eps), null) : null,
                fund.revenue_growth != null ? indicCell(
                    window.I18n.t('stock.revenueGrowth'), `${fund.revenue_growth >= 0 ? '+' : ''}${fund.revenue_growth}%`,
                    fund.revenue_growth >= 0 ? [window.I18n.t('stock.growth'), 'bg-success/20 text-success'] : [window.I18n.t('stock.decline'), 'bg-danger/20 text-danger']
                ) : null,
            ].filter(Boolean).join('');
            const sectorLine = fund.sector ? `
                <div class="text-[10px] text-textMuted mt-2 pt-2 border-t border-borderSubtle flex items-center gap-1.5">
                    <i data-lucide="building-2" class="w-3 h-3 shrink-0"></i>
                    <span>${fund.sector}${fund.industry ? ` · ${fund.industry}` : ''}</span>
                </div>` : '';
            fundCardHtml = `
                <div class="rounded-2xl bg-surfaceHighlight border border-borderLight p-4 mb-3">
                    <div class="text-[10px] font-bold text-textMuted uppercase tracking-widest mb-3">${window.I18n.t('stock.fundamentals')}</div>
                    <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">${fundCells}</div>
                    ${sectorLine}
                </div>`;
        }

        // ── AI 分析摘要 ───────────────────────────────────────────────────────
        const summaryHtml = window.renderAIAnalysisSection({
            data: data,
            tabName: 'INStockTab',
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
                        : escapeHtml(n.title)}</span>
                </li>`).join('');
            newsHtml = `
                <div class="rounded-2xl bg-surfaceHighlight border border-borderLight p-4 mt-3">
                    <div class="text-[10px] font-bold text-textMuted uppercase tracking-widest mb-2 flex items-center gap-1.5">
                        <i data-lucide="rss" class="w-3 h-3"></i> ${window.I18n.t('stock.recentNews')}
                    </div>
                    <ul>${newsItems}</ul>
                </div>`;
        }

        container.innerHTML = heroHtml + techHtml + fundCardHtml + summaryHtml + newsHtml;
        if (window.lucide) lucide.createIcons();
    }

    // ── Event binding ──────────────────────────────────────
    function bindEvents() {
        const searchBtn   = document.getElementById('instockPulseSearchBtn');
        const searchInput = document.getElementById('instockPulseSearchInput');
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
        addStock, removeStock,
        showPicker, _applyPicker,
        renderWatchlistControls,
        switchSubTab, refreshCurrent,
        refreshMarketWatch, _renderWatchlist,
        jumpToPulse, refreshMarketInfo,
        _loadIndicesSection, _loadNewsSection,
        initSectionToggles, toggleSection,
        refreshAIPulse, runDeepAnalysis, _renderAIPulse,
        bindEvents,
    };
})();
