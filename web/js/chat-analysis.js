// ========================================
// chat-analysis.js - 核心分析與訊息發送
// 職責：sendMessage、stopAnalysis、renderStoredBotMessage、流式輸出處理
// 依賴：chat-state.js, chat-sessions.js, chat-hitl.js
// ========================================

import { detectMessageLanguage as detectMessageLanguageFor } from './message-language.js';
import { appendShareButton } from './share-link.js';
import { userFacingMessage } from './error-message.js';


// ── Message Language Detection ───────────────────────────────────────────────
// 純函式在 message-language.js：句子看得出語言就跟句子，看不出（代號／數字／
// 一兩個英文字）就跟介面語言，不再一律回英文（2026-09-13 DANNY：回覆要跟使用者語言偏好）。
function detectMessageLanguage(text) {
    return detectMessageLanguageFor(text, window.I18n?.getLanguage?.() || 'zh-TW');
}
window.detectMessageLanguage = detectMessageLanguage;


// ── Global Helper for Button Cleanup ─────────────────────────────────────────
// ── Global Helper for Button Cleanup ─────────────────────────────────────────
function cleanupStaleButtons() {
    // Target ALL buttons within the chat container to ensure thorough cleanup
    const chatBtns = document.querySelectorAll('#chat-messages button');
    chatBtns.forEach((btn) => {
        // If the button is inside a bordered action bar (common in our cards), remove the bar.
        // Otherwise just remove the button.
        const parent = btn.closest('.flex');
        if (parent && parent.className.includes('border-t')) {
            parent.remove();
        } else {
            btn.remove();
        }
    });
}
window.cleanupStaleButtons = cleanupStaleButtons;

// ── Cross-Tab Navigation ──────────────────────────────────────────────────────
// Maps AI-resolved market names (from responseMetadata) and user keywords to tab IDs.
// label 用 key 延遲翻譯：此 map 在模組載入時求值，當時 i18n 多半尚未 init
// （i18next 未 init 的 t() 回傳原始 key）——先前直接在此求值導致 chip 顯示
// 「Go to tab.crypto tab」原文 key。改存 labelKey，_appendNavChip 渲染時翻譯。
const _MARKET_TAB_MAP = {
    crypto:    { tab: 'crypto',    labelKey: 'tab.crypto',    fallback: 'Crypto',     icon: 'bitcoin' },
    tw_stock:  { tab: 'twstock',   labelKey: 'tab.tw_stock',  fallback: 'TW Stock',   icon: 'trending-up' },
    us_stock:  { tab: 'usstock',   labelKey: 'tab.us_stock',  fallback: 'US Stock',   icon: 'bar-chart-2' },
    commodity: { tab: 'commodity', labelKey: 'tab.commodity', fallback: 'Commodity',  icon: 'package' },
    forex:     { tab: 'forex',     labelKey: 'tab.forex',     fallback: 'Forex',      icon: 'dollar-sign' },
};

// Client-side keyword patterns for instant hint (before AI response)
const _TAB_KEYWORD_RULES = [
    { tab: 'commodity', re: /黃金|貴金屬|silver|gold|xau|xag|原油|石油|oil|crude|天然氣|natural.?gas|銅|copper|大豆|小麥|玉米/i },
    { tab: 'forex',     re: /外匯|匯率|forex|usd\/|eur\/|gbp\/|jpy\/|cny\/|twd\/|美元|歐元|英鎊|日元|日幣|人民幣|澳幣/i },
    { tab: 'twstock',   re: /台股|台灣股|twse|加權指數|大盤|0050|2330|台積電|聯發科/i },
    { tab: 'usstock',   re: /美股|nasdaq|s&p\s*500|道瓊|dow\s*jones|標普|蘋果|apple|tesla|microsoft|nvda|輝達|英偉達/i },
    { tab: 'crypto',    re: /\b(btc|eth|bnb|xrp|sol|doge|usdt)\b|比特幣|以太幣|以太坊|加密貨幣|幣圈/i },
];

function _detectTabFromText(text) {
    for (const rule of _TAB_KEYWORD_RULES) {
        if (rule.re.test(text)) return rule.tab;
    }
    return null;
}

// Append a small navigation chip to a message element
function _appendNavChip(el, tabId, style = 'subtle') {
    const info = Object.values(_MARKET_TAB_MAP).find((m) => m.tab === tabId);
    if (!info || typeof switchTab !== 'function') return;
    // 旗標關閉的分頁（加密貨幣：Play 版、CRYPTO_TAB_ENABLED=false）不放「前往」按鈕——點了也進不去
    const navItem = (window.NAV_ITEMS || []).find((i) => i.id === tabId);
    if (navItem && window.NavPreferences && window.NavPreferences.isUnavailable(navItem)) return;
    // 渲染時翻譯 label（i18n 此時已 init；未 init 退 fallback，不用會回原始 key 的 t()）
    const label = window.I18n && window.I18n.isReady && window.I18n.isReady()
        ? window.I18n.t(info.labelKey)
        : info.fallback;
    const wrap = document.createElement('div');
    wrap.className = 'mt-2 flex items-center';
    if (style === 'prominent') {
        wrap.innerHTML = `<button data-click="switchTab" data-click-arg="${encodeURIComponent(info.tab)}"
            class="inline-flex items-center gap-1.5 text-xs px-4 py-1.5 rounded-full bg-primary/10 border border-primary/20 text-primary hover:bg-primary/20 transition">
            <i data-lucide="external-link" class="w-3 h-3"></i>
            ${window.I18n && window.I18n.isReady && window.I18n.isReady() ? window.I18n.t('chat.navChipProminent', { tab: label }) : 'Go to ' + label + ' tab for real-time data'}
        </button>`;
    } else {
        wrap.innerHTML = `<button data-click="switchTab" data-click-arg="${encodeURIComponent(info.tab)}"
            class="inline-flex items-center gap-1.5 text-xs px-3 py-1 rounded-full bg-surfaceHighlight border border-borderLight text-textMuted hover:text-primary hover:border-primary/30 transition">
            <i data-lucide="${info.icon}" class="w-3 h-3"></i>
            ${window.I18n && window.I18n.isReady && window.I18n.isReady() ? window.I18n.t('chat.navChip', { tab: label }) : 'Go to ' + label + ' tab'}
        </button>`;
    }
    el.appendChild(wrap);
    setTimeout(() => createIconsIn(wrap), 0);
}
window._appendNavChip = _appendNavChip;

function getSelectedUserModel(provider) {
    const savedModel = window.APIKeyManager?.getModelForProvider?.(provider);
    if (savedModel) return savedModel;

    const llmSt = window.llmState;
    if (llmSt?.savedKeys?.[provider]?.model) {
        return llmSt.savedKeys[provider].model;
    }

    const modelInput = document.getElementById('llm-model-input');
    const modelSelect = document.getElementById('llm-model-select');

    // free_input provider（openrouter / nvidia / volcengine）從文字框讀，其餘從下拉讀
    if (window.isFreeInputProvider ? window.isFreeInputProvider(provider) : provider === 'openrouter') {
        return modelInput?.value?.trim() || null;
    }

    return modelSelect?.value?.trim() || null;
}

