// forum/create.html 的頁面腳本（2026-09-25 自 inline <script> 原樣移出：正式站 CSP
// script-src 不再放行 'unsafe-inline'）。classic script——頂層宣告維持全域，
// 執行時機與原 inline 相同（解析到該 <script> 時同步執行）。

document.addEventListener('DOMContentLoaded', async () => {
    const backLink = document.getElementById('forum-create-back-link');
    if (backLink) {
        backLink.href = sessionStorage.getItem('forumBackHref') || '/static/index.html#forum';
    }

    if (typeof initializeAuth === 'function') {
        await initializeAuth();
    }

    if (typeof AuthManager !== 'undefined' && !AuthManager.currentUser) {
        window.location.replace('/static/index.html#forum');
        return;
    }

    if (typeof ForumApp !== 'undefined' && typeof ForumApp.init === 'function') {
        ForumApp.init();
    }
});
