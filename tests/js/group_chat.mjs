// 群組聊天前端共用（docs/plans/2026-10-01-group-chat-impl.md Part C1）。看守：
//   1. 系統訊息事件碼 → i18n（群名含冒號不能被切掉）
//   2. 「已讀 N」只算看得到這則的其他成員（入群前的訊息不算）
//   3. 群組氣泡：系統訊息置中、別人的有頭像＋暱稱、自己的有 .msg-read-status（「只標最新一則」照常運作）、內容轉義
//   4. 群組列表項：跟私訊同一個 data-conversation-id 機制（列表「輸入中…」共用），id 加 g- 前綴不跟對話撞
//   5. WS：group_* 事件不再被丟掉；群組 typing 送 group_id、節流 key 不跟對話 id 撞
//   6. 選單的表情／檢舉網址可換（群組走 /api/groups/messages/…）
//   7. regroupMessageRows：同一人連續時藏暱稱、頭像留位置
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
globalThis.document = {
    documentElement: { lang: 'zh-TW' },
    getElementById: () => null,
    createElement: () => ({ innerHTML: '', textContent: '' }),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
};
globalThis.addEventListener = () => {};
// i18n：回傳 key＋參數，測試看得到用了哪個 key、帶了什麼
globalThis.I18n = { t: (k, args) => (args ? `${k}|${JSON.stringify(args)}` : k) };
globalThis.console.log = () => {};

const load = async (rel) => import(await loadModuleUrl(new URL(rel, import.meta.url).pathname));
const gc = await load('../../web/js/group-chat.js');
const { avatarColorClass } = await load('../../web/js/avatar.js');
const names = { a: '小明', b: '小華', c: '阿土' };
const nameOf = (uid) => names[uid] || uid;

// ── 1) 系統訊息 ─────────────────────────────────────────
{
    const s = (code) => gc.groupSystemText(code, nameOf);
    assert.equal(s('created:a'), 'groups.system.created|{"name":"小明"}');
    assert.equal(s('member_joined:b'), 'groups.system.memberJoined|{"name":"小華"}');
    assert.equal(s('member_left:b'), 'groups.system.memberLeft|{"name":"小華"}');
    assert.equal(s('member_removed:c'), 'groups.system.memberRemoved|{"name":"阿土"}');
    assert.equal(s('owner_changed:b'), 'groups.system.ownerChanged|{"name":"小華"}');
    assert.equal(s('renamed:幣圈:閒聊'), 'groups.system.renamed|{"name":"幣圈:閒聊"}', '群名裡的冒號不能被切掉');
    assert.equal(s('history_visible:on'), 'groups.system.historyOn');
    assert.equal(s('history_visible:off'), 'groups.system.historyOff');
    assert.equal(s('mystery:x'), 'groups.system.unknown');
    // 後端帶的名字優先（對方已退群、成員名單還沒載入時 nameOf 只拿得到 id）
    assert.equal(gc.groupSystemText('member_left:zz', nameOf, '老王'), 'groups.system.memberLeft|{"name":"老王"}');
}

// ── 2) 已讀 N ──────────────────────────────────────────
{
    const members = [
        { user_id: 'me', last_read_message_id: 30, first_visible_message_id: 0 },
        { user_id: 'a', last_read_message_id: 10, first_visible_message_id: 0 },
        { user_id: 'b', last_read_message_id: 5, first_visible_message_id: 0 },
        { user_id: 'c', last_read_message_id: 20, first_visible_message_id: 15 },
    ];
    assert.equal(gc.groupReadCount({ id: 9, from_user_id: 'me' }, members), 1, 'a 讀到 10；c 入群前看不到 9');
    assert.equal(gc.groupReadCount({ id: 12, from_user_id: 'me' }, members), 0, 'c 讀到 20 但看不到 12');
    assert.equal(gc.groupReadCount({ id: 16, from_user_id: 'me' }, members), 1, 'c 看得到也讀過 16');
    assert.equal(gc.groupReadCount({ id: 4, from_user_id: 'a' }, members), 2, '不算發訊者本人；me、b 讀過，c 入群前看不到');
    assert.equal(gc.groupReadCount({ id: 4, from_user_id: 'me' }, []), 0);
}

