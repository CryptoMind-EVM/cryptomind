/**
 * 群組聊天前端共用（設計 docs/plans/2026-10-01-group-chat-design.md）：API、系統訊息文字、
 * 「已讀 N」、群組氣泡與列表項。好友頁（friends.js）與手機私訊頁共用；畫面狀態由各頁自己管。
 *
 * 氣泡沿用私訊的列結構（.msg-row[data-from][data-ts] > .msg-bubble＋.msg-meta），所以
 * insertMessageRow／regroupMessageRows／長按選單／表情都照用；群組多的只有：
 *   - 系統訊息：置中膠囊，data-from="__system"（打斷同人分組），沒有 .msg-bubble（不能長按／按表情）
 *   - 別人的訊息：.msg-avatar（彩色頭像）＋氣泡內 .msg-sender（暱稱）；同一人連續時 regroupMessageRows 會藏
 *   - 自己的訊息：.msg-read-status 寫「已讀 N」（沒人讀＝已送達），regroup 只留最新一則
 */

import { aiCardRowHtml, isAiCard } from './ai-card.js';
import { avatarColorClass, avatarInitial } from './avatar.js';
import { messageToolsHtml, quoteHtml, reactionsHtml, renderMessageText } from './dm-message-actions.js';
import { pinHoverButtonHtml, pinIndicatorHtml, pinKey } from './chat-pins.js';
import { escapeMessageAttr, formatMessageClock } from './messages.js';

const t = (key, args) => (window.I18n ? window.I18n.t(key, args) : key);
const esc = escapeMessageAttr;

// ============================================================================
// API
// ============================================================================

// GET 不重試：404＝開關關著／不是成員（AppAPI 預設會對 404 重試 3 次、等 6 秒）
const NO_RETRY = { retries: 0 };

const GroupChatAPI = {
    list: () => AppAPI.get('/api/groups', NO_RETRY),
    get: (groupId) => AppAPI.get(`/api/groups/${Number(groupId)}`, NO_RETRY),
    create: (name, inviteeIds = []) => AppAPI.post('/api/groups', { name, invitee_ids: inviteeIds }),
    update: (groupId, patch) => AppAPI.patch(`/api/groups/${Number(groupId)}`, patch),
    leave: (groupId) => AppAPI.post(`/api/groups/${Number(groupId)}/leave`),
    dissolve: (groupId) => AppAPI.post(`/api/groups/${Number(groupId)}/dissolve`),
    removeMember: (groupId, userId) =>
        AppAPI.delete(`/api/groups/${Number(groupId)}/members/${encodeURIComponent(userId)}`),
    transferOwner: (groupId, userId) => AppAPI.post(`/api/groups/${Number(groupId)}/owner`, { user_id: userId }),
    mute: (groupId, muted) => AppAPI.post(`/api/groups/${Number(groupId)}/mute`, { muted }),
    invite: (groupId, userIds) => AppAPI.post(`/api/groups/${Number(groupId)}/invites`, { user_ids: userIds }),
    invites: () => AppAPI.get('/api/groups/invites', NO_RETRY),
    acceptInvite: (inviteId) => AppAPI.post(`/api/groups/invites/${Number(inviteId)}/accept`),
    declineInvite: (inviteId) => AppAPI.post(`/api/groups/invites/${Number(inviteId)}/decline`),
    messages: (groupId, { beforeId, limit = 50 } = {}) =>
        AppAPI.get(
            `/api/groups/${Number(groupId)}/messages?limit=${Number(limit)}${beforeId ? `&before_id=${Number(beforeId)}` : ''}`,
            NO_RETRY
        ),
    send: (groupId, content, replyToMessageId = null) =>
        AppAPI.post(`/api/groups/${Number(groupId)}/messages`, {
            content,
            ...(replyToMessageId ? { reply_to_message_id: replyToMessageId } : {}),
        }),
    markRead: (groupId, lastMessageId) =>
        AppAPI.post(`/api/groups/${Number(groupId)}/read`, { last_message_id: Number(lastMessageId) }),
    // 給長按選單（dm-message-actions）換掉私訊網址用
    recallUrl: (messageId) => `/api/groups/messages/${Number(messageId)}`,
    reactionUrl: (messageId) => `/api/groups/messages/${Number(messageId)}/reaction`,
    reportUrl: (messageId) => `/api/groups/messages/${Number(messageId)}/report`,
};

