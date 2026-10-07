// 聊天室 AI 助理前端（web/js/chat-assistant.js）的純邏輯：SSE 解析、送出內容、錯誤對應、回答渲染。
// 抽屜本身（DOM、點擊）由 Playwright 實機驗。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
globalThis.I18n = { t: (k, args) => (args ? `${k}:${JSON.stringify(args)}` : k) };

const mod = await import(await loadModuleUrl('web/js/chat-assistant.js'));
const {
    parseSseChunk,
    buildAskBody,
    errorKey,
    shareErrorKey,
    progressKey,
    renderAnswerHtml,
    readStatusResponse,
    markdownToPlain,
    formatTurnTime,
    turnsToHistory,
    ChatAssistantAPI,
    confirmAction,
} = mod;

// ── SSE：完整事件、半包、多事件一包 ──
{
    const a = parseSseChunk('data: {"type":"stage","stage":"reading"}\n\ndata: {"type":"st');
    assert.deepEqual(a.events, [{ type: 'stage', stage: 'reading' }]);
    assert.equal(a.rest, 'data: {"type":"st');
    const b = parseSseChunk(a.rest + 'age","stage":"thinking"}\n\ndata: {"type":"answer","text":"hi"}\n\n');
    assert.deepEqual(b.events.map((e) => e.stage || e.type), ['thinking', 'answer']);
    assert.equal(b.rest, '');
    // 壞掉的一行不拖垮整包
    const c = parseSseChunk('data: {oops\n\ndata: {"type":"answer","text":"ok"}\n\n');
    assert.deepEqual(c.events, [{ type: 'answer', text: 'ok' }]);
    // 代理把換行改成 \r\n（review MEDIUM）：照樣解析
    const crlf = parseSseChunk('data: {"type":"stage","stage":"reading"}\r\n\r\ndata: {"type":"ans');
    assert.deepEqual(crlf.events, [{ type: 'stage', stage: 'reading' }]);
    assert.equal(crlf.rest, 'data: {"type":"ans');
    // 串流結束時最後一則沒有空行（review HIGH）：final 要把剩下的也解析掉，不然回答被吃掉
    assert.deepEqual(parseSseChunk('data: {"type":"answer","text":"尾巴"}\n').events, []);
    assert.deepEqual(parseSseChunk('data: {"type":"answer","text":"尾巴"}\n', { final: true }).events, [
        { type: 'answer', text: '尾巴' },
    ]);
    assert.deepEqual(parseSseChunk('', { final: true }), { events: [], rest: '' });
}

