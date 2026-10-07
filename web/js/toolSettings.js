// ========================================
// toolSettings.js - AI 工具設定 UI 控制器
// ========================================

import { userFacingMessage } from './error-message.js';

const _CATEGORY_ICONS = {
    crypto_basic: 'bitcoin',
    technical: 'bar-chart-2',
    derivatives: 'activity',
    news: 'newspaper',
    onchain: 'database',
    tw_stock: 'trending-up',
    us_stock: 'dollar-sign',
    general: 'wrench',
};

function _getCategoryLabel(cat) {
    if (typeof window !== 'undefined' && window.i18next) {
        const key = 'toolSettings.categories.' + cat;
        const translated = window.i18next.t(key);
        if (translated !== key) return translated;
    }
    return cat;
}

// Fallback to API value when tools.<tool_id>.* key is missing (backend _TOOLS_SEED still returns zh).
function _getToolDisplayName(tool) {
    if (typeof window !== 'undefined' && window.I18n && tool && tool.tool_id) {
        const key = 'tools.' + tool.tool_id + '.name';
        const translated = window.I18n.t(key);
        if (translated && translated !== key) return translated;
    }
    return (tool && tool.display_name) || '';
}

function _getToolDescription(tool) {
    if (typeof window !== 'undefined' && window.I18n && tool && tool.tool_id) {
        const key = 'tools.' + tool.tool_id + '.description';
        const translated = window.I18n.t(key);
        if (translated && translated !== key) return translated;
    }
    return (tool && tool.description) || '';
}

let _currentUserTier = 'free';
let _toolSettingsLoaded = false;
let _toolSettingsRetryScheduled = false;
let _toolSettingsLoadPromise = null;
let _toolSettingsWatchdogId = null;
let _savedToolStates = {};
let _draftToolStates = {};
let _allToolsCache = [];
// 工具搜尋詞（跨 render 保留，輸入時即時過濾）
let _toolSearchQuery = '';
// 每次 render 時追蹤「已渲染過 key 輸入框的 provider」，避免同 provider 重複渲染
// （etherscan 3 工具、tavily 2 工具會造成 DOM id 重複 + 冗餘輸入框）
let _renderedKeyProviders = new Set();

const TOOL_SETTINGS_DIAGNOSTICS_KEY = 'tool_settings_diagnostics_v1';

function readToolSettingsDiagnostics() {
    try {
        const raw = sessionStorage.getItem(TOOL_SETTINGS_DIAGNOSTICS_KEY);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
    } catch (_) {
        return [];
    }
}

function writeToolSettingsDiagnostics(entries) {
    try {
        sessionStorage.setItem(
            TOOL_SETTINGS_DIAGNOSTICS_KEY,
            JSON.stringify(entries.slice(-80))
        );
    } catch (_) {}
}

function pushToolSettingsDiagnostic(event, data) {
    const entries = readToolSettingsDiagnostics();
    entries.push({
        at: new Date().toISOString(),
        event,
        data: data || {},
    });
    writeToolSettingsDiagnostics(entries);
}

window.getToolSettingsDiagnostics = function () {
    return readToolSettingsDiagnostics();
};

function _showLoginRequired(container) {
    _toolSettingsLoaded = false;
    _currentUserTier = 'free';
    container.innerHTML = `<p class="text-sm text-textMuted text-center py-4">${window.I18n.t('toolSettings.loginToView')}</p>`;
    const notice = document.getElementById('tool-settings-free-notice');
    if (notice) notice.classList.remove('hidden');
}

/**
 * 初始化工具設定頁面 — 從後端拉取工具清單並渲染
 */
