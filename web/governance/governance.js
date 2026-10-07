/**
 * 社群治理系統前端邏輯
 */

// API 基礎路徑
const API_BASE = '/api/governance';

// 當前用戶信息
let currentUser = null;
let isProMember = false;

// ============================================
// 初始化
// ============================================

document.addEventListener('DOMContentLoaded', function() {
    // 標籤切換
    setupTabs();

    // 檢舉表單
    setupReportForm();

    // 檢查用戶登入狀態
    checkAuthStatus();

    // 模態框關閉
    setupModal();
});

// ============================================
// 標籤切換
// ============================================

function setupTabs() {
    const tabButtons = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');

    tabButtons.forEach(button => {
        button.addEventListener('click', function() {
            const tabName = this.dataset.tab;

            // 更新按鈕狀態
            tabButtons.forEach(btn => btn.classList.remove('active'));
            this.classList.add('active');

            // 更新內容顯示
            tabContents.forEach(content => {
                content.classList.remove('active');
                if (content.id === `${tabName}-tab`) {
                    content.classList.add('active');

                    // 載入相應數據
                    loadTabData(tabName);
                }
            });
        });
    });
}

function switchTab(tabName) {
    const tabButton = document.querySelector(`.tab-btn[data-tab="${tabName}"]`);
    if (tabButton) {
        tabButton.click();
    }
}

// ============================================
// 用戶認證
// ============================================

async function checkAuthStatus() {
    // 訪客不打 /api/user/me——governance 頁先前對未登入者也發請求，
    // 每次造訪吃一個 401（console 噪音）。優先使用共用的 AuthManager
    // 快取；僅在看似已登入時才向後端驗證。
    const cached = window.AuthManager && window.AuthManager.currentUser;
    if (!cached) {
        return;
    }
    try {
        const response = await fetch('/api/user/me', { credentials: 'include' });

        if (response.ok) {
            const data = await response.json();
            currentUser = data.user;
            isProMember = ['premium', 'pro'].includes(currentUser.membership_tier);

            // 更新 UI
            updateUIForUser();
        }
    } catch (error) {
        console.error('Auth check failed:', error);
    }
}

function updateUIForUser() {
    // 更新 PRO 專用內容
    if (isProMember) {
        document.getElementById('pro-notice').style.display = 'none';
        document.getElementById('review-content').style.display = 'block';
    }

    // 載入每日限制
    loadDailyLimit();
}

// ============================================
// 檢舉表單
// ============================================

function setupReportForm() {
    const form = document.getElementById('report-form');
    // 送出中再按一次會重複檢舉（還會多吃一次每日額度）
    let submitting = false;

    form.addEventListener('submit', async function(e) {
        e.preventDefault();
        if (submitting) return;

        if (!currentUser) {
            window.showToast(window.I18n.t('governance.pleaseLogin'), 'error');
            return;
        }

        const formData = new FormData(form);
        const data = {
            content_type: formData.get('content_type'),
            content_id: parseInt(formData.get('content_id')),
            report_type: formData.get('report_type'),
            description: formData.get('description') || null
        };

        // 驗證
        if (!data.content_type || !data.content_id || !data.report_type) {
            window.showToast(window.I18n.t('governance.pleaseFillAllFields'), 'error');
            return;
        }

        submitting = true;
        try {
            const response = await fetch(`${API_BASE}/reports`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                credentials: 'include',
                body: JSON.stringify(data)
            });

            const result = await response.json();

            if (response.ok) {
                window.showToast(window.I18n.t('governance.reportSubmitSuccess'), 'success');
                form.reset();
                loadDailyLimit();
            } else {
                // 422 的 detail 是驗證錯誤陣列，直接丟進 toast 會變成 [object Object]
                const detail = typeof result.detail === 'string' ? result.detail : '';
                window.showToast(detail || window.I18n.t('governance.submitFailed'), 'error');
            }
        } catch (error) {
            console.error('Submit report error:', error);
            window.showToast(window.I18n.t('governance.submitFailed'), 'error');
        } finally {
            submitting = false;
        }
    });
}

