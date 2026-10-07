/* Shared UI shell helpers for toast rendering and fixed-stack spacing. */

const DEFAULT_TOAST_DURATION = 3000;
const ROOT = document.documentElement;

function escapeText(value) {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

/* 底部佔位的量測。
   刻意不用「各元素高度相加」:元素之間的間距、互相重疊的部分都會讓相加的結果
   與畫面不符,表現為內容被切掉或多出一條空白。改量「視窗底部到最上緣的距離」。
   2026-09-24 手機底部導覽列拔除:固定的底部 chrome 只剩聊天輸入列。 */
const BOTTOM_CHROME_SELECTOR = '[data-shell-fixed-input]';
const BOTTOM_LAYER_SELECTOR = '[data-shell-bottom-layer]';

/* 底部佔位的基準高度。
   必須是 layout viewport:下面量的是 getBoundingClientRect(),那是 layout viewport
   座標。混用 visualViewport.height(實際可見高度)會讓兩套座標對不上 —— 鍵盤打開或
   iOS 底部工具列出現時,量出來的底部佔位少掉整個鍵盤/工具列的高度(實測要 399px
   卻只量到 137px),捲動區留白不夠,最後幾行就滑到輸入框底下。
   鍵盤造成的位移由 chat-state.js 的 --chat-keyboard-offset 處理(它才需要
   visualViewport),不是靠縮這個基準。 */
function viewportHeight() {
    return document.documentElement.clientHeight || window.innerHeight;
}

function isRendered(element) {
    if (!element) {
        return false;
    }
    const computedStyle = window.getComputedStyle(element);
    return !(
        computedStyle.display === 'none' ||
        computedStyle.visibility === 'hidden' ||
        computedStyle.opacity === '0'
    );
}

/* 回傳「視窗底部往上被這些元素佔掉多少」。沒有任何元素時回傳 0。 */
function measureBottomUnion(selector) {
    const height = viewportHeight();
    // 視窗尚未有尺寸(隱藏 iframe、剛開的分頁)時量出來的值沒有意義,不要寫進變數
    if (!height) {
        return null;
    }
    let topEdge = height;

    Array.from(document.querySelectorAll(selector)).forEach(function (element) {
        if (!isRendered(element)) {
            return;
        }

        const rect = element.getBoundingClientRect();
        if (rect.height === 0) {
            return;
        }
        // 貼在上半部的不屬於底部佔位
        if (rect.bottom < height / 2) {
            return;
        }
        topEdge = Math.min(topEdge, rect.top);
    });

    return Math.max(0, height - topEdge);
}

function applyScrollPadding() {
    document.querySelectorAll('[data-shell-scroll]').forEach(function (element) {
        const extra = element.getAttribute('data-shell-scroll-extra') || '0px';
        element.style.paddingBottom = 'calc(var(--shell-content-clearance, 0px) + ' + extra + ')';
    });
}

/* 跟 chat-state.js 的貼底門檻同一個值:差這麼多以內都算「使用者在看最新的」。 */
const STICK_THRESHOLD_PX = 80;

/* 寫變數之前先記下哪些捲動區貼在底部。
   變數一寫進去,padding 的 calc 立刻重算、scrollHeight 跟著變大,之後再問
   「原本在不在底部」永遠得到否 —— 所以這件事只能在寫入之前做。 */
function captureAnchoredScrollers() {
    return Array.from(document.querySelectorAll('[data-shell-scroll]')).filter(function (element) {
        // chat-state.js 放掉貼底的捲動區（訪客首頁 hero 要從頂端看）不錨定：hero 剛放進去時
        // 內容還矮、會被判定「在底部」，提示條一出現就被這裡拉到底（2026-09-27 正式站實測）
        if (element.dataset && element.dataset.stickReleased === '1') {
            return false;
        }
        return (
            element.scrollHeight - element.scrollTop - element.clientHeight <= STICK_THRESHOLD_PX
        );
    });
}

/* 底部佔位變大時(鍵盤打開、輸入框長高、提示條出現),只加留白是不夠的:
   scrollTop 不動,原本貼在底部的內容會被擠到輸入框後面。聊天介面的慣例是底部錨定 ——
   本來在底部就留在底部;使用者自己捲上去看歷史時(超過門檻)不要動他。 */
function restoreAnchoredScrollers(anchored) {
    anchored.forEach(function (element) {
        element.scrollTop = element.scrollHeight;
    });
}

let lastWritten = null;

/* 量一輪並寫進變數,回報這輪的數字跟上一輪一不一樣。 */
function measureAndWrite() {
    // 固定的底部 chrome(輸入列):其他浮層靠這個值定位
    const chromeOffset = measureBottomUnion(BOTTOM_CHROME_SELECTOR);
    if (chromeOffset === null) {
        // 量不到就保留上一次的值,讓 CSS fallback 繼續生效,不要覆寫成 0
        return 'unmeasurable';
    }
    // 再加上臨時浮層(例如無金鑰提示條):捲動內容要讓開的是這個值
    const contentClearance = Math.max(
        chromeOffset,
        measureBottomUnion(BOTTOM_CHROME_SELECTOR + ', ' + BOTTOM_LAYER_SELECTOR) || 0
    );

    const anchored = captureAnchoredScrollers();
    const previous = lastWritten;
    lastWritten = {
        chromeOffset: chromeOffset,
        contentClearance: contentClearance,
    };

    ROOT.style.setProperty('--shell-fixed-stack-offset', chromeOffset + 'px');
    ROOT.style.setProperty('--shell-content-clearance', contentClearance + 'px');
    // 量到的高度已含各元素自身的 safe-area padding,這裡不再重複加
    ROOT.style.setProperty('--shell-toast-bottom', 'calc(' + contentClearance + 'px + 1rem)');
    applyScrollPadding();

    if (!previous) {
        return 'changed';
    }
    // 只有底部佔位「變大」才會把內容擠到輸入框後面,變小或沒變就別碰使用者的捲動位置
    if (contentClearance > previous.contentClearance) {
        restoreAnchoredScrollers(anchored);
    }
    return previous.chromeOffset === chromeOffset &&
        previous.contentClearance === contentClearance
        ? 'stable'
        : 'changed';
}

/* 量 → 寫 → 再量,直到數字不再變。
   為什麼不能只量一次:寫進變數會觸發 padding 的 calc 重算,版面(進而底部佔位)可能
   再變一次,單次量到的是「上一輪版面」(底部導覽列還在時實測差過 59px,表現為輸入框
   壓在最後一則訊息上)—— 而 ResizeObserver 只在元素尺寸變化時觸發,量測本身造成的
   位移不會有人補量。 */
const MAX_SYNC_PASSES = 4;

function syncLayout() {
    for (let pass = 0; pass < MAX_SYNC_PASSES; pass += 1) {
        if (measureAndWrite() !== 'changed') {
            return;
        }
    }
}

function ensureToastContainer() {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.setAttribute('role', 'status');
        container.setAttribute('aria-live', 'polite');
        container.setAttribute('aria-atomic', 'true');
        container.className = 'fixed z-[110] flex flex-col gap-3 pointer-events-none bottom-4 left-4 right-4 items-center md:bottom-auto md:top-24 md:left-auto md:right-4 md:items-end';
        document.body.appendChild(container);
    }
    return container;
}

