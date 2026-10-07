/**
 * 群組聊天的對話框（設計 docs/plans/2026-10-01-group-chat-design.md）：
 *   - openFriendPicker：開群（群名＋勾好友）、邀請好友共用；只有 Pro 好友能勾
 *   - openNameDialog：改群名
 *   - openGroupInfoPanel：群組資訊（成員、通知、邀請、退群；群主另有改名、歷史紀錄開關、轉讓群主、踢人）
 * 版型照私訊檢舉對話框（dm-message-actions.js openReportDialog）：DOM API 組，使用者內容一律 textContent。
 * 動作本身（打 API）交給呼叫端（social-groups.js），這裡只負責收集輸入、回傳選擇。
 */

import { avatarColorClass, avatarInitial } from './avatar.js';

const t = (key, args) => (window.I18n ? window.I18n.t(key, args) : key);
const PRO_TIERS = ['premium', 'pro', 'plus']; // 同 FriendsUI.getMembershipBadge；後端會再驗一次（含到期）
const NAME_MAX = 30; // 同後端 group_chat_repo.NAME_MAX

function el(tag, cls = '', text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
}

function isProTier(tier) {
    return PRO_TIERS.includes(String(tier || '').toLowerCase());
}

function friendName(f) {
    return f.display_name || f.username || f.user_id;
}

/** 遮罩＋卡片；Esc／點遮罩＝取消。回 { overlay, card, close } */
function modalShell(resolve, { drawer = false } = {}) {
    const overlay = el(
        'div',
        drawer
            ? 'group-chat-panel fixed inset-0 z-[100] bg-black/40 flex justify-end animate-fade-in'
            : 'group-chat-dialog fixed inset-0 z-[100] bg-black/60 backdrop-blur-sm flex items-end md:items-center justify-center p-4 animate-fade-in overflow-y-auto'
    );
    const card = el(
        'div',
        drawer
            ? 'w-full max-w-sm h-full bg-surface border-l border-borderSubtle shadow-2xl flex flex-col'
            : 'w-full max-w-md bg-surface border border-borderSubtle rounded-3xl shadow-2xl flex flex-col max-h-[85vh]'
    );
    overlay.appendChild(card);
    const onKey = (e) => {
        if (e.key === 'Escape') close(null);
    };
    function close(value) {
        document.removeEventListener('keydown', onKey);
        overlay.remove();
        resolve(value);
    }
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) close(null);
    });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);
    return { overlay, card, close };
}

function footerButtons(close, submitText, onSubmit) {
    const footer = el('div', 'flex gap-3 p-4 border-t border-borderSubtle');
    const cancel = el('button', 'flex-1 py-2.5 rounded-2xl bg-surfaceHighlight text-textMain text-sm font-medium', t('common.cancel'));
    cancel.type = 'button';
    cancel.addEventListener('click', () => close(null));
    const submit = el('button', 'flex-1 py-2.5 rounded-2xl bg-primary text-background text-sm font-bold disabled:opacity-50', submitText);
    submit.type = 'button';
    submit.addEventListener('click', onSubmit);
    footer.append(cancel, submit);
    return { footer, submit };
}

/**
 * 勾好友（開群時多一個群名欄位）。
 * @param {{title: string, submitText: string, friends: object[], withName?: boolean, excludeIds?: Set<string>}} opts
 * @returns {Promise<{name: string, ids: string[]} | null>}
 */