async function initToolSettings() {
    let container = document.getElementById('tool-settings-list');
    if (!container) container = document.getElementById('tool-settings-modal-list');

    if (_toolSettingsLoadPromise) {
        pushToolSettingsDiagnostic('init:reuseInflight');
        return _toolSettingsLoadPromise;
    }

    _toolSettingsLoadPromise = (async () => {
        const currentUser = window.AuthManager?.currentUser || null;
        pushToolSettingsDiagnostic('init:start', {
            user_id: currentUser?.user_id || currentUser?.uid || null,
            active_tab: typeof AppStore !== 'undefined' ? AppStore.get('activeTab') : null,
        });
        const _loadingLabel = window.I18n ? window.I18n.t('common.loading') : 'Loading...';
        try {
            if (container) {
                container.innerHTML = `
                <div class="flex items-center justify-center py-8 text-textMuted">
                    <i data-lucide="loader" class="w-5 h-5 animate-spin mr-2"></i>
                    <span class="text-sm">${_loadingLabel}</span>
                </div>`;
                AppUtils?.refreshIcons?.();
            }
            clearTimeout(_toolSettingsWatchdogId);
            _toolSettingsWatchdogId = setTimeout(() => {
                if (_toolSettingsLoadPromise && container) {
                    pushToolSettingsDiagnostic('init:watchdogTimeout');
                    container.innerHTML = `
                    <div class="text-center py-4 space-y-2">
                        <p class="text-sm text-yellow-400">${window.I18n ? window.I18n.t('toolSettings.loadTimeout') : 'Load timeout'}</p>
                        <button data-click="initToolSettings" class="px-4 py-2 rounded-xl bg-primary/10 hover:bg-primary/20 text-primary text-sm font-bold transition">
                            ${window.I18n ? window.I18n.t('common.reload') : 'Reload'}
                        </button>
                    </div>`;
                }
            }, 12000);

            const data = await AppAPI.get('/api/user/tools', { timeout: 10000 });
            _currentUserTier = data.user_tier || 'free';
            _toolSettingsLoaded = true;
            _toolSettingsRetryScheduled = false;
            _allToolsCache = data.tools || [];
            _savedToolStates = {};
            _draftToolStates = {};
            (_allToolsCache).forEach((t) => {
                _savedToolStates[t.tool_id] = t.is_enabled;
                _draftToolStates[t.tool_id] = t.is_enabled;
            });
            if (container) renderToolList(container, _allToolsCache);
        } catch (err) {
            pushToolSettingsDiagnostic('init:error', {
                status: err?.status || 0,
                message: err?.message || 'unknown',
            });
            console.error('[toolSettings] fetch error:', err);
            if (err?.status === 401 || err?.status === 403) {
                const hasKnownUser =
                    !!window.AuthManager?.currentUser?.user_id ||
                    !!window.AuthManager?.currentUser?.uid;
                const loginInProgress =
                    window.__loginInFlight ||
                    (typeof AppStore !== 'undefined' && AppStore.get('loginInProgress'));

                if ((hasKnownUser || loginInProgress) && !_toolSettingsRetryScheduled) {
                    _toolSettingsRetryScheduled = true;
                    setTimeout(() => {
                        _toolSettingsRetryScheduled = false;
                        initToolSettings().catch((retryErr) =>
                            console.error('[toolSettings] delayed retry failed:', retryErr)
                        );
                    }, 600);
                    return;
                }

                if (container) _showLoginRequired(container);
                return;
            }
            if (container) {
                container.innerHTML = `
                    <div class="text-center py-4 space-y-2">
                        <p class="text-sm text-red-400">${window.I18n.t('toolSettings.loadFailedRetry')}</p>
                        <button data-click="initToolSettings" class="px-4 py-2 rounded-xl bg-primary/10 hover:bg-primary/20 text-primary text-sm font-bold transition">
                            ${window.I18n ? window.I18n.t('common.reload') : 'Reload'}
                        </button>
                    </div>`;
            }
        } finally {
            clearTimeout(_toolSettingsWatchdogId);
            _toolSettingsLoadPromise = null;
        }
    })();

    return _toolSettingsLoadPromise;
}

/**
 * 依 category 分組並渲染可折疊工具卡片
 */
let _lastRenderedTools = null;
let _lastRenderedContainer = null;

