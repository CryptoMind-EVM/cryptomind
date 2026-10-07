/**
 * 私訊訊息選單（LINE 式，2026-09-29）
 *
 * 單一入口：手機長按氣泡、桌機 hover「⋯」或右鍵，開同一個選單。以前每則氣泡旁常駐
 * 收回／垃圾桶按鈕——手機沒有 hover 等於藏起來，桌機一排按鈕又亂。
 * 兩支 UI（messages.js 的 MessagesUI、friends.js 的 SocialHub）共用；項目由
 * messageMenuItems 決定，動作由各 UI 提供 handlers。
 */

/** 收回時限：後端 messages_repo.RECALL_WINDOW 才是準，這裡只決定要不要顯示「收回」 */
const RECALL_WINDOW_MS = 24 * 60 * 60 * 1000;

function canRecallMessage(msg, now = Date.now()) {
    if (!msg || msg.message_type === 'recalled') return false;
    const sentAt = Date.parse(msg.created_at);
    return Number.isFinite(sentAt) && now - sentAt <= RECALL_WINDOW_MS;
}

const t = (key, fallback) => (window.I18n ? window.I18n.t(key) : fallback);
/** 帶參數的翻譯（{{name}} 之類）；沒有 I18n 時用 fallback 自己代換 */
const tp = (key, params, fallback) =>
    window.I18n
        ? window.I18n.t(key, params)
        : fallback.replace(/\{\{(\w+)\}\}/g, (_m, k) => params[k] ?? '');

function escapeHtml(value) {
    return String(value ?? '').replace(
        /[&<>"']/g,
        (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]
    );
}

/** 選單項目（順序即顯示順序）；danger 的排在分隔線後、紅字 */
const MENU_ITEMS = [
    { key: 'reply', icon: 'reply', danger: false },
    { key: 'copy', icon: 'copy', danger: false },
    // 聊天室 AI 助理（chat-assistant.js）：帶這則＋前後幾則開抽屜；開關關著頁面不放進 features
    { key: 'askAi', icon: 'sparkles', danger: false },
    // 群組：誰讀過自己這則（桌機 hover 時工具列蓋住「已讀 N」點不到，選單才是穩的入口）
    { key: 'readers', icon: 'check-check', danger: false },
    { key: 'report', icon: 'flag', danger: false },
    { key: 'recall', icon: 'undo-2', danger: true },
    { key: 'hide', icon: 'trash-2', danger: true },
];

/**
 * 這則訊息的選單要有哪些項目。回傳 item 物件，普通與危險兩組之間插 'sep'。
 * features：目前已上線的項目（還沒做的功能不出現）。
 */
function messageMenuItems({ isMine, messageType, createdAt, now = Date.now(), features }) {
    const recalled = messageType === 'recalled';
    const allowed = (item) => {
        if (!features.has(item.key)) return false;
        if (recalled) return item.key === 'hide'; // 沒內容可複製、回覆、檢舉
        if (item.key === 'recall') return isMine && canRecallMessage({ message_type: messageType, created_at: createdAt }, now);
        if (item.key === 'report') return !isMine;
        if (item.key === 'readers') return isMine;
        return true;
    };
    const normal = MENU_ITEMS.filter((i) => !i.danger && allowed(i));
    const danger = MENU_ITEMS.filter((i) => i.danger && allowed(i));
    return normal.length && danger.length ? [...normal, 'sep', ...danger] : [...normal, ...danger];
}

/** 複製文字。Telegram／Base App 的 iframe 可能擋 clipboard API → 退回 execCommand */
async function copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch (_e) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        let ok = false;
        try {
            ok = document.execCommand('copy');
        } catch (_err) {
            ok = false;
        }
        ta.remove();
        return ok;
    }
}

// ============================================================================
// 表情回應：8 個自繪 SVG（每個平台長得一樣）。DB 只存 key（core/dm_reactions.py）
// ============================================================================

const REACTIONS = ['like', 'love', 'haha', 'wow', 'sad', 'rocket', 'diamond', 'ok'];

// 32×32、扁平、固定色，亮暗模式共用
const REACTION_SYMBOLS = {
    like: '<rect x="4" y="14" width="6" height="13" rx="1.5" fill="#378ADD" stroke="#185FA5" stroke-width="1.2"/><path d="M11 15 L15 7 C15.6 5.4 18.6 5.6 18.6 8.6 L18 13 H25 C26.7 13 27.9 14.6 27.4 16.2 L24.9 25 C24.6 26.2 23.5 27 22.3 27 H11 Z" fill="#FAC775" stroke="#BA7517" stroke-width="1.4" stroke-linejoin="round"/><path d="M18.5 17.5 H26.5 M18.5 21.5 H25.5" stroke="#BA7517" stroke-width="1.1" stroke-linecap="round"/>',
    love: '<path d="M16 27.5 C16 27.5 4 20 4 11.8 C4 7.6 7 5 10.6 5 C13.1 5 15 6.5 16 8.6 C17 6.5 18.9 5 21.4 5 C25 5 28 7.6 28 11.8 C28 20 16 27.5 16 27.5 Z" fill="#E24B4A" stroke="#A32D2D" stroke-width="1.4" stroke-linejoin="round"/><path d="M8.5 11 C8.5 9.2 9.6 8.2 11 8" stroke="#F7C1C1" stroke-width="1.6" stroke-linecap="round" fill="none"/>',
    haha: '<circle cx="16" cy="16" r="13.5" fill="#FAC775" stroke="#BA7517" stroke-width="1.4"/><path d="M8.8 13.5 Q11 10.2 13.2 13.5 M18.8 13.5 Q21 10.2 23.2 13.5" stroke="#633806" stroke-width="1.8" stroke-linecap="round" fill="none"/><path d="M9 17.5 H23 Q23 25.5 16 25.5 Q9 25.5 9 17.5 Z" fill="#633806"/><path d="M12 22.6 Q16 20.6 20 22.6 Q18.6 25 16 25 Q13.4 25 12 22.6 Z" fill="#E24B4A"/><path d="M5.2 15 Q3.6 18 5 19.4 Q6.6 18 5.2 15 Z M26.8 15 Q28.4 18 27 19.4 Q25.4 18 26.8 15 Z" fill="#85B7EB"/>',
    wow: '<circle cx="16" cy="16" r="13.5" fill="#FAC775" stroke="#BA7517" stroke-width="1.4"/><path d="M9 9.6 Q11.4 7.8 13.6 9.2 M18.4 9.2 Q20.6 7.8 23 9.6" stroke="#633806" stroke-width="1.5" stroke-linecap="round" fill="none"/><circle cx="11.5" cy="13.6" r="1.9" fill="#633806"/><circle cx="20.5" cy="13.6" r="1.9" fill="#633806"/><ellipse cx="16" cy="21.4" rx="3.2" ry="4" fill="#633806"/>',
    sad: '<circle cx="16" cy="16" r="13.5" fill="#FAC775" stroke="#BA7517" stroke-width="1.4"/><path d="M8.8 11.4 L13.2 9.8 M23.2 11.4 L18.8 9.8" stroke="#633806" stroke-width="1.5" stroke-linecap="round"/><circle cx="11.5" cy="14.4" r="1.7" fill="#633806"/><circle cx="20.5" cy="14.4" r="1.7" fill="#633806"/><path d="M11.2 23.4 Q16 19.4 20.8 23.4" stroke="#633806" stroke-width="1.8" stroke-linecap="round" fill="none"/><path d="M10.6 17 Q8.8 20.4 10.6 21.6 Q12.4 20.4 10.6 17 Z" fill="#378ADD"/>',
    rocket: '<g transform="rotate(35 16 16)"><path d="M13 21.5 Q16 31 19 21.5 Z" fill="#EF9F27"/><path d="M14.4 21.5 Q16 27 17.6 21.5 Z" fill="#FAEEDA"/><path d="M10.8 15.5 L6.6 20.6 L7.2 23.6 L11.2 21.4 Z M21.2 15.5 L25.4 20.6 L24.8 23.6 L20.8 21.4 Z" fill="#E24B4A" stroke="#A32D2D" stroke-width="1.1" stroke-linejoin="round"/><path d="M16 2.8 C21.2 7 22.6 13.4 21.6 21.5 H10.4 C9.4 13.4 10.8 7 16 2.8 Z" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="1.4" stroke-linejoin="round"/><circle cx="16" cy="12" r="2.7" fill="#378ADD" stroke="#185FA5" stroke-width="1.1"/></g>',
    diamond: '<path d="M9 6 H23 L28.5 12.4 L16 27.5 L3.5 12.4 Z" fill="#85B7EB" stroke="#185FA5" stroke-width="1.4" stroke-linejoin="round"/><path d="M3.5 12.4 H28.5 M9 6 L12.2 12.4 L16 27.5 M23 6 L19.8 12.4 L16 27.5 M12.2 12.4 L16 6 L19.8 12.4" stroke="#185FA5" stroke-width="1" stroke-linejoin="round" fill="none"/><path d="M9.6 8.2 L7.2 11.2" stroke="#E6F1FB" stroke-width="1.4" stroke-linecap="round"/>',
    ok: '<circle cx="16" cy="16" r="13.5" fill="#5DCAA5" stroke="#0F6E56" stroke-width="1.4"/><path d="M9.6 16.6 L14.2 21.2 L22.6 11.4" stroke="#04342C" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
};

