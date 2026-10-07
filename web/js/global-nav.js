/**
 * Global Navigation Module
 *
 * 導覽只剩左側 sidebar（手機是 ☰ 抽屜）的「功能選單」。
 * 2026-09-24 DANNY：手機底部導覽列拔除——它與抽屜功能選單完全重複，卻是貼底量測、
 * 安全區、拖拉、標籤換行等版面 bug 的最大來源。論壇頁注入的浮動 pill 一併退場
 * （論壇頁有自己的 site-sidebar 抽屜）。
 */

import { enableDragReorder } from './drag-reorder.js';

const GlobalNav = {
    navReorder: false, // 功能選單排序模式（標題列「調整順序」）
    init() {
        this._cleanupLegacyNavState();
    },

    /**
     * 舊入口：底部列拔除後改為重畫側欄功能選單。
     * FeatureMenu 存檔／重設、登入狀態變更、切換語言都還呼叫這個名稱——
     * 保留名稱讓它們自動刷新側欄（先前存檔後側欄不會更新，要重新整理才看得到）。
     */
    renderNavButtons() {
        this.renderSidebarNav();
    },

    /**
     * Navigate to a main app tab
     * @param {string} tabId - The tab ID to navigate to
     */
    navigateToTab(tabId) {
        // Check if we're in the main SPA and switchTab function exists
        if (typeof switchTab === 'function') {
            // We're in the main app, use SPA navigation
            switchTab(tabId);
        } else {
            // Save current page info for potential return
            const currentPage = document.body.dataset.page;
            if (currentPage) {
                sessionStorage.setItem('lastForumPage', currentPage);
            }
            // Fallback: navigate to main app with tab
            window.location.href = `/static/index.html#${tabId}`;
        }
    },

    /**
     * Navigate to forum
     */
    navigateToForum() {
        // Check if we're in the main SPA
        if (typeof switchTab === 'function') {
            // We're in the main app, use SPA navigation
            switchTab('forum');
        } else {
            // Fallback: navigate to main app with forum hash
            window.location.href = '/static/index.html#forum';
        }
    },

    /* ============ Sidebar 導覽（2026-08-20，docs/plans/2026-08-20-sidebar-nav-design.md） ============
       桌機主要導覽併入既有左側 sidebar（ChatGPT/Discord 模式）。2026-09-24 起手機
       也只有這一個入口（☰ 抽屜），底部導覽列拔除。偏好（至少 3 個）沿用 NavPreferences。
       2026-08-23：導覽區與對話歷史改為雙 tab 切換（setSidebarTab），
       不再上下堆疊——各佔全高（DANNY 回饋：空間不足擠在一起太醜）。 */

    // 側欄雙分頁切換：history=對話歷史 / menu=功能選單。
    // HTML 初始為 history；此函式同步兩區顯示與 tab 視覺態（aria 同步）。
    // 選擇記入 localStorage——重造訪回到上次用的分頁（ChatGPT/Claude 同款）。
    setSidebarTab(name, persist = true) {
        // 收合成圖示列時點「對話歷史」：先展開再切過去（圖示列本身只放導覽圖示）
        if (this.isSidebarCollapsed()) this.setSidebarCollapsed(false);
        const navSection = document.getElementById('sidebar-nav-section');
        const sessionList = document.getElementById('chat-session-list');
        const tabHistory = document.getElementById('sidebar-tab-history');
        const tabMenu = document.getElementById('sidebar-tab-menu');
        if (!navSection || !sessionList || !tabHistory || !tabMenu) return;
        const isMenu = name === 'menu';
        navSection.classList.toggle('hidden', !isMenu);
        sessionList.classList.toggle('hidden', isMenu);
        tabHistory.setAttribute('aria-selected', String(!isMenu));
        tabMenu.setAttribute('aria-selected', String(isMenu));
        const onCls = ['bg-background', 'text-secondary', 'shadow-sm'];
        const offCls = ['bg-transparent', 'text-textMuted'];
        tabHistory.classList.remove(...(isMenu ? onCls : offCls));
        tabHistory.classList.add(...(isMenu ? offCls : onCls));
        tabMenu.classList.remove(...(isMenu ? offCls : onCls));
        tabMenu.classList.add(...(isMenu ? onCls : offCls));
        window.NavBadges?.paint(); // 「功能選單」分頁的紅點：選單打開就不用提醒
        if (persist === true) {
            try {
                localStorage.setItem('sidebarActiveTab', isMenu ? 'menu' : 'history');
            } catch (_e) {
                /* localStorage 不可用（隱私模式等）——不影響切換，只不記憶 */
            }
        }
        if (isMenu) {
            // 進 menu 前刷新（語言 / 登入態在背景可能已變）
            this.renderSidebarNav();
        } else {
            // 切離 menu：收起「更多分頁」popover，避免殘留
            const pop = document.getElementById('sidebar-nav-popover');
            if (pop) pop.classList.add('hidden');
        }
    },

    initSidebarNav() {
        if (this._sidebarNavInit) return;
        this._sidebarNavInit = true;
        if (!document.getElementById('sidebar-nav-items')) return; // 非 SPA 頁（論壇）無此區塊
        this._syncCollapseButton();

        let saved = 'history';
        try {
            saved = localStorage.getItem('sidebarActiveTab') === 'menu' ? 'menu' : 'history';
        } catch (_e) {
            /* 同上——不記憶即預設 history */
        }
        this.setSidebarTab(saved);
        this.renderSidebarNav();

        // switchTab 包裝：切頁後更新 active 標記（replaceState 不觸發 hashchange，
        // 沒有現成 tab 變更事件可訂）。switchTab 內部有 150ms 淡出動畫才真正換
        // 內容——立即刷一次、動畫後再刷一次，確保標記落在最終分頁。
        const orig = window.switchTab;
        if (typeof orig === 'function' && !orig._sidebarNavWrapped) {
            const wrapped = function (...args) {
                const result = orig.apply(this, args);
                if (window.GlobalNav) {
                    window.GlobalNav.refreshSidebarActive();
                    setTimeout(() => window.GlobalNav && window.GlobalNav.refreshSidebarActive(), 300);
                }
                return result;
            };
            wrapped._sidebarNavWrapped = true;
            window.switchTab = wrapped;
        }

        // 語言切換時重渲染（label 翻譯；收合鈕的提示字也跟著換）
        window.addEventListener('languageChanged', () => {
            this.renderSidebarNav();
            this._syncCollapseButton();
        });
        // 登入/登出後重渲染（訪客鎖定 → 解鎖；admin 分頁顯示與否）
        const rerender = () => this.renderSidebarNav();
        window.addEventListener('auth-success', rerender);
        window.addEventListener('auth:initialized', rerender);
        window.addEventListener('auth:changed', rerender);
    },

    // 桌機側欄收合成 64px 圖示列（Teams 式）：狀態記在 <html class="sidebar-collapsed">＋
    // localStorage（early-init.js 在第一次繪製前套用；獨立頁 site-sidebar.js 讀同一個 key）。
    // 樣式只在 md+ 生效，手機抽屜不受影響。
    isSidebarCollapsed() {
        return document.documentElement.classList.contains('sidebar-collapsed');
    },

    toggleSidebarCollapsed() {
        this.setSidebarCollapsed(!this.isSidebarCollapsed());
    },

    setSidebarCollapsed(collapsed) {
        document.documentElement.classList.toggle('sidebar-collapsed', collapsed);
        try {
            localStorage.setItem('sidebarCollapsed', collapsed ? '1' : '0');
        } catch (_e) {
            /* 不記憶，只影響這次 */
        }
        const pop = document.getElementById('sidebar-nav-popover');
        if (pop) pop.classList.add('hidden');
        this._syncCollapseButton();
        // 依寬度排版的元件（ui-shell 貼底、圖表）重算
        window.dispatchEvent(new Event('resize'));
    },

    _syncCollapseButton() {
        const btn = document.getElementById('sidebar-collapse-btn');
        if (!btn) return;
        const collapsed = this.isSidebarCollapsed();
        const t = (k, fb) => (window.I18n && window.I18n.isReady && window.I18n.isReady() ? window.I18n.t(k) : fb);
        const label = collapsed ? t('sidebar.expand', 'Expand sidebar') : t('sidebar.collapse', 'Collapse sidebar');
        btn.setAttribute('aria-label', label);
        btn.title = label;
        btn.setAttribute('aria-expanded', String(!collapsed));
    },

    // 訪客判定（init 早期 AuthManager 可能未就緒——未就緒時從寬判定為訪客，
    // auth:initialized 事件後會重渲染修正）
    _isGuest() {
        try {
            const A = window.AuthManager;
            return !(A && typeof A.isLoggedIn === 'function' && A.isLoggedIn());
        } catch (_e) {
            return true;
        }
    },

    // 訪客可直接使用的分頁：chat + 唯讀市場數據頁（Tier 1，2026-08-27 設計——
    // docs/plans/2026-08-27-guest-mode-tier1-design.md）。後端
    // GUEST_DATA_ACCESS=off 時 quota 會帶 data_access:false，全部收回鎖定。
    _isGuestAllowed(item) {
        if (window.AuthEnvironment && window.AuthEnvironment.guestDataAccess === false) {
            // 示範頁（guestOnly）是前端靜態資料、詐騙檢查（guestAlways）是公開查詢，
            // 都不受唯讀市場數據開關影響
            return item.id === 'chat' || item.guestOnly === true || item.guestAlways === true;
        }
        return item.id === 'chat' || item.guestAllowed === true;
    },

    _openLoginModal() {
        const modal = document.getElementById('login-modal');
        if (modal) modal.classList.remove('hidden');
    },

    /** 目前顯示中的 SPA 分頁 id（沒有可見分頁時回空字串）。 */
    _activeSpaTab() {
        return (document.querySelector('.tab-content:not(.hidden)')?.id || '').replace(/-tab$/, '');
    },

    /** 側欄功能選單的高亮跟著目前可見的 SPA 分頁走（切頁後、分頁顯示晚於 render 時補刷）。 */
    refreshSidebarActive() {
        // 市場子分頁（美股／台股／加密貨幣）停在哪一個，導覽都是「市場」那一項亮著
        const visible = this._activeSpaTab();
        const active = (window.NavPreferences && window.NavPreferences.groupOf(visible)) || visible;
        document.querySelectorAll('#sidebar-nav-items [data-tab]').forEach((btn) => {
            const on = btn.dataset.tab === active;
            btn.classList.toggle('text-primary', on);
            btn.classList.toggle('bg-primary/10', on);
            btn.classList.toggle('font-bold', on);
            btn.classList.toggle('text-textMuted', !on);
            btn.querySelector('i')?.classList.toggle('text-primary', on);
        });
    },

    renderSidebarNav() {
        const container = document.getElementById('sidebar-nav-items');
        if (!container) return;
        const isGuest = this._isGuest();
        // 訪客：固定選單、不讀偏好（2026-09-27 上市準備 design §3）；登入用戶照偏好
        let items;
        if (isGuest && window.NavPreferences && NavPreferences.getGuestItems) {
            items = NavPreferences.getGuestItems();
        } else if (window.NavPreferences) {
            items = NavPreferences.getEnabledItems();
        } else {
            items = (window.NAV_ITEMS || []).filter((i) => i.defaultEnabled);
        }
        const esc = window.escapeHtml || ((s) => String(s));
        const lockedHint =
            window.I18n && window.I18n.isReady && window.I18n.isReady()
                ? window.I18n.t('sidebar.lockedHint')
                : 'Login to unlock';

        // 標題列的「調整順序／完成」：登入用戶、2 項以上才有（訪客是固定選單）
        if (isGuest || items.length < 2) this.navReorder = false;
        const reorderBtn = document.getElementById('sidebar-nav-reorder');
        if (reorderBtn) {
            reorderBtn.classList.toggle('hidden', isGuest || items.length < 2);
            reorderBtn.textContent = this._t(this.navReorder ? 'common.done' : 'sidebar.reorder', this.navReorder ? 'Done' : 'Reorder');
            // 「完成」加粗就好（全站按鈕最小 36px，填色會變一顆大圓）；semibold 在 CSS 裡排在 bold 後面，要換掉不能疊
            reorderBtn.classList.toggle('font-bold', this.navReorder);
            reorderBtn.classList.toggle('font-semibold', !this.navReorder);
        }
        if (this.navReorder) {
            this._renderSidebarNavReorder(container, items);
            return;
        }

        // 全部啟用項都列出（桌機 2026-09-10 起、手機 2026-09-24 底部列拔除後）——
        // 選單可捲動，不再為了配合底部列格數截成 5 個。
        container.innerHTML = '';
        items.forEach((item) => {
            if (item.adminOnly) {
                const user = window.AuthManager && AuthManager.currentUser;
                if (!user || user.role !== 'admin') return;
            }
            const label =
                item.i18nKey && window.I18n && window.I18n.isReady && window.I18n.isReady()
                    ? window.I18n.t(item.i18nKey)
                    : item.label;
            const locked = isGuest && !this._isGuestAllowed(item);
            const btn = document.createElement('button');
            btn.dataset.tab = item.id;
            btn.dataset.navItem = item.id; // 未讀標示（nav-badges.js）認這個
            btn.className = locked
                ? 'w-full flex items-center gap-3 p-2.5 rounded-xl text-sm font-medium text-textMuted/45 ' +
                  'hover:text-textMuted hover:bg-surfaceHighlight/60 transition text-left'
                : 'w-full flex items-center gap-3 p-2.5 rounded-xl text-sm font-medium text-textMuted ' +
                  'hover:text-secondary hover:bg-surfaceHighlight transition text-left';
            btn.title = locked ? lockedHint : label; // 收合成圖示列時靠它看名稱
            btn.setAttribute('aria-label', label);
            btn.innerHTML =
                `<i data-lucide="${item.icon}" class="w-4 h-4 shrink-0"></i>` +
                `<span class="sb-label truncate flex-1">${esc(label)}</span>` +
                (locked ? '<i data-lucide="lock" class="sb-label w-3.5 h-3.5 shrink-0 opacity-70"></i>' : '');
            btn.onclick = () => {
                if (locked) {
                    // 訪客點鎖定項：直接開登入窗（比 switchTab 守門的路徑更直觀）
                    this._openLoginModal();
                    return;
                }
                if (item.id === 'forum') this.navigateToForum();
                else this.navigateToTab(item.id);
                this.refreshSidebarActive();
                // 手機抽屜：選完功能即收合（桌機 sidebar 常駐，innerWidth 守門擋掉 md+）
                if (window.innerWidth < 768 && typeof toggleSidebar === 'function') {
                    toggleSidebar();
                }
            };
            container.appendChild(btn);
        });

        // 訪客：導覽區塊尾端放登入 CTA（DANNY 2026-08-20 建議）。2026-09-27 起訪客選單
        // 是固定的開放項、通常沒有灰色鎖定項，CTA 改成訪客常駐——帳本／早報都要登入
        if (isGuest) {
            const cta = document.createElement('button');
            cta.className =
                'mt-1 w-full flex items-center justify-center gap-2 p-2.5 rounded-xl text-xs font-bold ' +
                'bg-primary/10 hover:bg-primary/20 border border-primary/25 text-primary transition';
            cta.innerHTML =
                '<i data-lucide="wallet" class="w-3.5 h-3.5 shrink-0"></i>' +
                `<span>${esc(
                    window.I18n && window.I18n.isReady && window.I18n.isReady()
                        ? window.I18n.t('sidebar.loginToUnlock')
                        : 'Connect wallet to unlock'
                )}</span>`;
            cta.onclick = () => this._openLoginModal();
            container.appendChild(cta);
        }

        // 訪客的「更多分頁」：其他市場都併進「市場」子分頁後沒有東西可列，整顆收起來（不留點了沒東西的空選單）
        const moreBtn = document.getElementById('sidebar-nav-more');
        if (moreBtn) {
            const nothingToList = isGuest && !!window.NavPreferences && NavPreferences.getGuestMoreItems().length === 0;
            moreBtn.parentElement?.classList.toggle('hidden', nothingToList);
        }

        if (window.AppUtils) AppUtils.refreshIcons();
        this.refreshSidebarActive();
        window.NavBadges?.paint(); // 重畫把標示洗掉了，用手上的數字補回去
    },

    _t(key, fallback) {
        const ready = window.I18n && window.I18n.isReady && window.I18n.isReady();
        const text = ready ? window.I18n.t(key) : '';
        return text && text !== key ? text : fallback;
    },

    /** 功能選單「調整順序」⇄「完成」 */
    toggleNavReorder() {
        this.navReorder = !this.navReorder;
        document.getElementById('sidebar-nav-popover')?.classList.add('hidden');
        this.renderSidebarNav();
    },

    /** 排序模式：每項右邊一個把手（drag-reorder.js），點了不會切分頁；拖完存進偏好 */
    _renderSidebarNavReorder(container, items) {
        const esc = window.escapeHtml || ((s) => String(s));
        const move = this._t('friends.pin.dragHandle', 'Move');
        container.innerHTML = items
            .filter((item) => !item.adminOnly || (window.AuthManager?.currentUser?.role === 'admin'))
            .map((item) => {
                const label = item.i18nKey ? this._t(item.i18nKey, item.label) : item.label;
                return (
                    `<div data-reorder-item="${esc(item.id)}" class="flex items-center gap-3 pl-2.5 pr-1 py-0.5 rounded-xl text-sm font-medium text-textMuted bg-surface">` +
                    `<i data-lucide="${esc(item.icon)}" class="w-4 h-4 shrink-0"></i>` +
                    `<span class="sb-label truncate flex-1">${esc(label)}</span>` +
                    `<button type="button" data-reorder-handle aria-label="${esc(`${move} ${label}`)}" class="w-10 h-10 shrink-0 rounded-lg flex items-center justify-center hover:text-secondary hover:bg-surfaceHighlight">` +
                    '<i data-lucide="grip-vertical" class="w-4 h-4"></i></button></div>'
                );
            })
            .join('');
        enableDragReorder(container, {
            onReorder: (ids) => {
                if (!NavPreferences.setItemOrder(ids)) {
                    window.showToast?.(this._t('sidebar.reorderFailed', "Couldn't save the order"), 'error');
                }
            },
        });
        if (window.AppUtils) AppUtils.refreshIcons();
    },

    toggleSidebarNavMore() {
        const pop = document.getElementById('sidebar-nav-popover');
        if (!pop) return;
        if (pop.classList.contains('hidden')) {
            this.renderSidebarNavPopover();
            // 視窗夾限：popover 絕對定位於導覽區下方，在矮視窗可能超過
            // 視窗底——以剩餘高度動態設 max-height（滾動留在 popover 內）。
            requestAnimationFrame(() => {
                const rect = pop.getBoundingClientRect();
                if (rect.bottom > window.innerHeight - 12) {
                    const available = Math.max(
                        160,
                        window.innerHeight - rect.top - 16
                    );
                    pop.style.maxHeight = available + 'px';
                } else {
                    pop.style.maxHeight = '';
                }
            });
            pop.classList.remove('hidden');
            // 點擊外部關閉（一次性）
            // Bug 1 修正：__suppressClickOutside 期間忽略（面板剛打開時的
            // pointerup click 不是「點外部」，是同一觸控手勢）
            setTimeout(() => {
                const closer = (e) => {
                    if (window.__suppressClickOutside && Date.now() < window.__suppressClickOutside) return;
                    if (!pop.contains(e.target) && e.target.id !== 'sidebar-nav-more') {
                        pop.classList.add('hidden');
                        document.removeEventListener('pointerdown', closer);
                    }
                };
                document.addEventListener('pointerdown', closer);
            }, 50);
        } else {
            pop.classList.add('hidden');
        }
    },

    renderSidebarNavPopover() {
        const list = document.getElementById('sidebar-nav-popover-list');
        if (!list || !window.NavPreferences) return;
        const enabledIds = NavPreferences.getEnabledItems().map((i) => i.id);
        const esc = window.escapeHtml || ((s) => String(s));
        const t = (item) =>
            item.i18nKey && window.I18n && window.I18n.isReady && window.I18n.isReady()
                ? window.I18n.t(item.i18nKey)
                : item.label;

        list.innerHTML = '';
        if (this._isGuest()) {
            // 訪客：只列其餘開放訪客的市場頁，點了直接前往（訪客沒有偏好可切，design §3）
            NavPreferences.getGuestMoreItems().forEach((item) => {
                const locked = !this._isGuestAllowed(item);
                const row = document.createElement('button');
                row.className =
                    'w-full flex items-center gap-2.5 p-2 rounded-xl text-sm text-left transition ' +
                    (locked ? 'text-textMuted/45' : 'text-textMuted hover:bg-surfaceHighlight');
                row.innerHTML =
                    `<i data-lucide="${item.icon}" class="w-4 h-4 shrink-0"></i>` +
                    `<span class="truncate flex-1">${esc(t(item))}</span>` +
                    (locked ? '<i data-lucide="lock" class="w-4 h-4 shrink-0 opacity-70"></i>' : '');
                row.onclick = () => {
                    document.getElementById('sidebar-nav-popover')?.classList.add('hidden');
                    if (locked) {
                        this._openLoginModal();
                        return;
                    }
                    this.navigateToTab(item.id);
                    if (window.innerWidth < 768 && typeof toggleSidebar === 'function') {
                        toggleSidebar();
                    }
                };
                list.appendChild(row);
            });
            if (window.AppUtils) AppUtils.refreshIcons();
            return;
        }
        (window.NAV_ITEMS || []).forEach((item) => {
            if (item.hidden) return;
            if (item.adminOnly) {
                const user = window.AuthManager && AuthManager.currentUser;
                if (!user || user.role !== 'admin') return;
            }
            const isOn = enabledIds.includes(item.id);
            const locked = this._isGuest() && !this._isGuestAllowed(item);
            const row = document.createElement('button');
            row.className =
                'w-full flex items-center gap-2.5 p-2 rounded-xl text-sm text-left transition ' +
                (locked
                    ? 'text-textMuted/45'
                    : isOn
                      ? 'text-primary bg-primary/10'
                      : 'text-textMuted hover:bg-surfaceHighlight');
            row.innerHTML =
                `<i data-lucide="${item.icon}" class="w-4 h-4 shrink-0"></i>` +
                `<span class="truncate flex-1">${esc(t(item))}</span>` +
                (locked
                    ? '<i data-lucide="lock" class="w-4 h-4 shrink-0 opacity-70"></i>'
                    : `<i data-lucide="${isOn ? 'check-circle' : 'circle'}" class="w-4 h-4 shrink-0"></i>`);
            if (locked) {
                row.onclick = () => this._openLoginModal();
                list.appendChild(row);
                return;
            }
            row.onclick = () => {
                const reason = NavPreferences.blockReason(item.id, !isOn);
                if (reason) {
                    if (typeof window.showToast === 'function') {
                        showToast(this._navLimitMessage(reason, t(item)), 'warning');
                    }
                    return;
                }
                NavPreferences.setItemEnabled(item.id, !isOn);
                this.renderSidebarNavPopover();
                this.renderSidebarNav();
            };
            list.appendChild(row);
        });
        if (window.AppUtils) AppUtils.refreshIcons();
    },

    // 固定項（對話、設定）關不掉要講「固定顯示」，不能冒充「至少 3 個」
    _navLimitMessage(reason, name) {
        const ready = window.I18n && window.I18n.isReady && window.I18n.isReady();
        if (reason === 'locked') {
            return ready ? window.I18n.t('sidebar.navLockedItem', { name }) : `${name} is always shown`;
        }
        if (reason === 'max') {
            const max = NavPreferences.MAX_ENABLED_ITEMS;
            return ready ? window.I18n.t('featureMenu.maxWarning', { max }) : `Up to ${max} items`;
        }
        const min = NavPreferences.MIN_ENABLED_ITEMS;
        return ready ? window.I18n.t('featureMenu.minWarning', { min }) : `At least ${min} items`;
    },

    /* 一次性清理底部導覽／浮動 pill 時代留下的 localStorage 鍵：
       navPosition（#510 拖曳位置）、navCollapsed（pill 折疊狀態）。 */
    _cleanupLegacyNavState() {
        try {
            localStorage.removeItem('navPosition');
            localStorage.removeItem('navCollapsed');
        } catch (_e) { /* ignore */ }
    },

    /**
     * Initialize language switcher component
     * sidebar footer 與手機頂欄各有容器——全部容器各自初始化
     * （querySelector 只挑第一個會讓另一個空著）。
     */
    initLanguageSwitcher() {
        document.querySelectorAll('.lang-switcher-container').forEach((container) => {
            if (container.childElementCount > 0) return; // 已初始化
            if (window.LanguageSwitcher && typeof window.LanguageSwitcher.init === 'function') {
                window.LanguageSwitcher.init(container);
            } else if (window.Components && window.Components.languageSwitcher) {
                // Fallback to component-based initialization
                container.innerHTML = window.Components.languageSwitcher;
                if (window.LanguageSwitcher && typeof window.LanguageSwitcher.init === 'function') {
                    window.LanguageSwitcher.init(container);
                }
            }
        });
    },

    /**
     * Initialize theme switcher component（深淺主題切換；多容器同 language switcher）
     */
    initThemeSwitcher() {
        document.querySelectorAll('.theme-switcher-container').forEach((container) => {
            if (container.childElementCount > 0) return;
            if (window.ThemeSwitcher && typeof window.ThemeSwitcher.init === 'function') {
                window.ThemeSwitcher.init(container);
            }
        });
    },

    /** 偏好變更後重畫（舊名稱，同 renderNavButtons）。 */
    refreshButtons() {
        this.renderSidebarNav();
    },
};

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => GlobalNav.init());
} else {
    GlobalNav.init();
}

// Export for use in other modules
window.GlobalNav = GlobalNav;
export { GlobalNav };