function renderToolList(container, tools) {
    _lastRenderedContainer = container;
    _lastRenderedTools = tools;
    _allToolsCache = tools;
    // 每次 render 重設：同一 provider 的 key 輸入框只渲染一次
    _renderedKeyProviders = new Set();

    if (tools.length === 0) {
        container.innerHTML = `<p class="text-sm text-textMuted text-center py-4">${window.I18n.t('toolSettings.noTools')}</p>`;
        return;
    }

    // 搜尋過濾：比對工具名 / 描述 / provider（不分大小寫）
    const q = _toolSearchQuery.trim().toLowerCase();
    const filtered = q
        ? tools.filter((t) => {
              const name = _getToolDisplayName(t).toLowerCase();
              const desc = _getToolDescription(t).toLowerCase();
              const provider = (t.key_provider || '').toLowerCase();
              const tid = (t.tool_id || '').toLowerCase();
              return (
                  name.includes(q) ||
                  desc.includes(q) ||
                  provider.includes(q) ||
                  tid.includes(q)
              );
          })
        : tools;

    // Build saved/draft states from tools data
    _savedToolStates = {};
    _draftToolStates = {};
    tools.forEach((t) => {
        _savedToolStates[t.tool_id] = t.is_enabled;
        _draftToolStates[t.tool_id] = t.is_enabled;
    });

    // Group by category (preserve insertion order from backend)
    const groups = {};
    filtered.forEach((t) => {
        if (!groups[t.category]) groups[t.category] = [];
        groups[t.category].push(t);
    });

    const searchBox = `
        <div class="sticky top-0 z-10 pb-2 mb-1 bg-background/95 backdrop-blur">
            <div class="relative">
                <i data-lucide="search" class="w-3.5 h-3.5 text-textMuted absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"></i>
                <input type="text" id="tool-search-input" value="${_toolSearchQuery.replace(/"/g, '&quot;')}"
                    class="w-full pl-9 pr-3 py-2 rounded-xl bg-background border border-borderLight text-sm text-secondary placeholder:text-textMuted/60 focus:border-primary/50 focus:outline-none transition"
                    placeholder="${window.I18n.t('toolSettings.searchPlaceholder')}"
                    autocomplete="off">
            </div>
        </div>`;

    const groupHtml = Object.entries(groups)
        .map(([cat, items], idx) => {
            const label = _getCategoryLabel(cat);
            const icon = _CATEGORY_ICONS[cat] || 'wrench';
            const rows = items.map((t) => _renderToolRow(t)).join('');
            const total = items.length;
            const enabled = items.filter((t) => {
                if (t.locked) return false;
                const reqKeyMissing = t.key_mode === 'required' && t.key_status !== 'set' && !t.has_user_key;
                return (_draftToolStates[t.tool_id] ?? t.is_enabled) && !reqKeyMissing;
            }).length;
            const catId = `tool-cat-${cat}`;
            // 搜尋時全部展開；否則第一個展開、其餘收合
            const expanded = q ? true : idx === 0;

            return `
            <div class="mb-1">
                <button data-click="toggleToolCategory" data-click-arg="${encodeURIComponent(catId)}"
                    class="w-full flex items-center justify-between px-3 py-2.5 rounded-xl hover:bg-surfaceHighlight transition group"
                    aria-expanded="${expanded}">
                    <div class="flex items-center gap-2">
                        <i data-lucide="${icon}" class="w-3.5 h-3.5 text-primary opacity-60"></i>
                        <span class="text-xs font-bold text-textMuted uppercase tracking-wider">${label}</span>
                        <span id="tool-count-${cat}" class="text-[10px] px-1.5 py-0.5 rounded-full bg-surfaceHighlight text-textMuted font-mono">${enabled}/${total}</span>
                    </div>
                    <i data-lucide="chevron-down" class="w-3.5 h-3.5 text-textMuted transition-transform duration-200 ${expanded ? 'rotate-180' : ''}"></i>
                </button>
                <div id="${catId}" class="space-y-1.5 overflow-hidden transition-all duration-200 ${expanded ? 'mt-1.5' : 'max-h-0'}">
                    ${rows}
                </div>
            </div>`;
        })
        .join('');

    const noResult =
        filtered.length === 0
            ? `<p class="text-sm text-textMuted text-center py-4">${window.I18n.t('toolSettings.noSearchResult')}</p>`
            : '';

    container.innerHTML = searchBox + groupHtml + noResult;
    AppUtils.refreshIcons();

    // 綁定搜尋框：輸入時 debounce 後重新渲染（保留游標聚焦）
    const searchInput = document.getElementById('tool-search-input');
    if (searchInput) {
        const debounceFn = window.Utils ? window.Utils.debounce : (fn) => fn;
        searchInput.addEventListener(
            'input',
            debounceFn((e) => {
                _toolSearchQuery = e.target.value;
                // 重新渲染當前 container，並還原焦點與游標位置
                renderToolList(_lastRenderedContainer, _allToolsCache);
                const restored = document.getElementById('tool-search-input');
                if (restored) {
                    restored.focus();
                    const len = restored.value.length;
                    restored.setSelectionRange(len, len);
                }
            }, 200),
        );
    }

    // Show upgrade notice for free users
    const notice = document.getElementById('tool-settings-free-notice');
    if (notice) {
        if (_currentUserTier === 'free') {
            notice.classList.remove('hidden');
        } else {
            notice.classList.add('hidden');
        }
    }
}

