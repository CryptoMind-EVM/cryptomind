/**
 * 列表列的選單（社群對話列表的「置頂／取消置頂」）：桌機右鍵、手機長按。
 *
 * 長按的規則跟訊息長按選單一樣：按住 500ms、手指移動超過 10px 就不算（是在捲動）。
 * 開了之後：
 * - click-delegator 那下 tap 取消，放手時不會順便打開那個對話
 * - 選單是手指還按著時冒出來的，放手那下的原生 click 可能剛好落在選單項目上——
 *   要有一次「在選單上按下」之後，項目才接受點擊（鍵盤的 click detail===0 直接放行）
 */

const LONG_PRESS_MS = 500;
const SLOP_PX = 10;
const EDGE = 8;

let current = null;

function closeRowMenu() {
    if (!current) return;
    current.cleanup();
    current = null;
}

function openMenu({ x, y, row, items, onPick, fromTouch }) {
    closeRowMenu();
    const menu = document.createElement('div');
    menu.setAttribute('role', 'menu');
    menu.className =
        'list-row-menu fixed z-[95] min-w-[168px] py-1 rounded-xl bg-surface border border-borderSubtle shadow-2xl animate-fade-in';
    for (const item of items) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.setAttribute('role', 'menuitem');
        btn.dataset.rowMenuKey = item.key;
        btn.className = `w-full flex items-center gap-2.5 px-3.5 py-2.5 text-sm text-left hover:bg-surfaceHighlight transition ${item.danger ? 'text-danger' : 'text-textMain'}`;
        btn.innerHTML = `<i data-lucide="${item.icon}" class="w-4 h-4 shrink-0"></i><span class="flex-1"></span>`;
        btn.querySelector('span').textContent = item.label;
        if (item.checked !== undefined) {
            // 單選項（例如排序方式）：選中的右邊打勾
            btn.setAttribute('role', 'menuitemradio');
            btn.setAttribute('aria-checked', String(!!item.checked));
            if (item.checked) btn.insertAdjacentHTML('beforeend', '<i data-lucide="check" class="w-4 h-4 shrink-0 text-primary"></i>');
        }
        if (item.separatorBefore) btn.classList.add('border-t', 'border-borderSubtle');
        menu.append(btn);
    }
    document.body.append(menu);
    window.AppUtils?.refreshIcons?.();

    // 位置：滑鼠在游標旁；觸控在那一列底下（放不下就往上），左右不出畫面
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const { width, height } = menu.getBoundingClientRect();
    let left = x;
    let top = y;
    if (fromTouch && row) {
        const r = row.getBoundingClientRect();
        left = r.left + r.width / 2 - width / 2;
        top = r.bottom + 4 + height <= vh - EDGE ? r.bottom + 4 : r.top - 4 - height;
    }
    menu.style.left = `${Math.max(EDGE, Math.min(left, vw - width - EDGE))}px`;
    menu.style.top = `${Math.max(EDGE, Math.min(top, vh - height - EDGE))}px`;

    let armed = !fromTouch; // 右鍵開的：之後的點擊都是新的一下
    const onMenuDown = () => (armed = true);
    const onMenuClick = (e) => {
        const btn = e.target.closest('[data-row-menu-key]');
        if (!btn || (!armed && e.detail !== 0)) return;
        const key = btn.dataset.rowMenuKey;
        closeRowMenu();
        onPick(key);
    };
    const onOutside = (e) => {
        if (!menu.contains(e.target)) closeRowMenu();
    };
    const onKey = (e) => {
        if (e.key === 'Escape') closeRowMenu();
    };
    menu.addEventListener('pointerdown', onMenuDown);
    menu.addEventListener('click', onMenuClick);
    // 下一輪才開始聽外面的按下：開選單的那一下不能把它自己關掉
    const timer = setTimeout(() => {
        document.addEventListener('pointerdown', onOutside, true);
        window.addEventListener('scroll', closeRowMenu, true);
        window.addEventListener('resize', closeRowMenu);
    }, 0);
    document.addEventListener('keydown', onKey);
    current = {
        cleanup() {
            clearTimeout(timer);
            document.removeEventListener('pointerdown', onOutside, true);
            window.removeEventListener('scroll', closeRowMenu, true);
            window.removeEventListener('resize', closeRowMenu);
            document.removeEventListener('keydown', onKey);
            menu.remove();
        },
    };
    menu.querySelector('button')?.focus({ preventScroll: true });
}

/**
 * @param {HTMLElement} container 列表容器（綁一次，列表重畫不用重綁）
 * @param {{ rowSelector: string, items: (row: HTMLElement) => Array<{key,label,icon,danger?}>|null,
 *           onPick: (row: HTMLElement, key: string) => void }} opts
 */
function bindRowMenu(container, opts) {
    if (!container || container.dataset.rowMenuBound) return;
    container.dataset.rowMenuBound = '1';
    let press = null;
    let lastPointerType = 'mouse';

    const open = (row, at, fromTouch) => {
        const items = opts.items(row);
        if (!items || !items.length) return;
        openMenu({ ...at, row, items, fromTouch, onPick: (key) => opts.onPick(row, key) });
    };
    const cancelPress = () => {
        if (press) clearTimeout(press.timer);
        press = null;
    };

    container.addEventListener('pointerdown', (e) => {
        lastPointerType = e.pointerType || 'mouse';
        if (e.pointerType !== 'touch' || e.isPrimary === false) return;
        const row = e.target.closest?.(opts.rowSelector);
        if (!row || !container.contains(row) || e.target.closest('[data-reorder-handle]')) return;
        cancelPress();
        press = {
            x: e.clientX,
            y: e.clientY,
            id: e.pointerId,
            timer: setTimeout(() => {
                press = null;
                window.cancelTouchTap?.();
                navigator.vibrate?.(10);
                open(row, {}, true);
                // 放手那下的原生 click：不讓 click-delegator 再派發（落在列上會打開對話）
                const swallow = () => {
                    window.__touchGestureClickUntil = Date.now() + 700;
                    document.removeEventListener('pointerup', swallow, true);
                };
                document.addEventListener('pointerup', swallow, true);
            }, LONG_PRESS_MS),
        };
    });
    container.addEventListener('pointermove', (e) => {
        if (press && e.pointerId === press.id && (Math.abs(e.clientX - press.x) > SLOP_PX || Math.abs(e.clientY - press.y) > SLOP_PX)) {
            cancelPress();
        }
    });
    container.addEventListener('pointerup', cancelPress);
    container.addEventListener('pointercancel', cancelPress);
    container.addEventListener('scroll', cancelPress, { passive: true });
    container.addEventListener('contextmenu', (e) => {
        const row = e.target.closest?.(opts.rowSelector);
        if (!row || !container.contains(row)) return;
        e.preventDefault(); // 觸控的長按（Android 也會發 contextmenu）交給上面的計時器
        if (lastPointerType === 'touch') return;
        open(row, { x: e.clientX, y: e.clientY }, false);
    });
}

/** 從按鈕打開的選單（例如社群的排序鈕）：貼在按鈕下方、右緣對齊 */
function openMenuAt(anchor, items, onPick) {
    const r = anchor.getBoundingClientRect();
    openMenu({ x: r.right - 168, y: r.bottom + 4, row: null, items, fromTouch: false, onPick });
}

export { bindRowMenu, closeRowMenu, openMenuAt };
