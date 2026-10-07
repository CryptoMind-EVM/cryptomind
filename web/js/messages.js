/**
 * messages.js - 私訊功能前端模組
 * v1.0
 */

import { aiCardPreviewText, aiCardRowHtml, isAiCard } from './ai-card.js';
import { avatarColorClass, avatarInitial } from './avatar.js';
import {
    RECALL_WINDOW_MS,
    canRecallMessage,
    messageToolsHtml,
    quoteHtml,
    reactionsHtml,
    renderMessageText,
} from './dm-message-actions.js';

// ============================================================================
// MessagesAPI - API 客戶端
// ============================================================================

const MessagesAPI = {
    /**
     * 取得當前用戶 ID
     */
    _getUserId() {
        if (typeof AuthManager !== 'undefined' && AuthManager.currentUser) {
            return AuthManager.currentUser.user_id || AuthManager.currentUser.uid;
        }
        return null;
    },


    /**
     * 取得對話列表
     */
    async getConversations(limit = 50, offset = 0) {
        const userId = this._getUserId();
        if (!userId)
            throw new Error(window.I18n ? window.I18n.t('messages.loginRequired') : 'Please login first');

        return await AppAPI.get(
            `/api/messages/conversations?user_id=${userId}&limit=${limit}&offset=${offset}`,
        );
    },

    /**
     * 取得對話訊息
     */
    async getMessages(conversationId, limit = 50, beforeId = null) {
        const userId = this._getUserId();
        if (!userId)
            throw new Error(window.I18n ? window.I18n.t('messages.loginRequired') : 'Please login first');

        let url = `/api/messages/conversation/${conversationId}?user_id=${userId}&limit=${limit}`;
        if (beforeId) url += `&before_id=${beforeId}`;

        return await AppAPI.get(url);
    },

    /**
     * 取得與特定用戶的對話
     */
    async getConversationWith(otherUserId, limit = 50) {
        const userId = this._getUserId();
        if (!userId)
            throw new Error(window.I18n ? window.I18n.t('messages.loginRequired') : 'Please login first');

        return await AppAPI.get(
            `/api/messages/with/${otherUserId}?user_id=${userId}&limit=${limit}`,
        );
    },

    /**
     * 發送訊息
     */
    async sendMessage(toUserId, content, replyToMessageId = null) {
        const userId = this._getUserId();
        if (!userId)
            throw new Error(window.I18n ? window.I18n.t('messages.loginRequired') : 'Please login first');

        const body = { to_user_id: toUserId, content };
        if (replyToMessageId) body.reply_to_message_id = replyToMessageId;
        return await AppAPI.post(`/api/messages/send?user_id=${userId}`, body);
    },

    /**
     * 標記對話為已讀
     */
    async markAsRead(conversationId) {
        const userId = this._getUserId();
        if (!userId)
            throw new Error(window.I18n ? window.I18n.t('messages.loginRequired') : 'Please login first');

        return await AppAPI.post(`/api/messages/read?user_id=${userId}`, { conversation_id: conversationId });
    },

    /**
     * 發送打招呼（Pro 專屬）
     */
    async sendGreeting(toUserId, content) {
        const userId = this._getUserId();
        if (!userId)
            throw new Error(window.I18n ? window.I18n.t('messages.loginRequired') : 'Please login first');

        return await AppAPI.post(`/api/messages/greeting?user_id=${userId}`, { to_user_id: toUserId, content });
    },

    /**
     * 搜尋訊息（Pro 專屬）
     */
    async searchMessages(query, limit = 50) {
        const userId = this._getUserId();
        if (!userId)
            throw new Error(window.I18n ? window.I18n.t('messages.loginRequired') : 'Please login first');

        return await AppAPI.get(
            `/api/messages/search?user_id=${userId}&q=${encodeURIComponent(query)}&limit=${limit}`,
        );
    },

    /**
     * 取得訊息限制狀態
     */
    async getLimits() {
        const userId = this._getUserId();
        if (!userId) return null;

        try {
            return await AppAPI.get(`/api/messages/limits?user_id=${userId}`);
        } catch {
            return null;
        }
    },
};

window.MessagesAPI = MessagesAPI;

// ============================================================================
// MessagesWebSocket - 即時連線（照 Teams／LINE 的做法）
// - 斷線無限重連（指數退避＋抖動，上限 30 秒），不會試幾次就放棄
// - 回到前景／網路恢復立刻重連；連著的話送 ping 驗活並補抓
// - 心跳沒回 pong 就當半死連線（手機切背景常見）關掉重建
// - 重連成功後叫頁面補抓斷線期間漏掉的訊息（onResync）
// - 「輸入中」：打字時每 3 秒送一次，送出／清空時送 stop
// ============================================================================

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const HEARTBEAT_MS = 25000; // Cloudflare 100 秒沒流量會切斷
const PONG_TIMEOUT_MS = 10000;
const TYPING_SEND_INTERVAL_MS = 3000;
const TYPING_SHOW_MS = 6000;