/** 把 8 個 symbol 放進頁面一次（<use href="#dmr-key"> 引用） */
function ensureReactionSprite() {
    if (document.getElementById('dm-reaction-sprite')) return;
    const holder = document.createElement('div');
    holder.innerHTML =
        '<svg id="dm-reaction-sprite" width="0" height="0" style="position:absolute" aria-hidden="true"><defs>' +
        REACTIONS.map((k) => `<symbol id="dmr-${k}" viewBox="0 0 32 32">${REACTION_SYMBOLS[k]}</symbol>`).join('') +
        '</defs></svg>';
    document.body.appendChild(holder.firstChild);
}

function reactionIcon(key, sizeClass = 'w-5 h-5') {
    if (!REACTIONS.includes(key)) return '';
    return `<svg class="${sizeClass} shrink-0" aria-hidden="true"><use href="#dmr-${key}"></use></svg>`;
}

/** [{user_id, reaction}] → [{key, count, mine}]，照 REACTIONS 的順序 */
function groupReactions(reactions, currentUserId) {
    const byKey = new Map();
    for (const r of reactions || []) {
        if (!REACTIONS.includes(r.reaction)) continue;
        const g = byKey.get(r.reaction) || { key: r.reaction, count: 0, mine: false };
        g.count += 1;
        if (r.user_id === currentUserId) g.mine = true;
        byKey.set(r.reaction, g);
    }
    return REACTIONS.filter((k) => byKey.has(k)).map((k) => byKey.get(k));
}

/** 氣泡裡文字下方的表情膠囊（沒人按就是空字串）。點膠囊＝切換自己的這個表情 */
function reactionsHtml(reactions, currentUserId, isMine) {
    const groups = groupReactions(reactions, currentUserId);
    if (!groups.length) return '';
    // 膠囊掛在氣泡下緣外（LINE 式，壓住下緣一點）：不撐大氣泡；那一列底下的空位由
    // styles.css 的 .msg-row:has(.msg-reactions) 留。DOM 仍在 .msg-bubble 裡，長按／更新照舊
    const pill = (g) => {
        const tone = g.mine ? 'ring-primary/70 text-primary' : 'ring-borderSubtle text-textMain';
        const label = escapeHtml(t(`messages.reactions.${g.key}`, g.key));
        return (
            `<button type="button" data-dm-reaction="${g.key}" aria-pressed="${g.mine}" aria-label="${label} ${g.count}" title="${label}" ` +
            `class="inline-flex items-center gap-1 h-6 pl-1 pr-2 rounded-full text-xs font-medium bg-surface shadow-sm ring-1 ${tone} hover:brightness-95 transition">` +
            `${reactionIcon(g.key, 'w-4 h-4')}<span>${g.count}</span></button>`
        );
    };
    const side = isMine ? 'right-2' : 'left-2';
    return `<div class="msg-reactions absolute top-full -mt-2 ${side} z-[1] flex gap-1 whitespace-nowrap">${groups.map(pill).join('')}</div>`;
}

/** 表情列（手機 overlay 上方、桌機 😊 開的小框）：8 個按鈕，已按的那個有底色 */
function buildReactionBar(currentReaction, onPick) {
    const bar = document.createElement('div');
    bar.className = 'dm-reaction-bar flex items-center gap-0.5 p-1 rounded-full bg-surface border border-borderSubtle shadow-2xl animate-fade-in';
    bar.setAttribute('role', 'group');
    bar.setAttribute('aria-label', t('messages.actions.react', 'React'));
    for (const key of REACTIONS) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.dataset.dmReact = key;
        btn.setAttribute('aria-label', t(`messages.reactions.${key}`, key));
        btn.setAttribute('aria-pressed', String(currentReaction === key));
        btn.className =
            'w-9 h-9 rounded-full flex items-center justify-center hover:bg-surfaceHighlight active:scale-90 transition' +
            (currentReaction === key ? ' bg-primary/15' : '');
        btn.innerHTML = reactionIcon(key, 'w-6 h-6');
        btn.addEventListener('click', () => onPick(key));
        bar.append(btn);
    }
    return bar;
}

// ============================================================================
// 連結與錢包地址：網址可點（外部網域先跳風險提示）、EVM 地址可點（開詐騙查詢）
// 先把原文切段、每段各自跳脫再組回——不在已跳脫的 HTML 上跑正則
// ============================================================================

// 網址：只認 http／https，遇到空白、引號、角括號就斷（撐不開 href）；中文句子常常網址後面
// 直接接字，遇到中日韓文字與全形標點也斷（西里爾等不斷：仿冒網域照樣切出來，提示框會露出 punycode）
// 地址：0x＋剛好 40 個 hex（前後不能再接 hex）
const TOKEN_RE =
    /(https?:\/\/[^\s<>"'`\u3000-\u303f\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af\uff00-\uffef]+)|((?<![0-9a-zA-Z])0x[0-9a-fA-F]{40}(?![0-9a-fA-F]))/g;
// 網址結尾的標點多半是句子的，不是網址的
// （含全形：」』）】。，、；：！？…）
const URL_TRAILING = /[.,;:!?)\]}'"\u300d\u300f\uff09\u3011\u3002\uff0c\u3001\uff1b\uff1a\uff01\uff1f\u2026]+$/;

