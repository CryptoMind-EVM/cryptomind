/**
 * 市場板塊（導覽的「市場」，2026-10-05 導覽整理成 AI 助理／市場／社群）。
 *
 * 美股、台股、加密貨幣，以及港股、日股、韓股、A 股、商品、外匯各自仍是獨立面板
 * （深連結 #crypto、#jpstock 等照舊直達），這支只做兩件事：
 *   1. 各面板的最上方共用一條子分頁列，點了就 switchTab 到該市場（放不下時橫向捲動）；
 *   2. 記住上次用的市場，導覽點「市場」時回到那裡。
 * 哪些子分頁可用由 NavPreferences（nav-config.js）決定——加密貨幣旗標關閉時這裡就不畫它。
 */

const LAST_KEY = 'marketSubTab';

// 子分頁多（九個）時按鈕不縮、不折行，整條列橫向捲動；少的時候 flex-grow 仍會撐滿。
// 不能用 flex-1：它的 basis 是 0，加上全站 button { min-width: 36px }（styles.css）會把每顆壓成 36px 寬
const BTN_BASE = 'flex-grow shrink-0 whitespace-nowrap py-2 px-3 rounded-lg font-bold text-sm transition flex items-center justify-center gap-2';
const BTN_ON = 'bg-primary text-background shadow-md';
const BTN_OFF = 'text-textMuted hover:text-textMain hover:bg-surfaceHighlight';

function esc(value) {
    return String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

/** 子分頁列 HTML（純函式）：members 是 NAV_ITEMS 項目，label 一律經 translate。 */
export function renderMarketBar(activeId, members, translate) {
    const buttons = members
        .map((item) => {
            const on = item.id === activeId;
            const label = translate(item.i18nKey, item.label);
            return (
                `<button type="button" role="tab" aria-selected="${on}" data-market-tab="${esc(item.id)}"` +
                ` data-click="switchTab" data-click-arg="${encodeURIComponent(item.id)}"` +
                ` class="${BTN_BASE} ${on ? BTN_ON : BTN_OFF}">` +
                `<i data-lucide="${esc(item.icon)}" class="w-4 h-4"></i><span>${esc(label)}</span></button>`
            );
        })
        .join('');
    return (
        '<div data-market-subtabs role="tablist" ' +
        'class="flex gap-1 p-1 bg-background/50 border border-borderSubtle rounded-xl mb-4 md:mr-16 overflow-x-auto custom-scrollbar">' +
        buttons +
        '</div>'
    );
}

function translate(key, fallback) {
    const ready = window.I18n && window.I18n.isReady && window.I18n.isReady();
    const text = ready ? window.I18n.t(key) : '';
    return text && text !== key ? text : fallback;
}

function lastMarketTab() {
    try {
        return localStorage.getItem(LAST_KEY) || null;
    } catch (_e) {
        return null; // 隱私模式等：不記憶，每次回第一個子分頁
    }
}

/** 導覽點「市場」要去的分頁 id（沒有任何可用子分頁回 null）。 */
function marketTarget() {
    return window.NavPreferences ? window.NavPreferences.resolveMarketTab(lastMarketTab()) : null;
}

/** 子分頁列橫向捲動時，把目前這一顆捲到中間（不用 scrollIntoView：它會連頁面一起捲）。 */
function centerActive(bar) {
    const on = bar && bar.querySelector('[aria-selected="true"]');
    if (!on || bar.scrollWidth <= bar.clientWidth) return;
    const left = on.getBoundingClientRect().left - bar.getBoundingClientRect().left + bar.scrollLeft;
    bar.scrollLeft = Math.max(0, left - (bar.clientWidth - on.offsetWidth) / 2);
}

/** 進到某個市場分頁時呼叫：記住它、把子分頁列放進它的面板（已經有就更新高亮與可用項）。 */
function showMarketTab(tabId) {
    if (!window.NavPreferences || window.NavPreferences.groupOf(tabId) !== 'market') return;
    try {
        localStorage.setItem(LAST_KEY, tabId);
    } catch (_e) {
        /* 不記憶而已 */
    }
    const shell = document.getElementById(`${tabId}-tab`)?.firstElementChild;
    if (!shell) return;
    const html = renderMarketBar(tabId, window.NavPreferences.marketMembers(), translate);
    const bar = shell.querySelector(':scope > [data-market-subtabs]');
    if (bar) bar.outerHTML = html;
    else shell.insertAdjacentHTML('afterbegin', html);
    window.AppUtils?.refreshIcons?.();
    centerActive(shell.querySelector(':scope > [data-market-subtabs]'));
}

window.MarketHub = { target: marketTarget, show: showMarketTab };

// 換語言：目前這個市場分頁的子分頁列標籤跟著換
window.addEventListener?.('languageChanged', () => {
    const active = document.querySelector('.tab-content:not(.hidden)')?.id?.replace(/-tab$/, '');
    if (active) showMarketTab(active);
});
