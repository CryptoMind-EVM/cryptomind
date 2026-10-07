// ========================================
// sample-portfolio.js - Sample portfolio 示範頁（訪客專用）
// 2026-09-27 上市準備 PR-3（docs/plans/2026-09-27-launch-readiness-design.md §4 PR-3）：
// 讓還沒登入的人看到「CryptoMind 認識你的持倉之後長什麼樣」。資料全是前端靜態 fixture，
// 不打任何 API、也不借帳本分頁的元件（避免跟帳本實作耦合）；日期在 render 時以今天為基準算。
// 合規：分析工具不是投顧——不寫買賣建議、不用「訊號」，頁尾放跟聊天輸入列同一句免責。
// 骨架在 components/tab-sample.js，這裡畫清單內容。
// ========================================

// ── fixture（價格為示意；改數字時損益、占比都是算出來的，不用手動對） ──
export const SAMPLE_HOLDINGS = [
    { symbol: 'BTC', kind: 'crypto', qty: 0.42, avgCost: 58200, price: 97850, source: 'manual' },
    { symbol: 'ETH', kind: 'crypto', qty: 6.5, avgCost: 2450, price: 3620, source: 'base' },
    { symbol: 'SOL', kind: 'crypto', qty: 85, avgCost: 142, price: 188.4, source: 'manual' },
    { symbol: 'ARB', kind: 'crypto', qty: 4200, avgCost: 1.12, price: 0.54, source: 'manual' },
    { symbol: 'NVDA', kind: 'stock', qty: 60, avgCost: 96.5, price: 178.2, source: 'manual' },
    { symbol: 'AAPL', kind: 'stock', qty: 40, avgCost: 188, price: 231.6, source: 'manual' },
];

// offsetDays：距今天幾天；weekdaysOnly：美國市場事件，落在週末就挪到週一（解鎖、個人提醒不挪）。
// 文案在 i18n sample.events.<id>
export const SAMPLE_EVENTS = [
    { id: 'cpi', offsetDays: 1, kind: 'macro', weekdaysOnly: true },
    { id: 'arbUnlock', offsetDays: 3, kind: 'unlock' },
    { id: 'fomc', offsetDays: 5, kind: 'macro', weekdaysOnly: true },
    { id: 'reminder', offsetDays: 7, kind: 'reminder' },
    { id: 'nvdaEarnings', offsetDays: 8, kind: 'earnings', weekdaysOnly: true },
    { id: 'nfp', offsetDays: 12, kind: 'macro', weekdaysOnly: true },
];

// 7 筆判斷、5 筆已評分、3 筆命中；example 的兩筆畫出來（文案在 sample.calls.<id>）
export const SAMPLE_CALLS = [
    { id: 'nvdaEarnings', status: 'hit', example: true },
    { id: 'ethVsBtc', status: 'miss', example: true },
    { status: 'hit' },
    { status: 'hit' },
    { status: 'miss' },
    { status: 'pending' },
    { status: 'pending' },
];

// ── 純計算（tests/js/sample_portfolio.mjs） ──

/** 每檔市值、未實現損益、占比，依市值由大到小；不改傳入的陣列。 */
export function summarizeHoldings(holdings) {
    const rows = holdings.map((h) => {
        const value = h.qty * h.price;
        const cost = h.qty * h.avgCost;
        const pnl = value - cost;
        return { ...h, value, cost, pnl, pnlPct: cost ? (pnl / cost) * 100 : 0 };
    });
    const totalValue = rows.reduce((sum, r) => sum + r.value, 0);
    const totalCost = rows.reduce((sum, r) => sum + r.cost, 0);
    const totalPnl = totalValue - totalCost;
    return {
        rows: rows
            .map((r) => ({ ...r, weightPct: totalValue ? (r.value / totalValue) * 100 : 0 }))
            .sort((a, b) => b.value - a.value),
        totalValue,
        totalCost,
        totalPnl,
        totalPnlPct: totalCost ? (totalPnl / totalCost) * 100 : 0,
    };
}

/**
 * 今天起 days 天內的事件（含今天與第 days 天），帶上本地日期、依日期排序。
 * weekdaysOnly 的事件落在週六／日就挪到週一，offsetDays 跟著改（早報的「幾天後」用它）。
 */
