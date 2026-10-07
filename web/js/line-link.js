// ========================================
// line-link.js - LINE 綁定管理
// design：docs/plans/2026-09-08-line-bot-shared-sessions-design.md
// ========================================
//
// 與 telegram-link.js 同一套流程（產生 5 分鐘 link token → 使用者到 LINE
// 對官方帳號送 `/link <token>` → 後端建立 line_bindings）。
//
// 差別：Telegram 那版把說明開成 modal，這裡直接在卡片內展開。少一層
// overlay 的生命週期要管，而步驟只有兩步，撐不起一個 modal。

const LineLinkApp = {
    _countdownTimer: null,
    _pollTimer: null,
    _expiryKillTimer: null,
    _tokenExpiresAt: null,

    // 連結碼過期後輪詢再保留的寬限窗：碼常在倒數最後一刻才送出，
    // 綁定落地的時間可以晚於碼本身的到期時刻——這段窗內偵測到綁定
    // 仍要自動翻卡（2026-09-09 審查補上）。
    _EXPIRY_POLL_GRACE_MS: 60000,

    async init() {
        if (typeof AuthManager === 'undefined' || !AuthManager.isLoggedIn()) {
            this.renderUnbound();
            return;
        }
        await this.loadStatus();
    },

    _t(key, fallback) {
        if (window.I18n && typeof window.I18n.t === 'function') {
            return window.I18n.t(key, fallback);
        }
        return fallback;
    },

    _setBadge(bound) {
        const badge = document.getElementById('line-status-badge');
        if (!badge) return;
        if (bound) {
            badge.className =
                'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium shrink-0 whitespace-nowrap bg-success/10 text-success';
            badge.innerHTML =
                '<i data-lucide="check" class="w-3 h-3"></i><span>' +
                this._t('line.connected', 'Connected') +
                '</span>';
        } else {
            badge.className =
                'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium shrink-0 whitespace-nowrap bg-surfaceHighlight text-textMuted';
            badge.innerHTML =
                '<i data-lucide="x" class="w-3 h-3"></i><span>' +
                this._t('line.notBound', 'Not linked') +
                '</span>';
        }
        if (window.AppUtils) window.AppUtils.refreshIcons();
    },

    async loadStatus() {
        const container = document.getElementById('line-link-content');
        if (!container) return;

        // 「我已送出」與分頁再進入都會走到這裡——說明卡還活著（或仍在
        // 過期寬限窗）時不要重渲染：碼、倒數與輪詢全掛在卡片上，洗掉
        // 等於叫使用者重產一組碼（切個分頁回來碼就消失，2026-09-09 審
        // 查補上）。這裡只檢查是否已翻成「已綁定」。
        if (this._countdownTimer || this._pollTimer) {
            try {
                const data = await AppAPI.get('/api/line/status');
                if (data.bound) this.renderBound(data);
            } catch (e) {
                /* 失敗不打擾使用者，輪詢下一輪再試 */
            }
            return;
        }

        container.innerHTML =
            '<div class="text-center py-4"><i data-lucide="loader" class="w-5 h-5 animate-spin mx-auto text-textMuted"></i></div>';
        if (window.AppUtils) window.AppUtils.refreshIcons();

        try {
            const data = await AppAPI.get('/api/line/status');
            if (data.bound) {
                this.renderBound(data);
            } else {
                this.renderUnbound();
            }
        } catch (e) {
            // 404 = 後端沒設 LINE_CHANNEL_SECRET（功能總開關關著）。這不是錯誤，
            // 是「還沒開放」——顯示錯誤紅字只會讓使用者以為壞了。
            if (e && e.status === 404) {
                this.renderUnavailable();
                return;
            }
            this.renderError(e && e.message);
        }
    },

    renderUnavailable() {
        const container = document.getElementById('line-link-content');
        if (!container) return;
        this._setBadge(false);
        container.innerHTML =
            '<p class="text-sm text-textMuted">' +
            this._t('line.unavailable', 'LINE linking is not available yet.') +
            '</p>';
    },

    renderBound(state) {
        const container = document.getElementById('line-link-content');
        if (!container) return;
        this._clearTimers();
        this._tokenExpiresAt = null;
        this._setBadge(true);

        const linkedAt = state.linked_at
            ? new Date(state.linked_at).toLocaleDateString()
            : '-';
        const name = state.display_name || this._t('line.thisAccount', 'your LINE account');

        container.innerHTML =
            '<div class="bg-success/5 rounded-xl p-4 border border-success/10">' +
            '<div class="flex items-center gap-3">' +
            '<div class="w-10 h-10 rounded-full bg-success/20 flex items-center justify-center">' +
            '<i data-lucide="check-circle" class="w-5 h-5 text-success"></i></div>' +
            '<div class="min-w-0">' +
            '<p class="text-sm font-bold text-success">' +
            this._t('line.boundAs', 'Linked to') +
            ' ' +
            AppUtils.escapeHtml(name) +
            '</p>' +
            '<p class="text-xs text-textMuted">' +
            this._t('line.linkedAt', 'Linked on') +
            ': ' +
            AppUtils.escapeHtml(linkedAt) +
            '</p></div></div>' +
            '<button data-click="LineLinkApp.handleConnect" ' +
            'class="w-full mt-4 py-2.5 bg-[#06C755]/10 hover:bg-[#06C755]/20 text-[#06C755] border border-[#06C755]/20 font-bold rounded-xl transition flex items-center justify-center gap-2">' +
            '<i data-lucide="repeat" class="w-4 h-4"></i>' +
            this._t('line.rebind', 'Re-link') +
            '</button>' +
            '<button data-click="LineLinkApp.handleUnbind" ' +
            'class="w-full mt-2 py-2.5 bg-danger/10 hover:bg-danger/20 text-danger border border-danger/20 font-bold rounded-xl transition flex items-center justify-center gap-2">' +
            '<i data-lucide="unlink" class="w-4 h-4"></i>' +
            this._t('line.unbind', 'Unlink') +
            '</button></div>';

        if (window.AppUtils) window.AppUtils.refreshIcons();
    },

    renderUnbound() {
        const container = document.getElementById('line-link-content');
        if (!container) return;
        this._clearTimers();
        this._tokenExpiresAt = null;
        this._setBadge(false);

        container.innerHTML =
            '<div class="' +
            (window.INFO_PANEL_CLASS ||
                'bg-background/50 rounded-xl p-4 border border-borderSubtle') +
            ' mb-4"><div class="flex items-start gap-3">' +
            '<i data-lucide="info" class="w-4 h-4 text-primary/60 mt-0.5 flex-shrink-0"></i>' +
            '<p class="text-sm text-textMuted leading-relaxed">' +
            this._t(
                'line.notBoundDesc',
                'Link LINE to chat with CryptoMind there. Your conversations stay shared with the web app.'
            ) +
            '</p></div></div>' +
            '<button data-click="LineLinkApp.handleConnect" ' +
            'class="w-full py-3 bg-[#06C755]/10 hover:bg-[#06C755]/20 text-[#06C755] border border-[#06C755]/20 font-bold rounded-xl transition flex items-center justify-center gap-2">' +
            '<i data-lucide="message-circle" class="w-5 h-5"></i>' +
            this._t('line.connect', 'Link LINE') +
            '</button>';

        if (window.AppUtils) window.AppUtils.refreshIcons();
    },

    renderError(msg) {
        const container = document.getElementById('line-link-content');
        if (!container) return;
        container.innerHTML =
            '<div class="text-center py-6 text-danger">' +
            '<i data-lucide="alert-circle" class="w-6 h-6 mx-auto mb-2"></i>' +
            '<p class="text-sm">' +
            AppUtils.escapeHtml(msg || this._t('common.error', 'Error occurred')) +
            '</p></div>';
        if (window.AppUtils) window.AppUtils.refreshIcons();
    },

    async handleConnect() {
        try {
            const data = await AppAPI.post('/api/line/link-token', {});
            this.renderInstructions(data.token, data.basic_id, data.expires_in);
        } catch (e) {
            if (typeof window.showToast === 'function') {
                window.showToast(
                    (e && e.message) || this._t('line.connectFailed', 'Could not start linking'),
                    'error'
                );
            }
        }
    },

    renderInstructions(token, basicId, expiresIn) {
        const container = document.getElementById('line-link-content');
        if (!container) return;
        this._clearTimers();
        // 牆鐘到期時刻——不依賴 setInterval 的 tick 次數（手機切去 LINE app
        // 後瀏覽器會節流/凍結 timer，畫面剩餘秒數會落後真實時間，出現
        // 「顯示還沒過期、碼其實早過期」的陷阱，複製出去必吃「已過期」）。
        this._tokenExpiresAt = Date.now() + expiresIn * 1000;

        const command = '/link ' + token;
        const addFriend = basicId
            ? '<a href="https://line.me/R/ti/p/' +
              encodeURIComponent(basicId) +
              '" target="_blank" rel="noopener noreferrer" ' +
              'class="text-xs text-[#06C755] hover:underline mt-1 inline-block">' +
              AppUtils.escapeHtml(basicId) +
              '</a>'
            : '<p class="text-xs text-textMuted mt-1">' +
              this._t('line.searchOfficialAccount', 'Search for the official account in LINE') +
              '</p>';

        container.innerHTML =
            '<div class="space-y-4">' +
            '<div class="flex items-start gap-3">' +
            '<div class="w-6 h-6 rounded-full bg-primary/20 text-primary text-xs font-bold flex items-center justify-center shrink-0">1</div>' +
            '<div><p class="text-sm text-secondary font-medium">' +
            this._t('line.step1', 'Add the CryptoMind official account as a friend') +
            '</p>' +
            addFriend +
            '</div></div>' +
            '<div class="flex items-start gap-3">' +
            '<div class="w-6 h-6 rounded-full bg-primary/20 text-primary text-xs font-bold flex items-center justify-center shrink-0">2</div>' +
            '<div class="flex-1 min-w-0"><p class="text-sm text-secondary font-medium">' +
            this._t('line.step2', 'Send this message to the account') +
            '</p><div class="mt-2 flex gap-2">' +
            '<div class="flex-1 min-w-0 bg-background border border-borderLight rounded-xl px-4 py-3 font-mono text-xs text-textMain break-all">' +
            AppUtils.escapeHtml(command) +
            '</div>' +
            '<button data-click="LineLinkApp.copyToken" data-click-arg="' +
            encodeURIComponent(command) +
            '" class="px-4 py-3 bg-surfaceHighlight hover:bg-surfaceHighlight border border-borderLight rounded-xl transition shrink-0" title="' +
            this._t('line.copy', 'Copy') +
            '"><i data-lucide="copy" class="w-4 h-4 text-textMuted"></i></button>' +
            '</div></div></div>' +
            '<p id="line-token-countdown" class="text-xs text-textMuted">' +
            this._t('line.expiresIn', 'Code expires in') +
            ' <span class="text-primary font-mono">' +
            expiresIn +
            '</span> ' +
            this._t('line.seconds', 'seconds') +
            '</p>' +
            '<button data-click="LineLinkApp.loadStatus" ' +
            'class="w-full py-2.5 bg-surfaceHighlight hover:bg-surfaceHighlight/70 text-textMuted border border-borderLight rounded-xl transition text-sm">' +
            this._t('line.done', 'I have sent it') +
            '</button></div>';

        if (window.AppUtils) window.AppUtils.refreshIcons();
        this._startCountdown();
        this._startPolling();
    },

    _startCountdown() {
        const expiresAt = this._tokenExpiresAt;
        this._countdownTimer = setInterval(() => {
            const el = document.getElementById('line-token-countdown');
            if (!el) {
                this._clearTimers();
                return;
            }
            const span = el.querySelector('span');
            const left = Math.ceil((expiresAt - Date.now()) / 1000);
            if (span) span.textContent = String(Math.max(left, 0));
            if (left <= 0) this.renderExpired();
        }, 1000);
    },

    renderExpired() {
        const container = document.getElementById('line-link-content');
        if (!container) return;
        this._stopCountdown();
        // 碼常在最後一刻才送出——輪詢留一個寬限窗，綁定晚幾秒落地仍會
        // 自動翻卡（_startPolling 的 renderBound 會收掉全部 timer）。
        this._expiryKillTimer = setTimeout(() => {
            if (this._pollTimer) clearInterval(this._pollTimer);
            this._pollTimer = null;
        }, this._EXPIRY_POLL_GRACE_MS);

        this._setBadge(false);
        container.innerHTML =
            '<div class="text-center py-6">' +
            '<i data-lucide="clock" class="w-6 h-6 mx-auto mb-2 text-textMuted"></i>' +
            '<p class="text-sm text-textMuted mb-4">' +
            this._t(
                'line.codeExpired',
                'Link code expired. Generate a new one and send it within 5 minutes.'
            ) +
            '</p>' +
            '<button data-click="LineLinkApp.handleConnect" ' +
            'class="w-full py-3 bg-[#06C755]/10 hover:bg-[#06C755]/20 text-[#06C755] border border-[#06C755]/20 font-bold rounded-xl transition flex items-center justify-center gap-2">' +
            '<i data-lucide="refresh-cw" class="w-4 h-4"></i>' +
            this._t('line.regenerate', 'Generate new code') +
            '</button></div>';
        if (window.AppUtils) window.AppUtils.refreshIcons();
    },

    _startPolling() {
        // 使用者在 LINE 送出後不必回來按按鈕——輪詢到綁定成功就自動切畫面。
        this._pollTimer = setInterval(async () => {
            try {
                const data = await AppAPI.get('/api/line/status');
                if (data.bound) {
                    this._clearTimers();
                    this.renderBound(data);
                    if (typeof window.showToast === 'function') {
                        window.showToast(
                            this._t('line.bindSuccess', 'LINE linked'),
                            'success'
                        );
                    }
                }
            } catch (e) {
                /* 輪詢失敗不打擾使用者，下一輪再試 */
            }
        }, 3000);
    },

    _stopCountdown() {
        if (this._countdownTimer) clearInterval(this._countdownTimer);
        this._countdownTimer = null;
    },

    _clearTimers() {
        this._stopCountdown();
        if (this._pollTimer) clearInterval(this._pollTimer);
        if (this._expiryKillTimer) clearTimeout(this._expiryKillTimer);
        this._pollTimer = null;
        this._expiryKillTimer = null;
    },

    copyToken(command) {
        if (!navigator.clipboard) return;
        navigator.clipboard.writeText(command).then(() => {
            if (typeof window.showToast === 'function') {
                window.showToast(this._t('line.copied', 'Copied'), 'success');
            }
        });
    },

    async handleUnbind() {
        const ok = window.showConfirmDialog
            ? await window.showConfirmDialog({
                  title: this._t('line.unbind', 'Unlink'),
                  message: this._t(
                      'line.unbindConfirm',
                      'Unlink LINE? You can link it again any time.'
                  ),
                  danger: true,
              })
            : true;
        if (!ok) return;

        try {
            await AppAPI.post('/api/line/unlink', {});
            if (typeof window.showToast === 'function') {
                window.showToast(this._t('line.unbindSuccess', 'LINE unlinked'), 'success');
            }
            await this.loadStatus();
        } catch (e) {
            if (typeof window.showToast === 'function') {
                window.showToast(
                    (e && e.message) || this._t('line.unbindFailed', 'Could not unlink'),
                    'error'
                );
            }
        }
    },
};

window.LineLinkApp = LineLinkApp;
export { LineLinkApp };