function normalizeChatErrorMessage(message, fallback) {
    if (typeof message !== 'string') {
        return fallback;
    }

    const normalized = message.trim();
    if (!normalized) {
        return fallback;
    }

    if (
        normalized === 'Internal server error. Please try again.' ||
        normalized.toLowerCase() === 'unknown' ||
        normalized.toLowerCase() === 'error'
    ) {
        return fallback;
    }

    // 後端英文原文 → i18n（2026-08-30 UX：zh/ru 介面不再透出英文錯誤句）
    const DETAIL_MAP = {
        'Analysis service is temporarily unavailable, please try again later':
            'chat.analysisUnavailable',
        'Login required': 'chat.pleaseLogin',
        'database_initializing': 'chat.dbInitializing',
        'database_unavailable': 'chat.dbInitializing',
    };
    if (DETAIL_MAP[normalized] && window.I18n) {
        const mapped = window.I18n.t(DETAIL_MAP[normalized]);
        if (mapped && mapped !== DETAIL_MAP[normalized]) return mapped;
    }
    // 非英語介面收到純英文句子 → 用本地化泛用錯誤（保留下層診斷於 console）
    const lang = window.I18n && window.I18n.getLanguage ? window.I18n.getLanguage() : 'zh-TW';
    if (lang && !lang.startsWith('en') && /^[A-Za-z0-9 .,:'\-()]+$/.test(normalized) && normalized.length > 12) {
        return window.I18n.t ? window.I18n.t('chat.serverError') : fallback;
    }

    return normalized;
}

/* 連線中斷 vs 真正的錯誤。
   手機切換 App、螢幕關閉、Wi-Fi/行動網路切換都會讓進行中的串流連線斷掉，
   瀏覽器丟出的是 TypeError（Chrome「Failed to fetch」、Safari「Load failed」、
   部分 WebView 直接給「network error」）。這類情況伺服器端仍在跑分析，
   不該當成失敗顯示給使用者。 */
function isConnectionLostError(error) {
    if (!error) return false;
    if (error instanceof TypeError) return true;
    const message = String(error.message || '').toLowerCase();
    return (
        message.includes('failed to fetch') ||
        message.includes('network error') ||
        message.includes('networkerror') ||
        message.includes('load failed') ||
        message.includes('connection was lost') ||
        message.includes('network connection')
    );
}

/* 連線斷掉之後，分析仍在伺服器端跑並會寫進對話紀錄。
   輪詢幾次把結果取回來，讓使用者不用自己重整。 */
const BACKGROUND_RESULT_POLL_INTERVAL_MS = 5000;
const BACKGROUND_RESULT_MAX_POLLS = 60;  // 最多盯 5 分鐘

/* 續傳串流的一幀要做什麼（resync 由呼叫端先處理）。純函式，node 測試直接跑。
   hitlShown：這次續傳已畫過 HITL 卡——重播幀＋伺服器補送幀、EventSource
   自動重連都可能讓同一個問題來第二次，不再畫。
   done+waiting＝暫停等回答，必須跟一般 done 分開：一般 done 走 finish()
   （loadChatHistory 整串重繪），但問題不在對話紀錄裡，重繪會把卡片洗掉。 */
function classifyResumeFrame(data, hitlShown) {
    if (!data || typeof data !== 'object') return 'ignore';
    if (data.type === 'hitl_question') return hitlShown ? 'ignore' : 'hitl';
    if (data.type === 'token' && data.content) return 'token';
    if (data.type === 'final' && data.content) return 'final';
    if (data.done) return data.waiting ? 'paused' : 'finish';
    return 'ignore';
}

/* 斷線後從斷點續傳。
   認證走 httpOnly cookie，所以 EventSource 可用 —— 它會自動帶 cookie，
   而且自己重連時會帶 Last-Event-ID。首次重連由我們用 ?after= 指定位置。
   EventSource 不可用或建立失敗時，退回輪詢（watchForBackgroundResult）。
   hitlResumeContext：sendMessage 的 HITL 上下文——斷線期間 agent 停在同意卡時，
   續傳要畫同一張卡，使用者核准也要能照原路送回（submitHITLAnswer 靠它）。 */
function resumeAnalysisStream({ sessionId, runId, afterEventId, statusEl, contentSoFar, hitlResumeContext }) {
    if (!runId || typeof window.EventSource !== 'function') {
        watchForBackgroundResult(sessionId, runId, statusEl);
        return;
    }

    let content = contentSoFar || '';
    let hitlShown = false;
    let source;
    try {
        source = new EventSource(
            `/api/analyze/stream/${encodeURIComponent(runId)}?after=${afterEventId || 0}`
        );
    } catch (_) {
        watchForBackgroundResult(sessionId, runId, statusEl);
        return;
    }

    // 注意：cancelRender 宣告在下方，但 finish 只會被 onmessage 在函式體求值完之後
    // 呼叫，不會踩到 TDZ。
    const finish = async () => {
        try { source.close(); } catch (_) {}
        cancelRender();  // loadChatHistory 會重繪整串，待處理的 rAF 沒必要也會蓋掉結果
        if (window.currentSessionId === sessionId && typeof window.loadChatHistory === 'function') {
            await window.loadChatHistory(sessionId);
        }
    };

    // 與主串流路徑同樣走 rAF 節流：每個 token 都整段重繪會是 O(n²)。
    let renderRafId = null;
    let renderRafIsTimer = false;
    const renderNow = () => {
        renderRafId = null;
        if (!statusEl || window.currentSessionId !== sessionId) return;
        statusEl.innerHTML = window.renderStoredBotMessage
            ? window.renderStoredBotMessage(content)
            : escapeHtml(content);
        if (typeof window.stickChatToBottom === 'function') window.stickChatToBottom();
    };
    const render = () => {
        if (renderRafId !== null) return;
        renderRafIsTimer = (typeof requestAnimationFrame !== 'function');
        renderRafId = renderRafIsTimer
            ? setTimeout(renderNow, 16)
            : requestAnimationFrame(renderNow);
    };
    const cancelRender = () => {
        if (renderRafId === null) return;
        if (renderRafIsTimer) clearTimeout(renderRafId);
        else cancelAnimationFrame(renderRafId);
        renderRafId = null;
    };

    source.onmessage = (event) => {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (_) {
            return;
        }

        // 緩衝區已經蓋不到斷線位置時，伺服器一次補齊整段內容
        if (data.type === 'resync') {
            // 伺服器還在跑（reasoning 模型思考期 / 工具執行中）會回空 content。
            // 此時不可渲染空框（線上 #特斯拉案例：連線斷後 resume 拿空快照，
            // 空框卡住數分鐘直到背景分析完成）。保留既有 content 或顯示思考提示。
            const resyncContent = data.content;
            if (resyncContent && resyncContent.trim()) {
                content = resyncContent;
                render();
            } else if (!content || !content.trim()) {
                // 沒有任何累積內容 → 顯示「分析進行中」提示，不渲染空框
                cancelRender();
                if (statusEl && window.currentSessionId === sessionId) {
                    statusEl.innerHTML = `
                        <div class="process-container" style="border-style: dashed; opacity: 0.7;">
                            <div class="flex items-center gap-2 px-4 py-3">
                                <i data-lucide="loader-2" class="w-4 h-4 animate-spin text-primary"></i>
                                <span class="font-medium text-sm text-textMuted">${window.I18n ? window.I18n.t('chat.analysisContinuesInBackground') : 'Analysis in progress, please wait...'}</span>
                            </div>
                        </div>`;
                    if (window.lucide) window.lucide.createIcons({ nodes: [statusEl] });
                }
            }
            return;
        }

        const action = classifyResumeFrame(data, hitlShown);
        if (action === 'token') {
            content += data.content;
            render();
            return;
        }
        if (action === 'final') {
            content = data.content;
            render();
            return;
        }
        if (action === 'hitl') {
            if (!hitlResumeContext) return;
            const idata = data.data || {};
            // 問題掛在這個對話自己名下（不是「目前對話」）：人已切走就先不畫，
            // 切回來時 switchSession → resumePendingHitl 會重畫
            hitlResumeContext.hitlType = idata.type;
            setSessionHitlContext(hitlResumeContext, sessionId);
            if (window.currentSessionId !== sessionId) return;
            if (!statusEl || !statusEl.isConnected) {
                // 切走又切回來，這個泡泡已被 loadChatHistory 換掉：另開一個看得到的。
                // 先關掉這條——同一個 run 兩條串流，這條晚到的 finish 會再
                // loadChatHistory 一次、把剛掛好的 HITL 上下文收掉
                try { source.close(); } catch (_) {}
                resumePendingHitl(sessionId);
                return;
            }
            hitlShown = true;
            hitlResumeContext.botMsgDiv = statusEl;  // 核准後的續傳接在這張卡上
            cancelRender();  // 待處理的 rAF 會把卡片蓋回串流畫面
            // 先把畫面還原成「前言」再畫卡：斷線提示／resync 轉圈要清掉，
            // 同意卡是 appendChild，不先清會疊出第二張
            const priorHtml = content && content.trim()
                ? (window.renderStoredBotMessage ? window.renderStoredBotMessage(content, false) : escapeHtml(content))
                : '';
            statusEl.innerHTML = priorHtml;
            renderHitlQuestion(idata, statusEl, priorHtml);
            if (typeof window.stickChatToBottom === 'function') window.stickChatToBottom();
            return;
        }
        if (action === 'paused') {
            // 暫停等回答：不走 finish()——問題不在對話紀錄裡，整串重繪會把卡片洗掉
            try { source.close(); } catch (_) {}
            cancelRender();
            if (statusEl && window.currentSessionId === sessionId) {
                enterHitlPausedUI(statusEl);
            }
            return;
        }
        if (action === 'finish') {
            // run 已不在等回答（別處答了／結束了）：收掉掛著的問題，切回來不再重播
            if (hitlResumeContext && getSessionHitlContext(sessionId) === hitlResumeContext) {
                setSessionHitlContext(null, sessionId);
            }
            finish();
        }
    };

    // EventSource 自己會重試；連線真的建立不起來才退回輪詢
    let errorCount = 0;
    source.onerror = () => {
        errorCount += 1;
        if (errorCount >= 3 || source.readyState === EventSource.CLOSED) {
            try { source.close(); } catch (_) {}
            watchForBackgroundResult(sessionId, runId, statusEl);
        }
    };
    return source;
}

/* 對話停在 HITL 等回答，但卡片不在畫面上（問題抵達時人在別的對話，或切走再
   切回來、loadChatHistory 把泡泡換掉了——問題不在對話紀錄裡）。走斷線續傳同一
   條路重畫：伺服器對 waiting 的 run 會重播前言＋補送問題、以 done+waiting 收尾；
   run 已經不在等（別處答了）就照一般 finish 載入結果。 */
const _hitlReplays = new Map();  // sessionId → { source, statusEl }：每個對話最多一條重播串流
function resumePendingHitl(sessionId) {
    const ctx = getSessionHitlContext(sessionId);
    if (!ctx || !ctx.runId || window.currentSessionId !== sessionId) return;
    if (ctx.botMsgDiv && ctx.botMsgDiv.isConnected) return;  // 卡片還在畫面上
    // 快速來回切換會連叫好幾次：上一條還開著且泡泡還在畫面上就不再開；泡泡已被
    // 換掉就先關掉它——同一個 run 兩條串流，舊的那條的 finish 會多 loadChatHistory 一次
    const prev = _hitlReplays.get(sessionId);
    if (prev) {
        const open = prev.source && prev.source.readyState !== 2;  // 2 = EventSource.CLOSED
        if (open && prev.statusEl.isConnected) return;
        try { if (prev.source) prev.source.close(); } catch (_) {}
        _hitlReplays.delete(sessionId);
    }
    const statusEl = appendMessage('bot', '');
    ctx.botMsgDiv = statusEl;
    const source = resumeAnalysisStream({
        sessionId,
        runId: ctx.runId,
        afterEventId: 0,
        statusEl,
        contentSoFar: '',
        hitlResumeContext: ctx,
    });
    if (source) _hitlReplays.set(sessionId, { source, statusEl });
}
window.resumePendingHitl = resumePendingHitl;

function watchForBackgroundResult(sessionId, runId, statusEl) {
    if (!sessionId || typeof window.loadChatHistory !== 'function') return;

    let attempts = 0;

    // 有 run_id 時可以拿到當下的部分輸出，讓使用者看到進度而不是乾等
    const showPartial = (content) => {
        if (!statusEl || !content) return;
        if (window.currentSessionId !== sessionId) return;
        statusEl.innerHTML =
            `<span class="text-amber-400 text-xs">${escapeHtml(
                window.I18n
                    ? window.I18n.t('chat.analysisContinuesInBackground')
                    : 'Connection dropped. The analysis is still running.'
            )}</span><div class="mt-2 opacity-70">${escapeHtml(content)}</div>`;
    };

    const poll = async () => {
        attempts += 1;
        // 使用者已經切到別的對話就不要打斷他
        if (window.currentSessionId !== sessionId) return;

        try {
            if (runId) {
                const run = await AppAPI.get(
                    `/api/analyze/status/${encodeURIComponent(runId)}`
                );
                if (run && run.success) {
                    if (run.status === 'completed') {
                        await window.loadChatHistory(sessionId);
                        return;
                    }
                    if (run.status === 'error' || run.status === 'timeout' || run.status === 'cancelled') {
                        // 伺服器端已結束且沒有結果，不用再等
                        await window.loadChatHistory(sessionId);
                        return;
                    }
                    showPartial(run.content);
                }
            } else {
                // 沒拿到 run_id（連線在 run_started 之前就斷了）→ 退回看對話紀錄
                const data = await AppAPI.get(
                    `/api/chat/history?session_id=${encodeURIComponent(sessionId)}`
                );
                const history = (data && data.history) || [];
                const last = history[history.length - 1];
                if (last && last.role === 'assistant') {
                    await window.loadChatHistory(sessionId);
                    return;
                }
            }
        } catch (_) {
            // 網路還沒恢復，或 run 已過期，下一輪再試
        }

        if (attempts < BACKGROUND_RESULT_MAX_POLLS) {
            window.setTimeout(poll, BACKGROUND_RESULT_POLL_INTERVAL_MS);
        }
    };

    window.setTimeout(poll, BACKGROUND_RESULT_POLL_INTERVAL_MS);
}
window.watchForBackgroundResult = watchForBackgroundResult;
window.isConnectionLostError = isConnectionLostError;

// 回答下方的「由哪個模型回答」（後端 response_metadata.model_label／歷史 metadata.model_label；
// 平台模型只會是品牌名）。textContent 等效跳脫，名稱來自後端但仍不信任
function renderModelLabel(label) {
    if (!label) return '';
    const esc = window.escapeHtml ? window.escapeHtml(String(label)) : String(label).replace(/[&<>"']/g, '');
    let text = window.I18n ? window.I18n.t('chat.answeredBy', { model: esc }) : '';
    if (!text || text === 'chat.answeredBy') text = `Answered by ${esc}`;
    return `<span class="chat-model-label min-w-0 truncate ml-3" title="${esc}">${text}</span>`;
}
window.renderModelLabel = renderModelLabel;

function renderResponseMetadata(metadata = {}) {
    // Trustworthy AI HITL 場景 B：詐騙判定證據鏈卡片
    const ev = metadata && metadata.scam_evidence;
    if (!ev || !ev.requires_ack) return '';

    const t = (k, fallback) => (window.I18n ? window.I18n.t(`chat.scamEvidence.${k}`, fallback) : fallback);
    const esc = (s) => (window.escapeHtml ? window.escapeHtml(String(s ?? '')) : String(s ?? ''));

    // verdict → 顏色 + 標籤
    const verdictStyle = {
        high_risk: { color: 'text-danger', border: 'border-danger/40', bg: 'bg-danger/10', label: t('verdictHighRisk', 'High risk') },
        trusted_with_permissions: { color: 'text-amber-400', border: 'border-amber-400/40', bg: 'bg-amber-400/10', label: t('verdictTrustedWithPermissions', 'Trusted, but with permission risks') },
        warning: { color: 'text-amber-400', border: 'border-amber-400/40', bg: 'bg-amber-400/10', label: t('verdictWarning', 'Risk signals present') },
    };
    const vs = verdictStyle[ev.verdict] || verdictStyle.warning;

    // 加權明細
    const breakdownHtml = (ev.breakdown || []).map((b) => {
        const w = b.weight;
        const wClass = w > 0 ? 'text-danger' : 'text-success';
        const wSign = w > 0 ? `+${w}` : `${w}`;
        return `<li class="flex items-start gap-2">
            <span class="${wClass} font-mono font-bold flex-shrink-0">${wSign}</span>
            <span class="text-textMuted">${esc(b.reason)}</span>
        </li>`;
    }).join('');

    // confirm 按鈕：帶 verdict 給 click handler；session_id 由 handler 從全域讀
    const btnArgs = encodeURIComponent(JSON.stringify([ev.verdict]));

    return `
    <div class="mt-4 rounded-2xl border ${vs.border} ${vs.bg} overflow-hidden" data-scam-evidence="${esc(ev.verdict)}">
        <div class="px-5 py-4">
            <div class="flex items-center gap-3 mb-3">
                <i data-lucide="shield-alert" class="w-5 h-5 ${vs.color} flex-shrink-0"></i>
                <h4 class="text-sm font-bold ${vs.color}">${t('title', 'Scam verdict evidence chain')}</h4>
            </div>
            <div class="flex items-center gap-4 mb-3 text-xs">
                <div>
                    <span class="text-textMuted">${t('riskLevel', 'Risk level')}:</span>
                    <span class="font-bold ${vs.color}">${esc(vs.label)}</span>
                </div>
                <div>
                    <span class="text-textMuted">${t('confidence', 'Confidence')}:</span>
                    <span class="font-bold font-mono">${esc(ev.confidence)}/100</span>
                </div>
            </div>
            ${breakdownHtml ? `
            <ul class="space-y-1.5 mb-3 text-xs">
                ${breakdownHtml}
            </ul>
            ` : ''}
            <div class="text-xs text-textMuted mb-3 italic">
                ${t('backendEvidenceNote', '⚠️ Backend evidence (GoPlus data), not investment advice. Assess the risk yourself.')}
            </div>
            <button data-click="confirmScamVerdict" data-click-args="${btnArgs}"
                class="w-full py-2.5 rounded-xl bg-primary hover:bg-primary/80 text-background font-bold text-sm transition">
                ${t('confirmButton', 'I have read the risks')}
            </button>
        </div>
    </div>`;
}
window.renderResponseMetadata = renderResponseMetadata;

/**
 * Trustworthy AI HITL 場景 B：使用者確認已閱讀詐騙判定風險。
 * 由證據卡片的「我已閱讀風險」按鈕觸發（data-click-args 帶 [verdict]）。
 * 後端用 session_id 反查最新 scam_evidence 訊息驗證，不信任 client 傳值。
 */
async function confirmScamVerdict(verdict) {
    const sessionId = window.currentSessionId;
    if (!sessionId) return;
    const btn = event && event.currentTarget;
    try {
        if (btn) {
            btn.disabled = true;
            btn.classList.add('opacity-50', 'cursor-not-allowed');
        }
        const res = await AppAPI.post('/api/chat/scam-confirm', {
            session_id: sessionId,
            verdict: verdict,
        });
        if (res && res.success && window.showToast) {
            const msg = window.I18n
                ? window.I18n.t('chat.scamEvidence.confirmed', 'Your risk acknowledgement has been recorded')
                : 'Your risk acknowledgement has been recorded';
            window.showToast(msg, 'success');
        }
    } catch (e) {
        if (btn) {
            btn.disabled = false;
            btn.classList.remove('opacity-50', 'cursor-not-allowed');
        }
        if (window.showToast) window.showToast(userFacingMessage(e), 'error');
    }
}
window.confirmScamVerdict = confirmScamVerdict;

function normalizeRenderedChatContent(rawContent) {
    if (typeof rawContent !== 'string') {
        return rawContent;
    }

    const trimmed = rawContent.trim();
    if (!trimmed) {
        return rawContent;
    }

    const directPatterns = [
        /["'](?:final_answer|answer|response|content|message|output_text|text)["']\s*:\s*["']([\s\S]*?)["']\s*(?:,|})/,
        /```(?:json)?\s*([\s\S]*?)```/i,
    ];

    for (const pattern of directPatterns) {
        const match = trimmed.match(pattern);
        if (match && match[1]) {
            const candidate = match[1]
                .replace(/\\n/g, '\n')
                .replace(/\\"/g, '"')
                .replace(/\\'/g, "'")
                .trim();
            if (candidate && candidate !== trimmed) {
                return candidate;
            }
        }
    }

    if (
        (trimmed.startsWith('{') || trimmed.startsWith('[')) &&
        (trimmed.includes("'metadata'") ||
            trimmed.includes('"metadata"') ||
            trimmed.includes("'raw'") ||
            trimmed.includes('"raw"'))
    ) {
        return window.I18n
        ? window.I18n.t('chat.unexpectedRawOutput')
        : window.I18n ? window.I18n.t('chat.unexpectedRawOutput') : 'Unexpected raw output. Try again.';
    }

    return rawContent;
}
window.normalizeRenderedChatContent = normalizeRenderedChatContent;


/* ── 訪客模式 ──────────────────────────────────────────────────────────────
   未登入使用者的 AI 體驗：POST /api/guest/analyze（每日限量、平台免費模型）。
   回應直接渲染進現有的 bot 訊息框（botMsgDiv 由 sendMessage 事先建立）。 */

// 訪客多輪（上市準備 PR-4）：只存在本頁記憶體，重新整理就清空、伺服器也不存。
// 每一輪綁著它的 bot 泡泡——「新對話」重畫 #chat-messages 後舊泡泡離開 DOM，
// 對應的歷史跟著丟。上限與後端驗證一致（超過會 422）。
const GUEST_HISTORY_MAX = 6;
const GUEST_HISTORY_CHARS = 2000;
let guestTurns = []; // [{ user, assistant, el }]
let guestSoftCtaShown = false; // 第一個成功回答後的小卡，每次載入頁面只出一次

function _clipGuestText(value) {
    // 以 code point 截斷（後端 max_length 算的是字元，不是 UTF-16 單位）
    return Array.from(String(value || '')).slice(0, GUEST_HISTORY_CHARS).join('');
}

function _guestHistoryPayload() {
    guestTurns = guestTurns.filter((turn) => turn.el && turn.el.isConnected);
    const messages = [];
    guestTurns.forEach((turn) => {
        messages.push({ role: 'user', content: _clipGuestText(turn.user) });
        messages.push({ role: 'assistant', content: _clipGuestText(turn.assistant) });
    });
    return messages.filter((m) => m.content.trim()).slice(-GUEST_HISTORY_MAX);
}

async function sendGuestMessage(text, botMsgDiv) {
    if (!botMsgDiv) return;
    botMsgDiv.innerHTML = `
        <div class="process-container" style="border-style: dashed; opacity: 0.7;">
            <div class="flex items-center gap-2 px-4 py-3">
                <i data-lucide="loader-2" class="w-4 h-4 animate-spin text-primary"></i>
                <span class="font-medium text-sm text-textMuted">${window.I18n ? window.I18n.t('chat.guestThinking') : 'Thinking (guest mode)...'}</span>
            </div>
        </div>`;
    if (window.lucide) window.lucide.createIcons({ nodes: [botMsgDiv] });

    let resp;
    try {
        resp = await fetch('/api/guest/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
                message: text,
                language: detectMessageLanguage(text),
                history: _guestHistoryPayload(),
            }),
        });
    } catch (err) {
        botMsgDiv.textContent = window.I18n
            ? window.I18n.t('chat.networkError')
            : 'Network error, please retry.';
        return;
    }

    if (resp.status === 429) {
        // 達每日限量 → 升級卡（連錢包解鎖）
        botMsgDiv.innerHTML = window.SECURE_GUEST_CARD
            ? window.SECURE_GUEST_CARD()
            : (window.I18n ? window.I18n.t('chat.guestLimitReached') : 'Guest daily limit reached — connect your wallet for unlimited analysis.');
        _appendConnectCta(botMsgDiv);
        return;
    }
    if (resp.status === 503) {
        botMsgDiv.textContent = window.I18n
            ? window.I18n.t('chat.guestUnavailable')
            : 'Guest mode is temporarily unavailable — connect a wallet to use your own API key.';
        _appendConnectCta(botMsgDiv);
        return;
    }
    if (!resp.ok) {
        let detail = '';
        try { const j = await resp.json(); detail = j.detail || ''; } catch {}
        botMsgDiv.textContent = detail || (window.I18n ? window.I18n.t('chat.serverError', { status: resp.status }) : `Server error (${resp.status})`);
        return;
    }

    let data;
    try { data = await resp.json(); } catch {
        botMsgDiv.textContent = (window.I18n ? window.I18n.t('chat.unexpectedResponse') : 'Unexpected response.');
        return;
    }

    // 與主流程同款 markdown 渲染 + XSS 清理。
    // 部分模型回覆會帶 <p>/<br> HTML 標籚，markdown 渲染後會以原文露出 → 先清掉
    const cleanReply = String(data.reply || '')
        .replace(/<br\s*\/?>/gi, '\n')
        .replace(/<\/?p>/gi, '');

    // XSS 安全說明：markdown-it 預設 html:false 會把殘留的原始 HTML 逃逸成
    // 文字（模型注入的 <script> 等不會執行），輸出只剩 md 自產的白名單標籤；
    // javascript: 連結也被 md 的 validateLink 預設擋掉。因此不做
    // sanitizeHTML 二次加工 —— 它是全逃逸型，會把 md 的 <p>/<br> 也變成
    // 字面文字（線上實測）。模型夾帶的 <p>/<br> 已在 cleanReply 清除。
    if (typeof md !== 'undefined' && md && typeof md.render === 'function') {
        botMsgDiv.innerHTML = md.render(cleanReply);
    } else {
        botMsgDiv.textContent = cleanReply;
    }

    // 剩餘額度提示（訪客權益可見化）
    if (typeof data.remaining === 'number') {
        const hint = document.createElement('div');
        hint.className = 'mt-3 text-xs text-textMuted flex items-center gap-1.5';
        hint.innerHTML = `<i data-lucide="zap" class="w-3 h-3"></i>
            ${window.I18n ? window.I18n.t('chat.guestQuota', { remaining: data.remaining, limit: data.limit }) : `Guest mode: ${data.remaining}/${data.limit} questions today`}`;
        botMsgDiv.appendChild(hint);
        if (window.lucide) window.lucide.createIcons({ nodes: [hint] });
    }

    // 成功的這一輪才進多輪歷史（錯誤分支上面都 return 了）
    guestTurns.push({ user: text, assistant: cleanReply, el: botMsgDiv });
    appendShareButton(botMsgDiv, text);
    if (!guestSoftCtaShown) {
        guestSoftCtaShown = true;
        _appendGuestSoftCta(botMsgDiv);
    }
    if (typeof stickChatToBottom === 'function') window.stickChatToBottom();
}
window.sendGuestMessage = sendGuestMessage;

/* 第一個回答後的小卡：不擋路、不催連錢包。2026-10-05 起說登入「馬上有用」的兩件事
   （每日早報、價格提醒），不再只說「記住持倉」。措辭只講資料與通知，不給買賣指令（analysis_not_advice）。 */
function _appendGuestSoftCta(el) {
    const t = (key, fallback) => (window.I18n ? window.I18n.t(key) : fallback);
    const card = document.createElement('div');
    card.className =
        'mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-primary/20 bg-primary/5 px-4 py-3';
    card.innerHTML = `
        <i data-lucide="bell-ring" class="w-4 h-4 text-primary shrink-0"></i>
        <span class="text-sm text-textMuted flex-1 min-w-0">${escapeHtml(
            t('chat.guestSoftCtaText', 'Want a morning brief on your positions, or an alert when a price hits your level? Sign in to turn them on.')
        )}</span>
        <button type="button" data-click="openGuestSignIn"
            class="shrink-0 whitespace-nowrap inline-flex items-center gap-2 text-xs px-4 py-2 rounded-full bg-primary/10 border border-primary/20 text-primary hover:bg-primary/20 transition">
            ${escapeHtml(t('chat.guestSoftCtaButton', 'Sign in to turn on'))}
        </button>`;
    el.appendChild(card);
    if (window.lucide) window.lucide.createIcons({ nodes: [card] });
}

// 小卡的「登入」：打開既有 #login-modal（CSP 禁 inline handler，走 click-delegator）
function openGuestSignIn() {
    const modal = document.getElementById('login-modal');
    if (modal) modal.classList.remove('hidden');
}
window.openGuestSignIn = openGuestSignIn;

/* 訪客升級 CTA：一鍵打開登入 modal（沿用既有 #login-modal） */
function _appendConnectCta(el) {
    const cta = document.createElement('div');
    cta.className = 'mt-4';
    cta.innerHTML = `<button type="button"
        class="inline-flex items-center gap-2 text-xs px-4 py-2 rounded-full bg-primary/10 border border-primary/20 text-primary hover:bg-primary/20 transition">
        <i data-lucide="wallet" class="w-3.5 h-3.5"></i>
        ${window.I18n ? window.I18n.t('chat.guestConnect') : 'Connect wallet to unlock'}
    </button>`;
    cta.querySelector('button').addEventListener('click', () => {
        const modal = document.getElementById('login-modal');
        if (modal) modal.classList.remove('hidden');
    });
    el.appendChild(cta);
    if (window.lucide) window.lucide.createIcons({ nodes: [cta] });
}
window._appendConnectCta = _appendConnectCta;

// ── 附圖（vision Tier 1，docs/plans/2026-08-27-vision-image-analysis-design.md）──
const IMAGE_MAX_BYTES = 4 * 1024 * 1024;
const IMAGE_ALLOWED_TYPES = ['image/png', 'image/jpeg', 'image/webp'];
let pendingImage = null; // { dataUrl, name }

function _visionI18n(key, fallback) {
    return (window.I18n && window.I18n.t && window.I18n.t(key)) || fallback;
}

function _visionErrorMessage(code) {
    const map = {
        VISION_UNSUPPORTED_MODEL: 'chat.visionModelUnsupported',
        VISION_IMAGE_TOO_LARGE: 'chat.imageTooLarge',
        VISION_INVALID_FORMAT: 'chat.imageBadFormat',
        VISION_DISABLED: 'chat.visionDisabled',
        VISION_RATE_LIMITED: 'chat.visionRateLimited',
    };
    const key = map[code];
    return key ? _visionI18n(key, code) : null;
}

function _showImageChip() {
    const chip = document.getElementById('chat-image-chip');
    const name = document.getElementById('chat-image-chip-name');
    if (chip && name) {
        name.textContent = pendingImage ? pendingImage.name : '';
        chip.classList.toggle('hidden', !pendingImage);
    }
    // 縮圖預覽：送出前看得到自己附了什麼圖（2026-08-30 UX）
    const thumb = document.getElementById('chat-image-chip-thumb');
    if (thumb) {
        if (pendingImage) {
            thumb.src = pendingImage.dataUrl;
            thumb.style.display = '';
            thumb.onclick = () => window.showImageLightbox && window.showImageLightbox(pendingImage.dataUrl);
        } else {
            thumb.removeAttribute('src');
            thumb.style.display = 'none';
        }
    }
    if (window.lucide) lucide.createIcons();
}

function _clearPendingImage() {
    pendingImage = null;
    const fi = document.getElementById('chat-image-input');
    if (fi) fi.value = '';
    _showImageChip();
}

// 手機照片動輒 4-8MB——超過壓縮門檻就自動降取樣轉 JPEG（最長邊 2048px），
// 使用者不需要手動壓縮；4MB 上限只作為壓縮後的最终防線。
const IMAGE_COMPRESS_OVER_BYTES = 1200 * 1024;
const IMAGE_MAX_EDGE = 2048;
const IMAGE_JPEG_QUALITY = 0.85;

async function _compressImageFile(file) {
    // EXIF 方向修正：手機直拍的照片若不處理 orientation 會橫倒
    let bitmap;
    try {
        bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
    } catch (_e) {
        bitmap = await createImageBitmap(file);
    }
    const scale = Math.min(1, IMAGE_MAX_EDGE / Math.max(bitmap.width, bitmap.height));
    const w = Math.max(1, Math.round(bitmap.width * scale));
    const h = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    canvas.getContext('2d').drawImage(bitmap, 0, 0, w, h);
    if (typeof bitmap.close === 'function') bitmap.close();
    return canvas.toDataURL('image/jpeg', IMAGE_JPEG_QUALITY);
}

async function _ingestImageFile(file) {
    if (!file) return;
    if (!IMAGE_ALLOWED_TYPES.includes(file.type)) {
        appendMessage('bot', _visionI18n('chat.imageBadFormat', 'Only PNG / JPEG / WebP images are supported.'));
        return;
    }
    try {
        let dataUrl;
        const canCompress = typeof createImageBitmap === 'function';
        if (file.size > IMAGE_COMPRESS_OVER_BYTES && canCompress) {
            // 大圖自動壓縮（螢幕截圖/照片都適用）；壓完仍超限才提示
            dataUrl = await _compressImageFile(file);
            const approxBytes = Math.floor((dataUrl.length - dataUrl.indexOf(',') - 1) * 3 / 4);
            if (approxBytes > IMAGE_MAX_BYTES) {
                appendMessage('bot', _visionI18n('chat.imageTooLarge', 'Image exceeds the 4MB limit.'));
                return;
            }
        } else {
            if (file.size > IMAGE_MAX_BYTES) {
                appendMessage('bot', _visionI18n('chat.imageTooLarge', 'Image exceeds the 4MB limit.'));
                return;
            }
            dataUrl = await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result);
                reader.onerror = () => reject(reader.error);
                reader.readAsDataURL(file);
            });
        }
        pendingImage = { dataUrl, name: file.name || 'image' };
        _showImageChip();
    } catch (e) {
        console.warn('[vision] read image failed:', e);
    }
}

