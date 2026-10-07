(function () {
    'use strict';

    function t(key, options) {
        return window.I18n ? window.I18n.t(key, options) : key;
    }

    function getLang() {
        return window.I18n ? window.I18n.getLanguage() : 'en';
    }

    function escapeHtml(value) {
        if (value === null || value === undefined) return '';
        const div = document.createElement('div');
        div.textContent = String(value);
        return div.innerHTML;
    }

    function formatDate(isoString) {
        const date = new Date(isoString);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);

        if (diffMins < 1) return t('time.justNow');
        if (diffMins < 60) return t('time.minutesAgo', { count: diffMins });

        const diffHours = Math.floor(diffMins / 60);
        if (diffHours < 24) return t('time.hoursAgo', { count: diffHours });

        const diffDays = Math.floor(diffHours / 24);
        if (diffDays < 7) return t('time.daysAgo', { count: diffDays });

        return date.toLocaleDateString(getLang());
    }

    function fallbackTypes() {
        return [
            { id: 'fake_official', name: t('safety.fakeOfficial'), icon: '🎭' },
            { id: 'investment_scam', name: t('safety.investmentScam'), icon: '💰' },
            { id: 'fake_airdrop', name: t('safety.fakeAirdrop'), icon: '🎁' },
            { id: 'trading_fraud', name: t('safety.tradingFraud'), icon: '🔄' },
            { id: 'gambling', name: t('safety.gambling'), icon: '🎰' },
            { id: 'phishing', name: t('safety.phishing'), icon: '🎣' },
            { id: 'other', name: t('safety.otherScam'), icon: '⚠️' },
        ];
    }

    // 語言：跟主站同一個四語切換器（2026-09-27 前是中文／EN 兩態鈕，zh-CN／ru 使用者一按就被切走）。
    // LanguageSwitcher 由 classic-compat.js（module）掛上 window；module 在 DOMContentLoaded 前跑完。
    function mountLanguageSwitcher() {
        const box = document.getElementById('scam-lang-switcher');
        if (!box || box.dataset.mounted || !window.LanguageSwitcher) return;
        box.dataset.mounted = '1';
        try {
            new window.LanguageSwitcher(box);
        } catch (_error) {
            delete box.dataset.mounted;
        }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', mountLanguageSwitcher);
    } else {
        mountLanguageSwitcher();
    }

    window.copyScamTrackerText = function copyScamTrackerText(elementId) {
        const el = document.getElementById(elementId);
        if (!el) return;
        navigator.clipboard.writeText(el.textContent || '');
        window.showToast?.(t('safety.copySuccess'), 'success');
    };

    function patchApp() {
        const app = window.ScamTrackerApp;
        if (!app) return false;

        app.formatDate = formatDate;

        app.getStatusBadge = function getStatusBadge(status) {
            const map = {
                verified: {
                    label: t('safety.statusVerified'),
                    classes: 'bg-success/20 text-success',
                    icon: '✅',
                },
                pending: {
                    label: t('safety.statusPending'),
                    classes: 'bg-danger/20 text-danger',
                    icon: '⏳',
                },
                disputed: {
                    label: t('safety.statusDisputed'),
                    classes: 'bg-danger/20 text-danger',
                    icon: '⚠️',
                },
            };
            const item = map[status] || map.pending;
            return `<span class="${item.classes} px-2 py-0.5 rounded text-xs font-bold">${item.icon} ${item.label}</span>`;
        };

        app.getTypeBadge = function getTypeBadge(type) {
            const match = (this.scamTypes || []).find((item) => item.id === type);
            const label = match ? `${match.icon} ${match.name}` : type;
            return `<span class="bg-primary/10 text-primary px-2 py-0.5 rounded text-xs font-bold">${escapeHtml(label)}</span>`;
        };

        app.loadScamTypes = async function loadScamTypesPatched() {
            try {
                const config = await window.ScamTrackerAPI.getConfig();
                this.scamTypes = config.scam_types?.length ? config.scam_types : fallbackTypes();
            } catch (error) {
                this.scamTypes = fallbackTypes();
            }

            const localizedFallbacks = fallbackTypes();
            this.scamTypes = this.scamTypes.map((type) => {
                const localized = localizedFallbacks.find((item) => item.id === type.id);
                return localized ? { ...type, name: localized.name, icon: type.icon || localized.icon } : type;
            });

            const submitSelect = document.getElementById('scam-type');
            if (submitSelect) {
                submitSelect.innerHTML = `<option value="">${t('safety.selectScamType')}</option>`;
                this.scamTypes.forEach((type) => {
                    const option = document.createElement('option');
                    option.value = type.id;
                    option.textContent = `${type.icon} ${type.name}`;
                    submitSelect.appendChild(option);
                });
            }
        };

        app.renderReportDetail = function renderReportDetailPatched(report) {
            const container = document.getElementById('report-detail');
            if (!container) return;

            container.innerHTML = `
                <div class="flex items-center gap-2 mb-4 flex-wrap">
                    ${this.getStatusBadge(report.verification_status)}
                    ${this.getTypeBadge(report.scam_type)}
                    <span class="text-xs text-textMuted ml-auto">${formatDate(report.created_at)}</span>
                </div>
                <div class="mb-4">
                    <label class="text-xs text-textMuted">${t('safety.suspiciousWallet')}</label>
                    <div class="flex items-center gap-2 bg-background rounded-xl p-3 mt-1">
                        <code class="flex-1 font-mono text-primary text-sm break-all" id="wallet-address-display">${escapeHtml(report.scam_wallet_address)}</code>
                        <button data-click="copyScamTrackerText" data-click-arg="wallet-address-display" class="text-textMuted hover:text-primary transition flex-shrink-0">
                            <i data-lucide="copy" class="w-4 h-4"></i>
                        </button>
                    </div>
                </div>
                ${
                    report.transaction_hash
                        ? `
                            <div class="mb-4">
                                <label class="text-xs text-textMuted">${t('safety.transactionHash')}</label>
                                <div class="flex items-center gap-2 bg-background rounded-xl p-3 mt-1">
                                    <code class="flex-1 font-mono text-xs text-textMuted break-all" id="tx-hash-display">${escapeHtml(report.transaction_hash)}</code>
                                    <button data-click="copyScamTrackerText" data-click-arg="tx-hash-display" class="text-textMuted hover:text-primary transition flex-shrink-0">
                                        <i data-lucide="copy" class="w-4 h-4"></i>
                                    </button>
                                </div>
                            </div>
                        `
                        : ''
                }
                <div class="mb-4">
                    <label class="text-xs text-textMuted">${t('safety.scamDescription')}</label>
                    <div class="bg-background rounded-xl p-4 mt-1 text-textMuted leading-relaxed text-sm">
                        ${escapeHtml(report.description).replace(/\n/g, '<br>')}
                    </div>
                </div>
                <div class="flex items-center justify-between text-xs text-textMuted border-t border-white/5 pt-4">
                    <span>${t('safety.reporter')}: ${escapeHtml(report.reporter_wallet_masked)}</span>
                    <span>${t('safety.views')}: ${report.view_count}</span>
                </div>
            `;

            document.getElementById('count-approve').textContent = report.approve_count;
            document.getElementById('count-reject').textContent = report.reject_count;
            this.updateVoteProgress(report.approve_count, report.reject_count);
            window.lucide?.createIcons();
        };

        app.renderComments = function renderCommentsPatched(comments) {
            const container = document.getElementById('comments-list');
            if (!container) return;

            if (!comments || comments.length === 0) {
                container.innerHTML = `<div class="text-center text-textMuted py-4">${t('safety.noComments')}</div>`;
                return;
            }

            container.innerHTML = comments
                .map(
                    (comment) => `
                        <div class="bg-background rounded-xl p-4">
                            <div class="flex items-center justify-between mb-2">
                                <span class="font-bold text-secondary text-sm">${escapeHtml(comment.username || t('safety.anonymous'))}</span>
                                <span class="text-xs text-textMuted">${formatDate(comment.created_at)}</span>
                            </div>
                            <p class="text-textMuted text-sm">${escapeHtml(comment.content).replace(/\n/g, '<br>')}</p>
                            ${
                                comment.transaction_hash
                                    ? `<div class="mt-2 pt-2 border-t border-white/5"><code class="text-xs text-textMuted font-mono">TX: ${escapeHtml(comment.transaction_hash)}</code></div>`
                                    : ''
                            }
                        </div>
                    `
                )
                .join('');
        };

        // handleSearch／handleSubmitReport 不覆寫：scam-tracker.js 的原版已是鏈中立
        // （EVM 0x／TON EQ/UQ）＋i18n；這裡的舊版只認 G 開頭地址，搜尋會永遠格式錯誤
        // （2026-09-24 正式站實測）。

        app.handleVote = async function handleVotePatched(voteType) {
            try {
                const result = await window.ScamTrackerAPI.vote(this.currentReportId, voteType);
                const messages = {
                    voted: voteType === 'approve' ? t('safety.voteSuccessApprove') : t('safety.voteSuccessReject'),
                    cancelled:
                        voteType === 'approve'
                            ? t('safety.voteCancelledApprove')
                            : t('safety.voteCancelledReject'),
                    switched:
                        voteType === 'approve'
                            ? t('safety.voteSwitchedApprove')
                            : t('safety.voteSwitchedReject'),
                };
                window.showToast?.(messages[result.action] || t('safety.voteFailed'), 'success');
                this.loadReportDetail();
            } catch (error) {
                window.showToast?.(error.message || t('safety.voteFailed'), 'error');
            }
        };

        app.handleSubmitComment = async function handleSubmitCommentPatched() {
            const contentInput = document.getElementById('comment-content');
            const txHashInput = document.getElementById('comment-tx-hash');
            if (!contentInput || !txHashInput) return;

            const content = contentInput.value.trim();
            const txHash = txHashInput.value.trim();
            if (!content || content.length < 10) {
                window.showToast?.(t('safety.commentMinLength'), 'warning');
                return;
            }
            // 與舉報同一套格式檢查（TON 64 hex／EVM 0x+64 hex）；後端也會擋，
            // 但 422 的 detail 是陣列，toast 會變成 [object Object]
            if (txHash && typeof isValidTxHash === 'function' && !isValidTxHash(txHash)) {
                window.showToast?.(t('safety.txHashFormatError'), 'error');
                return;
            }

            try {
                await window.ScamTrackerAPI.addComment(this.currentReportId, content, txHash || null);
                window.showToast?.(t('safety.commentSuccess'), 'success');
                contentInput.value = '';
                txHashInput.value = '';
                this.loadComments();
                this.loadReportDetail();
            } catch (error) {
                window.showToast?.(error.message || t('safety.commentFailed'), 'error');
            }
        };

        // 剩餘次數：scam-tracker.js 的 loadQuota() 向 /reports/quota 取真值並設好
        // data-i18n／data-i18n-args，語言切換由 i18n.js 全頁重掃重畫（以前這裡寫死 5）

        return true;
    }

    function refreshDynamicUi() {
        const page = document.body.dataset.page;
        const base = t('safety.trackerTitle');
        if (page === 'scam-detail') document.title = `${t('safety.reportDetail')} - ${base}`;
        if (page === 'scam-submit') document.title = `${t('safety.reportSubmitTitle')} - ${base}`;

        const app = window.ScamTrackerApp;
        if (!app) return;
        app.loadScamTypes?.();
        if (page === 'scam-detail' && app.currentReport) {
            app.renderReportDetail?.(app.currentReport);
            if (Array.isArray(app.comments)) app.renderComments?.(app.comments);
        }
    }

    // i18n 尚未 init 完成時 t() 會回原始 key（線上實測：載入瞬間標題閃
    // safety.trackerTitle）→ 等 init 完成 dispatched 的 languageChanged
    // 再做第一次 refresh，不在此主動呼叫。
    const refreshIfReady = () => {
        if (window.I18n && window.I18n.isReady && window.I18n.isReady()) {
            refreshDynamicUi();
        }
    };
    if (!patchApp()) {
        const timer = setInterval(() => {
            if (patchApp()) {
                clearInterval(timer);
                refreshIfReady();
            }
        }, 100);
    } else {
        refreshIfReady();
    }

    window.addEventListener('languageChanged', refreshDynamicUi);
})();