async function loadDailyLimit() {
    if (!currentUser) return;

    try {
        const response = await fetch(`${API_BASE}/reports`, { credentials: 'include' });

        if (response.ok) {
            const data = await response.json();
            const todayReports = data.reports ? data.reports.filter(r => {
                const reportDate = new Date(r.created_at).toDateString();
                const today = new Date().toDateString();
                return reportDate === today;
            }).length : 0;

            const limit = isProMember ? 10 : 5;
            const remaining = limit - todayReports;

            document.getElementById('daily-limit').textContent =
                window.I18n.t('governance.remainingToday').replace('{remaining}', remaining).replace('{limit}', limit);
        }
    } catch (error) {
        console.error('Load daily limit error:', error);
    }
}

// ============================================
// 標籤數據載入
// ============================================

async function loadTabData(tabName) {
    switch(tabName) {
        case 'review':
            await loadPendingReports();
            await loadMyReputation();
            break;
        case 'my-reports':
            await loadMyReports();
            break;
        case 'violations':
            await loadViolationPoints();
            break;
        case 'leaderboard':
            await loadLeaderboard();
            break;
    }
}

// ============================================
// 審核隊列
// ============================================

async function loadPendingReports() {
    const listElement = document.getElementById('pending-reports-list');
    const loadingElement = document.getElementById('review-loading');
    const emptyElement = document.getElementById('review-empty');

    loadingElement.style.display = 'block';
    listElement.innerHTML = '';
    emptyElement.style.display = 'none';

    try {
        const response = await fetch(`${API_BASE}/reports/pending`, { credentials: 'include' });

        if (response.ok) {
            const data = await response.json();
            const reports = data.reports || [];

            loadingElement.style.display = 'none';

            if (reports.length === 0) {
                emptyElement.style.display = 'block';
                return;
            }

            reports.forEach(report => {
                const reportElement = createReportElement(report, true);
                listElement.appendChild(reportElement);
            });
        } else {
            const result = await response.json();
            if (result.detail && result.detail.includes('PRO')) {
                document.getElementById('pro-notice').style.display = 'block';
                document.getElementById('review-content').style.display = 'none';
            }
            loadingElement.style.display = 'none';
        }
    } catch (error) {
        console.error('Load pending reports error:', error);
        loadingElement.style.display = 'none';
        window.showToast(window.I18n.t('governance.loadFailed'), 'error');
    }
}

function createReportElement(report, showVoteButtons = false) {
    const div = document.createElement('div');
    div.className = 'report-item';
    div.dataset.reportId = report.id;

    const typeLabels = {
        spam: window.I18n.t('governance.spam'),
        harassment: window.I18n.t('governance.harassment'),
        misinformation: window.I18n.t('governance.misinformation'),
        scam: window.I18n.t('governance.scam'),
        illegal: window.I18n.t('governance.illegal'),
        other: window.I18n.t('governance.other')
    };

    let html = `
        <div class="report-header">
            <span class="report-id">#${report.id}</span>
            <span class="report-status status-${report.review_status}">
                ${getStatusText(report.review_status)}
            </span>
        </div>
        <div class="report-content">
            <div class="report-icon">📋</div>
            <div class="report-details">
                <span class="report-type">${typeLabels[report.report_type] || report.report_type}</span>
                ${report.description ? `<p class="report-description">${escapeHtml(report.description)}</p>` : ''}
                <div class="report-meta">
                    <span>${report.content_type === 'post' ? window.I18n.t('governance.post') : window.I18n.t('governance.comment')} #${report.content_id}</span>
                    <span>${formatDate(report.created_at)}</span>
                    ${showVoteButtons ? `
                        <span>👍 ${report.approve_count || 0} / 👎 ${report.reject_count || 0}</span>
                    ` : ''}
                </div>
            </div>
        </div>
    `;

    if (showVoteButtons && report.review_status === 'pending') {
        html += `
            <div class="vote-section">
                <div class="vote-buttons">
                    <button class="vote-btn approve" data-click="voteOnReport" data-click-args="${encodeURIComponent(JSON.stringify([report.id, 'approve']))}">
                        👍 ${window.I18n.t('governance.voteApprove')}
                    </button>
                    <button class="vote-btn reject" data-click="voteOnReport" data-click-args="${encodeURIComponent(JSON.stringify([report.id, 'reject']))}">
                        👎 ${window.I18n.t('governance.voteReject')}
                    </button>
                </div>
            </div>
        `;
    }

    div.innerHTML = html;
    return div;
}