const MessagesWebSocket = {
    ws: null,
    connected: false,
    reconnectAttempts: 0,
    _reconnectTimer: null,
    _heartbeatTimer: null,
    _pongTimer: null,
    _manualClose: false,
    _hadConnection: false, // 連上過一次後再認證成功＝重連，要補抓
    _lifecycleBound: false,
    _typingSentAt: {},
    onMessageCallback: null,
    onReadReceiptCallback: null,
    onTypingCallback: null,
    onResyncCallback: null,
    onRecalledCallback: null,
    onReactionsCallback: null,
    onGroupEventCallback: null,

    connect() {
        const userId = MessagesAPI._getUserId();
        if (!userId) return;
        this._manualClose = false;
        this._bindLifecycle();
        if (
            this.ws &&
            (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)
        ) {
            return;
        }
        clearTimeout(this._reconnectTimer);

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const ws = new WebSocket(`${protocol}//${window.location.host}/ws/messages`);
        this.ws = ws;

        ws.onopen = () => ws.send(JSON.stringify({ action: 'auth', user_id: userId }));
        ws.onmessage = (event) => {
            let data;
            try {
                data = JSON.parse(event.data);
            } catch (e) {
                console.error('MessagesWebSocket: 解析訊息失敗', e);
                return;
            }
            this._handleMessage(data);
        };
        ws.onclose = () => {
            if (this.ws !== ws) return; // 已被新連線取代
            this.connected = false;
            this._stopHeartbeat();
            if (!this._manualClose) this._scheduleReconnect();
        };
        // onerror 之後一定接 onclose，重連在那邊處理
    },

    disconnect() {
        this._manualClose = true;
        clearTimeout(this._reconnectTimer);
        this._stopHeartbeat();
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        this.connected = false;
    },

    _scheduleReconnect() {
        clearTimeout(this._reconnectTimer);
        const cap = Math.min(RECONNECT_BASE_MS * 2 ** this.reconnectAttempts, RECONNECT_MAX_MS);
        this.reconnectAttempts++;
        // 抖動：伺服器重啟時大家不要同一秒湧回來
        this._reconnectTimer = setTimeout(() => this.connect(), cap / 2 + (Math.random() * cap) / 2);
    },

    /** 回到前景／網路恢復 */
    _wake() {
        if (this._manualClose || !MessagesAPI._getUserId()) return;
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this._ping();
            if (this.onResyncCallback) this.onResyncCallback();
            return;
        }
        if (this.ws && this.ws.readyState === WebSocket.CONNECTING) return;
        this.reconnectAttempts = 0;
        this.connect();
    },

    _bindLifecycle() {
        if (this._lifecycleBound) return;
        this._lifecycleBound = true;
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') this._wake();
        });
        window.addEventListener('online', () => this._wake());
    },

    _handleMessage(data) {
        switch (data.type) {
            case 'authenticated': {
                const isReconnect = this._hadConnection;
                this._hadConnection = true;
                this.connected = true;
                this.reconnectAttempts = 0;
                this._startHeartbeat();
                if (isReconnect && this.onResyncCallback) this.onResyncCallback();
                break;
            }
            case 'new_message':
            case 'message_sent': // 自己在別的裝置／分頁送的
                if (this.onMessageCallback) {
                    this.onMessageCallback(data.message, data.type === 'message_sent');
                }
                break;
            case 'read_receipt':
                if (this.onReadReceiptCallback) {
                    this.onReadReceiptCallback(data.conversation_id, data.read_by);
                }
                break;
            case 'typing':
                if (this.onTypingCallback) this.onTypingCallback(data);
                break;
            case 'message_recalled': // 對方（或自己在別的裝置）收回
                if (this.onRecalledCallback) this.onRecalledCallback(data.message_id, data.conversation_id);
                break;
            case 'reaction_updated': // 有人按／收回表情（含自己別的裝置）
                if (this.onReactionsCallback) {
                    this.onReactionsCallback(data.message_id, data.conversation_id, data.reactions);
                }
                break;
            // 群組（group_chat.py）：全部交給一個 callback，頁面自己分派（以前未知類型被靜默丟掉）
            case 'group_message':
            case 'group_message_recalled':
            case 'group_reaction_updated':
            case 'group_read':
            case 'group_typing':
            case 'group_updated':
                if (this.onGroupEventCallback) this.onGroupEventCallback(data);
                break;
            case 'pong':
                clearTimeout(this._pongTimer);
                break;
            case 'error':
                console.error('MessagesWebSocket: 伺服器錯誤', data.message);
                break;
        }
    },

    _ping() {
        const ws = this.ws;
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        ws.send(JSON.stringify({ action: 'ping' }));
        clearTimeout(this._pongTimer);
        this._pongTimer = setTimeout(() => ws.close(), PONG_TIMEOUT_MS);
    },

    _startHeartbeat() {
        this._stopHeartbeat();
        this._heartbeatTimer = setInterval(() => this._ping(), HEARTBEAT_MS);
    },

    _stopHeartbeat() {
        clearInterval(this._heartbeatTimer);
        clearTimeout(this._pongTimer);
        this._heartbeatTimer = null;
    },

    /** 打字時呼叫；同一對話 3 秒內只送一次 */
    sendTyping(conversationId) {
        if (!conversationId || !this.connected) return;
        const now = Date.now();
        if (now - (this._typingSentAt[conversationId] || 0) < TYPING_SEND_INTERVAL_MS) return;
        this._typingSentAt[conversationId] = now;
        this.ws.send(JSON.stringify({ action: 'typing', conversation_id: Number(conversationId) }));
    },

    /** 送出或清空輸入框：讓對方立刻收掉「輸入中」 */
    sendTypingStop(conversationId) {
        if (!conversationId || !this.connected || !this._typingSentAt[conversationId]) return;
        delete this._typingSentAt[conversationId];
        this.ws.send(
            JSON.stringify({ action: 'typing', conversation_id: Number(conversationId), state: 'stop' })
        );
    },

    /** 群組的「輸入中」：送 group_id（後端靠它分流）；節流 key 加 g 前綴，跟對話 id 分開 */
    sendGroupTyping(groupId) {
        if (!groupId || !this.connected) return;
        const key = `g${groupId}`;
        const now = Date.now();
        if (now - (this._typingSentAt[key] || 0) < TYPING_SEND_INTERVAL_MS) return;
        this._typingSentAt[key] = now;
        this.ws.send(JSON.stringify({ action: 'typing', group_id: Number(groupId) }));
    },

    sendGroupTypingStop(groupId) {
        const key = `g${groupId}`;
        if (!groupId || !this.connected || !this._typingSentAt[key]) return;
        delete this._typingSentAt[key];
        this.ws.send(JSON.stringify({ action: 'typing', group_id: Number(groupId), state: 'stop' }));
    },

    /** 頁面覆寫：使用者此刻正看著這個對話 → 通知中心不跳這則（好友頁提供） */
    isViewing(_conversationId) {
        return false;
    },

    onMessage(callback) {
        this.onMessageCallback = callback;
    },

    onReadReceipt(callback) {
        this.onReadReceiptCallback = callback;
    },

    onTyping(callback) {
        this.onTypingCallback = callback;
    },

    onResync(callback) {
        this.onResyncCallback = callback;
    },

    onRecalled(callback) {
        this.onRecalledCallback = callback;
    },

    onReactions(callback) {
        this.onReactionsCallback = callback;
    },

    onGroupEvent(callback) {
        this.onGroupEventCallback = callback;
    },
};

