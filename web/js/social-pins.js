/**
 * 社群對話列表的置頂與排序（Object.assign 進 SocialHub，方法裡的 this＝SocialHub）。
 *
 * - 置頂／取消：桌機 hover 的圖釘鈕、右鍵選單；手機長按選單（list-row-menu.js）
 * - 排序：置頂 2 個以上時，置頂區上方有「調整順序」→ 排序模式：只列置頂的、每列右邊一個大把手
 *   （drag-reorder.js），按「完成」回到列表。分類（私訊／群組）裡排只動那一類，其他類的位置不變
 */

import { avatarColorClass, avatarInitial } from './avatar.js';
import { ChatPinsAPI, mergeSubsetOrder, parsePinKey, pinKey } from './chat-pins.js';
import { enableDragReorder } from './drag-reorder.js';
import { bindRowMenu, openMenuAt } from './list-row-menu.js';
import { escapeMessageAttr } from './messages.js';

const t = (key, fallback) => {
    const text = window.I18n ? window.I18n.t(key) : '';
    return text && text !== key ? text : fallback || key;
};
const esc = escapeMessageAttr;

function toast(text, type = 'info') {
    if (typeof window.showToast === 'function') window.showToast(text, type);
}

function pinErrorText(err) {
    if (err?.message === 'pin_limit_reached') return t('friends.pin.limit', 'You can pin up to 10 chats');
    return t('friends.pin.failed', "Couldn't update pinned chats");
}

const ChatOrderAPI = {
    save: (keys) => AppAPI.put('/api/chat-order', { items: keys.map(parsePinKey) }),
};