export function upcomingEvents(events, today, days = 14) {
    const base = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    return events
        .map((e) => {
            let offset = e.offsetDays;
            const date = new Date(base.getFullYear(), base.getMonth(), base.getDate() + offset);
            if (e.weekdaysOnly) {
                const shift = date.getDay() === 6 ? 2 : date.getDay() === 0 ? 1 : 0;
                date.setDate(date.getDate() + shift);
                offset += shift;
            }
            return { ...e, offsetDays: offset, date };
        })
        .filter((e) => e.offsetDays >= 0 && e.offsetDays <= days)
        .sort((a, b) => a.offsetDays - b.offsetDays);
}

/** 判斷評分統計：命中率＝命中／已評分（四捨五入到整數 %）。 */
export function scorecardStats(calls) {
    const scored = calls.filter((c) => c.status === 'hit' || c.status === 'miss').length;
    const hits = calls.filter((c) => c.status === 'hit').length;
    return { total: calls.length, scored, hits, hitRate: scored ? Math.round((hits / scored) * 100) : 0 };
}

// ── 畫面 ──

const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ESC_MAP[c]);
}

// 輸出一律再經 esc()，所以插值不讓 i18next 先跳脫一次
function t(key, fallback, vars) {
    const options = vars ? { ...vars, interpolation: { escapeValue: false } } : undefined;
    if (window.I18n && typeof window.I18n.t === 'function') {
        const value = window.I18n.t(key, options);
        if (value && value !== key) return value;
    }
    return vars ? fallback.replace(/\{\{(\w+)\}\}/g, (m, k) => (k in vars ? vars[k] : m)) : fallback;
}

function lang() {
    try {
        return (window.I18n && window.I18n.getLanguage && window.I18n.getLanguage()) || 'en';
    } catch (_e) {
        return 'en';
    }
}

function usd(n, { signed = false, digits } = {}) {
    const abs = Math.abs(n);
    const max = digits != null ? digits : abs < 10 ? 2 : abs < 1000 ? 2 : 0;
    return new Intl.NumberFormat(lang(), {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: max,
        maximumFractionDigits: max,
        signDisplay: signed ? 'exceptZero' : 'auto',
    }).format(n);
}

function pct(n) {
    return new Intl.NumberFormat(lang(), {
        style: 'percent',
        maximumFractionDigits: 1,
        signDisplay: 'exceptZero',
    }).format(n / 100);
}

function qty(n) {
    return new Intl.NumberFormat(lang(), { maximumFractionDigits: 4 }).format(n);
}

const BADGE = 'px-1.5 py-0.5 rounded-full text-[10px] font-medium shrink-0 whitespace-nowrap';
const KIND_BADGE = {
    earnings: 'bg-primary/10 text-primary',
    unlock: 'bg-amber-500/10 text-amber-700 dark:text-amber-400',
    macro: 'bg-surfaceHighlight text-textMuted',
    reminder: 'bg-success/10 text-success',
};
const KIND_FALLBACK = { earnings: 'Earnings', unlock: 'Token unlock', macro: 'Macro', reminder: 'Your reminder' };
const EVENT_FALLBACK = {
    cpi: 'US CPI release (8:30 ET)',
    arbUnlock: 'ARB token unlock — about 92.6M ARB',
    fomc: 'FOMC rate decision',
    reminder: 'Reminder: review your NVDA call before earnings',
    nvdaEarnings: 'NVDA earnings (after the close)',
    nfp: 'US jobs report (NFP)',
};
const CALL_FALLBACK = {
    nvdaEarnings: "NVDA closes higher five trading days after last quarter's earnings",
    nvdaEarningsResult: 'Scored: NVDA +6.2% over the window',
    ethVsBtc: 'ETH outperforms BTC over 30 days',
    ethVsBtcResult: 'Scored: ETH trailed BTC by 3.1 points',
};
// 早報每一點對到行事曆的哪個事件：「明天」「3 天後」跟行事曆用同一個日期算，不會對不上
const BRIEF_ITEMS = [
    ['cpi', 'cpi', 'US CPI lands {{when}} at 8:30 ET — the macro print most likely to move BTC and NVDA.'],
    ['arb', 'arbUnlock', 'ARB unlocks about 92.6M tokens {{when}}, roughly 2% of circulating supply. Past unlocks have come with higher volatility.'],
    ['nvda', 'nvdaEarnings', "NVDA reports {{when}}. At about 10% of the portfolio, it's your largest stock position."],
    ['call', null, 'Your ETH-vs-BTC call was scored overnight: missed by 3.1 points.'],
];
const STATUS_BADGE = {
    hit: ['bg-success/10 text-success', 'sample.statusHit', 'Right'],
    miss: ['bg-danger/10 text-danger', 'sample.statusMiss', 'Missed'],
    pending: ['bg-surfaceHighlight text-textMuted', 'sample.statusPending', 'Pending'],
};