window.MessagesWebSocket = MessagesWebSocket;

// ============================================================================
// 「某某某正在輸入…」與「↓ 新訊息」— messages.html 與好友頁共用
// 輸入中放在訊息列表最後一列（對方那側的灰氣泡＋名字）；6 秒沒續就收掉，對方訊息
// 到了由呼叫端立刻收掉。新訊息按鈕永遠在它後面。
// ============================================================================

const _typingTimers = new WeakMap();

function showTypingIndicator(container, name) {
    if (!container) return;
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 80;
    let row = container.querySelector('.msg-typing');
    if (!row) {
        row = document.createElement('div');
        row.className = 'msg-typing flex items-end gap-1.5 mt-3';
        row.setAttribute('role', 'status');
        row.innerHTML =
            '<span class="dm-typing-bubble" aria-hidden="true"><span></span><span></span><span></span></span><span class="msg-typing-text pb-1 text-[11px] text-textMuted"></span>';
    }
    row.querySelector('.msg-typing-text').textContent = window.I18n
        ? window.I18n.t('messages.typing', { name })
        : `${name} is typing…`;
    container.appendChild(row);
    keepTrailingRowsLast(container);
    if (nearBottom) container.scrollTop = container.scrollHeight;
    clearTimeout(_typingTimers.get(container));
    _typingTimers.set(
        container,
        setTimeout(() => hideTypingIndicator(container), TYPING_SHOW_MS)
    );
}

function hideTypingIndicator(container) {
    if (!container) return;
    clearTimeout(_typingTimers.get(container));
    container.querySelector('.msg-typing')?.remove();
}

/** 訊息插進來之後：輸入中那列、新訊息按鈕要維持在最底（按鈕最後） */
function keepTrailingRowsLast(container) {
    const row = container?.querySelector('.msg-typing');
    if (row) container.appendChild(row);
    const pill = container?.querySelector('.dm-new-pill');
    if (pill) container.appendChild(pill);
}

function isNearBottom(container) {
    return container.scrollHeight - container.scrollTop - container.clientHeight < 120;
}

/**
 * 使用者往上翻時來了新訊息：不拉回底部，浮「↓ N 則新訊息」，點了捲到底。
 * composer：蓋在捲動區上的固定輸入列（手機），按鈕貼在它上方
 */
function showNewMessagePill(container, composer = null) {
    if (!container) return;
    let pill = container.querySelector('.dm-new-pill');
    if (!pill) {
        pill = document.createElement('button');
        pill.type = 'button';
        pill.className = 'dm-new-pill';
        pill.dataset.count = '0';
        pill.addEventListener('click', () => {
            container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
            hideNewMessagePill(container);
        });
    }
    const count = Number(pill.dataset.count) + 1;
    pill.dataset.count = String(count);
    const label = window.I18n
        ? count > 1
            ? window.I18n.t('notification.newMessages', { count })
            : window.I18n.t('notification.newMessage')
        : `${count} new`;
    pill.innerHTML = '<i data-lucide="arrow-down" class="w-3.5 h-3.5"></i><span></span>';
    pill.querySelector('span').textContent = label;
    // sticky 的 bottom 從「扣掉 padding 的內容下緣」算。手機的固定輸入列蓋在捲動區上：
    // 換算成貼在輸入列上方 8px（padding 比輸入列高時會是負值，往 padding 裡放）
    let offset = 8;
    if (composer) {
        const covered = container.getBoundingClientRect().bottom - composer.getBoundingClientRect().top;
        const padBottom = parseFloat(getComputedStyle(container).paddingBottom) || 0;
        offset = covered - padBottom + 8;
    }
    pill.style.bottom = `${offset}px`;
    container.appendChild(pill);
    if (!container.dataset.newPillBound) {
        container.dataset.newPillBound = '1';
        container.addEventListener(
            'scroll',
            () => {
                if (isNearBottom(container)) hideNewMessagePill(container);
            },
            { passive: true }
        );
    }
}

