// ========================================
// apiKeyManager.js - 用戶 API Key 管理
// ========================================

/**
 * API Key 管理器 - 負責存儲和管理用戶的 LLM API Keys
 *
 * 🔐 安全架構 (v3):
 * - API Keys 加密儲存在後端資料庫
 * - 前端只保留 provider / 遮蔽資訊，不再取回完整後端金鑰
 * - 由後端在需要時代表使用者讀取解密後的 Key
 * - 支援離線模式降級到 localStorage
 */
const APIKeyManager = {
    // Storage keys (用於離線模式和 UI 顯示)
    STORAGE_KEYS: {
        SELECTED_PROVIDER: 'user_selected_provider',
        OFFLINE_MODE: 'api_key_offline_mode',
    },

    // 支援的 providers（與後端 PROVIDER_REGISTRY 對齊；用於遍歷已存金鑰、遮蔽與遷移）
    PROVIDERS: [
        'openai',
        'google_gemini',
        'anthropic',
        'openrouter',
        'deepseek',
        'siliconflow',
        'groq',
        'moonshot',
        'dashscope',
        'zhipu',
        'minimax',
        'volcengine',
        'nvidia',
        'local_llama', // 平台模型：放最後，有真金鑰的 provider 優先成為預設
    ],

    // 平台提供、免金鑰的 provider（與後端 MODEL_CONFIG.keyless 對齊；快取未載入時的保底）
    KEYLESS_PROVIDERS: ['local_llama'],

    isKeylessProvider(provider) {
        if (!provider) return false;
        if (this.KEYLESS_PROVIDERS.includes(provider)) return true;
        return !!window.__modelConfigCache?.[provider]?.keyless;
    },

    // 內部緩存（遮蔽版本）
    _maskedKeysCache: null,
    _lastFetchTime: 0,
    // 並發呼叫共用同一個請求：開頁時 checkApiKeyStatus／llmSettings／chat-sessions 同時問，
    // 快取還沒寫進去前每支都各打一次 /api/user/api-keys（2026-09-26 量測：一頁 6 次）
    _maskedKeysInflight: null,
    CACHE_TTL: 30000, // 30 秒

    // 金鑰有變動（存、刪、遷移、登出）一律走這裡：連進行中的請求一起丟掉，
    // 免得它晚一步帶回舊資料寫進快取、或被下一個呼叫者拿去用
    invalidateMaskedKeysCache() {
        this._maskedKeysCache = null;
        this._lastFetchTime = 0;
        this._maskedKeysInflight = null;
        this._maskedKeysOwner = undefined;
    },
    // 快取／進行中請求是替哪個使用者抓的（換帳號時才需要丟掉）
    _maskedKeysOwner: undefined,

    /**
     * 檢查是否應該使用後端儲存
     * @returns {boolean}
     */
    _shouldUseBackend() {
        const user = window.AuthManager?.currentUser;
        return !!(user && (user.user_id || user.uid));
    },

    /**
     * 獲取 API 基礎 URL
     * @returns {string}
     * @deprecated Use AppAPI directly without prepending base URL.
     *             AppAPI handles routing; other modules use bare paths like '/api/user/api-keys'.
     */
    _getApiBase() {
        return '';
    },



    /**
     * 設置 API Key（儲存到後端）
     * @param {string} provider - 'openai', 'google_gemini', etc.
     * @param {string} key - API key
     * @param {string} model - 可選的模型選擇
     */
    async setKey(provider, key, model = null) {
        if (!key || key.trim() === '') {
            return await this.removeKey(provider);
        }

        if (this._shouldUseBackend()) {
            try {
                // AppAPI.post() returns parsed JSON on success, throws on error
                await AppAPI.post('/api/user/api-keys', {
                    provider: provider,
                    api_key: key.trim(),
                    model: model,
                });

                this.invalidateMaskedKeysCache();

                if (window.DEBUG_MODE) {
                    console.log(`🔐 ${provider} API Key saved to backend (encrypted)`);
                }

                return { success: true };
            } catch (e) {
                // Silently fall back — don't spam console on expected failures
                window.APP_CONFIG?.DEBUG_MODE &&
                    console.warn('[APIKeyManager] Backend save failed, using localStorage:', e.message);
                this.invalidateMaskedKeysCache();
                return await this._setKeyLocalStorage(provider, key, model);
            }
        } else {
            // 未登入，使用 localStorage
            return await this._setKeyLocalStorage(provider, key, model);
        }
    },

    /**
     * localStorage 降級方案
     */
    async _setKeyLocalStorage(provider, key, model) {
        const encrypted = await this._encrypt(key.trim());
        localStorage.setItem(`user_${provider}_api_key`, encrypted);
        if (model) {
            localStorage.setItem(`user_${provider}_selected_model`, model);
        }
        localStorage.setItem(this.STORAGE_KEYS.OFFLINE_MODE, 'true');
        window.APP_CONFIG?.DEBUG_MODE &&
            console.log(`🔐 ${provider} API Key saved to localStorage (offline mode)`);
        return { success: true, offline: true };
    },

    async _getKeyLocalStorage(provider) {
        const stored = localStorage.getItem(`user_${provider}_api_key`);
        if (!stored || stored.trim() === '') return null;

        const decrypted = await this._decrypt(stored, `user_${provider}_api_key`);
        return decrypted && decrypted.trim() !== '' ? decrypted.trim() : null;
    },

    async _migrateLegacyLocalKeyIfNeeded(provider) {
        const localKey = localStorage.getItem(`user_${provider}_api_key`);
        if (!localKey || !localKey.startsWith('enc:')) return;

        const decrypted = await this._decrypt(localKey, `user_${provider}_api_key`);
        if (!decrypted) return;

        window.APP_CONFIG?.DEBUG_MODE &&
            console.log(`[APIKeyManager] 遷移 ${provider} key 到後端...`);
        const result = await this.setKey(provider, decrypted);
        // 後端存失敗時 setKey 會退回寫 localStorage（offline）——那份就是唯一副本，不能刪
        if (result?.success && !result.offline) {
            localStorage.removeItem(`user_${provider}_api_key`);
        }
    },

    /**
     * 獲取所有 API Key 的遮蔽版本（用於 UI 顯示）
     * @returns {Promise<Object>}
     */
    async getAllKeysMasked() {
        // 使用緩存
        const now = Date.now();
        if (this._maskedKeysCache && now - this._lastFetchTime < this.CACHE_TTL) {
            return this._maskedKeysCache;
        }

        if (this._shouldUseBackend()) {
            if (!this._maskedKeysInflight) {
                const inflight = (async () => {
                    for (const provider of this.PROVIDERS) {
                        await this._migrateLegacyLocalKeyIfNeeded(provider);
                    }
                    // AppAPI.get() returns parsed JSON directly on success, throws on error
                    const data = await AppAPI.get('/api/user/api-keys');
                    // 途中被 invalidate（剛存／刪了金鑰）：這份可能是舊的，不寫回快取
                    if (this._maskedKeysInflight === inflight) {
                        this._maskedKeysCache = data.keys;
                        this._lastFetchTime = Date.now();
                    }
                    return data.keys;
                })();
                this._maskedKeysInflight = inflight;
                const owner = window.AuthManager?.currentUser;
                this._maskedKeysOwner = (owner && (owner.user_id || owner.uid)) || null;
                inflight
                    .finally(() => {
                        if (this._maskedKeysInflight === inflight) this._maskedKeysInflight = null;
                    })
                    .catch(() => {});
            }
            // 先抓進區域變數：等待期間被 invalidate 時欄位會變 null，但這個呼叫者仍要拿到結果
            const pending = this._maskedKeysInflight;
            try {
                return await pending;
            } catch (e) {
                window.APP_CONFIG?.DEBUG_MODE &&
                    console.warn('[APIKeyManager] Failed to fetch masked keys:', e.message);
            }
        }

        // 降級：從 localStorage 構建遮蔽版本
        const result = {};
        for (const provider of this.PROVIDERS) {
            const key = await this._getKeyLocalStorage(provider);
            result[provider] = {
                has_key: !!key,
                masked_key: key ? this._maskKey(key) : null,
                model: localStorage.getItem(`user_${provider}_selected_model`),
                updated_at: null,
            };
        }
        return result;
    },

    /**
     * 移除 API Key
     * @param {string} provider
     */
    async removeKey(provider) {
        if (this._shouldUseBackend()) {
            try {
                // AppAPI.delete() returns parsed JSON on success, throws on error
                await AppAPI.delete(`/api/user/api-keys/${provider}`);

                this.invalidateMaskedKeysCache();
                if (window.DEBUG_MODE) {
                    console.log(`🗑️ ${provider} API Key removed from backend`);
                }
                return { success: true };
            } catch (e) {
                window.APP_CONFIG?.DEBUG_MODE &&
                    console.warn('[APIKeyManager] Backend delete failed:', e.message);
                this.invalidateMaskedKeysCache();
                return { success: false, error: e?.message || 'Backend delete failed' };
            }
        }

        localStorage.removeItem(`user_${provider}_api_key`);
        localStorage.removeItem(`user_${provider}_selected_model`);
        this.invalidateMaskedKeysCache();
        return { success: true };
    },

    /**
     * 檢查是否有任何 API Key
     * @returns {Promise<boolean>}
     */
    async hasAnyKey() {
        const keys = await this.getAllKeysMasked();
        return Object.values(keys).some((k) => k.has_key);
    },

    /**
     * 獲取當前選擇的 provider
     * @returns {string|null}
     */
    getSelectedProvider() {
        return localStorage.getItem(this.STORAGE_KEYS.SELECTED_PROVIDER) || null;
    },

    /**
     * 設置選擇的 provider
     *
     * 本地立即寫入（UI 即時回饋）；已登入時 fire-and-forget 同步到後端，
     * 讓選擇跨裝置帶回（否則換瀏覽器/清快取後會被 provider 清單順序綁架）。
     * 後端寫入失敗只 log，不阻塞 UI——本地狀態已正確，下次主動切換會再同步。
     *
     * @param {string} provider
     */
    setSelectedProvider(provider) {
        // 值沒變就完全跳過——避免 auth 事件反覆觸發時（loadSavedApiKeys 每次
        // 都重設同一個 activeProvider）對 /api/user/preferences/llm-provider
        // 連發 PUT，打爆 rate limit（10/min）→ 429 → refresh 失敗 → 又觸發
        // loadSavedApiKeys → 無限循環（console 出現大量 429）。
        if (provider === this.getSelectedProvider()) {
            return;
        }
        localStorage.setItem(this.STORAGE_KEYS.SELECTED_PROVIDER, provider);
        if (this._shouldUseBackend() && provider) {
            AppAPI.put('/api/user/preferences/llm-provider', { provider }).catch(
                (e) => {
                    /* fire-and-forget：失敗不影響本地選擇 */
                    console.warn('[APIKeyManager] sync selected provider failed:', e);
                }
            );
        }
    },

    /**
     * 獲取當前有效的 provider。
     * 已登入時只回傳 provider，不再把完整後端 key 帶回前端。
     */
    async getCurrentProvider() {
        let provider = this.getSelectedProvider();

        // 平台模型（CryptoMind Lite）：選了就有效，不需要金鑰
        if (provider && this.isKeylessProvider(provider)) {
            return provider;
        }

        if (this._shouldUseBackend()) {
            const keys = await this.getAllKeysMasked();
            if (provider && keys[provider]?.has_key) {
                return provider;
            }
            for (const p of this.PROVIDERS) {
                if (keys[p]?.has_key) {
                    this.setSelectedProvider(p);
                    return p;
                }
            }
            return null;
        }

        if (provider) {
            const key = await this._getKeyLocalStorage(provider);
            if (key) return provider;
        }

        for (const p of this.PROVIDERS) {
            const key = await this._getKeyLocalStorage(p);
            if (key) {
                this.setSelectedProvider(p);
                return p;
            }
        }

        return null;
    },

    /**
     * 只更新本地的模型快取（不回寫後端）。
     * 用於「資料本來就來自後端」的同步場景（如 loadSavedApiKeys），
     * 把 server 剛回傳的 model 再 POST 回去是純浪費，遇到後端故障
     * 還會在每次設定頁渲染時產生 /api/user/api-keys/model 錯誤洪水。
     * @param {string} provider
     * @param {string} model
     */
    cacheModelForProvider(provider, model) {
        if (!provider || !model) return;
        localStorage.setItem(`user_${provider}_selected_model`, model.trim());
    },

    /**
     * 設置用戶選擇的模型
     * @param {string} provider
     * @param {string} model
     */
    async setModelForProvider(provider, model) {
        if (!provider || !model) return;

        // 先保存到 localStorage（快速）
        localStorage.setItem(`user_${provider}_selected_model`, model.trim());

        // 然後同步到後端
        if (this._shouldUseBackend()) {
            try {
                await AppAPI.post('/api/user/api-keys/model', {
                    provider: provider,
                    model: model.trim(),
                });
            } catch (e) {
                window.APP_CONFIG?.DEBUG_MODE &&
                    console.warn('[APIKeyManager] Failed to save model to backend:', e.message);
            }
        }

        window.APP_CONFIG?.DEBUG_MODE &&
            console.log(`✅ ${provider} selected model saved: ${model}`);
    },

    /**
     * 獲取用戶選擇的模型
     * @param {string} provider
     * @returns {string|null}
     */
    getModelForProvider(provider) {
        if (!provider) return null;
        const model = localStorage.getItem(`user_${provider}_selected_model`);
        return model && model.trim() !== '' ? model.trim() : null;
    },

    /**
     * 清除所有 keys
     */
    async clearAll() {
        for (const provider of this.PROVIDERS) {
            await this.removeKey(provider);
        }
        localStorage.removeItem(this.STORAGE_KEYS.SELECTED_PROVIDER);
        this.invalidateMaskedKeysCache();
        if (window.DEBUG_MODE) console.log('🗑️ All API Keys cleared');
    },

    /**
     * 驗證 API Key 格式
     * @param {string} provider
     * @param {string} key
     * @returns {{valid: boolean, message: string}}
     */
    validateKeyFormat(provider, key) {
        if (!key || key.trim() === '') {
            return { valid: false, message: window.I18n.t('llmSettings.apiKeyEmpty') };
        }

        const trimmedKey = key.trim();

        if (provider === 'openai') {
            if (!trimmedKey.startsWith('sk-')) {
                return { valid: false, message: window.I18n.t('llmSettings.openaiKeyPrefix') };
            }
            if (trimmedKey.length < 40) {
                return { valid: false, message: window.I18n.t('llmSettings.openaiKeyLength') };
            }
        } else if (provider === 'google_gemini') {
            if (trimmedKey.length < 30) {
                return { valid: false, message: window.I18n.t('llmSettings.geminiKeyLength') };
            }
        } else if (provider === 'openrouter') {
            if (!trimmedKey.startsWith('sk-or-')) {
                return { valid: false, message: window.I18n.t('llmSettings.openrouterKeyPrefix') };
            }
        } else if (provider === 'anthropic') {
            if (!trimmedKey.startsWith('sk-ant-')) {
                return { valid: false, message: window.I18n.t('llmSettings.anthropicKeyPrefix') };
            }
        }

        return { valid: true, message: 'OK' };
    },

    /**
     * 遮蔽 API Key 用於顯示
     * @param {string} key
     * @returns {string}
     */
    _maskKey(key) {
        if (!key || key.length < 8) return '****';
        const prefixLen = Math.min(4, Math.floor(key.length / 4));
        const suffixLen = Math.min(4, Math.floor(key.length / 4));
        return `${key.slice(0, prefixLen)}****...****${key.slice(-suffixLen)}`;
    },

    // ========================================
    // 加密功能（用於 localStorage 降級方案）
    // ========================================

    // v3（所有新寫入）：'enc:v3:' + base64(salt16 | iv12 | ciphertext+tag)，每筆隨機 salt
    // v2（舊）：'enc:v2:' + base64(iv12 | ciphertext+tag)，固定 salt——只解不寫，讀到就改寫成 v3
    ENCRYPTION_VERSION: 'v3',
    LEGACY_V2_SALT: 'llm-key-encryption-salt-v2',

    async _getEncryptionKey(salt) {
        try {
            const user = window.AuthManager?.currentUser;
            if (!user) return null;

            const stableId = user.user_id || user.uid;
            if (!stableId) return null;

            const encoder = new TextEncoder();
            const keyMaterial = await crypto.subtle.importKey(
                'raw',
                encoder.encode(stableId),
                'PBKDF2',
                false,
                ['deriveKey']
            );

            return await crypto.subtle.deriveKey(
                {
                    name: 'PBKDF2',
                    salt,
                    iterations: 100000,
                    hash: 'SHA-256',
                },
                keyMaterial,
                { name: 'AES-GCM', length: 256 },
                false,
                ['encrypt', 'decrypt']
            );
        } catch (e) {
            return null;
        }
    },

    async _encrypt(data) {
        try {
            const salt = crypto.getRandomValues(new Uint8Array(16));
            const key = await this._getEncryptionKey(salt);
            if (!key) return data;

            const encoder = new TextEncoder();
            const iv = crypto.getRandomValues(new Uint8Array(12));
            const encrypted = await crypto.subtle.encrypt(
                { name: 'AES-GCM', iv },
                key,
                encoder.encode(data)
            );

            const combined = new Uint8Array(salt.length + iv.length + encrypted.byteLength);
            combined.set(salt);
            combined.set(iv, salt.length);
            combined.set(new Uint8Array(encrypted), salt.length + iv.length);

            return 'enc:' + this.ENCRYPTION_VERSION + ':' + btoa(String.fromCharCode(...combined));
        } catch (e) {
            return data;
        }
    },

    async _decrypt(encryptedData, storageKey = null) {
        if (!encryptedData.startsWith('enc:')) return encryptedData;

        let version = 'v1';
        let data = encryptedData.slice(4);

        if (data.startsWith('v3:') || data.startsWith('v2:')) {
            version = data.slice(0, 2);
            data = data.slice(3);
        }

        // 解不開一律回 null、原值留在 localStorage（不刪），只留一行不含金鑰的 warn
        const warnUndecryptable = () =>
            console.warn(
                `[APIKeyManager] stored key could not be decrypted (${version}); kept as-is`,
                storageKey || ''
            );

        if (version === 'v1') {
            warnUndecryptable();
            return null;
        }

        try {
            const combined = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
            const saltLen = version === 'v3' ? 16 : 0;
            const salt =
                version === 'v3'
                    ? combined.slice(0, saltLen)
                    : new TextEncoder().encode(this.LEGACY_V2_SALT);

            // 沒登入（沒有 user id 可派生金鑰）不算解密失敗，照舊回 null
            const key = await this._getEncryptionKey(salt);
            if (!key) return null;

            const iv = combined.slice(saltLen, saltLen + 12);
            const encrypted = combined.slice(saltLen + 12);

            const decrypted = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, encrypted);
            const plaintext = new TextDecoder().decode(decrypted);
            if (version === 'v2' && storageKey) {
                await this._rewriteLegacyLocalValue(storageKey, encryptedData, plaintext);
            }
            return plaintext;
        } catch (e) {
            warnUndecryptable();
            return null;
        }
    },

    /** 舊 enc:v2 解開後就地改寫成 v3；只在值沒被別處改過、且確實加密成功時才寫 */
    async _rewriteLegacyLocalValue(storageKey, legacyValue, plaintext) {
        try {
            const upgraded = await this._encrypt(plaintext);
            if (!upgraded.startsWith(`enc:${this.ENCRYPTION_VERSION}:`)) return;
            if (localStorage.getItem(storageKey) !== legacyValue) return;
            localStorage.setItem(storageKey, upgraded);
        } catch (e) {
            console.warn('[APIKeyManager] legacy local key upgrade skipped', storageKey);
        }
    },
};

