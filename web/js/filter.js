// ========================================
// filter.js - 過濾器功能
// ========================================

// 預設熱門幣種列表 (當用戶未選擇時顯示)
const DEFAULT_MARKET_SYMBOLS = [
    'BTC-USDT',
    'ETH-USDT',
    'SOL-USDT',
    'DOGE-USDT',
    'XRP-USDT',
    'BNB-USDT',
    'ADA-USDT',
    'AVAX-USDT',
    'DOT-USDT',
    'LINK-USDT',
];

const INVALID_MARKET_SYMBOLS = new Set(['PROGRESS', 'ALL', 'NONE', 'LOADING', 'AUTO']);

function normalizeBaseSymbol(symbol) {
    if (typeof symbol !== 'string') return '';
    let token = symbol.toUpperCase().trim().replace(/\s+/g, '').replace(/_/g, '-');
    if (!token) return '';
    if (token.includes('/')) token = token.split('/')[0];
    if (token.includes('-')) token = token.split('-')[0];
    token = token.replace(/(USDT|BUSD|USD)$/g, '');
    if (
        token.length < 2 ||
        token.length > 15 ||
        !/^[A-Z0-9]+$/.test(token) ||
        INVALID_MARKET_SYMBOLS.has(token)
    ) {
        return '';
    }
    return token;
}

