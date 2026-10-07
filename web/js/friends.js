/**
 * friends.js - 好友功能前端 API 客戶端
 * v1.0
 */

import { aiCardRowHtml, isAiCard } from './ai-card.js';
import { applyAvatar, avatarColorClass, avatarInitial } from './avatar.js';
import { userFacingMessage } from './error-message.js';
import { pinHoverButtonHtml, pinIndicatorHtml, pinKey } from './chat-pins.js';
import { assistantEnabled, assistantStatus, closeAssistantDrawer, openAssistantDrawer } from './chat-assistant.js';
import { attachSelectionAsk } from './selection-ask.js';
import { SocialGroups } from './social-groups.js';
import { SocialPins } from './social-pins.js';
import { SocialSearch } from './social-search.js';
import {
    escapeMessageAttr,
    formatMessageClock,
    regroupMessageRows,
    showTypingIndicator,
    hideTypingIndicator,
    insertMessageRow,
    isNearBottom,
    showNewMessagePill,
    setListTyping,
    applyListTyping,
    renderQuotaHint,
    prependOlderRows,
    markMessageRowRecalled,
    recalledPreviewText,
    recallDmMessage,
    hideDmMessage,
    readStatusHtml,
    markRowsRead,
} from './messages.js';
import {
    messageToolsHtml,
    registerMessageActions,
    copyMessageText,
    quoteHtml,
    startReply,
    getReplyTarget,
    clearReply,
    handleMessageRecalled,
    handleReplySendError,
    reactionsHtml,
    updateMessageReactions,
    renderMessageText,
    reportDmMessage,
} from './dm-message-actions.js';

const SOCIAL_MSGS = '#social-messages-container';

// 社群對話列表分頁／分類
const CONV_PAGE = 20;
const CONV_API_MAX = 100; // /api/messages/conversations 的 limit 上限
const CONV_FILTERS = ['all', 'dm', 'group'];
const CONV_FILTER_KEY = 'socialConvFilter';
// 排序方式：依最新訊息（預設）／自訂順序（DANNY 2026-10-01：一般對話也要能自己排；新訊息不往上跳）
const CONV_SORTS = ['recent', 'custom'];
const CONV_SORT_KEY = 'socialConvSort';

function readConvSort() {
    try {
        const saved = localStorage.getItem(CONV_SORT_KEY);
        return CONV_SORTS.includes(saved) ? saved : 'recent';
    } catch (_e) {
        return 'recent';
    }
}

function readConvFilter() {
    try {
        const saved = localStorage.getItem(CONV_FILTER_KEY);
        return CONV_FILTERS.includes(saved) ? saved : 'all';
    } catch (_e) {
        return 'all';
    }
}

/** 列表排序用的時間（私訊與群組的時間字串格式不一定一樣，比字串會錯） */
function itemTime(item) {
    return Date.parse(item.last_message_at || item.created_at || '') || 0;
}

/** 私訊列表前 total 個：後端一次最多 100，超過就分段一起抓 */
async function fetchConversationPages(myId, total, order = 'recent') {
    const requests = [];
    for (let offset = 0; offset < total; offset += CONV_API_MAX) {
        const limit = Math.min(CONV_API_MAX, total - offset);
        // 自訂順序時後端照自己排的位置分頁（不然第 21 個之後的順序會亂）
        requests.push(
            AppAPI.get(`/api/messages/conversations?limit=${limit}&offset=${offset}&order=${order}&user_id=${myId}`)
        );
    }
    const pages = await Promise.all(requests);
    return {
        total_unread: pages[0]?.total_unread || 0,
        conversations: pages.flatMap((page) => page.conversations || []),
    };
}

// Safe i18n wrapper - i18n.js loads at Phase 17, friends.js at Phase 10
const t = (key) => window.i18next?.t(key) || key;

const FriendsAPI = {
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
     * 搜尋用戶
     * @param {string} query - 搜尋關鍵字
     * @param {number} limit - 結果數量限制
     */
    async searchUsers(query, limit = 20) {
        const userId = this._getUserId();
        if (!userId) throw new Error(t('friends.loginRequired'));

        return await AppAPI.get(
            `/api/friends/search?q=${encodeURIComponent(query)}&limit=${limit}`,
        );
    },

    /**
     * 取得用戶資料
     * @param {string} targetUserId - 目標用戶 ID
     */
    async getProfile(targetUserId) {
        return await AppAPI.get(`/api/friends/profile/${encodeURIComponent(targetUserId)}`);
    },

    /**
     * 發送好友請求
     * @param {string} targetUserId - 目標用戶 ID
     */
    async sendRequest(targetUserId) {
        const userId = this._getUserId();
        if (!userId) throw new Error(t('friends.loginRequired'));

        return await AppAPI.post('/api/friends/request', { target_user_id: targetUserId });
    },

    /**
     * 接受好友請求
     * @param {string} requesterId - 發送請求的用戶 ID
     */
    async acceptRequest(requesterId) {
        const userId = this._getUserId();
        if (!userId) throw new Error(t('friends.loginRequired'));

        return await AppAPI.post('/api/friends/accept', { target_user_id: requesterId });
    },

    /**
     * 拒絕好友請求
     * @param {string} requesterId - 發送請求的用戶 ID
     */
    async rejectRequest(requesterId) {
        const userId = this._getUserId();
        if (!userId) throw new Error(t('friends.loginRequired'));

        return await AppAPI.post('/api/friends/reject', { target_user_id: requesterId });
    },

    /**
     * 取消已發送的好友請求
     * @param {string} targetUserId - 目標用戶 ID
     */
    async cancelRequest(targetUserId) {
        const userId = this._getUserId();
        if (!userId) throw new Error(t('friends.loginRequired'));

        return await AppAPI.post('/api/friends/cancel', { target_user_id: targetUserId });
    },

    /**
     * 移除好友
     * @param {string} friendId - 好友的用戶 ID
     */
    async removeFriend(friendId) {
        const userId = this._getUserId();
        if (!userId) throw new Error(t('friends.loginRequired'));

        return await AppAPI.delete(`/api/friends/remove?target_user_id=${encodeURIComponent(friendId)}`);
    },

    /**
     * 取得好友列表
     * @param {number} limit - 結果數量限制
     * @param {number} offset - 偏移量
     */
    async getFriends(limit = 50, offset = 0) {
        const userId = this._getUserId();
        if (!userId) throw new Error(t('friends.loginRequired'));

        return await AppAPI.get(`/api/friends/list?limit=${limit}&offset=${offset}`);
    },

    /**
     * 取得收到的好友請求
     */
    async getReceivedRequests() {
        const userId = this._getUserId();
        if (!userId) throw new Error(t('friends.loginRequired'));

        return await AppAPI.get('/api/friends/requests/received');
    },

    /**
     * 取得已發送的好友請求
     */
    async getSentRequests() {
        const userId = this._getUserId();
        if (!userId) throw new Error(t('friends.loginRequired'));

        return await AppAPI.get('/api/friends/requests/sent');
    },

    /**
     * 取得與特定用戶的好友狀態
     * @param {string} targetUserId - 目標用戶 ID
     */
    async getStatus(targetUserId) {
        const userId = this._getUserId();
        if (!userId) return { status: null, is_friend: false };

        return await AppAPI.get(`/api/friends/status/${encodeURIComponent(targetUserId)}`);
    },

    /**
     * 取得好友相關數量
     */
    async getCounts() {
        const userId = this._getUserId();
        if (!userId) return { friends_count: 0, pending_received: 0 };

        return await AppAPI.get('/api/friends/counts');
    },

    /**
     * 封鎖用戶
     * @param {string} targetUserId - 目標用戶 ID
     */
    async blockUser(targetUserId) {
        const userId = this._getUserId();
        if (!userId) throw new Error(t('friends.loginRequired'));

        return await AppAPI.post('/api/friends/block', { target_user_id: targetUserId });
    },

    /**
     * 解除封鎖
     * @param {string} targetUserId - 目標用戶 ID
     */
    async unblockUser(targetUserId) {
        const userId = this._getUserId();
        if (!userId) throw new Error(t('friends.loginRequired'));

        return await AppAPI.post('/api/friends/unblock', { target_user_id: targetUserId });
    },

    /**
     * 取得封鎖名單
     */
    async getBlockedUsers() {
        const userId = this._getUserId();
        if (!userId) throw new Error(t('friends.loginRequired'));

        return await AppAPI.get('/api/friends/blocked');
    },
};

// 匯出到全域
window.FriendsAPI = FriendsAPI;

/**
 * 好友功能 UI 工具函數
 */