function hideNewMessagePill(container) {
    container?.querySelector('.dm-new-pill')?.remove();
}

// ============================================================================
// 對話列表的「輸入中…」（LINE 式）：那一列的預覽暫時換掉，6 秒沒續或訊息到了就還原。
// 列表常整份重畫（innerHTML），狀態記在這裡，重畫後呼叫 applyListTyping 補回去。
// 列要有 data-conversation-id，預覽那行要有 .conv-preview
// ============================================================================

const _listTyping = new Map(); // conversationId(字串) → 還原計時器

function _paintListTyping(listEl, key, typing) {
    const preview = listEl?.querySelector(`[data-conversation-id="${key}"] .conv-preview`);
    if (!preview) return;
    if (typing) {
        if (!preview.classList.contains('conv-typing')) preview.dataset.preview = preview.textContent;
        preview.textContent = window.I18n ? window.I18n.t('messages.typingShort') : 'typing…';
        preview.classList.add('conv-typing');
    } else if (preview.classList.contains('conv-typing')) {
        preview.textContent = preview.dataset.preview || '';
        preview.classList.remove('conv-typing');
    }
}

function setListTyping(listEl, conversationId, typing) {
    const key = String(conversationId);
    clearTimeout(_listTyping.get(key));
    if (typing) {
        _listTyping.set(key, setTimeout(() => setListTyping(listEl, key, false), TYPING_SHOW_MS));
    } else {
        _listTyping.delete(key);
    }
    _paintListTyping(listEl, key, typing);
}

function applyListTyping(listEl) {
    for (const key of _listTyping.keys()) _paintListTyping(listEl, key, true);
}

// ============================================================================
// 每日私訊額度（一般會員 20 則／天，Pro 無限）：輸入列下的小字
// ============================================================================

/** messageLimit 來自 /api/messages/limits 或送出回應的 message_limit（limit=-1 表示無限） */
function quotaState(messageLimit, isPremium) {
    const limit = Number(messageLimit?.limit);
    if (isPremium || !messageLimit || !(limit > 0)) return { hidden: true };
    const used = Math.min(Math.max(Number(messageLimit.used) || 0, 0), limit);
    const left = limit - used;
    return { hidden: false, left, limit, low: left <= 3 };
}

/** 畫出額度提示；回傳剩幾則（隱藏時回 null，呼叫端據此決定送出鈕要不要鎖） */
function renderQuotaHint(el, messageLimit, isPremium) {
    if (!el) return null;
    const q = quotaState(messageLimit, isPremium);
    el.classList.toggle('hidden', q.hidden);
    if (q.hidden) return null;
    const t = (k, o) => (window.I18n ? window.I18n.t(k, o) : k);
    el.classList.toggle('text-danger', q.low);
    el.classList.toggle('text-textMuted', !q.low);
    if (q.left > 0) {
        el.textContent = t('messages.quotaLeft', { left: q.left, limit: q.limit });
    } else {
        el.textContent = `${t('messages.quotaUsedUp', { limit: q.limit })} · `;
        const link = document.createElement('a');
        link.href = '/static/forum/premium.html';
        link.className = 'text-primary hover:underline';
        link.textContent = t('messages.upgradePro');
        el.appendChild(link);
    }
    return q.left;
}

/**
 * 一則訊息該插在第幾列之前（依 id 排序）。同一則會從 API 回應、WS、補抓三條路
 * 亂序進來：已在畫面上、或比目前載入範圍還舊（屬於往上翻的分頁）回 -1 不插；
 * 補抓回來的漏訊息可能比剛到的即時訊息舊，要插回中間，不能丟掉。
 */
function messageInsertIndex(existingIds, id) {
    if (existingIds.includes(id)) return -1;
    if (existingIds.length && id < existingIds[0]) return -1;
    const i = existingIds.findIndex((x) => x > id);
    return i === -1 ? existingIds.length : i;
}

/** 依 id 把一列放到正確位置；回傳有沒有插（列要帶 data-message-id） */
function insertMessageRow(container, msgId, html) {
    const rows = [...container.querySelectorAll('.msg-row[data-message-id]')];
    const at = messageInsertIndex(
        rows.map((r) => Number(r.dataset.messageId)),
        Number(msgId)
    );
    if (at < 0) return false;
    if (at < rows.length) rows[at].insertAdjacentHTML('beforebegin', html);
    else container.insertAdjacentHTML('beforeend', html);
    keepTrailingRowsLast(container);
    return true;
}

