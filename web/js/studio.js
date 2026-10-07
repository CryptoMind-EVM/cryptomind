/**
 * StudioTab — 提案工作台控制器（design 2026-08-16）
 *
 * - 列表 → 編輯器（markdown textarea＋預覽）
 * - 版本軌跡：存檔/自動存檔/採納建議各成不可變版本；任版可看、可 diff、可還原
 * - AI 教練：建議制不代寫；採納 = 以建議文字替換稿件原句（可先編輯）→ 存 ai_applied 版
 * - 匯出：markdown 複製 → 本人到 Manifund 送出（金流不過手）
 * - 自動存檔併版：debounce 30s 且與上次存檔差異 >200 字才產版（design 決策 #2）
 */

var StudioTab = {
    _initialized: false,
    _drafts: [],
    _current: null,      // 當前草稿 meta
    _content: '',        // 當前編輯內容（未存）
    _savedContent: '',   // 最後已存內容
    _autosaveTimer: null,
    _versions: [],

    init() {
        if (this._initialized) return;
        this._initialized = true;
        var editor = document.getElementById('studio-editor');
        if (editor) {
            editor.addEventListener('input', () => this._onEdit());
            editor.addEventListener('keydown', (e) => this._telemetryKey(e));
            editor.addEventListener('paste', (e) => this._telemetryPaste(e));
        }
        this._tel = { typed: 0, pastes: 0, pastedChars: 0, since: Date.now() };
        this.refresh();
    },

    async refresh() {
        document.getElementById('studio-degraded')?.classList.add('hidden');
        try {
            var data = await AppAPI.get('/api/studio/drafts');
            this._drafts = data.drafts || [];
            this._renderList();
        } catch (err) {
            var status = err && err.status;
            document.getElementById('studio-degraded')?.classList.remove('hidden');
            var text = document.getElementById('studio-degraded-text');
            if (text) {
                text.textContent = status === 404
                    ? this._t('disabled', '功能未開放')
                    : this._t('loadFailed', '載入失敗，請稍後再試');
            }
            var list = document.getElementById('studio-draft-list');
            if (list) list.innerHTML = '';
        }
    },

    _t(key, fallback) {
        if (window.I18n && typeof window.I18n.t === 'function') {
            var v = window.I18n.t('studio.' + key);
            if (v && v !== 'studio.' + key) return v;
        }
        return fallback;
    },

    _safeUrl(url) {
        // _esc 擋不住 javascript:——href 另過協定白名單（2026-08-24 review）
        return (typeof window.sanitizeUrl === 'function')
            ? window.sanitizeUrl(url)
            : (/^\s*(javascript|data|vbscript):/i.test(String(url || '')) ? '#' : (url || '#'));
    },

    _esc(str) {
        if (str === null || str === undefined) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    },

    _toast(msg, kind) {
        if (typeof window.showToast === 'function') window.showToast(msg, kind || 'success');
    },

    // ── 列表 ────────────────────────────────────────────────────

    _renderList() {
        var list = document.getElementById('studio-draft-list');
        if (!list) return;
        document.getElementById('studio-list-view')?.classList.remove('hidden');
        document.getElementById('studio-editor-view')?.classList.add('hidden');
        if (!this._drafts.length) {
            list.innerHTML =
                '<div class="rounded-xl border border-dashed border-borderSubtle/30 p-6 text-sm text-textMuted text-center">' +
                this._t('empty', '尚無草稿。建立第一份，開始與教練打磨你的提案。') + '</div>';
            return;
        }
        list.innerHTML = this._drafts.map((d) => {
            var statusLabel = d.status === 'exported'
                ? this._t('statusExported', '已匯出')
                : this._t('statusDraft', '草稿中');
            return (
                '<div class="rounded-xl border border-borderSubtle/10 bg-surface p-4 flex items-center justify-between gap-3">' +
                '<div class="min-w-0">' +
                '<div class="flex items-center gap-2">' +
                '<h4 class="font-medium text-secondary truncate">' + this._esc(d.title) + '</h4>' +
                '<span class="text-xs px-2 py-0.5 rounded-full bg-surfaceHighlight shrink-0">' + statusLabel + '</span>' +
                '</div>' +
                '<p class="text-xs text-textMuted mt-1">' +
                (d.cause ? this._esc(d.cause) + ' · ' : '') +
                this._t('versionsLabel', '版本') + ' ' + d.current_version_no + ' · ' +
                this._esc((d.updated_at || '').slice(0, 16).replace('T', ' ')) +
                '</p>' +
                '</div>' +
                '<button data-click="StudioTab.openEditor" data-click-arg="' + this._esc(d.draft_id) +
                '" class="px-3 py-1.5 rounded-lg bg-primary text-background text-xs font-medium shrink-0">' +
                this._t('open', '打開') + '</button>' +
                '</div>'
            );
        }).join('');
    },

    async createDraft() {
        var title = document.getElementById('studio-new-title')?.value?.trim();
        var cause = document.getElementById('studio-new-cause')?.value?.trim() || null;
        if (!title) {
            this._toast(this._t('titleRequired', '請先填標題'), 'error');
            return;
        }
        try {
            await AppAPI.post('/api/studio/drafts', { title: title, cause: cause });
            document.getElementById('studio-new-title').value = '';
            document.getElementById('studio-new-cause').value = '';
            await this.refresh();
        } catch (err) {
            this._toast(this._errText(err), 'error');
        }
    },

    _errText(err) {
        var status = err && err.status;
        var detail = err && err.detail;
        if (status === 409) return this._t('conflict', '同名草稿已存在或已達上限');
        if (status === 503) return this._t('noKey', '需要可用的 LLM 金鑰（Settings 設定）');
        return this._t('opFailed', '操作失敗，請稍後再試');
    },

    // ── 編輯器 ──────────────────────────────────────────────────

    async openEditor(draftId) {
        try {
            var data = await AppAPI.get('/api/studio/drafts/' + encodeURIComponent(draftId));
            this._current = data.draft;
            this._content = data.content_md || '';
            this._savedContent = this._content;
            this._renderReferences();
            document.getElementById('studio-list-view')?.classList.add('hidden');
            document.getElementById('studio-editor-view')?.classList.remove('hidden');
            document.getElementById('studio-edit-title').value = this._current.title;
            document.getElementById('studio-edit-cause').value = this._current.cause || '';
            document.getElementById('studio-editor').value = this._content;
            document.getElementById('studio-preview')?.classList.add('hidden');
            document.getElementById('studio-editor').classList.remove('hidden');
            this._updateEditorHeader();
            await this._loadTrail();
            document.getElementById('studio-coach-result').innerHTML = '';
            document.getElementById('studio-tab-body')?.scrollTo({ top: 0 });
            this._startCoachFab();
        } catch (err) {
            this._toast(this._errText(err), 'error');
        }
    },

    _updateEditorHeader() {
        if (!this._current) return;
        document.getElementById('studio-editor-status').textContent =
            this._current.status === 'exported'
                ? this._t('statusExported', '已匯出')
                : this._t('statusDraft', '草稿中');
        document.getElementById('studio-editor-versions').textContent =
            'v' + this._current.current_version_no;
        this._updateDirtyHint();
    },

    _updateDirtyHint() {
        var hint = document.getElementById('studio-dirty-hint');
        if (!hint) return;
        var dirty = this._content !== this._savedContent;
        hint.textContent = dirty ? this._t('unsaved', '有未存變更') : '';
        hint.className = dirty
            ? 'text-xs text-amber-500'
            : 'text-xs text-textMuted';
    },

    _telemetryKey(e) {
        if (!this._tel) this._tel = { typed: 0, pastes: 0, pastedChars: 0, since: Date.now() };
        if (e.key && e.key.length === 1 && !e.ctrlKey && !e.metaKey) this._tel.typed += 1;
    },

    _telemetryPaste(e) {
        if (!this._tel) this._tel = { typed: 0, pastes: 0, pastedChars: 0, since: Date.now() };
        this._tel.pastes += 1;
        var text = (e.clipboardData && e.clipboardData.getData('text')) || '';
        this._tel.pastedChars += text.length;
    },

    _telemetryPayload() {
        var t = this._tel || { typed: 0, pastes: 0, pastedChars: 0, since: Date.now() };
        return {
            typed_chars: t.typed,
            paste_events: t.pastes,
            pasted_chars: t.pastedChars,
            edit_seconds: Math.min(86400, Math.round((Date.now() - t.since) / 1000)),
        };
    },

    _telemetryReset() {
        this._tel = { typed: 0, pastes: 0, pastedChars: 0, since: Date.now() };
    },

    _onEdit() {
        this._content = document.getElementById('studio-editor')?.value || '';
        this._updateDirtyHint();
        this._refreshAdoptButtons();
        // 自動存檔（併版）：30s debounce＋差異 >200 字
        if (this._autosaveTimer) clearTimeout(this._autosaveTimer);
        this._autosaveTimer = setTimeout(() => {
            if (this._current && Math.abs(this._content.length - this._savedContent.length) > 200) {
                this._saveVersion('autosave', this._t('autosaveSummary', '自動存檔'));
            }
        }, 30000);
    },

    togglePreview() {
        var editor = document.getElementById('studio-editor');
        var preview = document.getElementById('studio-preview');
        if (!editor || !preview) return;
        var showing = !preview.classList.contains('hidden');
        if (showing) {
            preview.classList.add('hidden');
            editor.classList.remove('hidden');
        } else {
            preview.textContent = this._content || ' ';
            preview.classList.remove('hidden');
            editor.classList.add('hidden');
        }
    },

    async saveVersion() {
        // 標題/cause 有改先存 meta
        var title = document.getElementById('studio-edit-title')?.value?.trim();
        var cause = document.getElementById('studio-edit-cause')?.value?.trim() || null;
        if (title && title !== this._current.title) {
            try {
                var meta = await AppAPI.patch('/api/studio/drafts/' + encodeURIComponent(this._current.draft_id), { title: title });
                this._current = meta.draft;
            } catch (err) {
                this._toast(this._errText(err), 'error');
                return;
            }
        }
        await this._saveVersion('human', this._t('manualSummary', '手動存檔'));
    },

    async _saveVersion(source, summary) {
        if (!this._current) return null;
        try {
            var data = await AppAPI.post(
                '/api/studio/drafts/' + encodeURIComponent(this._current.draft_id) + '/versions',
                { content_md: this._content, change_summary: summary, source: source, telemetry: this._telemetryPayload() }
            );
            this._telemetryReset();
            this._savedContent = this._content;
            this._current.current_version_no += 1;
            this._updateEditorHeader();
            await this._loadTrail();
            if (source !== 'autosave') this._toast(this._t('saved', '已存為新版本'));
            return (data.version && data.version.version_no) || null;
        } catch (err) {
            this._toast(this._errText(err), 'error');
            return null;
        }
    },

    // 稽核鏈：記錄建議處置（失敗不阻斷 UX——版本已存，鏈路可後補）；
    // 寫入後重拉軌跡讓對話條目即時顯示 ✓/✕ 徽章
    async _recordOutcome(quote, outcome, versionNo) {
        if (!this._current || !this._coachExchangeId) return;
        try {
            await AppAPI.post(
                '/api/studio/drafts/' + encodeURIComponent(this._current.draft_id) + '/coach/outcomes',
                { exchange_id: this._coachExchangeId, quote: quote, outcome: outcome, version_no: versionNo || null }
            );
            await this._loadTrail();
        } catch (err) { /* 鏈路記錄失敗僅 console */ }
    },

    async dismissSuggestion(index) {
        var s = (this._coachSuggestions || [])[Number(index)];
        if (!s || !this._current) return;
        this._dismissedQuotes = this._dismissedQuotes || {};
        this._dismissedQuotes[s.quote] = true;
        // 卡片視覺回饋：淡出＋標記已忽略
        var card = document.querySelector('.coach-suggestion-edit[data-suggestion-index="' + index + '"]')?.closest('.rounded-lg');
        if (card) {
            card.style.opacity = '0.45';
            var badge = card.querySelector('.studio-outcome-badge');
            if (!badge) {
                badge = document.createElement('span');
                badge.className = 'studio-outcome-badge text-[10px] px-1.5 py-0.5 rounded bg-surfaceHighlight text-textMuted';
                badge.textContent = this._t('dismissedTag', '已忽略');
                card.querySelector('.flex.items-center')?.appendChild(badge);
            }
        }
        await this._recordOutcome(s.quote, 'dismissed', null);
    },

    async deleteDraft() {
        if (!this._current) return;
        var ok = await window.showConfirmDialog({
            title: this._t('confirmDelete', '確定刪除此草稿？'),
            message: this._t('confirmDeleteBody', '全部版本軌跡將一併刪除，無法復原。'),
            danger: true,
        });
        if (!ok) return;
        try {
            await AppAPI.delete('/api/studio/drafts/' + encodeURIComponent(this._current.draft_id));
            this._current = null;
            await this.refresh();
        } catch (err) {
            this._toast(this._errText(err), 'error');
        }
    },

    backToList() {
        if (this._autosaveTimer) clearTimeout(this._autosaveTimer);
        this._stopCoachFab();
        this.refresh();
    },

    // 手機浮動「請教練看稿」：教練面板離開視野才顯示（IntersectionObserver）
    _startCoachFab() {
        this._stopCoachFab();
        var fab = document.getElementById('studio-coach-fab');
        // 觀察「提問按鈕行」（而非整個面板）——按鈕不在視野才需要 FAB
        var coachRow = document.getElementById('studio-coach-question')?.parentElement;
        if (!fab || !coachRow || !('IntersectionObserver' in window)) return;
        fab.classList.remove('hidden');
        fab.classList.add('flex');
        var isMobile = window.matchMedia('(max-width: 767px)').matches;
        if (!isMobile) { fab.classList.add('hidden'); fab.classList.remove('flex'); return; }
        this._fabObserver = new IntersectionObserver((entries) => {
            var visible = entries[0].isIntersecting;
            fab.classList.toggle('hidden', visible);
            fab.classList.toggle('flex', !visible);
        }, { threshold: 0.15 });
        this._fabObserver.observe(coachRow);
    },

    _stopCoachFab() {
        if (this._fabObserver) { this._fabObserver.disconnect(); this._fabObserver = null; }
        var fab = document.getElementById('studio-coach-fab');
        if (fab) { fab.classList.add('hidden'); fab.classList.remove('flex'); }
    },

    // ── 版本軌跡 ────────────────────────────────────────────────

    async _loadTrail() {
        if (!this._current) return;
        try {
            var data = await AppAPI.get(
                '/api/studio/drafts/' + encodeURIComponent(this._current.draft_id) + '/versions'
            );
            this._versions = data.versions || [];
            this._renderTrail();
        } catch (err) {
            /* 軌跡載入失敗不阻斷編輯 */
        }
        try {
            var ex = await AppAPI.get(
                '/api/studio/drafts/' + encodeURIComponent(this._current.draft_id) + '/exchanges'
            );
            this._exchanges = ex.exchanges || [];
            this._renderTrail();
        } catch (err) {
            this._exchanges = [];
        }
    },

    // 版本軌跡字數曲線：累計字數（v1 的 char_delta＝初始字數，cumsum＝各版總長）
    // 渲染時以「實際容器寬度」計算座標（等比、無拉伸變形），視窗縮放時重繪
    _renderTrailCurve() {
        var box = document.getElementById('studio-trail-curve');
        if (!box) return;
        var versions = this._versions.slice().reverse(); // 舊→新
        if (versions.length < 2) { box.innerHTML = ''; return; }
        var cum = 0;
        var points = versions.map((v) => {
            cum += v.char_delta || 0;
            return { no: v.version_no, chars: Math.max(0, cum) };
        });
        var W = Math.max(240, Math.floor(box.clientWidth || 320));
        var H = 80, PAD_X = 10, PAD_Y = 10;
        var maxChars = Math.max.apply(null, points.map((p) => p.chars)) || 1;
        var minChars = Math.min.apply(null, points.map((p) => p.chars));
        var span = Math.max(1, maxChars - minChars);
        var xy = points.map((p, i) => {
            var x = PAD_X + (i / (points.length - 1)) * (W - 2 * PAD_X);
            var y = H - PAD_Y - ((p.chars - minChars) / span) * (H - 2 * PAD_Y);
            return [x, y, p];
        });
        var polyline = xy.map((a) => a[0].toFixed(1) + ',' + a[1].toFixed(1)).join(' ');
        var area = 'M' + PAD_X + ',' + (H - PAD_Y) + ' L' + polyline.split(' ').join(' L') + ' L' + (W - PAD_X) + ',' + (H - PAD_Y) + ' Z';
        var dots = xy.map((a, i) => {
            var last = i === xy.length - 1;
            var r = last ? 4 : 2.5;
            var fill = last ? 'rgb(var(--color-primary))' : 'rgb(var(--color-surface))';
            return '<circle cx="' + a[0].toFixed(1) + '" cy="' + a[1].toFixed(1) + '" r="' + r +
                '" fill="' + fill + '" stroke="rgb(var(--color-primary))" stroke-width="1.5">' +
                '<title>v' + a[2].no + ' · ' + a[2].chars + ' ' + this._t('charsUnit', '字') + '</title></circle>';
        }).join('');
        box.innerHTML =
            '<div class="flex items-center justify-between mb-1">' +
            '<span class="text-[10px] text-textMuted">' + this._t('curveTitle', '字數成長曲線') + '</span>' +
            '<span class="text-[10px] text-textMuted">v' + points[0].no + ' → v' + points[points.length - 1].no +
            ' · ' + points[points.length - 1].chars + ' ' + this._t('charsUnit', '字') + '</span>' +
            '</div>' +
            '<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '" class="max-w-full" role="img">' +
            '<path d="' + area + '" fill="rgb(var(--color-primary))" fill-opacity="0.08"/>' +
            '<polyline points="' + polyline + '" fill="none" stroke="rgb(var(--color-primary))" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>' +
            dots + '</svg>';
        // 視窗縮放 → 重繪（debounce 300ms，僅編輯視圖開啟時）
        if (!this._curveResizeBound) {
            this._curveResizeBound = true;
            var self = this;
            var t = null;
            window.addEventListener('resize', () => {
                if (t) clearTimeout(t);
                t = setTimeout(() => {
                    if (self._current && !document.getElementById('studio-editor-view')?.classList.contains('hidden')) {
                        self._renderTrailCurve();
                    }
                }, 300);
            });
        }
    },

    _sourceLabel(source) {
        if (source === 'ai_applied') return this._t('srcAi', '採納建議');
        if (source === 'autosave') return this._t('srcAuto', '自動');
        return this._t('srcHuman', '人工');
    },

    _renderTrail() {
        var trail = document.getElementById('studio-trail');
        if (!trail) return;
        this._renderTrailCurve();
        var items = this._mergeTrailItems();
        if (!items.length) {
            trail.innerHTML = '<p class="text-sm text-textMuted">' + this._t('noVersions', '尚無版本') + '</p>';
            return;
        }
        trail.innerHTML = items.map((it) => it.type === 'exchange'
            ? this._exchangeRowHTML(it)
            : this._versionRowHTML(it)).join('');
    },

    _mergeTrailItems() {
        var versions = (this._versions || []).map((v) => ({
            type: 'version', created_at: v.created_at || '', v: v,
        }));
        var exchanges = (this._exchanges || []).map((e) => ({
            type: 'exchange', created_at: e.created_at || '', e: e,
        }));
        return versions.concat(exchanges).sort((a, b) =>
            (a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0)
        ).reverse();
    },

    _exchangeRowHTML(it) {
        var e = it.e;
        var coach = e.coach || {};
        var qCount = (coach.clarifying_questions || []).length;
        var sCount = (coach.suggestions || []).length;
        var outcomes = e.outcomes || [];
        var adopted = outcomes.filter((o) => o.outcome === 'adopted').length;
        var dismissed = outcomes.filter((o) => o.outcome === 'dismissed').length;
        var details = '';
        if ((coach.clarifying_questions || []).length) {
            details += '<div class="mt-1.5"><p class="text-[10px] text-textMuted mb-0.5">' +
                this._t('coachQuestions', '教練反問') + '</p><ul class="space-y-0.5">' +
                coach.clarifying_questions.map((q) =>
                    '<li class="text-xs text-secondary flex gap-1.5"><span class="text-textMuted shrink-0">•</span><span>' + this._esc(q) + '</span></li>').join('') +
                '</ul></div>';
        }
        (coach.suggestions || []).forEach((s, i) => {
            var o = outcomes.find((x) => x.quote === s.quote);
            var badge = o
                ? (o.outcome === 'adopted'
                    ? '<span class="text-[9px] px-1 py-0.5 rounded bg-primary/15 text-primary">' + this._t('outcomeAdopted', '✓ 已採納') + '</span>'
                    : '<span class="text-[9px] px-1 py-0.5 rounded bg-surfaceHighlight text-textMuted">' + this._t('outcomeDismissed', '✕ 已忽略') + '</span>')
                : '';
            details += '<div class="text-xs mt-1 pl-2 border-l-2 border-borderSubtle/20">' +
                '<span class="text-textMuted">' + this._t('suggestionOn', '針對原句') + '：</span><span class="italic text-textMuted">' + this._esc(s.quote.slice(0, 40)) + (s.quote.length > 40 ? '…' : '') + '</span> ' + badge + '</div>';
        });
        return (
            '<details class="p-2.5 rounded-lg bg-primary/5 border border-borderSubtle/10">' +
            '<summary class="flex items-center gap-2 cursor-pointer list-none">' +
            '<i data-lucide="graduation-cap" class="w-3.5 h-3.5 text-primary shrink-0"></i>' +
            '<span class="text-xs font-medium text-primary shrink-0">' + this._t('exchangeRow', '教練對話') + '</span>' +
            '<span class="text-xs text-textMuted truncate flex-1">' + (e.question ? this._esc(e.question.slice(0, 30)) : this._t('reviewOnly', '看稿')) + '</span>' +
            '<span class="text-[10px] text-textMuted shrink-0">' + qCount + 'Q/' + sCount + 'S' +
            (adopted || dismissed ? ' · ✓' + adopted + ' ✕' + dismissed : '') + '</span>' +
            '<span class="text-[10px] text-textMuted shrink-0">' + this._esc((e.created_at || '').slice(5, 16).replace('T', ' ')) + '</span>' +
            '</summary>' + details + '</details>'
        );
    },

    // 輸入指紋顯示：打字分鐘數＋貼上；大量貼上（>500字或>版本一半）→ 警示徽章
    _fingerprintHTML(v) {
        var parts = [];
        if (v.edit_seconds > 0) parts.push(Math.max(1, Math.round(v.edit_seconds / 60)) + 'm');
        if (v.paste_events > 0) parts.push(this._t('pasteCount', '貼上') + '×' + v.paste_events);
        var contentLen = (v.char_delta != null ? Math.abs(v.char_delta) : 0);
        var heavy = (v.pasted_chars || 0) > 500 || (contentLen > 0 && (v.pasted_chars || 0) > contentLen * 0.5);
        var badge = heavy
            ? '<span class="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500 shrink-0">' + this._t('heavyPaste', '大量貼上') + '</span>'
            : '';
        var text = parts.length
            ? '<span class="text-[9px] text-textMuted/70 shrink-0">' + parts.join(' · ') + '</span>'
            : '';
        return badge + text;
    },

    _versionRowHTML(it) {
        var v = it.v;
        var badge = v.source === 'ai_applied'
            ? 'bg-primary/15 text-primary'
            : v.source === 'autosave'
                ? 'bg-surfaceHighlight text-textMuted'
                : 'bg-success/15 text-success';
        var delta = v.char_delta > 0 ? '+' + v.char_delta : String(v.char_delta);
        return (
            '<div class="flex items-center gap-3 p-2.5 rounded-lg bg-background/50 border border-borderSubtle/10">' +
            '<span class="text-xs font-mono text-textMuted shrink-0">v' + v.version_no + '</span>' +
            '<span class="text-[10px] px-1.5 py-0.5 rounded shrink-0 ' + badge + '">' + this._sourceLabel(v.source) + '</span>' +
            '<span class="text-xs text-textMuted truncate flex-1">' + this._esc(v.change_summary || '') + '</span>' +
            '<span class="text-[10px] font-mono text-textMuted shrink-0">' + delta + '</span>' +
            this._fingerprintHTML(v) +
            '<span class="text-[10px] text-textMuted shrink-0">' + this._esc((v.created_at || '').slice(5, 16).replace('T', ' ')) + '</span>' +
            '<button data-click="StudioTab.showDiff" data-click-arg="' + v.version_no +
            '" class="text-[10px] px-1.5 py-1 rounded bg-surfaceHighlight hover:text-secondary shrink-0">' +
            this._t('diff', 'diff') + '</button>' +
            '<button data-click="StudioTab.restoreVersion" data-click-arg="' + v.version_no +
            '" class="text-[10px] px-1.5 py-1 rounded bg-surfaceHighlight hover:text-secondary shrink-0">' +
            this._t('restore', '還原') + '</button>' +
            '</div>'
        );
    },

    async showDiff(versionNo) {
        var box = document.getElementById('studio-diff-box');
        if (!box || !this._current) return;
        var b = Number(versionNo);
        if (b <= 1) {
            box.classList.remove('hidden');
            box.innerHTML = '<pre class="text-xs text-textMuted whitespace-pre-wrap p-2">' +
                this._t('firstVersion', 'v1 為初始版，無前一版可比。') + '</pre>';
            return;
        }
        try {
            var data = await AppAPI.get(
                '/api/studio/drafts/' + encodeURIComponent(this._current.draft_id) +
                '/diff?a=' + (b - 1) + '&b=' + b
            );
            box.classList.remove('hidden');
            box.innerHTML =
                '<pre class="text-xs font-mono whitespace-pre-wrap p-3 rounded-lg bg-background/50 border border-borderSubtle/10 max-h-64 overflow-y-auto">' +
                this._esc(data.diff || this._t('noDiff', '(無差異)')) + '</pre>';
        } catch (err) {
            this._toast(this._errText(err), 'error');
        }
    },

    async restoreVersion(versionNo) {
        if (!this._current) return;
        try {
            var data = await AppAPI.get(
                '/api/studio/drafts/' + encodeURIComponent(this._current.draft_id) +
                '/versions/' + versionNo
            );
            this._content = data.content_md || '';
            document.getElementById('studio-editor').value = this._content;
            this._updateDirtyHint();
            this._toast(this._t('restored', '已載入 v' + versionNo + ' 內容（按「存檔」才會成為新版本）'));
        } catch (err) {
            this._toast(this._errText(err), 'error');
        }
    },

    // ── AI 教練 ─────────────────────────────────────────────────

    scrollToCoach() {
        var panel = document.getElementById('studio-coach-result')?.closest('.rounded-xl');
        if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
        document.getElementById('studio-coach-question')?.focus({ preventScroll: true });
    },

    // ── 參考專案（Discover→Studio 動線，design 2026-08-17 §Phase 2）──

    _renderReferences() {
        var box = document.getElementById('studio-references');
        if (!box) return;
        var refs = (this._current && this._current.references) || [];
        if (!refs.length) {
            box.innerHTML = '<p class="text-xs text-textMuted" data-i18n="studio.references.empty">' +
                this._t('references.empty', '尚無參考——到 Discover 搜尋後按「＋加到 Studio 參考」') + '</p>';
            return;
        }
        box.innerHTML = refs.map((r) =>
            '<div class="flex items-center gap-2 py-1">' +
            '<i data-lucide="link" class="w-3 h-3 shrink-0 text-textMuted"></i>' +
            '<a href="' + this._esc(this._safeUrl(r.url)) + '" target="_blank" rel="noopener noreferrer" ' +
            'class="flex-1 min-w-0 truncate text-sm text-secondary hover:text-primary">' +
            this._esc(r.title) + '</a>' +
            '<span class="text-[10px] px-1.5 py-0.5 rounded bg-surfaceHighlight text-textMuted shrink-0">' +
            this._esc(r.source === 'oc' ? 'Open Collective' : (r.source || '')) + '</span>' +
            '<button data-click="StudioTab.removeReference" data-click-arg="' + encodeURIComponent(r.url) + '" ' +
            'class="p-1 rounded hover:bg-surfaceHighlight text-textMuted shrink-0" aria-label="remove" data-i18n="studio.removeReference" data-i18n-attr="aria-label">' +
            '<i data-lucide="x" class="w-3.5 h-3.5"></i></button>' +
            '</div>'
        ).join('');
        if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
    },

    async removeReference(url) {
        if (!this._current || !url) return;
        try {
            var data = await AppAPI.delete(
                '/api/studio/drafts/' + encodeURIComponent(this._current.draft_id) +
                '/references?url=' + encodeURIComponent(url)
            );
            this._current.references = data.references || [];
            this._renderReferences();
        } catch (err) {
            this._toast(this._errText(err), 'error');
        }
    },

    _esc(str) {
        if (str === null || str === undefined) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    },

    async askCoach() {
        if (!this._current) return;
        this.scrollToCoach();
        this._coachExchangeId = null;
        this._dismissedQuotes = {};
        var result = document.getElementById('studio-coach-result');
        var question = document.getElementById('studio-coach-question')?.value?.trim() || '';
        var language = (window.I18n && typeof window.I18n.getLanguage === 'function')
            ? window.I18n.getLanguage() : 'zh-TW';
        result.innerHTML =
            '<div class="flex items-center gap-2 text-sm text-textMuted py-2">' +
            '<i data-lucide="loader-circle" class="w-4 h-4 animate-spin"></i>' +
            this._t('coachThinking', '教練看稿中，約需數十秒…') + '</div>';
        if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
        try {
            var data = await AppAPI.post(
                '/api/studio/drafts/' + encodeURIComponent(this._current.draft_id) + '/coach',
                {
                    question: question,
                    language: language,
                    // 參考專案 opt-in（design §Phase 2）：預設不含，勾選才進教練上下文
                    use_references: !!(document.getElementById('studio-coach-refs')?.checked),
                },
                { timeout: 100000 }
            );
            this._coachExchangeId = data.exchange_id || null;
            this._renderCoach(data.coach || {});
        } catch (err) {
            var status = err && err.status;
            result.innerHTML = '<p class="text-sm text-textMuted">' + this._errText(err) +
                (status === 404 ? '（' + this._t('disabled', '功能未開放') + '）' : '') + '</p>';
        }
    },

    _renderCoach(coach) {
        var result = document.getElementById('studio-coach-result');
        if (!result) return;
        var html = '';
        if ((coach.clarifying_questions || []).length) {
            html += this._coachSection(
                this._t('coachQuestions', '教練反問'), 'help-circle', 'text-primary',
                coach.clarifying_questions
            );
        }
        (coach.suggestions || []).forEach((s, i) => {
            html +=
                '<div class="rounded-lg bg-background/50 border border-borderSubtle/10 p-3">' +
                '<div class="flex items-center gap-2 mb-1.5">' +
                '<span class="text-[10px] px-1.5 py-0.5 rounded bg-primary/15 text-primary">' + this._esc(s.kind) + '</span>' +
                '<span class="text-xs text-textMuted">' + this._t('suggestionOn', '針對原句') + '</span>' +
                '</div>' +
                '<p class="text-xs text-textMuted italic mb-2 border-l-2 border-borderSubtle/20 pl-2">' +
                this._esc(s.quote) + '</p>' +
                '<textarea class="coach-suggestion-edit w-full bg-surfaceHighlight border border-borderSubtle/10 rounded-lg px-2.5 py-2 text-sm text-secondary resize-y mb-2" rows="2" data-suggestion-index="' + i + '">' +
                this._esc(s.suggestion) + '</textarea>' +
                '<button data-click="StudioTab.adoptSuggestion" data-click-arg="' + i +
                '" class="studio-adopt-btn px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap" data-quote-ok="' +
                (this._content.includes(s.quote) ? '1' : '0') + '">' +
                (this._content.includes(s.quote)
                    ? this._t('adopt', '採納（存為新版本）')
                    : this._t('adoptStale', '原句已變更（重新看稿）')) +
                '</button>' +
                '<button data-click="StudioTab.dismissSuggestion" data-click-arg="' + i +
                '" class="studio-dismiss-btn px-3 py-1.5 rounded-lg bg-surfaceHighlight text-textMuted text-xs hover:text-secondary whitespace-nowrap">' +
                this._t('dismiss', '忽略') + '</button>' +
                '</div>';
        });
        this._coachSuggestions = coach.suggestions || [];
        if ((coach.strength_notes || []).length) {
            html += this._coachSection(
                this._t('coachStrengths', '寫得好的地方'), 'circle-check', 'text-success',
                coach.strength_notes
            );
        }
        if (!html) {
            html = '<p class="text-sm text-textMuted">' + this._t('coachEmpty', '教練沒有產出建議，試著補充稿件內容再問一次。') + '</p>';
        }
        result.innerHTML = html;
        this._refreshAdoptButtons();
        if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
    },

    // 依 quote 是否仍存在於當前內容，啟用/停用採納鈕（editor input 後重算）
    _refreshAdoptButtons() {
        document.querySelectorAll('.studio-adopt-btn').forEach((btn) => {
            var i = Number(btn.getAttribute('data-click-arg'));
            var s = (this._coachSuggestions || [])[i];
            var ok = !!(s && this._content.includes(s.quote));
            btn.setAttribute('data-quote-ok', ok ? '1' : '0');
            btn.className = 'studio-adopt-btn px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap ' +
                (ok ? 'bg-primary text-background' : 'bg-surfaceHighlight text-textMuted cursor-not-allowed');
            btn.textContent = ok
                ? this._t('adopt', '採納（存為新版本）')
                : this._t('adoptStale', '原句已變更（重新看稿）');
        });
    },

    _coachSection(title, icon, color, items) {
        return (
            '<div><p class="text-xs font-medium text-textMuted mb-1.5 flex items-center gap-1.5">' +
            '<i data-lucide="' + icon + '" class="w-3.5 h-3.5 ' + color + '"></i>' + title + '</p>' +
            '<ul class="space-y-1.5">' +
            items.map((x) => '<li class="text-sm text-secondary flex gap-2"><span class="text-textMuted shrink-0">•</span><span>' + this._esc(x) + '</span></li>').join('') +
            '</ul></div>'
        );
    },

    async adoptSuggestion(index) {
        var s = (this._coachSuggestions || [])[Number(index)];
        if (!s || !this._current) return;
        var edited = document.querySelector('.coach-suggestion-edit[data-suggestion-index="' + index + '"]')?.value;
        if (edited == null || !edited.trim()) edited = s.suggestion;
        if (!this._content.includes(s.quote)) {
            this._toast(this._t('quoteMissing', '找不到建議對應的原句（稿件已改動），請重新請教練看稿'), 'error');
            return;
        }
        // 替換預覽確認（平台對話框）
        var ok = await window.showConfirmDialog({
            title: this._t('adoptConfirm', '以建議內容替換原句並存為新版本？'),
            message: '「' + s.quote.slice(0, 60) + (s.quote.length > 60 ? '…' : '') + '」→「' + edited.slice(0, 120) + (edited.length > 120 ? '…' : '') + '」',
            confirmText: this._t('adopt', '採納'),
        });
        if (!ok) return;
        this._content = this._content.replace(s.quote, edited);
        var editor = document.getElementById('studio-editor');
        editor.value = this._content;
        // 預覽開著也要同步（否則看起來「沒有變」）
        var previewEl = document.getElementById('studio-preview');
        if (previewEl && !previewEl.classList.contains('hidden')) {
            previewEl.textContent = this._content || ' ';
        }
        this._updateDirtyHint();
        // 變更可見性：選取被替換的新文字並捲動到該處（清楚回饋改了哪裡）
        var at = this._content.indexOf(edited);
        if (at >= 0) {
            try {
                editor.focus({ preventScroll: true });
                editor.setSelectionRange(at, at + edited.length);
            } catch (e) { /* 部分瀏覽器在 hidden textarea 上 setSelectionRange 例外：忽略 */ }
            editor.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
        var versionNo = await this._saveVersion(
            'ai_applied',
            this._t('adoptSummary', '採納教練建議') + '（' + s.kind + '）'
        );
        await this._recordOutcome(s.quote, 'adopted', versionNo);
    },

    // ── 匯出 ────────────────────────────────────────────────────

    async exportDraft() {
        if (!this._current) return;
        // 有未存變更先存一版
        if (this._content !== this._savedContent) {
            await this._saveVersion('human', this._t('exportSummary', '匯出前存檔'));
        }
        try {
            var data = await AppAPI.post(
                '/api/studio/drafts/' + encodeURIComponent(this._current.draft_id) + '/export'
            );
            document.getElementById('studio-export-content').value = data.markdown || '';
            document.getElementById('studio-export-modal')?.classList.remove('hidden');
            document.body.style.overflow = 'hidden';
            this._current.status = 'exported';
            this._updateEditorHeader();
        } catch (err) {
            this._toast(this._errText(err), 'error');
        }
    },

    async copyExport() {
        var ta = document.getElementById('studio-export-content');
        if (!ta) return;
        try {
            await navigator.clipboard.writeText(ta.value);
            this._toast(this._t('copied', '已複製'));
        } catch (e) {
            ta.select();
            document.execCommand('copy');
            this._toast(this._t('copied', '已複製'));
        }
    },

    closeExport() {
        document.getElementById('studio-export-modal')?.classList.add('hidden');
        document.body.style.overflow = '';
    },

    async shareDraft() {
        if (!this._current) return;
        try {
            var data = await AppAPI.post(
                '/api/studio/drafts/' + encodeURIComponent(this._current.draft_id) + '/share'
            );
            this._current.share_token = data.share_token;
            var input = document.getElementById('studio-share-url');
            input.value = location.origin + data.share_url;
            document.getElementById('studio-share-modal')?.classList.remove('hidden');
            document.body.style.overflow = 'hidden';
        } catch (err) {
            this._toast(this._errText(err), 'error');
        }
    },

    async copyShare() {
        var input = document.getElementById('studio-share-url');
        try {
            await navigator.clipboard.writeText(input.value);
            this._toast(this._t('copied', '已複製'));
        } catch (e) {
            input.select();
            document.execCommand('copy');
            this._toast(this._t('copied', '已複製'));
        }
    },

    async revokeShare() {
        if (!this._current) return;
        var ok = await window.showConfirmDialog({
            title: this._t('revokeConfirm', '撤銷分享？'),
            message: this._t('revokeConfirmBody', '既有連結將立即失效，無法復原。'),
            danger: true,
        });
        if (!ok) return;
        try {
            await AppAPI.delete(
                '/api/studio/drafts/' + encodeURIComponent(this._current.draft_id) + '/share'
            );
            this._current.share_token = null;
            this.closeShare();
            this._toast(this._t('revoked', '已撤銷分享'));
        } catch (err) {
            this._toast(this._errText(err), 'error');
        }
    },

    closeShare() {
        document.getElementById('studio-share-modal')?.classList.add('hidden');
        document.body.style.overflow = '';
    },

    _noop() {
        // modal 內部點擊止泡
    },
};

window.StudioTab = StudioTab;
