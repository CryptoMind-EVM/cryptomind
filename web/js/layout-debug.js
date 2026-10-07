/* 手機底部版面的即時數字面板 —— 只在網址帶 #layout-debug 時出現。

   「訊息被打字框擋住」在真實手機上重現過很多次,但桌機模擬與 e2e 都量不到,
   因為差別在裝置本身:layout viewport 與 visual viewport 的關係、鍵盤怎麼縮視窗、
   safe-area 有多少。這個面板把判斷需要的每個數字直接印在畫面上,拍一張截圖就能定位,
   不必再靠推論。

   關鍵那一行是「被蓋住」:最後一則訊息的底部減掉輸入框的頂部。> 0 就是真的被蓋住。 */

const FLAG = 'layout-debug';

/* 這個面板只在本機開發時存在。

   Production guard v2（2026-08-25 DANNY 第二次回報正式站跑出面板）：
   v1 寫成 `window.APP_CONFIG && window.APP_CONFIG.DEBUG_MODE !== true`，
   APP_CONFIG 不存在時 && 直接短路、return false 永遠不執行——而
   web/index.html 從來沒有載入 config.js，主 SPA 上 APP_CONFIG 恆為
   undefined，所以那個守衛等於不存在。
   改成以 hostname 判斷並 fail-closed：認不出是本機就一律關閉。

   連點頁首五下的開法已整個移除。它原本不在任何守衛後面、直接呼叫 mount()，
   而頁首正是國旗/主題鈕/通知鈴——最常被連續點的位置，一般使用者會誤觸。 */
const PRIVATE_IPV4 = new RegExp(
    '^(?:' +
    '10\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}' + '|' +
    '192\\.168\\.\\d{1,3}\\.\\d{1,3}' + '|' +
    '172\\.(?:1[6-9]|2\\d|3[01])\\.\\d{1,3}\\.\\d{1,3}' +
    ')$'
);

function isPrivateIPv4(host) {
    if (!PRIVATE_IPV4.test(host)) return false;
    return host.split('.').every((o) => Number(o) <= 255);
}

function isLocalHost() {
    const host = (window.location.hostname || '').toLowerCase();
    return (
        host === 'localhost' ||
        host === '127.0.0.1' ||
        host === '::1' ||
        host === '[::1]' ||
        host === '0.0.0.0' ||
        host.endsWith('.local') ||
        host.endsWith('.localhost') ||
        // 實機接區網開發機：192.168.x.x / 10.x.x.x / 172.16-31.x.x。
        // 必須整串比對——只錨定開頭的話 `192.168.1.5.evil.com` 這種網域
        // 也會通過（2026-08-25 自我測試抓到）。
        isPrivateIPv4(host)
    );
}

/* query string 才是耐用的那個 —— spa.js 的 switchTab 會把 hash 改寫成目前
   分頁（#chat），重新整理之後 hash 上的旗標就沒了。 */
function isEnabled() {
    if (!isLocalHost()) return false;
    return (
        window.location.search.toLowerCase().includes(FLAG) ||
        window.location.hash.toLowerCase().includes(FLAG)
    );
}

/* I18n 是 module 且非同步 init——mount() 可能早於它就緒（?layout-debug=1 在
   DOMContentLoaded 就觸發）。直接讀 window.I18n.t 會 TypeError，讓整個面板
   靜默不出現：帶了旗標卻沒反應就是這樣來的（2026-08-25）。這個面板是壞掉時
   才會開的工具，不能自己也跟著壞——一律走 fallback 文案。 */
function t(key, fallback) {
    if (window.I18n && typeof window.I18n.t === 'function') {
        const v = window.I18n.t(key);
        if (v && v !== key) return v;
    }
    return fallback;
}

function num(value) {
    return Math.round(Number(value) || 0);
}

function cssVar(name) {
    return num(parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name)));
}