function _renderToolRow(tool) {
    const isPremiumLocked = tool.locked;
    const quotaBadge =
        tool.quota_type === 'shared_limited'
            ? `<span class="text-[10px] text-yellow-400/70 font-mono ml-1">${window.I18n.t('toolSettings.hasQuota')}</span>`
            : '';
    const tierBadge =
        tool.tier_required === 'premium'
            ? `<span class="text-[10px] px-1.5 py-0.5 rounded bg-yellow-400/10 text-yellow-400 font-bold ml-1">PREMIUM</span>`
            : '';

    if (isPremiumLocked) {
        // Free user sees locked premium tool — greyed out, no toggle
        return `
            <div class="flex items-center gap-3 p-3 rounded-xl bg-background/50 border border-borderSubtle opacity-50 select-none">
                <div class="flex-1 min-w-0">
                    <div class="flex items-center flex-wrap">
                        <span class="text-sm text-secondary truncate">${_getToolDisplayName(tool)}</span>
                        ${tierBadge}${quotaBadge}
                    </div>
                    <p class="text-xs text-textMuted mt-0.5 truncate">${_getToolDescription(tool)}</p>
                </div>
                <i data-lucide="lock" class="w-4 h-4 text-yellow-400/60 flex-shrink-0"></i>
            </div>`;
    }

    const hasKeyRow = tool.key_mode === 'byok' || tool.key_mode === 'required';
    // 強制金鑰工具：未設定金鑰前鎖定開關（無法開啟）
    const requiredKeyMissing =
        tool.key_mode === 'required' &&
        tool.key_status !== 'set' &&
        !tool.has_user_key;

    const checked = (_draftToolStates[tool.tool_id] ?? tool.is_enabled) && !requiredKeyMissing ? 'checked' : '';
    const canToggle = _currentUserTier === 'premium' && !requiredKeyMissing;
    const disabledAttr = canToggle ? '' : 'disabled';
    const wrapperClass = canToggle ? 'cursor-pointer' : 'cursor-not-allowed opacity-60';
    const toggleTitle = requiredKeyMissing
        ? `title="${window.I18n.t('toolSettings.requiredKeyHint')}"`
        : canToggle
          ? ''
          : `title="${window.I18n.t('toolSettings.upgradeHint')}"`;
    // 同一 provider 的 key 輸入框只渲染一次：第一個遇到的工具顯示完整輸入框，
    // 其餘同 provider 工具只顯示「共用 key」精簡狀態（避免 DOM id 重複 + 冗餘輸入框）
    let keyRow = '';
    if (hasKeyRow && tool.key_provider) {
        if (_renderedKeyProviders.has(tool.key_provider)) {
            keyRow = _renderSharedKeyRow(tool);
        } else {
            _renderedKeyProviders.add(tool.key_provider);
            keyRow = _renderToolKeyRow(tool);
        }
    }

    return `
        <div class="rounded-xl bg-background/50 border border-borderSubtle hover:border-borderLight transition">
            <div class="flex items-center gap-3 p-3">
                <div class="flex-1 min-w-0">
                    <div class="flex items-center flex-wrap">
                        <span class="text-sm text-secondary truncate">${_getToolDisplayName(tool)}</span>
                        ${tierBadge}${quotaBadge}
                    </div>
                    <p class="text-xs text-textMuted mt-0.5 truncate">${_getToolDescription(tool)}</p>
                </div>
                <label class="relative inline-flex items-center flex-shrink-0 ${wrapperClass}" ${toggleTitle}>
                    <input type="checkbox" class="sr-only peer" ${checked} ${disabledAttr}
                        data-change-action="toolPreference" data-tool-id="${encodeURIComponent(tool.tool_id)}">
                    <div class="w-10 h-6 bg-surfaceHighlight peer-focus:outline-none rounded-full peer
                        peer-checked:after:translate-x-full peer-checked:after:border-white
                        after:content-[''] after:absolute after:top-[2px] after:left-[2px]
                        after:bg-white after:border-gray-300 after:border after:rounded-full
                        after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                </label>
            </div>
            ${keyRow}
        </div>`;
}

/**
 * Provider → 申請指引對照表。讓使用者知道每個 key 去哪申請、是否免費，
 * 不再只看到「貼上 key」輸入框卻不知道 key 從哪來。
 */
