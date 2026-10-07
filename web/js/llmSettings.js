// ========================================
// llmSettings.js - LLM Settings
// ========================================

var llmState = {
    testPassed: false,
    testPassedKey: '',
    testPassedModel: '',
    isSaving: false,
    isTesting: false,
    isModelsLoading: false,
    savedKeys: {},
    initialized: false,
};

AppStore.set('llmState', llmState);
window.llmState = llmState;

function getProviderSelect() {
    return document.getElementById('llm-provider-select');
}

function getModelSelect() {
    return document.getElementById('llm-model-select');
}

function getModelInput() {
    return document.getElementById('llm-model-input');
}

function getApiKeyInput() {
    return document.getElementById('llm-api-key-input');
}

function getTestButton() {
    return document.getElementById('test-llm-key-btn');
}

function getSaveButton() {
    return document.getElementById('save-llm-key-btn');
}

function getSelectedProvider() {
    return getProviderSelect()?.value || '';
}

// free_input provider（OpenRouter / NVIDIA / 火山方舟等）會把模型欄位切成文字輸入框。
// 用「文字框是否顯示」判斷，而非寫死某個 provider 名稱。
function isFreeInputActive() {
    var modelInput = getModelInput();
    return !!(modelInput && modelInput.style.display !== 'none');
}

function getSelectedLLMModel() {
    // 自訂模型選項（2026-08-28）：非 free_input provider 也允許手動輸入——
    // 下拉選到「其他」時改讀手動輸入框
    var modelSelect = getModelSelect();
    if (modelSelect && modelSelect.style.display !== 'none' && modelSelect.value === '__custom__') {
        return getModelInput()?.value?.trim() || '';
    }
    if (isFreeInputActive()) {
        return getModelInput()?.value?.trim() || '';
    }
    if (modelSelect && modelSelect.options.length === 0) {
        return '';
    }
    return modelSelect?.value || '';
}

function resetLLMTestState() {
    llmState.testPassed = false;
    llmState.testPassedKey = '';
    llmState.testPassedModel = '';
    disableSaveButton();
}

function maskApiKey(key) {
    if (!key || key.length < 8) return '****';
    return key.slice(0, 4) + '****...****' + key.slice(-4);
}

// in-flight 去重：auth:ready / auth:initialized / 進設定分頁 / 語言切換重渲染
// 常在同一瞬間各觸發一次，共用同一個請求避免對 /api/user/api-keys 連發 GET
var _loadSavedApiKeysInflight = null;

function loadSavedApiKeys() {
    if (_loadSavedApiKeysInflight) return _loadSavedApiKeysInflight;
    _loadSavedApiKeysInflight = _loadSavedApiKeysImpl().finally(function () {
        _loadSavedApiKeysInflight = null;
    });
    return _loadSavedApiKeysInflight;
}

async function _loadSavedApiKeysImpl() {
    const user = typeof AuthManager !== 'undefined' ? AuthManager.currentUser : null;
    const hasKnownSession = !!(user && (user.user_id || user.uid));

    if (!hasKnownSession) {
        return;
    }

    try {
        // kind=llm：這份資料驅動「已綁定模型」清單與綁定狀態，工具金鑰（tavily 等）另由 toolSettings 管理
        var data = await AppAPI.get('/api/user/api-keys?kind=llm');
        llmState.savedKeys = data.keys || {};

        // 先確保 model-config 已載入，getBoundProviders() 才能以固定順序排序，
        // 自動挑選才不會隨後端 dict 順序漂移
        await ensureModelConfigCache();

        var boundProviders = getBoundProviders();
        boundProviders.forEach(function (provider) {
            var info = llmState.savedKeys[provider];
            // 只同步 localStorage 快取；model 本來就來自後端，不可再 POST 回去
            // （進設定頁/語言切換都會重跑本函式，回寫會變成 /model 請求洪水）
            if (info?.model && typeof window.APIKeyManager?.cacheModelForProvider === 'function') {
                window.APIKeyManager.cacheModelForProvider(provider, info.model);
            }
        });
        var activeProvider = boundProviders.length > 0 ? boundProviders[0] : null;

        var currentSelection = localStorage.getItem('user_selected_provider');
        if (
            currentSelection &&
            llmState.savedKeys[currentSelection] &&
            llmState.savedKeys[currentSelection].has_key
        ) {
            activeProvider = currentSelection;
        }

        if (activeProvider) {
            // 同步下拉選單 DOM——但只在「首次初始化」或「select 還沒有值」時設。
            // 之前每次 loadSavedApiKeys 重跑（auth:ready / auth:initialized）都會強制
            // 把 select 覆蓋成 activeProvider，導致使用者在設定頁手動切到別的 provider
            // （例如選 NVIDIA 要測試）時，被覆蓋回 activeProvider（例如 OpenRouter），
            // 接著 test/save 就用錯 provider 把 key 存進錯的欄位。
            var providerSelect = document.getElementById('llm-provider-select');
            var needsInit = !llmState._activeProviderInitialized || !providerSelect?.value;
            if (providerSelect && needsInit && providerSelect.value !== activeProvider) {
                providerSelect.value = activeProvider;
                // 觸發模型列表更新
                if (typeof window.updateAvailableModels === 'function') {
                    window.updateAvailableModels();
                }
            }
            llmState._activeProviderInitialized = true;
            if (typeof window.APIKeyManager?.setSelectedProvider === 'function') {
                window.APIKeyManager.setSelectedProvider(activeProvider);
            }
        }

        updateBindingStatus();
        updateLLMFormState();
        await renderBoundModels();
    } catch (error) {
        console.error('[loadSavedApiKeys] Error:', error);
    }
}
window.loadSavedApiKeys = loadSavedApiKeys;

