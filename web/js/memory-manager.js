/**
 * AI Memory Manager — 載入/新增/編輯/刪除使用者記憶。
 *
 * 在 settings tab 載入時初始化。呼叫後端 /api/memory/facts API。
 * 使用 fetch（同源 cookie session）。
 * key 標籤走 i18n（settings.memory.keyLabels.*），value 過長截斷。
 */
window.MemoryManager = (function () {
    let memories = [];
    // 搜尋查詢：模組級狀態，跨 re-render 保留（同 toolSettings 的 _toolSearchQuery）
    let _searchQuery = '';

    /**
     * 純函式：依查詢字串過濾記憶（filter key + value，case-insensitive）。
     * 抽成獨立函式方便測試；不觸碰 DOM。
     * @param {Array} items - 記憶陣列（每項含 key, value, category）
     * @param {string} query - 原始查詢字串（空白不敏感）
     * @returns {Array} 過濾後陣列
     */
    function _filterMemories(items, query) {
        var q = String(query || '').trim().toLowerCase();
        if (!q) return items;
        return items.filter(function (m) {
            var key = String(m.key || '').toLowerCase();
            var value = String(m.value || '').toLowerCase();
            return key.includes(q) || value.includes(q);
        });
    }

    function _t(key) {
        return window.I18n ? window.I18n.t('settings.memory.' + key) : key;
    }

    async function load() {
        const listEl = document.getElementById('memory-list');
        const emptyEl = document.getElementById('memory-empty');
        if (!listEl) return;

        try {
            listEl.innerHTML =
                '<p class="text-sm text-textMuted text-center py-4">' +
                _t('loading') +
                '</p>';
            const resp = await fetch('/api/memory/facts', { credentials: 'include' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await resp.json();
            var shared = data.facts || [];
            // Mixer Step 4：agent 私有層（失敗不影響共享層顯示）
            var priv = [];
            try {
                const p = await fetch('/api/memory/private', { credentials: 'include' });
                if (p.ok) {
                    const pd = await p.json();
                    priv = (pd.private || []).map(function (r) {
                        return {
                            key: r.key,
                            value: r.value,
                            category: r.category || 'fact',
                            confidence: r.confidence || 'high',
                            valid_until: null,
                            status: 'active',
                            verified_at: null,
                            agent_id: r.agent_id,
                        };
                    });
                }
            } catch (e) { /* 私有層讀取失敗＝無私有記憶，静默 */ }
            memories = shared.concat(priv);
            render();
        } catch (e) {
            listEl.innerHTML =
                '<p class="text-sm text-red-400 text-center py-4">' +
                _t('loadFailed') +
                '</p>';
        }
    }

    function render() {
        const listEl = document.getElementById('memory-list');
        const emptyEl = document.getElementById('memory-empty');
        if (!listEl) return;

        if (memories.length === 0) {
            listEl.classList.add('hidden');
            if (emptyEl) emptyEl.classList.remove('hidden');
            return;
        }
        listEl.classList.remove('hidden');
        if (emptyEl) emptyEl.classList.add('hidden');

        // 搜尋過濾
        var filtered = _filterMemories(memories, _searchQuery);
        var hasQuery = String(_searchQuery || '').trim().length > 0;

        const catColors = {
            preference: 'rgb(34,197,94)',
            holding: 'rgb(59,130,246)',
            context: 'rgb(168,85,247)',
            fact: 'rgb(148,163,184)',
        };
        const catLabels = {
            preference: _t('catPreference'),
            holding: _t('catHolding'),
            context: _t('catContext'),
            fact: _t('catFact'),
        };

        // 搜尋框（sticky，同 toolSettings pattern）
        var searchBox =
            '<div class="sticky top-0 z-10 pb-2 mb-1 bg-background/95 backdrop-blur">' +
            '  <div class="relative">' +
            '    <i data-lucide="search" class="w-3.5 h-3.5 text-textMuted absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"></i>' +
            '    <input type="text" id="memory-search-input" value="' + _esc(_searchQuery).replace(/"/g, '&quot;') + '"' +
            '      class="w-full pl-9 pr-3 py-2 rounded-xl bg-background border border-borderLight text-sm text-secondary placeholder:text-textMuted/60 focus:border-primary/50 focus:outline-none transition"' +
            '      placeholder="' + _t('searchPlaceholder') + '" autocomplete="off">' +
            '  </div>' +
            '</div>';

        var rowsHtml = filtered
            .map(function (m) {
                const color = catColors[m.category] || catColors.fact;
                const label = catLabels[m.category] || catLabels.fact;
                var keyLabel = _keyToLabel(m.key);

                // ── c031 治理徽章（Part B）────────────────────────────
                var govBadges = '';
                var status = m.status || 'active';
                var isVerified = !!m.verified_at;
                if (isVerified) {
                    govBadges += '<span class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-green-500/15 text-green-600 flex items-center gap-0.5"><i data-lucide="badge-check" class="w-3 h-3"></i>' + _t('verified') + '</span>';
                }
                if (status === 'superseded') {
                    govBadges += '<span class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/15 text-amber-600">' + _t('superseded') + '</span>';
                }
                var validUntilStr = '';
                if (m.valid_until) {
                    var vu = new Date(m.valid_until);
                    var daysLeft = Math.ceil((vu - Date.now()) / 86400000);
                    if (daysLeft <= 7) {
                        validUntilStr = '<span class="text-[10px] text-amber-500/80">' + _t('expiresIn').replace('{d}', daysLeft) + '</span>';
                    }
                }

                var agentBadge = m.agent_id
                    ? '<span class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-violet-500/15 text-violet-500" title="' +
                      _esc(_t('agentPrivateTooltip')) + '">' +
                      _esc(_t('agentPrivate').replace('{{agent}}', m.agent_id)) + '</span>'
                    : '';
                var isPrivate = !!m.agent_id;
                return (
                    '<div class="flex items-start gap-3 p-3 rounded-xl bg-background/50 border border-borderSubtle group" data-memory-key="' +
                    _esc(m.key) +
                    '" data-agent-id="' + _esc(m.agent_id || '') + '">' +
                    '  <div class="flex-1 min-w-0">' +
                    '    <div class="flex items-center gap-2 mb-1 flex-wrap">' +
                    '      <span class="px-1.5 py-0.5 rounded text-[10px] font-medium" style="background:' +
                    color +
                    '20;color:' +
                    color +
                    ';">' +
                    label +
                    '</span>' +
                    govBadges +
                    agentBadge +
                    validUntilStr +
                    '      <span class="text-[10px] text-textMuted/60">' + _esc(keyLabel) + '</span>' +
                    '    </div>' +
                    '    <p class="text-sm text-secondary memory-value line-clamp-2" title="' +
                    _esc(m.value) +
                    '">' +
                    _esc(m.value) +
                    '</p>' +
                    '    <input type="text" class="hidden w-full bg-background border border-primary/40 rounded-lg px-2 py-1 text-sm text-secondary focus:outline-none memory-edit-input" value="' +
                    _esc(m.value) +
                    '" maxlength="512">' +
                    '  </div>' +
                    '  <div class="flex items-center gap-1 shrink-0">' +
                    (!isVerified && !isPrivate
                        ? '<button class="memory-verify-btn p-1.5 rounded-lg text-textMuted hover:text-green-500 hover:bg-green-500/10 transition" title="' + _t('verifyTooltip') + '">' +
                          '<i data-lucide="badge-check" class="w-3.5 h-3.5"></i>' +
                          '</button>'
                        : '') +
                    (!isPrivate
                        ? '    <button class="memory-edit-btn p-1.5 rounded-lg text-textMuted hover:text-primary hover:bg-surfaceHighlight transition" title="Edit" data-i18n="common.edit" data-i18n-attr="title">' +
                          '      <i data-lucide="pencil" class="w-3.5 h-3.5"></i>' +
                          '    </button>' +
                          '    <button class="memory-save-btn hidden p-1.5 rounded-lg text-green-400 hover:bg-green-400/10 transition" title="Save" data-i18n="common.save" data-i18n-attr="title">' +
                          '      <i data-lucide="check" class="w-3.5 h-3.5"></i>' +
                          '    </button>'
                        : '') +
                    '    <button class="memory-delete-btn p-1.5 rounded-lg text-textMuted hover:text-red-400 hover:bg-red-400/10 transition" title="Delete" data-i18n="common.delete" data-i18n-attr="title">' +
                    '      <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>' +
                    '    </button>' +
                    '  </div>' +
                    '</div>'
                );
            })
            .join('');

        var noResultHtml =
            hasQuery && filtered.length === 0
                ? '<p class="text-sm text-textMuted text-center py-4">' + _t('noSearchResult') + '</p>'
                : '';

        listEl.innerHTML = searchBox + rowsHtml + noResultHtml;

        if (window.lucide) window.lucide.createIcons({ nodes: [listEl] });
        bindEvents();
        _bindSearchInput(listEl);
    }

    /**
     * 綁定記憶搜尋框：輸入時 debounce 200ms 重新渲染（同 toolSettings pattern）。
     * 渲染後還原焦點與游標位置，避免打字跳針。
     */
    function _bindSearchInput(scope) {
        var input = scope.querySelector('#memory-search-input');
        if (!input) return;
        var debounceFn = window.Utils && window.Utils.debounce
            ? window.Utils.debounce
            : function (fn) { return fn; };
        input.addEventListener(
            'input',
            debounceFn(function (e) {
                _searchQuery = e.target.value;
                render();
                var restored = document.getElementById('memory-search-input');
                if (restored) {
                    restored.focus();
                    var len = restored.value.length;
                    restored.setSelectionRange(len, len);
                }
            }, 200)
        );
    }

    function bindEvents() {
        const listEl = document.getElementById('memory-list');
        if (!listEl) return;

        // c031 治理：verify 按鈕（標記「使用者已確認」——排序提升＋信任徽章）
        listEl.querySelectorAll('.memory-verify-btn').forEach(function (btn) {
            btn.addEventListener('click', async function () {
                var item = this.closest('[data-memory-key]');
                var key = item.getAttribute('data-memory-key');
                try {
                    var resp = await fetch(
                        '/api/memory/facts/' + encodeURIComponent(key) + '/verify',
                        { method: 'PUT', credentials: 'include' }
                    );
                    if (!resp.ok) throw new Error('HTTP ' + resp.status);
                    if (typeof window.showToast === 'function') {
                        window.showToast(_t('verifySuccess'), 'success');
                    }
                    // 重新載入以顯示 verified 徽章
                    load();
                } catch (e) {
                    console.error('[MemoryManager] verify failed:', e);
                    if (typeof window.showToast === 'function') {
                        window.showToast(_t('verifyFailed'), 'error');
                    }
                }
            });
        });

        // 點 value 文字 → 開彈窗完整檢視 + 編輯（長內容用）
        listEl.querySelectorAll('.memory-value').forEach(function (el) {
            el.style.cursor = 'pointer';
            el.addEventListener('click', function () {
                var item = this.closest('[data-memory-key]');
                var key = item.getAttribute('data-memory-key');
                var agentId = item.getAttribute('data-agent-id') || '';
                var m = memories.find(function (mm) {
                    return mm.key === key && (mm.agent_id || '') === agentId;
                });
                if (!m || !window.ContentModal) return;
                var catColors = {
                    preference: 'rgb(34,197,94)', holding: 'rgb(59,130,246)',
                    context: 'rgb(168,85,247)', fact: 'rgb(148,163,184)',
                };
                var catLabels = {
                    preference: _t('catPreference'), holding: _t('catHolding'),
                    context: _t('catContext'), fact: _t('catFact'),
                };
                window.ContentModal.open({
                    title: _keyToLabel(m.key),
                    subtitle: _t('title'),
                    badge: { label: catLabels[m.category] || catLabels.fact, color: catColors[m.category] || catColors.fact },
                    body: m.value,
                    editable: !m.agent_id,
                    maxLength: 2000,
                    onSave: function (newValue) { return saveMemory(key, newValue, item); },
                    onDelete: function () { return deleteMemory(key, item); },
                });
            });
        });

        // Edit
        listEl.querySelectorAll('.memory-edit-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const item = this.closest('[data-memory-key]');
                item.querySelector('.memory-value').classList.add('hidden');
                item.querySelector('.memory-edit-input').classList.remove('hidden');
                item.querySelector('.memory-edit-btn').classList.add('hidden');
                item.querySelector('.memory-save-btn').classList.remove('hidden');
                item.querySelector('.memory-edit-input').focus();
            });
        });

        // Save
        listEl.querySelectorAll('.memory-save-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const item = this.closest('[data-memory-key]');
                const key = item.getAttribute('data-memory-key');
                const newValue = item.querySelector('.memory-edit-input').value.trim();
                if (!newValue) return;
                saveMemory(key, newValue, item);
            });
        });

        // Delete
        listEl.querySelectorAll('.memory-delete-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var item = this.closest('[data-memory-key]');
                var key = item.getAttribute('data-memory-key');
                if (typeof window.showConfirm === 'function') {
                    window
                        .showConfirm({
                            title: _t('confirmDelete'),
                            message: _t('confirmDeleteDesc'),
                            type: 'danger',
                            confirmText: window.I18n ? window.I18n.t('common.delete') : 'Delete',
                        })
                        .then(function (ok) {
                            if (ok) deleteMemory(key, item);
                        });
                } else {
                    deleteMemory(key, item);
                }
            });
        });
    }

    async function saveMemory(key, value, itemEl) {
        try {
            // 私有記憶不可透過 PATCH 編輯（共享層專屬操作；UI 已隱藏，雙保險）
            var agentId = itemEl ? itemEl.getAttribute('data-agent-id') || '' : '';
            if (agentId) {
                if (window.showToast) window.showToast(_t('privateReadOnly'), 'error');
                return false;
            }
            const cat = memories.find(function (m) {
                return m.key === key && !m.agent_id;
            });
            const resp = await fetch('/api/memory/facts/' + encodeURIComponent(key), {
                method: 'PATCH',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    value: value,
                    category: cat ? cat.category : 'fact',
                }),
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            itemEl.querySelector('.memory-value').textContent = value;
            itemEl.querySelector('.memory-value').classList.remove('hidden');
            itemEl.querySelector('.memory-edit-input').classList.add('hidden');
            itemEl.querySelector('.memory-edit-btn').classList.remove('hidden');
            itemEl.querySelector('.memory-save-btn').classList.add('hidden');
            const m = memories.find(function (mm) {
                return mm.key === key && !mm.agent_id;
            });
            if (m) m.value = value;
            return true;
        } catch (e) {
            if (window.showToast) window.showToast(_t('saveFailed'), 'error');
            return false;
        }
    }

    async function deleteMemory(key, itemEl) {
        try {
            var agentId = itemEl ? itemEl.getAttribute('data-agent-id') || '' : '';
            var url = '/api/memory/facts/' + encodeURIComponent(key);
            if (agentId) url += '?agent_id=' + encodeURIComponent(agentId);
            const resp = await fetch(url, {
                method: 'DELETE',
                credentials: 'include',
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            itemEl.remove();
            memories = memories.filter(function (m) {
                return !(m.key === key && (m.agent_id || '') === agentId);
            });
            if (memories.length === 0) render();
            return true;
        } catch (e) {
            if (window.showToast) window.showToast(_t('deleteFailed'), 'error');
            return false;
        }
    }

    // ── 新增記憶 ──────────────────────────────────────────────────

    function openAddEditor() {
        var editor = document.getElementById('memory-add-editor');
        if (!editor) return;
        editor.classList.remove('hidden');
        document.getElementById('memory-add-key').value = '';
        document.getElementById('memory-add-value').value = '';
        document.getElementById('memory-add-key').focus();
        editor.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    async function addMemory() {
        var key = document.getElementById('memory-add-key').value.trim();
        var value = document.getElementById('memory-add-value').value.trim();
        if (!key) { if (window.showToast) window.showToast(_t('keyRequired'), 'error'); return; }
        if (!value) return;
        try {
            var resp = await fetch('/api/memory/facts', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: key, value: value, category: 'fact' }),
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            document.getElementById('memory-add-editor').classList.add('hidden');
            load();
        } catch (e) {
            if (window.showToast) window.showToast(_t('addFailed'), 'error');
        }
    }

    function _keyToLabel(key) {
        // 走 i18n（settings.memory.keyLabels.*），依使用者選擇語言顯示。
        // keyLabels 是 dynamic dict，key 不存在時 fallback 到原始 key（底線轉空格）。
        if (window.I18n) {
            var label = window.I18n.t('settings.memory.keyLabels.' + key);
            if (label && label !== 'settings.memory.keyLabels.' + key) return label;
        }
        return key.replace(/_/g, ' ');
    }

    function _esc(s) {
        if (typeof window.escapeHtml === 'function') return window.escapeHtml(s);
        return String(s == null ? '' : s).replace(/</g, '&lt;');
    }

    function init() {
        const refreshBtn = document.getElementById('btn-refresh-memory');
        if (refreshBtn) refreshBtn.addEventListener('click', load);
        var addBtn = document.getElementById('btn-add-memory');
        if (addBtn) addBtn.addEventListener('click', openAddEditor);
        var saveAddBtn = document.getElementById('btn-save-memory');
        if (saveAddBtn) saveAddBtn.addEventListener('click', addMemory);
        var cancelAddBtn = document.getElementById('btn-cancel-memory');
        if (cancelAddBtn)
            cancelAddBtn.addEventListener('click', function () {
                document.getElementById('memory-add-editor').classList.add('hidden');
            });
        load();
    }

    return { init: init, load: load, _filterMemories: _filterMemories };
})();