// ============================================================================
// 系統訊息、已讀
// ============================================================================

/** 系統訊息事件碼（created:<uid>、renamed:<群名>…）→ 顯示文字。只切第一個冒號：群名可能有冒號 */
function groupSystemText(content, nameOf = (uid) => uid, systemName = null) {
    const raw = String(content || '');
    const sep = raw.indexOf(':');
    const code = sep < 0 ? raw : raw.slice(0, sep);
    const arg = sep < 0 ? '' : raw.slice(sep + 1);
    const byUser = {
        created: 'groups.system.created',
        member_joined: 'groups.system.memberJoined',
        member_left: 'groups.system.memberLeft',
        member_removed: 'groups.system.memberRemoved',
        owner_changed: 'groups.system.ownerChanged',
    };
    // 後端帶的名字優先：對方已退群、或成員名單還沒載入時 nameOf 只拿得到 id
    if (byUser[code]) return t(byUser[code], { name: systemName || nameOf(arg) });
    if (code === 'renamed') return t('groups.system.renamed', { name: arg });
    if (code === 'history_visible') return t(arg === 'on' ? 'groups.system.historyOn' : 'groups.system.historyOff');
    return t('groups.system.unknown');
}

/** 「已讀 N」：發訊者以外、看得到這則（入群後的）、讀到這則以後的成員數 */
function groupReadCount(message, members = []) {
    const id = Number(message?.id);
    return members.filter(
        (m) =>
            m.user_id !== message?.from_user_id &&
            Number(m.first_visible_message_id || 0) < id &&
            Number(m.last_read_message_id || 0) >= id
    ).length;
}

/** 自己訊息旁的狀態：class 跟私訊一樣是 .msg-read-status（regroup 只留最新一則） */
function groupReadStatusHtml(count) {
    return count > 0
        ? `<span class="msg-read-status" data-read="1">${esc(t('groups.readCount', { count }))}</span>`
        : `<span class="msg-read-status" data-read="0">${esc(t('messages.deliveredStatus'))}</span>`;
}

// ============================================================================
// 氣泡、列表項
// ============================================================================

/**
 * @param {object} msg 群組訊息（同私訊形狀，多 group_id；message_type 可能是 system）
 * @param {{myId: string, nameOf: (uid:string)=>string, members: object[], idPrefix?: string}} ctx
 */
