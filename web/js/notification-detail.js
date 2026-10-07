// ========================================
// notification-detail.js - 點通知打開全文（2026-09-29）
// ========================================
// 以前只有公告會開全文；早報、價格提醒、錢包監控點下去只是關掉面板，列表又只顯示兩行，
// 等於內容看不到。現在沒有專屬去處的通知一律開全文，能去相關頁面的再給一顆按鈕。

export const NOTIFICATION_ICONS = {
    membership_expiring: 'crown',
    friend_request: 'user-plus',
    friend_accepted: 'user-check',
    message: 'message-circle',
    group_invite: 'users',
    group_dissolved: 'users',
    group_message: 'users',
    group_removed: 'user-minus',
    post_interaction: 'heart',
    system_update: 'refresh-cw',
    announcement: 'megaphone',
    system: 'megaphone',
    daily_brief: 'sunrise',
    price_alert: 'bell-ring',
    wallet_alert: 'wallet',
};

export const NOTIFICATION_COLORS = {
    membership_expiring: 'text-accent',
    friend_request: 'text-primary',
    friend_accepted: 'text-success',
    message: 'text-accent',
    group_invite: 'text-primary',
    group_dissolved: 'text-textMuted',
    group_message: 'text-accent',
    group_removed: 'text-textMuted',
    post_interaction: 'text-danger',
    system_update: 'text-success',
    announcement: 'text-secondary',
    system: 'text-secondary',
    daily_brief: 'text-accent',
    price_alert: 'text-primary',
    wallet_alert: 'text-primary',
};

const KIND_LABELS = {
    announcement: 'notifications.announcement',
    system: 'notifications.announcement',
    daily_brief: 'notification.kind.dailyBrief',
    price_alert: 'notification.kind.priceAlert',
    wallet_alert: 'notification.kind.walletAlert',
};

// 價格提醒的 market（後端 price_alerts.VALID_MARKETS）→ 前端分頁；A 股的分頁叫 astock
const MARKET_TABS = {
    crypto: 'crypto',
    tw_stock: 'twstock',
    us_stock: 'usstock',
    hk_stock: 'hkstock',
    jp_stock: 'jpstock',
    kr_stock: 'krstock',
    cn_stock: 'astock',
    in_stock: 'instock',
    commodity: 'commodity',
    forex: 'forex',
};

/** 全文視窗底下那顆「去相關頁面」的按鈕；沒有就回 null */
export function detailAction(notification) {
    switch (notification?.type) {
        case 'daily_brief':
            return { labelKey: 'notification.action.briefSettings', tab: 'settings', scrollTo: 'settings-brief-card' };
        case 'price_alert': {
            const tab = MARKET_TABS[notification.data?.market];
            // 旗標關閉的分頁（加密貨幣）進不去，不放一顆點了沒反應的按鈕
            const item = (window.NAV_ITEMS || []).find((i) => i.id === tab);
            if (item && window.NavPreferences && window.NavPreferences.isUnavailable(item)) return null;
            return tab ? { labelKey: 'notification.action.openMarket', tab } : null;
        }
        case 'wallet_alert':
            return { labelKey: 'notification.action.walletMonitor', tab: 'wallet-monitor' };
        default:
            return null;
    }
}

const BRIEF_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** Base App 早報推播點開是 /?brief=YYYY-MM-DD（core/daily_brief/send.py send_baseapp） */
export function briefDateFromSearch(search) {
    const value = new URLSearchParams(search || '').get('brief');
    return value && BRIEF_DATE_RE.test(value) ? value : null;
}

export function findBrief(notifications, date) {
    return (notifications || []).find((n) => n.type === 'daily_brief' && n.data?.date === date) || null;
}

function t(key, vars) {
    return window.I18n ? window.I18n.t(key, vars) : key;
}

/** 早報的標題就是「每日早報」，跟視窗標頭重複；改顯示是哪一天的（同一天兩份時才分得出來） */
function briefHeading(notification, fallback) {
    const date = notification.data?.date;
    if (!BRIEF_DATE_RE.test(date || '')) return fallback;
    const [y, m, d] = date.split('-').map(Number);
    try {
        return new Intl.DateTimeFormat(window.I18n?.getLanguage?.() || undefined, {
            year: 'numeric',
            month: 'long',
            day: 'numeric',
            weekday: 'short',
        }).format(new Date(y, m - 1, d));
    } catch {
        return date;
    }
}

function go(action) {
    if (typeof window.switchTab !== 'function') return;
    window.switchTab(action.tab);
    if (action.scrollTo) {
        // 分頁切過去後內容才渲染（同 onboarding-checklist 捲到早報卡的做法）
        setTimeout(() => {
            document.getElementById(action.scrollTo)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }, 400);
    }
}

// 同時只會有一個全文視窗；換開另一則時要把前一個的 keydown 監聽一起拿掉
let closeActive = null;

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
}