async function voteOnReport(reportId, voteType) {
    try {
        const response = await fetch(`${API_BASE}/reports/${reportId}/vote`, {
            method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                credentials: 'include',
            body: JSON.stringify({ vote_type: voteType })
        });

        const result = await response.json();

        if (response.ok) {
            window.showToast(result.message || window.I18n.t('governance.voteSuccess'), 'success');

            // 如果達成共識，顯示提示
            if (result.consensus_reached) {
                window.showToast(result.consensus_message || window.I18n.t('governance.consensusReached'), 'info');
            }

            // 重新載入列表
            loadPendingReports();
        } else {
            const detail = typeof result.detail === 'string' ? result.detail : '';
            window.showToast(detail || window.I18n.t('governance.voteFailed'), 'error');
        }
    } catch (error) {
        console.error('Vote error:', error);
        window.showToast(window.I18n.t('governance.voteFailed'), 'error');
    }
}

// ============================================
// 我的檢舉
// ============================================

async function loadMyReports(status = 'all') {
    const listElement = document.getElementById('my-reports-list');
    const loadingElement = document.getElementById('my-reports-loading');
    const emptyElement = document.getElementById('my-reports-empty');

    loadingElement.style.display = 'block';
    listElement.innerHTML = '';
    emptyElement.style.display = 'none';

    try {
        const url = status === 'all' ? `${API_BASE}/reports` : `${API_BASE}/reports?status=${status}`;
        const response = await fetch(url, { credentials: 'include' });

        if (response.ok) {
            const data = await response.json();
            const reports = data.reports || [];

            loadingElement.style.display = 'none';

            if (reports.length === 0) {
                emptyElement.style.display = 'block';
                return;
            }

            reports.forEach(report => {
                const reportElement = createReportElement(report);
                listElement.appendChild(reportElement);
            });
        } else {
            loadingElement.style.display = 'none';
        }
    } catch (error) {
        console.error('Load my reports error:', error);
        loadingElement.style.display = 'none';
        window.showToast(window.I18n.t('governance.loadFailed'), 'error');
    }
}

// 篩選按鈕
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('filter-btn')) {
        document.querySelectorAll('.filter-btn').forEach(btn => btn.classList.remove('active'));
        e.target.classList.add('active');
        loadMyReports(e.target.dataset.status);
    }
});

// ============================================
// 違規記錄
// ============================================

async function loadViolationPoints() {
    const pointsElement = document.getElementById('violation-points');
    const listElement = document.getElementById('violations-list');
    const loadingElement = document.getElementById('violations-loading');

    loadingElement.style.display = 'block';

    try {
        const response = await fetch(`${API_BASE}/violations`, { credentials: 'include' });

        if (response.ok) {
            const data = await response.json();
            const points = data.points || {};
            const violations = data.recent_violations || [];

            // 顯示點數摘要
            pointsElement.innerHTML = `
                <div class="points-summary">
                    <div class="points-header">
                        <div>
                            <div class="points-value">${points.points || 0}</div>
                            <div class="points-label">${window.I18n.t('governance.violationPoints')}</div>
                        </div>
                    </div>
                    <div class="points-actions">
                        <div class="points-action">
                            <div class="points-action-value">${points.total_violations || 0}</div>
                            <div class="points-action-label">${window.I18n.t('governance.totalViolations')}</div>
                        </div>
                        <div class="points-action">
                            <div class="points-action-value">${points.suspension_count || 0}</div>
                            <div class="points-action-label">${window.I18n.t('governance.suspensionCount')}</div>
                        </div>
                    </div>
                </div>
            `;

            // 顯示違規列表
            listElement.innerHTML = '';
            if (violations.length === 0) {
                listElement.innerHTML = '<p class="empty-state">✅ ' + window.I18n.t('governance.noViolations') + '</p>';
            } else {
                violations.forEach(violation => {
                    const violationElement = createViolationElement(violation);
                    listElement.appendChild(violationElement);
                });
            }
        }

        loadingElement.style.display = 'none';
    } catch (error) {
        console.error('Load violation points error:', error);
        loadingElement.style.display = 'none';
        window.showToast(window.I18n.t('governance.loadFailed'), 'error');
    }
}