const SocialPins = {
    convReorder: false, // 排序模式

    /** 全部置頂（不分類），照順序：key 陣列 */
    allPinKeys: function () {
        const data = this._convData;
        if (!data) return [];
        return [
            ...data.conversations.filter((c) => c.pin_position != null).map((c) => ({ key: pinKey('dm', c.id), pos: c.pin_position })),
            ...data.groups.filter((g) => g.pin_position != null).map((g) => ({ key: pinKey('group', g.id), pos: g.pin_position })),
        ]
            .sort((a, b) => a.pos - b.pos)
            .map((x) => x.key);
    },

    /** 後端回的完整置頂清單 → 寫回手上的資料（不用整個重抓就能重畫） */
    applyPins: function (pins) {
        const data = this._convData;
        if (!data || !Array.isArray(pins)) return;
        const pos = new Map(pins.map((p) => [pinKey(p.kind, p.id), p.position]));
        data.conversations.forEach((c) => (c.pin_position = pos.get(pinKey('dm', c.id)) ?? null));
        data.groups.forEach((g) => (g.pin_position = pos.get(pinKey('group', g.id)) ?? null));
    },

    togglePinFromList: async function (key) {
        const { kind, id } = parsePinKey(key);
        const pinned = this.allPinKeys().includes(key);
        this._convSeq += 1; // 還在路上的列表請求是改之前的置頂狀態：回來了也不能蓋掉
        try {
            const res = pinned ? await ChatPinsAPI.unpin(kind, id) : await ChatPinsAPI.pin(kind, id);
            this.applyPins(res?.pins);
            if (this.convReorder && this.allPinKeys().length < 2) this.convReorder = false;
            this.renderConvList();
        } catch (err) {
            toast(pinErrorText(err), 'error');
        }
    },

    /** 搜尋框旁的 ⇅：排序方式（依最新訊息／自訂順序）＋調整順序 */
    openSortMenu: function (anchor) {
        const items = [
            { key: 'recent', icon: 'clock', label: t('friends.order.recent', 'Newest first'), checked: this.convSort !== 'custom' },
            { key: 'custom', icon: 'list-ordered', label: t('friends.order.custom', 'Custom order'), checked: this.convSort === 'custom' },
            { key: 'reorder', icon: 'arrow-up-down', label: t('friends.pin.reorder', 'Reorder'), separatorBefore: true },
        ];
        openMenuAt(anchor, items, (key) => {
            if (key === 'reorder') this.startConvReorder();
            else this.setConvSort(key);
        });
    },

    startConvReorder: function () {
        // 能拖的東西不到 2 個（剛開始用、只有一段對話）：說明怎麼置頂，不進空的排序模式
        const data = this._convData;
        const pinnedCount = this.allPinKeys().length;
        const otherCount = data ? data.conversations.length + data.groups.length - pinnedCount : 0;
        if (pinnedCount < 2 && otherCount < 2) {
            toast(t('friends.pin.howTo', 'Long-press a chat (right-click on desktop) and choose Pin. With 2 or more pinned, you can drag them into order.'), 'info');
            return;
        }
        if (this.searchQuery) this.clearSearch?.();
        this.convReorder = true;
        const listEl = document.getElementById('social-conv-list');
        if (listEl) listEl.scrollTop = 0;
        this.renderConvList();
    },

    finishConvReorder: function () {
        this.convReorder = false;
        this.renderConvList();
    },

    /** 拖完放手：畫面已經換好位置，送出新順序；失敗就重抓回伺服器的順序 */
    saveConvOrder: async function (subsetKeys) {
        const order = mergeSubsetOrder(this.allPinKeys(), subsetKeys);
        // 還在路上的列表請求帶的是舊順序（剛進頁面、來訊時常有）：回來了也不能把畫面蓋回去
        this._convSeq += 1;
        try {
            const res = await ChatPinsAPI.order(order);
            this.applyPins(res?.pins);
            if (this._convRenderPending) this.renderConvList(); // 拖的時候有來訊：現在補畫
        } catch (err) {
            toast(pinErrorText(err), 'error');
            this.loadConversations();
        }
    },

    /**
     * 排序模式：置頂、其他對話各一區（各自拖，置頂永遠在上面）。依最新訊息時拖「其他對話」會自動改成
     * 自訂順序（saveCustomOrder），所以那區上面註明一句
     */
    reorderModeHtml: function (pinnedItems, otherItems) {
        const label = (text) => `<h3 class="px-4 pt-3 pb-1.5 text-[11px] font-semibold text-textMuted">${esc(text)}</h3>`;
        let html = `<div class="sticky top-0 z-[2] flex items-center gap-2 px-4 py-2.5 border-b border-borderSubtle bg-surface">
                        <i data-lucide="grip-vertical" class="w-4 h-4 text-textMuted shrink-0"></i>
                        <p class="flex-1 min-w-0 text-xs text-textMuted truncate">${esc(t('friends.pin.reorderHint', 'Drag the handles to reorder'))}</p>
                        <button type="button" data-click="SocialHub.finishConvReorder" class="h-8 px-4 rounded-full text-xs font-bold bg-primary text-background shrink-0">${esc(t('common.done', 'Done'))}</button>
                    </div>`;
        if (pinnedItems.length) {
            html += label(t('friends.pin.section', 'Pinned'));
            html += `<div data-pinned-list>${pinnedItems.map((item) => this.reorderRowHtml(item)).join('')}</div>`;
        }
        if (otherItems.length) {
            html += label(t('friends.order.others', 'Other chats'));
            if (this.convSort !== 'custom') {
                html += `<p class="px-4 pb-2 text-[11px] text-textMuted">${esc(t('friends.order.dragSwitchesHint', 'Dragging here switches to custom order'))}</p>`;
            }
            html += `<div data-other-list>${otherItems.map((item) => this.reorderRowHtml(item)).join('')}</div>`;
        }
        return html;
    },

    /** 一般對話拖完：送新順序（後端把這幾個放回它們原本佔的位置）；依最新訊息時順便改成自訂順序 */
    saveCustomOrder: async function (keys) {
        if (this.convSort !== 'custom') {
            this.setConvSort('custom', { reload: false });
            toast(t('friends.order.switchedToCustom', 'Switched to custom order'), 'info');
        }
        this._convSeq += 1; // 還在路上的列表請求是舊順序
        try {
            await ChatOrderAPI.save(keys);
        } catch (err) {
            toast(t('friends.order.failed', "Couldn't save the order"), 'error');
        }
        this.loadConversations(); // 拿後端排好的位置（之後來訊也照這個順序）
    },

    /** 置頂區：2 個以上才有標題列（「調整順序」） */
    pinnedSectionHtml: function (pinnedItems) {
        if (!pinnedItems.length) return '';
        const header =
            pinnedItems.length >= 2
                ? `<div class="flex items-center justify-between px-4 pt-3 pb-1">
                        <h3 class="text-[11px] font-semibold text-textMuted">${esc(t('friends.pin.section', 'Pinned'))} · ${pinnedItems.length}</h3>
                        <button type="button" data-click="SocialHub.startConvReorder" class="text-[11px] font-medium text-primary hover:underline">${esc(t('friends.pin.reorder', 'Reorder'))}</button>
                    </div>`
                : '';
        // 置頂區跟一般對話之間一條淺色分隔帶：看得出置頂到哪裡為止
        return `${header}<div data-pinned-list>${pinnedItems.map((item) => item.html()).join('')}</div><div class="h-1.5 bg-background border-b border-borderSubtle" aria-hidden="true"></div>`;
    },

    /** 排序模式的一列：頭像＋名字＋右邊 44px 的把手（整列不能點開對話） */
    reorderRowHtml: function (item) {
        const { kind, raw } = item;
        const name =
            kind === 'group' ? raw.name : raw.other_display_name || raw.other_username || raw.username || 'Unknown';
        const avatar =
            kind === 'group'
                ? '<div class="w-9 h-9 rounded-full bg-surfaceHighlight text-textMuted flex items-center justify-center shrink-0"><i data-lucide="users" class="w-4 h-4"></i></div>'
                : `<div class="w-9 h-9 rounded-full ${avatarColorClass(raw.other_user_id)} flex items-center justify-center text-sm font-bold shrink-0">${esc(avatarInitial(name))}</div>`;
        const label = `${t('friends.pin.dragHandle', 'Move')} ${name}`;
        return `<div data-reorder-item="${item.key}" class="flex items-center gap-3 pl-4 pr-1 py-2 border-b border-borderSubtle bg-surface">
                    ${avatar}
                    <span class="flex-1 min-w-0 truncate text-sm font-bold text-textMain">${esc(name)}</span>
                    <button type="button" data-reorder-handle aria-label="${esc(label)}" class="reorder-handle w-11 h-11 shrink-0 rounded-lg flex items-center justify-center text-textMuted hover:text-textMain hover:bg-surfaceHighlight">
                        <i data-lucide="grip-vertical" class="w-5 h-5"></i>
                    </button>
                </div>`;
    },

    /** 列表容器：拖拉排序＋長按／右鍵選單（各綁一次） */
    bindConvListPins: function (listEl) {
        // 拖的是哪一區：置頂（chat_pins）或其他對話（chat_order）
        enableDragReorder(listEl, {
            onReorder: (keys) =>
                this.allPinKeys().includes(keys[0]) ? this.saveConvOrder(keys) : this.saveCustomOrder(keys),
        });
        document.getElementById('social-reorder-btn')?.classList.toggle('text-primary', this.convSort === 'custom');
        bindRowMenu(listEl, {
            rowSelector: '[data-pin-key]',
            items: (row) => {
                if (this.convReorder) return null;
                const pinned = row.dataset.pinned === '1';
                const items = [
                    pinned
                        ? { key: 'unpin', icon: 'pin-off', label: t('friends.pin.unpin', 'Unpin') }
                        : { key: 'pin', icon: 'pin', label: t('friends.pin.pin', 'Pin to top') },
                ];
                if (pinned && this.allPinKeys().length >= 2) {
                    items.push({ key: 'reorder', icon: 'arrow-up-down', label: t('friends.pin.reorder', 'Reorder') });
                }
                if (row.dataset.pinKey.startsWith('dm:')) {
                    items.push({ key: 'delete', icon: 'trash-2', label: t('friends.deleteConversation', 'Delete chat'), danger: true });
                }
                return items;
            },
            onPick: (row, key) => {
                const pin = row.dataset.pinKey;
                if (key === 'pin' || key === 'unpin') this.togglePinFromList(pin);
                else if (key === 'reorder') this.startConvReorder();
                else if (key === 'delete') this.deleteConversation(parsePinKey(pin).id);
            },
        });
    },
};

export { SocialPins };
