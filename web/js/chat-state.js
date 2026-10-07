// ========================================
// chat-state.js - 聊天模組共享狀態與基礎組件
// 職責：共享狀態變量、MessageComponents、基本 UI 操作
// 載入順序：必須是所有 chat-*.js 中第一個載入
// ========================================

window.currentSessionId = null;
AppStore.set('currentSessionId', null);
window.chatInitialized = false; // 防止重複初始化
AppStore.set('chatInitialized', false);

// ── Per-session analysis state ──────────────────────────────────────────────
// 每個聊天室都應該有獨立的 isAnalyzing 旗標與 AbortController，
// 這樣切換 session 時舊的串流可以繼續在背景跑、新的 session 也能立即發訊息。
// Key: sessionId, Value: { isAnalyzing: bool, controller: AbortController|null, runId: string|null }
// runId 也要 per-session：以前是全域 _activeRunId，A 還在跑、B 又開一輪，
// 回 A 按 Stop 撤銷的是 B 的 run。
const _sessionAnalysisState = new Map();

function _getAnalysisState(sessionId) {
    if (!sessionId) return null;
    if (!_sessionAnalysisState.has(sessionId)) {
        _sessionAnalysisState.set(sessionId, { isAnalyzing: false, controller: null, runId: null });
    }
    return _sessionAnalysisState.get(sessionId);
}

function getAnalysisController(sessionId = window.currentSessionId) {
    return _getAnalysisState(sessionId)?.controller || null;
}
function setAnalysisController(controller, sessionId = window.currentSessionId) {
    if (!sessionId) return;
    _getAnalysisState(sessionId).controller = controller;
}
function clearAnalysisController(sessionId = window.currentSessionId) {
    const state = _sessionAnalysisState.get(sessionId);
    if (state) state.controller = null;
}
function isSessionAnalyzing(sessionId = window.currentSessionId) {
    return _getAnalysisState(sessionId)?.isAnalyzing || false;
}
function setSessionAnalyzing(value, sessionId = window.currentSessionId) {
    if (!sessionId) return;
    _getAnalysisState(sessionId).isAnalyzing = !!value;
}
function getSessionRunId(sessionId = window.currentSessionId) {
    return _sessionAnalysisState.get(sessionId)?.runId || null;
}
function setSessionRunId(runId, sessionId = window.currentSessionId) {
    if (!sessionId) return;
    _getAnalysisState(sessionId).runId = runId || null;
}

window.getAnalysisController = getAnalysisController;
window.setAnalysisController = setAnalysisController;
window.clearAnalysisController = clearAnalysisController;
window.isSessionAnalyzing = isSessionAnalyzing;
window.setSessionAnalyzing = setSessionAnalyzing;
window.getSessionRunId = getSessionRunId;
window.setSessionRunId = setSessionRunId;

// 相容舊名：_activeRunId 讀寫的是「目前對話」的 run
if (!Object.getOwnPropertyDescriptor(window, '_activeRunId')) {
    Object.defineProperty(window, '_activeRunId', {
        configurable: true,
        get() {
            return getSessionRunId(window.currentSessionId);
        },
        set(value) {
            setSessionRunId(value, window.currentSessionId);
        },
    });
}

// isAnalyzing 改為 per-session：讀寫都針對 currentSessionId，
// 這樣舊有的 isAnalyzing = true/false 程式碼不需改動也能正確隔離每個 session。
if (!Object.getOwnPropertyDescriptor(window, 'isAnalyzing')) {
    Object.defineProperty(window, 'isAnalyzing', {
        configurable: true,
        get() {
            return isSessionAnalyzing(window.currentSessionId);
        },
        set(value) {
            setSessionAnalyzing(value, window.currentSessionId);
        },
    });
}

// ── 切換 session 時同步 UI ───────────────────────────────────────────────────
// 根據「當前 session」的 isAnalyzing 狀態，調整輸入框 / 發送鍵的外觀，
// 讓使用者切到還在背景跑分析的 session 時可以看到紅色 Stop 鈕，
// 切到沒在跑的 session 時則是正常的發送鈕。
function syncChatUIForCurrentSession() {
    const input = document.getElementById('user-input');
    const sendBtn = document.getElementById('send-btn');
    if (!input || !sendBtn) return;

    const analyzing = isSessionAnalyzing(window.currentSessionId);

    if (analyzing) {
        sendBtn.classList.remove('bg-primary', 'hover:brightness-110');
        sendBtn.classList.add('bg-red-500', 'hover:bg-red-600', 'text-white');
        sendBtn.innerHTML = '<i data-lucide="square" class="w-4 h-4 fill-current"></i>';
        input.disabled = true;
        input.classList.add('opacity-50');
    } else {
        input.disabled = false;
        input.classList.remove('opacity-50');
        sendBtn.disabled = false;
        sendBtn.classList.remove(
            'opacity-50',
            'cursor-not-allowed',
            'bg-red-500',
            'hover:bg-red-600',
            'text-white'
        );
        sendBtn.classList.add('bg-primary', 'hover:brightness-110');
        sendBtn.innerHTML = '<i data-lucide="arrow-up" class="w-5 h-5"></i>';
    }
    if (window.lucide) createIconsIn(sendBtn);
}
window.syncChatUIForCurrentSession = syncChatUIForCurrentSession;

