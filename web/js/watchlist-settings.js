// ========================================
// watchlist-settings.js — 設定頁早報卡的「我的自選」（2026-09-27）
// 使用者自己挑的標的（/api/watchlist，c056）：早報每天列價格與漲跌、每一檔都能設價格警報
// （🔔 走 alerts.js 的 openAlertModal；警報清單也列在這裡，加密貨幣的警報原本沒地方看、沒地方刪）。
// 市場＝各市場分頁的 10 個（core/database/trading.WATCHLIST_MARKETS）；加入前後端會先抓一次報價，打錯代號會被擋。
// 2026-09-28：每一檔（含投資日誌的持倉）都有「📰 早報」開關——關掉只是不列進早報，
// 自選清單與帳本都不動（/api/user/brief-prefs/symbols，c057）。
// ========================================

const MARKET_KEYS = {
    crypto: ['settings.watchlist.marketCrypto', 'Crypto'],
    tw_stock: ['settings.watchlist.marketTw', 'TW stock'],
    us_stock: ['settings.watchlist.marketUs', 'US stock'],
    hk_stock: ['settings.watchlist.marketHk', 'HK stock'],
    jp_stock: ['settings.watchlist.marketJp', 'JP stock'],
    kr_stock: ['settings.watchlist.marketKr', 'KR stock'],
    cn_stock: ['settings.watchlist.marketCn', 'China A-share'],
    in_stock: ['settings.watchlist.marketIn', 'India stock'],
    commodity: ['settings.watchlist.marketCommodity', 'Commodity'],
    forex: ['settings.watchlist.marketForex', 'Forex'],
};

let _items = [];
let _max = 20;
let _alerts = [];
let _busy = false;
// 早報開關：持倉清單＋不列的 "market:SYMBOL"
let _positions = [];
let _hidden = new Set();

function _t(key, fallback, params) {
    const text = window.I18n ? window.I18n.t(key, params) : '';
    if (text && text !== key) return text;
    let out = String(fallback);
    Object.entries(params || {}).forEach(([k, v]) => {
        out = out.replace(`{{${k}}}`, String(v));
    });
    return out;
}

function _el(id) {
    return document.getElementById(id);
}