// Toast 去重：相同訊息 + 類型在短時間內重複呼叫時，不堆疊新元素，而是
// 重置現有 toast 的計時器（讓它再顯示一輪時長）。
//
// 根因（線上 #特斯拉設定案例）：手機上 pointerdown 委派器會合成一個 click，
// 加上原生 click，saveLLMKey 的驗證失敗 showToast 兩次，畫面堆出兩個相同
// 「Please complete the API key test before saving.」toast。專案裡所有 toast
// 呼叫都不防重，在這裡統一去重治本（所有 toast 受惠）。
const _toastDedup = new Map(); // key -> { timeoutId }
const TOAST_DEDUP_INTERVAL = 1200; // ms，同訊息 1.2s 內視為重複

function showToast(message, type, duration) {
    const container = ensureToastContainer();
    const tone = type || 'info';
    const timeout = typeof duration === 'number' ? duration : DEFAULT_TOAST_DURATION;

    // 去重：相同 message + tone 在 toast 存活期間重複 → 不建新 toast，
    // 只重置現有的自動消失計時器（延長可見時間，但不堆疊）。
    const dedupKey = tone + '|' + (message || '');
    const existing = _toastDedup.get(dedupKey);
    if (existing) {
        if (existing.timeoutId) {
            window.clearTimeout(existing.timeoutId);
        }
        if (timeout > 0) {
            existing.timeoutId = window.setTimeout(function () {
                _toastDedup.delete(dedupKey);
            }, timeout);
        }
        return null; // 已有相同 toast 在顯示，不重複堆疊
    }

    const icons = {
        success: 'check-circle',
        error: 'x-circle',
        warning: 'alert-triangle',
        info: 'info',
    };

    const colors = {
        success: 'bg-success/20 border-success/30 text-success',
        error: 'bg-danger/20 border-danger/30 text-danger',
        warning: 'bg-yellow-500/20 border-yellow-500/30 text-yellow-400',
        info: 'bg-primary/20 border-primary/30 text-primary',
    };

    const toast = document.createElement('div');
    toast.className = 'pointer-events-auto flex w-full max-w-md items-center gap-3 rounded-2xl border px-4 py-3 backdrop-blur-xl shadow-xl animate-fade-in-up ' + (colors[tone] || colors.info);
    toast.innerHTML = [
        '<i data-lucide="' + (icons[tone] || icons.info) + '" class="w-5 h-5 flex-shrink-0"></i>',
        '<p class="text-sm leading-snug flex-1">' + escapeText(message || '') + '</p>',
        '<button type="button" class="text-current opacity-60 hover:opacity-100 transition" aria-label="Close toast" data-i18n="common.closeToast" data-i18n-attr="aria-label">',
        '<i data-lucide="x" class="w-4 h-4"></i>',
        '</button>',
    ].join('');

    const dismiss = function () {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(8px)';
        toast.style.transition = 'all 0.25s ease';
        // 清掉去重登錄：dismiss 後相同訊息可再次顯示（但短時間內的連續呼叫
        // 已被上方的 dedup 攔截，不會堆疊）。
        const entry = _toastDedup.get(dedupKey);
        if (entry && entry.timeoutId) {
            window.clearTimeout(entry.timeoutId);
        }
        _toastDedup.delete(dedupKey);
        window.setTimeout(function () {
            toast.remove();
        }, 250);
    };

    toast.querySelector('button').addEventListener('click', dismiss);
    container.appendChild(toast);

    if (window.lucide && typeof window.lucide.createIcons === 'function') {
        window.lucide.createIcons();
    }

    // 登錄去重條目：在 toast 存活期間，相同 message+tone 的呼叫會被攔截。
    // 用 dedup 區間（而非 toast 的 timeout）控制登錄存活，確保即使 toast
    // 顯示較久，連續重複呼叫也不會堆疊。
    const dedupEntry = { timeoutId: null };
    _toastDedup.set(dedupKey, dedupEntry);
    dedupEntry.timeoutId = window.setTimeout(function () {
        _toastDedup.delete(dedupKey);
    }, Math.max(timeout > 0 ? timeout : DEFAULT_TOAST_DURATION, TOAST_DEDUP_INTERVAL));

    if (timeout > 0) {
        window.setTimeout(dismiss, timeout);
    }

    return toast;
}