// ✅ 效能優化：預先快取 userKey，避免每次 sendMessage 都打後端 API
let _cachedUserProvider = null;
async function getCachedUserProvider(forceRefresh = false) {
    if (!forceRefresh && _cachedUserProvider) return _cachedUserProvider;

    // 1. 先試 APIKeyManager
    _cachedUserProvider = (await window.APIKeyManager?.getCurrentProvider()) || null;

    // 2. Fallback：若 APIKeyManager 回 null，從 settings 確認 primary_model_provider
    if (!_cachedUserProvider && typeof hydrateSettingsFromBackend === 'function') {
        try {
            const hydrated = await hydrateSettingsFromBackend();
            const provider = hydrated?.settings?.primary_model_provider;
            // 全域設定可能是 BYOK 哨兵值 "user_provided"（非真 provider）。寫進
            // user_selected_provider 會毒化使用者選擇，之後被隨機 fallback 蓋掉。
            const isRealProvider =
                provider && (window.APIKeyManager?.PROVIDERS || []).includes(provider);
            if (isRealProvider) {
                if (window.APIKeyManager?.setSelectedProvider) {
                    window.APIKeyManager.setSelectedProvider(provider);
                }
                _cachedUserProvider = provider;
            }
        } catch (_) {}
    }

    // 3. 最後保底：localStorage 中有 selectedProvider 就信任它
    if (!_cachedUserProvider) {
        const saved = localStorage.getItem('selectedProvider');
        if (saved) _cachedUserProvider = saved;
    }

    return _cachedUserProvider;
}
window.getCachedUserProvider = getCachedUserProvider;

// 「設定模型」：綁模型金鑰的地方 2026-09-04 起在 AI Studio →「模型」分頁（不在 Settings）。
// AI Studio 是 lazy 注入、第一次 init 會切到總覽，所以等它 init 完再切到模型分頁。
function openModelSettings() {
    if (typeof window.switchTab === 'function') window.switchTab('ai-studio');
    let tries = 0;
    const timer = setInterval(() => {
        tries += 1;
        const studio = window.AIStudioTab;
        if (studio && studio._initialized && document.getElementById('ai-studio-models')) {
            clearInterval(timer);
            studio.showSection('models');
        } else if (tries >= 40) {
            clearInterval(timer);
        }
    }, 100);
}
window.openModelSettings = openModelSettings;
// 當 APIKeyManager 更新金鑰時，清除快取
window.addEventListener('apiKeyUpdated', () => {
    _cachedUserProvider = null;
});

