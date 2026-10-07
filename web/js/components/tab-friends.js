// Auto-generated from components.js split
// Tab: Friends (Integrated Social Hub - Friends + Messages) - original lines 549-731
window.Components = window.Components || {};
window.Components.friends = `
    <div class="h-full flex flex-col">
            <!--Header with Tab Switcher-->
            <div data-social-top class="flex items-center justify-between pl-4 pr-4 md:pr-16 py-3 border-b border-borderSubtle bg-surface/50">
                <h2 class="font-serif text-2xl text-secondary" data-i18n="friends.title"></h2>
                <div class="flex items-center gap-2">
                    <button data-click="SocialHub.refresh" data-click-element aria-label="Refresh" data-i18n="common.refresh" data-i18n-attr="aria-label" class="${ICON_ACTION_BUTTON_CLASS}">
                        <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                    </button>
                </div>
            </div>

            <!--Sub-tab Navigation-->
            <div data-social-top class="flex gap-1 p-2 bg-background/50 border-b border-borderSubtle">
                <button data-click="SocialHub.switchSubTab" data-click-arg="messages" id="social-tab-messages"
                    class="social-sub-tab flex-1 py-2.5 px-4 rounded-lg font-bold text-sm transition flex items-center justify-center gap-2 bg-primary text-background">
                    <i data-lucide="message-circle" class="w-4 h-4"></i>
                    <span data-i18n="friends.messages"></span>
                    <span id="messages-unread-badge" class="hidden px-1.5 py-0.5 text-xs bg-danger text-background rounded-full">0</span>
                </button>
                <button data-click="SocialHub.switchSubTab" data-click-arg="friends" id="social-tab-friends"
                    class="social-sub-tab flex-1 py-2.5 px-4 rounded-lg font-bold text-sm transition flex items-center justify-center gap-2 text-textMuted hover:text-textMain hover:bg-surfaceHighlight">
                    <i data-lucide="users" class="w-4 h-4"></i>
                    <span data-i18n="friends.friends"></span>
                    <span id="friends-request-badge" class="hidden px-1.5 py-0.5 text-xs bg-danger text-background rounded-full">0</span>
                </button>
            </div>

            <!-- ==================== MESSAGES SUB - TAB ==================== -->
            <!-- 1024 以上「對話清單＋聊天」並排；以下一次一欄，data-pane 由 friends.js 標、styles.css 決定顯示哪欄 -->
            <div id="social-content-messages" data-dm-panes data-pane="list" class="flex-1 flex overflow-hidden">
                <!-- Conversation List (Left)：288px（Teams／LINE 約 300）。以前 lg 起 384px，
                     1280 寬筆電上加整站側欄 288px，兩條列表佔掉一半以上、聊天區剩 608px -->
                <div id="social-conv-sidebar" data-dm-list class="w-full lg:w-72 shrink-0 border-r border-borderSubtle flex flex-col bg-surface/30">
                    <!-- data-shell-scroll:#friends-tab 是 overflow-hidden,吃不到
                         main .tab-content.overflow-y-auto 的底部留白規則,內層捲動區
                         得自己跟 ui-shell 要,否則捲到底時最後幾列躲在導覽列後面(實測 47px)。 -->
                    <!-- 搜尋：人、群組、訊息（social-search.js）；打字後結果蓋在下面列表的位置，分類列收起來 -->
                    <div class="flex items-center gap-1.5 px-3 pt-3 pb-2 border-b border-borderSubtle">
                        <div class="relative flex-1 min-w-0">
                            <i data-lucide="search" class="w-4 h-4 text-textMuted absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"></i>
                            <input id="social-search" type="search" enterkeyhint="search" autocomplete="off" maxlength="50"
                                data-i18n="friends.search.placeholder" data-i18n-attr="placeholder" placeholder="Search people, groups, messages"
                                class="w-full h-9 pl-9 pr-9 rounded-full bg-surfaceHighlight border border-transparent text-sm text-textMain placeholder:text-textMuted focus:outline-none focus:border-primary/40 focus:bg-surface transition [&::-webkit-search-cancel-button]:hidden">
                            <button type="button" id="social-search-clear" data-i18n="friends.search.clear" data-i18n-attr="aria-label" aria-label="Clear search"
                                class="hidden absolute right-1 top-1/2 -translate-y-1/2 w-7 h-7 rounded-full flex items-center justify-center text-textMuted hover:text-textMain hover:bg-surface transition">
                                <i data-lucide="x" class="w-4 h-4"></i>
                            </button>
                        </div>
                        <!-- 排序（social-pins.js openSortMenu）：依最新訊息／自訂順序／調整順序；一直看得到（DANNY：找不到排序模式） -->
                        <button type="button" id="social-reorder-btn" data-click="SocialHub.openSortMenu" data-click-element data-i18n="friends.order.menu" data-i18n-attr="aria-label" aria-label="Sort"
                            class="w-9 h-9 shrink-0 rounded-full flex items-center justify-center text-textMuted hover:text-textMain hover:bg-surfaceHighlight transition">
                            <i data-lucide="arrow-up-down" class="w-4 h-4"></i>
                        </button>
                    </div>
                    <!-- 分類＋建立群組（social-groups.js：開關 group_chat_enabled 關著時 GET /api/groups 404，整條藏起來——
                         只有私訊時不需要分類）。以前「建立群組」自己佔一整列，跟分類併成一列省高度 -->
                    <div id="social-group-bar" class="hidden flex items-center gap-2 px-3 py-2 border-b border-borderSubtle">
                        <!-- 分段控制（2026-10-01 DANNY：三顆寬度不一的膠囊靠左擠在一起看起來歪）：三格等寬、選中那格浮起來 -->
                        <div id="social-conv-filter" role="tablist" data-i18n="friends.filter.label" data-i18n-attr="aria-label" aria-label="Filter conversations" class="flex-1 min-w-0 grid grid-cols-3 gap-0.5 p-0.5 rounded-full bg-surfaceHighlight">
                            <button type="button" role="tab" data-conv-filter="all" data-click="SocialHub.setConvFilter" data-click-arg="all" class="social-conv-filter-btn h-9 min-w-0 px-2 rounded-full text-xs font-medium transition flex items-center justify-center gap-1 whitespace-nowrap"><span class="truncate" data-i18n="friends.filter.all">All</span></button>
                            <button type="button" role="tab" data-conv-filter="dm" data-click="SocialHub.setConvFilter" data-click-arg="dm" class="social-conv-filter-btn h-9 min-w-0 px-2 rounded-full text-xs font-medium transition flex items-center justify-center gap-1 whitespace-nowrap"><span class="truncate" data-i18n="friends.filter.dm">Chats</span><span data-filter-unread class="hidden inline-flex min-w-[16px] h-4 px-1 rounded-full bg-primary text-background text-[10px] font-bold leading-none items-center justify-center shrink-0"></span></button>
                            <button type="button" role="tab" data-conv-filter="group" data-click="SocialHub.setConvFilter" data-click-arg="group" class="social-conv-filter-btn h-9 min-w-0 px-2 rounded-full text-xs font-medium transition flex items-center justify-center gap-1 whitespace-nowrap"><span class="truncate" data-i18n="friends.filter.group">Groups</span><span data-filter-unread class="hidden inline-flex min-w-[16px] h-4 px-1 rounded-full bg-primary text-background text-[10px] font-bold leading-none items-center justify-center shrink-0"></span></button>
                        </div>
                        <button type="button" data-click="SocialHub.openCreateGroup" data-i18n="groups.create" data-i18n-attr="aria-label" aria-label="Create group" class="w-9 h-9 shrink-0 rounded-full flex items-center justify-center text-primary hover:bg-primary/10 transition">
                            <i data-lucide="user-plus" class="w-4 h-4"></i>
                        </button>
                    </div>
                    <div id="social-conv-list" data-shell-scroll class="flex-1 overflow-y-auto">
                        <div class="flex items-center justify-center h-full">
                            <div class="animate-spin w-6 h-6 border-2 border-primary border-t-transparent rounded-full"></div>
                        </div>
                    </div>
                </div>

                <!-- Chat Section (Desktop: inline, Mobile: hidden) -->
                <!-- min-w-0：flex-1 預設 min-width:auto，訊息裡一長串不斷行的內容（網址、錢包地址、程式碼、寬表格）會把整個聊天欄撐出手機畫面 -->
                <div id="social-chat-section" data-dm-chat class="hidden lg:flex flex-1 min-w-0 flex-col bg-background relative h-full">
                    <!-- Empty State (shown when no conversation selected) -->
                    <div id="social-chat-empty" class="flex-1 flex items-center justify-center">
                        <div class="text-center text-textMuted opacity-60">
                            <i data-lucide="message-circle" class="w-12 h-12 mx-auto mb-4"></i>
                            <p class="text-lg font-medium mb-2" data-i18n="friends.selectConversation">Select a conversation</p>
                            <p class="text-sm" data-i18n="friends.clickToStart">Click a conversation on the left to start chatting</p>
                        </div>
                    </div>

                    <!-- Chat Content (hidden until conversation selected) -->
                    <div id="social-chat-content" class="hidden flex-1 min-w-0 flex flex-col h-full">
                        <!-- Chat Header -->
                        <div id="social-chat-header" class="p-3 border-b border-borderSubtle flex items-center gap-3 bg-surface/50">
                            <button type="button" data-dm-back data-click="socialBackToList" aria-label="Back to conversations" data-i18n="messages.backToList" data-i18n-attr="aria-label" class="lg:hidden -ml-1 w-9 h-9 shrink-0 rounded-full flex items-center justify-center text-textMuted hover:text-textMain hover:bg-surfaceHighlight transition"><i data-lucide="arrow-left" class="w-5 h-5"></i></button>
                            <!-- 並排時收合左邊對話清單、放大聊天區（1024 以下有返回鈕，這顆藏起來，見 styles.css） -->
                            <button type="button" data-dm-list-toggle data-click="SocialHub.toggleConvList" aria-label="Collapse or expand conversation list" data-i18n="messages.toggleConvList" data-i18n-attr="aria-label" class="-ml-1 w-9 h-9 shrink-0 rounded-full flex items-center justify-center text-textMuted hover:text-textMain hover:bg-surfaceHighlight transition"><i data-lucide="panel-left-close" class="dm-list-icon-collapse w-5 h-5"></i><i data-lucide="panel-left-open" class="dm-list-icon-expand w-5 h-5"></i></button>
                            <a id="social-chat-profile-link" href="#" class="flex items-center gap-3 flex-1 min-w-0 hover:opacity-80 transition">
                                <div id="social-chat-avatar" class="w-9 h-9 rounded-full flex items-center justify-center font-bold flex-shrink-0">U</div>
                                <span id="social-chat-username" class="font-bold text-textMain truncate">Username</span>
                            </a>
                            <!-- 群組資訊（成員、通知、邀請、退群）；私訊時藏起來 -->
                            <button type="button" id="social-ai-btn" data-click="SocialHub.openAssistant" aria-label="AI assistant" data-i18n="assistant.button" data-i18n-attr="aria-label" class="hidden w-9 h-9 shrink-0 rounded-full flex items-center justify-center text-textMuted hover:text-primary hover:bg-surfaceHighlight transition"><i data-lucide="sparkles" class="w-5 h-5"></i></button>
                            <button type="button" id="social-group-info-btn" data-click="SocialHub.openGroupInfo" aria-label="Group info" data-i18n="groups.info" data-i18n-attr="aria-label" class="hidden w-9 h-9 shrink-0 rounded-full flex items-center justify-center text-textMuted hover:text-textMain hover:bg-surfaceHighlight transition"><i data-lucide="info" class="w-5 h-5"></i></button>
                        </div>

                        <!-- Messages Container -->
                        <div id="social-messages-container" class="flex-1 overflow-y-auto px-4 py-3 messages-scroll">
                            <!-- Messages will be rendered here -->
                        </div>

                        <!-- Message Input -->
                        <div id="social-msg-input-container" class="relative p-3 border-t border-borderSubtle bg-surface/30">
                            <form id="social-msg-form" data-submit="socialSendMessage" class="w-full">
                                <div class="flex items-end gap-2">
                                    <div class="flex-1 bg-background border border-borderLight rounded-3xl px-4 py-1.5 focus-within:border-primary/50 transition">
                                        <textarea id="social-msg-input"
                                            class="w-full bg-transparent text-textMain placeholder-textMuted resize-none leading-6 py-1 outline-none text-sm"
                                            placeholder="Type a message..." data-i18n="friends.typeMessage" data-i18n-attr="placeholder" rows="1" maxlength="500"
                                            data-keydown="socialInputKeydown"
                                            data-input-action="socialAutoResize"></textarea>
                                    </div>
                                    <button type="submit" id="social-send-btn" aria-label="Send" data-i18n="messages.sendMessage" data-i18n-attr="aria-label"
                                        class="w-10 h-10 mb-0.5 shrink-0 rounded-full flex items-center justify-center bg-primary hover:brightness-110 text-background transition disabled:opacity-50"
                                        disabled>
                                        <i data-lucide="send" class="w-[18px] h-[18px]"></i>
                                    </button>
                                </div>
                                <div class="flex items-center justify-between px-3">
                                    <span id="social-quota" class="hidden mt-1 text-[11px] text-textMuted"></span>
                                    <!-- 群組：Pro 到期留在群裡唯讀 -->
                                    <a id="social-group-readonly" href="/static/forum/premium.html" class="hidden mt-1 text-[11px] text-primary hover:underline" data-i18n="groups.readOnly">Renew Pro to post in groups</a>
                                    <span id="social-char-count" class="hidden mt-1 ml-auto text-[11px] text-textMuted/50">0/500</span>
                                </div>
                            </form>
                        </div>
                    </div>
                </div>
            </div>

            <!-- ==================== FRIENDS SUB - TAB ==================== -->
    <div id="social-content-friends" class="flex-1 overflow-y-auto p-4 hidden">
        <div class="max-w-4xl mx-auto space-y-6">
            <!-- Search Section -->
            <div class="bg-surface border border-borderSubtle rounded-2xl p-6">
                <h3 class="font-bold text-secondary text-lg mb-4 flex items-center gap-2">
                    <i data-lucide="search" class="w-5 h-5"></i>
                    <span data-i18n="friends.findFriends">Find Friends</span>
                </h3>
                <div class="relative">
                    <input type="text" id="friend-search-input" placeholder="Search by username..." data-i18n="friends.searchPlaceholder" data-i18n-attr="placeholder"
                        class="w-full bg-background border border-borderLight rounded-xl px-4 py-3 pl-10 text-secondary outline-none focus:border-primary/50 transition"
                        data-input-action="friendSearch">
                        <i data-lucide="search" class="w-5 h-5 text-textMuted absolute left-3 top-1/2 -translate-y-1/2"></i>
                </div>
                <div id="search-results" class="mt-4 space-y-2 hidden"></div>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <!-- Pending Requests -->
                <div class="bg-surface border border-borderSubtle rounded-2xl p-6">
                    <h3 class="font-bold text-secondary text-lg mb-4 flex items-center gap-2">
                        <i data-lucide="user-plus" class="w-5 h-5 text-primary"></i>
                        <span data-i18n="friends.friendRequests">Friend Requests</span>
                        <span id="pending-count-badge" class="hidden px-2 py-0.5 text-xs bg-danger text-background rounded-full"></span>
                    </h3>
                    <div id="pending-requests-list" class="space-y-2">
                        <div class="text-center text-textMuted py-6 opacity-50">
                            <i data-lucide="loader-2" class="w-5 h-5 animate-spin mx-auto mb-2"></i>
                            <span data-i18n="friends.loadingRequests">Loading requests...</span>
                        </div>
                    </div>
                </div>

                <!-- My Friends（以前外面又包一層同樣的卡片、標題重複、id 重複；兩層內距讓手機上
                     好友卡片只剩 258px，名字被右側按鈕擠成一條「I」） -->
                <div>
                    <div class="bg-surface border border-borderSubtle rounded-2xl p-6 relative">
                        <div class="flex items-center justify-between mb-4">
                            <h3 class="font-bold text-secondary text-lg flex items-center gap-2">
                                <i data-lucide="users" class="w-5 h-5 text-success"></i>
                                <span data-i18n="friends.myFriends">My Friends</span>
                                <span id="friends-count-badge" class="hidden px-2 py-0.5 text-xs bg-surfaceHighlight text-textMuted rounded-full">0</span>
                            </h3>
                            <button data-click="FriendsUI.toggleBlockedView" class="text-xs text-textMuted hover:text-danger transition flex items-center gap-1">
                                <i data-lucide="ban" class="w-3 h-3"></i>
                                <span data-i18n="friends.manageBlocked">Manage Blocklist</span>
                            </button>
                        </div>

                        <!-- Friends List View -->
                        <div id="friends-view-container">
                            <div id="friends-list" class="space-y-2">
                                <div class="text-center text-textMuted py-6 opacity-50">
                                    <i data-lucide="loader-2" class="w-5 h-5 animate-spin mx-auto mb-2"></i>
                                    <span data-i18n="friends.loadingFriends">Loading friends...</span>
                                </div>
                            </div>
                        </div>

                        <!-- Blocked List View (Hidden by default) -->
                        <div id="blocked-view-container" class="hidden">
                            <div class="flex items-center gap-2 mb-4 p-3 bg-danger/5 rounded-lg border border-danger/10">
                                <i data-lucide="info" class="w-4 h-4 text-danger"></i>
                                <p class="text-xs text-textMuted" data-i18n="friends.blockedInfo">Blocked users cannot send you messages or friend requests.</p>
                            </div>
                            <div id="blocked-users-list" class="space-y-2">
                                <!-- Blocked users injected here -->
                            </div>
                        </div>
                    </div>
                </div>

                <div class="h-20"></div>
            </div>
        </div>
    </div>
    `;

// Side-effect module — assigns to window.Components
export {};
