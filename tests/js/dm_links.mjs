// 私訊裡的連結與錢包地址（2026-09-29，PR 5）。
//
// 網址變成可點（點外部網域先跳風險提示，顯示瀏覽器解析出的 hostname——IDN 會是 punycode，
// 仿冒網域一眼看得出來）；EVM 地址變成可點，開詐騙查詢。切段後每段各自跳脫，
// 不在已跳脫的 HTML 上跑正則。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
globalThis.document = { addEventListener: () => {}, getElementById: () => null };

const { tokenizeMessage, renderMessageText, isOwnHost, linkHost, messagePlainText } = await import(
    await loadModuleUrl(new URL('../../web/js/dm-message-actions.js', import.meta.url).pathname)
);

const ADDR = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913';
const kinds = (text) => tokenizeMessage(text).map((t) => `${t.type}:${t.value}`);

// ── 1. 網址：結尾的中英文標點、括號不算進去 ───────────────────────────────
assert.deepEqual(kinds('看 https://example.com/a?b=1。'), ['text:看 ', 'url:https://example.com/a?b=1', 'text:。']);
assert.deepEqual(kinds('(https://x.com/p)'), ['text:(', 'url:https://x.com/p', 'text:)']);
assert.deepEqual(kinds('「https://x.com」好'), ['text:「', 'url:https://x.com', 'text:」好']);
assert.deepEqual(kinds('https://x.com, ok'), ['url:https://x.com', 'text:, ok']);
assert.deepEqual(kinds('no link here'), ['text:no link here']);

// ── 2. 只認 http／https ───────────────────────────────────────────────────
assert.deepEqual(kinds('javascript:alert(1)'), ['text:javascript:alert(1)']);
assert.deepEqual(kinds('ftp://x.com'), ['text:ftp://x.com']);

// ── 3. EVM 地址：剛好 40 個 hex；多一位、少一位都不算；網址裡的地址算網址 ────────
assert.deepEqual(kinds(`轉到 ${ADDR} 謝謝`), ['text:轉到 ', `address:${ADDR}`, 'text: 謝謝']);
assert.deepEqual(kinds(`${ADDR}a`), [`text:${ADDR}a`]);
assert.deepEqual(kinds(ADDR.slice(0, -1)), [`text:${ADDR.slice(0, -1)}`]);
assert.deepEqual(kinds(`https://etherscan.io/address/${ADDR}`), [`url:https://etherscan.io/address/${ADDR}`]);

// ── 4. XSS：原文每段各自跳脫；引號、角括號撐不開 href ─────────────────────────
const evil = renderMessageText('https://x.com/"><img src=x onerror=alert(1)> <script>alert(2)</script>');
assert.ok(!evil.includes('<img') && !evil.includes('<script'), evil);
assert.ok(evil.includes('href="https://x.com/"'), evil);
assert.ok(evil.includes('&lt;script&gt;'), evil);
const plain = renderMessageText('a & b <c>');
assert.equal(plain, 'a &amp; b &lt;c&gt;');

// ── 5. 連結屬性：另開分頁、noopener；地址縮短顯示但保留完整值 ─────────────────
const link = renderMessageText('https://example.com');
assert.ok(link.includes('data-dm-link') && link.includes('target="_blank"'));
assert.ok(link.includes('rel="noopener noreferrer nofollow"'));
const addr = renderMessageText(ADDR);
assert.ok(addr.includes(`data-dm-address="${ADDR}"`), addr);
assert.ok(addr.includes('0x8335…2913'), addr);

// ── 6. hostname：punycode 露出仿冒網域；自家網域不提示 ─────────────────────────
assert.equal(linkHost('https://раураl.com/login'), 'xn--l-7sba6dbr.com', '西里爾字母仿冒 paypal：顯示 punycode');
assert.equal(linkHost('https://Example.COM:8443/x'), 'example.com');
globalThis.location = { hostname: 'localhost' };
assert.equal(isOwnHost('getcryptomind.com'), true);
assert.equal(isOwnHost('app.getcryptomind.com'), true);
assert.equal(isOwnHost('localhost'), true);
assert.equal(isOwnHost('getcryptomind.com.evil.io'), false);
assert.equal(isOwnHost('evilgetcryptomind.com'), false);
assert.equal(isOwnHost('getcryptomind.com.'), true, '結尾多一個點還是自家網域');
assert.equal(isOwnHost('evil.io.'), false);

// ── 7. 複製／回覆用的原文：地址按鈕換回完整地址 ───────────────────────────────
function fakeNode(children) {
    return {
        children,
        cloneNode() {
            return fakeNode(this.children.map((c) => ({ ...c })));
        },
        querySelectorAll(sel) {
            assert.equal(sel, '[data-dm-address]');
            return this.children
                .filter((c) => c.address)
                .map((c) => ({
                    dataset: { dmAddress: c.address },
                    replaceWith: (text) => {
                        c.text = text;
                        delete c.address;
                    },
                }));
        },
        get textContent() {
            return this.children.map((c) => c.text).join('');
        },
    };
}
const bubbleText = fakeNode([
    { text: '轉到 ' },
    { text: '0x8335…2913', address: ADDR },
    { text: ' https://x.com/?a=1&b=2' },
]);
assert.equal(messagePlainText(bubbleText), `轉到 ${ADDR} https://x.com/?a=1&b=2`);
assert.equal(bubbleText.textContent, '轉到 0x8335…2913 https://x.com/?a=1&b=2', '不能改到畫面上那份');
assert.equal(messagePlainText(null), '');

console.log('dm_links: ok');