const FriendsUI = {
    /**
     * 格式化時間
     */
    formatTime(dateString) {
        if (!dateString) return '';
        // Fix: If date string has no timezone (e.g. from SQLite/MySQL datetime), treat as UTC
        if (!dateString.includes('Z') && !dateString.includes('+')) {
            dateString += 'Z';
        }
        const date = new Date(dateString);
        const now = new Date();
        const diff = now - date;

        if (diff < 60000) return t('time.justNow');
        if (diff < 3600000) return (t('time.minutesAgo')).replace('{{count}}', Math.floor(diff / 60000));
        if (diff < 86400000) return (t('time.hoursAgo')).replace('{{count}}', Math.floor(diff / 3600000));
        if (diff < 604800000) return (t('time.daysAgo')).replace('{{count}}', Math.floor(diff / 86400000));
        return date.toLocaleDateString('zh-TW');
    },

    /**
     * 取得會員等級徽章
     */
    getMembershipBadge(tier) {
        if (['premium', 'pro', 'plus'].includes((tier || 'free').toLowerCase())) {
            return '<span class="shrink-0 whitespace-nowrap px-1.5 py-0.5 text-[10px] leading-none font-bold bg-gradient-to-r from-yellow-500 to-orange-500 text-black rounded">PREMIUM</span>';
        }
        return '';
    },

    /**
     * 取得好友狀態按鈕 HTML
     */
    getFriendButton(userId, status, isRequester, blockedByMe = false) {
        if (status === 'accepted') {
            return `
                <div class="flex gap-2">
                    <button data-click="FriendsUI.handleRemoveFriend" data-click-arg="${encodeURIComponent(userId)}"
                            class="friend-btn bg-surfaceHighlight text-textMuted px-3 py-1.5 rounded-lg text-sm font-bold flex items-center gap-1 border border-borderLight hover:bg-danger/10 hover:text-danger hover:border-danger/20 transition group">
                        <i data-lucide="user-check" class="w-4 h-4 group-hover:hidden"></i>
                        <i data-lucide="user-minus" class="w-4 h-4 hidden group-hover:block"></i>
                        <span class="hidden sm:inline group-hover:hidden">${t('friends.friend')}</span>
                        <span class="hidden group-hover:inline">${t('friends.remove')}</span>
                    </button>
                    <button data-click="FriendsUI.handleBlock" data-click-arg="${encodeURIComponent(userId)}"
                            class="friend-btn bg-danger/5 text-danger/80 px-2 py-1.5 rounded-lg text-sm font-bold flex items-center gap-1 border border-danger/10 hover:bg-danger/20 hover:text-danger transition"
                            title="${t('friends.blockUserTitle')}">
                        <i data-lucide="ban" class="w-4 h-4"></i>
                    </button>
                </div>
            `;
        }
        if (status === 'pending') {
            if (isRequester) {
                return `
                    <button id="cancel-request-btn-${userId}" data-click="FriendsUI.handleCancelRequest" data-click-arg="${encodeURIComponent(userId)}"
                            class="friend-btn bg-surfaceHighlight text-textMuted px-3 py-1.5 rounded-lg text-sm font-bold flex items-center gap-1 border border-borderLight hover:bg-danger/10 hover:text-danger hover:border-danger/20 transition">
                        <i data-lucide="clock" class="w-4 h-4"></i>
                        <span>${t('friends.pendingStatus')}</span>
                    </button>
                `;
            }
            return `
                <div class="flex gap-2">
                    <button id="accept-btn-${userId}" data-click="FriendsUI.handleAcceptRequest" data-click-arg="${encodeURIComponent(userId)}"
                            class="bg-success/10 hover:bg-success/20 text-success px-3 py-1.5 rounded-lg text-sm font-bold transition disabled:opacity-50 disabled:cursor-not-allowed">
                        <i data-lucide="check" class="w-4 h-4"></i>
                    </button>
                    <button id="reject-btn-${userId}" data-click="FriendsUI.handleRejectRequest" data-click-arg="${encodeURIComponent(userId)}"
                            class="bg-danger/10 hover:bg-danger/20 text-danger px-3 py-1.5 rounded-lg text-sm font-bold transition disabled:opacity-50 disabled:cursor-not-allowed">
                        <i data-lucide="x" class="w-4 h-4"></i>
                    </button>
                </div>
            `;
        }
        // 被對方封鎖（我沒封鎖對方）：只顯示狀態，不給解除按鈕——按了只會 400
        if (status === 'blocked' && !blockedByMe) {
            return `
                <span class="friend-btn bg-surfaceHighlight text-textMuted px-3 py-1.5 rounded-lg text-sm font-bold flex items-center gap-1 border border-borderLight cursor-default">
                    <i data-lucide="user-x" class="w-4 h-4"></i>
                    <span>${t('friends.unavailable')}</span>
                </span>
            `;
        }
        if (status === 'blocked') {
            return `
                <button data-click="FriendsUI.handleUnblock" data-click-arg="${encodeURIComponent(userId)}"
                        class="friend-btn bg-danger/10 text-danger px-3 py-1.5 rounded-lg text-sm font-bold flex items-center gap-1 border border-danger/20 hover:bg-danger/20 transition">
                    <i data-lucide="ban" class="w-4 h-4"></i>
                    <span>${t('friends.blockedStatus')}</span>
                </button>
            `;
        }
        // 預設：加好友按鈕
        return `
            <button id="add-friend-btn-${userId}" data-click="FriendsUI.handleAddFriend" data-click-arg="${encodeURIComponent(userId)}"
                    class="friend-btn bg-primary/10 hover:bg-primary/20 text-primary px-3 py-1.5 rounded-lg text-sm font-bold flex items-center gap-1 transition border border-primary/20 disabled:opacity-50 disabled:cursor-not-allowed">
                <i data-lucide="user-plus" class="w-4 h-4"></i>
                <span>${t('friends.addFriend')}</span>
            </button>
        `;
    },

    /**
     * 渲染用戶卡片
     */
    renderUserCard(user, showActions = true) {
        const badge = this.getMembershipBadge(user.membership_tier);
        const actionBtn = showActions
            ? this.getFriendButton(user.user_id, user.friend_status, user.is_requester, user.blocked_by_me === true)
            : '';
        // 顯示暱稱（沒設才用帳號名）；有暱稱時第二行附 @帳號名，同名或長得像的人分得出來
        const name = user.display_name || user.username || user.user_id;
        const handle = user.display_name && user.username ? `@${user.username} · ` : '';
        const initial = SecurityUtils.escapeHTML(avatarInitial(name));

        // 如果是好友，顯示發訊息按鈕
        const messageBtn =
            user.friend_status === 'accepted'
                ? `
            <button data-click="friendsOpenChat" data-user-id="${encodeURIComponent(user.user_id)}" data-username="${SecurityUtils.escapeHTML(name)}"
               class="p-2 hover:bg-surfaceHighlight rounded-lg transition text-textMuted hover:text-primary"
               title="${t('friends.sendMsgTitle')}">
                <i data-lucide="message-circle" class="w-4 h-4"></i>
            </button>
        `
                : '';

        return `
            <div class="user-card bg-surface border border-borderSubtle rounded-xl p-4 flex items-center justify-between hover:border-borderLight transition">
                <a href="/static/forum/profile.html?id=${encodeURIComponent(user.user_id)}" data-click="setReturnTabFriends" class="flex items-center gap-3 flex-1 min-w-0">
                    <div class="w-10 h-10 rounded-full ${avatarColorClass(user.user_id)} flex items-center justify-center font-bold flex-shrink-0">
                        ${initial}
                    </div>
                    <div class="min-w-0 flex-1">
                        <!-- 名字獨佔一行、徽章放第二行：手機上右側三顆按鈕寬度固定，名字跟徽章同列時
                             被擠到 0 寬，只露出第一個字母的邊（看起來像「I」） -->
                        <span class="block font-bold text-textMain truncate">${SecurityUtils.escapeHTML(name)}</span>
                        <div class="flex items-center gap-1.5 min-w-0 text-xs text-textMuted">
                            ${badge}
                            <span class="truncate">${SecurityUtils.escapeHTML(handle)}
                            ${user.last_active_at ? (t('friends.lastActive')) + this.formatTime(user.last_active_at) : user.friends_since ? (t('friends.friendsSince')) + this.formatTime(user.friends_since) : ''}
                            ${user.requested_at ? (t('friends.requestReceived')) + this.formatTime(user.requested_at) : ''}
                            ${user.sent_at ? (t('friends.requestSentLabel')) + this.formatTime(user.sent_at) : ''}</span>
                        </div>
                    </div>
                </a>
                <div class="flex-shrink-0 ml-2 flex items-center gap-2">
                    ${messageBtn}
                    ${actionBtn}
                </div>
            </div>
        `;
    },

    /**
     * 處理加好友
     */
    async handleAddFriend(userId) {
        const btn = document.getElementById(`add-friend-btn-${userId}`);

        // 立即顯示加載狀態
        if (btn) {
            btn.disabled = true;
            btn.innerHTML =
                '<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i><span>' + (t('friends.sending')) + '</span>';
            AppUtils.refreshIcons();
        }

        try {
            await FriendsAPI.sendRequest(userId);
            if (typeof showToast === 'function') {
                showToast(t('friends.requestSent'), 'success');
            }

            // 更新為「等待中」按鈕
            if (btn) {
                btn.id = `cancel-request-btn-${userId}`;
                btn.disabled = false;
                btn.className =
                    'friend-btn bg-surfaceHighlight text-textMuted px-3 py-1.5 rounded-lg text-sm font-bold flex items-center gap-1 border border-borderLight hover:bg-danger/10 hover:text-danger hover:border-danger/20 transition';
                btn.innerHTML = '<i data-lucide="clock" class="w-4 h-4"></i><span>' + t('friends.pendingStatus') + '</span>';
                btn.onclick = (e) => {
                    e.stopPropagation();
                    e.preventDefault();
                    FriendsUI.handleCancelRequest(userId);
                };
                AppUtils.refreshIcons();
            }
        } catch (error) {
            // 恢復按鈕狀態
            if (btn) {
                btn.disabled = false;
                btn.innerHTML =
                    '<i data-lucide="user-plus" class="w-4 h-4"></i><span>' + (t('friends.addFriend')) + '</span>';
                AppUtils.refreshIcons();
            }

            const message = userFacingMessage(error, { fallbackKey: 'friends.sendRequestFailed' });
            if (typeof showToast === 'function') {
                showToast(message, 'error');
            } else {
                alert(message);
            }
        }
    },

    /**
     * 處理接受請求
     */
    async handleAcceptRequest(userId) {
        const acceptBtn = document.getElementById(`accept-btn-${userId}`);
        const rejectBtn = document.getElementById(`reject-btn-${userId}`);

        // 禁用按鈕防止重複點擊
        if (acceptBtn) {
            acceptBtn.disabled = true;
            acceptBtn.innerHTML = '<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i>';
        }
        if (rejectBtn) rejectBtn.disabled = true;

        try {
            await FriendsAPI.acceptRequest(userId);
            if (typeof showToast === 'function') {
                showToast(t('friends.becameFriends'), 'success');
            }
            if (typeof refreshFriendsUI === 'function') {
                refreshFriendsUI();
            } else {
                location.reload();
            }
        } catch (error) {
            // 恢復按鈕狀態
            if (acceptBtn) {
                acceptBtn.disabled = false;
                acceptBtn.innerHTML = '<i data-lucide="check" class="w-4 h-4"></i>';
                AppUtils.refreshIcons();
            }
            if (rejectBtn) rejectBtn.disabled = false;

            const message = userFacingMessage(error, { fallbackKey: 'friends.acceptFailed' });
            if (typeof showToast === 'function') {
                showToast(message, 'error');
            } else {
                alert(message);
            }
        }
    },

    /**
     * 處理拒絕請求
     */
    async handleRejectRequest(userId) {
        const acceptBtn = document.getElementById(`accept-btn-${userId}`);
        const rejectBtn = document.getElementById(`reject-btn-${userId}`);

        // 禁用按鈕防止重複點擊
        if (rejectBtn) {
            rejectBtn.disabled = true;
            rejectBtn.innerHTML = '<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i>';
        }
        if (acceptBtn) acceptBtn.disabled = true;

        try {
            await FriendsAPI.rejectRequest(userId);
            if (typeof showToast === 'function') {
                showToast(t('friends.requestRejected'), 'info');
            }
            if (typeof refreshFriendsUI === 'function') {
                refreshFriendsUI();
            } else {
                location.reload();
            }
        } catch (error) {
            // 恢復按鈕狀態
            if (rejectBtn) {
                rejectBtn.disabled = false;
                rejectBtn.innerHTML = '<i data-lucide="x" class="w-4 h-4"></i>';
                AppUtils.refreshIcons();
            }
            if (acceptBtn) acceptBtn.disabled = false;

            const message = userFacingMessage(error, { fallbackKey: 'friends.rejectFailed' });
            if (typeof showToast === 'function') {
                showToast(message, 'error');
            } else {
                alert(message);
            }
        }
    },

    /**
     * 處理取消請求
     */
    async handleCancelRequest(userId) {
        const btn = document.getElementById(`cancel-request-btn-${userId}`);

        // 立即顯示加載狀態
        if (btn) {
            btn.disabled = true;
            btn.innerHTML =
                '<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i><span>' + (t('friends.canceling')) + '</span>';
            AppUtils.refreshIcons();
        }

        try {
            await FriendsAPI.cancelRequest(userId);
            if (typeof showToast === 'function') {
                showToast(t('friends.requestCanceled'), 'info');
            }

            // 更新為「加好友」按鈕
            if (btn) {
                btn.id = `add-friend-btn-${userId}`;
                btn.disabled = false;
                btn.className =
                    'friend-btn bg-primary/10 hover:bg-primary/20 text-primary px-3 py-1.5 rounded-lg text-sm font-bold flex items-center gap-1 transition border border-primary/20 disabled:opacity-50 disabled:cursor-not-allowed';
                btn.innerHTML =
                    '<i data-lucide="user-plus" class="w-4 h-4"></i><span>' + t('friends.addFriend') + '</span>';
                btn.onclick = (e) => {
                    e.stopPropagation();
                    e.preventDefault();
                    FriendsUI.handleAddFriend(userId);
                };
                AppUtils.refreshIcons();
            }
        } catch (error) {
            // 恢復按鈕狀態
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = '<i data-lucide="clock" class="w-4 h-4"></i><span>' + (t('friends.pendingStatus')) + '</span>';
                AppUtils.refreshIcons();
            }

            const message = userFacingMessage(error, { fallbackKey: 'friends.cancelFailed' });
            if (typeof showToast === 'function') {
                showToast(message, 'error');
            } else {
                alert(message);
            }
        }
    },

    /**
     * 處理移除好友
     */
    async handleRemoveFriend(userId) {
        if (typeof showConfirm === 'function') {
            const confirmed = await showConfirm({
                title: t('friends.confirmRemoveTitle'),
                message: t('friends.confirmRemoveMessage'),
                type: 'warning',
                confirmText: t('friends.confirmRemoveBtn'),
                cancelText: t('common.cancel'),
            });
            if (!confirmed) return;
        } else if (!confirm(t('friends.confirmRemoveMessage'))) {
            return;
        }

        try {
            await FriendsAPI.removeFriend(userId);
            if (typeof showToast === 'function') {
                showToast(t('friends.friendRemoved'), 'info');
            }
            if (typeof refreshFriendsUI === 'function') {
                refreshFriendsUI();
            } else {
                location.reload();
            }
        } catch (error) {
            const message = userFacingMessage(error, { fallbackKey: 'friends.removeFriendFailed' });
            if (typeof showToast === 'function') {
                showToast(message, 'error');
            } else {
                alert(message);
            }
        }
    },

    /**
     * 處理封鎖
     */
    async handleBlock(userId) {
        if (typeof showConfirm === 'function') {
            const confirmed = await showConfirm({
                title: t('friends.blockUserTitle'),
                message: t('friends.blockUserMessage'),
                type: 'danger',
                confirmText: t('friends.confirmBlockBtn'),
                cancelText: t('common.cancel'),
            });
            if (!confirmed) return;
        } else if (!confirm(t('friends.confirmBlockMessage'))) {
            return;
        }

        try {
            await FriendsAPI.blockUser(userId);
            if (typeof showToast === 'function') {
                showToast(t('friends.userBlocked'), 'info');
            }
            // Refresh in-place without reloading
            await loadFriendsTabData();
            window.NavBadges?.schedule(); // 對話從列表消失、對方送我的邀請被刪掉：側欄「社群」數字會變
        } catch (error) {
            const message = userFacingMessage(error, { fallbackKey: 'friends.blockFailed' });
            if (typeof showToast === 'function') {
                showToast(message, 'error');
            } else {
                alert(message);
            }
        }
    },

    /**
     * 處理解除封鎖
     */
    async handleUnblock(userId) {
        try {
            const result = await FriendsAPI.unblockUser(userId);
            if (typeof showToast === 'function') {
                // 封鎖前是好友：解除後恢復好友（LINE 式，c062）
                showToast(
                    t(result?.friendship_restored ? 'friends.unblockedFriendRestored' : 'friends.userUnblocked'),
                    'success'
                );
            }
            // Refresh in-place without reloading
            await loadFriendsTabData();
            window.NavBadges?.schedule(); // 恢復好友後對話回到列表，它的未讀又會算進側欄數字
        } catch (error) {
            const message = userFacingMessage(error, { fallbackKey: 'friends.unblockFailed' });
            if (typeof showToast === 'function') {
                showToast(message, 'error');
            } else {
                alert(message);
            }
        }
    },

    // Toggle between Friends list and Blocked list view
    toggleBlockedView() {
        const friendsView = document.getElementById('friends-view-container');
        const blockedView = document.getElementById('blocked-view-container');
        const toggleBtnSpan = document.querySelector(
            'button[data-click="FriendsUI.toggleBlockedView"] span'
        );

        if (!friendsView || !blockedView) return;

        if (friendsView.classList.contains('hidden')) {
            // Switch to Friends View
            friendsView.classList.remove('hidden');
            blockedView.classList.add('hidden');
            if (toggleBtnSpan) toggleBtnSpan.textContent = t('friends.manageBlocked');
        } else {
            // Switch to Blocked View
            friendsView.classList.add('hidden');
            blockedView.classList.remove('hidden');
            if (toggleBtnSpan) toggleBtnSpan.textContent = t('friends.backToFriendsList');
        }
    },

    /**
     * 開啟與用戶的聊天（切換到聊天 tab 並開啟對話）
     */
    openChat(userId, username) {
        // 所有寬度都是同一個聊天室（好友頁的聊天欄；一次一欄時自己會切到聊天）。
        // 以前手機會跳去另一頁 messages.html——那是兩套 UI（2026-10-05 統一，舊網址由後端轉址）
        if (typeof SocialHub !== 'undefined') {
            SocialHub.switchSubTab('messages');
            SocialHub.openConversation(userId, username);
        }
    },
};