// ========================================
// 已綁定模型管理（列出所有已綁定的 provider，一鍵切換使用中的模型）
// ========================================

// provider 顯示名稱來自 /api/model-config（單一真實來源在後端 MODEL_CONFIG）
async function ensureModelConfigCache() {
    if (window.__modelConfigCache) return window.__modelConfigCache;
    try {
        // 走 AppAPI.getModelConfig：跟 app.js 的設定初始化共用同一個請求與快取
        //（以前各打各的，一開頁 /api/model-config 打兩次——2026-09-26 量測）
        if (typeof AppAPI.getModelConfig === 'function') {
            window.__modelConfigCache = (await AppAPI.getModelConfig()) || null;
            return window.__modelConfigCache;
        }
        var data = await AppAPI.get('/api/model-config');
        window.__modelConfigCache = data?.model_config || null;
    } catch (error) {
        console.warn('[ensureModelConfigCache] Failed to fetch model config:', error);
    }
    return window.__modelConfigCache;
}

function getProviderDisplayName(provider) {
    // 有翻譯用翻譯（zh 的品牌中文名），沒有退回後端 display 的英文標準
    if (window.I18n && typeof window.I18n.t === 'function') {
        var key = 'settings.ai.providerNames.' + provider;
        var v = window.I18n.t(key);
        if (v && v !== key) return v;
    }
    return window.__modelConfigCache?.[provider]?.display || provider;
}

function getBoundProviders() {
    var bound = Object.keys(llmState.savedKeys).filter(function (provider) {
        return !!llmState.savedKeys[provider]?.has_key;
    });
    // 後端回應的 dict 順序會隨資料列更新變動，依 model-config 順序排序讓清單穩定
    var cfg = window.__modelConfigCache || {};
    var order = Object.keys(cfg);
    if (order.length === 0) return bound;
    return bound.sort(function (a, b) {
        // 平台模型（keyless）永遠排第一
        var ka = cfg[a] && cfg[a].keyless ? 0 : 1;
        var kb = cfg[b] && cfg[b].keyless ? 0 : 1;
        if (ka !== kb) return ka - kb;
        var ia = order.indexOf(a);
        var ib = order.indexOf(b);
        return (ia === -1 ? order.length : ia) - (ib === -1 ? order.length : ib);
    });
}

// 「使用中」的 provider：localStorage 選擇優先（須仍有綁定），否則第一個有綁定的
function getEffectiveActiveProvider() {
    var bound = getBoundProviders();
    if (bound.length === 0) return null;
    var selected = window.APIKeyManager?.getSelectedProvider?.();
    if (selected && bound.indexOf(selected) !== -1) return selected;
    return bound[0];
}

// 清單展開成一列一個 (provider, model)：同 provider 保存多個模型時每個各佔一列。
// 後端沒回 models 清單（舊快取/舊部署）時退回單列，行為同改版前。
function getBoundModelEntries() {
    var entries = [];
    getBoundProviders().forEach(function (provider) {
        var info = llmState.savedKeys[provider];
        var models =
            Array.isArray(info?.models) && info.models.length > 0
                ? info.models
                : info?.model
                  ? [info.model]
                  : [null];
        models.forEach(function (model) {
            entries.push({ provider: provider, model: model });
        });
    });
    return entries;
}

