/**
 * 聊天室 AI 助理（設計 docs/plans/2026-10-01-chat-assistant-design.md）
 *
 * - 好友頁（SocialHub：私訊＋群組，所有寬度）用這支：API、SSE、抽屜。
 * - 只有自己看得到。問答存伺服器（每個聊天室最近 10 則、7 天，2026-10-04 DANNY），跨裝置接續：
 *   一打開抽屜就讀回來；誤關／斷線不會丟（伺服器在背景繼續產生，回來打開看得到「還在整理」→ 答案）。
 *   不寫 localStorage。回答不會自動發到聊天室：「分享」要使用者確認，才以自己的名義送出一則 AI 分析卡片
 *   （內容由伺服器依問答 id 取，不佔 500 字；表格／標題照原樣顯示，見 ai-card.js、ai-markdown.js）。
 * - 開關 chat_assistant_enabled 關著 → status 404 → 不顯示 ✨、選單也沒有「問 AI」。
 * - 「我沒看的」：一打開聊天室就會標已讀，所以由呼叫端在標已讀之前記下來（私訊＝未讀數、群組＝from_id）。
 */

import { aiMarkdownToPlain, renderAiMarkdown } from './ai-markdown.js';
import { attachSelectionAsk } from './selection-ask.js';

const t = (key, args) => (window.I18n ? window.I18n.t(key, args) : key);

/** 供應商顯示名（settings.ai.providerNames，跟設定頁同一份）；沒有翻譯就用代號 */
function providerLabel(provider) {
    const key = `settings.ai.providerNames.${provider}`;
    const text = t(key);
    return text && text !== key ? text : provider;
}

const STATUS_TTL_MS = 30000;
const HISTORY_MAX = 6; // 同後端 AskRequest.history max_length
const TURNS_MAX = 10; // 同後端 chat_assistant_history_repo.MAX_TURNS
const POLL_TICK_MS = 1000; // 「還在整理」的計時顯示；每 3 個 tick 問伺服器一次
const POLL_EVERY_TICKS = 3;
const POLL_MAX_MS = 180000; // 後端總預算 120 秒，超過就當失敗
const QUESTION_MAX = 500; // 同後端 AskRequest.question max_length
const QUOTE_ROOM = 80; // 引用之外留給使用者自己打問題的字數

/**
 * 選取的句子帶進輸入框的格式：「句子」＋換行，游標停在最後讓使用者接著打問題（2026-10-05）。
 * 引用太長就截斷，保證還留得下 QUOTE_ROOM 個字給問題（輸入框 maxLength 是 QUESTION_MAX）。
 */
function quoteForInput(text) {
    const clean = String(text ?? '').replace(/\s+/g, ' ').trim();
    if (!clean) return '';
    const room = QUESTION_MAX - QUOTE_ROOM - 3; // 3＝「」與換行
    const body = clean.length > room ? `${clean.slice(0, room - 1).trimEnd()}…` : clean;
    return `「${body}」\n`;
}
const PREMIUM_URL = '/static/forum/premium.html';
const RANGES = ['recent', 'unread', '24h', '3d'];
const KNOWN_ERRORS = new Set([
    'quota_exhausted',
    'pro_required',
    'assistant_busy_self',
    'busy',
    'timeout',
    'llm_failed',
    'own_key_failed',
    'no_model',
    'not_found',
    'network',
]);
const UPGRADE_ERRORS = new Set(['quota_exhausted', 'pro_required']);

let statusCache = null; // { at, value }
let current = null; // 開著的抽屜 { close }

// ── 純邏輯（tests/js/chat_assistant.mjs）──────────────────────────

/**
 * SSE 緩衝 → 完整事件＋剩下的半包。換行先統一（代理可能改成 \r\n）；
 * final＝串流結束：最後一則可能沒有空行結尾，剩下的也要解析（不然回答會被吃掉）
 */
function parseSseChunk(buffer, { final = false } = {}) {
    const parts = buffer.replace(/\r\n?/g, '\n').split('\n\n');
    const rest = final ? '' : parts.pop();
    const events = [];
    for (const part of parts) {
        for (const line of part.split('\n')) {
            if (!line.startsWith('data: ')) continue;
            try {
                events.push(JSON.parse(line.slice(6)));
            } catch (_e) {
                // 壞掉的一行跳過，不拖垮整包
            }
        }
    }
    return { events, rest };
}