function syncMobileChatViewport() {
    const root = document.documentElement;
    const vv = window.visualViewport;
    const fixedInput = document.querySelector('[data-shell-fixed-input]');
    const activeEl = document.activeElement;
    const inputFocused =
        !!fixedInput &&
        !!activeEl &&
        fixedInput.contains(activeEl) &&
        /^(INPUT|TEXTAREA)$/i.test(activeEl.tagName);

    // 記下「進入這次同步前」是否處於鍵盤開啟狀態，用於偵測「從開到收」的轉換。
    // 這個轉換是問題 1「送出當下 Thinking... 被輸入框蓋住」的關鍵時刻：鍵盤收、
    // 輸入框瞬間下移數百像素，剛插入的串流進度列若沒跟著重新貼底就會被蓋住。
    const wasKeyboardOpen = root.classList.contains('chat-keyboard-open');
    const canStick = typeof window.stickChatToBottom === 'function';
    const reanchorOnClose = () => {
        if (!canStick) return;
        // 兩段：立即一次（搶在第一個失準 frame 之前）+ 300ms 後再一次（動畫結束時
        // clientHeight 已穩定，補上時序競態漏掉的最終位置）。使用者若主動往上捲過，
        // stickChatToBottom 內部會檢查 _chatStickToBottom 旗標自行跳過，不會硬拉。
        window.stickChatToBottom();
        setTimeout(() => window.stickChatToBottom(), 300);
    };

    if (!vv || !fixedInput) {
        root.style.setProperty('--chat-keyboard-offset', '0px');
        root.classList.remove('chat-keyboard-open');
        if (wasKeyboardOpen) reanchorOnClose();
        return;
    }

    const keyboardOffset = Math.max(
        0,
        Math.round(window.innerHeight - vv.height - vv.offsetTop)
    );

    if (inputFocused && keyboardOffset > 0) {
        root.style.setProperty('--chat-keyboard-offset', `${keyboardOffset}px`);
        root.classList.add('chat-keyboard-open');
    } else {
        root.style.setProperty('--chat-keyboard-offset', '0px');
        root.classList.remove('chat-keyboard-open');
        // 鍵盤剛收：主動重新貼底。ui-shell 的 ResizeObserver 理論上會接手，但實機
        // 鍵盤收是漸進動畫（200~300ms），期間每個 frame clientHeight 都在變，
        // ResizeObserver 的 reanchor 與 stick tick（100ms）的時序競態會讓某些
        // frame 漏接，表現成「Thinking... 卡在原 scroll 位置被下移的輸入框蓋住」。
        // 這裡在偵測到收鍵盤的當下立刻補一次，並在動畫常見時長後再補一次。
        if (wasKeyboardOpen) reanchorOnClose();
    }
}

window.syncMobileChatViewport = syncMobileChatViewport;

if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', syncMobileChatViewport);
    window.visualViewport.addEventListener('scroll', syncMobileChatViewport);
}

window.addEventListener('resize', syncMobileChatViewport);
document.addEventListener('focusin', syncMobileChatViewport);
document.addEventListener('focusout', () => {
    window.setTimeout(syncMobileChatViewport, 80);
});
document.addEventListener('DOMContentLoaded', syncMobileChatViewport);

// ✅ 效能優化：scoped lucide icon 初始化，避免全頁 DOM 掃描
function createIconsIn(el) {
    if (!window.lucide || !el) return;
    window.lucide.createIcons({ nodes: Array.isArray(el) ? el : [el] });
}
window.createIconsIn = createIconsIn;

// 用於跟踪分析過程面板的展開狀態
AppStore.set('lastProcessOpenState', false);

// 編輯模式（批量刪除）
// Must be on window so chat-sessions.js (separate ES module) can access them
let isEditMode = false;
window.isEditMode = isEditMode;
let selectedSessions = new Set();
window.selectedSessions = selectedSessions;

// HITL (Human-in-the-Loop) 上下文 - 使用 Map 以避免多會話並發時的競態條件
// Key: sessionId, Value: HITL context object
const _hitlContextMap = new Map();

