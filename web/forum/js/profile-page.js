// forum/profile.html 的頁面腳本（2026-09-25 自 inline <script> 原樣移出：正式站 CSP
// script-src 不再放行 'unsafe-inline'）。classic script——頂層宣告維持全域，
// 執行時機與原 inline 相同（解析到該 <script> 時同步執行）。

// ========================================
// Profile Page Logic
// ========================================

let profileData = null;
let currentUserId = null;

// Confirm modal
function showConfirm(options) {
    return new Promise((resolve) => {
        const modal = document.getElementById('confirm-modal');
        const title = document.getElementById('confirm-modal-title');
        const message = document.getElementById('confirm-modal-message');
        const icon = document.getElementById('confirm-modal-icon');
        const okBtn = document.getElementById('confirm-ok-btn');
        const cancelBtn = document.getElementById('confirm-cancel-btn');

        title.textContent = options.title || (window.I18n ? window.I18n.t('common.confirm') : 'Confirm');
        message.textContent = options.message || (window.I18n ? window.I18n.t('common.confirmQuestion') : 'Are you sure?');

        const typeColors = {
            danger: { bg: 'bg-danger/20', text: 'text-danger', btn: 'bg-danger text-background' },
            warning: { bg: 'bg-yellow-500/20', text: 'text-yellow-400', btn: 'bg-yellow-500 text-black' },
            info: { bg: 'bg-primary/20', text: 'text-primary', btn: 'bg-primary text-background' },
            success: { bg: 'bg-success/20', text: 'text-success', btn: 'bg-success text-background' }
        };
        const colors = typeColors[options.type] || typeColors.info;

        icon.className = `w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-4 ${colors.bg}`;
        icon.innerHTML = `<i data-lucide="alert-triangle" class="w-8 h-8 ${colors.text}"></i>`;

        okBtn.textContent = options.confirmText || (window.I18n ? window.I18n.t('common.confirm') : 'Confirm');
        okBtn.className = `flex-1 py-3 rounded-xl font-bold transition ${colors.btn}`;
        cancelBtn.textContent = options.cancelText || (window.I18n ? window.I18n.t('common.cancel') : 'Cancel');

        modal.classList.remove('hidden');
        lucide.createIcons({ icons: lucide.icons, nameAttr: 'data-lucide' });

        const cleanup = () => {
            modal.classList.add('hidden');
            okBtn.onclick = null;
            cancelBtn.onclick = null;
        };

        okBtn.onclick = () => { cleanup(); resolve(true); };
        cancelBtn.onclick = () => { cleanup(); resolve(false); };
    });
}

// Normalize server UTC timestamp for JS parsing
function parseUTCDate(dateString) {
    if (!dateString) return null;
    let s = dateString;
    if (typeof s === 'string' && !s.endsWith('Z') && !s.includes('+') && !/\d{2}:\d{2}:\d{2}-/.test(s)) {
        s = s.replace(' ', 'T') + 'Z';
    }
    return new Date(s);
}

// Format date
function formatDate(dateString) {
    if (!dateString) return '-';
    const date = parseUTCDate(dateString);
    return date.toLocaleDateString('zh-TW', { year: 'numeric', month: 'short' });
}

function formatTimeAgo(dateString) {
    if (!dateString) return '';
    const date = parseUTCDate(dateString);
    const now = new Date();
    const diff = now - date;

    if (diff < 60000) return window.I18n ? window.I18n.t('profile.justNow') : 'Just now';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}${window.I18n ? window.I18n.t('profile.minutesAgo') : 'm ago'}`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}${window.I18n ? window.I18n.t('profile.hoursAgo') : 'h ago'}`;
    if (diff < 604800000) return `${Math.floor(diff / 86400000)}${window.I18n ? window.I18n.t('profile.daysAgo') : 'd ago'}`;
    return date.toLocaleDateString('zh-TW');
}

