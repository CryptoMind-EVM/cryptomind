// 六個市場頁的「AI 深度分析」（2026-09-25 前端盤查）：
// 1) 等待回應期間使用者改了輸入框 → 快取要存在「送出時的代號」底下，不是改過的那個；
// 2) 非 2xx 回應不能被當成分析結果寫進 localStorage（留 7 天）或記憶體快取，要顯示錯誤。
// 一般（非深度）分析 refreshAIPulse 也一樣（第 4 段，2026-09-25 市場頁盤查）。
// 這些檔案是傳統 script（window.XxxTab = (() => {...})()），用 vm 在假 window 裡跑。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const PAGES = [
    ['krstock', 'KRStockTab'],
    ['jpstock', 'JPStockTab'],
    ['astock', 'AStockTab'],
    ['instock', 'INStockTab'],
    ['forex', 'ForexTab'],
    ['commodity', 'CommodityTab'],
];

function makeEl(id) {
    const classes = new Set();
    return {
        id,
        value: '',
        innerHTML: '',
        textContent: '',
        style: {},
        dataset: {},
        classList: {
            add: (...c) => c.forEach((x) => classes.add(x)),
            remove: (...c) => c.forEach((x) => classes.delete(x)),
            contains: (c) => classes.has(c),
            toggle: (c, on) => ((on ?? !classes.has(c)) ? classes.add(c) : classes.delete(c)),
        },
        appendChild() {},
        addEventListener() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
    };
}

function makeContext() {
    const els = {};
    const store = new Map();
    const cache = {};
    const ctx = {
        console: { log() {}, warn() {}, error() {}, info() {}, debug() {} },
        setTimeout,
        clearTimeout,
        AbortController,
        URLSearchParams,
        Promise,
        showToast() {},
        escapeHtml: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
        localStorage: {
            getItem: (k) => (store.has(k) ? store.get(k) : null),
            setItem: (k, v) => store.set(k, String(v)),
            removeItem: (k) => store.delete(k),
        },
        document: {
            getElementById: (id) => (els[id] ||= makeEl(id)),
            createElement: () => makeEl(''),
            querySelector: () => null,
            querySelectorAll: () => [],
            addEventListener() {},
        },
        I18n: { t: (k, o) => (o && o.msg !== undefined ? `${k}: ${o.msg}` : k), getLanguage: () => 'en' },
        AppCache: {
            setWithTime: (k, v) => { cache[k] = v; },
            get: (k) => cache[k],
            getTimeStr: () => '',
            savedAt: {},
        },
        fetch: null,
    };
    ctx.window = ctx;
    ctx.globalThis = ctx;
    vm.createContext(ctx);
    return { ctx, els, store, cache };
}

const aiKeys = (store) => [...store.keys()].filter((k) => k.startsWith('ai_deep_'));