function buildBoundModelRow(entry, info, isActive) {
    var provider = entry.provider;
    var model = entry.model;

    var row = document.createElement('div');
    row.className =
        'flex items-center gap-3 bg-background border rounded-2xl px-4 py-3 transition ' +
        (isActive ? 'border-primary/50' : 'border-borderSubtle');

    var main = document.createElement('div');
    main.className = 'flex-1 min-w-0';

    var titleLine = document.createElement('div');
    titleLine.className = 'flex items-center gap-2';

    var name = document.createElement('span');
    name.className = 'text-sm font-bold text-secondary truncate';
    name.textContent = model || window.I18n.t('llmSettings.modelNotSet');
    name.title = model || '';
    titleLine.appendChild(name);

    if (isActive) {
        var badge = document.createElement('span');
        badge.className =
            'shrink-0 whitespace-nowrap px-2 py-0.5 rounded-full text-[10px] font-bold bg-success/15 text-success';
        badge.setAttribute('data-i18n', 'llmSettings.activeBadge');
        badge.textContent = window.I18n.t('llmSettings.activeBadge');
        titleLine.appendChild(badge);
    }
    main.appendChild(titleLine);

    var detail = document.createElement('p');
    detail.className = 'text-xs text-textMuted truncate mt-0.5';
    detail.textContent =
        getProviderDisplayName(provider) +
        (info.platform
            ? ' · ' + window.I18n.t('llmSettings.platformBinding')
            : info.masked_key ? ' · ' + info.masked_key : '');
    main.appendChild(detail);
    row.appendChild(main);

    // 兩個參數 (provider, model) 走 data-click-args（JSON 陣列）
    var clickArgs = encodeURIComponent(JSON.stringify([provider, model]));

    if (!isActive) {
        var useBtn = document.createElement('button');
        useBtn.type = 'button';
        useBtn.className =
            'shrink-0 whitespace-nowrap px-3 py-1.5 rounded-xl bg-primary/10 hover:bg-primary/20 text-primary text-xs font-bold transition';
        useBtn.setAttribute('data-click', 'llmActivateProvider');
        useBtn.setAttribute('data-click-args', clickArgs);
        useBtn.setAttribute('data-i18n', 'llmSettings.activateBtn');
        useBtn.textContent = window.I18n.t('llmSettings.activateBtn');
        row.appendChild(useBtn);
    }

    if (info.platform) {
        return row; // 平台綁定不能解除
    }

    var deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className =
        'shrink-0 p-2 rounded-xl text-textMuted hover:text-red-400 hover:bg-red-500/10 transition';
    deleteBtn.setAttribute('data-click', 'llmUnbindProvider');
    deleteBtn.setAttribute('data-click-args', clickArgs);
    deleteBtn.setAttribute('aria-label', window.I18n.t('llmSettings.unbindBtn'));
    deleteBtn.title = window.I18n.t('llmSettings.unbindBtn');
    var trashIcon = document.createElement('i');
    trashIcon.setAttribute('data-lucide', 'trash-2');
    // icon 內的 click 要冒泡到帶 data-click 的 button 本身，lucide 替換後仍成立（closest 會找到 button）
    trashIcon.className = 'w-4 h-4 pointer-events-none';
    deleteBtn.appendChild(trashIcon);
    row.appendChild(deleteBtn);

    return row;
}

async function renderBoundModels() {
    var container = document.getElementById('llm-bound-models');
    if (!container) return;

    await ensureModelConfigCache();

    var entries = getBoundModelEntries();
    container.textContent = '';

    if (entries.length === 0) {
        var empty = document.createElement('p');
        empty.className = 'text-xs text-textMuted/75 bg-background border border-borderSubtle rounded-2xl px-4 py-3';
        empty.setAttribute('data-i18n', 'llmSettings.noBoundModels');
        empty.textContent = window.I18n.t('llmSettings.noBoundModels');
        container.appendChild(empty);
        return;
    }

    var activeProvider = getEffectiveActiveProvider();
    entries.forEach(function (entry) {
        var info = llmState.savedKeys[entry.provider];
        // 使用中的列 = 使用中的 provider 上、model_selection 指到的那個模型
        var isActive =
            entry.provider === activeProvider &&
            (entry.model == null || entry.model === info.model);
        container.appendChild(buildBoundModelRow(entry, info, isActive));
    });

    if (window.lucide && typeof window.createIconsIn === 'function') {
        window.createIconsIn(container);
    }
}
window.renderBoundModels = renderBoundModels;

