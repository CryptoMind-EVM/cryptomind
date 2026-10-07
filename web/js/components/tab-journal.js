import { buildMonthGrid, groupByDate, cellSummary, weekdayLabels, monthLabel, monthKey, shiftMonth, isoDate } from '../journal-calendar.js';
import { userFacingMessage } from '../error-message.js';
/* Journal Tab — 統一帳本 Dashboard（投資＋支出＋收入）。
 *
 * 設計：docs/plans/2026-08-21-investment-journal-design.md
 * API：/api/journal/*（entries/entry/summary）
 * Agent 工具：record_entry / query_ledger / get_portfolio_pnl
 *
 * 功能：
 * - 4 張指標卡（支出總額/收入總額/投資損益/交易筆數）
 * - 類別 chips 篩選
 * - 類型+天數+搜尋 篩選
 * - 類別長條圖（Dashboard 特色）
 * - 條目列表（icon/類別/金額/幣別/換算/備註/來源）
 * - 手動新增 Modal（expense/income/trade）
 */

// ── 類別定義（icon + i18n key）────────────────────────────────
// UX 第二輪：支出/收入各自獨立清單；trade 不選分類（market/幣別/標的
// 已是足夠維度）。legacy investment/income 僅供舊資料顯示，不進新表單/chips。

const _CAT_KEY = (id) => 'journal.cat_' + id;
const EXPENSE_CATEGORIES = {
    food: { icon: 'utensils', label: 'Food' },
    transport: { icon: 'car', label: 'Transport' },
    housing: { icon: 'home', label: 'Housing' },
    shopping: { icon: 'shopping-bag', label: 'Shopping' },
    entertainment: { icon: 'film', label: 'Entertainment' },
    medical: { icon: 'heart-pulse', label: 'Medical' },
    education: { icon: 'book-open', label: 'Education' },
    other: { icon: 'package', label: 'Other' },
};
const INCOME_CATEGORIES = {
    salary: { icon: 'briefcase', label: 'Salary' },
    bonus: { icon: 'gift', label: 'Bonus' },
    investment_income: { icon: 'trending-up', label: 'Investment Income' },
    other: { icon: 'banknote', label: 'Other' },
};
// 舊資料的 category 值（顯示用）
const LEGACY_CATEGORIES = {
    investment: { icon: 'trending-up', label: 'Investment' },
    income: { icon: 'banknote', label: 'Income' },
};
// 列表/歷史渲染的顯示對照（含 legacy）；未知值＝自訂類別，顯示原文
const CATEGORY_LOOKUP = {
    ...EXPENSE_CATEGORIES, ...INCOME_CATEGORIES, ...LEGACY_CATEGORIES,
};

// 報表基準幣的預設值（使用者未設定時＝後端 TradeJournalRepo 的兜底值）。
// 實際值放 JournalState.baseCurrency，由 /api/journal/base-currency 載入——
// 自填匯率的語義是「1 單位計價幣 = ? 基準幣」，同幣別時不需要也不顯示匯率欄。
const DEFAULT_BASE_CURRENCY = 'TWD';

// 基準幣的顯示符號（JPY/CNY 同為 ¥，以 CN¥ 區分，避免兩種幣別看起來一樣）
const BASE_SYMBOLS = {
    TWD: 'NT$', USD: '$', HKD: 'HK$', JPY: '¥', CNY: 'CN¥', EUR: '€',
};

const TYPE_META = {
    trade: { icon: 'trending-up', color: 'text-primary' },
    expense: { icon: 'minus-circle', color: 'text-danger' },
    income: { icon: 'plus-circle', color: 'text-success' },
    // 兜底：未知 entry_type 不得讓整個列表渲染中斷
    // （TYPE_META[x] || TYPE_META.other 曾解析成 undefined → .color 拋錯）
    other: { icon: 'minus-circle', color: 'text-textMuted' },
};

// ── State ─────────────────────────────────────────────────────

const JournalState = {
    entries: [],
    totals: null, // 同篩選下全部條目的收支合計（/api/journal/entries 的 totals）
    summary: [],
    positions: [],
    calendar: [],
    scorecard: null, // 判斷成績（2026-09-13）
    calls: [], // v2 明確喊單
    callsCancelWindowHours: 24,
    calendarMonth: null, // 'YYYY-MM'（月曆目前看的月份；null＝本月）
    calendarSelected: null, // 'YYYY-MM-DD'（點選的日期；預設今天）
    calendarEditing: null, // 正在編輯的自訂事件（null＝表單是「新增」）
    holdings: null, // 綁定錢包鏈上餘額（2026-09-12）
    view: 'cashflow', // UX 第二輪：cashflow（收支總覽）| invest（投資持倉）| calendar（行事曆，2026-09-30 獨立出來）
    // 報表基準幣（c039，per-user）——換算額與彙總都以此幣別呈現
    baseCurrency: DEFAULT_BASE_CURRENCY,
    baseCurrencyOptions: [],
    filters: {
        entry_type: '',
        category: '',
        days: '30',
        // 特定月份（'YYYY-MM'，month picker）——有值時優先於 days
        month: '',
        search: '',
    },
    formOpen: false,
    // c037 版本史：回收筒檢視 + 時間線 modal 狀態
    showDeleted: false,
    deletedEntries: [],
    // 回收筒載入狀態：ready | loading | error——API 失敗不得偽裝成「空的」
    trashState: 'ready',
    // 編輯 modal 目前條目 id（null=關閉）
    editEntryId: null,
    historyEntryId: null,
};

// ── Main Component ────────────────────────────────────────────

