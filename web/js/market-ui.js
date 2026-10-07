/**
 * 港／日／韓／A 股、商品、外匯分頁共用的小零件（2026-10-05 市場板塊整理）。
 *
 *   - 資料來源標示：每個分頁底部一行，寫清楚報價、新聞、公告各從哪來（都是免費來源，可能延遲）
 *   - 自選卡片補資訊：成交量、當日區間、52 週區間（後端 fast_info 順手帶回，不另外打請求）
 *   - 指數格子欄數依指數數量調整（只有兩個指數時不留空格）
 *
 * 各市場分頁是各自的模組，這裡只放它們一模一樣的那幾段，不改它們的結構。
 */

function tr(key, fallback, vars) {
    const ready = window.I18n && window.I18n.isReady && window.I18n.isReady();
    const text = ready ? window.I18n.t(key, vars) : '';
    return text && text !== key ? text : fallback;
}

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

const COMPACT = (() => {
    try {
        return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 2 });
    } catch (_e) {
        return null; // 很舊的瀏覽器：退回完整數字
    }
})();

/** 成交量：9210000 → 9.21M */
export function compactNumber(n) {
    return COMPACT ? COMPACT.format(n) : String(Math.round(n));
}

/** 價位：位數依大小調整（276,000／2,867.5／4.43），不補多餘的 0 */
export function priceText(n) {
    const abs = Math.abs(n);
    const digits = abs >= 10000 ? 0 : abs >= 100 ? 1 : abs >= 1 ? 2 : 4;
    return Number(n).toLocaleString('en', { maximumFractionDigits: digits });
}

/**
 * 自選卡片下方的補充資訊（純函式）：
 *   第一行：成交量、今日區間；第二行：52 週區間條（目前價在區間的位置）。
 * 後端沒帶的欄位（指數沒有成交量、期貨沒有 52 週之類）整段略過，全部沒有就回空字串。
 */
export function quoteExtrasHtml(q) {
    if (!q) return '';
    const parts = [];
    if (isNum(q.volume) && q.volume > 0) {
        parts.push(`<span>${tr('stock.volume', 'Vol')} ${compactNumber(q.volume)}</span>`);
    }
    if (isNum(q.dayLow) && isNum(q.dayHigh)) {
        parts.push(`<span>${tr('stock.dayRange', 'Day')} ${priceText(q.dayLow)} – ${priceText(q.dayHigh)}</span>`);
    }
    const row = parts.length
        ? `<div class="flex items-center justify-between gap-2 mt-2 text-[10px] text-textMuted font-mono">${parts.join('')}</div>`
        : '';

    let bar = '';
    if (isNum(q.yearLow) && isNum(q.yearHigh) && q.yearHigh > q.yearLow && isNum(q.price)) {
        const pct = Math.min(100, Math.max(0, ((q.price - q.yearLow) / (q.yearHigh - q.yearLow)) * 100));
        bar =
            '<div class="mt-2">' +
            '<div class="flex justify-between text-[9px] text-textMuted mb-1 font-mono">' +
            `<span>${tr('stock.low52w', '52W low')} ${priceText(q.yearLow)}</span>` +
            `<span>${tr('stock.high52w', '52W high')} ${priceText(q.yearHigh)}</span></div>` +
            '<div class="h-1.5 rounded-full bg-surfaceHighlight overflow-hidden">' +
            `<div class="h-full rounded-full bg-gradient-to-r from-danger via-yellow-500 to-success" style="width:${pct.toFixed(0)}%"></div>` +
            '</div></div>';
    }
    return row + bar;
}

/** 指數格子的欄數：兩個並排、三個以上桌機三欄（原本固定三欄，兩個指數會留一個空格） */
export function indexGridClass(count) {
    const cols = count <= 1 ? 'grid-cols-1' : count === 2 ? 'grid-cols-2' : 'grid-cols-1 sm:grid-cols-3';
    return `grid ${cols} gap-3 px-1`;
}

function fitIndexGrid(container, count) {
    if (container) container.className = indexGridClass(count);
}

/** 「資料來源：…」整行文字（key 見 stock.source.*） */
export function sourceText(key) {
    return `${tr('stock.source.label', 'Data sources')}: ${tr(`stock.source.${key}`, '')}`.trim();
}

/** 在分頁的行情頁與 AI 快評頁底部各放一行資料來源（已經有就只更新文字）。 */
function mountSource(tabId, key) {
    ['market', 'pulse'].forEach((area) => {
        const root = document.querySelector(`#${tabId}-${area}-content > div`);
        if (!root) return;
        let note = root.querySelector(':scope > [data-market-source]');
        if (!note) {
            note = document.createElement('p');
            note.className = 'text-[10px] text-textMuted leading-relaxed px-1 pt-3 mt-2 border-t border-borderSubtle';
            root.appendChild(note);
        }
        note.setAttribute('data-market-source', key);
        note.textContent = sourceText(key);
    });
}

const todayLabel = () => tr('stock.today', 'Today');

window.MarketUI = { quoteExtrasHtml, fitIndexGrid, mountSource, todayLabel };

// 換語言：畫面上的來源標示跟著換
window.addEventListener?.('languageChanged', () => {
    document.querySelectorAll('[data-market-source]').forEach((el) => {
        el.textContent = sourceText(el.getAttribute('data-market-source'));
    });
});
