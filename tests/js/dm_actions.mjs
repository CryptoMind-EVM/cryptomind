// 私訊訊息選單的規則（2026-09-29，PR 2）。
//
// 以前每則氣泡旁常駐收回／垃圾桶 hover 按鈕：手機沒有 hover 等於藏起來，桌機一排按鈕又亂。
// 改成單一入口（手機長按、桌機「⋯」／右鍵）開同一個選單，項目由 messageMenuItems 決定。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
globalThis.document = { addEventListener: () => {}, getElementById: () => null };

const {
    messageMenuItems,
    copyText,
    placeMenu,
    quoteHtml,
    replyBarText,
    groupReactions,
    reactionsHtml,
    messageToolsHtml,
    jumpToMessage,
} = await import(
    await loadModuleUrl(new URL('../../web/js/dm-message-actions.js', import.meta.url).pathname)
);

const now = Date.parse('2026-09-29T12:00:00Z');
const hoursAgo = (h) => new Date(now - h * 3600 * 1000).toISOString();
const keys = (items) => items.map((i) => (i === 'sep' ? '|' : i.key)).join(',');
const ALL = new Set(['reply', 'copy', 'report', 'recall', 'hide']);

// ── 1. 自己的訊息：24 小時內可收回，之後不行 ─────────────────────────────
assert.equal(
    keys(messageMenuItems({ isMine: true, messageType: 'text', createdAt: hoursAgo(23), now, features: ALL })),
    'reply,copy,|,recall,hide'
);
assert.equal(
    keys(messageMenuItems({ isMine: true, messageType: 'text', createdAt: hoursAgo(25), now, features: ALL })),
    'reply,copy,|,hide'
);

// ── 2. 對方的訊息：可以檢舉，不能收回 ──────────────────────────────────────
assert.equal(
    keys(messageMenuItems({ isMine: false, messageType: 'text', createdAt: hoursAgo(1), now, features: ALL })),
    'reply,copy,report,|,hide'
);

// ── 3. 已收回：只剩為我刪除（沒有內容可複製／回覆） ─────────────────────────
assert.equal(
    keys(messageMenuItems({ isMine: true, messageType: 'recalled', createdAt: hoursAgo(1), now, features: ALL })),
    'hide'
);

// ── 4. 還沒上線的功能不出現（PR 2 只有 copy/recall/hide） ───────────────────
const PR2 = new Set(['copy', 'recall', 'hide']);
assert.equal(
    keys(messageMenuItems({ isMine: false, messageType: 'greeting', createdAt: hoursAgo(1), now, features: PR2 })),
    'copy,|,hide'
);

// ── 4b. 問 AI（聊天室 AI 助理開著才放進 features）：自己或對方的都有，收回的沒有 ──
const WITH_AI = new Set([...ALL, 'askAi']);
assert.equal(
    keys(messageMenuItems({ isMine: true, messageType: 'text', createdAt: hoursAgo(1), now, features: WITH_AI })),
    'reply,copy,askAi,|,recall,hide'
);
assert.equal(
    keys(messageMenuItems({ isMine: false, messageType: 'text', createdAt: hoursAgo(1), now, features: WITH_AI })),
    'reply,copy,askAi,report,|,hide'
);
assert.equal(
    keys(messageMenuItems({ isMine: false, messageType: 'recalled', createdAt: hoursAgo(1), now, features: WITH_AI })),
    'hide'
);

// ── 5. 危險動作標紅 ───────────────────────────────────────────────────────
const mine = messageMenuItems({ isMine: true, messageType: 'text', createdAt: hoursAgo(1), now, features: ALL });
assert.deepEqual(
    mine.filter((i) => i !== 'sep' && i.danger).map((i) => i.key),
    ['recall', 'hide']
);

// ── 6. 複製：clipboard API 被擋（Telegram／Base iframe）時退回 execCommand ──
let copied = null;
// Node 22 的 navigator 是唯讀 getter，要用 defineProperty 蓋掉
const setNavigator = (value) =>
    Object.defineProperty(globalThis, 'navigator', { value, configurable: true, writable: true });
