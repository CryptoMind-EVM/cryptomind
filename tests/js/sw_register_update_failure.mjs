// sw-register.js：每次開頁都 reg.update() 問有沒有新 SW。update() 回 Promise，sw.js 暫時抓不到
// （部署換容器的空窗 404、離線、VPN）會 reject "Failed to update a ServiceWorker … Not found"。
// 以前只包 try/catch，接不到非同步拒絕 → 掉進全域 unhandledrejection → 使用者看到紅色錯誤 toast
// （2026-10-06 部署後實際發生，DANNY 手機截圖）。這裡用 node 實跑註冊腳本，要求「完全沒有未處理的拒絕」。
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const src = await readFile(process.env.SW_SRC || 'web/public/sw-register.js', 'utf8');
const rejections = [];
process.on('unhandledRejection', (reason) => rejections.push(reason));

// 同一個 update() 的兩種實際錯誤文字（DANNY 手機 10:00 與 10:05 各一次）：
// 部署空窗 sw.js 404；以及 App 內建瀏覽器（Telegram WebView）registration 狀態失效的 InvalidStateError
const NOT_FOUND = "Failed to update a ServiceWorker for scope ('https://getcryptomind.com/') with script ('https://getcryptomind.com/sw.js'): Not found";
const INVALID_STATE = "Failed to update a ServiceWorker for scope ('https://getcryptomind.com/') with script ('Unknown'): The object is in an invalid state.";

function run({ updateRejects, registerRejects, message = NOT_FOUND }) {
    const handlers = {};
    const sw = {
        controller: null,
        register: () => (registerRejects ? Promise.reject(new TypeError(message)) : Promise.resolve({ scope: '/' })),
        ready: Promise.resolve({ update: () => (updateRejects ? Promise.reject(new TypeError(message)) : Promise.resolve()) }),
        addEventListener() {},
    };
    const sandbox = {
        navigator: { serviceWorker: sw },
        location: { protocol: 'https:', hostname: 'getcryptomind.com' },
        performance: { now: () => 99999 },
        console: { debug() {}, error() {}, log() {} },
    };
    sandbox.window = sandbox;
    sandbox.addEventListener = (name, fn) => { handlers[name] = fn; };
    vm.runInNewContext(src, sandbox);
    assert.equal(typeof handlers.load, 'function', 'sw-register 要在 load 時註冊');
    handlers.load();
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 60));

// 1. update() 被拒（sw.js 404／registration 失效）——不可有未處理的拒絕
for (const message of [NOT_FOUND, INVALID_STATE]) {
    run({ updateRejects: true, registerRejects: false, message });
    await tick();
    assert.deepEqual(rejections.map((r) => r && r.message), [], 'update() 失敗不該變成 unhandledrejection：' + message);
}

// 2. register() 被拒——本來就吞掉，維持
run({ updateRejects: false, registerRejects: true });
await tick();
assert.equal(rejections.length, 0);

// 3. 正常情況不受影響
run({ updateRejects: false, registerRejects: false });
await tick();
assert.equal(rejections.length, 0);

console.log('sw_register_update_failure: ok');