function tokenizeMessage(text) {
    const tokens = [];
    const pushText = (value) => {
        if (!value) return;
        const last = tokens[tokens.length - 1];
        if (last?.type === 'text') last.value += value;
        else tokens.push({ type: 'text', value });
    };
    let at = 0;
    for (const m of String(text ?? '').matchAll(TOKEN_RE)) {
        pushText(text.slice(at, m.index));
        if (m[1]) {
            const trailing = m[1].match(URL_TRAILING)?.[0] || '';
            const url = trailing ? m[1].slice(0, -trailing.length) : m[1];
            if (linkHost(url)) tokens.push({ type: 'url', value: url });
            else pushText(url);
            pushText(trailing);
        } else {
            tokens.push({ type: 'address', value: m[2] });
        }
        at = m.index + m[0].length;
    }
    pushText(String(text ?? '').slice(at));
    return tokens;
}

/** 瀏覽器解析出的 hostname（IDN 會是 punycode，仿冒網域露餡）；不是 http(s) 回 null */
function linkHost(url) {
    try {
        const u = new URL(url);
        return u.protocol === 'http:' || u.protocol === 'https:' ? u.hostname : null;
    } catch (_e) {
        return null;
    }
}

/** 自家網域不跳風險提示（結尾多一個點 getcryptomind.com. 也是同一個網域） */
function isOwnHost(host) {
    const h = host.endsWith('.') ? host.slice(0, -1) : host;
    return h === window.location?.hostname || h === 'getcryptomind.com' || h.endsWith('.getcryptomind.com');
}

const SHIELD_SVG =
    '<svg class="w-3.5 h-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>';

/**
 * 群組 @提及：把文字切成一般文字／提及。名字比對規則同後端 core/group_mentions.py
 * （每個 @ 比最長的名字、不分大小寫、全形＠也算）；mentions 是後端比對好的 [{user_id, name}]
 */
const nextAtSign = (value, from) => {
    const half = value.indexOf('@', from);
    const full = value.indexOf('＠', from); // 注音輸入法預設打全形
    if (half === -1) return full;
    return full === -1 ? half : Math.min(half, full);
};

function splitMentions(value, mentions) {
    const byName = new Map();
    for (const m of mentions) {
        if (!m?.name) continue;
        const key = String(m.name).toLowerCase();
        byName.set(key, [...(byName.get(key) || []), m.user_id]);
    }
    const names = [...byName.keys()].sort((a, b) => b.length - a.length);
    const parts = [];
    let at = 0;
    let i = nextAtSign(value, 0);
    while (i !== -1) {
        const start = i + 1;
        const hit = names.find((n) => value.slice(start, start + n.length).toLowerCase() === n);
        if (!hit) {
            i = nextAtSign(value, start);
            continue;
        }
        if (i > at) parts.push({ type: 'text', value: value.slice(at, i) });
        parts.push({ type: 'mention', value: value.slice(i, start + hit.length), userIds: byName.get(hit) });
        at = start + hit.length;
        i = nextAtSign(value, at);
    }
    if (at < value.length) parts.push({ type: 'text', value: value.slice(at) });
    return parts;
}

function mentionHtml(part, { myId, onPrimary }) {
    const isMe = !!myId && part.userIds.includes(myId);
    // 提及一律是色塊（一眼看出有人被 @）：自己的氣泡底色是主色 → 淺色半透明塊；別人的 → 主色字＋淡主色塊，@ 到我的塊更深
    const cls = onPrimary
        ? 'msg-mention font-semibold bg-background/25 rounded px-1'
        : `msg-mention font-semibold text-primary rounded px-1 ${isMe ? 'msg-mention-me bg-primary/25' : 'bg-primary/10'}`;
    return `<span class="${cls}" data-mention="${escapeHtml(part.userIds.join(' '))}">${escapeHtml(part.value)}</span>`;
}

/**
 * 訊息原文 → HTML（放進 .msg-text）：連結、地址變成可點，其他照字面。
 * 群組另外給 { mentions, myId, onPrimary }：@提及加樣式，@ 到自己的另外標示
 */
function renderMessageText(text, { mentions = [], myId = null, onPrimary = false } = {}) {
    return tokenizeMessage(text)
        .map((tok) => {
            if (tok.type === 'url') {
                const href = escapeHtml(tok.value);
                return `<a href="${href}" data-dm-link target="_blank" rel="noopener noreferrer nofollow" class="underline underline-offset-2 break-all">${href}</a>`;
            }
            if (tok.type === 'address') {
                const short = `${tok.value.slice(0, 6)}…${tok.value.slice(-4)}`;
                const title = escapeHtml(tp('messages.addressCheck', { address: tok.value }, 'Check {{address}} for scam signals'));
                return (
                    `<button type="button" data-dm-address="${tok.value}" title="${title}" ` +
                    'class="inline-flex items-center gap-0.5 align-baseline font-mono text-[0.92em] underline decoration-dotted underline-offset-2 hover:opacity-80">' +
                    `${short}${SHIELD_SVG}</button>`
                );
            }
            if (!mentions.length) return escapeHtml(tok.value);
            return splitMentions(tok.value, mentions)
                .map((part) => (part.type === 'mention' ? mentionHtml(part, { myId, onPrimary }) : escapeHtml(part.value)))
                .join('');
        })
        .join('');
}

/** 氣泡裡的原文（地址按鈕只顯示縮寫，複製、回覆要用完整的） */
function messagePlainText(el) {
    if (!el) return '';
    if (el.dataset?.plain != null) return el.dataset.plain; // AI 分析卡片（ai-card.js）：表格已轉成一列一行
    const copy = el.cloneNode(true);
    copy.querySelectorAll('[data-dm-address]').forEach((b) => b.replaceWith(b.dataset.dmAddress));
    return copy.textContent;
}

async function openLink(href) {
    const host = linkHost(href);
    if (!host) return;
    if (!isOwnHost(host)) {
        const ok = await (window.showConfirmDialog
            ? window.showConfirmDialog({
                  title: tp('messages.link.title', { host }, 'Open {{host}}?'),
                  message: t(
                      'messages.link.warning',
                      "This link was sent to you. Check the domain carefully — never connect your wallet or enter a seed phrase on a site you don't trust."
                  ),
                  confirmText: t('messages.link.continue', 'Open link'),
                  danger: true,
              })
            : Promise.resolve(window.confirm(host)));
        if (!ok) return;
    }
    window.open(href, '_blank', 'noopener,noreferrer');
}

async function openAddressCheck(address) {
    // 桌機 SPA：直接切到詐騙查詢分頁，不重新載入（聊天、草稿都還在）
    if (typeof window.switchTab === 'function') {
        await window.switchTab('scamcheck');
        const input = document.getElementById('scamcheck-input');
        if (input && typeof window.ScamCheckTab?.check === 'function') {
            input.value = address;
            window.ScamCheckTab.check();
            return;
        }
    }
    // 手機私訊頁是獨立頁：跳過去（上一頁回聊天）；不開新分頁——Telegram 等 webview 會跳外部瀏覽器。
    // 詐騙查詢分頁會讀 ?address= 自動查（scam-check.js takeAddressParam）
    window.location.href = `/static/index.html?address=${encodeURIComponent(address)}#scamcheck`;
}

// ============================================================================
// 檢舉：理由＋補充說明＋「同時封鎖」。快照由後端撈（被檢舉那則＋前 10 則），前端只送理由
// 外觀比照 ui-shell 的 showConfirmDialog；內容全部用 DOM API 組（訊息原文不進 innerHTML）
// ============================================================================

