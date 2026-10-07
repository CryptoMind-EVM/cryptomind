// 設定頁的 TEST_MODE「切換帳號」區塊（#dev-user-switcher）要在進設定頁時依
// test_mode 顯示（2026-09-29）。
//
// 以前只在 auth 初始化時 getElementById 一次，但設定頁是切過去才注入 DOM，
// 那時元素還不存在 → 區塊永遠 hidden。改由每次進設定頁都會跑的 initTestMode 同步。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

function fakeEl(hidden) {
    const cls = new Set(hidden ? ['hidden'] : []);
    return {
        classList: {
            add: (c) => cls.add(c),
            remove: (c) => cls.delete(c),
            toggle: (c, force) => (force ?? !cls.has(c)) ? cls.add(c) : cls.delete(c),
            contains: (c) => cls.has(c),
        },
    };
}

let els = {};
let config = {};
let blocked = false;
globalThis.window = globalThis;
globalThis.document = {
    getElementById: (id) => els[id] || null,
    querySelectorAll: () => [],
};
globalThis.PiEnvironment = { shouldBlockProtectedRequests: () => blocked };
globalThis.AppAPI = {
    getAppConfig: async () => config,
    get: async () => ({ is_test_mode: !!config.test_mode, tier: 'free' }),
};

await import(await loadModuleUrl(new URL('../../web/js/testMode.js', import.meta.url).pathname));

async function run({ testMode, startHidden, block = false }) {
    els = { 'dev-user-switcher': fakeEl(startHidden), 'test-tier-switcher': fakeEl(true) };
    config = { test_mode: testMode };
    blocked = block;
    await window.initTestMode();
    return els['dev-user-switcher'].classList.contains('hidden');
}

assert.equal(await run({ testMode: true, startHidden: true }), false, 'TEST_MODE 開著，切換帳號區塊要顯示');
assert.equal(await run({ testMode: false, startHidden: false }), true, 'TEST_MODE 關著，切換帳號區塊要藏起來');
assert.equal(await run({ testMode: true, startHidden: false, block: true }), true, '受限環境一律藏起來');

console.error('test_mode_dev_switcher: ok');