// ============================================================================
// 訊息分組（LINE 式）— messages.html 與好友頁（friends.js）共用
// 同一人連續的訊息貼緊；同一人同一分鐘只在最後一則標時間；換日插日期分隔。
// 每列（.msg-row）帶 data-from / data-ts，插入、收回、刪除之後呼叫一次
// regroupMessageRows 從 DOM 重算，不必在每條插入路徑各自記鄰居狀態。
// ============================================================================

// 後端有時回沒帶時區的 UTC 字串（同 MessagesUI.formatTime 的處理）
function _parseMessageDate(ts) {
    const s = String(ts || '');
    return new Date(/Z$|[+-]\d\d:?\d\d$/.test(s) ? s : s + 'Z');
}

const _dayKey = (d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
const _minuteKey = (d) => Math.floor(d.getTime() / 60000);
const _messageLocale = () => document.documentElement.lang || undefined;

/** 屬性值轉義（data-from 是使用者 ID，不能讓引號跳出屬性） */
function escapeMessageAttr(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/**
 * @param {{from: string, ts: string}[]} items 由舊到新
 * @returns {{newDay: boolean, tight: boolean, showTime: boolean}[]}
 */
function messageGroupLayout(items) {
    const dates = items.map((it) => _parseMessageDate(it.ts));
    return items.map((it, i) => {
        const prev = items[i - 1];
        const next = items[i + 1];
        const newDay = !prev || _dayKey(dates[i - 1]) !== _dayKey(dates[i]);
        return {
            newDay,
            tight: !newDay && prev.from === it.from,
            showTime: !(next && next.from === it.from && _minuteKey(dates[i + 1]) === _minuteKey(dates[i])),
        };
    });
}

/** 氣泡旁的時間（「上午11:17」），日期交給分隔線 */
function formatMessageClock(ts, locale = _messageLocale()) {
    const d = _parseMessageDate(ts);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' });
}

/** 日期分隔線文字：今天／昨天走 Intl（跟語系），其餘「9月27日 週日」，跨年帶年份 */
function formatDateSeparator(ts, now = new Date(), locale = _messageLocale()) {
    const d = _parseMessageDate(ts);
    const startOf = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate());
    const days = Math.round((startOf(now) - startOf(d)) / 86400000);
    if (days === 0 || days === 1) {
        return new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(days === 0 ? 0 : -1, 'day');
    }
    const opts = { month: 'long', day: 'numeric', weekday: 'short' };
    if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric';
    return new Intl.DateTimeFormat(locale, opts).format(d);
}

/** 依 DOM 裡的 .msg-row 重排間距、時間顯示與日期分隔 */
function regroupMessageRows(container) {
    if (!container) return;
    container.querySelectorAll('.msg-date-sep').forEach((el) => el.remove());
    const rows = [...container.querySelectorAll('.msg-row')];
    const layout = messageGroupLayout(rows.map((r) => ({ from: r.dataset.from, ts: r.dataset.ts })));
    rows.forEach((row, i) => {
        const { newDay, tight, showTime } = layout[i];
        if (newDay) {
            row.insertAdjacentHTML(
                'beforebegin',
                `<div class="msg-date-sep flex justify-center my-3" role="separator"><span class="px-3 py-0.5 rounded-full bg-surfaceHighlight text-[11px] text-textMuted">${escapeMessageAttr(formatDateSeparator(row.dataset.ts))}</span></div>`
            );
        }
        row.classList.remove('mt-0', 'mt-0.5', 'mt-3');
        row.classList.add(newDay ? 'mt-0' : tight ? 'mt-0.5' : 'mt-3');
        row.querySelector('.msg-meta')?.classList.toggle('hidden', !showTime);
        // 群組：同一人連續只在第一則顯示暱稱；頭像留位置（invisible）氣泡才對齊。私訊沒有這兩個元素
        row.querySelector('.msg-sender')?.classList.toggle('hidden', tight);
        row.querySelector('.msg-avatar')?.classList.toggle('invisible', tight);
    });
    // 已讀／已送達只標在自己最新一則（每則都標會疊出一排「已送達」）
    const statuses = [...container.querySelectorAll('.msg-read-status')];
    statuses.forEach((s, i) => s.classList.toggle('hidden', i !== statuses.length - 1));
}

/**
 * 自己傳的訊息旁的「已讀／已送達」（只給 Pro；後端也只推 read_receipt 給 Pro）。
 * 兩支 UI（messages.html、好友頁 SocialHub）共用；class 給 markRowsRead 找
 */
function readStatusHtml(msg) {
    const t = (key, fallback) => (window.I18n ? window.I18n.t(key) : fallback);
    return msg.is_read
        ? `<span class="msg-read-status" data-read="1">${t('messages.readStatus', 'Read')}</span>`
        : `<span class="msg-read-status" data-read="0">${t('messages.deliveredStatus', 'Delivered')}</span>`;
}

/** 收到已讀回執：畫面上自己傳的、還是「已送達」的全部改成已讀 */
function markRowsRead(container) {
    container?.querySelectorAll('.msg-read-status[data-read="0"]').forEach((status) => {
        status.dataset.read = '1';
        status.textContent = window.I18n ? window.I18n.t('messages.readStatus') : 'Read';
    });
}

// ============================================================================
// MessagesUI - UI 渲染工具
// ============================================================================

const MessagesUI = {
    currentUserId: null,

    /**
     * 初始化
     */
    init() {
        this.currentUserId = MessagesAPI._getUserId();
    },

    /**
     * 格式化時間（訊息氣泡用 - 顯示明確日期時間）
     */
    formatTime(dateString) {
        if (!dateString) return '';
        // 處理沒有時區的日期字串
        if (!dateString.includes('Z') && !dateString.includes('+')) {
            dateString += 'Z';
        }
        const date = new Date(dateString);
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const messageDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());

        // 同一天：只顯示時間
        if (messageDay.getTime() === today.getTime()) {
            return date.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' });
        }

        // 同一年：顯示 MM/DD HH:mm
        if (date.getFullYear() === now.getFullYear()) {
            return `${date.getMonth() + 1}/${date.getDate()} ${date.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' })}`;
        }

        // 不同年：顯示 YYYY/MM/DD HH:mm
        return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()} ${date.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' })}`;
    },

    /**
     * 格式化完整時間
     */
    formatFullTime(dateString) {
        if (!dateString) return '';
        if (!dateString.includes('Z') && !dateString.includes('+')) {
            dateString += 'Z';
        }
        const date = new Date(dateString);
        return date.toLocaleString('zh-TW', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    },

    /**
     * 取得會員徽章
     */
    getMembershipBadge(tier) {
        if (['premium', 'pro', 'plus'].includes((tier || 'free').toLowerCase())) {
            return '<span class="px-1.5 py-0.5 text-xs font-bold bg-gradient-to-r from-yellow-500 to-orange-500 text-black rounded">PREMIUM</span>';
        }
        return '';
    },

    /**
     * 取得用戶首字母
     */
    getInitial(username) {
        return avatarInitial(username);
    },

    /**
     * 渲染對話列表項目
     */
    renderConversationItem(conv, isActive = false) {
        const displayName = conv.other_display_name || conv.other_username; // 暱稱優先
        const initial = escapeMessageAttr(this.getInitial(displayName));
        const badge = this.getMembershipBadge(conv.other_membership_tier);
        const hasUnread = conv.unread_count > 0;

        // 未讀狀態樣式
        const unreadTextClass = hasUnread ? 'font-bold text-textMain' : 'text-textMuted';
        const unreadBgClass = hasUnread ? 'bg-primary/5' : '';
        const unreadAvatarClass = hasUnread
            ? 'ring-2 ring-primary ring-offset-2 ring-offset-background'
            : '';
        const unreadIndicator = hasUnread
            ? '<div class="absolute left-0 top-0 bottom-0 w-1 bg-primary rounded-r"></div>'
            : '';

        // 活動狀態樣式
        const activeClass = isActive ? 'bg-primary/10 border-primary/30' : 'hover:bg-surfaceHighlight';
        const timeStr = this.formatTime(conv.last_message_at);

        // 截斷訊息預覽
        let preview =
            recalledPreviewText(conv) ||
            conv.last_message ||
            (window.I18n ? window.I18n.t('messages.startConversation') : 'Start a conversation');
        if (preview.length > 30) {
            preview = preview.substring(0, 30) + '...';
        }

        // 安全轉義用戶數據，防止 XSS
        const escapedUsername =
            typeof SecurityUtils !== 'undefined'
                ? SecurityUtils.escapeHTML(displayName)
                : this._escapeHtml(displayName);

        const escapedPreview =
            typeof SecurityUtils !== 'undefined'
                ? SecurityUtils.escapeHTML(preview)
                : this._escapeHtml(preview);

        return `
            <div class="conversation-item cursor-pointer p-3 border-b border-borderSubtle ${activeClass} ${unreadBgClass} transition relative"
                 data-conversation-id="${conv.id}"
                 data-other-user-id="${conv.other_user_id}"
                 data-other-username="${escapedUsername}">
                ${unreadIndicator}
                <div class="flex items-center gap-3">
                    <div class="w-10 h-10 rounded-full ${avatarColorClass(conv.other_user_id)} flex items-center justify-center font-bold flex-shrink-0 ${unreadAvatarClass}">
                        ${initial}
                    </div>
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center justify-between gap-2">
                            <div class="flex items-center gap-2 min-w-0">
                                <span class="${hasUnread ? 'font-extrabold' : 'font-bold'} text-textMain truncate">${escapedUsername}</span>
                                ${badge}
                            </div>
                            <span class="text-xs ${hasUnread ? 'text-primary font-bold' : 'text-textMuted'} flex-shrink-0">${timeStr}</span>
                        </div>
                        <div class="flex items-center justify-between gap-2 mt-0.5">
                            <p class="conv-preview text-sm ${unreadTextClass} truncate">${escapedPreview}</p>
                            ${
                                hasUnread
                                    ? `
                                <span class="flex-shrink-0 min-w-5 h-5 px-1.5 rounded-full bg-primary text-background text-xs font-bold flex items-center justify-center animate-pulse">
                                    ${conv.unread_count > 99 ? '99+' : conv.unread_count}
                                </span>
                            `
                                    : ''
                            }
                        </div>
                    </div>
                </div>
            </div>
        `;
    },

    /**
     * 渲染訊息氣泡（LINE 式：時間貼在氣泡旁、對齊底部；間距與時間顯示由 regroupMessageRows 決定）
     * 氣泡的 max-w 必須掛在整列寬的 .msg-row 子層上——以前掛在縮成內容寬的父層，
     * 百分比跟著內容縮，四個字就換行、「已送達 上午11:17」被擠成四行
     */
    renderMessageBubble(msg, isPro = false) {
        const t = (key, fallback) => (window.I18n ? window.I18n.t(key) : fallback);
        const isMine = msg.from_user_id === this.currentUserId;
        const timeStr = formatMessageClock(msg.created_at);
        // data-type／.msg-text 給選單（dm-message-actions.js）判斷項目與複製原文
        const rowOpen = `<div id="msg-${msg.id}" data-message-id="${msg.id}" data-from="${escapeMessageAttr(msg.from_user_id)}" data-ts="${escapeMessageAttr(msg.created_at)}" data-type="${escapeMessageAttr(msg.message_type || 'text')}" class="msg-row group flex items-end gap-1.5 ${isMine ? 'justify-end' : 'justify-start'}">`;
        const meta = (extra = '') =>
            `<div class="msg-meta flex flex-col ${isMine ? 'items-end' : 'items-start'} shrink-0 text-[11px] leading-tight text-textMuted whitespace-nowrap">${extra}<span>${timeStr}</span></div>`;
        const bubbleBase = 'msg-bubble relative max-w-[75%] min-w-0 px-3.5 py-2 rounded-2xl text-[15px] leading-relaxed';
        const recalled = msg.message_type === 'recalled';
        const tools = messageToolsHtml({ isMine, recalled });

        if (recalled) {
            const recalledText = isMine
                ? t('messages.recalledByMe', 'You recalled the message')
                : t('messages.recalledByOther', 'Message recalled by sender');
            const bubble = `<div class="${bubbleBase} bg-surfaceHighlight border border-borderLight"><span class="text-textMuted/60 text-sm italic">${recalledText}</span></div>`;
            return `${rowOpen}${isMine ? meta() + tools + bubble : bubble + tools + meta()}</div>`;
        }

        // 已讀狀態（僅 Pro 可見）
        const readStatus = isMine && isPro ? readStatusHtml(msg) : '';

        // AI 分析卡片（分享自聊天室 AI 助理）：整列另外畫，見 ai-card.js
        if (isAiCard(msg)) {
            return aiCardRowHtml({ rowOpen, msg, isMine, meta: meta(readStatus), tools, myId: this.currentUserId });
        }

        // 打招呼訊息的標記
        const greetingBadge =
            msg.message_type === 'greeting'
                ? '<span class="text-xs text-accent mr-1">👋</span>'
                : '';

        const content = `${quoteHtml(msg.reply_to, isMine)}${greetingBadge}<span class="msg-text whitespace-pre-wrap break-words">${renderMessageText(msg.content)}</span>${reactionsHtml(msg.reactions, this.currentUserId, isMine)}`;

        // 收回、為我刪除等動作都在選單裡（手機長按、桌機 hover「⋯」／右鍵）；hover 時工具列接在時間的位置（時間淡出）
        if (isMine) {
            return `${rowOpen}${meta(readStatus)}${tools}<div class="${bubbleBase} bg-primary text-background">${content}</div></div>`;
        }
        return `${rowOpen}<div class="${bubbleBase} bg-surfaceHighlight border border-borderSubtle text-textMain">${content}</div>${tools}${meta()}</div>`;
    },

    /**
     * 渲染空狀態
     */
    renderEmptyState(message, icon = 'message-square') {
        return `
            <div class="flex flex-col items-center justify-center h-full text-textMuted p-8">
                <i data-lucide="${icon}" class="w-16 h-16 opacity-30 mb-4"></i>
                <p class="text-center">${message}</p>
            </div>
        `;
    },

    /**
     * 渲染載入狀態
     */
    renderLoadingState() {
        return `
            <div class="flex items-center justify-center h-full">
                <div class="animate-spin w-8 h-8 border-2 border-primary border-t-transparent rounded-full"></div>
            </div>
        `;
    },

    /**
     * 渲染新訊息分隔線
     */
    renderNewMessagesSeparator() {
        return `
            <div class="new-messages-separator flex items-center gap-4 my-4 px-4">
                <div class="flex-1 h-px bg-primary/30"></div>
                <span class="text-xs font-bold text-primary uppercase tracking-wider flex items-center gap-1">
                    <i data-lucide="arrow-down" class="w-3 h-3"></i>
                    ${window.I18n ? window.I18n.t('messages.newMessages') : 'New Messages'}
                </span>
                <div class="flex-1 h-px bg-primary/30"></div>
            </div>
        `;
    },

    /**
     * HTML 轉義
     */
    _escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },
};

