// 後台「系統配置」（web/js/admin.js ConfigManager）：用範例資料實際組一次畫面。
// 2026-10-01：群組開關放在 features 卡、排在最後、Edit 只在滑鼠移過去才出現——DANNY 找不到。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const els = { 'config-groups': { innerHTML: '' } };
globalThis.window = globalThis;
globalThis.document = { getElementById: (id) => els[id] || null };
globalThis.AppUtils = { refreshIcons: () => {} };
globalThis.AppAPI = {
    get: async (url) => {
        assert.equal(url, '/api/admin/config/all');
        return {
            configs_by_category: {
                pricing: [{ key: 'price_premium', value: '9.99', value_type: 'float' }],
                limits: [{ key: 'limit_group_create', value: 5, value_type: 'int' }],
                scam_tracker: [{ key: 'scam_report_daily_limit_pro', value: 5, value_type: 'int' }],
                features: [{ key: 'group_chat_enabled', value: false, value_type: 'bool', description: '群組聊天功能開關' }],
            },
        };
    },
};

const { AdminPanel } = await import(await loadModuleUrl('web/js/admin.js'));
await AdminPanel.ConfigManager.loadConfigs();
const html = els['config-groups'].innerHTML;

// 功能開關卡排第一，有看得懂的標題
const first = html.indexOf('group_chat_enabled');
assert.ok(first >= 0, 'features 卡有渲染出來');
for (const key of ['price_premium', 'limit_group_create', 'scam_report_daily_limit_pro']) {
    assert.ok(first < html.indexOf(key), `功能開關要排在 ${key} 前面`);
}
assert.match(html, /Features/, 'features 卡要有標題');
assert.doesNotMatch(html, /> features\s*</, '不要露出原始分類代碼');

// Edit 按鈕手機也點得到：不能只靠 hover 才顯示
const editButtons = [...html.matchAll(/<button[^>]*ConfigManager\.startEdit[^>]*>/g)].map((m) => m[0]);
assert.equal(editButtons.length, 4, '每一列都有 Edit');
for (const b of editButtons) assert.doesNotMatch(b, /\bopacity-0\b/, 'Edit 不能預設透明');

console.log('admin_config ok');
