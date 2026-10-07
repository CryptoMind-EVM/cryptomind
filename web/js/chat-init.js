// ========================================
// chat-init.js - 聊天初始化與反饋
// 職責：initChat、resetChatInit、submitFeedback
// 依賴：所有其他 chat-*.js 模組
// ========================================

async function initChat() {
    // 防止重複初始化
    if (window.chatInitialized) {
        console.log('initChat: already initialized, skipping');
        return;
    }

    // 🔒 安全檢查：必須先登入（用戶認證）才能載入聊天記錄
    // 這防止未授權的用戶看到歷史對話
    const isLoggedIn = window.AuthManager?.isLoggedIn();

    if (!isLoggedIn) {
        // 未登入，只顯示歡迎畫面，不載入任何歷史記錄
        showWelcomeScreen();
        // 清空側邊欄
        const list = document.getElementById('chat-session-list');
        if (list) {
            list.innerHTML =
                `<div class="text-center text-xs text-textMuted/40 py-4">${window.I18n?.t('chatSessions.loginFirst') || 'Please login first'}</div>`;
        }
        return;
    }

    window.chatInitialized = true;
    AppStore.set('chatInitialized', true);
    console.log('initChat: initializing chat...');

    // 2. 檢查是否有現有的 session，如果沒有才創建新的
    const userId =
        window.currentUserId ||
        AuthManager.currentUser?.user_id ||
        AuthManager.currentUser?.uid ||
        null;

    // Cookie-backed session 可能沒有前端 access token，但只要已有已驗證 user 就允許繼續載入。
    if (!userId) {
        console.error('initChat: No authenticated user found');
        window.chatInitialized = false;
        AppStore.set('chatInitialized', false);
        return;
    }

    // 1. 載入 sessions（同時渲染側邊欄並取得資料，不重複 fetch）
    let sessions = (await loadSessions({ shared: true })) || [];

    // 跨平台共用「當前對話」：伺服器端的 current_session_id（可能由 Telegram 或
    // 他處更新）與本機上次開啟的對話。下面的清理不能動它們，還原也看它們。
    let serverCurrent = null;
    let localLast = null;
    if (sessions.length > 0) {
        try {
            const r = await AppAPI.get('/api/chat/current-session');
            serverCurrent = r && r.session_id;
        } catch (_) {}
        try { localLast = localStorage.getItem('chat_last_session_id'); } catch (_) {}
    }

    // Auto-cleanup：清掉多餘的空「New Chat」（保留最新一個）。只看標題不夠——
    // 後端只在第一則「使用者」訊息才換掉預設標題，有內容的對話也可能叫
    // 'New Chat'，刪了就是整段歷史不見。所以只清確定沒訊息的（舊後端沒回
    // has_messages 就不刪），目前對話／上次開啟的對話一律不動。
    if (sessions.length > 0) {
        const keepIds = new Set(
            [serverCurrent, localLast, AppStore.get('currentSessionId'), window.currentSessionId].filter(Boolean)
        );
        const cleanupPromises = [];
        let keptNewest = false;

        // sessions is sorted by updated_at DESC (newest first)
        for (const s of sessions) {
            // has_messages 缺席／null（舊後端）一律當成有訊息——寧可留著也不能刪錯
            if (s.title !== 'New Chat' || s.has_messages !== false) continue;
            if (!keptNewest) {
                keptNewest = true;
                continue;
            }
            if (keepIds.has(s.id)) continue;
            cleanupPromises.push(AppAPI.delete(`/api/chat/sessions/${encodeURIComponent(s.id)}`));
        }

        if (cleanupPromises.length > 0) {
            console.log(`Cleaning up ${cleanupPromises.length} empty sessions...`);
            await Promise.allSettled(cleanupPromises);
            // 清理後重新整理側邊欄（合併原本的兩次 fetch+loadSessions 為一次）
            sessions = (await loadSessions()) || [];
        }
    }

    if (sessions.length > 0) {
        // 跨頁「新對話」旗標（site-sidebar 論壇/子頁）：本次造訪直接進歡迎畫面，
        // 不還原任何舊對話（伺服器指標與 localStorage 都不看）。用後即棄。
        let forceNewChat = false;
        try {
            if (sessionStorage.getItem('cmStartNewChat') === '1') {
                sessionStorage.removeItem('cmStartNewChat');
                forceNewChat = true;
            }
        } catch (_) {}

        // 優先伺服器端指標 → 達成 Web 跟隨；取不到才退回本機 localStorage。
        let savedId = null;
        if (!forceNewChat) {
            savedId = serverCurrent || localLast || AppStore.get('currentSessionId');
        }
        const matchedSession = savedId && sessions.find((s) => s.id === savedId);
        if (matchedSession) {
            console.log('initChat: restoring last session', savedId);
            window.currentSessionId = matchedSession.id;
            AppStore.set('currentSessionId', matchedSession.id);
            updateSessionActiveState(matchedSession.id);
            await loadChatHistory(matchedSession.id);
        } else {
            // 沒有上次記錄，顯示歡迎畫面讓用戶自行選擇
            window.currentSessionId = null;
            showWelcomeScreen();
        }
    } else {
        // 沒有 session，設定為 null (Lazy Creation)
        window.currentSessionId = null;
        console.log('initChat: no existing sessions, showing welcome screen');
        // 不需要創建新的 session，只顯示歡迎畫面
        showWelcomeScreen();
    }

    // 3. 顯示歡迎畫面（如果載入了歷史，loadChatHistory 會覆蓋它）
    // 如果沒有載入歷史 (currentSessionId is null), showWelcomeScreen 已被呼叫
}

// 重置初始化狀態（登出時調用）
function resetChatInit() {
    window.chatInitialized = false;
    AppStore.set('chatInitialized', false);
    window.currentSessionId = null;
    AppStore.set('currentSessionId', null);
}
window.resetChatInit = resetChatInit;

// 暴露到全域供其他模組使用
window.initChat = initChat;

// 不再自動執行 initChat，由 switchTab('chat') 觸發
// 反饋提交
async function submitFeedback(codebookId, score, btn) {
    if (!codebookId) return;

    // Disable buttons to prevent spam
    const parent = btn.parentElement;
    const buttons = parent.querySelectorAll('button');
    buttons.forEach((b) => (b.disabled = true));

    try {
        await AppAPI.post('/api/chat/feedback', {
            codebook_entry_id: codebookId,
            score: score,
        });

        // UI Feedback
        if (score > 0) {
            btn.innerHTML =
                '<i data-lucide="check-circle" class="w-3.5 h-3.5 text-success fill-success/20"></i>';
            btn.classList.add('text-success');
        } else {
            btn.innerHTML =
                '<i data-lucide="x-circle" class="w-3.5 h-3.5 text-danger fill-danger/20"></i>';
            btn.classList.add('text-danger');
        }
        createIconsIn(btn);
    } catch (e) {
        console.error('Feedback failed:', e);
        // Re-enable on error
        buttons.forEach((b) => (b.disabled = false));
        if (typeof showToast === 'function') showToast(window.I18n ? window.I18n.t('common.feedbackFailed') : 'Feedback submission failed, please try again later', 'error');
    }
};
window.submitFeedback = submitFeedback;

export { initChat, resetChatInit, submitFeedback };