window.MessagesUI = MessagesUI;

// ============================================================================
// 收回（兩支 UI 共用：MessagesUI 與 friends.js 的 SocialHub）
// ============================================================================

/** 把畫面上那一列換成「已收回」樣式（保留發送者與時間，讓分組不變）。不在畫面上回 false */
function markMessageRowRecalled(container, messageId, renderBubble) {
    const row = container?.querySelector(`[data-message-id="${Number(messageId)}"]`);
    if (!row) return false;
    row.outerHTML = renderBubble({
        id: Number(messageId),
        from_user_id: row.dataset.from,
        created_at: row.dataset.ts,
        message_type: 'recalled',
    });
    regroupMessageRows(container);
    return true;
}

/** 對話列表預覽：最後一則被收回時顯示「已收回訊息」（原文已清空，不然會是空白） */
function recalledPreviewText(conv) {
    if (conv?.last_message_type !== 'recalled') return null;
    return window.I18n ? window.I18n.t('messages.recalledPreview') : 'Message recalled';
}

/** 私訊裡的確認框一律走官方樣式（ui-shell 的 showConfirmDialog），不要跳「xxx.com 說」 */
async function confirmDmAction({ title, message, confirmText, danger = false }) {
    if (typeof window.showConfirmDialog === 'function') {
        return window.showConfirmDialog({ title, message, confirmText, danger });
    }
    return window.confirm(message || title);
}

