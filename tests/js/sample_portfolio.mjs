// Sample portfolio 示範頁（2026-09-27 上市準備 PR-3）的純計算：
// 損益、14 天內事件（日期在 render 時才以今天為基準算）、判斷評分統計。
// 數字是 fixture 算出來的，不寫死在畫面上——改 fixture 時總值／損益不會對不起來。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;

const moduleUrl = await loadModuleUrl('web/js/sample-portfolio.js');
const {
    SAMPLE_HOLDINGS,
    SAMPLE_EVENTS,
    SAMPLE_CALLS,
    summarizeHoldings,
    upcomingEvents,
    scorecardStats,
} = await import(moduleUrl);

// ---- 損益計算 ----
const s = summarizeHoldings([
    { symbol: 'AAA', qty: 1, avgCost: 20, price: 10 },
    { symbol: 'BBB', qty: 2, avgCost: 10, price: 15 },
]);
assert.deepEqual(s.rows.map((r) => r.symbol), ['BBB', 'AAA'], '依市值由大到小');
assert.equal(s.rows[0].value, 30);
assert.equal(s.rows[0].pnl, 10);
assert.equal(s.rows[0].pnlPct, 50);
assert.equal(s.rows[1].pnl, -10);
assert.equal(s.rows[1].pnlPct, -50);
assert.equal(s.totalValue, 40);
assert.equal(s.totalCost, 40);
assert.equal(s.totalPnl, 0);
assert.equal(s.totalPnlPct, 0);
assert.equal(s.rows[0].weightPct, 75, '占比＝市值／總值');
assert.equal(summarizeHoldings([]).totalPnlPct, 0, '空組合不能除以零');

// ---- 14 天內事件：以傳入的今天為基準、排序、過期與太遠的不列 ----
const today = new Date(2026, 8, 27, 15, 30); // 本地時間 2026-09-27 下午
const ev = upcomingEvents(
    [
        { id: 'far', offsetDays: 20 },
        { id: 'b', offsetDays: 8 },
        { id: 'past', offsetDays: -1 },
        { id: 'a', offsetDays: 1 },
        { id: 'edge', offsetDays: 14 },
        { id: 'today', offsetDays: 0 },
    ],
    today
);
assert.deepEqual(ev.map((e) => e.id), ['today', 'a', 'b', 'edge']);
assert.equal(ev[1].date.getFullYear(), 2026);
assert.equal(ev[1].date.getMonth(), 8);
assert.equal(ev[1].date.getDate(), 28);
assert.equal(ev[2].date.getMonth(), 9, '跨月');
assert.equal(ev[2].date.getDate(), 5);
assert.equal(today.getDate(), 27, '不能改到傳入的 today');

// ---- 市場事件不落在週末（週六、日往後挪到週一）；解鎖、個人提醒照原日期 ----
const friday = new Date(2026, 9, 2, 9, 0); // 2026-10-02 週五
const snapped = upcomingEvents(
    [
        { id: 'macroSat', offsetDays: 1, weekdaysOnly: true },
        { id: 'unlockSat', offsetDays: 1 },
        { id: 'macroSun', offsetDays: 2, weekdaysOnly: true },
        { id: 'macroMon', offsetDays: 3, weekdaysOnly: true },
    ],
    friday
);
const byId = Object.fromEntries(snapped.map((e) => [e.id, e]));
assert.equal(byId.unlockSat.date.getDay(), 6, '解鎖可以在週六');
assert.equal(byId.unlockSat.offsetDays, 1);
for (const id of ['macroSat', 'macroSun', 'macroMon']) {
    assert.equal(byId[id].date.getDay(), 1, `${id} 挪到週一`);
    assert.equal(byId[id].offsetDays, 3, `${id} 的距今天數跟著日期改（早報的「幾天後」才對得上）`);
}
assert.deepEqual(snapped.map((e) => e.id).slice(0, 1), ['unlockSat'], '挪完重新排序');
for (let d = 0; d < 7; d += 1) {
    const day = new Date(2026, 8, 27 + d);
    for (const e of upcomingEvents(SAMPLE_EVENTS, day)) {
        if (e.weekdaysOnly) assert.ok(![0, 6].includes(e.date.getDay()), `${e.id} 落在週末`);
    }
    assert.equal(upcomingEvents(SAMPLE_EVENTS, day).length, SAMPLE_EVENTS.length, '挪完仍在 14 天內');
}

// ---- 判斷評分統計 ----
assert.deepEqual(
    scorecardStats([{ status: 'hit' }, { status: 'miss' }, { status: 'pending' }, { status: 'hit' }]),
    { total: 4, scored: 3, hits: 2, hitRate: 67 }
);
assert.deepEqual(scorecardStats([{ status: 'pending' }]), { total: 1, scored: 0, hits: 0, hitRate: 0 });

// ---- fixture 本身 ----
assert.ok(SAMPLE_HOLDINGS.length >= 5, '約 5 檔持倉');
const kinds = new Set(SAMPLE_HOLDINGS.map((h) => h.kind));
assert.ok(kinds.has('crypto') && kinds.has('stock'), '加密貨幣與美股混搭');
const summary = summarizeHoldings(SAMPLE_HOLDINGS);
assert.ok(summary.rows.some((r) => r.pnl < 0), '要有賠錢的部位才像真的');
assert.ok(summary.totalPnl > 0);
const fixtureEvents = upcomingEvents(SAMPLE_EVENTS, today);
assert.equal(fixtureEvents.length, SAMPLE_EVENTS.length, '示範事件全都落在 14 天內');
const eventKinds = new Set(SAMPLE_EVENTS.map((e) => e.kind));
for (const k of ['earnings', 'unlock', 'macro', 'reminder']) {
    assert.ok(eventKinds.has(k), `行事曆要有 ${k}`);
}
assert.deepEqual(scorecardStats(SAMPLE_CALLS), { total: 7, scored: 5, hits: 3, hitRate: 60 });
assert.ok(SAMPLE_CALLS.filter((c) => c.example).length === 2, '兩筆範例判斷');

console.log('sample portfolio tests passed');