/** 抽屜狀態 → POST body。追問只帶最新 6 則，而且從 user 那則開始（不要半輪） */
function buildAskBody(state, question, history) {
    const body = {
        kind: state.kind,
        target_id: Number(state.targetId),
        question: String(question || '').trim().slice(0, QUESTION_MAX),
        range: state.range,
        language: state.language,
        tz: state.tz,
        history: [],
    };
    if (state.range === 'unread' && state.unread) {
        if (state.unread.from_id != null) body.from_id = Number(state.unread.from_id);
        else if (state.unread.unread_count != null) body.unread_count = Number(state.unread.unread_count);
    }
    if (state.range === 'message') body.around_id = Number(state.aroundId);
    let recent = history.slice(-HISTORY_MAX);
    while (recent.length && recent[0].role !== 'user') recent = recent.slice(1);
    body.history = recent.map((h) => ({ role: h.role, content: String(h.content).slice(0, 2000) }));
    return body;
}

function errorKey({ code, status }) {
    if (KNOWN_ERRORS.has(code)) return `assistant.errors.${code}`;
    if (status === 429) return 'assistant.errors.rate_limited';
    if (status === 404) return 'assistant.errors.not_found';
    return 'assistant.errors.generic';
}

/** 分享（送卡片）失敗 → i18n key：額度用完、這則已過期／不在這個聊天室、不是 Pro，其他一律「送出失敗」 */
function shareErrorKey({ code, status }) {
    if (status === 429) return 'assistant.shareErrors.limit';
    if (status === 404 || code === 'not_found') return 'assistant.shareErrors.notFound';
    if (code === 'pro_required') return 'assistant.errors.pro_required';
    return 'assistant.shareErrors.generic';
}

/** stage 事件 → [i18n key, 參數]；不認得回 null */
function progressKey(event) {
    const known = ['reading', 'queued', 'condensing', 'thinking'];
    if (!event || event.type !== 'stage' || !known.includes(event.stage)) return null;
    const args = event.stage === 'condensing' ? { done: event.done || 0, total: event.total || 0 } : undefined;
    return [`assistant.progress.${event.stage}`, args];
}

/**
 * 回答 → 安全的 HTML（表格、標題、清單；夾在裡面的 HTML 先轉回 markdown）。
 * 跟分享出去的 AI 分析卡片是同一個渲染（ai-markdown.js）：抽屜裡看到的就是別人會看到的；
 * 也不再靠 markdown-it——手機私訊頁（messages.html）沒載它，以前那邊的表格是一坨字。
 */
function renderAnswerHtml(text) {
    return renderAiMarkdown(text);
}

/** status 回應 → 物件；404（開關關）或任何錯誤都當作不可用（null），不拋 */
async function readStatusResponse(promise) {
    try {
        return (await promise) || null;
    } catch (_e) {
        return null;
    }
}

/** 回答（markdown，可能夾 HTML）→ 純文字（複製用）：表格變成一列一行，標題、粗體符號拿掉 */
function markdownToPlain(text) {
    return aiMarkdownToPlain(text);
}

