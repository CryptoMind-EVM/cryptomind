// Email 早報「寄確認信」按鈕：信箱已驗證（active）且輸入框還是同一個信箱時反灰、顯示「已驗證」，
// 點了或按 Enter 都不送；改填別的信箱才恢復（換信箱要重新驗證）。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

function makeEl(extra = {}) {
    const classes = new Set();
    const attrs = {};
    const listeners = {};
    return {
        value: '',
        disabled: false,
        textContent: '',
        className: '',
        dataset: {},
        classList: {
            add: (c) => classes.add(c),
            remove: (c) => classes.delete(c),
            contains: (c) => classes.has(c),
            toggle: (c, force) => {
                const on = force === undefined ? !classes.has(c) : !!force;
                if (on) classes.add(c);
                else classes.delete(c);
                return on;
            },
        },
        setAttribute: (k, v) => (attrs[k] = String(v)),
        getAttribute: (k) => (k in attrs ? attrs[k] : null),
        addEventListener: (type, fn) => (listeners[type] ||= []).push(fn),
        fire: (type) => (listeners[type] || []).forEach((fn) => fn()),
        checkValidity: () => true,
        ...extra,
    };
}

const label = makeEl();
label.setAttribute('data-i18n', 'settings.emailBrief.send');
const els = {
    'brief-email-section': makeEl(),
    'brief-email-input': makeEl(),
    'brief-email-send': makeEl({ querySelector: () => label }),
    'brief-email-status': makeEl(),
    'brief-email-remove': makeEl(),
    'brief-channel-email-row': makeEl(),
    'brief-channel-email': makeEl(),
};
globalThis.window = globalThis;
globalThis.document = { getElementById: (id) => els[id] || null };
const TEXT = {
    'settings.emailBrief.send': '寄確認信',
    'settings.emailBrief.verified': '✓ 已驗證',
};
globalThis.I18n = { t: (key) => TEXT[key] || key, getLanguage: () => 'zh-TW' };

let subscription = { status: 'active', email: 'me@example.com' };
const puts = [];
globalThis.AppAPI = {
    getAppConfig: async () => ({ email_brief_enabled: true }),
    get: async () => ({ subscription }),
    put: async (url, body) => {
        puts.push(body);
        return { sent: true, subscription: { status: 'pending', email: body.email } };
    },
    delete: async () => ({ removed: true }),
};

const { loadEmailBrief, saveBriefEmail, removeBriefEmail } = await import(
    await loadModuleUrl('web/js/email-brief-settings.js')
);
const btn = els['brief-email-send'];
const input = els['brief-email-input'];

// ---- 已驗證：反灰＋「已驗證」 ----
await loadEmailBrief();
assert.equal(input.value, 'me@example.com', '已驗證的信箱帶進輸入框');
assert.equal(btn.disabled, true, '已驗證的同一個信箱：按鈕停用');
assert.ok(btn.classList.contains('opacity-60'), '停用要看得出來（反灰）');
assert.equal(label.getAttribute('data-i18n'), 'settings.emailBrief.verified');
assert.equal(label.textContent, '✓ 已驗證');

// 停用中點按鈕／按 Enter 都不送（後端 3/hour 限流不該被白白吃掉）
await saveBriefEmail();
assert.equal(puts.length, 0, '已驗證的同一個信箱不再送出');

// ---- 改填別的信箱：恢復「寄確認信」 ----
input.value = 'new@example.com';
input.fire('input');
assert.equal(btn.disabled, false, '換信箱要能重新驗證');
assert.ok(!btn.classList.contains('opacity-60'));
assert.equal(label.getAttribute('data-i18n'), 'settings.emailBrief.send');
assert.equal(label.textContent, '寄確認信');

// 改回原信箱（大小寫、空白不同也算同一個）：又反灰
input.value = '  ME@Example.com ';
input.fire('input');
assert.equal(btn.disabled, true, '改回已驗證的信箱要再停用');

// ---- 送出新信箱 → pending：按鈕可用（可以重寄） ----
input.value = 'new@example.com';
input.fire('input');
await saveBriefEmail();
assert.equal(puts.length, 1);
assert.equal(puts[0].email, 'new@example.com');
assert.equal(btn.disabled, false, '待確認（pending）時可以重寄');

// ---- 重新載入不重複綁 input 監聽 ----
subscription = { status: 'active', email: 'new@example.com' };
await loadEmailBrief();
assert.equal(btn.disabled, true);
assert.equal(input.dataset.emailBriefBound, '1');

// ---- 移除信箱：回到「寄確認信」 ----
await removeBriefEmail();
assert.equal(input.value, '');
assert.equal(btn.disabled, false, '移除後按鈕恢復');
assert.equal(label.getAttribute('data-i18n'), 'settings.emailBrief.send');

console.log('email_brief_verified ok');
