/**
 * 「分享這則回答」對話框（AI 回答快照分享，2026-10-05 任務 D；旗標 CONVERSATION_SHARE_ENABLED，預設關）。
 *
 * 流程：預覽（伺服器取內容、遮蔽錢包地址等）→ 使用者確認 → 建立 → 顯示連結（只有這一次）→ 複製／停止分享。
 * 底部「我的分享」列出自己還有效的連結，隨時可以停止分享。
 *
 * 安全：問題與答案是使用者／模型產生的文字，一律用 textContent 放進畫面，不經 HTML 解析；
 * 按鈕用 addEventListener（CSP 禁 inline handler）。內容由伺服器依 session＋問題取，這裡不送答案。
 */

const tr = (key, fallback, params) => {
    const text = window.I18n ? window.I18n.t(key, params) : '';
    return text && text !== key ? text : fallback;
};

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
}

const BTN_PRIMARY =
    'px-4 py-2 rounded-full text-sm font-semibold bg-primary text-background hover:brightness-110 transition disabled:opacity-50';
const BTN_GHOST =
    'px-4 py-2 rounded-full text-sm font-semibold border border-borderLight text-textMain hover:bg-surfaceHighlight transition';
const BTN_LINK = 'text-xs text-primary hover:underline';

function errorText(err) {
    if (err && err.status === 409) return tr('answerShare.errTooMany', 'You have too many active shared links (limit 100). Stop sharing some before creating another.');
    if (err && err.status === 404) return tr('answerShare.errNotFound', 'Could not find this answer. Refresh and try again.');
    if (err && err.status === 429) return tr('answerShare.errLimit', "You've used today's share limit. Try again tomorrow.");
    return tr('answerShare.errGeneric', "Couldn't share. Please try again later.");
}

function dialog() {
    const overlay = el('div', 'fixed inset-0 z-[80] bg-background/80 backdrop-blur-sm flex items-center justify-center p-4');
    const panel = el('div', 'bg-surface w-full max-w-lg max-h-[85dvh] flex flex-col rounded-2xl border border-borderSubtle shadow-2xl');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    const header = el('div', 'p-4 border-b border-borderSubtle flex justify-between items-center shrink-0');
    const title = el('h2', 'font-semibold text-secondary', tr('answerShare.title', 'Share this answer'));
    panel.setAttribute('aria-label', title.textContent);
    const closeBtn = el('button', 'w-8 h-8 rounded-full bg-background text-textMuted hover:text-secondary transition', '×');
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', tr('common.close', 'Close'));
    header.append(title, closeBtn);
    const body = el('div', 'p-4 overflow-y-auto space-y-3');
    panel.append(header, body);
    overlay.appendChild(panel);

    const onKey = (e) => {
        if (e.key === 'Escape') close();
    };
    function close() {
        document.removeEventListener('keydown', onKey);
        overlay.remove();
    }
    closeBtn.addEventListener('click', close);
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) close();
    });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);
    closeBtn.focus();
    return { body, close };
}

function previewBox(label, text, tall) {
    const box = el('div', 'rounded-xl border border-borderSubtle bg-background/50 p-3');
    box.appendChild(el('p', 'text-[11px] text-textMuted mb-1', label));
    const content = el('div', 'text-sm whitespace-pre-wrap break-words overflow-y-auto ' + (tall ? 'max-h-48' : ''), text);
    box.appendChild(content);
    return box;
}

function formatDate(iso) {
    try {
        return new Date(iso).toLocaleDateString(window.I18n && window.I18n.getLanguage ? window.I18n.getLanguage() : undefined);
    } catch (_err) {
        return String(iso || '').slice(0, 10);
    }
}

async function renderMyShares(container, onChanged) {
    container.replaceChildren();
    container.appendChild(el('p', 'text-xs font-semibold text-textMuted', tr('answerShare.myShares', 'My shared links')));
    let items = [];
    try {
        const data = await window.AppAPI.get('/api/share/answers');
        items = (data && data.items) || [];
    } catch (_err) {
        return;
    }
    if (!items.length) {
        container.appendChild(el('p', 'text-xs text-textMuted/70', tr('answerShare.noShares', 'No active shared links')));
        return;
    }
    // 刪除對話不會讓連結失效、停止分享後第三方的預覽卡也可能留一陣子：講清楚，免得使用者以為刪了就沒了
    container.appendChild(el('p', 'text-[11px] text-textMuted/80', tr('answerShare.deleteNote', 'Deleting a chat does not disable links you already shared — stop sharing them here.')));
    container.appendChild(el('p', 'text-[11px] text-textMuted/80', tr('answerShare.previewCacheNote', 'After you stop sharing, link previews that social apps already fetched may linger for a while.')));
    items.forEach((item) => {
        const row = el('div', 'flex items-center gap-2 rounded-lg bg-background/50 px-3 py-2');
        const info = el('div', 'min-w-0 flex-1');
        info.appendChild(el('p', 'text-xs truncate', item.question));
        info.appendChild(
            el('p', 'text-[11px] text-textMuted', tr('answerShare.expiresOn', 'Expires {{date}}', { date: formatDate(item.expires_at) }))
        );
        const stop = el('button', BTN_LINK + ' shrink-0', tr('answerShare.stop', 'Stop sharing'));
        stop.type = 'button';
        stop.addEventListener('click', async () => {
            stop.disabled = true;
            try {
                await window.AppAPI.delete('/api/share/answers/' + encodeURIComponent(item.id));
                window.showToast?.(tr('answerShare.stopped', 'Stopped sharing — the link no longer works'), 'success');
                if (onChanged) onChanged(item.id);
                await renderMyShares(container, onChanged);
            } catch (err) {
                stop.disabled = false;
                window.showToast?.(errorText(err), 'error');
            }
        });
        row.append(info, stop);
        container.appendChild(row);
    });
}

