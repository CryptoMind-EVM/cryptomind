/**
 * 好友頁（friends.js SocialHub）的群組部分。friends.js 已 1,800 多行，群組另放這裡、
 * Object.assign 進 SocialHub（方法裡的 this＝SocialHub）。設計 docs/plans/2026-10-01-group-chat-design.md。
 *
 * 狀態：currentGroupId／currentGroup（含 members，算「已讀 N」與誰讀過）／groupsEnabled。
 * 群組 id 跟私訊對話 id 是兩個數列：一律用 currentGroupId，不碰 currentConversationId
 * （私訊的 WS 事件用 conversation_id 比對，混用會撞號）。
 * 開關 group_chat_enabled 關著時 GET /api/groups 回 404 → 整個群組入口不顯示。
 */

import {
    clearReply,
    copyMessageText,
    getReplyTarget,
    handleMessageRecalled,
    handleReplySendError,
    registerMessageActions,
    reportDmMessage,
    startReply,
    updateMessageReactions,
} from './dm-message-actions.js';
import { assistantEnabled, closeAssistantDrawer } from './chat-assistant.js';
import { isProTier, openFriendPicker, openGroupInfoPanel, openNameDialog } from './group-chat-dialogs.js';
import { createMentionPicker } from './group-mention-picker.js';
import {
    bindGroupInviteActions,
    GroupChatAPI,
    groupErrorText,
    groupReadCount,
    groupReadStatusHtml,
    renderGroupBubble,
    renderGroupInvites,
    renderGroupListItem,
} from './group-chat.js';
import {
    MessagesAPI,
    confirmDmAction,
    hideTypingIndicator,
    insertMessageRow,
    isNearBottom,
    markMessageRowRecalled,
    prependOlderRows,
    recallDmMessage,
    regroupMessageRows,
    setListTyping,
    showNewMessagePill,
    showTypingIndicator,
} from './messages.js';

const SOCIAL_MSGS = '#social-messages-container';
const t = (key, args) => (window.I18n ? window.I18n.t(key, args) : key);
const myId = () => MessagesAPI._getUserId();
const container = () => document.getElementById('social-messages-container');

function toast(text, type = 'info') {
    if (typeof window.showToast === 'function') window.showToast(text, type);
}