const JournalTab = {
    async init() {
        this._buildCategoryChips();
        this._bindFilters();
        this._initMonthPicker();
        this._bindLanguageListener();
        this._syncViewToggle();
        this._syncTrashToggle();
        this._bindFormCategory();
        this._bindRateRows();
        await this._loadBaseCurrency();
        await this.refresh();
    },

    _bindLanguageListener() {
        // spa.js 的 SELF_HANDLED 把 journal 列為「自行處理語言切換」，但此前
        // 沒掛監聽——切語言時帳本整頁停留原語言（2026-08-24 DANNY 回報）。
        // init 可重入（executeTabSwitch 重跑），flag 防監聽器重複註冊。
        if (this._langBound) return;
        this._langBound = true;
        window.addEventListener('languageChanged', () => {
            this._buildCategoryChips();
            this.refresh();
        });
    },

    // ── Data Loading ─────────────────────────────────────────

    _dateRange() {
        // 時間篩選統一出口：特定月份（picker）優先於快捷 select。
        // 帳本回顧以「絕對月份」為一等公民（業界慣例：鯊魚記帳/Moze 等
        // 均以月為報表單位）——使用者問的是「上個月花多少」，不是
        // 「最近30天」。回 {date_from, date_to}（ISO，可能缺一或皆缺）。
        const f = JournalState.filters;
        const m = f.month;
        if (m) {
            const [y, mo] = m.split('-').map(Number);
            if (y && mo >= 1 && mo <= 12) {
                const from = new Date(y, mo - 1, 1);
                const to = new Date(y, mo, 0, 23, 59, 59, 999); // 本月月底
                return { date_from: from.toISOString(), date_to: to.toISOString() };
            }
        }
        if (f.days === 'month') {
            const now = new Date();
            return {
                date_from: new Date(now.getFullYear(), now.getMonth(), 1).toISOString(),
            };
        }
        if (f.days === 'prev_month') {
            const now = new Date();
            const from = new Date(now.getFullYear(), now.getMonth() - 1, 1);
            const to = new Date(now.getFullYear(), now.getMonth(), 0, 23, 59, 59, 999);
            return { date_from: from.toISOString(), date_to: to.toISOString() };
        }
        if (f.days === 'year') {
            const now = new Date();
            return {
                date_from: new Date(now.getFullYear(), 0, 1).toISOString(),
            };
        }
        const days = parseInt(f.days, 10) || 0;
        if (days > 0) {
            return {
                date_from: new Date(Date.now() - days * 86400000).toISOString(),
            };
        }
        return {};
    },

    async _loadEntries() {
        const f = JournalState.filters;
        const params = new URLSearchParams();
        // UX 第二輪：視圖決定類型——收支=expense+income（逗號子集）、投資=trade
        if (JournalState.view === 'invest') {
            params.set('entry_type', 'trade');
        } else if (!f.entry_type) {
            params.set('entry_type', 'expense,income');
        } else if (f.entry_type) {
            params.set('entry_type', f.entry_type);
        }
        if (f.category) params.set('category', f.category);
        if (f.search) params.set('search', f.search);
        const range = this._dateRange();
        if (range.date_from) params.set('date_from', range.date_from);
        if (range.date_to) params.set('date_to', range.date_to);
        params.set('limit', '100');

        // 快速切篩選／視圖時，慢的舊回應晚到會蓋掉新條件的結果——只收最後一次
        const seq = (this._entriesSeq = (this._entriesSeq || 0) + 1);
        try {
            const res = await AppAPI.get('/api/journal/entries?' + params.toString());
            if (seq !== this._entriesSeq) return;
            JournalState.entries = res.entries || [];
            // 同篩選下「全部」條目的合計（列表只有前 100 筆，拿它加總會少算）
            JournalState.totals = res.totals || null;
        } catch (e) {
            if (seq !== this._entriesSeq) return;
            console.error('[Journal] load entries failed:', e);
            JournalState.entries = [];
            JournalState.totals = null;
        }
    },

    async _loadSummary() {
        const f = JournalState.filters;
        const params = new URLSearchParams();
        // 收支視圖的類別彙總不含投資（trade 類別是冗餘維度）
        if (!f.entry_type) params.set('entry_type', 'expense,income');
        else params.set('entry_type', f.entry_type);
        const range = this._dateRange();
        if (range.date_from) params.set('date_from', range.date_from);
        if (range.date_to) params.set('date_to', range.date_to);
        const seq = (this._summarySeq = (this._summarySeq || 0) + 1);
        try {
            const res = await AppAPI.get('/api/journal/summary?' + params.toString());
            if (seq !== this._summarySeq) return;
            JournalState.summary = this._mergeSummaryByCategory(res.summary || []);
        } catch (e) {
            if (seq !== this._summarySeq) return;
            JournalState.summary = [];
        }
    },

    _mergeSummaryByCategory(rows) {
        // 後端依 category＋原幣別分組（trade_journal_repo.get_category_summary），
        // 同一類別記過兩種幣別就會拆成兩列、畫出兩條「餐飲」。total 是
        // SUM(COALESCE(converted_amount, …))＝已換算成基準幣，直接相加即可。
        // 基準幣不同的列不合併（後端刻意分組讓混合狀態現形，不能在這裡無聲加總）
        const byCat = new Map();
        for (const r of rows) {
            const total = parseFloat(r.total) || 0;
            const count = parseInt(r.count, 10) || 0;
            const key = `${r.category}\u0000${r.base_currency || ''}`;
            const prev = byCat.get(key);
            if (prev) {
                prev.total += total;
                prev.count += count;
            } else {
                byCat.set(key, { ...r, total, count });
            }
        }
        return [...byCat.values()];
    },

    // ── Rendering ────────────────────────────────────────────

    _renderSummary() {
        const container = document.getElementById('journal-summary');
        if (!container) return;

        // 彙總計算：後端的 totals 是整個篩選範圍的 SQL 合計；舊回應沒有才退回
        // 拿列表加總（列表 limit 100，超過就少算——2026-09-25 盤查）
        let totalExpense = 0;
        let totalIncome = 0;
        const totals = JournalState.totals;

        if (totals) {
            totalExpense = parseFloat(totals.expense) || 0;
            totalIncome = parseFloat(totals.income) || 0;
        } else {
            for (const e of JournalState.entries) {
                // converted_amount 後端已是「price x quantity x rate」的總額
                // （trade_journal_repo.py:160/254）——再乘一次 quantity 會讓總支出/
                // 總收入放大 quantity 倍（2026-08-24 review：3 份 100 USD 記成 28,800）。
                const conv = parseFloat(e.converted_amount || 0);
                const amount = conv > 0
                    ? conv
                    : parseFloat(e.price || 0) * parseFloat(e.quantity || 1);
                if (e.entry_type === 'expense') totalExpense += amount;
                else if (e.entry_type === 'income') totalIncome += amount;
            }
        }

        // UX 第二輪：收支視圖第 4 張卡＝最大類別（投資筆數移至投資視圖）
        let topCat = null;
        for (const s of JournalState.summary) {
            if (!topCat || parseFloat(s.total) > parseFloat(topCat.total)) topCat = s;
        }
        const topCatText = topCat && parseFloat(topCat.total) > 0
            ? this._catDisplay(topCat.category).label : '—';

        // 先取整再判斷正負：顯示只到整數，-0.3 會變成紅色的「-NT$0」
        const net = Math.round(totalIncome - totalExpense) || 0;
        const cards = [
            { label: 'journal.totalExpenses', value: totalExpense, icon: 'minus-circle', color: 'text-danger' },
            { label: 'journal.totalIncome', value: totalIncome, icon: 'plus-circle', color: 'text-success' },
            { label: 'journal.netFlow', value: net, icon: 'wallet', color: net >= 0 ? 'text-success' : 'text-danger' },
            { label: 'journal.topCategory', text: topCatText, icon: 'chart-pie', color: 'text-primary' },
        ];

        // 金額：負號放在幣別符號前（-NT$12,268 而非 NT$-12,268）；手機卡片只有
        // ~130px，七位數以上會被 truncate 成「NT$12,353…」——依長度縮字級
        //（2026-09-12 擬真資料走查）
        const money = (v) => {
            const abs = Math.abs(v).toLocaleString('en', { maximumFractionDigits: 0 });
            return (v < 0 ? '-' : '') + this._sym() + abs;
        };
        const sizeFor = (text) => text.length > 13 ? 'text-sm' : text.length > 10 ? 'text-base' : 'text-xl';
        container.innerHTML = cards.map(c => {
            const text = c.text !== undefined ? c.text : money(c.value);
            return `
            <div class="bg-surface border border-borderLight rounded-2xl p-4 min-w-0">
                <div class="flex items-center gap-2 text-textMuted text-xs">
                    <i data-lucide="${c.icon}" class="w-4 h-4 ${c.color}"></i>
                    <span class="truncate">${this._catLabel(c.label)}</span>
                </div>
                <div class="mt-2 ${sizeFor(text)} font-bold ${c.color} truncate tabular-nums" title="${this._esc(text)}">
                    ${text}
                </div>
            </div>
        `;
        }).join('');
    },

    _renderBreakdown() {
        const container = document.getElementById('journal-category-breakdown');
        if (!container) return;

        let data = JournalState.summary.filter(s => parseFloat(s.total) > 0);
        if (!data.length) {
            container.innerHTML = '';
            return;
        }

        // UX 第二輪：Top 8；其餘（含單筆自訂類別）加總併入「其他」——
        // 防止自訂類別長尾碎片化佔比圖
        data = [...data].sort((a, b) => parseFloat(b.total) - parseFloat(a.total));
        const TOP_N = 8;
        const top = data.slice(0, TOP_N);
        const rest = data.slice(TOP_N);
        const rows = top.filter(r => r.category !== 'other');
        const otherTotal = rest.reduce((s, x) => s + parseFloat(x.total), 0)
            + top.filter(r => r.category === 'other')
                .reduce((s, x) => s + parseFloat(x.total), 0);
        if (otherTotal > 0) rows.push({ category: 'other', total: otherTotal });
        // 合併後按值重排——「其他」若擠進前段，append 在尾會讓最寬的
        // 條排在小的下面（視覺順序與數值矛盾）
        rows.sort((a, b) => parseFloat(b.total) - parseFloat(a.total));

        const max = Math.max(...rows.map(d => parseFloat(d.total)));

        container.innerHTML = rows.map(item => {
            const cat = this._catDisplay(item.category);
            const val = parseFloat(item.total);
            const pct = max > 0 ? (val / max * 100).toFixed(0) : 0;
            return `
                <div class="flex items-center gap-3">
                    <div class="flex items-center gap-1.5 w-28 shrink-0 text-xs text-textMuted">
                        <i data-lucide="${cat.icon}" class="w-3.5 h-3.5"></i>
                        <span class="truncate">${cat.label}</span>
                    </div>
                    <div class="flex-1 h-2.5 bg-surfaceHighlight rounded-full overflow-hidden">
                        <div class="h-full bg-primary/60 rounded-full transition-all" style="width:${pct}%"></div>
                    </div>
                    <div class="text-xs text-secondary font-medium w-20 text-right">${this._sym()}${val.toLocaleString('en', { maximumFractionDigits: 0 })}</div>
                </div>
            `;
        }).join('');
    },

    _renderList() {
        const container = document.getElementById(
            JournalState.view === 'invest' ? 'journal-invest-list' : 'journal-list'
        );
        if (!container) return;

        // 回收筒檢視（c037）：渲染已刪條目＋救回鈕（僅收支視圖）。
        // 載入中/載入失敗必須如實呈現——API 掛掉時顯示「空的」會讓使用者
        // 誤以為已刪條目永久消失（2026-08-26 review W3）
        if (JournalState.view === 'cashflow' && JournalState.showDeleted) {
            if (JournalState.trashState === 'loading') {
                container.innerHTML = '<div class="text-center text-textMuted py-8 text-sm">' +
                    this._t('journal.loading', 'Loading...') + '</div>';
                return;
            }
            if (JournalState.trashState === 'error') {
                container.innerHTML = `
                    <div class="text-center py-8 text-sm text-danger">
                        ${this._esc(this._t('journal.trashLoadError', 'Failed to load trash'))}
                    </div>
                    <div class="text-center">
                        <button data-click="Journal.toggleTrash"
                            class="text-xs px-3 py-1.5 rounded-lg border border-borderSubtle text-textMuted hover:text-primary hover:border-primary/30 transition">
                            ${this._esc(this._t('journal.retry', 'Retry'))}
                        </button>
                    </div>`;
                return;
            }
            if (!JournalState.deletedEntries.length) {
                container.innerHTML = '<div class="text-center text-textMuted py-8 text-sm">' +
                    this._t('journal.emptyTrash', 'Trash is empty') + '</div>';
                return;
            }
            container.innerHTML = JournalState.deletedEntries.map(e => {
                const cat = CATEGORY_LOOKUP[e.category] || { icon: 'package', label: e.category };
                const amount = parseFloat(e.price);
                const date = e.deleted_at ? new Date(e.deleted_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '';
                const undeleteLabel = this._t('journal.undelete', 'Undelete');
                return `
                <div class="bg-surface border border-borderLight rounded-2xl p-3.5 flex items-center gap-3 opacity-60">
                    <div class="w-9 h-9 rounded-xl bg-surfaceHighlight flex items-center justify-center shrink-0">
                        <i data-lucide="${cat.icon}" class="w-4 h-4 text-textMuted"></i>
                    </div>
                    <div class="flex-1 min-w-0">
                        <div class="text-sm text-textMuted font-medium truncate">${this._esc(e.symbol || e.currency)}</div>
                        <div class="text-xs text-textMuted/60 mt-0.5">🗑️ ${date}</div>
                    </div>
                    <div class="text-right shrink-0 text-sm text-textMuted">
                        ${amount.toLocaleString('en', { maximumFractionDigits: 8 })} ${this._esc(e.currency)}
                    </div>
                    <button data-click="Journal.undeleteEntry" data-click-arg="${e.id}"
                        class="p-1.5 rounded-lg hover:bg-success/10 focus-visible:bg-success/10 text-textMuted hover:text-success focus-visible:text-success focus-visible:outline focus-visible:outline-success/40 transition shrink-0"
                        aria-label="${this._esc(undeleteLabel)}" title="${this._esc(undeleteLabel)}">
                        <i data-lucide="undo-2" class="w-4 h-4"></i>
                    </button>
                </div>
            `;
            }).join('');
            return;
        }

        if (!JournalState.entries.length) {
            container.innerHTML = '<div class="text-center text-textMuted py-8 text-sm">' +
                (window.I18n ? window.I18n.t('journal.empty') : 'No entries yet. Add one or ask in chat!') +
                '</div>';
            return;
        }

        container.innerHTML = JournalState.entries.map(e => {
            const cat = this._catDisplay(e.category);
            const type = TYPE_META[e.entry_type] || TYPE_META.other;
            const amount = parseFloat(e.price);
            const qty = parseFloat(e.quantity || 1);
            const conv = parseFloat(e.converted_amount || 0);
            // 非當年份才顯示年（跨年持倉/舊紀錄可辨識）
            const d = e.traded_at ? new Date(e.traded_at) : null;
            const sameYear = d && d.getFullYear() === new Date().getFullYear();
            const date = d ? d.toLocaleDateString(undefined, sameYear
                ? { month: 'short', day: 'numeric' }
                : { year: 'numeric', month: 'short', day: 'numeric' }) : '';
            const noteHtml = e.note ? `<span class="text-textMuted/60 text-xs"> · ${this._esc(e.note)}</span>` : '';
            const manualRate = e.rate_source === 'manual';
            const rateTitle = manualRate
                ? this._t('journal.rateManualNote', 'Converted with a manually entered rate')
                : '';
            const convHtml = conv > 0 && e.currency !== (e.base_currency || JournalState.baseCurrency)
                ? `<span class="text-textMuted/50 text-xs" title="${this._esc(rateTitle)}">≈ ${this._symFor(e.base_currency)}${conv.toLocaleString('en', { maximumFractionDigits: 0 })}${manualRate ? '<span class="text-textMuted/40">✎</span>' : ''}</span>` : '';
            const sourceBadge = e.source === 'chat' ? '🤖' : e.source === 'manual' ? '✏️' : '';

            return `
                <div class="bg-surface border border-borderLight rounded-2xl p-3.5 flex items-center gap-3 hover:border-primary/20 transition group cursor-pointer md:cursor-default"
                    data-click="Journal.openEditForm" data-click-arg="${e.id}">
                    <div class="w-9 h-9 rounded-xl bg-surfaceHighlight flex items-center justify-center shrink-0">
                        <i data-lucide="${cat.icon}" class="w-4 h-4 ${type.color}"></i>
                    </div>
                    <div class="flex-1 min-w-0">
                        <div class="text-sm text-secondary font-medium truncate">
                            ${this._esc(e.symbol || e.currency)}${noteHtml}
                        </div>
                        <div class="text-xs text-textMuted flex items-center gap-1.5 mt-0.5 min-w-0">
                            <span class="shrink-0">${date}</span>
                            <span class="shrink-0">·</span>
                            <span class="shrink-0">${type.icon === 'trending-up' ? '📈' : type.icon === 'minus-circle' ? '💸' : '💰'}</span>
                            <span class="truncate">${cat.label}</span>
                            <span class="shrink-0">${sourceBadge}</span>
                        </div>
                    </div>
                    <div class="text-right shrink-0 max-w-[46%]">
                        <div class="text-[13px] md:text-sm font-bold ${type.color} whitespace-nowrap">
                            ${e.entry_type === 'income' ? '+' : e.entry_type === 'expense' ? '-' : ''}${amount.toLocaleString('en', { maximumFractionDigits: Number.isInteger(amount) ? 0 : 4 })} ${this._esc(e.currency)}
                        </div>
                        ${convHtml}
                    </div>
                    <button data-click="Journal.showHistory" data-click-arg="${e.id}"
                        class="hidden md:flex opacity-0 group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline focus-visible:outline-primary/40 p-1.5 rounded-lg hover:bg-primary/10 focus-visible:bg-primary/10 text-textMuted hover:text-primary focus-visible:text-primary transition shrink-0"
                        aria-label="${this._esc(this._t('journal.history', 'History'))}" title="${this._esc(this._t('journal.history', 'History'))}">
                        <i data-lucide="history" class="w-4 h-4"></i>
                    </button>
                    <button data-click="Journal.openEditForm" data-click-arg="${e.id}"
                        class="hidden md:flex opacity-0 group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline focus-visible:outline-primary/40 p-1.5 rounded-lg hover:bg-primary/10 focus-visible:bg-primary/10 text-textMuted hover:text-primary focus-visible:text-primary transition shrink-0"
                        aria-label="${this._esc(this._t('journal.edit', 'Edit'))}" title="${this._esc(this._t('journal.edit', 'Edit'))}">
                        <i data-lucide="pencil" class="w-4 h-4"></i>
                    </button>
                    <button data-click="Journal.deleteEntry" data-click-arg="${e.id}"
                        class="hidden md:flex opacity-0 group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline focus-visible:outline-danger/40 p-1.5 rounded-lg hover:bg-danger/10 focus-visible:bg-danger/10 text-textMuted hover:text-danger focus-visible:text-danger transition shrink-0"
                        aria-label="${this._esc(this._t('journal.deleteEntry', 'Delete'))}" title="${this._esc(this._t('journal.deleteEntry', 'Delete'))}">
                        <i data-lucide="trash-2" class="w-4 h-4"></i>
                    </button>
                </div>
            `;
        }).join('');
    },

    // ── Category Chips ───────────────────────────────────────

    _buildCategoryChips() {
        const container = document.getElementById('journal-category-chips');
        if (!container) return;
        // 收支視圖 chips＝支出＋收入類別聯集（legacy/自訂不進 chips）
        const union = { ...EXPENSE_CATEGORIES, ...INCOME_CATEGORIES };
        const chips = Object.entries(union).map(([id, cat]) => {
            const label = window.I18n ? (window.I18n.t(_CAT_KEY(id), { defaultValue: cat.label })) : cat.label;
            const active = JournalState.filters.category === id;
            return `<button data-click="Journal.setCategory" data-click-arg="${id}"
                class="journal-chip text-xs px-3 py-1.5 rounded-full border transition ${active ? 'bg-primary/15 border-primary/30 text-primary' : 'bg-surface border-borderSubtle text-textMuted hover:text-secondary'}">
                <i data-lucide="${cat.icon}" class="w-3 h-3 inline mr-1"></i>${label}
            </button>`;
        }).join('');
        container.innerHTML = chips;
        if (window.lucide) window.lucide.createIcons();
    },

    // ── Actions（data-click 綁定）────────────────────────────

    async refresh() { await this._refresh(); },

    async setCategory(catId) {
        JournalState.filters.category =
            JournalState.filters.category === catId ? '' : catId;
        this._buildCategoryChips();
        await this.refresh();
    },

    async deleteEntry(id) {
        // 刪除是破壞性操作——必須走確認 dialog（showConfirmDialog 存在時用系統
        // dialog，否則 fallback 原生 confirm）。先前條件寫反：dialog 存在時
        // 反而什麼都不問直接刪（2026-08-24 盤點）。
        let ok;
        if (window.showConfirmDialog) {
            ok = await window.showConfirmDialog({
                title: window.I18n ? window.I18n.t('journal.confirmDelete') : 'Delete this entry?',
                message: window.I18n
                    ? window.I18n.t('journal.confirmDeleteBody')
                    : 'This entry will be removed from your ledger.',
                danger: true,
            });
        } else {
            ok = window.confirm(window.I18n ? window.I18n.t('journal.confirmDelete') : 'Delete this entry?');
        }
        if (!ok) return;
        try {
            await AppAPI.delete('/api/journal/entry/' + id);
            if (typeof showToast === 'function') showToast(
                window.I18n ? window.I18n.t('journal.deleted') : 'Deleted', 'success');
            await this.refresh();
        } catch (e) {
            if (typeof showToast === 'function') showToast(
                window.I18n ? window.I18n.t('journal.deleteFailed') : 'Delete failed', 'error');
        }
    },

    // ── Version History（c037 版本史）───────────────────────

    async showHistory(id) {
        JournalState.historyEntryId = id;
        const modal = document.getElementById('journal-history-modal');
        const list = document.getElementById('journal-history-list');
        if (!modal || !list) return;
        list.innerHTML = '<div class="text-center text-textMuted py-8 text-sm">…</div>';
        modal.classList.remove('hidden');
        try {
            const res = await AppAPI.get('/api/journal/entry/' + id + '/revisions');
            const revs = res.revisions || [];
            if (!revs.length) {
                list.innerHTML = '<div class="text-center text-textMuted py-8 text-sm">' +
                    (window.I18n ? window.I18n.t('journal.historyEmpty') : 'No history yet') + '</div>';
                return;
            }
            const ACT = {
                create: { icon: 'plus-circle', key: 'journal.revCreated' },
                update: { icon: 'pencil', key: 'journal.revUpdated' },
                delete: { icon: 'trash-2', key: 'journal.revDeleted' },
                restore: { icon: 'undo-2', key: 'journal.revRestored' },
            };
            list.innerHTML = revs.map((r, idx) => {
                const meta = ACT[r.action] || ACT.update;
                const label = window.I18n ? window.I18n.t(meta.key, { defaultValue: r.action }) : r.action;
                const srcBadge = r.source === 'chat' ? '🤖' : r.source === 'restore' ? '↩️' : '✏️';
                const diffHtml = r.changed_fields
                    ? Object.entries(r.changed_fields).map(([f, d]) =>
                        '<div class="text-xs text-textMuted">' + this._esc(f) + ': ' +
                        this._esc(String(d.old)) + ' → ' + this._esc(String(d.new)) + '</div>').join('')
                    : '';
                // 最新一筆（idx=0，新到舊）＝目前狀態，不提供還原
                const restoreBtn = idx > 0
                    ? '<button data-click="Journal.restoreRevision" data-click-arg="' + id + ':' + r.revision_no + '"' +
                      ' class="text-xs px-2 py-1 rounded-lg border border-borderSubtle text-textMuted hover:text-primary hover:border-primary/30 transition shrink-0">' +
                      (window.I18n ? window.I18n.t('journal.restore', { defaultValue: 'Restore' }) : 'Restore') + '</button>'
                    : '';
                return `
                <div class="flex items-start gap-3 p-3 rounded-xl bg-surfaceHighlight/50">
                    <i data-lucide="${meta.icon}" class="w-4 h-4 mt-0.5 text-textMuted shrink-0"></i>
                    <div class="flex-1 min-w-0">
                        <div class="text-sm text-secondary">${label}
                            <span class="text-textMuted/60 text-xs">#${r.revision_no}</span> <span>${srcBadge}</span>
                        </div>
                        <div class="text-xs text-textMuted/60 mt-0.5">${r.created_at ? new Date(r.created_at).toLocaleString() : ''}</div>
                        ${diffHtml}
                    </div>
                    ${restoreBtn}
                </div>
            `;
            }).join('');
            if (window.lucide) window.lucide.createIcons();
        } catch (e) {
            console.error('[Journal] load history failed:', e);
            list.innerHTML = '<div class="text-center text-textMuted py-8 text-sm">✕</div>';
        }
    },

    closeHistory() {
        const modal = document.getElementById('journal-history-modal');
        if (modal) modal.classList.add('hidden');
    },

    async restoreRevision(arg) {
        const [id, rev] = String(arg).split(':');
        let ok = true;
        if (window.showConfirmDialog) {
            ok = await window.showConfirmDialog({
                title: window.I18n ? window.I18n.t('journal.confirmRestore') : 'Restore to this version?',
                message: window.I18n ? window.I18n.t('journal.confirmRestoreBody') :
                    'This will revert the entry to the selected version (using the exchange rate frozen at that time).',
            });
        } else {
            ok = window.confirm(window.I18n ? window.I18n.t('journal.confirmRestore') : 'Restore to this version?');
        }
        if (!ok) return;
        try {
            await AppAPI.post('/api/journal/entry/' + id + '/restore', { revision_no: parseInt(rev, 10) });
            if (typeof showToast === 'function') showToast(
                window.I18n ? window.I18n.t('journal.restored') : 'Restored', 'success');
            await this.refresh();
            await this.showHistory(id);
        } catch (e) {
            if (typeof showToast === 'function') showToast(
                window.I18n ? window.I18n.t('journal.restoreFailed') : 'Restore failed', 'error');
        }
    },

    async toggleTrash() {
        JournalState.showDeleted = !JournalState.showDeleted;
        this._syncTrashToggle();
        if (JournalState.showDeleted) {
            JournalState.trashState = 'loading';
            this._renderList();
            if (window.lucide) window.lucide.createIcons();
            try {
                const res = await AppAPI.get('/api/journal/deleted');
                JournalState.deletedEntries = res.entries || [];
                JournalState.trashState = 'ready';
            } catch (e) {
                console.error('[Journal] load deleted failed:', e);
                JournalState.deletedEntries = [];
                JournalState.trashState = 'error';
            }
        } else {
            JournalState.trashState = 'ready';
        }
        this._renderList();
        if (window.lucide) window.lucide.createIcons();
    },

    _syncTrashToggle() {
        const btn = document.getElementById('journal-trash-toggle');
        if (!btn) return;
        btn.classList.toggle('text-danger', JournalState.showDeleted);
        const label = this._t(
            JournalState.showDeleted ? 'journal.hideTrash' : 'journal.showTrash',
            JournalState.showDeleted ? 'Hide trash' : 'Show trash'
        );
        btn.title = label;
        btn.setAttribute('aria-label', label);
    },

    async undeleteEntry(id) {
        try {
            await AppAPI.post('/api/journal/entry/' + id + '/restore', {});
            if (typeof showToast === 'function') showToast(
                window.I18n ? window.I18n.t('journal.restored') : 'Restored', 'success');
            JournalState.showDeleted = false;
            this._syncTrashToggle();
            await this.refresh();
        } catch (e) {
            if (typeof showToast === 'function') showToast(
                window.I18n ? window.I18n.t('journal.undeleteFailed') : 'Undelete failed', 'error');
        }
    },

    // ── Form Modal ───────────────────────────────────────────

    openForm() {
        const modal = document.getElementById('journal-form-modal');
        if (modal) {
            modal.classList.remove('hidden');
            JournalState.formOpen = true;
            this._populateCategories();
            this._bindFormCategory();
        }
    },

    closeForm() {
        const modal = document.getElementById('journal-form-modal');
        if (modal) {
            modal.classList.add('hidden');
            JournalState.formOpen = false;
        }
    },

    _populateCategories() {
        const entryType = document.getElementById('journal-entry-type')?.value || 'expense';
        const select = document.getElementById('journal-entry-category');
        const custom = document.getElementById('journal-entry-category-custom');
        if (!select) return;
        // UX 第二輪：trade 不選分類（維度冗餘）——整組隱藏；
        // expense/income 各自獨立清單。trade 改顯示股數＋買賣方向。
        const isTrade = entryType === 'trade';
        select.classList.toggle('hidden', isTrade);
        if (custom) custom.classList.add('hidden');
        const tradeFields = document.getElementById('journal-entry-trade-fields');
        if (tradeFields) tradeFields.classList.toggle('hidden', !isTrade);
        const table = entryType === 'income' ? INCOME_CATEGORIES : EXPENSE_CATEGORIES;
        select.innerHTML = '';
        const auto = document.createElement('option');
        auto.value = '';
        auto.textContent = window.I18n
            ? window.I18n.t('journal.autoCategory', { defaultValue: 'Auto-categorize' })
            : 'Auto-categorize';
        select.appendChild(auto);
        Object.entries(table).forEach(([id, cat]) => {
            const opt = document.createElement('option');
            opt.value = id;
            opt.textContent = window.I18n
                ? window.I18n.t(_CAT_KEY(id), { defaultValue: cat.label })
                : cat.label;
            select.appendChild(opt);
        });
    },

    _bindFormCategory() {
        // 型別切換→重填類別表；選「其他」→顯示自填框（flag 防重複掛載）
        if (this._formCatBound) return;
        this._formCatBound = true;
        const typeSel = document.getElementById('journal-entry-type');
        const catSel = document.getElementById('journal-entry-category');
        const custom = document.getElementById('journal-entry-category-custom');
        if (typeSel) typeSel.addEventListener('change', () => this._populateCategories());
        if (catSel && custom) {
            catSel.addEventListener('change', () => {
                custom.classList.toggle('hidden', catSel.value !== 'other');
            });
        }
    },

    async submitForm() {
        // 送出中再按一次會記成兩筆（按鈕沒有 disabled）
        if (this._savingEntry) return;
        const entryType = document.getElementById('journal-entry-type')?.value || 'expense';
        const amount = parseFloat(document.getElementById('journal-entry-amount')?.value || '0');
        const currency = document.getElementById('journal-entry-currency')?.value || 'TWD';
        const symbol = document.getElementById('journal-entry-symbol')?.value?.trim() || '';
        const note = document.getElementById('journal-entry-note')?.value?.trim() || '';

        // UX 第二輪：trade 不送分類（後端統一存 investment）；
        // 選「其他」且自填框可見→以自填文字為類別（≤20 字，空值回 other）
        let category = '';
        if (entryType !== 'trade') {
            const catSel = document.getElementById('journal-entry-category');
            const custom = document.getElementById('journal-entry-category-custom');
            category = catSel?.value || '';
            if (category === 'other' && custom && !custom.classList.contains('hidden')) {
                category = (custom.value || '').trim().slice(0, 20) || 'other';
            }
        }

        if (!amount || amount <= 0) {
            if (typeof showToast === 'function') showToast(
                window.I18n ? window.I18n.t('journal.amountRequired') : 'Amount required', 'warning');
            return;
        }

        // 投資補完：股數/買賣方向（只 trade 送）；日期補記（留空＝現在）
        const payload = {
            entry_type: entryType,
            amount,
            currency,
            category,
            symbol,
            note,
            source: 'manual',
        };
        if (entryType === 'trade') {
            const qty = parseFloat(document.getElementById('journal-entry-quantity')?.value || '');
            if (qty && qty > 0) payload.quantity = qty;
            const sideSel = document.getElementById('journal-entry-side');
            if (sideSel?.value) payload.side = sideSel.value;
        }
        const dateVal = document.getElementById('journal-entry-date')?.value || '';
        if (dateVal) {
            const d = new Date(dateVal);
            if (!isNaN(d.getTime())) payload.traded_at = d.toISOString();
        }
        // 自填匯率（選填）——同基準幣時欄位是隱藏的，不送
        if (currency.toUpperCase() !== JournalState.baseCurrency) {
            const rate = parseFloat(document.getElementById('journal-entry-rate')?.value || '');
            if (rate > 0) payload.exchange_rate = rate;
        }

        this._savingEntry = true;
        try {
            const res = await AppAPI.post('/api/journal/entry', payload);
            if (res.ok) {
                if (typeof showToast === 'function') showToast(
                    window.I18n ? window.I18n.t('journal.saved') : 'Saved!', 'success');
                this.closeForm();
                this._resetEntryForm();
                await this.refresh();
            }
        } catch (e) {
            if (this._handleRateUnavailable(e, 'journal-entry-rate-row', 'journal-entry-rate')) return;
            if (typeof showToast === 'function') showToast(userFacingMessage(e, { fallbackKey: 'journal.saveFailed', fallback: 'Save failed' }), 'error');
        } finally {
            this._savingEntry = false;
        }
    },

    // ── 自填匯率 ──────────────────────────────────────────────
    // 幣別＝基準幣時不需要匯率（恆為 1），欄位隱藏；其餘幣別顯示為選填。
    // 留空＝自動抓取（新增）／沿用凍結匯率（編輯）。

    _bindRateRows() {
        [['journal-entry-currency', 'journal-entry-rate-row'],
         ['journal-edit-currency', 'journal-edit-rate-row']].forEach(([selId, rowId]) => {
            const sel = document.getElementById(selId);
            if (!sel || sel.dataset.rateBound) return;
            sel.dataset.rateBound = '1';
            sel.addEventListener('change', () => this._syncRateRow(selId, rowId));
        });
    },

    _syncRateRow(selId, rowId, currency) {
        const row = document.getElementById(rowId);
        if (!row) return;
        const cur = (currency
            || document.getElementById(selId)?.value
            || JournalState.baseCurrency).toUpperCase();
        row.classList.toggle('hidden', cur === JournalState.baseCurrency);
    },

    _handleRateUnavailable(err, rowId, inputId) {
        // 後端 fail-closed（匯率服務抓不到，回 400 rate unavailable）——
        // 這是唯一使用者自己能解的錯，要把匯率欄打開並聚焦，而不是丟一句
        // 看不懂的英文錯誤
        if (!String(err?.message || '').includes('rate unavailable')) return false;
        const row = document.getElementById(rowId);
        if (row) row.classList.remove('hidden');
        const input = document.getElementById(inputId);
        if (input) input.focus();
        if (typeof showToast === 'function') showToast(
            this._t('journal.rateUnavailable',
                'Exchange rate unavailable — please enter it manually'),
            'warning');
        return true;
    },

    _resetEntryForm() {
        const ids = ['journal-entry-amount', 'journal-entry-note',
            'journal-entry-date', 'journal-entry-quantity', 'journal-entry-rate'];
        ids.forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });
        const custom = document.getElementById('journal-entry-category-custom');
        if (custom) custom.value = '';
        const side = document.getElementById('journal-entry-side');
        if (side) side.value = 'buy';
    },

    // ── Edit Entry（PUT /api/journal/entry/{id} 的 UI 入口）──
    // 先前後端完整支援編輯（含修訂快照）但 Dashboard 無入口——記錯帳
    // 只能刪掉重記。只開放後端接受的欄位：金額/幣別/類別/備註。

    openEditForm(id) {
        const entry = this._findEntryById(id);
        if (!entry) return;
        JournalState.editEntryId = id;
        const amountInput = document.getElementById('journal-edit-amount');
        const currencySel = document.getElementById('journal-edit-currency');
        const catSel = document.getElementById('journal-edit-category');
        const noteInput = document.getElementById('journal-edit-note');
        if (!amountInput || !currencySel || !catSel || !noteInput) return;
        amountInput.value = parseFloat(entry.price) || '';
        currencySel.value = (entry.currency || 'TWD').toUpperCase();
        // trade 不編類別（後端統一 investment；隱藏並送空值）
        const isTrade = entry.entry_type === 'trade';
        catSel.classList.toggle('hidden', isTrade);
        // 先建選項再設值——select 還沒有 option 時指派 value 會被瀏覽器
        // 丟棄（回退成 ''），既有類別不會被選中、自訂類別也留不住
        this._populateEditCategories(
            entry.entry_type, isTrade ? '' : (entry.category || '')
        );
        noteInput.value = entry.note || '';
        const rateInput = document.getElementById('journal-edit-rate');
        if (rateInput) rateInput.value = '';
        this._syncRateRow(
            'journal-edit-currency', 'journal-edit-rate-row', currencySel.value
        );
        const modal = document.getElementById('journal-edit-modal');
        if (modal) modal.classList.remove('hidden');
    },

    _findEntryById(id) {
        const numId = parseInt(id, 10);
        return (
            JournalState.entries.find((e) => parseInt(e.id, 10) === numId)
            || JournalState.deletedEntries.find((e) => parseInt(e.id, 10) === numId)
            || null
        );
    },

    _populateEditCategories(entryType, current = '') {
        const select = document.getElementById('journal-edit-category');
        if (!select) return;
        const table = entryType === 'income' ? INCOME_CATEGORIES : EXPENSE_CATEGORIES;
        select.innerHTML = '';
        const keep = document.createElement('option');
        keep.value = '';
        keep.textContent = this._t('journal.editKeepCategory', 'Keep current category');
        select.appendChild(keep);
        Object.entries(table).forEach(([cid, cat]) => {
            const opt = document.createElement('option');
            opt.value = cid;
            opt.textContent = window.I18n
                ? window.I18n.t(_CAT_KEY(cid), { defaultValue: cat.label })
                : cat.label;
            select.appendChild(opt);
        });
        // 既有自訂類別（非清單值）保留為選項，避免存檔時意外改掉
        if (current && !table[current]) {
            const opt = document.createElement('option');
            opt.value = current;
            opt.textContent = current;
            select.appendChild(opt);
        }
        select.value = current || '';
    },

    closeEditForm() {
        const modal = document.getElementById('journal-edit-modal');
        if (modal) modal.classList.add('hidden');
        JournalState.editEntryId = null;
    },

    // 編輯 modal 內的歷史/刪除入口（手機無 hover 按鈕的唯一通道，2026-08-30 UX）
    async editModalHistory() {
        const id = JournalState.editEntryId;
        if (!id) return;
        this.closeEditForm();
        await this.showHistory(id);
    },

    async editModalDelete() {
        const id = JournalState.editEntryId;
        if (!id) return;
        this.closeEditForm();
        await this.deleteEntry(id);
    },

    async submitEditForm() {
        if (!JournalState.editEntryId) return;
        const payload = {};
        const amountRaw = document.getElementById('journal-edit-amount')?.value || '';
        if (amountRaw !== '') {
            const amount = parseFloat(amountRaw);
            if (!amount || amount <= 0) {
                if (typeof showToast === 'function') showToast(
                    window.I18n ? window.I18n.t('journal.amountRequired') : 'Amount required',
                    'warning');
                return;
            }
            payload.amount = amount;
        }
        const currency = document.getElementById('journal-edit-currency')?.value || '';
        if (currency) payload.currency = currency;
        const catSel = document.getElementById('journal-edit-category');
        if (catSel && !catSel.classList.contains('hidden')) {
            payload.category = catSel.value || undefined;
        }
        if (catSel && catSel.classList.contains('hidden')) {
            payload.category = ''; // trade：交後端統一 investment
        }
        // 備註一律送出（含空字串）——只在有值時送會讓「清空備註」
        // 永遠存不回去（後端 note='' 即清除）
        const noteEl = document.getElementById('journal-edit-note');
        if (noteEl) payload.note = (noteEl.value || '').trim();
        // 自填匯率（選填，留空＝沿用凍結匯率）
        const rateRaw = document.getElementById('journal-edit-rate')?.value || '';
        const rate = parseFloat(rateRaw);
        if (rateRaw !== '' && !(rate > 0)) {
            if (typeof showToast === 'function') showToast(
                this._t('journal.ratePositive', 'Exchange rate must be greater than 0'),
                'warning');
            return;
        }
        if (rate > 0) payload.exchange_rate = rate;
        try {
            const res = await AppAPI.put(
                '/api/journal/entry/' + JournalState.editEntryId, payload
            );
            if (res && res.ok !== false) {
                if (typeof showToast === 'function') showToast(
                    window.I18n ? window.I18n.t('journal.saved') : 'Saved!', 'success');
                this.closeEditForm();
                await this.refresh();
            }
        } catch (e) {
            if (this._handleRateUnavailable(e, 'journal-edit-rate-row', 'journal-edit-rate')) return;
            if (typeof showToast === 'function') showToast(
                window.I18n ? window.I18n.t('journal.updateFailed', 'Update failed') : 'Update failed',
                'error');
        }
    },

    // ── Filters ──────────────────────────────────────────────

    // 平台風格月份選擇器（取代原生 input type=month 的系統控件外觀）。
    // 選月寫入隱藏 #journal-filter-month 並 dispatch change → _bindFilters 既有邏輯零改動。
    _initMonthPicker() {
        const btn = document.getElementById('journal-month-btn');
        const input = document.getElementById('journal-filter-month');
        const wrap = document.getElementById('journal-month-picker-wrap');
        if (!btn || !input || !wrap || btn.dataset.monthPickerBound) return;
        btn.dataset.monthPickerBound = '1';

        const lang = () => (window.I18n && window.I18n.getLanguage ? window.I18n.getLanguage() : 'zh-TW');
        let viewYear = new Date().getFullYear();
        let pop = null;

        const fmtMonth = (y, m) => {
            // 依平台語言顯示月份縮寫（1月 / Jan / янв.）
            try {
                return new Intl.DateTimeFormat(lang(), { month: 'short' }).format(new Date(y, m - 1, 1));
            } catch (_e) { return `${m}`; }
        };
        const fmtYear = (y) => {
            try {
                return new Intl.DateTimeFormat(lang(), { year: 'numeric' }).format(new Date(y, 0, 1));
            } catch (_e) { return `${y}`; }
        };
        const syncLabel = () => {
            const label = document.getElementById('journal-month-btn-label');
            if (!label) return;
            const v = input.value;
            // 選了月份就拆掉 data-i18n，否則語系重掃會把月份洗回「選擇月份」
            if (/^\d{4}-\d{2}$/.test(v)) {
                const [y, m] = v.split('-').map(Number);
                delete label.dataset.i18n;
                label.textContent = `${fmtYear(y)} ${fmtMonth(y, m)}`;
            } else {
                label.dataset.i18n = 'journal.pickMonth';
                label.textContent = this._t('journal.pickMonth', 'Pick month');
            }
        };

        const close = () => { if (pop) { pop.remove(); pop = null; } };

        const open = () => {
            close();
            const cur = /^\d{4}-\d{2}$/.test(input.value) ? Number(input.value.slice(0, 4)) : viewYear;
            viewYear = cur;
            pop = document.createElement('div');
            pop.className = 'fixed z-[70] w-64 bg-surface border border-borderSubtle rounded-2xl shadow-xl p-3';
            pop.setAttribute('role', 'dialog');
            const render = () => {
                const curVal = input.value;
                const monthsHtml = Array.from({ length: 12 }, (_, i) => {
                    const m = i + 1;
                    const val = `${viewYear}-${String(m).padStart(2, '0')}`;
                    const active = curVal === val;
                    return `<button type="button" data-m="${val}"
                        class="h-9 rounded-lg text-sm transition ${active
                            ? 'bg-primary/15 text-primary font-bold'
                            : 'text-textMuted hover:bg-surfaceHighlight hover:text-secondary'}">
                        ${fmtMonth(viewYear, m)}
                    </button>`;
                }).join('');
                pop.innerHTML = `
                    <div class="flex items-center justify-between mb-2">
                        <button type="button" data-act="prev" class="w-7 h-7 rounded-lg flex items-center justify-center text-textMuted hover:text-primary hover:bg-surfaceHighlight transition" aria-label="Previous year" data-i18n="journal.prevYear" data-i18n-attr="aria-label">
                            <i data-lucide="chevron-left" class="w-4 h-4"></i>
                        </button>
                        <span class="text-sm font-bold text-secondary">${fmtYear(viewYear)}</span>
                        <button type="button" data-act="next" class="w-7 h-7 rounded-lg flex items-center justify-center text-textMuted hover:text-primary hover:bg-surfaceHighlight transition" aria-label="Next year" data-i18n="journal.nextYear" data-i18n-attr="aria-label">
                            <i data-lucide="chevron-right" class="w-4 h-4"></i>
                        </button>
                    </div>
                    <div class="grid grid-cols-4 gap-1">${monthsHtml}</div>
                    <button type="button" data-act="clear"
                        class="mt-2 w-full h-8 rounded-lg text-xs text-textMuted hover:text-danger hover:bg-danger/10 transition">
                        ${this._t('journal.monthAll', 'All months')}
                    </button>`;
                if (window.lucide) lucide.createIcons({ nodes: [pop] });
            };
            render();
            document.body.appendChild(pop);
            const r = btn.getBoundingClientRect();
            // 預設彈在按鈕上方；太窄可左移貼齊、太低改彈下方
            // 手機：水平置中（看起來刻意、對齊視覺中心）；桌機：對齊按鈕左緣
            if (innerWidth < 640) {
                pop.style.left = Math.max(8, (innerWidth - pop.offsetWidth) / 2) + 'px';
            } else {
                pop.style.left = Math.max(8, Math.min(r.left, innerWidth - pop.offsetWidth - 8)) + 'px';
            }
            const above = r.top - pop.offsetHeight - 8;
            pop.style.top = (above > 8 ? above : r.bottom + 8) + 'px';

            pop.addEventListener('click', async (ev) => {
                const b = ev.target.closest('button');
                if (!b) return;
                if (b.dataset.act === 'prev') { viewYear -= 1; render(); return; }
                if (b.dataset.act === 'next') { viewYear += 1; render(); return; }
                if (b.dataset.act === 'clear') {
                    input.value = '';
                } else if (b.dataset.m) {
                    input.value = b.dataset.m;
                } else { return; }
                syncLabel();
                close();
                input.dispatchEvent(new Event('change', { bubbles: true }));
            });
        };

        btn.addEventListener('click', () => { pop ? close() : open(); });
        // 外點關閉（點鈕自身除外）
        setTimeout(() => document.addEventListener('click', (ev) => {
            if (pop && !pop.contains(ev.target) && !wrap.contains(ev.target)) close();
        }));
        // 語言切換時刷新標籤（事件名對齊 i18n.js 的 languageChanged）
        window.addEventListener('languageChanged', syncLabel);
        syncLabel();
    },

    _bindFilters() {
        document.querySelectorAll('.journal-filter').forEach(el => {
            const evt = el.tagName === 'SELECT' || el.tagName === 'INPUT' ? 'change' : 'change';
            el.addEventListener(evt, async () => {
                JournalState.filters.entry_type = document.getElementById('journal-filter-type')?.value || '';
                // 'month'/'prev_month'/'year' 是字串哨兵——parseInt 會把它變 NaN，
                // 下游 ==='month' 永不成立 → 篩選形同全部時間
                JournalState.filters.days = document.getElementById('journal-filter-days')?.value || '30';
                // 特定月份 picker：有值優先於快捷 select；清空回 select 邏輯
                const monthVal = document.getElementById('journal-filter-month')?.value || '';
                JournalState.filters.month = /^\d{4}-\d{2}$/.test(monthVal) ? monthVal : '';
                JournalState.filters.search = document.getElementById('journal-search')?.value?.trim() || '';
                await this.refresh();
            });
        });
        // 搜尋 debounce
        const searchInput = document.getElementById('journal-search');
        if (searchInput) {
            let timer;
            searchInput.addEventListener('input', () => {
                clearTimeout(timer);
                timer = setTimeout(async () => {
                    JournalState.filters.search = searchInput.value.trim();
                    await this.refresh();
                }, 400);
            });
        }
    },

    // ── Helpers ──────────────────────────────────────────────

    _catLabel(i18nKey) {
        if (window.I18n && window.I18n.isReady && window.I18n.isReady()) {
            return window.I18n.t(i18nKey, { defaultValue: i18nKey.split('.').pop() });
        }
        return i18nKey.split('.').pop();
    },

    _catDisplay(catId) {
        // 已知類別→i18n 標籤；未知（自訂「其他」自填）→原文，必經 escape
        const meta = CATEGORY_LOOKUP[catId];
        if (meta) {
            const label = window.I18n
                ? window.I18n.t(_CAT_KEY(catId), { defaultValue: meta.label })
                : meta.label;
            return { icon: meta.icon, label: this._esc(label) };
        }
        return { icon: 'package', label: this._esc(catId) };
    },

    // ── 報表基準幣（c039）──────────────────────────────────────

    _sym() {
        return BASE_SYMBOLS[JournalState.baseCurrency] || (JournalState.baseCurrency + ' ');
    },

    _symFor(code) {
        // 列上的換算額用「那一列自己的基準幣」——切換基準幣時整批搬移，
        // 正常情況與目前基準幣一致；萬一有殘留也不會顯示成錯的幣別
        const c = (code || JournalState.baseCurrency).toUpperCase();
        return BASE_SYMBOLS[c] || (c + ' ');
    },

    async _loadBaseCurrency() {
        try {
            const res = await AppAPI.get('/api/journal/base-currency');
            if (res?.base_currency) JournalState.baseCurrency = res.base_currency;
            if (Array.isArray(res?.supported)) JournalState.baseCurrencyOptions = res.supported;
        } catch (e) {
            // 讀不到就用預設值——帳本不得因為讀不到偏好就不能用
            console.warn('[Journal] base currency unavailable, using default:', e?.message);
        }
        this._renderBaseCurrencySelect();
    },

    _renderBaseCurrencySelect() {
        const sel = document.getElementById('journal-base-currency');
        if (!sel) return;
        const opts = JournalState.baseCurrencyOptions.length
            ? JournalState.baseCurrencyOptions
            : [JournalState.baseCurrency];
        sel.innerHTML = opts.map(
            (c) => `<option value="${this._esc(c)}">${this._esc(c)}</option>`
        ).join('');
        sel.value = JournalState.baseCurrency;
        if (!sel.dataset.baseBound) {
            sel.dataset.baseBound = '1';
            sel.addEventListener('change', () => this.changeBaseCurrency(sel.value));
        }
    },

    async changeBaseCurrency(currency) {
        const next = (currency || '').toUpperCase();
        const prev = JournalState.baseCurrency;
        if (!next || next === prev) return;
        // 這會動到整本帳的換算值——必須確認（showConfirmDialog 存在時用系統
        // dialog，否則 fallback 原生 confirm，與 deleteEntry 同一模式）。
        // 取消就把下拉轉回原值。
        const title = this._t('journal.baseCurrencyConfirm', 'Change reporting currency?');
        const body = this._t('journal.baseCurrencyConfirmBody',
            'All converted amounts move to the new currency; the original amounts you recorded are unchanged.');
        let ok;
        if (window.showConfirmDialog) {
            ok = await window.showConfirmDialog({ title, message: body });
        } else {
            ok = window.confirm(title + '\n\n' + body);
        }
        if (!ok) {
            this._renderBaseCurrencySelect();
            return;
        }
        try {
            const res = await AppAPI.put('/api/journal/base-currency', { currency: next });
            JournalState.baseCurrency = res?.base_currency || next;
            if (typeof showToast === 'function') showToast(
                this._t('journal.baseCurrencyChanged', 'Reporting currency updated'), 'success');
            this._renderBaseCurrencySelect();
            await this.refresh();
        } catch (e) {
            if (typeof showToast === 'function') showToast(
                String(e?.message || '').includes('rate unavailable')
                    ? this._t('journal.baseCurrencyRateUnavailable',
                        'Exchange rate unavailable — reporting currency unchanged')
                    : this._t('journal.baseCurrencyFailed', 'Failed to change reporting currency'),
                'error');
            // 失敗時後端一列都沒動（fail-closed）——下拉必須轉回原值，
            // 不能讓 UI 顯示成已經切換
            JournalState.baseCurrency = prev;
            this._renderBaseCurrencySelect();
        }
    },

    _t(key, fallback) {
        return window.I18n ? window.I18n.t(key, { defaultValue: fallback }) : fallback;
    },

    _esc(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = String(str);
        return div.innerHTML;
    },

    async _refresh() {
        if (JournalState.view === 'calendar') {
            await this._loadCalendar();
            this._renderCalendar();
        } else if (JournalState.view === 'invest') {
            await Promise.all([this._loadEntries(), this._loadPositions(), this._loadHoldings(), this._loadScorecard()]);
            this._renderInvestSummary();
            this._renderPositions();
            this._renderHoldings();
            this._renderScorecard();
            this._renderList();
        } else {
            await Promise.all([this._loadEntries(), this._loadSummary()]);
            this._renderSummary();
            this._renderBreakdown();
            this._renderList();
        }
        if (window.lucide) window.lucide.createIcons();
    },

    // ── 子視圖切換（UX 第二輪）───────────────────────────────

    async switchView(v) {
        if (JournalState.view === v) return;
        JournalState.view = v;
        if (v === 'cashflow') {
            JournalState.showDeleted = false;
            this._syncTrashToggle();
        }
        // 類別篩選是收支視圖的維度——帶到投資視圖會渲染出
        // 「看似合理卻恆空」的清單且看不到原因（chips 已隱藏）
        if (v !== 'cashflow' && JournalState.filters.category) {
            JournalState.filters.category = '';
            this._buildCategoryChips();
        }
        this._syncViewToggle();
        if (v === 'invest') {
            // 持倉端點伺服器端同步查外部價格，冷快取可達數十秒——
            // 立即渲染骨架，不讓使用者面對整片空白
            const sum = document.getElementById('journal-invest-summary');
            const list = document.getElementById('journal-positions-list');
            const loading = this._esc(this._t('journal.loading', 'Loading...'));
            if (sum) sum.innerHTML = `<div class="col-span-2 md:col-span-4 text-center text-textMuted py-8 text-sm">${loading}</div>`;
            if (list) list.innerHTML = `<div class="text-center text-textMuted py-8 text-sm">${loading}</div>`;
        }
        await this.refresh();
    },

    _syncViewToggle() {
        const cash = document.getElementById('journal-cashflow-view');
        const inv = document.getElementById('journal-invest-view');
        const cal = document.getElementById('journal-calendar-view');
        if (cash) cash.classList.toggle('hidden', JournalState.view !== 'cashflow');
        if (inv) inv.classList.toggle('hidden', JournalState.view !== 'invest');
        if (cal) cal.classList.toggle('hidden', JournalState.view !== 'calendar');
        document.querySelectorAll('.journal-view-btn').forEach((btn) => {
            const active = btn.dataset.clickArg === JournalState.view;
            btn.className = 'journal-view-btn px-4 py-2 md:py-1.5 text-sm font-medium rounded-lg transition ' +
                (active ? 'bg-surface text-secondary shadow-sm' : 'text-textMuted hover:text-secondary');
        });
    },

    // ── 已驗證持倉（綁定錢包鏈上餘額，2026-09-12）────────────

    async _loadHoldings() {
        if (!document.getElementById('journal-verified-holdings-list')) return;
        try {
            JournalState.holdings = await AppAPI.get('/api/wallet/holdings');
        } catch (e) {
            console.error('[Journal] load holdings failed:', e);
            JournalState.holdings = null;
        }
    },

    _renderHoldings() {
        const list = document.getElementById('journal-verified-holdings-list');
        if (!list) return;
        const data = JournalState.holdings;
        if (!data) {
            list.innerHTML = `<div class="text-center text-textMuted py-3 text-sm">${this._esc(this._t('journal.holdings.failed', 'Could not load on-chain balances'))}</div>`;
            return;
        }
        const wallets = data.wallets || [];
        if (!wallets.length) {
            list.innerHTML = `<div class="text-center text-textMuted py-3 text-sm">${this._esc(this._t('journal.holdings.empty', 'Bind a wallet in Settings to see holdings verified on-chain'))}</div>`;
            return;
        }
        const totals = (data.totals || []).filter((t) => t.usd !== null && t.usd !== undefined);
        const fmtUsd = (v) => `$${Number(v || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        const fmtAmt = (v) => Number(v || 0).toLocaleString('en-US', { maximumFractionDigits: Number(v) >= 1 ? 4 : 6 });
        const rows = totals.slice(0, 8).map((t) => `
            <div class="flex items-center justify-between gap-3 min-h-9 px-2 rounded-lg">
                <span class="text-sm font-medium text-textMain">${this._esc(t.symbol)}</span>
                <span class="text-right"><span class="text-sm text-textMain">${this._esc(fmtAmt(t.amount))}</span><span class="text-xs text-textMuted ml-2">${this._esc(fmtUsd(t.usd))}</span></span>
            </div>`).join('');
        const walletsLine = this._esc(this._t('journal.holdings.wallets', '{n} wallets').replace('{n}', String(wallets.length)));
        list.innerHTML = `
            <div class="flex items-center justify-between gap-2 px-2 text-xs text-textMuted"><span>${walletsLine}</span><span class="font-bold text-secondary text-sm">${this._esc(fmtUsd(data.usd_total))}</span></div>
            ${rows || `<div class="text-center text-textMuted py-2 text-sm">${this._esc(this._t('journal.holdings.none', 'No priced assets'))}</div>`}`;
    },

    // ── 判斷成績（2026-09-13）───────────────────────────────

    async _loadScorecard() {
        if (!document.getElementById('journal-scorecard-body')) return;
        this._loadCalls();
        try {
            JournalState.scorecard = await AppAPI.get('/api/journal/scorecard');
        } catch (e) {
            console.error('[Journal] load scorecard failed:', e);
            JournalState.scorecard = null;
        }
    },

    _pct(value, digits = 1) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
        const n = Number(value);
        return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`;
    },

    _marketLabel(market) {
        return this._t(`journal.scorecard.markets.${market}`, market);
    },

    _scopeLabel(scope) {
        const map = {
            verified_90: ['journal.scorecard.scopeVerified90', 'verified · 90d'],
            all_90: ['journal.scorecard.scopeAll90', 'all trades · 90d'],
            all_30: ['journal.scorecard.scopeAll30', 'all trades · 30d'],
        };
        const [key, fallback] = map[scope] || map.all_90;
        return this._t(key, fallback);
    },

    _renderScorecard() {
        const body = document.getElementById('journal-scorecard-body');
        if (!body) return;
        const data = JournalState.scorecard;
        if (!data) {
            body.innerHTML = `<div class="text-center text-textMuted py-4 text-sm">${this._esc(this._t('journal.scorecard.failed', 'Could not load the scorecard'))}</div>`;
            return;
        }
        const counts = data.counts || {};
        const head = data.headline || {};
        const minSample = data.min_sample || 10;
        if (!counts.scored && !counts.pending) {
            body.innerHTML = `<div class="text-center text-textMuted py-4 text-sm">${this._esc(this._t('journal.scorecard.empty', 'No scorable trades yet. Record a buy or sell and the first score arrives 30 days later.'))}</div>`;
            return;
        }
        // 小螢幕三格並排：數字才用大字，「再 N 筆才顯示」這種文字用小字可換行，不截斷
        const tile = (label, value, sub, isText = false) => `
            <div class="rounded-xl bg-surfaceHighlight/50 p-3 min-w-0">
                <div class="text-[11px] text-textMuted truncate">${this._esc(label)}</div>
                <div class="${isText ? 'text-xs text-textMuted leading-tight min-h-7 flex items-center' : 'text-xl font-bold text-secondary tabular-nums truncate'}">${value}</div>
                ${sub ? `<div class="text-[10px] text-textMuted/70 truncate">${this._esc(sub)}</div>` : ''}
            </div>`;
        const scopeLabel = this._scopeLabel(head.scope);
        let tiles;
        if (head.masked) {
            const needed = this._t('journal.scorecard.needMore', '{n} more to unlock').replace('{n}', String(head.needed ?? minSample));
            tiles = [
                tile(this._t('journal.scorecard.hitRate', 'Hit rate'), this._esc(needed), scopeLabel, true),
                tile(this._t('journal.scorecard.avgExcess', 'Avg. vs market'), '—', '', true),
                tile(this._t('journal.scorecard.scored', 'Scored'), `${counts.scored || 0}`, this._t('journal.scorecard.pendingCount', '{n} pending').replace('{n}', String(counts.pending || 0))),
            ];
        } else {
            const excessCls = Number(head.avg_excess) >= 0 ? 'text-success' : 'text-danger';
            tiles = [
                tile(this._t('journal.scorecard.hitRate', 'Hit rate'), `${Math.round((head.hit_rate || 0) * 100)}%`, scopeLabel),
                tile(this._t('journal.scorecard.avgExcess', 'Avg. vs market'), `<span class="${excessCls}">${this._pct(head.avg_excess)}</span>`, this._t('journal.scorecard.medianExcess', 'median {v}').replace('{v}', this._pct(head.median_excess))),
                tile(this._t('journal.scorecard.scored', 'Scored'), `${counts.scored || 0}`, this._t('journal.scorecard.pendingCount', '{n} pending').replace('{n}', String(counts.pending || 0))),
            ];
        }
        const verifiedLabel = this._esc(this._t('journal.scorecard.verified', 'on-chain'));
        const selfLabel = this._esc(this._t('journal.scorecard.selfReported', 'self-reported'));
        const groups = (data.groups || []).map((g) => {
            const badge = g.verification === 'verified'
                ? `<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-success/15 text-success whitespace-nowrap">${verifiedLabel}</span>`
                : `<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-surfaceHighlight text-textMuted whitespace-nowrap">${selfLabel}</span>`;
            const right = g.masked
                ? `<span class="shrink-0 text-xs text-textMuted whitespace-nowrap">${this._esc(this._t('journal.scorecard.needMore', '{n} more to unlock').replace('{n}', String(g.needed)))}</span>`
                : `<span class="shrink-0 text-xs tabular-nums whitespace-nowrap"><span class="text-textMain">${Math.round(g.hit_rate * 100)}%</span><span class="text-textMuted mx-1">·</span><span class="${g.avg_excess >= 0 ? 'text-success' : 'text-danger'}">${this._pct(g.avg_excess)}</span></span>`;
            return `
            <div class="flex items-center justify-between gap-2 min-h-9 px-2 rounded-lg hover:bg-surfaceHighlight">
                <div class="flex flex-wrap items-center gap-2 min-w-0">
                    ${badge}
                    <span class="text-sm text-textMain truncate">${this._esc(this._marketLabel(g.market))}</span>
                    <span class="text-[10px] text-textMuted whitespace-nowrap">${g.horizon_days}d · n=${g.n}</span>
                </div>
                ${right}
            </div>`;
        }).join('');
        const longLabel = this._esc(this._t('journal.scorecard.long', 'long'));
        const shortLabel = this._esc(this._t('journal.scorecard.short', 'short'));
        const backfilledLabel = this._esc(this._t('journal.scorecard.backfilled', 'backfilled · not counted'));
        const recent = (data.recent || []).slice(0, 5).map((r) => `
            <div class="flex items-center justify-between gap-2 min-h-9 px-2 rounded-lg">
                <div class="flex flex-wrap items-center gap-2 min-w-0">
                    <span class="text-sm font-medium text-textMain">${this._esc(r.symbol)}</span>
                    <span class="text-[10px] px-1.5 py-0.5 rounded-full ${r.side === 'buy' ? 'bg-success/15 text-success' : 'bg-danger/15 text-danger'} whitespace-nowrap">${r.side === 'buy' ? longLabel : shortLabel}</span>
                    ${r.verification === 'backfilled' ? `<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-surfaceHighlight text-textMuted whitespace-nowrap">${backfilledLabel}</span>` : ''}
                    <span class="text-[10px] text-textMuted whitespace-nowrap">${r.horizon_days}d · ${this._esc(String(r.entry_date || '').slice(0, 10))}</span>
                </div>
                <span class="shrink-0 text-xs tabular-nums whitespace-nowrap ${r.hit ? 'text-success' : 'text-danger'}">${r.hit ? '✓' : '✗'} ${this._pct(r.excess_pct)}</span>
            </div>`).join('');
        body.innerHTML = `
            <div class="grid grid-cols-3 gap-2">${tiles.join('')}</div>
            ${groups ? `<div class="space-y-0.5"><div class="text-[11px] text-textMuted px-2">${this._esc(this._t('journal.scorecard.byMarket', 'By market'))}</div>${groups}</div>` : ''}
            ${recent ? `<div class="space-y-0.5 pt-2 border-t border-borderSubtle"><div class="text-[11px] text-textMuted px-2">${this._esc(this._t('journal.scorecard.recent', 'Recent'))}</div>${recent}</div>` : ''}
            <div class="text-[10px] text-textMuted/60 px-2">${this._esc(this._t('journal.scorecard.method', 'Buy = bullish, sell = bearish. Excess return = your return minus the same-period benchmark (0050 / SPY / BTC…). Groups need {n} trades before percentages show.').replace('{n}', String(minSample)))}</div>`;
    },

    // ── v2 明確喊單（2026-09-13）──────────────────────────────

    async _loadCalls() {
        const list = document.getElementById('journal-calls-list');
        if (!list) return;
        try {
            const res = await AppAPI.get('/api/journal/scorecard/calls');
            JournalState.calls = res && res.calls ? res.calls : [];
            JournalState.callsCancelWindowHours = (res && res.cancel_window_hours) || 24;
        } catch (e) {
            console.error('[Journal] load calls failed:', e);
            JournalState.calls = [];
        }
        this._renderCalls();
    },

    _renderCalls() {
        const list = document.getElementById('journal-calls-list');
        if (!list) return;
        const calls = (JournalState.calls || []).filter((c) => c.status !== 'cancelled').slice(0, 8);
        if (!calls.length) { list.innerHTML = ''; return; }
        const bullish = this._esc(this._t('journal.calls.bullish', 'Bullish'));
        const bearish = this._esc(this._t('journal.calls.bearish', 'Bearish'));
        const cancelLabel = this._esc(this._t('journal.calls.cancel', 'Undo'));
        const pendingLabel = this._esc(this._t('journal.calls.pending', 'judged {d}'));
        const windowMs = (JournalState.callsCancelWindowHours || 24) * 3600 * 1000;
        list.innerHTML = calls.map((c) => {
            const createdMs = Date.parse(c.created_at || '') || 0;
            const canCancel = c.status === 'open' && c.score_status !== 'scored' && (Date.now() - createdMs) < windowMs;
            const right = c.score_status === 'scored'
                ? `<span class="shrink-0 text-xs tabular-nums whitespace-nowrap ${c.hit ? 'text-success' : 'text-danger'}">${c.hit ? '✓' : '✗'} ${this._pct(c.excess_pct)}</span>`
                : `<span class="shrink-0 text-[10px] text-textMuted whitespace-nowrap">${pendingLabel.replace('{d}', this._esc(String(c.exit_date || '').slice(0, 10)))}</span>`;
            const cancel = canCancel
                ? `<button data-click="Journal.cancelCall" data-click-arg="${c.id}" class="shrink-0 min-h-9 px-2 text-[11px] text-textMuted hover:text-danger">${cancelLabel}</button>`
                : '';
            return `
            <div class="flex items-center justify-between gap-2 min-h-9 px-2 rounded-lg">
                <div class="flex flex-wrap items-center gap-2 min-w-0">
                    <span class="text-sm font-medium text-textMain">${this._esc(c.symbol)}</span>
                    <span class="text-[10px] px-1.5 py-0.5 rounded-full ${c.side === 'buy' ? 'bg-success/15 text-success' : 'bg-danger/15 text-danger'} whitespace-nowrap">${c.side === 'buy' ? bullish : bearish}</span>
                    <span class="text-[10px] text-textMuted whitespace-nowrap">${c.horizon_days}d · @${this._esc(String(c.entry_price))}${c.target_price ? ' → ' + this._esc(String(c.target_price)) : ''}</span>
                </div>
                <div class="flex items-center gap-1">${right}${cancel}</div>
            </div>`;
        }).join('');
    },

    async submitCall() {
        // 喊單 24h 後鎖定不能撤——連點記成兩筆就收不回來
        if (this._savingCall) return;
        const status = document.getElementById('journal-calls-status');
        const symbol = (document.getElementById('journal-call-symbol')?.value || '').trim();
        const side = document.getElementById('journal-call-side')?.value || 'bullish';
        const horizon = Number(document.getElementById('journal-call-horizon')?.value || 30);
        const targetRaw = document.getElementById('journal-call-target')?.value;
        if (!symbol) {
            if (status) status.textContent = this._t('journal.calls.needSymbol', 'Enter a ticker first');
            return;
        }
        if (status) status.textContent = this._t('journal.calls.saving', 'Recording…');
        this._savingCall = true;
        try {
            const body = { symbol, side, horizon_days: horizon };
            if (targetRaw && Number(targetRaw) > 0) body.target_price = Number(targetRaw);
            await AppAPI.post('/api/journal/scorecard/calls', body);
            const sym = document.getElementById('journal-call-symbol'); if (sym) sym.value = '';
            const tgt = document.getElementById('journal-call-target'); if (tgt) tgt.value = '';
            if (status) status.textContent = this._t('journal.calls.saved', 'Recorded. It will be judged at expiry.');
            await this._loadCalls();
        } catch (e) {
            // api-client 把伺服器的 detail 放在 message（400：市場不支援／抓不到收盤價）；
            // 中文 detail 照顯示，英文／技術字串／網路層錯誤換成在地化文案
            if (status) status.textContent = userFacingMessage(e, { fallbackKey: 'journal.calls.failed', fallback: 'Could not record the call' });
        } finally {
            this._savingCall = false;
        }
    },

    async cancelCall(id) {
        const status = document.getElementById('journal-calls-status');
        try {
            await AppAPI.delete(`/api/journal/scorecard/calls/${encodeURIComponent(id)}`);
            if (status) status.textContent = this._t('journal.calls.cancelled', 'Call removed');
            await this._loadCalls();
        } catch (e) {
            if (status) status.textContent = this._t('journal.calls.cancelFailed', 'Too late to undo — calls lock after 24 hours');
        }
    },

    async refreshScorecard() {
        const status = document.getElementById('journal-scorecard-status');
        if (status) status.textContent = this._t('journal.scorecard.refreshing', 'Scoring…');
        try {
            JournalState.scorecard = await AppAPI.post('/api/journal/scorecard/refresh', {});
            this._renderScorecard();
            if (status) status.textContent = this._t('journal.scorecard.refreshed', 'Updated');
        } catch (e) {
            const code = e && (e.status || (e.response && e.response.status));
            if (status) status.textContent = code === 429 ? this._t('journal.scorecard.tooSoon', 'Please wait a minute') : this._t('journal.scorecard.refreshFailed', 'Update failed, please try again later');
        }
    },

    // ── 行事曆（每日早報 Phase B）──────────────────────────────

    _calendarTodayIso() {
        return isoDate(new Date());
    },

    _calendarLang() {
        return (window.I18n && (window.I18n.currentLang || window.I18n.lang)) || document.documentElement.lang || undefined;
    },

    async _loadCalendar() {
        const todayIso = this._calendarTodayIso();
        if (!JournalState.calendarMonth) JournalState.calendarMonth = monthKey(new Date());
        if (!JournalState.calendarSelected) JournalState.calendarSelected = todayIso;
        const grid = buildMonthGrid(JournalState.calendarMonth, todayIso);
        // 快速連點上／下個月：前一個月的慢回應晚到會把別月的事件蓋進目前的月曆
        // ——只收最後一次請求的結果（呼叫端之後的 _renderCalendar 照目前狀態畫）
        const seq = (this._calendarSeq = (this._calendarSeq || 0) + 1);
        try {
            const res = await AppAPI.get(`/api/journal/calendar?start=${grid.start}&end=${grid.end}`);
            if (seq !== this._calendarSeq) return;
            JournalState.calendar = res.events || [];
        } catch (e) {
            if (seq !== this._calendarSeq) return;
            console.error('[Journal] load calendar failed:', e);
            JournalState.calendar = [];
        }
    },

    _kindOf(ev) {
        return ev.source === 'system' ? (ev.kind || 'custom') : 'custom';
    },

    _kindLabel(kind) {
        const map = {
            earnings: ['journal.calendar.kindEarnings', 'earnings'],
            revenue: ['journal.calendar.kindReport', 'reports'],
            report: ['journal.calendar.kindReport', 'reports'],
            unlock: ['journal.calendar.kindUnlock', 'unlock'],
            macro: ['journal.calendar.kindMacro', 'macro'],
            custom: ['journal.calendar.kindCustom', 'mine'],
        };
        const [key, fallback] = map[kind] || map.custom;
        return this._t(key, fallback);
    },

    _kindDotClass(kind) {
        return {
            earnings: 'bg-primary',
            revenue: 'bg-primary/40',
            report: 'bg-primary/40',
            unlock: 'bg-danger',
            macro: 'bg-success',
            custom: 'bg-secondary',
        }[kind] || 'bg-secondary';
    },

    _renderCalendar() {
        const gridEl = document.getElementById('journal-calendar-grid');
        if (!gridEl) return;
        const todayIso = this._calendarTodayIso();
        const lang = this._calendarLang();
        const grid = buildMonthGrid(JournalState.calendarMonth || monthKey(new Date()), todayIso);
        const byDate = groupByDate(JournalState.calendar || []);
        const selected = JournalState.calendarSelected || todayIso;
        const monthEl = document.getElementById('journal-calendar-month');
        if (monthEl) monthEl.textContent = monthLabel(grid.key, lang);
        const weekEl = document.getElementById('journal-calendar-weekdays');
        if (weekEl) weekEl.innerHTML = weekdayLabels(lang).map((w) => `<span class="py-1">${this._esc(w)}</span>`).join('');
        gridEl.innerHTML = grid.cells.map((c) => {
            const summary = cellSummary(byDate[c.iso]);
            const isSel = c.iso === selected;
            const cls = [
                'relative flex flex-col items-center justify-start min-h-11 sm:min-h-14 rounded-lg px-0.5 pt-1 pb-1 text-sm transition',
                c.inMonth ? 'text-textMain' : 'text-textMuted/40',
                isSel ? 'bg-primary/15 ring-1 ring-primary' : 'hover:bg-surfaceHighlight',
                c.isToday && !isSel ? 'ring-1 ring-borderLight' : '',
            ].join(' ');
            const dots = summary.kinds.map((k) => `<span class="w-1.5 h-1.5 rounded-full ${this._kindDotClass(k)}"></span>`).join('');
            const more = summary.count > 1 ? `<span class="text-[9px] text-textMuted leading-none">${summary.count}</span>` : '';
            return `<button type="button" data-click="Journal.calendarSelect" data-click-arg="${c.iso}" class="${cls}" aria-label="${c.iso}" aria-pressed="${isSel ? 'true' : 'false'}">
                <span class="${c.isToday ? 'font-bold text-primary' : ''}">${c.day}</span>
                <span class="flex items-center gap-0.5 mt-0.5 h-2">${dots}${more}</span>
            </button>`;
        }).join('');
        this._renderCalendarDay(byDate[selected] || [], selected);
        const dateInput = document.getElementById('journal-calendar-date');
        if (dateInput && !JournalState.calendarEditing) dateInput.value = selected;
        if (window.lucide) window.lucide.createIcons();
    },

    _repeatLabel(recurrence) {
        const map = {
            weekly: ['journal.calendar.repeatWeekly', 'Weekly'],
            monthly: ['journal.calendar.repeatMonthly', 'Monthly'],
            yearly: ['journal.calendar.repeatYearly', 'Yearly'],
        };
        const hit = map[recurrence];
        return hit ? this._t(hit[0], hit[1]) : '';
    },

    _renderCalendarDay(events, iso) {
        const list = document.getElementById('journal-calendar-list');
        const title = document.getElementById('journal-calendar-day-title');
        const count = document.getElementById('journal-calendar-day-count');
        if (title) {
            let label = iso;
            try {
                const [y, m, d] = iso.split('-').map(Number);
                label = new Intl.DateTimeFormat(this._calendarLang(), { month: 'long', day: 'numeric', weekday: 'short' }).format(new Date(y, m - 1, d));
            } catch (e) { /* 保留 ISO */ }
            title.textContent = label;
        }
        if (count) count.textContent = events.length ? this._t('journal.calendar.dayCount', '{n} events').replace('{n}', String(events.length)) : '';
        if (!list) return;
        if (!events.length) {
            list.innerHTML = `<div class="text-textMuted py-2 text-sm">${this._esc(this._t('journal.calendar.noDayEvents', 'Nothing on this day. Add a reminder below.'))}</div>`;
            return;
        }
        const delLabel = this._esc(this._t('journal.calendar.delete', 'Delete'));
        const editLabel = this._esc(this._t('journal.calendar.edit', 'Edit'));
        list.innerHTML = events.map((ev) => {
            const kind = this._kindOf(ev);
            const repeat = this._repeatLabel(ev.recurrence);
            const repeatBadge = repeat
                ? `<span class="shrink-0 flex items-center gap-0.5 text-[10px] text-textMuted whitespace-nowrap"><i data-lucide="repeat" class="w-3 h-3"></i>${this._esc(repeat)}</span>`
                : '';
            // 系統事件（財報、總經）每天同步會蓋回去，只給自訂事件編輯
            const editBtn = ev.source === 'user'
                ? `<button data-click="Journal.editCalendarEvent" data-click-arg="${ev.id}" class="shrink-0 min-h-11 px-2 text-textMuted hover:text-secondary" aria-label="${editLabel}"><i data-lucide="pencil" class="w-3.5 h-3.5"></i></button>`
                : '';
            return `
            <div class="flex items-center gap-3 min-h-11 px-2 rounded-lg hover:bg-surfaceHighlight">
                <span class="shrink-0 w-2 h-2 rounded-full ${this._kindDotClass(kind)}"></span>
                <span class="flex-1 min-w-0 truncate text-sm text-textMain">${this._esc(ev.title || '')}</span>
                ${repeatBadge}
                <span class="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full bg-surfaceHighlight text-textMuted whitespace-nowrap">${this._esc(this._kindLabel(kind))}</span>
                ${editBtn}
                <button data-click="Journal.deleteCalendarEvent" data-click-arg="${ev.id}" class="shrink-0 min-h-11 px-2 text-xs text-textMuted hover:text-danger" aria-label="${delLabel}">✕</button>
            </div>`;
        }).join('');
    },

    async calendarSelect(iso) {
        if (!iso) return;
        JournalState.calendarSelected = String(iso).slice(0, 10);
        const month = JournalState.calendarSelected.slice(0, 7);
        if (month !== JournalState.calendarMonth) {
            JournalState.calendarMonth = month;
            await this._loadCalendar();
        }
        this._renderCalendar();
    },

    async calendarShift(delta) {
        JournalState.calendarMonth = shiftMonth(JournalState.calendarMonth || monthKey(new Date()), delta);
        // 換月時把選取日移到那個月 1 號，避免清單停在看不到的日子
        JournalState.calendarSelected = `${JournalState.calendarMonth}-01`;
        await this._loadCalendar();
        this._renderCalendar();
    },

    async calendarPrev() { await this.calendarShift(-1); },
    async calendarNext() { await this.calendarShift(1); },

    async calendarToday() {
        const todayIso = this._calendarTodayIso();
        JournalState.calendarMonth = todayIso.slice(0, 7);
        JournalState.calendarSelected = todayIso;
        await this._loadCalendar();
        this._renderCalendar();
    },

    async addCalendarEvent() {
        // 送出中再按一次會建兩筆（第二筆撞 409 或變成重複事件）
        if (this._savingCalendar) return;
        const dateEl = document.getElementById('journal-calendar-date');
        const titleEl = document.getElementById('journal-calendar-title');
        const repeatEl = document.getElementById('journal-calendar-repeat');
        const status = document.getElementById('journal-calendar-status');
        const eventDate = dateEl?.value || '';
        const title = (titleEl?.value || '').trim();
        const recurrence = repeatEl?.value || 'none';
        if (!eventDate || !title) {
            if (status) status.textContent = this._t('journal.calendar.needDateTitle', 'Pick a date and write what to remember');
            return;
        }
        const editing = JournalState.calendarEditing;
        this._savingCalendar = true;
        try {
            if (editing) {
                // 重複事件＝整個系列一起改；沒動到的欄位照原本的送回去
                await AppAPI.put(`/api/journal/calendar/${encodeURIComponent(editing.id)}`, {
                    event_date: eventDate,
                    title,
                    remind_days_before: editing.remind_days_before ?? 1,
                    note: editing.note || '',
                    recurrence,
                    recurrence_until: recurrence === 'none' ? null : (editing.recurrence_until || null),
                });
            } else {
                await AppAPI.post('/api/journal/calendar', { event_date: eventDate, title, remind_days_before: 1, recurrence });
            }
            this._resetCalendarForm();
            if (status) status.textContent = editing
                ? this._t('journal.calendar.saved', 'Saved')
                : this._t('journal.calendar.added', 'Added');
            // 新增／改到哪一天就跳到那一天（可能在別的月份）
            JournalState.calendarSelected = eventDate;
            JournalState.calendarMonth = eventDate.slice(0, 7);
            await this._loadCalendar();
            this._renderCalendar();
        } catch (e) {
            console.error('[Journal] save calendar event failed:', e);
            const code = e && (e.status || (e.response && e.response.status));
            if (status) {
                status.textContent = code === 409
                    ? this._t('journal.calendar.duplicate', 'You already have this event on that day')
                    : editing
                        ? this._t('journal.calendar.saveFailed', 'Could not save the event, please try again')
                        : this._t('journal.calendar.addFailed', 'Could not add the event, please try again');
            }
        } finally {
            this._savingCalendar = false;
        }
    },

    _resetCalendarForm() {
        JournalState.calendarEditing = null;
        const titleEl = document.getElementById('journal-calendar-title');
        const repeatEl = document.getElementById('journal-calendar-repeat');
        const cancelEl = document.getElementById('journal-calendar-cancel');
        const labelEl = document.getElementById('journal-calendar-submit-label');
        if (titleEl) titleEl.value = '';
        if (repeatEl) repeatEl.value = 'none';
        if (cancelEl) cancelEl.classList.add('hidden');
        if (labelEl) {
            // 同步改 data-i18n，語系重掃時才不會把字蓋回去
            labelEl.dataset.i18n = 'journal.calendar.add';
            labelEl.textContent = this._t('journal.calendar.add', 'Add');
        }
    },

    editCalendarEvent(id) {
        const ev = (JournalState.calendar || []).find((e) => String(e.id) === String(id));
        if (!ev || ev.source !== 'user') return;
        JournalState.calendarEditing = ev;
        const dateEl = document.getElementById('journal-calendar-date');
        const titleEl = document.getElementById('journal-calendar-title');
        const repeatEl = document.getElementById('journal-calendar-repeat');
        const cancelEl = document.getElementById('journal-calendar-cancel');
        const labelEl = document.getElementById('journal-calendar-submit-label');
        const status = document.getElementById('journal-calendar-status');
        // 重複事件改的是整個系列：日期欄放錨點日，不是點到的那一次
        if (dateEl) dateEl.value = ev.series_start || ev.event_date;
        if (titleEl) titleEl.value = ev.title || '';
        if (repeatEl) repeatEl.value = ev.recurrence || 'none';
        if (cancelEl) cancelEl.classList.remove('hidden');
        if (labelEl) {
            labelEl.dataset.i18n = 'journal.calendar.save';
            labelEl.textContent = this._t('journal.calendar.save', 'Save');
        }
        if (status) status.textContent = ev.recurrence && ev.recurrence !== 'none'
            ? this._t('journal.calendar.editSeriesHint', 'Changes apply to every repeat')
            : '';
        if (titleEl) titleEl.focus();
    },

    cancelCalendarEdit() {
        this._resetCalendarForm();
        const status = document.getElementById('journal-calendar-status');
        if (status) status.textContent = '';
        this._renderCalendar();
    },

    async deleteCalendarEvent(id) {
        const status = document.getElementById('journal-calendar-status');
        const ev = (JournalState.calendar || []).find((e) => String(e.id) === String(id));
        // 單次事件也要確認：✕ 就在編輯鈕旁邊，誤觸即刪、沒有復原
        const isSeries = ev && ev.recurrence && ev.recurrence !== 'none';
        const msg = isSeries
            ? this._t('journal.calendar.deleteSeriesConfirm', 'Delete every repeat of this event?')
            : this._t('journal.calendar.deleteConfirm', 'Delete this event?');
        const ok = typeof window.showConfirmDialog === 'function'
            ? await window.showConfirmDialog({ title: msg, message: (ev && ev.title) || '', danger: true })
            : window.confirm(msg);
        if (!ok) return;
        try {
            await AppAPI.delete(`/api/journal/calendar/${encodeURIComponent(id)}`);
            if (JournalState.calendarEditing && String(JournalState.calendarEditing.id) === String(id)) {
                this._resetCalendarForm();
            }
            JournalState.calendar = (JournalState.calendar || []).filter((ev) => String(ev.id) !== String(id));
            this._renderCalendar();
            if (status) status.textContent = '';
        } catch (e) {
            console.error('[Journal] delete calendar event failed:', e);
            if (status) status.textContent = this._t('journal.calendar.deleteFailed', 'Could not delete the event');
        }
    },

    // ── 投資持倉視圖（UX 第二輪）─────────────────────────────

    async _loadPositions() {
        try {
            const res = await AppAPI.get('/api/journal/positions');
            JournalState.positions = res.positions || [];
        } catch (e) {
            console.error('[Journal] load positions failed:', e);
            JournalState.positions = [];
        }
    },

    _renderInvestSummary() {
        const container = document.getElementById('journal-invest-summary');
        if (!container) return;

        const open = JournalState.positions.filter(p => parseFloat(p.quantity) > 0);
        let realized = 0;
        let unrealized = 0;
        let hasRealized = false;
        let hasUnrealized = false;
        for (const p of JournalState.positions) {
            const r = parseFloat(p.realized_pnl || 0);
            if (r !== 0) { realized += r; hasRealized = true; }
            if (p.unrealized_pnl != null) {
                unrealized += parseFloat(p.unrealized_pnl);
                hasUnrealized = true;
            }
        }
        // 先取整再判斷正負：顯示只到整數，-0.3 會變成紅色的「≈ -0」（|| 0 把 -0 轉成 0）
        realized = Math.round(realized) || 0;
        unrealized = Math.round(unrealized) || 0;
        // 合計為混合幣別近似值（沿用 agent 工具口徑）——「≈」標示
        const fmt = (v) => (v >= 0 ? '+' : '') + v.toLocaleString('en', { maximumFractionDigits: 0 });
        const approxNote = window.I18n ? window.I18n.t('journal.approxNote', { defaultValue: 'Mixed-currency approximate total' }) : 'Mixed-currency approximate total';
        // cards 用 positions 的 trade_count 總和（entries 上限 100 筆會低估）
        const tradeCount = Math.max(
            JournalState.positions.reduce((s, p) => s + (p.trade_count || 0), 0),
            JournalState.entries.length,
        );
        const cards = [
            { label: 'journal.positionsCount', text: String(open.length), icon: 'layers', color: 'text-primary', title: '' },
            { label: 'journal.investTradeCount', text: String(tradeCount), icon: 'trending-up', color: 'text-primary', title: '' },
            { label: 'journal.realizedTotal', text: hasRealized ? '≈ ' + fmt(realized) : '—', icon: 'badge-check', color: realized >= 0 ? 'text-success' : 'text-danger', title: approxNote },
            { label: 'journal.unrealizedTotal', text: hasUnrealized ? '≈ ' + fmt(unrealized) : '—', icon: 'line-chart', color: unrealized >= 0 ? 'text-success' : 'text-danger', title: approxNote },
        ];

        container.innerHTML = cards.map(c => `
            <div class="bg-surface border border-borderLight rounded-2xl p-4 min-w-0" ${c.title ? `title="${this._esc(c.title)}"` : ''}>
                <div class="flex items-center gap-2 text-textMuted text-xs">
                    <i data-lucide="${c.icon}" class="w-4 h-4 ${c.color}"></i>
                    <span class="truncate">${this._catLabel(c.label)}</span>
                </div>
                <div class="mt-2 ${c.text.length > 13 ? 'text-sm' : c.text.length > 10 ? 'text-base' : 'text-xl'} font-bold ${c.color} truncate tabular-nums">${c.text}</div>
            </div>
        `).join('');
    },

    _renderPositions() {
        const container = document.getElementById('journal-positions-list');
        if (!container) return;

        const open = JournalState.positions.filter(p => parseFloat(p.quantity) > 0);
        if (!open.length) {
            container.innerHTML = '<div class="text-center text-textMuted py-8 text-sm">' +
                (window.I18n ? window.I18n.t('journal.emptyPositions') : 'No open positions') + '</div>';
            return;
        }

        const UNITS = { crypto: '', tw_stock: window.I18n ? window.I18n.t('journal.units.tw_stock') : '張', us_stock: window.I18n ? window.I18n.t('journal.units.us_stock') : '股', hk_stock: window.I18n ? window.I18n.t('journal.units.hk_stock') : '股', jp_stock: window.I18n ? window.I18n.t('journal.units.jp_stock') : '株', kr_stock: window.I18n ? window.I18n.t('journal.units.kr_stock') : '股', cn_stock: window.I18n ? window.I18n.t('journal.units.cn_stock') : '股', forex: 'lot', commodity: window.I18n ? window.I18n.t('journal.units.commodity') : '口' };
        container.innerHTML = open.map(p => {
            const qty = parseFloat(p.quantity);
            const avg = parseFloat(p.avg_cost);
            const unit = UNITS[p.market] || '';
            const lev = p.leverage > 1 ? ` ${p.leverage}x` : '';
            const dir = p.direction === 'short' ? ' 🔻' : '';
            const unrl = p.unrealized_pnl != null ? parseFloat(p.unrealized_pnl) : null;
            const rl = parseFloat(p.realized_pnl || 0);
            const holding = this._holdingSince(p.first_traded_at);
            const pnlHtml = unrl !== null
                ? `<div class="text-sm font-bold ${unrl >= 0 ? 'text-success' : 'text-danger'}">${unrl >= 0 ? '+' : ''}${unrl.toLocaleString('en', { maximumFractionDigits: 2 })} ${this._esc(p.currency)}</div>`
                : '<div class="text-sm text-textMuted">—</div>';
            const priceHtml = p.current_price != null
                ? parseFloat(p.current_price).toLocaleString('en', { maximumFractionDigits: 8 })
                : '—';
            const stat = (label, value) => `
                <div class="text-center">
                    <div class="text-[10px] text-textMuted/60">${label}</div>
                    <div class="text-xs text-secondary font-medium mt-0.5">${value}</div>
                </div>`;

            return `
                <div class="bg-surface border border-borderLight rounded-2xl p-3.5">
                    <div class="flex items-center justify-between gap-3">
                        <div class="min-w-0">
                            <div class="text-sm font-bold text-secondary truncate">
                                ${this._esc(p.symbol)}${lev}${dir}
                                <span class="text-textMuted/50 text-xs">${this._esc(this._marketLabel(p.market))}</span>
                            </div>
                            <div class="text-xs text-textMuted mt-0.5">
                                ${qty.toLocaleString('en', { maximumFractionDigits: 8 })}${unit ? ' ' + this._esc(unit) : ''}
                                @ ${avg.toLocaleString('en', { maximumFractionDigits: 8 })} ${this._esc(p.currency)}
                            </div>
                        </div>
                        <div class="text-right shrink-0">
                            ${pnlHtml}
                        </div>
                    </div>
                    <div class="flex justify-between gap-2 mt-3 pt-2.5 border-t border-borderSubtle/60">
                        ${stat(this._catLabel('journal.currentPrice'), priceHtml)}
                        ${stat(this._catLabel('journal.avgCost'), avg.toLocaleString('en', { maximumFractionDigits: 8 }))}
                        ${stat(this._catLabel('journal.realizedPnl'), rl !== 0 ? (rl > 0 ? '+' : '') + rl.toLocaleString('en', { maximumFractionDigits: 2 }) : '—')}
                        ${stat(this._catLabel('journal.holdingSince'), holding || '—')}
                    </div>
                </div>
            `;
        }).join('');
    },

    _holdingSince(fromIso) {
        // 持有期間＝first_traded_at 至今（衍生值，不落欄位）
        if (!fromIso) return '';
        const from = new Date(fromIso);
        if (isNaN(from.getTime())) return '';
        const yLabel = window.I18n ? window.I18n.t('journal.yearUnit', { defaultValue: 'y' }) : 'y';
        const mLabel = window.I18n ? window.I18n.t('journal.monthUnit', { defaultValue: 'mo' }) : 'mo';
        const months = Math.max(0, Math.round((Date.now() - from.getTime()) / (30.44 * 86400000)));
        const y = Math.floor(months / 12);
        const m = months % 12;
        if (y > 0) return m > 0 ? `${y}${yLabel} ${m}${mLabel}` : `${y}${yLabel}`;
        return `${m}${mLabel}`;
    },
};

// data-click 綁定用
window.Journal = {
    refresh: () => JournalTab.refresh(),
    switchView: (v) => JournalTab.switchView(v),
    setCategory: (cat) => JournalTab.setCategory(cat),
    deleteEntry: (id) => JournalTab.deleteEntry(id),
    showHistory: (id) => JournalTab.showHistory(id),
    closeHistory: () => JournalTab.closeHistory(),
    restoreRevision: (arg) => JournalTab.restoreRevision(arg),
    toggleTrash: () => JournalTab.toggleTrash(),
    undeleteEntry: (id) => JournalTab.undeleteEntry(id),
    openForm: () => JournalTab.openForm(),
    closeForm: () => JournalTab.closeForm(),
    submitForm: () => JournalTab.submitForm(),
    openEditForm: (id) => JournalTab.openEditForm(id),
    closeEditForm: () => JournalTab.closeEditForm(),
    editModalHistory: () => JournalTab.editModalHistory(),
    editModalDelete: () => JournalTab.editModalDelete(),
    submitEditForm: () => JournalTab.submitEditForm(),
    // 行事曆（每日早報 Phase B）——外觀物件是白名單，漏登記按鈕就靜默沒反應
    addCalendarEvent: () => JournalTab.addCalendarEvent(),
    deleteCalendarEvent: (id) => JournalTab.deleteCalendarEvent(id),
    editCalendarEvent: (id) => JournalTab.editCalendarEvent(id),
    cancelCalendarEdit: () => JournalTab.cancelCalendarEdit(),
    refreshScorecard: () => JournalTab.refreshScorecard(),
    submitCall: () => JournalTab.submitCall(),
    cancelCall: (id) => JournalTab.cancelCall(id),
    calendarSelect: (iso) => JournalTab.calendarSelect(iso),
    calendarPrev: () => JournalTab.calendarPrev(),
    calendarNext: () => JournalTab.calendarNext(),
    calendarToday: () => JournalTab.calendarToday(),
    init: () => JournalTab.init(),
};

// Export for lazy-loading
window.JournalTab = JournalTab;
export { JournalTab };