// Export to global scope
window.APIKeyManager = APIKeyManager;

/**
 * 更新 Settings 頁面的 LLM 連接狀態 UI
 */
async function updateLLMStatusUI() {
    const statusBadge = document.getElementById('llm-status-badge');
    if (!statusBadge) return;

    try {
        const keys = await APIKeyManager.getAllKeysMasked();
        // 回應含工具金鑰（tavily 等），徽章只看 LLM provider
        const boundLLM = APIKeyManager.PROVIDERS.filter((p) => keys[p]?.has_key);
        const hasAny = boundLLM.length > 0;

        if (hasAny) {
            // 「使用中」的 provider：使用者選擇優先（須仍有綁定），否則第一個有綁定的
            const selected = APIKeyManager.getSelectedProvider();
            const activeProvider = boundLLM.includes(selected) ? selected : boundLLM[0];

            const providerNames = {
                openai: 'OpenAI',
                google_gemini: 'Gemini',
                anthropic: 'Anthropic',
                groq: 'Groq',
                openrouter: 'OpenRouter',
                deepseek: 'DeepSeek',
                siliconflow: 'SiliconFlow',
                moonshot: 'Kimi',
                dashscope: 'Qwen',
                zhipu: 'GLM',
                minimax: 'MiniMax',
                volcengine: '豆包',
                nvidia: 'NVIDIA',
            };
            const providerName = providerNames[activeProvider] || activeProvider;

            statusBadge.innerHTML = `
                <span class="w-2 h-2 rounded-full bg-success animate-pulse"></span>
                <span class="text-success">${providerName}</span>
            `;
            statusBadge.className =
                'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-success/10 border border-success/20';
        } else {
            statusBadge.innerHTML = `
                <span class="w-2 h-2 rounded-full bg-textMuted"></span>
                <span class="text-textMuted" data-i18n="settings.apiKeyNotConnected">Not Connected</span>
            `;
            statusBadge.className =
                'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-surfaceHighlight border border-borderLight';
        }
    } catch (e) {
        console.error('[updateLLMStatusUI] Error:', e);
    }
}

