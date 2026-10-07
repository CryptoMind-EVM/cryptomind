/**
 * 可疑錢包追蹤系統 - 前端模組
 */

/**
 * 鏈中立的錢包地址驗證——接受 TON（G+base58 / EQ/UQ/0Q）或 EVM（0x+40hex）。
 * 後端 scam_tracker 本來就接受任意鏈地址（32-90 字符），前端舊版只認 TON base58。
 * docs/plans/2026-08-11-evm-multichain-scam-detection-design.md
 */
function isValidWalletAddress(addr) {
    if (!addr || typeof addr !== 'string') return false;
    var s = addr.trim();
    if (!s) return false;
    // TON bounceable (G + 55 base58 chars, 大寫後) 或 raw/unbounceable (EQ/UQ/0Q + 46 chars)
    var isTonBase58 = /^G[A-Z234567]{55}$/.test(s);
    var isTonRaw = /^[EUQ0]Q[A-Za-z0-9_\-]{46}$/.test(s);
    // EVM (0x + 40 hex, 大小寫皆可)
    var isEvm = /^0x[a-fA-F0-9]{40}$/.test(s);
    return isTonBase58 || isTonRaw || isEvm;
}

/**
 * 輸入框的地址 → 送 API 的形式。只有 G 開頭 base32 地址要轉大寫；
 * 0x（轉大寫會變 0X）與 EQ/UQ（base64，大小寫有意義）一律原樣。
 */
function normalizeWalletInput(addr) {
    var s = String(addr || '').trim();
    return /^g[a-z2-7]{55}$/i.test(s) ? s.toUpperCase() : s;
}

/**
 * 交易雜湊驗證——接受 TON（64 hex）或 EVM（0x + 64 hex）。
 */
function isValidTxHash(hash) {
    if (!hash || typeof hash !== 'string') return false;
    var s = hash.trim().toLowerCase();
    if (!s) return false;
    // TON: 64 hex chars; EVM: 0x + 64 hex
    return /^[a-f0-9]{64}$/.test(s) || /^0x[a-f0-9]{64}$/.test(s);
}

/**
 * 舉報／留言送出失敗的訊息。後端 detail.reason 對得到的關卡用在地化文案
 * （detail.message 是英文後備）；其餘沿用 detail.message、字串 detail、422 陣列。
 */
function scamApiErrorMessage(detail, premiumKey, fallback) {
    // 422 的 detail 是陣列（[{msg, loc, ...}]），直接丟會變成 [object Object]
    if (Array.isArray(detail)) return detail.map((e) => e.msg).join(', ');
    switch (detail?.reason) {
        case 'premium_membership_required':
            return window.I18n.t(premiumKey);
        case 'daily_limit_reached':
            return window.I18n.t('safety.dailyLimitReached', { limit: detail.limit });
        case 'content_validation_failed': {
            // 發文內容檢查的保護型規則（貼出助記詞、索取助記詞、叫人匯款）：講出是哪一條
            const codes = Array.isArray(detail.codes) ? detail.codes : [];
            if (codes.length) {
                const reasons = codes.map((c) => window.I18n.t(`forum.moderation.reason.${c}`)).join('\u3001');
                return `${window.I18n.t('forum.moderation.block')}\uFF1A${reasons}`;
            }
            return window.I18n.t('safety.contentRejected');
        }
        case 'report_not_found':
            return window.I18n.t('safety.reportNotExist');
        default:
            return detail?.message || detail || fallback;
    }
}

function resolveScamTrackerCurrentUser() {
    if (typeof getCurrentUser === 'function') {
        try {
            return getCurrentUser();
        } catch (_) {
            // Fallback to AuthManager/localStorage for compatibility pages.
        }
    }

    if (window.AuthManager && window.AuthManager.currentUser) {
        return window.AuthManager.currentUser;
    }

    try {
        const raw = localStorage.getItem('pi_user');
        return raw ? JSON.parse(raw) : null;
    } catch (_) {
        return null;
    }
}

