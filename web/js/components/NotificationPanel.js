// ========================================
// NotificationPanel.js - 通知面板組件
// ========================================

import { NOTIFICATION_COLORS, NOTIFICATION_ICONS, showNotificationDetail } from '../notification-detail.js';

class NotificationPanel {
    constructor(container) {
        this.container = container;
        this.isVisible = false;
        this.init();
    }

    init() {
        this.render();
        this.attachEvents();

        // 监听通知更新
        window.addEventListener('notificationsUpdated', (e) => {
            if (this.isVisible) {
                this.renderNotifications(e.detail.notifications);
            }
        });
    }

    render() {
        this.container.innerHTML = `
            <div id="notification-panel" 
                 class="notification-panel fixed top-14 right-4 w-80 max-h-[70dvh] bg-surface border border-borderLight rounded-2xl shadow-2xl z-[60] hidden overflow-hidden">
                <!-- 头部 -->
                <div class="flex items-center justify-between px-4 py-3 border-b border-borderSubtle">
                    <h3 class="font-bold text-secondary" data-i18n="notification.center">Notification Center</h3>
                    <button id="notification-mark-all-read" 
                            class="text-xs text-textMuted hover:text-primary transition"
                            data-i18n="notification.markAllRead">
                        Mark all as read
                    </button>
                </div>
                
                <!-- 通知列表 -->
                <div id="notification-list" class="overflow-y-auto max-h-[calc(70dvh-50px)]">
                    <!-- 通知项会动态插入 -->
                </div>
                
                <!-- 空状态 -->
                <div id="notification-empty" class="hidden p-8 text-center">
                    <i data-lucide="bell-off" class="w-12 h-12 text-textMuted/30 mx-auto mb-3"></i>
                    <p class="text-textMuted text-sm" data-i18n="notification.empty">No notifications</p>
                </div>
            </div>
        `;

        // 初始化图标
        AppUtils.refreshIcons();
    }

    attachEvents() {
        // 全部已读按钮
        const markAllBtn = document.getElementById('notification-mark-all-read');
        if (markAllBtn) {
            markAllBtn.addEventListener('click', () => {
                NotificationService.markAllAsRead();
            });
        }

        // 阻止面板内点击冒泡
        const panel = document.getElementById('notification-panel');
        if (panel) {
            panel.addEventListener('click', (e) => {
                e.stopPropagation();
            });
        }
    }

    show() {
        this.isVisible = true;
        const panel = document.getElementById('notification-panel');
        if (panel) {
            panel.classList.remove('hidden');
            this.renderNotifications(NotificationService.getNotifications());
        }
    }

    hide() {
        this.isVisible = false;
        const panel = document.getElementById('notification-panel');
        if (panel) {
            panel.classList.add('hidden');
        }
    }