// 金鑰已在後端綁定，切換不需重新輸入金鑰。
// 同 provider 換模型時要先把 model_selection 寫回後端（聊天實際用的模型以後端為準）；
// 這是使用者主動點擊才發的 POST，不是 #190 禁止的「後端資料回音」自動路徑。
async function llmActivateProvider(provider, model) {
    if (!provider || !llmState.savedKeys[provider]?.has_key) return;

    var info = llmState.savedKeys[provider];
    if (model && info.model !== model) {
        try {
            var res = await AppAPI.post('/api/user/api-keys/model', {
                provider: provider,
                model: model,
            });
            if (!res || res.success === false) {
                llmShowToast(window.I18n.t('llmSettings.switchFailed'), 'error');
                return;
            }
        } catch (error) {
            console.error('[llmActivateProvider] switch model failed:', error);
            llmShowToast(window.I18n.t('llmSettings.switchFailed'), 'error');
            return;
        }
        info.model = model;
        if (typeof window.APIKeyManager?.cacheModelForProvider === 'function') {
            window.APIKeyManager.cacheModelForProvider(provider, model);
        }
    }

    if (typeof window.APIKeyManager?.setSelectedProvider === 'function') {
        window.APIKeyManager.setSelectedProvider(provider);
    }

    // 失效聊天端的 provider / key 狀態快取，下一則訊息就用新選擇
    if (typeof AppStore !== 'undefined' && typeof AppStore.set === 'function') {
        AppStore.set('lastApiKeyCheck', 0);
    }
    window.dispatchEvent(new Event('apiKeyUpdated'));

    // 同步「新增綁定」表單顯示到剛啟用的 provider
    var providerSelect = getProviderSelect();
    if (providerSelect && providerSelect.value !== provider) {
        providerSelect.value = provider;
        if (typeof window.updateAvailableModels === 'function') {
            await window.updateAvailableModels();
        }
        updateBindingStatus();
        updateLLMFormState();
    }

    await renderBoundModels();
    if (typeof window.updateLLMStatusUI === 'function') {
        await window.updateLLMStatusUI();
    }

    llmShowToast(
        window.I18n.t('llmSettings.switchedTo', {
            name: getProviderDisplayName(provider) + (model ? ' · ' + model : ''),
        }),
        'success'
    );
}
window.llmActivateProvider = llmActivateProvider;

async function llmUnbindProvider(provider, model) {
    if (!provider || !llmState.savedKeys[provider]?.has_key) return;

    var info = llmState.savedKeys[provider];
    var displayName = getProviderDisplayName(provider);
    var savedCount = Array.isArray(info.models)
        ? info.models.length
        : info.model
          ? 1
          : 0;
    // 還有其他保存模型時只刪那一筆；刪最後一個（或舊資料沒 model）= 整個 provider 解綁
    var isModelDelete = !!model && savedCount > 1;

    var confirmMsg = isModelDelete
        ? window.I18n.t('llmSettings.unbindModelConfirm', {
              name: displayName + ' · ' + model,
          })
        : window.I18n.t('llmSettings.unbindConfirm', { name: displayName });

    // 用 app 的自訂確認框（showConfirm），不用瀏覽器原生 confirm()——
    // 與 chat-sessions / friends / memory-manager 等模組一致，避免破壞 UI 一致性。
    var confirmed =
        typeof showConfirm === 'function'
            ? await showConfirm({
                  title: displayName,
                  message: confirmMsg,
                  confirmText: window.I18n ? window.I18n.t('llmSettings.delete') || window.I18n.t('common.delete') : 'Delete',
                  cancelText: window.I18n ? window.I18n.t('llmSettings.cancel') || window.I18n.t('common.cancel') : 'Cancel',
                  type: 'danger',
              })
            : confirm(confirmMsg);
    if (!confirmed) return;

    try {
        var result;
        if (isModelDelete) {
            result = await AppAPI.delete(
                '/api/user/api-keys/' + provider + '?model=' + encodeURIComponent(model)
            );
        } else {
            result = await window.APIKeyManager.removeKey(provider);
        }
        if (!result?.success) {
            llmShowToast(window.I18n.t('llmSettings.unbindFailed'), 'error');
            return;
        }

        var providerRemoved = !isModelDelete || result.provider_removed === true;
        if (providerRemoved && typeof window.setKeyValidity === 'function') {
            window.setKeyValidity(provider, false);
        }
        if (typeof AppStore !== 'undefined' && typeof AppStore.set === 'function') {
            AppStore.set('lastApiKeyCheck', 0);
        }
        window.dispatchEvent(new Event('apiKeyUpdated'));

        // 重新載入綁定資料（內含 renderBoundModels；使用中的被刪掉時會自然 fallback 到下一個）
        await loadSavedApiKeys();
        if (typeof window.updateLLMStatusUI === 'function') {
            await window.updateLLMStatusUI();
        }

        llmShowToast(window.I18n.t('llmSettings.unbindDone'), 'success');
    } catch (error) {
        console.error('[llmUnbindProvider] Error:', error);
        llmShowToast(window.I18n.t('llmSettings.unbindFailed'), 'error');
    }
}
window.llmUnbindProvider = llmUnbindProvider;