/**
 * 收回（兩支 UI 共用）：官方確認框 → DELETE → 那一列換成「已收回」。成功回 true。
 * 對方畫面靠後端推的 message_recalled 換掉，不用這裡管。
 */
async function recallDmMessage(messageId, { container, renderBubble, btnElement, url } = {}) {
    const t = (key, fallback) => (window.I18n ? window.I18n.t(key) : fallback);
    const confirmed = await confirmDmAction({
        title: t('messages.recallConfirmTitle', 'Recall this message?'),
        message: t('messages.recallConfirm', 'The other person will see "Message recalled"'),
        confirmText: t('messages.recallBtn', 'Recall'),
        danger: true,
    });
    if (!confirmed) return false;

    if (btnElement) btnElement.disabled = true;
    try {
        await AppAPI.delete(url || `/api/messages/${Number(messageId)}`); // 群組傳自己的網址
        markMessageRowRecalled(container, messageId, renderBubble);
        return true;
    } catch (e) {
        if (e?.message === 'already_recalled') {
            // 別的裝置／分頁先收回了：結果一樣，畫面跟上就好
            markMessageRowRecalled(container, messageId, renderBubble);
            return true;
        }
        console.error('recallDmMessage failed:', e);
        const text =
            e?.message === 'recall_window_expired'
                ? t('messages.recallExpired', 'Messages older than 24 hours can\'t be recalled')
                : e?.message || t('messages.recallFailed', 'Recall failed');
        if (typeof window.showToast === 'function') window.showToast(text, 'error');
        if (btnElement) btnElement.disabled = false;
        return false;
    }
}