// Backward compatibility: expose a getter that returns context for current session
Object.defineProperty(window, '_hitlContext', {
    get() {
        return _hitlContextMap.get(window.currentSessionId);
    },
    set(value) {
        if (value === null) {
            _hitlContextMap.delete(window.currentSessionId);
        } else {
            _hitlContextMap.set(window.currentSessionId, value);
        }
    },
});
// 指定對話讀寫：問題抵達時人可能在別的對話——不能走上面以「目前對話」為鍵的
// setter（會把 A 的問題掛到 B 上）。掛在 A 自己名下，切回 A 時才重畫。
function getSessionHitlContext(sessionId) {
    return _hitlContextMap.get(sessionId) || null;
}
function setSessionHitlContext(ctx, sessionId) {
    if (!sessionId) return;
    if (ctx) _hitlContextMap.set(sessionId, ctx);
    else _hitlContextMap.delete(sessionId);
}
window.getSessionHitlContext = getSessionHitlContext;
window.setSessionHitlContext = setSessionHitlContext;
// ========================================
// MessageComponents - 結構化消息卡片組件
// ========================================
const MessageComponents = {
    /**
     * 價格卡片 - 用於顯示加密貨幣價格資訊
     */
    priceCard(symbol, data) {
        const price = data.price || data.current_price || 'N/A';
        const change = data.change_24h || data.change || 0;
        const changeClass = change >= 0 ? 'text-success' : 'text-danger';
        const changeIcon = change >= 0 ? '📈' : '📉';

        return `
            <div class="price-card bg-surface border border-borderLight rounded-2xl p-4 my-3 hover:border-primary/30 transition">
                <div class="flex justify-between items-center mb-2">
                    <div class="flex items-center gap-2">
                        <span class="text-2xl font-bold text-secondary">${symbol}</span>
                        ${data.exchange ? `<span class="text-xs text-textMuted">(${data.exchange})</span>` : ''}
                    </div>
                    <span class="${changeClass} text-sm font-medium flex items-center gap-1">
                        <span>${changeIcon}</span> ${change >= 0 ? '+' : ''}${Math.abs(change).toFixed(2)}%
                    </span>
                </div>
                <div class="text-3xl font-mono text-secondary font-bold">
                    ${typeof price === 'number' ? '$' + price.toLocaleString() : price}
                </div>
                ${
                    data.high_24h || data.low_24h
                        ? `
                <div class="flex gap-4 mt-2 text-xs text-textMuted">
                    ${data.high_24h ? `<span>H: $${data.high_24h}</span>` : ''}
                    ${data.low_24h ? `<span>L: $${data.low_24h}</span>` : ''}
                    ${data.volume_24h ? `<span>Vol: ${data.volume_24h}</span>` : ''}
                </div>
                `
                        : ''
                }
            </div>
        `;
    },

    /**
     * Market info card - displays CryptoMind related information
     */
    marketInfoCard(data) {
        const title = data.title || 'CryptoMind';
        const content = data.content || '';
        const icon = data.icon || '💎';

        return `
            <div class="ton-info-card border border-purple-500/20 bg-purple-500/5 rounded-2xl p-4 my-3">
                <div class="flex items-center gap-3 mb-3">
                    <div class="w-10 h-10 rounded-full bg-purple-500/20 flex items-center justify-center">
                        <span class="text-2xl">${icon}</span>
                    </div>
                    <div>
                        <div class="text-sm font-medium text-secondary">${title}</div>
                        <div class="text-xs text-textMuted">${window.I18n ? window.I18n.t('chat.realtimeInfo') : 'Real-time Info'}</div>
                    </div>
                </div>
                <div class="text-sm text-textMain">
                    ${content}
                </div>
            </div>
        `;
    },

    /**
     * 市場指標卡片 - 用於顯示恐懼貪婪指數等
     */
    marketIndicatorCard(data) {
        const name = data.name || (window.I18n.t('chat.indicatorFallback') || 'Indicator');
        const value = data.value || 0;
        const status = data.status || '';
        const statusClass =
            status.includes('貪婪') || status.includes('Greed')
                ? 'text-success'
                : status.includes('恐慌') || status.includes('Fear')
                  ? 'text-danger'
                  : 'text-textMain';

        return `
            <div class="market-indicator-card bg-surface border border-borderLight rounded-xl p-4 my-3">
                <div class="flex justify-between items-center mb-2">
                    <span class="text-sm text-textMuted">${name}</span>
                    <span class="text-lg font-bold ${statusClass}">${value}/100</span>
                </div>
                <div class="text-xs ${statusClass}">${status}</div>
            </div>
        `;
    },

    /**
     * Gas 費用卡片 - 用於顯示 Ethereum Gas 費用
     */
    gasFeeCard(data) {
        const low = data.low || data.safe || 'N/A';
        const average = data.average || data.propose || 'N/A';
        const high = data.high || data.fast || 'N/A';

        return `
            <div class="gas-fee-card bg-surface border border-amber-600/20 rounded-xl p-4 my-3">
                <div class="flex items-center gap-2 mb-3">
                    <span class="text-xl">⛽</span>
                    <span class="text-sm font-medium text-secondary">${window.I18n ? window.I18n.t('chat.ethGasFee') : 'Ethereum Gas Fee (Gwei)'}</span>
                </div>
                <div class="grid grid-cols-3 gap-2 text-center">
                    <div class="bg-background/50 rounded-lg p-2">
                        <div class="text-xs text-textMuted">${window.I18n ? window.I18n.t('chat.gasSlow') : '🐢 Slow'}</div>
                        <div class="text-sm font-mono text-secondary">${low}</div>
                    </div>
                    <div class="bg-background/50 rounded-lg p-2">
                        <div class="text-xs text-textMuted">${window.I18n ? window.I18n.t('chat.gasStandard') : '🚗 Standard'}</div>
                        <div class="text-sm font-mono text-secondary">${average}</div>
                    </div>
                    <div class="bg-background/50 rounded-lg p-2">
                        <div class="text-xs text-textMuted">${window.I18n ? window.I18n.t('chat.gasFast') : '🚀 Fast'}</div>
                        <div class="text-sm font-mono text-secondary">${high}</div>
                    </div>
                </div>
            </div>
        `;
    },

    /**
     * 鯨魚交易卡片 - 用於顯示大額交易
     */
    whaleTransactionCard(data) {
        const transactions = data.transactions || [];

        if (transactions.length === 0) {
            return `
                <div class="whale-card bg-surface border border-blue-500/20 rounded-xl p-4 my-3">
                    <div class="flex items-center gap-2 mb-2">
                        <span class="text-xl">🐋</span>
                        <span class="text-sm font-medium text-secondary">${window.I18n ? window.I18n.t('chat.whaleMonitoring') : 'Whale Monitoring'}</span>
                    </div>
                    <div class="text-sm text-textMuted">${window.I18n ? window.I18n.t('chat.noWhaleActivity') : 'No large whale transactions detected currently'}</div>
                </div>
            `;
        }

        let txList = transactions
            .slice(0, 5)
            .map(
                (tx) => `
            <div class="flex justify-between items-center py-2 border-b border-borderSubtle last:border-0">
                <div class="flex items-center gap-2">
                    <span class="text-xs text-textMuted font-mono">${tx.hash || 'N/A'}</span>
                </div>
                <div class="text-right">
                    <div class="text-sm font-medium ${tx.usd >= 1000000 ? 'text-amber-600' : 'text-secondary'}">$${(tx.usd || 0).toLocaleString()}</div>
                    <div class="text-xs text-textMuted">${tx.amount || ''} ${tx.symbol || ''}</div>
                </div>
            </div>
        `
            )
            .join('');

        return `
            <div class="whale-card bg-surface border border-blue-500/20 rounded-xl p-4 my-3">
                <div class="flex items-center justify-between mb-3">
                    <div class="flex items-center gap-2">
                        <span class="text-xl">🐋</span>
                        <span class="text-sm font-medium text-secondary">${window.I18n ? window.I18n.t('chat.whaleLargeTransfer') : 'Whale Large Transfer'}</span>
                    </div>
                    <span class="text-xs text-textMuted">≥ $${(data.min_value || 500000).toLocaleString()}</span>
                </div>
                <div class="divide-y divide-borderSubtle">
                    ${txList}
                </div>
            </div>
        `;
    },

    /**
     * 資金流向卡片 - 用於顯示交易所資金流
     */
    exchangeFlowCard(data) {
        const symbol = data.symbol || 'BTC';
        const flow = data.flow || 'outflow';
        const flowClass =
            flow.includes('outflow') || flow.includes('流出')
                ? 'text-success'
                : 'text-danger';
        const interpretation = data.interpretation || '';

        return `
            <div class="flow-card bg-surface border border-borderLight rounded-xl p-4 my-3">
                <div class="flex items-center justify-between mb-3">
                    <div class="flex items-center gap-2">
                        <span class="text-xl">🏦</span>
                        <span class="text-sm font-medium text-secondary">${symbol} ${window.I18n ? window.I18n.t('chat.exchangeFlow') : 'Exchange Fund Flow'}</span>
                    </div>
                    <span class="text-sm font-medium ${flowClass}">${flow}</span>
                </div>
                ${interpretation ? `<div class="text-xs text-textMuted">${interpretation}</div>` : ''}
            </div>
        `;
    },

    /**
     * 通用資訊卡片 - 用於顯示任何類型的摘要資訊
     */
    infoCard(title, content, icon = '📊') {
        return `
            <div class="info-card bg-surface border border-borderLight rounded-xl p-4 my-3">
                <div class="flex items-center gap-2 mb-2">
                    <span class="text-xl">${icon}</span>
                    <span class="text-sm font-medium text-secondary">${title}</span>
                </div>
                <div class="text-sm text-textMain">
                    ${content}
                </div>
            </div>
        `;
    },

    /**
     * 檢測內容中的結構化數據並渲染為卡片
     */
    renderStructuredContent(content, data) {
        // 如果有結構化數據，嘗試渲染卡片
        if (data && typeof data === 'object') {
            let cardsHtml = '';

            // 價格數據
            if (data.price_data || data.symbol) {
                cardsHtml += this.priceCard(data.symbol || 'BTC', data.price_data || data);
            }

            // Market data
            if (data.pi_data || data.pi_network) {
                cardsHtml += this.marketInfoCard(data.pi_data || data.pi_network);
            }

            // Gas 費用數據
            if (data.gas_fees) {
                cardsHtml += this.gasFeeCard(data.gas_fees);
            }

            // 鯨魚交易數據
            if (data.whale_transactions) {
                cardsHtml += this.whaleTransactionCard(data.whale_transactions);
            }

            // 資金流向數據
            if (data.exchange_flow) {
                cardsHtml += this.exchangeFlowCard(data.exchange_flow);
            }

            // 如果有卡片，則在內容前插入
            if (cardsHtml) {
                return cardsHtml + content;
            }
        }

        // 沒有結構化數據，直接返回原始內容
        return content;
    },
};

