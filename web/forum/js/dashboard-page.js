// forum/dashboard.html 的頁面腳本（2026-09-25 自 inline <script> 原樣移出：正式站 CSP
// script-src 不再放行 'unsafe-inline'）。classic script——頂層宣告維持全域，
// 執行時機與原 inline 相同（解析到該 <script> 時同步執行）。

function showConfirm(options) {
    return new Promise((resolve) => {
        const modal = document.getElementById('confirm-modal');
        document.getElementById('confirm-modal-title').textContent = options.title || (window.I18n ? window.I18n.t('common.confirm') : 'Confirm');
        document.getElementById('confirm-modal-message').textContent = options.message || (window.I18n ? window.I18n.t('common.confirmQuestion') : 'Are you sure?');
        document.getElementById('confirm-modal-confirm').textContent = options.confirmText || (window.I18n ? window.I18n.t('common.confirm') : 'Confirm');
        document.getElementById('confirm-modal-cancel').textContent = options.cancelText || (window.I18n ? window.I18n.t('common.cancel') : 'Cancel');
        modal.classList.remove('hidden');

        const cleanup = () => {
            modal.classList.add('hidden');
            document.getElementById('confirm-modal-confirm').onclick = null;
            document.getElementById('confirm-modal-cancel').onclick = null;
        };
        document.getElementById('confirm-modal-confirm').onclick = () => { cleanup(); resolve(true); };
        document.getElementById('confirm-modal-cancel').onclick = () => { cleanup(); resolve(false); };
    });
}

// ========================================
// Initialize
// ========================================
document.addEventListener('DOMContentLoaded', async () => {
    const backLink = document.getElementById('forum-dashboard-back-link');
    if (backLink) {
        backLink.href = sessionStorage.getItem('forumBackHref') || '/static/index.html#forum';
    }

    // 1. Initialize Auth
    if (typeof AuthManager !== 'undefined') {
        await AuthManager.init();
    }

    // 2. Initialize Forum App
    if (typeof ForumApp !== 'undefined') {
        ForumApp.init();
    }

    lucide.createIcons();
});