window.FriendsUI = FriendsUI;

// 定義 refreshFriendsUI 函數，讓所有好友操作都能正常更新
function refreshFriendsUI() {
    // 如果在好友 Tab 中，重新載入好友數據
    if (typeof loadFriendsTabData === 'function') {
        loadFriendsTabData();
    }
    // 如果 SocialHub 存在，刷新
    if (typeof SocialHub !== 'undefined' && SocialHub.refresh) {
        SocialHub.refresh();
    }
    window.NavBadges?.schedule(); // 好友邀請數字會變（pending count 不依賴通知已讀狀態）
}
window.refreshFriendsUI = refreshFriendsUI;

// ========================================
// Helper Functions
// ========================================

/**
 * Update badge count and visibility
 */
function updateBadge(elementId, count, hideIfZero = false) {
    const el = document.getElementById(elementId);
    if (!el) return;

    el.textContent = count > 99 ? '99+' : count;

    if (hideIfZero && count === 0) {
        el.classList.add('hidden');
    } else {
        el.classList.remove('hidden');
    }
}

/**
 * Render empty state HTML
 */
function renderEmptyState(message) {
    return `
        <div class="text-center py-6 opacity-50">
            <p class="text-sm text-textMuted">${message}</p>
        </div>
    `;
}

/**
 * Render error state HTML
 */
function renderErrorState(message) {
    return `
        <div class="text-center py-6 text-danger opacity-80">
            <i data-lucide="alert-circle" class="w-5 h-5 mx-auto mb-2"></i>
            <p class="text-sm">${SecurityUtils.escapeHTML(message || '')}</p>
        </div>
    `;
}

// ========================================
// Friends Logic Controller (UI Orchestration)
// ========================================

/**
 * 載入好友分頁的所有數據
 */
async function loadFriendsTabData() {
    window.APP_CONFIG?.DEBUG_MODE && console.log('loadFriendsTabData called');

    // Check if AuthManager exists
    if (typeof AuthManager === 'undefined') {
        console.error('AuthManager not found');
        return;
    }

    const isLoggedIn = AuthManager.isLoggedIn();
    const pendingListEl = document.getElementById('pending-requests-list');
    const friendsListEl = document.getElementById('friends-list');
    const blockedListEl = document.getElementById('blocked-users-list');

    // Reset badges
    updateBadge('friends-request-badge', 0, true);
    updateBadge('pending-count-badge', 0, true);
    updateBadge('friends-count-badge', 0, true);
    updateBadge('blocked-count-badge', 0, true);

    if (!isLoggedIn) {
        const loginMsg = `<div class="text-center py-6"><p class="text-textMuted mb-3">${t('friends.loginToUseFriends')}</p><button data-click="safeEvmLogin" data-tma-hide class="px-4 py-2 bg-primary/10 text-primary rounded-lg text-sm font-bold">${t('friends.loginBtn')}</button></div>`;
        if (pendingListEl) pendingListEl.innerHTML = loginMsg;
        if (friendsListEl) friendsListEl.innerHTML = loginMsg;
        if (blockedListEl) blockedListEl.innerHTML = renderEmptyState(t('friends.loginRequired'));
        return;
    }

    // Load Data in Parallel
    try {
        const [requestsRes, friendsRes, blockedRes] = await Promise.all([
            FriendsAPI.getReceivedRequests().catch((e) => ({ error: e })),
            FriendsAPI.getFriends().catch((e) => ({ error: e })),
            FriendsAPI.getBlockedUsers().catch((e) => ({ error: e })),
        ]);

        // Debug: Log API errors
        if (requestsRes.error || friendsRes.error || blockedRes.error) {
            console.error('[Friends] API errors:', {
                requests: requestsRes.error?.message || requestsRes.error,
                friends: friendsRes.error?.message || friendsRes.error,
                blocked: blockedRes.error?.message || blockedRes.error,
            });
        }

        // Render Requests
        if (pendingListEl) {
            if (requestsRes.error) {
                const errMsg = userFacingMessage(requestsRes.error, { fallbackKey: 'friends.getRequestsFailed' });
                pendingListEl.innerHTML = renderErrorState(errMsg);
            } else if (!requestsRes.requests || requestsRes.requests.length === 0) {
                pendingListEl.innerHTML = renderEmptyState(t('friends.noPendingRequests'));
            } else {
                pendingListEl.innerHTML = requestsRes.requests
                    .map((req) => {
                        // Normalize data structure if needed
                        const user = {
                            ...req,
                            friend_status: 'pending',
                            is_requester: false, // We are receiving, so we are NOT the requester
                        };
                        return FriendsUI.renderUserCard(user);
                    })
                    .join('');

                // Update badge
                updateBadge('pending-count-badge', requestsRes.requests.length);
                updateBadge('friends-request-badge', requestsRes.requests.length, true); // 好友分頁鈕
            }
        }

        // Render Friends
        if (friendsListEl) {
            if (friendsRes.error) {
                const errMsg = userFacingMessage(friendsRes.error, { fallbackKey: 'friends.getFriendsFailed' });
                friendsListEl.innerHTML = renderErrorState(errMsg);
            } else if (!friendsRes.friends || friendsRes.friends.length === 0) {
                friendsListEl.innerHTML = renderEmptyState(t('friends.noFriendsYet'));
            } else {
                friendsListEl.innerHTML = friendsRes.friends
                    .map((friend) => {
                        const user = {
                            ...friend,
                            friend_status: 'accepted',
                        };
                        return FriendsUI.renderUserCard(user);
                    })
                    .join('');
                updateBadge('friends-count-badge', friendsRes.friends.length);
            }
        }

        // Render Blocked
        if (blockedListEl) {
            if (blockedRes.error) {
                const errMsg = userFacingMessage(blockedRes.error, { fallbackKey: 'friends.getBlockedFailed' });
                blockedListEl.innerHTML = renderErrorState(errMsg);
            } else if (!blockedRes.blocked_users || blockedRes.blocked_users.length === 0) {
                blockedListEl.innerHTML = renderEmptyState(t('friends.blocklistEmpty'));
            } else {
                blockedListEl.innerHTML = blockedRes.blocked_users
                    .map((user) => {
                        const u = {
                            ...user,
                            friend_status: 'blocked',
                            blocked_by_me: true, // 封鎖清單裡都是我封鎖的人
                        };
                        return FriendsUI.renderUserCard(u);
                    })
                    .join('');
                // updateBadge('blocked-count-badge', blockedRes.blocked_users.length); // Tab badge removed
                updateBadge('blocked-count-badge-content', blockedRes.blocked_users.length, true); // Content header badge (if kept) or could be reused
            }
        }

        // Re-initialize icons after rendering dynamic content
        AppUtils.refreshIcons();
    } catch (e) {
        console.error('Failed to load friend data:', e);
        if (typeof showToast === 'function') showToast(t('friends.loadFriendsFailed'), 'error');
    }
}

