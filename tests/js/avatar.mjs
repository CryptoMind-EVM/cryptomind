// 彩色頭像（2026-10-01 DANNY：「讓人更換大頭貼不然很醜」→ 先不做上傳，改依 user_id 固定配色）。
// 看守：同一個 id 永遠同色、色票真的有分散、首字支援 emoji／罕用字、白字對比 ≥ 4.5、
// styles.css 的八色跟 JS 的色票一致（兩邊各寫一份，漂移就紅）。
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
const url = await loadModuleUrl(new URL('../../web/js/avatar.js', import.meta.url).pathname);
const { avatarColorClass, avatarInitial, applyAvatar, AVATAR_COLORS } = await import(url);

// 1) 固定、分散
assert.equal(avatarColorClass('test-user-001'), avatarColorClass('test-user-001'), '同一個 id 要同色');
assert.match(avatarColorClass('abc'), /^avatar-c[0-7]$/);
assert.equal(avatarColorClass(''), 'avatar-c0');
assert.equal(avatarColorClass(undefined), 'avatar-c0');
const used = new Set(Array.from({ length: 200 }, (_, i) => avatarColorClass(`user-${i}`)));
assert.equal(used.size, 8, '200 個 id 要用到全部 8 色');

// 2) 首字
assert.equal(avatarInitial('danny'), 'D');
assert.equal(avatarInitial('你猜我是誰'), '你');
assert.equal(avatarInitial('😀smile'), '😀', 'emoji 不能被切成半個 surrogate');
assert.equal(avatarInitial('𠀋字'), '𠀋', '罕用字（BMP 外）也要完整');
assert.equal(avatarInitial(''), '?');
assert.equal(avatarInitial(null), '?');

// 3) 白字對比 ≥ 4.5（WCAG AA）
const lum = (hex) => {
    const c = [1, 3, 5]
        .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
        .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};
const contrast = (a, b) => {
    const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
    return (x + 0.05) / (y + 0.05);
};
assert.equal(AVATAR_COLORS.length, 8);
for (const hex of AVATAR_COLORS) {
    assert.ok(contrast(hex, '#FFFFFF') >= 4.5, `${hex} 白字對比不足 4.5`);
}

// 4) styles.css 跟 JS 色票一致
const css = await readFile(new URL('../../web/styles.css', import.meta.url), 'utf8');
AVATAR_COLORS.forEach((hex, i) => {
    const m = css.match(new RegExp(`\\.avatar-c${i}\\s*\\{[^}]*background-color:\\s*(#[0-9A-Fa-f]{6})`));
    assert.ok(m, `styles.css 缺 .avatar-c${i}`);
    assert.equal(m[1].toUpperCase(), hex.toUpperCase(), `.avatar-c${i} 跟 JS 色票不一致`);
});

// 5) applyAvatar：換掉舊色、設字；元素不在不炸
const classes = new Set(['w-9', 'avatar-c3']);
const el = {
    textContent: '',
    classList: {
        add: (c) => classes.add(c),
        remove: (...cs) => cs.forEach((c) => classes.delete(c)),
        forEach: (fn) => [...classes].forEach(fn),
    },
};
applyAvatar(el, 'test-user-001', 'danny');
assert.equal(el.textContent, 'D');
assert.ok(classes.has(avatarColorClass('test-user-001')));
assert.equal([...classes].filter((c) => /^avatar-c\d$/.test(c)).length, 1, '只能留一個顏色 class');
applyAvatar(null, 'x', 'y');

assert.equal(typeof window.Avatar?.colorClass, 'function', 'classic script（profile-page.js）要用得到');

// 6) 各渲染點都套上顏色、不再是舊的 bg-primary/20、bg-surfaceHighlight 單色
{
    const mkEl = () => ({
        textContent: '',
        innerHTML: '',
        classList: { add() {}, remove() {}, forEach() {}, contains: () => false, toggle() {} },
        dataset: {},
        style: {},
        addEventListener() {},
        querySelector: () => null,
        querySelectorAll: () => [],
    });
    globalThis.document = {
        documentElement: { lang: 'zh-TW' },
        getElementById: () => mkEl(),
        createElement: () => mkEl(),
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
    };
    globalThis.addEventListener = () => {};
    globalThis.innerWidth = 1200;
    globalThis.I18n = { t: (k) => k };
    globalThis.AppUtils = { refreshIcons() {} };
    globalThis.SecurityUtils = { escapeHTML: (s) => String(s) };
    globalThis.AuthManager = { currentUser: { user_id: 'me' } };
    globalThis.console.log = () => {};
    const load = async (rel) => import(await loadModuleUrl(new URL(rel, import.meta.url).pathname));
    const { SocialHub, FriendsUI } = await load('../../web/js/friends.js');
    const { MessagesUI } = await load('../../web/js/messages.js');
    const want = avatarColorClass('bob-id');
    // 頭像 div：class 裡有該 user 的顏色，內容是暱稱首字
    const avatarOf = (html) => new RegExp(`class="[^"]*${want}[^"]*">\\s*小`).test(html);

    const card = FriendsUI.renderUserCard({ user_id: 'bob-id', username: 'bob', display_name: '小明', friend_status: 'accepted' });
    assert.ok(avatarOf(card), '好友卡片：顏色＋暱稱首字');

    const conv = { id: 1, other_user_id: 'bob-id', other_username: 'bob', other_display_name: '小明', unread_count: 0 };
    assert.ok(avatarOf(SocialHub.renderConversationItem(conv)), '桌機對話列表要套色');
    assert.ok(avatarOf(MessagesUI.renderConversationItem(conv)), '手機對話列表要套色');
    assert.equal(MessagesUI.getInitial('😀hi'), '😀', 'getInitial 改走 avatarInitial');
}
console.error('avatar: ok');