// ── 3) 群組氣泡 ─────────────────────────────────────────
{
    const members = [
        { user_id: 'me', last_read_message_id: 50, first_visible_message_id: 0 },
        { user_id: 'a', last_read_message_id: 50, first_visible_message_id: 0 },
        { user_id: 'b', last_read_message_id: 0, first_visible_message_id: 0 },
    ];
    const ctx = { myId: 'me', nameOf, members, idPrefix: 'social-msg-' };
    const base = { group_id: 3, created_at: '2026-10-01T03:17:00Z', reactions: [], reply_to: null };

    const sys = gc.renderGroupBubble({ ...base, id: 1, from_user_id: null, message_type: 'system', content: 'member_joined:b' }, ctx);
    assert.ok(sys.includes('data-type="system"') && sys.includes('data-from="__system"'), '系統訊息另一種列');
    assert.ok(sys.includes('id="social-msg-1"') && sys.includes('data-message-id="1"'), '要能被 insertMessageRow 排序／去重');
    assert.ok(sys.includes('groups.system.memberJoined'), '系統訊息顯示組好的字');
    assert.ok(!sys.includes('msg-bubble'), '系統訊息不能長按／按表情');

    const other = gc.renderGroupBubble(
        { ...base, id: 2, from_user_id: 'a', from_display_name: '小明', message_type: 'text', content: '<img src=x onerror=1>' },
        ctx
    );
    assert.ok(other.includes('msg-avatar') && other.includes(avatarColorClass('a')), '別人的訊息有彩色頭像');
    assert.ok(/class="msg-sender[^"]*"[^>]*>小明</.test(other), '別人的訊息有暱稱');
    assert.ok(other.includes('msg-bubble') && other.includes('msg-text'), '選單要找得到 .msg-bubble／.msg-text');
    assert.ok(!other.includes('<img'), '內容要轉義');
    assert.ok(!other.includes('msg-read-status'), '別人的訊息不標已讀');

    const mine = gc.renderGroupBubble({ ...base, id: 40, from_user_id: 'me', message_type: 'text', content: 'hi' }, ctx);
    assert.ok(mine.includes('msg-read-status') && mine.includes('groups.readCount|{&quot;count&quot;:1}'), '自己的：已讀 1（a 讀了、b 還沒）');
    assert.ok(!mine.includes('msg-avatar'), '自己的訊息不放頭像');
    const unread = gc.renderGroupBubble({ ...base, id: 60, from_user_id: 'me', message_type: 'text', content: 'hi' }, ctx);
    assert.ok(unread.includes('msg-read-status') && unread.includes('messages.deliveredStatus'), '沒人讀＝已送達（仍要有狀態，最新一則才標得到）');

    const recalled = gc.renderGroupBubble({ ...base, id: 5, from_user_id: 'a', message_type: 'recalled', content: '' }, ctx);
    assert.ok(recalled.includes('messages.recalledByOther') && !recalled.includes('msg-text'));
}

// ── 4) 列表項 ──────────────────────────────────────────
{
    const group = {
        id: 5,
        name: '<b>幣圈</b>',
        member_count: 4,
        unread_count: 2,
        last_message_at: '2026-10-01T03:17:00Z',
        last_message: { id: 9, message_type: 'text', content: '早安', from_user_id: 'a', from_display_name: '小明' },
    };
    const item = gc.renderGroupListItem(group, { nameOf, formatTime: () => '上午11:17' });
    assert.ok(item.includes('data-conversation-id="g-5"'), '列表「輸入中…」共用 data-conversation-id，加 g- 不跟對話 id 撞');
    assert.ok(item.includes('data-click="SocialHub.openGroup"') && item.includes('data-click-arg="5"'));
    assert.ok(!item.includes('<b>') && item.includes('&lt;b&gt;'), '群名要轉義');
    assert.ok(item.includes('conv-preview') && item.includes('小明: 早安'));
    assert.ok(item.includes('>2<'), '未讀數');
    const sysPreview = gc.renderGroupListItem(
        { ...group, unread_count: 0, last_message: { id: 9, message_type: 'system', content: 'member_joined:b', from_user_id: null } },
        { nameOf, formatTime: () => '' }
    );
    assert.ok(sysPreview.includes('groups.system.memberJoined'), '最後一則是系統訊息時預覽組字');
}

