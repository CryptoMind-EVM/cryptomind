// 論壇通知的顯示文字（notification-service.js describe，2026-10-02）。看守：
//   1. 推／留言／也在這篇留言：走 i18n，一個人與「等 N 人」不同句
//   2. 舊格式（沒有 post_title 的 like／comment 舊列）照後端原文，不能變成空標題
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.document = { visibilityState: 'visible', addEventListener: () => {}, getElementById: () => null };
globalThis.CustomEvent = class {
    constructor(type, init) {
        this.detail = init?.detail;
    }
};
globalThis.dispatchEvent = () => {};
console.log = () => {};

const T = {
    'notification.forum': '論壇',
    'notification.postPushed': '{{name}} 推了你的文章「{{title}}」',
    'notification.postPushedMany': '{{name}} 等 {{count}} 人推了你的文章「{{title}}」',
    'notification.postCommented': '{{name}} 留言了你的文章「{{title}}」',
    'notification.postCommentedMany': '{{name}} 等 {{count}} 人留言了你的文章「{{title}}」',
    'notification.postThreadReply': '{{name}} 也在「{{title}}」留言',
    'notification.postThreadReplyMany': '{{name}} 等 {{count}} 人也在「{{title}}」留言',
};
globalThis.I18n = { t: (k, o = {}) => (T[k] || k).replace(/\{\{(\w+)\}\}/g, (_, n) => o[n]) };
globalThis.AppAPI = { get: async () => ({ notifications: [], unread_count: 0 }), post: async () => ({}) };
globalThis.AuthManager = { currentUser: { user_id: 'me' }, isLoggedIn: () => true };

await import(await loadModuleUrl(new URL('../../web/js/notification-service.js', import.meta.url).pathname));
const NS = window.NotificationService;

const n = (kind, count, extra = {}) => ({
    id: 'p1',
    type: 'post_interaction',
    title: '論壇',
    body: '後端原文',
    data: { post_id: 5, post_title: 'BTC 週線', interaction_type: kind, from_username: '小明', count, ...extra },
});

assert.deepEqual(NS.describe(n('push', 1)), { title: '論壇', body: '小明 推了你的文章「BTC 週線」' });
assert.deepEqual(NS.describe(n('push', 3)), { title: '論壇', body: '小明 等 3 人推了你的文章「BTC 週線」' });
assert.equal(NS.describe(n('comment', 1)).body, '小明 留言了你的文章「BTC 週線」');
assert.equal(NS.describe(n('comment', 2)).body, '小明 等 2 人留言了你的文章「BTC 週線」');
assert.equal(NS.describe(n('thread_reply', 1)).body, '小明 也在「BTC 週線」留言');
assert.equal(NS.describe(n('thread_reply', 4)).body, '小明 等 4 人也在「BTC 週線」留言');

// 舊列：interaction_type 是 like／comment 但沒有 post_title
const legacy = { id: 'old', type: 'post_interaction', title: '帖子互動', body: '小明 評論了你的文章「舊文」', data: { post_id: 1, interaction_type: 'comment' } };
assert.deepEqual(NS.describe(legacy), { title: '帖子互動', body: '小明 評論了你的文章「舊文」' });

console.error('forum_notifications: ok');
