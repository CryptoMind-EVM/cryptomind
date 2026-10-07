// 送出舉報／留言失敗的訊息（2026-09-25）：後端 detail.reason 對得到的關卡
// （Premium／每日上限／內容過濾／舉報不存在）走 i18n；其餘沿用 detail.message、
// 字串 detail、422 陣列。submit 頁的「今日剩餘次數」從 /reports/quota 來，不再寫死 5。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
globalThis.I18n = {
    t: (key, params) => `T:${key}${params ? JSON.stringify(params) : ''}`,
};
globalThis.AuthManager = { currentUser: { user_id: 'u1' } };
const elements = new Map();
globalThis.document = { getElementById: (id) => elements.get(id) || null };
globalThis.console.error = () => {};
let nextResponse;
const fetched = [];
globalThis.fetch = async (url) => {
    fetched.push(url);
    if (nextResponse instanceof Error) throw nextResponse;
    return nextResponse;
};
const respond = (status, body) => {
    nextResponse = { ok: status < 400, status, json: async () => body };
};

await import(
    await loadModuleUrl(
        new URL('../../web/scam-tracker/js/scam-tracker.js', import.meta.url).pathname,
    )
);
const api = window.ScamTrackerAPI;
const app = window.ScamTrackerApp;

async function errorOf(call, status, body) {
    respond(status, body);
    try {
        await call();
    } catch (e) {
        return e.message;
    }
    throw new Error('應該要丟錯');
}

// ── 舉報 ──
const submit = () => api.submitReport({});
assert.equal(
    await errorOf(submit, 403, { detail: { reason: 'premium_membership_required', message: 'en' } }),
    'T:safety.proRequiredDesc',
);
assert.equal(
    await errorOf(submit, 429, {
        detail: { reason: 'daily_limit_reached', message: 'en', limit: 5, used: 5 },
    }),
    'T:safety.dailyLimitReached{"limit":5}',
);
assert.equal(
    await errorOf(submit, 400, {
        detail: { reason: 'content_validation_failed', message: 'en', warnings: ['x'] },
    }),
    'T:safety.contentRejected',
);
// 沒有 reason 的照舊
assert.equal(await errorOf(submit, 409, { detail: { error: 'x', message: 'already' } }), 'already');
assert.equal(await errorOf(submit, 400, { detail: 'Invalid transaction hash: x' }), 'Invalid transaction hash: x');
assert.equal(await errorOf(submit, 422, { detail: [{ msg: 'a' }, { msg: 'b' }] }), 'a, b');

// ── 留言 ──
const comment = () => api.addComment(3, 'some comment text');
assert.equal(
    await errorOf(comment, 403, { detail: { reason: 'premium_membership_required', message: 'en' } }),
    'T:safety.proRequired',
);
assert.equal(
    await errorOf(comment, 400, {
        detail: { reason: 'content_validation_failed', message: 'en', warnings: ['x'] },
    }),
    'T:safety.contentRejected',
);
assert.equal(
    await errorOf(comment, 404, { detail: { reason: 'report_not_found', message: 'en' } }),
    'T:safety.reportNotExist',
);
assert.equal(await errorOf(comment, 422, { detail: [{ msg: 'c' }] }), 'c');
assert.equal(await errorOf(comment, 500, { detail: 'boom' }), 'boom');

// ── 今日剩餘次數 ──
function quotaElements() {
    const removed = [];
    const notice = { classList: { remove: (c) => removed.push(c) } };
    const copy = { dataset: { i18n: 'stale' }, textContent: '', closest: () => notice };
    elements.set('daily-limit-copy', copy);
    return { copy, removed };
}

let q = quotaElements();
fetched.length = 0;
respond(200, { success: true, limit: 5, used: 2, remaining: 3 });
await app.loadQuota();
assert.deepEqual(fetched, ['/api/scam-tracker/reports/quota']);
assert.equal(q.copy.textContent, 'T:safety.dailyQuota{"count":3}');
// 語言切換時 i18n.js 全頁重掃會用 data-i18n／data-i18n-args 重畫——兩個都要對
assert.equal(q.copy.dataset.i18n, 'safety.dailyQuota');
assert.equal(q.copy.dataset.i18nArgs, '{"count":3}');
assert.deepEqual(q.removed, ['hidden']);

q = quotaElements();
q.copy.dataset.i18nArgs = '{"count":3}';
nextResponse = new Error('network down');
await app.loadQuota();
assert.equal(q.copy.textContent, 'T:safety.quotaLoadFailed');
assert.equal(q.copy.dataset.i18n, 'safety.quotaLoadFailed');
assert.equal(q.copy.dataset.i18nArgs, undefined);

q = quotaElements();
respond(500, { detail: 'x' });
await app.loadQuota();
assert.equal(q.copy.dataset.i18n, 'safety.quotaLoadFailed');

elements.clear();
await app.loadQuota(); // 沒有這個元素（非 Premium 表單被換掉）不能炸

console.error = (m) => process.stderr.write(`${m}\n`);
console.error('scam_submit_errors: ok');