const SocialGroups = {
    currentGroupId: null,
    currentGroup: null,
    groupsEnabled: null, // null＝還不知道；false＝開關關著（GET /api/groups 404）

    // ── 列表 ────────────────────────────────────────────

    /** 我的群組；開關關著（404）回 []，入口藏起來 */
    fetchGroups: async function () {
        if (this.groupsEnabled === false) return [];
        try {
            const res = await GroupChatAPI.list();
            this._setGroupsEnabled(true);
            return res.groups || [];
        } catch (e) {
            if (e?.status === 404) this._setGroupsEnabled(false);
            else console.warn('[SocialHub] load groups failed:', e);
            return [];
        }
    },

    // ── 待回覆的邀請（列表最上面）────────────────────────
    // 每次重畫列表都抓會多一支請求（來訊、已讀都會重畫），所以只在「可能變了」時抓：
    // 第一次、按重新整理、通知中心的群組邀請有增減（收到新的、在鈴鐺那邊處理掉）
    pendingInvites: [],
    _invitesStale: true,
    _inviteSig: null,

    loadInvites: async function () {
        if (this.groupsEnabled === false) {
            this.pendingInvites = [];
            return this.pendingInvites;
        }
        try {
            const res = await GroupChatAPI.invites();
            this.pendingInvites = res.invites || [];
            this._invitesStale = false;
        } catch (e) {
            if (e?.status === 404) this._setGroupsEnabled(false);
            else console.warn('[SocialHub] load group invites failed:', e);
        }
        return this.pendingInvites;
    },

    /** loadConversations 用：需要才重抓，不然用手上的 */
    invitesForList: function () {
        return this._invitesStale ? this.loadInvites() : Promise.resolve(this.pendingInvites);
    },

    invitesHtml: function () {
        return renderGroupInvites(this.pendingInvites);
    },

    /** 列表容器的接受／拒絕＋通知中心同步（init 每次都叫；兩個都只綁一次） */
    bindInviteList: function (listEl) {
        bindGroupInviteActions(listEl, ({ accepted, groupId }) => {
            this.pendingInvites = this.pendingInvites.filter((inv) => Number(inv.group_id) !== groupId);
            this._invitesStale = true;
            if (accepted && groupId) {
                toast(t('groups.joined'), 'success');
                this.openGroup(groupId);
            }
            this.loadConversations();
            window.NavBadges?.schedule(); // 邀請數字少了；通知若早已讀就不會有 push 補這一步
        });
        if (this._inviteWatchBound) return;
        this._inviteWatchBound = true;
        window.addEventListener('notificationsUpdated', (e) => {
            const unread = (e.detail?.notifications || []).filter((n) => !n.is_read);
            const sigOf = (pick) =>
                unread
                    .filter(pick)
                    .map((n) => n.id)
                    .sort()
                    .join(',');
            const inviteSig = sigOf((n) => n.type === 'group_invite');
            // 「有人提及你」：鈴鐺標已讀會改變靜音群算不算進數字（側欄走後端、已正確），
            // 列表的徽章與提及標記也要跟著重抓
            const mentionSig = sigOf((n) => n.type === 'group_message' && n.data?.mentioned);
            const invitesChanged = inviteSig !== this._inviteSig;
            if (!invitesChanged && mentionSig === this._mentionSig) return;
            this._inviteSig = inviteSig;
            this._mentionSig = mentionSig;
            if (invitesChanged) this._invitesStale = true;
            if (document.getElementById('social-conv-list')) this.loadConversations();
        });
    },

    _setGroupsEnabled: function (enabled) {
        this.groupsEnabled = enabled;
        document.getElementById('social-group-bar')?.classList.toggle('hidden', !enabled);
    },

    renderGroupItem: function (group) {
        return renderGroupListItem(group, {
            nameOf: (uid) => uid,
            formatTime: (ts) => (window.FriendsUI ? window.FriendsUI.formatTime(ts) : ''),
            active: Number(group.id) === Number(this.currentGroupId),
            pinControls: true,
        });
    },

    /** 深連結 /?group=<id>#friends（手機私訊頁、論壇頁的通知帶過來）：打開那個群、參數拿掉 */
    openGroupFromUrl: function () {
        const params = new URLSearchParams(window.location.search || '');
        const id = Number(params.get('group'));
        if (!id) return;
        params.delete('group');
        const qs = params.toString();
        history.replaceState(history.state, '', `${location.pathname}${qs ? `?${qs}` : ''}${location.hash || ''}`);
        if (this.activeSubTab && this.activeSubTab !== 'messages') this.switchSubTab?.('messages');
        this.openGroup(id);
    },

    // ── 開啟群組 ────────────────────────────────────────

    groupNameOf: function (userId) {
        const m = (this.currentGroup?.members || []).find((x) => x.user_id === userId);
        return m ? m.display_name || m.username || userId : userId;
    },

    renderGroupRow: function (msg) {
        return renderGroupBubble(msg, {
            myId: myId(),
            nameOf: (uid) => this.groupNameOf(uid),
            members: this.currentGroup?.members || [],
            idPrefix: 'social-msg-',
        });
    },

    /** 長按選單：群組沒有「為我刪除」；表情／檢舉／收回走群組網址；檢舉不給「同時封鎖」 */
    groupActionsConfig: function () {
        return {
            features: new Set(['react', 'reply', 'copy', 'readers', 'report', 'recall', ...(assistantEnabled() ? ['askAi'] : [])]),
            currentUserId: () => myId(),
            nameOf: (uid) => this.groupNameOf(uid),
            composer: () => document.getElementById('social-msg-form'),
            currentConversation: () => (this.currentGroupId ? `g${this.currentGroupId}` : null),
            loadOlder: () => this.loadMoreGroupMessages(),
            urls: { reaction: GroupChatAPI.reactionUrl },
            handlers: {
                reply: (info) => startReply(SOCIAL_MSGS, info),
                copy: ({ text }) => copyMessageText(text),
                report: (info) => reportDmMessage(info, { url: GroupChatAPI.reportUrl(info.id), allowBlock: false }),
                recall: ({ id }) => this.recallGroupMessage(id),
                readers: ({ id }) => this.showGroupReaders(id),
                askAi: ({ id, text }) => this.openAssistant(id, text),
            },
        };
    },

    /** 點列表的群組（data-click="SocialHub.openGroup" data-click-arg="<id>"） */
    openGroup: function (groupId) {
        const id = Number(groupId);
        if (!id) return;
        if (id !== this.currentGroupId) clearReply(SOCIAL_MSGS);
        closeAssistantDrawer();
        this._assistantUnread = undefined; // loadGroup 拿到 me.last_read_message_id 才記（標已讀之前）
        // 從私訊切過來：私訊狀態清掉（WS 比對、往前翻都看 currentConversationId）
        this.currentChatUserId = null;
        this.currentChatUsername = null;
        this.currentConversationId = null;
        this.currentGroupId = id;
        this.currentGroup = null;
        registerMessageActions(SOCIAL_MSGS, this.groupActionsConfig());
        this._ensureMentionPicker()?.close();

        document.getElementById('social-chat-empty')?.classList.add('hidden');
        document.getElementById('social-chat-content')?.classList.remove('hidden');
        this.setPane('chat');
        this._paintGroupHeader();
        this.loadQuota(); // 帶回 isPremium：Pro 到期的人唯讀
        this.loadGroup();
        this.loadConversations(); // 列表選中狀態
    },

    _paintGroupHeader: function () {
        const group = this.currentGroup;
        const avatar = document.getElementById('social-chat-avatar');
        if (avatar) {
            avatar.className =
                'w-9 h-9 rounded-full bg-surfaceHighlight text-textMuted flex items-center justify-center flex-shrink-0';
            avatar.innerHTML = '<i data-lucide="users" class="w-5 h-5"></i>';
        }
        const nameEl = document.getElementById('social-chat-username');
        if (nameEl) {
            nameEl.textContent = group
                ? `${group.name} (${(group.members || []).length})`
                : t('common.loading');
        }
        // 標題列整塊點了開群組資訊（私訊是連到對方個人頁）
        const link = document.getElementById('social-chat-profile-link');
        if (link) {
            link.removeAttribute('href');
            link.setAttribute('data-click', 'SocialHub.openGroupInfo');
            link.classList.add('cursor-pointer');
        }
        document.getElementById('social-group-info-btn')?.classList.remove('hidden');
        window.AppUtils?.refreshIcons();
    },

    /** 換回私訊（openConversation 呼叫）：群組狀態清掉、標題列與選單還原 */
    leaveGroupView: function () {
        if (!this.currentGroupId) return;
        this.currentGroupId = null;
        this.currentGroup = null;
        this._mentionPicker?.close();
        hideTypingIndicator(container());
        // 頭像換回私訊用的（applyAvatar 只換字與顏色 class，群組把整個 className 換掉了）
        const avatar = document.getElementById('social-chat-avatar');
        if (avatar) {
            avatar.className = 'w-9 h-9 rounded-full flex items-center justify-center font-bold flex-shrink-0';
            avatar.textContent = '';
        }
        const link = document.getElementById('social-chat-profile-link');
        link?.removeAttribute('data-click');
        document.getElementById('social-group-info-btn')?.classList.add('hidden');
        this._setReadOnly(false);
        registerMessageActions(SOCIAL_MSGS, this.dmActionsConfig());
    },

    /** Pro 到期：留在群裡唯讀（輸入列換成提示） */
    _setReadOnly: function (readOnly) {
        const input = document.getElementById('social-msg-input');
        if (input) input.disabled = readOnly;
        document.getElementById('social-group-readonly')?.classList.toggle('hidden', !readOnly);
        document.getElementById('social-send-btn')?.toggleAttribute('disabled', readOnly);
    },

    loadGroup: async function () {
        const el = container();
        const groupId = this.currentGroupId;
        if (!el || !groupId) return;
        el.innerHTML = `<div class="flex justify-center py-8"><i data-lucide="loader-2" class="w-6 h-6 animate-spin text-primary"></i></div>`;
        window.AppUtils?.refreshIcons();
        const seq = ++this.pagingSeq;
        this.hasMoreMessages = false;
        this.oldestMessageId = null;
        this.isLoadingMore = false;
        this._loadMorePromise = null;
        try {
            const [info, data] = await Promise.all([GroupChatAPI.get(groupId), GroupChatAPI.messages(groupId)]);
            const limits = await this._limitsPromise;
            if (limits) this.isPremium = !!limits.is_premium;
            if (seq !== this.pagingSeq || groupId !== this.currentGroupId) return;
            this.currentGroup = info.group;
            if (this._assistantUnread === undefined) {
                // AI 助理「我沒看的」：打開前讀到哪（之後重載不覆蓋）
                const lastRead = Number(info.group.me?.last_read_message_id) || 0;
                const newest = Number(data.messages?.at(-1)?.id) || 0;
                this._assistantUnread = newest > lastRead ? { from_id: lastRead + 1 } : null;
            }
            this._paintGroupHeader();
            this._setReadOnly(!this.isPremium);
            const messages = data.messages || [];
            this.hasMoreMessages = !!data.has_more;
            this.oldestMessageId = messages[0]?.id ?? null;
            this.setupOlderScroll(el);
            this._bindReadersClick(el);
            el.innerHTML = messages.map((m) => this.renderGroupRow(m)).join('');
            regroupMessageRows(el);
            el.scrollTop = el.scrollHeight;
            this.markGroupRead();
            window.AppUtils?.refreshIcons();
            this.runPendingJump?.(`group:${groupId}`); // 從搜尋結果點進來：跳到那則（social-search.js）
        } catch (e) {
            if (seq !== this.pagingSeq) return;
            if (e?.status === 404) {
                // 被踢、群刪了、開關關了
                toast(t('groups.errors.not_found'), 'error');
                this.closeChat();
                this.loadConversations();
                return;
            }
            console.error('[SocialHub] load group failed:', e);
            el.innerHTML = `<div class="text-center text-danger py-4 text-sm">${t('friends.loadFailed')}</div>`;
        }
        window.AppUtils?.refreshIcons();
    },

    /**
     * 回到前景／重連（SocialHub.resync）：只補最新一頁，畫面上的訊息不動（同私訊的 resync）。
     * 不能叫 loadGroup——它先把整片換成轉圈、再全部重畫、捲到底，切回視窗就閃一下（2026-10-02）。
     */
    refreshGroupAfterResume: async function () {
        const groupId = this.currentGroupId;
        if (!groupId) return;
        try {
            const data = await GroupChatAPI.messages(groupId);
            if (groupId !== this.currentGroupId) return;
            (data.messages || []).forEach((m) => this.appendGroupRow(m)); // 已在畫面上的會被略過
            this.reloadGroupInfo(); // 離開期間的已讀、成員異動
            this.markGroupRead();
        } catch (e) {
            console.warn('[SocialHub] group resync failed:', e);
        }
    },

    loadMoreGroupMessages: function (pageSize = 20) {
        if (this._loadMorePromise) return this._loadMorePromise;
        const el = container();
        const groupId = this.currentGroupId;
        if (!el || !groupId || !this.hasMoreMessages || !this.oldestMessageId) return Promise.resolve(false);
        const seq = this.pagingSeq;
        const beforeId = this.oldestMessageId;
        this.isLoadingMore = true;
        const run = async () => {
            try {
                const data = await GroupChatAPI.messages(groupId, { beforeId, limit: Number(pageSize) || 20 });
                if (seq !== this.pagingSeq) return false;
                const messages = data.messages || [];
                if (!messages.length) {
                    this.hasMoreMessages = false;
                    return false;
                }
                prependOlderRows(el, messages, (m) => this.renderGroupRow(m));
                this.hasMoreMessages = !!data.has_more;
                this.oldestMessageId = messages[0].id;
                window.AppUtils?.refreshIcons();
                return true;
            } catch (e) {
                console.warn('[SocialHub] load older group messages failed:', e);
                return false;
            }
        };
        const promise = run().finally(() => {
            if (this._loadMorePromise === promise) {
                this._loadMorePromise = null;
                this.isLoadingMore = false;
            }
        });
        this._loadMorePromise = promise;
        return promise;
    },

    appendGroupRow: function (msg) {
        const el = container();
        if (!el) return;
        const isMine = msg.from_user_id === myId();
        const nearBottom = isNearBottom(el);
        if (!insertMessageRow(el, msg.id, this.renderGroupRow(msg))) return;
        regroupMessageRows(el);
        if (isMine || nearBottom) el.scrollTop = el.scrollHeight;
        else showNewMessagePill(el);
        window.AppUtils?.refreshIcons();
    },

    // ── 送出、輸入中、已讀 ─────────────────────────────────

    sendGroupMessage: async function (e) {
        if (e) e.preventDefault();
        const groupId = this.currentGroupId;
        const input = document.getElementById('social-msg-input');
        const content = input?.value?.trim();
        if (!groupId || !content || this.isSending) return;
        this.isSending = true;
        const btn = document.getElementById('social-send-btn');
        if (btn) btn.disabled = true;
        try {
            const data = await GroupChatAPI.send(groupId, content, getReplyTarget(SOCIAL_MSGS));
            clearReply(SOCIAL_MSGS);
            input.value = '';
            this._mentionPicker?.close();
            this.autoResizeInput(input);
            window.MessagesWebSocket?.sendGroupTypingStop(groupId);
            if (data.message) this.appendGroupRow(data.message);
            this.loadConversations().catch(() => {});
        } catch (err) {
            if (err?.message === 'pro_required') this._setReadOnly(true);
            toast(handleReplySendError(SOCIAL_MSGS, err) || groupErrorText(err), 'error');
        } finally {
            this.isSending = false;
            this.updateCharCount();
            input?.focus();
        }
    },

    // ── @提及選單 ───────────────────────────────────────

    _mentionPicker: null,
    _mentionInput: null,

    /** 輸入框跟私訊共用：選單只在開著群組、而且能發言時作用。輸入框被重畫過就重建 */
    _ensureMentionPicker: function () {
        const input = document.getElementById('social-msg-input');
        const anchor = document.getElementById('social-msg-input-container');
        if (!input || !anchor) return null;
        if (this._mentionPicker && this._mentionInput === input) return this._mentionPicker;
        this._mentionPicker?.close();
        this._mentionInput = input;
        this._mentionPicker = createMentionPicker({
            input,
            anchor,
            isActive: () => !!this.currentGroupId && !input.disabled,
            getMembers: () => this.currentGroup?.members || [],
            getMyId: () => myId(),
            onApplied: (el) => this.onComposerInput(el),
        });
        return this._mentionPicker;
    },

    /** SocialHub.handleInputKeydown 擋完輸入法後先問這裡：選單開著時 ↑↓／Enter／Tab／Esc 歸選單 */
    handleMentionKeydown: function (e) {
        return !!this.currentGroupId && !!this._mentionPicker?.handleKeydown(e);
    },

    onGroupComposerInput: function (el) {
        if (!window.MessagesWebSocket) return;
        if (el.value.trim()) window.MessagesWebSocket.sendGroupTyping(this.currentGroupId);
        else window.MessagesWebSocket.sendGroupTypingStop(this.currentGroupId);
    },

    /** 讀到畫面上最新一則：畫面真的在眼前才算；連續來訊合併成一次 */
    markGroupRead: function () {
        const groupId = this.currentGroupId;
        if (!groupId || !this._isChatVisible()) return;
        clearTimeout(this._groupReadTimer);
        this._groupReadTimer = setTimeout(async () => {
            const rows = [...(container()?.querySelectorAll('.msg-row[data-message-id]') || [])];
            const lastId = Math.max(0, ...rows.map((r) => Number(r.dataset.messageId) || 0));
            if (!lastId || groupId !== this.currentGroupId) return;
            try {
                await GroupChatAPI.markRead(groupId, lastId);
                this._scheduleListReload();
                window.NavBadges?.schedule(); // 側欄「社群」徽章也要跟著少，不然要重整才消
            } catch (e) {
                console.debug('[SocialHub] mark group read failed', e);
            }
        }, 800);
    },

    _scheduleListReload: function () {
        clearTimeout(this._convReloadTimer);
        this._convReloadTimer = setTimeout(() => this.loadConversations(), 300);
    },

    /** 有人讀了：更新那個人的讀取位置，重算自己訊息旁的「已讀 N」 */
    refreshGroupReadStatuses: function () {
        const el = container();
        const me = myId();
        if (!el || !this.currentGroup) return;
        el.querySelectorAll('.msg-row[data-message-id]').forEach((row) => {
            if (row.dataset.from !== me || row.dataset.type === 'recalled') return;
            const status = row.querySelector('.msg-read-status');
            if (!status) return;
            const count = groupReadCount(
                { id: Number(row.dataset.messageId), from_user_id: me },
                this.currentGroup.members || []
            );
            const hidden = status.classList.contains('hidden');
            status.outerHTML = groupReadStatusHtml(count);
            if (hidden) row.querySelector('.msg-read-status')?.classList.add('hidden');
        });
        regroupMessageRows(el);
    },

    /** 誰讀過這則：長按選單「已讀名單」，或手機上直接點「已讀 N」 */
    showGroupReaders: function (messageId) {
        if (!this.currentGroup) return;
        const id = Number(messageId);
        const readers = (this.currentGroup.members || []).filter(
            (m) =>
                m.user_id !== myId() &&
                Number(m.first_visible_message_id || 0) < id &&
                Number(m.last_read_message_id || 0) >= id
        );
        const names = readers.map((m) => m.display_name || m.username || m.user_id);
        window.showInfoDialog?.({
            title: t('groups.readers'),
            message: names.length ? names.join('、') : t('groups.noReaders'),
        });
    },

    /** 點「已讀 N」看誰讀過（觸控裝置；桌機 hover 會被工具列蓋住，走選單）。只綁一次 */
    _bindReadersClick: function (el) {
        if (!el || el.dataset.groupReadersBound) return;
        el.dataset.groupReadersBound = '1';
        el.addEventListener('click', (e) => {
            const status = e.target.closest?.('.msg-read-status');
            if (!status || !this.currentGroupId) return;
            this.showGroupReaders(status.closest('.msg-row')?.dataset.messageId);
        });
    },

    // ── 即時事件（/ws/messages 的 group_*） ─────────────────

    onGroupEvent: function (data) {
        const groupId = Number(data.group_id);
        const isOpen = groupId === this.currentGroupId;
        const el = container();
        switch (data.type) {
            case 'group_message': {
                const msg = data.message || {};
                if (msg.from_user_id && msg.from_user_id !== myId()) {
                    setListTyping(document.getElementById('social-conv-list'), `g-${groupId}`, false);
                }
                if (isOpen) {
                    if (msg.from_user_id !== myId()) hideTypingIndicator(el);
                    this.appendGroupRow(msg);
                    if (msg.from_user_id !== myId()) this.markGroupRead();
                }
                this._scheduleListReload();
                break;
            }
            case 'group_message_recalled':
                if (isOpen) {
                    markMessageRowRecalled(el, data.message_id, (m) => this.renderGroupRow(m));
                    handleMessageRecalled(SOCIAL_MSGS, data.message_id);
                }
                this._scheduleListReload();
                break;
            case 'group_reaction_updated':
                if (isOpen) updateMessageReactions(SOCIAL_MSGS, data.message_id, data.reactions);
                break;
            case 'group_read': {
                // 自己在別台裝置讀了這個群（後端會廣播給自己）：這台的列表未讀與側欄徽章也要跟著少；
                // 沒開著這個群時下面就 break 了，所以要先處理
                if (data.user_id === myId()) {
                    this._scheduleListReload();
                    window.NavBadges?.schedule();
                }
                if (!isOpen || !this.currentGroup) break;
                const member = (this.currentGroup.members || []).find((m) => m.user_id === data.user_id);
                if (member) {
                    member.last_read_message_id = Math.max(
                        Number(member.last_read_message_id || 0),
                        Number(data.last_read_message_id || 0)
                    );
                    this.refreshGroupReadStatuses();
                }
                break;
            }
            case 'group_typing': {
                const typing = data.state !== 'stop';
                setListTyping(document.getElementById('social-conv-list'), `g-${groupId}`, typing);
                if (!isOpen) break;
                if (typing) showTypingIndicator(el, data.from_username || this.groupNameOf(data.from_user_id));
                else hideTypingIndicator(el);
                break;
            }
            case 'group_updated':
                // 自己被踢、退群（別的裝置）、群刪了、群主解散：關掉；其他（加入、改名、設定）重抓成員與標題
                if (isOpen && ['removed', 'left', 'deleted', 'dissolved'].includes(data.kind)) {
                    if (data.kind === 'removed') toast(t('groups.removed'), 'info');
                    if (data.kind === 'dissolved' && Number(data.group_id) !== this._dissolvingGroupId) {
                        toast(t('groups.dissolvedNotice'), 'info');
                    }
                    this.closeChat();
                } else if (isOpen) {
                    this.reloadGroupInfo();
                }
                this._scheduleListReload();
                window.NavBadges?.schedule(); // 退群／解散／加入會改變未讀與邀請數字
                break;
        }
    },

    reloadGroupInfo: async function () {
        const groupId = this.currentGroupId;
        if (!groupId) return;
        try {
            const info = await GroupChatAPI.get(groupId);
            if (groupId !== this.currentGroupId) return;
            this.currentGroup = info.group;
            this._paintGroupHeader();
            this.refreshGroupReadStatuses();
        } catch (e) {
            if (e?.status === 404 && groupId === this.currentGroupId) this.closeChat();
        }
    },

    // ── 收回 ────────────────────────────────────────────

    recallGroupMessage: async function (messageId) {
        const ok = await recallDmMessage(messageId, {
            container: container(),
            renderBubble: (m) => this.renderGroupRow(m),
            url: GroupChatAPI.recallUrl(messageId),
        });
        if (ok) this._scheduleListReload();
    },

    // ── 開群、群組資訊 ───────────────────────────────────

    _friendsForPicker: async function () {
        const res = await AppAPI.get('/api/friends/list?limit=100');
        return res.friends || [];
    },

    /** 「＋ 建立群組」：非 Pro 提示升級；Pro 選名稱＋好友 */
    openCreateGroup: async function () {
        if (!this._limitsPromise) this.loadQuota();
        const limits = await this._limitsPromise;
        if (limits) this.isPremium = !!limits.is_premium;
        if (!this.isPremium) {
            const go = await confirmDmAction({
                title: t('groups.proOnlyTitle'),
                message: t('groups.proOnlyMessage'),
                confirmText: t('groups.upgrade'),
            });
            if (go) window.location.href = '/static/forum/premium.html';
            return;
        }
        let friends = [];
        try {
            friends = await this._friendsForPicker();
        } catch (e) {
            console.warn('[SocialHub] load friends failed', e);
        }
        const picked = await openFriendPicker({
            title: t('groups.createTitle'),
            submitText: t('groups.createSubmit'),
            friends,
            withName: true,
        });
        if (!picked) return;
        try {
            const res = await GroupChatAPI.create(picked.name, picked.ids);
            toast(this._inviteSummary(t('groups.created'), res), 'success');
            await this.loadConversations();
            this.openGroup(res.group.id);
        } catch (err) {
            toast(groupErrorText(err), 'error');
        }
    },

    _inviteSummary: function (head, res) {
        const parts = [head];
        if (res.invited?.length) parts.push(t('groups.invitedCount', { count: res.invited.length }));
        if (res.skipped?.length) {
            const reasons = [...new Set(res.skipped.map((s) => t(`groups.skip.${s.reason}`)))].join('、');
            parts.push(t('groups.skippedCount', { count: res.skipped.length, reasons }));
        }
        return parts.join('；');
    },

    openGroupInfo: async function () {
        const group = this.currentGroup;
        if (!group) return;
        const picked = await openGroupInfoPanel({ group, myId: myId(), aiNotice: assistantEnabled() });
        if (!picked) return;
        const groupId = this.currentGroupId;
        try {
            switch (picked.action) {
                case 'invite': {
                    const friends = await this._friendsForPicker();
                    const result = await openFriendPicker({
                        title: t('groups.inviteTitle'),
                        submitText: t('groups.inviteSubmit'),
                        friends,
                        excludeIds: new Set((group.members || []).map((m) => m.user_id)),
                    });
                    if (!result?.ids.length) return;
                    const res = await GroupChatAPI.invite(groupId, result.ids);
                    toast(this._inviteSummary('', res).replace(/^；/, ''), 'success');
                    return;
                }
                case 'mute':
                    await GroupChatAPI.mute(groupId, !group.me?.muted);
                    await this.reloadGroupInfo();
                    this._scheduleListReload();
                    window.NavBadges?.schedule(); // 靜音的群不算進側欄數字，後端沒有 push 通知
                    return;
                case 'history':
                    await GroupChatAPI.update(groupId, { history_visible: !group.history_visible });
                    await this.reloadGroupInfo();
                    return;
                case 'rename': {
                    const name = await openNameDialog({ title: t('groups.renameTitle'), value: group.name });
                    if (!name || name === group.name) return;
                    await GroupChatAPI.update(groupId, { name });
                    await this.reloadGroupInfo();
                    this._scheduleListReload();
                    return;
                }
                case 'remove': {
                    const name = this.groupNameOf(picked.userId);
                    const ok = await confirmDmAction({
                        title: t('groups.removeConfirmTitle', { name }),
                        confirmText: t('groups.removeMember'),
                        danger: true,
                    });
                    if (!ok) return;
                    await GroupChatAPI.removeMember(groupId, picked.userId);
                    await this.reloadGroupInfo();
                    return;
                }
                case 'transfer': {
                    const name = this.groupNameOf(picked.userId);
                    const ok = await confirmDmAction({
                        title: t('groups.transferConfirmTitle', { name }),
                        message: t('groups.transferConfirmMessage'),
                        confirmText: t('groups.transferOwner'),
                    });
                    if (!ok) return;
                    await GroupChatAPI.transferOwner(groupId, picked.userId);
                    await this.reloadGroupInfo();
                    return;
                }
                case 'leave': {
                    const isOwner = group.owner_id === myId();
                    const ok = await confirmDmAction({
                        title: t('groups.leaveConfirmTitle', { name: group.name }),
                        message: isOwner ? t('groups.leaveOwnerHint') : t('groups.leaveConfirmMessage'),
                        confirmText: t('groups.leave'),
                        danger: true,
                    });
                    if (!ok) return;
                    await GroupChatAPI.leave(groupId);
                    this.closeChat();
                    this.loadConversations();
                    window.NavBadges?.schedule();
                    return;
                }
                case 'dissolve': {
                    const ok = await confirmDmAction({
                        title: t('groups.dissolveConfirmTitle', { name: group.name }),
                        message: t('groups.dissolveConfirmMessage'),
                        confirmText: t('groups.dissolve'),
                        danger: true,
                    });
                    if (!ok) return;
                    // WS 的「群主已解散」會比 API 回應先到：自己按的就不要再跳那則（只留「群組已解散」）
                    this._dissolvingGroupId = Number(groupId);
                    await GroupChatAPI.dissolve(groupId);
                    toast(t('groups.dissolvedSelf'), 'success');
                    this.closeChat();
                    this.loadConversations();
                    window.NavBadges?.schedule();
                    return;
                }
            }
        } catch (err) {
            toast(groupErrorText(err), 'error');
        }
    },
};

export { SocialGroups, groupErrorText, isProTier };