/** 全文視窗（官方樣式，不用原生 alert）；節點一律 createElement＋textContent 建，不拼 HTML 字串 */
export function showNotificationDetail(notification) {
    closeActive?.();
    document.getElementById('notif-detail-modal')?.remove();
    const service = window.NotificationService;
    const { title, body } = service ? service.describe(notification) : notification;

    const modal = el('div', 'fixed inset-0 z-[9999] flex items-center justify-center p-4');
    modal.id = 'notif-detail-modal';
    modal.dataset.type = notification.type || '';
    const backdrop = el('div', 'absolute inset-0 bg-black/60 backdrop-blur-sm');
    backdrop.id = 'notif-modal-backdrop';
    const card = el(
        'div',
        'relative w-full max-w-md bg-surface border border-borderLight rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[85dvh]'
    );
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');

    const header = el('div', 'flex items-center justify-between px-5 pt-5 pb-3 border-b border-borderSubtle shrink-0');
    const kind = el('div', 'flex items-center gap-2 min-w-0');
    const icon = el('i', `w-4 h-4 shrink-0 ${NOTIFICATION_COLORS[notification.type] || 'text-primary'}`);
    icon.setAttribute('data-lucide', NOTIFICATION_ICONS[notification.type] || 'bell');
    kind.append(icon, el('span', 'text-sm font-bold text-primary truncate', t(KIND_LABELS[notification.type] || 'notification.title')));
    const closeBtn = el(
        'button',
        'w-7 h-7 flex items-center justify-center rounded-full hover:bg-surfaceHighlight transition text-textMuted shrink-0'
    );
    closeBtn.id = 'notif-modal-close';
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', t('common.close'));
    const x = el('i', 'w-4 h-4');
    x.setAttribute('data-lucide', 'x');
    closeBtn.append(x);
    header.append(kind, closeBtn);

    const content = el('div', 'px-5 py-4 overflow-y-auto min-h-0');
    const heading = notification.type === 'daily_brief' ? briefHeading(notification, title) : title;
    content.append(el('h3', 'text-base font-semibold text-secondary mb-3 break-words', heading || ''));
    const text = el('p', 'text-sm text-textMuted leading-relaxed whitespace-pre-wrap break-words', body || '');
    text.id = 'notif-modal-body';
    content.append(text);

    const footer = el('div', 'px-5 pb-4 pt-2 flex items-center justify-between gap-3 shrink-0');
    footer.append(el('span', 'text-[10px] text-textMuted/60', service?.formatTime(notification.created_at) || ''));

    const close = () => {
        modal.remove();
        document.removeEventListener('keydown', onKey);
        if (closeActive === close) closeActive = null;
    };
    const onKey = (e) => {
        if (e.key === 'Escape') close();
    };

    const action = detailAction(notification);
    if (action && typeof window.switchTab === 'function') {
        const btn = el(
            'button',
            'text-xs px-3 py-1.5 bg-primary/10 hover:bg-primary/20 text-primary rounded-lg transition shrink-0 whitespace-nowrap',
            t(action.labelKey)
        );
        btn.id = 'notif-modal-action';
        btn.type = 'button';
        btn.addEventListener('click', () => {
            close();
            go(action);
        });
        footer.append(btn);
    }

    card.append(header, content, footer);
    modal.append(backdrop, card);
    document.body.appendChild(modal);
    if (window.lucide) window.lucide.createIcons({ nodes: [modal] });

    closeBtn.addEventListener('click', close);
    backdrop.addEventListener('click', close);
    document.addEventListener('keydown', onKey);
    closeActive = close;
    return modal;
}

const EXTRA_PAGE_SIZE = 100;
const MAX_EXTRA_PAGES = 3;

/**
 * 登入後第一次拿到通知清單時呼叫：網址有 ?brief= 就打開那份早報並標已讀。
 * 參數用完就從網址拿掉（重新整理不會再跳一次；await 之前就拿掉，同時進來的第二次 fetch 不會重複處理）。
 * 清單只有最新 50 則：推播晚點才點開、中間又來很多通知時，往後翻幾頁找（有上限）。找不到講一聲。
 */
export async function consumeBriefDeepLink(notifications, { hasMore = false, fetchPage = null } = {}) {
    const params = new URLSearchParams(window.location.search);
    if (!params.has('brief')) return false;
    const date = briefDateFromSearch(window.location.search);
    params.delete('brief');
    const query = params.toString();
    window.history.replaceState(
        window.history.state,
        '',
        `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`
    );
    let brief = date ? findBrief(notifications, date) : null;
    let offset = notifications.length;
    for (let i = 0; date && !brief && hasMore && fetchPage && i < MAX_EXTRA_PAGES; i++) {
        let page;
        try {
            page = await fetchPage(offset, EXTRA_PAGE_SIZE);
        } catch {
            break;
        }
        brief = findBrief(page, date);
        offset += page.length;
        hasMore = page.length >= EXTRA_PAGE_SIZE;
    }
    if (!brief) {
        if (typeof window.showToast === 'function') window.showToast(t('notification.briefNotFound'), 'info');
        return false;
    }
    showNotificationDetail(brief);
    // 翻頁才找到的不在本機清單裡，markAsRead 會略過（本來就不在鈴鐺列表、不影響未讀數）
    window.NotificationService?.markAsRead(brief.id);
    return true;
}