function dismissToast(toast) {
    if (!toast || typeof toast.remove !== 'function') {
        return;
    }

    toast.style.opacity = '0';
    toast.style.transform = 'translateY(8px)';
    toast.style.transition = 'all 0.25s ease';
    window.setTimeout(function () {
        toast.remove();
    }, 250);
}

function clearToasts() {
    const container = document.getElementById('toast-container');
    if (!container) {
        return;
    }

    Array.from(container.children).forEach(function (toast) {
        dismissToast(toast);
    });
}

function initialize() {
    const watched = BOTTOM_CHROME_SELECTOR + ', ' + BOTTOM_LAYER_SELECTOR;
    const resizeObserver =
        typeof window.ResizeObserver === 'function' ? new window.ResizeObserver(syncLayout) : null;
    const attributeObserver =
        typeof window.MutationObserver === 'function' ? new window.MutationObserver(syncLayout) : null;

    /* 重新掛 observer:底部浮層(無金鑰提示等)可能在初始化後才進 DOM,
       只在 initialize 當下抓一次會漏掉,量到的就是舊值。 */
    function observeAll() {
        document.querySelectorAll(watched).forEach(function (element) {
            if (element.dataset.shellObserved === '1') {
                return;
            }
            element.dataset.shellObserved = '1';
            if (resizeObserver) {
                resizeObserver.observe(element);
            }
            if (attributeObserver) {
                attributeObserver.observe(element, {
                    attributes: true,
                    attributeFilter: ['class', 'style', 'hidden'],
                });
            }
        });
    }

    function resync() {
        observeAll();
        syncLayout();
    }

    resync();

    if (attributeObserver) {
        // 新增/移除節點時把新的浮層納入觀察
        attributeObserver.observe(document.body, { childList: true, subtree: true });
    }

    const debounced = window.Utils ? window.Utils.debounce(resync, 100) : resync;

    // 字型載入完、圖片載入完都會改變高度,初始量測必須在那之後再補一次
    window.addEventListener('load', resync);
    if (document.fonts && typeof document.fonts.ready === 'object') {
        document.fonts.ready.then(resync).catch(function () {});
    }

    window.addEventListener('resize', debounced);
    window.addEventListener('orientationchange', debounced);

    // 手機軟鍵盤只會改 visualViewport,不會觸發 window resize
    if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', debounced);
        window.visualViewport.addEventListener('scroll', debounced);
    }
}