function createViolationElement(violation) {
    const div = document.createElement('div');
    div.className = 'violation-item';

    const levelLabels = {
        mild: window.I18n.t('governance.levelMild'),
        medium: window.I18n.t('governance.levelMedium'),
        severe: window.I18n.t('governance.levelSevere'),
        critical: window.I18n.t('governance.levelCritical')
    };

    const typeLabels = {
        spam: window.I18n.t('governance.spam'),
        harassment: window.I18n.t('governance.harassment'),
        misinformation: window.I18n.t('governance.misinformation'),
        scam: window.I18n.t('governance.scam'),
        illegal: window.I18n.t('governance.illegal'),
        other: window.I18n.t('governance.other')
    };

    div.innerHTML = `
        <span class="violation-level level-${violation.violation_level}">
            ${levelLabels[violation.violation_level] || violation.violation_level}
        </span>
        <div class="violation-details">
            <div class="violation-type">
                ${typeLabels[violation.violation_type] || violation.violation_type} - ${violation.points} ${window.I18n.t('governance.points')}
            </div>
            <div class="violation-meta">
                ${formatDate(violation.created_at)}
                ${violation.action_taken ? `· ${getActionText(violation.action_taken)}` : ''}
            </div>
        </div>
    `;

    return div;
}

// ============================================
// 排行榜
// ============================================

async function loadLeaderboard() {
    const listElement = document.getElementById('leaderboard-list');
    const loadingElement = document.getElementById('leaderboard-loading');

    loadingElement.style.display = 'block';
    listElement.innerHTML = '';

    try {
        const response = await fetch(`${API_BASE}/reviewers/leaderboard`, { credentials: 'include' });

        if (response.ok) {
            const data = await response.json();
            const reviewers = data.leaderboard || [];

            loadingElement.style.display = 'none';

            reviewers.forEach((reviewer, index) => {
                const reviewerElement = createLeaderboardItem(reviewer, index + 1);
                listElement.appendChild(reviewerElement);
            });
        } else {
            loadingElement.style.display = 'none';
        }
    } catch (error) {
        console.error('Load leaderboard error:', error);
        loadingElement.style.display = 'none';
        window.showToast(window.I18n.t('governance.loadFailed'), 'error');
    }
}

function createLeaderboardItem(reviewer, rank) {
    const div = document.createElement('div');
    div.className = 'leaderboard-item';

    const rankClass = rank <= 3 ? `rank-${rank}` : '';

    div.innerHTML = `
        <div class="leaderboard-rank ${rankClass}">${rank}</div>
        <div class="leaderboard-avatar">
            ${reviewer.username ? reviewer.username.charAt(0).toUpperCase() : '?'}
        </div>
        <div class="leaderboard-info">
            <div class="leaderboard-name">${escapeHtml(reviewer.username || window.I18n.t('governance.anonymous'))}</div>
            <div class="leaderboard-stats">
                ${window.I18n.t('governance.reviews')}: ${reviewer.total_reviews} · ${window.I18n.t('governance.accuracy')}: ${(reviewer.accuracy_rate * 100).toFixed(1)}%
            </div>
        </div>
        <div class="leaderboard-score">
            <div class="reputation-score">${reviewer.reputation_score}</div>
            <div class="accuracy-rate">${window.I18n.t('governance.reputationScore')}</div>
        </div>
    `;

    return div;
}