setNavigator({ clipboard: { writeText: async () => Promise.reject(new Error('blocked')) } });
const appended = [];
globalThis.document = {
    addEventListener: () => {},
    getElementById: () => null,
    createElement: () => ({ style: {}, select() {}, setAttribute() {}, remove() {} }),
    body: { appendChild: (el) => appended.push(el) },
    execCommand: (cmd) => {
        copied = cmd;
        return true;
    },
};
assert.equal(await copyText('hello'), true);
assert.equal(copied, 'copy');
globalThis.document.execCommand = () => false;
assert.equal(await copyText('hello'), false, '兩種都失敗要回 false，讓畫面顯示「無法複製」');
setNavigator({ clipboard: { writeText: async () => {} } });
assert.equal(await copyText('hello'), true);

// ── 7. 選單位置：下方放得下放下方、否則上方、都不行貼底；靠自己那側對齊 ───────
const vp = { width: 390, height: 844 };
const size = { width: 180, height: 200 };
let pos = placeMenu({ top: 100, bottom: 140, left: 200, right: 370 }, size, vp, true);
assert.deepEqual(pos, { top: 148, left: 190, mode: 'below' });
pos = placeMenu({ top: 700, bottom: 740, left: 20, right: 200 }, size, vp, false);
assert.deepEqual(pos, { top: 492, left: 20, mode: 'above' });
pos = placeMenu({ top: 150, bottom: 700, left: 20, right: 300 }, size, vp, false);
assert.equal(pos.mode, 'sheet');

// ── 8. 引用區塊：原文一律跳脫（暱稱、內容都是使用者輸入）；已收回顯示「訊息已收回」 ──
const evil = quoteHtml(
    { id: 9, from_display_name: '<img src=x onerror=alert(1)>', snippet: '"><script>x</script>', recalled: false },
    false
);
assert.ok(!evil.includes('<img') && !evil.includes('<script'), evil);
assert.ok(evil.includes('&lt;img src=x onerror=alert(1)&gt;'));
assert.ok(evil.includes('data-dm-jump="9"'));
const idInjection = quoteHtml({ id: '9" onclick="x', from_display_name: 'a', snippet: 'b', recalled: false }, true);
assert.ok(idInjection.includes('data-dm-jump="NaN"'), 'id 只收數字，不讓屬性被撐開');
const recalledQuote = quoteHtml({ id: 3, from_display_name: 'A', snippet: '', recalled: true }, false);
assert.ok(recalledQuote.includes('Message recalled'), '已收回顯示「訊息已收回」（這裡沒 I18n，走 fallback）');
assert.equal(quoteHtml(null, false), '', '不是回覆就沒有引用區塊');

// ── 9. 草稿列：「回覆 某某」＋前 50 字 ─────────────────────────────────────
const long = 'x'.repeat(80);
const bar = replyBarText({ text: long }, 'Bob');
assert.equal(bar.snippet, 'x'.repeat(50) + '…');
assert.equal(replyBarText({ text: 'short' }, 'Bob').snippet, 'short');

// ── 10. 表情：依 key 分組計數、標出自己按的；清單外的 key 丟掉 ───────────────
const groups = groupReactions(
    [
        { user_id: 'me', reaction: 'love' },
        { user_id: 'you', reaction: 'love' },
        { user_id: 'you', reaction: 'rocket' },
        { user_id: 'x', reaction: '<img onerror=1>' },
    ],
    'me'
);
assert.deepEqual(groups, [
    { key: 'love', count: 2, mine: true },
    { key: 'rocket', count: 1, mine: false },
]);
const pills = reactionsHtml([{ user_id: 'me', reaction: 'haha' }], 'me', false);
assert.ok(pills.includes('data-dm-reaction="haha"') && pills.includes('aria-pressed="true"'));
assert.ok(pills.includes('href="#dmr-haha"'));
assert.equal(reactionsHtml([], 'me', true), '', '沒人按就不畫膠囊');
// 膠囊掛在氣泡下緣外（2026-10-01 DANNY：放在氣泡裡把框撐變形）：絕對定位不佔氣泡的版面，
// 自己的靠右、對方的靠左；底色自己帶（不再用氣泡裡的半透明底，露在外面那半會看不見）
const onMine = reactionsHtml([{ user_id: 'you', reaction: 'ok' }], 'me', true);
const onTheirs = reactionsHtml([{ user_id: 'you', reaction: 'ok' }], 'me', false);
assert.match(onMine, /class="msg-reactions absolute top-full [^"]*\bright-2\b/);
assert.match(onTheirs, /class="msg-reactions absolute top-full [^"]*\bleft-2\b/);
assert.ok(onMine.includes('bg-surface') && !onMine.includes('bg-background/'), onMine);