    renderNotifications(notifications) {
        const listEl = document.getElementById('notification-list');
        const emptyEl = document.getElementById('notification-empty');

        if (!listEl || !emptyEl) return;

        if (notifications.length === 0) {
            listEl.classList.add('hidden');
            emptyEl.classList.remove('hidden');
            return;
        }

        listEl.classList.remove('hidden');
        emptyEl.classList.add('hidden');

        listEl.innerHTML = notifications.map(n => this.renderNotificationItem(n)).join('');

        // 绑定事件
        listEl.querySelectorAll('.notification-item').forEach(item => {
            const id = item.dataset.id;
            
            // 点击标记已读
            item.addEventListener('click', () => {
                NotificationService.markAsRead(id);
                this.handleNotificationClick(JSON.parse(item.dataset.notification));
            });

            // 操作按钮
            item.querySelectorAll('.notification-action').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.handleAction(id, btn.dataset.action, JSON.parse(item.dataset.notification));
                });
            });
        });

        // 重新初始化图标
        AppUtils.refreshIcons();
    }

    renderNotificationItem(notification) {
        const icon = NOTIFICATION_ICONS[notification.type] || 'bell';
        const color = NOTIFICATION_COLORS[notification.type] || 'text-textMuted';
        const unreadClass = notification.is_read ? '' : 'bg-surfaceHighlight/50';
        const dotClass = notification.is_read ? 'invisible' : '';
        // 後端存固定中文；私訊（合併則數）與好友類走 i18n
        const { title, body } = NotificationService.describe(notification);

        // 操作按钮
        let actionsHtml = '';
        if (notification.type === 'friend_request' && !notification.is_read) {
            // 動態產生的：data-i18n 只在載入時翻一次，這裡直接取譯文（以前顯示英文 Accept／Reject）
            const t = (k, fb) => (window.I18n ? window.I18n.t(k) : fb);
            actionsHtml = `
                <div class="flex gap-2 mt-2">
                    <button class="notification-action text-xs px-2 py-1 bg-success/10 hover:bg-success/20 text-success rounded-lg transition" data-action="accept">
                        ${t('friends.accept', 'Accept')}
                    </button>
                    <button class="notification-action text-xs px-2 py-1 bg-danger/10 hover:bg-danger/20 text-danger rounded-lg transition" data-action="reject">
                        ${t('friends.reject', 'Reject')}
                    </button>
                </div>
            `;
        } else if (notification.type === 'group_invite' && !notification.is_read) {
            const t = (k, fb) => (window.I18n ? window.I18n.t(k) : fb);
            actionsHtml = `
                <div class="flex gap-2 mt-2">
                    <button class="notification-action text-xs px-2 py-1 bg-success/10 hover:bg-success/20 text-success rounded-lg transition" data-action="group_accept">
                        ${t('friends.accept', 'Accept')}
                    </button>
                    <button class="notification-action text-xs px-2 py-1 bg-danger/10 hover:bg-danger/20 text-danger rounded-lg transition" data-action="group_decline">
                        ${t('friends.reject', 'Reject')}
                    </button>
                </div>
            `;
        } else if (notification.type === 'system_update') {
            actionsHtml = `
                <button class="notification-action text-xs px-2 py-1 bg-primary/10 hover:bg-primary/20 text-primary rounded-lg transition mt-2" data-action="update">
                    ${window.I18n ? window.I18n.t('notification.updateNow') : 'Update Now'}
                </button>
            `;
        }

        return `
            <div class="notification-item px-4 py-3 hover:bg-surfaceHighlight cursor-pointer transition border-b border-borderSubtle ${unreadClass}"
                 data-id="${notification.id}"
                 data-notification='${JSON.stringify(notification).replace(/'/g, "&#39;")}'>
                <div class="flex gap-3">
                    <!-- 未读标记 -->
                    <div class="w-2 h-2 rounded-full bg-primary mt-2 shrink-0 ${dotClass}"></div>
                    <!-- 图标 -->
                    <div class="w-8 h-8 rounded-full bg-surfaceHighlight flex items-center justify-center shrink-0">
                        <i data-lucide="${icon}" class="w-4 h-4 ${color}"></i>
                    </div>
                    <!-- 内容 -->
                    <div class="flex-1 min-w-0">
                        <div class="text-sm font-medium text-secondary">${SecurityUtils.escapeHTML(title)}</div>
                        <div class="text-xs text-textMuted mt-0.5 line-clamp-2">${SecurityUtils.escapeHTML(body)}</div>
                        ${actionsHtml}
                        <div class="text-[10px] text-textMuted/60 mt-1">
                            ${NotificationService.formatTime(notification.created_at)}
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    handleNotificationClick(notification) {
        // 有專屬去處的跳過去；其他（公告、早報、價格提醒、錢包監控…）一律開全文——
        // 以前只有公告會開，其他點了只是關掉面板，列表又只顯示兩行，內容等於看不到
        switch (notification.type) {
            case 'friend_request':
            case 'friend_accepted':
                if (typeof switchTab === 'function') {
                    switchTab('friends');
                }
                break;
            case 'message':
                if (typeof switchTab === 'function') {
                    switchTab('friends');
                }
                if (notification.data && notification.data.from_user_id && window.SocialHub) {
                    const fromUsername = notification.data.from_username || '';
                    setTimeout(() => SocialHub.openConversation(notification.data.from_user_id, fromUsername), 300);
                }
                break;
            case 'group_message': {
                // data.group_id 是字串（後端 JSON 比對用）
                const groupId = Number(notification.data?.group_id);
                if (!groupId) break;
                if (typeof switchTab === 'function' && window.SocialHub) {
                    switchTab('friends');
                    setTimeout(() => SocialHub.openGroup(groupId), 300);
                } else {
                    // 論壇等多頁頁面沒有好友頁：帶去首頁的好友頁開群（深連結）
                    window.location.href = `/?group=${groupId}#friends`;
                }
                break;
            }
            case 'membership_expiring':
                // 續費入口在 Settings 的會員卡（crypto 沒有自動續扣，靠提醒）
                if (typeof switchTab === 'function') {
                    switchTab('settings');
                }
                break;
            case 'post_interaction':
                if (typeof switchTab === 'function') {
                    switchTab('forum');
                }
                if (notification.data && notification.data.post_id && window.ForumApp) {
                    setTimeout(() => ForumApp.loadPostDetail(notification.data.post_id), 300);
                }
                break;
            default:
                showNotificationDetail(notification);
                break;
        }

        // 关闭面板
        this.hide();
        if (window.notificationBell) {
            window.notificationBell.isPanelOpen = false;
        }
    }

    async handleAction(notificationId, action, notification) {
        switch (action) {
            case 'accept':
            case 'reject':
                await this.respondFriendRequest(notificationId, action, notification);
                break;
            case 'group_accept':
            case 'group_decline':
                await this.respondGroupInvite(notificationId, action, notification);
                break;
            case 'update':
                // 刷新页面以更新
                if (typeof showToast === 'function') {
                    showToast(window.I18n ? window.I18n.t('notification.updating') : 'Updating...', 'info');
                }
                setTimeout(() => window.location.reload(), 500);
                break;
        }
    }

    /**
     * 鈴鐺裡直接接受／拒絕：直接打 API。以前呼叫 FriendsUI，它要切過好友頁才載入，
     * 沒進過就按＝什麼都沒送出、通知卻被標已讀，邀請默默卡住。
     * 成功後後端也會把這筆清成已讀並推給其他分頁
     */
    /** 鈴鐺裡直接接受／拒絕群組邀請（後端成功後也會把這筆清成已讀並推給其他分頁） */
    async respondGroupInvite(notificationId, action, notification) {
        const t = (k, o) => (window.I18n ? window.I18n.t(k, o) : k);
        const inviteId = notification.data?.invite_id;
        if (!inviteId) return;
        const verb = action === 'group_accept' ? 'accept' : 'decline';
        try {
            await AppAPI.post(`/api/groups/invites/${Number(inviteId)}/${verb}`);
            NotificationService.applyRemoteRead([notificationId]);
            if (typeof showToast === 'function') {
                if (verb === 'accept') {
                    showToast(t('notification.groupJoined', { group: notification.data?.group_name || '' }), 'success');
                } else {
                    showToast(t('notification.groupDeclined'), 'info');
                }
            }
            window.SocialHub?.loadConversations?.();
        } catch (error) {
            // 後端錯誤碼（群滿、已達上限、不是 Pro…）→ 在地化；認不得的照原文
            const key = `groups.errors.${error.message}`;
            const text = t(key);
            if (typeof showToast === 'function') showToast(text && text !== key ? text : error.message, 'error');
        }
    }

    async respondFriendRequest(notificationId, action, notification) {
        const t = (k) => (window.I18n ? window.I18n.t(k) : k);
        const fromUserId = notification.data?.from_user_id;
        if (!fromUserId) return;
        try {
            await AppAPI.post(`/api/friends/${action}`, { target_user_id: fromUserId });
            NotificationService.applyRemoteRead([notificationId]);
            if (typeof showToast === 'function') {
                if (action === 'accept') showToast(t('friends.becameFriends'), 'success');
                else showToast(t('friends.requestRejected'), 'info');
            }
            if (typeof window.refreshFriendsUI === 'function') window.refreshFriendsUI();
        } catch (error) {
            if (typeof showToast === 'function') showToast(error.message, 'error');
        }
    }
}

// 暴露全局
window.NotificationPanel = NotificationPanel;
export { NotificationPanel };
