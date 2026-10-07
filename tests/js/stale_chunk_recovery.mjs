// 部署後舊頁面懶載入 404 的自我修復（2026-09-02 回報：
// 「Failed to fetch dynamically imported module: .../exports-la3EoOne.js」
// → EVM 續登失敗）。每次部署整包換掉 assets/ hash，瀏覽器裡殘留的舊頁
// （PWA 回流、Telegram TWA、背景分頁）一跑動態 import 就 404——
// stale-chunk-recovery 必須：認得三種瀏覽器的錯誤訊息、重載一次換新版、
// 用 sessionStorage 守衛擋掉無限重載迴圈、守衛過期後允許再救一次。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const moduleUrl = await loadModuleUrl('web/js/stale-chunk-recovery.js');

// ---- 可重置的 sessionStorage / location 假件 ----
function installGlobals() {
    const store = new Map();
    globalThis.sessionStorage = {
        getItem: (k) => (store.has(k) ? store.get(k) : null),
        setItem: (k, v) => store.set(k, String(v)),
        removeItem: (k) => store.delete(k),
    };
    let reloads = 0;
    globalThis.window = {
        location: {
            reload() {
                reloads += 1;
            },
        },
    };
    return {
        store,
        get reloads() {
            return reloads;
        },
    };
}

const { isStaleChunkError, recoverFromStaleChunk } = await import(moduleUrl);

// ---- isStaleChunkError：三種瀏覽器措辭都要認得 ----
assert.equal(
    isStaleChunkError(
        new TypeError(
            'Failed to fetch dynamically imported module: https://cryptomind-ton.zeabur.app/static/assets/exports-la3EoOne.js'
        )
    ),
    true,
    'Chrome/Edge 措辭要命中'
);
assert.equal(isStaleChunkError(new TypeError('Importing a module script failed')), true, 'Safari 措辭要命中');
assert.equal(
    isStaleChunkError(new TypeError('error loading dynamically imported module')),
    true,
    'Firefox 措辭要命中'
);

// ---- 一般網路／程式錯誤不能誤判（誤判會造成不必要的整頁重載）----
assert.equal(isStaleChunkError(new TypeError('Failed to fetch')), false, '普通 fetch 失敗不是過期 chunk');
assert.equal(isStaleChunkError(new DOMException('...', 'AbortError')), false, 'AbortError 不是過期 chunk');
assert.equal(isStaleChunkError(new Error('Cannot read properties of undefined')), false, '一般程式錯誤不是');
assert.equal(isStaleChunkError(null), false, 'null 不能噴例外');
assert.equal(isStaleChunkError('Failed to fetch dynamically imported module: x'), true, '純字串訊息也要命中');

// ---- recoverFromStaleChunk：第一次 → 重載一次並寫守衛 ----
{
    const g = installGlobals();
    const outcome = recoverFromStaleChunk(
        new TypeError('Failed to fetch dynamically imported module: https://x/assets/exports-1.js')
    );
    assert.equal(outcome, 'reloaded', '第一次要重載');
    assert.equal(g.reloads, 1, '只重載一次');
    assert.ok(g.store.get('staleChunkReloadAt'), '要寫入守衛時間戳');

    // 守衛視窗內再失敗 → blocked（呼叫端改顯示「請手動重新整理」），不能無限迴圈
    const again = recoverFromStaleChunk(
        new TypeError('Failed to fetch dynamically imported module: https://x/assets/exports-1.js')
    );
    assert.equal(again, 'blocked', '守衛視窗內要擋下來');
    assert.equal(g.reloads, 1, 'blocked 時不能再次重載');
}

// ---- 守衛過期後要允許再救一次（使用者隔一陣子才又踩到）----
{
    const g = installGlobals();
    g.store.set('staleChunkReloadAt', String(Date.now() - 60_000));
    const outcome = recoverFromStaleChunk(new TypeError('Importing a module script failed'));
    assert.equal(outcome, 'reloaded', '守衛過期後允許再重載');
    assert.equal(g.reloads, 1);
}

// ---- 非過期 chunk 錯誤：不重載、不寫守衛 ----
{
    const g = installGlobals();
    const outcome = recoverFromStaleChunk(new TypeError('Failed to fetch'));
    assert.equal(outcome, 'not-stale', '不相關錯誤要回 not-stale 給呼叫端照舊處理');
    assert.equal(g.reloads, 0, '不相關錯誤不能重載');
    assert.equal(g.store.get('staleChunkReloadAt'), undefined, '不相關錯誤不能寫守衛');
}

// ---- 隱私模式（sessionStorage 拋例外）：退而求其次仍要重載 ----
{
    let reloads = 0;
    globalThis.sessionStorage = {
        getItem: () => {
            throw new DOMException('denied', 'SecurityError');
        },
        setItem: () => {
            throw new DOMException('denied', 'SecurityError');
        },
        removeItem: () => {},
    };
    globalThis.window = {
        location: {
            reload() {
                reloads += 1;
            },
        },
    };
    const outcome = recoverFromStaleChunk(
        new TypeError('error loading dynamically imported module')
    );
    assert.equal(outcome, 'reloaded', 'sessionStorage 不可用時仍要嘗試重載一次');
    assert.equal(reloads, 1);
}

console.log('stale chunk recovery tests passed');
