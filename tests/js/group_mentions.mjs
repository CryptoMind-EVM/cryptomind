// 群組 @提及（前端）。看守：
//   1. 訊息文字：後端給的 mentions 名字變成 .msg-mention（最長優先、不分大小寫、轉義），@ 到我的另外標 .msg-mention-me
//   2. 群組氣泡：別人的氣泡 @ 到我有醒目標示；自己的氣泡（底色是主色）不能用主色字
//   3. 列表：mentioned 的群有「有人提及你」，而且放在 .conv-preview 外面（「輸入中…」會整個換掉 preview 的文字）
//   4. 輸入框選單的純函式：游標前的 @查詢、候選名單（不含自己、前綴優先）、插入後游標位置
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
globalThis.I18n = { t: (k, args) => (args ? `${k}|${JSON.stringify(args)}` : k) };
globalThis.console.log = () => {};

const load = async (rel) => import(await loadModuleUrl(new URL(rel, import.meta.url).pathname));
const { renderMessageText } = await load('../../web/js/dm-message-actions.js');
const gc = await load('../../web/js/group-chat.js');
const picker = await load('../../web/js/group-mention-picker.js');

const mentions = [
    { user_id: 'me', name: 'Danny' },
    { user_id: 'u2', name: 'Dan' },
    { user_id: 'u3', name: '<b>壞人</b>' },
];
const count = (html, needle) => html.split(needle).length - 1;

// ── 1) 訊息文字 ─────────────────────────────────────────
{
    const html = renderMessageText('@Danny 跟 @dan 看 https://x.com/@Danny', { mentions, myId: 'me' });
    assert.equal(count(html, 'class="msg-mention'), 2, '網址裡的 @ 不算');
    assert.ok(html.includes('data-mention="me"') && html.includes('>@Danny</span>'), '最長的 Danny 優先');
    assert.ok(html.includes('data-mention="u2"') && html.includes('>@dan</span>'), '不分大小寫、保留原文大小寫');
    assert.equal(count(html, 'msg-mention-me'), 1, '只有 @ 到我的那個另外標示');
    assert.ok(html.includes('href="https://x.com/@Danny"'), '網址照樣是連結');

    const evil = renderMessageText('@<b>壞人</b> 你好', { mentions, myId: 'me' });
    assert.ok(evil.includes('@&lt;b&gt;壞人&lt;/b&gt;</span>') && !evil.includes('<b>'), '名字要轉義');

    const full = renderMessageText('＠Danny 早', { mentions, myId: 'me' });
    assert.ok(full.includes('data-mention="me"') && full.includes('>＠Danny</span>'), '全形＠也算（注音預設打全形）');
    assert.equal(renderMessageText('@Danny 你好'), '@Danny 你好', '沒給 mentions 照舊（私訊不受影響）');
    assert.equal(renderMessageText('@Bob 你好', { mentions, myId: 'me' }), '@Bob 你好', '不是被提及的名字不標');
}