const REPORT_REASONS = ['scam', 'harassment', 'spam', 'other'];

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

/** 開檢舉對話框；送出成功回 { blocked }，取消回 null */
/** 表情／檢舉的 API 網址：沒給就是私訊；群組在 config.urls／reportDmMessage 的 opts 換掉 */
function reactionUrl(config, id) {
    return config?.urls?.reaction ? config.urls.reaction(id) : `/api/messages/${Number(id)}/reaction`;
}

function reportUrl(opts, id) {
    return opts?.url || `/api/messages/${Number(id)}/report`;
}

function openReportDialog(info, { url, allowBlock = true } = {}) {
    return new Promise((resolve) => {
        const overlay = el(
            'div',
            'dm-report-dialog fixed inset-0 z-[100] bg-black/60 backdrop-blur-sm flex items-end md:items-center justify-center p-4 animate-fade-in overflow-y-auto'
        );
        const card = el(
            'div',
            'w-full max-w-sm max-h-[85dvh] flex flex-col rounded-3xl bg-surface border border-borderSubtle shadow-2xl animate-fade-in-up overflow-hidden'
        );
        card.setAttribute('role', 'dialog');
        card.setAttribute('aria-modal', 'true');
        const body = el('div', 'p-6 pb-2 overflow-y-auto');
        const title = el('h3', 'font-serif text-lg text-secondary leading-tight', t('messages.report.title', 'Report this message'));
        title.id = 'dm-report-title';
        card.setAttribute('aria-labelledby', title.id);
        const quote = el(
            'p',
            'mt-2 pl-3 border-l-[3px] border-borderLight text-sm text-textMuted line-clamp-2 break-words',
            info.text.length > 80 ? `${info.text.slice(0, 80)}…` : info.text
        );
        const legend = el('p', 'mt-4 text-sm font-medium text-textMain', t('messages.report.reasonLabel', 'Why are you reporting it?'));
        const group = el('div', 'mt-2 space-y-1');
        group.setAttribute('role', 'radiogroup');
        for (const reason of REPORT_REASONS) {
            const label = el('label', 'flex items-center gap-3 px-3 py-2 rounded-xl hover:bg-surfaceHighlight cursor-pointer text-sm text-textMain');
            const radio = el('input', 'accent-[rgb(var(--color-primary))]');
            radio.type = 'radio';
            radio.name = 'dm-report-reason';
            radio.value = reason;
            label.append(radio, el('span', '', t(`messages.report.reasons.${reason}`, reason)));
            group.append(label);
        }
        const note = el(
            'textarea',
            'mt-3 w-full rounded-xl bg-background border border-borderLight px-3 py-2 text-sm text-textMain placeholder-textMuted resize-none focus:outline-none focus:border-primary/50'
        );
        note.rows = 3;
        note.maxLength = 500;
        note.placeholder = t('messages.report.notePlaceholder', 'Anything else we should know? (optional)');
        // 只有勾選框＋字可以點：以前 label 整列滿寬，手機打完補充說明、點空白收鍵盤
        // 就默默勾上「同時封鎖」（2026-10-01 DANNY 沒勾卻被封鎖）
        const blockRow = el('div', 'mt-3');
        const blockLabel = el('label', 'inline-flex items-center gap-2 py-1 text-sm text-textMain cursor-pointer');
        const blockBox = el('input', 'accent-[rgb(var(--color-danger))]');
        blockBox.type = 'checkbox';
        blockLabel.append(blockBox, el('span', '', t('messages.report.block', 'Also block this person')));
        blockRow.append(blockLabel);
        const error = el('p', 'mt-2 text-sm text-danger hidden');
        // 群組沒有「同時封鎖」（被檢舉的人還在群裡，封鎖改不了什麼）
        body.append(title, quote, legend, group, note, ...(allowBlock ? [blockRow] : []), error);

        const actions = el('div', 'flex gap-2 justify-end p-6 pt-3 shrink-0');
        const cancel = el('button', 'px-4 py-2 rounded-xl text-sm text-textMuted hover:text-secondary hover:bg-surfaceHighlight transition', t('common.cancel', 'Cancel'));
        cancel.type = 'button';
        const submit = el('button', 'px-4 py-2 rounded-xl text-sm font-medium bg-danger/10 text-danger hover:bg-danger/20 transition disabled:opacity-50', t('messages.report.submit', 'Report'));
        submit.type = 'button';
        // 勾了封鎖，按鈕字跟著變：送出前一眼看得出會不會封鎖
        blockBox.addEventListener('change', () => {
            submit.textContent = blockBox.checked
                ? t('messages.report.submitAndBlock', 'Report and block')
                : t('messages.report.submit', 'Report');
        });
        actions.append(cancel, submit);
        card.append(body, actions);
        overlay.append(card);
        document.body.appendChild(overlay);

        let settled = false;
        const finish = (value) => {
            if (settled) return;
            settled = true;
            document.removeEventListener('keydown', onKey);
            overlay.remove();
            resolve(value);
        };
        const onKey = (e) => {
            if (e.key === 'Escape') finish(null);
        };
        document.addEventListener('keydown', onKey);
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) finish(null);
        });
        cancel.addEventListener('click', () => finish(null));
        group.addEventListener('change', () => error.classList.add('hidden'));
        submit.addEventListener('click', async () => {
            const reason = group.querySelector('input:checked')?.value;
            if (!reason) {
                error.textContent = t('messages.report.pickReason', 'Pick a reason first');
                error.classList.remove('hidden');
                return;
            }
            submit.disabled = true;
            try {
                const res = await AppAPI.post(reportUrl({ url }, info.id), {
                    reason,
                    note: note.value.trim() || null,
                    ...(allowBlock ? { block: blockBox.checked } : {}),
                });
                finish({ blocked: !!res?.blocked });
            } catch (e) {
                submit.disabled = false;
                error.textContent =
                    e?.message === 'already_reported'
                        ? t('messages.report.already', "You've already reported this message")
                        : t('messages.report.failed', "Couldn't send the report. Try again.");
                error.classList.remove('hidden');
            }
        });
        group.querySelector('input')?.focus();
    });
}

/** 選單的「檢舉」：對話框送出後提示；有順便封鎖就讓頁面收掉這段對話 */
async function reportDmMessage(info, { onBlocked, url, allowBlock } = {}) {
    const result = await openReportDialog(info, { url, allowBlock });
    if (!result) return;
    window.showToast?.(
        result.blocked
            ? t('messages.report.doneBlocked', 'Report received. This person is blocked.')
            : t('messages.report.done', 'Report received. Thanks for letting us know.'),
        'success'
    );
    if (result.blocked) onBlocked?.();
}

// ============================================================================
// 回覆引用：氣泡裡的引用區塊、輸入列上方的草稿列、點引用跳到原訊息
// ============================================================================

const REPLY_BAR_SNIPPET = 50;
const MAX_JUMP_PAGES = 5; // 往前翻最多 5 頁（約 250 則）找原訊息

/**
 * 氣泡頂端的引用區塊（reply_to 是後端給的預覽：暱稱、前 100 字、是否已收回）。
 * 是按鈕：點了跳到原訊息。原訊息被收回就顯示「訊息已收回」。
 */
