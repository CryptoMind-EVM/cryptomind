/**
 * 社群列表的搜尋（Object.assign 進 SocialHub，方法裡的 this＝SocialHub）：找人、找群組、找訊息。
 *
 * - 打字停 300ms 才打 /api/chat-search；結果蓋在對話列表的位置，分類列先收起來
 * - 清掉（✕、Esc、刪光）回到列表；搜尋中來訊不會把結果洗掉（renderConvList 看 searchQuery 先不畫）
 * - 點訊息：打開那段對話並捲到那則、閃一下（一次往前翻 100 則，最多 5 次）；
 *   手機也在好友頁開（私訊聊天室統一成同一個，2026-10-05）
 */

import { avatarColorClass, avatarInitial } from './avatar.js';
import { jumpToMessage } from './dm-message-actions.js';
import { escapeMessageAttr } from './messages.js';

const SEARCH_DEBOUNCE_MS = 300;
const JUMP_PAGE_SIZE = 100;

const t = (key, fallback) => {
    const text = window.I18n ? window.I18n.t(key) : '';
    return text && text !== key ? text : fallback || key;
};
const esc = escapeMessageAttr;

/** 把 q 在文字裡出現的地方標起來（不分大小寫）；先切再各段轉義，不會切到 &amp; 之類 */
function highlight(text, q) {
    const raw = String(text || '');
    if (!q) return esc(raw);
    const pattern = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
    let out = '';
    let last = 0;
    for (const m of raw.matchAll(pattern)) {
        out += esc(raw.slice(last, m.index));
        out += `<mark class="bg-primary/20 text-inherit rounded-sm px-0.5">${esc(m[0])}</mark>`;
        last = m.index + m[0].length;
    }
    return out + esc(raw.slice(last));
}

function sectionTitle(text, count) {
    return `<h3 class="px-4 pt-3 pb-1.5 text-[11px] font-semibold text-textMuted">${esc(text)} · ${count}</h3>`;
}

