// 功能選單的未讀標示（web/js/nav-badges.js，2026-10-02）。看守：
//   1. 社群 [data-nav-item="friends"] 掛數字（99+ 封頂）、論壇掛紅點；歸零就拿掉、重畫不重複掛
//   2. 選單看不到的入口（手機 ☰、獨立頁漢堡、停在「對話歷史」時的「功能選單」分頁）掛紅點；
//      「功能選單」分頁正被選著時不掛（選單就在眼前）
//   3. 資料：登入才打 /api/notifications/badges；訪客全部清掉；API 失敗保留上次的
//   4. 沒有 AuthManager 的獨立頁（governance）：登入與否由 site-sidebar 問完 /api/user/me 後
//      用 setSessionHint 告知；沒告知之前當訪客（不打 API、不掛徽章）
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

function makeNode(tag, attrs = {}) {
    const node = {
        tag,
        className: '',
        textContent: '',
        children: [],
        parent: null,
        dataset: {},
        attrs: { ...attrs },
        style: {},
        appendChild(kid) {
            kid.parent = node;
            node.children.push(kid);
            return kid;
        },
        remove() {
            if (!node.parent) return;
            node.parent.children = node.parent.children.filter((c) => c !== node);
            node.parent = null;
        },
        setAttribute(k, v) {
            node.attrs[k] = String(v);
        },
        getAttribute(k) {
            return k in node.attrs ? node.attrs[k] : null;
        },
    };
    return node;
}
const walk = (n, out = []) => (out.push(n), n.children.forEach((c) => walk(c, out)), out);

const root = makeNode('body');
const navItem = (id) => {
    const btn = makeNode('button');
    btn.dataset.navItem = id;
    return root.appendChild(btn);
};
const friends = navItem('friends');
const forum = navItem('forum');
const crypto = navItem('crypto');
const burger = root.appendChild(makeNode('button'));
burger.dataset.navDotHost = '';
const menuTab = root.appendChild(makeNode('button', { 'aria-selected': 'false' }));
menuTab.dataset.navDotHost = '';

globalThis.window = globalThis;
globalThis.document = {
    createElement: (tag) => makeNode(tag),
    querySelectorAll: (sel) => {
        const key = { '[data-nav-item]': 'navItem', '[data-nav-dot-host]': 'navDotHost' }[sel];
        assert.ok(key, `沒預期的選擇器 ${sel}`);
        return walk(root).filter((n) => key in n.dataset);
    },
    addEventListener() {},
    visibilityState: 'visible',
};
globalThis.addEventListener = () => {};
globalThis.I18n = { t: (k) => k };

let loggedIn = true;
let response = { success: true, social: 3, forum: true };
const calls = [];
globalThis.AuthManager = { isLoggedIn: () => loggedIn };
globalThis.AppAPI = {
    get: async (url, opts) => {
        calls.push([url, opts]);
        if (response instanceof Error) throw response;
        return response;
    },
};

const { badgeText } = await import(await loadModuleUrl(new URL('../../web/js/nav-badges.js', import.meta.url).pathname));
const NB = window.NavBadges;
const badgeOf = (btn) => btn.children.find((c) => 'navBadge' in c.dataset);
const dotOf = (el) => el.children.find((c) => 'navDot' in c.dataset);

// ── 純函式 ─────────────────────────────────────────────
assert.equal(badgeText(0), '');
assert.equal(badgeText(7), '7');
assert.equal(badgeText(99), '99');
assert.equal(badgeText(150), '99+');

// ── 1) 數字與紅點 ───────────────────────────────────────
await NB.refresh();
assert.deepEqual(calls.at(-1), ['/api/notifications/badges', { retries: 0 }]);
assert.equal(badgeOf(friends).textContent, '3');
assert.equal(badgeOf(friends).dataset.navBadge, 'count');
assert.equal(badgeOf(forum).dataset.navBadge, 'dot');
assert.equal(badgeOf(crypto), undefined, '工具類不掛');

const sameBadge = badgeOf(friends);
NB.paint(); // 重畫不重複掛
assert.equal(friends.children.filter((c) => 'navBadge' in c.dataset).length, 1);
assert.equal(badgeOf(friends), sameBadge, '數字沒變就不動 DOM（切回視窗會重抓一次，不要每次拆掉重掛）');

response = { success: true, social: 120, forum: false };
await NB.refresh();
assert.equal(badgeOf(friends).textContent, '99+');
assert.equal(badgeOf(forum), undefined, '論壇沒東西就拿掉');

// ── 2) 入口紅點 ─────────────────────────────────────────
assert.ok(dotOf(burger), '手機 ☰ 掛紅點');
const sameDot = dotOf(burger);
NB.paint();
assert.equal(dotOf(burger), sameDot, '紅點沒變也不動 DOM');
assert.ok(dotOf(menuTab), '停在「對話歷史」時「功能選單」分頁掛紅點');
menuTab.setAttribute('aria-selected', 'true');
NB.paint();
assert.equal(dotOf(menuTab), undefined, '功能選單已經打開：不掛');
assert.ok(dotOf(burger));

response = { success: true, social: 0, forum: false };
await NB.refresh();
assert.equal(badgeOf(friends), undefined);
assert.equal(dotOf(burger), undefined, '都沒有就拿掉');

// 選單裡沒有「社群」（功能選單可自訂）：社群的數字不能點亮入口紅點，不然點開找不到原因
response = { success: true, social: 4, forum: false };
await NB.refresh();
assert.ok(dotOf(burger));
friends.remove();
NB.paint();
assert.equal(dotOf(burger), undefined, '社群不在選單：入口不亮');
response = { success: true, social: 4, forum: true };
await NB.refresh();
assert.ok(dotOf(burger), '論壇在選單、有新留言：照亮');
root.appendChild(friends);

// ── 3) 失敗與訪客 ───────────────────────────────────────
response = { success: true, social: 2, forum: true };
await NB.refresh();
response = new Error('500');
await NB.refresh();
assert.equal(badgeOf(friends).textContent, '2', 'API 失敗保留上次的');

loggedIn = false;
const before = calls.length;
await NB.refresh();
assert.equal(calls.length, before, '訪客不打 API');
assert.equal(badgeOf(friends), undefined);
assert.equal(dotOf(burger), undefined);

// ── 4) 沒有 AuthManager 的獨立頁 ─────────────────────────
delete globalThis.AuthManager;
response = { success: true, social: 5, forum: false };
const callsBeforeHint = calls.length;
await NB.refresh();
assert.equal(calls.length, callsBeforeHint);
assert.equal(badgeOf(friends), undefined, '還不知道有沒有登入：當訪客，不打 API、不掛徽章');
NB.setSessionHint(true);
await new Promise((r) => setTimeout(r, 30)); // setSessionHint 排一次重抓
assert.ok(calls.length > callsBeforeHint, '告知已登入 → 重抓');
assert.equal(badgeOf(friends).textContent, '5');
NB.setSessionHint(false);
await new Promise((r) => setTimeout(r, 30));
assert.equal(badgeOf(friends), undefined, '告知是訪客 → 清掉');

console.error('nav_badges: ok');
