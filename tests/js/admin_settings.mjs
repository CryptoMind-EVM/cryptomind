// 後台設定中心（web/js/admin-settings.js）：用範例資料實際組一次畫面。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const els = {};
const el = () => ({ innerHTML: '' });
for (const id of [
    'admin-subpage-content',
    'settings-center-flags',
    'settings-center-params',
    'settings-center-services',
]) {
    els[id] = el();
}
globalThis.window = globalThis;
globalThis.document = { getElementById: (id) => els[id] || null };
globalThis.I18n = { t: (k) => `T:${k}` };

let fail = false;
const calls = [];
let confirmAnswer = true;
globalThis.showConfirmDialog = async () => confirmAnswer;
globalThis.showToast = () => {};
globalThis.AppAPI = {
    put: async (url, body) => calls.push(['PUT', url, body]),
    delete: async (url) => calls.push(['DELETE', url]),
    get: async (url) => {
        assert.equal(url, '/api/admin/settings-center');
        if (fail) throw new Error('boom');
        return {
            flags: [
                {
                    name: 'EMAIL_BRIEF_ENABLED',
                    group: '產品功能',
                    description: 'Email 早報',
                    default: 'on',
                    env: null,
                    value: true,
                    effective: false,
                    unmet: ['缺環境變數 RESEND_API_KEY'],
                    services: { api: true },
                    consistent: true,
                    overridable: true,
                    override: null,
                },
                {
                    name: 'VISION_ENABLED',
                    group: '產品功能',
                    description: '圖片分析 <b>',
                    default: 'on',
                    env: 'true',
                    value: true,
                    effective: true,
                    unmet: [],
                    services: { api: true, 'analysis-worker': false },
                    consistent: false,
                    overridable: true,
                    override: 'true',
                    override_by: 'admin1',
                    override_at: '2026-09-27T07:00:00+00:00',
                },
                {
                    name: 'MCP_ENABLED',
                    group: 'Agent 行為',
                    description: 'MCP',
                    default: 'off',
                    env: null,
                    value: false,
                    effective: false,
                    unmet: [],
                    services: {},
                    consistent: true,
                    overridable: false,
                    override: null,
                },
            ],
            params: [
                { name: 'FREE_DAILY_CHAT_LIMIT', group: '額度', description: '免費會員每日 AI 對話', unit: '次／天', value: 20, env_key: 'FREE_DAILY_CHAT_LIMIT', env: '20', overridable: true, override: null, bounds: [0, 10000] },
                { name: 'FORUM_TIP_DEFAULT_USD', group: '價格', description: '打賞預設', unit: 'USD', value: 1, env_key: null, env: null, overridable: false, override: null },
            ],
            services: { api: '2026-09-27T07:00:00+00:00' },
        };
    },
};

const { AdminSettingsCenter } = await import(await loadModuleUrl('web/js/admin-settings.js'));
AdminSettingsCenter.render();
await new Promise((r) => setTimeout(r, 0));

const flags = els['settings-center-flags'].innerHTML;
assert.ok(flags.includes('EMAIL_BRIEF_ENABLED'));
assert.ok(flags.includes('T:admin.settingsCenter.notEffective'), '開了但缺設定 → 未生效');
assert.ok(flags.includes('缺環境變數 RESEND_API_KEY'));
assert.ok(flags.includes('T:admin.settingsCenter.inconsistent'), '各服務不一致要標出來');
assert.ok(flags.includes('analysis-worker=off'));
assert.ok(flags.includes('T:admin.settingsCenter.off'));
assert.ok(flags.includes('圖片分析 &lt;b&gt;'), '說明要跳脫');
assert.ok(!flags.includes('<b>'));
assert.equal((flags.match(/產品功能/g) || []).length, 1, '同組只出一張卡');

const params = els['settings-center-params'].innerHTML;
assert.ok(params.includes('20') && params.includes('次／天'));
assert.ok(params.includes('T:admin.settingsCenter.sourceCode'), '沒有環境變數的顯示「寫在程式裡」');
assert.ok(els['settings-center-services'].innerHTML.includes('api'));

// 載入失敗：顯示錯誤、不丟例外
fail = true;
await AdminSettingsCenter.load();
assert.ok(els['settings-center-flags'].innerHTML.includes('text-danger'));

// ---- 控制項：可覆寫的有開／關／用預設；其他顯示「改環境變數」 ----
assert.ok(flags.includes('AdminSettingsCenter.setOverride'), '可覆寫的旗標要有按鈕');
assert.ok(flags.includes('T:admin.settingsCenter.sourceOverride'), '有覆寫要標來源');
assert.ok(flags.includes('admin1'));
assert.ok(flags.includes('T:admin.settingsCenter.envOnly'), '不可覆寫的顯示改法');
const mcpRow = flags.slice(flags.indexOf('data-flag="MCP_ENABLED"'));
assert.ok(!mcpRow.includes('AdminSettingsCenter.setOverride'), 'MCP 不能在後台切');
assert.ok(params.includes('settings-param-FREE_DAILY_CHAT_LIMIT'), '可覆寫的額度要有輸入框');
assert.ok(params.includes('AdminSettingsCenter.saveParam'));

// ---- 確認後才送；取消就不送 ----
fail = false;
confirmAnswer = false;
assert.equal(await AdminSettingsCenter.setOverride('VISION_ENABLED', 'false'), false);
assert.equal(calls.length, 0, '取消確認不能送出');
confirmAnswer = true;
assert.equal(await AdminSettingsCenter.setOverride('VISION_ENABLED', 'false'), true);
assert.deepEqual(calls.at(-1), ['PUT', '/api/admin/settings-center/overrides/VISION_ENABLED', { value: 'false' }]);
assert.equal(await AdminSettingsCenter.clearOverride('VISION_ENABLED'), true);
assert.deepEqual(calls.at(-1), ['DELETE', '/api/admin/settings-center/overrides/VISION_ENABLED']);

// ---- 額度：非整數不送；整數送字串 ----
els['settings-param-FREE_DAILY_CHAT_LIMIT'] = { value: '-3' };
const before = calls.length;
assert.equal(await AdminSettingsCenter.saveParam('FREE_DAILY_CHAT_LIMIT'), false);
assert.equal(calls.length, before, '負數／小數不送');
els['settings-param-FREE_DAILY_CHAT_LIMIT'] = { value: ' 30 ' };
assert.equal(await AdminSettingsCenter.saveParam('FREE_DAILY_CHAT_LIMIT'), true);
assert.deepEqual(calls.at(-1), ['PUT', '/api/admin/settings-center/overrides/FREE_DAILY_CHAT_LIMIT', { value: '30' }]);

console.log('admin_settings ok');
