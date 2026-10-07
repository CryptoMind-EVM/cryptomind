// ========================================
// watchlist-sync.js — 各市場分頁的自選 ↔ 伺服器（/api/watchlist，c056；2026-09-27）
//
// 登入的人：自選存在伺服器，換裝置也在；早報每天列、設定頁「我的自選」看得到同一份。
// 沒登入：照舊只存在這台裝置的 localStorage。
//
// 規則：
//   - 分頁打開時 hydrate()：伺服器有這個市場的清單就用伺服器的。
//   - 伺服器沒有、而這台裝置的清單跟預設不一樣（使用者改過）→ 搬上去一次。
//   - 沒動過的預設清單不上傳——預設不算使用者選的，早報也不該被塞滿。
//   - 使用者在分頁加／刪／勾選後 push()：整份清單寫回伺服器（同一市場 600ms 內合併一次）。
// 加密貨幣分頁存的是交易對（BTC-USDT），伺服器存幣種（BTC），這裡轉換。
// ========================================

const TTL_MS = 5000;
const DEBOUNCE_MS = 600;
const _state = (window.__watchlistSync = window.__watchlistSync || {
    uid: null,
    at: 0,
    promise: null,
    items: null,
    timers: {},
});

function _uid() {
    try {
        const AM = window.AuthManager;
        if (!AM || typeof AM.isLoggedIn !== 'function' || !AM.isLoggedIn()) return null;
        const u = AM.currentUser || {};
        return u.user_id || u.uid || null;
    } catch (e) {
        return null;
    }
}

function _fromServer(market, symbol) {
    return market === 'crypto' ? `${symbol}-USDT` : symbol;
}

function _norm(market, symbol) {
    const s = String(symbol || '').toUpperCase().trim();
    return market === 'crypto' ? s.replace(/[-/]?(USDT|USDC|USD)$/, '') : s;
}

function sameList(a, b, market) {
    const x = new Set((a || []).map((s) => _norm(market, s)));
    const y = new Set((b || []).map((s) => _norm(market, s)));
    if (x.size !== y.size) return false;
    for (const s of x) if (!y.has(s)) return false;
    return true;
}

async function _fetchAll(uid) {
    if (_state.uid === uid && _state.promise && Date.now() - _state.at < TTL_MS) return _state.promise;
    _state.uid = uid;
    _state.at = Date.now();
    _state.promise = window.AppAPI.get('/api/watchlist')
        .then((res) => {
            _state.items = (res && res.items) || [];
            return _state.items;
        })
        .catch(() => {
            _state.promise = null;
            return null;
        });
    return _state.promise;
}

function _serverList(market) {
    return (_state.items || []).filter((i) => i.market === market).map((i) => _fromServer(market, i.symbol));
}

async function _put(market, list) {
    const res = await window.AppAPI.put(`/api/watchlist/${encodeURIComponent(market)}`, { symbols: list });
    const saved = (res && res.symbols) || [];
    _state.items = (_state.items || [])
        .filter((i) => i.market !== market)
        .concat(saved.map((symbol) => ({ market, symbol })));
    if (res && res.truncated && typeof window.showToast === 'function') {
        window.showToast(
            window.I18n ? window.I18n.t('settings.watchlist.full', { max: 50 }) : 'Watchlist is full',
            'error'
        );
    }
    return saved;
}

/** 分頁打開時呼叫：回伺服器上這個市場的清單（分頁格式）；沒登入／伺服器沒有 → null（用本機的）。 */
async function hydrate(market, localList, defaults) {
    const uid = _uid();
    if (!uid || !window.AppAPI) return null;
    const items = await _fetchAll(uid);
    if (!items) return null;
    const server = _serverList(market);
    if (server.length) return server;
    // 伺服器沒有這個市場：這台裝置改過（跟預設不同）就搬上去一次
    if (localList && localList.length && !sameList(localList, defaults || [], market)) {
        try {
            await _put(market, localList);
        } catch (e) {
            /* 下次打開分頁再試 */
        }
    }
    return null;
}

/** 使用者在分頁改了清單後呼叫：整份寫回伺服器（沒登入不做事）。 */
function push(market, list, defaults) {
    if (!_uid() || !window.AppAPI) return;
    const hasServer = (_state.items || []).some((i) => i.market === market);
    // 伺服器沒有、而清單還是預設：沒動過，不存
    if (!hasServer && sameList(list, defaults || [], market)) return;
    clearTimeout(_state.timers[market]);
    const snapshot = [...(list || [])];
    _state.timers[market] = setTimeout(() => {
        _put(market, snapshot).catch(() => {
            /* 網路失敗：本機仍有這份，下次改動再同步 */
        });
    }, DEBOUNCE_MS);
}

window.WatchlistSync = { hydrate, push, sameList };
export { hydrate, push, sameList };
