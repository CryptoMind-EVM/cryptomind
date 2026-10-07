// ============================================
// Forum App Logic
// ============================================
import { loadPiPrices, loadForumLimits, getPrice, getLimit, formatTWDate, formatUsdc } from './forum-config.js';
import { blockedMessage, createModerationGate } from './forum-moderation.js';
import { userFacingMessage } from './error-message.js';
// 論壇付款 2026-09-25 從 TON 改成 USDC on Base：跟 premium 共用錢包直付
import {
    fetchBoundEvmAddresses,
    hasInjectedWallet,
    isTelegramMiniApp,
    pollUsdcClaim,
    showOpenInBrowserNotice,
    walletSendUsdc,
} from './usdc-pay.js';

function _t(key, fallback, params) {
    const s = window.I18n ? window.I18n.t(key, params) : '';
    return s && s !== key ? s : fallback;
}

// 分類標籤：跟篩選列同一組字串（forum.categoryAnalysis…），以前卡片直接印英文代號
function _categoryLabel(category) {
    const key = String(category || '').toLowerCase();
    if (!key) return 'general';
    return _t(`forum.category${key.charAt(0).toUpperCase()}${key.slice(1)}`, key);
}

// 發文頁（forum-create entry）沒載 security-utils.js——直接呼叫 SecurityUtils 會在
// 「已付款但發文失敗」的視窗裡丟 ReferenceError，使用者看不到 tx hash
function _esc(value) {
    if (typeof SecurityUtils !== 'undefined') return SecurityUtils.escapeHTML(String(value ?? ''));
    return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

// 付款／打賞列的資產別：0x＋64 hex＝USDC on Base，其餘＝舊的 TON 紀錄（後端有帶就用）
function _txAsset(tx) {
    if (tx && tx.asset) return tx.asset;
    return /^0x[0-9a-fA-F]{64}$/.test(String((tx && (tx.tx_hash || tx.payment_tx_hash)) || '')) ? 'USDC' : 'TON';
}

// Telegram Mini App 內不收加密貨幣付款：不建單、只給「用瀏覽器開啟」
function showForumTmaPayNotice() {
    showOpenInBrowserNotice({
        id: 'forum-tma-pay-notice',
        title: _t('forum.pay.tmaTitle', 'Pay in your browser'),
        hint: _t(
            'forum.pay.tmaHint',
            'Forum payments (USDC on Base) are handled on the CryptoMind website. Open this page in your browser to pay; your account stays the same.'
        ),
        url: window.location.href,
    });
}

// 錢包直付前的共同檢查：有注入錢包、付款錢包已綁定。回傳綁定清單（null＝查不到，交給後端擋）；
// 不能付時回 false（已顯示提示）。
async function _prepareUsdcPayer() {
    if (!hasInjectedWallet()) {
        showToast(
            _t(
                'forum.pay.noWallet',
                'No wallet found in this browser. Open this page in your wallet app\'s browser, or a browser with a wallet extension.'
            ),
            'warning'
        );
        return false;
    }
    const bound = await fetchBoundEvmAddresses();
    if (Array.isArray(bound) && !bound.length) {
        showToast(
            _t('forum.pay.bindFirst', 'Bind the wallet you will pay from in Settings → Wallet first; only USDC sent from a bound wallet counts.'),
            'warning'
        );
        return false;
    }
    return bound;
}

function _isWalletRejection(e) {
    return /cancel|closed|dismiss|abort|reject|denied/i.test((e && e.message) || '') || (e && e.code === 4001);
}

function normalizePostTags(rawTags) {
    if (!rawTags) return [];
    if (Array.isArray(rawTags)) return rawTags.filter(Boolean);
    if (typeof rawTags === 'string') {
        try {
            const parsed = JSON.parse(rawTags);
            return Array.isArray(parsed) ? parsed.filter(Boolean) : [];
        } catch {
            return [];
        }
    }
    return [];
}

const ForumApp = {
    FORUM_HOME_URL: '/static/index.html#forum',
    FORUM_INDEX_REFRESH_TTL_MS: 60 * 1000,

    CATEGORY_PILL_BASE_CLASS:
        'category-pill shrink-0 inline-flex items-center gap-2 px-4 py-2 rounded-full text-xs font-bold transition whitespace-nowrap border',

    CATEGORY_PILL_VARIANTS: {
        '': {
            active: 'border-primary/50 bg-primary/15 text-primary',
            inactive: 'border-borderSubtle text-textMuted hover:border-primary/40 hover:text-primary hover:bg-primary/10',
        },
        analysis: {
            active: 'border-amber-400/50 bg-amber-400/10 text-amber-400',
            inactive: 'border-borderSubtle text-textMuted hover:border-amber-400/50 hover:text-amber-400 hover:bg-amber-400/10',
        },
        question: {
            active: 'border-blue-400/50 bg-blue-400/10 text-blue-400',
            inactive: 'border-borderSubtle text-textMuted hover:border-blue-400/50 hover:text-blue-400 hover:bg-blue-400/10',
        },
        tutorial: {
            active: 'border-emerald-400/50 bg-emerald-400/10 text-emerald-400',
            inactive: 'border-borderSubtle text-textMuted hover:border-emerald-400/50 hover:text-emerald-400 hover:bg-emerald-400/10',
        },
        news: {
            active: 'border-violet-400/50 bg-violet-400/10 text-violet-400',
            inactive: 'border-borderSubtle text-textMuted hover:border-violet-400/50 hover:text-violet-400 hover:bg-violet-400/10',
        },
        chat: {
            active: 'border-rose-400/50 bg-rose-400/10 text-rose-400',
            inactive: 'border-borderSubtle text-textMuted hover:border-rose-400/50 hover:text-rose-400 hover:bg-rose-400/10',
        },
        insight: {
            active: 'border-cyan-400/50 bg-cyan-400/10 text-cyan-400',
            inactive: 'border-borderSubtle text-textMuted hover:border-cyan-400/50 hover:text-cyan-400 hover:bg-cyan-400/10',
        },
    },

    rememberForumReturnTarget() {
        try {
            sessionStorage.setItem('forumBackHref', this.FORUM_HOME_URL);
        } catch (e) {
            console.warn('[Forum] Failed to remember return target:', e);
        }
    },

    navigateToPost(postId) {
        this.rememberForumReturnTarget();
        const target = `/static/forum/post.html?id=${postId}`;
        if (typeof smoothNavigate === 'function') {
            smoothNavigate(target);
        } else {
            window.location.href = target;
        }
    },

    init() {
        // Ensure prices and limits are loaded
        if (!window.ForumPrices?.loaded) {
            loadPiPrices();
        }
        if (!window.ForumLimits?.loaded) {
            loadForumLimits();
        }

        try {
            this.bindEvents();
            // ?�面?��??��???
            const page = document.body.dataset.page;
            window.APP_CONFIG?.DEBUG_MODE && console.log('ForumApp: page detected', page);

            if (page === 'index') this.initIndexPage();
            else if (page === 'post') this.initPostPage();
            else if (page === 'create') this.initCreatePage();
            else if (page === 'dashboard') this.initDashboardPage();
            // SPA mode (no data-page attribute): load forum content when switching tabs.
            // 其他論壇子頁（premium 等）不是列表頁：以前也落到這裡，平白多抓文章列表與熱門標籤
            else if (!page) this.initIndexPage();

            this.updateAuthUI();
        } catch (err) {
            console.error('ForumApp: Init failed', err);
        }
    },

    bindEvents() {
        if (this._eventsBound) return;
        this._eventsBound = true;

        document.addEventListener('auth:login', () => this.updateAuthUI());
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState !== 'visible') return;
            if (document.body.dataset.page) return;
            if (AppStore.get('activeTab') !== 'forum') return;

            this.refreshIndexPage();
        });
    },

    updateAuthUI() {
        const user = AuthManager.currentUser;
        const authElements = document.querySelectorAll('.auth-only');
        const guestElements = document.querySelectorAll('.guest-only');

        if (user) {
            authElements.forEach((el) => el.classList.remove('hidden'));
            guestElements.forEach((el) => el.classList.add('hidden'));

            // ?�新?�戶顯示?�稱
            const nameEls = document.querySelectorAll('.user-display-name');
            nameEls.forEach((el) => (el.textContent = user.username));
        } else {
            authElements.forEach((el) => el.classList.add('hidden'));
            guestElements.forEach((el) => el.classList.remove('hidden'));
        }
    },

    // ===========================================
    // Index Page Logic
    // ===========================================
    async initIndexPage() {
        if (typeof this.currentTagFilter !== 'string') {
            this.currentTagFilter = '';
        }
        this.bindIndexPageEvents();
        this.updatePostFiltersUI();
        this.refreshIndexPage({ force: true });
    },

    bindIndexPageEvents() {
        const categoryFilter = document.getElementById('category-filter');
        if (categoryFilter) {
            categoryFilter.onchange = (e) => {
                this.refreshIndexPage({ force: true });
                this.updatePostFiltersUI();
            };
        }

        // 篩選指示器在頁面上有多組（篩選面板內、主內容區各一），
        // 原本兩組共用同一個 id，getElementById 只會拿到第一個 —— 第二組的
        // 清除鈕永遠沒有 listener。改用 class 一次綁定全部。
        document.querySelectorAll('.post-filter-clear').forEach((btn) => {
            btn.onclick = () => {
                this.currentTagFilter = '';
                const selectFilter = document.getElementById('category-filter');
                if (selectFilter) selectFilter.value = '';
                this.refreshIndexPage({ force: true });
                this.updatePostFiltersUI();
            };
        });

        // Bind category pill click handlers
        this.bindCategoryPills();
    },

    getCurrentPostFilters() {
        const category = document.getElementById('category-filter')?.value || '';
        return {
            category: category || undefined,
            tag: this.currentTagFilter || undefined,
        };
    },

    async refreshIndexPage({ force = false } = {}) {
        const now = Date.now();
        if (!force && this.lastIndexRefreshAt && now - this.lastIndexRefreshAt < this.FORUM_INDEX_REFRESH_TTL_MS) {
            return;
        }

        const posts = await this.loadPosts(this.getCurrentPostFilters());
        if (posts.length > 0) {
            await this.loadTrendingTags();
        } else {
            const tagsContainer = document.getElementById('trending-tags');
            if (tagsContainer) tagsContainer.innerHTML = '';
        }
        this.lastIndexRefreshAt = Date.now();
    },

    bindCategoryPills() {
        document.querySelectorAll('.category-pill').forEach((button) => {
            button.onclick = () => {
                const nextCategory = button.dataset.category || '';
                const categoryFilter = document.getElementById('category-filter');
                if (categoryFilter) {
                    categoryFilter.value = nextCategory;
                }

                this.refreshIndexPage({ force: true });
                this.updatePostFiltersUI();
            };
        });
    },

    updateCategoryPillsUI(category = '') {
        document.querySelectorAll('.category-pill').forEach((button) => {
            const buttonCategory = button.dataset.category || '';
            const variant =
                this.CATEGORY_PILL_VARIANTS[buttonCategory] || this.CATEGORY_PILL_VARIANTS[''];
            const isActive = buttonCategory === category;
            button.className = `${this.CATEGORY_PILL_BASE_CLASS} ${
                isActive ? variant.active : variant.inactive
            }`;
        });
    },

    updatePostFiltersUI() {
        // 頁面上有多組指示器，全部一起更新（原本靠 id 只更新得到第一組）
        const indicators = document.querySelectorAll('.post-filter-indicator');
        const texts = document.querySelectorAll('.post-filter-text');
        const category = document.getElementById('category-filter')?.value || '';
        this.updateCategoryPillsUI(category);

        if (indicators.length === 0) return;

        const parts = [];
        if (category) parts.push(`${window.I18n ? window.I18n.t('forum.categoryFilter') : 'Category:'}${category}`);
        if (this.currentTagFilter) parts.push(`#${this.currentTagFilter}`);

        if (parts.length === 0) {
            indicators.forEach((el) => el.classList.add('hidden'));
            texts.forEach((el) => { el.textContent = ''; });
            return;
        }

        const label = parts.join(' · ');
        indicators.forEach((el) => el.classList.remove('hidden'));
        texts.forEach((el) => { el.textContent = label; });
    },

    getFilteredEmptyStateMessage() {
        // 顯示翻好的分類名（以前直接印 question 這種代號）
        const code = document.getElementById('category-filter')?.value || '';
        const category = code ? _categoryLabel(code) : '';
        const _ = (key, fallback) => window.I18n ? window.I18n.t(key) : fallback;
        if (this.currentTagFilter && category) {
            return (window.I18n ? window.I18n.t('forum.emptyWithTag', { tag: this.currentTagFilter, category }) : `No posts found with #${this.currentTagFilter} in ${category}.`);
        }
        if (this.currentTagFilter) {
            return (window.I18n ? window.I18n.t('forum.emptyWithTagOnly', { tag: this.currentTagFilter }) : `No posts found with #${this.currentTagFilter}.`);
        }
        if (category) {
            return (window.I18n ? window.I18n.t('forum.emptyWithCategory', { category }) : `No posts in ${category} category yet.`);
        }
        return (window.I18n ? window.I18n.t('forum.emptyDefault') : 'No posts yet.');
    },

    setTrendingTagsVisible(isVisible) {
        const container = document.getElementById('trending-tags');
        if (!container) return;
        container.classList.toggle('hidden', !isVisible);
    },

    updateForumStats(posts = []) {
        const postsEl = document.getElementById('forum-stat-posts');
        const usersEl = document.getElementById('forum-stat-users');
        const commentsEl = document.getElementById('forum-stat-comments');

        if (!postsEl || !usersEl || !commentsEl) return;

        // Calculate stats from loaded posts
        const totalPosts = posts.length;
        const uniqueUsers = new Set(posts.map(p => p.user_id)).size;
        const totalComments = posts.reduce((sum, p) => sum + (p.comment_count || 0), 0);

        // Animate number change
        const animateNumber = (el, target) => {
            const current = parseInt(el.textContent) || 0;
            if (current === target) return;
            const diff = target - current;
            const steps = 10;
            const stepValue = diff / steps;
            let step = 0;
            const interval = setInterval(() => {
                step++;
                el.textContent = Math.round(current + stepValue * step);
                if (step >= steps) {
                    el.textContent = target;
                    clearInterval(interval);
                }
            }, 30);
        };

        animateNumber(postsEl, totalPosts);
        animateNumber(usersEl, uniqueUsers);
        animateNumber(commentsEl, totalComments);
    },

    // loadBoards() 2026-09-26 移除：抓了看板清單卻不顯示（發文固定 crypto、列表不分看板），
    // 只多一次請求、失敗時還會跳錯誤提示

    async loadPosts(filters = {}) {
        const container = document.getElementById('post-list');
        if (!container) return [];

        // Enhanced loading skeleton
        container.innerHTML = `
            <div class="space-y-3">
                ${[1, 2, 3].map(() => `
                    <div class="rounded-2xl border border-white/5 bg-surface/60 p-4 animate-pulse">
                        <div class="flex items-center gap-2 mb-3">
                            <div class="h-4 w-14 rounded-full bg-white/5"></div>
                            <div class="h-3 w-16 rounded bg-white/5"></div>
                            <div class="h-3 w-10 rounded bg-white/5 ml-auto"></div>
                        </div>
                        <div class="h-5 w-3/4 rounded bg-white/5 mb-2.5"></div>
                        <div class="h-4 w-1/2 rounded bg-white/5 mb-3"></div>
                        <div class="flex gap-3 pt-3 border-t border-white/[0.03]">
                            <div class="h-3 w-10 rounded bg-white/5"></div>
                            <div class="h-3 w-10 rounded bg-white/5"></div>
                            <div class="h-3 w-10 rounded bg-white/5"></div>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;

        try {
            const response = await ForumAPI.getPosts(filters);
            const posts = response.posts || [];
            this.updatePostFiltersUI();

            container.innerHTML = '';
            this.setTrendingTagsVisible(posts.length > 0);

            if (posts.length === 0) {
                container.innerHTML = `
                    <div class="rounded-3xl border border-white/5 bg-surface/40 px-6 py-16 text-center">
                        <div class="w-20 h-20 rounded-full bg-surface/60 flex items-center justify-center mx-auto mb-6">
                            <i data-lucide="inbox" class="w-10 h-10 text-textMuted/40"></i>
                        </div>
                        <h3 class="text-lg font-bold text-secondary mb-2">${window.I18n ? window.I18n.t('forum.noPostsTitle') : 'No posts yet'}</h3>
                        <p class="text-textMuted/60 text-sm mb-6 max-w-xs mx-auto">${_esc(this.getFilteredEmptyStateMessage())}</p>
                        <a href="/static/forum/create.html" class="inline-flex items-center gap-2 bg-primary/15 hover:bg-primary/25 text-primary px-5 py-2.5 rounded-full text-sm font-bold transition">
                            <i data-lucide="plus" class="w-4 h-4"></i>
                            ${window.I18n ? window.I18n.t('forum.beFirstPoster') : 'Be the first to post'}
                        </a>
                    </div>
                `;
                // 以前這裡提早 return、沒畫圖示：圓圈空的，按鈕的 + 變成佔位的空 <i> 把字擠偏
                AppUtils.refreshIcons();
                this.updateForumStats([]);
                return [];
            }

            // Category color config for post cards
            const CATEGORY_COLORS = {
                analysis: { avatar: 'bg-amber-400/12 text-amber-300', tag: 'bg-amber-400/10 text-amber-300 border border-amber-400/20', badge: 'text-amber-300 border-amber-400/20 bg-amber-400/10', accent: 'from-amber-400/20' },
                question: { avatar: 'bg-blue-400/12 text-blue-300', tag: 'bg-blue-400/10 text-blue-300 border border-blue-400/20', badge: 'text-blue-300 border-blue-400/20 bg-blue-400/10', accent: 'from-blue-400/20' },
                tutorial: { avatar: 'bg-emerald-400/12 text-emerald-300', tag: 'bg-emerald-400/10 text-emerald-300 border border-emerald-400/20', badge: 'text-emerald-300 border-emerald-400/20 bg-emerald-400/10', accent: 'from-emerald-400/20' },
                news:     { avatar: 'bg-violet-400/12 text-violet-300', tag: 'bg-violet-400/10 text-violet-300 border border-violet-400/20', badge: 'text-violet-300 border-violet-400/20 bg-violet-400/10', accent: 'from-violet-400/20' },
                chat:     { avatar: 'bg-rose-400/12 text-rose-300', tag: 'bg-rose-400/10 text-rose-300 border border-rose-400/20', badge: 'text-rose-300 border-rose-400/20 bg-rose-400/10', accent: 'from-rose-400/20' },
                insight:  { avatar: 'bg-cyan-400/12 text-cyan-300', tag: 'bg-cyan-400/10 text-cyan-300 border border-cyan-400/20', badge: 'text-cyan-300 border-cyan-400/20 bg-cyan-400/10', accent: 'from-cyan-400/20' },
            };
            const DEFAULT_COLORS = { avatar: 'bg-primary/10 text-primary', tag: 'bg-white/5 text-textMuted border border-white/5', badge: 'text-primary border-primary/20 bg-primary/10', accent: 'from-primary/20' };

            posts.forEach((post, index) => {
                const el = document.createElement('div');
                const colors = CATEGORY_COLORS[(post.category || '').toLowerCase()] || DEFAULT_COLORS;
                const isPinned = post.is_pinned || false;

                el.className = `
                    group rounded-2xl border border-white/5 bg-surface/80 p-4 cursor-pointer transition-all duration-200
                    hover:bg-surface hover:border-white/10
                    ${isPinned ? 'border-l-2 border-l-warning/40 bg-amber-500/[0.02]' : ''}
                `;
                el.onclick = () => this.navigateToPost(post.id);

                // Tags HTML
                let tagsHtml = '';
                try {
                    if (post.tags) {
                        const tags = normalizePostTags(post.tags);
                        tagsHtml = tags
                            .map((tag) => {
                                const safe = typeof SecurityUtils !== 'undefined' ? SecurityUtils.escapeHTML(tag) : tag.replace(/</g, '&lt;').replace(/>/g, '&gt;');
                                return `<span class="text-[11px] font-semibold px-2.5 py-1 rounded-full ${colors.tag}">#${safe}</span>`;
                            })
                            .join('');
                    }
                } catch (e) {
                    console.warn('[Forum] Tags parsing failed for post', post.id, e);
                }

                const date = formatTWDate(post.created_at);
                const pushCount = Math.max(0, post.push_count || 0);
                const booCount = Math.max(0, post.boo_count || 0);
                const safeUsername = typeof SecurityUtils !== 'undefined' ? SecurityUtils.escapeHTML(post.username || post.user_id) : post.username || post.user_id;
                const safeTitle = typeof SecurityUtils !== 'undefined' ? SecurityUtils.escapeHTML(post.title || '') : post.title || '';
                const categoryShort = _esc(_categoryLabel(post.category));

                const pinnedHtml = isPinned ? `
                    <div class="flex items-center gap-1.5 mb-2 text-amber-600/70 text-[10px] font-bold uppercase tracking-wider">
                        <i data-lucide="pin" class="w-3 h-3"></i>${window.I18n ? window.I18n.t('forum.pinned') : 'Pinned'}
                    </div>
                ` : '';

                const tipsHtml = post.tips_total > 0 ? `
                    <span class="inline-flex items-center gap-1 text-primary/80">
                        <i data-lucide="gift" class="w-3 h-3"></i>${formatUsdc(post.tips_total)}
                    </span>
                ` : '';

                el.innerHTML = `
                    ${pinnedHtml}
                    <div class="flex items-center gap-2 mb-2.5">
                        <span class="text-[10px] font-bold uppercase tracking-[0.15em] px-2.5 py-1 rounded-full ${colors.badge}">${categoryShort}</span>
                        <a href="/static/forum/profile.html?id=${encodeURIComponent(post.user_id)}" class="text-xs text-textMuted/70 hover:text-primary transition truncate" data-click="stopPropagation">${safeUsername}</a>
                        <span class="text-[10px] text-textMuted/40 ml-auto shrink-0">${date}</span>
                    </div>
                    <h3 class="font-bold text-secondary text-[15px] leading-snug mb-2.5 group-hover:text-primary transition-colors line-clamp-2">${safeTitle}</h3>
                    ${tagsHtml ? `<div class="flex flex-wrap gap-1.5 mb-3">${tagsHtml}</div>` : '<div class="mb-1"></div>'}
                    <div class="flex items-center gap-3 text-[11px] text-textMuted/50 pt-3 border-t border-white/[0.03]">
                        <span class="inline-flex items-center gap-1 ${pushCount > 0 ? 'text-success/70' : ''}">
                            <i data-lucide="thumbs-up" class="w-3 h-3"></i>${pushCount}
                        </span>
                        <span class="inline-flex items-center gap-1 ${booCount > 0 ? 'text-danger/70' : ''}">
                            <i data-lucide="thumbs-down" class="w-3 h-3"></i>${booCount}
                        </span>
                        <span class="inline-flex items-center gap-1">
                            <i data-lucide="message-circle" class="w-3 h-3"></i>${post.comment_count || 0}
                        </span>
                        ${tipsHtml}
                    </div>
                `;
                container.appendChild(el);
            });

            // Update stats
            this.updateForumStats(posts);

            AppUtils.refreshIcons();
            return posts;
        } catch (e) {
            console.error(e);
            container.innerHTML = `<div class="rounded-[28px] border border-danger/20 bg-danger/5 px-6 py-10 text-center text-danger">${window.I18n ? window.I18n.t('forum.loadFailed') : 'Failed to load'}</div>`;
            return [];
        }
    },

    async loadTrendingTags() {
        const container = document.getElementById('trending-tags');
        if (!container) return;

        try {
            const response = await ForumAPI.getTrendingTags();
            const tags = response.tags || [];

            if (tags.length === 0) {
                container.innerHTML = '';
                return;
            }

            const MAX_VISIBLE = 8;
            const visible = tags.slice(0, MAX_VISIBLE);
            const hidden = tags.slice(MAX_VISIBLE);

            container.innerHTML = visible
                .map((tag) => {
                    const safeName =
                        typeof SecurityUtils !== 'undefined'
                            ? SecurityUtils.escapeHTML(tag.name)
                            : tag.name;
                    const isActive = this.currentTagFilter === tag.name;

                    return `<button type="button" data-tag="${safeName}"
                        class="trending-tag shrink-0 max-w-[8.5rem] inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border text-[11px] font-semibold overflow-hidden transition border-white/5 bg-white/5 text-textMuted ${isActive ? '!text-primary !border-primary/20 bg-primary/10' : 'hover:text-secondary hover:border-white/10'}">
                        <span class="truncate">#${safeName}</span><span class="opacity-50 text-[10px] shrink-0 ml-0.5">${tag.post_count}</span>
                    </button>`;
                })
                .join('') + (hidden.length > 0 ? `<button type="button" id="tags-expand-btn" class="shrink-0 inline-flex items-center gap-1 px-3 py-1.5 rounded-full border text-[11px] font-semibold transition border-white/5 bg-white/5 text-textMuted hover:text-primary hover:border-primary/20">+${hidden.length}</button>` : '') + `<div id="tags-extra" class="hidden flex items-center gap-2 flex-wrap">${hidden
                    .map((tag) => {
                        const safeName =
                            typeof SecurityUtils !== 'undefined'
                                ? SecurityUtils.escapeHTML(tag.name)
                                : tag.name;
                        const isActive = this.currentTagFilter === tag.name;
                        return `<button type="button" data-tag="${safeName}"
                            class="trending-tag shrink-0 max-w-[8.5rem] inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border text-[11px] font-semibold overflow-hidden transition border-white/5 bg-white/5 text-textMuted ${isActive ? 'text-primary border-primary/20 bg-primary/10' : 'hover:text-secondary hover:border-white/10'}">
                            <span class="truncate">#${safeName}</span><span class="opacity-50 text-[10px] shrink-0 ml-0.5">${tag.post_count}</span>
                        </button>`;
                    })
                    .join('')}</div>`;

            container.querySelectorAll('.trending-tag').forEach((button) => {
                button.addEventListener('click', () => {
                    const nextTag = button.dataset.tag || '';
                    this.currentTagFilter =
                        this.currentTagFilter === nextTag ? '' : nextTag;
                    this.refreshIndexPage({ force: true });
                    this.updatePostFiltersUI();
                });
            });

            // Bind expand/collapse toggle
            const expandBtn = container.querySelector('#tags-expand-btn');
            const extraDiv = container.querySelector('#tags-extra');
            if (expandBtn && extraDiv) {
                let expanded = false;
                expandBtn.addEventListener('click', () => {
                    expanded = !expanded;
                    extraDiv.classList.toggle('hidden', !expanded);
                    extraDiv.classList.toggle('flex', expanded);
                    expandBtn.innerHTML = expanded
                        ? `<i data-lucide="chevron-up" class="w-3 h-3"></i> ${window.I18n ? window.I18n.t('forum.collapseTags') : 'Collapse'}`
                        : `+${hidden.length}`;
                    if (typeof lucide !== 'undefined') lucide.createIcons();
                });
            }
        } catch (e) {
            console.error('Failed to load tags', e);
            if (container) {
                container.innerHTML = `<div class="text-sm text-danger py-1">${window.I18n ? window.I18n.t('forum.tagLoadFailed') : 'Failed to load tags'}</div>`;
            }
        }
    },

    // ===========================================
    // Post Page Logic
    // ===========================================
    async initPostPage() {
        const urlParams = new URLSearchParams(window.location.search);
        const postId = urlParams.get('id');

        if (!postId) {
            if (typeof smoothNavigate === 'function') {
                smoothNavigate(this.FORUM_HOME_URL);
            } else {
                window.location.href = this.FORUM_HOME_URL;
            }
            return;
        }

        this.currentPostId = postId;
        await this.loadPostDetail(postId);
        await this.loadComments(postId);

        // 綁�??��?事件 - 使用?��??��?法防止�?複�?�?
        const bindButton = (id, handler) => {
            const btn = document.getElementById(id);
            if (btn) {
                const newBtn = btn.cloneNode(true);
                btn.parentNode.replaceChild(newBtn, btn);
                newBtn.addEventListener('click', handler);
            }
        };

        bindButton('btn-push', () => this.handlePush(postId));
        bindButton('btn-boo', () => this.handleBoo(postId));
        bindButton('btn-reply', () => this.toggleReplyForm());
        bindButton('btn-tip', () => this.handleTip(postId));
        bindButton('submit-reply', () => this.submitReply(postId));
        bindButton('btn-delete', () => this.handleDelete(postId));
        bindButton('btn-edit', () => this.handleEdit(postId));
    },

    async loadPostDetail(id) {
        try {
            const response = await ForumAPI.getPost(id);
            const post = response.post;

            // 保�??��??��?對象，用?��?續檢?��?如�?賞�?檢查作者�?
            this.currentPost = post;

            document.title = `${post.title} - CryptoMind Forum`;

            document.getElementById('post-category').textContent = _categoryLabel(post.category);
            document.getElementById('post-title').textContent = post.title;

            // 安全?�創建�??��??��??�止 XSS�?
            const authorContainer = document.getElementById('post-author');
            authorContainer.innerHTML = '';
            if (typeof SecurityUtils !== 'undefined') {
                const authorLink = SecurityUtils.createSafeLink(
                    `/static/forum/profile.html?id=${SecurityUtils.encodeURL(post.user_id)}`,
                    post.username || post.user_id,
                    { className: 'hover:text-primary transition' }
                );
                authorContainer.appendChild(authorLink);
            } else {
                // Fallback: 使用 textContent
                const authorLink = document.createElement('a');
                authorLink.href = `/static/forum/profile.html?id=${encodeURIComponent(post.user_id)}`;
                authorLink.textContent = post.username || post.user_id;
                authorLink.className = 'hover:text-primary transition';
                authorContainer.appendChild(authorLink);
            }

            document.getElementById('post-date').textContent = formatTWDate(post.created_at, true);

            // 安全?�渲??Markdown ?�容（防�?XSS�?
            const contentContainer = document.getElementById('post-content');
            if (typeof SecurityUtils !== 'undefined') {
                // 使用 SecurityUtils 安全渲�?
                contentContainer.innerHTML = SecurityUtils.renderMarkdownSafely(post.content);
            } else {
                contentContainer.textContent = post.content;
            }

            // Tags
            const tagsContainer = document.getElementById('post-tags');
            if (post.tags && tagsContainer) {
                try {
                    const tags = normalizePostTags(post.tags);
                    tagsContainer.innerHTML = tags
                        .map(
                            (tag) =>
                                `<span class="inline-flex items-center rounded-full border border-primary/25 bg-primary/12 px-3.5 py-1.5 text-xs font-semibold text-primary">#${typeof SecurityUtils !== 'undefined' ? SecurityUtils.escapeHTML(tag) : tag}</span>`
                        )
                        .join('');
                } catch (e) {
                    console.warn('[Forum] Post tags parsing failed for post', id, e);
                }
            }

            // 顯示作者�?作�??��?編輯/?�除�?
            this.updateAuthorActions(post);

            // 作者沒有 EVM 錢包、收不了 USDC 打賞 → 按鈕淡化（點下去會說明原因，見 handleTip）
            const tipBtn = document.getElementById('btn-tip');
            if (tipBtn) {
                const noWallet = post.author_tippable === false;
                tipBtn.classList.toggle('opacity-50', noWallet);
                tipBtn.title = noWallet
                    ? _t('forum.tipNoEvmWallet', "This author hasn't linked an EVM wallet, so tips aren't available yet")
                    : '';
            }

            // Stats
            this.updatePostStats(post);
            // 後端 GET 文章時會清掉這篇的留言通知；post.html 沒載 NotificationService，側欄紅點要自己叫它重抓
            window.NavBadges?.schedule();

            // Re-render icons
            AppUtils.refreshIcons();
        } catch (e) {
            showToast(window.I18n ? window.I18n.t('forum.postLoadFailed') : 'Failed to load post', 'error');
            console.error(e);
        }
    },

    updateAuthorActions(post) {
        const currentUserId = AuthManager.currentUser?.user_id || AuthManager.currentUser?.uid;
        const isAuthor = currentUserId && post.user_id && currentUserId === post.user_id;

        // 尋找?�創建�??��?作�??�容??
        let actionsContainer = document.getElementById('author-actions');
        if (!actionsContainer) {
            // ?��?題�??��??��?作�??�容??
            const titleEl = document.getElementById('post-title');
            if (titleEl) {
                actionsContainer = document.createElement('div');
                actionsContainer.id = 'author-actions';
                actionsContainer.className = 'flex flex-wrap gap-2 mt-5 mb-2';
                titleEl.parentNode.insertBefore(actionsContainer, titleEl.nextSibling);
            }
        }

        if (actionsContainer) {
            if (isAuthor) {
                    actionsContainer.innerHTML = `
                    <button id="btn-edit"
                        class="inline-flex items-center gap-2.5 rounded-full border border-white/10 bg-background/60 px-5 py-2.5 text-sm text-secondary transition hover:bg-white/10">
                        <i data-lucide="edit-2" class="w-4 h-4"></i>
                        <span>${window.I18n ? window.I18n.t('forum.edit') : 'Edit'}</span>
                    </button>
                    <button id="btn-delete"
                        class="inline-flex items-center gap-2.5 rounded-full border border-danger/25 bg-danger/10 px-5 py-2.5 text-sm text-danger transition hover:bg-danger/20">
                        <i data-lucide="trash-2" class="w-4 h-4"></i>
                        <span>${window.I18n ? window.I18n.t('forum.delete') : 'Delete'}</span>
                    </button>
                `;
            } else {
                actionsContainer.innerHTML = '';
            }
        }
    },

    updatePostStats(post) {
        const btnPush = document.getElementById('btn-push');
        const btnBoo = document.getElementById('btn-boo');
        const statPush = document.getElementById('stat-push');
        const statBoo = document.getElementById('stat-boo');
        const statTips = document.getElementById('stat-tips');

        if (statPush) statPush.textContent = post.push_count;
        if (statBoo) statBoo.textContent = post.boo_count;
        if (statTips) statTips.textContent = post.tips_total;

        // ?�置顏色
        btnPush?.classList.remove('text-success');
        btnPush?.classList.add('text-textMuted');
        btnBoo?.classList.remove('text-danger');
        btnBoo?.classList.add('text-textMuted');

        // ?��??�票?�?��???
        if (post.viewer_vote === 'push') {
            btnPush?.classList.remove('text-textMuted');
            btnPush?.classList.add('text-success');
        } else if (post.viewer_vote === 'boo') {
            btnBoo?.classList.remove('text-textMuted');
            btnBoo?.classList.add('text-danger');
        }
    },

    async loadComments(postId) {
        const container = document.getElementById('comments-list');
        try {
            const response = await ForumAPI.getComments(postId);
            const comments = response.comments || [];

            container.innerHTML = '';

            if (comments.length === 0) {
                container.innerHTML = `<div class="text-center text-textMuted py-4">${window.I18n ? window.I18n.t('forum.noComments') : 'No comments yet'}</div>`;
                return;
            }

            comments.forEach((comment) => {
                if (comment.type !== 'comment') return; // ?�顯示�??��?�?

                const el = document.createElement('div');
                el.className =
                    'rounded-[24px] border border-borderSubtle bg-background/40 px-5 py-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]';
                el.innerHTML = `
                    <div class="mb-3 flex items-start justify-between gap-4">
                        <a href="/static/forum/profile.html?id=${encodeURIComponent(comment.user_id)}" class="font-bold text-sm text-secondary hover:text-primary transition">${typeof SecurityUtils !== 'undefined' ? SecurityUtils.escapeHTML(comment.username || comment.user_id) : comment.username || comment.user_id}</a>
                        <div class="flex items-center gap-3">
                            <span class="text-xs text-textMuted/70">${formatTWDate(comment.created_at, true)}</span>
                            <!-- 2026-09-26 恢復：api_server.py 只單獨開 POST /api/governance/reports
                                 （governance 其他端點仍關），檢舉進後台 /api/admin/forum/reports -->
                            <button data-report-type="comment" data-report-id="${comment.id}" class="rounded-full border border-borderSubtle bg-background/50 p-2 text-textMuted transition hover:text-danger hover:border-danger/25 hover:bg-danger/10 report-trigger" title="${window.I18n ? window.I18n.t('common.report') : 'Report'}">
                                <i data-lucide="flag" class="w-3.5 h-3.5"></i>
                            </button>
                        </div>
                    </div>
                    <div class="text-sm leading-7 text-textMain/90">${escapeHtml(comment.content)}</div>
                `;
                container.appendChild(el);
            });
            AppUtils.refreshIcons();
        } catch (e) {
            console.error('[Forum] loadComments failed:', e);
            if (container) {
                container.innerHTML =
                    `<div class="text-center py-4 text-danger">${window.I18n ? window.I18n.t('forum.commentLoadFailed') : 'Failed to load comments'}</div>`;
            }
        }
    },

    async handlePush(postId) {
        if (!AuthManager.currentUser) return showToast(window.I18n ? window.I18n.t('forum.pleaseLoginFirst') : 'Please log in first', 'warning');
        const post = this.currentPost;
        if (!post) return;

        // Optimistic UI update
        const wasPush = post.viewer_vote === 'push';
        const wasBoo = post.viewer_vote === 'boo';
        if (wasPush) {
            post.push_count = Math.max(0, (post.push_count || 0) - 1);
            post.viewer_vote = null;
        } else {
            post.push_count = (post.push_count || 0) + 1;
            if (wasBoo) post.boo_count = Math.max(0, (post.boo_count || 0) - 1);
            post.viewer_vote = 'push';
        }
        this.updatePostStats(post);

        try {
            await ForumAPI.pushPost(postId);
        } catch (e) {
            // Revert on failure
            if (wasPush) {
                post.push_count = (post.push_count || 0) + 1;
                post.viewer_vote = 'push';
            } else {
                post.push_count = Math.max(0, (post.push_count || 0) - 1);
                if (wasBoo) post.boo_count = (post.boo_count || 0) + 1;
                post.viewer_vote = wasBoo ? 'boo' : null;
            }
            this.updatePostStats(post);
            showToast(userFacingMessage(e), 'error');
        }
    },

    async handleBoo(postId) {
        if (!AuthManager.currentUser) return showToast(window.I18n ? window.I18n.t('forum.pleaseLoginFirst') : 'Please log in first', 'warning');
        const post = this.currentPost;
        if (!post) return;

        // Optimistic UI update
        const wasBoo = post.viewer_vote === 'boo';
        const wasPush = post.viewer_vote === 'push';
        if (wasBoo) {
            post.boo_count = Math.max(0, (post.boo_count || 0) - 1);
            post.viewer_vote = null;
        } else {
            post.boo_count = (post.boo_count || 0) + 1;
            if (wasPush) post.push_count = Math.max(0, (post.push_count || 0) - 1);
            post.viewer_vote = 'boo';
        }
        this.updatePostStats(post);

        try {
            await ForumAPI.booPost(postId);
        } catch (e) {
            // Revert on failure
            if (wasBoo) {
                post.boo_count = (post.boo_count || 0) + 1;
                post.viewer_vote = 'boo';
            } else {
                post.boo_count = Math.max(0, (post.boo_count || 0) - 1);
                if (wasPush) post.push_count = (post.push_count || 0) + 1;
                post.viewer_vote = wasPush ? 'push' : null;
            }
            this.updatePostStats(post);
            showToast(userFacingMessage(e), 'error');
        }
    },

    toggleReplyForm() {
        if (!AuthManager.currentUser) return showToast(window.I18n ? window.I18n.t('forum.pleaseLoginFirst') : 'Please log in first', 'warning');
        const form = document.getElementById('reply-form');
        form.classList.toggle('hidden');
    },

    async submitReply(postId) {
        // ?��??保護：�??�正?��?交中，直?��???
        if (this.isSubmittingReply) {
            window.APP_CONFIG?.DEBUG_MODE &&
                console.log('[submitReply] ?�� Already submitting, ignoring duplicate click');
            return;
        }

        const content = document.getElementById('reply-content').value;
        if (!content) return;

        const submitBtn = document.getElementById('submit-reply');

        try {
            // 設置?�交中�?�?
            this.isSubmittingReply = true;

            // 禁用?��?並顯示�??��???
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerHTML =
                    `<div class="flex items-center gap-2 justify-center"><svg class="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg><span>${window.I18n ? window.I18n.t('forum.submitting') : 'Submitting...'}</span></div>`;
            }

            await ForumAPI.createComment(postId, { type: 'comment', content });

            // 清空輸入框並?��??��?表單
            document.getElementById('reply-content').value = '';
            this.toggleReplyForm();

            // ?�新載入評�??�表
            this.loadComments(postId);

            showToast(window.I18n ? window.I18n.t('forum.replySubmitted') : 'Reply sent', 'success');
        } catch (e) {
            // 留言沒過內容檢查（2026-10-01）：講出原因，留言框內容留著讓人改
            if (e?.message === 'content_blocked') {
                const message = await blockedMessage(
                    (title, body) => AppAPI.post('/api/forum/posts/check', { title, content: body }),
                    '',
                    content,
                    (key, vars) => _t(key, key, vars)
                );
                showToast(message, 'warning');
                return;
            }
            // 每日留言額度用完（後端 429 "Daily comment limit reached"）；頻率限制的 429 照原訊息
            const quotaHit = e.status === 429 && /Daily comment limit/.test(e.message || '');
            showToast(quotaHit && window.I18n ? window.I18n.t('forum.commentLimitReached') : userFacingMessage(e, { fallbackKey: 'forum.submitFailed' }), 'error');
        } finally {
            // ?�復?��??�?�並清除?�交中�?�?
            this.isSubmittingReply = false;
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerHTML =
                    `<div class="flex items-center gap-2 justify-center"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"></path></svg><span>${window.I18n ? window.I18n.t('forum.submitReplyBtn') : 'Submit reply'}</span></div>`;
            }
        }
    },

    async handleDelete(postId) {
        if (!AuthManager.currentUser) {
            return showToast(window.I18n ? window.I18n.t('forum.pleaseLoginFirst') : 'Please log in first', 'warning');
        }

        // 確�??�除
        const confirmed = await showConfirm({
            title: window.I18n ? window.I18n.t('forum.confirmDelete') : 'Confirm delete',
            message: 'Delete this post? This action cannot be undone.',
            type: 'warning',
            confirmText: window.I18n ? window.I18n.t('forum.delete') : 'Delete',
            cancelText: window.I18n ? window.I18n.t('forum.cancel') : 'Cancel',
        });

        if (!confirmed) return;

        const btnElement = document.getElementById('btn-delete');
        if (btnElement) {
            btnElement.disabled = true;
            btnElement.classList.add('opacity-50', 'cursor-not-allowed');
        }

        try {
            await ForumAPI.deletePost(postId);
            showToast(window.I18n ? window.I18n.t('forum.postDeleted') : 'Post deleted', 'success');

            // 延遲後�??��?首�?
            setTimeout(() => {
                if (typeof smoothNavigate === 'function') {
                    smoothNavigate(this.FORUM_HOME_URL);
                } else {
                    window.location.href = this.FORUM_HOME_URL;
                }
            }, 1000);
        } catch (e) {
            showToast((window.I18n ? window.I18n.t('forum.deleteFailed') : 'Delete failed') + ': ' + userFacingMessage(e), 'error');
            if (btnElement) {
                btnElement.disabled = false;
                btnElement.classList.remove('opacity-50', 'cursor-not-allowed');
            }
        }
    },

    async handleEdit(postId) {
        if (!AuthManager.currentUser) {
            return showToast(window.I18n ? window.I18n.t('forum.pleaseLoginFirst') : 'Please log in first', 'warning');
        }

        // TODO: 實現編輯?�能 - ?�以?�建一?�編輯模?��??��??�到編輯?�面
        showToast(window.I18n ? window.I18n.t('forum.editNotAvailable') : 'Editing is not yet available', 'info');
    },

    async handleTip(postId) {
        if (!AuthManager.currentUser) {
            return showToast(window.I18n ? window.I18n.t('forum.pleaseLoginFirst') : 'Please log in first', 'warning');
        }

        // 檢查?�否?��?賞自己�??��?
        const currentUserId = AuthManager.currentUser.user_id || AuthManager.currentUser.uid;
        const postAuthorId = this.currentPost?.user_id;

        if (currentUserId && postAuthorId && currentUserId === postAuthorId) {
            return showToast(window.I18n ? window.I18n.t('forum.cannotTipSelf') : 'Cannot tip your own post', 'warning');
        }

        // 作者沒有 EVM 錢包（身份不是 EVM、也沒綁）：先講清楚，不要讓人付了才吃 400
        if (this.currentPost?.author_tippable === false) {
            return showToast(_t('forum.tipNoEvmWallet', "This author hasn't linked an EVM wallet, so tips aren't available yet"), 'info');
        }

        // Telegram Mini App 內不收加密貨幣付款：只給「用瀏覽器開啟」
        if (isTelegramMiniApp()) {
            showForumTmaPayNotice();
            return;
        }

        const bound = await _prepareUsdcPayer();
        if (bound === false) return;

        const amount = await this._promptTipAmount();
        if (amount === null) return;

        const btn = document.getElementById('btn-tip');
        if (btn && btn.disabled) return; // 上一筆打賞還在跑
        const restore = btn ? btn.innerHTML : '';
        const setBtn = (text) => {
            if (!btn) return;
            btn.disabled = true;
            btn.textContent = text;
        };
        setBtn(_t('forum.processing', 'Processing') + '…');
        let txHash = null;
        try {
            // 1. 建單：收款人＝作者的 EVM 地址、金額含唯一尾數，都簽進訂單
            const order = await AppAPI.post(`/api/forum/posts/${postId}/tip/payment-order`, { amount });
            // 2. 錢包直付（USDC on Base，直接轉給作者）
            setBtn(_t('premium.walletPaySending', 'Check your wallet…'));
            txHash = await walletSendUsdc(order, bound);
            // 3. 帶 tx hash 領取（等區塊確認，可重試）
            await pollUsdcClaim(() => ForumAPI.tipPost(postId, order, txHash), {
                onProgress: (i, total) =>
                    setBtn(
                        _t('premium.verifyingAttempt', 'Verifying ({{current}}/{{total}})…')
                            .replace('{{current}}', i)
                            .replace('{{total}}', total)
                    ),
            });
            showToast(_t('forum.tipSentThanks', 'Tip sent, thank you for your support!'), 'success');
            this.loadPostDetail(postId);
        } catch (e) {
            if (!txHash && _isWalletRejection(e)) {
                showToast(_t('forum.paymentCancelled', 'Payment cancelled'), 'warning');
            } else {
                showToast(_t('forum.tipFailed', 'Tip failed') + ': ' + ((e && e.message) || _t('forum.pleaseRetry', 'Please try again')), 'error');
            }
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = restore;
                AppUtils.refreshIcons();
            }
        }
    },

    /**
     * 打賞金額視窗（USDC；範圍來自 /api/premium/pricing 的 forum.prices，後端會再驗）。
     * 回傳金額（到分），取消回 null。
     */
    _promptTipAmount() {
        const min = Number(getPrice('tip_min') ?? 0.1);
        const max = Number(getPrice('tip_max') ?? 100);
        const def = Number(getPrice('tip') ?? 1);
        const esc = (s) => (typeof SecurityUtils !== 'undefined' ? SecurityUtils.escapeHTML(String(s)) : String(s));
        const range = { min: min.toFixed(2), max: max.toFixed(2) };
        return new Promise((resolve) => {
            document.getElementById('forum-tip-amount')?.remove();
            const overlay = document.createElement('div');
            overlay.id = 'forum-tip-amount';
            overlay.className = 'fixed inset-0 z-[80] bg-background/80 backdrop-blur-sm flex items-center justify-center p-4';
            overlay.innerHTML = `
                <div class="bg-surface w-full max-w-md p-6 rounded-[2rem] border border-borderLight shadow-2xl space-y-4">
                    <h3 class="font-serif text-lg text-secondary text-center">${esc(_t('forum.tipModal.title', 'Tip this post'))}</h3>
                    <label class="block text-xs text-textMuted" for="forum-tip-amount-input">${esc(_t('forum.tipModal.amountLabel', 'Amount (USDC)'))}</label>
                    <input id="forum-tip-amount-input" type="number" inputmode="decimal" min="${min}" max="${max}" step="0.01" value="${def}"
                        class="w-full bg-background border border-borderLight rounded-xl px-4 py-3 text-sm font-mono text-secondary placeholder:text-textMuted/50 focus:border-primary/50 focus:outline-none">
                    <p class="text-sm text-textMuted leading-relaxed">${esc(_t('forum.tipModal.hint', 'Sent as USDC on Base straight to the author\'s wallet; the platform takes no cut. {{min}}–{{max}} USDC.', range).replace('{{min}}', range.min).replace('{{max}}', range.max))}</p>
                    <p data-tip-error class="hidden text-xs text-danger"></p>
                    <button data-tip-confirm class="w-full px-4 py-3 min-h-11 bg-primary hover:bg-primary/90 text-background font-bold rounded-xl transition">${esc(_t('forum.tipModal.payCta', 'Tip with wallet'))}</button>
                    <button data-tip-cancel class="w-full py-2 min-h-11 text-textMuted text-sm font-bold hover:text-secondary transition">${esc(_t('common.cancel', 'Cancel'))}</button>
                </div>`;
            document.body.appendChild(overlay);
            const input = overlay.querySelector('#forum-tip-amount-input');
            const errEl = overlay.querySelector('[data-tip-error]');
            const done = (value) => {
                overlay.remove();
                resolve(value);
            };
            overlay.querySelector('[data-tip-cancel]').addEventListener('click', () => done(null));
            overlay.querySelector('[data-tip-confirm]').addEventListener('click', () => {
                const value = Number(input.value);
                const cents = Math.round(value * 100);
                if (!Number.isFinite(value) || value < min || value > max || Math.abs(value * 100 - cents) > 1e-6) {
                    errEl.textContent = _t('forum.tipModal.amountInvalid', 'Enter an amount between {{min}} and {{max}} USDC (up to 2 decimals)', range)
                        .replace('{{min}}', range.min)
                        .replace('{{max}}', range.max);
                    errEl.classList.remove('hidden');
                    return;
                }
                done(cents / 100);
            });
        });
    },

    // ===========================================
    // Create Post Logic
    // ===========================================
    initCreatePage() {
        const log = (msg, data = {}) => {
            window.APP_CONFIG?.DEBUG_MODE && console.log('[CreatePost]', msg, data);
        };

        // 發文費顯示：FORUM_POST_FEE_USD 可以是 0（冷啟動免費）。價格跟會員狀態各自非同步
        // 載入，兩邊到的時候都呼叫一次；會員狀態還沒到就先當非 Premium。
        let knownIsPro = false;
        const applyFeeUI = (isPro = knownIsPro) => {
            knownIsPro = isPro;
            const fee = getPrice('create_post');
            if (fee === null) return; // 價格還沒載入——forum-prices-updated 會再叫一次
            const amount = Number(fee).toFixed(2);
            const feeFree = !(Number(fee) > 0);
            const paySpan = document.querySelector('#post-form button[type="submit"] > span');
            if (paySpan) {
                paySpan.textContent =
                    isPro || feeFree
                      ? _t('forum.publishPost', 'Publish Post')
                      : _t('forum.payUsdcAndPost', `Pay ${amount} USDC & Post`, { amount }).replace('{{amount}}', amount);
            }
            const cost = document.getElementById('post-cost');
            if (cost) cost.textContent = isPro || feeFree ? _t('forum.create.free', 'Free') : `${amount} USDC`;
            const premiumHint = document.getElementById('post-cost-premium-hint');
            if (premiumHint) premiumHint.classList.toggle('hidden', isPro || feeFree);
            const requirements = document.getElementById('post-requirements');
            const requirementsText = document.getElementById('post-requirements-text');
            if (requirements && requirementsText) {
                const needsPayment = !isPro && !feeFree;
                requirements.classList.toggle('hidden', !needsPayment);
                if (needsPayment) {
                    requirementsText.textContent = _t(
                        'forum.create.paidRequirements',
                        `Each post costs ${amount} USDC (free for Premium). Before posting, have an EVM wallet bound in Settings, USDC on Base in it, and a little ETH on Base for gas.`,
                        { amount }
                    ).replace('{{amount}}', amount);
                }
            }
        };
        document.addEventListener('forum-prices-updated', () => applyFeeUI());
        applyFeeUI();

        const updateUIForMembership = async () => {
            const userId = AuthManager.currentUser?.user_id || AuthManager.currentUser?.uid;
            if (!userId) {
                console.warn('[CreatePost] No user ID found for UI update');
                return;
            }

            const limitDisplay = document.getElementById('daily-limit-display');

            try {
                const limitsData = await ForumAPI.checkLimits();
                window.APP_CONFIG?.DEBUG_MODE &&
                    console.log('[CreatePost] UI Update limits data:', limitsData);

                if (limitsData.success) {
                    const isPro = limitsData.membership?.is_premium ?? false;
                    // 使用後端返�??��??��?fallback 使用?��?載入?��?�?
                    const defaultLimit = getLimit('daily_post_free');
                    const postLimit = limitsData.limits?.post || {
                        count: 0,
                        limit: defaultLimit,
                        remaining: defaultLimit,
                    };

                    // Update Daily Limit Display
                    if (limitDisplay) {
                        if (isPro) {
                            limitDisplay.innerHTML = `
                                <div class="bg-primary/10 text-primary px-3 py-1 rounded-full border border-primary/20 flex items-center gap-1.5 font-bold">
                                    <i data-lucide="crown" class="w-3 h-3"></i>
                                    ${_esc(_t('forum.dailyLimitUnlimited', 'Daily limit: unlimited'))}
                                </div>
                            `;
                        } else {
                            const total =
                                postLimit.limit !== null
                                    ? postLimit.limit
                                    : getLimit('daily_post_free') || 0;
                            const used = postLimit.count || 0;
                            // Ensure remaining logic is consistent
                            const remaining =
                                postLimit.remaining !== undefined
                                    ? postLimit.remaining
                                    : total - used;
                            const isLow = remaining <= 0;

                            limitDisplay.innerHTML = `
                                <div class="bg-surfaceHighlight px-3 py-1 rounded-full border border-borderLight flex items-center gap-1.5">
                                    <span class="opacity-60">${window.I18n ? window.I18n.t('forum.dailyPosts') : 'Daily Posts:'}</span>
                                    <span class="font-bold ${isLow ? 'text-danger' : 'text-success'}">${used}/${total}</span>
                                </div>
                            `;
                        }
                        AppUtils.refreshIcons();
                    }

                    // Update Submit Button and Cost Info
                    applyFeeUI(isPro);
                }
            } catch (error) {
                console.warn('[CreatePost] Failed to update UI status:', error);
                if (limitDisplay) {
                    if (error?.status === 401) {
                        limitDisplay.innerHTML =
                            `<span class="text-amber-600 text-xs">${window.I18n ? window.I18n.t('forum.loginExpiredPleaseRefresh') : 'Login expired, please refresh the page'}</span>`;
                    } else {
                        limitDisplay.innerHTML =
                            '<span class="text-danger text-xs">' + (window.I18n ? window.I18n.t('common.connectionError') : 'Connection Error') + '</span>';
                    }
                }
            }
        };

        if (AuthManager.currentUser) {
            updateUIForMembership();
        } else {
            // Auth completes async ??re-run when user logs in
            const onAuthReady = () => {
                if (AuthManager.currentUser) {
                    updateUIForMembership();
                    window.removeEventListener('auth-success', onAuthReady);
                }
            };
            window.addEventListener('auth-success', onAuthReady);
            // Also poll briefly in case auth restores from backend before event fires
            const checkAuth = setInterval(() => {
                if (AuthManager.currentUser) {
                    clearInterval(checkAuth);
                    updateUIForMembership();
                }
            }, 500);
            setTimeout(() => clearInterval(checkAuth), 10000); // stop after 10s
        }

        // 切換語言：發文按鈕與每日上限是 JS 寫進去的文字，data-i18n 管不到，要重寫一次
        window.addEventListener('languageChanged', () => {
            applyFeeUI();
            if (AuthManager.currentUser) updateUIForMembership();
        });

        // ?��??��??�統計�???
        const initCharCounters = () => {
            const titleInput = document.getElementById('input-title');
            const contentInput = document.getElementById('input-content');
            const titleCurrent = document.getElementById('title-current');
            const contentCurrent = document.getElementById('content-current');
            const titleMax = document.getElementById('title-max');
            const contentMax = document.getElementById('content-max');

            // 從�?端�?置獲?��??��??��?端�??��??��?
            const MAX_TITLE = 200;
            const MAX_CONTENT = 10000;

            // ?�新顯示?��?大�?
            if (titleMax) titleMax.textContent = MAX_TITLE;
            if (contentMax) contentMax.textContent = MAX_CONTENT;

            // 標�?字數統�?
            if (titleInput && titleCurrent) {
                const updateTitleCount = () => {
                    const count = titleInput.value.length;
                    titleCurrent.textContent = count;

                    // 顏色變�??�示
                    const titleCounter = document.getElementById('title-counter');
                    if (count > MAX_TITLE * 0.9) {
                        titleCurrent.className = 'text-danger font-bold';
                        titleCounter?.classList.add('border-danger/30');
                    } else if (count > MAX_TITLE * 0.7) {
                        titleCurrent.className = 'text-amber-600 font-bold';
                        titleCounter?.classList.remove('border-danger/30');
                    } else {
                        titleCurrent.className = 'text-primary font-bold';
                        titleCounter?.classList.remove('border-danger/30');
                    }
                };

                titleInput.addEventListener('input', updateTitleCount);
                titleInput.addEventListener('paste', () => setTimeout(updateTitleCount, 10));
                updateTitleCount(); // ?��???
            }

            // ?�容字數統�?
            if (contentInput && contentCurrent) {
                const updateContentCount = () => {
                    const count = contentInput.value.length;
                    contentCurrent.textContent = count;

                    // 顏色變�??�示
                    const contentCounter = document.getElementById('content-counter');
                    if (count > MAX_CONTENT * 0.9) {
                        contentCurrent.className = 'text-danger font-bold';
                        contentCounter?.classList.add('border-danger/30');
                    } else if (count > MAX_CONTENT * 0.7) {
                        contentCurrent.className = 'text-amber-600 font-bold';
                        contentCounter?.classList.remove('border-danger/30');
                    } else {
                        contentCurrent.className = 'text-primary font-bold';
                        contentCounter?.classList.remove('border-danger/30');
                    }
                };

                contentInput.addEventListener('input', updateContentCount);
                contentInput.addEventListener('paste', () => setTimeout(updateContentCount, 10));
                updateContentCount(); // ?��???
            }

            window.APP_CONFIG?.DEBUG_MODE &&
                console.log('[CharCounter] Character counters initialized');
        };

        // ?��??��???
        initCharCounters();

        // 撰寫／預覽：用文章頁同一個 renderer（markdown-it＋DOMPurify），看到的就是發出去的樣子
        const modeBtns = document.querySelectorAll('#post-form [data-mode]');
        const contentEl = document.getElementById('input-content');
        const previewEl = document.getElementById('content-preview');
        const setMode = (mode) => {
            if (!contentEl || !previewEl) return;
            const preview = mode === 'preview';
            modeBtns.forEach((b) => b.setAttribute('aria-selected', String(b.dataset.mode === mode)));
            if (preview) {
                previewEl.innerHTML = contentEl.value.trim()
                    ? typeof SecurityUtils !== 'undefined'
                        ? SecurityUtils.renderMarkdownSafely(contentEl.value)
                        : `<p>${_esc(contentEl.value)}</p>`
                    : `<p class="text-textMuted">${_esc(_t('forum.create.previewEmpty', 'Nothing to preview yet'))}</p>`;
            }
            contentEl.classList.toggle('hidden', preview);
            previewEl.classList.toggle('hidden', !preview);
        };
        modeBtns.forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode)));
        // 在預覽模式按發文：先切回撰寫，否則內文空白時瀏覽器驗證要聚焦被藏起來的欄位，會靜默擋下
        document
            .querySelector('#post-form button[type="submit"]')
            ?.addEventListener('click', () => setMode('write'));

        // 發文前的內容檢查（2026-10-01 試驗）：打字停下來自動檢查，綠燈（或檢查服務暫時不在）才能按發文
        const moderationGate = createModerationGate({
            titleEl: document.getElementById('input-title'),
            contentEl: document.getElementById('input-content'),
            statusEl: document.getElementById('moderation-status'),
            submitBtn: document.querySelector('#post-form button[type="submit"]'),
            check: (title, content) => AppAPI.post('/api/forum/posts/check', { title, content }),
            t: (key, vars) => _t(key, key, vars),
            refreshIcons: () => AppUtils.refreshIcons(),
        });

        document.getElementById('post-form')?.addEventListener('submit', async (e) => {
            e.preventDefault();
            window.APP_CONFIG?.DEBUG_MODE && console.log('[CreatePost] V38 Handler Active');
            window.APP_CONFIG?.DEBUG_MODE && console.log('[CreatePost] Form submitted');

            // Disable button to prevent double submit
            const submitBtn = document.querySelector('button[type="submit"]');
            let originalBtnContent = '';

            if (submitBtn) {
                if (submitBtn.disabled) return; // Already processing（或內容檢查還沒亮綠燈）
                moderationGate.setBusy(true);
                submitBtn.disabled = true;
                originalBtnContent = submitBtn.innerHTML;
                submitBtn.innerHTML =
                    `<i class="animate-spin" data-lucide="loader-2"></i> ${window.I18n ? window.I18n.t('forum.processing') : 'Processing'}...`;
                AppUtils.refreshIcons();
            }

            // Function to reset button state
        const resetButton = () => {
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerHTML = originalBtnContent;
                AppUtils.refreshIcons();
            }
            // 交回內容檢查決定按鈕能不能按
            moderationGate.setBusy(false);
        };

        if (!AuthManager?.currentUser) {
            showToast(window.I18n ? window.I18n.t('forum.pleaseLoginFirst') : 'Please log in first', 'warning');
            resetButton();
            return;
        }

            // 付款前先確定內容檢查通過（內容改過就立刻重查）：先付了錢才被擋，錢就卡住了
            const moderation = await moderationGate.ensureChecked();
            if (!moderation.canSubmit) {
                if (moderation.state === 'block') {
                    showToast(_t('forum.moderation.blockedToast', 'Your post did not pass the content check. Please edit it and try again.'), 'warning');
                }
                resetButton();
                return;
            }

            const title = document.getElementById('input-title').value;
            const content = document.getElementById('input-content').value;
            const category =
                document.querySelector('#post-form input[name="category"]:checked')?.value || 'analysis';
            const tagsStr = document.getElementById('input-tags').value;
            const tags = tagsStr
                .split(' ')
                .map((t) => t.replace('#', '').trim())
                .filter((t) => t);

            const postAmount = getPrice('create_post');
            if (postAmount === null) {
                showToast(window.I18n ? window.I18n.t('forum.priceLoadFailed') : 'Price settings failed to load, please refresh the page', 'error');
                resetButton();
                return;
            }
            const createPostWarning = document.getElementById('create-post-warning');
            const setCreatePostWarning = (message) => {
                if (!createPostWarning) {
                    showToast(message, 'warning');
                    return;
                }
                createPostWarning.textContent = message;
                createPostWarning.classList.remove('hidden');
            };
            const clearCreatePostWarning = () => {
                if (!createPostWarning) return;
                createPostWarning.textContent = '';
                createPostWarning.classList.add('hidden');
            };
            let createOrder = null; // USDC 訂單（/api/forum/posts/payment-order）
            let txHash = null; // 錢包送出的 tx hash——發文失敗時給使用者留存
            clearCreatePostWarning();

            const userId = AuthManager.currentUser?.user_id || AuthManager.currentUser?.uid;
            let isProMember = false;

            if (userId) {
                try {
                    // First, check limits and membership status
                    window.APP_CONFIG?.DEBUG_MODE &&
                        console.log('[CreatePost] Checking limits and membership...');
                    const limitsData = await ForumAPI.checkLimits();

                    if (limitsData.success) {
                        const postLimit = limitsData.limits.post;
                        isProMember = limitsData.membership?.is_premium ?? false;

                        // Check if limit reached
                        // limit === null means unlimited (Pro)
                        if (postLimit.limit !== null && postLimit.remaining <= 0) {
                            console.warn('[CreatePost] Daily limit reached:', postLimit);

                            // Custom Styled Modal
                            const modal = document.createElement('div');
                            modal.className =
                                'fixed inset-0 bg-background/90 backdrop-blur-sm z-[150] flex items-center justify-center p-4 animate-fade-in';
                            modal.innerHTML = `
                                            <div class="bg-surface w-full max-w-sm max-h-[80dvh] overflow-y-auto custom-scrollbar p-6 rounded-3xl border border-white/10 shadow-2xl animate-scale-in text-center">
                                                <div class="w-16 h-16 bg-amber-500/10 rounded-full flex items-center justify-center mx-auto mb-5 border border-amber-600/20">
                                                    <i data-lucide="lock" class="w-8 h-8 text-amber-600"></i>
                                                </div>
                                                <h3 class="text-xl font-bold text-secondary mb-2">${window.I18n ? window.I18n.t('forum.dailyLimitReached') : 'Daily posting limit reached'}</h3>
                                                <div class="text-textMuted text-sm mb-6 leading-relaxed">
                                                    ${window.I18n ? window.I18n.t('forum.postsCreatedToday') : 'Posts created today'} <span class="text-textMain font-bold text-base">${postLimit.count}</span> / <span class="text-textMain font-bold text-base">${postLimit.limit}</span> ${window.I18n ? window.I18n.t('forum.articles') : 'articles'}<br>
                                                    <span class="opacity-70">${window.I18n ? window.I18n.t('forum.upgradePremiumHint') : 'Upgrade to Premium for higher or unlimited quotas'}</span>
                                                </div>
                                                </div>
                                                <div class="flex flex-col gap-3">
                                                    <button data-click="smoothNavigate" data-click-arg="%2Fstatic%2Fforum%2Fpremium.html" class="w-full py-3.5 bg-gradient-to-r from-primary to-primary/80 hover:to-primary text-background font-bold rounded-2xl transition shadow-lg flex items-center justify-center gap-2 transform active:scale-95">
                                                        <i data-lucide="crown" class="w-4 h-4"></i>
                                                        <span>${window.I18n ? window.I18n.t('forum.goToPremium') : 'Go to Premium'}</span>
                                                    </button>
                                                    <button data-click="removeClosest" data-click-arg=".fixed" class="w-full py-3.5 bg-surfaceHighlight hover:bg-white/10 text-textMuted font-bold rounded-2xl transition border border-white/5 hover:text-white">
                                                        ${window.I18n ? window.I18n.t('forum.close') : 'Close'}
                                                    </button>
                                                    </button>
                                                </div>
                                            </div>
                                        `;
                            document.body.appendChild(modal);
                            AppUtils.refreshIcons();

                            resetButton();
                            return; // STOP HERE - Do not proceed to payment
                        }
                    }
                } catch (error) {
                    console.warn('[CreatePost] Failed to check limits:', error);
                    if (error?.status === 401) {
                        if (typeof showToast === 'function')
                            showToast(window.I18n ? window.I18n.t('forum.loginExpired') : 'Login expired, please log in again', 'error');
                        resetButton();
                        return;
                    } else {
                        if (typeof showToast === 'function')
                            showToast(window.I18n ? window.I18n.t('forum.checkLimitFailed') : 'Failed to check posting limit, please try again later', 'warning');
                        // 驗證錯誤時允許繼續，由伺服器端限制把關
                    }
                }
            }

            window.APP_CONFIG?.DEBUG_MODE &&
                console.log(
                    `[CreatePost] User: ${userId}, IsPro: ${isProMember}, Amount: ${postAmount}`
                );

            // Premium 免費；發文費設 0（冷啟動）時免費會員也不用付
            const postingIsFree = isProMember || !(Number(postAmount) > 0);
            if (postingIsFree) {
                createOrder = null;
                window.APP_CONFIG?.DEBUG_MODE &&
                    console.log('[CreatePost] Posting is free, skipping payment');
            } else {
                // USDC on Base 發文費：Telegram 內不收加密貨幣付款，只給「用瀏覽器開啟」
                if (isTelegramMiniApp()) {
                    showForumTmaPayNotice();
                    resetButton();
                    return;
                }
                const bound = await _prepareUsdcPayer();
                if (bound === false) {
                    resetButton();
                    return;
                }
                // 建單（金額含唯一尾數、收款人＝平台，簽進訂單）→ 錢包直付
                try {
                    createOrder = await AppAPI.post('/api/forum/posts/payment-order', {});
                    if (submitBtn) submitBtn.textContent = _t('premium.walletPaySending', 'Check your wallet…');
                    txHash = await walletSendUsdc(createOrder, bound);
                } catch (paymentError) {
                    console.error('[CreatePost] USDC payment failed:', paymentError);
                    if (_isWalletRejection(paymentError)) {
                        showToast(_t('forum.paymentCancelled', 'Payment cancelled'), 'warning');
                    } else {
                        showToast(paymentError?.message || _t('forum.paymentFailed', 'Payment failed, please try again'), 'error');
                    }
                    resetButton();
                    return;
                }
            }

            try {
                const postData = {
                    board_slug: 'crypto',
                    category,
                    title,
                    content,
                    tags,
                };
                let result;
                if (postingIsFree) {
                    // 免費發文；後端依 membership／發文費跳過付款驗證（不會把 hash 寫進 DB）
                    if (isProMember) postData.payment_tx_hash = 'pro_member_free';
                    result = await ForumAPI.createPost(postData);
                } else {
                    // USDC 發文費：帶訂單＋tx hash，後端鏈上驗證（等區塊確認，輪詢重試）
                    postData.order_token = createOrder.order_token;
                    postData.payment_tx_hash = txHash;
                    result = await pollUsdcClaim(() => ForumAPI.createPost(postData), {
                        onProgress: (i, total) => {
                            if (submitBtn) {
                                submitBtn.textContent = _t('premium.verifyingAttempt', 'Verifying ({{current}}/{{total}})…')
                                    .replace('{{current}}', i)
                                    .replace('{{total}}', total);
                            }
                        },
                    });
                }
                window.APP_CONFIG?.DEBUG_MODE &&
                    console.log('[Forum] Post created successfully:', result);

                if (window.UIShell && typeof window.UIShell.clearToasts === 'function') {
                    window.UIShell.clearToasts();
                }
                clearCreatePostWarning();

                // Success Modal
                const successModal = document.createElement('div');
                successModal.className =
                    'fixed inset-0 bg-background/90 backdrop-blur-sm z-[150] flex items-center justify-center p-4 animate-fade-in';
                successModal.innerHTML = `
                                <div class="bg-surface w-full max-w-sm max-h-[80dvh] overflow-y-auto custom-scrollbar p-6 rounded-3xl border border-white/10 shadow-2xl animate-scale-in text-center">
                                    <div class="w-16 h-16 bg-success/10 rounded-full flex items-center justify-center mx-auto mb-5 border border-success/20">
                                        <i data-lucide="check-circle-2" class="w-8 h-8 text-success"></i>
                                    </div>
                                    <h3 class="text-xl font-bold text-secondary mb-2">${window.I18n ? window.I18n.t('forum.postPublishedSuccess') : 'Post published successfully'}</h3>
                                    <div class="text-textMuted text-sm mb-6">
                                        ${window.I18n ? window.I18n.t('forum.postCreatedSuccessfully') : 'Post created successfully.'}<br>
                                        <span class="text-primary animate-pulse">${window.I18n ? window.I18n.t('forum.redirectingToPost') : 'Redirecting to post page...'}...</span>
                                    </div>
                                    <button id="btn-go-now" class="w-full py-3.5 bg-gradient-to-r from-success/80 to-success text-background font-bold rounded-2xl transition shadow-lg transform active:scale-95">
                                        ${window.I18n ? window.I18n.t('forum.viewNow') : 'View now'}
                                    </button>
                                </div>
                            `;
                document.body.appendChild(successModal);
                AppUtils.refreshIcons();

                // Determine redirect URL
                const targetUrl = result.post_id
                    ? `/static/forum/post.html?id=${result.post_id}`
                    : this.FORUM_HOME_URL;

                // Redirect Action (with smooth transition)
                const doRedirect = () => {
                    window.APP_CONFIG?.DEBUG_MODE &&
                        console.log('[Forum] Redirecting to:', targetUrl);
                    if (typeof smoothNavigate === 'function') {
                        smoothNavigate(targetUrl);
                    } else {
                        try {
                            window.location.assign(targetUrl);
                        } catch (e) {
                            window.location.href = targetUrl;
                        }
                    }
                };

                // Bind button
                document.getElementById('btn-go-now').onclick = doRedirect;

                // Auto redirect
                setTimeout(doRedirect, 2000);

                // Update button state (just in case)
                if (submitBtn) {
                    submitBtn.disabled = true;
                    submitBtn.innerHTML =
                        `<i class="w-4 h-4 animate-spin" data-lucide="loader-2"></i> ${window.I18n ? window.I18n.t('forum.redirecting') : 'Redirecting'}...`;
                    AppUtils.refreshIcons();
                }
            } catch (err) {
                console.error('[Forum] CreatePost API failed:', err);
                // 伺服器重檢擋下（前端檢查時檢查服務還沒起來）：重查一次，好在狀態列顯示原因
                const blocked = err?.message === 'content_blocked';
                if (blocked) moderationGate.recheck();
                // If payment was made but post failed, we should alert the user to copy their content
                if (txHash && txHash !== 'pro_member_free' && !txHash.startsWith('mock_')) {
                    // 顯示?�好?�錯�?Modal ?��? alert
                    const errorModal = document.createElement('div');
                    errorModal.className =
                        'fixed inset-0 bg-background/90 backdrop-blur-sm z-[150] flex items-center justify-center p-4 animate-fade-in';
                    errorModal.innerHTML = `
                        <div class="bg-surface w-full max-w-md max-h-[80dvh] overflow-y-auto custom-scrollbar p-6 rounded-3xl border border-white/10 shadow-2xl animate-scale-in">
                            <div class="w-16 h-16 bg-danger/10 rounded-full flex items-center justify-center mx-auto mb-5 border border-danger/20">
                                <i data-lucide="alert-triangle" class="w-8 h-8 text-danger"></i>
                            </div>
                            <h3 class="text-xl font-bold text-secondary mb-3 text-center">${window.I18n ? window.I18n.t('forum.postFailedPaymentDone') : 'Post failed, but payment completed'}</h3>
                            <div class="text-textMuted text-sm mb-4 leading-relaxed">
                                <p class="mb-2">${window.I18n ? window.I18n.t('forum.paymentDonePostFailed') : 'Payment completed, but post creation failed. Please keep the following transaction ID and contact customer service.'}</p>
                                <div class="bg-background/50 p-3 rounded-xl border border-white/10 mb-3">
                                    <div class="text-xs text-textMuted mb-1">${window.I18n ? window.I18n.t('forum.transactionId') : 'Transaction ID'}</div>
                                    <div class="text-textMain font-mono text-xs break-all" id="error-txhash">${_esc(txHash || '')}</div>
                                </div>
                                <p class="text-xs opacity-60">${window.I18n ? window.I18n.t('forum.errorMsg') : 'Error message'}：${_esc(err.message || '')}</p>
                            </div>
                            <div class="flex flex-col gap-2">
                                <button id="copy-txhash-btn" class="w-full py-3 bg-primary hover:brightness-110 text-background font-bold rounded-2xl transition shadow-lg flex items-center justify-center gap-2">
                                    <i data-lucide="copy" class="w-4 h-4"></i>
                                    <span>${window.I18n ? window.I18n.t('forum.copyTxId') : 'Copy transaction ID'}</span>
                                </button>
                                <button data-click="removeClosest" data-click-arg=".fixed" class="w-full py-3 bg-surfaceHighlight hover:bg-white/10 text-textMuted font-bold rounded-2xl transition border border-white/5">
                                    ${window.I18n ? window.I18n.t('common.close') : 'Close'}
                                </button>
                            </div>
                        </div>
                    `;
                    document.body.appendChild(errorModal);
                    AppUtils.refreshIcons();

                    // 複製?�能
                    document.getElementById('copy-txhash-btn').onclick = () => {
                        navigator.clipboard
                            .writeText(txHash)
                            .then(() => {
                                showToast(window.I18n ? window.I18n.t('forum.txIdCopied') : 'Transaction ID copied', 'success');
                            })
                            .catch(() => {
                                // Fallback for older browsers
                                const textArea = document.createElement('textarea');
                                textArea.value = txHash;
                                document.body.appendChild(textArea);
                                textArea.select();
                                document.execCommand('copy');
                                document.body.removeChild(textArea);
                                showToast(window.I18n ? window.I18n.t('forum.txIdCopied') : 'Transaction ID copied', 'success');
                            });
                    };
                } else if (blocked) {
                    showToast(_t('forum.moderation.blockedToast', 'Your post did not pass the content check. Please edit it and try again.'), 'warning');
                } else {
                    showToast((window.I18n ? window.I18n.t('forum.postFailed') : 'Post failed') + ': ' + userFacingMessage(err), 'error');
                }
                resetButton();
            }
        });
    },

    // ===========================================
    // Dashboard Logic
    // ===========================================
    async initDashboardPage() {
        if (!AuthManager.currentUser) {
            if (typeof smoothNavigate === 'function') {
                smoothNavigate(this.FORUM_HOME_URL);
            } else {
                window.location.href = this.FORUM_HOME_URL;
            }
            return;
        }

        const user = AuthManager.currentUser;

        const usernameEl = document.getElementById('nav-username');
        const avatarEl = document.getElementById('nav-avatar');

        if (usernameEl) {
            usernameEl.textContent = user.username || user.pi_username || 'User';
        }
        if (avatarEl && user.username) {
            // XSS Fix: 使用 textContent ?�代 innerHTML
            const span = document.createElement('span');
            span.className = 'text-primary font-bold';
            span.textContent = user.username[0].toUpperCase();
            avatarEl.innerHTML = '';
            avatarEl.appendChild(span);
        }

        const loaders = [
            this.loadWalletStatus().catch((err) =>
                console.error('Wallet Status Load Failed:', err)
            ),
            this.loadStats().catch((err) => console.error('Stats Load Failed:', err)),
            this.loadMyPosts().catch((err) => console.error('Posts Load Failed:', err)),
            this.loadTransactions().catch((err) => console.error('Tx Load Failed:', err)),
        ];

        await Promise.allSettled(loaders);
    },

    async loadWalletStatus() {
        const statusText = document.getElementById('wallet-status-text');
        const usernameEl = document.getElementById('wallet-username');
        const actionArea = document.getElementById('wallet-action-area');
        const iconEl = document.getElementById('wallet-icon');

        if (!statusText || !actionArea) return;
        // data-i18n 跟著狀態改，語系重掃才不會把「已連接」洗回 HTML 預設的「載入中」
        const setStatusText = (key, fallback) => {
            statusText.dataset.i18n = key;
            statusText.textContent = window.I18n ? window.I18n.t(key) : fallback;
        };

        if (typeof window.getWalletStatus !== 'function') {
            setStatusText('error.systemAuth', 'System Error (Auth)');
            statusText.classList.add('text-danger');
            return;
        }

        try {
            const status = await getWalletStatus();

            if (status.has_wallet || status.auth_method === 'pi_network') {
                setStatusText('forum.connected', 'Connected');
                statusText.classList.remove('text-textMuted', 'text-danger');
                statusText.classList.add('text-success');

                if (iconEl) {
                    iconEl.classList.remove('bg-primary/20');
                    iconEl.classList.add('bg-success/20');
                    iconEl.innerHTML =
                        '<i data-lucide="check-circle" class="w-7 h-7 text-success"></i>';
                }

                if (status.pi_username) {
                    usernameEl.textContent = `@${status.pi_username}`;
                    usernameEl.classList.remove('hidden');
                }

                actionArea.innerHTML = `
                    <div class="flex items-center gap-2 text-success">
                        <i data-lucide="shield-check" class="w-5 h-5"></i>
                        <span class="text-sm font-bold" data-i18n="forum.verifiedBadge">Verified</span>
                    </div>
                `;
            } else {
                // 已登入但沒綁 EVM 錢包（Google／Telegram 帳號）。以前寫「無法使用」＋「登入帳號」
                // （safeEvmLogin），但這頁沒載 evm-auth.js，按了沒反應——改成到設定頁綁定
                setStatusText('auth.notBound', 'Not bound');
                statusText.classList.remove('text-success', 'text-danger');
                statusText.classList.add('text-textMuted');

                actionArea.innerHTML = `
                    <a href="/static/index.html#settings" data-tma-hide class="bg-primary/10 hover:bg-primary/20 text-primary px-4 py-2 rounded-xl flex items-center gap-2 transition text-sm font-bold border border-primary/20">
                        <i data-lucide="wallet" class="w-4 h-4"></i>
                        ${_esc(_t('premium.bindWalletCta', 'Bind wallet'))}
                    </a>
                `;
            }

            AppUtils.refreshIcons();
        } catch (e) {
            setStatusText('forum.loadFailed', 'Load failed');
            statusText.classList.add('text-danger');

            actionArea.innerHTML = `
                <button data-click="reloadPage" class="text-xs text-textMuted hover:text-white underline">
                    Retry
                </button>
            `;
        }
    },

    async loadStats() {
        try {
            const data = await ForumAPI.getMyStats();
            if (data.success && data.stats) {
                const s = data.stats;
                const postCountEl = document.getElementById('dash-post-count');
                const tipsRecEl = document.getElementById('dash-tips-received');

                if (postCountEl) postCountEl.textContent = s.post_count || 0;
                // 後端只加總 USDC 打賞（2026-09-25 前的 TON 打賞不算進來）
                if (tipsRecEl) tipsRecEl.textContent = (Number(s.tips_received) || 0).toFixed(2);
            }

            const sentData = await ForumAPI.getMyTipsSent();
            const tipsSentEl = document.getElementById('dash-tips-sent');
            if (tipsSentEl) {
                if (sentData.success && sentData.tips) {
                    // 卡片單位是 USDC：舊的 TON 打賞不混進同一個數字（明細照樣列出）
                    const totalSent = sentData.tips
                        .filter((tip) => _txAsset(tip) === 'USDC')
                        .reduce((acc, tip) => acc + (Number(tip.amount) || 0), 0);
                    tipsSentEl.textContent = totalSent.toFixed(2);
                } else {
                    tipsSentEl.textContent = '0';
                }
            }
        } catch (e) {
            console.error('loadStats error', e);
        }
    },

    async loadMyPosts() {
        const container = document.getElementById('dash-posts-list');
        if (!container) return;

        try {
            const data = await ForumAPI.getMyPosts();
            const posts = data.posts || [];

            container.innerHTML = '';
            if (posts.length === 0) {
            container.innerHTML =
                `<div class="text-center text-textMuted py-4">${window.I18n ? window.I18n.t('forum.noPosts') : 'No posts yet'}</div>`;
                return;
            }

            posts.forEach((post) => {
                const el = document.createElement('div');
                el.className =
                    'flex items-center justify-between border-b border-white/5 pb-4 last:border-0 last:pb-0';

                const pushCount = Math.max(0, post.push_count || 0);

                el.innerHTML = `
                    <div class="overflow-hidden mr-4">
                         <a href="/static/forum/post.html?id=${post.id}" class="font-bold text-textMain hover:text-primary transition truncate block">${typeof SecurityUtils !== 'undefined' ? SecurityUtils.escapeHTML(post.title || '') : post.title || ''}</a>
                         <div class="text-xs text-textMuted mt-1.5 flex items-center gap-2.5">
                            <span>${formatTWDate(post.created_at)}</span>
                            <span class="bg-white/10 px-2 py-0.5 rounded text-[10px] uppercase">${_esc(_categoryLabel(post.category))}</span>
                         </div>
                    </div>
                    <div class="flex items-center gap-4 text-xs text-textMuted shrink-0">
                        <span class="flex items-center gap-1.5"><i data-lucide="message-square" class="w-3.5 h-3.5"></i> ${post.comment_count}</span>
                        <span class="flex items-center gap-1.5 ${pushCount > 0 ? 'text-success' : ''}"><i data-lucide="thumbs-up" class="w-3.5 h-3.5"></i> ${pushCount}</span>
                        <span class="flex items-center gap-1.5 ${post.boo_count > 0 ? 'text-danger' : ''}"><i data-lucide="thumbs-down" class="w-3.5 h-3.5"></i> ${post.boo_count || 0}</span>
                    </div>
                `;
                container.appendChild(el);
            });
            AppUtils.refreshIcons();
        } catch (e) {
            console.error('loadMyPosts error', e);
            container.innerHTML = `<div class="text-center text-danger py-4">${window.I18n ? window.I18n.t('forum.loadFailed') : 'Failed to load'}</div>`;
        }
    },

    async loadTransactions() {
        const container = document.getElementById('dash-tx-list');
        if (!container) return;

        try {
            // 使用 Promise.allSettled 確�??��? API 失�??��??�顯示可?�數??
            const results = await Promise.allSettled([
                ForumAPI.getMyPayments(),
                ForumAPI.getMyTipsSent(),
            ]);

            // 記�?失�???API
            results.forEach((result, index) => {
                if (result.status === 'rejected') {
                    const apiNames = ['getMyPayments', 'getMyTipsSent'];
                    console.warn(`[Dashboard] ${apiNames[index]} failed:`, result.reason);
                }
            });

            // ?��??��?，失?��?使用空數�?
            const paymentsData =
                results[0].status === 'fulfilled' ? results[0].value : { payments: [] };
            const tipsSentData =
                results[1].status === 'fulfilled' ? results[1].value : { tips: [] };

            const payments = (paymentsData.payments || [])
                // 保�? Premium ?�員?�費?��?記�?但�?記為?�費
                .map((p) => {
                    const isFree = p.tx_hash === 'pro_member_free';
                    const isGrant = !!p.is_admin_grant;
                    // 金額與資產別以後端 display_amount／asset 為準（0x＝USDC，舊紀錄＝TON）
                    return {
                        ...p,
                        type: isFree ? 'post_payment_free' : 'post_payment',
                        asset: isGrant ? null : _txAsset(p),
                        amount: isFree || isGrant ? 0 : -(Number(p.display_amount ?? p.amount) || 0),
                        isFree: isFree,
                    };
                });
            const tips = (tipsSentData.tips || []).map((t) => ({
                ...t,
                type: 'tip_sent',
                asset: _txAsset(t),
                amount: -(Number(t.amount) || 0),
                title: `Tip: ${t.post_title || 'Post'}`,
            }));

            const allTx = [...payments, ...tips].sort(
                (a, b) => new Date(b.created_at) - new Date(a.created_at)
            );

            container.innerHTML = '';
            if (allTx.length === 0) {
                container.innerHTML =
                    '<div class="text-center text-textMuted py-4" data-i18n="forum.noTransactions">No transactions</div>';
                return;
            }

            allTx.slice(0, 20).forEach((tx, idx) => {
                const el = document.createElement('div');
                el.className =
                    'flex items-center justify-between border-b border-white/5 py-4 hover:bg-white/5 px-3 rounded-xl transition cursor-pointer last:border-0';

                el.dataset.txData = JSON.stringify(tx);
                el.onclick = function () {
                    ForumApp.showTransactionDetail(tx);
                };

                let icon = 'credit-card';
                let title = window.I18n?.t('wallet.txTypePayment') || 'Payment';

                if (tx.type === 'post_payment') {
                    icon = 'file-text';
                    title = window.I18n?.t('wallet.txTypePostFee') || 'Post Fee';
                } else if (tx.type === 'post_payment_free') {
                    icon = 'file-text';
                    title = window.I18n?.t('wallet.txTypePostFree') || 'Post (FREE)';
                } else if (tx.type === 'tip_sent') {
                    icon = 'gift';
                    title = tx.title || 'Tip Sent';
                }

                const safeTitle = typeof escapeHtml === 'function' ? escapeHtml(title) : title.replace(/</g, '&lt;');

                const amountClass = tx.isFree
                    ? 'text-success'
                    : tx.amount < 0
                      ? 'text-danger'
                      : 'text-success';
                const amountText = tx.isFree
                    ? 'FREE'
                    : tx.asset
                      ? `${tx.amount > 0 ? '+' : ''}${Math.abs(tx.amount).toFixed(2)} ${tx.asset}`
                      : '—';

                el.innerHTML = `
                    <div class="flex items-center gap-4 overflow-hidden">
                         <div class="w-11 h-11 rounded-full bg-surfaceHighlight flex items-center justify-center shrink-0">
                            <i data-lucide="${icon}" class="w-5 h-5 text-textMuted"></i>
                         </div>
                         <div class="overflow-hidden">
                              <div class="font-bold text-textMain truncate">${safeTitle}</div>
                             <div class="text-xs text-textMuted mt-0.5">${formatTWDate(tx.created_at)}</div>
                         </div>
                    </div>
                    <div class="text-right shrink-0">
                         <div class="font-bold ${amountClass}">${amountText}</div>
                         <div class="text-[10px] text-textMuted opacity-60 mt-0.5">${formatTWDate(tx.created_at)}</div>
                    </div>
                 `;
                container.appendChild(el);
            });

            AppUtils.refreshIcons();
        } catch (e) {
            console.error('loadTransactions error', e);
            container.innerHTML = `<div class="text-center text-danger py-4">${window.I18n ? window.I18n.t('forum.loadFailed') : 'Failed to load'}</div>`;
        }
    },

    showTransactionDetail(tx) {
        const txId = tx.tx_hash || tx.payment_tx_hash || 'N/A';
        const typeLabel =
            tx.type === 'post_payment' ? (window.I18n ? window.I18n.t('forum.postPublicationFee') : 'Post Publication Fee') : (window.I18n ? window.I18n.t('forum.articleTipSupport') : 'Article Tip Support');
        const status = 'Completed';
        const memo =
            tx.title || (tx.type === 'post_payment' ? (window.I18n ? window.I18n.t('forum.postingFee') : 'Forum Posting Fee') : (window.I18n ? window.I18n.t('forum.tipToAuthor') : 'Tip to Author'));

        const modal = document.createElement('div');
        modal.id = 'tx-detail-modal';
        modal.className =
            'fixed inset-0 bg-background/90 backdrop-blur-md z-[110] flex items-center justify-center p-4 animate-fade-in';
        modal.innerHTML = `
            <div class="bg-surface w-full max-w-md max-h-[80dvh] overflow-y-auto custom-scrollbar p-6 rounded-3xl border border-white/10 shadow-2xl animate-scale-in">
                <div class="flex justify-between items-center mb-6">
                    <h3 class="text-xl font-bold text-secondary" data-i18n="forum.txDetailTitle">Transaction Detail</h3>
                    <button data-click="removeById" data-click-arg="tx-detail-modal" class="text-textMuted hover:text-white transition">
                        <i data-lucide="x" class="w-6 h-6"></i>
                    </button>
                </div>

                <div class="space-y-4">
                    <div class="text-center py-6 bg-background/50 rounded-2xl border border-white/5 mb-4">
                        <div class="text-textMuted text-xs uppercase font-bold tracking-widest mb-1"data-i18n="forum.txAmount">Amount</div>
                        <div class="text-3xl font-bold text-primary">${tx.asset ? `${Math.abs(tx.amount).toFixed(2)} <span class="text-sm">${tx.asset === 'USDC' ? 'USDC' : 'TON'}</span>` : '—'}</div>
                    </div>

                    <div class="grid grid-cols-1 gap-4 text-sm">
                        <div class="flex justify-between border-b border-white/5 pb-2">
                            <span class="text-textMuted" data-i18n="forum.txType">Type</span>
                            <span class="text-secondary font-medium">${typeLabel}</span>
                        </div>
                        <div class="flex justify-between border-b border-white/5 pb-2">
                            <span class="text-textMuted" data-i18n="forum.txStatus">Status</span>
                            <span class="text-success font-bold flex items-center gap-1">
                                <i data-lucide="check-circle" class="w-3 h-3"></i> ${status}
                            </span>
                        </div>
                        <div class="flex justify-between border-b border-white/5 pb-2">
                            <span class="text-textMuted" data-i18n="forum.txDate">Date</span>
                            <span class="text-secondary">${formatTWDate(tx.created_at, true)}</span>
                        </div>
                        <div class="flex flex-col gap-1 border-b border-white/5 pb-2">
                            <span class="text-textMuted" data-i18n="forum.txId">Transaction ID</span>
                            <span class="text-primary font-mono text-[10px] break-all bg-white/5 p-2 rounded-lg mt-1">${txId}</span>
                        </div>
                        <div class="flex flex-col gap-1">
                            <span class="text-textMuted">Memo / Note</span>
                            <span class="text-secondary italic text-xs bg-white/5 p-3 rounded-lg mt-1">"${memo}"</span>
                        </div>
                    </div>
                </div>

                <button data-click="removeById" data-click-arg="tx-detail-modal"
                    class="w-full mt-8 py-4 bg-surfaceHighlight hover:bg-white/10 text-textMain font-bold rounded-2xl transition border border-white/5">
                    Close
                </button>
            </div>
        `;

        document.body.appendChild(modal);
        AppUtils.refreshIcons();
    },

    // ===========================================
    // Reporting Logic
    // ===========================================
    openReportModal(type, id) {
        if (!AuthManager.currentUser) return showToast(window.I18n ? window.I18n.t('forum.pleaseLoginFirst') : 'Please log in first', 'warning');

        const modal = document.getElementById('report-modal');
        if (!modal) return;

        document.getElementById('report-content-type').value = type;
        document.getElementById('report-content-id').value = id;
        document.getElementById('report-type').value = '';
        document.getElementById('report-description').value = '';

        // Clear previous errors
        const errorDiv = document.getElementById('report-error');
        if (errorDiv) errorDiv.classList.add('hidden');

        modal.classList.remove('hidden');
        AppUtils.refreshIcons();
    },

    closeReportModal() {
        const modal = document.getElementById('report-modal');
        if (modal) modal.classList.add('hidden');
    },

    async submitReport() {
        const contentType = document.getElementById('report-content-type').value;
        const contentId = document.getElementById('report-content-id').value;
        const reportType = document.getElementById('report-type').value;
        const description = document.getElementById('report-description').value;

        const errorDiv = document.getElementById('report-error');
        const errorMsg = document.getElementById('report-error-msg');

        const showError = (msg) => {
            if (errorDiv && errorMsg) {
                errorMsg.textContent = msg;
                errorDiv.classList.remove('hidden');
            } else {
                showToast(msg, 'error');
            }
        };

        if (!reportType) {
            return showError(_t('forum.selectReportReason', 'Please select a report reason.'));
        }

        const btn = document.getElementById('btn-submit-report');
        if (!btn) return;

        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = `<i class="animate-spin" data-lucide="loader-2"></i> ${window.I18n ? window.I18n.t('common.submitting') : 'Submitting...'}`;
        AppUtils.refreshIcons();

        // Clear previous error
        if (errorDiv) errorDiv.classList.add('hidden');

        try {
            const res = await AppAPI.post('/api/governance/reports', {
                content_type: contentType,
                content_id: parseInt(contentId),
                report_type: reportType,
                description: description,
            });

            showToast(window.I18n ? window.I18n.t('forum.reportSubmittedReview') : 'Report submitted, we will review it soon', 'success');
        } catch (e) {
            showError((window.I18n ? window.I18n.t('forum.submitFailed') : 'Submission failed') + ': ' + userFacingMessage(e));
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalText;
            AppUtils.refreshIcons();
        }
    },
};