function normalizePairSymbol(symbol) {
    if (typeof symbol !== 'string') return '';
    let token = symbol.toUpperCase().trim().replace(/\s+/g, '').replace(/_/g, '-');
    if (!token || INVALID_MARKET_SYMBOLS.has(token)) return '';
    token = token.replace(/\//g, '-');
    const parts = token.split('-').filter(Boolean);
    if (parts.length === 0) return '';
    if (parts.length === 1) {
        const base = normalizeBaseSymbol(parts[0]);
        return base ? `${base}-USDT` : '';
    }
    const base = normalizeBaseSymbol(parts[0]);
    const quote = parts[1];
    if (!base || !/^[A-Z0-9]{2,10}$/.test(quote)) return '';
    return `${base}-${quote}`;
}

function sanitizePairSymbols(symbols) {
    const seen = new Set();
    return (symbols || [])
        .map((symbol) => normalizePairSymbol(symbol))
        .filter((symbol) => {
            if (!symbol || seen.has(symbol)) return false;
            seen.add(symbol);
            return true;
        });
}

function sanitizeBaseSymbols(symbols) {
    const seen = new Set();
    return (symbols || [])
        .map((symbol) => normalizeBaseSymbol(symbol))
        .filter((symbol) => {
            if (!symbol || seen.has(symbol)) return false;
            seen.add(symbol);
            return true;
        });
}

// 穩定幣不進熱門榜與自動釘選清單（2026-09-27 上市準備 PR-2）：USDC-USDT 成交量常年第一，
// 以前會被自動釘成市場頁的第一個幣。後端 analysis/crypto_screener_light.py 有同一份清單。
const STABLECOIN_BASES = new Set([
    'USDC', 'USDT', 'DAI', 'FDUSD', 'TUSD', 'USDE', 'PYUSD', 'USDD', 'USDP', 'BUSD',
    'EURC', 'USDS', 'USD1', 'RLUSD', 'USDG', 'GUSD', 'FRAX', 'LUSD', 'EURT', 'EURI',
]);

// 這裡不用 normalizeBaseSymbol：它會把結尾的 USD 砍掉（FDUSD → FD），判斷就錯了
function isStablecoin(symbol) {
    if (typeof symbol !== 'string') return false;
    const token = symbol.toUpperCase().trim().replace(/\//g, '-');
    let base = token;
    if (token.includes('-')) base = token.split('-')[0];
    else if (token.length > 4 && token.endsWith('USDT')) base = token.slice(0, -4);
    return STABLECOIN_BASES.has(base);
}

function withoutStablecoins(symbols) {
    return (symbols || []).filter((symbol) => !isStablecoin(symbol));
}

const SymbolSanitizer = {
    normalizeBaseSymbol,
    normalizePairSymbol,
    sanitizePairSymbols,
    sanitizeBaseSymbols,
    isStablecoin,
    withoutStablecoins,
};

window.SymbolSanitizer = SymbolSanitizer;

// 從 localStorage 載入已保存的選擇
function loadSavedSymbolSelection() {
    try {
        const saved = localStorage.getItem('marketWatchSymbols');
        if (saved) {
            const parsed = JSON.parse(saved);
            if (Array.isArray(parsed) && parsed.length > 0) {
                // 以前自動釘選會把 USDC-USDT 釘進來、按套用後存進 localStorage——載入時清掉
                const sanitized = withoutStablecoins(sanitizePairSymbols(parsed));
                AppStore.set('globalSelectedSymbols', sanitized);
                window.globalSelectedSymbols = sanitized;
                if (sanitized.length !== parsed.length) {
                    localStorage.setItem('marketWatchSymbols', JSON.stringify(sanitized));
                    console.warn(window.I18n.t('filter.cleanedInvalidSymbols') || '[Filter] 已清理無效符號:', parsed.length - sanitized.length, '個');
                }
                console.log(window.I18n.t('filter.loadedSavedSelection', { count: sanitized.length }) || `[Filter] 已載入保存的選擇: ${sanitized.length} 個幣種`);
                if (sanitized.length > 0) {
                    return true;
                }
            }
        }
    } catch (e) {
        console.error(window.I18n.t('filter.loadSavedSelectionFailed') || '[Filter] 載入保存的選擇失敗:', e);
    }

    // 如果沒有保存的選擇，保持為空，讓 market.js 加載後端預設數據 (Auto Mode)
    AppStore.set('globalSelectedSymbols', []);
    window.globalSelectedSymbols = [];
    console.log(window.I18n.t('filter.noSavedSelection') || '[Filter] 無保存紀錄，初始化為空 (Auto Mode)');
    return true;
}

// 保存選擇到 localStorage
function saveSymbolSelection() {
    try {
        const sanitized = sanitizePairSymbols(AppStore.get('globalSelectedSymbols') || []);
        AppStore.set('globalSelectedSymbols', sanitized);
        window.globalSelectedSymbols = sanitized;

        if (sanitized.length > 0) {
            localStorage.setItem(
                'marketWatchSymbols',
                JSON.stringify(sanitized)
            );
            console.log(window.I18n.t('filter.savedSelection', { count: sanitized.length }) || `[Filter] 已保存選擇: ${sanitized.length} 個幣種`);
        } else {
            localStorage.removeItem('marketWatchSymbols');
            console.log(window.I18n.t('filter.clearedSelection') || '[Filter] 已清除保存的選擇');
        }
    } catch (e) {
        console.error(window.I18n.t('filter.saveSelectionFailed') || '[Filter] 保存選擇失敗:', e);
    }
    // 登入的人同步到伺服器自選（web/js/watchlist-sync.js；空清單＝Auto 模式，不上傳）
    if (window.WatchlistSync) window.WatchlistSync.push('crypto', AppStore.get('globalSelectedSymbols') || [], []);
}

// 初始化時載入保存的選擇
document.addEventListener('DOMContentLoaded', () => {
    loadSavedSymbolSelection();
});

async function openGlobalFilter() {
    const modal = document.getElementById('global-filter-modal');
    modal.classList.remove('hidden');

    const select = document.getElementById('filter-exchange-select');
    if (select) select.value = AppStore.get('currentFilterExchange') || 'okx';

    if (!AppStore.get('allMarketSymbols') || AppStore.get('allMarketSymbols').length === 0) {
        await fetchSymbols(AppStore.get('currentFilterExchange') || 'okx');
    } else {
        renderSymbolList(AppStore.get('allMarketSymbols'));
    }
}

async function switchFilterExchange(exchange) {
    if (exchange === AppStore.get('currentFilterExchange')) return;

    if (AppStore.get('globalSelectedSymbols') && AppStore.get('globalSelectedSymbols').length > 0) {
        const confirmed = await showConfirm({
            title: window.I18n ? window.I18n.t('filter.switchExchangeTitle') : 'Switch Exchange',
            message: window.I18n ? window.I18n.t('filter.switchExchangeMessage') : 'Switching exchange will clear current selections. Continue?',
            type: 'warning',
            confirmText: window.I18n.t('common.continue') || 'Continue',
            cancelText: window.I18n.t('common.cancel') || 'Cancel',
        });

        if (!confirmed) {
            const select = document.getElementById('filter-exchange-select');
            if (select) select.value = AppStore.get('currentFilterExchange') || 'okx';
            return;
        }
    }

    AppStore.set('currentFilterExchange', exchange);
    window.currentFilterExchange = exchange;
    AppStore.set('globalSelectedSymbols', []);
    window.globalSelectedSymbols = [];
    AppStore.set('allMarketSymbols', []);
    window.allMarketSymbols = [];
    await fetchSymbols(exchange);
}

async function fetchSymbols(exchange) {
    const container = document.getElementById('symbol-list-container');
    container.innerHTML =
        `<div class="text-center py-8 text-slate-500 animate-pulse">${window.I18n ? window.I18n.t('filter.loadingCoins') : 'Loading symbols from exchange...'}</div>`;

    try {
        const data = await AppAPI.get(`/api/market/symbols?exchange=${exchange}`);
        if (data.symbols) {
            AppStore.set('allMarketSymbols', data.symbols.sort());
            window.allMarketSymbols = AppStore.get('allMarketSymbols');
            renderSymbolList(AppStore.get('allMarketSymbols'));
        } else {
            container.innerHTML =
                `<div class="text-center py-8 text-red-400">${window.I18n ? window.I18n.t('filter.fetchErrorFormat') : 'Failed to fetch symbol list (format error)'}</div>`;
        }
    } catch (e) {
        console.error('Failed to fetch symbols', e);

        let errorMessage = window.I18n ? window.I18n.t('error.networkError') : 'Network error. Please check your connection.';
        let detail = e.message;

        if (e.message.includes('429')) {
            errorMessage = window.I18n ? window.I18n.t('error.rateLimit') : 'Too many requests. Please wait a moment.';
        } else if (e.message.includes('500')) {
            errorMessage = window.I18n ? window.I18n.t('error.serverError') : 'Server error. Please try again later.';
        } else if (
            e.message.includes('timeout') ||
            e.message.includes('NetworkError') ||
            e.message.includes('Failed to fetch')
        ) {
            errorMessage = window.I18n ? window.I18n.t('error.networkError') : 'Network error. Please check your connection.';
        }

        container.innerHTML = `
            <div class="text-center py-8 text-red-400 flex flex-col items-center gap-2">
                <i data-lucide="wifi-off" class="w-8 h-8 opacity-50"></i>
                <span class="font-bold">${errorMessage}</span>
                <span class="text-xs opacity-70 mb-2">${detail}</span>
                <button data-click="fetchSymbols" data-click-arg="${encodeURIComponent(exchange)}" class="px-4 py-2 bg-red-500/10 hover:bg-red-500/20 text-red-400 rounded-lg text-sm transition border border-red-500/20">
                    ${window.I18n ? window.I18n.t('common.reload') : 'Reload'}
                </button>
            </div>`;
        if (window.AppUtils) window.AppUtils.refreshIcons();
    }
}

function renderSymbolList(symbols) {
    const container = document.getElementById('symbol-list-container');
    container.innerHTML = '';
    AppStore.set('globalSelectedSymbols', sanitizePairSymbols(AppStore.get('globalSelectedSymbols') || []));
    window.globalSelectedSymbols = AppStore.get('globalSelectedSymbols');

    const searchVal = document.getElementById('symbol-search').value.toUpperCase().trim();
    const filtered = symbols.filter((s) => s.includes(searchVal));

    const selected = [];
    const unselected = [];

    // [Optimization] Split into two lists for better UX
    filtered.sort().forEach((s) => {
        if (AppStore.get('globalSelectedSymbols') && AppStore.get('globalSelectedSymbols').includes(s)) {
            selected.push(s);
        } else {
            unselected.push(s);
        }
    });

    // Render "Selected" section first
    if (selected.length > 0) {
        const header = document.createElement('div');
        // [Fix] Removed sticky positioning to prevent blocking the view when scrolling
        header.className =
            'text-xs font-bold text-primary/80 uppercase tracking-wider px-3 py-2 mt-2 bg-surfaceHighlight/30 rounded-lg border border-primary/10';
        header.innerHTML = `${window.I18n.t('filter.selectedLabel')} <span class="ml-1 px-1.5 py-0.5 bg-primary/20 rounded-full text-primary text-[10px]">${selected.length}</span>`;
        container.appendChild(header);

        selected.forEach((s) => {
            const div = createSymbolItem(s, true);
            container.appendChild(div);
        });
    }

    // Render "All" section
    if (unselected.length > 0) {
        if (selected.length > 0) {
            const divider = document.createElement('div');
            divider.className =
                'text-xs font-bold text-textMuted uppercase tracking-wider px-3 py-2 mt-4 mb-2 border-b border-borderSubtle';
            divider.innerText = window.I18n ? window.I18n.t('filter.unselectedLabel') : 'Unselected';
            container.appendChild(divider);
        }

        // Limit rendering for performance if search is empty
        const limit = searchVal ? 200 : 100;
        unselected.slice(0, limit).forEach((s) => {
            const div = createSymbolItem(s, false);
            container.appendChild(div);
        });
    }

    if (selected.length === 0 && unselected.length === 0) {
        container.innerHTML =
            `<div class="text-center py-8 text-slate-500">${window.I18n ? window.I18n.t('filter.noMatch') : 'No matching coins found'}</div>`;
    }

    document.getElementById('selected-count-modal').innerText = (
        AppStore.get('globalSelectedSymbols') || []
    ).length;
    if (window.AppUtils) window.AppUtils.refreshIcons();
}

// Helper to create list item
function createSymbolItem(s, isChecked) {
    const div = document.createElement('div');
    div.className = `flex items-center justify-between p-3 rounded-lg cursor-pointer transition select-none ${isChecked ? 'bg-blue-900/20 border border-blue-500/30' : 'hover:bg-slate-800 border border-transparent'}`;
    div.onclick = () => toggleSymbolSelection(s);
    div.innerHTML = `
        <span class="text-sm font-mono ${isChecked ? 'text-blue-300 font-bold' : 'text-slate-300'}">${s}</span>
        <div class="w-5 h-5 rounded border ${isChecked ? 'bg-blue-600 border-blue-600' : 'border-slate-600 bg-slate-800'} flex items-center justify-center transition">
            ${isChecked ? '<i data-lucide="check" class="w-3.5 h-3.5 text-white"></i>' : ''}
        </div>
    `;
    return div;
}

function toggleSymbolSelection(s) {
    const normalizedSymbol = normalizePairSymbol(s);
    if (!normalizedSymbol) {
        if (typeof showToast === 'function') showToast(window.I18n ? window.I18n.t('filter.invalidSymbol') : 'Invalid symbol, skipped', 'warning');
        return;
    }

    if (AppStore.get('globalSelectedSymbols') && AppStore.get('globalSelectedSymbols').includes(normalizedSymbol)) {
        AppStore.set('globalSelectedSymbols', AppStore.get('globalSelectedSymbols').filter(
            (item) => item !== normalizedSymbol
        ));
        window.globalSelectedSymbols = AppStore.get('globalSelectedSymbols');
    } else {
        if (!AppStore.get('globalSelectedSymbols')) {
            AppStore.set('globalSelectedSymbols', []);
            window.globalSelectedSymbols = [];
        }

        // [Restriction] Max 10 items limit as requested by user
        if (AppStore.get('globalSelectedSymbols').length >= 10) {
            if (typeof showToast === 'function') {
                showToast(window.I18n ? window.I18n.t('filter.maxCoins') : 'Max 10 coins', 'warning');
            } else {
                alert(window.I18n ? window.I18n.t('filter.maxCoins') : 'Max 10 coins');
            }
            return;
        }

        AppStore.get('globalSelectedSymbols').push(normalizedSymbol);
    }
    AppStore.set('globalSelectedSymbols', sanitizePairSymbols(AppStore.get('globalSelectedSymbols')));
    window.globalSelectedSymbols = AppStore.get('globalSelectedSymbols');
    renderSymbolList(AppStore.get('allMarketSymbols') || []);
}

function selectAllMatches() {
    const searchVal = document.getElementById('symbol-search').value.toUpperCase().trim();
    if (!searchVal) return;

    const filtered = (AppStore.get('allMarketSymbols') || []).filter((s) => s.includes(searchVal));
    let addedCount = 0;
    filtered.forEach((s) => {
        if (!AppStore.get('globalSelectedSymbols')) {
            AppStore.set('globalSelectedSymbols', []);
            window.globalSelectedSymbols = [];
        }
        if (AppStore.get('globalSelectedSymbols').length >= 10) return;
        const normalizedSymbol = normalizePairSymbol(s);
        if (!normalizedSymbol) return;
        if (!AppStore.get('globalSelectedSymbols').includes(normalizedSymbol)) {
            AppStore.get('globalSelectedSymbols').push(normalizedSymbol);
            addedCount++;
        }
    });
    if (addedCount > 0) {
        AppStore.set('globalSelectedSymbols', sanitizePairSymbols(AppStore.get('globalSelectedSymbols')));
        window.globalSelectedSymbols = AppStore.get('globalSelectedSymbols');
        renderSymbolList(AppStore.get('allMarketSymbols') || []);
    }
}

function applyGlobalFilter() {
    document.getElementById('global-filter-modal').classList.add('hidden');
    const indicator = document.getElementById('active-filter-indicator');
    const headerBadge = document.getElementById('global-count-badge');

    // 保存選擇到 localStorage
    AppStore.set('globalSelectedSymbols', sanitizePairSymbols(AppStore.get('globalSelectedSymbols') || []));
    window.globalSelectedSymbols = AppStore.get('globalSelectedSymbols');
    saveSymbolSelection();

    const count = (AppStore.get('globalSelectedSymbols') || []).length;
    if (headerBadge) {
        headerBadge.innerText = count > 0 ? count : 'Auto';
    }

    const filterCount = document.getElementById('filter-count');
    if (filterCount) filterCount.innerText = count > 0 ? count : '';

    // Refresh both main components
    if (typeof window.refreshScreener === 'function') {
        window.refreshScreener(true);
    } else if (typeof refreshScreener === 'function') {
        refreshScreener(true);
    } else {
        console.error('[Filter] refreshScreener is not defined');
    }

    // Only refresh Pulse if visible (to save tokens), otherwise it will refresh on tab switch
    if (!document.getElementById('pulse-tab').classList.contains('hidden')) {
        checkMarketPulse(true);
    }
}

// Expose functions globally for inline onclick handlers
window.openGlobalFilter = openGlobalFilter;
window.switchFilterExchange = switchFilterExchange;
window.fetchSymbols = fetchSymbols;
window.renderSymbolList = renderSymbolList;
window.toggleSymbolSelection = toggleSymbolSelection;
window.selectAllMatches = selectAllMatches;
window.applyGlobalFilter = applyGlobalFilter;
window.loadSavedSymbolSelection = loadSavedSymbolSelection;
window.saveSymbolSelection = saveSymbolSelection;

export { DEFAULT_MARKET_SYMBOLS, SymbolSanitizer, normalizeBaseSymbol, normalizePairSymbol, sanitizePairSymbols, sanitizeBaseSymbols };