const SocialSearch = {
    searchQuery: '',
    _searchSeq: 0,
    _searchTimer: null,
    _pendingJump: null,

    /** 搜尋框的事件（模板注入一次就綁一次；直接 addEventListener，嚴格 CSP 下也能用） */
    bindSearch: function () {
        const input = document.getElementById('social-search');
        if (!input || input.dataset.searchBound) return;
        input.dataset.searchBound = '1';
        input.addEventListener('input', () => this.onSearchInput(input.value));
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.clearSearch();
                input.blur();
            }
        });
        document.getElementById('social-search-clear')?.addEventListener('click', () => {
            this.clearSearch();
            input.focus();
        });
    },

    onSearchInput: function (value) {
        const q = String(value || '').trim().slice(0, 50);
        clearTimeout(this._searchTimer);
        document.getElementById('social-search-clear')?.classList.toggle('hidden', !value);
        if (!q) {
            this.clearSearch({ keepInput: true });
            return;
        }
        this.searchQuery = q;
        this._setSearching(true);
        this._searchTimer = setTimeout(() => this.runSearch(q), SEARCH_DEBOUNCE_MS);
    },

    _setSearching: function (on) {
        const sidebar = document.getElementById('social-conv-sidebar');
        if (sidebar) sidebar.toggleAttribute('data-searching', on); // styles.css：搜尋時分類列收起來
    },

    runSearch: async function (q) {
        const seq = ++this._searchSeq;
        const listEl = document.getElementById('social-conv-list');
        if (!listEl) return;
        listEl.innerHTML =
            '<div class="py-10 flex justify-center"><div class="animate-spin w-5 h-5 border-2 border-primary border-t-transparent rounded-full"></div></div>';
        try {
            const res = await AppAPI.get(`/api/chat-search?q=${encodeURIComponent(q)}`, { retries: 0 });
            if (seq !== this._searchSeq || q !== this.searchQuery) return;
            listEl.innerHTML = this.searchResultsHtml(res || {}, q);
            listEl.scrollTop = 0;
        } catch (e) {
            if (seq !== this._searchSeq) return;
            console.warn('[SocialHub] search failed:', e);
            listEl.innerHTML = `<div class="p-6 text-center text-sm text-danger">${esc(t('friends.search.failed', "Search didn't work. Try again."))}</div>`;
        }
        window.AppUtils?.refreshIcons();
    },

    /** 清掉搜尋、回到對話列表 */
    clearSearch: function (opts = {}) {
        clearTimeout(this._searchTimer);
        this._searchSeq += 1; // 還在路上的搜尋結果作廢
        const wasSearching = !!this.searchQuery;
        this.searchQuery = '';
        const input = document.getElementById('social-search');
        if (input && !opts.keepInput) input.value = '';
        if (!opts.keepInput) document.getElementById('social-search-clear')?.classList.add('hidden');
        this._setSearching(false);
        if (wasSearching || opts.keepInput) this.renderConvList();
    },

    searchResultsHtml: function (res, q) {
        const contacts = res.contacts || [];
        const groups = res.groups || [];
        const messages = res.messages || [];
        if (!contacts.length && !groups.length && !messages.length) {
            return `<div class="py-12 px-6 flex flex-col items-center text-center text-textMuted">
                        <i data-lucide="search-x" class="w-8 h-8 mb-2 opacity-60"></i>
                        <p class="text-sm">${esc(t('friends.search.empty', 'No results'))}</p>
                    </div>`;
        }
        let html = '';
        if (contacts.length) {
            html += sectionTitle(t('friends.search.contacts', 'People'), contacts.length);
            html += contacts
                .map((c) => {
                    const name = c.display_name || c.username || c.user_id;
                    const sub = c.display_name ? `@${c.username}` : '';
                    return `<div data-click="socialOpenConversation" data-user-id="${encodeURIComponent(c.user_id)}" data-username="${esc(name)}"
                                class="flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-surfaceHighlight transition">
                                <div class="w-9 h-9 rounded-full ${avatarColorClass(c.user_id)} flex items-center justify-center text-sm font-bold shrink-0">${esc(avatarInitial(name))}</div>
                                <div class="min-w-0">
                                    <p class="text-sm font-bold text-textMain truncate">${highlight(name, q)}</p>
                                    ${sub ? `<p class="text-xs text-textMuted truncate">${highlight(sub, q)}</p>` : ''}
                                </div>
                            </div>`;
                })
                .join('');
        }
        if (groups.length) {
            html += sectionTitle(t('friends.search.groups', 'Groups'), groups.length);
            html += groups
                .map(
                    (g) => `<div data-click="SocialHub.openGroup" data-click-arg="${Number(g.id)}"
                                class="flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-surfaceHighlight transition">
                                <div class="w-9 h-9 rounded-full bg-surfaceHighlight text-textMuted flex items-center justify-center shrink-0"><i data-lucide="users" class="w-4 h-4"></i></div>
                                <p class="min-w-0 text-sm font-bold text-textMain truncate">${highlight(g.name, q)} <span class="text-[11px] font-normal text-textMuted">(${Number(g.member_count || 0)})</span></p>
                            </div>`
                )
                .join('');
        }
        if (messages.length) {
            html += sectionTitle(t('friends.search.messages', 'Messages'), messages.length);
            html += messages.map((m) => this.searchMessageRowHtml(m, q)).join('');
        }
        return html;
    },

    searchMessageRowHtml: function (m, q) {
        const isGroup = m.kind === 'group';
        const args = isGroup
            ? ['group', Number(m.group_id), Number(m.message_id)]
            : ['dm', String(m.other_user_id), Number(m.message_id), String(m.chat_name || '')];
        const avatar = isGroup
            ? '<div class="w-9 h-9 rounded-full bg-surfaceHighlight text-textMuted flex items-center justify-center shrink-0"><i data-lucide="users" class="w-4 h-4"></i></div>'
            : `<div class="w-9 h-9 rounded-full ${avatarColorClass(m.other_user_id)} flex items-center justify-center text-sm font-bold shrink-0">${esc(avatarInitial(m.chat_name || ''))}</div>`;
        // 群組要知道是誰說的；私訊對方說的就不重複寫名字，自己說的標「你」
        const who = isGroup ? `${m.from_name || ''}: ` : m.from_user_id === m.other_user_id ? '' : `${t('groups.you', 'You')}: `;
        const time = window.FriendsUI ? window.FriendsUI.formatTime(m.created_at) : '';
        return `<div data-click="SocialHub.openSearchMessage" data-click-args="${encodeURIComponent(JSON.stringify(args))}"
                    class="flex items-start gap-3 px-4 py-2.5 cursor-pointer hover:bg-surfaceHighlight transition">
                    ${avatar}
                    <div class="flex-1 min-w-0">
                        <div class="flex justify-between items-baseline gap-2">
                            <p class="text-sm font-bold text-textMain truncate">${esc(m.chat_name || '')}</p>
                            <span class="text-[10px] text-textMuted shrink-0">${esc(time)}</span>
                        </div>
                        <p class="text-xs text-textMuted line-clamp-2 break-words">${esc(who)}${highlight(m.snippet, q)}</p>
                    </div>
                </div>`;
    },

    /** 點搜尋到的訊息：打開那段對話，載完再跳到那則 */
    openSearchMessage: function (kind, chatId, messageId, name) {
        if (kind === 'group') {
            this._pendingJump = { key: `group:${Number(chatId)}`, messageId: Number(messageId) };
            this.openGroup(chatId);
            return;
        }
        const userId = encodeURIComponent(chatId);
        this._pendingJump = { key: `dm:${userId}`, messageId: Number(messageId) };
        this.openConversation(userId, name);
    },

    /** loadMessages／loadGroup 畫完第一頁時叫：有等著跳的訊息就跳過去 */
    runPendingJump: function (key) {
        const pending = this._pendingJump;
        if (!pending || pending.key !== key) return;
        this._pendingJump = null;
        const container = document.getElementById('social-messages-container');
        if (!container) return;
        // 不在第一頁：要往前翻好幾頁（慢網路要幾秒），先說一聲不然以為沒反應
        if (!container.querySelector(`[data-message-id="${pending.messageId}"]`)) {
            window.showToast?.(t('friends.search.jumping', 'Finding that message…'), 'info');
        }
        jumpToMessage(
            {
                container,
                config: {
                    currentConversation: () => (this.currentGroupId ? `g${this.currentGroupId}` : this.currentConversationId),
                    loadOlder: () =>
                        this.currentGroupId
                            ? this.loadMoreGroupMessages(JUMP_PAGE_SIZE)
                            : this.loadMoreMessages(JUMP_PAGE_SIZE),
                },
            },
            pending.messageId
        );
    },
};

export { highlight, JUMP_PAGE_SIZE, SocialSearch };
