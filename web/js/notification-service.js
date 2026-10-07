// ========================================
// notification-service.js - 通知服務
// ========================================

import { consumeBriefDeepLink } from './notification-detail.js';

const NotificationService = {
    // 通知列表
    notifications: [],

    // 未读计数（伺服器的總未讀；本機只載最近 50 筆）
    unreadCount: 0,

    // 伺服器未讀裡超出本機載入範圍的那一批：鈴鐺數字＝本機未讀＋這個，
    // 不然未讀超過 50 筆時，第一次標已讀數字就會突然掉到「只算前 50 筆」
    _unreadOutsideLoaded: 0,
    // 範圍外有未讀時，新通知可能是「合併進範圍外那一筆」（同 id，本機分不出來，數字會多 1）：稍後重抓校正
    _resyncUnreadTimer: null,
    _resyncUnreadDelayMs: 1500,

    // badges_changed 合併成一次處理：伺服器在 commit 之前就推了，馬上重抓可能讀到舊資料
    _badgesChangedTimer: null,
    _badgesChangedDelayMs: 500,

    // WebSocket 连接
    ws: null,

    // 重连定时器
    reconnectTimer: null,

    // 重連次數（只用來算退避，不再試幾次就放棄）
    reconnectAttempts: 0,
    _heartbeatTimer: null,
    _pongTimer: null,
    _hadConnection: false,
    _lifecycleBound: false,
    _fetchRetryTimer: null,
    _fetchRetries: 0,

    // 是否已登录
    isLoggedIn: false,

    // 避免重複初始化 / 重複 WebSocket
    _initialized: false,
    _initializedUserId: null,

    /**
     * 初始化通知服务
     */
    async init() {
        const { userId } = this._getCredentials();
        this._initialized = true;

        // Same logged-in user with an active socket does not need full re-init.
        if (
            this.isLoggedIn &&
            userId &&
            this._initializedUserId === userId &&
            this.ws &&
            (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)
        ) {
            return;
        }

        // 檢查是否登录
        this.isLoggedIn = window.AuthManager && window.AuthManager.isLoggedIn();

        if (this.isLoggedIn) {
            this._initializedUserId = userId;
            // 已登录：从 API 获取真实数据
            await this.fetchNotifications();

            // 连接 WebSocket
            this.connectWebSocket();
        } else {
            this._initializedUserId = null;
            this.disconnectWebSocket();
            // 未登录：顯示空列表
            this.notifications = [];
            this.unreadCount = 0;
            this._unreadOutsideLoaded = 0;
            this.notifyUpdate();
            console.log('[NotificationService] Not logged in, empty notifications');
        }
    },

    /**
     * 獲取用戶憑證
     */
    _getCredentials() {
        if (typeof AuthManager !== 'undefined' && AuthManager.currentUser) {
            const userId = AuthManager.currentUser.user_id || AuthManager.currentUser.uid;
            const hasSessionUser = !!(
                AuthManager.currentUser.user_id ||
                AuthManager.currentUser.uid
            );

            return { userId, hasSessionUser };
        }
        return { userId: null, hasSessionUser: false };
    },

    /**
     * 檢查 token 是否過期，     */
    _isTokenExpired() {
        if (typeof AuthManager !== 'undefined' && AuthManager.shouldDeferExpiredSessionCleanup?.()) {
            return false;
        }

        const { userId, hasSessionUser } = this._getCredentials();

        if (!userId || !hasSessionUser) {
            return true;
        }

        // 檢查 AuthManager 是否有過期檢查方法
        if (typeof AuthManager.isTokenExpired === 'function') {
            return AuthManager.isTokenExpired();
        }

        // 備用檢查：檢查 accessTokenExpiry
        const expiry = AuthManager.currentUser?.accessTokenExpiry;
        if (!expiry) {
            // 沒有過期時間≠已過期（session restore 中／舊格式 session）。
            // 2026-08-22 修：此處原判 true → fetchNotifications 直接
            // clearExpiredToken 會把使用者登出（無 silent refresh 的真兇）。
            return false;
        }

        return Date.now() > expiry;
    },

    /**
     * 从 API 获取通知
     */
    async fetchNotifications() {
        try {
            if (typeof AuthManager !== 'undefined' && AuthManager.isLoginInFlight?.()) {
                // 只避開「登入正在進行中」，過一下再補。以前用 shouldDeferExpiredSessionCleanup
                // 直接 return 且沒人再抓：它連登入成功後 15 秒寬限期都算，開頁時剛好續登
                // （token 過期隔天打開）通知中心就一直是空的
                this._retryFetchSoon();
                return;
            }

            // 檢查 token 是否過期
            if (this._isTokenExpired()) {
                // 2026-08-22 修：原本直接呼叫全域登出（clearExpiredToken）——
                // 通知輪詢不該有全域登出權。改為背景觸發單飛 refresh
                // （AppAPI 的 401→refresh→retry 鏈是權威），本輪跳過。
                console.warn('[NotificationService] Token expired, triggering silent refresh');
                if (
                    typeof AuthManager !== 'undefined' &&
                    typeof AuthManager.backendTokenRefresh === 'function'
                ) {
                    AuthManager.backendTokenRefresh().catch(() => {});
                }
                this._retryFetchSoon();
                return;
            }

            const { userId, hasSessionUser } = this._getCredentials();

            if (!userId || !hasSessionUser) {
                if (window.DEBUG_MODE)
                    console.log('[NotificationService] No credentials, empty notifications');
                this.notifications = [];
                this.unreadCount = 0;
                this._unreadOutsideLoaded = 0;
                this.notifyUpdate();
                return;
            }

            const data = await AppAPI.get(`/api/notifications?user_id=${userId}&limit=50`);
            this._fetchRetries = 0;
            this.notifications = data.notifications || [];
            this.unreadCount = data.unread_count || 0;
            this._unreadOutsideLoaded = Math.max(
                0,
                this.unreadCount - this.notifications.filter((n) => !n.is_read).length
            );
            this.notifyUpdate();
            // Base App 早報推播點開是 /?brief=日期：登入後第一次拿到清單就打開那份
            consumeBriefDeepLink(this.notifications, {
                hasMore: this.notifications.length >= 50,
                fetchPage: (offset, size) =>
                    AppAPI.get(`/api/notifications?user_id=${userId}&limit=${size}&offset=${offset}`).then(
                        (page) => page.notifications || []
                    ),
            }).catch((error) => console.warn('[NotificationService] brief deep link failed:', error));
            console.log(
                '[NotificationService] Loaded from API:',
                this.notifications.length,
                'notifications'
            );
        } catch (error) {
            // 401: AppAPI 的 401→refresh→retry 鏈已嘗試過仍丟上來＝refresh 暫時
            // 失敗（429/網路）或真失效。2026-08-22 修：不再由通知服務登出全域
            // session——真正的失效由 switchTab 守門／使用者互動時的權威鏈處理。
            if (error.status === 401) {
                console.warn('[NotificationService] 401 loading notifications (refresh likely failed); skipping this poll');
            } else {
                console.error('[NotificationService] Fetch error:', error);
            }
            this.notifications = [];
            this.unreadCount = 0;
            this._unreadOutsideLoaded = 0;
            this.notifyUpdate();
        }
    },

    /**
     * 获取所有通知
     */
    getNotifications() {
        return this.notifications;
    },

    /**
     * 获取未读数量
     */
    getUnreadCount() {
        return this.unreadCount;
    },

    /**
     * 更新未读计数
     */
    updateUnreadCount() {
        this.unreadCount =
            this.notifications.filter((n) => !n.is_read).length + this._unreadOutsideLoaded;
    },

    /**
     * 标记通知为已读
     */
    async markAsRead(notificationId) {
        const notification = this.notifications.find((n) => n.id === notificationId);
        if (notification && !notification.is_read) {
            notification.is_read = true;
            this.updateUnreadCount();
            this.notifyUpdate();

            // 同步到服务器
            if (this.isLoggedIn) {
                try {
                    const { userId } = this._getCredentials();
                    if (userId) {
                        await AppAPI.post(
                            `/api/notifications/${notificationId}/read?user_id=${userId}`,
                        );
                        window.NavBadges?.schedule(); // POST 完才重抓；notifyUpdate 那次可能比它早到
                    }
                } catch (error) {
                    console.error('[NotificationService] Mark as read error:', error);
                }
            }
        }
    },

    /**
     * 标记所有通知为已读
     */
    async markAllAsRead() {
        this.notifications.forEach((n) => (n.is_read = true));
        this._unreadOutsideLoaded = 0; // 伺服器那邊是全部標已讀，範圍外的也一起
        this.updateUnreadCount();
        this.notifyUpdate();

        // 同步到服务器
        if (this.isLoggedIn) {
            try {
                const { userId } = this._getCredentials();
                if (userId) {
                    await AppAPI.post(`/api/notifications/read-all?user_id=${userId}`);
                    window.NavBadges?.schedule(); // POST 完才重抓；notifyUpdate 那次可能比它早到
                }
            } catch (error) {
                console.error('[NotificationService] Mark all read error:', error);
            }
        }
    },

    /**
     * 添加新通知
     */
    /**
     * 顯示用的標題／內文。後端存的是固定中文，這幾種依型別走 i18n；其他照原文
     */
    describe(notification) {
        const t = (k, o) => (window.I18n ? window.I18n.t(k, o) : k);
        const name = notification.data?.from_username || notification.data?.from_user_id || '';
        switch (notification.type) {
            case 'message': {
                const count = Number(notification.data?.count) || 1;
                return {
                    title: count > 1 ? t('notification.newMessages', { count }) : t('notification.newMessage'),
                    // 被收回的：後端 body 是固定中文，這裡換成在地化文案
                    body: notification.data?.recalled
                        ? t('notification.messageRecalledBody', { name })
                        : notification.body,
                };
            }
            // 論壇（2026-10-02）：同一篇合併成一筆，count＝不同人數；舊列（沒有 post_title）照後端原文
            case 'post_interaction': {
                const keys = {
                    push: ['notification.postPushed', 'notification.postPushedMany'],
                    comment: ['notification.postCommented', 'notification.postCommentedMany'],
                    thread_reply: ['notification.postThreadReply', 'notification.postThreadReplyMany'],
                }[notification.data?.interaction_type];
                if (!keys || !notification.data?.post_title) {
                    return { title: notification.title, body: notification.body };
                }
                const count = Number(notification.data?.count) || 1;
                return {
                    title: t('notification.forum'),
                    body: t(keys[count > 1 ? 1 : 0], { name, count, title: notification.data.post_title }),
                };
            }
            case 'friend_request':
                return { title: t('notification.friendRequest'), body: t('notification.friendRequestBody', { name }) };
            case 'friend_accepted':
                return { title: t('notification.friendAccepted'), body: t('notification.friendAcceptedBody', { name }) };
            // 群組（c066）：標題是群名、多則加則數；收回的換在地化文案
            case 'group_invite':
                return {
                    title: t('notification.groupInvite'),
                    body: t('notification.groupInviteBody', {
                        name: notification.data?.inviter_name || notification.data?.inviter_id || '',
                        group: notification.data?.group_name || '',
                    }),
                };
            case 'group_dissolved':
                return {
                    title: t('notification.groupDissolved'),
                    body: t('notification.groupDissolvedBody', {
                        group: notification.data?.group_name || '',
                        name: notification.data?.by_name || '',
                    }),
                };
            case 'group_message': {
                const group = notification.data?.group_name || notification.title;
                const count = Number(notification.data?.count) || 1;
                let title = count > 1 ? t('notification.groupMessages', { group, count }) : group;
                // 有人 @ 我（後端合併時這個標記不會掉）：關了群組通知也會收到這筆
                if (notification.data?.mentioned) title = t('notification.groupMentioned', { group });
                return {
                    title,
                    body: notification.data?.recalled
                        ? t('notification.messageRecalledBody', { name })
                        : notification.body,
                };
            }
            case 'group_removed':
                return {
                    title: t('notification.groupRemoved'),
                    body: t('notification.groupRemovedBody', { group: notification.data?.group_name || '' }),
                };
            default:
                return { title: notification.title, body: notification.body };
        }
    },

    addNotification(notification) {
        // 正看著那個對話就不跳（像 Teams／LINE）；頁面標已讀時後端也會把它清掉
        const ws = window.MessagesWebSocket;
        const viewing =
            (notification.type === 'message' && !!ws?.isViewing?.(notification.data?.conversation_id)) ||
            // 群組通知的 group_id 是字串（後端 JSON 比對用），轉數字跟畫面比
            (notification.type === 'group_message' && !!ws?.isViewingGroup?.(Number(notification.data?.group_id)));
        const item = viewing ? { ...notification, is_read: true } : notification;
        const isKnown = this.notifications.some((n) => n.id === notification.id);
        // 同一對話的私訊通知後端合併成同一筆（同 id）：取代舊的並浮到最上面
        this.notifications = [item, ...this.notifications.filter((n) => n.id !== notification.id)];
        this.updateUnreadCount();
        this.notifyUpdate();
        if (!isKnown && !item.is_read && this._unreadOutsideLoaded > 0) this._resyncUnreadSoon();

        if (!viewing && typeof showToast === 'function') {
            showToast(this.describe(notification).body, 'info');
        }
        // 好友邀請／接受：好友頁載入過的話，邀請清單與好友列表即時更新
        if (notification.type === 'friend_request' || notification.type === 'friend_accepted') {
            if (typeof window.refreshFriendsUI === 'function') window.refreshFriendsUI();
        }
    },

    _resyncUnreadSoon() {
        clearTimeout(this._resyncUnreadTimer);
        this._resyncUnreadTimer = setTimeout(() => this.fetchNotifications(), this._resyncUnreadDelayMs);
    },

    /** 側欄數字變了但沒有通知可推（例如對方撤回好友邀請，通知早已讀）：重抓徽章與邀請列表 */
    onBadgesChanged() {
        clearTimeout(this._badgesChangedTimer);
        this._badgesChangedTimer = setTimeout(() => {
            window.NavBadges?.schedule();
            if (typeof window.refreshFriendsUI === 'function') window.refreshFriendsUI();
        }, this._badgesChangedDelayMs);
    },

    /** 後端改寫了某筆通知（例如私訊被收回）→ 原地換掉；不是新通知，不跳 toast、不浮上來 */
    applyRemoteUpdate(notification) {
        if (!notification?.id) return;
        let changed = false;
        this.notifications = this.notifications.map((n) => {
            if (n.id !== notification.id) return n;
            changed = true;
            return notification;
        });
        if (changed) {
            this.updateUnreadCount();
            this.notifyUpdate();
        }
    },

    /** 別的分頁／裝置讀掉了（例如讀了私訊對話）→ 這邊同步變已讀 */
    applyRemoteRead(ids) {
        const cleared = new Set(ids || []);
        let changed = false;
        // 讀掉的若不在本機載入範圍內（超過 50 筆之外的未讀），從範圍外的數字扣
        const known = new Set(this.notifications.map((n) => n.id));
        const outside = [...cleared].filter((id) => !known.has(id)).length;
        if (outside > 0 && this._unreadOutsideLoaded > 0) {
            this._unreadOutsideLoaded = Math.max(0, this._unreadOutsideLoaded - outside);
            changed = true;
        }
        this.notifications = this.notifications.map((n) => {
            if (!cleared.has(n.id) || n.is_read) return n;
            changed = true;
            return { ...n, is_read: true };
        });
        if (changed) {
            this.updateUnreadCount();
            this.notifyUpdate();
        }
    },

    /** 登入中／token 續不回來時稍後再抓：退避 2→4→8… 秒，6 次後放手（重連、回前景還會再抓） */
    _retryFetchSoon() {
        clearTimeout(this._fetchRetryTimer);
        if (this._fetchRetries >= 6) return;
        const delay = 2000 * 2 ** this._fetchRetries;
        this._fetchRetries++;
        this._fetchRetryTimer = setTimeout(() => this.fetchNotifications(), delay);
    },

    /**
     * 通知 UI 更新
     */
    notifyUpdate() {
        window.dispatchEvent(
            new CustomEvent('notificationsUpdated', {
                detail: {
                    notifications: this.notifications,
                    unreadCount: this.unreadCount,
                },
            }),
        );
    },

    /**
     * 連 WebSocket（跟私訊同一套做法）：斷線無限重連、心跳驗活（Cloudflare 閒置 100 秒
     * 會切斷，以前沒送心跳、重試 5 次就放棄，之後通知再也進不來）、重連或回到前景時
     * 重抓一次列表補齊漏掉的
     */
    connectWebSocket() {
        const { userId } = this._getCredentials();
        if (!userId) return;
        this._bindLifecycle();

        if (
            this.ws &&
            (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)
        ) {
            return;
        }

        this.disconnectWebSocket(false);

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const ws = new WebSocket(`${protocol}//${window.location.host}/ws/notifications`);
        this.ws = ws;

        // 同源 socket，後端用 httpOnly cookie 認證
        ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', user_id: userId }));

        ws.onmessage = (event) => {
            let data;
            try {
                data = JSON.parse(event.data);
            } catch {
                return;
            }
            if (data.type === 'connected') {
                const isReconnect = this._hadConnection;
                this._hadConnection = true;
                this.reconnectAttempts = 0;
                this._startHeartbeat();
                if (isReconnect) this.fetchNotifications();
            } else if (data.type === 'notification') {
                this.addNotification(data.data);
            } else if (data.type === 'notifications_read') {
                this.applyRemoteRead(data.data?.ids);
            } else if (data.type === 'notification_updated') {
                this.applyRemoteUpdate(data.data);
            } else if (data.type === 'badges_changed') {
                this.onBadgesChanged();
            } else if (data.type === 'pong') {
                clearTimeout(this._pongTimer);
            }
        };

        ws.onclose = () => {
            if (this.ws !== ws) return; // 已被新連線取代
            this._stopHeartbeat();
            this.scheduleReconnect();
        };
    },

    /** 指數退避＋抖動，上限 60 秒；不放棄 */
    scheduleReconnect() {
        clearTimeout(this.reconnectTimer);
        if (!this.isLoggedIn) return;
        const cap = Math.min(2000 * 2 ** this.reconnectAttempts, 60000);
        this.reconnectAttempts++;
        this.reconnectTimer = setTimeout(() => this.connectWebSocket(), cap / 2 + (Math.random() * cap) / 2);
    },

    _ping() {
        const ws = this.ws;
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        ws.send(JSON.stringify({ type: 'ping' }));
        clearTimeout(this._pongTimer);
        this._pongTimer = setTimeout(() => ws.close(), 10000); // 沒回＝半死連線，關掉重連
    },

    _startHeartbeat() {
        this._stopHeartbeat();
        this._heartbeatTimer = setInterval(() => this._ping(), 25000);
    },

    _stopHeartbeat() {
        clearInterval(this._heartbeatTimer);
        clearTimeout(this._pongTimer);
        this._heartbeatTimer = null;
    },

    /** 回到前景／網路恢復：斷了就立刻重連，連著就重抓一次（手機背景時可能漏） */
    _bindLifecycle() {
        if (this._lifecycleBound) return;
        this._lifecycleBound = true;
        const wake = () => {
            if (!this.isLoggedIn || document.visibilityState !== 'visible') return;
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this._ping();
                this.fetchNotifications();
            } else if (!this.ws || this.ws.readyState !== WebSocket.CONNECTING) {
                this.reconnectAttempts = 0;
                this.connectWebSocket();
            }
        };
        document.addEventListener('visibilitychange', wake);
        window.addEventListener('online', wake);
    },

    disconnectWebSocket(resetReconnect = true) {
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
        this._stopHeartbeat();

        if (this.ws) {
            const ws = this.ws;
            this.ws = null; // 先放掉，onclose 看到不是自己就不會排重連
            try {
                if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
                    ws.close();
                }
            } catch (error) {
                console.warn('[NotificationService] Failed to close WebSocket cleanly', error);
            }
        }

        if (resetReconnect) {
            this.reconnectAttempts = 0;
        }
    },

    /**
     * 格式化时间
     */
    formatTime(dateString) {
        if (!dateString) return '';
        // PostgreSQL returns "2026-02-28 18:02:54.123" (space) — Android/mobile browsers
        // requires ISO 8601 "2026-02-28T18:02:54" (T) for reliable parsing
        const normalized =
            typeof dateString === 'string'
                ? dateString.replace(' ', 'T').replace(/(\.\d+)$/, '') // strip microseconds
                : dateString;
        const date = new Date(normalized);
        if (isNaN(date.getTime())) return '';

        const now = new Date();
        const diff = now - date;
        const minutes = Math.floor(diff / 60000);
        const hours = Math.floor(diff / 3600000);
        const days = Math.floor(diff / 86400000);

        const _t = (k, o) => (window.I18n ? window.I18n.t(k, o) : k);
        if (minutes < 1) return _t('time.justNow');
        if (minutes < 60) return _t('time.minutesAgo', { count: minutes });
        if (hours < 24) return _t('time.hoursAgo', { count: hours });
        if (days < 7) return _t('time.daysAgo', { count: days });

        return date.toLocaleDateString((window.I18n?.getLanguage?.() || 'zh-TW') === 'en' ? 'en-US' : 'zh-TW');
    },
};

function _deferredInit() {
    if (typeof AuthManager !== 'undefined' && AuthManager.currentUser) {
        NotificationService.init();
    } else {
        window.addEventListener('auth:ready', () => NotificationService.init(), { once: true });
    }
}

window.NotificationService = NotificationService;
window._initNotificationService = _deferredInit;

// Only init notifications after user is authenticated (deferred via auth:ready)
window.addEventListener('auth:ready', () => {
    if (!NotificationService._initialized) {
        NotificationService.init();
    }
}, { once: true });

window.addEventListener('auth:initialized', () => {
    NotificationService.init().catch((error) =>
        console.error('[NotificationService] auth:initialized sync failed:', error)
    );
});

export { NotificationService };
