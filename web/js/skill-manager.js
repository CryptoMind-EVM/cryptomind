/**
 * Skill Manager — 官方 skill 開關 + 使用者自訂 skill 管理。
 *
 * 在 settings tab 載入時初始化。呼叫後端 /api/skills API。
 * 使用 fetch（同源 cookie session，同 memory-manager 模式）。
 *
 * 安全：所有使用者輸入顯示時經 _esc() HTML escape（防 XSS）。
 */
window.SkillManager = (function () {
    let officialSkills = [];
    let customSkills = [];
    // 搜尋查詢：官方 + 自訂共用一個框，同時 filter 兩邊（同 toolSettings pattern）
    let _searchQuery = '';

    /**
     * 純函式：依查詢字串過濾 skill（case-insensitive）。
     * 官方 skill 比對 name + description + auto_firekeywords；
     * 自訂 skill 比對 name + description + trigger_keywords + body。
     * 抽成獨立函式方便測試；不觸碰 DOM。
     * @param {Array} items - skill 陣列
     * @param {string} query - 原始查詢字串
     * @param {boolean} isCustom - 是否為自訂 skill（決定要比對 body/trigger_keywords）
     * @returns {Array} 過濾後陣列
     */
    function _filterSkill(items, query, isCustom) {
        var q = String(query || '').trim().toLowerCase();
        if (!q) return items;
        return items.filter(function (s) {
            var name = String(s.name || '').toLowerCase();
            var desc = String(s.description || '').toLowerCase();
            if (name.includes(q) || desc.includes(q)) return true;
            if (isCustom) {
                var trig = String(s.trigger_keywords || '').toLowerCase();
                var body = String(s.body || '').toLowerCase();
                if (trig.includes(q) || body.includes(q)) return true;
            } else {
                var auto = (s.auto_fire_keywords || []).join(' ').toLowerCase();
                if (auto.includes(q)) return true;
            }
            return false;
        });
    }

    async function load() {
        const officialEl = document.getElementById('skill-official-list');
        const customEl = document.getElementById('skill-custom-list');
        if (!officialEl && !customEl) return;

        try {
            const resp = await fetch('/api/skills', { credentials: 'include' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await resp.json();
            officialSkills = data.official || [];
            customSkills = data.custom || [];
            renderOfficial();
            renderCustom();
        } catch (e) {
            if (officialEl)
                officialEl.innerHTML =
                    '<p class="text-sm text-red-400 text-center py-4">' +
                    _t('loadFailed') +
                    '</p>';
        }
    }

    function renderOfficial() {
        const listEl = document.getElementById('skill-official-list');
        if (!listEl) return;

        // 共用搜尋框：放在官方 list 頂部，視覺上涵蓋官方 + 自訂（官方在上方）
        var hasAny = officialSkills.length > 0 || customSkills.length > 0;
        var searchBox = hasAny
            ? '<div class="sticky top-0 z-10 pb-2 mb-2 bg-background/95 backdrop-blur">' +
              '  <div class="relative">' +
              '    <i data-lucide="search" class="w-3.5 h-3.5 text-textMuted absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"></i>' +
              '    <input type="text" id="skill-search-input" value="' + _esc(_searchQuery).replace(/"/g, '&quot;') + '"' +
              '      class="w-full pl-9 pr-3 py-2 rounded-xl bg-background border border-borderLight text-sm text-secondary placeholder:text-textMuted/60 focus:border-primary/50 focus:outline-none transition"' +
              '      placeholder="' + _t('searchPlaceholder') + '" autocomplete="off">' +
              '  </div>' +
              '</div>'
            : '';

        if (officialSkills.length === 0 && customSkills.length === 0) {
            listEl.innerHTML =
                '<p class="text-sm text-textMuted text-center py-4">' +
                _t('empty') +
                '</p>';
            return;
        }

        var filtered = _filterSkill(officialSkills, _searchQuery, false);
        var hasQuery = String(_searchQuery || '').trim().length > 0;

        var rowsHtml = filtered
            .map(function (s) {
                var checked = s.is_enabled !== false ? 'checked' : '';
                var eager = s.eager_load
                    ? '<span class="px-1 py-0.5 rounded text-[9px] ml-1" style="background:rgba(245,158,11,0.15);color:rgb(245,158,11);">eager</span>'
                    : '';
                return (
                    '<div class="flex items-center gap-3 p-3 rounded-xl bg-background/50 border border-borderSubtle" data-skill-name="' +
                    _esc(s.name) +
                    '">' +
                    '  <div class="flex-1 min-w-0">' +
                    '    <div class="flex items-center gap-1.5">' +
                    '      <span class="px-1.5 py-0.5 rounded text-[10px] font-medium" style="background:rgba(59,130,246,0.15);color:rgb(59,130,246);">' +
                    _t('official') +
                    '</span>' +
                    eager +
                    '      <span class="text-sm text-secondary font-medium">' +
                    _esc(_officialField(s, 'name')) +
                    '</span>' +
                    '    </div>' +
                    '    <p class="text-xs text-textMuted mt-0.5 line-clamp-1 cursor-pointer hover:text-secondary transition skill-desc">' +
                    _esc(_officialField(s, 'description')) +
                    '</p>' +
                    '  </div>' +
                    '  <label class="relative inline-flex items-center cursor-pointer shrink-0">' +
                    '    <input type="checkbox" class="sr-only peer skill-toggle" ' +
                    checked +
                    '>' +
                    '    <div class="w-9 h-5 bg-surfaceHighlight rounded-full peer peer-checked:bg-primary transition"></div>' +
                    '    <div class="absolute left-0.5 top-0.5 w-4 h-4 bg-textMuted rounded-full peer-checked:translate-x-4 peer-checked:bg-white transition"></div>' +
                    '  </label>' +
                    '</div>'
                );
            })
            .join('');

        var noResultHtml =
            hasQuery && filtered.length === 0
                ? '<p class="text-sm text-textMuted text-center py-3">' + _t('noSearchResult') + '</p>'
                : '';

        listEl.innerHTML = searchBox + rowsHtml + noResultHtml;
        if (window.lucide) window.lucide.createIcons({ nodes: [listEl] });
        bindToggleEvents();
        bindOfficialClickEvents();
        _bindSearchInput();
    }

    /**
     * 綁定 skill 搜尋框：輸入時 debounce 200ms，同時重 render 官方 + 自訂。
     * 渲染後還原焦點與游標位置。
     */
    function _bindSearchInput() {
        var input = document.getElementById('skill-search-input');
        if (!input) return;
        var debounceFn = window.Utils && window.Utils.debounce
            ? window.Utils.debounce
            : function (fn) { return fn; };
        input.addEventListener(
            'input',
            debounceFn(function (e) {
                _searchQuery = e.target.value;
                renderOfficial();
                renderCustom();
                var restored = document.getElementById('skill-search-input');
                if (restored) {
                    restored.focus();
                    var len = restored.value.length;
                    restored.setSelectionRange(len, len);
                }
            }, 200)
        );
    }

    function bindOfficialClickEvents() {
        // 點官方 skill 描述 → 開彈窗顯示完整資訊（唯讀，官方 skill 不可改內容）
        document.querySelectorAll('.skill-desc').forEach(function (el) {
            el.addEventListener('click', function (e) {
                e.stopPropagation();
                var item = this.closest('[data-skill-name]');
                var name = item.getAttribute('data-skill-name');
                var s = officialSkills.find(function (sk) { return sk.name === name; });
                if (!s || !window.ContentModal) return;
                var keywords = (s.auto_fire_keywords || []).join(', ');
                window.ContentModal.open({
                    title: _officialField(s, 'name') || s.name,
                    subtitle: _t('official') + ' Skill',
                    badge: { label: s.eager_load ? 'eager load' : '', color: 'rgb(245,158,11)' },
                    body:
                        (_officialField(s, 'description') || s.description || '') +
                        (keywords ? '\n\n**' + _t('triggerLabelShort') + ':** ' + keywords : ''),
                    editable: false,
                });
            });
        });
    }

    function renderCustom() {
        const listEl = document.getElementById('skill-custom-list');
        if (!listEl) return;
        if (customSkills.length === 0) {
            listEl.innerHTML =
                '<p class="text-xs text-textMuted text-center py-3">' +
                _t('customEmpty') +
                '</p>';
            return;
        }
        var filtered = _filterSkill(customSkills, _searchQuery, true);
        var hasQuery = String(_searchQuery || '').trim().length > 0;
        var rowsHtml = filtered
            .map(function (s) {
                var checked = s.is_enabled !== false ? 'checked' : '';
                return (
                    '<div class="p-3 rounded-xl bg-background/50 border border-borderSubtle" data-custom-name="' +
                    _esc(s.name) +
                    '">' +
                    '  <div class="flex items-center gap-2 mb-1">' +
                    '    <span class="px-1.5 py-0.5 rounded text-[10px] font-medium" style="background:rgba(34,197,94,0.15);color:rgb(34,197,94);">' +
                    _t('custom') +
                    '</span>' +
                    '    <span class="text-sm text-secondary font-medium flex-1 min-w-0 truncate">' +
                    _esc(s.name) +
                    '</span>' +
                    '    <button class="custom-history-btn p-1 rounded-lg text-textMuted hover:text-blue-400 transition" title="History" data-i18n="common.history" data-i18n-attr="title"><i data-lucide="history" class="w-3 h-3"></i></button>' +
                    '    <button class="custom-edit-btn p-1 rounded-lg text-textMuted hover:text-primary transition" title="Edit" data-i18n="common.edit" data-i18n-attr="title"><i data-lucide="pencil" class="w-3 h-3"></i></button>' +
                    '    <button class="custom-delete-btn p-1 rounded-lg text-textMuted hover:text-red-400 transition" title="Delete" data-i18n="common.delete" data-i18n-attr="title"><i data-lucide="trash-2" class="w-3 h-3"></i></button>' +
                    '    <label class="relative inline-flex items-center cursor-pointer">' +
                    '      <input type="checkbox" class="sr-only peer custom-toggle" ' +
                    checked +
                    '>' +
                    '      <div class="w-9 h-5 bg-surfaceHighlight rounded-full peer peer-checked:bg-primary transition"></div>' +
                    '      <div class="absolute left-0.5 top-0.5 w-4 h-4 bg-textMuted rounded-full peer-checked:translate-x-4 peer-checked:bg-white transition"></div>' +
                    '    </label>' +
                    '  </div>' +
                    '  <p class="text-xs text-textMuted line-clamp-2">' +
                    _esc(s.description || s.body.slice(0, 100)) +
                    '</p>' +
                    '</div>'
                );
            })
            .join('');

        var noResultHtml =
            hasQuery && filtered.length === 0
                ? '<p class="text-xs text-textMuted text-center py-3">' + _t('noSearchResult') + '</p>'
                : '';

        listEl.innerHTML = rowsHtml + noResultHtml;
        if (window.lucide) window.lucide.createIcons({ nodes: [listEl] });
        bindCustomEvents();
    }

    function bindToggleEvents() {
        document.querySelectorAll('.skill-toggle').forEach(function (cb) {
            cb.addEventListener('change', async function () {
                var item = this.closest('[data-skill-name]');
                var name = item.getAttribute('data-skill-name');
                try {
                    var resp = await fetch(
                        '/api/skills/' + encodeURIComponent(name) + '/toggle',
                        {
                            method: 'POST',
                            credentials: 'include',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ is_enabled: this.checked }),
                        }
                    );
                    if (!resp.ok) throw new Error('HTTP ' + resp.status);
                } catch (e) {
                    this.checked = !this.checked; // revert
                    if (window.showToast) window.showToast(_t('saveFailed'), 'error');
                }
            });
        });
    }

    function bindCustomEvents() {
        document.querySelectorAll('.custom-toggle').forEach(function (cb) {
            cb.addEventListener('change', async function () {
                var item = this.closest('[data-custom-name]');
                var name = item.getAttribute('data-custom-name');
                try {
                    var resp = await fetch(
                        '/api/skills/custom/' + encodeURIComponent(name),
                        {
                            method: 'PATCH',
                            credentials: 'include',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ is_enabled: this.checked }),
                        }
                    );
                    if (!resp.ok) throw new Error('HTTP ' + resp.status);
                } catch (e) {
                    this.checked = !this.checked;
                    if (window.showToast) window.showToast(_t('saveFailed'), 'error');
                }
            });
        });

        document.querySelectorAll('.custom-edit-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var name = this.closest('[data-custom-name]').getAttribute('data-custom-name');
                openEditor(name);
            });
        });

        // c031 版本歷史按鈕 → 彈窗顯示版本列表 + rollback
        document.querySelectorAll('.custom-history-btn').forEach(function (btn) {
            btn.addEventListener('click', async function () {
                var name = this.closest('[data-custom-name]').getAttribute('data-custom-name');
                try {
                    var resp = await fetch(
                        '/api/skills/custom/' + encodeURIComponent(name) + '/history',
                        { credentials: 'include' }
                    );
                    if (!resp.ok) throw new Error('HTTP ' + resp.status);
                    var data = await resp.json();
                    var history = data.history || [];
                    if (history.length === 0) {
                        if (typeof window.showToast === 'function') {
                            window.showToast(_t('noVersionHistory'), 'info');
                        }
                        return;
                    }
                    // 用 ContentModal 顯示版本列表
                    if (window.ContentModal) {
                        var historyHtml = history
                            .map(function (rev, idx) {
                                var date = new Date(rev.created_at).toLocaleString();
                                var isLatest = idx === 0;
                                return (
                                    '<div class="flex items-center justify-between gap-2 p-2.5 rounded-lg border ' +
                                    (isLatest ? 'border-green-500/30 bg-green-500/5' : 'border-borderSubtle') +
                                    '" data-revision="' + rev.revision + '">' +
                                    '  <div class="flex-1 min-w-0">' +
                                    '    <div class="text-xs text-secondary">v' + rev.revision +
                                    (isLatest ? ' <span class="text-green-500 font-medium">(' + _t('currentTag') + ')</span>' : '') +
                                    '    <span class="text-textMuted ml-2">' + date + '</span></div>' +
                                    '    <div class="text-[11px] text-textMuted truncate">' +
                                    (rev.description || '').slice(0, 60) + '</div>' +
                                    '  </div>' +
                                    (!isLatest
                                        ? '<button class="rollback-btn px-2.5 py-1 rounded-lg text-xs bg-amber-500/15 text-amber-600 hover:bg-amber-500/25 transition" data-revision="' + rev.revision + '">' + _t('rollback') + '</button>'
                                        : '') +
                                    '</div>'
                                );
                            })
                            .join('');
                        window.ContentModal.open({
                            title: name,
                            subtitle: _t('versionHistory'),
                            body: historyHtml,
                            editable: false,
                        });
                        // 綁 rollback 按鈕
                        setTimeout(function () {
                            document.querySelectorAll('.rollback-btn').forEach(function (rb) {
                                rb.addEventListener('click', async function () {
                                    var targetRev = parseInt(this.getAttribute('data-revision'), 10);
                                    try {
                                        var rr = await fetch(
                                            '/api/skills/custom/' + encodeURIComponent(name) + '/rollback',
                                            {
                                                method: 'POST',
                                                credentials: 'include',
                                                headers: { 'Content-Type': 'application/json' },
                                                body: JSON.stringify({ target_revision: targetRev }),
                                            }
                                        );
                                        if (!rr.ok) throw new Error('HTTP ' + rr.status);
                                        if (typeof window.showToast === 'function') {
                                            window.showToast(_t('rolledBackTo', { v: targetRev }), 'success');
                                        }
                                        if (window.ContentModal) window.ContentModal.close();
                                        load();
                                    } catch (e) {
                                        console.error('[SkillManager] rollback failed:', e);
                                        if (typeof window.showToast === 'function') {
                                            window.showToast(_t('rollbackFailed'), 'error');
                                        }
                                    }
                                });
                            });
                        }, 300);
                    }
                } catch (e) {
                    console.error('[SkillManager] history load failed:', e);
                }
            });
        });

        // 點 skill 描述 → 開彈窗完整檢視 body（長內容用）
        document.querySelectorAll('[data-custom-name] > p').forEach(function (el) {
            el.style.cursor = 'pointer';
            el.addEventListener('click', function () {
                var name = this.closest('[data-custom-name]').getAttribute('data-custom-name');
                var s = customSkills.find(function (sk) { return sk.name === name; });
                if (!s || !window.ContentModal) return;
                window.ContentModal.open({
                    title: s.name,
                    subtitle: _t('custom') + ' Skill',
                    badge: { label: s.description ? s.description.slice(0, 40) : '', color: 'rgb(34,197,94)' },
                    body: s.body || s.description || '',
                    editable: true,
                    maxLength: 3000,
                    onSave: async function (newBody) {
                        // 用既有 API 更新 body（保留原 desc/trigger）
                        try {
                            var resp = await fetch('/api/skills/custom/' + encodeURIComponent(name), {
                                method: 'PATCH',
                                credentials: 'include',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ description: s.description || '', trigger_keywords: s.trigger_keywords || '', body: newBody }),
                            });
                            if (!resp.ok) throw new Error('HTTP ' + resp.status);
                            s.body = newBody;
                            load();
                            return true;
                        } catch (e) {
                            if (window.showToast) window.showToast(_t('saveFailed'), 'error');
                            return false;
                        }
                    },
                });
            });
        });

        document.querySelectorAll('.custom-delete-btn').forEach(function (btn) {
            btn.addEventListener('click', async function () {
                var item = this.closest('[data-custom-name]');
                var name = item.getAttribute('data-custom-name');
                // 自訂確認框（非瀏覽器 confirm()）
                var doDelete = function () {
                    fetch('/api/skills/custom/' + encodeURIComponent(name), {
                        method: 'DELETE',
                        credentials: 'include',
                    })
                        .then(function (resp) {
                            if (!resp.ok) throw new Error('HTTP ' + resp.status);
                            item.remove();
                            customSkills = customSkills.filter(function (s) {
                                return s.name !== name;
                            });
                        })
                        .catch(function () {
                            if (window.showToast) window.showToast(_t('deleteFailed'), 'error');
                        });
                };
                if (typeof window.showConfirm === 'function') {
                    window
                        .showConfirm({
                            title: _t('confirmDelete'),
                            message: _t('confirmDeleteDesc') || _t('confirmDelete'),
                            type: 'danger',
                            confirmText: window.I18n
                                ? window.I18n.t('common.delete')
                                : 'Delete',
                        })
                        .then(function (ok) {
                            if (ok) doDelete();
                        });
                } else {
                    doDelete();
                }
            });
        });
    }

    function openEditor(existingName) {
        var existing = existingName
            ? customSkills.find(function (s) {
                  return s.name === existingName;
              })
            : null;
        var editor = document.getElementById('skill-editor');
        if (!editor) return;
        editor.classList.remove('hidden');
        document.getElementById('skill-edit-name').value = existing ? existing.name : '';
        document.getElementById('skill-edit-name').disabled = !!existing; // 編輯時不可改名
        document.getElementById('skill-edit-desc').value = existing ? existing.description : '';
        document.getElementById('skill-edit-trigger').value = existing
            ? existing.trigger_keywords
            : '';
        document.getElementById('skill-edit-body').value = existing ? existing.body : '';
        editor.dataset.mode = existing ? 'edit' : 'create';
        editor.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    async function saveCustomSkill() {
        var editor = document.getElementById('skill-editor');
        if (!editor) return;
        var name = document.getElementById('skill-edit-name').value.trim();
        var desc = document.getElementById('skill-edit-desc').value.trim();
        var trig = document.getElementById('skill-edit-trigger').value.trim();
        var body = document.getElementById('skill-edit-body').value.trim();
        if (!name || !body) {
            if (window.showToast) window.showToast(_t('requiredFields'), 'error');
            return;
        }
        if (body.length > 3000) {
            if (window.showToast) window.showToast(_t('bodyTooLong'), 'error');
            return;
        }
        var isEdit = editor.dataset.mode === 'edit';
        var url = isEdit
            ? '/api/skills/custom/' + encodeURIComponent(name)
            : '/api/skills/custom';
        var method = isEdit ? 'PATCH' : 'POST';
        var payload = isEdit
            ? { description: desc, trigger_keywords: trig, body: body }
            : { skill_name: name, description: desc, trigger_keywords: trig, body: body };
        try {
            var resp = await fetch(url, {
                method: method,
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!resp.ok) throw await _responseError(resp);
            editor.classList.add('hidden');
            load(); // 重新載入
        } catch (e) {
            if (window.showToast) window.showToast(_errorText(e), 'error');
        }
    }

    // 失敗回應 → 帶 status 的 Error。以前 res.json() 在 HTML 502 會炸成 SyntaxError、422 的 detail 陣列變
    // '[object Object]'。error-message.js 由 main.js 載入並註冊到 window.ErrorMessage（classic script 不能 import）。
    async function _responseError(resp) {
        if (window.ErrorMessage) return window.ErrorMessage.errorFromResponse(resp);
        var err = new Error('HTTP ' + resp.status);
        err.status = resp.status;
        return err;
    }

    // 錯誤 → 給人看的訊息：後端的中文 detail 照顯示，'Failed to fetch'／英文 detail 換成在地化文案
    function _errorText(e) {
        return window.ErrorMessage
            ? window.ErrorMessage.userFacingMessage(e, { fallbackKey: 'settings.skills.saveFailed' })
            : _t('saveFailed');
    }

    function _t(key, params) {
        return window.I18n ? window.I18n.t('settings.skills.' + key, params) : key;
    }

    // 官方 skill 名稱/描述的 i18n（keys 由 scripts/gen_catalog_i18n.py 產生；
    // 缺譯 fallback API 原值——自訂 skill 為使用者自己的文字，一律不翻）
    function _officialField(s, field) {
        if (window.I18n && s && s.name) {
            var key = 'skills.' + s.name + '.' + field;
            var translated = window.I18n.t(key);
            if (translated && translated !== key) return translated;
        }
        return s ? (s[field] || '') : '';
    }

    function _esc(s) {
        if (typeof window.escapeHtml === 'function') return window.escapeHtml(s);
        return String(s == null ? '' : s).replace(/</g, '&lt;');
    }

    function init() {
        var refreshBtn = document.getElementById('btn-refresh-skills');
        if (refreshBtn) refreshBtn.addEventListener('click', load);
        var addBtn = document.getElementById('btn-add-skill');
        if (addBtn) addBtn.addEventListener('click', function () { openEditor(null); });
        var saveBtn = document.getElementById('btn-save-skill');
        if (saveBtn) saveBtn.addEventListener('click', saveCustomSkill);
        var cancelBtn = document.getElementById('btn-cancel-skill');
        if (cancelBtn)
            cancelBtn.addEventListener('click', function () {
                document.getElementById('skill-editor').classList.add('hidden');
            });
        load();
    }

    return { init: init, load: load, _filterSkill: _filterSkill };
})();
