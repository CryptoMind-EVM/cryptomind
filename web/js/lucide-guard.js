/**
 * lucide.createIcons 防重畫（2026-10-02，切回視窗會閃一下、操作不順）。
 *
 * lucide 0.577 有兩個坑：
 *   1. 畫好的 <svg> 仍帶 data-lucide，原版每次呼叫都把整頁所有圖示（社群頁 300+ 個）砍掉重畫；
 *      全站 refreshIcons／createIcons 呼叫超過 200 處，一個事件常叫好幾次（手機上一次約 30ms）
 *   2. 沒有 nodes 選項：refreshIcons(container)、createIconsIn(el) 傳的 { nodes: [...] } 被忽略，
 *      以為只畫容器，其實每次都是整頁
 * 這層包住 createIcons：只畫還沒畫的（<i data-lucide>）或名稱被改過的（ThemeSwitcher 直接改 svg 的
 * data-lucide 換圖示），{ nodes } 當成範圍。utils.js 載入時裝上，所有呼叫點不用改。
 */

const PENDING = 'data-lucide-pending';
const RENDERED = 'data-lucide-rendered';

function needsRender(el, nameAttr) {
    const name = el.getAttribute(nameAttr);
    if (!name) return false;
    if (String(el.tagName).toLowerCase() !== 'svg') return true;
    const stamp = el.getAttribute(RENDERED);
    // 換過名字的 svg 會殘留舊的 lucide-xxx class，所以有標記就只看標記
    if (stamp != null) return stamp !== name;
    // 裝這層之前畫好的：class 對得上就當畫好了
    return !(el.classList && el.classList.contains(`lucide-${name}`));
}

function guardLucide(lucide) {
    if (!lucide || typeof lucide.createIcons !== 'function' || lucide.createIcons.__guarded) return;
    const original = lucide.createIcons;
    const guarded = function (opts = {}) {
        const { nodes, ...rest } = opts || {};
        const nameAttr = rest.nameAttr || 'data-lucide';
        let roots = [document];
        if (rest.root) roots = [rest.root];
        else if (Array.isArray(nodes) && nodes.length) roots = nodes.filter(Boolean);
        for (const root of roots) {
            if (!root || typeof root.querySelectorAll !== 'function') continue;
            const pending = Array.from(root.querySelectorAll(`[${nameAttr}]`)).filter((el) =>
                needsRender(el, nameAttr)
            );
            if (!pending.length) continue;
            // 只讓 lucide 看到要畫的：把名字抄到暫存屬性，叫原版用那個屬性掃
            pending.forEach((el) => el.setAttribute(PENDING, el.getAttribute(nameAttr)));
            original({ ...rest, root, nameAttr: PENDING });
            root.querySelectorAll(`[${PENDING}]`).forEach((el) => {
                el.setAttribute(RENDERED, el.getAttribute(PENDING));
                el.removeAttribute(PENDING);
            });
        }
    };
    guarded.__guarded = true;
    lucide.createIcons = guarded;
}

// lucide 是 <script defer>，排在模組前面，通常這時已經在；不在就等 DOM 就緒再裝
if (window.lucide) guardLucide(window.lucide);
else if (typeof window.addEventListener === 'function') {
    window.addEventListener('DOMContentLoaded', () => guardLucide(window.lucide), { once: true });
}

export { guardLucide, needsRender };