function renderCreated(ui, made, sharesBox) {
    ui.body.replaceChildren();
    ui.body.appendChild(el('p', 'text-sm', tr('answerShare.linkReady', "Link created. You won't see the full link again after closing — you can stop sharing any time below.")));
    const input = el('input', 'w-full rounded-lg border border-borderSubtle bg-background px-3 py-2 text-xs');
    input.type = 'text';
    input.readOnly = true;
    input.value = made.url;
    input.addEventListener('focus', () => input.select());
    const actions = el('div', 'flex items-center gap-2');
    const copy = el('button', BTN_PRIMARY, tr('answerShare.copyLink', 'Copy link'));
    copy.type = 'button';
    copy.addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(made.url);
            window.showToast?.(tr('answerShare.linkCopied', 'Link copied'), 'success');
        } catch (_err) {
            input.focus();
            input.select();
        }
    });
    const done = el('button', BTN_GHOST, tr('common.close', 'Close'));
    done.type = 'button';
    done.addEventListener('click', ui.close);
    actions.append(copy, done);
    ui.body.append(input, actions, sharesBox);
    renderMyShares(sharesBox, () => {});
}

/** 打開對話框。sessionId＋question 讓伺服器找出是哪一輪（答案不從前端送）。 */
export async function openAnswerShare({ sessionId, question }) {
    const ui = dialog();
    const sharesBox = el('div', 'space-y-2 pt-2 border-t border-borderSubtle');
    ui.body.appendChild(el('p', 'text-sm text-textMuted', tr('answerShare.loading', 'Preparing preview…')));

    let preview;
    try {
        preview = await window.AppAPI.post('/api/share/answers/preview', { session_id: sessionId, question });
    } catch (err) {
        ui.body.replaceChildren(el('p', 'text-sm text-danger', errorText(err)));
        return;
    }

    ui.body.replaceChildren();
    ui.body.appendChild(
        el('p', 'text-sm', tr('answerShare.publicNotice', 'Anyone with the link can see the content below (no login needed). The link expires in {{days}} days and you can stop sharing any time.', { days: preview.ttl_days }))
    );
    if (preview.redactions > 0) {
        ui.body.appendChild(
            el('p', 'text-xs text-primary', tr('answerShare.redacted', '{{count}} wallet address(es), transaction hash(es) or email(s) were masked automatically.', { count: preview.redactions }))
        );
    }
    ui.body.appendChild(
        el('p', 'text-xs font-semibold text-textMain', tr('answerShare.reviewNotice', "Make sure it doesn't include anything you don't want public, such as position sizes or account details."))
    );
    ui.body.append(
        previewBox(tr('answerShare.questionLabel', 'Question'), preview.question, false),
        previewBox(tr('answerShare.answerLabel', "The AI's answer"), preview.answer, true)
    );
    if (preview.truncated) {
        ui.body.appendChild(el('p', 'text-xs text-textMuted', tr('answerShare.truncated', 'The answer is long, so only the first part is shared.')));
    }

    const actions = el('div', 'flex items-center justify-end gap-2 pt-1');
    const cancel = el('button', BTN_GHOST, tr('common.cancel', 'Cancel'));
    cancel.type = 'button';
    cancel.addEventListener('click', ui.close);
    const create = el('button', BTN_PRIMARY, tr('answerShare.create', 'Create share link'));
    create.type = 'button';
    create.addEventListener('click', async () => {
        create.disabled = true;
        create.textContent = tr('answerShare.creating', 'Creating…');
        try {
            const made = await window.AppAPI.post('/api/share/answers', { session_id: sessionId, question });
            renderCreated(ui, made, sharesBox);
        } catch (err) {
            create.disabled = false;
            create.textContent = tr('answerShare.create', 'Create share link');
            window.showToast?.(errorText(err), 'error');
        }
    });
    actions.append(cancel, create);
    ui.body.append(actions, sharesBox);
    renderMyShares(sharesBox, () => {});
}