// 暴露到全域供其他模組使用
window.updateLLMStatusUI = updateLLMStatusUI;

// 頁面載入時更新狀態：僅在已登入時跑，未登入交給 auth:ready 監聽
// （gate 已保證 AppAPI 等待 init，但未登入時不必送無謂的 api-keys 請求）
document.addEventListener('DOMContentLoaded', () => {
    if (window.AuthManager && window.AuthManager.currentUser) {
        setTimeout(updateLLMStatusUI, 100);
    }
});

// 登入成功後自動刷新 Key 狀態（TON 登入 & 頁面重載恢復登入）
// 只在「快取屬於別的使用者」時才丟掉：auth:ready 一頁會發兩次，每次都丟的話
// 同一個使用者的並發請求沒辦法共用，又回到一頁打好幾次 /api/user/api-keys
['auth-success', 'auth:ready'].forEach((event) => {
    window.addEventListener(event, () => {
        const u = window.AuthManager?.currentUser;
        const uid = (u && (u.user_id || u.uid)) || null;
        if (APIKeyManager._maskedKeysOwner !== uid) {
            APIKeyManager.invalidateMaskedKeysCache();
        }
        setTimeout(updateLLMStatusUI, 400);
    });
});

export {
    APIKeyManager,
    updateLLMStatusUI,
};
