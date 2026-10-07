// 私訊聊天室 LINE 式版面（2026-09-29 DANNY：「聊天室佈局很醜」）。
// 看守三件事：
//   1. 分組規則（純函式）：同一人連續訊息貼緊；同一人同一分鐘只在最後一則標時間；
//      換日插日期分隔。
//   2. 兩支氣泡渲染（messages.html 的 MessagesUI、好友頁的 SocialHub）都產出同一種
//      列結構：.msg-row 帶 data-from/data-ts，氣泡的百分比寬度掛在整列寬的 flex 列上
//      （以前掛在縮成內容寬的父層上，「還沒，你呢？」四個字就換行），時間不換行。
//   3. 字數只在快到上限時才顯示。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
function makeEl(id = '') {
    const classes = new Set();
    let text = '';
    const el = {
        id,
        value: '',
        disabled: false,
        innerHTML: '',
        style: {},
        dataset: {},
        classList: {
            add: (...c) => c.forEach((x) => classes.add(x)),
            remove: (...c) => c.forEach((x) => classes.delete(x)),
            contains: (c) => classes.has(c),
            toggle: (c, on) => ((on ?? !classes.has(c)) ? classes.add(c) : classes.delete(c)),
        },
        addEventListener() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
    };
    // createElement('div') + textContent → innerHTML 的轉義（_escapeHtml 的做法）
    Object.defineProperty(el, 'textContent', {
        get: () => text,
        set: (v) => { text = String(v); el.innerHTML = esc(text); },
    });
    return el;
}
let els = {};
const el = (id) => (els[id] ||= makeEl(id));

globalThis.window = globalThis;
globalThis.innerWidth = 1200;
globalThis.addEventListener = () => {};
globalThis.document = {
    documentElement: { lang: 'zh-TW' },
    getElementById: (id) => el(id),
    createElement: () => makeEl(),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
};
globalThis.I18n = { t: (k) => k };
globalThis.AppUtils = { refreshIcons() {} };
globalThis.SecurityUtils = { escapeHTML: (s) => String(s) };
globalThis.console.log = () => {};

const load = async (rel) => import(await loadModuleUrl(new URL(rel, import.meta.url).pathname));
const { messageGroupLayout, formatDateSeparator, MessagesUI } = await load('../../web/js/messages.js');

// ── 1) 分組規則 ─────────────────────────────────────────────
{
    const at = (d, h, m, s = 0) => new Date(2026, 8, d, h, m, s).toISOString();
    const layout = messageGroupLayout([
        { from: 'bob', ts: at(28, 23, 59) }, //   0 前一天
        { from: 'bob', ts: at(29, 11, 17, 5) }, // 1 換日
        { from: 'bob', ts: at(29, 11, 17, 40) }, // 2 同人同分鐘
        { from: 'bob', ts: at(29, 11, 18) }, //    3 同人下一分鐘
        { from: 'me', ts: at(29, 11, 18, 10) }, // 4 換人
        { from: 'me', ts: at(29, 11, 18, 20) }, // 5 同人同分鐘（最後一則）
    ]);
    assert.deepEqual(layout.map((x) => x.newDay), [true, true, false, false, false, false], '第一則與換日要插日期');
    assert.deepEqual(layout.map((x) => x.tight), [false, false, true, true, false, true], '同一人連續才貼緊；換日、換人要拉開');
    assert.deepEqual(
        layout.map((x) => x.showTime),
        [true, false, true, true, false, true],
        '同一人同一分鐘只在最後一則標時間'
    );
    assert.deepEqual(messageGroupLayout([]), []);
}

// ── 2) 日期分隔文字：今天／昨天用 Intl（跟語系走，不寫死中文）───────
{
    const now = new Date(2026, 8, 29, 12, 0);
    assert.equal(formatDateSeparator(new Date(2026, 8, 29, 9, 0).toISOString(), now, 'zh-TW'), '今天');
    assert.equal(formatDateSeparator(new Date(2026, 8, 28, 23, 0).toISOString(), now, 'zh-TW'), '昨天');
    assert.equal(formatDateSeparator(new Date(2026, 8, 27, 9, 0).toISOString(), now, 'en'), 'Sun, September 27');
    assert.ok(formatDateSeparator(new Date(2025, 0, 2).toISOString(), now, 'zh-TW').includes('2025'), '不同年要帶年份');
}