function openFriendPicker({ title, submitText, friends = [], withName = false, excludeIds = new Set() }) {
    return new Promise((resolve) => {
        const { card, close } = modalShell(resolve);
        const body = el('div', 'p-5 overflow-y-auto');
        body.appendChild(el('h3', 'text-lg font-bold text-textMain', title));

        let nameInput = null;
        if (withName) {
            nameInput = el(
                'input',
                'mt-4 w-full rounded-xl bg-background border border-borderLight px-3 py-2.5 text-sm text-textMain placeholder-textMuted focus:outline-none focus:border-primary/50'
            );
            nameInput.maxLength = NAME_MAX;
            nameInput.placeholder = t('groups.namePlaceholder');
            body.appendChild(nameInput);
        }

        body.appendChild(el('p', 'mt-4 mb-2 text-xs text-textMuted', t('groups.pickFriends')));
        const list = el('div', 'flex flex-col gap-1');
        const boxes = [];
        if (!friends.length) list.appendChild(el('p', 'py-6 text-center text-sm text-textMuted', t('groups.noFriends')));
        friends.forEach((f) => {
            const already = excludeIds.has(f.user_id);
            const pro = isProTier(f.membership_tier);
            const row = el(
                'label',
                `flex items-center gap-3 px-2 py-2 rounded-xl ${already || !pro ? 'opacity-50' : 'hover:bg-surfaceHighlight cursor-pointer'}`
            );
            const box = el('input', 'accent-[rgb(var(--color-primary))] w-4 h-4 shrink-0');
            box.type = 'checkbox';
            box.value = f.user_id;
            box.disabled = already || !pro;
            const avatar = el(
                'span',
                `w-8 h-8 rounded-full ${avatarColorClass(f.user_id)} flex items-center justify-center text-xs font-bold shrink-0`,
                avatarInitial(friendName(f))
            );
            const name = el('span', 'flex-1 min-w-0 truncate text-sm text-textMain', friendName(f));
            row.append(box, avatar, name);
            if (already || !pro) {
                row.appendChild(el('span', 'shrink-0 whitespace-nowrap text-[11px] text-textMuted', t(already ? 'groups.alreadyIn' : 'groups.needsPro')));
            }
            boxes.push(box);
            list.appendChild(row);
        });
        body.appendChild(list);
        const error = el('p', 'mt-2 text-sm text-danger hidden');
        body.appendChild(error);

        const { footer } = footerButtons(close, submitText, () => {
            const name = nameInput ? nameInput.value.trim() : '';
            const ids = boxes.filter((b) => b.checked).map((b) => b.value);
            if (withName && !name) {
                error.textContent = t('groups.nameRequired');
                error.classList.remove('hidden');
                return;
            }
            if (!withName && !ids.length) return;
            close({ name, ids });
        });
        card.append(body, footer);
        (nameInput || boxes.find((b) => !b.disabled))?.focus();
    });
}

/** 改群名 → 新名稱或 null */
function openNameDialog({ title, value = '' }) {
    return new Promise((resolve) => {
        const { card, close } = modalShell(resolve);
        const body = el('div', 'p-5');
        body.appendChild(el('h3', 'text-lg font-bold text-textMain', title));
        const input = el(
            'input',
            'mt-4 w-full rounded-xl bg-background border border-borderLight px-3 py-2.5 text-sm text-textMain focus:outline-none focus:border-primary/50'
        );
        input.maxLength = NAME_MAX;
        input.value = value;
        body.appendChild(input);
        const { footer } = footerButtons(close, t('groups.save'), () => {
            const name = input.value.trim();
            if (name) close(name);
        });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229 && input.value.trim()) close(input.value.trim());
        });
        card.append(body, footer);
        input.focus();
        input.select();
    });
}

/**
 * 群組資訊面板（右側抽屜）。按了動作就關掉並回傳 { action, userId? }，由呼叫端執行後視需要再打開。
 * action：invite｜mute｜history｜rename｜leave｜remove
 * @returns {Promise<{action: string, userId?: string} | null>}
 */
