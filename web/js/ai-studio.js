/**
 * AIStudioTab — Agents / Presets 管理中心（Phase 1 骨架 + Phase 2 preset UI）
 *
 * 規格：design.md §7（AI Studio 資訊架構）、§8（UI 元件規格）
 * - 總覽：官方 Agent 數 / Preset 數 / 行動政策 / catalog 版本
 * - Agents：官方 Profile catalog 卡片（唯讀；tier、capability、資料來源標示）
 * - Presets：列出/建立/啟用/刪除（Premium；server 端驗證 agent_ids）
 * - Memory/Skill/Tool 完整管理器仍在 Settings（Phase 1 相容期）
 */

var AIStudioTab = {
    _initialized: false,
    _profiles: [],
    _presets: [],
    // 編輯中的 preset（null = 表單是「建立」模式）。所有出口都要清——
    // 殘留會讓下一次「建立」變成覆寫某個舊 Preset
    _editingPresetId: null,
    _quota: { max: 10, used: 0 },
    // Model Mixer Step 1：per-agent 模型指定（agent_id → model_selection）
    _configs: {},
    _modelConfig: null,

    init() {
        if (this._initialized) return;
        this._initialized = true;
        // 語言切換要重渲染（2026-09-08 DANNY 回報：#ai-studio 切英文後仍是中文）。
        // 靜態 data-i18n 節點由 i18n.js 的 updatePageContent 換掉，但本檔 49 處
        // `_t()` 是 JS 拼字串塞 innerHTML 的——只有重跑 render 才會換語言。
        // spa.js 的 languageChanged 本來就會「切走再切回」重跑 init 來達成這件事，
        // 但上面那道 _initialized 守衛讓它直接 return，動態字串因此停在初次語言。
        // 自行監聽（並登記進 spa.js 的 SELF_HANDLED）比拿掉守衛好：不必重打 API，
        // 也不會重跑子管理器的 init。此處只註冊一次，不會洩漏監聽器。
        window.addEventListener('languageChanged', () => this._rerenderForLanguage());
        // 初始分頁高亮（template 預設全部非作用中）
        this.showSection('overview');
        // Memory／Skill 管理器與 Tool 設定（自 Settings 搬入；綁定 ID 不變，
        // 邏輯完全不動——僅改由 AI Studio 進入時初始化）
        if (window.MemoryManager && typeof window.MemoryManager.init === 'function') {
            Promise.resolve(window.MemoryManager.init()).catch((e) => console.warn('MemoryManager init failed:', e));
        }
        if (window.SkillManager && typeof window.SkillManager.init === 'function') {
            Promise.resolve(window.SkillManager.init()).catch((e) => console.warn('SkillManager init failed:', e));
        }
        if (typeof window.initToolSettings === 'function') {
            Promise.resolve(window.initToolSettings()).catch((e) => console.warn('ToolSettings init failed:', e));
        }
        // 模型分頁（2026-09-04 自 Settings 搬入）：填 provider/model 下拉並
        // 渲染已綁定清單。函式本身對缺元素有守衛，順序不敏感。
        // 先載已綁定金鑰：per-agent 模型下拉只列已綁定的 provider，
        // 沒載到的話第一次渲染會是空下拉。
        if (typeof window.loadSavedApiKeys === 'function') {
            Promise.resolve(window.loadSavedApiKeys())
                .then(() => this.renderProfiles())
                .catch((e) => console.warn('loadSavedApiKeys failed:', e));
        }
        if (typeof window.updateAvailableModels === 'function') {
            Promise.resolve(window.updateAvailableModels()).catch((e) => console.warn('updateAvailableModels failed:', e));
        }
        if (typeof window.renderBoundModels === 'function') {
            Promise.resolve(window.renderBoundModels()).catch((e) => console.warn('renderBoundModels failed:', e));
        }
        this.refresh();
    },

    _t(key, fallback) {
        if (window.I18n && typeof window.I18n.t === 'function') return window.I18n.t(key, fallback);
        return fallback;
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

    async refresh() {
        // loadConfigs 依賴 loadPresets 的結果（作用域＝作用中的 preset），
        // 不能跟它並行——並行的話第一次進頁面會讀不到設定。
        await Promise.all([this.loadProfiles(), this.loadPresets(), this.ensureModelConfig()]);
        await this.loadConfigs();
        this.renderOverview();
        this.renderProfiles();
        this.renderPresets();
    },

    /**
     * 語言切換後以快取資料重畫（不重打 API——語言換了但資料沒換）。
     * 分頁還沒開過就沒東西可重畫，開的時候 refresh() 自然會用新語言渲染。
     */
    _rerenderForLanguage() {
        if (!this._initialized) return;
        this.renderOverview();
        this.renderProfiles();
        this.renderPresets();
    },

    async loadProfiles() {
        try {
            var data = await AppAPI.get('/api/agent-profiles');
            this._profiles = data.profiles || [];
            this._configVersion = data.config_version || '';
            // Mixer Step 4：skill 勾選 UI 的資料源（官方＋自訂的輕量清單）
            this._skillsCatalog = Array.isArray(data.skills_catalog) ? data.skills_catalog : [];
        } catch (err) {
            this._profiles = [];
            this._skillsCatalog = [];
            if (err && err.status === 404) {
                this._flagOff = true;
            }
        }
    },

    async loadPresets() {
        if (this._templates === undefined) await this.loadTemplates();
        this._presetsLoadFailed = false;
        try {
            var data = await AppAPI.get('/api/agent-presets');
            this._presets = data.presets || [];
            this._quota = data.quota || { max: 10, used: this._presets.length };
            this._officialDefault = data.official_default || null;
        } catch (err) {
            this._presets = [];
            // 404 = AGENT_PRESETS_ENABLED 關著。不標記的話下面會顯示
            // 「尚無自訂 Preset」——那是謊報：不是使用者沒建，是功能沒開。
            if (err && err.status === 404) {
                this._flagOff = true;
            } else {
                // 401／500／斷線都會走到這裡。以前一律吞成空陣列，前端就再也
                // 分不出「使用者還沒建 Preset」與「session 過期讀不到」——
                // 錯誤訊息只好兩個原因一起講，結果對真正沒建 Preset 的人說
                // 「登入已失效，請重新登入」，他去重登也沒用（2026-09-08 回報）。
                this._presetsLoadFailed = true;
            }
        }
    },

    // 沒有作用中 preset 時的原因。兩種情況要對使用者說完全不同的話：
    //   'load-failed' → 讀不到（多半 session 過期）：重新整理／重新登入
    //   'no-preset'   → 真的還沒建：去 Presets 分頁建一個並設為預設
    // 回 null 代表有作用中的 preset，可以正常存檔。
    _noScopeReason() {
        if (this._scopePresetId()) return null;
        return this._presetsLoadFailed ? 'load-failed' : 'no-preset';
    },

    _noScopeMessage() {
        return this._noScopeReason() === 'load-failed'
            ? this._t('aiStudio.sessionLoadFailedRetry', 'Could not load your Presets — refresh and sign in again if needed')
            : this._t('aiStudio.agentsNoScope', 'No active Preset — create one in the Presets tab and set it as default first');
    },

    // ── Model Mixer Step 1：per-agent 模型指定 ────────────────────────────

    // c044：per-agent 設定的作用域是 preset。Agents 分頁編的是「目前作用中的
    // preset」——沒有使用者層可退。沒有 preset 時不給編，而不是靜默寫到某個
    // 看不見的全域範圍（那正是 c044 要根治的東西）。
    _scopePresetId() {
        var active = (this._presets || []).find((p) => p.is_default);
        return active ? active.preset_id : null;
    },

    _configsBase() {
        var pid = this._scopePresetId();
        return pid ? '/api/agent-presets/' + encodeURIComponent(pid) + '/agent-configs' : null;
    },

    async loadConfigs() {
        try {
            var base = this._configsBase();
            if (!base) { this._configs = {}; return; }
            var data = await AppAPI.get(base);
            this._configs = {};
            (data.configs || []).forEach((c) => {
                this._configs[c.agent_id] = {
                    model_selection: c.model_selection || null,
                    tools: Array.isArray(c.tools) ? c.tools : [],
                    skills: Array.isArray(c.skills) ? c.skills : [],
                    system_prompt: c.system_prompt || null,
                };
            });
        } catch (err) {
            this._configs = {};
        }
    },

    async ensureModelConfig() {
        // 與主站設定頁共用同一份快取（app.js updateAvailableModels）
        if (window.__modelConfigCache) {
            this._modelConfig = window.__modelConfigCache;
            return;
        }
        try {
            // 跟其他地方共用 AppAPI.getModelConfig 的請求與快取
            var data =
                typeof AppAPI.getModelConfig === 'function'
                    ? { model_config: await AppAPI.getModelConfig() }
                    : await AppAPI.get('/api/model-config');
            this._modelConfig = data.model_config || null;
            if (this._modelConfig) window.__modelConfigCache = this._modelConfig;
        } catch (err) {
            this._modelConfig = null;
        }
    },

    async onAgentModelChange(sel) {
        var opt = sel.selectedOptions && sel.selectedOptions[0];
        var custom = opt && opt.hasAttribute('data-custom-model');
        var box = sel.closest('[data-agent-model-control]');
        box.querySelector('[data-agent-model-custom]').hidden = !custom;
        if (custom) {
            var input = box.querySelector('[data-agent-model-input]');
            input.value = '';
            input.setCustomValidity('');
            input.focus();
            return; // Choosing a provider is not a model reset; wait for Save.
        }
        var provider = opt ? opt.getAttribute('data-provider') : null;
        var model = opt ? opt.getAttribute('data-model') : null;
        await this._saveAgentModel(sel, provider && model ? { provider, model } : null);
    },

    async _saveAgentModel(sel, selection) {
        var agentId = sel.getAttribute('data-agent-model');
        var controls = sel.closest('[data-agent-model-control]').querySelectorAll('select, input, button');
        controls.forEach((control) => { control.disabled = true; });
        try {
            // Only update the model; never DELETE the shared tools/config row.
            var base = this._configsBase();
            if (!base) throw new Error('no active preset');
            await AppAPI.put(base + '/' + encodeURIComponent(agentId), {
                model_selection: selection,
            });
            this._configs[agentId] = { ...(this._configs[agentId] || {}), model_selection: selection };
            this._restoreAgentModelSelect(sel);
            // Router 卡的「目前生效」行同步（2026-09-09 設計 B）
            if (agentId === 'router') {
                var line = document.querySelector('[data-router-effective]');
                if (line) {
                    line.textContent = selection
                        ? this._t('aiStudio.routerModelSet', 'Assigned:') + ' ' + selection.provider + ' / ' + selection.model
                        : this._t('aiStudio.routerModelDefault', 'Not assigned — uses your current default model');
                }
            }
            if (typeof window.showToast === 'function') {
                window.showToast(selection
                    ? this._t('aiStudio.modelSaved', '已更新模型')
                    : this._t('aiStudio.modelResetDone', '已重設為預設'), 'success');
            }
        } catch (err) {
            this._restoreAgentModelSelect(sel);
            // "no active preset" 有兩個完全不同的成因，以前混在同一句話裡講，
            // 而且把「登入已失效」擺在前面——真正只是還沒建 Preset 的人會去
            // 重新登入，當然沒用（2026-09-08 回報）。改成依實際成因分開講。
            var raw = (err && err.message) || '';
            var msg = err && err.status === 403
                ? this._t('aiStudio.premiumRequired', '此為 Premium 功能')
                : (/no active preset/.test(raw) ? this._noScopeMessage() : raw);
            window.showInfoDialog({
                title: this._t('aiStudio.modelChangeFailed', '模型設定失敗'),
                message: msg,
                tone: 'error',
            });
        } finally {
            controls.forEach((control) => { control.disabled = false; });
        }
    },

    // 已綁定的 provider（/api/user/api-keys?kind=llm → llmState.savedKeys）。
    // 2026-09-04：下拉原本列出整份靜態目錄，包含使用者沒有金鑰的 provider——
    // 選了會靜默退回預設模型（原本只有下方一行小字提示）。跟 fan-out 不套用
    // per-agent 模型、跟幽靈旗標同一類「設定得下去但不生效」。
    // 已綁定的 provider。
    // ⚠️ 一定要看 has_key：GET /api/user/api-keys 會替「每一個」支援的 provider
    // 補一筆預設空紀錄（user_api_keys_repo.get_all_user_api_keys 的
    // "Ensure all supported providers have an entry"），所以 Object.keys(savedKeys)
    // ＝全部 provider，不是使用者綁過的那些。少了這道過濾，per-agent 模型下拉
    // 會把沒綁金鑰的 provider 全列出來，選了也跑不動（2026-09-07 DANNY 回報
    // 「有些都沒有綁定」）。settings 那邊的 getBoundProviders() 一直都有過濾，
    // 是這裡漏掉。
    _boundProviders() {
        var saved = (window.llmState && window.llmState.savedKeys) || {};
        return new Set(Object.keys(saved).filter((p) => saved[p] && saved[p].has_key));
    },

    // 某個 provider 底下「使用者自己綁的模型」清單（Settings→模型 的已綁定清單
    // 就是這份資料）。綁了金鑰但沒存過模型的舊資料回空陣列，由呼叫端決定退路。
    _boundModelsFor(providerKey) {
        var info = ((window.llmState && window.llmState.savedKeys) || {})[providerKey];
        if (!info) return [];
        var models = Array.isArray(info.models) ? info.models.slice() : [];
        if (info.model && models.indexOf(info.model) === -1) models.unshift(info.model);
        return models.filter(Boolean);
    },

    _agentModelSelectHtml(agentId) {
        var cfg = this._modelConfig || {};
        var bound = this._boundProviders();
        var groups = '';
        // 平台模型（keyless，每個人預設綁著）排第一組
        var providerKeys = Object.keys(cfg).sort(
            (a, b) => ((cfg[b] || {}).keyless ? 1 : 0) - ((cfg[a] || {}).keyless ? 1 : 0)
        );
        providerKeys.forEach((providerKey) => {
            if (!bound.has(providerKey)) return;   // 沒綁金鑰的不列
            var provider = cfg[providerKey] || {};
            // 只列使用者自己綁的模型（DANNY：「應該只能指派自己綁定的模型」）。
            // 綁了金鑰卻沒存過任何模型的舊資料，退回該 provider 的完整清單——
            // 金鑰是通的，這時候給空下拉等於把人鎖死。
            var boundModels = this._boundModelsFor(providerKey);
            var models = boundModels.length
                ? boundModels.map((value) => {
                    var known = (provider.available_models || []).find((m) => m.value === value);
                    return known || { value: value, name: value };
                })
                : provider.available_models || [];
            var options = models
                .map((m) => '<option value="' + this._escapeHTML(JSON.stringify([providerKey, m.value])) + '" data-model="' + this._escapeHTML(m.value) + '" data-provider="' + this._escapeHTML(providerKey) + '">' + this._escapeHTML(m.name || m.value) + '</option>')
                .join('');
            if (provider.free_input) {
                options += '<option value="' + this._escapeHTML(JSON.stringify([providerKey, null])) + '" data-provider="' + this._escapeHTML(providerKey) + '" data-custom-model>' +
                    this._escapeHTML(this._t('aiStudio.modelCustomOption', '自訂模型 ID…')) + '</option>';
            }
            if (!options) return;
            groups += '<optgroup label="' + this._escapeHTML(provider.display || providerKey) + '">' + options + '</optgroup>';
        });
        var label = this._t('aiStudio.modelLabel', '模型');
        var defaultOption = this._t('aiStudio.modelDefaultOption', '預設（官方）');
        var hint = groups
            ? this._t('aiStudio.modelBoundOnlyHint', '只列出你已綁定金鑰的模型')
            : this._t('aiStudio.modelNoBindingHint', '你還沒有綁定任何模型——到上方「模型」分頁綁定後，這裡才會出現可選項目');
        var noteHtml = '';
        // 後端只在「preset 恰好一個 Agent」時套用這裡的模型（analysis.py
        // _resolve_preset_model_override）；多 Agent 一律跑預設模型。Router 不受影響。
        var agentCount = agentId === 'router' ? 0 : this._activeAgentCount();
        if (agentCount >= 2) {
            var note = this._t('aiStudio.modelMultiAgentInactive', 'The active preset has {{count}} agents: multi-agent presets always use your default model. The model set here only applies to single-agent presets.');
            noteHtml =
                '<p class="mt-2 text-xs leading-snug text-amber-600 dark:text-amber-400">⚠ ' +
                this._escapeHTML(String(note).replace('{{count}}', String(agentCount))) +
                '</p>';
        }
        // 一個都沒綁時，下拉只剩「預設（官方）」。原本這句只掛在 title 上，
        // 手機沒有 hover ＝完全看不到，使用者只會覺得下拉是壞的。
        if (!groups) {
            noteHtml =
                '<p class="mt-2 text-xs leading-snug text-textMuted">' + this._escapeHTML(hint) + '</p>' +
                noteHtml;
        }
        // 沒有作用中的 preset ＝ 存不進去（_saveAgentModel 會拋 no active preset）。
        // 以前下拉照樣可選，使用者選完才吃到一個錯誤對話框——正是這個檔案自己
        // 的註解在罵的「設定得下去但不生效」。改成直接停用並把該做的事寫出來。
        var noScope = this._noScopeReason();
        var disabledAttr = noScope ? ' disabled' : '';
        if (noScope) {
            noteHtml =
                '<p class="mt-2 text-xs leading-snug text-amber-600 dark:text-amber-400">' +
                this._escapeHTML(this._noScopeMessage()) + '</p>' +
                noteHtml;
        }
        return (
            '<div data-agent-model-control class="mt-3 pt-3 border-t border-borderSubtle/10"><div class="flex items-center gap-2">' +
            '<span class="text-xs text-textMuted shrink-0">' + this._escapeHTML(label) + '</span>' +
            '<select data-agent-model="' + this._escapeHTML(agentId) + '" data-prev-value="" title="' +
            this._escapeHTML(noScope ? this._noScopeMessage() : hint) + '"' + disabledAttr + ' ' +
            'class="flex-1 min-w-0 text-xs rounded-lg bg-surfaceHighlight px-2 py-1.5 text-secondary disabled:opacity-50 disabled:cursor-not-allowed">' +
            '<option value="">' + this._escapeHTML(defaultOption) + '</option>' + groups + '</select></div>' +
            noteHtml +
            '<div data-agent-model-custom hidden><div class="flex items-center gap-2 mt-2">' +
            '<input data-agent-model-input type="text" maxlength="200" autocomplete="off" aria-label="' + this._escapeHTML(label) + '" ' +
            'class="flex-1 min-w-0 text-xs rounded-lg bg-surfaceHighlight px-2 py-1.5 text-secondary" />' +
            '<button type="button" data-agent-model-save class="text-xs px-2 py-1.5 rounded-lg bg-surfaceHighlight text-secondary">' +
            this._escapeHTML(this._t('common.save', '儲存')) + '</button></div></div></div>'
        );
    },

    _restoreAgentModelSelect(sel) {
        var stored = this._configs[sel.getAttribute('data-agent-model')];
        var current = stored ? stored.model_selection : null;
        var match = current && Array.from(sel.options).find((o) =>
            o.getAttribute('data-model') === current.model && o.getAttribute('data-provider') === current.provider
        );
        if (!match && current) {
            match = Array.from(sel.options).find((o) =>
                o.hasAttribute('data-custom-model') && o.getAttribute('data-provider') === current.provider
            );
        }
        sel.selectedIndex = match ? match.index : 0;
        var box = sel.closest('[data-agent-model-control]');
        var input = box.querySelector('[data-agent-model-input]');
        input.value = current ? current.model : '';
        input.setCustomValidity('');
        box.querySelector('[data-agent-model-custom]').hidden = !(match && match.hasAttribute('data-custom-model'));
        sel.setAttribute('data-prev-value', sel.value);
    },

    _bindAgentModelSelects(container) {
        if (!container) return;
        container.querySelectorAll('select[data-agent-model]').forEach((sel) => {
            this._restoreAgentModelSelect(sel);
            sel.addEventListener('change', () => this.onAgentModelChange(sel));
            var box = sel.closest('[data-agent-model-control]');
            var input = box.querySelector('[data-agent-model-input]');
            var save = box.querySelector('[data-agent-model-save]');
            input.addEventListener('input', () => input.setCustomValidity(''));
            save.addEventListener('click', async () => {
                var opt = sel.selectedOptions[0];
                if (!opt || !opt.hasAttribute('data-custom-model')) return;
                var model = input.value.trim();
                if (!model) {
                    input.setCustomValidity(this._t('aiStudio.modelIdRequired', '請輸入模型 ID'));
                    input.reportValidity();
                    return;
                }
                await this._saveAgentModel(sel, { provider: opt.getAttribute('data-provider'), model });
            });
            input.addEventListener('keydown', (event) => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    save.click();
                }
            });
        });
    },

    // ── Model Mixer Step 2：per-agent 工具勾選 ────────────────────────────

    _toolDisplayName(tool) {
        if (window.I18n && typeof window.I18n.t === 'function') {
            return window.I18n.t('tools.' + tool.tool_id + '.name', tool.display_name || tool.tool_id);
        }
        return tool.display_name || tool.tool_id;
    },

    // 初始勾選狀態：未設定（tools=[]）= 全部勾（官方預設）；有白名單 =
    // 白名單 ∪ 必要工具（必要工具恆勾、disabled）
    _initialToolSelection(profile) {
        var candidates = profile.candidate_tools || [];
        var cfg = this._configs[profile.id] || { tools: [] };
        var stored = Array.isArray(cfg.tools) ? cfg.tools : [];
        if (!stored.length) return new Set(candidates.map((t) => t.tool_id));
        var valid = new Set(candidates.map((t) => t.tool_id));
        var selected = new Set(candidates.filter((t) => t.required).map((t) => t.tool_id));
        stored.forEach((id) => { if (valid.has(id)) selected.add(id); });
        return selected;
    },

    _agentToolsHtml(profile) {
        var candidates = profile.candidate_tools || [];
        if (!candidates.length) return '';
        var selected = this._initialToolSelection(profile);
        var groups = {};
        candidates.forEach((t) => {
            var cat = t.category || 'other';
            (groups[cat] = groups[cat] || []).push(t);
        });
        var badgeReq = this._t('aiStudio.toolsRequiredBadge', '必要');
        var badgeLock = this._t('aiStudio.toolsLockedBadge', '需 Premium');
        var mcpLabel = this._t('aiStudio.toolsMcpGroup', 'MCP 工具');
        var body = Object.keys(groups).sort().map((cat) => {
            var catLabel = cat === 'mcp' ? mcpLabel : cat;
            var rows = groups[cat]
                .map((t) => {
                    var checked = selected.has(t.tool_id);
                    var disabled = t.required || t.locked ? ' disabled' : '';
                    var badges = '';
                    if (t.required) {
                        badges += '<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-primary/15 text-primary">' + this._escapeHTML(badgeReq) + '</span>';
                    }
                    if (t.locked) {
                        badges += '<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-surfaceHighlight text-textMuted">' + this._escapeHTML(badgeLock) + '</span>';
                    }
                    return (
                        '<label class="flex items-center gap-2 py-1 cursor-pointer">' +
                        '<input type="checkbox" data-agent-tool="' + this._escapeHTML(t.tool_id) + '"' +
                        (checked ? ' checked' : '') + disabled + ' class="accent-primary" />' +
                        '<span class="text-xs text-secondary truncate">' + this._escapeHTML(this._toolDisplayName(t)) + '</span>' +
                        badges +
                        '</label>'
                    );
                })
                .join('');
            return (
                '<div class="mb-2">' +
                '<div class="text-[11px] uppercase tracking-wide text-textMuted mb-1">' + this._escapeHTML(catLabel) + '</div>' +
                '<div class="grid grid-cols-1 sm:grid-cols-2 gap-x-3">' + rows + '</div>' +
                '</div>'
            );
        }).join('');
        var cfg = this._configs[profile.id] || { tools: [] };
        var stored = Array.isArray(cfg.tools) ? cfg.tools : [];
        var status = stored.length
            ? selected.size + ' / ' + candidates.length
            : this._t('aiStudio.toolsDefaultHint', '全部可用（官方預設）');
        return (
            '<details class="mt-3 pt-3 border-t border-borderSubtle/10" data-tools-panel="' + this._escapeHTML(profile.id) + '">' +
            '<summary class="text-xs text-textMuted cursor-pointer select-none flex items-center gap-2">' +
            this._t('aiStudio.toolsLabel', '工具') +
            '<span data-tools-count class="px-1.5 py-0.5 rounded-full bg-surfaceHighlight">' + this._escapeHTML(status) + '</span>' +
            '</summary>' +
            '<div class="mt-3 max-h-72 overflow-y-auto pr-1">' + body + '</div>' +
            '<div class="flex gap-2 mt-3">' +
            '<button data-click="AIStudioTab.saveAgentTools" data-click-arg="' + encodeURIComponent(profile.id) + '" class="px-3 py-1.5 rounded-lg bg-primary text-background text-xs">' + this._t('aiStudio.toolsSave', '儲存工具設定') + '</button>' +
            '<button data-click="AIStudioTab.resetAgentTools" data-click-arg="' + encodeURIComponent(profile.id) + '" class="px-3 py-1.5 rounded-lg bg-surfaceHighlight text-xs">' + this._t('aiStudio.toolsReset', '重設為預設') + '</button>' +
            '</div>' +
            '</details>'
        );
    },

    _collectAgentTools(panel) {
        var ids = [];
        panel.querySelectorAll('input[data-agent-tool]:checked').forEach((box) => {
            ids.push(box.getAttribute('data-agent-tool'));
        });
        return ids.sort();
    },

    async _putAgentTools(agentId, toolsPayload) {
        var panel = document.querySelector('[data-tools-panel="' + agentId + '"]');
        if (!panel || panel.dataset.saving) return;
        panel.dataset.saving = 'true';
        var controls = Array.from(panel.querySelectorAll('input, button'));
        var disabledBefore = controls.map((control) => control.disabled);
        controls.forEach((control) => { control.disabled = true; });
        try {
            var base = this._configsBase();
            if (!base) throw new Error('no active preset');
            var data = await AppAPI.put(base + '/' + encodeURIComponent(agentId), toolsPayload);
            // Only reconcile the tools field and this panel. Re-fetching and rebuilding
            // all cards discards unsaved tool/model drafts, including on other agents.
            this._configs[agentId] = Object.assign({}, this._configs[agentId], {
                tools: data.config.tools,
            });
            var profile = this._profiles.find((item) => item.id === agentId);
            if (profile && panel.isConnected) {
                var wrapper = document.createElement('div');
                wrapper.innerHTML = this._agentToolsHtml(profile);
                this._bindAgentToolPanels(wrapper);
                var updated = wrapper.firstElementChild;
                if (updated) {
                    updated.open = panel.open;
                    panel.replaceWith(updated);
                }
            }
            if (typeof window.showToast === 'function') {
                window.showToast(
                    toolsPayload.tools === null
                        ? this._t('aiStudio.toolsResetDone', '已重設為官方預設')
                        : this._t('aiStudio.toolsSaved', '工具設定已儲存'),
                    'success'
                );
            }
        } catch (err) {
            // 跟模型那條同樣處理：'no active preset' 是內部訊息，直接丟給使用者
            // 看的話是一句沒翻譯的英文，而且沒告訴他該做什麼。
            var rawTools = (err && err.message) || '';
            var msg = err && err.status === 403
                ? this._t('aiStudio.premiumRequired', '此為 Premium 功能')
                : (/no active preset/.test(rawTools) ? this._noScopeMessage() : rawTools);
            window.showInfoDialog({
                title: this._t('aiStudio.toolsSaveFailed', '工具設定失敗'),
                message: msg,
                tone: 'error',
            });
        } finally {
            delete panel.dataset.saving;
            controls.forEach((control, index) => { control.disabled = disabledBefore[index]; });
        }
    },

    saveAgentTools(agentId) {
        var panel = document.querySelector('[data-tools-panel="' + agentId + '"]');
        if (!panel) return;
        var ids = this._collectAgentTools(panel);
        if (!ids.length) {
            // 空清單送出去＝「未設定」＝全部工具可用，與「我全都不要」正好相反。
            // 目前必要工具是 checked+disabled 所以踩不到，但沒有 required_tools
            // 的 agent 一出現就會靜默反轉——在這裡擋掉，不靠上游巧合。
            if (typeof window.showToast === 'function') {
                window.showToast(this._t('aiStudio.toolsNeedOne', '至少要保留一個工具'), 'error');
            }
            return;
        }
        this._putAgentTools(agentId, { tools: ids });
    },

    resetAgentTools(agentId) {
        this._putAgentTools(agentId, { tools: null });
    },

    // ── Model Mixer Step 4：per-agent 分析技能（skill）勾選 ────────────────
    // 語意與工具完全同構：未設定（skills=[]）= 全部（官方預設）；有白名單 =
    // 只注入白名單內的方法。skill 沒有 required/locked——它是方法指導，
    // 不授予能力，沒有 tier 問題。

    _initialSkillSelection(profile) {
        var catalog = this._skillsCatalog || [];
        var cfg = this._configs[profile.id] || { skills: [] };
        var stored = Array.isArray(cfg.skills) ? cfg.skills : [];
        if (!stored.length) return new Set(catalog.map((s) => s.name));
        var valid = new Set(catalog.map((s) => s.name));
        var selected = new Set();
        stored.forEach((name) => { if (valid.has(name)) selected.add(name); });
        return selected;
    },

    _agentSkillsHtml(profile) {
        var catalog = this._skillsCatalog || [];
        if (!catalog.length) return '';
        var selected = this._initialSkillSelection(profile);
        var official = catalog.filter((s) => s.is_official);
        var custom = catalog.filter((s) => !s.is_official);
        var self = this;
        function rows(list) {
            return list
                .map((s) => (
                    '<label class="flex items-center gap-2 py-1 cursor-pointer">' +
                    '<input type="checkbox" data-agent-skill="' + self._escapeHTML(s.name) + '"' +
                    (selected.has(s.name) ? ' checked' : '') + ' class="accent-primary" />' +
                    '<span class="text-xs text-secondary truncate" title="' + self._escapeHTML(s.description || '') + '">' +
                    self._escapeHTML(s.name) + '</span>' +
                    '</label>'
                ))
                .join('');
        }
        var body = '';
        if (official.length) {
            body += '<div class="mb-2"><div class="text-[11px] uppercase tracking-wide text-textMuted mb-1">' +
                this._escapeHTML(this._t('aiStudio.skillsOfficialGroup', 'Official skills')) +
                '</div><div class="grid grid-cols-1 sm:grid-cols-2 gap-x-3">' + rows(official) + '</div></div>';
        }
        if (custom.length) {
            body += '<div class="mb-2"><div class="text-[11px] uppercase tracking-wide text-textMuted mb-1">' +
                this._escapeHTML(this._t('aiStudio.skillsCustomGroup', 'Custom skills')) +
                '</div><div class="grid grid-cols-1 sm:grid-cols-2 gap-x-3">' + rows(custom) + '</div></div>';
        }
        var cfg = this._configs[profile.id] || { skills: [] };
        var stored = Array.isArray(cfg.skills) ? cfg.skills : [];
        var status = stored.length
            ? selected.size + ' / ' + catalog.length
            : this._t('aiStudio.skillsDefaultHint', 'All available (official default)');
        return (
            '<details class="mt-2 pt-3 border-t border-borderSubtle/10" data-skills-panel="' + this._escapeHTML(profile.id) + '">' +
            '<summary class="text-xs text-textMuted cursor-pointer select-none flex items-center gap-2">' +
            this._t('aiStudio.skillsLabel', 'Analysis Skills') +
            '<span data-skills-count class="px-1.5 py-0.5 rounded-full bg-surfaceHighlight">' + this._escapeHTML(status) + '</span>' +
            '</summary>' +
            '<div class="mt-3 max-h-72 overflow-y-auto pr-1">' + body + '</div>' +
            '<div class="flex gap-2 mt-3">' +
            '<button data-click="AIStudioTab.saveAgentSkills" data-click-arg="' + encodeURIComponent(profile.id) + '" class="px-3 py-1.5 rounded-lg bg-primary text-background text-xs">' + this._t('aiStudio.skillsSave', 'Save skill settings') + '</button>' +
            '<button data-click="AIStudioTab.resetAgentSkills" data-click-arg="' + encodeURIComponent(profile.id) + '" class="px-3 py-1.5 rounded-lg bg-surfaceHighlight text-xs">' + this._t('aiStudio.skillsReset', 'Reset to default') + '</button>' +
            '</div>' +
            '</details>'
        );
    },

    _collectAgentSkills(panel) {
        var names = [];
        panel.querySelectorAll('input[data-agent-skill]:checked').forEach((box) => {
            names.push(box.getAttribute('data-agent-skill'));
        });
        return names.sort();
    },

    async _putAgentSkills(agentId, skillsPayload) {
        var panel = document.querySelector('[data-skills-panel="' + agentId + '"]');
        if (!panel || panel.dataset.saving) return;
        panel.dataset.saving = 'true';
        var controls = Array.from(panel.querySelectorAll('input, button'));
        var disabledBefore = controls.map((control) => control.disabled);
        controls.forEach((control) => { control.disabled = true; });
        try {
            var base = this._configsBase();
            if (!base) throw new Error('no active preset');
            var data = await AppAPI.put(base + '/' + encodeURIComponent(agentId), skillsPayload);
            // 與工具同一策略：只 reconcile skills 欄與本面板，不整頁重抓
            //（重抓會丟掉其他 agent 卡上未存檔的草稿）。
            this._configs[agentId] = Object.assign({}, this._configs[agentId], {
                skills: data.config.skills,
            });
            var profile = this._profiles.find((item) => item.id === agentId);
            if (profile && panel.isConnected) {
                var wrapper = document.createElement('div');
                wrapper.innerHTML = this._agentSkillsHtml(profile);
                this._bindAgentSkillPanels(wrapper);
                var updated = wrapper.firstElementChild;
                if (updated) {
                    updated.open = panel.open;
                    panel.replaceWith(updated);
                }
            }
            if (typeof window.showToast === 'function') {
                window.showToast(
                    skillsPayload.skills === null
                        ? this._t('aiStudio.skillsResetDone', 'Reset to official default')
                        : this._t('aiStudio.skillsSaved', 'Skill settings saved'),
                    'success'
                );
            }
        } catch (err) {
            var raw = (err && err.message) || '';
            var msg = err && err.status === 403
                ? this._t('aiStudio.premiumRequired', 'Premium only')
                : (/no active preset/.test(raw) ? this._noScopeMessage() : raw);
            window.showInfoDialog({
                title: this._t('aiStudio.skillsSaveFailed', 'Failed to save skill settings'),
                message: msg,
                tone: 'error',
            });
        } finally {
            delete panel.dataset.saving;
            controls.forEach((control, index) => { control.disabled = disabledBefore[index]; });
        }
    },

    saveAgentSkills(agentId) {
        var panel = document.querySelector('[data-skills-panel="' + agentId + '"]');
        if (!panel) return;
        var names = this._collectAgentSkills(panel);
        if (!names.length) {
            // 空清單送出去＝「未設定」＝全部技能可用，與「我全都不要」正好相反
            //（後端 skills=[] 語意是官方預設）——在這裡擋掉，與工具同一防護。
            if (typeof window.showToast === 'function') {
                window.showToast(this._t('aiStudio.skillsNeedOne', 'Keep at least one skill'), 'error');
            }
            return;
        }
        this._putAgentSkills(agentId, { skills: names });
    },

    resetAgentSkills(agentId) {
        this._putAgentSkills(agentId, { skills: null });
    },

    // 勾選當下就更新「n / m」計數——否則要存檔重渲染才會動，看起來像沒生效
    _bindAgentToolPanels(container) {
        if (!container) return;
        container.querySelectorAll('[data-tools-panel]').forEach((panel) => {
            var counter = panel.querySelector('[data-tools-count]');
            if (!counter) return;
            var total = panel.querySelectorAll('input[data-agent-tool]').length;
            panel.addEventListener('change', (e) => {
                if (!e.target || !e.target.hasAttribute || !e.target.hasAttribute('data-agent-tool')) return;
                counter.textContent = panel.querySelectorAll('input[data-agent-tool]:checked').length + ' / ' + total;
            });
        });
    },

    _bindAgentSkillPanels(container) {
        if (!container) return;
        container.querySelectorAll('[data-skills-panel]').forEach((panel) => {
            var counter = panel.querySelector('[data-skills-count]');
            if (!counter) return;
            var total = panel.querySelectorAll('input[data-agent-skill]').length;
            panel.addEventListener('change', (e) => {
                if (!e.target || !e.target.hasAttribute || !e.target.hasAttribute('data-agent-skill')) return;
                counter.textContent = panel.querySelectorAll('input[data-agent-skill]:checked').length + ' / ' + total;
            });
        });
    },

    renderOverview() {
        document.getElementById('ai-studio-count-profiles').textContent = this._flagOff ? '–' : String(this._profiles.length);
        document.getElementById('ai-studio-count-presets').textContent = String(this._quota.used || 0);
        var policy = 'read_only';
        var active = this._presets.find((p) => p.is_default);
        if (active) policy = active.action_policy;
        else if (this._officialDefault) policy = this._officialDefault.action_policy;
        // 統計磚只放得下短標籤。這裡本來用 aiStudio.confirmActions／readOnly，
        // 但那兩個 key 的實際文案是下拉選項的完整句子（「需要我確認（可用高
        // 風險工具）」），塞進 24px 的值列會撐成三行、把整排排版推歪
        //（2026-09-07 DANNY 回報）。完整說明改掛 title，滑過去還看得到。
        var policyEl = document.getElementById('ai-studio-current-policy');
        policyEl.textContent = policy === 'confirm_actions'
            ? this._t('aiStudio.policyShortConfirm', '可行動') : this._t('aiStudio.policyShortReadOnly', '唯讀');
        policyEl.title = this._policyLabel(policy);
        // Catalog 版號不顯示給使用者——那是內部資料版號，放在總覽磚裡只會讓人
        // 以為自己該看懂它（2026-09-08 DANNY）。仍寫進 data 屬性，客服要問
        // 「你的 catalog 是哪一版」時從 devtools 就查得到，不必再發一次 API。
        var overviewSection = document.getElementById('ai-studio-overview');
        if (overviewSection) {
            overviewSection.dataset.configVersion = this._configVersion || '';
        }
    },

    renderProfiles() {
        var el = document.getElementById('ai-studio-profiles');
        if (!el) return;
        if (this._flagOff) {
            el.innerHTML = '<p class="text-sm text-textMuted col-span-2">' + this._t('aiStudio.flagOff', 'Agent Presets 功能未開放') + '</p>';
            return;
        }
        // fan-out 生效中 → 頂部橫幅說明 per-agent 模型設定的 v1 限制
        // c044：per-agent 設定屬於某個 preset。作用域看不見的話，使用者會以為
        // 改的是全域——那就是 c044 之前的實際行為，也是這次要根治的。
        var scopeName = (this._presets || []).find((p) => p.is_default);
        var scopeBar =
            '<div class="col-span-2 rounded-xl border border-borderSubtle/10 bg-surfaceHighlight p-3 text-xs text-textMuted">'
            + (scopeName
                ? this._escapeHTML(
                    String(this._t('aiStudio.agentsScope', '以下設定屬於作用中的 Preset：{{name}}'))
                        .replace('{{name}}', scopeName.name || ''))
                : this._escapeHTML(this._noScopeMessage()))
            + '</div>';
        el.innerHTML = scopeBar + this._profiles
            .map((p) => {
                var caps = (p.capabilities || []).map((c) => '<span class="text-xs px-2 py-0.5 rounded-full bg-surfaceHighlight">' + this._escapeHTML(c) + '</span>').join(' ');
                return (
                    '<div class="rounded-xl border border-borderSubtle/10 bg-surface p-4">' +
                    '<div class="flex items-center justify-between mb-1">' +
                    '<h4 class="font-medium text-secondary">' + this._escapeHTML(p.display_name) + '</h4>' +
                    '<span class="text-xs px-2 py-0.5 rounded-full ' + (p.tier === 'premium' ? 'bg-primary/15 text-primary' : 'bg-surfaceHighlight') + '">' + this._escapeHTML(p.tier) + '</span>' +
                    '</div>' +
                    '<p class="text-sm text-textMuted mb-2">' + this._escapeHTML(p.description) + '</p>' +
                    '<div class="flex flex-wrap gap-1.5">' + caps + '</div>' +
                    this._agentModelSelectHtml(p.id) +
                    this._agentToolsHtml(p) +
                    this._agentSkillsHtml(p) +
                    this._agentPromptHtml(p.id, p.display_name) +
                    '</div>'
                );
            })
            .join('') + this._routerCardHtml();
        this._bindAgentModelSelects(el);
        this._bindAgentToolPanels(el);
        this._bindAgentSkillPanels(el);
        this._bindAgentPromptPanels(el);
    },

    // ── Per-agent system prompt（2026-09-09 設計）─────────────────────
    // 語意：append-only——墊在官方 prompt 與全域自訂之後，只能強化、無法
    // 覆蓋安全規則；null/清空＝重設為未設定。
    _agentPromptHtml(agentId, displayName) {
        var cfg = this._configs[agentId] || {};
        var saved = typeof cfg.system_prompt === 'string' ? cfg.system_prompt : '';
        return (
            '<details data-prompt-panel="' + this._escapeHTML(agentId) + '" data-prompt-display="' + this._escapeHTML(displayName) + '" class="mt-3 pt-3 border-t border-borderSubtle/10">' +
            '<summary class="cursor-pointer select-none text-xs text-textMuted">' +
            this._t('aiStudio.promptPanelTitle', 'Custom instructions (this Agent only)') +
            (saved ? ' <span class="text-primary">●</span>' : '') +
            '</summary>' +
            '<textarea data-prompt-input rows="4" maxlength="2000" placeholder="' +
            this._escapeHTML(this._t('aiStudio.promptPlaceholder', 'e.g. Focus on contract risk and fund safety; conclusion first')) +
            '" class="mt-2 w-full rounded-lg bg-surfaceHighlight px-3 py-2 text-xs text-secondary placeholder:text-textMuted/60">' +
            this._escapeHTML(saved) +
            '</textarea>' +
            '<p class="mt-1 text-[10px] leading-snug text-textMuted">' +
            this._escapeHTML(this._t('aiStudio.promptHint', "Appended after '") + this._escapeHTML(displayName) + this._t('aiStudio.promptHintSuffix', "' official instructions; cannot override safety rules. Save empty to reset.")) +
            '</p>' +
            '<div class="mt-2 flex gap-2">' +
            '<button type="button" data-prompt-save class="text-xs px-3 py-1.5 rounded-lg bg-surfaceHighlight text-secondary">' +
            this._escapeHTML(this._t('common.save', 'Save')) + '</button>' +
            '<button type="button" data-prompt-reset class="text-xs px-3 py-1.5 rounded-lg bg-surfaceHighlight text-textMuted">' +
            this._escapeHTML(this._t('aiStudio.promptReset', 'Reset')) + '</button>' +
            '</div></details>'
        );
    },

    _bindAgentPromptPanels(root) {
        (root || document).querySelectorAll('[data-prompt-panel]').forEach((panel) => {
            if (panel.dataset.bound) return;
            panel.dataset.bound = 'true';
            panel.querySelector('[data-prompt-save]').addEventListener('click', () => {
                var text = panel.querySelector('[data-prompt-input]').value;
                this._putAgentPrompt(
                    panel.getAttribute('data-prompt-panel'),
                    { system_prompt: text.trim() ? text : null }
                );
            });
            panel.querySelector('[data-prompt-reset]').addEventListener('click', () => {
                this._putAgentPrompt(
                    panel.getAttribute('data-prompt-panel'),
                    { system_prompt: null }
                );
            });
        });
    },

    async _putAgentPrompt(agentId, payload) {
        var panel = document.querySelector('[data-prompt-panel="' + agentId + '"]');
        if (!panel || panel.dataset.saving) return;
        panel.dataset.saving = 'true';
        var controls = Array.from(panel.querySelectorAll('textarea, button'));
        var disabledBefore = controls.map((c) => c.disabled);
        controls.forEach((c) => { c.disabled = true; });
        try {
            var base = this._configsBase();
            if (!base) throw new Error('no active preset');
            var data = await AppAPI.put(base + '/' + encodeURIComponent(agentId), payload);
            this._configs[agentId] = Object.assign({}, this._configs[agentId], {
                system_prompt: data.config.system_prompt || null,
            });
            // reconcile：重建面板以更新「已設定」圓點與文字（保留展開狀態）
            var wrapper = document.createElement('div');
            wrapper.innerHTML = this._agentPromptHtml(
                agentId,
                panel.getAttribute('data-prompt-display') || agentId
            );
            this._bindAgentPromptPanels(wrapper);
            var updated = wrapper.firstElementChild;
            if (updated) {
                updated.open = panel.open;
                panel.replaceWith(updated);
            }
            if (typeof window.showToast === 'function') {
                window.showToast(
                    payload.system_prompt === null
                        ? this._t('aiStudio.promptResetDone', 'Custom instructions reset')
                        : this._t('aiStudio.promptSaved', 'Custom instructions saved'),
                    'success'
                );
            }
        } catch (err) {
            var raw = (err && err.message) || '';
            var msg = err && err.status === 403
                ? this._t('aiStudio.premiumRequired', 'Premium only')
                : (/no active preset/.test(raw) ? this._noScopeMessage() : raw);
            window.showInfoDialog({
                title: this._t('aiStudio.promptSaveFailed', 'Failed to save custom instructions'),
                message: msg,
                tone: 'error',
            });
        } finally {
            delete panel.dataset.saving;
            controls.forEach((c, i) => { c.disabled = disabledBefore[i]; });
        }
    },

    // ── Router 模型可指派＋可視（2026-09-09 設計 B）──────────────────
    _routerCardHtml() {
        var stored = this._configs.router ? this._configs.router.model_selection : null;
        var effective = stored
            ? this._t('aiStudio.routerModelSet', 'Assigned:') + ' ' + stored.provider + ' / ' + stored.model
            : this._t('aiStudio.routerModelDefault', 'Not assigned — uses your current default model');
        return (
            '<div class="col-span-2 rounded-xl border border-borderSubtle/10 bg-surface p-4">' +
            '<div class="flex items-center justify-between mb-1">' +
            '<h4 class="font-medium text-secondary">' +
            this._t('aiStudio.routerCardTitle', 'Router (dispatcher)') +
            ' <span class="text-xs px-2 py-0.5 rounded-full bg-surfaceHighlight text-textMuted">' +
            this._t('aiStudio.routerCardBadge', 'not an analysis Agent') + '</span></h4>' +
            '</div>' +
            '<p class="text-sm text-textMuted mb-2">' +
            this._escapeHTML(this._t('aiStudio.routerCardDesc', 'One-shot routing decision before each question. Assign a dedicated model; unassigned follows your default model.')) +
            '</p>' +
            '<p class="text-xs text-primary mb-1" data-router-effective>' + this._escapeHTML(effective) + '</p>' +
            this._agentModelSelectHtml('router') +
            '<p class="mt-2 text-[10px] leading-snug text-textMuted">' +
            this._escapeHTML(this._t('aiStudio.routerMeasureNote', 'Measurement (#665): switching models has limited speed benefit for the Router; an advanced option.')) +
            '</p>' +
            '</div>'
        );
    },

    // preset mode / action_policy 的 enum → 各語言標籤（未知的 enum 值原樣顯示）
    _modeLabel(mode) {
        var labels = {
            single: this._t('aiStudio.modeSingle', '單一 Agent'),
            auto: this._t('aiStudio.modeAuto', '自動調度'),
            team: this._t('aiStudio.modeTeam', '團隊協作'),
        };
        return labels[mode] || mode;
    },

    _policyLabel(policy) {
        var labels = {
            read_only: this._t('aiStudio.readOnly', '唯讀'),
            confirm_actions: this._t('aiStudio.confirmActions', '可行動（需確認）'),
        };
        return labels[policy] || policy;
    },

    // 作用中 preset 的 Agent 數（決定 per-agent 模型會不會被套用）
    _activeAgentCount() {
        if (this._flagOff) return 0;
        var active = (this._presets || []).find((p) => p.is_default) || this._officialDefault;
        return ((active && active.agent_ids) || []).length;
    },

    _renderTemplatesRow() {
        var templates = this._templates || [];
        if (!templates.length) return '';
        var self = this;
        return (
            '<div class="rounded-xl border border-borderSubtle/10 bg-surface p-3 mb-3">' +
            '<div class="text-xs text-textMuted mb-2">' + this._t('aiStudio.templatesTitle', 'Team templates') + '</div>' +
            '<div class="flex flex-wrap gap-2">' +
            templates.map(function (t) {
                return '<button data-click="AIStudioTab.createFromTemplate" data-click-arg="' + encodeURIComponent(t.id) + '"' +
                    ' title="' + self._escapeHTML(t.description || '') + '"' +
                    ' class="min-h-11 px-3 py-2 rounded-lg bg-primary/10 hover:bg-primary/20 text-primary text-xs transition">' +
                    self._escapeHTML(t.name) + ' <span class="text-textMuted">· ' + self._escapeHTML((t.agent_ids || []).join(' + ')) + '</span></button>';
            }).join('') +
            '</div></div>'
        );
    },

    async loadTemplates() {
        try {
            var lang = (window.I18n && window.I18n.currentLanguage) || 'zh-TW';
            var data = await AppAPI.get('/api/agent-presets/templates?language=' + encodeURIComponent(lang));
            this._templates = (data && data.templates) || [];
        } catch (e) {
            this._templates = [];
        }
    },

    async createFromTemplate(templateId) {
        try {
            var lang = (window.I18n && window.I18n.currentLanguage) || 'zh-TW';
            await AppAPI.post('/api/agent-presets/from-template/' + encodeURIComponent(templateId) + '?language=' + encodeURIComponent(lang), {});
            if (window.Toast) Toast.success(this._t('aiStudio.templateCreated', 'Preset created from template'));
            await this.loadPresets();
        } catch (e) {
            console.error('[AIStudio] create from template failed:', e);
            if (window.Toast) Toast.error(this._t('aiStudio.templateFailed', 'Could not create the preset'));
        }
    },

    renderPresets() {
        var list = document.getElementById('ai-studio-preset-list');
        var quota = document.getElementById('ai-studio-preset-quota');
        if (!list) return;
        quota.textContent = this._flagOff ? '–' : (this._quota.used || 0) + ' / ' + (this._quota.max || 10);
        if (this._flagOff) {
            list.innerHTML = '<div class="rounded-xl border border-dashed border-borderSubtle/10 p-4 text-sm text-textMuted">'
                + this._t('aiStudio.flagOff', 'Agent Presets 功能未開放') + '</div>';
            return;
        }
        // 團隊模板（2026-09-12 派工）：一鍵建立多 agent preset，讓派工有可見入口
        var templatesHtml = this._renderTemplatesRow();
        if (!this._presets.length) {
            var official = this._officialDefault;
            list.innerHTML = templatesHtml +
                '<div class="rounded-xl border border-dashed border-borderSubtle/10 p-4 text-sm text-textMuted">' +
                this._t('aiStudio.noPresets', '尚無自訂 Preset。') +
                (official ? ' ' + this._t('aiStudio.usingOfficial', '目前使用官方預設') + ' · ' + this._escapeHTML(official.agent_ids.join(', ')) : '') +
                '</div>';
            return;
        }
        list.innerHTML = templatesHtml + this._presets
            .map((p) => {
                var agents = (p.agent_ids || []).map(this._escapeHTML).join(', ');
                return (
                    '<div class="rounded-xl border border-borderSubtle/10 bg-surface p-4 flex items-center justify-between gap-3">' +
                    '<div class="min-w-0">' +
                    '<div class="flex items-center gap-2">' +
                    '<h4 class="font-medium text-secondary truncate">' + this._escapeHTML(p.name) + '</h4>' +
                    (p.is_default ? '<span class="text-xs px-2 py-0.5 rounded-full bg-primary/15 text-primary">' + this._t('aiStudio.active', '使用中') + '</span>' : '') +
                    '</div>' +
                    '<p class="text-xs text-textMuted mt-1">' + this._escapeHTML(agents) +
                    ' · ' + this._escapeHTML(this._modeLabel(p.mode)) +
                    ' · ' + this._escapeHTML(this._policyLabel(p.action_policy)) + '</p>' +
                    '</div>' +
                    '<div class="flex gap-2 shrink-0">' +
                    (p.is_default ? '' : '<button data-click="AIStudioTab.activatePreset" data-click-arg="' + encodeURIComponent(p.preset_id) + '" class="px-3 py-1.5 rounded-lg bg-primary text-background text-xs">' + this._t('aiStudio.activate', '啟用') + '</button>') +
                    '<button data-click="AIStudioTab.editPreset" data-click-arg="' + encodeURIComponent(p.preset_id) + '" class="px-3 py-1.5 rounded-lg bg-surfaceHighlight text-xs">' + this._t('aiStudio.edit', 'Edit') + '</button>' +
                    '<button data-click="AIStudioTab.deletePreset" data-click-arg="' + encodeURIComponent(p.preset_id) + '" class="px-3 py-1.5 rounded-lg bg-surfaceHighlight text-xs">' + this._t('aiStudio.delete', '刪除') + '</button>' +
                    '</div></div>'
                );
            })
            .join('');
    },

    // 返回：回到進入 AI Studio 前的分頁（spa.js 記錄），沒有紀錄就回 chat
    goBack() {
        const returnTab = (window.AppStore && AppStore.get('aiStudioReturnTab')) || 'chat';
        if (typeof window.switchTab === 'function') window.switchTab(returnTab);
    },

    // 極簡主題切換（light↔dark）：與主站共用 selectedTheme，
    // 行為對齊 ThemeSwitcher.applyTheme（class + localStorage + themeChanged 事件）
    toggleTheme() {
        const root = document.documentElement;
        const toDark = !root.classList.contains('dark');
        root.classList.toggle('dark', toDark);
        try {
            localStorage.setItem('selectedTheme', toDark ? 'dark' : 'light');
        } catch (err) { /* localStorage 不可用時只切當前頁 */ }
        const meta = document.querySelector('meta[name="theme-color"]');
        if (meta) meta.setAttribute('content', toDark ? '#14161F' : '#FFFDF9');
        window.dispatchEvent(new CustomEvent('themeChanged', { detail: { theme: toDark ? 'dark' : 'light', effective: toDark ? 'dark' : 'light' } }));
    },

    showSection(section) {
        ['overview', 'agents', 'presets', 'models', 'skills', 'memory', 'tools'].forEach((name) => {
            document.getElementById('ai-studio-' + name)?.classList.toggle('hidden', name !== section);
        });
        // segmented control：作用中鈕 = surface 底浮起；非作用中 = muted 文字
        document.querySelectorAll('.ai-studio-nav').forEach((btn) => {
            var isActive = btn.getAttribute('data-click-arg') === section;
            btn.classList.toggle('bg-surface', isActive);
            btn.classList.toggle('text-secondary', isActive);
            btn.classList.toggle('shadow-sm', isActive);
            btn.classList.toggle('text-textMuted', !isActive);
        });
    },

    openCreatePreset() {
        var form = document.getElementById('ai-studio-preset-form');
        if (!form) return;
        var boxes = document.getElementById('ai-studio-preset-agent-checkboxes');
        boxes.innerHTML = this._profiles
            .map(
                (p) =>
                    '<label class="flex items-center gap-2 text-sm"><input type="checkbox" class="ai-studio-agent-cb" value="' +
                    this._escapeHTML(p.id) + '"> ' + this._escapeHTML(p.display_name) + '</label>'
            )
            .join('');
        boxes.onchange = () => {
            this._renderPresetCapabilities();
        };
        this._renderPresetCapabilities();
        // 進入點即清編輯態：從「編輯」回頭按「建立 Preset」不能帶著殘留的
        // _editingPresetId，否則建立會變成覆寫剛才編輯的對象
        this._editingPresetId = null;
        var title = document.getElementById('ai-studio-preset-form-title');
        if (title) {
            // data-i18n 一併換回來：語言切換重套 i18n 時標題才不會停在「編輯」
            title.setAttribute('data-i18n', 'aiStudio.newPresetTitle');
            title.textContent = this._t('aiStudio.newPresetTitle', 'New Preset');
        }
        form.classList.remove('hidden');
    },

    // 編輯既有 Preset：重用建立表單（checkbox 骨架、capability
    // 渲染都是同一套），預填現值後送 PATCH。後端 PATCH /api/agent-presets/{id}
    // 自 Mixer Step 起就存在，這裡只是第一次接上它。
    async editPreset(presetId) {
        var p = (this._presets || []).find((x) => x.preset_id === presetId);
        if (!p) return;
        this.openCreatePreset(); // 建乾淨骨架（會先清 _editingPresetId）
        this._editingPresetId = p.preset_id;
        document.getElementById('ai-studio-preset-name').value = p.name || '';
        document.querySelectorAll('.ai-studio-agent-cb').forEach((cb) => {
            cb.checked = (p.agent_ids || []).indexOf(cb.value) !== -1;
        });
        this._renderPresetCapabilities();
        // 被 capability_overrides 關掉的能力呈現為未勾選。後端 update 是整體
        // 取代（非合併），重新勾選＝移除 override，行為一致
        var overrides = p.capability_overrides || {};
        document.querySelectorAll('.ai-studio-cap-cb').forEach((cb) => {
            if (overrides[cb.value] === false) cb.checked = false;
        });
        document.getElementById('ai-studio-preset-mode').value = p.mode || 'single';
        document.getElementById('ai-studio-preset-policy').value = p.action_policy || 'read_only';
        var title = document.getElementById('ai-studio-preset-form-title');
        if (title) {
            title.setAttribute('data-i18n', 'aiStudio.editPresetTitle');
            title.textContent = this._t('aiStudio.editPresetTitle', 'Edit Preset');
        }
        // 編輯鈕在列表卡上，表單掛在列表下方——不捲進視野使用者會以為沒反應
        document.getElementById('ai-studio-preset-form')?.scrollIntoView({
            behavior: 'smooth',
            block: 'nearest',
        });
    },

    // Preset 層的工具範圍：capability_overrides。
    // 注意鍵是「能力」不是工具 ID——capability_resolver.py:171 會忽略不認識的鍵。
    // 而且**只能關不能開**（同檔 :166「開啟不得擴權」），所以 UI 是「預設全開、
    // 取消勾選＝這個 preset 關掉它」，不是自由勾選。
    // 這也是目前唯一 per-preset 的工具範圍手段：per-agent 的 tools 存在
    // user_agent_configs，主鍵是 (user_id, agent_id)，沒有 preset_id——
    // 兩個 preset 會共用同一份 per-agent 工具設定。
    _renderPresetCapabilities() {
        var host = document.getElementById('ai-studio-preset-capabilities');
        if (!host) return;
        var picked = new Set(
            Array.from(document.querySelectorAll('.ai-studio-agent-cb:checked')).map((cb) => cb.value)
        );
        var caps = new Set();
        (this._profiles || []).forEach((p) => {
            if (picked.has(p.id)) (p.capabilities || []).forEach((c) => caps.add(c));
        });
        var sorted = Array.from(caps).sort();
        if (!sorted.length) {
            host.innerHTML = '<p class="text-xs text-textMuted">'
                + this._escapeHTML(this._t('aiStudio.capsPickAgentFirst', '先選 Agent，這裡會列出可關閉的能力'))
                + '</p>';
            return;
        }
        host.innerHTML =
            '<p class="text-xs text-textMuted mb-2">'
            + this._escapeHTML(this._t('aiStudio.capsHint', '預設全開。取消勾選＝這個 Preset 不使用該能力（只能關、不能加開）'))
            + '</p><div class="flex flex-wrap gap-x-4 gap-y-1">'
            + sorted.map((c) =>
                '<label class="flex items-center gap-2 text-sm"><input type="checkbox" class="ai-studio-cap-cb" value="'
                + this._escapeHTML(c) + '" checked> ' + this._escapeHTML(c) + '</label>').join('')
            + '</div>';
    },

    // 只送「被關掉的」——送 true 進去後端會忽略（不得擴權），送了只是噪音。
    _collectCapabilityOverrides() {
        var out = {};
        document.querySelectorAll('.ai-studio-cap-cb').forEach((cb) => {
            if (!cb.checked) out[cb.value] = false;
        });
        return out;
    },

    closePresetForm() {
        // 所有出口清編輯態：取消編輯後按「建立 Preset」必須真的是建立
        this._editingPresetId = null;
        document.getElementById('ai-studio-preset-form')?.classList.add('hidden');
    },

    // 建立與編輯共用同一張表單：_editingPresetId 決定送 POST 還是 PATCH。
    async submitPresetForm() {
        var name = document.getElementById('ai-studio-preset-name')?.value?.trim();
        var agentIds = Array.from(document.querySelectorAll('.ai-studio-agent-cb:checked')).map((cb) => cb.value);
        var mode = document.getElementById('ai-studio-preset-mode')?.value || 'single';
        var policy = document.getElementById('ai-studio-preset-policy')?.value || 'read_only';
        if (!name || !agentIds.length) {
            window.showInfoDialog({
                title: this._t('aiStudio.formInvalid', '請填寫名稱並選擇至少一個 Agent'),
                tone: 'warning',
            });
            return;
        }
        // analysis_mode：UI 沒有這個欄位。建立固定 quick；編輯必須沿用該
        // Preset 現值——硬送 quick 會把已存的 verified/research 悄悄降級
        var editing = (this._presets || []).find((x) => x.preset_id === this._editingPresetId);
        var payload = {
            name: name,
            agent_ids: agentIds,
            mode: mode,
            analysis_mode: editing ? (editing.analysis_mode || 'quick') : 'quick',
            action_policy: policy,
            capability_overrides: this._collectCapabilityOverrides(),
        };
        try {
            if (this._editingPresetId) {
                // 不送 is_default：切換作用中 Preset 是「啟用」鈕的職責，
                // 編輯表單順便切換會讓使用者誤以為只改了名字
                await AppAPI.patch(
                    '/api/agent-presets/' + encodeURIComponent(this._editingPresetId),
                    payload
                );
            } else {
                payload.is_default = false;
                await AppAPI.post('/api/agent-presets', payload);
            }
            this.closePresetForm();
            await this.refresh();
        } catch (err) {
            window.showInfoDialog({
                title: this._t(
                    this._editingPresetId ? 'aiStudio.updateFailed' : 'aiStudio.createFailed',
                    this._editingPresetId ? 'Update failed' : 'Create failed'
                ),
                message: (err && err.message) || '',
                tone: 'error',
            });
        }
    },

    async activatePreset(presetId) {
        try {
            await AppAPI.post('/api/agent-presets/' + encodeURIComponent(presetId) + '/activate');
            await this.refresh();
        } catch (err) {
            window.showInfoDialog({
                title: this._t('aiStudio.activateFailed', '啟用失敗'),
                message: (err && err.message) || '',
                tone: 'error',
            });
        }
    },

    async deletePreset(presetId) {
        const ok = await window.showConfirmDialog({
            title: this._t('aiStudio.delete', '刪除'),
            message: this._t('aiStudio.confirmDelete', '確定刪除此 Preset？'),
            danger: true,
        });
        if (!ok) return;
        try {
            await AppAPI.delete('/api/agent-presets/' + encodeURIComponent(presetId));
            if (typeof window.showToast === 'function') {
                window.showToast(this._t('aiStudio.deleteDone', '已刪除'), 'success');
            }
            await this.refresh();
        } catch (err) {
            window.showInfoDialog({
                title: this._t('aiStudio.deleteFailed', '刪除失敗'),
                message: (err && err.message) || '',
                tone: 'error',
            });
        }
    },
};

window.AIStudioTab = AIStudioTab;