/** 「好友」分頁鈕上的待回覆邀請數：在訊息分頁也看得到有人加你（沒進過好友分頁也一樣） */
async function loadRequestBadge() {
    try {
        const res = await FriendsAPI.getReceivedRequests();
        updateBadge('friends-request-badge', res.requests?.length || 0, true);
    } catch {
        // 抓不到就不顯示，不打擾
    }
}

/**
 * 處理好友搜尋
 */
let searchTimeout = null;
async function handleFriendSearch(query) {
    const resultsEl = document.getElementById('search-results');
    if (!resultsEl) return;

    if (!query || query.trim().length === 0) {
        resultsEl.classList.add('hidden');
        resultsEl.innerHTML = '';
        return;
    }

    resultsEl.classList.remove('hidden');
    resultsEl.innerHTML = `
        <div class="text-center py-4 text-textMuted">
            <i data-lucide="loader-2" class="w-4 h-4 animate-spin inline-block mr-2"></i>
            ${t('friends.searching')}
        </div>
    `;
    AppUtils.refreshIcons();

    // Debounce
    if (searchTimeout) clearTimeout(searchTimeout);

    searchTimeout = setTimeout(async () => {
        try {
            const res = await FriendsAPI.searchUsers(query);

            if (res.users && res.users.length > 0) {
                resultsEl.innerHTML = res.users
                    .map((user) => FriendsUI.renderUserCard(user))
                    .join('');
            } else {
                resultsEl.innerHTML = renderEmptyState(t('friends.noUsersFound'));
            }

            // 重新初始化 Lucide 圖標
            AppUtils.refreshIcons();
        } catch (e) {
            resultsEl.innerHTML = `<div class="text-center text-danger py-4">${SecurityUtils.escapeHTML(userFacingMessage(e, { fallbackKey: 'friends.searchFailed' }))}</div>`;
        }
    }, 500);
}
// click-delegator（classic script）靠全域名稱呼叫；少這行輸入框就默默沒反應
window.handleFriendSearch = handleFriendSearch;

// ========================================
// SocialHub (Friends Tab Logic)
// ========================================