function _initImageAttach() {
    const btn = document.getElementById('chat-attach-btn');
    const fileInput = document.getElementById('chat-image-input');
    const input = document.getElementById('user-input');
    const removeBtn = document.getElementById('chat-image-remove');
    if (btn && fileInput && !btn.dataset.visionBound) {
        btn.dataset.visionBound = '1';
        btn.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', () =>
            _ingestImageFile(fileInput.files && fileInput.files[0])
        );
    }
    // 手機相機直達（capture=environment；與 📎 相簿入口並存）
    const camBtn = document.getElementById('chat-attach-cam-btn');
    const camInput = document.getElementById('chat-camera-input');
    if (camBtn && camInput && !camBtn.dataset.visionBound) {
        camBtn.dataset.visionBound = '1';
        camBtn.addEventListener('click', () => camInput.click());
        camInput.addEventListener('change', () =>
            _ingestImageFile(camInput.files && camInput.files[0])
        );
    }
    if (input && !input.dataset.visionPasteBound) {
        input.dataset.visionPasteBound = '1';
        input.addEventListener('paste', (e) => {
            const items = e.clipboardData && e.clipboardData.items;
            if (!items) return;
            for (const item of items) {
                if (item.type && IMAGE_ALLOWED_TYPES.includes(item.type)) {
                    const file = item.getAsFile();
                    if (file) {
                        e.preventDefault();
                        _ingestImageFile(file);
                    }
                    break;
                }
            }
        });
    }
    if (removeBtn && !removeBtn.dataset.visionBound) {
        removeBtn.dataset.visionBound = '1';
        removeBtn.addEventListener('click', _clearPendingImage);
    }
}
_initImageAttach();