const ScamTrackerAPI = {
    /**
     * 獲取舉報詳情
     */
    async getReportDetail(reportId) {
        const res = await fetch(`/api/scam-tracker/reports/${reportId}`, { credentials: 'include' });
        if (!res.ok) {
            if (res.status === 404) throw new Error(window.I18n.t('safety.reportNotExist'));
            throw new Error('Failed to fetch report detail');
        }
        return await res.json();
    },

    /**
     * 投票
     */
    async vote(reportId, voteType) {
        if (!window.AuthManager?.currentUser) throw new Error(window.I18n.t('safety.loginRequired'));

        const res = await fetch(`/api/scam-tracker/votes/${reportId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'include',
            body: JSON.stringify({ vote_type: voteType })
        });

        if (!res.ok) {
            const error = await res.json();
            throw new Error(error.detail || 'Vote failed');
        }
        return await res.json();
    },

    /**
     * 獲取評論列表
     */
    async getComments(reportId) {
        const res = await fetch(`/api/scam-tracker/comments/${reportId}`);
        if (!res.ok) throw new Error('Failed to fetch comments');
        return await res.json();
    },

    /**
     * 添加評論
     */
    async addComment(reportId, content, txHash = null) {
        if (!window.AuthManager?.currentUser) throw new Error(window.I18n.t('safety.loginRequired'));

        const res = await fetch(`/api/scam-tracker/comments/${reportId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'include',
            body: JSON.stringify({
                content,
                transaction_hash: txHash
            })
        });

        if (!res.ok) {
            const error = await res.json();
            throw new Error(scamApiErrorMessage(error.detail, 'safety.proRequired', 'Comment failed'));
        }
        return await res.json();
    },

    /**
     * 提交舉報
     */
    async submitReport(data) {
        if (!window.AuthManager?.currentUser) throw new Error(window.I18n.t('safety.loginRequired'));

        const res = await fetch('/api/scam-tracker/reports', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'include',
            body: JSON.stringify(data)
        });

        if (!res.ok) {
            const error = await res.json();
            throw new Error(scamApiErrorMessage(error.detail, 'safety.proRequiredDesc', 'Submit failed'));
        }
        return await res.json();
    },

    /**
     * 今日剩餘舉報次數
     */
    async getQuota() {
        const res = await fetch('/api/scam-tracker/reports/quota', { credentials: 'include' });
        if (!res.ok) throw new Error('Failed to fetch quota');
        return await res.json();
    },

    /**
     * 獲取系統配置
     */
    async getConfig() {
        const res = await fetch('/api/scam-tracker/reports/config');
        if (!res.ok) throw new Error('Failed to fetch config');
        return await res.json();
    }
};