/** 問答時間：今天只顯示時間，其他天加上 月/日（本機時區） */
function formatTurnTime(iso, now = new Date(), lang = 'zh-TW') {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const time = d.toLocaleTimeString(lang, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
    const sameDay = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
    return sameDay ? time : `${d.getMonth() + 1}/${d.getDate()} ${time}`;
}

/** 存在伺服器的問答 → 追問的上下文（buildAskBody 再取最新 6 則） */
function turnsToHistory(turns) {
    return turns.flatMap((turn) => [
        { role: 'user', content: turn.question },
        { role: 'assistant', content: turn.answer },
    ]);
}

async function copyToClipboard(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch (_e) {
        // 沒有權限／非 https：退回 execCommand
    }
    try {
        const area = document.createElement('textarea');
        area.value = text;
        area.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
        document.body.appendChild(area);
        area.select();
        const ok = document.execCommand('copy');
        area.remove();
        return ok;
    } catch (_e) {
        return false;
    }
}

function toast(message, type = 'success') {
    window.showToast?.(message, type);
}

// 抽屜的 z-index（手機私訊頁右上角主題鈕是 9999，所以抽屜明確設 10000）；從抽屜開的確認框要再高一層
const DRAWER_Z = 10000;

/**
 * 確認框一律走官方樣式（ui-shell 的 showConfirmDialog），不跳瀏覽器的「xxx.com 說」。
 * 層級要高過抽屜，不然確認框會被蓋在後面（先前就是為了這個才退回原生 confirm）。
 * ui-shell 沒載入（獨立頁）才退回原生。
 */
async function confirmAction({ title, message, confirmText, danger = false }) {
    if (typeof window.showConfirmDialog === 'function') {
        return window.showConfirmDialog({ title, message, confirmText, danger, zIndex: DRAWER_Z + 1 });
    }
    return window.confirm(message || title);
}

// ── API ─────────────────────────────────────────────────────

const ChatAssistantAPI = {
    async status({ fresh = false } = {}) {
        if (!fresh && statusCache && Date.now() - statusCache.at < STATUS_TTL_MS) return statusCache.value;
        if (typeof AppAPI === 'undefined') return null;
        const value = await readStatusResponse(AppAPI.get('/api/chat-assistant/status', { retries: 0 }));
        statusCache = { at: Date.now(), value };
        return value;
    },

    /** 這個聊天室存著的問答（由舊到新）＋伺服器還在整理的那題 {question, elapsed}｜null；失敗回 null（不拋） */
    async history(kind, targetId) {
        if (typeof AppAPI === 'undefined') return null;
        try {
            const data = await AppAPI.get(
                `/api/chat-assistant/history?kind=${encodeURIComponent(kind)}&target_id=${Number(targetId)}`,
                { retries: 0 }
            );
            return data && Array.isArray(data.turns) ? { turns: data.turns, pending: data.pending || null } : null;
        } catch (_e) {
            return null;
        }
    },

    /**
     * 把一則問答分享成聊天室裡的 AI 分析卡片：走一般送訊息的 API（好友／封鎖／群組權限、額度、推播、通知
     * 都照舊），只多帶 assistant_turn_id，內容由伺服器取。成功 {ok:true, data}；失敗 {ok:false, code, status}。
     */
    async shareTurn({ kind, userId, groupId }, turnId) {
        if (typeof AppAPI === 'undefined') return { ok: false, code: 'network' };
        try {
            const data =
                kind === 'group'
                    ? await AppAPI.post(`/api/groups/${Number(groupId)}/messages`, { assistant_turn_id: Number(turnId) }, { retries: 0 })
                    : await AppAPI.post('/api/messages/send', { to_user_id: userId, assistant_turn_id: Number(turnId) }, { retries: 0 });
            return { ok: true, data };
        } catch (e) {
            return { ok: false, code: e?.message, status: e?.status };
        }
    },

    async deleteTurn(id) {
        try {
            await AppAPI.delete(`/api/chat-assistant/history/${Number(id)}`, { retries: 0 });
            return true;
        } catch (_e) {
            return false;
        }
    },

    async clearHistory(kind, targetId) {
        try {
            await AppAPI.delete(
                `/api/chat-assistant/history?kind=${encodeURIComponent(kind)}&target_id=${Number(targetId)}`,
                { retries: 0 }
            );
            return true;
        } catch (_e) {
            return false;
        }
    },

    /** POST＋讀 SSE；HTTP 錯誤與斷線都轉成 {type:'error'} 事件；中止（關抽屜）不回報 */
    async ask(body, onEvent, signal) {
        let response;
        try {
            response = await fetch('/api/chat-assistant/ask', {
                method: 'POST',
                headers: AppAPI.buildHeaders(),
                credentials: 'include',
                body: JSON.stringify(body),
                signal,
            });
        } catch (e) {
            if (e?.name !== 'AbortError') onEvent({ type: 'error', code: 'network' });
            return;
        }
        if (!response.ok) {
            let code = `http_${response.status}`;
            try {
                const data = await response.json();
                if (typeof data.detail === 'string') code = data.detail;
            } catch (_e) {
                // 非 JSON：用狀態碼
            }
            onEvent({ type: 'error', code, status: response.status });
            return;
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let finished = false;
        try {
            for (;;) {
                const { value, done } = await reader.read();
                buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
                const parsed = parseSseChunk(buffer, { final: done });
                buffer = parsed.rest;
                for (const event of parsed.events) {
                    if (event.type === 'answer' || event.type === 'error') finished = true;
                    onEvent(event);
                }
                if (done) break;
            }
        } catch (e) {
            if (e?.name === 'AbortError') return;
        }
        if (!finished && !signal?.aborted) onEvent({ type: 'error', code: 'network' });
    },
};

function assistantStatus(opts) {
    return ChatAssistantAPI.status(opts);
}

/** 同步讀快取：status 回來前一律 false（選單「問 AI」、✨ 先不出現） */
function assistantEnabled() {
    return !!statusCache?.value;
}

// ── 抽屜 ────────────────────────────────────────────────────

function el(tag, cls = '', text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
}

function currentLanguage() {
    const i18n = window.I18n;
    const lang = i18n?.getLanguage?.() || i18n?.currentLanguage || 'zh-TW';
    return ['zh-TW', 'zh-CN', 'en', 'ru'].includes(lang) ? lang : 'zh-TW';
}

function closeAssistantDrawer() {
    if (current) current.close();
}

/**
 * 開抽屜。ctx：{ kind:'dm'|'group', targetId, title, unread: {from_id}|{unread_count}|null, aroundId?,
 *   userId?（私訊：對方的 user_id，分享卡片要送給誰）, onShared?(data)（分享送出後，呼叫端把新訊息放進畫面） }
 * 回傳 { close }。同時只會有一個抽屜。
 */
function openAssistantDrawer(ctx) {
    closeAssistantDrawer();
    const state = {
        kind: ctx.kind,
        targetId: ctx.targetId,
        unread: ctx.unread || null,
        aroundId: ctx.aroundId || null,
        range: ctx.aroundId ? 'message' : 'recent',
        language: currentLanguage(),
        tz: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Taipei',
    };
    let turns = []; // 伺服器存著的這個聊天室的問答（由舊到新）；追問的上下文也從這來
    let busy = true; // 先讀回存著的問答（load）才開放提問，不然剛問的會被讀回來的蓋掉
    let sharing = false; // 分享送出中：不要連點送兩張卡片
    let confirming = false; // 確認框開著：連點不要開出第二個
    let pendingQuote = quoteForInput(ctx.quote); // 從訊息選取句子問 AI：輸入框一開放就帶進去
    let closed = false;
    let controller = null;

    // 手機私訊頁右上角的主題切換鈕是 z-index 9999（不然蓋住 ✕）
    const overlay = el('div', 'chat-assistant-panel fixed inset-0 z-[9999] bg-black/40 flex justify-end animate-fade-in');
    overlay.style.zIndex = String(DRAWER_Z); // 明確高過主題鈕，不靠 DOM 先後
    const card = el('div', 'w-full max-w-sm h-full bg-surface border-l border-borderSubtle shadow-2xl flex flex-col');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-label', t('assistant.title'));
    overlay.appendChild(card);

    // 標題
    const head = el('div', 'flex items-center gap-3 p-4 border-b border-borderSubtle');
    const titleBox = el('div', 'flex-1 min-w-0');
    titleBox.append(el('h3', 'font-bold text-textMain truncate', `✨ ${t('assistant.title')}`));
    if (ctx.title) titleBox.append(el('p', 'text-xs text-textMuted truncate', ctx.title));
    const clearBtn = el('button', 'hidden w-9 h-9 rounded-full flex items-center justify-center text-textMuted hover:bg-surfaceHighlight', '🗑');
    clearBtn.type = 'button';
    clearBtn.title = t('assistant.actions.clear');
    clearBtn.setAttribute('aria-label', t('assistant.actions.clear'));
    const closeBtn = el('button', 'w-9 h-9 rounded-full flex items-center justify-center text-textMuted hover:bg-surfaceHighlight', '✕');
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', t('common.close'));
    head.append(titleBox, clearBtn, closeBtn);

    // 範圍
    const chips = el('div', 'flex flex-wrap gap-1.5 px-4 pt-3');
    const chipButtons = {};
    const rangeList = state.aroundId ? ['message', ...RANGES] : RANGES;
    for (const range of rangeList) {
        const chip = el('button', '', t(`assistant.ranges.${range}`));
        chip.type = 'button';
        chip.dataset.range = range;
        if (range === 'unread' && !state.unread) {
            chip.disabled = true;
            chip.title = t('assistant.noUnread');
        }
        chip.addEventListener('click', () => {
            if (chip.disabled || busy) return;
            state.range = range;
            paintChips();
        });
        chipButtons[range] = chip;
        chips.append(chip);
    }
    function paintChips() {
        for (const [range, chip] of Object.entries(chipButtons)) {
            const active = range === state.range;
            chip.className = `px-3 py-1 rounded-full text-xs border transition ${
                active ? 'bg-primary/10 text-primary border-primary' : 'border-borderSubtle text-textMuted hover:bg-surfaceHighlight'
            } ${chip.disabled ? 'opacity-50 cursor-not-allowed' : ''}`;
            chip.setAttribute('aria-pressed', active ? 'true' : 'false');
        }
    }
    paintChips();

    const note = el('p', 'px-4 pt-2 text-[11px] text-textMuted', t('assistant.privateNote'));

    // 問答區＋快捷問題
    const log = el('div', 'flex-1 overflow-y-auto p-4 flex flex-col gap-3');
    const quick = el('div', 'flex flex-col gap-2');
    const quickPrompts = state.aroundId
        ? [['message', 'assistant.quick.message']]
        : [
              ['recent', 'assistant.quick.summary'],
              ['unread', 'assistant.quick.unread'],
          ];
    for (const [range, key] of quickPrompts) {
        const btn = el('button', 'text-left text-sm px-3 py-2 rounded-xl border border-borderSubtle text-textMain hover:bg-surfaceHighlight transition', t(key));
        btn.type = 'button';
        if (range === 'unread' && !state.unread) btn.disabled = true;
        btn.addEventListener('click', () => {
            if (btn.disabled || busy) return;
            state.range = range;
            paintChips();
            send(t(key));
        });
        quick.append(btn);
    }
    log.append(quick);

    // 輸入列
    const footer = el('div', 'border-t border-borderSubtle p-3 flex flex-col gap-2');
    footer.style.paddingBottom = 'calc(0.75rem + env(safe-area-inset-bottom, 0px))';
    const form = el('form', 'flex items-end gap-2');
    const input = el('textarea', 'flex-1 resize-none bg-background border border-borderLight rounded-xl px-3 py-2 text-sm text-textMain focus:outline-none focus:border-primary/50');
    input.rows = 2;
    input.maxLength = QUESTION_MAX;
    input.placeholder = t('assistant.placeholder');
    const sendBtn = el('button', 'px-3 py-2 rounded-xl bg-primary text-background text-sm font-medium disabled:opacity-50', t('assistant.send'));
    sendBtn.type = 'submit';
    form.append(input, sendBtn);
    input.disabled = true;
    sendBtn.disabled = true;
    const meta = el('p', 'text-[11px] text-textMuted');
    footer.append(form, meta);

    card.append(head, chips, note, log, footer);

    function paintMeta(status) {
        if (!status) return;
        // 自己綁的 key：寫供應商顯示名（設定頁同一份）；平台模型（含在設定選了 CryptoMind Lite）寫品牌名
        if (status.own_key && !status.platform_model) {
            meta.textContent = t('assistant.ownKey', { model: providerLabel(status.model_provider || status.model_label) });
        } else if (status.remaining != null) meta.textContent = t('assistant.remaining', { count: status.remaining });
        else meta.textContent = t('assistant.unlimited', { model: status.model_label });
    }
    assistantStatus({ fresh: true }).then((status) => !closed && paintMeta(status));

    function scrollToEnd() {
        log.scrollTop = log.scrollHeight;
    }

    function showError(slot, event) {
        slot.className = 'assistant-live self-start text-xs text-danger flex flex-col gap-2';
        slot.textContent = '';
        slot.append(el('span', '', t(errorKey(event))));
        if (UPGRADE_ERRORS.has(event.code)) {
            const go = el('a', 'self-start px-3 py-1 rounded-full bg-primary text-background text-xs font-medium', t('assistant.upgrade'));
            go.href = PREMIUM_URL;
            slot.append(go);
        }
    }

    function liveBubble(question) {
        return el('div', 'assistant-live self-end max-w-[85%] rounded-2xl px-3 py-2 bg-primary/10 text-sm text-textMain whitespace-pre-wrap break-words', question);
    }

    /** 把句子引用放進輸入框（原本有字就接在它後面一行，先選的在前），游標停在最後 */
    function putQuote(quote) {
        if (!quote) return;
        const rest = input.value.trim();
        input.value = rest ? `${rest}
${quote}` : quote;
        input.focus();
        const end = input.value.length;
        input.setSelectionRange?.(end, end);
    }

    function setBusy(value) {
        busy = value;
        input.disabled = value;
        sendBtn.disabled = value;
        if (!value && pendingQuote) {
            putQuote(pendingQuote); // 輸入框第一次開放（讀回問答之後）才帶，不然會被 disabled 吃掉
            pendingQuote = '';
        }
    }

    function actionButton(label, onClick) {
        const button = el('button', '', label);
        button.type = 'button';
        button.addEventListener('click', onClick);
        return button;
    }

    /**
     * 分享＝使用者確認後，以自己的名義把這則回答送進聊天室成一張 AI 分析卡片（不佔 500 字、表格照原樣）。
     * 內容由伺服器依問答 id 取；送出後由呼叫端（ctx.onShared）把新訊息放進畫面。
     */
    async function shareTurn(turn) {
        if (sharing || busy || confirming) return;
        if (turn.id == null) {
            toast(t('assistant.shareUnsaved'), 'error'); // 存檔失敗的問答沒有 id，伺服器取不到
            return;
        }
        const confirmKey = state.kind === 'group' ? 'assistant.shareConfirm.group' : 'assistant.shareConfirm.dm';
        confirming = true;
        const ok = await confirmAction({
            title: t('assistant.actions.share'),
            message: t(confirmKey, { chat: ctx.title || t('assistant.thisChat') }),
            confirmText: t('assistant.shareConfirmBtn'),
        });
        confirming = false;
        if (!ok || closed) return;
        sharing = true;
        const result = await ChatAssistantAPI.shareTurn({ kind: state.kind, userId: ctx.userId, groupId: state.targetId }, turn.id);
        sharing = false;
        if (!result.ok) {
            toast(t(shareErrorKey(result)), 'error');
            return;
        }
        ctx.onShared?.(result.data);
        toast(t(state.kind === 'group' ? 'assistant.shared.group' : 'assistant.shared.dm'));
        close();
    }

    async function copyTurn(turn) {
        const ok = await copyToClipboard(markdownToPlain(turn.answer));
        toast(t(ok ? 'assistant.actions.copied' : 'assistant.actions.copyFailed'), ok ? 'success' : 'error');
    }

    async function deleteTurn(turn) {
        if (busy) return;
        if (!(await ChatAssistantAPI.deleteTurn(turn.id))) {
            toast(t('assistant.deleteFailed'), 'error');
            return;
        }
        if (closed) return;
        turns = turns.filter((item) => item !== turn);
        paintTurns();
    }

    function renderTurn(turn) {
        const box = el('div', 'assistant-turn flex flex-col gap-2');
        box.append(el('div', 'self-end max-w-[85%] rounded-2xl px-3 py-2 bg-primary/10 text-sm text-textMain whitespace-pre-wrap break-words', turn.question));
        const answer = el('div', 'assistant-answer ai-md self-start max-w-full rounded-2xl px-3 py-2 bg-surfaceHighlight text-sm text-textMain break-words');
        answer.innerHTML = renderAnswerHtml(turn.answer || '');
        // 「最近」「這則」本來就只取最新的，不用提醒；長範圍（我沒看的／24h／3d）被截才提醒
        if (turn.meta?.truncated && !['recent', 'message'].includes(turn.meta.range)) {
            answer.append(el('p', 'mt-2 text-[11px] text-textMuted', t('assistant.truncated')));
        }
        const actions = el('div', 'assistant-actions self-start');
        const when = formatTurnTime(turn.created_at, new Date(), currentLanguage());
        if (when) actions.append(el('span', '', when));
        actions.append(actionButton(t('assistant.actions.copy'), () => copyTurn(turn)));
        actions.append(actionButton(t('assistant.actions.share'), () => shareTurn(turn)));
        if (turn.id != null) actions.append(actionButton(t('assistant.actions.delete'), () => deleteTurn(turn)));
        box.append(answer, actions);
        return box;
    }

    /** 依 turns 重畫問答區（含收起／放回快捷問題、清空鈕）；進行中／錯誤的暫時節點一併清掉 */
    function paintTurns() {
        log.querySelectorAll('.assistant-turn, .assistant-live').forEach((node) => node.remove());
        if (turns.length) quick.remove();
        else if (!busy && !quick.isConnected) log.prepend(quick);
        for (const turn of turns) log.append(renderTurn(turn));
        clearBtn.classList.toggle('hidden', !turns.length);
        scrollToEnd();
    }

    /**
     * 伺服器還在整理一題（關掉抽屜或斷線時它照跑）：顯示計時，每 3 秒問一次，好了就換成答案。
     * 沒等到答案（失敗、逾時）顯示錯誤。重用 slot／bubble＝接在剛才那題的畫面後面。
     */
    async function waitForServer(pending, { bubble, slot } = {}) {
        setBusy(true);
        quick.remove();
        const known = new Set(turns.map((turn) => turn.id));
        const asked = bubble || log.appendChild(liveBubble(pending.question));
        const label = slot || log.appendChild(el('div', 'assistant-live self-start text-xs text-textMuted'));
        label.className = 'assistant-live self-start text-xs text-textMuted animate-pulse';
        const startedAt = Date.now() - (pending.elapsed || 0) * 1000;
        let data = null;
        for (let tick = 1; !closed; tick++) {
            label.textContent = t('assistant.pending', { seconds: Math.floor((Date.now() - startedAt) / 1000) });
            scrollToEnd();
            await new Promise((resolve) => setTimeout(resolve, POLL_TICK_MS));
            if (closed) return;
            if (tick % POLL_EVERY_TICKS) continue;
            data = await ChatAssistantAPI.history(state.kind, state.targetId);
            if (closed) return;
            if (data && !data.pending) break;
            if (Date.now() - startedAt > POLL_MAX_MS) break;
        }
        if (closed) return;
        if (data) turns = data.turns;
        asked.remove();
        setBusy(false);
        const gained = !!data && data.turns.some((turn) => !known.has(turn.id));
        paintTurns();
        if (gained) {
            assistantStatus({ fresh: true }).then((status) => !closed && paintMeta(status));
        } else {
            const failed = el('div', 'assistant-live');
            showError(failed, { code: data ? 'llm_failed' : 'network' });
            log.append(failed);
            scrollToEnd();
        }
        input.focus();
    }

    /** 開抽屜：讀回存著的問答；伺服器還有一題在整理就接著等。讀不到（網路、聊天室看不到）就當空的 */
    async function load() {
        const data = await ChatAssistantAPI.history(state.kind, state.targetId);
        if (closed) return;
        if (data) turns = data.turns.slice(-TURNS_MAX);
        busy = false;
        paintTurns();
        if (data?.pending) {
            waitForServer(data.pending);
            return;
        }
        setBusy(false);
        if (window.matchMedia?.('(min-width: 768px)').matches) input.focus();
    }

    async function send(question) {
        const text = String(question || '').trim();
        if (!text || busy || closed) return;
        setBusy(true);
        input.value = '';
        quick.remove();
        const bubble = log.appendChild(liveBubble(text));
        const slot = el('div', 'assistant-live self-start text-xs text-textMuted animate-pulse', t('assistant.progress.reading'));
        log.append(slot);
        scrollToEnd();
        controller = new AbortController();
        const body = buildAskBody(state, text, turnsToHistory(turns));
        const knownIds = new Set(turns.map((turn) => turn.id));
        let dropped = false;
        await ChatAssistantAPI.ask(
            body,
            (event) => {
                if (closed) return;
                if (event.type === 'stage') {
                    const progress = progressKey(event);
                    if (progress) slot.textContent = t(progress[0], progress[1]);
                } else if (event.type === 'answer') {
                    turns = [
                        ...turns,
                        {
                            id: event.turn_id ?? null, // 存檔失敗（null）也先顯示，只是不能刪、重開不會在
                            question: text,
                            answer: event.text || '',
                            meta: { ...(event.meta || {}), range: body.range },
                            created_at: new Date().toISOString(),
                        },
                    ].slice(-TURNS_MAX);
                    paintTurns();
                    if (event.meta && event.meta.remaining != null) meta.textContent = t('assistant.remaining', { count: event.meta.remaining });
                } else if (event.type === 'error') {
                    if (event.code === 'network') dropped = true;
                    else showError(slot, event);
                }
                scrollToEnd();
            },
            controller.signal
        );
        controller = null;
        if (closed) return;
        if (dropped) {
            // 連線斷了不代表伺服器停了：問一下它是不是還在整理、或已經存好了
            const data = await ChatAssistantAPI.history(state.kind, state.targetId);
            if (closed) return;
            if (data?.pending) {
                waitForServer(data.pending, { bubble, slot });
                return;
            }
            if (data && data.turns.some((turn) => !knownIds.has(turn.id))) {
                turns = data.turns;
                paintTurns();
            } else {
                showError(slot, { code: 'network' });
            }
        }
        setBusy(false);
        input.focus();
    }

    clearBtn.addEventListener('click', async () => {
        if (busy || confirming || !turns.length) return;
        confirming = true;
        const ok = await confirmAction({
            title: t('assistant.actions.clear'),
            message: t('assistant.clearConfirm'),
            confirmText: t('assistant.actions.delete'),
            danger: true,
        });
        confirming = false;
        if (!ok || closed) return;
        if (!(await ChatAssistantAPI.clearHistory(state.kind, state.targetId))) {
            toast(t('assistant.deleteFailed'), 'error');
            return;
        }
        if (closed) return;
        turns = [];
        paintTurns();
    });

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        send(input.value);
    });
    input.addEventListener('keydown', (e) => {
        // 輸入法選字中的 Enter 不送（同私訊的兩道防，見 dm 重複送出事故）
        if (e.key !== 'Enter' || e.shiftKey || e.isComposing || e.keyCode === 229) return;
        e.preventDefault();
        send(input.value);
    });

    const onKey = (e) => {
        if (e.key === 'Escape') close();
    };
    function close() {
        if (closed) return;
        closed = true;
        detachSelection();
        controller?.abort();
        document.removeEventListener('keydown', onKey);
        overlay.remove();
        if (current?.close === close) current = null;
    }
    // 在 AI 的回答裡選一句話追問：帶進輸入框（觸控裝置的回答區可以選字）
    const detachSelection = attachSelectionAsk({
        root: log,
        eligible: '.assistant-answer',
        label: () => t('assistant.askSelection'),
        enabled: () => !busy && !closed,
        onAsk: ({ text }) => putQuote(quoteForInput(text)),
    });
    closeBtn.addEventListener('click', close);
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) close();
    });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);

    current = { close };
    load();
    return current;
}

export {
    ChatAssistantAPI,
    assistantStatus,
    assistantEnabled,
    openAssistantDrawer,
    closeAssistantDrawer,
    parseSseChunk,
    buildAskBody,
    errorKey,
    shareErrorKey,
    progressKey,
    renderAnswerHtml,
    readStatusResponse,
    markdownToPlain,
    formatTurnTime,
    turnsToHistory,
    confirmAction,
    quoteForInput,
};