// ── 5) WebSocket ───────────────────────────────────────
{
    const { MessagesWebSocket } = await load('../../web/js/messages.js');
    const got = [];
    MessagesWebSocket.onGroupEvent((d) => got.push(d.type));
    for (const type of ['group_message', 'group_message_recalled', 'group_reaction_updated', 'group_read', 'group_typing', 'group_updated']) {
        MessagesWebSocket._handleMessage({ type, group_id: 3 });
    }
    assert.equal(got.length, 6, 'group_* 事件都要交給 onGroupEvent（以前未知類型被靜默丟掉）');

    const sent = [];
    MessagesWebSocket.ws = { send: (s) => sent.push(JSON.parse(s)) };
    MessagesWebSocket.connected = true;
    MessagesWebSocket.sendGroupTyping(3);
    MessagesWebSocket.sendGroupTyping(3);
    assert.deepEqual(sent, [{ action: 'typing', group_id: 3 }], '送 group_id（數字）且 3 秒內只送一次');
    MessagesWebSocket.sendTyping(3);
    assert.equal(sent.length, 2, '對話 3 跟群組 3 的節流分開');
    MessagesWebSocket.sendGroupTypingStop(3);
    assert.deepEqual(sent[2], { action: 'typing', group_id: 3, state: 'stop' });
}

// ── 6) 選單網址 ─────────────────────────────────────────
{
    const { reactionUrl, reportUrl } = await load('../../web/js/dm-message-actions.js');
    assert.equal(reactionUrl({}, 7), '/api/messages/7/reaction', '私訊預設不變');
    assert.equal(reactionUrl({ urls: { reaction: (id) => `/api/groups/messages/${id}/reaction` } }, 7), '/api/groups/messages/7/reaction');
    assert.equal(reportUrl({}, 7), '/api/messages/7/report');
    assert.equal(reportUrl({ url: '/api/groups/messages/7/report' }, 7), '/api/groups/messages/7/report');
}

// ── 7) regroupMessageRows：連續同一人藏暱稱、頭像留位置 ──────────────
{
    const { regroupMessageRows } = await load('../../web/js/messages.js');
    const mkClass = () => {
        const set = new Set();
        return {
            set,
            add: (...c) => c.forEach((x) => set.add(x)),
            remove: (...c) => c.forEach((x) => set.delete(x)),
            toggle: (c, on) => (on ? set.add(c) : set.delete(c)),
            contains: (c) => set.has(c),
        };
    };
    const mkRow = (from, ts) => {
        const parts = { '.msg-sender': { classList: mkClass() }, '.msg-avatar': { classList: mkClass() }, '.msg-meta': { classList: mkClass() } };
        return { dataset: { from, ts }, classList: mkClass(), querySelector: (sel) => parts[sel] || null, insertAdjacentHTML() {}, parts };
    };
    const rows = [mkRow('a', '2026-10-01T03:00:00Z'), mkRow('a', '2026-10-01T03:00:10Z'), mkRow('b', '2026-10-01T03:00:20Z')];
    regroupMessageRows({ querySelectorAll: (sel) => (sel === '.msg-row' ? rows : []) });
    const hidden = (r) => r.parts['.msg-sender'].classList.contains('hidden');
    const invisible = (r) => r.parts['.msg-avatar'].classList.contains('invisible');
    assert.deepEqual(rows.map(hidden), [false, true, false], '連續同一人只有第一則顯示暱稱');
    assert.deepEqual(rows.map(invisible), [false, true, false], '頭像留位置（invisible）氣泡才對齊');
}

// ── 8) GET 不重試：404＝開關關／不是成員，重試只會讓列表晚 6 秒出來 ─────────
{
    const calls = [];
    globalThis.AppAPI = {
        get: (url, opts) => (calls.push([url, opts]), Promise.resolve({})),
        post: () => Promise.resolve({}),
    };
    await gc.GroupChatAPI.list();
    await gc.GroupChatAPI.get(3);
    await gc.GroupChatAPI.messages(3, { beforeId: 9 });
    await gc.GroupChatAPI.invites();
    assert.ok(calls.every(([, opts]) => opts?.retries === 0), JSON.stringify(calls));
    assert.equal(calls[2][0], '/api/groups/3/messages?limit=50&before_id=9');
}

// ── 9) 選單「已讀名單」：只有群組開、只在自己的訊息（桌機 hover 時工具列蓋住「已讀 N」，點不到） ──
{
    const { messageMenuItems } = await load('../../web/js/dm-message-actions.js');
    const keys = (opts) => messageMenuItems({ createdAt: new Date().toISOString(), messageType: 'text', ...opts }).map((i) => i.key || i);
    const group = new Set(['react', 'reply', 'copy', 'report', 'recall', 'readers']);
    assert.ok(keys({ isMine: true, features: group }).includes('readers'), '群組：自己的訊息有已讀名單');
    assert.ok(!keys({ isMine: false, features: group }).includes('readers'), '別人的訊息沒有');
    assert.ok(!keys({ isMine: true, features: new Set(['reply', 'copy', 'recall', 'hide']) }).includes('readers'), '私訊沒開就沒有');
    assert.ok(!keys({ isMine: true, messageType: 'recalled', features: group }).includes('readers'), '收回的沒有');
}