// ── 自訂 Dialog(取代原生 alert/confirm — 對齊設計系統)──
// 輕量 promise-based modal:showConfirmDialog(options) → Promise<boolean>,
// showInfoDialog(options) → Promise<void>。樣式用設計系統 token。

/**
 * 顯示自訂確認 dialog(取代原生 confirm)。
 * options: { title, message, confirmText, cancelText, danger, zIndex }
 *   zIndex：從比 z-[100] 更高的面板（例如 AI 助理抽屜 10000）開確認框時指定，不然會被蓋在後面。
 * 回 Promise<boolean> — true = 使用者按確認。
 */
function showConfirmDialog(options) {
    const opts = options || {};
    const confirmText = opts.confirmText || (window.I18n ? window.I18n.t('common.confirm') : 'Confirm');
    const cancelText = opts.cancelText || (window.I18n ? window.I18n.t('common.cancel') : 'Cancel');
    const danger = opts.danger === true;
    const confirmCls = danger
        ? 'bg-danger/10 text-danger hover:bg-danger/20'
        : 'bg-primary text-background hover:bg-primary/90';
    const icon = opts.icon || (danger ? 'alert-triangle' : 'help-circle');
    const iconCls = danger ? 'text-danger' : 'text-primary';

    const overlay = document.createElement('div');
    overlay.className = 'fixed inset-0 z-[100] bg-black/60 backdrop-blur-sm flex items-end md:items-center justify-center p-4 animate-fade-in overflow-y-auto';
    if (opts.zIndex) overlay.style.zIndex = String(opts.zIndex);
    const dialog = document.createElement('div');
    // max-h + flex：動態 message 過長時卡片內滾，不超出視窗（2026-08-21 盤查）
    dialog.className = 'w-full max-w-sm max-h-[80dvh] flex flex-col rounded-3xl bg-surface border border-borderSubtle shadow-2xl animate-fade-in-up overflow-hidden';
    dialog.innerHTML = [
        '<div class="flex items-start gap-3 p-6 pb-0 shrink-0">',
        '<div class="w-10 h-10 rounded-full bg-surfaceHighlight flex items-center justify-center shrink-0">',
        '<i data-lucide="' + icon + '" class="w-5 h-5 ' + iconCls + '"></i>',
        '</div>',
        '<div class="min-w-0">',
        '<h3 class="font-serif text-lg text-secondary leading-tight">' + escapeText(opts.title || '') + '</h3>',
        opts.message ? '<p class="text-sm text-textSecondary mt-1 leading-relaxed">' + escapeText(opts.message) + '</p>' : '',
        '</div>',
        '</div>',
        '<div class="flex gap-2 justify-end p-6 pt-4 shrink-0">',
        '<button type="button" data-dialog-action="cancel" class="px-4 py-2 rounded-xl text-sm text-textMuted hover:text-secondary hover:bg-surfaceHighlight transition">' + cancelText + '</button>',
        '<button type="button" data-dialog-action="confirm" class="px-4 py-2 rounded-xl text-sm font-medium ' + confirmCls + ' transition">' + confirmText + '</button>',
        '</div>',
    ].join('');
    overlay.appendChild(dialog);

    return new Promise(function (resolve) {
        let settled = false;
        // Esc 關閉。不能用 { once: true }：先按任何別的鍵就把監聽用掉，Esc 失效
        const onKey = function (e) {
            if (e.key === 'Escape') finish(false);
        };
        const finish = function (val) {
            if (settled) return;
            settled = true;
            document.removeEventListener('keydown', onKey);
            overlay.style.opacity = '0';
            overlay.style.transition = 'opacity 0.2s ease';
            window.setTimeout(function () {
                overlay.remove();
            }, 200);
            resolve(val);
        };
        overlay.addEventListener('click', function (e) {
            if (e.target === overlay) finish(false); // 點背景 = 取消
        });
        dialog.querySelector('[data-dialog-action="cancel"]').addEventListener('click', function () {
            finish(false);
        });
        dialog.querySelector('[data-dialog-action="confirm"]').addEventListener('click', function () {
            finish(true);
        });
        document.addEventListener('keydown', onKey);
        document.body.appendChild(overlay);
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons();
        }
    });
}