const SocialHub = {
    container: null,
    activeSubTab: 'messages', // 'messages' or 'friends'
    isSending: false, // 私訊送出中（擋 Enter 重送）

    /**
     * 初始化 Friends Tab (SocialHub)
     */
    init: async function () {
        window.APP_CONFIG?.DEBUG_MODE && console.log('SocialHub initializing...');
        this.container = document.getElementById('friends-tab');

        // Check MessagesAPI availability
        if (typeof MessagesAPI === 'undefined') {
            console.debug('SocialHub: MessagesAPI not available, using AppAPI directly');
        }

        await this.render();
        this.setupRealtime();
        this.initAssistant();
        this.initSelectionAsk();
        this.openGroupFromUrl(); // /?group=<id>#friends（social-groups.js）
        this.openChatFromUrl(); // /?chat=<userId>[&msg=<id>]#friends（個人頁「發訊息」、舊的 messages.html 網址）
    },

    /**
     * 私訊深連結 /?chat=<userId>[&msg=<id>]#friends：個人頁的「發訊息」、社群搜尋、舊私訊頁的
     * 網址（api_server 302 過來）都走這裡，進同一個聊天室。
     * 用完就把參數從網址拿掉（重新整理不會又開一次）；msg＝載完跳到那則。
     */
    openChatFromUrl: function () {
        const params = new URLSearchParams(window.location.search || '');
        const raw = params.get('chat');
        if (!raw) return;
        const msg = Number(params.get('msg')) || 0;
        params.delete('chat');
        params.delete('msg');
        const qs = params.toString();
        history.replaceState(history.state, '', `${location.pathname}${qs ? `?${qs}` : ''}${location.hash || ''}`);
        if (this.activeSubTab && this.activeSubTab !== 'messages') this.switchSubTab('messages');
        const userId = encodeURIComponent(raw);
        if (msg) this._pendingJump = { key: `dm:${userId}`, messageId: msg };
        // 先開（名字留空，標題列暫時顯示 id），名字查到再補，不讓開對話等這一步
        this.openConversation(userId, '');
        this._resolveChatName(raw).then((name) => {
            if (name && this.currentChatUserId === userId && !this.currentChatUsername) {
                this.currentChatUsername = name;
                this.showChatContent(userId, name);
            }
        });
    },

    /** 對方的顯示名稱：對話列表有就用；沒有（還沒聊過的好友）查公開資料；都查不到回空字串 */
    _resolveChatName: async function (rawUserId) {
        const conv = (this._convData?.conversations || []).find((c) => c.other_user_id === rawUserId);
        const known = conv && (conv.other_display_name || conv.other_username);
        if (known) return known;
        try {
            const res = await FriendsAPI.getProfile(rawUserId);
            const p = res && res.profile;
            return (p && (p.display_name || p.username)) || '';
        } catch (_e) {
            return '';
        }
    },

    /**
     * 即時訊息（所有寬度）。init 每次切進好友頁都會跑，綁定只做一次
     */
    setupRealtime: function () {
        // 訊息選單：手機長按、桌機「⋯」／右鍵（document 層委派，分頁重繪容器也不用重綁）
        // 開著群組時用群組的設定（social-groups.js）
        registerMessageActions(SOCIAL_MSGS, this.currentGroupId ? this.groupActionsConfig() : this.dmActionsConfig());
        if (typeof MessagesWebSocket === 'undefined') return;
        if (!this._realtimeBound) {
            this._realtimeBound = true;
            MessagesWebSocket.onMessage((message, isSent) => this.onRealtimeMessage(message, isSent));
            MessagesWebSocket.onTyping((data) => {
                const typing = data.state !== 'stop';
                // 左邊列表那一列：不管有沒有開著這個對話都顯示「輸入中…」
                setListTyping(document.getElementById('social-conv-list'), data.conversation_id, typing);
                if (String(data.conversation_id) !== String(this.currentConversationId)) return;
                const container = document.getElementById('social-messages-container');
                if (typing) showTypingIndicator(container, data.from_username || this.currentChatUsername);
                else hideTypingIndicator(container);
            });
            // 斷線重連／回到前景：補抓漏掉的訊息
            MessagesWebSocket.onResync(() => this.resync());
            // 對方收回（或自己在別的裝置收回）：開著的那一列換掉，列表預覽跟著更新
            MessagesWebSocket.onRecalled((messageId, conversationId) => {
                if (String(conversationId) === String(this.currentConversationId)) {
                    markMessageRowRecalled(
                        document.getElementById('social-messages-container'),
                        messageId,
                        (msg) => this.renderMessageBubble(msg)
                    );
                    handleMessageRecalled(SOCIAL_MSGS, messageId);
                }
                this.loadConversations();
            });
            // 表情變了（對方按的，或自己在別的裝置按的）
            MessagesWebSocket.onReactions((messageId, conversationId, reactions) => {
                if (String(conversationId) === String(this.currentConversationId)) {
                    updateMessageReactions(SOCIAL_MSGS, messageId, reactions);
                }
            });
            // 對方讀了：已送達 → 已讀（僅 Pro）
            MessagesWebSocket.onReadReceipt((conversationId) => {
                if (this.isPremium && String(conversationId) === String(this.currentConversationId)) {
                    markRowsRead(document.getElementById('social-messages-container'));
                }
            });
            // 通知中心：正看著的對話不跳 toast
            MessagesWebSocket.isViewing = (conversationId) =>
                this._isChatVisible() && String(conversationId) === String(this.currentConversationId);
            // 群組：group_* 事件（social-groups.js 分派）；正看著的群組不跳 toast
            MessagesWebSocket.onGroupEvent((data) => this.onGroupEvent(data));
            MessagesWebSocket.isViewingGroup = (groupId) =>
                this._isChatVisible() && Number(groupId) === this.currentGroupId;
        }
        MessagesWebSocket.connect();
    },

    /** 私訊的長按選單設定（群組的在 social-groups.js groupActionsConfig） */
    dmActionsConfig: function () {
        return {
            features: new Set(['react', 'reply', 'copy', 'report', 'recall', 'hide', ...(assistantEnabled() ? ['askAi'] : [])]),
            currentUserId: () => FriendsAPI._getUserId(),
            nameOf: () => this.currentChatUsername,
            composer: () => document.getElementById('social-msg-form'),
            // 點引用而原訊息還沒載入：往前翻（沒有更多回 false）；途中換對話就停
            currentConversation: () => this.currentConversationId,
            loadOlder: () => this.loadMoreMessages(),
            handlers: {
                reply: (info) => startReply(SOCIAL_MSGS, info),
                copy: ({ text }) => copyMessageText(text),
                // 順便封鎖了：對話從列表消失（LINE 模式），聊天區一起關掉
                report: (info) =>
                    reportDmMessage(info, {
                        onBlocked: () => {
                            this.closeChat();
                            this.loadConversations();
                            window.NavBadges?.schedule(); // 這段對話的未讀不再算進側欄數字
                        },
                    }),
                recall: ({ id }) => this.recallMessage(id),
                hide: ({ id }) => this.hideMessage(id),
                // text＝這則訊息的原文：選單「問 AI」要把它帶進輸入框（觸控裝置的氣泡不能選字，長按選單是唯一入口）
                askAi: ({ id, text }) => this.openAssistant(id, text),
            },
        };
    },

    /** ✨ AI 助理（chat-assistant.js）：開關關著 status 404 → 按鈕與選單「問 AI」都不出現 */
    initAssistant: function () {
        assistantStatus().then((status) => {
            document.getElementById('social-ai-btn')?.classList.toggle('hidden', !status);
            // 選單的「問 AI」看 assistantEnabled()：status 回來後重新登記一次
            if (status) registerMessageActions(SOCIAL_MSGS, this.currentGroupId ? this.groupActionsConfig() : this.dmActionsConfig());
        });
    },

    /**
     * 在訊息裡選一句話 → 浮出「問 AI」→ 開抽屜、那句話帶進輸入框（selection-ask.js）。
     * 只在聊天室 AI 助理開著時才出現；同一個容器重複 attach 只是更新設定（init 每次切進好友頁都會跑）。
     */
    initSelectionAsk: function () {
        const root = document.getElementById('social-messages-container');
        if (!root) return;
        attachSelectionAsk({
            root,
            eligible: '.msg-bubble',
            label: () => t('assistant.askSelection'),
            enabled: () => assistantEnabled(),
            onAsk: ({ text, target }) => {
                const row = target.closest('[data-message-id]');
                this.openAssistant(row ? row.dataset.messageId : null, text);
            },
        });
    },

    /** 標題列 ✨ 或選單「問 AI」（aroundId＝那則訊息；quote＝選取的句子，帶進輸入框） */
    openAssistant: function (aroundId, quote) {
        const around = Number(aroundId) || null;
        if (this.currentGroupId) {
            openAssistantDrawer({
                kind: 'group',
                targetId: this.currentGroupId,
                title: this.currentGroup?.name || '',
                unread: this._assistantUnread || null,
                aroundId: around,
                quote,
                // 分享成卡片後：跟一般送出一樣把新訊息放進畫面、更新列表（WebSocket 推來的同一則會用 id 去重）
                onShared: (data) => {
                    if (data?.message) this.appendGroupRow(data.message);
                    this.loadConversations().catch(() => {});
                },
            });
        } else if (this.currentConversationId) {
            openAssistantDrawer({
                kind: 'dm',
                targetId: this.currentConversationId,
                title: this.currentChatUsername || '',
                unread: this._assistantUnread || null,
                aroundId: around,
                userId: this.currentChatUserId,
                quote,
                onShared: (data) => {
                    if (data?.message) this.appendMessageRow(data.message);
                    if (data?.message_limit) this.renderQuota(data.message_limit);
                    this.loadConversations().catch(() => {});
                },
            });
        }
    },

    /** 聊天畫面此刻真的在使用者眼前（好友頁是目前分頁、瀏覽器在前景） */
    _isChatVisible: function () {
        const container = document.getElementById('social-messages-container');
        return !!container && container.offsetParent !== null && document.visibilityState === 'visible';
    },

    onRealtimeMessage: function (message, isSent) {
        if (!isSent) setListTyping(document.getElementById('social-conv-list'), message.conversation_id, false);
        const container = document.getElementById('social-messages-container');
        if (container && String(message.conversation_id) === String(this.currentConversationId)) {
            if (!isSent) hideTypingIndicator(container);
            this.appendMessageRow(message);
            if (!isSent) this.markCurrentAsRead();
        }
        // 列表的預覽、未讀數、排序跟著更新（連續來訊合併成一次）
        clearTimeout(this._convReloadTimer);
        this._convReloadTimer = setTimeout(() => this.loadConversations(), 300);
    },

    /** 標記目前對話已讀：畫面真的在眼前才算；連續來訊合併成一次請求 */
    markCurrentAsRead: function () {
        if (this.currentGroupId) return this.markGroupRead();
        const convId = this.currentConversationId;
        if (!convId || !this._isChatVisible()) return;
        clearTimeout(this._markReadTimer);
        this._markReadTimer = setTimeout(() => {
            const myId = FriendsAPI._getUserId();
            if (!myId) return;
            AppAPI.post(`/api/messages/read?user_id=${myId}`, { conversation_id: convId })
                .then(() => {
                    this.loadConversations();
                    window.NavBadges?.schedule(); // 側欄「社群」徽章跟著少
                })
                .catch(() => console.debug('SocialHub.markCurrentAsRead failed'));
        }, 800);
    },

    /** 重連／回到前景後補抓 */
    resync: async function () {
        this.loadConversations();
        if (this.currentGroupId) return this.refreshGroupAfterResume();
        const convId = this.currentConversationId;
        const myId = FriendsAPI._getUserId();
        if (!convId || !myId) return;
        try {
            const data = await AppAPI.get(`/api/messages/conversation/${convId}?user_id=${myId}&limit=50`);
            if (String(convId) !== String(this.currentConversationId)) return;
            (data.messages || []).forEach((msg) => this.appendMessageRow(msg));
            this.markCurrentAsRead();
        } catch (e) {
            console.warn('[SocialHub] resync failed:', e);
        }
    },

    /**
     * 把一則放進目前對話。同一則可能從 API 回應、WS、補抓三條路亂序進來，依 id 放到
     * 正確位置（去重在 insertMessageRow）；往上翻舊訊息時不把人拉回底部（自己送的例外），
     * 改浮「↓ 新訊息」
     */
    appendMessageRow: function (msg) {
        const container = document.getElementById('social-messages-container');
        if (!container) return;

        const emptyState = container.querySelector('.flex.flex-col.items-center');
        if (emptyState) container.innerHTML = '';

        const isMine = msg.from_user_id === FriendsAPI._getUserId();
        const nearBottom = isNearBottom(container);
        if (!insertMessageRow(container, msg.id, this.renderMessageBubble(msg))) return;
        regroupMessageRows(container);
        if (isMine || nearBottom) container.scrollTop = container.scrollHeight;
        else showNewMessagePill(container);
        AppUtils.refreshIcons();
    },

    /** 輸入框每次輸入（click-delegator 的 socialAutoResize 呼叫） */
    onComposerInput: function (el) {
        this.autoResizeInput(el);
        this.updateCharCount();
        if (this.currentGroupId) return this.onGroupComposerInput(el);
        if (typeof MessagesWebSocket === 'undefined') return;
        if (el.value.trim()) MessagesWebSocket.sendTyping(this.currentConversationId);
        else MessagesWebSocket.sendTypingStop(this.currentConversationId);
    },

    /**
     * 渲染完整佈局 (從 Components 獲取模板)
     */
    render: async function () {
        if (!Components.isInjected('friends')) {
            await Components.inject('friends');
        }
        this.applyConvListCollapsed();
        this.bindSearch(); // 搜尋框（social-search.js；模板注入一次就綁一次）

        // 確保 Lucide 圖標渲染
        AppUtils.refreshIcons();

        // 根據 activeSubTab 顯示正確的內容（只切畫面：資料由下面載一次就好，
        // 以前 switchSubTab 也會載，一開好友頁就抓兩次——2026-09-26 量測）
        this.switchSubTab(this.activeSubTab, { skipLoad: true });

        // 載入初始數據
        if (this.activeSubTab === 'friends') {
            loadFriendsTabData();
        } else {
            this.loadConversations();
            loadRequestBadge();
        }
    },

    /**
     * 切換子分頁 (聊天 / 好友)
     */
    switchSubTab: function (tabName, opts) {
        this.activeSubTab = tabName;

        // Update Buttons
        document.querySelectorAll('.social-sub-tab').forEach((btn) => {
            btn.classList.remove('bg-primary', 'text-background');
            btn.classList.add('text-textMuted', 'hover:text-textMain', 'hover:bg-surfaceHighlight');
        });
        const activeBtn = document.getElementById(`social-tab-${tabName}`);
        if (activeBtn) {
            activeBtn.classList.remove('text-textMuted', 'hover:text-textMain', 'hover:bg-surfaceHighlight');
            activeBtn.classList.add('bg-primary', 'text-background');
        }

        // Show/Hide Content
        document.getElementById('social-content-messages').classList.add('hidden');
        document.getElementById('social-content-friends').classList.add('hidden');
        // document.getElementById('social-content-blocked').classList.add('hidden'); // Tab removed

        const contentEl = document.getElementById(`social-content-${tabName}`);
        if (contentEl) contentEl.classList.remove('hidden');

        if (opts && opts.skipLoad) return;

        // If switching to friends load data if needed
        if (tabName === 'friends') {
            loadFriendsTabData();
        } else {
            // If switching to messages, ensure content is loaded
            if (document.getElementById('social-conv-list').innerHTML.includes('animate-spin')) {
                this.loadConversations();
            }
        }
    },

    /**
     * 加載對話列表：抓資料（私訊照目前載到的數量、群組、邀請），畫面交給 renderConvList。
     * 來訊、已讀、收回都會叫這支；同時好幾次在路上時只畫最後發出的那次
     */
    loadConversations: async function () {
        const listEl = document.getElementById('social-conv-list');
        if (!listEl) return;

        const seq = ++this._convSeq;
        try {
            const myId = FriendsAPI._getUserId();
            if (!myId) {
                console.error('[SocialHub] User ID not found (not logged in)');
                listEl.innerHTML = renderEmptyState(t('friends.loginRequired'));
                return;
            }

            this.bindInviteList(listEl); // 邀請列的接受／拒絕（social-groups.js；只綁一次）
            this.bindConvListPins(listEl); // 置頂：長按／右鍵選單、拖拉排序（social-pins.js；只綁一次）
            // 群組邀請等群組那支回來才抓：開關關著（線上預設）就不多打一支 404
            const groupsReady = this.fetchGroups(); // 開關關著回 []
            const limit = this.convLimit;
            const [res, groups] = await Promise.all([
                fetchConversationPages(myId, limit, this.convSort),
                groupsReady,
                groupsReady.then(() => this.invitesForList()), // 待回覆的群組邀請（放最上面）
            ]);
            if (seq !== this._convSeq) return;
            const conversations = res.conversations || [];
            const dmUnread = res.total_unread || 0;
            // 靜音的群不算進徽章，除非有人 @ 我（同功能選單的數字 core/nav_badges.py；列上的未讀數照常顯示）
            const groupUnread = groups
                .filter((g) => !g.muted || g.mentioned)
                .reduce((sum, g) => sum + Number(g.unread_count || 0), 0);
            this._convData = { conversations, groups, hasMore: conversations.length >= limit };
            updateBadge('messages-unread-badge', dmUnread + groupUnread, true);
            this.renderFilterBar(dmUnread, groupUnread);
            this.renderConvList();
        } catch (e) {
            if (seq !== this._convSeq) return;
            console.error('Failed to load conversations:', e);
            listEl.innerHTML = `<div class="p-4 text-center text-danger text-sm">${t('friends.loadFailed')}</div>`;
        }
    },

    // ── 列表分頁與分類 ───────────────────────────────────
    // 一次多 20 個私訊；重畫（來訊、已讀）照目前載到的數量抓，捲到底再多 20。群組不分頁（上限 20 個）
    convLimit: CONV_PAGE,
    _convSeq: 0,
    _convData: null,
    _convLoadingMore: false,
    convFilter: readConvFilter(),
    convSort: readConvSort(),

    /** 用手上的資料畫列表（換分類不用重抓） */
    renderConvList: function () {
        const listEl = document.getElementById('social-conv-list');
        const data = this._convData;
        if (!listEl || !data) return;
        // 搜尋中：列表位置放的是搜尋結果，資料照樣更新、清掉搜尋時再畫（social-search.js）
        if (this.searchQuery) return;
        // 正在拖拉排序：先別重畫（手上那列會被換掉），放手存完順序再畫（social-pins.js saveConvOrder）
        if (listEl.classList.contains('reorder-active')) {
            this._convRenderPending = true;
            return;
        }
        this._convRenderPending = false;
        // 只有私訊（群組開關關著）就沒有分類可選
        const filter = this.groupsEnabled ? this.convFilter : 'all';
        const { conversations, groups, hasMore } = data;
        const dmMore = hasMore && filter !== 'group';
        const pinOf = (x) => (x.pin_position == null ? null : Number(x.pin_position));
        const ordOf = (x) => (x.order_position == null ? null : Number(x.order_position));
        // 一般對話的順序：依最新訊息＝新的在上；自訂順序＝還沒排過的（新對話）在上、照時間，其他照自己排的位置
        const custom = this.convSort === 'custom';
        const cmp = (a, b) => {
            if (!custom || (a.ord === null && b.ord === null)) return b.at - a.at;
            if (a.ord === null) return -1;
            if (b.ord === null) return 1;
            return a.ord - b.ord || b.at - a.at;
        };
        // 還有更多私訊沒載：排在已載最後一個私訊後面的群組先不放，不然往下捲時群組會插隊到中間
        const lastConv = [...conversations].reverse().find((c) => c.pin_position == null);
        const last = hasMore && lastConv ? { at: itemTime(lastConv), ord: ordOf(lastConv) } : null;
        const all = [
            ...(filter === 'group' ? [] : conversations).map((conv) => ({
                kind: 'dm',
                raw: conv,
                key: pinKey('dm', conv.id),
                pin: pinOf(conv),
                ord: ordOf(conv),
                at: itemTime(conv),
                html: () => this.renderConversationItem(conv),
            })),
            ...(filter === 'dm' ? [] : groups).map((group) => ({
                kind: 'group',
                raw: group,
                key: pinKey('group', group.id),
                pin: pinOf(group),
                ord: ordOf(group),
                at: itemTime(group),
                html: () => this.renderGroupItem(group),
            })),
        ];
        // 置頂的照自己排的順序放最上面（後端讓置頂私訊一定在第一頁）；其他私訊與群組混排（LINE 式），新的在上
        const pinned = all.filter((x) => x.pin !== null).sort((a, b) => a.pin - b.pin);
        const items = all
            .filter((x) => x.pin === null)
            .filter((x) => x.kind === 'dm' || filter === 'group' || !last || cmp(x, last) <= 0)
            .sort(cmp);
        if (this.convReorder && pinned.length < 2 && items.length < 2) this.convReorder = false;

        const scrollTop = listEl.scrollTop; // 重畫不跳回頂端（捲到一半來訊也一樣）
        if (this.convReorder) {
            // 排序模式：置頂、其他對話各一區，都有把手；邀請、載入更多先收起來（social-pins.js）
            listEl.innerHTML = this.reorderModeHtml(pinned, items);
            listEl.scrollTop = scrollTop;
            this.watchConvMore(listEl);
            AppUtils.refreshIcons();
            return;
        }
        const invitesHtml = filter === 'dm' ? '' : this.invitesHtml(); // 群組邀請不放在「私訊」分類
        if (items.length === 0 && pinned.length === 0) {
            listEl.innerHTML = invitesHtml + this.convEmptyHtml(filter, !!invitesHtml);
        } else {
            const more = dmMore
                ? '<div data-conv-more class="py-4 flex justify-center"><div class="animate-spin w-5 h-5 border-2 border-primary border-t-transparent rounded-full"></div></div>'
                : '';
            listEl.innerHTML =
                invitesHtml + this.pinnedSectionHtml(pinned) + items.map((item) => item.html()).join('') + more;
            applyListTyping(listEl); // 重畫後把「輸入中…」補回去
        }
        listEl.scrollTop = scrollTop;
        this.watchConvMore(listEl);
        AppUtils.refreshIcons();
    },

    convEmptyHtml: function (filter, compact) {
        const box = `${compact ? 'py-10' : 'h-full'} flex flex-col items-center justify-center text-textMuted opacity-60 p-4 text-center`;
        if (filter === 'group') {
            return `<div class="${box}">
                        <i data-lucide="users" class="w-8 h-8 mb-2"></i>
                        <p class="text-sm">${t('friends.filter.noGroups')}</p>
                        <button data-click="SocialHub.openCreateGroup" class="mt-4 text-primary text-xs hover:underline">${t('groups.create')}</button>
                    </div>`;
        }
        return `<div class="${box}">
                        <i data-lucide="message-square-off" class="w-8 h-8 mb-2"></i>
                        <p class="text-sm">${t('messages.noConversations')}</p>
                        <button data-click="socialSwitchSubTab" data-click-arg="friends" class="mt-4 text-primary text-xs hover:underline">
                            ${t('messages.goFindFriends')}
                        </button>
                    </div>`;
    },

    /** 底部的「載入中」露出來就多抓一頁（列表短到不能捲時也會觸發） */
    watchConvMore: function (listEl) {
        this._convMoreObserver?.disconnect();
        const sentinel = listEl.querySelector('[data-conv-more]');
        if (!sentinel || typeof IntersectionObserver === 'undefined') return;
        this._convMoreObserver = new IntersectionObserver(
            (entries) => {
                if (entries.some((e) => e.isIntersecting)) this.loadMoreConversations();
            },
            { root: listEl, rootMargin: '200px 0px' }
        );
        this._convMoreObserver.observe(sentinel);
    },

    loadMoreConversations: async function () {
        if (this._convLoadingMore || !this._convData?.hasMore) return;
        this._convLoadingMore = true;
        this.convLimit += CONV_PAGE;
        try {
            await this.loadConversations();
        } finally {
            this._convLoadingMore = false;
        }
    },

    /** 分類：全部／私訊／群組（記在 localStorage） */
    setConvFilter: function (filter) {
        if (!CONV_FILTERS.includes(filter) || filter === this.convFilter) return;
        this.convFilter = filter;
        try {
            localStorage.setItem(CONV_FILTER_KEY, filter);
        } catch (_e) {
            // 隱私模式存不了：這次照樣切換
        }
        this.renderFilterBar();
        const listEl = document.getElementById('social-conv-list');
        if (listEl) listEl.scrollTop = 0;
        this.renderConvList();
    },

    /** 排序方式：依最新訊息／自訂順序（記在 localStorage；換了要重抓——後端照它分頁） */
    setConvSort: function (sort, opts = {}) {
        if (!CONV_SORTS.includes(sort) || sort === this.convSort) return;
        this.convSort = sort;
        try {
            localStorage.setItem(CONV_SORT_KEY, sort);
        } catch (_e) {
            // 隱私模式存不了：這次照樣切換
        }
        document.getElementById('social-reorder-btn')?.classList.toggle('text-primary', sort === 'custom');
        if (opts.reload !== false) {
            const listEl = document.getElementById('social-conv-list');
            if (listEl && !this.convReorder) listEl.scrollTop = 0;
            this.loadConversations();
        }
    },

    /** 分類鈕的選中樣式＋私訊／群組各自的未讀數（不給數字就沿用上次的） */
    renderFilterBar: function (dmUnread, groupUnread) {
        if (dmUnread !== undefined) this._filterUnread = { dm: dmUnread, group: groupUnread };
        const unread = this._filterUnread || { dm: 0, group: 0 };
        document.querySelectorAll('[data-conv-filter]').forEach((btn) => {
            const key = btn.dataset.convFilter;
            const active = key === this.convFilter;
            btn.setAttribute('aria-selected', String(active));
            // 分段控制：選中那格白底浮起來（tab-friends.js 模板）
            ['bg-surface', 'text-textMain', 'shadow-sm', 'font-semibold'].forEach((c) => btn.classList.toggle(c, active));
            ['text-textMuted', 'hover:text-textMain', 'font-medium'].forEach((c) => btn.classList.toggle(c, !active));
            const badge = btn.querySelector('[data-filter-unread]');
            if (!badge) return;
            const n = Number(unread[key] || 0);
            badge.textContent = n > 99 ? '99+' : String(n);
            badge.classList.toggle('hidden', n <= 0);
        });
    },

    renderConversationItem: function (conv) {
        // currentChatUserId 是 data-user-id 原值（encodeURIComponent 過）
        const isActive = !!this.currentChatUserId && encodeURIComponent(conv.other_user_id) === this.currentChatUserId;
        const activeClass = isActive
            ? 'bg-surfaceHighlight border-l-2 border-primary'
            : 'hover:bg-surfaceHighlight border-l-2 border-transparent';
        const unreadBadge =
            conv.unread_count > 0
                ? `<span class="w-5 h-5 rounded-full bg-primary text-background text-[10px] font-bold flex items-center justify-center">${conv.unread_count}</span>`
                : '';

        // 暱稱優先（other_display_name），沒設才用帳號名
        const username = conv.other_display_name || conv.other_username || conv.username || 'Unknown';
        const lastMessage = recalledPreviewText(conv) || conv.last_message || t('friends.clickToChat');

        const pinned = conv.pin_position != null;
        const key = pinKey('dm', conv.id);
        return `
            <div id="conv-${conv.id}" data-conversation-id="${conv.id}" data-pin-key="${key}" data-pinned="${pinned ? 1 : 0}" class="relative group">
                <div data-click="socialOpenConversation"
                     data-user-id="${encodeURIComponent(conv.other_user_id)}"
                     data-username="${SecurityUtils.escapeHTML(username)}"
                     class="p-4 border-b border-borderSubtle cursor-pointer transition ${activeClass}">
                    <div class="flex items-center gap-3">
                        <div class="w-10 h-10 rounded-full ${avatarColorClass(conv.other_user_id)} flex items-center justify-center font-bold flex-shrink-0 relative">
                            ${SecurityUtils.escapeHTML(avatarInitial(username))}
                            ${conv.unread_count > 0 ? '<span class="absolute top-0 right-0 w-2.5 h-2.5 bg-primary rounded-full border-2 border-surface"></span>' : ''}
                        </div>
                        <div class="flex-1 min-w-0">
                            <div class="flex justify-between items-baseline mb-0.5">
                                <h4 class="flex items-center gap-1 min-w-0 font-bold text-sm text-textMain pr-2"><span class="truncate">${SecurityUtils.escapeHTML(username)}</span>${pinned ? pinIndicatorHtml() : ''}</h4>
                                <span class="text-[10px] text-textMuted flex-shrink-0">${FriendsUI.formatTime(conv.last_message_at)}</span>
                            </div>
                            <div class="flex justify-between items-center">
                                <p class="conv-preview text-xs text-textMuted truncate pr-2 opacity-80">${SecurityUtils.escapeHTML(lastMessage)}</p>
                                ${unreadBadge}
                            </div>
                        </div>
                    </div>
                </div>
                ${pinHoverButtonHtml(key, pinned, 'right-10')}
                <!-- 刪除對話按鈕（hover 時顯示，右下位置避免與時間重疊） -->
                <button data-click="socialDeleteConversation" data-conv-id="${conv.id}"
                        class="absolute right-3 bottom-3 p-1.5 text-textMuted/40 hover:text-danger opacity-0 group-hover:opacity-100 transition-all duration-200"
                        title="${t('friends.deleteConversation')}">
                    <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
                </button>
            </div>
        `;
    },

    /**
     * 開啟對話 - 桌面端內嵌，移動端跳轉
     */
    currentChatUserId: null,
    currentChatUsername: null,
    // 往前翻舊訊息：游標＝畫面上最舊那則的 id；
    // pagingSeq 每換一次對話加一，還在路上的舊對話回應就認得出過期
    hasMoreMessages: false,
    oldestMessageId: null,
    isLoadingMore: false,
    pagingSeq: 0,
    _loadMorePromise: null,

    openConversation: function (userId, username) {
        // 內嵌顯示（所有寬度；一次一欄時切到聊天欄）。換對話時上一段的回覆草稿作廢
        if (userId !== this.currentChatUserId) clearReply(SOCIAL_MSGS);
        closeAssistantDrawer();
        // 標已讀之前記下未讀數（AI 助理「我沒看的」；userId 是 data-user-id 原值，encode 過）
        const conv = (this._convData?.conversations || []).find((c) => encodeURIComponent(c.other_user_id) === userId);
        const unread = Number(conv?.unread_count) || 0;
        this._assistantUnread = unread > 0 ? { unread_count: unread } : null;
        this.leaveGroupView(); // 從群組切過來：群組狀態、標題列、選單還原
        this.currentChatUserId = userId;
        this.currentChatUsername = username;
        this.showChatContent(userId, username);
        // 一次一欄時（1024 以下）切到聊天；並排時兩欄都在（styles.css）
        this.setPane('chat');
        this.loadMessages(userId);
        this.loadQuota();
    },

    /**
     * 一次一欄（1024 以下）時切到聊天欄或清單。進到聊天時順便收起上面的「社群」標題與訊息／好友分頁列
     * （max-lg:hidden，寬螢幕不受影響）：私訊聊天室統一進好友頁後，手機上聊天要跟以前獨立的全螢幕私訊頁一樣佔滿，
     * 不能被標題列吃掉一截。回清單時再放回來，所以切分頁一定看得到。
     */
    setPane: function (pane) {
        const panes = document.getElementById('social-content-messages');
        if (panes) panes.dataset.pane = pane;
        document.querySelectorAll('[data-social-top]').forEach((el) => el.classList.toggle('max-lg:hidden', pane === 'chat'));
    },

    /** 聊天欄上方的「返回對話列表」（一次一欄時才看得到） */
    backToList: function () {
        this.closeChat();
    },

    /** 並排時收合對話清單、放大聊天區（只在聊天中收，見 styles.css）；狀態記在 localStorage */
    toggleConvList: function () {
        const panes = document.getElementById('social-content-messages');
        if (!panes) return;
        const collapsed = panes.dataset.listCollapsed !== '1';
        panes.dataset.listCollapsed = collapsed ? '1' : '0';
        try {
            localStorage.setItem('dmListCollapsed', collapsed ? '1' : '0');
        } catch (_e) {
            // 隱私模式存不了：這次照樣切換，下次進來恢復展開
        }
    },

    /** 進好友頁時套回上次的收合狀態 */
    applyConvListCollapsed: function () {
        const panes = document.getElementById('social-content-messages');
        if (!panes) return;
        let collapsed = false;
        try {
            collapsed = localStorage.getItem('dmListCollapsed') === '1';
        } catch (_e) {
            // 讀不到就維持展開
        }
        panes.dataset.listCollapsed = collapsed ? '1' : '0';
    },

    /** 今日私訊額度（一般會員 20 則／天，Pro 無限不顯示） */
    loadQuota: async function () {
        // loadMessages 渲染前會等這個（要先知道是不是 Pro 才決定顯不顯示已讀）
        this._limitsPromise = AppAPI.get('/api/messages/limits').catch(() => null);
        const limits = await this._limitsPromise;
        // 拿不到就不顯示，送出時後端照樣會擋
        if (!limits) return;
        this.isPremium = !!limits.is_premium;
        this.renderQuota(limits.message_limit);
    },

    renderQuota: function (messageLimit) {
        if (this.currentGroupId) {
            // 群組沒有每日則數（唯讀與否看 Pro，social-groups.js _setReadOnly）
            document.getElementById('social-quota')?.classList.add('hidden');
            this.quotaLeft = null;
            this.updateCharCount();
            return;
        }
        this.quotaLeft = renderQuotaHint(document.getElementById('social-quota'), messageLimit, this.isPremium);
        this.updateCharCount();
    },

    /**
     * 顯示聊天內容區域
     */
    showChatContent: function (userId, username) {
        // 隱藏空狀態，顯示聊天內容
        const emptyState = document.getElementById('social-chat-empty');
        const chatContent = document.getElementById('social-chat-content');
        if (emptyState) emptyState.classList.add('hidden');
        if (chatContent) chatContent.classList.remove('hidden');

        // 更新頭部
        const avatar = document.getElementById('social-chat-avatar');
        const usernameEl = document.getElementById('social-chat-username');
        const profileLink = document.getElementById('social-chat-profile-link');

        applyAvatar(avatar, userId, username);
        if (usernameEl) usernameEl.textContent = username || userId;
        if (profileLink) profileLink.href = `/static/forum/profile.html?id=${userId}`;

        // 更新對話列表的選中狀態
        document.querySelectorAll('#social-conv-list > div').forEach((item) => {
            item.classList.remove('bg-primary/10', 'border-l-2', 'border-primary');
        });

        AppUtils.refreshIcons();
    },

    /**
     * 載入訊息
     */
    loadMessages: async function (userId) {
        const container = document.getElementById('social-messages-container');
        if (!container) return;

        // Check MessagesAPI availability
        if (typeof MessagesAPI === 'undefined') {
            console.debug('SocialHub.loadMessages: MessagesAPI not available');
        }

        container.innerHTML = `<div class="flex justify-center py-8"><i data-lucide="loader-2" class="w-6 h-6 animate-spin text-primary"></i></div>`;
        AppUtils.refreshIcons();
        // 換對話：分頁狀態作廢；快速連點兩個對話時，先點的那個較晚回來也不能蓋掉畫面
        const seq = ++this.pagingSeq;
        this.hasMoreMessages = false;
        this.oldestMessageId = null;
        this.isLoadingMore = false;
        this._loadMorePromise = null;

        try {
            const myId = FriendsAPI._getUserId();
            if (!myId) return;

            const data = await AppAPI.get(`/api/messages/with/${userId}?user_id=${myId}&limit=50`);
            // 額度查詢（openConversation 同時發出）帶是不是 Pro：回來才知道要不要畫已讀
            const limits = await this._limitsPromise;
            if (limits) this.isPremium = !!limits.is_premium;
            if (seq !== this.pagingSeq) return;

            if (!data.success) throw new Error(data.error || (t('friends.loadFailed')));
            this.hasMoreMessages = !!data.has_more;
            this.oldestMessageId = data.messages?.[0]?.id ?? null;
            this.setupOlderScroll(container);

            // Set current conversation ID for deletion checks
            if (data.conversation?.id) {
                this.currentConversationId = data.conversation.id;
            }

            if (!data.messages || data.messages.length === 0) {
                container.innerHTML = `
                    <div class="flex flex-col items-center justify-center h-full text-textMuted opacity-50">
                        <i data-lucide="message-circle" class="w-10 h-10 mb-3"></i>
                        <p class="text-sm">${t('friends.noMessagesYet')}</p>
                    </div>
                `;
            } else {
                container.innerHTML = data.messages
                    .map((msg) => this.renderMessageBubble(msg))
                    .join('');
                regroupMessageRows(container);
                container.scrollTop = container.scrollHeight;
            }
            AppUtils.refreshIcons();
            this.runPendingJump(`dm:${userId}`); // 從搜尋結果點進來：跳到那則（social-search.js）

            // 標記已讀（後台執行）
            if (data.conversation?.id) {
                AppAPI.post(`/api/messages/read?user_id=${myId}`, { conversation_id: data.conversation.id })
                    .then(() => {
                        this.loadConversations();
                        window.NavBadges?.schedule(); // 側欄「社群」徽章跟著少
                    })
                    .catch(() => {
                        console.debug('SocialHub.loadMessages: mark as read failed');
                    });
            }
        } catch (e) {
            console.error(window.I18n.t('friends.loadMessagesFailed') || '載入訊息失敗:', e);
                container.innerHTML = `<div class="text-center text-danger py-4 text-sm">${SecurityUtils.escapeHTML(userFacingMessage(e, { fallbackKey: 'friends.loadFailed' }))}</div>`;
        }

        AppUtils.refreshIcons();
    },

    /**
     * 渲染訊息氣泡
     */
    renderMessageBubble: function (msg) {
        // 列結構與 messages.js 的 MessagesUI.renderMessageBubble 相同（LINE 式，
        // 時間貼氣泡旁、對齊底部），間距／時間顯示／日期分隔由 regroupMessageRows 決定
        const isMe = msg.from_user_id === FriendsAPI._getUserId();
        const time = formatMessageClock(msg.created_at);
        // data-type／.msg-text 給選單（dm-message-actions.js）判斷項目與複製原文
        const rowOpen = `<div id="social-msg-${msg.id}" data-message-id="${msg.id}" data-from="${escapeMessageAttr(msg.from_user_id)}" data-ts="${escapeMessageAttr(msg.created_at)}" data-type="${escapeMessageAttr(msg.message_type || 'text')}" class="msg-row group flex items-end gap-1.5 ${isMe ? 'justify-end' : 'justify-start'}">`;
        // 已讀狀態（僅 Pro 可見），疊在時間上方
        const readStatus = isMe && this.isPremium && msg.message_type !== 'recalled' ? readStatusHtml(msg) : '';
        const meta = `<div class="msg-meta flex flex-col ${isMe ? 'items-end' : 'items-start'} shrink-0 text-[11px] leading-tight text-textMuted whitespace-nowrap">${readStatus}<span>${time}</span></div>`;
        const bubbleBase = 'msg-bubble relative max-w-[75%] min-w-0 px-3.5 py-2 rounded-2xl text-[15px] leading-relaxed';
        const recalled = msg.message_type === 'recalled';
        const tools = messageToolsHtml({ isMine: isMe, recalled });

        if (recalled) {
            const recalledText = isMe ? t('messages.recalledByMe') : t('messages.recalledByOther');
            const bubble = `<div class="${bubbleBase} bg-surfaceHighlight border border-borderLight"><span class="text-textMuted/60 text-sm italic">${recalledText}</span></div>`;
            return `${rowOpen}${isMe ? meta + tools + bubble : bubble + tools + meta}</div>`;
        }

        // AI 分析卡片（分享自聊天室 AI 助理）：整列另外畫，見 ai-card.js
        if (isAiCard(msg)) {
            return aiCardRowHtml({ rowOpen, msg, isMine: isMe, meta, tools, myId: FriendsAPI._getUserId() });
        }

        // 收回、為我刪除等動作都在選單裡（手機長按、桌機 hover「⋯」／右鍵）；hover 時工具列接在時間的位置（時間淡出）
        const content = `${quoteHtml(msg.reply_to, isMe)}<p class="msg-text whitespace-pre-wrap break-words">${renderMessageText(msg.content)}</p>${reactionsHtml(msg.reactions, FriendsAPI._getUserId(), isMe)}`;

        if (isMe) {
            return `${rowOpen}${meta}${tools}<div class="${bubbleBase} bg-primary text-background">${content}</div></div>`;
        }
        return `${rowOpen}<div class="${bubbleBase} bg-surfaceHighlight border border-borderSubtle text-textMain">${content}</div>${tools}${meta}</div>`;
    },

    /**
     * 收回訊息
     */
    recallMessage: async function (messageId, btnElement) {
        const recalled = await recallDmMessage(messageId, {
            container: document.getElementById('social-messages-container'),
            renderBubble: (msg) => this.renderMessageBubble(msg),
            btnElement,
        });
        if (recalled) this.loadConversations(); // 列表預覽
    },

    /**
     * 隱藏訊息（只對自己隱藏）
     */
    hideMessage: async function (messageId) {
        const hidden = await hideDmMessage(messageId, {
            container: document.getElementById('social-messages-container'),
        });
        if (hidden) this.loadConversations(); // 刪的可能是最後一則
    },

    /**
     * 往前翻一頁舊訊息，插在最前面並保持捲動位置。正在載入時回同一個 promise（點引用
     * 跳轉會 await 它），沒有更多回 false。途中換了對話：回應作廢、不動新對話的狀態。
     */
    loadMoreMessages: function (pageSize = 20) {
        if (this.currentGroupId) return this.loadMoreGroupMessages(pageSize);
        if (this._loadMorePromise) return this._loadMorePromise;
        const container = document.getElementById('social-messages-container');
        if (!container || !this.hasMoreMessages || !this.oldestMessageId || !this.currentConversationId) {
            return Promise.resolve(false);
        }
        const seq = this.pagingSeq;
        const convId = this.currentConversationId;
        const beforeId = this.oldestMessageId;
        this.isLoadingMore = true;
        const indicator = document.createElement('div');
        indicator.className = 'text-center py-2 text-textMuted text-sm';
        indicator.textContent = t('common.loading');
        container.insertBefore(indicator, container.firstChild);

        const run = async () => {
            try {
                const myId = FriendsAPI._getUserId();
                const data = await AppAPI.get(
                    `/api/messages/conversation/${convId}?user_id=${myId}&limit=${Number(pageSize) || 20}&before_id=${beforeId}`
                );
                indicator.remove(); // 插入前先移除，不然捲動位置會差一個提示的高度
                if (seq !== this.pagingSeq) return false;
                const messages = data.messages || [];
                if (messages.length === 0) {
                    this.hasMoreMessages = false;
                    return false;
                }
                // API 已是舊→新；先把畫面寫好，再推進游標（寫到一半出錯不會跳過一段）
                prependOlderRows(container, messages, (m) => this.renderMessageBubble(m));
                this.hasMoreMessages = !!data.has_more;
                this.oldestMessageId = messages[0].id;
                AppUtils.refreshIcons();
                return true;
            } catch (e) {
                console.warn('[SocialHub] load older messages failed:', e);
                return false;
            } finally {
                indicator.remove();
            }
        };
        const promise = run().finally(() => {
            // 過期（換過對話）的不動：loadMessages 已經把旗標重設給新對話了
            if (this._loadMorePromise === promise) {
                this._loadMorePromise = null;
                this.isLoadingMore = false;
            }
        });
        this._loadMorePromise = promise;
        return promise;
    },

    /**
     * 捲到接近頂端就往前翻。旗標記在容器元素上：換對話只換裡面的訊息、容器是同一個，
     * 不會重複綁；好友分頁整個重繪會換一個新容器，新容器沒有旗標就會再綁一次
     */
    setupOlderScroll: function (container) {
        if (!container || container.dataset.olderScrollBound) return;
        container.dataset.olderScrollBound = '1';
        container.addEventListener(
            'scroll',
            () => {
                if (container.scrollTop < 100 && this.hasMoreMessages && !this.isLoadingMore) {
                    this.loadMoreMessages();
                }
            },
            { passive: true }
        );
    },

    /**
     * 關掉目前的聊天區（刪除對話、檢舉時順便封鎖之後）：清空訊息，避免使用者以為還在
     */
    closeChat: function () {
        closeAssistantDrawer();
        this._assistantUnread = null;
        this.leaveGroupView();
        // 一次一欄時回到清單（刪除／封鎖後也是）
        this.setPane('list');
        document.getElementById('social-chat-content')?.classList.add('hidden');
        document.getElementById('social-chat-empty')?.classList.remove('hidden');
        const messagesContainer = document.getElementById('social-messages-container');
        if (messagesContainer) messagesContainer.innerHTML = '';
        clearReply(SOCIAL_MSGS);
        this.currentChatUserId = null;
        this.currentChatUsername = null;
        this.currentConversationId = null;
        this.pagingSeq += 1; // 還在路上的往前翻回應作廢
        this.hasMoreMessages = false;
        this.oldestMessageId = null;
        this.isLoadingMore = false;
        this._loadMorePromise = null;
    },

    /**
     * 刪除對話（隱藏整段對話）
     */
    deleteConversation: async function (conversationId, btnElement) {
        // 使用平台風格的確認對話框
        const confirmed =
            typeof showConfirm === 'function'
                ? await showConfirm({
                      title: t('friends.deleteConversation'),
                      message:
                          t('friends.deleteConversationMsg'),
                      type: 'warning',
                      confirmText: t('friends.deleteBtn'),
                      cancelText: t('common.cancel'),
                  })
                : confirm(
                      t('friends.deleteConversationMsg')
                  );

        if (!confirmed) return;

        if (btnElement) btnElement.disabled = true;

        try {
            const myId = FriendsAPI._getUserId();
            if (!myId) return;

            const data = await AppAPI.delete(`/api/conversations/${conversationId}?user_id=${myId}`);

            if (data.success) {
                window.NavBadges?.schedule(); // 這段對話的未讀已歸零：側欄「社群」數字跟著少
                // 從 DOM 中移除對話
                const convEl = document.getElementById(`conv-${conversationId}`);
                if (convEl) {
                    convEl.remove();
                }

                // 如果刪除的是當前打開的對話，清空聊天區域和訊息內容
                if (this.currentChatUserId) {
                    const currentConvId = this.currentConversationId;
                    if (currentConvId === conversationId) {
                        this.closeChat();

                        // Force refresh icons in empty state
                        AppUtils.refreshIcons();
                    }
                }

                // 檢查列表是否為空
                const listEl = document.getElementById('social-conv-list');
                if (listEl && listEl.children.length === 0) {
                    listEl.innerHTML = `
                        <div class="h-full flex flex-col items-center justify-center text-textMuted opacity-50 p-4 text-center">
                            <i data-lucide="message-square-off" class="w-8 h-8 mb-2"></i>
                            <p class="text-sm">${t('messages.noConversations')}</p>
                            <button data-click="socialSwitchSubTab" data-click-arg="friends" class="mt-4 text-primary text-xs hover:underline">
                                ${t('messages.goFindFriends')}
                            </button>
                        </div>
                    `;
                    AppUtils.refreshIcons();
                }

                if (typeof showToast === 'function') {
                    showToast(t('friends.conversationDeleted'), 'success');
                }
                this.loadConversations(); // 列表上方分頁的未讀徽章也重算
            } else {
                throw new Error(data.error || data.detail || t('friends.deleteFailed'));
            }
        } catch (e) {
            console.error(window.I18n.t('friends.deleteConversationFailed') || '刪除對話失敗:', e);
            const message = userFacingMessage(e, { fallbackKey: 'friends.deleteFailed' });
            if (typeof showToast === 'function') {
                showToast(message, 'error');
            } else {
                alert(message);
            }
            if (btnElement) btnElement.disabled = false;
        }
    },

    /**
     * HTML 轉義
     */
    escapeHtml: function (text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    /**
     * 發送訊息
     */
    sendMessage: async function (e) {
        if (this.currentGroupId) return this.sendGroupMessage(e);
        if (e) e.preventDefault();
        if (!this.currentChatUserId) return;

        const input = document.getElementById('social-msg-input');
        const content = input?.value?.trim();
        if (!content) return;
        // 按鈕 disabled 擋不到 Enter；回應回來清空輸入框前再按會用同一段文字再送一次
        if (this.isSending) return;
        this.isSending = true;

        const btn = document.getElementById('social-send-btn');
        if (btn) {
            btn.disabled = true;
            btn.classList.add('opacity-50');
        }

        try {
            const myId = FriendsAPI._getUserId();
            if (!myId) throw new Error(t('friends.loginRequired'));

            const body = { to_user_id: this.currentChatUserId, content };
            const replyTo = getReplyTarget(SOCIAL_MSGS);
            if (replyTo) body.reply_to_message_id = replyTo;
            const data = await AppAPI.post(`/api/messages/send?user_id=${myId}`, body);

            if (data.success) {
                clearReply(SOCIAL_MSGS);
                input.value = '';
                this.autoResizeInput(input);
                this.updateCharCount();

                if (typeof MessagesWebSocket !== 'undefined') {
                    MessagesWebSocket.sendTypingStop(this.currentConversationId);
                }
                if (data.message) this.appendMessageRow(data.message);
                if (data.message_limit) this.renderQuota(data.message_limit);

                // 異步更新對話列表（不等待，避免阻塞 UI）
                // 訊息已經立即顯示，列表在背景更新
                this.loadConversations().catch((err) => {
                    console.warn('Background conversation list update failed:', err);
                });
            } else {
                throw new Error(data.detail || (t('messages.sendFailed')));
            }
        } catch (err) {
            console.error(window.I18n.t('friends.sendFailed') || '發送失敗:', err);
            if (err.status === 429) this.loadQuota(); // 額度用完：輸入列下改成「已用完」
            const message = userFacingMessage(err, { fallbackKey: 'messages.sendFailed' });
            if (typeof showToast === 'function') {
                showToast(handleReplySendError(SOCIAL_MSGS, err) || message, 'error');
            } else {
                alert(message);
            }
        } finally {
            this.isSending = false;
            if (btn) {
                btn.disabled = false;
                btn.classList.remove('opacity-50');
            }
            this.updateCharCount();
            input?.focus();
        }
    },

    /**
     * 輸入框按鍵處理
     */
    handleInputKeydown: function (e) {
        // 輸入法選字的 Enter 交給輸入法（Safari 在 compositionend 後才發，只剩 keyCode 229 認得出來）
        if (e.isComposing || e.keyCode === 229) return;
        // 群組 @提及選單開著：Enter 是選人不是送出（social-groups.js）
        if (this.handleMentionKeydown?.(e)) return;
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            this.sendMessage(e);
        }
    },

    /**
     * 自動調整輸入框高度
     */
    autoResizeInput: function (textarea) {
        if (!textarea) return;
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
    },

    /**
     * 更新字數統計和按鈕狀態
     */
    updateCharCount: function () {
        const input = document.getElementById('social-msg-input');
        const countSpan = document.getElementById('social-char-count');
        const btn = document.getElementById('social-send-btn');

        if (!input) return;

        const len = input.value.length;
        const hasContent = input.value.trim().length > 0;

        if (countSpan) {
            countSpan.textContent = `${len}/500`;
            countSpan.classList.toggle('hidden', len < 400); // 快到上限才顯示
        }

        if (btn) {
            // 今日額度用完也鎖；群組 Pro 到期唯讀（_setReadOnly 鎖了輸入框）也不能被這裡打開
            btn.disabled = !hasContent || this.quotaLeft === 0 || !!input.disabled;
            if (hasContent) {
                btn.classList.remove('opacity-50');
            } else {
                btn.classList.add('opacity-50');
            }
        }
    },

    /** 重新整理；btn＝標題列的重整鈕（使用者按的）：資料回來前轉圈、鎖住，連按不重打 */
    refresh: async function (btn) {
        if (btn) {
            if (this._refreshing) return;
            this._refreshing = true;
            btn.disabled = true;
            btn.setAttribute('aria-busy', 'true');
            btn.querySelector('svg, i')?.classList.add('animate-spin');
        }
        const jobs = [];
        if (this.activeSubTab === 'friends') {
            jobs.push(loadFriendsTabData());
        } else {
            this._invitesStale = true; // 群組邀請也重抓
            jobs.push(this.loadConversations(), loadRequestBadge());
            // 如果有打開的對話，也刷新訊息
            if (this.currentGroupId) {
                jobs.push(this.loadGroup());
            } else if (this.currentChatUserId) {
                jobs.push(this.loadMessages(this.currentChatUserId));
            }
        }
        try {
            await Promise.allSettled(jobs);
        } finally {
            if (btn) {
                this._refreshing = false;
                btn.disabled = false;
                btn.removeAttribute('aria-busy');
                btn.querySelector('svg, i')?.classList.remove('animate-spin');
                btn.blur?.(); // 手機上按完別一直留著按下去的底色
            }
        }
    },
};

// 群組（social-groups.js）：方法裡的 this＝SocialHub
Object.assign(SocialHub, SocialGroups);
Object.assign(SocialHub, SocialPins); // 置頂與排序（social-pins.js）
Object.assign(SocialHub, SocialSearch); // 搜尋人、群組、訊息（social-search.js）

window.SocialHub = SocialHub;

export {
    FriendsAPI,
    FriendsUI,
    refreshFriendsUI,
    loadFriendsTabData,
    loadRequestBadge,
    handleFriendSearch,
    updateBadge,
    renderEmptyState,
    renderErrorState,
    SocialHub,
};