// 全域暴露 MessageComponents
window.MessageComponents = MessageComponents;
// isAnalyzing is declared globally in app.js
// Note: lastProcessOpenState, isEditMode, selectedSessions are already declared at the top of this file

/**
 * 建構一個 chat message row（純 DOM，不 append）。
 * 共用給 live streaming（appendMessage）跟 history loader（_buildHistoryMsgEl）使用，
 * 避免兩條路徑用不同 class / 不同結構導致 history 載入後排版跑掉。
 *
 * @returns {{ row: HTMLElement, content: HTMLElement }}
 *   row: 外層 row 元素（chat-row chat-row-{user|ai}）
 *   content: 內部訊息容器（chat-bubble-user 或 chat-content-ai prose）
 */
function buildMessageRow(role) {
    const row = document.createElement('div');
    const div = document.createElement('div');

    if (role === 'user') {
        row.className = 'chat-row chat-row-user';
        div.className = 'chat-bubble-user';
        row.appendChild(div);
    } else {
        row.className = 'chat-row chat-row-ai';
        const avatar = document.createElement('div');
        avatar.className = 'chat-avatar';
        avatar.innerHTML = '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14H9V8h2v8zm4 0h-2V8h2v8z"/></svg>';
        div.className = 'chat-content-ai prose';
        row.appendChild(avatar);
        row.appendChild(div);
    }

    return { row, content: div };
}
window.buildMessageRow = buildMessageRow;


