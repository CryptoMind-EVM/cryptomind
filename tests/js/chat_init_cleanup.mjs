// chat-init 的「New Chat」自動清理（2026-09-25 盤查）：只清沒有訊息的空對話，
// 目前對話／上次開啟的對話一律不動。
//
// 以前只看標題：後端只在第一則「使用者」訊息才把 'New Chat' 換成內容
// （core/database/chat.py save_chat_message 的 upsert），第一則是助理訊息、
// 或舊資料，就會留著預設標題——整段對話在下次開聊天頁時被刪掉。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const SESSIONS = [
    // 新→舊（後端 ORDER BY updated_at DESC）
    { id: 's-newest-empty', title: 'New Chat', has_messages: false }, // 最新一個空的：照舊保留
    { id: 's-with-history', title: 'New Chat', has_messages: true }, // 有內容：不能刪（本次的 bug）
    { id: 's-current', title: 'New Chat', has_messages: false }, // 伺服器「目前對話」
    { id: 's-last', title: 'New Chat', has_messages: false }, // localStorage 上次開啟
    { id: 's-old-backend', title: 'New Chat' }, // 舊後端沒回 has_messages：不知道就不刪
    { id: 's-empty-old', title: 'New Chat', has_messages: false }, // 唯一該清的
    { id: 's-titled', title: 'BTC?', has_messages: false }, // 不是預設標題
];

const deleted = [];
const store = new Map();
globalThis.window = globalThis;
globalThis.document = { getElementById: () => null };
globalThis.console.log = () => {};
globalThis.AppStore = { get: (k) => store.get(k), set: (k, v) => store.set(k, v) };
globalThis.AuthManager = { isLoggedIn: () => true, currentUser: { user_id: 'u1' } };
globalThis.localStorage = {
    getItem: (k) => (k === 'chat_last_session_id' ? 's-last' : null),
    setItem() {},
    removeItem() {},
};
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.loadSessions = async () => SESSIONS.filter((s) => !deleted.includes(s.id));
globalThis.AppAPI = {
    get: async (url) => (url === '/api/chat/current-session' ? { session_id: 's-current' } : {}),
    delete: async (url) => {
        deleted.push(decodeURIComponent(url.split('/').pop()));
        return {};
    },
};
const restored = [];
globalThis.updateSessionActiveState = () => {};
globalThis.loadChatHistory = async (sid) => { restored.push(sid); };
globalThis.showWelcomeScreen = () => {};

await import(await loadModuleUrl(new URL('../../web/js/chat-init.js', import.meta.url).pathname));
await window.initChat();

assert.deepEqual(deleted, ['s-empty-old'], `只能清掉空的舊 New Chat，實際刪了：${JSON.stringify(deleted)}`);
assert.equal(window.currentSessionId, 's-current', '清理後仍要還原伺服器端的目前對話');
assert.deepEqual(restored, ['s-current']);

console.error('chat_init_cleanup: ok');
