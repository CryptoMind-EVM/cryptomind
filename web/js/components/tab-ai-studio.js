// Auto-generated from components.js split
// Tab: AI Studio — Agents/Presets 管理中心（Phase 1 骨架 + Phase 2 preset UI）
// 規格見 design.md §7（AI Studio 資訊架構）；入口：Settings「我的 AI」摘要卡
window.Components = window.Components || {};
window.Components['ai-studio'] = `
        <div class="${TAB_SHELL_CLASS}">
            <!-- Header -->
            <div class="${TAB_HEADER_CLASS}">
                <div class="flex items-center gap-2 min-w-0">
                    <button data-click="AIStudioTab.goBack" class="${ICON_ACTION_BUTTON_CLASS} shrink-0" aria-label="back" data-i18n-label="aiStudio.back">
                        <i data-lucide="arrow-left" class="w-4 h-4"></i>
                    </button>
                    <h2 class="font-serif text-3xl text-secondary truncate" data-i18n="aiStudio.title">AI Studio</h2>
                </div>
                <div class="flex items-center gap-2">
                    <button data-click="AIStudioTab.toggleTheme" class="${ICON_ACTION_BUTTON_CLASS}" aria-label="theme" data-i18n-label="aiStudio.toggleTheme">
                        <i data-lucide="sun-moon" class="w-4 h-4"></i>
                    </button>
                    <button data-click="AIStudioTab.refresh" class="${ICON_ACTION_BUTTON_CLASS}" aria-label="refresh" data-i18n="common.refresh" data-i18n-attr="aria-label">
                        <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                    </button>
                </div>
            </div>

            <!-- Content Area -->
            <div class="${TAB_CONTENT_AREA_CLASS}">
                <div id="ai-studio-tab-body" ${SHELL_SCROLL_ATTR} class="absolute inset-0 ${SHELL_SCROLLBAR_CLASS}">
                    <div class="max-w-4xl mx-auto pb-10">

                        <!-- 頁籤：總覽 | Agents | Presets（segmented control：雙主題皆有
                             surfaceHighlight/surface 對比，避免淺色下文字黏在一起） -->
                        <div class="flex flex-wrap gap-1 mb-6 p-1 rounded-xl bg-surfaceHighlight border border-borderSubtle/10" role="tablist">
                            <button data-click="AIStudioTab.showSection" data-click-arg="overview"
                                class="ai-studio-nav px-3 py-1.5 text-sm font-medium rounded-lg text-textMuted hover:text-secondary transition-colors"
                                data-i18n="aiStudio.overview">總覽</button>
                            <button data-click="AIStudioTab.showSection" data-click-arg="agents"
                                class="ai-studio-nav px-3 py-1.5 text-sm font-medium rounded-lg text-textMuted hover:text-secondary transition-colors"
                                data-i18n="aiStudio.agents">Agents</button>
                            <button data-click="AIStudioTab.showSection" data-click-arg="presets"
                                class="ai-studio-nav px-3 py-1.5 text-sm font-medium rounded-lg text-textMuted hover:text-secondary transition-colors"
                                data-i18n="aiStudio.presets">Presets</button>
                            <button data-click="AIStudioTab.showSection" data-click-arg="models"
                                class="ai-studio-nav px-3 py-1.5 text-sm font-medium rounded-lg text-textMuted hover:text-secondary transition-colors"
                                data-i18n="aiStudio.modelsTab">模型</button>
                            <button data-click="AIStudioTab.showSection" data-click-arg="skills"
                                class="ai-studio-nav px-3 py-1.5 text-sm font-medium rounded-lg text-textMuted hover:text-secondary transition-colors"
                                data-i18n="aiStudio.skillsTab">Skills</button>
                            <button data-click="AIStudioTab.showSection" data-click-arg="memory"
                                class="ai-studio-nav px-3 py-1.5 text-sm font-medium rounded-lg text-textMuted hover:text-secondary transition-colors"
                                data-i18n="aiStudio.memoryTab">Memory</button>
                            <button data-click="AIStudioTab.showSection" data-click-arg="tools"
                                class="ai-studio-nav px-3 py-1.5 text-sm font-medium rounded-lg text-textMuted hover:text-secondary transition-colors"
                                data-i18n="aiStudio.toolsTab">Tools</button>
                        </div>

                        <!-- 總覽 -->
                        <section id="ai-studio-overview" class="ai-studio-section">
                            <!-- Stat tiles share one rhythm: fixed-height value row, one label class.
                                 Catalog version is deliberately NOT a tile (internal data version,
                                 meaningless to users); it lives on this section's data-config-version.
                                 Rationale + regression guards: tests/test_ai_studio_overview_layout.py -->
                            <div class="grid grid-cols-3 gap-3 mb-6">
                                <div class="min-w-0 rounded-xl border border-borderSubtle/10 bg-surface p-4 text-center">
                                    <p class="flex h-8 items-center justify-center">
                                        <span class="block w-full truncate text-2xl font-semibold leading-none tabular-nums text-secondary" id="ai-studio-count-profiles">–</span>
                                    </p>
                                    <p class="text-xs leading-snug text-textMuted mt-1.5" data-i18n="aiStudio.agentCount">官方 Agents</p>
                                </div>
                                <div class="min-w-0 rounded-xl border border-borderSubtle/10 bg-surface p-4 text-center">
                                    <p class="flex h-8 items-center justify-center">
                                        <span class="block w-full truncate text-2xl font-semibold leading-none tabular-nums text-secondary" id="ai-studio-count-presets">–</span>
                                    </p>
                                    <p class="text-xs leading-snug text-textMuted mt-1.5" data-i18n="aiStudio.presetCount">我的 Presets</p>
                                </div>
                                <div class="min-w-0 rounded-xl border border-borderSubtle/10 bg-surface p-4 text-center">
                                    <p class="flex h-8 items-center justify-center">
                                        <span class="block w-full truncate text-sm font-semibold leading-none text-secondary" id="ai-studio-current-policy">–</span>
                                    </p>
                                    <p class="text-xs leading-snug text-textMuted mt-1.5" data-i18n="aiStudio.actionPolicy">行動政策</p>
                                </div>
                            </div>
                            <p class="text-sm text-textMuted" data-i18n="aiStudio.overviewNote">
                                Memory、Skill 與 Tool 管理器已整合於上方分頁；Agents 為官方 Profile 目錄，Presets 組合你專屬的分析團隊。
                            </p>
                        </section>

                        <!-- Agents（官方 Profile catalog，唯讀） -->
                        <section id="ai-studio-agents" class="ai-studio-section hidden">
                            <div id="ai-studio-profiles" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
                        </section>

                        <!-- Presets（Premium 自訂；Free 顯示官方預設） -->
                        <section id="ai-studio-presets" class="ai-studio-section hidden">
                            <div class="flex items-center justify-between mb-4">
                                <p class="text-sm text-textMuted" id="ai-studio-preset-quota"></p>
                                <button data-click="AIStudioTab.openCreatePreset" class="px-4 py-2 rounded-xl bg-primary text-background text-sm font-medium" data-i18n="aiStudio.createPreset">建立 Preset</button>
                            </div>
                            <div id="ai-studio-preset-list" class="grid grid-cols-1 gap-3"></div>

                            <!-- 建立／編輯 Preset 表單（modal 式卡片；編輯模式由 editPreset() 預填） -->
                            <div id="ai-studio-preset-form" class="hidden mt-4 p-4 rounded-xl border border-borderSubtle/10 bg-surface">
                                <p id="ai-studio-preset-form-title" class="font-medium mb-3" data-i18n="aiStudio.newPresetTitle">新增 Preset</p>
                                <label class="block text-xs text-textMuted mb-1" data-i18n="aiStudio.presetName">名稱</label>
                                <input id="ai-studio-preset-name" type="text" maxlength="50"
                                    class="w-full mb-3 px-3 py-2 rounded-lg bg-surfaceHighlight border border-borderSubtle/10 text-sm"
                                    data-i18n="aiStudio.presetNamePlaceholder" data-i18n-attr="placeholder" placeholder="My Research Team">
                                <label class="block text-xs text-textMuted mb-1" data-i18n="aiStudio.presetAgents">Agent Profiles（最多 4 個）</label>
                                <div id="ai-studio-preset-agent-checkboxes" class="grid grid-cols-1 md:grid-cols-2 gap-2 mb-3"></div>
                                <label class="block text-xs text-textMuted mb-1" data-i18n="aiStudio.presetMode">模式</label>
                                <select id="ai-studio-preset-mode" class="w-full mb-3 px-3 py-2 rounded-lg bg-surfaceHighlight border border-borderSubtle/10 text-sm">
                                    <option value="single" data-i18n="aiStudio.modeSingle">單一 Agent</option>
                                    <option value="auto" data-i18n="aiStudio.modeAuto">自動調度</option>
                                    <option value="team" data-i18n="aiStudio.modeTeam">團隊協作</option>
                                </select>
                                <label class="block text-xs text-textMuted mb-1" data-i18n="aiStudio.presetCaps">能力範圍</label>
                                <div id="ai-studio-preset-capabilities" class="mb-4"></div>
                                <label class="block text-xs text-textMuted mb-1" data-i18n="aiStudio.presetPolicy">工具權限</label>
                                <select id="ai-studio-preset-policy" class="w-full mb-1 px-3 py-2 rounded-lg bg-surfaceHighlight border border-borderSubtle/10 text-sm">
                                    <option value="read_only" data-i18n="aiStudio.readOnly">只讀（不用高風險工具）</option>
                                    <option value="confirm_actions" data-i18n="aiStudio.confirmActions">需要我確認（可用高風險工具）</option>
                                </select>
                                <p class="mb-4 text-xs text-textMuted" data-i18n="aiStudio.policyHint">「只讀」會把標記高風險的工具整個從可用池移除；「需要我確認」保留它們，執行前跳確認卡。</p>
                                <div class="flex gap-2">
                                    <button data-click="AIStudioTab.submitPresetForm" class="px-4 py-2 rounded-lg bg-primary text-background text-sm font-medium" data-i18n="aiStudio.save">儲存</button>
                                    <button data-click="AIStudioTab.closePresetForm" class="px-4 py-2 rounded-lg bg-surfaceHighlight text-sm" data-i18n="aiStudio.cancel">取消</button>
                                </div>
                            </div>
                        </section>

                        <!-- Memory（自 Settings 搬入） -->
                        <section id="ai-studio-memory" class="ai-studio-section hidden">
                            <div class="max-w-2xl mx-auto">
                <!-- Memory（自 Settings 搬入；綁定 ID 不變，memory-manager.js 邏輯不動） -->
                <div id="settings-memory-card" class="${SECTION_CARD_DEFAULT}">
                    <div class="flex items-center justify-between mb-4">
                        <div class="flex items-center gap-3">
                            <div class="w-10 h-10 rounded-xl flex items-center justify-center" style="background: rgba(168,85,247,0.1);">
                                <i data-lucide="brain" class="w-5 h-5" style="color: rgb(168,85,247);"></i>
                            </div>
                            <div>
                                <h3 class="text-lg font-serif text-secondary" data-i18n="settings.memory.title">AI Memory</h3>
                                <p class="text-xs text-textMuted" data-i18n="settings.memory.desc">What the AI remembers about you</p>
                            </div>
                        </div>
                        <div class="flex items-center gap-2">
                            <button id="btn-add-memory" class="text-xs px-3 py-1.5 rounded-lg bg-primary/20 text-primary hover:bg-primary/30 transition" data-i18n="settings.memory.add">+ Add</button>
                            <button id="btn-refresh-memory" class="text-textMuted hover:text-primary transition" title="Refresh" data-i18n="common.refresh" data-i18n-attr="title">
                                <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                            </button>
                        </div>
                    </div>
                    <div id="memory-list" class="space-y-2">
                        <p class="text-sm text-textMuted text-center py-4" data-i18n="settings.memory.loading">Loading...</p>
                    </div>
                    <div id="memory-empty" class="hidden text-center py-6">
                        <i data-lucide="brain" class="w-8 h-8 text-textMuted/30 mx-auto mb-2"></i>
                        <p class="text-sm text-textMuted" data-i18n="settings.memory.empty">No memories yet. The AI will remember your preferences as you chat.</p>
                    </div>
                    <!-- 新增記憶編輯器 -->
                    <div id="memory-add-editor" class="hidden space-y-3 mt-3 p-4 rounded-xl bg-background/50 border border-primary/20">
                        <div>
                            <label class="text-xs text-textMuted block mb-1" data-i18n="settings.memory.keyLabel">Label</label>
                            <input id="memory-add-key" type="text" class="w-full bg-background border border-borderLight rounded-lg px-3 py-2 text-sm text-secondary focus:outline-none focus:border-primary/40" data-i18n="settings.memory.addKeyPlaceholder" data-i18n-attr="placeholder" placeholder="Label" maxlength="100">
                        </div>
                        <div>
                            <label class="text-xs text-textMuted block mb-1" data-i18n="settings.memory.valueLabel">Content</label>
                            <input id="memory-add-value" type="text" class="w-full bg-background border border-borderLight rounded-lg px-3 py-2 text-sm text-secondary focus:outline-none focus:border-primary/40" data-i18n="settings.memory.addValuePlaceholder" data-i18n-attr="placeholder" placeholder="Content" maxlength="512">
                        </div>
                        <div class="flex items-center gap-2">
                            <button id="btn-save-memory" class="text-xs px-4 py-1.5 rounded-lg bg-primary/30 text-primary hover:bg-primary/40 transition" data-i18n="common.save">Save</button>
                            <button id="btn-cancel-memory" class="text-xs px-4 py-1.5 rounded-lg text-textMuted hover:text-secondary transition font-bold" data-i18n="common.cancel">Cancel</button>
                        </div>
                    </div>
                </div>

                            </div>
                        </section>

                        <!-- Skills（自 Settings 搬入） -->
                        <!-- 模型：金鑰綁定與已綁定模型清單（2026-09-04 自 Settings 搬入；
                             ID 全部沿用，apiKeyManager.js／updateAvailableModels 邏輯不動） -->
                        <section id="ai-studio-models" class="ai-studio-section hidden">
                        <div id="settings-llm-card" class="${SECTION_CARD_PRIMARY}">
                            <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between mb-6">
                                <div class="${CARD_HEADER_ROW_CLASS}">
                                    <div class="${HERO_ICON_BOX_CLASS}">
                                        <i data-lucide="brain" class="w-5 h-5 text-primary"></i>
                                    </div>
                                    <div>
                                        <h3 class="text-lg font-serif text-primary" data-i18n="settings.ai.title">AI Intelligence</h3>
                                        <p class="text-xs text-textMuted" data-i18n="settings.ai.description">Configure your LLM provider</p>
                                    </div>
                                </div>
                                <div id="llm-status-badge" class="${STATUS_BADGE_CLASS} self-start sm:self-auto">
                                </div>
                            </div>

                            <div class="space-y-5">
                                <!-- 已綁定模型清單：金鑰綁定一次後，在這裡一鍵切換要使用的模型（renderBoundModels() 動態渲染） -->
                                <div class="space-y-2">
                                    <label class="block text-xs font-bold text-textMuted uppercase tracking-wider mb-2" data-i18n="settings.ai.boundModels">Bound Models</label>
                                    <div id="llm-bound-models" class="space-y-2"></div>
                                    <p class="text-xs leading-5 text-textMuted/75" data-i18n="settings.ai.boundModelsHint">Tap "Use" to switch models. Keys only need to be bound once.</p>
                                </div>

                                <div class="border-t border-borderSubtle pt-5">
                                    <h4 class="text-sm font-bold text-secondary" data-i18n="settings.ai.addBinding">Add Model Binding</h4>
                                </div>

                                <div class="space-y-2">
                                    <label class="block text-xs font-bold text-textMuted uppercase tracking-wider mb-2" data-i18n="settings.ai.provider">Provider</label>
                                    <!-- 選項由 populateProviderSelect() 依 /api/model-config 動態填充；
                                         下面僅為初次渲染的 fallback。新增 provider 請改後端 MODEL_CONFIG。 -->
                                    <select id="llm-provider-select" data-change-action="llmProviderChange" class="w-full bg-background border border-borderSubtle rounded-2xl px-4 py-3.5 text-secondary outline-none focus:border-primary/50 transition appearance-none">
                                        <option value="openai">OpenAI</option>
                                        <option value="google_gemini">Google Gemini</option>
                                        <option value="openrouter">OpenRouter</option>
                                    </select>
                                    <p class="text-xs leading-5 text-textMuted/75" data-i18n="settings.ai.providerHint">Pick the AI provider you want to bind first, then choose the model to use.</p>
                                </div>

                                <div class="space-y-2">
                                    <label class="block text-xs font-bold text-textMuted uppercase tracking-wider mb-2" data-i18n="settings.ai.model">Model</label>
                                    <select id="llm-model-select" class="w-full bg-background border border-borderSubtle rounded-2xl px-4 py-3.5 text-secondary outline-none focus:border-primary/50 transition appearance-none" style="display: block;">
                                    <!-- Models loaded dynamically via updateAvailableModels() -->
                                    </select>
                                    <input type="text" id="llm-model-input" class="w-full bg-background border border-borderSubtle rounded-2xl px-4 py-3.5 text-sm text-secondary outline-none focus:border-primary/50 transition mt-3"
                                           placeholder="e.g., openai/gpt-4o, anthropic/claude-3.5-sonnet" data-i18n="settings.ai.modelPlaceholder" data-i18n-attr="placeholder"
                                           style="display: none;" />
                                    <p id="llm-model-hint" class="text-xs leading-5 text-textMuted/75" data-i18n="settings.ai.modelHint">Select a model first, then enter the corresponding API key for a smoother flow.</p>
                                </div>

                                <div class="space-y-2">
                                    <label class="block text-xs font-bold text-textMuted uppercase tracking-wider mb-2" data-i18n="settings.ai.apiKey">API Key</label>
                                    <div class="flex gap-2 items-stretch">
                                        <input type="password" id="llm-api-key-input" class="flex-1 min-w-0 bg-background border border-borderSubtle rounded-2xl px-4 py-3 text-secondary outline-none focus:border-primary/50 font-mono text-sm disabled:cursor-not-allowed disabled:opacity-50" placeholder="sk-..." disabled>
                                        <button id="test-llm-key-btn" data-click="testLLMKey" class="shrink-0 px-4 py-3 bg-surfaceHighlight hover:bg-surfaceHighlight text-secondary rounded-2xl transition font-bold text-xs whitespace-nowrap disabled:opacity-50 disabled:cursor-not-allowed" disabled data-i18n="settings.ai.test">TEST</button>
                                    </div>
                                    <p id="llm-key-status" class="text-xs leading-5 text-textMuted/75" data-i18n="settings.ai.keyStatusHint">Please select a provider and model before entering the API key.</p>
                                    <p id="llm-binding-status" class="text-xs leading-5 text-success hidden"></p>
                                </div>

                                <button id="save-llm-key-btn" data-click="saveLLMKey" class="w-full py-3.5 bg-primary text-background font-bold rounded-xl shadow-lg shadow-primary/10 opacity-50 cursor-not-allowed transition mt-2" disabled data-i18n="settings.ai.saveConfig">
                                    Save AI Configuration
                                </button>
                            </div>
                        </div>
                        </section>

                        <section id="ai-studio-skills" class="ai-studio-section hidden">
                            <div class="max-w-2xl mx-auto">
                <!-- Skills（自 Settings 搬入；skill-manager.js 邏輯不動） -->
                <div id="settings-skill-card" class="${SECTION_CARD_DEFAULT}">
                    <div class="flex items-center justify-between mb-4">
                        <div class="flex items-center gap-3">
                            <div class="w-10 h-10 rounded-xl flex items-center justify-center" style="background: rgba(59,130,246,0.1);">
                                <i data-lucide="wrench" class="w-5 h-5" style="color: rgb(59,130,246);"></i>
                            </div>
                            <div>
                                <h3 class="text-lg font-serif text-secondary" data-i18n="settings.skills.title">Analysis Skills</h3>
                                <p class="text-xs text-textMuted" data-i18n="settings.skills.desc">Toggle official skills or add your own</p>
                            </div>
                        </div>
                        <div class="flex items-center gap-2">
                            <button id="btn-add-skill" class="text-xs px-3 py-1.5 rounded-lg bg-primary/20 text-primary hover:bg-primary/30 transition" data-i18n="settings.skills.add">+ Add</button>
                            <button id="btn-refresh-skills" class="text-textMuted hover:text-primary transition" title="Refresh" data-i18n="common.refresh" data-i18n-attr="title">
                                <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                            </button>
                        </div>
                    </div>
                    <!-- Official skills (toggle only) -->
                    <p class="text-xs text-textMuted mb-2" data-i18n="settings.skills.officialTitle">Official Skills</p>
                    <div id="skill-official-list" class="space-y-2 mb-4">
                        <p class="text-sm text-textMuted text-center py-4" data-i18n="settings.skills.loading">Loading...</p>
                    </div>
                    <!-- Custom skills (CRUD) -->
                    <p class="text-xs text-textMuted mb-2" data-i18n="settings.skills.customTitle">Your Custom Skills</p>
                    <div id="skill-custom-list" class="space-y-2 mb-4"></div>
                    <!-- Editor (hidden by default) -->
                    <div id="skill-editor" class="hidden space-y-3 p-4 rounded-xl bg-background/50 border border-primary/20">
                        <div>
                            <label class="text-xs text-textMuted block mb-1" data-i18n="settings.skills.nameLabel">Skill Name (English/numbers/_/-)</label>
                            <input id="skill-edit-name" type="text" class="w-full bg-background border border-borderLight rounded-lg px-3 py-2 text-sm text-secondary focus:outline-none focus:border-primary/40" placeholder="my-analysis-style" maxlength="50" data-i18n="settings.skills.namePlaceholder" data-i18n-attr="placeholder">
                        </div>
                        <div>
                            <label class="text-xs text-textMuted block mb-1" data-i18n="settings.skills.descLabel">Description</label>
                            <input id="skill-edit-desc" type="text" class="w-full bg-background border border-borderLight rounded-lg px-3 py-2 text-sm text-secondary focus:outline-none focus:border-primary/40" data-i18n="settings.skills.descPlaceholder" data-i18n-attr="placeholder" placeholder="One-line description" maxlength="200">
                        </div>
                        <div>
                            <label class="text-xs text-textMuted block mb-1" data-i18n="settings.skills.triggerLabel">Trigger Keywords (comma-separated)</label>
                            <input id="skill-edit-trigger" type="text" class="w-full bg-background border border-borderLight rounded-lg px-3 py-2 text-sm text-secondary focus:outline-none focus:border-primary/40" placeholder="分析,比較,適合買嗎" maxlength="200" data-i18n="settings.skills.triggerPlaceholder" data-i18n-attr="placeholder">
                        </div>
                        <div>
                            <label class="text-xs text-textMuted block mb-1" data-i18n="settings.skills.bodyLabel">Analysis Method (max 3000 chars)</label>
                            <textarea id="skill-edit-body" class="w-full bg-background border border-borderLight rounded-lg px-3 py-2 text-sm text-secondary focus:outline-none focus:border-primary/40 resize-y" rows="6" placeholder="Step 1: ...&#10;Step 2: ..." maxlength="3000" data-i18n="settings.skills.bodyPlaceholder" data-i18n-attr="placeholder"></textarea>
                        </div>
                        <div class="flex items-center gap-2">
                            <button id="btn-save-skill" class="text-xs px-4 py-1.5 rounded-lg bg-primary/30 text-primary hover:bg-primary/40 transition" data-i18n="settings.skills.save">Save</button>
                            <button id="btn-cancel-skill" class="text-xs px-4 py-1.5 rounded-lg text-textMuted hover:text-secondary transition font-bold" data-i18n="settings.skills.cancel">Cancel</button>
                        </div>
                    </div>
                </div>

                            </div>
                        </section>

                        <!-- Tools（自 Settings 搬入；modal 為 index.html 全域） -->
                        <section id="ai-studio-tools" class="ai-studio-section hidden">
                            <div class="max-w-2xl mx-auto">
                <!-- Tools（自 Settings 搬入；開啟全域 tool-settings modal） -->
                <div class="${SECTION_CARD_SECONDARY}">
                    <div class="flex items-center justify-between mb-6">
                        <div class="flex items-center gap-3">
                            <div class="w-10 h-10 rounded-xl bg-accent/10 flex items-center justify-center">
                                <i data-lucide="sliders-horizontal" class="w-5 h-5 text-accent"></i>
                            </div>
                            <div>
                                <h3 class="text-lg font-serif text-accent" data-i18n="aiStudio.toolSelectionTitle">AI Tool Selection</h3>
                                <p class="text-xs text-textMuted" data-i18n="aiStudio.toolSelectionDesc">Choose which analysis tools your agent can use</p>
                            </div>
                        </div>
                    </div>

                    <div id="tool-settings-free-notice" class="hidden mb-4 bg-background/50 rounded-xl p-4 border border-borderSubtle/10">
                        <p class="text-sm text-textMuted leading-relaxed">
                            <i data-lucide="lock" class="w-4 h-4 inline-block mr-1 opacity-70"></i>
                            <span data-i18n="settings.upgradeForTools">Upgrade to Premium to customize your agent's tool set.</span>
                        </p>
                    </div>

                    <button data-click="openToolSettingsModal"
                        class="w-full py-3 px-4 bg-primary/10 hover:bg-primary/20 border border-primary/20 rounded-2xl text-primary font-semibold text-sm transition flex items-center justify-center gap-2">
                        <i data-lucide="settings-2" class="w-4 h-4"></i>
                        <span data-i18n="toolSettings.openModal">Manage Tools</span>
                    </button>
                </div>

                            </div>
                        </section>

                    </div>
                </div>
            </div>
        </div>
`;
