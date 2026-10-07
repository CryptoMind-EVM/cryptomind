// ========================================
// brief-settings.js — Settings 的「每日早報」卡（2026-09-12 留存核心第 1 項）
// 讀／存偏好（/api/user/brief-prefs）、預覽（/preview）。
// 按鈕走 click-delegator 的全域函式白名單：loadBriefPrefs / saveBriefPrefs / previewBrief
// ========================================

import { loadEmailBrief } from './email-brief-settings.js';
import { loadWatchlist } from './watchlist-settings.js';

const BRIEF_TIMEZONES = [
    'Asia/Taipei', 'Asia/Shanghai', 'Asia/Hong_Kong', 'Asia/Tokyo', 'Asia/Seoul',
    'Asia/Singapore', 'Europe/London', 'Europe/Moscow', 'America/New_York',
    'America/Los_Angeles', 'UTC',
];

function _t(key, fallback) {
    return (window.I18n ? window.I18n.t(key) : '') || fallback;
}

function _el(id) {
    return document.getElementById(id);
}

function _setStatus(text, tone) {
    const el = _el('brief-status');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'text-xs mt-2 ' + (tone === 'error' ? 'text-danger' : tone === 'ok' ? 'text-success' : 'text-textMuted');
}

function _fillOptions() {
    const hour = _el('brief-hour');
    if (hour && !hour.options.length) {
        for (let h = 0; h < 24; h++) {
            const opt = document.createElement('option');
            opt.value = String(h);
            opt.textContent = `${String(h).padStart(2, '0')}:00`;
            hour.appendChild(opt);
        }
    }
    const tz = _el('brief-timezone');
    if (tz && !tz.options.length) {
        BRIEF_TIMEZONES.forEach((name) => {
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            tz.appendChild(opt);
        });
    }
}

function _applyPrefs(prefs) {
    _fillOptions();
    const enabled = _el('brief-enabled');
    if (enabled) enabled.checked = !!prefs.enabled;
    const hour = _el('brief-hour');
    if (hour) hour.value = String(prefs.send_hour ?? 8);
    const tz = _el('brief-timezone');
    if (tz) {
        if (prefs.timezone && !BRIEF_TIMEZONES.includes(prefs.timezone)) {
            const opt = document.createElement('option');
            opt.value = prefs.timezone;
            opt.textContent = prefs.timezone;
            tz.appendChild(opt);
        }
        tz.value = prefs.timezone || 'Asia/Taipei';
    }
    const spend = _el('brief-include-spend');
    if (spend) spend.checked = prefs.include_spend !== false;
    const macro = _el('brief-include-macro');
    if (macro) macro.checked = prefs.include_macro !== false;
    const channels = prefs.channels || [];
    const tg = _el('brief-channel-telegram');
    if (tg) {
        tg.checked = channels.includes('telegram');
        tg.disabled = !prefs.telegram_bound;
    }
    const hint = _el('brief-telegram-hint');
    if (hint) hint.classList.toggle('hidden', !!prefs.telegram_bound);
    const inapp = _el('brief-channel-inapp');
    if (inapp) inapp.checked = channels.includes('inapp');
    // Base App／Farcaster 宿主推播：只有存到 token 的人看得到這一列
    const baseRow = _el('brief-channel-baseapp-row');
    const baseapp = _el('brief-channel-baseapp');
    if (baseRow) baseRow.classList.toggle('hidden', !prefs.baseapp_available);
    if (baseapp) baseapp.checked = channels.includes('baseapp');
    // Email（PR-8）：勾選狀態照存的偏好；這一列要不要顯示由 email-brief-settings.js 依訂閱狀態決定
    const email = _el('brief-channel-email');
    if (email) email.checked = channels.includes('email');
}

function _readForm() {
    const channels = [];
    if (_el('brief-channel-telegram')?.checked) channels.push('telegram');
    if (_el('brief-channel-inapp')?.checked) channels.push('inapp');
    if (_el('brief-channel-baseapp')?.checked) channels.push('baseapp');
    if (_el('brief-channel-email')?.checked) channels.push('email');
    return {
        enabled: !!_el('brief-enabled')?.checked,
        send_hour: Number(_el('brief-hour')?.value ?? 8),
        timezone: _el('brief-timezone')?.value || 'Asia/Taipei',
        channels: channels.length ? channels : ['inapp'],
        include_spend: !!_el('brief-include-spend')?.checked,
        include_macro: !!_el('brief-include-macro')?.checked,
    };
}

async function loadBriefPrefs() {
    const card = _el('settings-brief-card');
    if (!card || typeof AppAPI === 'undefined') return;
    if (typeof AuthManager !== 'undefined' && !AuthManager.isLoggedIn()) {
        card.classList.add('hidden');
        return;
    }
    card.classList.remove('hidden');
    _setStatus(_t('settings.brief.loading', 'Loading…'));
    try {
        const res = await AppAPI.get('/api/user/brief-prefs');
        _applyPrefs((res && res.prefs) || {});
        _setStatus('');
        loadEmailBrief();
        loadWatchlist();
    } catch (e) {
        console.warn('[BriefSettings] load failed', e);
        _applyPrefs({});
        _setStatus(_t('settings.brief.loadFailed', 'Could not load your brief settings'), 'error');
    }
}

async function saveBriefPrefs() {
    _setStatus(_t('settings.brief.saving', 'Saving…'));
    try {
        const res = await AppAPI.put('/api/user/brief-prefs', _readForm());
        if (res && res.prefs) _applyPrefs(res.prefs);
        _setStatus(_t('settings.brief.saved', 'Saved'), 'ok');
    } catch (e) {
        console.warn('[BriefSettings] save failed', e);
        _setStatus(_t('settings.brief.saveFailed', 'Save failed, please try again'), 'error');
    }
}

async function previewBrief() {
    const box = _el('brief-preview');
    if (!box) return;
    box.classList.remove('hidden');
    box.textContent = _t('settings.brief.previewLoading', 'Building today’s brief…');
    try {
        const res = await AppAPI.post('/api/user/brief-prefs/preview', {});
        if (res && res.text) {
            box.textContent = res.text;
        } else {
            box.textContent = _t(
                'settings.brief.previewEmpty',
                'Nothing to report yet. Add ledger entries, a price alert or a watchlist symbol and try again.'
            );
        }
    } catch (e) {
        console.warn('[BriefSettings] preview failed', e);
        box.textContent = _t('settings.brief.previewFailed', 'Preview failed, please try again');
    }
}

window.loadBriefPrefs = loadBriefPrefs;
window.saveBriefPrefs = saveBriefPrefs;
window.previewBrief = previewBrief;

export { loadBriefPrefs, saveBriefPrefs, previewBrief, BRIEF_TIMEZONES };