function updateBindingStatus() {
    var statusElement = document.getElementById('llm-binding-status');
    var modelSelect = getModelSelect();
    var provider = getSelectedProvider();
    var keyInfo = llmState.savedKeys[provider];

    if (!statusElement) return;

    if (keyInfo && keyInfo.has_key) {
        statusElement.innerHTML =
            '<span class="text-green-400">' + window.I18n.t('llmSettings.bindingStatusLabel') + '</span> <span class="text-textMuted">' +
            (keyInfo.masked_key || '') +
            '</span>';
        statusElement.classList.remove('hidden');

        if (keyInfo.model && modelSelect) {
            setTimeout(function () {
                var optionExists = Array.from(modelSelect.options).some(function (opt) {
                    return opt.value === keyInfo.model;
                });
                if (optionExists) {
                    modelSelect.value = keyInfo.model;
                    updateLLMFormState();
                }
            }, 300);
        }
        return;
    }

    statusElement.textContent = '';
    statusElement.classList.add('hidden');
}

function isKeylessProvider(provider) {
    return !!(window.APIKeyManager && typeof window.APIKeyManager.isKeylessProvider === 'function'
        ? window.APIKeyManager.isKeylessProvider(provider)
        : window.__modelConfigCache?.[provider]?.keyless);
}

// 平台模型（免金鑰）：金鑰列與 TEST 隱藏，儲存鈕變「使用此模型」且直接可按
function applyKeylessFormState(keyless) {
    var apiKeyInput = getApiKeyInput();
    var saveBtn = getSaveButton();
    var keyStatus = document.getElementById('llm-key-status');
    var bindingStatus = document.getElementById('llm-binding-status');
    var row = apiKeyInput ? apiKeyInput.parentElement : null;
    var label = row ? row.previousElementSibling : null;
    if (row) row.style.display = keyless ? 'none' : '';
    if (label && label.tagName === 'LABEL') label.style.display = keyless ? 'none' : '';
    if (keyless) {
        if (keyStatus) {
            keyStatus.textContent = window.I18n.t('llmSettings.keylessHint');
            keyStatus.classList.remove('hidden');
        }
        if (bindingStatus) bindingStatus.classList.add('hidden');
        if (saveBtn && !llmState.isSaving) {
            saveBtn.innerHTML = window.I18n.t('llmSettings.useButton');
            enableSaveButton();
        }
    } else if (saveBtn && !llmState.isSaving) {
        saveBtn.innerHTML = window.I18n.t('llmSettings.saveButton');
    }
}