function _esc(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function _args(...values) {
    return encodeURIComponent(JSON.stringify(values));
}

function _setStatus(text, tone) {
    const el = _el('watchlist-status');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'text-xs ' + (tone === 'error' ? 'text-danger' : tone === 'ok' ? 'text-success' : 'text-textMuted');
}

// 警報的代號可能帶交易所後綴（2330.TW）；比對時去掉
function _bare(symbol) {
    return String(symbol || '').toUpperCase().replace(/\.(TWO|TW)$/, '');
}

function _alertsFor(item) {
    return _alerts.filter((a) => a.market === item.market && _bare(a.symbol) === _bare(item.symbol));
}

function _alertLabel(a) {
    const cond = {
        above: _t('modals.priceAlert.above', '≥'),
        below: _t('modals.priceAlert.below', '≤'),
        change_pct_up: _t('modals.priceAlert.changePctUp', '+%'),
        change_pct_down: _t('modals.priceAlert.changePctDown', '-%'),
    };
    return `${cond[a.condition] || a.condition} ${a.target}`;
}

function _key(market, symbol) {
    return `${market}:${String(symbol || '').toUpperCase()}`;
}

function _marketLabel(market) {
    const [key, fallback] = MARKET_KEYS[market] || [market, market];
    return _t(key, fallback);
}

// 「📰 早報」開關：開＝主色；關＝灰字加刪除線（不只靠顏色）
function _briefToggle(market, symbol) {
    const on = !_hidden.has(_key(market, symbol));
    const title = on
        ? _t('settings.watchlist.briefOn', 'In your daily brief. Tap to leave it out (it stays on your watchlist).')
        : _t('settings.watchlist.briefOff', 'Left out of your daily brief. Tap to add it back.');
    return `<button type="button" data-click="WatchlistSettings.toggleBrief" data-click-args="${_args(market, symbol)}"
        aria-pressed="${on}" title="${_esc(title)}" aria-label="${_esc(title)}"
        class="min-h-9 px-2 rounded-lg text-[11px] whitespace-nowrap transition ${on ? 'bg-primary/10 text-primary' : 'bg-surfaceHighlight text-textMuted line-through'}">📰 ${_esc(_t('settings.watchlist.briefChip', 'Brief'))}</button>`;
}

function _renderPositions() {
    const block = _el('brief-positions-block');
    const list = _el('brief-position-items');
    if (!block || !list) return;
    block.classList.toggle('hidden', !_positions.length);
    list.innerHTML = _positions
        .map(
            (p) => `<li class="p-2.5 rounded-xl bg-background/50 border border-borderSubtle">
                <div class="flex items-center gap-2">
                    <span class="text-[10px] px-1.5 py-0.5 rounded bg-surfaceHighlight text-textMuted shrink-0">${_esc(_marketLabel(p.market))}</span>
                    <span class="text-sm font-mono text-textMain flex-1 min-w-0 truncate">${_esc(p.symbol)}</span>
                    ${_briefToggle(p.market, p.symbol)}
                </div>
            </li>`
        )
        .join('');
}

function _render() {
    _renderPositions();
    const list = _el('watchlist-items');
    if (!list) return;
    const count = _el('watchlist-count');
    if (count) count.textContent = `${_items.length}/${_max}`;
    if (!_items.length) {
        list.innerHTML = `<li class="text-xs text-textMuted">${_esc(
            _t('settings.watchlist.empty', 'Nothing yet. Add a coin or stock to follow it in your daily brief.')
        )}</li>`;
        return;
    }
    list.innerHTML = _items
        .map((item) => {
            const [key, fallback] = MARKET_KEYS[item.market] || [item.market, item.market];
            const alerts = _alertsFor(item)
                .map(
                    (a) => `<span class="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-primary/10 text-primary">
                        🔔 ${_esc(_alertLabel(a))}
                        <button type="button" data-click="deleteUserAlert" data-click-arg="${encodeURIComponent(a.id)}"
                            class="text-textMuted hover:text-danger" aria-label="${_esc(_t('modals.priceAlert.deleteAlert', 'Delete'))}">✕</button>
                    </span>`
                )
                .join('');
            return `<li class="p-2.5 rounded-xl bg-background/50 border border-borderSubtle" data-watch="${_esc(item.market)}:${_esc(item.symbol)}">
                <div class="flex items-center gap-2">
                    <span class="text-[10px] px-1.5 py-0.5 rounded bg-surfaceHighlight text-textMuted shrink-0">${_esc(_t(key, fallback))}</span>
                    <span class="text-sm font-mono text-textMain flex-1 min-w-0 truncate">${_esc(item.symbol)}</span>
                    ${_briefToggle(item.market, item.symbol)}
                    <button type="button" data-click="openAlert" data-click-args="${_args(item.symbol, item.market)}"
                        class="min-h-9 px-2.5 rounded-lg text-xs text-primary hover:bg-primary/10 transition"
                        title="${_esc(_t('settings.watchlist.setAlert', 'Set a price alert'))}">🔔</button>
                    <button type="button" data-click="WatchlistSettings.remove" data-click-args="${_args(item.market, item.symbol)}"
                        class="min-h-9 px-2.5 rounded-lg text-xs text-textMuted hover:text-danger transition"
                        aria-label="${_esc(_t('settings.watchlist.remove', 'Remove'))}">✕</button>
                </div>
                ${alerts ? `<div class="flex flex-wrap gap-1.5 mt-2">${alerts}</div>` : ''}
            </li>`;
        })
        .join('');
}

async function _loadAlerts() {
    try {
        const res = await window.AppAPI.get('/api/alerts');
        _alerts = (res && res.alerts) || [];
    } catch (e) {
        _alerts = [];
    }
}

// 持倉＋不列的標的；讀不到就當全部都列（開關照樣能按）
async function _loadBriefSymbols() {
    try {
        const res = await window.AppAPI.get('/api/user/brief-prefs/symbols');
        _positions = (res && res.positions) || [];
        _hidden = new Set(((res && res.hidden) || []).map((h) => _key(h.market, h.symbol)));
    } catch (e) {
        _positions = [];
        _hidden = new Set();
    }
}

async function toggleBriefSymbol(market, symbol) {
    const key = _key(market, symbol);
    const hidden = !_hidden.has(key);
    if (hidden) _hidden.add(key);
    else _hidden.delete(key);
    _render();
    try {
        await window.AppAPI.put('/api/user/brief-prefs/symbols', { market, symbol, hidden });
        _setStatus(
            hidden
                ? _t('settings.watchlist.briefHidden', '{{symbol}} will be left out of your brief', { symbol })
                : _t('settings.watchlist.briefShown', '{{symbol}} is back in your brief', { symbol }),
            'ok'
        );
        return true;
    } catch (e) {
        if (hidden) _hidden.delete(key);
        else _hidden.add(key);
        _render();
        _setStatus(
            e && e.status === 400
                ? _t('settings.watchlist.briefTooMany', 'Too many symbols left out of the brief. Turn some back on first.')
                : _t('settings.watchlist.failed', 'Something went wrong, please try again'),
            'error'
        );
        return false;
    }
}

async function loadWatchlist() {
    const section = _el('brief-watchlist-section');
    if (!section || typeof window.AppAPI === 'undefined') return;
    try {
        const [res] = await Promise.all([
            window.AppAPI.get('/api/watchlist'),
            _loadAlerts(),
            _loadBriefSymbols(),
        ]);
        _items = (res && res.items) || [];
        _max = (res && res.max) || _max;
        _render();
    } catch (e) {
        _setStatus(_t('settings.watchlist.loadFailed', "Couldn't load your watchlist"), 'error');
    }
}

function _errorText(e) {
    const status = e && e.status;
    if (status === 422) return _t('settings.watchlist.invalid', "That doesn't look like a valid symbol for this market");
    if (status === 404) return _t('settings.watchlist.notFound', "Couldn't find a price for that symbol. Check the market and symbol.");
    if (status === 400) return _t('settings.watchlist.full', 'Your watchlist is full ({{max}}). Remove one first.', { max: _max });
    return _t('settings.watchlist.failed', 'Something went wrong, please try again');
}

async function addWatchlistItem() {
    if (_busy) return false;
    const market = (_el('watchlist-market') || {}).value || 'crypto';
    const input = _el('watchlist-symbol');
    const symbol = ((input && input.value) || '').trim();
    if (!symbol) {
        _setStatus(_errorText({ status: 422 }), 'error');
        return false;
    }
    _busy = true;
    const btn = _el('watchlist-add');
    if (btn) btn.disabled = true;
    _setStatus(_t('settings.watchlist.adding', 'Checking the price…'));
    try {
        const res = await window.AppAPI.post('/api/watchlist/add', { market, symbol });
        const item = res && res.item;
        if (item && !_items.some((i) => i.market === item.market && i.symbol === item.symbol)) {
            _items.push(item);
        }
        if (input) input.value = '';
        _render();
        _setStatus(_t('settings.watchlist.added', '{{symbol}} added', { symbol: item ? item.symbol : symbol }), 'ok');
        return true;
    } catch (e) {
        _setStatus(_errorText(e), 'error');
        return false;
    } finally {
        _busy = false;
        if (btn) btn.disabled = false;
    }
}

async function removeWatchlistItem(market, symbol) {
    try {
        await window.AppAPI.post('/api/watchlist/remove', { market, symbol });
        _items = _items.filter((i) => !(i.market === market && i.symbol === symbol));
        _render();
        _setStatus('');
        return true;
    } catch (e) {
        _setStatus(_t('settings.watchlist.failed', 'Something went wrong, please try again'), 'error');
        return false;
    }
}

// alerts.js 建立／刪除警報後會重抓清單並發 alerts:changed
window.addEventListener('alerts:changed', (e) => {
    _alerts = (e && e.detail && e.detail.alerts) || _alerts;
    _render();
});

window.WatchlistSettings = {
    add: () => addWatchlistItem(),
    remove: (market, symbol) => removeWatchlistItem(market, symbol),
    toggleBrief: (market, symbol) => toggleBriefSymbol(market, symbol),
};

export { loadWatchlist, addWatchlistItem, removeWatchlistItem, toggleBriefSymbol };