function collect() {
    const root = document.documentElement;
    const vv = window.visualViewport;
    const box = document.getElementById('chat-messages');
    const inputBar = document.querySelector('[data-shell-fixed-input]');
    const active = document.activeElement;

    const inputRect = inputBar ? inputBar.getBoundingClientRect() : null;
    const last = box && box.lastElementChild ? box.lastElementChild.getBoundingClientRect() : null;

    return {
        [t('layoutDebug.windowInner', '視窗 innerHeight')]: num(window.innerHeight),
        [t('layoutDebug.windowClient', '視窗 clientHeight')]: num(root.clientHeight),
        [t('layoutDebug.vvHeight', '可見 vv.height')]: vv ? num(vv.height) : 'n/a',
        'vv.offsetTop': vv ? num(vv.offsetTop) : 'n/a',
        'vv.scale': vv ? (Math.round(vv.scale * 100) / 100) : 'n/a',
        'safe-area-bottom': num(
            getComputedStyle(root).getPropertyValue('--mobile-safe-bottom')
        ),
        [t('layoutDebug.measured', '—— 量出來的 ——')]: '',
        'clearance': cssVar('--shell-content-clearance'),
        [t('layoutDebug.keyboardShift', '鍵盤位移')]: cssVar('--chat-keyboard-offset'),
        'keyboard-open': root.classList.contains('chat-keyboard-open') ? 'YES' : 'no',
        'activeElement': active ? (active.id || active.tagName) : 'n/a',
        [t('layoutDebug.scrollSection', '—— 捲動區 ——')]: '',
        'padding-bottom': box ? num(parseFloat(getComputedStyle(box).paddingBottom)) : 'n/a',
        'clientHeight': box ? num(box.clientHeight) : 'n/a',
        [t('layoutDebug.distToBottom', '離底部')]: box ? num(box.scrollHeight - box.scrollTop - box.clientHeight) : 'n/a',
        [t('layoutDebug.geometry', '—— 幾何 ——')]: '',
        [t('layoutDebug.inputTop', '輸入框 top')]: inputRect ? num(inputRect.top) : 'n/a',
        [t('layoutDebug.inputBottom', '輸入框 bottom')]: inputRect ? num(inputRect.bottom) : 'n/a',
        [t('layoutDebug.lastMsgBottom', '最後一則 bottom')]: last ? num(last.bottom) : 'n/a',
        [t('layoutDebug.covered', '★ 被蓋住')]: last && inputRect ? num(last.bottom - inputRect.top) : 'n/a',
    };
}

function render(panel, body) {
    const data = collect();
    body.textContent = Object.entries(data)
        .map(([key, value]) => (value === '' ? key : `${key}: ${value}`))
        .join('\n');
    panel.dataset.snapshot = body.textContent;
}

function mount() {
    if (document.getElementById('layout-debug-panel')) return;

    const panel = document.createElement('div');
    panel.id = 'layout-debug-panel';
    panel.style.cssText = [
        'position:fixed', 'top:0', 'left:0', 'z-index:99999',
        'background:rgba(0,0,0,.88)', 'color:#0f0',
        'font:11px/1.35 ui-monospace,monospace', 'padding:6px 8px',
        'max-width:70vw', 'white-space:pre', 'pointer-events:auto',
        'border:1px solid #0f0', 'border-radius:0 0 6px 0',
        // 有些環境（Telegram 內建瀏覽器、開了安全旗標的 App）截不了圖,
        // 至少要能長按選取把文字撈出來
        'user-select:text', '-webkit-user-select:text',
    ].join(';');

    const body = document.createElement('div');
    const copy = document.createElement('button');
    copy.textContent = t('layoutDebug.copyButton', '複製');
    copy.style.cssText = 'margin-top:4px;font:11px monospace;padding:2px 8px';
    /* 截圖在部分環境會被擋（Telegram 內建瀏覽器、開了安全旗標的 App）,所以複製這條路
       必須撐得住:先試 Clipboard API,失敗再退回 textarea + execCommand,兩條都不通就把
       文字攤成一個選取好的 textarea,讓使用者長按複製。 */
    copy.addEventListener('click', () => {
        const text = panel.dataset.snapshot || '';

        const fallback = () => {
            const area = document.createElement('textarea');
            area.value = text;
            area.readOnly = true;
            area.style.cssText =
                'width:66vw;height:9rem;font:11px/1.35 ui-monospace,monospace;' +
                'background:#000;color:#0f0;border:1px solid #0f0';
            panel.insertBefore(area, copy);
            area.focus();
            area.setSelectionRange(0, text.length);
            let copied = false;
            try {
                copied = document.execCommand('copy');
            } catch (_) {
                copied = false;
            }
            copy.textContent = copied ? (t('layoutDebug.copied', '已複製')) : (t('layoutDebug.copyHint', '請長按選取複製 ↑'));
        };

        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(
                () => { copy.textContent = t('layoutDebug.copied', '已複製'); },
                fallback
            );
        } else {
            fallback();
        }
    });

    panel.appendChild(body);
    panel.appendChild(copy);
    document.body.appendChild(panel);

    const tick = () => render(panel, body);
    tick();
    // 鍵盤、網址列、串流都會讓數字變,持續更新才看得到出事的那一刻
    window.setInterval(tick, 300);
}

function initialize() {
    const start = () => {
        if (isEnabled()) mount();
    };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
}

initialize();
window.addEventListener('hashchange', () => {
    if (isEnabled()) mount();
});

export { collect };