// ============================================
// 聲望信息
// ============================================

async function loadMyReputation() {
    const reputationElement = document.getElementById('my-reputation');

    try {
        const response = await fetch(`${API_BASE}/reputation`, { credentials: 'include' });

        if (response.ok) {
            const data = await response.json();
            const reputation = data.reputation || {};

            reputationElement.innerHTML = `
                <div class="reputation-display">
                    <div class="reputation-stat">
                        <div class="reputation-value">${reputation.total_reviews || 0}</div>
                        <div class="reputation-label">${window.I18n.t('governance.totalReviews')}</div>
                    </div>
                    <div class="reputation-stat">
                        <div class="reputation-value">${reputation.correct_votes || 0}</div>
                        <div class="reputation-label">${window.I18n.t('governance.correctVotes')}</div>
                    </div>
                    <div class="reputation-stat">
                        <div class="reputation-value">${(reputation.accuracy_rate * 100).toFixed(1)}%</div>
                        <div class="reputation-label">${window.I18n.t('governance.accuracy')}</div>
                    </div>
                    <div class="reputation-stat">
                        <div class="reputation-value">${reputation.reputation_score || 0}</div>
                        <div class="reputation-label">${window.I18n.t('governance.reputationScore')}</div>
                    </div>
                </div>
            `;
        }
    } catch (error) {
        console.error('Load reputation error:', error);
    }
}

// ============================================
// 工具函數
// ============================================

function getStatusText(status) {
    const statusMap = {
        pending: window.I18n.t('governance.statusPending'),
        approved: window.I18n.t('governance.statusApproved'),
        rejected: window.I18n.t('governance.statusRejected')
    };
    return statusMap[status] || status;
}

function getActionText(action) {
    const actionMap = {
        warning: window.I18n.t('governance.actionWarning'),
        suspend_3d: window.I18n.t('governance.actionSuspend3d'),
        suspend_7d: window.I18n.t('governance.actionSuspend7d'),
        suspend_30d: window.I18n.t('governance.actionSuspend30d'),
        permanent_ban: window.I18n.t('governance.actionPermanentBan')
    };
    return actionMap[action] || action;
}

function formatDate(dateString) {
    if (!dateString) return '';
    const date = new Date(dateString);
    const now = new Date();
    const diff = now - date;

    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);

    if (minutes < 1) return window.I18n.t('time.justNow');
    if (minutes < 60) return window.I18n.t('time.minutesAgo').replace('{n}', minutes);
    if (hours < 24) return window.I18n.t('time.hoursAgo').replace('{n}', hours);
    if (days < 7) return window.I18n.t('time.daysAgo').replace('{n}', days);

    return date.toLocaleDateString('zh-TW');
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================
// 模態框
// ============================================

function setupModal() {
    const modal = document.getElementById('report-detail-modal');
    const closeBtn = modal.querySelector('.close-btn');

    closeBtn.addEventListener('click', () => {
        modal.classList.remove('show');
    });

    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.classList.remove('show');
        }
    });
}

function showReportDetail(reportId) {
    // TODO: 實現檢舉詳情顯示
    const modal = document.getElementById('report-detail-modal');
    const content = document.getElementById('report-detail-content');

    content.innerHTML = '<p>' + window.I18n.t('governance.loading') + '</p>';
    modal.classList.add('show');

    // 載入詳情數據
    fetch(`${API_BASE}/reports/${reportId}`, { credentials: 'include' })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // 顯示詳情
        }
    })
    .catch(error => {
        console.error('Load report detail error:', error);
        content.innerHTML = '<p>' + window.I18n.t('governance.loadFailed') + '</p>';
    });
}