function updateLLMFormState() {
    var provider = getSelectedProvider();
    var model = getSelectedLLMModel();
    var hasModel = !!model;
    var apiKeyInput = getApiKeyInput();
    var testBtn = getTestButton();
    var keyStatus = document.getElementById('llm-key-status');
    var modelHint = document.getElementById('llm-model-hint');

    if (isKeylessProvider(provider)) {
        if (modelHint) modelHint.textContent = '';
        applyKeylessFormState(true);
        return;
    }
    applyKeylessFormState(false);

    // UX 修正（2026-08-28）：金鑰輸入不再被「先選模型」綁住——provider 選定
    // 即可輸入金鑰；未選模型時後端會使用供應商預設模型（validate-key 與
    // 儲存皆支援空 model）。
    if (apiKeyInput) {
        apiKeyInput.disabled = false;
        var placeholders = {
            openai: 'sk-...',
            google_gemini: 'AIza...',
            anthropic: 'sk-ant-...',
            groq: 'gsk_...',
            openrouter: 'sk-or-...',
        };
        apiKeyInput.placeholder = placeholders[provider] || (window.I18n ? window.I18n.t('llmSettings.apiKeyPlaceholder') : 'API Key...');
    }

    if (testBtn) {
        testBtn.disabled = llmState.isTesting;
    }

    if (modelHint) {
        // 自填模型的 provider 不只 OpenRouter（nvidia / volcengine…）——提示要講
        // 當下選的那一家，否則選 NVIDIA NIM 卻被叫去輸入「OpenRouter 模型 ID」
        modelHint.textContent = hasModel
            ? (isFreeInputActive()
                ? window.I18n.t('llmSettings.hintFreeInputModelId', {
                    name: getProviderDisplayName(provider),
                })
                : '')
            : window.I18n.t('llmSettings.hintDefaultModelUsed');
    }

    var bindingStatus = document.getElementById('llm-binding-status');

    if (keyStatus) {
        if (!hasModel) {
            keyStatus.textContent = window.I18n.t('llmSettings.defaultModelHint');
        } else if (llmState.testPassed) {
            var maskedKey = maskApiKey(llmState.testPassedKey);
            keyStatus.innerHTML =
                '<span class="text-green-400">&#10003; ' + window.I18n.t('llmSettings.testPassed') + '</span> <span class="text-textMuted/60">(' + maskedKey + ')</span>';
            if (bindingStatus) {
                bindingStatus.classList.add('hidden');
            }
        } else {
            keyStatus.textContent = window.I18n.t('llmSettings.testBeforeSave');
        }
        keyStatus.classList.remove('hidden');
    }
}
window.updateLLMFormState = updateLLMFormState;

function updateLLMKeyInput() {
    var apiKeyInput = getApiKeyInput();

    if (apiKeyInput) {
        apiKeyInput.value = '';
    }

    resetLLMTestState();
    updateBindingStatus();
    updateLLMFormState();
}
window.updateLLMKeyInput = updateLLMKeyInput;

function enableSaveButton() {
    var saveBtn = getSaveButton();
    if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.classList.remove('opacity-50', 'cursor-not-allowed');
    }
}
window.enableSaveButton = enableSaveButton;

function disableSaveButton() {
    var saveBtn = getSaveButton();
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.classList.add('opacity-50', 'cursor-not-allowed');
    }
}
window.disableSaveButton = disableSaveButton;

function setSaveButtonLoading(loading) {
    var saveBtn = getSaveButton();
    if (!saveBtn) return;

    if (loading) {
        saveBtn.disabled = true;
        saveBtn.innerHTML =
            '<span class="inline-flex items-center"><svg class="animate-spin -ml-1 mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>' + window.I18n.t('llmSettings.saving') + '</span>';
    } else {
        saveBtn.innerHTML = window.I18n.t('llmSettings.saveButton');
    }

    updateLLMFormState();
}

