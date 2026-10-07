// 詐騙檢查分頁（2026-09-27）的純邏輯：地址驗證、API 判定 → 畫面狀態、動作網址、?address= 讀取。
// 合規：只有 verdict=no_red_flags 能變綠；查不完整、失敗、看不懂的回應一律中性「無法完成檢查」。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;

const moduleUrl = await loadModuleUrl('web/js/scam-check.js');
const {
    EXAMPLE_ADDRESS,
    validateAddress,
    mapCheckResult,
    errorView,
    askPrompt,
    reportUrl,
    detailUrl,
    takeAddressParam,
    shortAddress,
    resultHtml,
    recentHtml,
} = await import(moduleUrl);

const EVM = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913';
const TON = 'EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs';

// ---- 地址驗證 ----
assert.equal(EXAMPLE_ADDRESS, EVM, '範例是 Base 上的 USDC');
assert.deepEqual(validateAddress(`  ${EVM}\n`), { ok: true, value: EVM, family: 'evm' }, '自動 trim');
assert.deepEqual(validateAddress(EVM.toLowerCase()), { ok: true, value: EVM.toLowerCase(), family: 'evm' });
assert.deepEqual(validateAddress(TON), { ok: true, value: TON, family: 'ton' }, '後端也收 TON，別擋掉');
assert.equal(validateAddress(`0:${'a'.repeat(64)}`).family, 'ton', 'TON raw 格式');
assert.deepEqual(validateAddress('   '), { ok: false, value: '', error: 'empty' });
assert.deepEqual(validateAddress(null), { ok: false, value: '', error: 'empty' });
for (const bad of ['0x123', `0x${'g'.repeat(40)}`, `${EVM}0`, 'hello world', `0X${EVM.slice(2)}`]) {
    assert.equal(validateAddress(bad).ok, false, `${bad} 要擋`);
    assert.equal(validateAddress(bad).error, 'format');
}

// ---- 判定對應 ----
const community = (found, extra = {}) => ({ status: 'ok', found, ...extra });
const clean = mapCheckResult({
    success: true,
    address: EVM,
    family: 'evm',
    verdict: 'no_red_flags',
    reasons: [],
    sources: { community: community(false), goplus: { status: 'ok', flags: [], has_records: true } },
});
assert.equal(clean.state, 'clean');
assert.deepEqual(clean.flags, []);
assert.deepEqual(
    clean.sources.map((s) => [s.id, s.status]),
    [
        ['community', 'noReports'],
        ['goplus', 'noFlags'],
    ]
);

const reported = mapCheckResult({
    success: true,
    family: 'evm',
    verdict: 'high_risk',
    reasons: ['community_report', 'phishing_activities', 'phishing_activities'],
    sources: {
        community: community(true, { report_id: 42 }),
        goplus: { status: 'ok', flags: ['phishing_activities'] },
    },
});
assert.equal(reported.state, 'danger');
assert.deepEqual(reported.flags, ['community_report', 'phishing_activities'], '去重、保留順序');
assert.deepEqual(reported.sources[0], { id: 'community', status: 'reported', reportId: 42 });
assert.equal(reported.sources[1].status, 'flagged');

const adminKey = mapCheckResult({
    success: true,
    family: 'ton',
    verdict: 'caution',
    reasons: ['jetton_admin_privilege'],
    sources: { community: community(false), tonapi: { status: 'ok', verification: 'none', has_admin: true } },
});
assert.equal(adminKey.state, 'caution', '有具體訊號的 caution 才是黃色');
assert.deepEqual(
    adminKey.sources.map((s) => s.status),
    ['noReports', 'adminRights'],
    '來源列不能跟上面的風險訊號互相矛盾（顯示「沒有標記」）'
);

const goplusDown = mapCheckResult({
    success: true,
    family: 'evm',
    verdict: 'caution',
    reasons: [],
    sources: { community: community(false), goplus: { status: 'error', detail: 'unavailable' } },
});
assert.equal(goplusDown.state, 'incomplete', 'GoPlus 沒回應：中性「無法完成」，不是黃色也不是綠色');
assert.equal(goplusDown.sources[1].status, 'unavailable');

const communityDown = mapCheckResult({
    success: true,
    family: 'evm',
    verdict: 'caution',
    reasons: [],
    sources: { community: { status: 'error', found: false }, goplus: { status: 'ok', flags: [] } },
});
assert.equal(communityDown.state, 'incomplete');
assert.equal(communityDown.sources[0].status, 'unavailable');

const blacklisted = mapCheckResult({
    success: true,
    family: 'ton',
    verdict: 'high_risk',
    reasons: ['tonapi_blacklist'],
    sources: { community: community(false), tonapi: { status: 'ok', verification: 'blacklist' } },
});
assert.equal(blacklisted.state, 'danger');
assert.equal(blacklisted.sources[1].status, 'blacklisted');

