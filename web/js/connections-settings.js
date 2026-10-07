// ========================================
// connections-settings.js — Settings 既有「Connections」引導卡上的綁定狀態徽章
// （2026-09-12 盤點 §13：connections 分頁自導覽下架，入口就是這張卡；卡本身用
// GlobalNav.navigateToTab 進分頁，這裡只負責把 Telegram／Email／LINE／Google 狀態填上去，
// 不在 Settings 重複注入 telegram-link／line-link 的 DOM）。
// 另提供 SettingsConnections.open(target)：早報「送到」裡「去綁定 →」用，跳到 Connections 的對應卡。
// 不能用 ConnectionsTab.*——那是 connections 分頁的 lazy chunk，在 Settings 上還沒載入。
// ========================================

const _OPEN_TARGETS = new Set(['telegram', 'email', 'line', 'google']);

function _t(key, fallback) {
    return (window.I18n ? window.I18n.t(key) : '') || fallback;
}

function _badge(el, bound, label) {
    if (!el) return;
    el.textContent = label;
    el.className =
        'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs whitespace-nowrap ' +
        (bound ? 'bg-success/15 text-success' : 'bg-surfaceHighlight text-textMuted');
}

async function loadConnectionsSummary() {
    if (!document.getElementById('settings-connections-telegram') || typeof AppAPI === 'undefined') return;
    if (typeof AuthManager !== 'undefined' && !AuthManager.isLoggedIn()) return;
    const boundLabel = _t('settings.connections.bound', 'Linked');
    const notBoundLabel = _t('settings.connections.notBound', 'Not linked');
    const results = await Promise.allSettled([
        AppAPI.get('/api/telegram/status'),
        AppAPI.get('/api/line/status'),
        AppAPI.get('/api/google/status'),
        _loadEmailStatus(),
    ]);
    const tg = results[0].status === 'fulfilled' ? results[0].value || {} : null;
    const line = results[1].status === 'fulfilled' ? results[1].value || {} : null;
    const google = results[2].status === 'fulfilled' ? results[2].value || {} : null;
    const googleEl = document.getElementById('settings-connections-google');
    if (googleEl) {
        if (google && google.enabled === false) googleEl.classList.add('hidden');
        else _badge(googleEl, !!(google && google.bound),
            _t('connections.googleTitle', 'Google sign-in') + ' · ' + (google ? (google.bound ? `${boundLabel}${google.email ? ' ' + google.email : ''}` : notBoundLabel) : _t('settings.connections.loadFailed', 'Unavailable')));
    }
    _badge(
        document.getElementById('settings-connections-telegram'),
        !!(tg && tg.bound),
        'Telegram · ' + (tg ? (tg.bound ? `${boundLabel}${tg.telegram_username ? ' @' + tg.telegram_username : ''}` : notBoundLabel) : _t('settings.connections.loadFailed', 'Unavailable'))
    );
    _emailBadge(results[3]);
    _badge(
        document.getElementById('settings-connections-line'),
        !!(line && line.bound),
        'LINE · ' + (line ? (line.bound ? `${boundLabel}${line.display_name ? ' ' + line.display_name : ''}` : notBoundLabel) : _t('settings.connections.loadFailed', 'Unavailable'))
    );
}

// Email 早報沒開（旗標關或寄信設定不齊）→ null，徽章不顯示
async function _loadEmailStatus() {
    const cfg = AppAPI.getAppConfig ? await AppAPI.getAppConfig() : null;
    if (!cfg || !cfg.email_brief_enabled) return null;
    const res = await AppAPI.get('/api/user/email-brief');
    return (res && res.subscription) || { status: 'none' };
}

function _emailBadge(result) {
    const el = document.getElementById('settings-connections-email');
    if (!el) return;
    if (result.status === 'fulfilled' && !result.value) {
        el.classList.add('hidden');
        return;
    }
    const sub = result.status === 'fulfilled' ? result.value : null;
    const active = !!(sub && sub.status === 'active');
    let text;
    if (!sub) text = _t('settings.connections.loadFailed', 'Unavailable');
    else if (active) text = `${_t('settings.emailBrief.badgeActive', 'Subscribed')}${sub.email ? ' ' + sub.email : ''}`;
    else if (sub.status === 'pending') text = _t('settings.emailBrief.badgePending', 'Awaiting confirmation');
    else text = _t('settings.connections.notBound', 'Not linked');
    _badge(el, active, 'Email · ' + text);
}

// 早報「送到」的「去綁定 →」：進 Connections 分頁，等卡片出現再捲過去
// （分頁是 lazy 注入；Email 卡要等 /api/config 回來才會取消 hidden）
function openConnection(target) {
    const key = _OPEN_TARGETS.has(target) ? target : '';
    if (window.GlobalNav && typeof window.GlobalNav.navigateToTab === 'function') {
        window.GlobalNav.navigateToTab('connections');
    }
    if (!key) return;
    let tries = 0;
    const timer = setInterval(() => {
        tries += 1;
        const card = document.getElementById(`connections-${key}-card`);
        if (card && !card.classList.contains('hidden')) {
            clearInterval(timer);
            card.scrollIntoView({ behavior: 'smooth', block: 'start' });
        } else if (tries >= 20) {
            clearInterval(timer);
        }
    }, 150);
}

window.loadConnectionsSummary = loadConnectionsSummary;
window.SettingsConnections = { open: openConnection };

export { loadConnectionsSummary, openConnection };
