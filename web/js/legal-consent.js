// 條款／隱私政策改版後的同意提示（2026-09-27；版本號與流程見 core/legal.py）
// /api/user/me 回 legal: {version, accepted}；accepted=false 就顯示 #legal-consent-card
// （手機在上方、桌機在右下角的聊天輸入列上方，不擋操作）。只能按「我已閱讀並同意」收起——要留同意紀錄，
// 所以沒有關閉鈕。POST /api/user/legal/accept 成功才收；409＝條款剛又改版，請重新整理。
//
// 剛登入時 currentUser 是登入流程重建的、還沒有 /me 的欄位（legal === undefined）→
// 自己查一次（同一個使用者只查一次）；重新整理頁面時 auth 還原 session 已經帶回 legal，不會多打。

const CARD_ID = 'legal-consent-card';

function _t(key, fallback) {
    const text = window.I18n ? window.I18n.t(key) : '';
    return text && text !== key ? text : fallback;
}

function _uid(user) {
    return (user && (user.user_id || user.uid)) || null;
}

function _fetchLegal(uid) {
    const state = (window.__legalConsentFetch = window.__legalConsentFetch || {});
    if (state.uid === uid && state.promise) return state.promise;
    state.uid = uid;
    state.promise = Promise.resolve()
        .then(() => window.AppAPI.get('/api/user/me'))
        .then((res) => (res && res.user && res.user.legal) || null)
        .catch(() => null);
    return state.promise;
}

function _setError(text) {
    const el = document.getElementById('legal-consent-error');
    if (!el) return;
    el.textContent = text || '';
    el.classList.toggle('hidden', !text);
}

const LegalConsent = {
    _busy: false,
    _pending: false,

    // 同意卡現在是不是顯示中。同意卡是 fixed 浮層，桌機落在聊天欄中下段，會蓋住歡迎畫面裡的
    // 新手三步清單（2026-10-06）；清單據此讓路，同意後再出現。
    isPending() {
        return this._pending === true;
    },

    _setPending(card, pending) {
        card.classList.toggle('hidden', !pending);
        if (this._pending === pending) return;
        this._pending = pending;
        try {
            window.dispatchEvent(new CustomEvent('legal:consent-pending', { detail: { pending } }));
        } catch (_) {
            // 沒有 CustomEvent 的環境：只是少一個通知，卡片本身照常顯示／收起
        }
    },

    sync(isLoggedIn, user) {
        const card = document.getElementById(CARD_ID);
        if (!card) return;
        if (!isLoggedIn || !user) {
            this._setPending(card, false);
            return;
        }
        if (user.legal === undefined && window.AppAPI && window.AppAPI.get) {
            this._setPending(card, false);
            const uid = _uid(user);
            _fetchLegal(uid).then((legal) => {
                const AM = window.AuthManager;
                const current = AM && AM.currentUser;
                if (!current || _uid(current) !== uid) return; // 期間換了帳號或登出
                if (typeof AM._mergeCurrentUser === 'function') AM._mergeCurrentUser({ legal });
                this.sync(true, AM.currentUser);
            });
            return;
        }
        const legal = user.legal;
        this._setPending(card, !!(legal && legal.accepted === false));
    },

    async accept() {
        const AM = window.AuthManager;
        const legal = AM && AM.currentUser && AM.currentUser.legal;
        if (this._busy || !legal || !legal.version) return;
        this._busy = true;
        const btn = document.getElementById('legal-consent-accept');
        if (btn) btn.disabled = true;
        _setError('');
        try {
            await window.AppAPI.post('/api/user/legal/accept', { version: legal.version });
            if (typeof AM._mergeCurrentUser === 'function') {
                AM._mergeCurrentUser({ legal: { ...legal, accepted: true } });
            }
            this.sync(true, AM.currentUser);
        } catch (e) {
            _setError(
                e && e.status === 409
                    ? _t(
                          'auth.legalConsent.outdated',
                          'The terms were just updated again. Please reload the page and review them.'
                      )
                    : _t('auth.legalConsent.failed', "Couldn't save, please try again")
            );
        } finally {
            this._busy = false;
            if (btn) btn.disabled = false;
        }
    },
};

// auth.js 的 _updateUI 一開頭就發 auth:changed（currentUser 已是最新）
window.addEventListener('auth:changed', (e) => {
    LegalConsent.sync(!!(e && e.detail && e.detail.isLoggedIn), window.AuthManager?.currentUser);
});

window.LegalConsent = LegalConsent;
export { LegalConsent };
