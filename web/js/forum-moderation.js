// ========================================
// forum-moderation.js — 發文前的內容檢查（2026-10-01 試驗）
// ========================================
// DANNY：發文前會有一個檢測符號，通過亮綠燈才能發文。
// 打字停下來就自動問 /api/forum/posts/check；結果跟目前的標題＋內文對得上、而且是
// pass（綠）或 unavailable（檢查服務暫時不在，照常讓人發）才放行。
// 伺服器按發文時會再檢查一次（前端可以被繞過），所以這裡只管體驗。

/** 純函式：檢查結果 → 畫面狀態 */
export function moderationView(result) {
    if (!result) return { state: 'idle', canSubmit: false };
    if (result.state === 'checking') return { state: 'checking', canSubmit: false };
    if (result.status === 'pass') return { state: 'pass', canSubmit: true };
    if (result.status === 'block') return { state: 'block', canSubmit: false };
    // unavailable／請求失敗（限流、斷線）：照常讓人發，伺服器會再擋一次
    return { state: 'unavailable', canSubmit: true };
}

/** 被擋的原因（給人看的，不是代碼）：harmful_model（模型判斷該擋）、leaked_secret（貼出助記詞／私鑰） */
export function blockReasonText(result, t) {
    if (!result || result.status !== 'block') return '';
    return (result.reasons || []).map((reason) => t(`forum.moderation.reason.${reason}`)).join('\uFF1B');
}

/**
 * 伺服器回 content_blocked 時（留言、推噓），問一次 /check 拿原因組成給人看的訊息
 * （同一段內容伺服器剛檢查過，有快取）。拿不到原因就給通用訊息。
 */
export async function blockedMessage(check, title, content, t) {
    try {
        const result = await check(title, content);
        const reason = blockReasonText(result, t);
        if (reason) return `${t('forum.moderation.block')}\uFF1A${reason}`;
    } catch {
        // 原因拿不到不影響告知被擋
    }
    return t('forum.moderation.blockedToast');
}

const STYLE = {
    idle: { icon: 'shield', cls: 'text-textMuted' },
    stale: { icon: 'shield', cls: 'text-textMuted' },
    checking: { icon: 'loader-2', cls: 'text-textMuted', spin: true },
    pass: { icon: 'shield-check', cls: 'text-success' },
    block: { icon: 'shield-alert', cls: 'text-danger' },
    unavailable: { icon: 'shield-off', cls: 'text-textMuted' },
};

/**
 * 接上發文表單：opts = { titleEl, contentEl, statusEl, submitBtn, check(title, content) → Promise<result>,
 *                       t(key, vars), refreshIcons(), debounceMs }
 * 回傳 { ensureChecked(), recheck(), setBusy(on), view() }
 */
export function createModerationGate(opts) {
    const { titleEl, contentEl, statusEl, submitBtn, check, t } = opts;
    const debounceMs = opts.debounceMs ?? 900;
    let result = null; // 最後一次檢查結果（帶 key）
    let pending = null; // { key, promise }
    let timer = null;
    let busy = false; // 發文中：不要把按鈕打開（會重複送出）
    let stale = false;

    const keyOf = () => `${(titleEl?.value || '').trim()}\n${(contentEl?.value || '').trim()}`;
    const isEmpty = () => keyOf().trim() === '';

    const current = () => {
        if (isEmpty()) return { state: 'idle', canSubmit: false };
        if (pending && pending.key === keyOf()) return { state: 'checking', canSubmit: false };
        if (!result || result.key !== keyOf()) return { state: stale ? 'stale' : 'idle', canSubmit: false };
        return moderationView(result);
    };

    const render = () => {
        const view = current();
        if (statusEl) {
            const style = STYLE[view.state];
            const text =
                view.state === 'block'
                    ? `${t('forum.moderation.block')}${blockReasonText(result, t) ? `\uFF1A${blockReasonText(result, t)}` : ''}`
                    : t(`forum.moderation.${view.state}`);
            statusEl.dataset.state = view.state;
            statusEl.className = `flex items-start gap-1.5 text-xs leading-5 ${style.cls}`;
            statusEl.replaceChildren();
            const icon = document.createElement('i');
            icon.setAttribute('data-lucide', style.icon);
            icon.className = `mt-0.5 h-4 w-4 shrink-0${style.spin ? ' animate-spin' : ''}`;
            const span = document.createElement('span');
            span.textContent = text;
            statusEl.append(icon, span);
            opts.refreshIcons?.();
        }
        if (submitBtn && !busy) {
            submitBtn.disabled = !view.canSubmit;
            submitBtn.classList.toggle('opacity-50', !view.canSubmit);
            submitBtn.classList.toggle('cursor-not-allowed', !view.canSubmit);
        }
        return view;
    };

    const run = () => {
        clearTimeout(timer);
        timer = null;
        const key = keyOf();
        if (isEmpty()) {
            render();
            return Promise.resolve(current());
        }
        // 「檢查服務暫時不在」不沿用：按發文（付款前）時再問一次，服務回來了就照實檢查
        if (result && result.key === key && result.status !== 'unavailable') return Promise.resolve(render());
        if (pending && pending.key === key) return pending.promise.then(() => current());
        const [title, content] = [titleEl?.value || '', contentEl?.value || ''];
        const promise = Promise.resolve()
            .then(() => check(title, content))
            .then((r) => ({ ...r, key }))
            .catch(() => ({ status: 'unavailable', key }))
            .then((r) => {
                // 檢查期間又改了字：這份結果作廢——晚回來的舊結果不能蓋掉新的（改字時已排了下一次檢查）
                if (r.key === keyOf()) result = r;
                if (pending && pending.key === key) pending = null;
                render();
                return current();
            });
        pending = { key, promise };
        render();
        return promise;
    };

    const onInput = () => {
        stale = true;
        clearTimeout(timer);
        timer = setTimeout(run, debounceMs);
        render();
    };
    titleEl?.addEventListener('input', onInput);
    contentEl?.addEventListener('input', onInput);
    render();

    return {
        /** 按發文時：內容跟最後一次檢查的不一樣就立刻重查，回傳 { state, canSubmit } */
        ensureChecked: () => run(),
        /** 伺服器說被擋（例如前端檢查時服務還沒起來）：丟掉舊結果重查，好顯示原因 */
        recheck: () => {
            result = null;
            return run();
        },
        setBusy: (on) => {
            busy = !!on;
            if (!busy) render();
        },
        view: () => current(),
    };
}