const TOOL_KEY_PROVIDERS = {
    fred: { name: 'FRED', url: 'https://fred.stlouisfed.org/docs/api/api_key/', free: true },
    tavily: { name: 'Tavily', url: 'https://app.tavily.com/', free: true },
    serper: { name: 'Serper', url: 'https://serper.dev/', free: true },
    fmp: { name: 'FMP', url: 'https://site.financialmodelingprep.com/developer/docs', free: true },
    coinmarketcap: { name: 'CoinMarketCap', url: 'https://pro.coinmarketcap.com/signup', free: true },
    etherscan: { name: 'Etherscan', url: 'https://etherscan.io/myapikey', free: true },
    cryptopanic: { name: 'CryptoPanic', url: 'https://cryptopanic.com/developers/api/', free: true },
    newsapi: { name: 'NewsAPI', url: 'https://newsapi.org/register', free: true },
};

/**
 * 同一 provider 的 key 輸入框已在前一個工具渲染過時，本工具只顯示精簡狀態行：
 * 「↳ 使用 [Provider] 金鑰 [badge]」，不再重複輸入框（避免 DOM id 衝突 + 冗餘）。
 */
function _renderSharedKeyRow(tool) {
    const provider = tool.key_provider;
    const pInfo = TOOL_KEY_PROVIDERS[provider];
    const providerLabel = pInfo ? pInfo.name : provider;
    const hasKey = tool.key_status === 'set' || tool.has_user_key;
    const badge = hasKey
        ? `<span class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-400/10 text-emerald-400 font-bold">${window.I18n.t('toolSettings.keySet')}</span>`
        : `<span class="text-[10px] px-1.5 py-0.5 rounded bg-surfaceHighlight text-textMuted font-bold">${window.I18n.t('toolSettings.sharedKeyWaiting')}</span>`;
    return `
        <div class="px-3 pb-2 pt-0 -mt-1 flex items-center gap-1.5">
            <span class="text-[11px] text-textMuted">↳</span>
            <span class="text-[11px] text-textMuted">${window.I18n.t('toolSettings.sharedKeyWith', { provider: providerLabel, defaultValue: 'shares key with ' + providerLabel })}</span>
            ${badge}
        </div>`;
}

/**
 * BYOK 工具的金鑰設定子列。
 * key_mode='byok'：未設定則用免費 fallback。
 * key_mode='required'：未設定則工具停用（開關鎖定）。
 */
function _renderToolKeyRow(tool) {
    const provider = tool.key_provider;
    if (!provider) return '';

    const isRequired = tool.key_mode === 'required';
    const hasKey = tool.key_status === 'set' || tool.has_user_key;
    const pInfo = TOOL_KEY_PROVIDERS[provider];
    const providerLabel = pInfo ? pInfo.name : provider;
    const providerDesc = pInfo ? (window.I18n ? window.I18n.t('toolSettings.providerDesc.' + provider) : '') : '';
    const missingLabel = isRequired
        ? (window.I18n ? window.I18n.t('toolSettings.required') : 'Required')
        : (window.I18n ? window.I18n.t('toolSettings.notSet') : 'Not set');
    const missingClass = isRequired
        ? 'bg-amber-400/10 text-amber-400'
        : 'bg-surfaceHighlight text-textMuted';
    const statusBadge = hasKey
        ? `<span class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-400/10 text-emerald-400 font-bold">${window.I18n.t('toolSettings.keySet')}</span>`
        : `<span class="text-[10px] px-1.5 py-0.5 rounded ${missingClass} font-bold">${missingLabel}</span>`;
    const deleteBtn = hasKey
        ? `<button data-click="deleteToolKey" data-click-arg="${encodeURIComponent(provider)}"
                class="text-[11px] px-2 py-1 rounded-lg text-red-400/80 hover:bg-red-400/10 transition flex-shrink-0">${window.I18n.t('common.remove')}</button>`
        : '';
    const helpLink = pInfo ? `
            <a href="${pInfo.url}" target="_blank" rel="noopener noreferrer"
               class="inline-flex items-center gap-1 text-[11px] text-primary/70 hover:text-primary mt-1.5 transition">
                <i data-lucide="external-link" class="w-3 h-3"></i>
                ${window.I18n.t('toolSettings.getKeyLink', { name: pInfo.name, defaultValue: 'Get ' + pInfo.name + ' API key' })}${pInfo.free ? ' · ' + (window.I18n.t('toolSettings.free') || 'Free') : ''}
            </a>` : '';

    return `
        <div class="px-3 pb-3 pt-0 -mt-1">
            <div class="flex items-center gap-2 mb-1.5">
                <i data-lucide="key-round" class="w-3 h-3 text-textMuted"></i>
                <span class="text-[11px] text-textMuted">${providerLabel}${providerDesc ? ' · ' + providerDesc : ''}</span>
                ${statusBadge}
            </div>
            <div class="flex items-center gap-1.5">
                <input type="password" id="toolkey-${provider}"
                    placeholder="${hasKey ? window.I18n.t('toolSettings.enterNewKey') : window.I18n.t('toolSettings.pasteApiKey', { provider: providerLabel })}"
                    autocomplete="off"
                    class="flex-1 min-w-0 px-2.5 py-1.5 rounded-lg bg-background border border-borderLight text-xs text-secondary placeholder:text-textMuted/60 focus:border-primary/50 focus:outline-none">
                <button data-click="saveToolKey" data-click-arg="${encodeURIComponent(provider)}" data-click-element
                    class="text-[11px] px-3 py-1.5 rounded-lg bg-primary/10 hover:bg-primary/20 text-primary font-bold transition flex-shrink-0">${window.I18n.t('common.save')}</button>
                <button data-click="testToolKey" data-click-arg="${encodeURIComponent(provider)}" data-click-element
                    class="text-[11px] px-3 py-1.5 rounded-lg bg-surfaceHighlight hover:bg-surfaceHighlight text-textMuted hover:text-secondary transition flex-shrink-0">${window.I18n.t('toolSettings.test') || 'Test'}</button>
                ${deleteBtn}
            </div>
            <div id="toolkey-test-result-${provider}" class="text-[11px] mt-1.5 hidden"></div>
            ${helpLink}
        </div>`;
}

