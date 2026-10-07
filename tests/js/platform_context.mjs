// platform-context.js：query → localStorage → html.play；CMPlatform.get() 的優先序。
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const src = await readFile(new URL('../../web/js/platform-context.js', import.meta.url), 'utf8');

function run({ search, stored, classes = [] }) {
    const cls = new Set(classes);
    const store = new Map(stored ? [['cm:platform', stored]] : []);
    const ctx = {
        window: {},
        document: { documentElement: { classList: { add: (c) => cls.add(c), contains: (c) => cls.has(c) } } },
        localStorage: { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v) },
        URLSearchParams,
    };
    ctx.window.location = { search };
    ctx.window.localStorage = ctx.localStorage;
    vm.createContext(ctx);
    vm.runInContext(src, ctx);
    return { platform: ctx.window.CMPlatform.get(), isPlay: ctx.window.CMPlatform.isPlay(), stored: store.get('cm:platform') };
}

// 1) ?platform=play → 記住、html.play
let r = run({ search: '?platform=play' });
assert.equal(r.platform, 'play'); assert.equal(r.isPlay, true); assert.equal(r.stored, 'play');
// 2) 沒 query 但之前記過 → 還是 play
r = run({ search: '', stored: 'play' });
assert.equal(r.platform, 'play');
// 3) 亂帶 → web，且不覆蓋記錄
r = run({ search: '?platform=ios', stored: null });
assert.equal(r.platform, 'web'); assert.equal(r.stored, undefined);
// 4) tma／baseapp 由各自腳本加 class，優先於 play 記錄
r = run({ search: '', stored: 'play', classes: ['tma'] });
assert.equal(r.platform, 'tma');
r = run({ search: '', stored: 'play', classes: ['miniapp'] });
assert.equal(r.platform, 'baseapp');
console.log('platform_context: ok');
