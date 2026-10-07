// 群組資訊面板（group-chat-dialogs.js openGroupInfoPanel）的成員列動作。看守：
//   1. 群主：其他成員旁有「轉讓群主」＋「移除」，自己那列沒有
//   2. 一般成員：誰旁邊都沒有
//   3. 按「轉讓群主」回傳 { action: 'transfer', userId }，面板關掉
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

function makeNode(tag) {
    const listeners = {};
    const node = {
        tag,
        className: '',
        textContent: '',
        type: '',
        checked: false,
        children: [],
        parent: null,
        attrs: {},
        append(...kids) {
            kids.forEach((k) => node.appendChild(k));
        },
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
            node.attrs[k] = v;
        },
        addEventListener(type, fn) {
            (listeners[type] ||= []).push(fn);
        },
        fire(type, event = {}) {
            (listeners[type] || []).forEach((fn) => fn({ target: node, ...event }));
        },
    };
    return node;
}

function walk(node, out = []) {
    out.push(node);
    node.children.forEach((c) => walk(c, out));
    return out;
}

const body = makeNode('body');
globalThis.window = globalThis;
globalThis.I18n = { t: (k) => k };
globalThis.document = {
    body,
    createElement: (tag) => makeNode(tag),
    addEventListener() {},
    removeEventListener() {},
};

const { openGroupInfoPanel } = await import(await loadModuleUrl('web/js/group-chat-dialogs.js'));

const group = {
    id: 3,
    name: '投資閒聊',
    owner_id: 'me',
    history_visible: false,
    me: { muted: false },
    members: [
        { user_id: 'me', display_name: '我', is_owner: true },
        { user_id: 'bob', display_name: 'Bob', is_owner: false },
        { user_id: 'amy', display_name: 'Amy', is_owner: false },
    ],
};

function memberRow(name) {
    return walk(body).find((n) => n.children.some((c) => c.tag === 'span' && c.textContent === name && c.className.includes('flex-1')));
}
const buttonsIn = (row) => row.children.filter((c) => c.tag === 'button').map((b) => b.textContent);

// 1. 群主
{
    const pending = openGroupInfoPanel({ group, myId: 'me' });
    assert.deepEqual(buttonsIn(memberRow('Bob')), ['groups.transferOwner', 'groups.removeMember']);
    assert.deepEqual(buttonsIn(memberRow('Amy')), ['groups.transferOwner', 'groups.removeMember']);
    assert.deepEqual(buttonsIn(memberRow('我')), [], '自己那列沒有動作');

    // 3. 按轉讓
    memberRow('Amy')
        .children.find((c) => c.textContent === 'groups.transferOwner')
        .fire('click');
    assert.deepEqual(await pending, { action: 'transfer', userId: 'amy' });
    assert.equal(body.children.length, 0, '面板關掉');
}

// 2. 一般成員
{
    const pending = openGroupInfoPanel({ group, myId: 'bob' });
    for (const name of ['我', 'Bob', 'Amy']) assert.deepEqual(buttonsIn(memberRow(name)), [], `${name} 那列沒有動作`);
    body.children[0].fire('click'); // 點遮罩關掉
    assert.equal(await pending, null);
}

console.error('group_info_panel: ok');