function pnlClass(n) {
    if (n > 0) return 'text-success';
    if (n < 0) return 'text-danger';
    return 'text-textMuted';
}

function renderHoldings() {
    const s = summarizeHoldings(SAMPLE_HOLDINGS);
    const summary = document.getElementById('sample-summary');
    if (summary) {
        summary.innerHTML = `
            <div class="rounded-2xl bg-surfaceHighlight/60 p-3.5 min-w-0">
                <p class="text-xs text-textMuted">${esc(t('sample.totalValue', 'Total value'))}</p>
                <p class="mt-1 text-lg md:text-xl font-semibold text-textMain tabular-nums truncate">${esc(usd(s.totalValue, { digits: 0 }))}</p>
            </div>
            <div class="rounded-2xl bg-surfaceHighlight/60 p-3.5 min-w-0">
                <p class="text-xs text-textMuted">${esc(t('sample.unrealizedPnl', 'Unrealized P/L'))}</p>
                <p class="mt-1 text-lg md:text-xl font-semibold tabular-nums truncate ${pnlClass(s.totalPnl)}">${esc(usd(s.totalPnl, { signed: true, digits: 0 }))}</p>
                <p class="text-xs tabular-nums ${pnlClass(s.totalPnl)}">${esc(pct(s.totalPnlPct))}</p>
            </div>`;
    }
    const list = document.getElementById('sample-holdings');
    if (!list) return;
    const avgLabel = t('sample.avg', 'avg');
    list.innerHTML = s.rows
        .map((r) => {
            const kind =
                r.kind === 'stock' ? t('sample.kindStock', 'US stock') : t('sample.kindCrypto', 'Crypto');
            const source =
                r.source === 'base'
                    ? `<span class="${BADGE} bg-primary/10 text-primary">${esc(t('sample.sourceBase', 'Base wallet'))}</span>`
                    : '';
            return `
            <li class="flex items-center gap-3 py-3 border-b border-borderSubtle last:border-0">
                <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-1.5 flex-wrap">
                        <span class="text-sm font-semibold text-textMain">${esc(r.symbol)}</span>
                        <span class="${BADGE} bg-surfaceHighlight text-textMuted">${esc(kind)}</span>
                        ${source}
                    </div>
                    <p class="mt-0.5 text-xs leading-snug text-textMuted tabular-nums">${esc(qty(r.qty))} · ${esc(avgLabel)} ${esc(usd(r.avgCost))} → ${esc(usd(r.price))}</p>
                </div>
                <div class="text-right shrink-0">
                    <p class="text-sm font-semibold text-textMain tabular-nums">${esc(usd(r.value, { digits: 0 }))}</p>
                    <p class="text-xs tabular-nums ${pnlClass(r.pnl)}">${esc(usd(r.pnl, { signed: true, digits: 0 }))} · ${esc(pct(r.pnlPct))}</p>
                </div>
            </li>`;
        })
        .join('');
}

function renderCalendar(events) {
    const list = document.getElementById('sample-calendar');
    if (!list) return;
    const weekday = new Intl.DateTimeFormat(lang(), { weekday: 'short' });
    const monthDay = new Intl.DateTimeFormat(lang(), { month: 'short', day: 'numeric' });
    const relative = new Intl.RelativeTimeFormat(lang(), { numeric: 'auto' });
    list.innerHTML = events
        .map(
            (e) => `
            <li class="flex items-start gap-3 py-2.5">
                <div class="w-12 shrink-0 rounded-xl bg-surfaceHighlight/70 py-1.5 text-center">
                    <p class="text-[10px] uppercase tracking-wide text-textMuted">${esc(weekday.format(e.date))}</p>
                    <p class="mt-0.5 text-base font-semibold leading-none text-textMain tabular-nums">${esc(e.date.getDate())}</p>
                </div>
                <div class="min-w-0 flex-1">
                    <p class="text-sm text-textMain leading-snug">${esc(t(`sample.events.${e.id}`, EVENT_FALLBACK[e.id]))}</p>
                    <div class="mt-1 flex items-center gap-1.5 flex-wrap">
                        <span class="${BADGE} ${KIND_BADGE[e.kind]}">${esc(t(`sample.kinds.${e.kind}`, KIND_FALLBACK[e.kind]))}</span>
                        <span class="text-[11px] text-textMuted">${esc(monthDay.format(e.date))} · ${esc(relative.format(e.offsetDays, 'day'))}</span>
                    </div>
                </div>
            </li>`
        )
        .join('');
}

