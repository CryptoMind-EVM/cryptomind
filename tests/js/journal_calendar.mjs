// 帳本月曆（2026-09-12）：格點、分組、格內摘要、月份位移——純函式。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const m = await import(await loadModuleUrl(new URL('../../web/js/journal-calendar.js', import.meta.url).pathname));

// 1) 42 格、週日起、跨月補齊、今天標記
const g = m.buildMonthGrid('2026-09', '2026-09-12');
assert.equal(g.cells.length, 42);
assert.equal(g.start, '2026-08-30', '9/1 是週二 → 從 8/30 週日開始');
assert.equal(g.end, '2026-10-10');
assert.equal(g.cells.filter((c) => c.inMonth).length, 30);
assert.equal(g.cells[0].weekday, 0);
assert.equal(g.cells.find((c) => c.isToday).iso, '2026-09-12');
assert.ok(!g.cells[0].inMonth && g.cells[2].inMonth);

// 2) 月份位移跨年
assert.equal(m.shiftMonth('2026-12', 1), '2027-01');
assert.equal(m.shiftMonth('2026-01', -1), '2025-12');
assert.equal(m.monthKey(new Date(2026, 0, 31)), '2026-01');

// 3) isoDate 用本地日期，不受時區推移
assert.equal(m.isoDate(new Date(2026, 8, 1, 0, 30)), '2026-09-01');

// 4) 分組與排序（系統事件類型在自訂前面）
const by = m.groupByDate([
    { id: 1, event_date: '2026-09-15', kind: 'custom', source: 'user', title: 'a' },
    { id: 2, event_date: '2026-09-15', kind: 'unlock', source: 'system', title: 'b' },
    { id: 3, event_date: '2026-09-15T00:00:00', kind: 'earnings', source: 'system', title: 'c' },
    { id: 4, event_date: '', title: 'no date' },
]);
assert.deepEqual(Object.keys(by), ['2026-09-15']);
assert.deepEqual(by['2026-09-15'].map((e) => e.id), [3, 2, 1]);

// 5) 格內摘要：同類型合併、最多三個點、總數
const s = m.cellSummary([
    { kind: 'earnings', source: 'system' },
    { kind: 'earnings', source: 'system' },
    { kind: 'macro', source: 'system' },
    { kind: 'unlock', source: 'system' },
    { kind: 'whatever', source: 'user' },
]);
assert.deepEqual(s, { kinds: ['earnings', 'macro', 'unlock'], count: 5 });
assert.deepEqual(m.cellSummary(undefined), { kinds: [], count: 0 });

// 6) 標籤走 Intl（zh-TW 週日起）
assert.equal(m.weekdayLabels('zh-TW').join(''), '日一二三四五六');
assert.equal(m.monthLabel('2026-09', 'en'), 'September 2026');

console.error('journal_calendar: ok');