function renderGroupBubble(msg, ctx) {
    const prefix = ctx.idPrefix || 'msg-';
    const type = msg.message_type || 'text';
    const idAttrs = `id="${prefix}${Number(msg.id)}" data-message-id="${Number(msg.id)}" data-ts="${esc(msg.created_at)}" data-type="${esc(type)}"`;

    if (type === 'system') {
        return `<div ${idAttrs} data-from="__system" class="msg-row msg-system flex justify-center"><span class="max-w-[85%] px-3 py-0.5 rounded-full bg-surfaceHighlight text-[11px] text-textMuted text-center">${esc(groupSystemText(msg.content, ctx.nameOf, msg.system_name))}</span></div>`;
    }

    const isMine = msg.from_user_id === ctx.myId;
    const recalled = type === 'recalled';
    const time = formatMessageClock(msg.created_at);
    const rowOpen = `<div ${idAttrs} data-from="${esc(msg.from_user_id)}" class="msg-row group flex items-end gap-1.5 ${isMine ? 'justify-end' : 'justify-start'}">`;
    const readStatus = isMine && !recalled ? groupReadStatusHtml(groupReadCount(msg, ctx.members)) : '';
    const meta = `<div class="msg-meta flex flex-col ${isMine ? 'items-end' : 'items-start'} shrink-0 text-[11px] leading-tight text-textMuted whitespace-nowrap">${readStatus}<span>${esc(time)}</span></div>`;
    const bubbleBase = 'msg-bubble relative max-w-[75%] min-w-0 px-3.5 py-2 rounded-2xl text-[15px] leading-relaxed';
    const tools = messageToolsHtml({ isMine, recalled });

    const name = msg.from_display_name || msg.from_username || ctx.nameOf(msg.from_user_id);
    const avatar = isMine
        ? ''
        : `<div class="msg-avatar self-start w-8 h-8 rounded-full ${avatarColorClass(msg.from_user_id)} flex items-center justify-center text-xs font-bold shrink-0">${esc(avatarInitial(name))}</div>`;
    const sender = isMine ? '' : `<div class="msg-sender text-[12px] font-semibold text-textMuted mb-0.5 truncate">${esc(name)}</div>`;

    if (recalled) {
        const recalledText = t(isMine ? 'messages.recalledByMe' : 'messages.recalledByOther');
        const bubble = `<div class="${bubbleBase} bg-surfaceHighlight border border-borderLight">${sender}<span class="text-textMuted/60 text-sm italic">${esc(recalledText)}</span></div>`;
        return `${rowOpen}${isMine ? meta + tools + bubble : avatar + bubble + tools + meta}</div>`;
    }

    // AI 分析卡片（分享自聊天室 AI 助理）：整列另外畫，見 ai-card.js；別人分享的要看得出是誰
    if (isAiCard(msg)) {
        return aiCardRowHtml({ rowOpen, msg, isMine, meta, tools, avatar, sender, myId: ctx.myId });
    }

    const text = renderMessageText(msg.content, { mentions: msg.mentions || [], myId: ctx.myId, onPrimary: isMine });
    const content = `${quoteHtml(msg.reply_to, isMine)}<p class="msg-text whitespace-pre-wrap break-words">${text}</p>${reactionsHtml(msg.reactions, ctx.myId, isMine)}`;
    if (isMine) {
        return `${rowOpen}${meta}${tools}<div class="${bubbleBase} bg-primary text-background">${content}</div></div>`;
    }
    return `${rowOpen}${avatar}<div class="${bubbleBase} bg-surfaceHighlight border border-borderSubtle text-textMain">${sender}${content}</div>${tools}${meta}</div>`;
}

/** 列表最後一則的預覽文字 */
function groupPreviewText(last, nameOf) {
    if (!last) return t('groups.noMessages');
    if (last.message_type === 'system') return groupSystemText(last.content, nameOf, last.system_name);
    const who = last.from_display_name || last.from_username || nameOf(last.from_user_id);
    const body = last.message_type === 'recalled' ? t('messages.recalledPreview') : last.content || '';
    return `${who}: ${body}`;
}

/**
 * 群組在對話列表的那一列：跟私訊列同樣的版型；data-conversation-id 用 g-<id>（列表「輸入中…」共用，不跟對話 id 撞）
 * @param {{nameOf: Function, formatTime: Function, active?: boolean, href?: string, size?: 'lg'}} ctx
 *   size 'lg'：messages.html 的私訊列字比較大，群組列跟著放大才不會一大一小
 *   href：沒有 SocialHub 的頁面（messages.html）改成連結，帶去好友頁開群
 */