function quoteHtml(replyTo, isMine) {
    if (!replyTo) return '';
    const tone = isMine
        ? 'border-background/70 bg-background/15 text-background/90'
        : 'border-primary/60 bg-primary/5 text-textSecondary';
    const text = replyTo.recalled
        ? `<span class="italic opacity-80">${escapeHtml(t('messages.recalledPreview', 'Message recalled'))}</span>`
        : escapeHtml(replyTo.snippet);
    return (
        `<button type="button" data-dm-jump="${Number(replyTo.id)}" ` +
        `class="msg-quote block w-full min-w-0 text-left mb-1.5 pl-2 pr-2 py-1 border-l-[3px] rounded-r-md ${tone} text-xs leading-snug hover:brightness-95 transition">` +
        `<span class="block font-medium truncate">${escapeHtml(replyTo.from_display_name)}</span>` +
        `<span class="msg-quote-text block truncate">${text}</span></button>`
    );
}

/** 草稿列顯示什麼：「回覆 某某」＋前 50 字 */
function replyBarText(info, name) {
    const text = info.text.length > REPLY_BAR_SNIPPET ? `${info.text.slice(0, REPLY_BAR_SNIPPET)}…` : info.text;
    return { title: tp('messages.replyingTo', { name }, 'Replying to {{name}}'), snippet: text };
}

/** 選單的「複製」：整則原文，結果用 toast 告知 */
async function copyMessageText(text) {
    const ok = await copyText(text);
    window.showToast?.(
        ok ? t('messages.actions.copied', 'Copied') : t('messages.actions.copyFailed', "Couldn't copy"),
        ok ? 'success' : 'error'
    );
    return ok;
}

const MENU_GAP = 8;
const EDGE = 12;

/**
 * 選單放哪：氣泡下方放得下就下方，否則上方，都不行（長訊息）就貼底當 sheet。
 * 水平靠發送者那側對齊（自己的靠右），並夾在畫面內。
 */
function placeMenu(rect, size, viewport, isMine) {
    const left = Math.min(
        Math.max(isMine ? rect.right - size.width : rect.left, EDGE),
        viewport.width - size.width - EDGE
    );
    const below = rect.bottom + MENU_GAP;
    if (below + size.height + EDGE <= viewport.height) return { top: below, left, mode: 'below' };
    const above = rect.top - MENU_GAP - size.height;
    if (above >= EDGE) return { top: above, left, mode: 'above' };
    return { top: viewport.height - size.height - EDGE, left, mode: 'sheet' };
}

/**
 * 桌機 hover 工具列（表情、回覆、⋯）。觸控裝置由 styles.css 藏起來（改長按）。
 * 放在列上、氣泡與時間欄之間的零寬錨點，往時間那側展開：滑鼠停 0.2 秒，時間淡出、工具列接在
 * 同一個位置，不蓋到任何訊息（DANNY 2026-10-01：以前浮在氣泡上緣，一移上去就蓋到相鄰訊息）。
 * 桌機氣泡最寬扣掉工具列寬度（styles.css），窄的聊天欄也放得下。錨點的負邊距抵掉多出來的 flex gap。
 */
function messageToolsHtml({ isMine, recalled = false }) {
    const btn = (attr, icon, label) =>
        `<button type="button" ${attr} aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}" ` +
        'class="w-7 h-7 rounded-full flex items-center justify-center text-textMuted hover:text-textMain hover:bg-surfaceHighlight transition">' +
        `<i data-lucide="${icon}" class="w-4 h-4"></i></button>`;
    // 已收回的只剩「⋯」（裡面只有為我刪除）。左右鏡像：「⋯」永遠最靠氣泡，極窄的聊天欄被裁的是最外側
    const buttons = [
        ...(recalled
            ? []
            : [
                  btn('data-dm-react-open aria-haspopup="true"', 'smile-plus', t('messages.actions.react', 'React')),
                  btn('data-dm-reply', 'reply', t('messages.actions.reply', 'Reply')),
              ]),
        btn('data-dm-more aria-haspopup="menu"', 'ellipsis', t('messages.actions.more', 'More actions')),
    ];
    if (!isMine) buttons.reverse();
    return (
        `<div class="msg-tools-anchor relative w-0 shrink-0 self-end ${isMine ? '-ml-1.5' : '-mr-1.5'}">` +
        `<div class="msg-tools absolute bottom-0 ${isMine ? 'right-0' : 'left-0'} z-10 flex items-center gap-0.5 p-0.5 rounded-full bg-surface border border-borderSubtle shadow-sm whitespace-nowrap ` +
        'opacity-0 pointer-events-none transition-opacity duration-150 group-hover:delay-200 group-hover:opacity-100 group-hover:pointer-events-auto focus-within:opacity-100 focus-within:pointer-events-auto">' +
        buttons.join('') +
        '</div></div>'
    );
}


// ============================================================================
// 選單 UI：手機長按＝overlay（背景變暗＋氣泡複本＋動作清單），桌機＝popover
// 掛在 document.body：訊息容器的祖先有 transform（分頁動畫），fixed 會跑位
// ============================================================================

const LONG_PRESS_MS = 500;
const TAP_SLOP_PX = 10; // 同 click-delegator：移動超過就是滑動，不是長按

const registry = new Map(); // selector → { features, handlers, currentUserId }
let installed = false;
let current = null; // 開著的選單：{ close }
let press = null; // 進行中的長按：{ id, x, y, timer }
let lastPointerType = 'mouse';

function rowInfo(row) {
    return {
        id: Number(row.dataset.messageId),
        fromUserId: row.dataset.from,
        messageType: row.dataset.type || 'text',
        createdAt: row.dataset.ts,
        text: messagePlainText(row.querySelector('.msg-text')),
        // 自己按了哪個表情（膠囊 aria-pressed），表情列要標出來、再按一次是取消
        myReaction: row.querySelector('.msg-reactions [aria-pressed="true"]')?.dataset.dmReaction ?? null,
        row,
    };
}

/** 事件落在哪個註冊過的容器、哪一列 */
function locate(target) {
    if (!target?.closest) return null;
    for (const [selector, config] of registry) {
        const container = target.closest(selector);
        if (!container) continue;
        const row = target.closest('.msg-row');
        if (!row || !container.contains(row)) continue; // 容器裡但不在某一列上（空狀態等）
        return { container, row, config };
    }
    return null;
}

function itemsFor(hit) {
    const info = rowInfo(hit.row);
    const items = messageMenuItems({
        isMine: info.fromUserId === hit.config.currentUserId(),
        messageType: info.messageType,
        createdAt: info.createdAt,
        features: hit.config.features,
    });
    return { info, items };
}

function closeMenu() {
    const open = current;
    current = null;
    open?.close();
}

function buildMenu(items, onPick) {
    const menu = document.createElement('div');
    menu.setAttribute('role', 'menu');
    menu.className =
        'dm-action-menu fixed z-[96] min-w-[11rem] py-1 rounded-2xl bg-surface border border-borderSubtle shadow-2xl text-[15px] text-textMain animate-fade-in';
    for (const item of items) {
        if (item === 'sep') {
            const sep = document.createElement('div');
            sep.setAttribute('role', 'separator');
            sep.className = 'my-1 border-t border-borderSubtle';
            menu.append(sep);
            continue;
        }
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.setAttribute('role', 'menuitem');
        btn.dataset.dmAction = item.key;
        btn.className =
            'w-full flex items-center justify-between gap-6 px-4 py-2.5 text-left hover:bg-surfaceHighlight focus:bg-surfaceHighlight focus:outline-none' +
            (item.danger ? ' text-danger' : '');
        const label = document.createElement('span');
        label.textContent = t(`messages.actions.${item.key}`, item.key);
        const icon = document.createElement('i');
        icon.setAttribute('data-lucide', item.icon);
        icon.className = 'w-4 h-4 shrink-0';
        btn.append(label, icon);
        btn.addEventListener('click', () => onPick(item.key));
        menu.append(btn);
    }
    return menu;
}