async function testLLMKey() {
    if (llmState.isTesting) {
        return;
    }

    if (llmState.isModelsLoading) {
        llmShowToast(window.I18n.t('llmSettings.modelsLoading'), 'warning');
        return;
    }

    var provider = getSelectedProvider();
    var model = getSelectedLLMModel();
    var apiKey = getApiKeyInput()?.value?.trim() || '';
    var testBtn = getTestButton();

    if (!provider) {
        llmShowToast(window.I18n.t('llmSettings.selectProvider'), 'error');
        return;
    }

    // 模型可留空（2026-08-28 UX）：留空時後端使用供應商預設模型測試
    if (!apiKey) {
        llmShowToast(window.I18n.t('llmSettings.enterApiKeyToTest'), 'error');
        return;
    }

    llmState.isTesting = true;
    updateLLMFormState();

    if (testBtn) {
        testBtn.disabled = true;
        testBtn.innerHTML =
            '<span class="inline-flex items-center"><svg class="animate-spin -ml-1 mr-2 h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>' + window.I18n.t('llmSettings.testingBtn') + '</span>';
    }

    llmShowToast(window.I18n.t('llmSettings.testingConnection'), 'info');

    // 看門狗：無論請求為什麼沒回（AbortController 沒觸發、auth gate 卡住、
    // 分頁被凍結後 fetch 永不 settle…），時間到就一定把按鈕交還給使用者。
    // 沒有這道保險時，一旦 promise 不 settle，按鈕就永遠停在「測試中…」，
    // 連重試都按不下去（2026-09-02 回報）。
    var watchdog = setTimeout(function () {
        if (!llmState.isTesting) return;
        llmState.isTesting = false;
        resetLLMTestState();
        if (testBtn) {
            testBtn.disabled = false;
            testBtn.textContent = window.I18n.t('llmSettings.testBtn');
        }
        updateLLMFormState();
        llmShowToast(window.I18n.t('llmSettings.testFailedRetry'), 'error');
    }, 95000);

    try {
        var data = await AppAPI.post('/api/settings/validate-key', {
            provider: provider,
            model: model,
            api_key: apiKey,
            language: window.I18n ? window.I18n.getLanguage() : 'zh-TW',
        }, {
            // 後端最壞情況 = 認證探測 12s + 測試對話 60s（reasoning 模型如
            // minimax-m3 / GLM 在 NVIDIA 端點回應極不穩定）。前端需 > 後端，
            // 否則會先 abort 誤報「連線測試失敗」。給 90s 緩衝。
            // 註：模型有列在 provider 的清單裡時後端會秒回，這只是最壞情況上限。
            timeout: 90000,
        });

        if (data.valid) {
            llmState.testPassed = true;
            llmState.testPassedKey = apiKey;
            llmState.testPassedModel = model;
            enableSaveButton();
            llmShowToast(data.message || window.I18n.t('llmSettings.keyValid'), 'success');
        } else {
            resetLLMTestState();
            llmShowToast(
                data.message || data.error || data.detail || window.I18n.t('llmSettings.testFailed'),
                'error'
            );
        }
    } catch (error) {
        console.error('Test LLM key error:', error);
        resetLLMTestState();
        llmShowToast(window.I18n.t('llmSettings.testFailedRetry'), 'error');
    } finally {
        clearTimeout(watchdog);
        llmState.isTesting = false;
        if (testBtn) {
            // disabled 也要收：只還原文字的話按鈕會停在不可按的狀態
            testBtn.disabled = false;
            testBtn.textContent = window.I18n.t('llmSettings.testBtn');
        }
        updateLLMFormState();
    }
}
window.testLLMKey = testLLMKey;

async function useKeylessProvider(provider, model) {
    llmState.isSaving = true;
    setSaveButtonLoading(true);
    try {
        if (typeof window.APIKeyManager?.setSelectedProvider === 'function') {
            window.APIKeyManager.setSelectedProvider(provider);
        }
        if (typeof window.APIKeyManager?.cacheModelForProvider === 'function') {
            window.APIKeyManager.cacheModelForProvider(provider, model || '');
        }
        if (typeof AppStore !== 'undefined' && typeof AppStore.set === 'function') {
            AppStore.set('lastApiKeyCheck', 0);
        }
        window.dispatchEvent(new Event('apiKeyUpdated'));
        if (typeof checkApiKeyStatus === 'function') {
            await checkApiKeyStatus();
        }
        if (typeof window.updateLLMStatusUI === 'function') {
            await window.updateLLMStatusUI();
        }
        llmShowToast(window.I18n.t('llmSettings.keylessSelected'), 'success');
    } finally {
        llmState.isSaving = false;
        setSaveButtonLoading(false);
        updateLLMFormState();
    }
}

