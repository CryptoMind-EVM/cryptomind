/**
 * AI 分析卡片（message_type = 'ai_card'，2026-10-04 DANNY）
 *
 * 聊天室 AI 助理的回答「分享到聊天室」會送出一則卡片訊息（後端 core/ai_card.py）：
 * 內容是回答的 markdown 原文，這裡把它畫成卡片——表格、標題、清單照原樣顯示，
 * 夾在裡面的 HTML 先轉回 markdown（ai-markdown.js），不佔一般訊息的 500 字。
 * 三個聊天介面（好友頁私訊、群組、手機私訊頁）共用這支：只負責「這則訊息的那一列」。
 */

import { aiMarkdownToPlain, renderAiMarkdown } from './ai-markdown.js';
import { quoteHtml, reactionsHtml } from './dm-message-actions.js';

const AI_CARD_TYPE = 'ai_card';
const COLLAPSE_CHARS = 280; // 超過就預設收合（聊天室裡一張卡片不要吃掉整個畫面）

const t = (key, fallback) => (window.I18n ? window.I18n.t(key) : fallback);

function esc(value) {
    return String(value ?? '').replace(
        /[&<>"']/g,
        (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]
    );
}

/** 卡片氣泡：比一般訊息寬、不管自己或對方都是卡片底色（一般氣泡自己是藍底白字，表格在上面看不清楚） */
const AI_CARD_BUBBLE_CLASS =
    'msg-bubble ai-card relative min-w-0 px-3.5 py-2.5 rounded-2xl text-sm leading-relaxed bg-surface border border-borderSubtle text-textMain';

/** 列表預覽、toast 用：卡片不顯示整篇 markdown */
function isAiCard(msg) {
    return msg?.message_type === AI_CARD_TYPE;
}

function aiCardPreviewText() {
    return `✨ ${t('messages.aiCard.title', 'AI analysis')}`;
}

/** 卡片裡面（引用＋標題＋內文＋展開鈕＋提醒＋表情）；.msg-text 的 data-plain 給選單「複製」用 */
function aiCardInnerHtml(msg, { myId } = {}) {
    const content = String(msg.content || '');
    const long = content.length > COLLAPSE_CHARS;
    const more = t('messages.aiCard.expand', 'Show all');
    const less = t('messages.aiCard.collapse', 'Collapse');
    return (
        quoteHtml(msg.reply_to, false) +
        `<div class="ai-card-head"><span aria-hidden="true">✨</span><span>${esc(t('messages.aiCard.title', 'AI analysis'))}</span></div>` +
        `<div class="msg-text ai-card-body ai-md${long ? ' ai-card-collapsed' : ''}" data-plain="${esc(aiMarkdownToPlain(content))}">${renderAiMarkdown(content)}</div>` +
        (long
            ? `<button type="button" class="ai-card-toggle" data-ai-card-toggle aria-expanded="false" data-more="${esc(more)}" data-less="${esc(less)}">${esc(more)}</button>`
            : '') +
        `<div class="ai-card-note">${esc(t('messages.aiCard.note', 'AI-generated, for reference only'))}</div>` +
        reactionsHtml(msg.reactions, myId, false)
    );
}

/**
 * 卡片訊息的整列（各介面傳自己的 rowOpen／meta／tools，結構跟一般氣泡的那一列相同）。
 * avatar／sender 只有群組用（別人的卡片也要看得出誰分享的）。
 */
function aiCardRowHtml({ rowOpen, msg, isMine, meta, tools, avatar = '', sender = '', myId }) {
    const bubble = `<div class="${AI_CARD_BUBBLE_CLASS}">${sender}${aiCardInnerHtml(msg, { myId })}</div>`;
    return isMine
        ? `${rowOpen}${meta}${tools}${bubble}</div>`
        : `${rowOpen}${avatar}${bubble}${tools}${meta}</div>`;
}

let toggleBound = false;
/** 「展開全文／收合」：卡片會被整列重畫，用文件層的事件代理（只綁一次） */
function bindAiCardToggle() {
    if (toggleBound || typeof document === 'undefined') return;
    toggleBound = true;
    document.addEventListener('click', (event) => {
        const button = event.target?.closest?.('[data-ai-card-toggle]');
        if (!button) return;
        const body = button.closest('.ai-card')?.querySelector('.ai-card-body');
        if (!body) return;
        const open = !body.classList.toggle('ai-card-collapsed');
        button.textContent = open ? button.dataset.less : button.dataset.more;
        button.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
}
bindAiCardToggle();

export { AI_CARD_TYPE, isAiCard, aiCardPreviewText, aiCardRowHtml, bindAiCardToggle };