/**
 * 方向鍵在選項間移動（焦點還不在選單裡時，第一下把它帶進來——手機 overlay 不主動聚焦，
 * 免得輸入框失焦收鍵盤）；Esc 關閉並把焦點還給觸發按鈕。
 */
function bindMenuKeys(menu, close) {
    const onKey = (e) => {
        // Tab 移出去就關：不然選單還開著、焦點已經在別處，方向鍵又會把人拉回來
        if (e.key === 'Escape' || (e.key === 'Tab' && menu.contains(document.activeElement))) {
            if (e.key === 'Tab') e.preventDefault();
            close({ restoreFocus: true });
            return;
        }
        if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
        e.preventDefault();
        const buttons = [...menu.querySelectorAll('[role="menuitem"]')];
        const i = buttons.indexOf(document.activeElement);
        const step = e.key === 'ArrowDown' ? 1 : -1;
        buttons[i < 0 ? 0 : (i + step + buttons.length) % buttons.length]?.focus();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
}

function viewport() {
    return { width: window.innerWidth, height: window.innerHeight };
}

/** 桌機：「⋯」或右鍵開 popover。anchor 是按鈕或游標位置的 rect */
function openPopover(hit, anchor, trigger, kind = 'menu') {
    closeMenu();
    const { info, items } = itemsFor(hit);
    const isMine = info.fromUserId === hit.config.currentUserId();
    let menu;
    if (kind === 'react') {
        // 😊：只有表情列
        menu = buildReactionBar(info.myReaction, (key) => {
            closeMenu();
            reactTo(hit, info, key);
        });
        menu.classList.add('fixed', 'z-[96]');
    } else {
        if (!items.length) return;
        menu = buildMenu(items, (key) => pick(hit, key, info));
    }
    document.body.appendChild(menu);
    window.AppUtils?.refreshIcons?.(menu);
    const pos = placeMenu(anchor, { width: menu.offsetWidth, height: menu.offsetHeight }, viewport(), isMine);
    menu.style.top = `${pos.top}px`;
    menu.style.left = `${pos.left}px`;

    const onOutside = (e) => {
        // 按同一顆「⋯」交給下面的 click 切換，不要在這裡先關掉又被 click 打開
        if (!menu.contains(e.target) && !trigger?.contains(e.target)) close();
    };
    document.addEventListener('pointerdown', onOutside, true);
    const unbindKeys = kind === 'react' ? bindReactKeys(menu, close) : bindMenuKeys(menu, close);
    // 只有 Esc 關閉才把焦點還給「⋯」；點別則的「⋯」換選單時還回去會讓舊工具列閃一下
    function close({ restoreFocus = false } = {}) {
        document.removeEventListener('pointerdown', onOutside, true);
        unbindKeys();
        menu.remove();
        if (current?.menu === menu) current = null;
        if (restoreFocus && trigger?.isConnected) trigger.focus();
    }
    current = { close, menu, trigger, row: hit.row, rowTop: hit.row.getBoundingClientRect().top };
    menu.querySelector('[role="menuitem"], [data-dm-react]')?.focus();
}

/** 手機：長按開 overlay。openingPointer 是還按著的那根手指——它放開時產生的 click 不算選擇 */
function openOverlay(hit, bubble, openingPointer) {
    closeMenu();
    const { info, items } = itemsFor(hit);
    if (!items.length) return;
    const isMine = info.fromUserId === hit.config.currentUserId();
    const rect = bubble.getBoundingClientRect();

    const overlay = document.createElement('div');
    overlay.className = 'dm-action-overlay fixed inset-0 z-[95] bg-black/50 animate-fade-in';
    const clone = bubble.cloneNode(true);
    clone.querySelector('.msg-tools')?.remove();
    clone.removeAttribute('id');
    clone.setAttribute('aria-hidden', 'true');
    Object.assign(clone.style, {
        position: 'fixed',
        top: `${rect.top}px`,
        left: `${rect.left}px`,
        width: `${rect.width}px`,
        maxWidth: 'none',
        margin: '0',
        overflow: 'hidden',
        pointerEvents: 'none',
    });
    const menu = buildMenu(items, (key) => {
        if (armed) pick(hit, key, info);
    });
    menu.classList.add('dm-overlay-menu');
    // 表情列（已收回的沒有）：放得下就在氣泡正上方，不然疊在動作清單上面
    const canReact = hit.config.features.has('react') && info.messageType !== 'recalled';
    const bar = canReact
        ? buildReactionBar(info.myReaction, (key) => {
              if (!armed) return;
              closeMenu();
              reactTo(hit, info, key);
          })
        : null;
    if (bar) bar.classList.add('fixed');
    overlay.append(clone, ...(bar ? [bar] : []), menu);
    document.body.appendChild(overlay);
    window.AppUtils?.refreshIcons?.(menu);

    // 表情列貼著氣泡（放得下就在上方，不然在下方），「表情列＋氣泡」當成一塊再放選單：
    // 選單只會在這塊的下方或上方，不會跟表情列疊在一起（訊息靠底時選單翻到上方就會撞）
    const vp = viewport();
    const barH = bar ? bar.offsetHeight : 0;
    const barAbove = !bar || rect.top - barH - MENU_GAP >= EDGE;
    const anchor = !bar
        ? rect
        : barAbove
          ? { ...rect, top: rect.top - barH - MENU_GAP }
          : { ...rect, bottom: rect.bottom + MENU_GAP + barH };
    const size = { width: menu.offsetWidth, height: menu.offsetHeight };
    const pos = placeMenu(anchor, size, vp, isMine);
    menu.style.top = `${pos.top}px`;
    menu.style.left = `${pos.left}px`;
    if (bar) {
        const barW = bar.offsetWidth;
        const barLeft = Math.min(Math.max(isMine ? rect.right - barW : rect.left, EDGE), vp.width - barW - EDGE);
        bar.style.left = `${barLeft}px`;
        bar.style.top = `${barAbove ? rect.top - barH - MENU_GAP : rect.bottom + MENU_GAP}px`;
    }
    if (pos.mode === 'sheet') {
        // 長訊息：氣泡複本縮到選單上方，超出的裁掉；表情列放在複本上方（放不下就蓋在複本頂端）
        const reserve = bar ? barH + MENU_GAP : 0;
        const room = pos.top - 2 * EDGE - reserve;
        const cloneTop = Math.max(EDGE + reserve, Math.min(rect.top, pos.top - EDGE - rect.height));
        clone.style.top = `${cloneTop}px`;
        clone.style.maxHeight = `${room}px`;
        if (bar) bar.style.top = `${cloneTop - reserve}px`;
    }

    // 殘影點擊：開啟的手指放開前，overlay 上的 click 一律不算；之後要有新的按下才生效
    let fingerDown = true;
    let armed = false;
    const onRelease = (e) => {
        if (e.pointerId === openingPointer) fingerDown = false;
    };
    const onPress = () => {
        if (!fingerDown) armed = true;
    };
    document.addEventListener('pointerup', onRelease, true);
    document.addEventListener('pointercancel', onRelease, true);
    overlay.addEventListener('pointerdown', onPress, true);
    overlay.addEventListener('click', (e) => {
        if (armed && e.target === overlay) close();
    });
    const unbindKeys = bindMenuKeys(menu, close);
    function close() {
        document.removeEventListener('pointerup', onRelease, true);
        document.removeEventListener('pointercancel', onRelease, true);
        unbindKeys();
        overlay.remove();
        if (current?.overlay === overlay) current = null;
    }
    current = { close, overlay, row: hit.row, rowTop: hit.row.getBoundingClientRect().top };
}


// ── 回覆草稿（每個註冊的容器一份）──────────────────────────────────────────
const replyDrafts = new Map(); // selector → { id, bar }

function clearReply(selector) {
    replyDrafts.get(selector)?.bar.remove();
    replyDrafts.delete(selector);
}

/** 送出時帶哪一則（沒有就 null） */
function getReplyTarget(selector) {
    return replyDrafts.get(selector)?.id ?? null;
}

function startReply(selector, info) {
    const config = registry.get(selector);
    const form = config?.composer?.();
    if (!form) return;
    clearReply(selector);
    const name =
        info.fromUserId === config.currentUserId()
            ? t('messages.you', 'You')
            : config.nameOf?.(info.fromUserId) || info.fromUserId;
    const { title, snippet } = replyBarText(info, name);

    // 全部用 textContent 組，訊息原文不進 innerHTML
    const bar = document.createElement('div');
    bar.className =
        'dm-reply-bar flex items-center gap-2 mb-2 pl-3 pr-1 py-1.5 border-l-[3px] border-primary bg-surfaceHighlight rounded-r-lg';
    const textBox = document.createElement('div');
    textBox.className = 'min-w-0 flex-1';
    const titleEl = document.createElement('div');
    titleEl.className = 'text-xs font-medium text-primary truncate';
    titleEl.textContent = title;
    const snippetEl = document.createElement('div');
    snippetEl.className = 'text-sm text-textMuted truncate';
    snippetEl.textContent = snippet;
    textBox.append(titleEl, snippetEl);
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.setAttribute('aria-label', t('messages.cancelReply', 'Cancel reply'));
    cancel.className =
        'w-8 h-8 shrink-0 rounded-full flex items-center justify-center text-textMuted hover:text-textMain hover:bg-surface transition';
    cancel.innerHTML = '<i data-lucide="x" class="w-4 h-4"></i>';
    cancel.addEventListener('click', () => {
        clearReply(selector);
        form.querySelector('textarea')?.focus();
    });
    bar.append(textBox, cancel);
    form.prepend(bar);
    window.AppUtils?.refreshIcons?.(bar);
    replyDrafts.set(selector, { id: info.id, bar });
    form.querySelector('textarea')?.focus();
}

/**
 * 某則被收回了（WS message_recalled）：引用它的區塊改成「訊息已收回」；
 * 草稿正在回覆它就取消並提示。兩支 UI 的 onRecalled 都要呼叫。
 */
function handleMessageRecalled(selector, messageId) {
    const container = document.querySelector(selector);
    container?.querySelectorAll(`[data-dm-jump="${Number(messageId)}"] .msg-quote-text`).forEach((el) => {
        const span = document.createElement('span');
        span.className = 'italic opacity-80';
        span.textContent = t('messages.recalledPreview', 'Message recalled');
        el.replaceChildren(span);
    });
    if (getReplyTarget(selector) === Number(messageId)) {
        clearReply(selector);
        window.showToast?.(t('messages.replyTargetRecalled', 'The message you were replying to was recalled'), 'info');
    }
}

/** 點引用：捲到原訊息並閃一下；還沒載入就往前翻（最多 MAX_JUMP_PAGES 頁） */
async function jumpToMessage(hit, messageId) {
    const find = () => hit.container.querySelector(`[data-message-id="${Number(messageId)}"]`);
    // 往前翻到一半換了對話：停手，不然會替新對話多載好幾頁
    const conversation = hit.config.currentConversation?.();
    const switched = () => conversation !== undefined && hit.config.currentConversation() !== conversation;
    let row = find();
    for (let i = 0; !row && i < MAX_JUMP_PAGES && hit.config.loadOlder; i += 1) {
        if (switched() || (await hit.config.loadOlder()) === false) break;
        row = find();
    }
    if (switched()) return;
    if (!row) {
        window.showToast?.(t('messages.quoteTooOld', "That message is too far back to show"), 'info');
        return;
    }
    // 不用 scrollIntoView：它會無視輸入列的 padding，把目標藏到輸入列底下
    const c = hit.container;
    c.scrollTop += row.getBoundingClientRect().top - c.getBoundingClientRect().top - c.clientHeight / 3;
    const bubble = row.querySelector('.msg-bubble');
    const flash = ['ring-2', 'ring-primary', 'ring-offset-2', 'ring-offset-background'];
    bubble?.classList.add(...flash);
    setTimeout(() => bubble?.classList.remove(...flash), 1200);
}

/** 表情列用方向鍵左右移動、Esc 關閉 */
function bindReactKeys(bar, close) {
    const onKey = (e) => {
        if (e.key === 'Escape' || (e.key === 'Tab' && bar.contains(document.activeElement))) {
            if (e.key === 'Tab') e.preventDefault();
            close({ restoreFocus: true });
            return;
        }
        if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
        e.preventDefault();
        const buttons = [...bar.querySelectorAll('[data-dm-react]')];
        const i = buttons.indexOf(document.activeElement);
        const step = e.key === 'ArrowRight' ? 1 : -1;
        buttons[i < 0 ? 0 : (i + step + buttons.length) % buttons.length]?.focus();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
}

/** 把那一列氣泡裡的表情膠囊換成最新的（WS reaction_updated、自己按完的回應都走這裡） */
function applyReactions(container, config, messageId, reactions) {
    const row = container?.querySelector(`[data-message-id="${Number(messageId)}"]`);
    const bubble = row?.querySelector('.msg-bubble');
    if (!bubble || row.dataset.type === 'recalled') return false;
    bubble.querySelector('.msg-reactions')?.remove();
    const me = config.currentUserId();
    const html = reactionsHtml(reactions, me, row.dataset.from === me);
    if (html) bubble.insertAdjacentHTML('beforeend', html); // 工具列在列上、不在氣泡裡，膠囊直接接在最後
    return true;
}

/** WS reaction_updated：兩支 UI 的 onReactions 都呼叫這個 */
function updateMessageReactions(selector, messageId, reactions) {
    const config = registry.get(selector);
    if (!config) return false;
    return applyReactions(document.querySelector(selector), config, messageId, reactions);
}

/** 按表情：同一個再按一次＝取消，按別的＝替換 */
async function reactTo(hit, info, key) {
    const remove = info.myReaction === key;
    try {
        const url = reactionUrl(hit.config, info.id);
        const res = remove ? await AppAPI.delete(url) : await AppAPI.put(url, { reaction: key });
        if (res?.reactions) applyReactions(hit.container, hit.config, info.id, res.reactions);
    } catch (e) {
        console.error('reactTo failed:', e);
        const text =
            e?.message === 'message_recalled'
                ? t('messages.recalledPreview', 'Message recalled')
                : t('messages.reactFailed', "Couldn't add the reaction");
        window.showToast?.(text, 'error');
    }
}

function pick(hit, key, info) {
    closeMenu();
    const handler = hit.config.handlers[key];
    if (handler) handler(info);
    else console.warn(`[dm-message-actions] 選單有「${key}」但沒註冊 handler`);
}

function cancelPress(e) {
    if (!press || (e && e.pointerId !== press.id)) return;
    clearTimeout(press.timer);
    press = null;
}

function install() {
    if (installed) return;
    installed = true;

    document.addEventListener('pointerdown', (e) => {
        lastPointerType = e.pointerType || 'mouse';
        if (e.pointerType !== 'touch' || e.isPrimary === false) return;
        const bubble = e.target.closest?.('.msg-bubble');
        // 長按連結也開我們的選單：原生「在新分頁開啟」會繞過外部連結的風險提示
        // （觸控裝置的氣泡已關掉 -webkit-touch-callout，連結的原生選單不會跳）
        if (!bubble) return;
        const hit = locate(bubble);
        if (!hit) return;
        cancelPress();
        const id = e.pointerId;
        press = {
            id,
            x: e.clientX,
            y: e.clientY,
            timer: setTimeout(() => {
                press = null;
                navigator.vibrate?.(10);
                openOverlay(hit, bubble, id);
            }, LONG_PRESS_MS),
        };
    });
    document.addEventListener('pointermove', (e) => {
        if (!press || e.pointerId !== press.id) return;
        if (Math.abs(e.clientX - press.x) > TAP_SLOP_PX || Math.abs(e.clientY - press.y) > TAP_SLOP_PX) cancelPress(e);
    });
    document.addEventListener('pointerup', cancelPress);
    document.addEventListener('pointercancel', cancelPress);

    document.addEventListener('click', (e) => {
        const link = e.target.closest?.('a[data-dm-link]');
        if (link && locate(link)) {
            e.preventDefault(); // 外部網域先跳風險提示
            openLink(link.getAttribute('href'));
            return;
        }
        const address = e.target.closest?.('[data-dm-address]');
        if (address && locate(address)) {
            e.preventDefault();
            openAddressCheck(address.dataset.dmAddress);
            return;
        }
        const jump = e.target.closest?.('[data-dm-jump]');
        if (jump) {
            const hit = locate(jump);
            if (hit) {
                e.preventDefault();
                jumpToMessage(hit, jump.dataset.dmJump);
            }
            return;
        }
        const pill = e.target.closest?.('[data-dm-reaction]');
        if (pill) {
            const hit = locate(pill);
            if (hit?.config.features.has('react')) {
                e.stopPropagation();
                reactTo(hit, rowInfo(hit.row), pill.dataset.dmReaction);
            }
            return;
        }
        const reactOpen = e.target.closest?.('[data-dm-react-open]');
        if (reactOpen) {
            const hit = locate(reactOpen);
            if (!hit) return;
            e.stopPropagation();
            if (current?.trigger === reactOpen) {
                closeMenu();
                return;
            }
            openPopover(hit, reactOpen.getBoundingClientRect(), reactOpen, 'react');
            return;
        }
        const replyBtn = e.target.closest?.('[data-dm-reply]');
        if (replyBtn) {
            const hit = locate(replyBtn);
            if (!hit) return;
            e.stopPropagation();
            pick(hit, 'reply', rowInfo(hit.row));
            return;
        }
        const btn = e.target.closest?.('[data-dm-more]');
        if (!btn) return;
        const hit = locate(btn);
        if (!hit) return;
        e.stopPropagation();
        if (current?.trigger === btn) {
            closeMenu();
            return;
        }
        openPopover(hit, btn.getBoundingClientRect(), btn);
    });

    // 滑鼠中鍵點連結走 auxclick、不走 click：不攔的話會直接開新分頁，繞過風險提示
    document.addEventListener('auxclick', (e) => {
        if (e.button !== 1) return;
        const link = e.target.closest?.('a[data-dm-link]');
        if (link && locate(link)) {
            e.preventDefault();
            openLink(link.getAttribute('href'));
        }
    });

    document.addEventListener('contextmenu', (e) => {
        const bubble = e.target.closest?.('.msg-bubble');
        if (!bubble) return;
        const hit = locate(bubble);
        if (!hit) return;
        // Android 長按會發 contextmenu：交給上面的長按，不要跳原生選單
        if (e.pointerType === 'touch' || lastPointerType === 'touch') {
            e.preventDefault();
            return;
        }
        // 已經選了氣泡裡的一段字：放行原生選單，讓使用者複製那一段
        const sel = window.getSelection?.();
        if (sel && !sel.isCollapsed && bubble.contains(sel.anchorNode)) return;
        e.preventDefault();
        const point = { top: e.clientY, bottom: e.clientY, left: e.clientX, right: e.clientX };
        openPopover(hit, point, null);
    });

    // 那則訊息被捲走了（位置變了）才關：只要有 scroll 事件就關的話，點擊前剛結束的
    // 慣性捲動（或瀏覽器把按鈕捲進畫面）晚一拍送到，會把剛打開的選單立刻關掉
    document.addEventListener(
        'scroll',
        () => {
            cancelPress();
            if (!current) return;
            const moved = !current.row.isConnected || Math.abs(current.row.getBoundingClientRect().top - current.rowTop) > 4;
            if (moved) closeMenu();
        },
        true
    );
    // Esc：沒開選單時，取消焦點所在輸入列的回覆草稿
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape' || current) return;
        for (const [selector, draft] of replyDrafts) {
            if (draft.bar.parentElement?.contains(document.activeElement)) clearReply(selector);
        }
    });
    // 桌機 popover 位置跟著版面，縮放就關；手機 overlay 是全螢幕的，鍵盤收起也會觸發
    // resize，關掉反而讓人按不到
    window.addEventListener('resize', () => {
        if (current?.menu) closeMenu();
    });
    window.addEventListener('hashchange', closeMenu);
    window.addEventListener('popstate', closeMenu);
}