/**
 * 往前翻到的一頁插在最前面（兩支 UI 共用）：畫面上已經有的跳過（WS／補抓可能先放進來），
 * 以原本最上面那則為錨點把捲動位置補回去——不管瀏覽器自己有沒有 scroll anchoring 都對。
 * 回傳實際插入幾則。
 */
function prependOlderRows(container, messages, renderBubble) {
    const fresh = messages.filter((m) => !container.querySelector(`[data-message-id="${Number(m.id)}"]`));
    if (!fresh.length) return 0;
    const anchor = container.querySelector('.msg-row');
    const anchorTop = anchor?.getBoundingClientRect?.().top;
    const oldScrollHeight = container.scrollHeight;
    container.insertAdjacentHTML('afterbegin', fresh.map(renderBubble).join(''));
    regroupMessageRows(container);
    if (anchor && anchorTop !== undefined) {
        container.scrollTop += anchor.getBoundingClientRect().top - anchorTop;
    } else {
        container.scrollTop += container.scrollHeight - oldScrollHeight;
    }
    return fresh.length;
}

/** 為我刪除（兩支 UI 共用）：官方確認框 → 隱藏 → 移除那一列。成功回 true */
async function hideDmMessage(messageId, { container } = {}) {
    const t = (key, fallback) => (window.I18n ? window.I18n.t(key) : fallback);
    const confirmed = await confirmDmAction({
        title: t('friends.hideMessageConfirmTitle', 'Delete this message?'),
        message: t('friends.hideMessageConfirm', "It's only removed from your view."),
        confirmText: t('friends.hideMessageBtn', 'Delete'),
        danger: true,
    });
    if (!confirmed) return false;
    try {
        await AppAPI.post(`/api/messages/${Number(messageId)}/hide`);
        container?.querySelector(`[data-message-id="${Number(messageId)}"]`)?.remove();
        regroupMessageRows(container);
        return true;
    } catch (e) {
        console.error('hideDmMessage failed:', e);
        window.showToast?.(e?.message || t('friends.deleteFailed', 'Delete failed'), 'error');
        return false;
    }
}

export {
    RECALL_WINDOW_MS,
    prependOlderRows,
    recallDmMessage,
    hideDmMessage,
    canRecallMessage,
    markMessageRowRecalled,
    recalledPreviewText,
    confirmDmAction,
    MessagesAPI,
    MessagesWebSocket,
    MessagesUI,
    escapeMessageAttr,
    messageGroupLayout,
    formatMessageClock,
    formatDateSeparator,
    regroupMessageRows,
    showTypingIndicator,
    hideTypingIndicator,
    isNearBottom,
    showNewMessagePill,
    messageInsertIndex,
    insertMessageRow,
    setListTyping,
    applyListTyping,
    quotaState,
    renderQuotaHint,
    readStatusHtml,
    markRowsRead,
};