/**
 * 顯示自訂資訊 dialog(取代原生 alert)。
 * options: { title, message, tone: 'info'|'success'|'error'|'warning', confirmText }
 * 回 Promise<void> — 使用者關閉後 resolve。
 */
function showInfoDialog(options) {
    const opts = options || {};
    const tones = {
        success: { icon: 'check-circle-2', cls: 'text-success' },
        error: { icon: 'x-circle', cls: 'text-danger' },
        warning: { icon: 'alert-triangle', cls: 'text-amber-400' },
        info: { icon: 'info', cls: 'text-primary' },
    };
    const tone = tones[opts.tone] || tones.info;
    const confirmText = opts.confirmText || (window.I18n ? window.I18n.t('common.confirm') : 'OK');

    const overlay = document.createElement('div');
    overlay.className = 'fixed inset-0 z-[100] bg-black/60 backdrop-blur-sm flex items-end md:items-center justify-center p-4 animate-fade-in overflow-y-auto';
    const dialog = document.createElement('div');
    // max-h + flex：動態 message 過長時卡片內滾，不超出視窗（2026-08-21 盤查）
    dialog.className = 'w-full max-w-sm max-h-[80dvh] flex flex-col rounded-3xl bg-surface border border-borderSubtle shadow-2xl animate-fade-in-up overflow-hidden';
    dialog.innerHTML = [
        '<div class="flex items-start gap-3 p-6 pb-0 shrink-0">',
        '<div class="w-10 h-10 rounded-full bg-surfaceHighlight flex items-center justify-center shrink-0">',
        '<i data-lucide="' + tone.icon + '" class="w-5 h-5 ' + tone.cls + '"></i>',
        '</div>',
        '<div class="min-w-0">',
        '<h3 class="font-serif text-lg text-secondary leading-tight">' + escapeText(opts.title || '') + '</h3>',
        opts.message ? '<p class="text-sm text-textSecondary mt-1 leading-relaxed break-words max-h-[50dvh] overflow-y-auto custom-scrollbar">' + escapeText(opts.message) + '</p>' : '',
        '</div>',
        '</div>',
        '<div class="flex justify-end p-6 pt-4 shrink-0">',
        '<button type="button" data-dialog-action="ok" class="px-4 py-2 rounded-xl text-sm font-medium bg-primary text-background hover:bg-primary/90 transition">' + confirmText + '</button>',
        '</div>',
    ].join('');
    overlay.appendChild(dialog);

    return new Promise(function (resolve) {
        let settled = false;
        const onKey = function (e) {
            if (e.key === 'Escape') finish();
        };
        const finish = function () {
            if (settled) return;
            settled = true;
            document.removeEventListener('keydown', onKey);
            overlay.style.opacity = '0';
            overlay.style.transition = 'opacity 0.2s ease';
            window.setTimeout(function () {
                overlay.remove();
            }, 200);
            resolve();
        };
        overlay.addEventListener('click', function (e) {
            if (e.target === overlay) finish();
        });
        dialog.querySelector('[data-dialog-action="ok"]').addEventListener('click', finish);
        document.addEventListener('keydown', onKey);
        document.body.appendChild(overlay);
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons();
        }
    });
}

const UIShell = {
    ensureToastContainer: ensureToastContainer,
    showToast: showToast,
    dismissToast: dismissToast,
    clearToasts: clearToasts,
    syncLayout: syncLayout,
    applyScrollPadding: applyScrollPadding,
    initialize: initialize,
};

window.UIShell = UIShell;
window.showToast = showToast;
window.showConfirmDialog = showConfirmDialog;
window.showInfoDialog = showInfoDialog;

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize);
} else {
    initialize();
}

export { UIShell, showToast, showConfirmDialog, showInfoDialog, captureAnchoredScrollers };
