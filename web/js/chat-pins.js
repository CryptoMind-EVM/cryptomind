/**
 * 社群對話置頂（私訊＋群組共用一組順序，後端 chat_pins，最多 10 個）：API 與列表共用的小零件。
 * 置頂的排在最上面、照自己排的順序；其他照最後動態（Telegram 式）。
 */

const t = (key, fallback) => {
    const text = window.I18n ? window.I18n.t(key) : '';
    return text && text !== key ? text : fallback;
};

const ChatPinsAPI = {
    pin: (kind, id) => AppAPI.put(`/api/chat-pins/${kind}/${Number(id)}`),
    unpin: (kind, id) => AppAPI.delete(`/api/chat-pins/${kind}/${Number(id)}`),
    order: (keys) => AppAPI.put('/api/chat-pins/order', { items: keys.map(parsePinKey) }),
};

/** 列上的識別：dm:12、group:7 */
function pinKey(kind, id) {
    return `${kind}:${Number(id)}`;
}

function parsePinKey(key) {
    const [kind, id] = String(key).split(':');
    return { kind, id: Number(id) };
}

/**
 * 只排了其中一部分（例如在「私訊」分類裡排）：那幾個依新順序填回它們原本佔的位置，
 * 其他（群組）的位置不動。回傳完整的新順序
 */
function mergeSubsetOrder(allKeys, subsetKeys) {
    const subset = new Set(subsetKeys);
    let k = 0;
    return allKeys.map((key) => (subset.has(key) ? subsetKeys[k++] : key));
}

/** 名字旁的小圖釘（置頂中） */
function pinIndicatorHtml() {
    return `<i data-lucide="pin" class="w-3 h-3 shrink-0 text-textMuted rotate-45" aria-label="${t('friends.pin.pinned', 'Pinned')}"></i>`;
}

/** 桌機 hover 出現的置頂鈕（刪除鈕左邊）；手機沒有 hover，改長按選單 */
function pinHoverButtonHtml(key, pinned, rightClass) {
    const label = pinned ? t('friends.pin.unpin', 'Unpin') : t('friends.pin.pin', 'Pin to top');
    return `<button type="button" data-click="SocialHub.togglePinFromList" data-click-arg="${key}" aria-label="${label}" title="${label}"
                class="absolute ${rightClass} bottom-3 p-1.5 ${pinned ? 'text-primary' : 'text-textMuted/40 hover:text-primary'} opacity-0 group-hover:opacity-100 focus:opacity-100 transition-all duration-200">
                <i data-lucide="${pinned ? 'pin-off' : 'pin'}" class="w-3.5 h-3.5"></i>
            </button>`;
}

export { ChatPinsAPI, mergeSubsetOrder, parsePinKey, pinHoverButtonHtml, pinIndicatorHtml, pinKey };
