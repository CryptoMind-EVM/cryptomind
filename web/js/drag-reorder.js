/**
 * 拖拉排序（社群的置頂對話、聊天側欄的收藏對話共用）：按住列上的把手（[data-reorder-handle]）
 * 上下拖，放開時把列移到新位置並回呼新順序。只在同一個父層裡 [data-reorder-item] 的列之間換。
 *
 * - 容器綁一次就好（事件委派），列表重畫不用重綁；再呼叫一次只更新設定
 * - 觸控：把手 touch-action:none（styles.css），按住把手不會捲動；手指放開後同一手勢的 click 吞掉，
 *   不會順便打開對話（click-delegator 的 tap 也取消）
 * - 鍵盤：把手是 button，聚焦時 ↑／↓ 移一格
 */

const configs = new WeakMap();

function itemsOf(item) {
    return [...item.parentElement.children].filter((el) => el.matches('[data-reorder-item]'));
}

function orderOf(parent) {
    return [...parent.children].filter((el) => el.matches('[data-reorder-item]')).map((el) => el.dataset.reorderItem);
}

/**
 * 拖到哪一格：拿被拖那列的中心點跟其他列（拖之前的位置）比。用 <=／>=：拖拉被夾在第一列與
 * 最後一列之間，等高的列拖到頂時中心點剛好落在第一列的中線上——用 < 的話永遠換不到第一格
 */
function targetIndex(rects, from, centerY) {
    for (let i = 0; i < from; i += 1) {
        if (centerY <= rects[i].top + rects[i].height / 2) return i;
    }
    for (let i = rects.length - 1; i > from; i -= 1) {
        if (centerY >= rects[i].top + rects[i].height / 2) return i;
    }
    return from;
}

function moveItem(item, items, to) {
    const from = items.indexOf(item);
    if (to === from || to < 0 || to >= items.length) return false;
    const ref = to > from ? items[to].nextSibling : items[to];
    item.parentElement.insertBefore(item, ref);
    return true;
}

/**
 * @param {HTMLElement} container 列表容器（列在它裡面，可以不是直接子層）
 * @param {{ onReorder: (ids: string[]) => void }} opts ids＝那一組列的 data-reorder-item，新順序
 */
function enableDragReorder(container, opts) {
    if (!container) return;
    const first = !configs.has(container);
    configs.set(container, opts);
    if (!first) return;

    let drag = null;
    const done = (parent) => configs.get(container).onReorder(orderOf(parent));

    container.addEventListener('pointerdown', (e) => {
        const handle = e.target.closest?.('[data-reorder-handle]');
        if (!handle || drag || (e.pointerType === 'mouse' && e.button !== 0)) return;
        const item = handle.closest('[data-reorder-item]');
        if (!item || !container.contains(item)) return;
        const items = itemsOf(item);
        if (items.length < 2) return;
        e.preventDefault();
        window.cancelTouchTap?.(); // click-delegator 不要在放手時把這下當成點擊
        try {
            handle.setPointerCapture(e.pointerId);
        } catch (_e) {
            // 合成事件沒有 pointer capture：照樣用 container 收 move
        }
        const rects = items.map((el) => el.getBoundingClientRect());
        const from = items.indexOf(item);
        // 每格的間距（含列之間的空隙）：用相鄰兩列的頂端差
        const step =
            from < rects.length - 1 ? rects[from + 1].top - rects[from].top : rects[from].top - rects[from - 1].top;
        drag = { id: e.pointerId, item, items, rects, from, to: from, startY: e.clientY, step };
        item.classList.add('reorder-dragging');
        container.classList.add('reorder-active');
    });

    container.addEventListener('pointermove', (e) => {
        if (!drag || e.pointerId !== drag.id) return;
        const { rects, from, items, item, step } = drag;
        const dy = Math.max(
            rects[0].top - rects[from].top,
            Math.min(rects[rects.length - 1].top - rects[from].top, e.clientY - drag.startY)
        );
        item.style.transform = `translateY(${dy}px)`;
        const to = targetIndex(rects, from, rects[from].top + rects[from].height / 2 + dy);
        drag.to = to;
        items.forEach((el, i) => {
            if (el === item) return;
            let shift = 0;
            if (from < to && i > from && i <= to) shift = -step;
            else if (from > to && i >= to && i < from) shift = step;
            el.style.transform = shift ? `translateY(${shift}px)` : '';
        });
    });

    const end = (e, cancelled) => {
        if (!drag || e.pointerId !== drag.id) return;
        const { item, items, to } = drag;
        drag = null;
        items.forEach((el) => (el.style.transform = ''));
        item.classList.remove('reorder-dragging');
        container.classList.remove('reorder-active');
        // 拖到一半列表重畫了（來訊、別的裝置改了置頂）：手上的列已經不在畫面上，這次作廢
        if (cancelled || !item.isConnected || !items.every((el) => el.isConnected)) return;
        if (moveItem(item, items, to)) done(item.parentElement);
    };
    container.addEventListener('pointerup', (e) => end(e, false));
    container.addEventListener('pointercancel', (e) => end(e, true));

    // 把手上的 click（拖完放手、或只是點一下）不往上傳：列本身是「打開對話」
    container.addEventListener(
        'click',
        (e) => {
            if (e.detail !== 0 && e.target.closest?.('[data-reorder-handle]')) {
                e.stopPropagation();
                e.preventDefault();
            }
        },
        true
    );

    container.addEventListener('keydown', (e) => {
        if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
        const handle = e.target.closest?.('[data-reorder-handle]');
        const item = handle?.closest('[data-reorder-item]');
        if (!item || !container.contains(item)) return;
        e.preventDefault();
        const items = itemsOf(item);
        const to = items.indexOf(item) + (e.key === 'ArrowUp' ? -1 : 1);
        if (!moveItem(item, items, to)) return;
        handle.focus(); // 搬動節點會失焦
        done(item.parentElement);
    });
}

export { enableDragReorder, targetIndex };