function escapeHTML(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// Load profile
async function loadProfile() {
    const params = new URLSearchParams(window.location.search);
    const targetUserId = params.get('id');

    if (!targetUserId) {
        document.getElementById('user-posts').innerHTML = `
            <div class="p-6 text-center text-danger">
                <i data-lucide="alert-circle" class="w-8 h-8 mx-auto mb-2"></i>
                <p><span data-i18n="forum.profile.invalidId">Invalid user ID</span></p>
            </div>
        `;
        lucide.createIcons();
        return;
    }

    // Get current user
    if (typeof AuthManager !== 'undefined') {
        await AuthManager.init();
        currentUserId = AuthManager.currentUser?.user_id || AuthManager.currentUser?.uid;
    }

    try {
        const result = await FriendsAPI.getProfile(targetUserId);
        if (!result.success) throw new Error('User not found');

        profileData = result.profile;
        renderProfile();
        loadUserPosts(targetUserId);

    } catch (error) {
        console.error('Load profile error:', error);
        document.getElementById('public-profile-username').innerHTML = `
            <span class="text-danger"><span data-i18n="forum.profile.notFound">User not found</span></span>
        `;
        document.getElementById('user-posts').innerHTML = `
            <div class="p-6 text-center text-textMuted">
                <i data-lucide="user-x" class="w-8 h-8 mx-auto mb-2 text-danger"></i>
                <p><span data-i18n="forum.profile.loadFailed">Unable to load user profile</span></p>
            </div>
        `;
        lucide.createIcons();
    }
}

// Render profile
function renderProfile() {
    const p = profileData;
    const isOwn = currentUserId && currentUserId === p.user_id;

    // Avatar
    const shownName = p.display_name || p.username || p.user_id; // 暱稱優先
    // 不能用 #profile-avatar：auth.js 會把「登入者自己」的頭像字母寫進那個 id。
    // 彩色頭像走 avatar.js 掛的 window.Avatar（module 還沒好就退回原本的首字＋主色）
    const avatarEl = document.getElementById('public-profile-avatar');
    const Avatar = window.Avatar;
    avatarEl.textContent = Avatar ? Avatar.initial(shownName) : (shownName || 'U')[0].toUpperCase();
    avatarEl.classList.add(...(Avatar ? [Avatar.colorClass(p.user_id)] : ['bg-primary/20', 'text-primary']));

    // Username（不能用 #profile-username：auth.js 會把「登入者自己」的名字寫進那個 id，
    // 打開好友個人頁會先閃自己的名字、token 續登後還可能蓋回來）
    const nameEl = document.getElementById('public-profile-username');
    nameEl.textContent = shownName;
    if (p.display_name && p.username) {
        // 有暱稱時附帳號名：暱稱可以改，帳號名才是不會撞的身分
        const handle = document.createElement('span');
        handle.className = 'ml-2 text-sm font-normal text-textMuted';
        handle.textContent = '@' + p.username;
        nameEl.appendChild(handle);
    }

    // Badges
    let badges = '';
    if (['premium', 'pro', 'plus'].includes((p.membership_tier || 'free').toLowerCase())) {
        badges += '<span class="px-2 py-1 text-xs font-bold bg-gradient-to-r from-yellow-500 to-orange-500 text-black rounded-lg"><span data-i18n="forum.c.premium">PREMIUM</span></span>';
    }
    if (p.is_friend) {
        badges += '<span class="px-2 py-1 text-xs font-bold bg-success/20 text-success rounded-lg flex items-center gap-1"><i data-lucide="user-check" class="w-3 h-3"></i> <span data-i18n="profile.friendBadge">Friend</span></span>';
    }
    document.getElementById('profile-badges').innerHTML = badges;

    // Meta
    document.getElementById('profile-meta').innerHTML = `
        <span data-i18n="profile.memberSince">Member since</span> ${formatDate(p.member_since)}
    `;

    // Stats
    document.getElementById('stat-posts').textContent = p.post_count || 0;
    document.getElementById('stat-pushes').textContent = p.total_pushes || 0;
    document.getElementById('stat-member').textContent = formatDate(p.member_since);

    // Friends count
    document.getElementById('stat-friends').textContent = p.friends_count || 0;

    // Actions
    if (isOwn) {
        document.getElementById('profile-actions').innerHTML = `
            <a href="/static/forum/dashboard.html" class="bg-surfaceHighlight hover:bg-surfaceHighlight text-textMuted px-4 py-2 rounded-full text-sm font-bold transition flex items-center gap-2">
                <i data-lucide="settings" class="w-4 h-4"></i>
                <span data-i18n="forum.dash.myDashboard">Dashboard</span>
            </a>
        `;
        document.getElementById('more-actions').classList.add('hidden');
    } else {
        renderFriendButton();
        document.getElementById('more-actions').classList.remove('hidden');
        document.getElementById('block-btn').onclick = () => handleBlock();
    }

    lucide.createIcons();
    // 上面動態插入的 data-i18n 節點要再翻一次（i18n init 早於資料載入）
    if (window.I18n && window.I18n.updatePageContent) window.I18n.updatePageContent();
}

// Render friend button
function renderFriendButton() {
    const p = profileData;
    let buttonHtml = '';

    if (p.friend_status === 'accepted') {
        buttonHtml = `
            <div class="flex gap-2">
                <a href="/?chat=${encodeURIComponent(p.user_id)}#friends"
                   class="bg-primary/10 hover:bg-primary/20 text-primary px-4 py-2 rounded-full text-sm font-bold flex items-center gap-2 transition">
                    <i data-lucide="message-circle" class="w-4 h-4"></i>
                    <span><span data-i18n="forum.profile.message">Message</span></span>
                </a>
                <button data-click="handleRemoveFriend"
                        class="bg-success/10 text-success px-4 py-2 rounded-full text-sm font-bold flex items-center gap-2 hover:bg-danger/10 hover:text-danger transition group">
                    <i data-lucide="user-check" class="w-4 h-4 group-hover:hidden"></i>
                    <i data-lucide="user-minus" class="w-4 h-4 hidden group-hover:block"></i>
                    <span class="group-hover:hidden"><span data-i18n="forum.profile.friends">Friends</span></span>
                    <span class="hidden group-hover:inline" data-i18n="profile.remove">Remove</span>
                </button>
            </div>
        `;
    } else if (p.friend_status === 'pending') {
        if (p.is_requester) {
            buttonHtml = `
                <button data-click="handleCancelRequest"
                        class="bg-surfaceHighlight text-textMuted px-4 py-2 rounded-full text-sm font-bold flex items-center gap-2 hover:bg-danger/10 hover:text-danger transition">
                    <i data-lucide="clock" class="w-4 h-4"></i>
                    <span><span data-i18n="forum.profile.pending">Pending</span></span>
                </button>
            `;
        } else {
            buttonHtml = `
                <div class="flex gap-2">
                    <button data-click="handleAcceptRequest"
                            class="bg-success/10 hover:bg-success/20 text-success px-4 py-2 rounded-full text-sm font-bold transition flex items-center gap-2">
                        <i data-lucide="check" class="w-4 h-4"></i> <span data-i18n="profile.accept">Accept</span>
                    </button>
                    <button data-click="handleRejectRequest"
                            class="bg-danger/10 hover:bg-danger/20 text-danger px-4 py-2 rounded-full text-sm font-bold transition flex items-center gap-2">
                        <i data-lucide="x" class="w-4 h-4"></i> <span data-i18n="profile.reject">Reject</span>
                    </button>
                </div>
            `;
        }
    } else if (p.friend_status === 'blocked' && !p.blocked_by_me) {
        // 被對方封鎖：只顯示狀態，不給解除（按了只會 400）；自己仍可以封鎖對方
        buttonHtml = `
            <span class="bg-surfaceHighlight text-textMuted px-4 py-2 rounded-full text-sm font-bold flex items-center gap-2 cursor-default">
                <i data-lucide="user-x" class="w-4 h-4"></i>
                <span><span data-i18n="forum.profile.unavailable">Unavailable</span></span>
            </span>
        `;
    } else if (p.friend_status === 'blocked') {
        buttonHtml = `
            <button data-click="handleUnblock"
                    class="bg-danger/10 text-danger px-4 py-2 rounded-full text-sm font-bold flex items-center gap-2 hover:bg-danger/20 transition">
                <i data-lucide="ban" class="w-4 h-4"></i>
                <span><span data-i18n="forum.profile.blocked">Blocked</span></span>
            </button>
        `;
        document.getElementById('block-btn').innerHTML = `
            <i data-lucide="unlock" class="w-5 h-5"></i>
            <span><span data-i18n="forum.profile.unblock">Unblock User</span></span>
        `;
        document.getElementById('block-btn').onclick = () => handleUnblock();
    } else {
        buttonHtml = `
            <button data-click="handleAddFriend"
                    class="bg-primary/10 hover:bg-primary/20 text-primary px-4 py-2 rounded-full text-sm font-bold flex items-center gap-2 transition">
                <i data-lucide="user-plus" class="w-4 h-4"></i>
                <span><span data-i18n="forum.profile.addFriend">Add Friend</span></span>
            </button>
        `;
    }

    document.getElementById('profile-actions').innerHTML = buttonHtml;
    lucide.createIcons();
    if (window.I18n && window.I18n.updatePageContent) window.I18n.updatePageContent();
}

// 看板分類顯示名（與 forum-app.js 的 _categoryLabel 同一組 key：forum.categoryXxx）
function categoryLabel(category) {
    const key = String(category || '').toLowerCase();
    if (!key) return '';
    const i18nKey = `forum.category${key.charAt(0).toUpperCase()}${key.slice(1)}`;
    const text = window.I18n ? window.I18n.t(i18nKey) : i18nKey;
    return text && text !== i18nKey ? text : key;
}

// Load user posts
async function loadUserPosts(userId) {
    try {
        const res = await fetch(`/api/forum/me/user-posts/${encodeURIComponent(userId)}?limit=10`);
        const data = await res.json();

        const container = document.getElementById('user-posts');

        if (!data.posts || data.posts.length === 0) {
            container.innerHTML = `
                <div class="p-6 text-center text-textMuted">
                    <i data-lucide="file-x" class="w-8 h-8 mx-auto mb-2 opacity-50"></i>
                    <p><span data-i18n="forum.profile.noPosts">No posts yet</span></p>
                </div>
            `;
            lucide.createIcons();
            if (window.I18n && window.I18n.updatePageContent) window.I18n.updatePageContent();
            return;
        }

        container.innerHTML = data.posts.map(post => `
            <a href="/static/forum/post.html?id=${encodeURIComponent(post.id)}" class="block px-6 py-4 hover:bg-surfaceHighlight transition">
                <div class="flex items-start justify-between gap-4">
                    <div class="min-w-0 flex-1">
                        <div class="text-xs text-primary font-bold uppercase tracking-wider mb-1">${escapeHTML(categoryLabel(post.category))}</div>
                        <h3 class="font-bold text-textMain truncate">${escapeHTML(post.title)}</h3>
                        <div class="text-xs text-textMuted mt-1">${formatTimeAgo(post.created_at)}</div>
                    </div>
                    <div class="flex items-center gap-3 text-xs text-textMuted flex-shrink-0">
                        <span class="flex items-center gap-1">
                            <i data-lucide="thumbs-up" class="w-3 h-3"></i>
                            ${Math.max(0, post.push_count || 0)}
                        </span>
                        <span class="flex items-center gap-1">
                            <i data-lucide="message-square" class="w-3 h-3"></i>
                            ${post.comment_count || 0}
                        </span>
                    </div>
                </div>
            </a>
        `).join('');

        lucide.createIcons();

    } catch (error) {
        console.error('Load posts error:', error);
        document.getElementById('user-posts').innerHTML = `
            <div class="p-6 text-center text-textMuted">
                <p><span data-i18n="forum.profile.postsLoadFailed">Unable to load posts</span></p>
            </div>
        `;
        if (window.I18n && window.I18n.updatePageContent) window.I18n.updatePageContent();
    }
}

// 好友操作失敗的訊息：後端 detail 是英文（'You are already friends'）、網路層是 'Failed to fetch'，
// 不能直接 toast——過 userFacingMessage（window.ErrorMessage 由 error-message.js 註冊；forum-profile.js 載的 friends.js 會帶進來）。
function friendActionError(error, fallbackKey) {
    if (window.ErrorMessage) return window.ErrorMessage.userFacingMessage(error, { fallbackKey });
    return window.I18n ? window.I18n.t(fallbackKey) : 'Something went wrong. Please try again.';
}

// Friend action handlers
async function handleAddFriend() {
    try {
        await FriendsAPI.sendRequest(profileData.user_id);
        window.showToast(window.I18n ? window.I18n.t('profile.friendRequestSent') : 'Friend request sent', 'success');
        profileData.friend_status = 'pending';
        profileData.is_requester = true;
        renderFriendButton();
    } catch (error) {
        window.showToast(friendActionError(error, 'friends.sendRequestFailed'), 'error');
    }
}

async function handleAcceptRequest() {
    try {
        await FriendsAPI.acceptRequest(profileData.user_id);
        window.showToast(window.I18n ? window.I18n.t('profile.friendsNow') : 'You are now friends', 'success');
        profileData.friend_status = 'accepted';
        profileData.is_friend = true;
        renderProfile();
    } catch (error) {
        window.showToast(friendActionError(error, 'friends.acceptFailed'), 'error');
    }
}

async function handleRejectRequest() {
    try {
        await FriendsAPI.rejectRequest(profileData.user_id);
        window.showToast(window.I18n ? window.I18n.t('profile.requestRejected') : 'Request rejected', 'info');
        profileData.friend_status = null;
        renderFriendButton();
    } catch (error) {
        window.showToast(friendActionError(error, 'friends.rejectFailed'), 'error');
    }
}

async function handleCancelRequest() {
    try {
        await FriendsAPI.cancelRequest(profileData.user_id);
        window.showToast(window.I18n ? window.I18n.t('profile.requestCancelled') : 'Request cancelled', 'info');
        profileData.friend_status = null;
        renderFriendButton();
    } catch (error) {
        window.showToast(friendActionError(error, 'friends.cancelFailed'), 'error');
    }
}

async function handleRemoveFriend() {
    const confirmed = await showConfirm({
        title: window.I18n ? window.I18n.t('profile.confirmRemoveTitle') : 'Remove Friend',
        message: window.I18n ? window.I18n.t('profile.confirmRemoveMsg') : 'Are you sure you want to remove this friend?',
        type: 'warning',
        confirmText: window.I18n ? window.I18n.t('profile.remove') : 'Remove',
        cancelText: window.I18n ? window.I18n.t('common.cancel') : 'Cancel'
    });

    if (!confirmed) return;

    try {
        await FriendsAPI.removeFriend(profileData.user_id);
        window.showToast(window.I18n ? window.I18n.t('profile.friendRemoved') : 'Friend removed', 'info');
        profileData.friend_status = null;
        profileData.is_friend = false;
        renderProfile();
    } catch (error) {
        window.showToast(friendActionError(error, 'friends.removeFriendFailed'), 'error');
    }
}

async function handleBlock() {
    const confirmed = await showConfirm({
        title: window.I18n ? window.I18n.t('profile.confirmBlockTitle') : 'Block User',
        message: window.I18n ? window.I18n.t('profile.confirmBlockMsg') : 'This user will not be able to send you friend requests. Are you sure?',
        type: 'danger',
        confirmText: window.I18n ? window.I18n.t('profile.block') : 'Block',
        cancelText: window.I18n ? window.I18n.t('common.cancel') : 'Cancel'
    });

    if (!confirmed) return;

    try {
        await FriendsAPI.blockUser(profileData.user_id);
        window.showToast(window.I18n ? window.I18n.t('profile.userBlocked') : 'User blocked', 'info');
        profileData.friend_status = 'blocked';
        profileData.blocked_by_me = true;
        profileData.is_friend = false;
        renderProfile();
    } catch (error) {
        window.showToast(friendActionError(error, 'friends.blockFailed'), 'error');
    }
}

async function handleUnblock() {
    try {
        const result = await FriendsAPI.unblockUser(profileData.user_id);
        // 封鎖前是好友：解除後恢復好友（LINE 式，c062）；對方也封鎖了我的話仍是封鎖狀態
        const restored = !!result?.friendship_restored;
        const key = restored ? 'profile.unblockedFriendRestored' : 'profile.userUnblocked';
        window.showToast(window.I18n ? window.I18n.t(key) : 'User unblocked', 'success');
        const status = await FriendsAPI.getStatus(profileData.user_id).catch(() => null);
        profileData.friend_status = status ? status.status : restored ? 'accepted' : null;
        profileData.is_friend = profileData.friend_status === 'accepted';
        profileData.blocked_by_me = !!status?.blocked_by_me;
        renderProfile();
    } catch (error) {
        window.showToast(friendActionError(error, 'friends.unblockFailed'), 'error');
    }
}

// 統一返回主站 forum 分頁，避免出現多個 forum 首頁入口
function handleProfileBack() {
    if (typeof smoothNavigate === 'function') {
        smoothNavigate('/static/index.html#forum');
    } else {
        window.location.href = '/static/index.html#forum';
    }
}

// Init
document.addEventListener('DOMContentLoaded', async () => {
    // profile 頁不載 forum-app.js，自行觸發 i18n 初始化（否則整頁停在英文 fallback、toast 顯示原始 key）
    if (window.I18n) {
        try { await window.I18n.init(); } catch (e) { console.error('[Profile] i18n Init Error:', e); }
    }
    if (typeof AuthManager !== 'undefined') {
        await AuthManager.init();
    }
    lucide.createIcons();
    loadProfile();
});
