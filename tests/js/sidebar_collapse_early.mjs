// early-init.js 在第一次繪製前套用側欄收合（免得先寬後窄閃一下）。實跑 IIFE。
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const src = await readFile(new URL('../../web/js/early-init.js', import.meta.url), 'utf8');

function run(stored) {
    const classes = new Set();
    const store = new Map(Object.entries(stored));
    const ctx = {
        localStorage: {
            getItem: (k) => (store.has(k) ? store.get(k) : null),
            setItem: (k, v) => store.set(k, String(v)),
            removeItem: (k) => store.delete(k),
        },
        document: {
            documentElement: { classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) } },
            getElementById: () => null,
            querySelector: () => null,
            querySelectorAll: () => [],
            addEventListener: () => {},
            body: null,
        },
        window: { matchMedia: () => ({ matches: false }), addEventListener: () => {} },
        console,
    };
    ctx.window.localStorage = ctx.localStorage;
    ctx.window.document = ctx.document;
    vm.runInNewContext(src, ctx);
    return classes;
}

assert.ok(run({ sidebarCollapsed: '1' }).has('sidebar-collapsed'), '記住收合：第一次繪製前就套上');
assert.ok(!run({ sidebarCollapsed: '0' }).has('sidebar-collapsed'));
assert.ok(!run({}).has('sidebar-collapsed'), '沒設定＝展開');

console.error('sidebar_collapse_early: ok');
