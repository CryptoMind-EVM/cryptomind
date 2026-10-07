/* site-sidebar.js — 獨立頁共用側欄（forum 7 頁 / scam-tracker 3 頁 / governance）
   設計：docs/plans/2026-08-24-site-sidebar-unification.md（DANNY Approved 2026-08-24）

   與 SPA 側欄（index.html 內建 + global-nav.js）同款體驗：品牌 / 新對話 /
   「對話歷史 | 功能選單」雙分頁 / 用戶卡。桌機 fixed 常駐 + body padding 讓位；
   手機抽屜 + 遮罩 + 漢堡（注入各頁 <nav>；無 nav 的頁 fallback 浮動漢堡）。

   為何 classic script：governance / scam-tracker 不在 Vite 多頁 build input，
   module 引用會在 prod 404；classic + Dockerfile 白名單與 click-delegator 同模式。
   版面全 inline style + design token（governance 頁沒載 tailwind-built.css），
   顏色缺 token 時有 fallback，不依賴任何 utility class。 */
(function () {
    'use strict';

    if (document.getElementById('chat-sidebar')) return; // SPA 有自己的側欄
    if (window.__siteSidebarInit) return;
    window.__siteSidebarInit = true;

    var DESKTOP_MIN = 768;
    var WIDTH = 288;
    var COLLAPSED_WIDTH = 64; // 收合成圖示列（與 SPA 側欄同一個 localStorage key：sidebarCollapsed）
    var mq = window.matchMedia('(min-width: ' + DESKTOP_MIN + 'px)');

    var els = {}; // sidebar / backdrop / hamburger / 區塊容器
    var state = { tab: 'history', user: null, userChecked: false };

    /* ---------- 小工具 ---------- */

    function t(key, fallback) {
        if (window.I18n && typeof window.I18n.t === 'function') {
            var s = window.I18n.t(key);
            if (s && s !== key) return s;
        }
        return fallback;
    }

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    // 側欄歷史面板在開頁時會被重畫三次（首次繪製、languageChanged、auth:initialized），
    // 以前每次都重抓 /api/chat/sessions（2026-09-26 量測：論壇文章頁 3 次）。
    // 同一個使用者 10 秒內共用同一份結果（論壇頁不會在這裡新增／刪除對話）；失敗不快取。
    var SESSIONS_TTL_MS = 10000;
    var sessionsCache = null;
    function fetchSessionsShared(uid) {
        if (sessionsCache && sessionsCache.uid === uid && Date.now() - sessionsCache.at < SESSIONS_TTL_MS) {
            return sessionsCache.p;
        }
        var entry = { uid: uid, at: Date.now(), p: null };
        entry.p = api('GET', '/api/chat/sessions?user_id=' + encodeURIComponent(uid));
        entry.p.catch(function () { if (sessionsCache === entry) sessionsCache = null; });
        sessionsCache = entry;
        return entry.p;
    }

    // 直接 fetch（不依賴 AppAPI——classic 頁有、forum 頁也有，但保持零依賴最穩）
    function api(method, path, body) {
        return fetch(path, {
            method: method,
            credentials: 'include',
            headers: body ? { 'Content-Type': 'application/json' } : undefined,
            body: body ? JSON.stringify(body) : undefined,
        }).then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.status === 204 ? null : r.json();
        });
    }

    // 與 SPA 側欄同一份資料（nav-config guestAllowed）；子頁拿不到唯讀市場數據開關，
    // 開關關掉時由 SPA switchTab 守門擋下
    function isGuestAllowed(item) {
        return item.id === 'chat' || item.guestAllowed === true;
    }

    function isGuest() {
        return !state.user;
    }

    /* 2026-08-24 review 修復：此前讀的 token 名（--surface/--primary/--textMuted…）
       在設計系統中不存在——實際 token 是 --color-* 色頻三聯組（"37 99 235"），
       getComputedStyle 讀不到 → 全部靜默 fallback 成硬編碼深色值，獨立頁側欄
       不跟隨主題、accent 也不是品牌藍（DESIGN_SYSTEM: #2563EB）。
       改為內嵌 var() 運算式：token 存在 → 跟隨主題；不存在 → 原 fallback 值。
       色頻 token 的半透明變體用 tokenAlpha()（rgb(channels / a)），不可串接 hex alpha。 */
    var _TOKEN_EXPR = {
        '--surface': 'var(--surface-color, rgba(20,24,33,0.96))',
        '--borderSubtle': 'var(--border-subtle-color, rgba(128,128,128,0.18))',
        '--primary': 'var(--primary-color, #0098ea)',
        '--background': 'var(--bg-color, #0d1017)',
        '--secondary': 'var(--secondary-color, #e8ecf3)',
        '--textMain': 'rgb(var(--color-text-primary, 213 218 226))',
        '--textMuted': 'rgb(var(--color-text-muted, 139 147 163))',
        // 無獨立 token：以 muted 色頻 12% 疊加呈現 highlight（=原 fallback 語意）
        '--surfaceHighlight': 'rgb(var(--color-text-muted, 128 128 128) / 0.12)',
    };

    function cssVar(name, fallback) {
        return _TOKEN_EXPR[name] || fallback;
    }

    function tokenAlpha(channelsVar, fallbackChannels, alpha) {
        return 'rgb(var(' + channelsVar + ', ' + fallbackChannels + ') / ' + alpha + ')';
    }

    /* ---------- 骨架（parse 期立即建，避免內容閃現後才讓位） ---------- */

    function buildSkeleton() {
        var aside = document.createElement('aside');
        aside.id = 'site-sidebar';
        aside.setAttribute('aria-label', 'Sidebar');
        aside.setAttribute('style', [
            'position:fixed', 'top:0', 'bottom:0', 'left:0',
            'width:' + WIDTH + 'px', 'z-index:60',
            'display:flex', 'flex-direction:column',
            'background:' + cssVarSafe('--surface', 'rgba(20,24,33,0.96)'),
            'border-right:1px solid ' + cssVarSafe('--borderSubtle', 'rgba(128,128,128,0.18)'),
            '-webkit-backdrop-filter:blur(14px)', 'backdrop-filter:blur(14px)',
            'transform:translateX(-100%)', 'transition:transform .3s ease',
            'overscroll-behavior:contain',
        ].join(';'));
        document.body.appendChild(aside);
        els.sidebar = aside;

        var backdrop = document.createElement('div');
        backdrop.id = 'site-sidebar-backdrop';
        backdrop.setAttribute('style', [
            'position:fixed', 'inset:0', 'z-index:55',
            'background:rgba(0,0,0,0.5)', '-webkit-backdrop-filter:blur(2px)',
            'backdrop-filter:blur(2px)', 'opacity:0', 'pointer-events:none',
            'transition:opacity .3s ease',
        ].join(';'));
        backdrop.addEventListener('click', closeDrawer);
        document.body.appendChild(backdrop);
        els.backdrop = backdrop;

        // 漢堡：優先注入頁面 <nav> 開頭（與既有頂欄並排）；無 nav（governance）
        // fallback 固定左上浮動鈕。
        var nav = document.querySelector('nav');
        var burger = document.createElement('button');
        burger.id = 'site-sidebar-burger';
        burger.type = 'button';
        burger.setAttribute('aria-label', t('sidebar.open', 'Menu'));
        burger.setAttribute('style', [
            'display:inline-flex', 'align-items:center', 'justify-content:center',
            'width:2.25rem', 'height:2.25rem', 'border-radius:9999px',
            'border:none', 'cursor:pointer', 'flex-shrink:0',
            'background:transparent', 'color:inherit',
        ].join(';'));
        burger.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" ' +
            'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
            'stroke-linejoin="round"><line x1="4" y1="7" x2="20" y2="7"/>' +
            '<line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="17" x2="20" y2="17"/></svg>';
        burger.addEventListener('click', function () {
            if (mq.matches) return; // 桌機側欄常駐，漢堡隱藏（updateLayout 控制）
            toggleDrawer();
        });
        if (nav && nav.firstElementChild) {
            // nav 本身不是 flex：漢堡會自己佔一行、手機頂欄變兩行高（2026-09-26 回報）。
            // 讓 nav 橫排，原本的內容容器吃掉剩餘寬度（桌機漢堡隱藏，內容容器照舊置中）
            var navInner = nav.firstElementChild;
            nav.style.display = 'flex';
            nav.style.alignItems = 'center';
            nav.style.gap = '0.25rem';
            navInner.style.flex = '1 1 0%';
            navInner.style.minWidth = '0';
            nav.insertBefore(burger, navInner);
        } else {
            burger.setAttribute('style', burger.getAttribute('style') +
                ';position:fixed;top:0.75rem;left:0.75rem;z-index:54;' +
                'background:' + cssVarSafe('--surface', 'rgba(20,24,33,0.9)') +
                ';border:1px solid rgba(128,128,128,0.25)');
            document.body.appendChild(burger);
        }
        // 選單看不到時的未讀紅點（nav-badges.js）
        burger.setAttribute('data-nav-dot-host', '');
        if (!burger.style.position) burger.style.position = 'relative';
        els.burger = burger;

        renderContent();
        updateLayout();
        mq.addEventListener('change', updateLayout);
        window.addEventListener('resize', updateLayout);
    }

    /* 圖示：頁面有載 lucide 就用跟 SPA 側欄同一套線條圖示（NAV_ITEMS.icon），
       沒有（governance）才退回文字符號。以前一律是文字符號，選單幾乎全是「•」 */
    function iconHtml(name, fallback) {
        var L = window.lucide;
        if (L && L.icons && typeof L.createElement === 'function' && name) {
            var key = String(name).replace(/(^|-)([a-z0-9])/g, function (_m, _d, c) {
                return c.toUpperCase();
            });
            if (L.icons[key]) {
                try {
                    var svg = L.createElement(L.icons[key]);
                    svg.setAttribute('width', '16');
                    svg.setAttribute('height', '16');
                    svg.setAttribute('aria-hidden', 'true');
                    return svg.outerHTML;
                } catch (_e) { /* 退回文字符號 */ }
            }
        }
        return fallback;
    }

    function cssVarSafe(name, fallback) {
        try {
            var v = cssVar(name, fallback);
            return v;
        } catch (_e) {
            return fallback;
        }
    }

    /* ---------- 內容渲染 ---------- */

    function renderContent() {
        var sb = els.sidebar;
        sb.innerHTML = '';

        // 1. 品牌列
        var brand = document.createElement('div');
        brand.className = 'ss-brand';
        brand.setAttribute('style', 'display:flex;align-items:center;justify-content:' +
            'space-between;padding:1rem 1rem 0.75rem;border-bottom:1px solid ' +
            cssVarSafe('--borderSubtle', 'rgba(128,128,128,0.18)'));
        brand.innerHTML =
            '<a href="/static/index.html" style="display:flex;align-items:center;gap:0.5rem;' +
            'text-decoration:none;color:' + cssVarSafe('--primary', '#0098ea') +
            ';letter-spacing:0.01em">' +
            '<img src="/static/img/title_icon.png" alt="CryptoMind Logo" ' +
            'style="width:1.75rem;height:1.75rem;border-radius:0.5rem;object-fit:cover">' +
            '<span class="ss-label" style="font-weight:700;font-size:1.05rem">CryptoMind</span></a>';
        // 手機關閉鈕（桌機隱藏由 updateLayout 處理 display）
        var closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.setAttribute('aria-label', 'Close');
        closeBtn.setAttribute('style', 'display:none;border:none;background:transparent;' +
            'color:inherit;cursor:pointer;padding:0.375rem;border-radius:9999px;font-size:1rem;line-height:1');
        closeBtn.textContent = '✕';
        closeBtn.addEventListener('click', closeDrawer);
        els.closeBtn = closeBtn;
        brand.appendChild(closeBtn);
        // 桌機收合鈕（手機隱藏由 updateLayout 處理 display）
        var collapseBtn = document.createElement('button');
        collapseBtn.type = 'button';
        collapseBtn.setAttribute('style', 'display:none;border:none;background:transparent;color:' +
            cssVarSafe('--textMuted', '#8b93a3') + ';cursor:pointer;padding:0.375rem;border-radius:0.5rem;line-height:1');
        collapseBtn.addEventListener('click', function () { setCollapsed(!isCollapsed()); });
        els.collapseBtn = collapseBtn;
        brand.appendChild(collapseBtn);
        syncCollapseBtn();
        sb.appendChild(brand);

        // 2. 新對話
        var newWrap = document.createElement('div');
        newWrap.className = 'ss-pad';
        newWrap.setAttribute('style', 'padding:1rem');
        var newBtn = document.createElement('button');
        newBtn.type = 'button';
        newBtn.title = t('sidebar.newChat', 'New Chat');
        newBtn.innerHTML = '＋<span class="ss-label"> ' + esc(t('sidebar.newChat', 'New Chat')) + '</span>';
        newBtn.setAttribute('style', [
            'width:100%', 'padding:0.7rem 0', 'border-radius:0.75rem', 'cursor:pointer',
            'font-weight:700', 'font-size:0.85rem',
            'background:' + tokenAlpha('--color-primary', '0 152 234', 0.1),
            'border:1px solid ' + tokenAlpha('--color-primary', '0 152 234', 0.2),
            'color:' + cssVarSafe('--primary', '#0098ea'),
        ].join(';'));
        newBtn.addEventListener('click', function () {
            // 跨頁「新對話」：以 sessionStorage 旗標通知 SPA 起始即歡迎畫面
            // （cmStartNewChat）。不可用 POST current-session null——API 的
            // ownership 檢查會 403、指標清不掉，SPA 會從伺服器指標還原舊對話。
            try { sessionStorage.setItem('cmStartNewChat', '1'); } catch (_e) {}
            try { localStorage.removeItem('chat_last_session_id'); } catch (_e) {}
            window.location.href = '/static/index.html#chat';
        });
        newWrap.appendChild(newBtn);
        sb.appendChild(newWrap);

        // 3. 雙分頁 tab（與 SPA 同一個 localStorage key，跨頁一致）
        var tabBar = document.createElement('div');
        tabBar.className = 'ss-tabs ss-pad';
        tabBar.setAttribute('style', 'padding:0 1rem 0.75rem');
        tabBar.setAttribute('role', 'tablist');
        var tabRow = document.createElement('div');
        tabRow.setAttribute('style', 'display:grid;grid-template-columns:1fr 1fr;gap:0.25rem;' +
            'padding:0.25rem;border-radius:0.75rem;background:' +
            cssVarSafe('--surfaceHighlight', 'rgba(128,128,128,0.12)'));
        var tabHist = makeTabBtn('history', 'history', t('sidebar.tabHistory', 'History'));
        var tabMenu = makeTabBtn('menu', 'grid', t('sidebar.tabMenu', 'Menu'));
        tabMenu.classList.add('ss-tab-menu'); // 圖示列直接放導覽圖示，這顆多餘
        tabRow.appendChild(tabHist);
        tabRow.appendChild(tabMenu);
        tabBar.appendChild(tabRow);
        sb.appendChild(tabBar);
        els.tabHist = tabHist;
        els.tabMenu = tabMenu;

        // 4. 分頁內容區（兩個 panel，同一個 flex-1 容器輪替）
        var panel = document.createElement('div');
        panel.setAttribute('style', 'flex:1;min-height:0;display:flex;flex-direction:column;');
        var histPanel = document.createElement('div');
        histPanel.id = 'site-sidebar-history';
        var menuPanel = document.createElement('div');
        menuPanel.id = 'site-sidebar-menu';
        menuPanel.setAttribute('style', 'flex:1;min-height:0;overflow-y:auto;padding:0 0.5rem 0.75rem');
        panel.appendChild(histPanel);
        panel.appendChild(menuPanel);
        sb.appendChild(panel);
        els.histPanel = histPanel;
        els.menuPanel = menuPanel;

        // 5. footer 用戶卡
        renderFooter(sb);

        applyTab(state.tab, false);
    }

    function makeTabBtn(name, icon, label) {
        var b = document.createElement('button');
        b.type = 'button';
        b.setAttribute('role', 'tab');
        b.dataset.tabName = name;
        b.setAttribute('style', [
            'display:flex', 'align-items:center', 'justify-content:center', 'gap:0.375rem',
            'padding:0.5rem 0', 'border-radius:0.5rem', 'border:none', 'cursor:pointer',
            'font-weight:700', 'font-size:0.72rem', 'transition:all .2s',
            'font-family:inherit',
        ].join(';'));
        b.title = label;
        b.innerHTML = iconHtml(icon === 'history' ? 'history' : 'layout-grid',
            icon === 'history' ? '🕘' : '▦') + '<span class="ss-label">' + esc(label) + '</span>';
        b.addEventListener('click', function () {
            if (isCollapsed()) setCollapsed(false); // 圖示列點「對話歷史」：先展開
            applyTab(name, true);
        });
        return b;
    }

    function styleTabBtn(btn, active) {
        btn.setAttribute('aria-selected', active ? 'true' : 'false');
        btn.style.background = active ? cssVarSafe('--background', '#0d1017') : 'transparent';
        btn.style.color = active ? cssVarSafe('--secondary', '#e8ecf3') : cssVarSafe('--textMuted', '#8b93a3');
        btn.style.boxShadow = active ? '0 1px 3px rgba(0,0,0,0.25)' : 'none';
    }

    function applyTab(name, userAction) {
        state.tab = name === 'menu' ? 'menu' : 'history';
        if (userAction) {
            try { localStorage.setItem('sidebarActiveTab', state.tab); } catch (_e) {}
        }
        styleTabBtn(els.tabHist, state.tab === 'history');
        styleTabBtn(els.tabMenu, state.tab === 'menu');
        els.histPanel.style.display = state.tab === 'history' ? 'flex' : 'none';
        els.menuPanel.style.display = state.tab === 'menu' ? 'block' : 'none';
        if (state.tab === 'history') renderHistory();
        else renderMenu();
        // 收合成圖示列時導覽圖示一律要在，不管上次停在哪個分頁
        if (state.tab === 'history' && isCollapsed()) renderMenu();
    }

    /* ---------- 對話歷史 ---------- */

    function renderHistory() {
        var p = els.histPanel;
        // 直向排列：少了 flex-direction，每筆對話會被排成橫向一整排、擠成窄條（2026-09-26 回報）
        p.setAttribute('style', 'flex:1;min-height:0;overflow-y:auto;padding:0 0.5rem 0.75rem;' +
            'display:flex;flex-direction:column');
        p.innerHTML = '<div style="padding:1rem 0.5rem;color:' +
            cssVarSafe('--textMuted', '#8b93a3') + ';font-size:0.78rem;opacity:0.7">' +
            esc(t('common.loading', 'Loading...')) + '</div>';

        ensureUser().then(function () {
            if (!state.user) {
                renderGuestCta(p, 'sidebar.loginToUnlock', 'Connect wallet to unlock everything');
                return;
            }
            var uid = state.user.user_id || state.user.uid || state.user.id;
            if (!uid) { renderEmpty(p); return; }
            fetchSessionsShared(uid)
                .then(function (data) {
                    var sessions = (data && data.sessions) || [];
                    if (!sessions.length) { renderEmpty(p); return; }
                    p.innerHTML = '';
                    sessions.slice(0, 30).forEach(function (s) {
                        var item = document.createElement('button');
                        item.type = 'button';
                        item.setAttribute('style', [
                            'display:block', 'width:100%', 'text-align:left', 'cursor:pointer',
                            'padding:0.55rem 0.6rem', 'border-radius:0.75rem', 'border:none',
                            'background:transparent', 'font-family:inherit',
                            'color:' + cssVarSafe('--textMain', '#d5dae2'),
                        ].join(';'));
                        item.onmouseenter = function () {
                            item.style.background = cssVarSafe('--surfaceHighlight', 'rgba(128,128,128,0.12)');
                        };
                        item.onmouseleave = function () { item.style.background = 'transparent'; };
                        var when = (s.updated_at || s.created_at || '').slice(0, 10);
                        item.innerHTML =
                            '<div style="font-size:0.8rem;font-weight:600;overflow:hidden;' +
                            'text-overflow:ellipsis;white-space:nowrap">' +
                            esc(s.title || s.name || t('sidebar.noHistory', 'Chat')) + '</div>' +
                            (when ? '<div style="font-size:0.68rem;opacity:0.55;margin-top:0.15rem">' +
                                esc(when) + '</div>' : '');
                        item.addEventListener('click', function () {
                            // 與 SPA switchSession 同款持久化路徑，chat-init 會還原
                            try { localStorage.setItem('chat_last_session_id', s.id); } catch (_e) {}
                            api('POST', '/api/chat/current-session', { session_id: s.id }).catch(function () {});
                            window.location.href = '/static/index.html#chat';
                        });
                        p.appendChild(item);
                    });
                })
                .catch(function () { renderEmpty(p); });
        });
    }

    function renderEmpty(p) {
        p.innerHTML = '<div style="padding:1rem 0.5rem;color:' +
            cssVarSafe('--textMuted', '#8b93a3') + ';font-size:0.78rem;opacity:0.6">' +
            esc(t('sidebar.noHistory', 'No history')) + '</div>';
    }

    function renderGuestCta(p, key, fallback) {
        p.innerHTML = '';
        var a = document.createElement('a');
        a.href = '/static/index.html';
        a.setAttribute('style', [
            // 歷史面板是直向 flex——不能 flex-grow，否則會被拉成整個面板高的空框
            'flex:0 0 auto',
            'display:flex', 'align-items:center', 'justify-content:center', 'gap:0.4rem',
            'margin:0.5rem 0.25rem', 'padding:0.6rem', 'border-radius:0.75rem',
            'text-decoration:none', 'font-size:0.75rem', 'font-weight:700', 'cursor:pointer',
            'background:' + tokenAlpha('--color-primary', '0 152 234', 0.1),
            'border:1px solid ' + tokenAlpha('--color-primary', '0 152 234', 0.25),
            'color:' + cssVarSafe('--primary', '#0098ea'),
        ].join(';'));
        a.textContent = t(key, fallback);
        p.appendChild(a);
    }

    /* ---------- 功能選單 ---------- */

    function renderMenu() {
        var p = els.menuPanel;
        var guest = isGuest();
        var items;
        if (guest && window.NavPreferences && NavPreferences.getGuestItems) {
            // 訪客：與 SPA 側欄同一份固定選單、不讀偏好（2026-09-27 上市準備 design §3）
            items = NavPreferences.getGuestItems();
        } else {
            items = (window.NavPreferences && NavPreferences.getEnabledItems
                ? NavPreferences.getEnabledItems()
                : (window.NAV_ITEMS || []).filter(function (i) { return i.defaultEnabled; })
            ); // 全部啟用項（2026-09-24 起不再截成 5 個，與 SPA 側欄一致）
        }
        if (!items.length) {
            p.innerHTML = '<div style="padding:1rem 0.5rem;opacity:0.6;font-size:0.78rem;color:' +
                cssVarSafe('--textMuted', '#8b93a3') + '">' +
                esc(t('sidebar.noHistory', 'No items')) + '</div>';
            return;
        }
        p.innerHTML = '';

        var head = document.createElement('div');
        head.setAttribute('style', 'padding:0.35rem 0.6rem 0.5rem;font-size:0.62rem;' +
            'font-weight:800;letter-spacing:0.08em;text-transform:uppercase;opacity:0.45;color:' +
            cssVarSafe('--textMuted', '#8b93a3'));
        head.className = 'ss-label';
        head.textContent = t('sidebar.navigation', 'Navigation');
        p.appendChild(head);

        items.forEach(function (item) {
            if (item.adminOnly && (!state.user || state.user.role !== 'admin')) return;
            var locked = guest && !isGuestAllowed(item);
            var label = (item.i18nKey && t(item.i18nKey, item.label)) || item.label || item.id;
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.setAttribute('data-nav-item', item.id); // 未讀標示（nav-badges.js）認這個
            btn.title = label; // 收合成圖示列時靠它看名稱
            btn.setAttribute('aria-label', label);
            btn.setAttribute('style', [
                'display:flex', 'align-items:center', 'gap:0.7rem', 'width:100%',
                'padding:0.6rem 0.65rem', 'border-radius:0.75rem', 'border:none',
                'cursor:pointer', 'font-family:inherit', 'font-size:0.82rem', 'font-weight:500',
                'text-align:left', 'transition:background .2s',
                'color:' + (locked
                    ? tokenAlpha('--color-text-muted', '139 147 163', 0.6)
                    : cssVarSafe('--textMuted', '#8b93a3')),
                'background:transparent',
            ].join(';'));
            btn.onmouseenter = function () {
                btn.style.background = cssVarSafe('--surfaceHighlight', 'rgba(128,128,128,0.12)');
            };
            btn.onmouseleave = function () { btn.style.background = 'transparent'; };
            btn.innerHTML = '<span style="width:1rem;display:inline-flex;justify-content:center;' +
                'flex-shrink:0;opacity:0.85">' +
                iconHtml(item.icon, item.icon === 'zap' ? '⚡' : item.icon === 'chart-candlestick' ? '🕯' : '•') +
                '</span><span class="ss-label" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
                esc(label) + '</span>' +
                (locked ? '<span class="ss-label" style="opacity:0.7;display:inline-flex">' + iconHtml('lock', '🔒') + '</span>' : '');
            btn.addEventListener('click', function () {
                window.location.href = '/static/index.html#' + item.id;
                closeDrawer();
            });
            p.appendChild(btn);
        });
        if (window.NavBadges) window.NavBadges.paint(); // 重畫把標示洗掉了，用手上的數字補回去

        // 訪客沒有偏好可編（「更多分頁」導向的設定頁要登入），其他市場在 SPA 側欄的更多分頁
        if (guest) return;

        // 「更多分頁」不在子頁做 popover——導向 SPA 設定（偏好編輯的家）
        var more = document.createElement('button');
        more.type = 'button';
        more.className = 'ss-more';
        more.setAttribute('style', [
            'display:flex', 'align-items:center', 'gap:0.7rem', 'width:100%',
            'padding:0.6rem 0.65rem', 'border-radius:0.75rem', 'border:none',
            'cursor:pointer', 'font-family:inherit', 'font-size:0.82rem', 'font-weight:500',
            'text-align:left', 'background:transparent',
            'color:' + cssVarSafe('--textMuted', '#8b93a3'),
        ].join(';'));
        more.onmouseenter = function () {
            more.style.background = cssVarSafe('--surfaceHighlight', 'rgba(128,128,128,0.12)');
        };
        more.onmouseleave = function () { more.style.background = 'transparent'; };
        more.innerHTML = '<span style="width:1rem;display:inline-flex;justify-content:center;flex-shrink:0">' +
            iconHtml('layout-grid', '▦') + '</span>' +
            '<span style="flex:1">' + esc(t('sidebar.moreTabs', 'More tabs')) + '</span>';
        more.addEventListener('click', function () {
            window.location.href = '/static/index.html#settings';
            closeDrawer();
        });
        p.appendChild(more);
    }

    /* ---------- footer 用戶卡 ---------- */

    function renderFooter(sb) {
        var foot = document.createElement('div');
        foot.className = 'ss-foot';
        foot.setAttribute('style', 'padding:0.9rem 1rem calc(0.9rem + env(safe-area-inset-bottom,0px));' +
            'display:flex;align-items:center;gap:0.5rem;' +
            'border-top:1px solid ' + cssVarSafe('--borderSubtle', 'rgba(128,128,128,0.18)'));
        var card = document.createElement('a');
        card.href = '/static/index.html#settings';
        card.title = t('sidebar.settings', 'Settings');
        card.setAttribute('style', 'flex:1;min-width:0;display:flex;align-items:center;gap:0.7rem;' +
            'padding:0.55rem;border-radius:0.75rem;text-decoration:none;color:inherit');
        card.onmouseenter = function () {
            card.style.background = cssVarSafe('--surfaceHighlight', 'rgba(128,128,128,0.12)');
        };
        card.onmouseleave = function () { card.style.background = 'transparent'; };
        els.userCard = card;
        foot.appendChild(card);
        // 語系切換：與 SPA 側欄底部同一個元件（下拉往上開）。以前子頁沒有，發文時切不了語言
        //（2026-09-26 回報）。只建一次、重畫時搬回來，避免重複綁事件；頁面沒載入元件就不顯示。
        // 不用 .lang-switcher-container——GlobalNav.initLanguageSwitcher 會掃那個 class 再建一次
        if (!els.langBox && window.LanguageSwitcher) {
            els.langBox = document.createElement('div');
            els.langBox.className = 'site-sidebar-lang';
            els.langBox.style.flexShrink = '0';
            try { new window.LanguageSwitcher(els.langBox); } catch (_e) { els.langBox = null; }
        }
        if (els.langBox) foot.appendChild(els.langBox);
        sb.appendChild(foot);
        renderUserCard();
    }

    function renderUserCard() {
        var card = els.userCard;
        if (!card) return;
        var name = state.user
            ? (state.user.display_name || state.user.username || state.user.user_id || 'User')
            : t('sidebar.loginToUnlock', 'Connect wallet to unlock everything');
        // 與 SPA 側欄（auth.js #sidebar-user-avatar）一致：登入＝名字首字母＋主色漸層，訪客＝人形圖示。
        // 以前不分登入與否都畫人形 emoji，登入後看起來像沒登入（2026-09-26 回報）
        var avatar = state.user
            ? 'background:linear-gradient(135deg,rgb(var(--color-primary, 37 99 235)),' +
              'rgb(var(--color-accent, 29 78 216)));color:rgb(var(--color-background, 20 22 31))">' +
              esc(String(name).charAt(0).toUpperCase())
            : 'background:' + cssVarSafe('--surfaceHighlight', 'rgba(128,128,128,0.15)') + ';color:' +
              cssVarSafe('--textMuted', '#8b93a3') + '">' +
              '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
              'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
              '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
        card.innerHTML =
            '<span style="width:2rem;height:2rem;border-radius:9999px;flex-shrink:0;display:flex;' +
            'align-items:center;justify-content:center;font-size:0.8rem;font-weight:800;' +
            avatar + '</span>' +
            '<span class="ss-label" style="flex:1;min-width:0"><span style="display:block;font-size:0.8rem;' +
            'font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:' +
            cssVarSafe('--textMain', '#d5dae2') + '">' + esc(name) + '</span>' +
            '<span style="display:block;font-size:0.65rem;opacity:0.55">' +
            esc(t('sidebar.settings', 'Settings')) + '</span></span>';
    }

    /* ---------- 使用者判定（AuthManager 優先，否則 /api/user/me） ---------- */

    // auth:initialized／auth-success 時 AuthManager 已經有最終結果：直接用它，訪客也不必再打
    // /api/user/me（以前這裡清掉狀態重跑 ensureUser，訪客每頁多一次 401——2026-09-26 量測）。
    // 沒有 AuthManager 的頁面才自己問後端。
    function syncUserFromAuth() {
        var am = window.AuthManager;
        if (am && typeof am.isLoggedIn === 'function') {
            state.user = am.isLoggedIn() && am.currentUser ? am.currentUser : null;
            state.userChecked = true;
            renderUserCard();
            return Promise.resolve(state.user);
        }
        state.userChecked = false;
        state.user = null;
        return ensureUser();
    }

    // 沒有 AuthManager 的頁面（governance）nav-badges.js 不知道有沒有登入：問完 /api/user/me 後告知
    function tellNavBadges() {
        if (window.NavBadges && window.NavBadges.setSessionHint) window.NavBadges.setSessionHint(!!state.user);
    }

    function ensureUser() {
        if (state.userChecked) return Promise.resolve(state.user);
        state.userChecked = true;
        var am = window.AuthManager;
        if (am && typeof am.isLoggedIn === 'function' && am.isLoggedIn() && am.currentUser) {
            state.user = am.currentUser;
            return Promise.resolve(state.user);
        }
        return api('GET', '/api/user/me')
            .then(function (data) {
                state.user = (data && (data.user || data)) || null;
                if (!state.user || (!state.user.user_id && !state.user.uid && !state.user.id)) {
                    state.user = null; // 401/形狀不對 → 訪客
                }
                renderUserCard();
                tellNavBadges();
                return state.user;
            })
            .catch(function () {
                state.user = null;
                renderUserCard();
                tellNavBadges();
                return null;
            });
    }

    /* ---------- 桌機收合成圖示列（Teams 式；early-init.js 在第一次繪製前套 html class） ---------- */

    function isCollapsed() {
        return document.documentElement.classList.contains('sidebar-collapsed');
    }

    function syncCollapseBtn() {
        var b = els.collapseBtn;
        if (!b) return;
        var collapsed = isCollapsed();
        var label = collapsed ? t('sidebar.expand', 'Expand sidebar') : t('sidebar.collapse', 'Collapse sidebar');
        b.title = label;
        b.setAttribute('aria-label', label);
        b.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        b.innerHTML = collapsed ? iconHtml('panel-left-open', '»') : iconHtml('panel-left-close', '«');
    }

    function setCollapsed(collapsed) {
        document.documentElement.classList.toggle('sidebar-collapsed', collapsed);
        try { localStorage.setItem('sidebarCollapsed', collapsed ? '1' : '0'); } catch (_e) {}
        syncCollapseBtn();
        if (collapsed && els.menuPanel) renderMenu(); // 圖示列要有導覽圖示
        updateLayout();
    }

    /* ---------- 桌機讓位 / 手機抽屜 ---------- */

    var lastDesktop = null;

    function updateLayout() {
        var desktop = mq.matches;
        if (desktop) {
            els.sidebar.style.transform = 'translateX(0)';
            els.backdrop.style.opacity = '0';
            els.backdrop.style.pointerEvents = 'none';
            els.burger.style.display = 'none';
            if (els.closeBtn) els.closeBtn.style.display = 'none';
            if (els.collapseBtn) els.collapseBtn.style.display = 'inline-flex';
            var w = isCollapsed() ? COLLAPSED_WIDTH : WIDTH;
            els.sidebar.style.width = w + 'px';
            document.body.style.paddingLeft = w + 'px';
        } else {
            els.sidebar.style.width = WIDTH + 'px'; // 手機抽屜維持全寬
            if (els.collapseBtn) els.collapseBtn.style.display = 'none';
            document.body.style.paddingLeft = '';
            els.burger.style.display = 'inline-flex';
            if (els.closeBtn) els.closeBtn.style.display = 'block';
            // 只在第一次或剛從桌機切到手機時收起抽屜。手機的 resize 很頻繁（網址列收合、
            // 鍵盤彈出），以前每次都關，抽屜一打開就自己縮回去（2026-09-26 回報）
            if (lastDesktop !== false) closeDrawer();
        }
        lastDesktop = desktop;
    }

    function drawerOpen() {
        return els.sidebar.style.transform === 'translateX(0)' && !mq.matches;
    }

    function toggleDrawer() {
        if (drawerOpen()) closeDrawer();
        else openDrawer();
    }

    function openDrawer() {
        if (mq.matches) return;
        els.sidebar.style.transform = 'translateX(0)';
        els.backdrop.style.opacity = '1';
        els.backdrop.style.pointerEvents = 'auto';
        // 抽屜打開時刷新當前分頁（session 清單可能已變）
        applyTab(state.tab, false);
    }

    function closeDrawer() {
        if (mq.matches) return;
        els.sidebar.style.transform = 'translateX(-100%)';
        els.backdrop.style.opacity = '0';
        els.backdrop.style.pointerEvents = 'none';
    }

    /* ---------- 啟動 ---------- */

    function boot() {
        try {
            state.tab = localStorage.getItem('sidebarActiveTab') === 'menu' ? 'menu' : 'history';
        } catch (_e) { /* 預設 history */ }
        buildSkeleton();

        // i18n / auth 為 module（deferred）：DOMContentLoaded 後才就緒；初始化完成
        // 會發 languageChanged / auth:initialized，屆時重渲染文字與使用者態。
        window.addEventListener('languageChanged', function () {
            renderContent();
            updateLayout();
        });
        window.addEventListener('auth:initialized', function () {
            syncUserFromAuth().then(function () { renderContent(); });
        });
        window.addEventListener('auth-success', function () {
            syncUserFromAuth().then(function () { renderContent(); });
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') closeDrawer();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