// ── 11. hover 工具列：一般訊息有表情／回覆／⋯；已收回只剩 ⋯ ────────────────
const tools = messageToolsHtml({ isMine: true });
assert.ok(tools.includes('data-dm-react-open') && tools.includes('data-dm-reply') && tools.includes('data-dm-more'));
const recalledTools = messageToolsHtml({ isMine: true, recalled: true });
assert.ok(!recalledTools.includes('data-dm-react-open') && !recalledTools.includes('data-dm-reply'));
assert.ok(recalledTools.includes('data-dm-more'));

// ── 11b. 工具列接在時間的位置（零寬錨點往時間那側展開、時間淡出），不蓋到訊息；滑鼠停一下才出現 ──
// DANNY 2026-10-01：原本浮在氣泡上緣、一移上去就出現，蓋到上下相鄰的訊息
{
    const anchor = (html) => html.match(/^<div class="([^"]*)"/)[1];
    const bar = (html) => html.match(/<div class="(msg-tools [^"]*)"/)[1];
    assert.ok(anchor(tools).includes('w-0'), '錨點零寬，不佔版面');
    assert.ok(bar(tools).includes('right-0') && !bar(tools).includes('left-0'), '自己的訊息在右：工具列往左展開');
    const theirs = messageToolsHtml({ isMine: false });
    assert.ok(bar(theirs).includes('left-0') && !bar(theirs).includes('right-0'), '對方的訊息在左：工具列往右展開');
    assert.ok(!tools.includes('-top-'), '不再浮在氣泡上緣');
    assert.ok(bar(tools).includes('group-hover:delay-200'), '停一下才出現，滑過去不閃');
}

// ── 12. 點引用跳轉：原訊息不在畫面上就往前翻（最多 5 頁）；途中換對話就停；找不到才提示 ──
{
    const toastsSeen = [];
    window.showToast = (msg) => toastsSeen.push(msg);
    globalThis.setTimeout = () => 0;
    const flashes = [];
    const makeRow = () => ({
        getBoundingClientRect: () => ({ top: 500 }),
        querySelector: () => ({ classList: { add: (...c) => flashes.push(c), remove: () => {} } }),
    });
    const rows = new Map([[100, makeRow()]]);
    const container = {
        scrollTop: 0,
        clientHeight: 600,
        getBoundingClientRect: () => ({ top: 0 }),
        querySelector: (sel) => rows.get(Number(sel.match(/"(\d+)"/)[1])) || null,
    };
    let conv = 1;
    let loads = 0;
    const config = {
        currentConversation: () => conv,
        loadOlder: async () => {
            loads += 1;
            if (loads === 3) rows.set(10, makeRow()); // 第 3 頁才載到
            return true;
        },
    };
    await jumpToMessage({ container, config }, 10);
    assert.equal(loads, 3, '翻到找到為止');
    assert.equal(flashes.length, 1, '找到要閃一下');
    assert.equal(container.scrollTop, 300, '捲到原訊息（容器高度 1/3 處）');
    assert.equal(toastsSeen.length, 0);

    // 翻滿 5 頁還是沒有 → 提示太久遠
    loads = 0;
    await jumpToMessage({ container, config }, 1);
    assert.equal(loads, 5);
    assert.equal(toastsSeen.length, 1);

    // 沒有更多（loadOlder 回 false）→ 立刻停、提示
    loads = 0;
    await jumpToMessage({ container, config: { ...config, loadOlder: async () => (loads++, false) } }, 2);
    assert.equal(loads, 1);
    assert.equal(toastsSeen.length, 2);

    // 翻到一半換了對話 → 停手、不提示、不捲動
    loads = 0;
    container.scrollTop = 0;
    const switching = {
        currentConversation: () => conv,
        loadOlder: async () => {
            loads += 1;
            conv = 2;
            return true;
        },
    };
    await jumpToMessage({ container, config: switching }, 3);
    assert.equal(loads, 1, '換對話後不再往前翻');
    assert.equal(toastsSeen.length, 2, '換對話了不提示');
    assert.equal(container.scrollTop, 0);
}

console.log('dm_actions: ok');