function openGroupInfoPanel({ group, myId, aiNotice = false }) {
    return new Promise((resolve) => {
        const { card, close } = modalShell(resolve, { drawer: true });
        const isOwner = group.owner_id === myId;
        const members = group.members || [];

        const head = el('div', 'flex items-center gap-3 p-4 border-b border-borderSubtle');
        const titleBox = el('div', 'flex-1 min-w-0');
        titleBox.append(
            el('h3', 'font-bold text-textMain truncate', group.name),
            el('p', 'text-xs text-textMuted', t('groups.memberCount', { count: members.length }))
        );
        const closeBtn = el('button', 'w-9 h-9 rounded-full flex items-center justify-center text-textMuted hover:bg-surfaceHighlight', '✕');
        closeBtn.type = 'button';
        closeBtn.setAttribute('aria-label', t('common.close'));
        closeBtn.addEventListener('click', () => close(null));
        head.append(titleBox, closeBtn);

        const body = el('div', 'flex-1 overflow-y-auto p-4 flex flex-col gap-4');

        const toggle = (label, hint, checked, action) => {
            const row = el('label', 'flex items-center justify-between gap-3 cursor-pointer');
            const text = el('div', 'min-w-0');
            text.appendChild(el('p', 'text-sm text-textMain', label));
            if (hint) text.appendChild(el('p', 'text-[11px] text-textMuted', hint));
            const box = el('input', 'accent-[rgb(var(--color-primary))] w-4 h-4 shrink-0');
            box.type = 'checkbox';
            box.checked = !!checked;
            box.addEventListener('change', () => close({ action }));
            row.append(text, box);
            return row;
        };
        const actionBtn = (label, action, danger = false) => {
            const btn = el(
                'button',
                `w-full py-2.5 rounded-2xl text-sm font-medium ${danger ? 'text-danger bg-danger/10' : 'text-textMain bg-surfaceHighlight'}`,
                label
            );
            btn.type = 'button';
            btn.addEventListener('click', () => close({ action }));
            return btn;
        };

        const settings = el('div', 'flex flex-col gap-3');
        settings.appendChild(toggle(t('groups.mute'), '', group.me?.muted, 'mute'));
        if (isOwner) settings.appendChild(toggle(t('groups.historyVisible'), t('groups.historyHint'), group.history_visible, 'history'));
        body.appendChild(settings);

        const actions = el('div', 'flex flex-col gap-2');
        actions.appendChild(actionBtn(t('groups.inviteFriends'), 'invite'));
        if (isOwner) actions.appendChild(actionBtn(t('groups.rename'), 'rename'));
        body.appendChild(actions);

        body.appendChild(el('p', 'text-xs text-textMuted', t('groups.members', { count: members.length })));
        const list = el('div', 'flex flex-col gap-1');
        members.forEach((m) => {
            const name = m.display_name || m.username || m.user_id;
            const row = el('div', 'flex items-center gap-3 py-1.5');
            row.appendChild(
                el(
                    'span',
                    `w-8 h-8 rounded-full ${avatarColorClass(m.user_id)} flex items-center justify-center text-xs font-bold shrink-0`,
                    avatarInitial(name)
                )
            );
            row.appendChild(el('span', 'flex-1 min-w-0 truncate text-sm text-textMain', name));
            if (m.is_owner) row.appendChild(el('span', 'shrink-0 whitespace-nowrap text-[11px] text-primary', t('groups.owner')));
            else if (m.user_id === myId) row.appendChild(el('span', 'shrink-0 whitespace-nowrap text-[11px] text-textMuted', t('groups.you')));
            if (isOwner && m.user_id !== myId) {
                const transfer = el('button', 'shrink-0 whitespace-nowrap text-[11px] text-primary hover:underline', t('groups.transferOwner'));
                transfer.type = 'button';
                transfer.addEventListener('click', () => close({ action: 'transfer', userId: m.user_id }));
                const kick = el('button', 'shrink-0 whitespace-nowrap text-[11px] text-danger hover:underline', t('groups.removeMember'));
                kick.type = 'button';
                kick.addEventListener('click', () => close({ action: 'remove', userId: m.user_id }));
                row.append(transfer, kick);
            }
            list.appendChild(row);
        });
        body.appendChild(list);
        // 聊天室 AI 助理開著時告訴成員（設計 docs/plans/2026-10-01-chat-assistant-design.md〈揭露〉）
        if (aiNotice) body.appendChild(el('p', 'text-[11px] text-textMuted', `✨ ${t('groups.aiNotice')}`));

        const foot = el('div', 'p-4 border-t border-borderSubtle flex flex-col gap-2');
        foot.appendChild(actionBtn(t('groups.leave'), 'leave', true));
        // 群主才有：解散＝所有人移出、群組消失（退出則由最早加入的成員接手群主）
        if (isOwner) foot.appendChild(actionBtn(t('groups.dissolve'), 'dissolve', true));
        card.append(head, body, foot);
    });
}

export { openFriendPicker, openNameDialog, openGroupInfoPanel, isProTier };
