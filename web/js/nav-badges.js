/**
 * 功能選單的未讀標示（2026-10-02，規則在後端 core/nav_badges.py）：
 *   - 社群掛數字（私訊＋群組＋好友邀請＋群組邀請；靜音的群不算，除非有人 @ 我）
 *   - 論壇掛紅點（有人留言你的文章、也在你留言過的文章留言；推不算）
 *   - 選單看不到的入口掛紅點：手機 ☰、獨立頁漢堡、停在「對話歷史」時的「功能選單」分頁
 * 行情、錢包這類工具不掛——處處有紅點大家會乾脆全部忽略。
 *
 * 標示掛在 [data-nav-item] 按鈕（SPA 側欄 global-nav.js、獨立頁側欄 site-sidebar.js 都有標），
 * 入口紅點掛在 [data-nav-dot-host]。兩邊側欄重畫完會呼叫 paint()；資料在通知有變動時重抓
 * （新私訊、群組訊息、邀請、論壇留言、讀過清通知都會觸發 notificationsUpdated）。
 */

const state = { social: 0, forum: false };
let timer = null;
let reqSeq = 0; // 兩次重抓亂序回來時，舊的結果不能蓋掉新的
let sessionHint = null; // 沒有 AuthManager 的獨立頁（governance）：site-sidebar 問完 /api/user/me 後告知

/** 有 AuthManager 的頁面看它；沒有的頁面看 site-sidebar 的告知（沒告知之前當訪客，不打 API） */
function isLoggedIn() {
    const am = window.AuthManager;
    if (am && typeof am.isLoggedIn === 'function') return !!am.isLoggedIn();
    return sessionHint === true;
}

/** 數字徽章文字：0 不顯示、99 以上 99+ */
function badgeText(count) {
    const n = Number(count) || 0;
    if (n <= 0) return '';
    return n > 99 ? '99+' : String(n);
}

const findChild = (el, key) => Array.from(el.children || []).find((c) => c.dataset && key in c.dataset);

function paintItem(btn) {
    const id = btn.dataset.navItem;
    const want =
        id === 'friends' && state.social > 0 ? ['count', badgeText(state.social)] : id === 'forum' && state.forum ? ['dot', ''] : null;
    const cur = findChild(btn, 'navBadge');
    // 沒變就不動 DOM（切回視窗、每則通知都會重抓一次）
    if (cur && want && cur.dataset.navBadge === want[0] && (cur.textContent || '') === want[1]) return;
    cur?.remove();
    const t = (k) => (window.I18n ? window.I18n.t(k) : k);
    if (id === 'friends' && state.social > 0) {
        const el = document.createElement('span');
        el.dataset.navBadge = 'count';
        el.className =
            'nav-badge ml-auto min-w-[18px] h-[18px] px-1 rounded-full bg-danger text-background text-[10px] font-bold leading-[18px] text-center shrink-0 whitespace-nowrap';
        el.textContent = badgeText(state.social);
        el.setAttribute('aria-label', t('sidebar.unreadBadge'));
        btn.appendChild(el);
    } else if (id === 'forum' && state.forum) {
        const el = document.createElement('span');
        el.dataset.navBadge = 'dot';
        el.className = 'nav-badge nav-badge-dot ml-auto w-2 h-2 rounded-full bg-danger shrink-0';
        el.setAttribute('aria-label', t('sidebar.forumActivityBadge'));
        btn.appendChild(el);
    }
}

function paintDotHost(el, pending) {
    // 「功能選單」分頁正被選著：選單就在眼前，不用再提醒
    const show = pending && el.getAttribute('aria-selected') !== 'true';
    const cur = findChild(el, 'navDot');
    if (cur && show) return; // 已經掛著，不動 DOM
    cur?.remove();
    if (!show) return;
    const dot = document.createElement('span');
    dot.dataset.navDot = '';
    dot.className = 'nav-dot absolute top-1 right-1 w-2 h-2 rounded-full bg-danger pointer-events-none';
    el.appendChild(dot);
}

function paint() {
    const items = Array.from(document.querySelectorAll('[data-nav-item]'));
    items.forEach(paintItem);
    // 入口紅點只算選單裡真的有的項目（功能選單可自訂：沒放「社群」的人點開會找不到紅點從哪來）
    const inMenu = new Set(items.map((b) => b.dataset.navItem));
    const pending = (state.social > 0 && inMenu.has('friends')) || (state.forum && inMenu.has('forum'));
    document.querySelectorAll('[data-nav-dot-host]').forEach((el) => paintDotHost(el, pending));
}

async function refresh() {
    clearTimeout(timer);
    if (!isLoggedIn()) {
        state.social = 0;
        state.forum = false;
        paint();
        return;
    }
    const seq = ++reqSeq;
    try {
        const res = await window.AppAPI.get('/api/notifications/badges', { retries: 0 });
        if (seq !== reqSeq) return; // 已有更新的一次在路上，讓它來畫
        state.social = Number(res?.social) || 0;
        state.forum = !!res?.forum;
    } catch (e) {
        // 拿不到就維持上次的（不要因為一次網路抖動整個熄掉）
        console.debug?.('[NavBadges] refresh failed', e);
    }
    paint();
}

/** 通知一次來好幾則（合併、已讀推送）：合併成一次重抓 */
function schedule(delay = 400) {
    clearTimeout(timer);
    timer = setTimeout(refresh, delay);
}

/** 沒有 AuthManager 的頁面用：site-sidebar 確認登入狀態後告知，然後重抓 */
function setSessionHint(loggedIn) {
    sessionHint = !!loggedIn;
    schedule(0);
}

const NavBadges = { refresh, schedule, paint, state, setSessionHint };
window.NavBadges = NavBadges;

window.addEventListener('notificationsUpdated', () => schedule());
window.addEventListener('auth:initialized', () => schedule(0));
// 上一頁／下一頁從 bfcache 還原時 visibilitychange 不一定會觸發，徽章會停在離開當下的數字
window.addEventListener('pageshow', (e) => {
    if (e.persisted) schedule(0);
});
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') schedule(0);
});

export { badgeText, NavBadges };