for (const [file, globalName] of PAGES) {
    const src = readFileSync(new URL(`../../web/js/${file}.js`, import.meta.url), 'utf8');
    const { ctx, els, store, cache } = makeContext();
    vm.runInContext(src, ctx, { filename: `${file}.js` });
    const tab = ctx[globalName];
    assert.ok(tab && typeof tab.runDeepAnalysis === 'function', `${file}: 沒有 ${globalName}.runDeepAnalysis`);
    const input = ctx.document.getElementById(`${file}PulseSearchInput`);
    const result = ctx.document.getElementById(`${file}-pulse-result`);

    // 1) 等待期間改輸入框 → 仍以送出時的代號存快取
    input.value = 'aaa';
    let resolveFetch;
    const calls = [];
    ctx.fetch = (url) => {
        calls.push(url);
        return new Promise((r) => { resolveFetch = r; });
    };
    const pending = tab.runDeepAnalysis(true);
    await new Promise((r) => setImmediate(r));
    assert.equal(calls.length, 1, `${file}: 應該送出一次請求`);
    assert.ok(calls[0].includes('/pulse/AAA'), `${file}: 請求代號應為 AAA，實際 ${calls[0]}`);
    input.value = 'bbb';
    resolveFetch({ ok: true, status: 200, json: async () => ({ symbol: 'AAA', current_price: 1, change_24h: 0 }) });
    await pending;
    const keys = aiKeys(store);
    assert.equal(keys.length, 1, `${file}: 應寫入一筆 localStorage，實際 ${JSON.stringify(keys)}`);
    assert.ok(keys[0].includes('_AAA_'), `${file}: localStorage 應存在 AAA 底下，實際 ${keys[0]}`);
    assert.ok(`${file}_pulse_AAA` in cache, `${file}: 記憶體快取應存在 ${file}_pulse_AAA`);
    assert.ok(!(`${file}_pulse_BBB` in cache), `${file}: 不能存到使用者後來改的 BBB`);

    // 2) 非 2xx：不寫任何快取、畫出錯誤（server detail 要 escape）
    store.clear();
    for (const k of Object.keys(cache)) delete cache[k];
    input.value = 'ccc';
    ctx.fetch = async () => ({ ok: false, status: 500, json: async () => ({ detail: 'boom <b>' }) });
    await tab.runDeepAnalysis(true);
    assert.deepEqual(aiKeys(store), [], `${file}: 錯誤回應不能寫進 localStorage`);
    assert.deepEqual(Object.keys(cache), [], `${file}: 錯誤回應不能寫進記憶體快取`);
    assert.ok(result.innerHTML.includes('stock.analysisFailedMsg'), `${file}: 應顯示分析失敗訊息，實際 ${result.innerHTML}`);
    assert.ok(result.innerHTML.includes('boom &lt;b&gt;'), `${file}: detail 應被 escape 後顯示，實際 ${result.innerHTML}`);

    // 3) 422 之類 detail 不是字串 → 顯示 HTTP 狀態碼，不是 [object Object]
    ctx.fetch = async () => ({ ok: false, status: 422, json: async () => ({ detail: [{ msg: 'x' }] }) });
    await tab.runDeepAnalysis(true);
    assert.ok(result.innerHTML.includes('HTTP 422'), `${file}: 非字串 detail 應退回 HTTP 狀態，實際 ${result.innerHTML}`);
    assert.deepEqual(aiKeys(store), [], `${file}: 422 也不能寫快取`);

    // 4) 一般（非深度）分析 refreshAIPulse：以前沒看 r.ok，錯誤的 {detail} 被快取 15 分鐘，
    //    重試一直命中快取看到壞結果。要跟深度分析一樣：只快取成功結果、錯誤 escape 後顯示
    store.clear();
    for (const k of Object.keys(cache)) delete cache[k];
    input.value = 'ddd';
    let pulseCalls = 0;
    ctx.fetch = async (url) => {
        pulseCalls++;
        assert.ok(!url.includes('deep_analysis=true'), `${file}: refreshAIPulse 不該打深度分析，實際 ${url}`);
        return { ok: false, status: 404, json: async () => ({ detail: 'not found <i>' }) };
    };
    tab.refreshAIPulse();
    await new Promise((r) => setTimeout(r, 0));
    assert.deepEqual(Object.keys(cache), [], `${file}: 一般分析的錯誤回應不能寫進記憶體快取，實際 ${JSON.stringify(Object.keys(cache))}`);
    assert.ok(result.innerHTML.includes('stock.loadFailedMsg'), `${file}: 一般分析失敗應顯示錯誤，實際 ${result.innerHTML}`);
    assert.ok(result.innerHTML.includes('not found &lt;i&gt;'), `${file}: detail 應被 escape 後顯示，實際 ${result.innerHTML}`);

    ctx.fetch = async () => {
        pulseCalls++;
        return { ok: true, status: 200, json: async () => ({ symbol: 'DDD', current_price: 1, change_24h: 0 }) };
    };
    tab.refreshAIPulse();
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(pulseCalls, 2, `${file}: 失敗後重試要重新打 API，不是命中快取`);
    assert.ok(`${file}_pulse_DDD` in cache, `${file}: 成功結果才寫快取`);
    void els;
}

console.error('market_deep_analysis: ok');
