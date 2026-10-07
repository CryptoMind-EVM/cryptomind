// 貼底放掉之後，捲動事件不能自己把貼底接回去（2026-09-27 正式站：訪客首頁一載入被拉到最底）。
// 時序：hero 剛放進去、圖示與字型還沒撐開高度 → 捲回頂端時內容還矮、判定「在底部」→
// 舊的捲動監聽把貼底接回來 → 內容長高時錨定把畫面拉到底，訪客看不到標題。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
window.addEventListener = () => {};
window.AppStore = { set() {}, get() {} };

const listeners = {};
const el = {
    dataset: {},
    scrollTop: 0,
    scrollHeight: 700,
    clientHeight: 776,
    addEventListener(type, fn) {
        listeners[type] = fn;
    },
};
globalThis.document = {
    readyState: 'complete',
    getElementById: (id) => (id === 'chat-messages' ? el : null),
    addEventListener: () => {},
    querySelector: () => null,
    querySelectorAll: () => [],
};

const { releaseChatStickToBottom, resetChatStickToBottom, stickChatToBottom } = await import(
    await loadModuleUrl('web/js/chat-state.js')
);
assert.equal(typeof listeners.scroll, 'function', '載入時要掛上捲動監聽');

// 放掉貼底，捲回頂端；此時內容還矮（700 < 776），位置判定「在底部」
releaseChatStickToBottom();
el.scrollTop = 0;
listeners.scroll();

// 圖示、字型載入後內容長高，錨定觸發
el.scrollHeight = 1138;
stickChatToBottom();
assert.equal(el.scrollTop, 0, '放掉貼底後，捲動事件不能把貼底接回來');

// 送出訊息：接回貼底
resetChatStickToBottom();
stickChatToBottom();
assert.equal(el.scrollTop, 1138, '送出訊息後恢復貼底');

// 接回之後，一般聊天的行為不變：使用者往上捲就交還控制權，捲回底部附近再恢復
el.scrollTop = 100;
listeners.scroll();
el.scrollHeight = 1500;
stickChatToBottom();
assert.equal(el.scrollTop, 100, '使用者往上捲時不硬拉回底部');
el.scrollTop = 1500 - 776;
listeners.scroll();
el.scrollHeight = 1600;
stickChatToBottom();
assert.equal(el.scrollTop, 1600, '捲回底部附近就恢復貼底');

console.log('chat stick release tests passed');