/* HITL 問題卡：依型別分派到各卡片渲染器。即時串流與斷線續傳（resumeAnalysisStream）
   共用這一份——兩邊各寫一份遲早會長歪（#804：型別對不上就掉進 clarify fallback）。
   呼叫前 _hitlContext 必須已指向這次的上下文（clarify 會把問題寫回去）。
   priorHtml：已串流的前言（＋思考區塊），clarify 卡會保留在卡片上方。 */
function renderHitlQuestion(idata, botMsgDiv, priorHtml) {
    if (idata.type === 'pre_research') {
        renderPreResearchCard(idata, botMsgDiv);
    } else if (idata.type === 'confirm_plan') {
        renderPlanCard(idata, botMsgDiv);
    } else if (idata.type === 'consent_gate') {
        // Consent Gate（Trustworthy AI Hackathon — Policy Gate）
        renderConsentCard(idata, botMsgDiv);
    } else if (idata.type === 'skill_create_consent') {
        // Skill 建立/修改/刪除同意卡（agent 自主管理 HITL）
        if (typeof window.renderSkillConsentCard === 'function') {
            window.renderSkillConsentCard(idata, botMsgDiv);
        }
    } else if (idata.type === 'memory_consent') {
        // Memory 記憶同意卡
        if (typeof window.renderMemoryConsentCard === 'function') {
            window.renderMemoryConsentCard(idata, botMsgDiv);
        }
    } else if (idata.type === 'multi_consent') {
        // 多項操作批次同意卡（一輪多提案一次顯示全部）
        if (typeof window.renderMultiConsentCard === 'function') {
            window.renderMultiConsentCard(idata, botMsgDiv);
        }
    } else if (idata.type === 'journal_consent') {
        // Journal 記帳確認卡（金流 HITL：核准後才寫入統一帳本）
        if (typeof window.renderJournalConsentCard === 'function') {
            window.renderJournalConsentCard(idata, botMsgDiv);
        }
    } else if (idata.type === 'loop_fork') {
        // 分岔卡（HITL 第 9 種）。軟門檻是在**第一輪**跑到第
        // 15 個工具時觸發的，走的就是這條 sendMessage 串流——
        // 只在 chat-hitl 的 resume 串流接它，卡片實際上永遠
        // 不會出現（掉進下面的 clarify fallback）。
        if (typeof window.renderLoopForkCard === 'function') {
            window.renderLoopForkCard(idata, botMsgDiv);
        }
    } else {
        // Render clarification question inline (clear spinner, show question)
        const question = idata.question || (window.I18n ? window.I18n.t('chat.clarificationQuestion') : 'What specifically would you like to know?');
        // 存進 _hitlContext，讓 hitlPaused 區塊（1001+）不再用空 fallback 蓋掉
        _hitlContext.question = question;
        _hitlContext.clarifyOptions = Array.isArray(idata.options) ? idata.options : [];
        // 結構化選項（學 Hermes single-select MC）。有 options 時渲染可點選按鈕。
        const _esc = typeof window.escapeHtml === 'function'
            ? window.escapeHtml
            : (s) => String(s).replace(/</g, '&lt;');
        const options = _hitlContext.clarifyOptions;
        let optionsHtml = '';
        if (options.length > 0) {
            optionsHtml = '<div class="mt-3 flex flex-col gap-2">';
            options.forEach((opt) => {
                const label = typeof opt === 'string' ? opt : (opt.label || '');
                const hint = typeof opt === 'string' ? opt : (opt.hint || opt.label || '');
                optionsHtml += `<button type="button" data-clarify-answer="${_esc(hint)}" class="clarify-option-btn w-full text-left px-4 py-2.5 rounded-xl bg-background border border-borderSubtle text-secondary text-sm hover:border-primary/50 hover:bg-primary/5 transition">${_esc(label)}</button>`;
            });
            optionsHtml += '</div>';
        }
        // 保留已串流的前言（2026-09-07 回報「答案顯示完又
        // 消失」）：模型常先串流一段看起來像答案的說明才呼
        // 叫 clarify——直接蓋掉會讓使用者以為回答不見了。
        // 前言（＋思考區塊）留在卡片上方，問題與前文並存。
        botMsgDiv.innerHTML = (priorHtml || '') + `
            <div class="rounded-2xl border border-borderLight bg-surfaceHighlight overflow-hidden">
                <div class="px-5 py-4 flex items-start gap-3">
                    <i data-lucide="help-circle" class="w-4 h-4 text-primary mt-0.5 flex-shrink-0"></i>
                    <div class="flex-1">
                        <p class="text-sm text-secondary">${_esc(question)}</p>
                        ${optionsHtml}
                        <p class="text-xs text-textMuted mt-2">${options.length > 0 ? (window.I18n ? window.I18n.t('chat.clickOrType') : 'Pick an option above, or type your question') : (window.I18n ? window.I18n.t('chat.typeYourReply') : 'Just type your question')}</p>
                    </div>
                </div>
            </div>`;
        if (window.lucide) lucide.createIcons({ nodes: [botMsgDiv] });
        // 綁定選項按鈕：點了直接送 hint 當答案
        botMsgDiv.querySelectorAll('.clarify-option-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                const answer = btn.getAttribute('data-clarify-answer') || '';
                if (typeof window.submitHITLAnswer === 'function') {
                    window.submitHITLAnswer(answer);
                }
            });
        });
    }
}
// chat-hitl.js 的續傳串流（核准後又停下來問）共用同一份分派
window.renderHitlQuestion = renderHitlQuestion;

/* HITL 暫停：收掉卡片旁殘留的 spinner（卡片沒畫出來時補 fallback 卡），
   並解鎖輸入框讓使用者回覆。即時串流的 finally 與斷線續傳共用。 */
function enterHitlPausedUI(botMsgDiv) {
    // HITL paused: Unlock input so user can type negotiation/answer
    const input = document.getElementById('user-input');
    const sendBtn = document.getElementById('send-btn');

    // P2-2: Update botMsgDiv to show waiting-for-input state
    // (botMsgDiv still has spinner from Proto-Process if _hitlContext.question wasn't set yet)
    if (botMsgDiv) {
        const hitlType = _hitlContext?.hitlType;
        // 同意卡型別（卡片渲染器缺席/事件丟失時）：fallback 至少要給
        // 「能回覆」的指引——resume parser 認得同意/取消（四語詞集）。
        // 2026-09-21 線上案例：hitl_question 幀丟失後只顯示「請在下方
        // 輸入回應...」，使用者完全不知道 agent 在問什麼。
        const consentCardTypes = [
            'consent_gate',
            'skill_create_consent',
            'memory_consent',
            'multi_consent',
            'journal_consent',
            'loop_fork',
        ];
        const isConsentFallback =
            hitlType && consentCardTypes.includes(hitlType);
        const question =
            _hitlContext?.question ||
            (window.I18n
                ? window.I18n.t(
                    isConsentFallback ? 'chat.hitlConsentFallback' : 'chat.replyPrompt'
                )
                : isConsentFallback
                    ? 'The agent has a proposed action waiting for your approval. Reply “approve” or “cancel”.'
                    : 'Please type your response below...');

        if (hitlType === 'pre_research' || hitlType === 'confirm_plan' || hitlType === 'consent_gate') {
            // These have their own full-card rendering; just clear any leftover spinner.
            // 注意：不可用 botMsgDiv.innerHTML = botMsgDiv.innerHTML（會把 DOM 重新序列化，
            // 殺掉所有 JS 事件 handler，包含 consent 卡片的 data-click 委派也會失效）。
            // 卡片已由 renderConsentCard/renderPlanCard append 完成，這裡只移除 spinner。
            const spinner = botMsgDiv.querySelector('.typing-indicator, .animate-pulse, [data-spinner]');
            if (spinner) spinner.remove();
        } else {
            // clarify 卡片：若第一次渲染（802+）已經畫好含 options 的卡片，
            // 不要用空 fallback 蓋掉它（會殺掉按鈕 + 顯示「Please type your response」）。
            // 只在卡片還沒渲染（還是 spinner）時才 fallback。
            const alreadyRendered = botMsgDiv.querySelector('.clarify-option-btn, .text-secondary');
            if (!alreadyRendered) {
                botMsgDiv.innerHTML = `
                    <div class="rounded-2xl border border-borderLight bg-surfaceHighlight overflow-hidden">
                        <div class="px-5 py-4 flex items-start gap-3">
                            <i data-lucide="help-circle" class="w-4 h-4 text-primary mt-0.5 flex-shrink-0"></i>
                            <div>
                                <p class="text-sm text-secondary">${escapeHtml(question)}</p>
                                <p class="text-xs text-textMuted mt-1.5">${window.I18n ? window.I18n.t('chat.replyInInputBelow') : 'Please reply in the input box below'}</p>
                            </div>
                        </div>
                    </div>`;
                if (window.lucide) lucide.createIcons({ nodes: [botMsgDiv] });
            } else {
                // 卡片已渲染，只清殘留 spinner
                const spinner = botMsgDiv.querySelector('.typing-indicator, .animate-pulse, [data-spinner]');
                if (spinner) spinner.remove();
            }
        }
    }

    if (input) {
        input.disabled = false;
        input.classList.remove('opacity-50');
        input.focus();
        input.placeholder = window.I18n.t('chat.hitlPlaceholder');
    }
    if (sendBtn) {
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
        if (window.lucide) lucide.createIcons({ nodes: [sendBtn] });
    }
}

