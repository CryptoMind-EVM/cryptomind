// ========================================
// email-brief-settings.js — Connections 頁的 Email 卡＋Settings 早報「送到」的 Email 列（PR-8）
// 2026-09-28：訂閱設定自 Settings 早報卡搬到 Connections（綁定集中一處）；
// Settings 只留「送到」的 Email 勾選，沒訂閱時顯示「去設定 →」。兩邊哪個在 DOM 就更新哪個。
// /api/config 的 email_brief_enabled 為 true（旗標開且寄信設定齊全）才顯示；否則整塊隱藏。
// 狀態：none／pending／expired／active／unsubscribed（GET /api/user/email-brief）。
// 按鈕走 click-delegator 的全域函式白名單：saveBriefEmail / removeBriefEmail；Enter 走 data-enter
// email 只用 textContent 寫進畫面（不走 innerHTML）。
// ========================================

const STATUS_TEXT = {
    none: [
        'settings.emailBrief.statusNone',
        "We'll send a confirmation email first. The brief starts after you click the link in it.",
    ],
    pending: [
        'settings.emailBrief.statusPending',
        'Confirmation sent to {{email}}. Click the link in it to start receiving the brief.',
    ],
    expired: [
        'settings.emailBrief.statusExpired',
        'The confirmation link sent to {{email}} has expired. Please send it again.',
    ],
    active: ['settings.emailBrief.statusActive', 'Your brief goes to {{email}}.'],
    unsubscribed: [
        'settings.emailBrief.statusUnsubscribed',
        '{{email}} is unsubscribed. Send your email again to start receiving the brief.',
    ],
};

function _t(key, fallback, params) {
    const text = window.I18n ? window.I18n.t(key, params) : '';
    if (text && text !== key) return text;
    return String(fallback).replace('{{email}}', (params && params.email) || '');
}

function _el(id) {
    return document.getElementById(id);
}

// 目前已驗證（active）的信箱；沒有就是空字串
let _activeEmail = '';

// 已驗證、且輸入框還是同一個信箱：按鈕反灰顯示「已驗證」，不再重寄確認信；
// 改填別的信箱才恢復成「寄確認信」（換信箱要重新驗證）
function _syncSendButton() {
    const btn = _el('brief-email-send');
    if (!btn) return;
    const input = _el('brief-email-input');
    const typed = ((input && input.value) || '').trim().toLowerCase();
    const verified = !!_activeEmail && typed === _activeEmail;
    btn.disabled = verified;
    btn.classList.toggle('opacity-60', verified);
    btn.classList.toggle('cursor-not-allowed', verified);
    const label = btn.querySelector('[data-i18n]');
    if (label) {
        const [key, fallback] = verified
            ? ['settings.emailBrief.verified', '✓ Verified']
            : ['settings.emailBrief.send', 'Send confirmation'];
        label.setAttribute('data-i18n', key);
        label.textContent = _t(key, fallback);
    }
}

const BADGE = {
    active: ['settings.emailBrief.badgeActive', 'Subscribed', true],
    pending: ['settings.emailBrief.badgePending', 'Awaiting confirmation', false],
    expired: ['settings.emailBrief.badgeExpired', 'Link expired', false],
    unsubscribed: ['settings.emailBrief.badgeUnsubscribed', 'Unsubscribed', false],
    none: ['settings.connections.notBound', 'Not linked', false],
};

function _setBadge(status) {
    const badge = _el('email-status-badge');
    if (!badge) return;
    const [key, fallback, ok] = BADGE[status] || BADGE.none;
    badge.className =
        'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium shrink-0 whitespace-nowrap ' +
        (ok ? 'bg-success/10 text-success' : 'bg-surfaceHighlight text-textMuted');
    badge.textContent = _t(key, fallback);
}

// Settings 早報「送到」：Email 列在功能開著時一直顯示；沒有有效訂閱就不能勾，改顯示「去設定 →」
function _syncChannelRow(enabled, status) {
    const row = _el('brief-channel-email-row');
    if (row) row.classList.toggle('hidden', !enabled);
    const active = enabled && status === 'active';
    const box = _el('brief-channel-email');
    if (box) box.disabled = !active;
    const hint = _el('brief-email-hint');
    if (hint) hint.classList.toggle('hidden', !enabled || active);
}

function _setMessage(text, tone) {
    const el = _el('brief-email-status');
    if (!el) return;
    el.textContent = text || '';
    el.className =
        'text-xs break-all ' +
        (tone === 'error' ? 'text-danger' : tone === 'ok' ? 'text-success' : 'text-textMuted');
}