/**
 * 展開 / 折疊工具分類
 */
function toggleToolCategory(catId) {
    const panel = document.getElementById(catId);
    const btn = panel?.previousElementSibling;
    if (!panel || !btn) return;

    const isOpen = btn.getAttribute('aria-expanded') === 'true';
    if (isOpen) {
        panel.style.maxHeight = panel.scrollHeight + 'px';
        requestAnimationFrame(() => {
            panel.style.maxHeight = '0';
            panel.classList.add('mt-0');
            panel.classList.remove('mt-1.5');
        });
        btn.setAttribute('aria-expanded', 'false');
        const icon = btn.querySelector('[data-lucide="chevron-down"]');
        if (icon) icon.classList.remove('rotate-180');
    } else {
        panel.style.maxHeight = panel.scrollHeight + 'px';
        panel.classList.add('mt-1.5');
        panel.classList.remove('mt-0');
        btn.setAttribute('aria-expanded', 'true');
        const icon = btn.querySelector('[data-lucide="chevron-down"]');
        if (icon) icon.classList.add('rotate-180');
        // Remove max-height after transition
        panel.addEventListener(
            'transitionend',
            () => {
                panel.style.maxHeight = 'none';
            },
            { once: true }
        );
    }
}

/**
 * 呼叫 API 更新工具偏好（Premium 會員）
 */
async function toggleToolPreference(toolId, isEnabled, checkboxEl) {
    if (_currentUserTier !== 'premium') return;
    _draftToolStates[toolId] = isEnabled;
    _updatePendingCount();
    _updateCategoryCounts();
}

/**
 * 儲存使用者自帶的工具金鑰（BYOK）
 */
async function saveToolKey(provider, btnEl) {
    const input = document.getElementById(`toolkey-${provider}`);
    const apiKey = input?.value?.trim();
    if (!apiKey) {
        if (typeof showToast === 'function') showToast(window.I18n.t('toolSettings.enterApiKey'), 'error');
        return;
    }

    const originalText = btnEl ? btnEl.textContent : '';
    if (btnEl) {
        btnEl.disabled = true;
        btnEl.textContent = window.I18n.t('common.saving');
    }
    try {
        await AppAPI.post('/api/user/api-keys', { provider, api_key: apiKey });
        if (input) input.value = '';
        if (typeof showToast === 'function') showToast(window.I18n.t('toolSettings.keySaved'), 'success');
        await initToolSettings();
    } catch (err) {
        console.error('[toolSettings] saveToolKey error:', err);
        if (typeof showToast === 'function') showToast(window.I18n.t('toolSettings.saveFailed'), 'error');
        if (btnEl) {
            btnEl.disabled = false;
            btnEl.textContent = originalText;
        }
    }
}