/**
 * 讓 selector 對應的訊息容器有選單。可重複呼叫（後註冊的設定蓋掉前一次）。
 * config.features：已上線的項目；handlers[key](info)；currentUserId() 判斷自己的訊息。
 */
function registerMessageActions(selector, config) {
    registry.set(selector, config);
    install();
    if (config.features.has('react')) ensureReactionSprite();
}

/** 送出失敗是因為回覆的那則不能回了：取消草稿並回傳在地化提示；不是就回 null */
function handleReplySendError(selector, error) {
    const text = {
        reply_target_recalled: t('messages.replyTargetRecalled', 'The message you were replying to was recalled'),
        reply_target_not_found: t('messages.replyTargetGone', 'The message you were replying to is gone'),
    }[error?.message];
    if (!text) return null;
    clearReply(selector);
    return text;
}

export {
    registerMessageActions,
    jumpToMessage,
    reportDmMessage,
    reactionUrl,
    reportUrl,
    tokenizeMessage,
    renderMessageText,
    messagePlainText,
    linkHost,
    isOwnHost,
    REACTIONS,
    reactionsHtml,
    groupReactions,
    updateMessageReactions,
    handleReplySendError,
    quoteHtml,
    replyBarText,
    startReply,
    getReplyTarget,
    clearReply,
    handleMessageRecalled,
    RECALL_WINDOW_MS,
    canRecallMessage,
    MENU_ITEMS,
    messageMenuItems,
    messageToolsHtml,
    copyText,
    copyMessageText,
    placeMenu,
};