// ── 2) 群組氣泡 ─────────────────────────────────────────
{
    const ctx = { myId: 'me', nameOf: (u) => u, members: [] };
    const base = { id: 9, created_at: '2026-10-02T01:00:00Z', message_type: 'text', reactions: [] };
    const theirs = gc.renderGroupBubble({ ...base, from_user_id: 'u2', content: '@Danny 早', mentions: [mentions[0]] }, ctx);
    assert.ok(theirs.includes('msg-mention-me'), '別人 @ 我：醒目標示');
    const mine = gc.renderGroupBubble({ ...base, from_user_id: 'me', content: '@Dan 早', mentions: [mentions[1]] }, ctx);
    const span = mine.match(/<span class="msg-mention[^"]*"/)[0];
    assert.ok(!span.includes('text-primary'), '自己的氣泡底色是主色，字不能也是主色');
    // DANNY 2026-10-02：提及要有色塊讓人一眼看出有人被 @，不靠底線
    assert.ok(span.includes('bg-background/25') && !span.includes('underline'), '自己的氣泡：淺色色塊、不用底線');
    const other = gc.renderGroupBubble({ ...base, from_user_id: 'u3', content: '@Dan 早', mentions: [mentions[1]] }, ctx);
    const otherSpan = other.match(/<span class="msg-mention[^"]*"/)[0];
    assert.ok(otherSpan.includes('bg-primary/10') && !otherSpan.includes('msg-mention-me'), '@ 別人：淡色塊');
    assert.ok(theirs.match(/<span class="msg-mention[^"]*"/)[0].includes('bg-primary/25'), '@ 我：色塊更深');
    const noField = gc.renderGroupBubble({ ...base, from_user_id: 'u2', content: '@Danny 早' }, ctx);
    assert.ok(!noField.includes('msg-mention'), '舊資料沒有 mentions 欄位也不炸');
}

// ── 3) 列表 ────────────────────────────────────────────
{
    const group = {
        id: 5,
        name: '投資閒聊',
        member_count: 3,
        unread_count: 2,
        last_message_at: '2026-10-02T01:00:00Z',
        last_message: { id: 9, content: '@Danny 早', message_type: 'text', from_user_id: 'u2', from_display_name: 'Dan' },
    };
    const ctx = { nameOf: (u) => u, formatTime: () => '上午9:00' };
    const marked = gc.renderGroupListItem({ ...group, mentioned: true }, ctx);
    assert.ok(marked.includes('conv-mention') && marked.includes('groups.mentionedYou'));
    const preview = marked.match(/<p class="conv-preview[^>]*>([^<]*)<\/p>/);
    assert.ok(preview && !preview[1].includes('groups.mentionedYou'), '標記不能在 .conv-preview 裡');
    assert.ok(!gc.renderGroupListItem(group, ctx).includes('conv-mention'));
}

// ── 4) 輸入框選單的純函式 ────────────────────────────────
{
    const { mentionQuery, mentionCandidates, applyMention } = picker;
    assert.deepEqual(mentionQuery('嗨 @Da', 6), { start: 2, query: 'Da' });
    assert.deepEqual(mentionQuery('@', 1), { start: 0, query: '' }, '剛打 @ 就開選單');
    assert.deepEqual(mentionQuery('你好@小', 4), { start: 2, query: '小' }, '中文前面不用空白');
    assert.deepEqual(mentionQuery('嗨 ＠Da', 6), { start: 2, query: 'Da' }, '全形＠（注音預設）也要開選單');
    assert.deepEqual(mentionQuery('＠', 1), { start: 0, query: '' });
    assert.deepEqual(mentionQuery('a@b ＠x', 6), { start: 4, query: 'x' }, '看最靠近游標的那個');
    assert.equal(mentionQuery('@Danny 你好', 9), null, '@ 後面已經有空白：不是在打名字');
    assert.equal(mentionQuery('@Da\nx', 5), null);
    assert.equal(mentionQuery('沒有', 2), null);
    assert.deepEqual(mentionQuery('@Da 跟 @Am', 6), null, '游標在中間的空白後面');
    assert.deepEqual(mentionQuery('@Da 跟 @Am', 3), { start: 0, query: 'Da' }, '看的是游標前面');

    const members = [
        { user_id: 'me', display_name: 'Danny' },
        { user_id: 'u2', display_name: 'Adan' },
        { user_id: 'u3', username: 'dana' },
        { user_id: 'u4', display_name: '小明' },
    ];
    assert.deepEqual(
        mentionCandidates(members, 'da', 'me').map((m) => m.user_id),
        ['u3', 'u2'],
        '不含自己、前綴符合的排前面'
    );
    assert.deepEqual(
        mentionCandidates(members, '', 'me').map((m) => m.name),
        ['Adan', 'dana', '小明'],
        '空查詢列出全部（自己以外）'
    );
    assert.equal(mentionCandidates(members, 'zz', 'me').length, 0);

    assert.deepEqual(applyMention('嗨 @Da 你好', 5, 2, 'Danny'), { value: '嗨 @Danny 你好', caret: 9 }, '後面已經有空白就不再補');
    assert.deepEqual(applyMention('嗨 @Da你好', 5, 2, 'Danny'), { value: '嗨 @Danny 你好', caret: 9 });
    assert.deepEqual(applyMention('@', 1, 0, '小明'), { value: '@小明 ', caret: 4 });
    assert.deepEqual(applyMention('＠小', 2, 0, '小明'), { value: '@小明 ', caret: 4 }, '選了之後統一成半形');
}

console.error('group_mentions: ok');