function renderGroupListItem(group, ctx) {
    const id = Number(group.id);
    const activeClass = ctx.active
        ? 'bg-surfaceHighlight border-l-2 border-primary'
        : 'hover:bg-surfaceHighlight border-l-2 border-transparent';
    const unread = Number(group.unread_count || 0);
    const unreadBadge =
        unread > 0
            ? `<span class="min-w-5 h-5 px-1 rounded-full bg-primary text-background text-[10px] font-bold flex items-center justify-center shrink-0">${unread > 99 ? '99+' : unread}</span>`
            : '';
    const muted = group.muted ? '<i data-lucide="bell-off" class="w-3 h-3 text-textMuted shrink-0"></i>' : '';
    // 有人 @ 我還沒讀（LINE 的「[有人提及你]」）；放在 .conv-preview 外面：列表「輸入中…」會整個換掉 preview 的文字
    const mention = group.mentioned
        ? `<span class="conv-mention ${ctx.size === 'lg' ? 'text-xs' : 'text-[11px]'} font-bold text-danger shrink-0 whitespace-nowrap">${esc(t('groups.mentionedYou'))}</span>`
        : '';
    const sz =
        ctx.size === 'lg'
            ? { pad: 'p-3', name: 'text-base', time: 'text-xs', preview: 'text-sm' }
            : { pad: 'p-4', name: 'text-sm', time: 'text-[10px]', preview: 'text-xs' };
    const open = ctx.href
        ? `<a href="${esc(ctx.href)}" class="block ${sz.pad} border-b border-borderSubtle cursor-pointer transition ${activeClass}">`
        : `<div data-click="SocialHub.openGroup" data-click-arg="${id}" class="${sz.pad} border-b border-borderSubtle cursor-pointer transition ${activeClass}">`;
    const close = ctx.href ? '</a>' : '</div>';
    // 置頂（只有好友頁的列表有：ctx.pinControls）：長按／右鍵選單認 data-pin-key，桌機 hover 有圖釘鈕
    const pinned = !!ctx.pinControls && group.pin_position != null;
    const key = pinKey('group', id);
    const pinAttrs = ctx.pinControls ? ` data-pin-key="${key}" data-pinned="${pinned ? 1 : 0}"` : '';
    return `
            <div id="group-${id}" data-conversation-id="g-${id}" data-group-id="${id}"${pinAttrs} class="relative group">
                ${open}
                    <div class="flex items-center gap-3">
                        <div class="w-10 h-10 rounded-full bg-surfaceHighlight text-textMuted flex items-center justify-center flex-shrink-0 relative">
                            <i data-lucide="users" class="w-5 h-5"></i>
                            ${unread > 0 ? '<span class="absolute top-0 right-0 w-2.5 h-2.5 bg-primary rounded-full border-2 border-surface"></span>' : ''}
                        </div>
                        <div class="flex-1 min-w-0">
                            <div class="flex justify-between items-baseline mb-0.5">
                                <h4 class="flex items-center gap-1 min-w-0 font-bold ${sz.name} text-textMain pr-2"><span class="truncate">${esc(group.name)}</span><span class="text-[11px] font-normal text-textMuted shrink-0">(${Number(group.member_count || 0)})</span>${muted}${pinned ? pinIndicatorHtml() : ''}</h4>
                                <span class="${sz.time} text-textMuted flex-shrink-0">${esc(ctx.formatTime(group.last_message_at || group.created_at))}</span>
                            </div>
                            <div class="flex justify-between items-center">
                                <div class="flex items-center gap-1 min-w-0 pr-2">${mention}<p class="conv-preview ${sz.preview} text-textMuted truncate min-w-0 opacity-80">${esc(groupPreviewText(group.last_message, ctx.nameOf))}</p></div>
                                ${unreadBadge}
                            </div>
                        </div>
                    </div>
                ${close}
                ${ctx.pinControls ? pinHoverButtonHtml(key, pinned, 'right-3') : ''}
            </div>
        `;
}

/** 好友頁開某個群組的網址（深連結；SPA 看到 ?group= 會切到好友頁並打開） */
function groupDeepLink(groupId) {
    return `/?group=${Number(groupId)}#friends`;
}

/** API 錯誤碼 → 在地化文案（後端 detail 是 groups.errors.* 的 key） */
function groupErrorText(err) {
    const code = err?.message || '';
    const key = `groups.errors.${code}`;
    const text = t(key);
    return text && text !== key ? text : t('groups.errors.default');
}

