// ========================================
// journal-calendar.js — 帳本行事曆的月曆格點（純函式，node 可測）
// 一格一天、週日起算、固定 6 列 × 7 欄（42 格）；事件依日期分組。
// ========================================

const KIND_ORDER = ['earnings', 'revenue', 'report', 'unlock', 'macro', 'custom'];

function pad2(n) {
    return String(n).padStart(2, '0');
}

// 本地日期 → 'YYYY-MM-DD'（不走 toISOString，避免時區把日期往前推一天）
function isoDate(d) {
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

function parseIso(iso) {
    const [y, m, d] = String(iso || '').slice(0, 10).split('-').map(Number);
    return new Date(y, (m || 1) - 1, d || 1);
}

function monthKey(d) {
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}`;
}

function shiftMonth(key, delta) {
    const [y, m] = key.split('-').map(Number);
    const d = new Date(y, m - 1 + delta, 1);
    return monthKey(d);
}

// 月曆格點：從該月 1 號所在週的週日開始，共 42 天
function buildMonthGrid(key, todayIso) {
    const [y, m] = key.split('-').map(Number);
    const first = new Date(y, m - 1, 1);
    const start = new Date(y, m - 1, 1 - first.getDay());
    const cells = [];
    for (let i = 0; i < 42; i += 1) {
        const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
        cells.push({
            iso: isoDate(d),
            day: d.getDate(),
            inMonth: d.getMonth() === m - 1,
            isToday: isoDate(d) === todayIso,
            weekday: d.getDay(),
        });
    }
    return { key, cells, start: cells[0].iso, end: cells[41].iso };
}

function groupByDate(events) {
    const map = {};
    for (const ev of events || []) {
        const iso = String(ev.event_date || '').slice(0, 10);
        if (!iso) continue;
        (map[iso] = map[iso] || []).push(ev);
    }
    for (const iso of Object.keys(map)) {
        map[iso].sort((a, b) => KIND_ORDER.indexOf(a.kind || 'custom') - KIND_ORDER.indexOf(b.kind || 'custom'));
    }
    return map;
}

// 一格最多畫三個點：同類型只算一次，其餘用 +N
function cellSummary(dayEvents) {
    const kinds = [];
    for (const ev of dayEvents || []) {
        const k = ev.source === 'system' ? ev.kind || 'custom' : 'custom';
        if (!kinds.includes(k)) kinds.push(k);
    }
    return { kinds: kinds.slice(0, 3), count: (dayEvents || []).length };
}

function weekdayLabels(lang) {
    try {
        const fmt = new Intl.DateTimeFormat(lang || undefined, { weekday: 'narrow' });
        // 2026-09-06 是週日
        return Array.from({ length: 7 }, (_, i) => fmt.format(new Date(2026, 8, 6 + i)));
    } catch (e) {
        return ['S', 'M', 'T', 'W', 'T', 'F', 'S'];
    }
}

function monthLabel(key, lang) {
    const [y, m] = key.split('-').map(Number);
    try {
        return new Intl.DateTimeFormat(lang || undefined, { year: 'numeric', month: 'long' }).format(new Date(y, m - 1, 1));
    } catch (e) {
        return key;
    }
}

export { KIND_ORDER, isoDate, parseIso, monthKey, shiftMonth, buildMonthGrid, groupByDate, cellSummary, weekdayLabels, monthLabel };