/**
 * 移除使用者自帶的工具金鑰
 */
async function deleteToolKey(provider) {
    // 移除金鑰不能復原（要重新去供應商拿一把貼回來）——與 journal deleteEntry 同一確認模式
    const title = window.I18n.t('toolSettings.confirmRemoveKey', { defaultValue: 'Remove this API key?' });
    const ok = typeof window.showConfirmDialog === 'function'
        ? await window.showConfirmDialog({ title, message: String(provider || ''), danger: true })
        : window.confirm(title);
    if (!ok) return;
    try {
        await AppAPI.delete(`/api/user/api-keys/${provider}`);
        if (typeof showToast === 'function') showToast(window.I18n.t('toolSettings.keyRemoved'), 'success');
        await initToolSettings();
    } catch (err) {
        console.error('[toolSettings] deleteToolKey error:', err);
        if (typeof showToast === 'function') showToast(window.I18n.t('toolSettings.removeFailed'), 'error');
    }
}

/**
 * 測試 BYOK 金鑰是否有效。
 * 輸入框有值 → 測輸入框的；沒值 → 測已儲存的。
 */
async function testToolKey(provider, btnEl) {
    const input = document.getElementById(`toolkey-${provider}`);
    const resultEl = document.getElementById(`toolkey-test-result-${provider}`);
    const apiKey = input?.value?.trim() || null;

    const originalText = btnEl ? btnEl.textContent : '';
    if (btnEl) {
        btnEl.disabled = true;
        btnEl.textContent = window.I18n.t('toolSettings.testing') || 'Testing...';
    }
    if (resultEl) {
        resultEl.classList.remove('hidden', 'text-emerald-400', 'text-red-400', 'text-amber-400');
        resultEl.classList.add('text-textMuted');
        resultEl.textContent = window.I18n.t('toolSettings.testing') || 'Testing...';
    }

    try {
        const body = apiKey ? { api_key: apiKey } : {};
        const r = await AppAPI.post(`/api/user/api-keys/${provider}/test`, body);
        if (!resultEl) return;
        resultEl.classList.remove('text-textMuted');
        const ok = r.success;
        const latency = r.latency_ms ? ` · ${r.latency_ms}ms` : '';
        if (ok) {
            resultEl.classList.add('text-emerald-400');
            resultEl.innerHTML = `✓ ${window.I18n.t('toolSettings.testSuccess') || 'Key valid'}${latency}`;
        } else {
            const reason = r.message || (window.I18n.t('toolSettings.testFailed') || 'Key invalid');
            resultEl.classList.add('text-red-400');
            resultEl.innerHTML = `✗ ${SecurityUtils.escapeHTML(reason)}${latency}`;
        }
    } catch (err) {
        console.error('[toolSettings] testToolKey error:', err);
        if (resultEl) {
            resultEl.classList.remove('text-textMuted');
            resultEl.classList.add('text-amber-400');
            const detail = userFacingMessage(err, { fallbackKey: 'toolSettings.testError', fallback: 'Test request failed' });
            resultEl.textContent = `⚠ ${detail}`;
        }
    } finally {
        if (btnEl) {
            btnEl.disabled = false;
            btnEl.textContent = originalText;
        }
    }
}

function _updatePendingCount() {
    const countEl = document.getElementById('tool-settings-pending-count');
    if (!countEl) return;
    let pending = 0;
    Object.keys(_draftToolStates).forEach((toolId) => {
        if (_draftToolStates[toolId] !== _savedToolStates[toolId]) pending++;
    });
    if (pending > 0) {
        countEl.textContent = window.I18n.t('toolSettings.pendingChanges', { count: pending });
        countEl.classList.remove('hidden');
    } else {
        countEl.textContent = '';
        countEl.classList.add('hidden');
    }
}

function _updateCategoryCounts() {
    if (!_allToolsCache || _allToolsCache.length === 0) return;
    const groups = {};
    _allToolsCache.forEach((t) => {
        if (!groups[t.category]) groups[t.category] = [];
        groups[t.category].push(t);
    });
    Object.entries(groups).forEach(([cat, items]) => {
        const el = document.getElementById(`tool-count-${cat}`);
        if (!el) return;
        const total = items.length;
        const enabled = items.filter((t) => {
            if (t.locked) return false;
            const reqKeyMissing = t.key_mode === 'required' && t.key_status !== 'set' && !t.has_user_key;
            return (_draftToolStates[t.tool_id] ?? t.is_enabled) && !reqKeyMissing;
        }).length;
        el.textContent = `${enabled}/${total}`;
    });
}