// ── 送出內容：範圍參數、私訊未讀數／群組 from_id、單則 around_id、追問最多 6 則 ──
{
    const base = { kind: 'dm', targetId: 9, language: 'zh-TW', tz: 'Asia/Taipei' };
    assert.deepEqual(buildAskBody({ ...base, range: 'recent' }, '  在聊什麼？ ', []), {
        kind: 'dm',
        target_id: 9,
        question: '在聊什麼？',
        range: 'recent',
        language: 'zh-TW',
        tz: 'Asia/Taipei',
        history: [],
    });
    const dmUnread = buildAskBody({ ...base, range: 'unread', unread: { unread_count: 4 } }, 'q', []);
    assert.equal(dmUnread.unread_count, 4);
    assert.equal(dmUnread.from_id, undefined);
    const groupUnread = buildAskBody({ ...base, kind: 'group', range: 'unread', unread: { from_id: 120 } }, 'q', []);
    assert.equal(groupUnread.from_id, 120);
    // 沒有在 unread 範圍就不帶未讀參數
    assert.equal(buildAskBody({ ...base, range: '24h', unread: { from_id: 1 } }, 'q', []).from_id, undefined);
    const around = buildAskBody({ ...base, range: 'message', aroundId: 77 }, 'q', []);
    assert.equal(around.around_id, 77);
    const history = Array.from({ length: 10 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', content: `m${i}` }));
    const body = buildAskBody({ ...base, range: 'recent' }, 'q', history);
    assert.equal(body.history.length, 6);
    assert.equal(body.history[0].content, 'm4', '只帶最新 6 則，而且從 user 開始');
    assert.equal(body.history[0].role, 'user');
}

// ── 錯誤碼 → i18n key；HTTP 狀態補位 ──
{
    assert.equal(errorKey({ code: 'quota_exhausted' }), 'assistant.errors.quota_exhausted');
    assert.equal(errorKey({ code: 'Rate limit exceeded: 3 per 1 minute', status: 429 }), 'assistant.errors.rate_limited');
    assert.equal(errorKey({ code: 'http_422', status: 422 }), 'assistant.errors.generic');
    assert.equal(errorKey({ code: 'not_found', status: 404 }), 'assistant.errors.not_found');
    assert.equal(errorKey({ code: 'whatever' }), 'assistant.errors.generic');
}

// ── 進度文字 ──
{
    assert.deepEqual(progressKey({ type: 'stage', stage: 'condensing', done: 2, total: 5 }), [
        'assistant.progress.condensing',
        { done: 2, total: 5 },
    ]);
    assert.deepEqual(progressKey({ type: 'stage', stage: 'thinking' }), ['assistant.progress.thinking', undefined]);
    assert.equal(progressKey({ type: 'stage', stage: 'unknown' }), null);
}

// ── 回答渲染：跟分享出去的卡片同一個渲染（ai-markdown.js），不靠 markdown-it ──
{
    // 有沒有 markdown-it 結果都一樣（手機私訊頁沒載它）
    globalThis.md = { render: () => '<p>不該被用到</p>' };
    const html = renderAnswerHtml('<img src=x onerror=alert(1)>\n**重點** 與 <b>粗</b>');
    assert.ok(!html.includes('<img') && !html.includes('onerror='), html);
    assert.ok(html.includes('<strong>重點</strong>') && html.includes('<strong>粗</strong>'), html);
    assert.ok(!html.includes('不該被用到'));
    delete globalThis.md;
    // 表格照原樣變表格（以前沒有 markdown-it 的頁面是一坨字）
    const table = renderAnswerHtml('| 指標 | 數值 |\n|---|---|\n| RSI | 72 |');
    assert.ok(table.includes('<table class="ai-md-table">') && table.includes('<td>72</td>'), table);
}

// ── 分享失敗的提示：額度用完／已過期或不在這個聊天室／不是 Pro／其他 ──
{
    assert.equal(shareErrorKey({ status: 429, code: 'Daily message limit reached (50)' }), 'assistant.shareErrors.limit');
    assert.equal(shareErrorKey({ status: 404, code: 'not_found' }), 'assistant.shareErrors.notFound');
    assert.equal(shareErrorKey({ status: 403, code: 'pro_required' }), 'assistant.errors.pro_required');
    assert.equal(shareErrorKey({ status: 403, code: 'You can only send messages to friends' }), 'assistant.shareErrors.generic');
    assert.equal(shareErrorKey({ code: 'network' }), 'assistant.shareErrors.generic');
}

// ── status：開關關（404）＝null、其他錯誤也當作不可用但不拋 ──
{
    assert.equal(await readStatusResponse(Promise.reject(Object.assign(new Error('nf'), { status: 404 }))), null);
    assert.equal(await readStatusResponse(Promise.reject(new Error('boom'))), null);
    const ok = { own_key: false, remaining: 3 };
    assert.deepEqual(await readStatusResponse(Promise.resolve(ok)), ok);
}

// ── 問答存伺服器後：分享／複製的純文字、時間、追問上下文、history API ──
{
    // markdown → 純文字（複製用）：標題、粗體、行內 code、清單符號、多餘空行；表格一列一行
    const md = '## 重點\n\n**BTC** 回測 `60000`\n\n\n\n* 第一點\n+ 第二點\n- 第三點\n```js\nx\n```';
    assert.equal(markdownToPlain(md), '重點\n\nBTC 回測 60000\n\n• 第一點\n• 第二點\n• 第三點\n\nx');
    assert.equal(markdownToPlain(null), '');
    assert.equal(markdownToPlain('| 指標 | 數值 |\n|---|---|\n| RSI | 72 |'), '• RSI：72');

    // 時間：今天只顯示時間，其他天加月/日（本機時區，用本機時間建日期，不受 TZ 影響）
    const now = new Date(2026, 9, 4, 18, 0);
    assert.equal(formatTurnTime(new Date(2026, 9, 4, 9, 5).toISOString(), now, 'en'), '09:05');
    assert.equal(formatTurnTime(new Date(2026, 9, 3, 23, 40).toISOString(), now, 'en'), '10/3 23:40');
    assert.equal(formatTurnTime('不是日期', now, 'en'), '');

    // 追問上下文：伺服器的問答 → user/assistant 交替，buildAskBody 再取最新 6 則（3 輪）
    const turns = Array.from({ length: 5 }, (_, i) => ({ id: i + 1, question: `q${i}`, answer: `a${i}` }));
    const history = turnsToHistory(turns);
    assert.equal(history.length, 10);
    assert.deepEqual(history.slice(0, 2), [
        { role: 'user', content: 'q0' },
        { role: 'assistant', content: 'a0' },
    ]);
    const body = buildAskBody({ kind: 'dm', targetId: 1, range: 'recent', language: 'zh-TW', tz: 'UTC' }, '追問', history);
    assert.deepEqual(body.history.map((h) => h.content), ['q2', 'a2', 'q3', 'a3', 'q4', 'a4']);
}

{
    // history API：網址帶 kind／target_id；失敗（網路、404）回 null 不拋；刪除回布林
    const calls = [];
    globalThis.AppAPI = {
        get: async (url, opts) => {
            calls.push(['get', url, opts]);
            if (url.includes('target_id=404')) throw new Error('404');
            return { turns: [{ id: 1, question: 'q', answer: 'a' }], pending: { question: 'q2', elapsed: 4 } };
        },
        delete: async (url, opts) => {
            calls.push(['delete', url, opts]);
            if (url.includes('/history/99')) throw new Error('404');
            return { success: true };
        },
    };
    const ok = await ChatAssistantAPI.history('group', 9);
    assert.equal(calls[0][1], '/api/chat-assistant/history?kind=group&target_id=9');
    assert.deepEqual(ok, { turns: [{ id: 1, question: 'q', answer: 'a' }], pending: { question: 'q2', elapsed: 4 } });
    assert.equal(await ChatAssistantAPI.history('dm', 404), null);
    globalThis.AppAPI.get = async () => ({ nope: true });
    assert.equal(await ChatAssistantAPI.history('dm', 5), null, '格式不對也當讀不到');
    assert.equal(await ChatAssistantAPI.deleteTurn(7), true);
    assert.equal(calls.at(-1)[1], '/api/chat-assistant/history/7');
    assert.equal(await ChatAssistantAPI.deleteTurn(99), false);
    assert.equal(await ChatAssistantAPI.clearHistory('dm', 5), true);
    assert.equal(calls.at(-1)[1], '/api/chat-assistant/history?kind=dm&target_id=5');
    delete globalThis.AppAPI;
}

{
    // 分享成卡片：走一般送訊息的 API，只帶 assistant_turn_id（內容由伺服器取，前端不送文字）
    const posts = [];
    globalThis.AppAPI = {
        post: async (url, body, opts) => {
            posts.push([url, body, opts]);
            if (body.assistant_turn_id === 404) throw Object.assign(new Error('not_found'), { status: 404 });
            return { success: true, message: { id: 1, message_type: 'ai_card' } };
        },
    };
    const dm = await ChatAssistantAPI.shareTurn({ kind: 'dm', userId: 'u2', groupId: 7 }, 12);
    assert.deepEqual(posts[0], ['/api/messages/send', { to_user_id: 'u2', assistant_turn_id: 12 }, { retries: 0 }]);
    assert.deepEqual(dm, { ok: true, data: { success: true, message: { id: 1, message_type: 'ai_card' } } });
    const group = await ChatAssistantAPI.shareTurn({ kind: 'group', userId: 'u2', groupId: 7 }, 13);
    assert.deepEqual(posts[1], ['/api/groups/7/messages', { assistant_turn_id: 13 }, { retries: 0 }]);
    assert.equal(group.ok, true);
    // 失敗：回 {ok:false, code, status}，不拋
    assert.deepEqual(await ChatAssistantAPI.shareTurn({ kind: 'dm', userId: 'u2' }, 404), { ok: false, code: 'not_found', status: 404 });
    delete globalThis.AppAPI;
    assert.deepEqual(await ChatAssistantAPI.shareTurn({ kind: 'dm', userId: 'u2' }, 1), { ok: false, code: 'network' });
}

// ── 確認框：走官方樣式（showConfirmDialog），層級高過抽屜（10000）；ui-shell 沒載入才退回原生 ──
{
    const seen = [];
    globalThis.showConfirmDialog = async (opts) => {
        seen.push(opts);
        return opts.title !== 'no';
    };
    let native = null;
    globalThis.confirm = (m) => {
        native = m;
        return false;
    };
    assert.equal(await confirmAction({ title: 'T', message: 'M', confirmText: 'OK', danger: true }), true);
    assert.deepEqual(seen[0], { title: 'T', message: 'M', confirmText: 'OK', danger: true, zIndex: 10001 });
    assert.equal(seen[0].zIndex > 10000, true, '要高過抽屜，不然確認框被蓋在後面');
    assert.equal(await confirmAction({ title: 'no', message: 'M' }), false, '取消回 false');
    assert.equal(native, null, 'showConfirmDialog 在就不能碰原生 confirm');
    delete globalThis.showConfirmDialog;
    assert.equal(await confirmAction({ title: 'T', message: 'fallback msg' }), false);
    assert.equal(native, 'fallback msg', 'ui-shell 沒載入才退回原生');
    delete globalThis.confirm;
}

console.log('chat_assistant ok');
