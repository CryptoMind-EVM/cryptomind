/**
 * 選取文字 → 浮出「問 AI」小鈕（2026-10-05，DANNY：選某句話問 AI，要直接帶進輸入框，不要自己複製貼上）。
 *
 * 用法：attachSelectionAsk({ root, eligible, label, onAsk, enabled })
 *   root      只看這個容器裡的選取（例如聊天訊息區、AI 回答區）
 *   eligible  選取必須整段落在這種元素裡（例如 '.msg-bubble'）；跨兩則訊息的選取不算
 *   label     按鈕文字（字串或回傳字串的函式，換語言才跟得上）
 *   onAsk     按下去：({ text, target }) => …；text 是整理過的選取文字，target 是那個 eligible 元素
 *   enabled   選用：回傳 false 就不顯示（例如聊天室 AI 助理沒開）
 * 回傳 detach()。同一個 root 重複 attach 只會更新設定（好友頁每次切進來都會跑 init）。
 *
 * 按鈕放在選取範圍的下方：手機瀏覽器的原生選字工具列在上方，放下面才不會蓋到。
 * 觸控裝置的訊息氣泡本來就不能選字（長按是訊息選單，見 styles.css），所以那裡不會出現；
 * AI 回答區在觸控裝置上可以選，會出現。
 */

const MAX_TEXT = 300;
const GAP = 8;
const Z_INDEX = 10001; // 高過 AI 助理抽屜（chat-assistant.js 的 10000）

const registrations = new Map(); // root → 設定
let button = null;
let pending = null; // 按鈕目前對應的選取：{ reg, text, target }
let timer = null;
let wired = false;

/** 選取文字整理：空白（含換行）壓成一個空格、去頭尾、太長截斷加「…」。空字串＝不算選取。 */
export function normalizeSelection(text, max = MAX_TEXT) {
    const clean = String(text ?? '').replace(/\s+/g, ' ').trim();
    if (clean.length < 2) return ''; // 單一字元多半是手滑，不浮按鈕
    return clean.length > max ? `${clean.slice(0, max - 1).trimEnd()}…` : clean;
}

/**
 * 按鈕位置（純函式）：預設放在選取範圍下方、水平置中於範圍；超出視窗就夾回來，
 * 下方放不下（靠近底邊）改放上方。rect 是選取範圍最後一行的 client rect。
 */
export function placeButton(rect, size, view, gap = GAP) {
    const left = Math.min(Math.max(rect.left + rect.width / 2 - size.width / 2, gap), Math.max(gap, view.width - size.width - gap));
    let top = rect.bottom + gap;
    if (top + size.height > view.height - gap) top = Math.max(gap, rect.top - gap - size.height);
    return { left: Math.round(left), top: Math.round(top) };
}

function labelOf(reg) {
    return typeof reg.label === 'function' ? reg.label() : reg.label || '';
}

function ensureButton() {
    if (button) return button;
    button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('data-selection-ask', '');
    button.className =
        'fixed hidden items-center gap-1 rounded-full bg-primary text-background text-xs font-bold px-3 py-1.5 shadow-lg hover:bg-primary/90 transition';
    button.style.zIndex = String(Z_INDEX);
    // 按下時不要讓選取消失（mousedown／pointerdown 的預設行為會清掉選取）
    const keep = (e) => e.preventDefault();
    button.addEventListener('mousedown', keep);
    button.addEventListener('pointerdown', keep);
    button.addEventListener('click', (e) => {
        e.preventDefault();
        const hit = pending;
        hide();
        window.getSelection?.()?.removeAllRanges?.();
        if (hit) hit.reg.onAsk({ text: hit.text, target: hit.target });
    });
    document.body.appendChild(button);
    return button;
}

function hide() {
    pending = null;
    if (button) {
        button.classList.add('hidden');
        button.classList.remove('flex');
    }
}

/** 目前的選取是不是落在某個登記過的 root 的 eligible 元素裡；是就回 { reg, text, target, range } */
function currentHit() {
    const sel = window.getSelection?.();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
    const range = sel.getRangeAt(0);
    const node = range.commonAncestorContainer;
    const element = node && (node.nodeType === 1 ? node : node.parentElement);
    if (!element) return null;
    for (const reg of registrations.values()) {
        if (reg.enabled && !reg.enabled()) continue;
        if (!reg.root.contains(element)) continue;
        const target = element.closest(reg.eligible);
        if (!target || !reg.root.contains(target)) continue;
        const text = normalizeSelection(sel.toString(), reg.max);
        if (text) return { reg, text, target, range };
    }
    return null;
}

function update() {
    timer = null;
    const hit = currentHit();
    if (!hit) {
        hide();
        return;
    }
    const rects = hit.range.getClientRects?.();
    const rect = (rects && rects.length ? rects[rects.length - 1] : null) || hit.range.getBoundingClientRect();
    if (!rect || (rect.width === 0 && rect.height === 0)) {
        hide();
        return;
    }
    const btn = ensureButton();
    btn.textContent = `✨ ${labelOf(hit.reg)}`;
    btn.setAttribute('aria-label', labelOf(hit.reg));
    pending = { reg: hit.reg, text: hit.text, target: hit.target };
    btn.classList.remove('hidden');
    btn.classList.add('flex');
    const size = { width: btn.offsetWidth || 72, height: btn.offsetHeight || 30 };
    const pos = placeButton(rect, size, { width: window.innerWidth, height: window.innerHeight });
    btn.style.left = `${pos.left}px`;
    btn.style.top = `${pos.top}px`;
}

function schedule() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(update, 120);
}

const onEscape = (e) => {
    if (e.key === 'Escape') hide();
};

function wire() {
    if (wired) return;
    wired = true;
    document.addEventListener('selectionchange', schedule);
    document.addEventListener('pointerup', schedule);
    document.addEventListener('keyup', schedule);
    document.addEventListener('keydown', onEscape);
    window.addEventListener('scroll', hide, true);
    window.addEventListener('resize', hide);
}

function unwire() {
    if (!wired) return;
    wired = false;
    document.removeEventListener('selectionchange', schedule);
    document.removeEventListener('pointerup', schedule);
    document.removeEventListener('keyup', schedule);
    document.removeEventListener('keydown', onEscape);
    window.removeEventListener('scroll', hide, true);
    window.removeEventListener('resize', hide);
    if (timer) clearTimeout(timer);
    timer = null;
    hide();
}

export function attachSelectionAsk({ root, eligible, label, onAsk, enabled = null, max = MAX_TEXT }) {
    if (!root || !eligible || typeof onAsk !== 'function') return () => {};
    registrations.set(root, { root, eligible, label, onAsk, enabled, max });
    wire();
    return function detach() {
        if (registrations.get(root)?.onAsk === onAsk) registrations.delete(root);
        if (registrations.size === 0) unwire();
    };
}

/** 測試用：目前登記幾個容器 */
export function _registeredCount() {
    return registrations.size;
}