function appendMessage(role, content) {
    const container = document.getElementById('chat-messages');

    const { row, content: div } = buildMessageRow(role);

    if (role === 'bot') {
        // BUG FIX: 檢查 md 對象是否存在且 render 方法可用
        // 防止在 markdown 庫未載入時崩潰
        // 安全修復: 使用 SecurityUtils 清理 HTML 防止 XSS
        if (typeof md !== 'undefined' && md && typeof md.render === 'function') {
            const rendered = md.render(content);
            // 使用 SecurityUtils.sanitizeHTML 清理，如果不存在則降級為 textContent
            if (typeof SecurityUtils !== 'undefined' && SecurityUtils.sanitizeHTML) {
                div.innerHTML = SecurityUtils.sanitizeHTML(rendered);
            } else {
                div.innerHTML = rendered;
                console.warn(window.I18n.t('chat.securityUtilsMissing') || '[chat] SecurityUtils 未載入，XSS 防護可能不足');
            }
        } else {
            // 降級處理：使用純文本顯示，轉義 HTML 防止 XSS
            div.textContent = content;
            console.warn(window.I18n.t('chat.markdownMissing') || '[chat] markdown 庫未載入，使用純文本顯示');
        }
        // Wrap tables in overflow-x container for proper horizontal scroll + border styling
        div.querySelectorAll('table').forEach((table) => {
            const wrapper = document.createElement('div');
            wrapper.className = 'table-wrapper';
            table.parentNode.insertBefore(wrapper, table);
            wrapper.appendChild(table);
        });
        const match = content.match(/\b([A-Z]{2,5})\b/);
        if (
            match &&
            !content.includes('Loading') &&
            !content.includes('Loading') &&
            !content.includes('Error')
        ) {
            // 安全修復: 驗證 symbol 只包含合法的大寫字母
            let symbol = match[1];
            if (!/^[A-Z]{2,5}$/.test(symbol)) {
                console.warn(window.I18n.t('chat.invalidSymbol') || '[chat] 無效的股票代號:', symbol);
                symbol = '';
            }
            if (symbol) {
                const actionsDiv = document.createElement('div');
                actionsDiv.className = 'flex gap-2 mt-4 pt-4 border-t border-borderSubtle';
                const chartBtn = document.createElement('button');
                chartBtn.className =
                    'text-xs bg-primary/10 text-primary px-3 py-1.5 rounded-full hover:bg-primary/20 border border-primary/20 transition flex items-center gap-1.5';
                chartBtn.innerHTML = '<i data-lucide="bar-chart" class="w-3 h-3"></i> Chart';
                chartBtn.onclick = () => showChart(symbol);
                actionsDiv.appendChild(chartBtn);

                div.appendChild(actionsDiv);
                setTimeout(() => createIconsIn(div), 0);
            }
        }
    } else {
        div.textContent = content;
    }

    container.appendChild(row);
    container.scrollTop = container.scrollHeight;
    return div;
}
window.appendMessage = appendMessage;

