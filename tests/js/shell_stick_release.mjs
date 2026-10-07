// ui-shell.js 的底部錨定要尊重 chat-state.js 放掉的貼底（2026-09-27 正式站：訪客首頁被拉到最底）。
// 時序：hero 剛放進去、內容還矮 → 訪客提示條出現、底部佔位變大 → ui-shell 判定 #chat-messages
// 「原本在底部」→ 寫完留白後把它捲到底，訪客第一眼看不到標題。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
window.addEventListener = () => {};
window.AppStore = { set() {}, get() {} };

const chat = { id: 'chat-messages', dataset: {}, scrollTop: 0, scrollHeight: 700, clientHeight: 776, addEventListener() {} };
const legal = { id: 'legal-content', dataset: {}, scrollTop: 0, scrollHeight: 700, clientHeight: 776, addEventListener() {} };
globalThis.document = {
    readyState: 'loading',
    documentElement: { style: { setProperty() {} }, clientHeight: 837 },
    addEventListener: () => {},
    getElementById: (id) => (id === 'chat-messages' ? chat : null),
    querySelector: () => null,
    querySelectorAll: (sel) => (sel === '[data-shell-scroll]' ? [chat, legal] : []),
};

const { captureAnchoredScrollers } = await import(await loadModuleUrl('web/js/ui-shell.js'));
const { releaseChatStickToBottom, resetChatStickToBottom } = await import(
    await loadModuleUrl('web/js/chat-state.js')
);

// 兩個捲動區都在底部附近（內容比視窗矮）
assert.deepEqual(captureAnchoredScrollers(), [chat, legal], '平常：貼在底部的都要錨定');

releaseChatStickToBottom();
assert.equal(chat.dataset.stickReleased, '1', '放掉貼底要標在 #chat-messages 上，給 ui-shell 看');
assert.deepEqual(captureAnchoredScrollers(), [legal], '放掉貼底的聊天區不能被 ui-shell 拉到底');

resetChatStickToBottom();
assert.equal(chat.dataset.stickReleased, undefined, '送出訊息接回貼底時拿掉標記');
assert.deepEqual(captureAnchoredScrollers(), [chat, legal], '接回後恢復錨定');

console.log('shell stick release tests passed');