// ?�露?�全局
window.ForumApp = ForumApp;
export { ForumApp };


// 確�???DOM 載入後執行�??��??��? forum ?�面，SPA 主�???switchTab 觸發�?
document.addEventListener('DOMContentLoaded', async () => {
    document.addEventListener('click', (e) => {
        const trigger = e.target.closest('.report-trigger');
        if (trigger) {
            const type = trigger.dataset.reportType;
            const id = trigger.dataset.reportId;
            if (type && id && window.ForumApp && ForumApp.openReportModal) {
                ForumApp.openReportModal(type, id);
            }
        }
    });
    // 論壇子頁（create/post/...）先前沒有人呼叫 I18n.init()，整頁停在英文 fallback；
    // 在 ForumApp 啟動前先完成 i18n 初始化（重複呼叫由 i18n.js 內部單例擋掉）
    if (window.I18n) {
        try { await window.I18n.init(); } catch (e) { console.error('[Forum] i18n Init Error:', e); }
    }
    const page = document.body.dataset.page;
    if (!page) return; // SPA mode: skip, forum content loaded via switchTab()
    // 這幾頁的頁面腳本（web/forum/js/*-page.js）會在 initializeAuth() 之後自己呼叫
    // ForumApp.init()；這裡再叫一次會整頁初始化兩遍（2026-09-26 量測：文章頁的文章與留言
    // 各抓 2 次）。保留頁面腳本那次——它較晚、等 auth 完成，原本最後的畫面就是它畫的。
    if (['post', 'create', 'dashboard'].includes(page)) return;
    if (window.ForumApp) {
        ForumApp.init();
    } else {
        const checkApp = setInterval(() => {
            if (window.ForumApp) {
                clearInterval(checkApp);
                ForumApp.init();
            }
        }, 100);
    }
});