async function openToolSettingsModal() {
    const modal = document.getElementById('tool-settings-modal');
    const modalList = document.getElementById('tool-settings-modal-list');
    if (!modal || !modalList) return;
    modal.classList.remove('hidden');

    // 如果 cache 空（init 在 modal 開啟前跑過但找不到渲染目標），重新載入
    if (!_allToolsCache || _allToolsCache.length === 0) {
        modalList.innerHTML = `<div class="flex items-center justify-center py-8 text-textMuted"><i data-lucide="loader" class="w-5 h-5 animate-spin mr-2"></i><span class="text-sm">${window.I18n ? window.I18n.t('common.loading') : 'Loading...'}</span></div>`;
        AppUtils?.refreshIcons?.();
        _toolSettingsLoaded = false; // 重置，強制重新載入
        await initToolSettings();
    }

    _draftToolStates = { ..._savedToolStates };
    renderToolList(modalList, _allToolsCache);
    _updatePendingCount();
    AppUtils?.refreshIcons?.();
}

function closeToolSettingsModal() {
    const modal = document.getElementById('tool-settings-modal');
    if (modal) modal.classList.add('hidden');
}

async function saveAllToolPreferences() {
    const btn = document.getElementById('btn-save-tool-prefs');
    if (btn) {
        btn.disabled = true;
        btn.textContent = window.I18n.t('common.saving');
    }
    let saved = 0;
    let failed = 0;
    for (const toolId of Object.keys(_draftToolStates)) {
        if (_draftToolStates[toolId] !== _savedToolStates[toolId]) {
            try {
                await AppAPI.put(`/api/user/tools/${toolId}/preference`, { is_enabled: _draftToolStates[toolId] });
                _savedToolStates[toolId] = _draftToolStates[toolId];
                saved++;
            } catch (err) {
                console.error('[toolSettings] save error:', err);
                failed++;
            }
        }
    }
    if (btn) {
        btn.disabled = false;
        btn.textContent = window.I18n.t('toolSettings.confirmSave');
    }
    closeToolSettingsModal();
    if (failed > 0) {
        if (typeof showToast === 'function') showToast(window.I18n.t('toolSettings.saveFailed'), 'error');
    } else if (saved > 0) {
        if (typeof showToast === 'function') showToast(window.I18n.t('toolSettings.updateSuccess') || window.I18n.t('common.saved'), 'success');
    }
    const container = document.getElementById('tool-settings-list');
    if (container) renderToolList(container, _allToolsCache);
}

function cancelToolPreferences() {
    _draftToolStates = { ..._savedToolStates };
    closeToolSettingsModal();
    const container = document.getElementById('tool-settings-list');
    if (container) renderToolList(container, _allToolsCache);
}

window.initToolSettings = initToolSettings;
window.toggleToolPreference = toggleToolPreference;
window.toggleToolCategory = toggleToolCategory;
window.saveToolKey = saveToolKey;
window.deleteToolKey = deleteToolKey;
window.testToolKey = testToolKey;
window.isToolSettingsLoaded = () => _toolSettingsLoaded;
window.openToolSettingsModal = openToolSettingsModal;
window.closeToolSettingsModal = closeToolSettingsModal;
window.saveAllToolPreferences = saveAllToolPreferences;
window.cancelToolPreferences = cancelToolPreferences;

window.addEventListener('auth:ready', () => {
    const container = document.getElementById('tool-settings-list');
    if (!container) return;
    initToolSettings().catch((err) =>
        console.error('[toolSettings] auth:ready reload failed:', err)
    );
});

window.addEventListener('auth:initialized', (event) => {
    const container = document.getElementById('tool-settings-list');
    if (!container) return;
    if (event?.detail?.isLoggedIn) {
        initToolSettings().catch((err) =>
            console.error('[toolSettings] auth:initialized reload failed:', err)
        );
    }
});

window.addEventListener('languageChanged', () => {
    if (_lastRenderedContainer && _lastRenderedTools) {
        renderToolList(_lastRenderedContainer, _lastRenderedTools);
    }
});

export {
    initToolSettings,
    toggleToolPreference,
    toggleToolCategory,
    saveToolKey,
    deleteToolKey,
    openToolSettingsModal,
    closeToolSettingsModal,
    saveAllToolPreferences,
    cancelToolPreferences,
};