async function saveLLMKey() {
    if (llmState.isSaving) {
        return;
    }

    var keylessProvider = getSelectedProvider();
    if (isKeylessProvider(keylessProvider)) {
        await useKeylessProvider(keylessProvider, getSelectedLLMModel());
        return;
    }

    if (!llmState.testPassed) {
        llmShowToast(window.I18n.t('llmSettings.completeTestFirst'), 'error');
        return;
    }

    var provider = getSelectedProvider();
    var apiKey = llmState.testPassedKey;
    var model = llmState.testPassedModel; // 可為空字串 → 後端使用供應商預設模型
    var apiKeyInput = getApiKeyInput();

    if (!provider || !apiKey) {
        llmShowToast(window.I18n.t('llmSettings.allFieldsRequired'), 'error');
        return;
    }

    llmState.isSaving = true;
    setSaveButtonLoading(true);

    try {
        var data = await AppAPI.post('/api/user/api-keys', {
            provider: provider,
            model: model,
            api_key: apiKey,
        });

        if (data.success || data.ok) {
            if (apiKeyInput) {
                apiKeyInput.value = '';
            }

            resetLLMTestState();
            await loadSavedApiKeys();

            if (typeof window.setKeyValidity === 'function') {
                window.setKeyValidity(provider, true);
            }
            if (typeof window.APIKeyManager?.setSelectedProvider === 'function') {
                window.APIKeyManager.setSelectedProvider(provider);
            }
            // model 已隨 POST /api/user/api-keys 一併存進後端，這裡只需同步本地快取
            if (typeof window.APIKeyManager?.cacheModelForProvider === 'function') {
                window.APIKeyManager.cacheModelForProvider(provider, model);
            }
            if (window.APIKeyManager) {
                window.APIKeyManager.invalidateMaskedKeysCache();
            }

            // 失效 checkApiKeyStatus 的 TTL 快取（spa.js 用 lastApiKeyCheck 控制 30s TTL），
            // 確保使用者切回 chat tab 時 #no-llm-key-warning overlay 會重新檢查
            if (typeof AppStore !== 'undefined' && typeof AppStore.set === 'function') {
                AppStore.set('lastApiKeyCheck', 0);
            }

            window.dispatchEvent(new Event('apiKeyUpdated'));
            if (typeof checkApiKeyStatus === 'function') {
                await checkApiKeyStatus();
            }
            if (typeof window.updateLLMStatusUI === 'function') {
                await window.updateLLMStatusUI();
            }

            llmShowToast(window.I18n.t('llmSettings.saved'), 'success');
        } else {
            llmShowToast(
                data.detail || data.error || window.I18n.t('llmSettings.saveFailed'),
                'error'
            );
        }
    } catch (error) {
        console.error('Save LLM key error:', error);
        llmShowToast(window.I18n.t('llmSettings.saveFailedRetry'), 'error');
    } finally {
        llmState.isSaving = false;
        setSaveButtonLoading(false);
        updateLLMFormState();
    }
}
window.saveLLMKey = saveLLMKey;

function llmShowToast(message, type) {
    if (typeof window.showToast === 'function') {
        window.showToast(message, type);
        return;
    }

    console.warn('showToast is unavailable:', message, type);
}

function bindLLMSettingsEvents() {
    if (document.body?.dataset.llmSettingsBound === 'true') {
        return;
    }

    document.body.dataset.llmSettingsBound = 'true';

    document.addEventListener('change', function (event) {
        var target = event.target;
        if (!target || !target.id) return;

        if (target.id === 'llm-provider-select') {
            updateLLMKeyInput();
            if (typeof window.updateAvailableModels === 'function') {
                window.updateAvailableModels();
            }
            return;
        }

        if (target.id === 'llm-model-select') {
            resetLLMTestState();
            updateLLMFormState();
        }
    });

    document.addEventListener('input', function (event) {
        var target = event.target;
        if (!target || !target.id) return;

        if (
            target.id === 'llm-api-key-input' ||
            target.id === 'llm-model-input'
        ) {
            resetLLMTestState();
            updateLLMFormState();
        }
    });
}

document.addEventListener('DOMContentLoaded', function () {
    updateLLMKeyInput();
    disableSaveButton();
    bindLLMSettingsEvents();
    llmState.initialized = true;
    updateLLMFormState();
});

window.addEventListener('auth:ready', function () {
    loadSavedApiKeys().catch(function (error) {
        console.error('[loadSavedApiKeys] auth:ready reload failed:', error);
    });
});

window.addEventListener('auth:initialized', function (event) {
    if (!event?.detail?.isLoggedIn) {
        return;
    }

    loadSavedApiKeys().catch(function (error) {
        console.error('[loadSavedApiKeys] auth:initialized reload failed:', error);
    });
});

export {
    llmState,
    loadSavedApiKeys,
    renderBoundModels,
    llmActivateProvider,
    llmUnbindProvider,
    updateBindingStatus,
    updateLLMFormState,
    updateLLMKeyInput,
    enableSaveButton,
    disableSaveButton,
    setSaveButtonLoading,
    testLLMKey,
    saveLLMKey,
    llmShowToast,
};