async function sendMessage() {
    const input = document.getElementById('user-input');
    const sendBtn = document.getElementById('send-btn');
    const text = input.value.trim();
    if (!text && !pendingImage && !isAnalyzing) return; // Allow empty text if we are just stopping? No, stop is a separate click.

    // 捕獲啟動時的 sessionId，用於 finally 判斷是否還在原 session（避免背景完成時動到新 session 的 DOM）
    // 新對話此時是 null，下方 lazy 建立 session 後會改指向新 session
    let sessionIdAtStart = window.currentSessionId;

    // ── Global Cleanup ──
    // Force remove old buttons on any new interaction
    cleanupStaleButtons();

    // ── Input State Management for "Stop" capability ─────────────────────
    if (isAnalyzing) {
        // If we are in HITL pause (waiting for input), allow typing
        // But isAnalyzing is technically false during HITL pause (set in finally block)
        // Wait, in my previous edit, I set isAnalyzing = false in finally if hitlPaused.
        // So this block only runs if isAnalyzing is TRUE (streaming).
        // So clicking button here means STOP.

        // However, if the user hits ENTER in the input box...
        // If input is enabled (which it shouldn't be during streaming, but IS during HITL pause),
        // we need to check if we are actually in HITL mode.

        // Wait, if isAnalyzing is true, input SHOULD be disabled.
        // If isAnalyzing is false (HITL pause), we fall through to Start Analysis logic below.

        stopAnalysis();
        return;
    }

    // ── HITL Input Routing ───────────────────────────────────────────────
    // If we have a pending HITL context, this input is an answer/negotiation
    if (_hitlContext && _hitlContext.sessionId === window.currentSessionId) {
        const hitlType = _hitlContext.hitlType;

        // Clear input immediately
        input.value = '';

        // If it's a plan confirmation or pre_research, send the user's raw text
        // and let the backend's LLM determine if it's a question, modification, or confirmation.
        if (hitlType === 'confirm_plan' || hitlType === 'pre_research') {
            appendMessage('user', text);
            window.submitHITLAnswer(text);
            return;
        }

        // For other HITL types (e.g. simple clarification), send raw text
        appendMessage('user', text);
        window.submitHITLAnswer(text);
        return;
    }

    if (!text && !pendingImage) return;

    // ── 訪客模式短路（2026-08-19 設計）─────────────────────────────────
    // 訪客不經 BYOK 閘門 / lazy session / 主分析流程：直接走 guest 端點
    // （每日限量、平台免費模型）。位置必須在 !userProvider 檢查之前。
    if (!(AuthManager.currentUser?.user_id || AuthManager.currentUser?.uid)) {
        // 附圖僅限登入用戶（vision Tier 1 設計：免費線成本考量）——明確引導
        if (pendingImage) {
            appendMessage('bot', _visionI18n('chat.imageLoginRequired', 'Image analysis requires login. Please log in first, then re-attach your image.'));
            return;
        }
        isAnalyzing = true;
        try {
            input.value = '';
            if (document.activeElement === input) input.blur();
            appendMessage('user', text);
            const botDiv = appendMessage('bot', '');
            await sendGuestMessage(text, botDiv);
        } finally {
            resetChatUI();
        }
        return;
    }

    // ── Start Analysis ───────────────────────────────────────────────────
    isAnalyzing = true;

    // Change Send button to Stop button
    sendBtn.classList.remove('bg-primary', 'hover:brightness-110');
    sendBtn.classList.add('bg-red-500', 'hover:bg-red-600', 'text-white');
    sendBtn.innerHTML = '<i data-lucide="square" class="w-4 h-4 fill-current"></i>'; // Stop icon
    if (window.lucide) lucide.createIcons({ nodes: [sendBtn] });

    // Disable Input but keep Button enabled (as Stop)
    input.disabled = true;
    input.classList.add('opacity-50');
    // sendBtn.disabled = true; // Don't disable, we need it for Stop

    // 檢查用戶是否有設置 API key（使用快取，避免每次發送都打後端）
    const userProvider = await getCachedUserProvider();

    if (!userProvider) {
        resetChatUI(); // Helper to reset UI state
        showAlert({
            title: window.I18n ? window.I18n.t('chat.noModelTitle') : 'No AI model available',
            message:
                window.I18n
                    ? window.I18n.t('chat.noModelMessage')
                    : "The free CryptoMind Lite model isn't available right now, and you haven't linked your own AI model yet.\n\nGo to AI Studio → Models and link any provider (OpenAI, Claude, Gemini, DeepSeek and more) to keep chatting.",
            type: 'warning',
            confirmText: window.I18n ? window.I18n.t('chat.goToModels') : 'Set up a model',
        }).then(() => {
            if (typeof window.openModelSettings === 'function') window.openModelSettings();
        });
        return;
    }

    // Enable UI for sending (transition to analysis state)
    sendBtn.disabled = false;
    input.classList.remove('opacity-50');
    sendBtn.classList.remove('opacity-50', 'cursor-not-allowed');

    const _sendIcon = sendBtn.querySelector('i[data-lucide]');
    // Note: We changed icon to Stop square earlier, so we don't want to reset it to arrow-up yet!
    // The previous code block was copy-pasted wrong.
    // We already set it to square icon at the top of function.

    // Remove the redundant error check block that was here.

    // Lazy Creation: 如果沒有 currentSessionId，先建立新的 Session
    // 訪客模式（2026-08-19）：訪客不建 session（無帳號），直接走 guest 流程
    const _isGuestUser = !(
        AuthManager.currentUser?.user_id || AuthManager.currentUser?.uid
    );
    if (!window.currentSessionId && !_isGuestUser) {
        try {
            const userId = AuthManager.currentUser.user_id;

            // 這裡可以傳遞 title (e.g., text.substring(0, 20)) 但後端通常會預設為 New Chat 或由第一條訊息生成
            const createData = await AppAPI.post('/api/chat/sessions', {
                title: text.substring(0, 40),
            });
            window.currentSessionId = createData.session_id;
            AppStore.set('currentSessionId', window.currentSessionId);
            // 上面的 isAnalyzing = true 當時掛在 null 上（no-op）——改掛到剛建好的
            // session，Stop 鈕、controller、續傳與 finally 收尾才認得這次分析
            sessionIdAtStart = window.currentSessionId;
            setSessionAnalyzing(true, sessionIdAtStart);

            // 刷新列表以顯示新對話
            // loadSessions();
        } catch (e) {
            console.error('Failed to create lazy session:', e);
            appendMessage('bot', '❌ ' + (window.I18n ? window.I18n.t('chat.createSessionFailed') : 'Failed to create session'));
            resetChatUI();
            return;
        }
    }

    const userSelectedModel = getSelectedUserModel(userProvider);
    const needsManualModel = window.isFreeInputProvider
        ? window.isFreeInputProvider(userProvider)
        : userProvider === 'openrouter';
    if (needsManualModel && !userSelectedModel) {
        resetChatUI();
        if (typeof window.showToast === 'function') {
            window.showToast(
                window.I18n?.t('llmSettings.enterModelName') || 'Please enter a model name first.',
                'error'
            );
        }
        return;
    }
    const marketType = 'spot';
    const autoExecute = false;

    // 純圖片無文字 → 帶預設分析指示（後端 message 為必填）
    let outgoingText = text;
    if (!outgoingText && pendingImage) {
        outgoingText = _visionI18n(
            'chat.imageDefaultMessage',
            'Please analyze the attached image.'
        );
    }

    input.value = '';
    // 送出後主動收鍵盤：手機上使用者送完訊息就是要看 AI 回覆（Telegram / iMessage 慣例）。
    // blur 會觸發 chat-state.js 的 focusout → 收鍵盤 → 輸入框貼回視窗底部。這個動作
    // 必須在 sendMessage 開始就做，避免鍵盤收起的漸進動畫期間把剛插入的「Thinking...」
    // 進度列擠到輸入框後面（問題 1 的根因之一）。
    // 注意：與 click-delegator.js 的 pointerdown preventDefault 配合 —— preventDefault 擋住
    // 瀏覽器把焦點「轉給按鈕」這條路徑，這裡的 blur 才是真正收鍵盤的觸發；兩者不衝突。
    if (document.activeElement === input) {
        input.blur();
    }

    // 附圖縮圖渲染進使用者氣泡（vision Tier 1）——data URL 僅存在於本頁面，
    // 歷史重播（伺服器端只存文字描述）不會帶圖，屬 v1 已知限制
    const outgoingImage = pendingImage;
    const userMsgDiv = appendMessage('user', outgoingText);
    if (outgoingImage) {
        try {
            const img = document.createElement('img');
            img.src = outgoingImage.dataUrl;
            img.alt = outgoingImage.name || 'attached image';
            // 置中呈現：保持圖片自然尺寸、只設上限（w-full 會把小圖拉到滿寬，
            // 手機上整個氣泡被圖佔滿——2026-08-28 使用者回報「超大」的根因）
            img.className =
                'block mx-auto mb-2.5 rounded-xl max-w-[min(240px,100%)] h-auto cursor-zoom-in';
            img.addEventListener('click', () =>
                window.showImageLightbox && window.showImageLightbox(outgoingImage.dataUrl)
            );
            userMsgDiv.prepend(img);
        } catch (e) {
            console.warn('[vision] bubble image render failed:', e);
        }
    }

    // Instant cross-tab hint: detect market intent from user's text
    const _detectedTab = _detectTabFromText(text);
    if (_detectedTab) _appendNavChip(userMsgDiv, _detectedTab, 'subtle');

    const botMsgDiv = appendMessage('bot', '');
    const startTime = Date.now();
    let timerInterval;

    // 重置分析過程面板的展開狀態
    AppStore.set('lastProcessOpenState', false);

    // Initial "Proto-Process" UI to match the final analysis UI for seamless transition
    botMsgDiv.innerHTML = `
        <div class="process-container" style="border-style: dashed; opacity: 0.7;">
            <div class="process-head flex items-center gap-2 px-4 py-3">
                <i data-lucide="loader-2" class="w-4 h-4 animate-spin text-primary"></i>
                <span class="process-head-label font-medium text-sm text-textMuted">${window.I18n ? window.I18n.t('chat.thinking') : 'Thinking...'}</span>
                <span class="process-head-meta ml-auto flex items-center gap-1.5">
                    <span class="elapsed-stage text-xs text-textMuted/60"></span>
                    <span id="loading-timer" class="text-xs font-mono text-textMuted/50">0s</span>
                </span>
            </div>
        </div>
    `;

    // 新的一次分析：使用者理應看著最新內容，回到貼底狀態
    if (typeof window.resetChatStickToBottom === 'function') {
        window.resetChatStickToBottom();
    }

    timerInterval = setInterval(() => {
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
        window.ChatStreamUI.updateTimers(botMsgDiv, elapsed);
        // 進度列在分析期間會持續長高，不跟著捲就會滑到固定輸入框底下
        if (typeof window.stickChatToBottom === 'function') {
            window.stickChatToBottom();
        }
    }, 100);

    const ANALYSIS_REQUEST_TIMEOUT_MS = 3600000;  // 1 小時總超時（深度分析 + 多輪 LLM reasoning 可能很久）
    const ANALYSIS_STREAM_IDLE_TIMEOUT_MS = 600000;  // 10 分鐘閒置超時（LLM reflect/synthesize 過程中無 progress 是正常）

    // 伺服器端的執行識別碼，由 run_started 事件帶回；斷線後靠它續傳
    let activeRunId = null;
    // 最後收到的 SSE 事件 id，重連時告訴伺服器從哪裡繼續
    let lastEventId = 0;

    const localAnalysisController = new AbortController();
    if (sessionIdAtStart) {
        setAnalysisController(localAnalysisController, sessionIdAtStart);
    }
    let requestTimeoutId = null;
    let streamIdleTimeoutId = null;

    const clearAnalysisTimeouts = () => {
        if (requestTimeoutId) {
            clearTimeout(requestTimeoutId);
            requestTimeoutId = null;
        }
        if (streamIdleTimeoutId) {
            clearTimeout(streamIdleTimeoutId);
            streamIdleTimeoutId = null;
        }
    };

        const armStreamIdleTimeout = () => {
        if (streamIdleTimeoutId) clearTimeout(streamIdleTimeoutId);
        streamIdleTimeoutId = setTimeout(() => {
            localAnalysisController.abort(new Error('STREAM_IDLE_TIMEOUT'));
        }, ANALYSIS_STREAM_IDLE_TIMEOUT_MS);
    };

    // Pre-build HITL resume context (used if server sends hitl_question)
    const _hitlResumeContext = {
        originalMessage: text,
        // 用這次分析自己的 session（新對話在上面 lazy 建立後已更新）；中間有 await，
        // 不能讀當下的 window.currentSessionId（使用者可能已切到別的對話）
        sessionId: sessionIdAtStart,
        userProvider,
        userSelectedModel,
        presetId: window.ChatPreset ? window.ChatPreset.getSelectedId() : null,
        runId: null,
        botMsgDiv,
        startTime,
    };

    // Declared OUTSIDE try so finally can read it
    let hitlPaused = false;
    // 同理：斷線時 catch 要拿已收到的內容當續傳起點，宣告在 try 內會取不到
    let fullContent = '';

    // ── 串流渲染節流（rAF）─────────────────────────────────────────────
    // 每個 token 都跑一次 renderStoredBotMessage(整段) + innerHTML 全換，成本
    // 隨訊息長度線性上升 → 整段串流是 O(n²)，手機上長回覆會明顯掉幀。
    // 改成把 token 累進 fullContent 後只「排程」一次 rAF flush，同一幀內的多個
    // token 合併成一次渲染。視覺上無差別（本來就快過 60fps），但 CPU 大幅下降。
    let streamRafId = null;
    let streamRafIsTimer = false;  // rAF id 與 timeout id 是不同命名空間，不可混清
    let streamElapsed = null;
    let reasoningContent = '';
    const flushStreamRender = () => {
        streamRafId = null;
        botMsgDiv.innerHTML = renderStoredBotMessage(fullContent, true, streamElapsed);
        // 思考區塊擺在答案前面。renderStoredBotMessage 會整個換掉 innerHTML，
        // 所以每次 flush 都要重貼，不能只在收到第一段推理時插一次。
        if (reasoningContent) {
            botMsgDiv.insertAdjacentHTML('afterbegin', renderThinkingBlock(reasoningContent, true));
            if (window.lucide) window.lucide.createIcons({ nodes: [botMsgDiv] });
        }
        // Smart scroll: only auto-scroll when streaming if user is near bottom
        if (sessionIdAtStart === window.currentSessionId) {
            const chatContainer = document.getElementById('chat-messages');
            if (chatContainer) {
                const isAtBottom =
                    chatContainer.scrollHeight - chatContainer.scrollTop - chatContainer.clientHeight < 120;
                if (isAtBottom) {
                    chatContainer.scrollTo({ top: chatContainer.scrollHeight, behavior: 'smooth' });
                }
            }
        }
    };
    const scheduleStreamRender = (elapsed) => {
        streamElapsed = elapsed;
        if (streamRafId !== null) return;
        streamRafIsTimer = (typeof requestAnimationFrame !== 'function');
        streamRafId = streamRafIsTimer
            ? setTimeout(flushStreamRender, 16)
            : requestAnimationFrame(flushStreamRender);
    };
    // done / error 路徑要先取消待處理的 flush，否則 rAF 會在 final render 之後
    // 才跑，把「含耗時 badge 的完整版」覆蓋回「串流中」的版本。
    const cancelStreamRender = () => {
        if (streamRafId === null) return;
        if (streamRafIsTimer) clearTimeout(streamRafId);
        else cancelAnimationFrame(streamRafId);
        streamRafId = null;
    };

    try {
        const currentUser = AuthManager.currentUser;
        const hasKnownSession = !!(
            currentUser?.user_id ||
            currentUser?.uid
        );
        if (!hasKnownSession) {
            // 理論上訪客已在函式前段短路；防禦：未登入一律導向登入
            if (typeof showToast === 'function') showToast(window.I18n ? window.I18n.t('chat.pleaseLogin') : 'Please log in first', 'warning');
            return;
        }
        requestTimeoutId = setTimeout(() => {
            localAnalysisController.abort(new Error('ANALYSIS_TIMEOUT'));
        }, ANALYSIS_REQUEST_TIMEOUT_MS);

        const response = await fetch('/api/analyze', {
            method: 'POST',
            headers: AppAPI.buildHeaders(),
            credentials: 'include',
            body: JSON.stringify({
                message: outgoingText,
                market_type: marketType,
                auto_execute: autoExecute,
                user_provider: userProvider,
                user_model: userSelectedModel,
                session_id: window.currentSessionId,
                language: detectMessageLanguage(text || outgoingText),
                preset_id: (window.ChatPreset && typeof window.ChatPreset.getSelectedId === 'function')
                    ? window.ChatPreset.getSelectedId() : null,
                image_data_url: pendingImage ? pendingImage.dataUrl : null,
            }),
            signal: localAnalysisController.signal,
        });
        // body 已序列化即可清（fetch 內部已持有字串）；失敗重送需重新附圖
        _clearPendingImage();

        if (!response.ok) {
            let errorMsg = window.I18n
                ? window.I18n.t('chat.serverError', { status: response.status })
                : `Server error (${response.status})`;
            try {
                const responseText = await response.text();
                if (responseText) {
                    try {
                        const errorData = JSON.parse(responseText);
                        if (typeof errorData.detail === 'string' && errorData.detail.trim()) {
                            // vision 錯誤碼優先映射 i18n（VISION_* → 4 語提示）
                            errorMsg = _visionErrorMessage(errorData.detail.trim())
                                || errorData.detail.trim();
                        } else if (typeof errorData.message === 'string' && errorData.message.trim()) {
                            errorMsg = errorData.message.trim();
                        } else if (typeof errorData.error === 'string' && errorData.error.trim()) {
                            errorMsg = errorData.error.trim();
                        }
                    } catch {
                        errorMsg = responseText.substring(0, 160).trim();
                    }
                }
            } catch {
            }
            throw new Error(
                normalizeChatErrorMessage(
                    errorMsg,
                    window.I18n
                        ? window.I18n.t('chat.sendFailedCheckModel')
                        : window.I18n ? window.I18n.t('chat.sendFailedModel') : 'Send failed.'
                )
            );
        }

        // Backend 已經保存了用戶訊息並更新了標題，立即刷新列表以顯示新標題
        loadSessions().catch(function() {});

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let responseMetadata = null;
        let pendingBuffer = '';

        armStreamIdleTimeout();

        while (true) {
            const { value, done } = await reader.read();
            if (done || hitlPaused) break;

            armStreamIdleTimeout();
            const chunk = decoder.decode(value, { stream: true });
            const parsed = window.ChatStreamUI.consumeChunk(pendingBuffer, chunk);
            const lines = parsed.lines;
            pendingBuffer = parsed.pending;
            const currentElapsed = ((Date.now() - startTime) / 1000).toFixed(1);

            for (const line of lines) {
                // 事件 id：斷線重連時要從這裡之後續傳
                if (line.startsWith('id: ')) {
                    const parsedId = parseInt(line.slice(4).trim(), 10);
                    if (Number.isFinite(parsedId)) lastEventId = parsedId;
                    continue;
                }
                if (line.startsWith('data: ')) {
                    let data;
                    try {
                        data = JSON.parse(line.substring(6));
                    } catch (parseErr) {
                        // 幀丟棄必須留痕：2026-09-21 線上案例——hitl_question 帶
                        // NaN 匯率（json.dumps allow_nan 產出瀏覽器不認得的 token），
                        // 幀被靜默吞掉後同意卡整張消失，只剩空 fallback 卡。
                        console.warn('[chat] SSE 幀 JSON parse 失敗（已丟棄）:', parseErr?.message, line.slice(0, 200));
                        continue;
                    }

                    // 伺服器端這次執行的識別碼。連線斷掉時用它查詢當下進度，
                    // 不必等分析全部跑完才知道發生什麼事。
                    if (data.type === 'run_started') {
                        activeRunId = data.run_id || null;
                        // 掛在這次分析自己的對話上（不是「目前對話」）：Stop 撤銷才不會撤到別的對話的 run
                        setSessionRunId(activeRunId, sessionIdAtStart);
                        _hitlResumeContext.runId = activeRunId;
                        continue;
                    }

                    // ── HITL: server needs user input ──────────────────────
                    if (data.type === 'hitl_question') {
                        clearInterval(timerInterval);
                        clearAnalysisTimeouts();
                        // 卡片渲染前先取消待處理的串流渲染——rAF 若在卡片
                        // 之後才跑，會把整張卡蓋回串流畫面
                        cancelStreamRender();
                        const idata = data.data || {};
                        // Store HITL type for sendMessage routing
                        _hitlResumeContext.hitlType = idata.type;
                        // 問題掛在這次分析自己的對話名下（同 resumeAnalysisStream）——
                        // 不能用以「目前對話」為鍵的 _hitlContext setter：使用者已切到
                        // 別的對話時會把 A 的問題掛到 B 上，B 下一句話就被當成回答送去續傳 A
                        setSessionHitlContext(_hitlResumeContext, sessionIdAtStart);
                        if (sessionIdAtStart === window.currentSessionId && botMsgDiv.isConnected) {
                            // 保留已串流的前言（＋思考區塊）：clarify 卡會把它留在問題上方
                            renderHitlQuestion(
                                idata,
                                botMsgDiv,
                                (reasoningContent ? renderThinkingBlock(reasoningContent, false) : '') +
                                    renderStoredBotMessage(fullContent, false)
                            );
                        } else if (sessionIdAtStart === window.currentSessionId) {
                            // 切走又切回來，這個泡泡已被 loadChatHistory 換掉：走續傳路徑畫在看得到的地方。
                            // 人還在別的對話就等 switchSession 切回來再畫（resumePendingHitl）
                            resumePendingHitl(sessionIdAtStart);
                        }
                    }
                    if (data.waiting) {
                        hitlPaused = true;
                        break;
                    }

                    // ── Meta Update (Codebook ID) ───────────────────────────
                    if (data.type === 'meta') {
                        if (data.codebook_id) {
                            botMsgDiv.dataset.codebookId = data.codebook_id;
                        }
                    }

                    // ── Progress Update (Parallel Execution) ────────────────
                    if (data.type === 'progress') {
                        window.ChatStreamUI.applyProgress(botMsgDiv, data.data || {});
                    }

                    if (data.type === 'response_metadata') {
                        responseMetadata = data.data || null;
                    }

                    // ── Revocation（可信 AI 第 6 要素）──────────────────────
                    // 使用者撤銷授權 → agent 已停止。把已串流的部份內容保留，
                    // 並 append 撤銷提示，讓 done 路徑的 final render 自然顯示。
                    if (data.type === 'revoked') {
                        const revokeNotice = data.message || window.I18n.t('chat.revokedNotice') || 'Authorization revoked; the agent has stopped.';
                        fullContent +=
                            (fullContent ? '\n\n' : '') +
                            '> ⏹️ **' + revokeNotice + '**';
                        if (window.showToast) {
                            window.showToast(revokeNotice, 'info');
                        }
                    }

                    // 思考 token：獨立累積，絕不能落到下面的 data.content 分支
                    // ——那裡是「任何帶 content 的幀都累加進答案」，推理會被
                    // 直接寫進回覆正文裡。
                    if (data.type === 'reasoning') {
                        if (data.content) {
                            reasoningContent += data.content;
                            scheduleStreamRender(currentElapsed);
                        }
                        continue;
                    }

                    if (data.content) {
                        // type==='final' 是後端送出的「權威完整回覆」，必須「取代」
                        // 累積內容，否則會和先前串流進來的 token 重複（整段顯示兩次）。
                        // token 串流 / 舊版無 type 的分塊 → 累加。
                        //
                        // 安全性：後端 claw_loop._StreamSanitizer 已對每個 token chunk
                        // 做 tool name sanitize（防 LLM 把 get_crypto_price 等內部工具名
                        // 寫進回應）。final event 再經 _clean_claw_response 完整清洗一次
                        // 並覆蓋累積內容。雙層防護：即使串流中的 sanitize 有遺漏，
                        // final 覆蓋也會清掉。
                        if (data.type === 'final') {
                            fullContent = data.content;
                        } else {
                            fullContent += data.content;
                        }
                        // 實時更新內容，傳入 isStreaming=true 和當前耗時。
                        // 走 rAF 節流：同一幀內的多個 token 合併成一次渲染。
                        scheduleStreamRender(currentElapsed);
                    }

                    if (data.done) {
                        clearInterval(timerInterval);
                        clearAnalysisTimeouts();
                        cancelStreamRender();  // 防待處理的 rAF 覆蓋掉下面的 final render
                        // 不用 isAnalyzing = false：那是「目前」對話，背景對話收尾會清掉別人的
                        setSessionAnalyzing(false, sessionIdAtStart);
                        const totalTime = ((Date.now() - startTime) / 1000).toFixed(1);

                        // Final render，傳入 isStreaming=false
                        botMsgDiv.innerHTML = renderStoredBotMessage(fullContent, false, totalTime);
                        if (reasoningContent) {
                            botMsgDiv.insertAdjacentHTML('afterbegin', renderThinkingBlock(reasoningContent, false));
                        }
                        if (responseMetadata) {
                            botMsgDiv.insertAdjacentHTML('beforeend', renderResponseMetadata(responseMetadata));
                        }

                        const timeBadge = document.createElement('div');
                        timeBadge.className =
                            'mt-4 flex items-center justify-between text-xs text-textMuted/60 font-mono';
                        timeBadge.innerHTML = `<span>${window.I18n.t('chat.elapsed', { time: totalTime })}</span>`;
                        if (responseMetadata && responseMetadata.model_label) {
                            timeBadge.insertAdjacentHTML('beforeend', renderModelLabel(responseMetadata.model_label));
                        }
                        botMsgDiv.appendChild(timeBadge);
                        appendShareButton(botMsgDiv, text);
                        if (window.lucide) {
                            lucide.createIcons({ nodes: [botMsgDiv] });
                        }

                        // Cross-tab nav: if AI resolved a specific market, show a prominent
                        // "Go to [Tab]" button so user can check live data in one click.
                        const _resolvedTab = responseMetadata?.resolved_market
                            ? (_MARKET_TAB_MAP[responseMetadata.resolved_market]?.tab || null)
                            : null;
                        // Also fall back to client-side detection if backend didn't resolve one
                        const _navTab = _resolvedTab || _detectTabFromText(text);
                        if (_navTab) _appendNavChip(botMsgDiv, _navTab, 'prominent');

                        // Refresh sessions list (to update title if it was new)
                        loadSessions().catch(function() {});

                        if (sessionIdAtStart === window.currentSessionId) {
                            const chatContainerDone = document.getElementById('chat-messages');
                            if (chatContainerDone) {
                                chatContainerDone.scrollTop = chatContainerDone.scrollHeight;
                            }
                        }
                    }

                    if (data.error) {
                        clearInterval(timerInterval);
                        clearAnalysisTimeouts();
                        cancelStreamRender();
                        botMsgDiv.innerHTML = `<span class="text-red-400">${escapeHtml(
                            normalizeChatErrorMessage(
                                data.error,
                                window.I18n
                                    ? window.I18n.t('chat.analysisFailedGeneric')
                                    : window.I18n ? window.I18n.t('chat.analysisFailed') : 'Analysis failed.'
                            )
                        )}</span>`;
                        // 立即恢復輸入框與送出鈕：不能等串流關閉才 reset —
                        // 部分 proxy（如 Zeabur）在 error 事件後仍掛著 SSE 連線，
                        // finally 不會馬上跑，送出鈕會卡在紅色中止圖案
                        setSessionAnalyzing(false, sessionIdAtStart);
                        if (sessionIdAtStart === window.currentSessionId) {
                            resetChatUI();
                        }
                    }
                }
            }
        }

        // Stream-close fallback（2026-09-07「回答完思考還一直轉圈」）：串流
        // 正常關閉但沒收到 done 幀（proxy／SSE 提早斷線）時，迴圈安靜退出、
        // 沒人做最終渲染——最後一次串流渲染的思考轉圈與串流樣式會永久殘留。
        // done／error 幀都會把這個對話的分析旗標翻成 false，所以這裡只攔「漏接
        // done」的情境，用與 done 路徑相同的收尾渲染。
        // HITL 暫停（done+waiting）不是漏接：旗標沒翻是正常的，這時重繪會把
        // 剛畫好的同意卡／clarify 卡整張蓋掉，只剩空白或 fallback 提示。
        if (!hitlPaused && isSessionAnalyzing(sessionIdAtStart)) {
            cancelStreamRender();
            const totalTimeFallback = ((Date.now() - startTime) / 1000).toFixed(1);
            botMsgDiv.innerHTML = renderStoredBotMessage(fullContent, false, totalTimeFallback);
            if (reasoningContent) {
                botMsgDiv.insertAdjacentHTML('afterbegin', renderThinkingBlock(reasoningContent, false));
            }
            if (window.lucide) {
                lucide.createIcons({ nodes: [botMsgDiv] });
            }
            setSessionAnalyzing(false, sessionIdAtStart);
        }
    } catch (err) {
        cancelStreamRender();  // 同 done/error：不讓待處理的 rAF 蓋掉錯誤訊息
        if (err.name === 'AbortError') {
            const abortReason = localAnalysisController.signal.reason;
            if (abortReason instanceof Error && abortReason.message === 'ANALYSIS_TIMEOUT') {
                botMsgDiv.innerHTML =
                    '<span class="text-red-400">' + (window.I18n ? window.I18n.t('chat.analysisTimeout') : 'Analysis timeout. Please narrow down your question and try again.') + '</span>';
            } else if (
                abortReason instanceof Error &&
                abortReason.message === 'STREAM_IDLE_TIMEOUT'
            ) {
                botMsgDiv.innerHTML =
                    '<span class="text-red-400">' + (window.I18n ? window.I18n.t('chat.streamIdleTimeout') : 'Analysis flow idle for too long, automatically stopped. Please retry.') + '</span>';
            } else {
                console.log('Analysis aborted by user');
                botMsgDiv.innerHTML = '<span class="text-orange-400">' + (window.I18n ? window.I18n.t('chat.analysisCancelled') : 'Analysis cancelled.') + '</span>';
            }
        } else if (isConnectionLostError(err)) {
            // 手機切到別的 App、螢幕關閉、網路切換都會讓串流連線中斷。
            // 伺服器端不會取消分析（見 analysis.py 的 detach_analysis），
            // 結果會照常寫進對話紀錄，所以這裡不該報錯嚇人，改成告知並自動取回。
            console.warn('Stream connection lost, analysis continues on server:', err);
            botMsgDiv.innerHTML = `<span class="text-amber-400">${escapeHtml(
                window.I18n
                    ? window.I18n.t('chat.analysisContinuesInBackground')
                    : 'Connection dropped. The analysis is still running and the result will appear here shortly.'
            )}</span>`;
            resumeAnalysisStream({
                sessionId: sessionIdAtStart,
                runId: activeRunId,
                afterEventId: lastEventId,
                statusEl: botMsgDiv,
                contentSoFar: fullContent,
                hitlResumeContext: _hitlResumeContext,
            });
        } else {
            console.error(err);
            botMsgDiv.innerHTML = `<span class="text-red-400">${escapeHtml(
                normalizeChatErrorMessage(
                    err?.message,
                    window.I18n
                        ? window.I18n.t('chat.sendFailedCheckModel')
                        : window.I18n ? window.I18n.t('chat.sendFailedModel') : 'Send failed.'
                )
            )}</span>`;
        }
        clearInterval(timerInterval);
        clearAnalysisTimeouts();
        setSessionAnalyzing(false, sessionIdAtStart);
        // resetChatUI 動的是「目前」對話的旗標、Stop 鈕與 run id
        if (sessionIdAtStart === window.currentSessionId) {
            resetChatUI();
        }
    } finally {
        clearInterval(timerInterval);
        clearAnalysisTimeouts();

        clearAnalysisController(sessionIdAtStart);
        setSessionAnalyzing(false, sessionIdAtStart);
        // 這一輪串流結束：清掉這個對話的 run id（停下來等回答時，核准用的是 HITL 上下文裡的 runId）
        if (activeRunId && getSessionRunId(sessionIdAtStart) === activeRunId) {
            setSessionRunId(null, sessionIdAtStart);
        }

        // 背景完成保護：若使用者已切到別的 session，不要動當前可見的 DOM，
        // 否則會把新 session 的輸入框/發送鈕狀態污染成舊 session 的樣子
        const isStillActiveSession = sessionIdAtStart === window.currentSessionId;

        if (!isStillActiveSession) {
            // 不能 resetChatUI：它把目前對話的分析旗標清掉、Stop 鈕打回送出鈕。
            // 依目前對話自己的狀態重畫即可（它可能也正在分析）
            syncChatUIForCurrentSession();
            return;
        }

        if (hitlPaused) {
            enterHitlPausedUI(botMsgDiv);
        } else {
            // Normal finish or Abort
            resetChatUI();

            // 防禦性兜底（2026-08-23）：串流「正常結束」但 botMsgDiv 仍空或只剩
            // spinner＝事件全丟失的 edge case（頁面長開/多輪切換後觀察過兩次：
            // worker 已完成寫 DB、前端卻停在舊畫面）。結果必已在對話紀錄——
            // 直接從 DB 刷新救回。正常路徑（內容已串流渲染）不會觸發。
            const bubbleEmptyOrSpinnerOnly =
                botMsgDiv &&
                sessionIdAtStart === window.currentSessionId &&
                (
                    (botMsgDiv.innerText || '').trim() === '' ||
                    botMsgDiv.querySelector('.typing-indicator, [data-spinner], .animate-pulse')
                ) &&
                typeof window.loadChatHistory === 'function';

            // 防禦性兜底二（2026-08-25）：串流結束但初始進度卡（div.process-container）
            // 從沒被內容取代＝final/done 事件沒送達，泡泡會永遠停在
            // 「正在整理回覆」+ 各工具 spinner 的轉圈狀態（線上回報：回答完成
            // 後仍持續轉圈）。有 run_id 時先查伺服器端狀態：已結束→從 DB 救回；
            // 還在跑→交給背景輪詢接手，不要清掉使用者的畫面。
            // 注意 selector 用 div.process-container：完成路徑渲染的是
            // details.process-container（Analysis Steps 卡），不能誤判。
            const leftoverInitialProgressCard =
                botMsgDiv &&
                sessionIdAtStart === window.currentSessionId &&
                botMsgDiv.querySelector('div.process-container') &&
                typeof window.loadChatHistory === 'function';

            if (bubbleEmptyOrSpinnerOnly) {
                window.loadChatHistory(sessionIdAtStart);
            } else if (leftoverInitialProgressCard) {
                if (!activeRunId) {
                    window.loadChatHistory(sessionIdAtStart);
                } else {
                    AppAPI.get(`/api/analyze/status/${encodeURIComponent(activeRunId)}`)
                        .then((run) => {
                            if (!run || !run.success || window.currentSessionId !== sessionIdAtStart) return;
                            if (run.status === 'running' || run.status === 'detached' || run.status === 'waiting') {
                                watchForBackgroundResult(sessionIdAtStart, activeRunId, botMsgDiv);
                            } else {
                                window.loadChatHistory(sessionIdAtStart);
                            }
                        })
                        .catch(() => { /* 狀態查詢失敗就保守不動，避免誤清畫面 */ });
                }
            }
        }
    }
}
window.sendMessage = sendMessage;

