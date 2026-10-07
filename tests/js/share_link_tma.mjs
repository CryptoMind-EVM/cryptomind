// Telegram 內直接開啟分享連結（2026-10-05 任務 C）：startapp 參數的編解碼、連結組裝、落地預填。
// 只讀 start param、不碰登入；參數限 A-Za-z0-9_- 且約 64 字，編不下就退回網頁連結。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const input = {
    value: '',
    dataset: {},
    disabled: false,
    closest: () => ({ prepend() {} }),
    dispatchEvent() {},
    focus() {},
    addEventListener() {},
};
let replaced = null;

globalThis.window = globalThis;
globalThis.Event = class { constructor(type) { this.type = type; } };
globalThis.I18n = { t: (key) => key };
// 在 Telegram 裡開：沒有 ?ask=，問題在 WebApp 的 start_param
globalThis.location = { origin: 'https://app.example', pathname: '/', search: '', hash: '#tgWebAppData=x&tgWebAppStartParam=ignored' };
globalThis.history = { state: null, replaceState: (_s, _t, url) => { replaced = url; } };
globalThis.document = {
    getElementById: (id) => (id === 'user-input' ? input : null),
    createElement: () => ({ remove() {} }),
};

const encoded = Buffer.from('BTC 現在怎麼看', 'utf8').toString('base64url');
globalThis.Telegram = { WebApp: { initDataUnsafe: { start_param: 'ask_' + encoded } } };

const mod = await import(await loadModuleUrl(new URL('../../web/js/share-link.js', import.meta.url).pathname));

// ── 1. 編碼：ask_ ＋ base64url(UTF-8)；只含 A-Za-z0-9_-、總長 ≤ 64 ───────────
const SAFE = /^[A-Za-z0-9_-]+$/;
const p = mod.encodeStartParam('BTC 現在怎麼看');
assert.equal(p, 'ask_' + encoded);
assert.match(p, SAFE);
assert.ok(p.length <= 64);
assert.equal(mod.encodeStartParam('BTC?'), 'ask_' + Buffer.from('BTC?').toString('base64url'), '特殊字元走 base64url，不會漏進網址');
assert.equal(mod.encodeStartParam('這是一個很長很長很長的問題這是一個很長很長很長的問題'), null, '編不下（>64）就不用 startapp，退回網頁連結');
assert.equal(mod.encodeStartParam('   '), null, '空問題');
assert.equal(mod.encodeStartParam('a'.repeat(45)).length, 64, '剛好 64 字可以');
assert.equal(mod.encodeStartParam('a'.repeat(46)), null, '多一個字就編不下');

// ── 2. 解碼：只收 ask_ 開頭；壞掉的一律當作沒有 ─────────────────────────────
assert.equal(mod.decodeStartParam(p), 'BTC 現在怎麼看');
assert.equal(mod.decodeStartParam('ask_%%%'), '');
assert.equal(mod.decodeStartParam('other_' + encoded), '', '不是 ask_ 的 start param 不管（別的功能用）');
assert.equal(mod.decodeStartParam(''), '');
assert.equal(mod.decodeStartParam(null), '');
assert.equal(
    mod.decodeStartParam('ask_' + Buffer.from('<script>x</script>\n\u0000hi').toString('base64url')),
    '<script>x</script> hi',
    '解碼後一樣過 cleanAsk（控制字元、空白）'
);
assert.equal(
    Array.from(mod.decodeStartParam('ask_' + Buffer.from('x'.repeat(40)).toString('base64url'))).length,
    40
);

// ── 3. 連結：有設定且在 Telegram 裡才用 t.me；否則網頁連結 ───────────────────
const cfg = { bot_username: 'getcryptomind_bot', short_name: 'cmind' };
assert.equal(
    mod.buildAskLink('BTC 現在怎麼看', { telegram: cfg }),
    `https://t.me/getcryptomind_bot/cmind?startapp=${p}`
);
assert.ok(mod.buildAskLink('這是一個很長很長很長的問題這是一個很長很長很長的問題', { telegram: cfg }).startsWith('https://app.example/?ask='), '太長退回網頁連結');
assert.ok(mod.buildAskLink('BTC', {}).startsWith('https://app.example/?ask='), '沒設定＝維持現在的網頁連結');
assert.ok(mod.buildAskLink('BTC').startsWith('https://app.example/?ask='));
assert.equal(mod.buildAskLink('   ', { telegram: cfg }), 'https://app.example/', '空問題回首頁');

// ── 4. 落地：Telegram 開啟時從 start_param 預填，跟 ?ask= 同一套預填 ─────────
await new Promise((resolve) => setTimeout(resolve, 450));
assert.equal(input.value, 'BTC 現在怎麼看', 'start_param 的問題要預填進輸入框');
assert.equal(replaced, null, '沒有 ?ask= 要清，網址不動（Telegram 的 hash 是它自己的）');

// ── 5. 沒有 SDK 物件時，從網址 hash 的 tgWebAppStartParam 讀（只讀）──────────
{
    const saved = globalThis.Telegram;
    delete globalThis.Telegram;
    globalThis.location.hash = `#tgWebAppData=x&tgWebAppStartParam=${p}&tgWebAppVersion=8`;
    assert.equal(mod.readStartParam(), p);
    globalThis.location.hash = '';
    assert.equal(mod.readStartParam(), '');
    globalThis.Telegram = saved;
    assert.equal(mod.readStartParam(), p, '有 SDK 就用 SDK');
}

// ── 6. shareAsk：只有在 Telegram（tma）裡且後端有給名稱才用 t.me 直連 ──────────
{
    const copied = [];
    Object.defineProperty(globalThis, 'navigator', { value: { clipboard: { writeText: async (text) => { copied.push(text); } } }, configurable: true });
    globalThis.showToast = () => {};
    globalThis.AppAPI = { getAppConfig: async () => ({ telegram_miniapp: cfg }) };

    globalThis.CMPlatform = { get: () => 'tma' };
    await mod.shareAsk('BTC 現在怎麼看');
    assert.equal(copied.at(-1), `https://t.me/getcryptomind_bot/cmind?startapp=${p}`, 'Telegram 裡分享 → t.me 直連');

    globalThis.CMPlatform = { get: () => 'web' };
    await mod.shareAsk('BTC 現在怎麼看');
    assert.ok(copied.at(-1).startsWith('https://app.example/?ask='), '一般網頁分享 → 網頁連結（貼到 X、LINE 才打得開）');

    globalThis.CMPlatform = { get: () => 'tma' };
    globalThis.AppAPI = { getAppConfig: async () => ({ telegram_miniapp: null }) };
    await mod.shareAsk('BTC 現在怎麼看');
    assert.ok(copied.at(-1).startsWith('https://app.example/?ask='), '後端沒設名稱 → 維持網頁連結');

    globalThis.AppAPI = { getAppConfig: async () => { throw new Error('offline'); } };
    await mod.shareAsk('BTC 現在怎麼看');
    assert.ok(copied.at(-1).startsWith('https://app.example/?ask='), '取不到設定 → 維持網頁連結');
}

console.error('share_link_tma: ok');
process.exit(0);
