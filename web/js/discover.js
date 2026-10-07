/**
 * DiscoverTab — People & Projects 探索介面（Phase 3）
 *
 * 規格：design.md §10（People & Projects 探索模組）
 * - 搜尋 → 專案卡 → 詳情 → 收藏 → 前往 Manifund 官方頁（outbound link）
 * - 收藏為 Priority 1 輕量標記（無通知/背景工作；「追蹤更新」屬 Phase 5）
 * - 狀態：loading skeleton / empty / error / MCP 降級橫幅
 * - 安全：所有外部內容一律 escapeHTML 後才插入 DOM（AGENTS.md 安全渲染）
 */

var DiscoverTab = {
    _initialized: false,
    _disclosureAcked: false,
    _favorites: {}, // key: item_type:item_id → favorite

    init() {
        if (this._initialized) return;
        this._initialized = true;
        this._disclosureAcked = localStorage.getItem('discoverDisclosureAcked') === '1';
        if (!this._disclosureAcked) {
            document.getElementById('discover-disclosure')?.classList.remove('hidden');
        }
        var input = document.getElementById('discover-search-input');
        if (input) {
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') this.search();
            });
        }
        this.loadFavorites();
        this.loadOpportunities();
        this.refresh();
    },

    ackDisclosure() {
        this._disclosureAcked = true;
        localStorage.setItem('discoverDisclosureAcked', '1');
        document.getElementById('discover-disclosure')?.classList.add('hidden');
    },

    _t(key, fallback) {
        if (window.I18n && typeof window.I18n.t === 'function') return window.I18n.t(key, fallback);
        return fallback;
    },

    _safeUrl(url) {
        // _escapeHTML 只跳脫引號/角括號，javascript: 原封不動地活下來——
        // href 必須另外過協定白名單（2026-08-24 review）。
        return (typeof window.sanitizeUrl === 'function')
            ? window.sanitizeUrl(url)
            : (/^\s*(javascript|data|vbscript):/i.test(String(url || '')) ? '#' : (url || '#'));
    },

    _escapeHTML(str) {
        if (str === null || str === undefined) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    },

    _setStatus(message) {
        var el = document.getElementById('discover-status');
        var results = document.getElementById('discover-results');
        if (!el) return;
        if (message === null) {
            el.classList.add('hidden');
            el.textContent = '';
            if (results) results.classList.remove('hidden');
        } else {
            el.textContent = message;
            el.classList.remove('hidden');
            if (results) results.classList.add('hidden');
        }
    },

    _setDegraded(degraded) {
        document.getElementById('discover-degraded')?.classList.toggle('hidden', !degraded);
    },

    _renderSkeleton() {
        var results = document.getElementById('discover-results');
        if (!results) return;
        this._setStatus(null);
        results.innerHTML = Array.from({ length: 4 })
            .map(() =>
                '<div class="rounded-xl border border-borderSubtle/10 bg-surface p-4 animate-pulse">' +
                '<div class="h-4 w-2/3 bg-surfaceHighlight rounded mb-3"></div>' +
                '<div class="h-3 w-full bg-surfaceHighlight rounded mb-2"></div>' +
                '<div class="h-3 w-1/2 bg-surfaceHighlight rounded mb-4"></div>' +
                '<div class="h-8 w-full bg-surfaceHighlight rounded"></div></div>')
            .join('');
    },

    async refresh() {
        this._setDegraded(false);
        this._renderSkeleton();
        try {
            var q = document.getElementById('discover-search-input')?.value?.trim() || '';
            var params = new URLSearchParams();
            if (q) params.set('q', q);
            var data = await AppAPI.get('/api/discover/search?' + params.toString());
            this._renderResults(data.results || []);
            this._updateSearchHint(q, data.results || []);
        } catch (err) {
            this._handleError(err);
        }
    },

    // 語意搜尋永遠會回「最接近」的結果；弱匹配時提示使用者這不是精確符合，避免看起來像亂跳的隨機專案
    _updateSearchHint(q, items) {
        var el = document.getElementById('discover-search-hint');
        if (!el) return;
        var weak = false;
        if (q && items.length) {
            var maxSim = 0;
            items.forEach(function (p) {
                if (typeof p.similarity === 'number' && p.similarity > maxSim) maxSim = p.similarity;
            });
            weak = maxSim > 0 && maxSim < 0.4;
        }
        el.classList.toggle('hidden', !weak);
    },

    async search() {
        await this.refresh();
        var results = document.getElementById('discover-results');
        if (results && results.children.length) results.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    },

    // ── 出資者旅程 P0-2：為我推薦（design 2026-08-15）─────────────────────

    async recommend() {
        var statusEl = document.getElementById('discover-recommend-status');
        var resultsEl = document.getElementById('discover-recommend-results');
        if (!resultsEl) return;
        statusEl?.classList.remove('hidden');
        statusEl.textContent = this._t('discover.recommend.loading', '推薦產生中…');
        resultsEl.classList.remove('hidden');
        resultsEl.innerHTML = this._skeletonCards(3);
        var interests = document.getElementById('discover-recommend-interests')?.value?.trim() || '';
        var budgetRaw = document.getElementById('discover-recommend-budget')?.value?.trim() || '';
        var params = new URLSearchParams();
        if (interests) params.set('interests', interests);
        if (budgetRaw) {
            var budget = parseFloat(budgetRaw);
            if (!isNaN(budget) && budget >= 0) params.set('budget', String(budget));
        }
        params.set('limit', '6');
        try {
            var data = await AppAPI.get('/api/discover/recommend?' + params.toString());
            resultsEl.innerHTML = '';
            if (!(data.results || []).length) {
                statusEl.textContent = this._t('discover.recommend.empty', '沒有找到符合的推薦。');
                return;
            }
            statusEl.textContent =
                data.distill_source === 'favorites'
                    ? this._t('discover.recommend.fromFavorites', '依你的收藏推薦')
                    : '';
            if (!statusEl.textContent) statusEl.classList.add('hidden');
            resultsEl.innerHTML = (data.results || [])
                .map((p) => this._projectCardHTML(this._projectSummary(p), 'manifund_project'))
                .join('');
            if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
        } catch (err) {
            resultsEl.innerHTML = '';
            statusEl.textContent = this._t('discover.loadFailed', '載入失敗，請稍後再試');
        }
    },

    _skeletonCards(n) {
        return Array.from({ length: n })
            .map(() =>
                '<div class="rounded-xl border border-borderSubtle/10 bg-surface p-4 animate-pulse">' +
                '<div class="h-4 w-2/3 bg-surfaceHighlight rounded mb-3"></div>' +
                '<div class="h-3 w-full bg-surfaceHighlight rounded mb-2"></div>' +
                '<div class="h-8 w-full bg-surfaceHighlight rounded"></div></div>')
            .join('');
    },

    // ── 出資者旅程 P0-3：專案詳情＋AI 出資者視角評估（design 2026-08-15）──

    // ── 深掘問答（design 2026-08-17）────────────────────────────

    _qaKey(slug) { return 'discoverQA:' + slug; },

    _loadQaHistory(slug) {
        try { return JSON.parse(localStorage.getItem(this._qaKey(slug)) || '[]'); }
        catch (err) { return []; }
    },

    _saveQaHistory(slug, history) {
        try { localStorage.setItem(this._qaKey(slug), JSON.stringify(history.slice(-12))); }
        catch (err) { /* localStorage 不可用僅當前頁 */ }
    },

    _renderQaHistory(history) {
        var box = document.getElementById('discover-qa-history');
        if (!box) return;
        box.innerHTML = (history || []).map((m) =>
            '<div class="flex ' + (m.role === 'user' ? 'justify-end' : 'justify-start') + '">' +
            '<div class="max-w-[85%] rounded-xl px-3 py-2 text-sm ' +
            (m.role === 'user'
                ? 'bg-primary text-background'
                : 'bg-surfaceHighlight text-secondary') + '">' +
            this._escapeHTML(m.content) + '</div></div>'
        ).join('');
        box.scrollTop = box.scrollHeight;
    },

    async sendQuestion() {
        var slug = this._detailItemId;
        var input = document.getElementById('discover-qa-input');
        var q = input?.value?.trim();
        if (!slug || !q) return;
        input.value = '';
        var history = this._loadQaHistory(slug);
        history.push({ role: 'user', content: q });
        this._renderQaHistory(history.concat([{ role: 'assistant', content: this._t('discover.ask.askThinking', '思考中…') }]));
        var language = (window.I18n && typeof window.I18n.getLanguage === 'function')
            ? window.I18n.getLanguage() : 'zh-TW';
        try {
            var data = await AppAPI.post(
                '/api/discover/projects/' + encodeURIComponent(slug) + '/ask',
                { question: q, history: history.slice(0, -1), language: language },
                { timeout: 70000 }
            );
            history.push({ role: 'assistant', content: data.answer || '' });
        } catch (err) {
            history.push({ role: 'assistant', content: this._t('discover.ask.askFailed', '暫時無法回答，請稍後再試') });
        }
        this._saveQaHistory(slug, history);
        this._renderQaHistory(history);
    },

    _noop() {
        // 對話框內部點擊的止泡目標（避免誤關 modal）
    },

    async openDetail(itemId) {
        var modal = document.getElementById('discover-detail-modal');
        if (!modal) return;
        // 開啟即渲染骨架，資料到了再填（詳情端點 fail-soft）
        document.getElementById('discover-detail-title').textContent =
            this._t('discover.detail.loading', '載入中…');
        document.getElementById('discover-detail-tags').innerHTML = '';
        document.getElementById('discover-detail-desc').textContent = '';
        document.getElementById('discover-detail-progress').classList.add('hidden');
        document.getElementById('discover-detail-link').href = '#';
        document.getElementById('discover-ai-review').classList.add('hidden');
        document.getElementById('discover-ai-review').innerHTML = '';
        modal.classList.remove('hidden');
        document.body.style.overflow = 'hidden';
        this._detailItemId = String(itemId);
        this._renderQaHistory(this._loadQaHistory(String(itemId)));
        var qaInput = document.getElementById('discover-qa-input');
        if (qaInput && !qaInput._enterBound) {
            qaInput._enterBound = true;
            qaInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this.sendQuestion();
                }
            });
        }
        try {
            var data = await AppAPI.get('/api/discover/projects/' + encodeURIComponent(String(itemId)));
            this._renderDetail(data.project || {});
        } catch (err) {
            document.getElementById('discover-detail-title').textContent =
                this._t('discover.detail.loadFailed', '專案資料載入失敗');
        }
    },

    closeDetail() {
        var modal = document.getElementById('discover-detail-modal');
        if (!modal) return;
        modal.classList.add('hidden');
        document.body.style.overflow = '';
        this._detailItemId = null;
    },

    _renderDetail(p) {
        var s = this._projectSummary(p);
        document.getElementById('discover-detail-title').innerHTML = s.title;
        var tags = [];
        if (s.cause) tags.push('<span class="inline-block text-xs px-2 py-0.5 rounded-full bg-surfaceHighlight">' + s.cause + '</span>');
        if (s.stage) tags.push('<span class="inline-block text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary">' + s.stage + '</span>');
        document.getElementById('discover-detail-tags').innerHTML = tags.join(' ');
        document.getElementById('discover-detail-desc').textContent = s.desc;
        var progressWrap = document.getElementById('discover-detail-progress');
        if (typeof s.raised === 'number' && typeof s.goal === 'number' && s.goal > 0) {
            var pct = Math.min(100, Math.round((s.raised / s.goal) * 100));
            progressWrap.classList.remove('hidden');
            document.getElementById('discover-detail-progress-bar').style.width = pct + '%';
            document.getElementById('discover-detail-progress-text').textContent =
                '$' + Number(s.raised).toLocaleString() + ' / $' + Number(s.goal).toLocaleString() + ' · ' + pct + '%';
        }
        var link = document.getElementById('discover-detail-link');
        if (s.url) link.href = s.url;
        if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
    },

    async requestAiReview() {
        var itemId = this._detailItemId;
        var box = document.getElementById('discover-ai-review');
        if (!itemId || !box) return;
        box.classList.remove('hidden');
        box.innerHTML =
            '<div class="flex items-center gap-2 text-sm text-textMuted py-2">' +
            '<i data-lucide="loader-circle" class="w-4 h-4 animate-spin"></i>' +
            this._t('discover.aiReview.loading', '評估產生中，約需數十秒…') + '</div>';
        if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
        var language = (window.I18n && typeof window.I18n.getLanguage === 'function')
            ? window.I18n.getLanguage() : 'zh-TW';
        try {
            var data = await AppAPI.post(
                '/api/discover/projects/' + encodeURIComponent(itemId) + '/ai-review',
                { language: language },
                // 評估為單發 LLM 生成，慢模型需 60-90s；覆寫 api-client 預設 15s timeout
                { timeout: 100000 }
            );
            this._renderAiReview(data);
        } catch (err) {
            var status = err && err.status;
            box.innerHTML =
                '<p class="text-sm text-textMuted py-1">' +
                this._t(
                    'discover.aiReview.unavailable',
                    '評估暫時無法產生，請稍後再試'
                ) + (status === 404 ? '（' + this._t('discover.aiReview.notEnabled', '功能未開放') + '）' : '') +
                '</p>';
        }
    },

    _renderAiReview(data) {
        var box = document.getElementById('discover-ai-review');
        if (!box) return;
        var r = (data && data.review) || {};
        var list = (title, items, icon) =>
            !items || !items.length
                ? ''
                : '<div><p class="text-xs font-medium text-textMuted mb-1.5 flex items-center gap-1.5">' +
                '<i data-lucide="' + icon + '" class="w-3.5 h-3.5"></i>' + title + '</p>' +
                '<ul class="space-y-1.5">' +
                items.map((x) => '<li class="text-sm text-secondary flex gap-2"><span class="text-textMuted shrink-0">•</span><span>' + this._escapeHTML(x) + '</span></li>').join('') +
                '</ul></div>';
        var outlook = r.funding_outlook || {};
        var outlookHtml = outlook.summary
            ? '<div><p class="text-xs font-medium text-textMuted mb-1.5 flex items-center gap-1.5">' +
            '<i data-lucide="trending-up" class="w-3.5 h-3.5"></i>' +
            this._t('discover.aiReview.outlook', '募資展望') +
            ' <span class="px-1.5 py-0.5 rounded bg-surfaceHighlight text-[10px]">' +
            this._escapeHTML(outlook.confidence || 'low') + '</span></p>' +
            '<p class="text-sm text-secondary">' + this._escapeHTML(outlook.summary) + '</p>' +
            (outlook.rationale ? '<p class="text-xs text-textMuted mt-1">' + this._escapeHTML(outlook.rationale) + '</p>' : '') +
            '</div>'
            : '';
        box.innerHTML =
            '<p class="text-sm font-medium text-primary flex items-center gap-2">' +
            '<i data-lucide="scan-search" class="w-4 h-4"></i>' +
            this._t('discover.aiReview.button', 'AI 出資者視角評估') + '</p>' +
            list(this._t('discover.aiReview.highlights', '亮點'), r.highlights, 'circle-check') +
            list(this._t('discover.aiReview.risks', '風險與未答問題'), r.risks, 'triangle-alert') +
            outlookHtml +
            list(this._t('discover.aiReview.regrantorQuestions', 'Regrantor 問過的問題'), r.regrantor_questions, 'message-circle-question') +
            list(this._t('discover.aiReview.similarContext', '相似脈絡'), r.similar_context, 'git-compare') +
            '<div class="pt-2 border-t border-borderSubtle/10 space-y-2">' +
            '<button data-click="DiscoverTab.analyze" data-click-arg="' + encodeURIComponent(String(this._detailItemId)) + '" ' +
            'class="px-3 py-1.5 rounded-lg bg-primary/15 text-primary text-xs font-medium">' +
            this._t('discover.aiReview.askAgent', '到聊天追問') + '</button>' +
            '<p class="text-xs text-textMuted">' + this._escapeHTML((data && data.disclaimer) || '') + '</p>' +
            '</div>';
        if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
        // 追問按鈕點擊時一併關閉詳情（analyze 會切到 chat tab）
        var self = this;
        box.querySelectorAll('[data-click="DiscoverTab.analyze"]').forEach(function (btn) {
            btn.addEventListener('click', function () { self.closeDetail(); });
        });
    },

    _handleError(err) {
        var status = err && err.status;
        var results = document.getElementById('discover-results');
        if (results) results.innerHTML = '';
        if (status === 503) {
            this._setDegraded(true);
            this._setStatus(this._t('discover.degraded', 'Manifund 資料暫時無法取得'));
        } else if (status === 404) {
            this._setStatus(this._t('discover.disabled', '探索功能未開放'));
        } else {
            this._setStatus(this._t('discover.loadFailed', '載入失敗，請稍後再試'));
        }
    },

    _projectSummary(p) {
        if (!p || typeof p !== 'object') return { id: '', title: '', desc: '' };
        const causes = Array.isArray(p.causes) ? p.causes : [];
        return {
            // Manifund 實測欄位（2026-08-15）：slug/title/blurb/stage/
            // total_raised/funding_goal/causes[{title}]/url
            id: this._escapeHTML(p.id || p.slug || p.project_id || ''),
            title: this._escapeHTML(p.title || p.name || p.project_name || '(untitled)'),
            desc: this._escapeHTML((p.blurb || p.description || p.summary || p.one_liner || '').slice(0, 140)),
            url: this._escapeHTML(this._safeUrl(p.url || (p.slug ? 'https://manifund.org/projects/' + p.slug : ''))),
            cause: this._escapeHTML(p.cause || p.category || (causes[0] && (causes[0].title || causes[0].slug)) || ''),
            stage: this._escapeHTML(p.stage || ''),
            raised: p.total_raised != null ? p.total_raised : (p.raised || p.funds_raised || p.amount_raised),
            goal: p.funding_goal != null ? p.funding_goal : (p.goal || p.target),
            source: this._escapeHTML(p.source || 'manifund'),
        };
    },

    _renderResults(items) {
        var results = document.getElementById('discover-results');
        if (!results) return;
        if (!items.length) {
            results.innerHTML = '';
            this._setStatus(this._t('discover.empty', '沒有找到符合的專案。試試更換關鍵字或瀏覽最新專案。'));
            return;
        }
        this._setStatus(null);
        results.innerHTML = items.map((p) => this._projectCardHTML(this._projectSummary(p), 'manifund_project')).join('');
        if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
    },

    // ── 多來源（design 2026-08-17-discover-multisource）────────────────────

    _sourceBadge(source) {
        // 品牌名不經 i18n（manifund / oc / curated）；樣式隨來源區分
        var map = {
            manifund: ['Manifund', 'bg-primary/10 text-primary'],
            oc: ['Open Collective', 'bg-sky-500/10 text-sky-400'],
            curated: ['★ ' + this._t('discover.opportunities.badge', '機會表'), 'bg-danger/10 text-danger'],
        };
        var m = map[source] || map.manifund;
        return '<span class="inline-block text-[10px] font-semibold px-1.5 py-0.5 rounded ' + m[1] + '">' + m[0] + '</span>';
    },

    _projectCardHTML(s, itemType) {
        var favKey = itemType + ':' + s.id;
        var isFav = !!this._favorites[favKey];
        var star = isFav ? '★' : '☆';
        var progress = '';
        if (typeof s.raised === 'number' && typeof s.goal === 'number' && s.goal > 0) {
            var pct = Math.min(100, Math.round((s.raised / s.goal) * 100));
            progress =
                '<div class="h-1.5 w-full bg-surfaceHighlight rounded-full overflow-hidden mb-2">' +
                '<div class="h-full bg-primary" style="width:' + pct + '%"></div></div>' +
                '<p class="text-xs text-textMuted mb-2">' + pct + '%</p>';
        }
        var favArgs = encodeURIComponent(JSON.stringify([itemType, s.id, s.url]));
        var analyzeArg = encodeURIComponent(String(s.id));
        var detailArg = encodeURIComponent(String(s.id));
        var refArgs = encodeURIComponent(JSON.stringify([s.id, s.title, s.url, s.source]));
        return (
            '<div class="rounded-xl border border-borderSubtle/10 bg-surface p-4 flex flex-col">' +
            '<div class="flex items-start justify-between gap-2 mb-1">' +
            '<div class="min-w-0 flex-1">' +
            this._sourceBadge(s.source) +
            '<button data-click="DiscoverTab.openDetail" data-click-arg="' + detailArg +
            '" class="block text-left font-medium text-secondary leading-snug hover:text-primary transition mt-1">' + s.title + '</button>' +
            '</div>' +
            '<button data-click="DiscoverTab.toggleFavorite" data-click-args="' + favArgs +
            '" class="text-lg leading-none px-1 hover:scale-110 transition" aria-label="favorite" data-i18n="discover.favorite" data-i18n-attr="aria-label">' + star + '</button>' +
            '</div>' +
            (s.cause || s.stage ? '<div class="flex flex-wrap gap-1.5 mb-2">' +
                (s.cause ? '<span class="inline-block text-xs px-2 py-0.5 rounded-full bg-surfaceHighlight">' + s.cause + '</span>' : '') +
                (s.stage ? '<span class="inline-block text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary">' + s.stage + '</span>' : '') +
                '</div>' : '') +
            '<p class="text-sm text-textMuted mb-3 line-clamp-2">' + s.desc + '</p>' +
            progress +
            '<div class="mt-auto flex flex-wrap gap-2">' +
            '<button data-click="DiscoverTab.openDetail" data-click-arg="' + detailArg + '" ' +
            'class="flex-1 min-w-[90px] px-3 py-1.5 rounded-lg bg-primary text-background text-xs font-medium text-center whitespace-nowrap" data-i18n="discover.detail.button">' +
            this._t('discover.detail.button', '查看詳情') + '</button>' +
            '<button data-click="DiscoverTab.addToReference" data-click-args="' + refArgs + '" ' +
            'class="flex-1 min-w-[110px] px-3 py-1.5 rounded-lg bg-surfaceHighlight border border-borderSubtle/40 text-xs text-center whitespace-nowrap" data-i18n="discover.addRef">' +
            this._t('discover.addRef', '＋加到 Studio 參考') + '</button>' +
            (s.url ? '<a href="' + s.url + '" target="_blank" rel="noopener noreferrer" class="px-3 py-1.5 rounded-lg bg-surfaceHighlight border border-borderSubtle/40 text-xs inline-flex items-center justify-center gap-1 whitespace-nowrap">' +
            this._t('discover.goExternal', '前往') + ' <i data-lucide="external-link" class="w-3 h-3"></i></a>' : '') +
            '</div></div>'
        );
    },

    // ── 補助機會表（curated；無 API 平台的涵蓋網）──────────────────────────

    async loadOpportunities() {
        var section = document.getElementById('discover-opportunities-section');
        var list = document.getElementById('discover-opportunities');
        if (!section || !list) return;
        try {
            var data = await AppAPI.get('/api/discover/opportunities');
            var items = data.results || [];
            if (!items.length) {
                section.classList.add('hidden');
                return;
            }
            section.classList.remove('hidden');
            list.innerHTML = items.map((o) => this._opportunityCardHTML(o)).join('');
            if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
        } catch (err) {
            // 機會表載入失敗不阻斷探索主流程
            section.classList.add('hidden');
        }
    },

    _opportunityCardHTML(o) {
        var cycleMap = { rolling: 'discover.opportunities.cycleRolling', annual: 'discover.opportunities.cycleAnnual', program: 'discover.opportunities.cycleProgram' };
        var cycle = this._t(cycleMap[o.cycle] || 'discover.opportunities.cycleProgram', '專案制');
        var deadline = o.deadline
            ? '· ' + this._t('discover.opportunities.deadline', '截止') + ' ' + this._escapeHTML(String(o.deadline).slice(0, 10))
            : '';
        return (
            '<div class="rounded-xl border border-borderSubtle/10 bg-surface p-4 flex flex-col">' +
            '<div class="flex items-center gap-2 mb-1">' +
            this._sourceBadge('curated') +
            (o.status === 'closed'
                ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-surfaceHighlight text-textMuted">' + this._t('discover.opportunities.closed', '已截止') + '</span>'
                : '') +
            '</div>' +
            '<h4 class="font-medium text-secondary leading-snug">' + this._escapeHTML(o.name || '') + '</h4>' +
            '<p class="text-xs text-textMuted mb-2">' + this._escapeHTML(o.platform || '') + ' · ' + cycle + ' ' + deadline + '</p>' +
            (o.amount ? '<p class="text-sm text-textMuted mb-1">💰 ' + this._escapeHTML(o.amount) + '</p>' : '') +
            (o.eligibility ? '<p class="text-sm text-textMuted line-clamp-2 mb-2">' + this._escapeHTML(o.eligibility) + '</p>' : '') +
            '<div class="mt-auto">' +
            '<a href="' + this._escapeHTML(this._safeUrl(o.url)) + '" target="_blank" rel="noopener noreferrer" ' +
            'class="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-primary text-background text-xs font-medium">' +
            this._t('discover.opportunities.visit', '前往官網') + ' <i data-lucide="external-link" class="w-3 h-3"></i></a>' +
            '</div></div>'
        );
    },

    // ── Discover→Studio 參考動線（§Phase 2）────────────────────────────────

    async addToReference(itemId, title, url, source) {
        try {
            var data = await AppAPI.get('/api/studio/drafts');
            var drafts = data.drafts || [];
            if (!drafts.length) {
                if (typeof window.showToast === 'function')
                    window.showToast(this._t('discover.ref.noDraft', '先在 Studio 建立草稿，再回來加參考'), 'warning');
                return;
            }
            this._pendingRef = {
                source: source === 'oc' ? 'oc' : 'manifund',
                url: url || '',
                title: String(title || itemId || '').slice(0, 200),
                snippet: '',
            };
            var list = document.getElementById('discover-ref-list');
            list.innerHTML = drafts.map((d) =>
                '<button data-click="DiscoverTab._pickDraft" data-click-arg="' + encodeURIComponent(d.draft_id) + '" ' +
                'class="w-full text-left px-3 py-2 rounded-lg bg-surfaceHighlight hover:bg-primary/10 text-sm text-secondary mb-1 truncate">' +
                this._escapeHTML(d.title || d.draft_id) + '</button>'
            ).join('');
            document.getElementById('discover-ref-modal')?.classList.remove('hidden');
        } catch (err) {
            // Studio 未開放（404）或載入失敗
            if (typeof window.showToast === 'function')
                window.showToast(this._t('discover.ref.noDraft', '先在 Studio 建立草稿，再回來加參考'), 'warning');
        }
    },

    async _pickDraft(draftId) {
        var ref = this._pendingRef;
        if (!ref || !draftId) return;
        this._closeRefPicker();
        try {
            await AppAPI.post(
                '/api/studio/drafts/' + encodeURIComponent(draftId) + '/references',
                ref
            );
            if (typeof window.showToast === 'function')
                window.showToast(this._t('discover.ref.added', '已加入草稿參考'), 'success');
        } catch (err) {
            if (typeof window.showToast === 'function')
                window.showToast(this._t('discover.ref.failed', '加入參考失敗，請稍後再試'), 'error');
        }
    },

    _closeRefPicker() {
        this._pendingRef = null;
        document.getElementById('discover-ref-modal')?.classList.add('hidden');
    },

    async analyze(itemId) {
        // deep-link 到聊天（帶 People & Projects 語境），聊天入口仍是唯一對話介面
        var query = this._t('discover.analyzePrompt', '請幫我分析 Manifund 專案') + ' ' + itemId;
        try {
            if (typeof window.switchTab === 'function') {
                await window.switchTab('chat');
            }
        } catch (err) {
            // 切換失敗不阻斷：仍嘗試填入查詢
        }
        var input = document.querySelector('#user-input');
        if (input) {
            input.value = query;
            input.focus();
        }
    },

    async loadFavorites() {
        try {
            var data = await AppAPI.get('/api/discover/favorites');
            this._favorites = {};
            (data.favorites || []).forEach((f) => {
                this._favorites[f.item_type + ':' + f.item_id] = f;
            });
            this._renderFavorites(data.favorites || []);
        } catch (err) {
            // 收藏載入失敗不阻斷探索（僅隱藏區塊）
            document.getElementById('discover-favorites-section')?.classList.add('hidden');
        }
    },

    _renderFavorites(favorites) {
        var section = document.getElementById('discover-favorites-section');
        var list = document.getElementById('discover-favorites');
        if (!section || !list) return;
        if (!favorites.length) {
            section.classList.add('hidden');
            return;
        }
        section.classList.remove('hidden');
        list.innerHTML = favorites
            .map((f) => {
                var s = {
                    id: this._escapeHTML(f.item_id),
                    title: this._escapeHTML(f.item_id),
                    desc: '',
                    url: this._escapeHTML(f.source_url || ''),
                    cause: '',
                };
                var card = this._projectCardHTML(s, f.item_type);
                return card;
            })
            .join('');
        if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
    },

    async toggleFavorite(itemType, itemId, itemUrl) {
        var key = itemType + ':' + itemId;
        var isFav = !!this._favorites[key];
        try {
            if (isFav) {
                await AppAPI.delete('/api/discover/favorites/' + encodeURIComponent(itemType) + '/' + encodeURIComponent(itemId));
                delete this._favorites[key];
            } else {
                var data = await AppAPI.post('/api/discover/favorites', {
                    item_type: itemType,
                    item_id: itemId,
                    source_url: itemUrl || null,
                });
                this._favorites[key] = data.favorite;
            }
            // 原地更新星號——不做全列表 refresh（骨架重繪會讓卡片消失、捲動跳頂）
            this._updateStars(itemType, itemId, !!this._favorites[key]);
            this.loadFavorites();
        } catch (err) {
            // 收藏失敗：還原星號並提示（design §10.4；平台 toast，不用原生 alert）
            if (typeof window.showToast === 'function') {
                window.showToast(this._t('discover.favoriteFailed', '收藏操作失敗，請稍後再試'), 'error');
            }
        }
    },

    // 原地切換星號（含收藏區的卡）——不觸發列表重繪
    _updateStars(itemType, itemId, isFav) {
        document.querySelectorAll('[data-click="DiscoverTab.toggleFavorite"]').forEach((btn) => {
            try {
                var args = JSON.parse(decodeURIComponent(btn.getAttribute('data-click-args') || '[]'));
                if (args[0] === itemType && args[1] === itemId) {
                    btn.textContent = isFav ? '★' : '☆';
                }
            } catch (err) { /* args 解析失敗跳過 */ }
        });
    },
};

window.DiscoverTab = DiscoverTab;
