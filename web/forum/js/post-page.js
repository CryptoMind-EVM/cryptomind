// forum/post.html 的頁面腳本（2026-09-25 自 inline <script> 原樣移出：正式站 CSP
// script-src 不再放行 'unsafe-inline'）。classic script——頂層宣告維持全域，
// 執行時機與原 inline 相同（解析到該 <script> 時同步執行）。

document.addEventListener('DOMContentLoaded', async () => {
    const backLink = document.getElementById('forum-back-link');
    if (backLink) {
        backLink.href = sessionStorage.getItem('forumBackHref') || '/static/index.html#forum';
    }

    if (typeof initializeAuth === 'function') await initializeAuth();
    if (typeof ForumApp !== 'undefined') ForumApp.init();
    initReportModal();
});

function initReportModal() {
    const reportBtn = document.getElementById('btn-report-post');
    const modal = document.getElementById('report-modal');
    const closeBtn = document.getElementById('report-modal-close');
    const cancelBtn = document.getElementById('report-modal-cancel');
    const form = document.getElementById('report-form');

    if (!reportBtn) return;

    reportBtn.addEventListener('click', function () {
        const postId = ForumApp?.currentPostId;
        if (postId) {
            document.getElementById('report-content-id').value = postId;
            document.getElementById('report-content-type').value = 'post';
            const errorDiv = document.getElementById('report-error');
            if (errorDiv) errorDiv.classList.add('hidden');
            modal.classList.remove('hidden');
        }
    });

    const closeModal = () => {
        modal.classList.add('hidden');
        form.reset();
    };

    closeBtn?.addEventListener('click', closeModal);
    cancelBtn?.addEventListener('click', closeModal);
    modal?.addEventListener('click', (e) => {
        if (e.target === modal) closeModal();
    });

    form?.addEventListener('submit', async function (e) {
        e.preventDefault();
        const contentType = document.getElementById('report-content-type').value;
        const contentId = document.getElementById('report-content-id').value;
        const reportType = document.getElementById('report-type').value;
        const description = document.getElementById('report-description').value;
        const errorDiv = document.getElementById('report-error');
        const errorMsg = document.getElementById('report-error-msg');

        const showError = (msg) => {
            if (errorDiv && errorMsg) {
                errorMsg.textContent = msg;
                errorDiv.classList.remove('hidden');
            } else {
                showToast(msg, 'error');
            }
        };

        if (!reportType) {
            showError(window.I18n ? window.I18n.t('forum.selectReportReasonFirst') : 'Please select a report reason');
            return;
        }

        if (errorDiv) errorDiv.classList.add('hidden');

        try {
            if (!(typeof AuthManager !== 'undefined' && AuthManager.currentUser)) {
                showToast(window.I18n ? window.I18n.t('forum.pleaseLoginFirst') : 'Please login first', 'error');
                return;
            }

            const response = await fetch('/api/governance/reports', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                credentials: 'include',
                body: JSON.stringify({
                    content_type: contentType,
                    content_id: parseInt(contentId, 10),
                    report_type: reportType,
                    description
                })
            });

            const result = await response.json();
            if (!response.ok || !result.success) {
                showError(result.detail || (window.I18n ? window.I18n.t('forum.submitReportFailed') : 'Failed to submit report'));
                return;
            }

            showToast(window.I18n ? window.I18n.t('forum.reportSubmitted') : 'Report submitted', 'success');
            closeModal();
        } catch (error) {
            showError(window.I18n ? window.I18n.t('forum.submitReportFailed') : 'Failed to submit report');
            console.error('Report submit error:', error);
        }
    });
}