/**
 * 待回覆的群組邀請，放在對話列表最上面（好友頁、手機私訊頁共用）。以前只進通知中心，
 * toast 一關、鈴鐺沒點開就找不到。按鈕由 bindGroupInviteActions 接
 */
function renderGroupInvites(invites) {
    if (!invites || !invites.length) return '';
    const rows = invites
        .map(
            (inv) => `
            <div data-invite-id="${Number(inv.invite_id)}" data-group-id="${Number(inv.group_id)}" class="flex items-start gap-3 px-4 py-3 border-b border-borderSubtle bg-primary/5">
                <div class="w-10 h-10 rounded-full bg-primary/15 text-primary flex items-center justify-center shrink-0"><i data-lucide="user-plus" class="w-5 h-5"></i></div>
                <div class="flex-1 min-w-0">
                    <p class="flex items-center gap-1 min-w-0 text-sm font-bold text-textMain"><span class="truncate">${esc(inv.group_name)}</span><span class="text-[11px] font-normal text-textMuted shrink-0">(${Number(inv.member_count || 0)})</span></p>
                    <p class="text-xs text-textMuted truncate">${esc(t('groups.invitedBy', { name: inv.inviter_name || inv.inviter_id || '' }))}</p>
                    <div class="flex gap-2 mt-2">
                        <button type="button" data-invite-accept class="h-8 px-4 rounded-lg text-xs font-bold bg-primary text-background hover:brightness-110 active:scale-[0.98] transition disabled:opacity-50">${esc(t('groups.acceptInvite'))}</button>
                        <button type="button" data-invite-decline class="h-8 px-4 rounded-lg text-xs font-medium text-textMuted bg-surfaceHighlight hover:text-textMain active:scale-[0.98] transition disabled:opacity-50">${esc(t('groups.declineInvite'))}</button>
                    </div>
                </div>
            </div>`
        )
        .join('');
    return `<section id="group-invites" aria-label="${esc(t('groups.pendingInvites'))}">
            <h3 class="px-4 pt-3 pb-1.5 text-[11px] font-semibold text-textMuted">${esc(t('groups.pendingInvites'))} · ${invites.length}</h3>
            ${rows}
        </section>`;
}

/**
 * 邀請列的接受／拒絕（掛在列表容器上一次就好，列表重畫不用重綁）。
 * onDone({ accepted, groupId })：成功後由頁面決定要重抓列表、打開群組還是跳頁
 */
function bindGroupInviteActions(listEl, onDone) {
    if (!listEl || listEl.dataset.inviteActionsBound) return;
    listEl.dataset.inviteActionsBound = '1';
    listEl.addEventListener('click', async (e) => {
        const btn = e.target.closest?.('[data-invite-accept], [data-invite-decline]');
        const row = btn?.closest('[data-invite-id]');
        if (!row) return;
        const accept = btn.hasAttribute('data-invite-accept');
        const inviteId = Number(row.dataset.inviteId);
        row.querySelectorAll('button').forEach((b) => (b.disabled = true));
        try {
            const res = accept ? await GroupChatAPI.acceptInvite(inviteId) : await GroupChatAPI.declineInvite(inviteId);
            row.remove();
            const section = listEl.querySelector('#group-invites');
            if (section && !section.querySelector('[data-invite-id]')) section.remove();
            onDone?.({ accepted: accept, groupId: Number(res?.group_id || row.dataset.groupId) });
        } catch (err) {
            row.querySelectorAll('button').forEach((b) => (b.disabled = false));
            window.showToast?.(groupErrorText(err), 'error');
            // 邀請已失效（被取消、已處理過）：重抓讓那一列消失
            if (err?.message === 'invite_not_found') onDone?.({ accepted: false, stale: true });
        }
    });
}

export {
    bindGroupInviteActions,
    groupDeepLink,
    groupErrorText,
    GroupChatAPI,
    renderGroupInvites,
    groupSystemText,
    groupReadCount,
    groupReadStatusHtml,
    renderGroupBubble,
    groupPreviewText,
    renderGroupListItem,
};
