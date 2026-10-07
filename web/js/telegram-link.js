// ========================================
// telegram-link.js - Telegram Binding Management
// ========================================

const TelegramLinkApp = {
    initialized: false,
    _countdownTimer: null,
    _pollTimer: null,
    _polling: false,

    async init() {
        // Ensure AuthManager is ready
        if (typeof AuthManager === 'undefined') {
            this.renderError('System Error: Auth module missing');
            return;
        }

        if (!AuthManager.isLoggedIn()) {
            this.renderUnbound();
            return;
        }

        this.initialized = true;
        await this.loadStatus();
    },

    async loadStatus() {
        const container = document.getElementById('telegram-link-content');
        if (!container) return;

        container.innerHTML = '<div class="text-center py-4"><i data-lucide="loader" class="w-5 h-5 animate-spin mx-auto text-textMuted"></i></div>';
        if (window.AppUtils) window.AppUtils.refreshIcons();

        try {
            const data = await AppAPI.get('/api/telegram/status');

            if (data.bound) {
                this.renderBound({
                    telegram_id: data.telegram_id,
                    telegram_username: data.telegram_username,
                    linked_at: data.linked_at
                });
            } else {
                this.renderUnbound();
            }
        } catch (e) {
            // If API fails, show unbound state
            this.renderUnbound();
        }
    },

    // 更新右上角狀態徽章。模板中徽章寫死「Loading」且無人更新，
    // 導致狀態載入完成後仍一直顯示 Loading。
    _setBadge(bound) {
        const badge = document.getElementById('telegram-status-badge');
        if (!badge) return;
        const t = (key) => (window.I18n ? window.I18n.t(key) : key);
        if (bound) {
            badge.className = 'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium shrink-0 whitespace-nowrap bg-success/10 text-success';
            badge.innerHTML = `<i data-lucide="check" class="w-3 h-3"></i><span>${t('telegram.connected')}</span>`;
        } else {
            badge.className = 'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium shrink-0 whitespace-nowrap bg-surfaceHighlight text-textMuted';
            badge.innerHTML = `<i data-lucide="x" class="w-3 h-3"></i><span>${t('telegram.not_bound')}</span>`;
        }
        if (window.AppUtils) window.AppUtils.refreshIcons();
    },

    renderBound(state) {
        const container = document.getElementById('telegram-link-content');
        const t = (key) => (window.I18n ? window.I18n.t(key) : key);

        if (!container) return;
        this._setBadge(true);

        const linkedAt = state.linked_at
            ? new Date(state.linked_at).toLocaleDateString()
            : '-';

        container.innerHTML = `
            <div class="bg-success/5 rounded-xl p-4 border border-success/10">
                <div class="flex items-center gap-3">
                    <div class="w-10 h-10 rounded-full bg-success/20 flex items-center justify-center">
                        <i data-lucide="check-circle" class="w-5 h-5 text-success"></i>
                    </div>
                    <div>
                        <p class="text-sm font-bold text-success">${t('telegram.bound_as')} @${AppUtils.escapeHtml(state.telegram_username || 'unknown')}</p>
                        <p class="text-xs text-textMuted">${t('telegram.linked_at')}: ${linkedAt}</p>
                    </div>
                </div>
                <button
                    data-click="TelegramLinkApp.handleRebind"
                    class="w-full mt-4 py-2.5 bg-[#229ED9]/10 hover:bg-[#229ED9]/20 text-[#229ED9] border border-[#229ED9]/20 font-bold rounded-xl transition flex items-center justify-center gap-2"
                >
                    <i data-lucide="repeat" class="w-4 h-4"></i>
                    ${t('telegram.rebind')}
                </button>
                <button
                    data-click="TelegramLinkApp.handleUnbind"
                    class="w-full mt-2 py-2.5 bg-danger/10 hover:bg-danger/20 text-danger border border-danger/20 font-bold rounded-xl transition flex items-center justify-center gap-2"
                >
                    <i data-lucide="unlink" class="w-4 h-4"></i>
                    ${t('telegram.unbind')}
                </button>
            </div>
        `;

        if (window.AppUtils) window.AppUtils.refreshIcons();
    },

    renderUnbound() {
        const container = document.getElementById('telegram-link-content');
        const t = (key) => (window.I18n ? window.I18n.t(key) : key);

        if (!container) return;
        this._setBadge(false);

        container.innerHTML = `
            <div class="${INFO_PANEL_CLASS} mb-4">
                <div class="flex items-start gap-3">
                    <i data-lucide="info" class="w-4 h-4 text-primary/60 mt-0.5 flex-shrink-0"></i>
                    <p class="text-sm text-textMuted leading-relaxed">${t('telegram.not_bound_desc')}</p>
                </div>
            </div>
            <button
                data-click="TelegramLinkApp.handleConnect"
                class="w-full py-3 bg-[#229ED9]/10 hover:bg-[#229ED9]/20 text-[#229ED9] border border-[#229ED9]/20 font-bold rounded-xl transition flex items-center justify-center gap-2"
            >
                <svg viewBox="0 0 24 24" fill="currentColor" class="w-5 h-5">
                    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69a.2.2 0 00-.05-.18c-.06-.05-.14-.03-.21-.02-.09.02-1.49.95-4.22 2.79-.4.27-.76.41-1.08.4-.36-.01-1.04-.2-1.55-.37-.63-.2-1.12-.31-1.08-.66.02-.18.27-.36.74-.55 2.92-1.27 4.86-2.11 5.83-2.51 2.78-1.16 3.35-1.36 3.73-1.36.08 0 .27.02.39.12.1.08.13.19.14.27-.01.06.01.24 0 .37z"/>
                </svg>
                ${t('telegram.connect')}
            </button>
        `;

        if (window.AppUtils) window.AppUtils.refreshIcons();
    },

    async handleConnect() {
        const t = (key) => (window.I18n ? window.I18n.t(key) : key);

        try {
            const data = await AppAPI.post('/api/telegram/link-token', {});

            // 一鍵綁定（PR-7）：直接開 t.me/<bot>?start=link_<code>，Telegram 按「開始」就綁好。
            // 取 token 是非同步的，有些瀏覽器會擋這次彈窗——modal 的主按鈕是同一個連結，一按就開。
            if (data.deep_link) this.openDeepLink(data.deep_link);
            this.showInstructionsModal(
                data.token,
                data.bot_username,
                data.expires_in,
                data.deep_link
            );
        } catch (e) {
            if (typeof window.showToast === 'function') {
                window.showToast(e.message || t('telegram.connect_failed'), 'error');
            }
        }
    },

    // 開 Telegram deep link：Mini App 裡要用 openTelegramLink（一般 window.open 會跳出 Mini App），
    // 網頁用新分頁（t.me 會再轉 tg:// 叫起 App）。只接受 https://t.me/ 開頭。
    openDeepLink(url) {
        if (typeof url !== 'string' || !url.startsWith('https://t.me/')) return false;
        const tg = window.Telegram && window.Telegram.WebApp;
        if (window.IS_TELEGRAM_MINIAPP && tg && typeof tg.openTelegramLink === 'function') {
            try {
                tg.openTelegramLink(url);
                return true;
            } catch (_) {
                // 舊版 Telegram 客戶端沒有這個方法的實作 → 退回新分頁
            }
        }
        const win = window.open(url, '_blank');
        if (!win) return false;
        try {
            win.opener = null;
        } catch (_) {
            // 跨網域後拿不到 opener，無所謂
        }
        return true;
    },

    showInstructionsModal(token, botUsername, expiresIn, deepLink) {
        const t = (key) => (window.I18n ? window.I18n.t(key) : key);

        // Create modal overlay
        const overlay = document.createElement('div');
        overlay.id = 'telegram-link-modal-overlay';
        overlay.className = 'fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 overflow-y-auto';
        overlay.onclick = (e) => {
            if (e.target === overlay) this.closeModal();
        };

        // Create modal content
        const modal = document.createElement('div');
        modal.id = 'telegram-link-modal';
        modal.className = 'bg-surface border border-borderLight rounded-2xl w-full max-w-md max-h-[80dvh] overflow-y-auto custom-scrollbar shadow-2xl';

        // 手動 /link 指令：有 deep link 時收進 <details> 當備援，沒有時照舊是主要步驟
        const manualSteps = `
                <div class="flex items-start gap-3">
                    <div class="w-6 h-6 rounded-full bg-primary/20 text-primary text-xs font-bold flex items-center justify-center shrink-0">1</div>
                    <div>
                        <p class="text-sm text-secondary font-medium">${t('telegram.step1')}</p>
                        <a href="https://t.me/${AppUtils.escapeHtml(botUsername)}" target="_blank" rel="noopener noreferrer" class="text-xs text-[#229ED9] hover:underline mt-1 inline-block">@${AppUtils.escapeHtml(botUsername)}</a>
                    </div>
                </div>

                <div class="flex items-start gap-3">
                    <div class="w-6 h-6 rounded-full bg-primary/20 text-primary text-xs font-bold flex items-center justify-center shrink-0">2</div>
                    <div class="flex-1">
                        <p class="text-sm text-secondary font-medium">${t('telegram.step2')}</p>
                        <div class="mt-2 flex gap-2">
                            <div class="flex-1 bg-background border border-borderLight rounded-xl px-4 py-3 font-mono text-sm text-textMain break-all">/link ${AppUtils.escapeHtml(token)}</div>
                            <button
                                data-click="TelegramLinkApp.copyToken" data-click-arg="${encodeURIComponent(token)}"
                                class="px-4 py-3 bg-surfaceHighlight hover:bg-surfaceHighlight border border-borderLight rounded-xl transition shrink-0"
                                title="${t('telegram.copy_token')}" aria-label="${t('telegram.copy_token')}"
                            >
                                <i data-lucide="copy" class="w-4 h-4 text-textMuted"></i>
                            </button>
                        </div>
                    </div>
                </div>

                <div class="flex items-start gap-3">
                    <div class="w-6 h-6 rounded-full bg-primary/20 text-primary text-xs font-bold flex items-center justify-center shrink-0">3</div>
                    <div>
                        <p class="text-sm text-secondary font-medium">${t('telegram.step3')}</p>
                        <p class="text-xs text-textMuted mt-1">${t('telegram.step3_desc')}</p>
                    </div>
                </div>`;
        const oneTap = deepLink
            ? `
                <p class="text-sm text-textMuted leading-relaxed">${t('telegram.one_tap_desc')}</p>
                <button
                    data-click="TelegramLinkApp.openDeepLink" data-click-arg="${encodeURIComponent(deepLink)}"
                    class="w-full py-3 bg-primary text-background font-bold rounded-xl transition hover:opacity-90 flex items-center justify-center gap-2"
                >
                    <i data-lucide="send" class="w-4 h-4"></i>
                    ${t('telegram.open_telegram')}
                </button>
                <p class="text-xs text-textMuted flex items-center gap-2">
                    <i data-lucide="loader" class="w-3 h-3 animate-spin shrink-0"></i>
                    <span>${t('telegram.waiting_for_start')}</span>
                </p>
                <details class="pt-2 border-t border-borderSubtle">
                    <summary class="text-xs text-textMuted cursor-pointer py-2">${t('telegram.manual_fallback')}</summary>
                    <div class="space-y-4 pt-2">${manualSteps}</div>
                </details>`
            : manualSteps;

        modal.innerHTML = `
            <div class="p-6 border-b border-borderSubtle">
                <div class="flex items-center justify-between">
                    <h3 class="text-lg font-serif text-secondary flex items-center gap-2">
                        <svg viewBox="0 0 24 24" fill="currentColor" class="w-5 h-5 text-[#229ED9]">
                            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69a.2.2 0 00-.05-.18c-.06-.05-.14-.03-.21-.02-.09.02-1.49.95-4.22 2.79-.4.27-.76.41-1.08.4-.36-.01-1.04-.2-1.55-.37-.63-.2-1.12-.31-1.08-.66.02-.18.27-.36.74-.55 2.92-1.27 4.86-2.11 5.83-2.51 2.78-1.16 3.35-1.36 3.73-1.36.08 0 .27.02.39.12.1.08.13.19.14.27-.01.06.01.24 0 .37z"/>
                        </svg>
                        ${t('telegram.instructions')}
                    </h3>
                    <button data-click="TelegramLinkApp.closeModal" class="w-8 h-8 rounded-lg bg-surfaceHighlight hover:bg-surfaceHighlight flex items-center justify-center transition">
                        <i data-lucide="x" class="w-4 h-4 text-textMuted"></i>
                    </button>
                </div>
                <p id="telegram-modal-countdown" class="text-xs text-textMuted mt-2">${t('telegram.token_expires_in')}: <span class="text-primary font-mono">${expiresIn}</span> ${t('telegram.seconds')}</p>
            </div>
            <div class="p-6 space-y-4">${oneTap}
            </div>
            <div class="p-6 bg-background/50 border-t border-borderSubtle">
                <button data-click="TelegramLinkApp.closeModal" class="w-full py-3 bg-surfaceHighlight hover:bg-surfaceHighlight text-secondary font-bold rounded-xl transition">
                    ${t('common.close')}
                </button>
            </div>
        `;

        overlay.appendChild(modal);
        document.body.appendChild(overlay);
        document.body.style.overflow = 'hidden';

        if (window.AppUtils) window.AppUtils.refreshIcons();

        // Start countdown
        let remaining = expiresIn;
        const countdownEl = document.getElementById('telegram-modal-countdown');

        if (this._countdownTimer) {
            clearInterval(this._countdownTimer);
        }

        this._countdownTimer = setInterval(() => {
            remaining -= 1;
            if (countdownEl) {
                countdownEl.innerHTML = `${t('telegram.token_expires_in')}: <span class="text-primary font-mono">${remaining}</span> ${t('telegram.seconds')}`;
            }
            if (remaining <= 0) {
                this.closeModal();
                if (typeof window.showToast === 'function') {
                    window.showToast(t('telegram.token_expired'), 'error');
                }
            }
        }, 1000);

        // 即時確認：modal 開著時輪詢綁定狀態。一旦使用者在 Telegram 完成 /link，
        // 後端 binding 出現 → 立刻切「綁定成功」並刷新，不必使用者手動重整。
        this._startBindingPoll();
    },

    // 每 2.5 秒查一次 /api/telegram/status，偵測到 bound 即顯示成功。
    // 從 Telegram 切回來（分頁重新可見／視窗拿回焦點）時立刻再查一次，不用等下一輪；
    // 手機瀏覽器在背景時計時器會被暫停，靠這個回來就更新。
    _startBindingPoll() {
        this._stopBindingPoll();
        this._pollTimer = setInterval(() => this._checkBindingNow(), 2500);
        this._onReturn = () => {
            if (document.visibilityState === 'visible') this._checkBindingNow();
        };
        document.addEventListener('visibilitychange', this._onReturn);
        window.addEventListener('focus', this._onReturn);
    },

    async _checkBindingNow() {
        if (this._polling || !this._pollTimer) return; // 避免請求重疊；已停止就不查
        this._polling = true;
        try {
            const data = await AppAPI.get('/api/telegram/status');
            if (data && data.bound && this._pollTimer) {
                this._stopBindingPoll();
                this._onBindingConfirmed(data);
            }
        } catch (_) {
            // 暫時性錯誤忽略，下一輪再試
        } finally {
            this._polling = false;
        }
    },

    _stopBindingPoll() {
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
        if (this._onReturn) {
            document.removeEventListener('visibilitychange', this._onReturn);
            window.removeEventListener('focus', this._onReturn);
            this._onReturn = null;
        }
        this._polling = false;
    },

    // 綁定確認成功：modal 切成成功畫面，刷新設定頁狀態，短暫後自動關閉。
    _onBindingConfirmed(data) {
        const t = (key) => (window.I18n ? window.I18n.t(key) : key);

        if (this._countdownTimer) {
            clearInterval(this._countdownTimer);
            this._countdownTimer = null;
        }

        const modal = document.getElementById('telegram-link-modal');
        if (modal) {
            modal.innerHTML = `
                <div class="p-8 flex flex-col items-center text-center gap-4">
                    <div class="w-16 h-16 rounded-full bg-success/15 flex items-center justify-center">
                        <i data-lucide="check-circle" class="w-8 h-8 text-success"></i>
                    </div>
                    <div>
                        <p class="text-lg font-bold text-success">${t('telegram.bind_success_title')}</p>
                        <p class="text-sm text-textMuted mt-1">${t('telegram.bound_as')} @${AppUtils.escapeHtml(data.telegram_username || 'unknown')}</p>
                    </div>
                </div>`;
            if (window.AppUtils) window.AppUtils.refreshIcons();
        }

        // 刷新設定頁的綁定區塊與右上角徽章。
        if (this.initialized) {
            Promise.resolve(this.loadStatus()).catch(() => {});
        }
        // 通知其他地方（聊天首頁的新手清單）重新抓完成狀態
        window.dispatchEvent(new CustomEvent('telegram:bound'));
        if (typeof window.showToast === 'function') {
            window.showToast(t('telegram.bind_success_title'), 'success');
        }

        setTimeout(() => this.closeModal(), 1800);
    },

    closeModal() {
        if (this._countdownTimer) {
            clearInterval(this._countdownTimer);
            this._countdownTimer = null;
        }
        this._stopBindingPoll();

        const overlay = document.getElementById('telegram-link-modal-overlay');
        if (overlay) {
            overlay.remove();
        }
        document.body.style.overflow = '';
    },

    async copyToken(token) {
        const t = (key) => (window.I18n ? window.I18n.t(key) : key);

        const textToCopy = `/link ${token}`;
        try {
            await navigator.clipboard.writeText(textToCopy);
            if (typeof window.showToast === 'function') {
                window.showToast(t('common.copied'), 'success');
            }
        } catch (e) {
            // Fallback for older browsers
            const textarea = document.createElement('textarea');
            textarea.value = textToCopy;
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            document.body.removeChild(textarea);

            if (typeof window.showToast === 'function') {
                window.showToast(t('common.copied'), 'success');
            }
        }
    },

    // 重新綁定：直接開啟連接流程。不先 unlink —— 後端 create_telegram_binding
    // 為原子覆蓋（先刪舊綁定再插新的），故新綁定完成時自動覆蓋舊的；若使用者
    // 中途取消，舊綁定仍完整保留（比舊版「先解除」更安全）。
    async handleRebind() {
        const t = (key) => (window.I18n ? window.I18n.t(key) : key);

        const confirmed =
            typeof window.showConfirm === 'function'
                ? await window.showConfirm({
                      message: t('telegram.rebind_confirm'),
                      type: 'warning',
                  })
                : window.confirm(t('telegram.rebind_confirm'));

        if (!confirmed) return;

        await this.handleConnect();
    },

    async handleUnbind() {
        const t = (key) => (window.I18n ? window.I18n.t(key) : key);

        const confirmed =
            typeof window.showConfirm === 'function'
                ? await window.showConfirm({
                      message: t('telegram.unbind_confirm'),
                      type: 'danger',
                  })
                : window.confirm(t('telegram.unbind_confirm'));

        if (!confirmed) return;

        try {
            await AppAPI.post('/api/telegram/unlink', {});

            if (typeof window.showToast === 'function') {
                window.showToast(t('telegram.unbind_success'), 'success');
            }

            await this.loadStatus();
        } catch (e) {
            if (typeof window.showToast === 'function') {
                window.showToast(e.message || t('telegram.unbind_failed'), 'error');
            }
        }
    },

    renderError(msg) {
        const container = document.getElementById('telegram-link-content');
        if (container) {
            container.innerHTML = `
                <div class="text-center py-6 text-danger">
                    <i data-lucide="alert-circle" class="w-6 h-6 mx-auto mb-2"></i>
                    <p class="text-sm">${AppUtils.escapeHtml(msg)}</p>
                </div>
            `;
            if (window.AppUtils) window.AppUtils.refreshIcons();
        }
    },
};

// Auto-init when tab is shown (called from tab-settings.js)
// Also expose globally
window.TelegramLinkApp = TelegramLinkApp;
export { TelegramLinkApp };