// 看不懂的回應絕對不能變綠
for (const weird of [null, {}, { success: false, verdict: 'no_red_flags' }, { success: true, verdict: 'safe' }, 'x']) {
    assert.notEqual(mapCheckResult(weird).state, 'clean', JSON.stringify(weird));
    assert.equal(mapCheckResult(weird).state, 'incomplete');
}
// reasons 不是陣列、來源缺欄位也不炸
assert.deepEqual(mapCheckResult({ success: true, verdict: 'high_risk', reasons: 'x', sources: null }).flags, []);

// ---- 請求失敗 ----
assert.deepEqual(errorView({ status: 422 }), { state: 'invalid' });
for (const err of [{ status: 500 }, { status: 429 }, { status: 404 }, { status: 0 }, new Error('offline'), null]) {
    assert.deepEqual(errorView(err), { state: 'error' }, JSON.stringify(err));
}

// ---- 動作 ----
const t = (key, fallback, vars) =>
    vars ? fallback.replace(/\{\{(\w+)\}\}/g, (m, k) => (k in vars ? vars[k] : m)) : fallback;
assert.ok(askPrompt(t, EVM).includes(EVM), '問 AI 的提示要帶地址');
assert.equal(reportUrl(EVM), `/static/scam-tracker/submit.html?address=${EVM}`);
assert.equal(reportUrl('a&b=c'), '/static/scam-tracker/submit.html?address=a%26b%3Dc', 'encode');
assert.equal(detailUrl('7"><x'), '/static/scam-tracker/detail.html?id=7%22%3E%3Cx');
assert.equal(shortAddress(EVM), '0x8335…2913');
assert.equal(shortAddress('short'), 'short');

// ---- ?address= 讀取：讀完從網址拿掉，其他參數留著 ----
assert.deepEqual(takeAddressParam(`?address=${EVM}`), { address: EVM, search: '' });
assert.deepEqual(takeAddressParam(`?ref=x&address=${EVM}`), { address: EVM, search: '?ref=x' });
assert.deepEqual(takeAddressParam('?ref=x'), { address: null, search: '?ref=x' });
assert.deepEqual(takeAddressParam(''), { address: null, search: '' });
assert.deepEqual(takeAddressParam('?address=%20%20'), { address: null, search: '' }, '空白也要清掉');

// ---- 畫面：外部字串一律 escape；狀態色調對得上；中性狀態沒有「綠色」 ----
const html = resultHtml(
    mapCheckResult({
        success: true,
        family: 'evm',
        verdict: 'high_risk',
        reasons: ['<img src=x onerror=alert(1)>'],
        sources: { community: community(true, { report_id: '1"><script>' }), goplus: { status: 'ok', flags: [] } },
    }),
    '0x<b>',
    t
);
assert.ok(!html.includes('<img src=x'), 'reason 要 escape');
assert.ok(!html.includes('<script>'), 'report id 要 escape');
assert.ok(!html.includes('0x<b>'), '地址要 escape');
assert.ok(html.includes('data-scamcheck-state="danger"'));
assert.ok(html.includes('text-danger'));

const cleanHtml = resultHtml(clean, EVM, t);
assert.ok(cleanHtml.includes('data-scamcheck-state="clean"'));
assert.ok(cleanHtml.includes('text-success'));
assert.ok(cleanHtml.includes('scamcheck.note') || /not a guarantee/i.test(cleanHtml), '綠色一定帶「不是保證」');

for (const view of [goplusDown, { state: 'error' }]) {
    const neutral = resultHtml(view, EVM, t);
    assert.ok(!neutral.includes('text-success'), `${view.state} 不能是綠色`);
    assert.ok(neutral.includes('data-click="ScamCheckTab.retry"'), `${view.state} 要能重試`);
}
for (const view of [clean, reported, adminKey, goplusDown]) {
    const h = resultHtml(view, EVM, t);
    assert.ok(h.includes('data-click="ScamCheckTab.askAI"'));
    assert.ok(h.includes('data-click="ScamCheckTab.report"'));
    assert.ok(!/\son[a-z]+\s*=/.test(h.replace(/&lt;[^]*?&gt;/g, '')), 'no inline handlers');
}
assert.ok(resultHtml(adminKey, TON, t).includes('amber'), 'caution 是 amber');

// ---- 社群舉報列表：空的不畫；外部字串 escape；連到 detail ----
assert.equal(recentHtml([], t, 'en', new Date()), '');
assert.equal(recentHtml(null, t, 'en', new Date()), '');
const now = new Date('2026-09-27T12:00:00Z');
const recent = recentHtml(
    [
        {
            id: 9,
            scam_wallet_address: '0xabc<script>def0000000000000000000000000000000000',
            scam_type: 'phishing',
            verification_status: 'verified',
            created_at: '2026-09-26T12:00:00Z',
        },
        { id: 'bad"', scam_wallet_address: EVM, scam_type: '<i>x</i>', verification_status: 'weird', created_at: 'nope' },
    ],
    t,
    'en',
    now
);
assert.ok(recent.includes('/static/scam-tracker/detail.html?id=9'));
assert.ok(!recent.includes('<script>') && !recent.includes('<i>x</i>'));
assert.ok(recent.includes('id=bad%22'));

console.log('scam check tests passed');