// ── 3) 兩支渲染的列結構 ─────────────────────────────────────
function assertRowShape(label, html, { from }) {
    assert.ok(/class="msg-row[\s"]/.test(html), `${label}：列要有 .msg-row`);
    assert.ok(html.includes(`data-from="${from}"`), `${label}：列要帶 data-from`);
    assert.ok(/data-ts="[^"]+"/.test(html), `${label}：列要帶 data-ts`);
    assert.ok(/class="msg-meta[^"]*whitespace-nowrap/.test(html), `${label}：時間不能換行`);
    assert.ok(/class="msg-bubble[^"]*max-w-\[75%\]/.test(html), `${label}：氣泡寬度上限掛在氣泡本身`);
    assert.ok(!html.includes('max-width: 70%'), `${label}：舊的縮寬父層 max-width 要拿掉`);
}
{
    MessagesUI.currentUserId = 'me';
    const msg = { id: 7, from_user_id: 'me', to_user_id: 'bob', content: '還沒，你呢？', created_at: '2026-09-29T03:17:00Z', is_read: false };
    const mine = MessagesUI.renderMessageBubble(msg, true);
    assertRowShape('MessagesUI 自己', mine, { from: 'me' });
    assert.ok(mine.includes('msg-read-status'), 'Pro 的已讀狀態要有 class，已讀回執才找得到');
    assertRowShape('MessagesUI 對方', MessagesUI.renderMessageBubble({ ...msg, from_user_id: 'bob' }), { from: 'bob' });
    assertRowShape('MessagesUI 已收回', MessagesUI.renderMessageBubble({ ...msg, message_type: 'recalled' }), { from: 'me' });
}
{
    const { SocialHub } = await load('../../web/js/friends.js');
    globalThis.AuthManager = { currentUser: { user_id: 'me' } };
    const msg = { id: 8, from_user_id: 'me', content: '啥', created_at: '2026-09-29T03:17:00Z' };
    assertRowShape('SocialHub 自己', SocialHub.renderMessageBubble(msg), { from: 'me' });
    assertRowShape('SocialHub 對方', SocialHub.renderMessageBubble({ ...msg, from_user_id: 'bob' }), { from: 'bob' });
    assertRowShape('SocialHub 已收回', SocialHub.renderMessageBubble({ ...msg, message_type: 'recalled' }), { from: 'me' });

    // 已讀（2026-09-29 DANNY：「手機會顯示已讀電腦會嗎」——以前桌機完全不畫）：只給 Pro，同 messages.html
    SocialHub.isPremium = false;
    assert.ok(!SocialHub.renderMessageBubble(msg).includes('msg-read-status'), '非 Pro 不顯示已讀');
    SocialHub.isPremium = true;
    const sent = SocialHub.renderMessageBubble({ ...msg, is_read: false });
    assert.ok(sent.includes('msg-read-status" data-read="0"') && sent.includes('messages.deliveredStatus'), 'Pro：未讀＝已送達');
    assert.ok(SocialHub.renderMessageBubble({ ...msg, is_read: true }).includes('data-read="1"'), 'Pro：已讀');
    assert.ok(!SocialHub.renderMessageBubble({ ...msg, from_user_id: 'bob' }).includes('msg-read-status'), '對方的訊息不顯示');
    assert.ok(!SocialHub.renderMessageBubble({ ...msg, message_type: 'recalled' }).includes('msg-read-status'), '收回的不顯示');
    assertRowShape('SocialHub 自己（Pro）', sent, { from: 'me' });
    SocialHub.isPremium = false;

    // 已讀回執：兩支 UI 共用 markRowsRead，只翻還是「已送達」的
    const { markRowsRead } = await load('../../web/js/messages.js');
    const statuses = [{ dataset: { read: '0' }, textContent: '' }, { dataset: { read: '0' }, textContent: '' }];
    let selector = '';
    markRowsRead({ querySelectorAll: (sel) => ((selector = sel), statuses) });
    assert.equal(selector, '.msg-read-status[data-read="0"]');
    assert.deepEqual(statuses.map((s) => [s.dataset.read, s.textContent]), [['1', 'messages.readStatus'], ['1', 'messages.readStatus']]);
    markRowsRead(null); // 容器不在（換頁了）不能炸

    // 狀態只標在自己最新一則（2026-10-01 DANNY：「已送達顯示兩個也很怪」）
    const { regroupMessageRows } = await load('../../web/js/messages.js');
    const status = () => makeEl();
    const shown = [status(), status(), status()];
    regroupMessageRows({ querySelectorAll: (sel) => (sel === '.msg-read-status' ? shown : []) });
    assert.deepEqual(
        shown.map((s) => s.classList.contains('hidden')),
        [true, true, false],
        '已讀／已送達只留最後一則，前面的藏起來'
    );

    // 對話清單收合（2026-10-01 DANNY：「不用的時候可以扁平化放大聊天視窗」）：切 data-list-collapsed、記住，
    // 重新進好友頁套回去；隱私模式 localStorage 會丟錯，不能炸
    const store = {};
    globalThis.localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); } };
    const panes = el('social-content-messages');
    SocialHub.toggleConvList();
    assert.deepEqual([panes.dataset.listCollapsed, store.dmListCollapsed], ['1', '1'], '收起來並記住');
    SocialHub.toggleConvList();
    assert.deepEqual([panes.dataset.listCollapsed, store.dmListCollapsed], ['0', '0'], '再按展開');
    store.dmListCollapsed = '1';
    SocialHub.applyConvListCollapsed();
    assert.equal(panes.dataset.listCollapsed, '1', '重新進來要套回上次的狀態');
    globalThis.localStorage = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
    SocialHub.toggleConvList();
    assert.equal(panes.dataset.listCollapsed, '0', '存不了也要照樣切換');
    SocialHub.applyConvListCollapsed();
    assert.equal(panes.dataset.listCollapsed, '0', '讀不到就維持展開');

    // ── 4) 字數：快到上限才顯示 ──────────────────────────────
    el('social-msg-input').value = 'a'.repeat(10);
    SocialHub.updateCharCount();
    assert.ok(el('social-char-count').classList.contains('hidden'), 'SocialHub：字數遠低於上限要藏起來');
    el('social-msg-input').value = 'a'.repeat(400);
    SocialHub.updateCharCount();
    assert.ok(!el('social-char-count').classList.contains('hidden'), 'SocialHub：到 80% 要顯示字數');
}
console.error('dm_chat_layout: ok');