/* 側邊欄與它的背景遮罩必須一起開關。
   遮罩是 fixed inset-0 的滿版半透明黑幕，只要有人單獨關掉側邊欄、忘了收遮罩，
   畫面上就會留下一層蓋住聊天記錄的黑幕；而且點它會再呼叫 toggleSidebar，
   此時側邊欄已關閉 → 走開啟分支 → 側邊欄又滑出來。
   所以開關一律走這兩個函式，不要在別處直接動 -translate-x-full。 */
/* 手機打開抽屜時預設顯示哪一頁（2026-09-24 底部導覽列拔除後抽屜是唯一導覽）：
   在聊天頁打開多半是要切換對話 → 對話歷史；在其他分頁打開多半是要換功能 → 功能選單，
   省掉一次「切到功能選單」。 */
function sidebarTabOnOpen(activeTab) {
    return activeTab && activeTab !== 'chat' ? 'menu' : 'history';
}

function openSidebar() {
    const sidebar = document.getElementById('chat-sidebar');
    const backdrop = document.getElementById('sidebar-backdrop');
    if (!sidebar) {
        return;
    }

    // 只在手機抽屜套用；桌機 sidebar 常駐，維持使用者記住的分頁。
    // persist=false：自動切換不覆寫使用者手動選的分頁記憶。
    if (window.innerWidth < 768 && window.GlobalNav && window.GlobalNav.setSidebarTab) {
        window.GlobalNav.setSidebarTab(sidebarTabOnOpen(window.GlobalNav._activeSpaTab()), false);
    }

    sidebar.classList.remove('-translate-x-full');
    // 遮罩本身有 md:hidden，桌面版不會顯示
    if (backdrop) {
        backdrop.classList.remove('hidden');
    }
    document.getElementById('mobile-drawer-toggle')?.setAttribute('aria-expanded', 'true');
}

function closeSidebar() {
    const sidebar = document.getElementById('chat-sidebar');
    const backdrop = document.getElementById('sidebar-backdrop');
    if (!sidebar) {
        return;
    }

    sidebar.classList.add('-translate-x-full');
    if (backdrop) {
        backdrop.classList.add('hidden');
    }
    document.getElementById('mobile-drawer-toggle')?.setAttribute('aria-expanded', 'false');

    // 如果當前頁面不是活動標籤頁，則返回到活動標籤頁
    if (typeof currentActiveTab !== 'undefined' && typeof switchTab === 'function') {
        // 延遲執行，確保側邊欄動畫完成
        setTimeout(() => {
            const currentVisibleTab = document.querySelector('.tab-content:not(.hidden)');
            if (currentVisibleTab && !currentVisibleTab.id.includes(AppStore.get('activeTab'))) {
                switchTab(AppStore.get('activeTab'));
            }
        }, 150);
    }
}

function toggleSidebar() {
    const sidebar = document.getElementById('chat-sidebar');
    if (!sidebar) {
        return;
    }

    if (sidebar.classList.contains('-translate-x-full')) {
        openSidebar();
    } else {
        closeSidebar();
    }
}
window.openSidebar = openSidebar;
window.closeSidebar = closeSidebar;
window.toggleSidebar = toggleSidebar;

/* ── 串流期間維持貼底 ─────────────────────────────────────────────────────
   分析可以跑數十秒，期間進度列會持續長高。原本只在送出後捲一次到底，
   之後就不再捲，於是進度列慢慢滑到固定輸入框底下被蓋住。

   採用一般聊天介面的慣例：內容增長時自動貼底，但使用者主動往上捲（例如回頭
   看前面的訊息）就交還控制權，不要把人硬拉回底部；等他自己捲回底部附近再恢復。 */
const CHAT_STICK_THRESHOLD_PX = 80;
let _chatStickToBottom = true;
// 放掉貼底（releaseChatStickToBottom）後，捲動事件不能自己接回來——只有下一次送出訊息
// （resetChatStickToBottom）才接回。見 releaseChatStickToBottom 的說明。
let _chatStickReleased = false;

function isChatNearBottom(element, threshold = CHAT_STICK_THRESHOLD_PX) {
    if (!element) return false;
    return element.scrollHeight - element.scrollTop - element.clientHeight <= threshold;
}

function resetChatStickToBottom() {
    _chatStickReleased = false;
    _chatStickToBottom = true;
    const element = document.getElementById('chat-messages');
    if (element) delete element.dataset.stickReleased;
}