// CSP-safe 綁定 Enter 送出：取代 index.html 內被 prod 嚴格 CSP 擋掉的
// inline onkeypress（keypress 已棄用，改 keydown；isComposing 防中文輸入法
// 選字時的 Enter 誤送）。ES module 為 deferred，執行時 DOM 已就緒。
(function bindChatInputEnter() {
    const input = document.getElementById('user-input');
    if (!input || input.dataset.boundEnter === '1') return;
    input.dataset.boundEnter = '1';
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
            e.preventDefault();
            sendMessage();
        }
    });
    input.addEventListener('input', () => {
        const sendBtn = document.getElementById('send-btn');
        if (!sendBtn) return;
        const hasText = input.value.trim().length > 0;
        const analyzing = window.isSessionAnalyzing && window.isSessionAnalyzing(window.currentSessionId);
        if (hasText && !analyzing) {
            sendBtn.classList.remove('opacity-40', 'cursor-not-allowed');
            sendBtn.disabled = false;
        } else {
            sendBtn.classList.add('opacity-40', 'cursor-not-allowed');
        }
    });
})();

function stopAnalysis() {
    // 可信 AI 第 6 要素（Revocation）：不只是前端斷線，要真正終止後端 agent。
    // 若有 activeRunId，呼叫 revoke endpoint cancel 底層 task（fire-and-forget）。
    // 撤銷的是「目前對話」自己的 run——背景對話的 run 不能被這裡撤掉。
    const rid = getSessionRunId(window.currentSessionId);
    if (rid) {
        setSessionRunId(null, window.currentSessionId);
        if (window.AppAPI && window.AppAPI.post) {
            window.AppAPI.post(`/api/analyze/${encodeURIComponent(rid)}/revoke`).catch(
                function (e) {
                    console.warn('Revoke request failed (non-blocking):', e);
                }
            );
        }
    }

    if (getAnalysisController()) {
        getAnalysisController().abort();
        clearAnalysisController();
    }

    // IMPORTANT: Clear HITL context so subsequent messages are treated as new queries
    window._hitlContext = null;

    // Append "Stopped" message
    const chatContainer = document.getElementById('chat-messages');
    if (chatContainer) {
        const stopMsg = document.createElement('div');
        stopMsg.className = 'flex justify-center my-4 opacity-0 animate-fade-in-up';
        stopMsg.style.animationFillMode = 'forwards';
        stopMsg.innerHTML =
            '<span class="px-3 py-1 rounded-full bg-red-500/10 text-red-500 text-xs font-mono border border-red-500/20">⛔ ' + (window.I18n ? window.I18n.t('chat.analysisTerminated') : 'Analysis terminated') + '</span>';
        chatContainer.appendChild(stopMsg);
        setTimeout(() => (chatContainer.scrollTop = chatContainer.scrollHeight), 100);
    }

    resetChatUI();
}
window.stopAnalysis = stopAnalysis;