// ── 10) 列表項給 href 就是連結（messages.html 沒有 SocialHub，點了帶去好友頁開群） ──
{
    const item = gc.renderGroupListItem(
        { id: 9, name: '群', member_count: 2, unread_count: 0, last_message: null },
        { nameOf, formatTime: () => '', href: '/?group=9#friends' }
    );
    assert.ok(item.includes('href="/?group=9#friends"'), item);
    assert.ok(!item.includes('SocialHub.openGroup'), '有 href 就不掛 SocialHub 的 data-click');
    assert.ok(item.includes('data-conversation-id="g-9"'), '列表輸入中照樣認得到');
    const lg = gc.renderGroupListItem(
        { id: 9, name: '群', member_count: 2, unread_count: 0, last_message: null },
        { nameOf, formatTime: () => '', href: '/?group=9#friends', size: 'lg' }
    );
    assert.ok(lg.includes('text-base') && lg.includes('conv-preview text-sm'), 'messages.html 的字級跟私訊列一致');
}

// ── 11) 待回覆的邀請：列表最上面的「加入／拒絕」（以前只進通知中心） ──
{
    assert.equal(gc.renderGroupInvites([]), '', '沒有邀請就不畫');
    const html = gc.renderGroupInvites([{ invite_id: 3, group_id: 12, group_name: '<i>群', member_count: 2, inviter_name: '小明' }]);
    assert.ok(html.includes('data-invite-id="3"') && html.includes('data-group-id="12"'));
    assert.ok(html.includes('&lt;i&gt;群') && !html.includes('<i>群'), '群名要轉義');
    assert.ok(html.includes('groups.invitedBy') && html.includes('小明'), '寫誰邀請的');
    assert.ok(html.includes('data-invite-accept') && html.includes('data-invite-decline'));

    // 按鈕：假的列表容器，click 委派
    const mkBtn = (attr) => ({ disabled: false, hasAttribute: (a) => a === attr });
    const accept = mkBtn('data-invite-accept');
    const decline = mkBtn('data-invite-decline');
    let removed = 0;
    const row = { dataset: { inviteId: '3', groupId: '12' }, querySelectorAll: () => [accept, decline], remove: () => (removed += 1) };
    accept.closest = (sel) => (sel === '[data-invite-id]' ? row : null);
    decline.closest = accept.closest;
    let handler = null;
    const list = { dataset: {}, addEventListener: (type, fn) => type === 'click' && (handler = fn), querySelector: () => null };
    const done = [];
    const calls = [];
    const toasts = [];
    globalThis.showToast = (text, type) => toasts.push([text, type]);
    let fail = null;
    globalThis.AppAPI = {
        post: async (url) => {
            calls.push(url);
            if (fail) throw new Error(fail);
            return { success: true, group_id: 12 };
        },
    };
    gc.bindGroupInviteActions(list, (r) => done.push(r));
    gc.bindGroupInviteActions(list, (r) => done.push(r)); // 重畫再叫一次也只綁一次
    const click = (btn) => handler({ target: { closest: (sel) => (sel.includes('data-invite-') ? btn : null) } });

    await click(accept);
    assert.deepEqual(calls, ['/api/groups/invites/3/accept']);
    assert.deepEqual(done, [{ accepted: true, groupId: 12 }], '只綁一次：回呼一次');
    assert.equal(removed, 1, '成功就把那列拿掉');

    // 失敗：按鈕恢復、跳錯誤（在地化）；邀請已失效 → 叫頁面重抓
    fail = 'group_full';
    const realI18n = globalThis.I18n;
    globalThis.I18n = { t: (k) => (k === 'groups.errors.group_full' ? '群組人數已滿' : k) };
    await click(decline);
    globalThis.I18n = realI18n;
    assert.equal(calls.at(-1), '/api/groups/invites/3/decline');
    assert.equal(accept.disabled, false);
    assert.deepEqual(toasts.at(-1), ['群組人數已滿', 'error']);
    assert.equal(done.length, 1, '一般失敗不通知頁面');
    fail = 'invite_not_found';
    await click(accept);
    assert.deepEqual(done.at(-1), { accepted: false, stale: true });
}

console.error('group_chat: ok');