// 2026-09-27：列表頁（index.html：搜尋＋篩選＋舉報清單）已改成 SPA 分頁 #scamcheck
// （web/js/scam-check.js），這裡只剩詳情頁與舉報頁。
const ScamTrackerApp = {
    currentReportId: null,
    currentReport: null,
    scamTypes: [],

    /**
     * 初始化詳情頁
     */
    initDetailPage() {
        const params = new URLSearchParams(window.location.search);
        const reportId = params.get('id');

        if (!reportId) {
            showToast(window.I18n.t('safety.invalidReportId'), 'error');
            setTimeout(() => window.location.href = '/static/index.html#scamcheck', 2000);
            return;
        }

        this.currentReportId = reportId;
        this.loadReportDetail();
        this.loadComments();
        this.bindDetailEvents();
    },

    /**
     * 初始化提交頁
     */
    initSubmitPage() {
        // 從詐騙檢查分頁的「舉報這個地址」過來：?address= 預填（只填輸入框，送出前照常驗證）
        const address = (new URLSearchParams(window.location.search).get('address') || '').trim();
        const scamWallet = document.getElementById('scam-wallet');
        if (address && scamWallet && !scamWallet.value) scamWallet.value = address;
        this.loadScamTypes();
        this.checkPROStatus();
        this.bindSubmitEvents();
    },

    /**
     * 載入詐騙類型（從配置）
     */
    async loadScamTypes() {
        try {
            const config = await ScamTrackerAPI.getConfig();
            this.scamTypes = config.scam_types || [
                {id: 'fake_official', name: window.I18n.t('safety.fakeOfficial'), icon: '🎭'},
                {id: 'investment_scam', name: window.I18n.t('safety.investmentScam'), icon: '💰'},
                {id: 'fake_airdrop', name: window.I18n.t('safety.fakeAirdrop'), icon: '🎁'},
                {id: 'trading_fraud', name: window.I18n.t('safety.tradingFraud'), icon: '🔄'},
                {id: 'gambling', name: window.I18n.t('safety.gambling'), icon: '🎰'},
                {id: 'phishing', name: window.I18n.t('safety.phishing'), icon: '🎣'},
                {id: 'other', name: window.I18n.t('safety.otherScam'), icon: '⚠️'}
            ];
        } catch (e) {
            this.scamTypes = [
                {id: 'fake_official', name: window.I18n.t('safety.fakeOfficial'), icon: '🎭'},
                {id: 'investment_scam', name: window.I18n.t('safety.investmentScam'), icon: '💰'},
                {id: 'fake_airdrop', name: window.I18n.t('safety.fakeAirdrop'), icon: '🎁'},
                {id: 'trading_fraud', name: window.I18n.t('safety.tradingFraud'), icon: '🔄'},
                {id: 'gambling', name: window.I18n.t('safety.gambling'), icon: '🎰'},
                {id: 'phishing', name: window.I18n.t('safety.phishing'), icon: '🎣'},
                {id: 'other', name: window.I18n.t('safety.otherScam'), icon: '⚠️'}
            ];
        }

        // 後端 scam_types 僅存中文名 → UI 以 i18n key 優先（查無 key 才退回後端名），
        // 語言切換時選項才能跟著翻譯
        const TYPE_I18N_KEYS = {
            fake_official: 'safety.fakeOfficial',
            investment_scam: 'safety.investmentScam',
            fake_airdrop: 'safety.fakeAirdrop',
            trading_fraud: 'safety.tradingFraud',
            gambling: 'safety.gambling',
            phishing: 'safety.phishing',
            other: 'safety.otherScam',
        };
        const typeName = (type) => {
            const key = TYPE_I18N_KEYS[type.id];
            if (key && window.I18n && window.I18n.isReady && window.I18n.isReady()) {
                const translated = window.I18n.t(key);
                if (translated && translated !== key) return translated;
            }
            return type.name;
        };
        // 更新提交頁選擇器
        const submitSelect = document.getElementById('scam-type');
        if (submitSelect) {
            submitSelect.innerHTML = '<option value="">' + window.I18n.t('safety.selectScamType') + '</option>';
            this.scamTypes.forEach(type => {
                const option = document.createElement('option');
                option.value = type.id;
                option.textContent = `${type.icon} ${typeName(type)}`;
                submitSelect.appendChild(option);
            });
        }
    },

    /**
     * 檢查 PRO 狀態
     */
    checkPROStatus() {
        const user = resolveScamTrackerCurrentUser();
        if (!user) {
            window.location.href = '/static/index.html#forum';
            return;
        }

        const isPro = user.is_premium || user.is_pro;
        if (!isPro) {
            document.querySelector('form').innerHTML = `
                <div class="text-center py-12">
                    <i data-lucide="shield" class="w-16 h-16 text-textMuted mx-auto mb-4"></i>
                    <h3 class="text-xl font-bold text-secondary mb-2">${window.I18n.t('safety.proRequired')}</h3>
                    <p class="text-textMuted mb-6">${window.I18n.t('safety.proRequiredDesc')}</p>
                    <a href="/static/forum/premium.html" class="inline-block bg-primary text-background px-6 py-3 rounded-xl font-bold hover:opacity-90 transition">
                        ${window.I18n.t('safety.upgradePro')}
                    </a>
                </div>
            `;
            lucide.createIcons();
            return;
        }

        this.loadQuota();
    },

    /**
     * 今日剩餘檢舉次數（/reports/quota；以前寫死 5）。
     * 同步改 data-i18n／data-i18n-args：語言切換時 i18n.js 全頁重掃會用它們重畫。
     */
    async loadQuota() {
        const copy = document.getElementById('daily-limit-copy');
        if (!copy) return;
        let key = 'safety.quotaLoadFailed';
        let args = null;
        try {
            const quota = await ScamTrackerAPI.getQuota();
            if (typeof quota.remaining === 'number') {
                key = 'safety.dailyQuota';
                args = { count: quota.remaining };
            }
        } catch (error) {
            console.error('Load quota failed:', error);
        }
        copy.dataset.i18n = key;
        if (args) copy.dataset.i18nArgs = JSON.stringify(args);
        else delete copy.dataset.i18nArgs;
        copy.textContent = args ? window.I18n.t(key, args) : window.I18n.t(key);
        copy.closest('#daily-limit-notice')?.classList.remove('hidden');
    },

    /**
     * 載入舉報詳情
     */
    async loadReportDetail() {
        try {
            const data = await ScamTrackerAPI.getReportDetail(this.currentReportId);
            this.currentReport = data.report;
            this.renderReportDetail(this.currentReport);
            this.updateVoteButtons(this.currentReport);
        } catch (error) {
            console.error('Load report detail failed:', error);
            const container = document.getElementById('report-detail');
            if (container) {
                container.innerHTML =
                    '<div class="text-center text-danger py-8">' + window.I18n.t('safety.loadFailed') + ': ' + this.escapeHTML(error.message) + '</div>';
            }
        }
    },

    /**
     * 渲染舉報詳情
     */
    renderReportDetail(report) {
        const container = document.getElementById('report-detail');
        if (!container) return;

        container.innerHTML = `
            <div class="flex items-center gap-2 mb-4 flex-wrap">
                ${this.getStatusBadge(report.verification_status)}
                ${this.getTypeBadge(report.scam_type)}
                <span class="text-xs text-textMuted ml-auto">${this.formatDate(report.created_at)}</span>
            </div>

            <div class="mb-4">
                <label class="text-xs text-textMuted">${window.I18n.t('safety.walletAddress')}</label>
                <div class="flex items-center gap-2 bg-background rounded-xl p-3 mt-1">
                    <code class="flex-1 font-mono text-primary text-sm break-all" id="wallet-address-display">${this.escapeHTML(report.scam_wallet_address)}</code>
                    <button data-click="copyScamTrackerText" data-click-arg="wallet-address-display"
                        class="text-textMuted hover:text-primary transition flex-shrink-0">
                        <i data-lucide="copy" class="w-4 h-4"></i>
                    </button>
                </div>
            </div>

            ${report.transaction_hash ? `
            <div class="mb-4">
                <label class="text-xs text-textMuted">${window.I18n.t('safety.txHash')}</label>
                <div class="flex items-center gap-2 bg-background rounded-xl p-3 mt-1">
                    <code class="flex-1 font-mono text-xs text-textMuted break-all" id="tx-hash-display">${this.escapeHTML(report.transaction_hash)}</code>
                    <button data-click="copyScamTrackerText" data-click-arg="tx-hash-display"
                        class="text-textMuted hover:text-primary transition flex-shrink-0">
                        <i data-lucide="copy" class="w-4 h-4"></i>
                    </button>
                </div>
            </div>
            ` : ''}

            <div class="mb-4">
                <label class="text-xs text-textMuted">${window.I18n.t('safety.scamDescription')}</label>
                <div class="bg-background rounded-xl p-4 mt-1 text-textMuted leading-relaxed text-sm">
                    ${this.escapeHTML(report.description).replace(/\n/g, '<br>')}
                </div>
            </div>

            <div class="flex items-center justify-between text-xs text-textMuted border-t border-white/5 pt-4">
                <span>${window.I18n.t('safety.reporter')}: ${this.escapeHTML(report.reporter_wallet_masked)}</span>
                <span>${window.I18n.t('safety.views')}: ${report.view_count}</span>
            </div>
        `;

        // 更新投票計數
        document.getElementById('count-approve').textContent = report.approve_count;
        document.getElementById('count-reject').textContent = report.reject_count;

        // 更新進度條
        this.updateVoteProgress(report.approve_count, report.reject_count);

        lucide.createIcons();
    },

    /**
     * 更新投票按鈕狀態
     */
    updateVoteButtons(report) {
        const btnApprove = document.getElementById('btn-approve');
        const btnReject = document.getElementById('btn-reject');

        if (!btnApprove || !btnReject) return;

        // 重置按鈕狀態
        btnApprove.classList.remove('ring-2', 'ring-success');
        btnReject.classList.remove('ring-2', 'ring-danger');

        // 設置當前投票狀態
        if (report.viewer_vote === 'approve') {
            btnApprove.classList.add('ring-2', 'ring-success');
        } else if (report.viewer_vote === 'reject') {
            btnReject.classList.add('ring-2', 'ring-danger');
        }
    },

    /**
     * 更新投票進度條
     */
    updateVoteProgress(approve, reject) {
        const total = approve + reject;
        const percentage = total > 0 ? Math.round((approve / total) * 100) : 0;

        document.getElementById('vote-percentage').textContent = percentage + '%';
        document.getElementById('vote-progress-bar').style.width = percentage + '%';

        // 根據進度條顏色
        const progressBar = document.getElementById('vote-progress-bar');
        if (percentage >= 70) {
            progressBar.className = 'h-full bg-success transition-all duration-300';
        } else if (percentage >= 40) {
            progressBar.className = 'h-full bg-danger transition-all duration-300';
        } else {
            progressBar.className = 'h-full bg-danger transition-all duration-300';
        }
    },

    /**
     * 載入評論
     */
    async loadComments() {
        try {
            const data = await ScamTrackerAPI.getComments(this.currentReportId);
            const comments = data.comments || [];
            this.renderComments(comments);

            // 如果用戶是 PRO，顯示評論表單
            const user = resolveScamTrackerCurrentUser();
            if (user && (user.is_premium || user.is_pro)) {
                const commentForm = document.getElementById('comment-form');
                if (commentForm) commentForm.classList.remove('hidden');
            }
        } catch (error) {
            console.error('Load comments failed:', error);
        }
    },

    /**
     * 渲染評論列表
     */
    renderComments(comments) {
        const container = document.getElementById('comments-list');
        if (!container) return;

        if (comments.length === 0) {
            container.innerHTML = '<div class="text-center text-textMuted py-4">' + window.I18n.t('safety.noComments') + '</div>';
            return;
        }

        container.innerHTML = comments.map(comment => `
            <div class="bg-background rounded-xl p-4">
                <div class="flex items-center justify-between mb-2">
                    <span class="font-bold text-secondary text-sm">${this.escapeHTML(comment.username || window.I18n.t('safety.anonymous'))}</span>
                    <span class="text-xs text-textMuted">${this.formatDate(comment.created_at)}</span>
                </div>
                <p class="text-textMuted text-sm">${this.escapeHTML(comment.content).replace(/\n/g, '<br>')}</p>
                ${comment.transaction_hash ? `
                    <div class="mt-2 pt-2 border-t border-white/5">
                        <code class="text-xs text-textMuted font-mono">TX: ${this.escapeHTML(comment.transaction_hash)}</code>
                    </div>
                ` : ''}
            </div>
        `).join('');
    },

    /**
     * 綁定詳情頁事件
     */
    bindDetailEvents() {
        // 投票按鈕
        const btnApprove = document.getElementById('btn-approve');
        const btnReject = document.getElementById('btn-reject');

        if (btnApprove) {
            btnApprove.addEventListener('click', () => this.handleVote('approve'));
        }
        if (btnReject) {
            btnReject.addEventListener('click', () => this.handleVote('reject'));
        }

        // 提交評論
        const btnSubmitComment = document.getElementById('btn-submit-comment');
        if (btnSubmitComment) {
            btnSubmitComment.addEventListener('click', () => this.handleSubmitComment());
        }
    },

    /**
     * 綁定提交頁事件
     */
    bindSubmitEvents() {
        const form = document.getElementById('scam-report-form');
        const description = document.getElementById('description');
        const charCount = document.getElementById('char-count');

        if (description && charCount) {
            description.addEventListener('input', () => {
                charCount.textContent = description.value.length;
            });
        }

        if (form) {
            form.addEventListener('submit', (e) => this.handleSubmitReport(e));
        }
    },

    /**
     * 處理投票
     */
    async handleVote(voteType) {
        try {
            const result = await ScamTrackerAPI.vote(this.currentReportId, voteType);
            const voteLabel = voteType === 'approve' ? window.I18n.t('safety.approve') : window.I18n.t('safety.reject');
            const actionMessages = {
                'voted': window.I18n.t('safety.voted').replace('{action}', voteLabel),
                'cancelled': window.I18n.t('safety.voteCancelled').replace('{action}', voteLabel),
                'switched': window.I18n.t('safety.voteSwitched').replace('{action}', voteLabel)
            };
            showToast(actionMessages[result.action] || window.I18n.t('safety.voteSuccess'), 'success');
            this.loadReportDetail();
        } catch (error) {
            console.error('Vote failed:', error);
            showToast(error.message || window.I18n.t('safety.voteFailed'), 'error');
        }
    },

    /**
     * 處理提交評論
     */
    async handleSubmitComment() {
        const contentInput = document.getElementById('comment-content');
        const txHashInput = document.getElementById('comment-tx-hash');

        const content = contentInput.value.trim();
        const txHash = txHashInput.value.trim();

        if (!content || content.length < 10) {
            showToast(window.I18n.t('safety.commentMinLength'), 'warning');
            return;
        }
        if (txHash && !isValidTxHash(txHash)) {
            showToast(window.I18n.t('safety.txHashFormatError'), 'error');
            return;
        }

        try {
            await ScamTrackerAPI.addComment(this.currentReportId, content, txHash || null);
            showToast(window.I18n.t('safety.commentSuccess'), 'success');
            contentInput.value = '';
            txHashInput.value = '';
            this.loadComments();
            this.loadReportDetail();
        } catch (error) {
            console.error('Add comment failed:', error);
            showToast(error.message || window.I18n.t('safety.commentFailed'), 'error');
        }
    },

    /**
     * 處理提交舉報
     */
    async handleSubmitReport(e) {
        e.preventDefault();

        const scamWallet = normalizeWalletInput(document.getElementById('scam-wallet').value);
        const reporterWallet = normalizeWalletInput(document.getElementById('reporter-wallet').value);
        const scamType = document.getElementById('scam-type').value;
        const description = document.getElementById('description').value.trim();
        const txHash = document.getElementById('tx-hash').value.trim().toLowerCase();

        // 驗證
        if (!isValidWalletAddress(scamWallet)) {
            showToast(window.I18n.t('safety.scamWalletFormatError'), 'error');
            return;
        }
        if (!isValidWalletAddress(reporterWallet)) {
            showToast(window.I18n.t('safety.reporterWalletFormatError'), 'error');
            return;
        }
        if (!scamType) {
            showToast(window.I18n.t('safety.pleaseSelectScamType'), 'warning');
            return;
        }
        if (description.length < 20 || description.length > 2000) {
            showToast(window.I18n.t('safety.descriptionLengthError'), 'error');
            return;
        }
        if (txHash && !isValidTxHash(txHash)) {
            showToast(window.I18n.t('safety.txHashFormatError'), 'error');
            return;
        }

        const btnSubmit = document.getElementById('btn-submit');
        btnSubmit.disabled = true;
        btnSubmit.innerHTML = '<i data-lucide="loader-2" class="w-5 h-5 animate-spin"></i> ' + window.I18n.t('safety.submitting') + '...';
        lucide.createIcons();

        try {
            const result = await ScamTrackerAPI.submitReport({
                scam_wallet_address: scamWallet,
                reporter_wallet_address: reporterWallet,
                scam_type: scamType,
                description: description,
                transaction_hash: txHash || null
            });

            showToast(window.I18n.t('safety.reportSubmitSuccess'), 'success');
            setTimeout(() => {
                window.location.href = `/static/scam-tracker/detail.html?id=${result.report_id}`;
            }, 1000);
        } catch (error) {
            console.error('Submit report failed:', error);
            showToast(error.message || window.I18n.t('safety.submitFailed'), 'error');
            btnSubmit.disabled = false;
            btnSubmit.innerHTML = '<i data-lucide="send" class="w-5 h-5"></i> ' + window.I18n.t('safety.submitReport');
            lucide.createIcons();
        }
    },

    /**
     * 工具函數
     */
    getStatusBadge(status) {
        const badges = {
            'verified': '<span class="bg-success/20 text-success px-2 py-0.5 rounded text-xs font-bold">✅ ' + window.I18n.t('safety.verified') + '</span>',
            'pending': '<span class="bg-danger/20 text-danger px-2 py-0.5 rounded text-xs font-bold">⏳ ' + window.I18n.t('safety.pending') + '</span>',
            'disputed': '<span class="bg-danger/20 text-danger px-2 py-0.5 rounded text-xs font-bold">⚠️ ' + window.I18n.t('safety.disputed') + '</span>'
        };
        return badges[status] || badges.pending;
    },

    getTypeBadge(type) {
        const typeObj = this.scamTypes.find(t => t.id === type);
        const name = typeObj ? `${typeObj.icon} ${typeObj.name}` : type;
        return `<span class="bg-primary/10 text-primary px-2 py-0.5 rounded text-xs font-bold">${name}</span>`;
    },

    formatDate(isoString) {
        const date = new Date(isoString);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);

        if (diffMins < 1) return window.I18n.t('time.justNow');
        if (diffMins < 60) return window.I18n.t('time.minutesAgo').replace('{n}', diffMins);

        const diffHours = Math.floor(diffMins / 60);
        if (diffHours < 24) return window.I18n.t('time.hoursAgo').replace('{n}', diffHours);

        const diffDays = Math.floor(diffHours / 24);
        if (diffDays < 7) return window.I18n.t('time.daysAgo').replace('{n}', diffDays);

        return date.toLocaleDateString(window.I18n ? window.I18n.getLanguage() : 'en');
    },

    escapeHTML(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
};

// Expose to global scope for cross-module access (e.g. safetyTab.js, scam-tracker-i18n.js)
window.ScamTrackerAPI = ScamTrackerAPI;
// scam-tracker-i18n.js 的 patchApp/refreshDynamicUi 透過 window.ScamTrackerApp
// 取得 App 本體（const 不會自動掛 window）——語言切換時的重渲染靠它
window.ScamTrackerApp = ScamTrackerApp;