function resetChatUI() {
    isAnalyzing = false;
    setSessionRunId(null, window.currentSessionId); // 清掉目前對話的 run_id，避免 stopAnalysis 拿到已結束的 run
    const input = document.getElementById('user-input');
    const sendBtn = document.getElementById('send-btn');

    if (input) {
        input.disabled = false;
        input.classList.remove('opacity-50');
        input.focus();
    }
    if (sendBtn) {
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
window.resetChatUI = resetChatUI;

// Reuse the renderStoredBotMessage function from previous step
/**
 * 思考過程的可摺疊區塊。
 *
 * 為什麼要有：推理模型在最後一次呼叫要先產 1000+ 個 reasoning token（實測 ≈9 秒）
 * 才吐出第一個可見字元，中間前端完全空白——就是使用者回報的「卡很久才出字」。
 * 把思考內容串出來，等待就變成看得見的進度，不必為了體感去關掉推理（關了答案會變差）。
 *
 * 預設收合：思考內容通常又長又是模型的自言自語，攤開會把答案擠到看不見。
 * 收合狀態沿用 process-container 那套 AppStore 記憶，使用者展開過就記住。
 */
function renderThinkingBlock(reasoningText, isStreaming = false) {
    if (!reasoningText) return '';
    const isOpen = AppStore.get('lastThinkingOpenState') === true;
    const label = window.I18n
        ? window.I18n.t('chat.thinkingProcess')
        : 'Thinking process';
    const charCount = reasoningText.length;
    // 字數後綴常駐（2026-09-07 回報「之前是字數、完成後顯示不太一樣」）：
    // 串流中與完成後的標頭格式要一致——完成態只掉 spinner、不掉字數。
    const suffix = ` · ${charCount}${window.I18n ? window.I18n.t('chat.thinkingChars') : ' chars'}`;
    const icon = isStreaming
        ? '<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin text-primary shrink-0"></i>'
        : '<i data-lucide="brain" class="w-3.5 h-3.5 text-textMuted shrink-0"></i>';
    // 思考內容是模型自由生成的文字，一律當純文字插入（escapeHtml），
    // 不走 markdown 渲染——它不是要給人讀的排版，而且省一次解析。
    return `
        <details class="thinking-container" ${isOpen ? 'open' : ''}>
            <summary data-click="toggleThinkingState" data-click-element>
                ${icon}
                <span class="thinking-label">${label}${suffix}</span>
                <i data-lucide="chevron-down" class="thinking-chevron w-3.5 h-3.5 shrink-0"></i>
            </summary>
            <div class="thinking-body">${escapeHtml(reasoningText)}</div>
        </details>`;
}

// chat-history.js 重播歷史訊息時共用同一個渲染（metadata.reasoning）——
// 兩邊各寫一份 HTML 遲早會長歪。
window.renderThinkingBlock = renderThinkingBlock;

/** 記住展開狀態，對齊 toggleProcessState 的做法。 */
function toggleThinkingState(summaryElement) {
    const details = summaryElement && summaryElement.closest('details');
    if (!details) return;
    // summary 的預設行為會在此之後才切換 open，所以這裡取反
    AppStore.set('lastThinkingOpenState', !details.open);
}
window.toggleThinkingState = toggleThinkingState;

function renderStoredBotMessage(fullContent, isStreaming = false, elapsedTime = null) {
    fullContent = normalizeRenderedChatContent(fullContent);

    // ⚠️ 2026-08-10: Swap 整體關閉(design 附錄) — 不再處理 SWAP_QUOTE_READY sentinel。
    // 後端 claw_loop 已停止附加 sentinel;這裡保留 sentinel 清理(防舊訊息殘留),
    // 但不再插入任何執行按鈕。

    let processContent = '';
    let resultContent = '';
    let hasProcessContent = false;

    const contentLines = fullContent.split('\n');
    let currentMode = 'normal';

    for (const cLine of contentLines) {
        if (cLine.includes('[PROCESS_START]')) {
            currentMode = 'process';
            hasProcessContent = true;
            continue;
        }
        if (cLine.includes('[PROCESS_END]')) {
            currentMode = 'normal';
            continue;
        }
        if (cLine.includes('[RESULT]')) {
            currentMode = 'result';
            continue;
        }
        if (cLine.startsWith('[PROCESS]')) {
            processContent += cLine.substring(9) + '\n';
            hasProcessContent = true;
        } else if (currentMode === 'process') {
            processContent += cLine + '\n';
        } else if (currentMode === 'result') {
            resultContent += cLine + '\n';
        } else {
            resultContent += cLine + '\n';
        }
    }

    let html = '';
    if (hasProcessContent && processContent.trim()) {
        const stepCount = (processContent.match(/✅|📊|⚔️|👨‍⚖️|⚖️|🛡️|💰|🚀|🔍|⏳/g) || []).length;
        const processLines = processContent
            .trim()
            .split('\n')
            .filter((l) => l.trim());
        let stepsHtml = '';
        let hasTimeInfo = false;

        processLines.forEach((line, index) => {
            const trimmed = line.trim();
            const isLastLine = index === processLines.length - 1;

            // Determine content
            let lineContent = '';
            if (trimmed.startsWith('---') || trimmed.startsWith('###')) {
                lineContent = `<div class="mt-3 mb-2 text-accent font-semibold text-sm">${md.renderInline(trimmed.replace(/^---\s*/, '').replace(/^###\s*/, ''))}</div>`;
            } else if (
                trimmed.startsWith('**🐂') ||
                trimmed.startsWith('**🐻') ||
                trimmed.startsWith('**⚖️')
            ) {
                lineContent = `<div class="mt-2 font-medium text-secondary">${md.renderInline(trimmed)}</div>`;
            } else if (trimmed.startsWith('>')) {
                lineContent = `<div class="pl-3 border-l-2 border-borderLight text-textMuted text-xs my-1">${md.renderInline(trimmed.substring(1).trim())}</div>`;
            } else if (trimmed.startsWith('→')) {
                lineContent = `<div class="pl-4 text-textMuted/60 text-xs">${trimmed}</div>`;
            } else if (trimmed.includes('⏱️ **分析完成**: 總耗時')) {
                hasTimeInfo = true;
                const timeMatch = trimmed.match(/⏱️ \*\*分析完成\*\*: 總耗時 ([\d.]+) 秒/);
                if (timeMatch) {
                    lineContent = `<div class="mt-2 p-3 rounded-xl bg-surface border border-borderLight flex items-center gap-2">
                                    <span class="text-primary">⏱️</span>
                                    <span class="text-textMuted">${window.I18n ? window.I18n.t('chat.totalTimeLabel') : 'Total time'}: <span class="text-secondary font-mono">${timeMatch[1]} ${window.I18n ? window.I18n.t('chat.secondsUnit') : 'sec'}</span></span>
                                  </div>`;
                }
            } else {
                lineContent = `<div class="process-step-item py-1">${md.renderInline(trimmed)}</div>`;
            }

            // Append Loading Spinner to the last line if streaming
            if (isStreaming && isLastLine && !trimmed.includes('分析完成')) {
                const spinnerSvg = `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-loader-2 animate-spin inline-block ml-2 text-primary"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>`;

                // Check if it's a div wrapper (standard lines) or just text
                if (lineContent.includes('<div')) {
                    // Insert before the closing div
                    lineContent = lineContent.replace('</div>', ` ${spinnerSvg}</div>`);
                } else {
                    lineContent += ` ${spinnerSvg}`;
                }
            }
            stepsHtml += lineContent;
        });

        // 工具/分析過程預設「收合」，與 Gemini / ChatGPT / Claude 一致：
        // 摘要列已含 spinner、計時器、步驟數，收合狀態也看得到進度，想看細節再點開。
        // 使用者手動展開後，AppStore 會記住偏好並沿用到後續訊息。
        const isCurrentlyOpen =
            AppStore.get('lastProcessOpenState') !== undefined ? AppStore.get('lastProcessOpenState') : false;

        // 如果在步驟中沒有找到時間信息，則檢查完整內容
        let timeInfo = '';
        let timerHeader = '';

        if (!hasTimeInfo) {
            const timeMatch = fullContent.match(
                /\[PROCESS\]⏱️ \*\*分析完成\*\*: 總耗時 ([\d.]+) 秒/
            );
            if (timeMatch) {
                timeInfo = `<div class="mt-2 p-3 rounded-xl bg-surface border border-borderLight flex items-center gap-2">
                              <span class="text-primary">⏱️</span>
                              <span class="text-textMuted">${window.I18n ? window.I18n.t('chat.totalTimeLabel') : 'Total time'}: <span class="text-secondary font-mono">${timeMatch[1]} ${window.I18n ? window.I18n.t('chat.secondsUnit') : 'sec'}</span></span>
                            </div>`;
            } else if (isStreaming && elapsedTime) {
                // Live Timer in Header - Reuses the ID so the interval keeps updating it
                timerHeader = `<span class="ml-2 px-2 py-0.5 rounded-full bg-primary/10 text-primary text-[10px] font-mono flex items-center gap-1">
                                <i data-lucide="clock" class="w-3 h-3"></i> 
                                <span id="loading-timer">${elapsedTime}s</span>
                               </span>`;
            }
        }

        html += `
            <details class="process-container" ${isCurrentlyOpen ? 'open' : ''}>
                <summary data-click="toggleProcessState" data-click-element>
                    <div class="flex items-center gap-2">
                        <i data-lucide="chevron-right" class="w-4 h-4 chevron"></i>
                        ${isStreaming ? '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-loader animate-spin text-primary"><path d="M12 2v4"/><path d="m16.2 7.8 2.9-2.9"/><path d="M18 12h4"/><path d="m16.2 16.2 2.9 2.9"/><path d="M12 18v4"/><path d="m4.9 19.1 2.9-2.9"/><path d="M2 12h4"/><path d="m4.9 4.9 2.9 2.9"/></svg>' : '<i data-lucide="check-circle" class="w-4 h-4 text-green-500"></i>'}
                        <span class="font-medium">${window.I18n ? window.I18n.t('chat.analysisSteps') : 'Analysis Steps'}</span>
                        ${timerHeader}
                    </div>
                    <span class="ml-auto text-xs text-textMuted/50">${stepCount} ${window.I18n ? window.I18n.t('chat.steps', { count: stepCount }) : 'steps'}</span>
                </summary>
                <div class="process-content custom-scrollbar pl-6 border-l border-borderSubtle ml-2 mt-2 space-y-1">
                    ${stepsHtml}
                </div>
                ${timeInfo}
            </details>
        `;
    }

    // 渲染前先移除 LLM 殘留的行內 HTML(如 <sup>[3]</sup>),避免 markdown-it
    // (html:false) 把它們當字面文字顯示。stripInlineHtml 為 app.js 全域函式。
    const renderMd = (text) => {
        const cleaned = window.stripInlineHtml ? window.stripInlineHtml(text) : text;
        return md ? md.render(cleaned) : `<pre>${cleaned.replace(/</g, '&lt;')}</pre>`;
    };

    if (resultContent.trim()) {
        html += `<div class="result-container prose mt-4">${renderMd(resultContent)}</div>`;
    } else if (!hasProcessContent) {
        let timerHtml = '';
        if (isStreaming && elapsedTime) {
            timerHtml = `<div class="flex items-center gap-2 mb-2 text-xs text-textMuted/50 font-mono">
                            <i data-lucide="loader-2" class="w-3 h-3 animate-spin"></i>
                            <span id="loading-timer">${elapsedTime}s</span>
                          </div>`;
        }
        html = timerHtml + renderMd(fullContent);
    }

    // ⚠️ 2026-08-10: Swap 整體關閉(design 附錄) — 不再插入執行按鈕。
    // 平台完全退出交換鏈路;sentinel 清理保留在函式開頭(防舊訊息殘留)。

    // Wrap <table> elements for proper overflow + border styling
    if (html.includes('<table')) {
        const temp = document.createElement('div');
        temp.innerHTML = html;
        temp.querySelectorAll('table').forEach((table) => {
            if (!table.parentElement.classList.contains('table-wrapper')) {
                const wrapper = document.createElement('div');
                wrapper.className = 'table-wrapper';
                table.parentNode.insertBefore(wrapper, table);
                wrapper.appendChild(table);
            }
        });
        html = temp.innerHTML;
    }

    if (!isStreaming) {
        var disclaimer = window.I18n ? window.I18n.t('chat.disclaimer') : '⚠️ AI analysis, not investment advice. Assess the risk yourself before trading.';
        html += '<div class="mt-3 text-[11px] text-textMuted/60 border-t border-borderSubtle pt-2">' + disclaimer + '</div>';
    }

    return html;
}
window.renderStoredBotMessage = renderStoredBotMessage;

// 保存展開狀態的函數
function toggleProcessState(summaryElement) {
    // 獲取對應的 details 元素
    const detailsElement = summaryElement.parentElement;
    // 延遲執行以確保狀態已更新
    setTimeout(() => {
        // 更新狀態標記
        AppStore.set('lastProcessOpenState', detailsElement.open);
    }, 0);
}
window.toggleProcessState = toggleProcessState;

export {
    cleanupStaleButtons,
    renderResponseMetadata,
    sendMessage,
    stopAnalysis,
    resetChatUI,
    renderStoredBotMessage,
    toggleProcessState,
};