function _render(subscription) {
    const status = (subscription && subscription.status) || 'none';
    const email = (subscription && subscription.email) || '';
    const [key, fallback] = STATUS_TEXT[status] || STATUS_TEXT.none;
    const tone = status === 'active' ? 'ok' : status === 'expired' ? 'error' : null;
    _setMessage(_t(key, fallback, { email }), tone);
    const input = _el('brief-email-input');
    if (input && email && !input.value) input.value = email;
    _activeEmail = status === 'active' ? email.trim().toLowerCase() : '';
    _syncSendButton();
    const remove = _el('brief-email-remove');
    if (remove) remove.classList.toggle('hidden', status === 'none');
    _setBadge(status);
    _syncChannelRow(true, status);
}

function _errorText(e) {
    const status = e && e.status;
    if (status === 422) {
        return _t('settings.emailBrief.invalid', "That doesn't look like a valid email address");
    }
    if (status === 429) {
        return _t('settings.emailBrief.tooMany', 'Too many attempts. Please try again in an hour.');
    }
    if (status === 404 || status === 503) {
        return _t('settings.emailBrief.unavailable', "The email brief isn't available right now.");
    }
    return _t(
        'settings.emailBrief.sendFailed',
        "Couldn't send the confirmation email. Please try again later."
    );
}

async function loadEmailBrief() {
    const card = _el('connections-email-card');
    if ((!card && !_el('brief-channel-email-row')) || typeof AppAPI === 'undefined') return;
    let enabled = false;
    try {
        const cfg = AppAPI.getAppConfig ? await AppAPI.getAppConfig() : null;
        enabled = !!(cfg && cfg.email_brief_enabled);
    } catch (e) {
        enabled = false;
    }
    // 訂閱綁在帳號上：沒登入不顯示 Email 卡
    if (typeof AuthManager !== 'undefined' && !AuthManager.isLoggedIn()) enabled = false;
    if (card) card.classList.toggle('hidden', !enabled);
    const input = _el('brief-email-input');
    if (input && !input.dataset.emailBriefBound) {
        input.dataset.emailBriefBound = '1';
        input.addEventListener('input', _syncSendButton);
    }
    if (!enabled) {
        _syncChannelRow(false, 'none');
        return;
    }
    try {
        const res = await AppAPI.get('/api/user/email-brief');
        _render(res && res.subscription);
    } catch (e) {
        console.warn('[EmailBrief] load failed', e);
        const badge = _el('email-status-badge');
        if (badge) badge.textContent = _t('settings.connections.loadFailed', 'Unavailable');
        _setMessage(_t('settings.emailBrief.loadFailed', "Couldn't load your email status"), 'error');
    }
}

async function saveBriefEmail() {
    const btn = _el('brief-email-send');
    if (btn && btn.disabled) return; // 已驗證的同一個信箱（Enter 鍵也會走到這裡）
    const input = _el('brief-email-input');
    const email = ((input && input.value) || '').trim();
    if (!email || (input && typeof input.checkValidity === 'function' && !input.checkValidity())) {
        _setMessage(_errorText({ status: 422 }), 'error');
        return;
    }
    _setMessage(_t('settings.emailBrief.sending', 'Sending…'));
    try {
        const language = window.I18n && window.I18n.getLanguage ? window.I18n.getLanguage() : null;
        const res = await AppAPI.put('/api/user/email-brief', { email, language });
        _render(res && res.subscription);
        if (res && res.sent) {
            _setMessage(
                _t('settings.emailBrief.sent', 'Confirmation email sent. Check your inbox.'),
                'ok'
            );
        }
    } catch (e) {
        console.warn('[EmailBrief] save failed', e);
        _setMessage(_errorText(e), 'error');
    }
}

async function removeBriefEmail() {
    try {
        await AppAPI.delete('/api/user/email-brief');
        const input = _el('brief-email-input');
        if (input) input.value = '';
        const channel = _el('brief-channel-email');
        if (channel) channel.checked = false;
        _render(null);
        _setMessage(_t('settings.emailBrief.removed', 'Email removed'), 'ok');
    } catch (e) {
        console.warn('[EmailBrief] remove failed', e);
        _setMessage(
            _t('settings.emailBrief.removeFailed', "Couldn't remove your email, please try again"),
            'error'
        );
    }
}

window.saveBriefEmail = saveBriefEmail;
window.removeBriefEmail = removeBriefEmail;
// 輸入框按 Enter：click-delegator 的 data-enter 只認「物件.方法」形式
window.EmailBriefSettings = { save: () => saveBriefEmail() };

export { loadEmailBrief, saveBriefEmail, removeBriefEmail };
