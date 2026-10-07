/**
 * 群組輸入框的 @提及選單：打 @ 跳出成員清單，選了插入「@暱稱 」。
 * 訊息原文就是「@暱稱」，誰被提及由後端比對成員名單（core/group_mentions.py），前端不另外送 id。
 *
 * 鍵盤：↑↓ 移動、Enter／Tab 選、Esc 關。keydown 由 SocialHub.handleInputKeydown 在擋完輸入法之後
 * 轉進來（handleKeydown 回 true＝吃掉了，不要送出）；其他事件（打字、點游標、失焦）這裡自己聽。
 */

import { avatarColorClass, avatarInitial } from './avatar.js';

const MAX_CANDIDATES = 8;
const MAX_QUERY = 30; // 同群名上限，暱稱不會更長

/**
 * 游標前的 @查詢：@ 到游標之間沒有空白／換行才算在打名字。回 { start, query } 或 null。
 * 全形「＠」也算：注音輸入法預設打全形（只認半形的話台灣使用者打 @ 選單根本不會出來）
 */
function mentionQuery(value, caret) {
    const before = String(value || '').slice(0, caret);
    const start = Math.max(before.lastIndexOf('@'), before.lastIndexOf('＠'));
    if (start < 0) return null;
    const query = before.slice(start + 1);
    if (/\s/.test(query) || query.length > MAX_QUERY) return null;
    return { start, query };
}

/** 候選成員：不含自己、名字含查詢字（不分大小寫），前綴符合的排前面 */
function mentionCandidates(members, query, myId, limit = MAX_CANDIDATES) {
    const q = String(query || '').toLowerCase();
    return (members || [])
        .filter((m) => m.user_id !== myId)
        .map((m) => ({ user_id: m.user_id, name: m.display_name || m.username || m.user_id }))
        .filter((m) => m.name.toLowerCase().includes(q))
        .sort((a, b) => Number(b.name.toLowerCase().startsWith(q)) - Number(a.name.toLowerCase().startsWith(q)))
        .slice(0, limit);
}

/** 把 value[start, caret) 的「@查詢」（半形或全形）換成半形「@名字 」；後面本來就是空白就不再補。回新的 value 與游標 */
function applyMention(value, caret, start, name) {
    const after = value.slice(caret);
    const spaced = /^\s/.test(after);
    const insert = `@${name}${spaced ? '' : ' '}`;
    return { value: value.slice(0, start) + insert + after, caret: start + insert.length + (spaced ? 1 : 0) };
}

/**
 * @param {{input: HTMLTextAreaElement, anchor: HTMLElement, isActive: () => boolean,
 *          getMembers: () => object[], getMyId: () => string, onApplied?: (input) => void}} opts
 *   anchor：選單掛在它裡面（要是 relative），貼在輸入框上方
 *   isActive：私訊與群組共用同一個輸入框，只有開著群組時作用
 *   onApplied：插入後（調整高度、字數）
 */
function createMentionPicker({ input, anchor, isActive, getMembers, getMyId, onApplied }) {
    const t = (key) => (window.I18n ? window.I18n.t(key) : key);
    let box = null;
    let items = [];
    let active = 0;
    let current = null; // { start, query }

    function close() {
        box?.remove();
        box = null;
        items = [];
        current = null;
    }

    function pick(index) {
        const item = items[index];
        if (!item || !current) return;
        const next = applyMention(input.value, input.selectionStart ?? input.value.length, current.start, item.name);
        input.value = next.value;
        input.setSelectionRange?.(next.caret, next.caret);
        close();
        input.focus();
        onApplied?.(input);
    }

    function paint() {
        if (!box) {
            box = document.createElement('div');
            box.className =
                'group-mention-picker absolute left-3 right-3 bottom-full mb-1 z-30 max-h-60 overflow-y-auto bg-surface border border-borderSubtle rounded-2xl shadow-xl py-1';
            box.setAttribute('role', 'listbox');
            box.setAttribute('aria-label', t('groups.mentionPicker'));
            // 按下去不要讓輸入框失焦（失焦會關掉選單，click 就收不到）
            box.addEventListener('mousedown', (e) => e.preventDefault());
            box.addEventListener('click', (e) => {
                const btn = e.target.closest?.('[data-mention-index]');
                if (btn) pick(Number(btn.dataset.mentionIndex));
            });
            anchor.appendChild(box);
        }
        box.replaceChildren(
            ...items.map((m, i) => {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.dataset.mentionIndex = String(i);
                btn.setAttribute('role', 'option');
                btn.setAttribute('aria-selected', i === active ? 'true' : 'false');
                btn.className = `w-full flex items-center gap-2 px-3 py-2 text-left text-sm text-textMain hover:bg-surfaceHighlight ${i === active ? 'bg-surfaceHighlight' : ''}`;
                const avatar = document.createElement('span');
                avatar.className = `w-7 h-7 rounded-full ${avatarColorClass(m.user_id)} flex items-center justify-center text-[11px] font-bold shrink-0`;
                avatar.textContent = avatarInitial(m.name);
                const name = document.createElement('span');
                name.className = 'truncate';
                name.textContent = m.name;
                btn.append(avatar, name);
                return btn;
            })
        );
        box.children[active]?.scrollIntoView?.({ block: 'nearest' });
    }

    function update() {
        if (!isActive()) return close();
        const q = mentionQuery(input.value, input.selectionStart ?? input.value.length);
        if (!q) return close();
        const sameQuery = current && current.start === q.start && current.query === q.query;
        current = q;
        if (sameQuery && box) return; // ↑↓ 的 keyup 也會進來：同一個查詢不要把選取重設回第一個
        items = mentionCandidates(getMembers(), q.query, getMyId());
        active = 0;
        if (!items.length) {
            box?.remove();
            box = null;
            return;
        }
        paint();
    }

    /** 回 true＝這個鍵選單用掉了（呼叫端不要再當送出） */
    function handleKeydown(e) {
        if (!box || !items.length) return false;
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            active = (active + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length;
            paint();
            return true;
        }
        if ((e.key === 'Enter' && !e.shiftKey) || e.key === 'Tab') {
            e.preventDefault();
            pick(active);
            return true;
        }
        if (e.key === 'Escape') {
            e.preventDefault();
            close();
            return true;
        }
        return false;
    }

    input.addEventListener('input', update);
    input.addEventListener('click', update);
    input.addEventListener('keyup', (e) => {
        if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) update();
    });
    // 點選單時 mousedown 已經擋掉失焦；真的離開輸入框才關
    input.addEventListener('blur', () => setTimeout(() => document.activeElement !== input && close(), 150));

    return { update, close, handleKeydown };
}

export { applyMention, createMentionPicker, mentionCandidates, mentionQuery };