/* 放掉貼底：內容本來就要從頂端開始看的畫面（訪客首頁 hero 比一屏高）。
   不放的話上面的錨定會在內容一變就把人拉到最底。下一次送出訊息時 resetChatStickToBottom 會接回來。
   放掉期間捲動事件不改貼底狀態：hero 剛放進去、圖示與字型還沒撐開高度時，捲回頂端會被判定
   「在底部」而把貼底接回，等內容長高又被拉到底（2026-09-27 正式站實測）。 */
function releaseChatStickToBottom() {
    _chatStickReleased = true;
    _chatStickToBottom = false;
    // ui-shell.js 的底部錨定（提示條／鍵盤讓底部佔位變大時）也會把捲動區拉到底，
    // 標在元素上讓它跳過（它不 import 這個模組）
    const element = document.getElementById('chat-messages');
    if (element) element.dataset.stickReleased = '1';
}

function stickChatToBottom(force = false) {
    const element = document.getElementById('chat-messages');
    if (!element) return;
    if (!force && !_chatStickToBottom) return;
    element.scrollTop = element.scrollHeight;
}

function _attachChatStickListener() {
    const element = document.getElementById('chat-messages');
    if (!element || element.dataset.stickListenerAttached === '1') return;
    element.dataset.stickListenerAttached = '1';
    element.addEventListener(
        'scroll',
        () => {
            if (_chatStickReleased) return;
            // 我們自己捲到底時也會觸發，但那時 near-bottom 為真，狀態不變
            _chatStickToBottom = isChatNearBottom(element);
        },
        { passive: true }
    );
}

/* 貼底錨定：使用者本來在看最新的，就讓他繼續看得到最新的。

   這件事之前被拆成三套各自盯「原因」的機制，所以每次都漏掉一種原因：
   各個渲染點手寫補捲（#261 串流進度列、#267 最終渲染，chat-hitl 就漏了）、
   ui-shell 的「底部佔位變大」（#266，鍵盤與提示條）。都補完之後還是會被蓋住，
   因為還有第四種原因沒人管 —— **捲動區可用高度變小**：手機網址列出現、鍵盤彈出、
   轉向時 clientHeight 縮小，scrollTop 不動，原本貼在底部的內容就被推到輸入框
   後面。實測視窗高度少 144px，最後 128px 躲進輸入框後面，最後一行被攔腰切掉。
   （桌機測試一律在固定視窗尺寸下量，所以三次都測不出來。）

   所以這裡改成盯「結果」而不是原因：捲動區只要動了（內容變、尺寸變），本來在底部
   就重新貼底。已經在底部時重設 scrollTop 是 no-op，不必分辨是哪一種變化。
   使用者捲上去看歷史（含往上捲觸發載入更舊訊息）時 _chatStickToBottom 為 false，
   stickChatToBottom() 自己會擋掉，不會把人硬拉回來。 */
function _attachChatScrollAnchor() {
    const element = document.getElementById('chat-messages');
    if (!element || element.dataset.scrollAnchorAttached === '1') return;
    element.dataset.scrollAnchorAttached = '1';

    const reanchor = () => stickChatToBottom();

    // 內容變動：訊息長高、最終渲染附加免責聲明與耗時徽章
    if (typeof window.MutationObserver === 'function') {
        new window.MutationObserver(reanchor).observe(element, {
            childList: true,
            subtree: true,
            characterData: true,
        });
    }
    // 可用高度變動：網址列、鍵盤、轉向
    if (typeof window.ResizeObserver === 'function') {
        new window.ResizeObserver(reanchor).observe(element);
    }
    /* ResizeObserver 的回呼綁在「更新繪製」那一步，分頁沒有在繪製時不會送達；
       而手機軟鍵盤只改 visualViewport，不會觸發 window resize。兩個事件都補上，
       錨定才不會依賴某一種通知剛好有到。重複觸發沒有代價 —— 已經在底部時重設
       scrollTop 是 no-op。 */
    window.addEventListener('resize', reanchor);
    window.addEventListener('orientationchange', reanchor);
    if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', reanchor);
    }
}

function _initChatScrollAnchoring() {
    _attachChatStickListener();
    _attachChatScrollAnchor();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _initChatScrollAnchoring);
} else {
    _initChatScrollAnchoring();
}

window.isChatNearBottom = isChatNearBottom;
window.resetChatStickToBottom = resetChatStickToBottom;
window.releaseChatStickToBottom = releaseChatStickToBottom;
window.stickChatToBottom = stickChatToBottom;

export {
    getCachedUserProvider,
    createIconsIn,
    MessageComponents,
    appendMessage,
    openSidebar,
    closeSidebar,
    toggleSidebar,
    isChatNearBottom,
    resetChatStickToBottom,
    releaseChatStickToBottom,
    stickChatToBottom,
};
