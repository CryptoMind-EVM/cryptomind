// 訪客首頁 hero（2026-09-27 上市準備 PR-2）：只給未登入訪客、送出第一題就收起。
// 按鈕走 click-delegator 的 data-click（prod CSP 擋 inline handler）；翻譯字串一律跳脫。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;

const moduleUrl = await loadModuleUrl('web/js/guest-home.js');
const { guestWelcomeHtml, guestChipItems, isUserMessageNode } = await import(moduleUrl);

// ---- 範例問題（2026-10-05）：中文一個市場一題（加密、美股、台股）；其他語言維持 BTC、ETH vs SOL、NVDA ----
const keys = (lang, opts) => guestChipItems(lang, opts).map((c) => c.prompt);
assert.deepEqual(keys('en'), ['chat.examplePromptBtc', 'chat.examplePromptEthSol', 'chat.examplePromptNvda']);
assert.deepEqual(keys('ru'), keys('en'));
assert.deepEqual(keys('zh-TW'), ['chat.examplePromptBtc', 'chat.examplePromptNvda', 'chat.examplePromptTsmc']);
assert.deepEqual(keys('zh-CN'), keys('zh-TW'));
// 加密貨幣分頁關閉（Play 版、CRYPTO_TAB_ENABLED=false）：不放加密貨幣的問題，其餘市場照放
for (const lang of ['en', 'ru', 'zh-TW']) {
    assert.deepEqual(keys(lang, { crypto: false }), ['chat.examplePromptNvda', 'chat.examplePromptTsmc'], lang);
}

// ---- 標記 ----
const t = (key) => (key === 'guestHome.title' ? '<img src=x onerror=alert(1)>' : `[${key}]`);
const html = guestWelcomeHtml(t, 'en');
assert.ok(html.includes('id="guest-home"'));
assert.ok(!html.includes('welcome-title'), '訪客第一眼的標題不延遲淡入（語言切換重畫認 #guest-home）');
assert.ok(html.includes('data-click="focusChatInput"'), 'Ask the AI');
assert.ok(/data-click="switchTab" data-click-arg="sample"/.test(html), 'See a sample portfolio');
assert.ok(html.includes('data-click="openLoginModal"'), 'Sign in');
assert.ok(html.includes('data-click-arg="chat.examplePromptNvda"'));
assert.ok(html.includes('data-click-arg="chat.examplePromptBtc"'));
assert.ok(!guestWelcomeHtml(t, 'en', { crypto: false }).includes('chat.examplePromptBtc'), '旗標關閉：不放 BTC 範例問題');
// 範例問題在功能卡之前：手機首屏要看得到可以直接點的問題（實測：放在功能卡後面時 y=771，被擠到首屏之外）
assert.ok(
    html.indexOf('guestHome.tryAsking') > html.indexOf('data-click="openLoginModal"') &&
        html.indexOf('guestHome.tryAsking') < html.indexOf('guestHome.featureBriefTitle'),
    '順序：三個入口 → 範例問題 → 功能卡'
);
assert.ok(!html.includes('<img src=x'), '翻譯字串要跳脫');
assert.ok(html.includes('&lt;img src=x'), '跳脫後照樣顯示');
for (const k of [
    'guestHome.subtitle',
    'guestHome.featureBriefTitle',
    'guestHome.featureLedgerTitle',
    'guestHome.featureCallsTitle',
    'guestHome.ctaAsk',
    'guestHome.ctaSample',
    'guestHome.ctaSignIn',
]) {
    assert.ok(html.includes(`[${k}]`), `hero 少了 ${k}`);
}

// ---- 收起：只看使用者訊息列 ----
const node = (...cls) => ({ nodeType: 1, classList: { contains: (c) => cls.includes(c) } });
assert.equal(isUserMessageNode(node('chat-row', 'chat-row-user')), true);
assert.equal(isUserMessageNode(node('chat-row', 'chat-row-ai')), false);
assert.equal(isUserMessageNode({ nodeType: 3 }), false, '文字節點');

// ---- 捲動：hero 比一屏高，chat-state 的貼底錨定會把訪客拉到最底、看不到標題 ----
const { renderGuestWelcome } = await import(moduleUrl);
let observerCb = null;
globalThis.MutationObserver = class {
    constructor(cb) { observerCb = cb; }
    observe() {}
    disconnect() {}
};
let released = 0;
let resets = 0;
const sticks = [];
window.releaseChatStickToBottom = () => { released += 1; };
window.resetChatStickToBottom = () => { resets += 1; };
window.stickChatToBottom = (force) => { sticks.push(force); };
let heroRemoved = false;
globalThis.document = {
    getElementById: (id) => (id === 'guest-home' && !heroRemoved ? { remove() { heroRemoved = true; } } : null),
};
const container = { innerHTML: '', scrollTop: 999 };
renderGuestWelcome(container);
assert.equal(released, 1, 'hero 畫出來要先釋放貼底');
assert.equal(container.scrollTop, 0, '訪客第一眼要看到標題（捲回頂端）');
assert.equal(resets, 0, '還沒送出前不能恢復貼底');

// 送出第一題：收起 hero，並恢復貼底讓回答跟著捲
observerCb([{ addedNodes: [node('chat-row', 'chat-row-user')] }]);
assert.equal(heroRemoved, true, '送出第一題就收起 hero');
assert.equal(resets, 1, '收起 hero 後恢復貼底');
assert.deepEqual(sticks, [true], '收起 hero 後立刻貼底一次');

console.log('guest home tests passed');