function renderScorecard() {
    const stats = scorecardStats(SAMPLE_CALLS);
    const tiles = document.getElementById('sample-score-stats');
    if (tiles) {
        const tile = (label, value) => `
            <div class="rounded-2xl bg-surfaceHighlight/60 p-3 text-center min-w-0">
                <p class="text-lg font-semibold text-textMain tabular-nums">${esc(value)}</p>
                <p class="text-[11px] text-textMuted truncate">${esc(label)}</p>
            </div>`;
        tiles.innerHTML =
            tile(t('sample.statCalls', 'Calls'), stats.total) +
            tile(t('sample.statScored', 'Scored'), stats.scored) +
            tile(t('sample.statHitRate', 'Hit rate'), `${stats.hitRate}%`);
    }
    const list = document.getElementById('sample-calls');
    if (!list) return;
    list.innerHTML = SAMPLE_CALLS.filter((c) => c.example)
        .map((c) => {
            const [cls, key, fallback] = STATUS_BADGE[c.status];
            return `
            <li class="rounded-2xl border border-borderSubtle p-3.5">
                <div class="flex items-start justify-between gap-3">
                    <p class="text-sm text-textMain leading-snug">${esc(t(`sample.calls.${c.id}`, CALL_FALLBACK[c.id]))}</p>
                    <span class="${BADGE} ${cls}">${esc(t(key, fallback))}</span>
                </div>
                <p class="mt-1 text-xs text-textMuted tabular-nums">${esc(t(`sample.calls.${c.id}Result`, CALL_FALLBACK[`${c.id}Result`]))}</p>
            </li>`;
        })
        .join('');
}

function renderBrief(events) {
    const el = document.getElementById('sample-brief-date');
    if (el) {
        el.textContent = new Intl.DateTimeFormat(lang(), {
            weekday: 'long',
            month: 'long',
            day: 'numeric',
        }).format(new Date());
    }
    const list = document.getElementById('sample-brief-items');
    if (!list) return;
    const relative = new Intl.RelativeTimeFormat(lang(), { numeric: 'auto' });
    list.innerHTML = BRIEF_ITEMS.map(([key, eventId, fallback]) => {
        const event = eventId && events.find((e) => e.id === eventId);
        const vars = event ? { when: relative.format(event.offsetDays, 'day') } : undefined;
        return `
            <li class="flex gap-2.5 text-sm leading-relaxed text-textMain">
                <span class="mt-2 w-1.5 h-1.5 rounded-full bg-primary shrink-0"></span>
                <span>${esc(t(`sample.brief.${key}`, fallback, vars))}</span>
            </li>`;
    }).join('');
}

const SampleTab = {
    _listening: false,

    init() {
        this.render();
        if (this._listening || typeof window.addEventListener !== 'function') return;
        this._listening = true;
        // 語言切換：spa.js 只替登入用戶重跑分頁，這頁只有訪客會看——自己重畫
        window.addEventListener('languageChanged', () => {
            if (this._isVisible()) this.render();
        });
        // 在這頁登入（Make it yours）→ 直接帶去真的帳本
        window.addEventListener('auth:changed', () => {
            const loggedIn = window.AuthManager && window.AuthManager.isLoggedIn();
            if (loggedIn && this._isVisible() && typeof window.switchTab === 'function') {
                window.switchTab('journal');
            }
        });
    },

    _isVisible() {
        const tab = document.getElementById('sample-tab');
        return !!tab && !tab.classList.contains('hidden');
    },

    render() {
        const events = upcomingEvents(SAMPLE_EVENTS, new Date());
        renderHoldings();
        renderCalendar(events);
        renderScorecard();
        renderBrief(events);
        const root = document.getElementById('sample-tab');
        if (root && typeof window.createIconsIn === 'function') window.createIconsIn(root);
    },
};

window.SampleTab = SampleTab;
export { SampleTab };
