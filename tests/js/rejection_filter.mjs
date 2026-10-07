// rejection-filter.js：全域 unhandledrejection 的分類（2026-10-06）。
// 要守的是：DANNY 手機上看到的兩種 ServiceWorker 英文紅框要被吃掉、瀏覽器 API 的 DOMException 不跳 toast、
// 部署殘留的 stale chunk 仍交給整頁重載（不能被當成網路錯誤吃掉）、其餘維持原本行為。
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

// web/js 的檔案是 .js 但沒有 package type=module：複製成 .mjs 再 import（同 tests/js 其他測試的做法精神）
const dir = mkdtempSync(join(tmpdir(), 'rejfilter-'));
writeFileSync(join(dir, 'stale-chunk-recovery.mjs'), readFileSync('web/js/stale-chunk-recovery.js', 'utf8'));
writeFileSync(
    join(dir, 'rejection-filter.mjs'),
    readFileSync('web/js/rejection-filter.js', 'utf8').replace("'./stale-chunk-recovery.js'", "'./stale-chunk-recovery.mjs'")
);
const { classifyRejection } = await import(pathToFileURL(join(dir, 'rejection-filter.mjs')).href);

const SW_404 =
    "Failed to update a ServiceWorker for scope ('https://getcryptomind.com/') with script ('https://getcryptomind.com/sw.js'): Not found";
const SW_INVALID =
    "Failed to update a ServiceWorker for scope ('https://getcryptomind.com/') with script ('Unknown'): The object is in an invalid state.";

// 兩張截圖的實際訊息
assert.equal(classifyRejection(new TypeError(SW_404)), 'ignore');
assert.equal(classifyRejection(new TypeError(SW_INVALID)), 'ignore');
assert.equal(classifyRejection(new DOMException(SW_INVALID, 'InvalidStateError')), 'ignore');

// 取消的請求、錢包 SDK 的 Operation aborted
assert.equal(classifyRejection(new DOMException('aborted', 'AbortError')), 'ignore');
assert.equal(classifyRejection({ name: 'AbortError', message: 'x' }), 'ignore');
assert.equal(classifyRejection(new Error('Operation aborted')), 'ignore');

// 瀏覽器 API 的 DOMException：剪貼簿被擋、WebView 擋 storage、容量滿
for (const [msg, name] of [
    ["Failed to execute 'writeText' on 'Clipboard': Write permission denied.", 'NotAllowedError'],
    ["Failed to read the 'localStorage' property from 'Window': Access is denied for this document.", 'SecurityError'],
    ['The quota has been exceeded.', 'QuotaExceededError'],
]) {
    assert.equal(classifyRejection(new DOMException(msg, name)), 'ignore', name);
}

// 部署殘留舊頁的動態 import：必須交給 stale chunk 流程（不是網路錯誤）
for (const msg of [
    'Failed to fetch dynamically imported module: https://x/static/assets/a-1.js',
    'Importing a module script failed.',
    'error loading dynamically imported module',
]) {
    assert.equal(classifyRejection(new TypeError(msg)), 'stale-chunk', msg);
}

// 網路層 fetch 失敗：線上提示「網路不穩」，離線（背景輪詢必失敗）不吵
for (const msg of ['Failed to fetch', 'Load failed', 'NetworkError when attempting to fetch resource.']) {
    assert.equal(classifyRejection(new TypeError(msg), { online: true }), 'network', msg);
    assert.equal(classifyRejection(new TypeError(msg), { online: false }), 'ignore', msg);
}

// 原生程式錯誤：顯示通用訊息，不貼英文原文
assert.equal(classifyRejection(new TypeError("Cannot read properties of undefined (reading 'x')")), 'generic');
assert.equal(classifyRejection(new ReferenceError('foo is not defined')), 'generic');

// 應用層的友善錯誤、錢包 SDK 的訊息、字串：維持原本行為（顯示原文）
assert.equal(classifyRejection(new Error('額度已用完，請明天再來')), 'show');
assert.equal(classifyRejection('some string reason'), 'show');
assert.equal(classifyRejection(undefined), 'show');
assert.equal(classifyRejection({ code: 4001, message: 'User rejected the request.' }), 'show');

console.log('rejection_filter: ok');
