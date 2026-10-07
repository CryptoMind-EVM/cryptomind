// ========================================
// auth.js - 用戶身份認證模塊 (TON Connect 版)
// Session 協調器：token refresh / session restore / UI。
// 登入由 evm-auth.js 透過 SIWE 執行，成功後呼叫 _applyEvmSession；
// Telegram Mini App 另走 ton-auth.js 的 _applyTelegramSession。
// ========================================

const AUTH_DIAGNOSTICS_KEY = 'auth_diagnostics_v1';
const MAX_AUTH_DIAGNOSTICS = 120;

// session 儲存的 localStorage key（TON 版）。舊版用 'pi_user'，restore 時自動搬移。
const SESSION_STORAGE_KEY = 'ton_user';
const LEGACY_SESSION_STORAGE_KEY = 'pi_user';

function readAuthDiagnostics() {
    try {
        const raw = sessionStorage.getItem(AUTH_DIAGNOSTICS_KEY);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
    } catch (_) {
        return [];
    }
}

function writeAuthDiagnostics(entries) {
    try {
        sessionStorage.setItem(
            AUTH_DIAGNOSTICS_KEY,
            JSON.stringify(entries.slice(-MAX_AUTH_DIAGNOSTICS))
        );
    } catch (_) {
        console.debug('writeAuthDiagnostics: sessionStorage full or unavailable');
    }
}

function pushAuthDiagnostic(event, data) {
    const entries = readAuthDiagnostics();
    entries.push({
        at: new Date().toISOString(),
        event,
        data: data || {},
    });
    writeAuthDiagnostics(entries);
}

window.getAuthDiagnostics = function () {
    return readAuthDiagnostics();
};

window.clearAuthDiagnostics = function () {
    sessionStorage.removeItem(AUTH_DIAGNOSTICS_KEY);
};

window.copyAuthDiagnostics = async function () {
    const serialized = JSON.stringify(readAuthDiagnostics(), null, 2);
    if (!navigator.clipboard?.writeText) {
        return serialized;
    }

    await navigator.clipboard.writeText(serialized);
    if (typeof showToast === 'function') {
        showToast(window.I18n.t('auth.copiedDiagnostics'), 'success');
    }
    return serialized;
};

const DebugLog = {
    send(level, message, data = null) {
        if (
            message.includes('TON') ||
            message.includes('Token') ||
            message.includes('session') ||
            message.includes('登入') ||
            message.includes('login') ||
            message.includes('refresh')
        ) {
            pushAuthDiagnostic(message, { level, ...(data || {}) });
        }
        if (window.APP_CONFIG && window.APP_CONFIG.DEBUG_MODE !== true) {
            return;
        }
        console.log(`[${level.toUpperCase()}] ${message}`, data);
        // 上報進行中產生的 log（例如這個請求 401 → refresh 又記了一筆）不再上報，
        // 否則 401 → refresh → 上報 → 401 會無限循環（訪客頁十秒可打上千次）
        if (window.APP_CONFIG && window.APP_CONFIG.DEBUG_MODE === true && !DebugLog._posting) {
            DebugLog._posting = true;
            AppAPI.post('/api/debug-log', { level, message, data }, { keepalive: true })
                .catch(() => {
                    console.debug('DebugLog: server logging unavailable');
                })
                .finally(() => {
                    DebugLog._posting = false;
                });
        }
    },
    info(msg, data) {
        this.send('info', msg, data);
    },
    error(msg, data) {
        this.send('error', msg, data);
    },
    warn(msg, data) {
        this.send('warn', msg, data);
    },
};

window.DebugLog = DebugLog;

const LOGIN_RECOVERY_GRACE_MS = 15000;

var _refreshPromise = null;
var _testSessionRecoveryPromise = null;

// 認證環境輔助（method-agnostic；TON Connect 不需特定瀏覽器）
const AuthEnvironment = {
    isLocalhost() {
        return (
            window.location.hostname === 'localhost' ||
            window.location.hostname === '127.0.0.1' ||
            window.location.hostname === '::1'
        );
    },

    getAccessToken() {
        return null;
    },

    isAuthenticated() {
        const currentUser = window.AuthManager?.currentUser || null;
        if (!currentUser) return false;

        return !!(currentUser.user_id || currentUser.uid);
    },

    getAuthHeaders(extraHeaders = {}) {
        return { ...extraHeaders };
    },

    shouldBlockProtectedRequests() {
        return !this.isAuthenticated();
    },

    // 訪客模式 Tier 1（2026-08-27 設計）：唯讀市場數據開關。預設 true（與後端
    // GUEST_DATA_ACCESS 預設一致）；_hydrateGuestBanner 取 quota 後以伺服器值覆寫。
    guestDataAccess: true,
};

window.AuthEnvironment = AuthEnvironment;
// 向後相容：舊程式碼以 window.PiEnvironment.* 呼叫 method-agnostic 部分
window.PiEnvironment = AuthEnvironment;

const AuthManager = {
    currentUser: null,
    _refreshTimer: null,
    _initPromise: null,

    // Token 過期時間（24 小時，與後端 ACCESS_TOKEN_EXPIRE_MINUTES 一致）
    TOKEN_EXPIRY_MS: 24 * 60 * 60 * 1000,

    // 在過期前多久開始刷新（6 小時）
    REFRESH_BEFORE_EXPIRY_MS: 6 * 60 * 60 * 1000,

    isPasswordSession(user) {
        const method = user?.authMethod || user?.auth_method;
        return method === 'password';
    },

    isMockSession(user = this.currentUser) {
        const userId = user?.user_id || user?.uid || '';
        const method = user?.authMethod || user?.auth_method || '';
        return !!window.__APP_TEST_MODE && (userId.startsWith('test-user-') || method === 'dev_test');
    },

    isTokenExpired() {
        if (!this.currentUser) return true;

        const expiry = this.currentUser.accessTokenExpiry;
        if (!expiry) {
            // 沒有過期時間，假設已過期（舊格式 token）
            return true;
        }

        return Date.now() > expiry;
    },

    /**
     * 檢查 token 是否即將過期（1 小時內）
     * @returns {boolean} true = 即將過期
     */
    isTokenExpiringSoon() {
        if (!this.currentUser) return true;

        const expiry = this.currentUser.accessTokenExpiry;
        if (!expiry) return true;

        const oneHour = 60 * 60 * 1000;
        return expiry - Date.now() < oneHour;
    },

    /**
     * 檢查 token 是否需要刷新（過期前 1 天內）
     * @returns {boolean} true = 需要刷新
     */
    needsRefresh() {
        if (!this.currentUser) return false;

        const expiry = this.currentUser.accessTokenExpiry;
        if (!expiry) return false;

        const timeUntilExpiry = expiry - Date.now();
        // 在過期前 REFRESH_BEFORE_EXPIRY_MS 毫秒內需要刷新
        return timeUntilExpiry > 0 && timeUntilExpiry < this.REFRESH_BEFORE_EXPIRY_MS;
    },

    _saveUserSession() {
        if (!this.currentUser) return;
        const safe = {
            ...this.currentUser,
            accessTokenExpiry: this.currentUser.accessTokenExpiry,
        };
        // Browser sessions are cookie-only: never persist a bearer token where XSS can read it.
        delete safe.accessToken;
        delete safe.token;
        delete safe.refreshToken;
        localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(safe));
    },

    _mergeCurrentUser(patch) {
        this.currentUser = {
            ...(this.currentUser || {}),
            ...(patch || {}),
        };
        this._saveUserSession();
        return this.currentUser;
    },

    _applyBackendSessionUser(backendUser) {
        // 後端 /me 帶回「使用者上次選的 LLM provider」（跨裝置還原）。
        // 策略：僅在本機 localStorage 尚未記錄時才填入——首次在這個裝置登入時
        // 還原使用者偏好；若本機已有值（使用者剛在本裝置切換過），尊重本機選擇，
        // 避免覆蓋掉比後端更新的本地狀態。
        var backendProvider = backendUser.selected_provider;
        if (
            backendProvider &&
            window.APIKeyManager &&
            typeof window.APIKeyManager?.getSelectedProvider === 'function' &&
            !window.APIKeyManager.getSelectedProvider()
        ) {
            // 直接寫 localStorage，不呼叫 setSelectedProvider（那會再 PUT 回後端，形成迴圈）
            localStorage.setItem('user_selected_provider', backendProvider);
        }
        this._mergeCurrentUser({
            user_id: backendUser.user_id || this.currentUser?.user_id,
            uid:
                backendUser.user_id ||
                this.currentUser?.uid ||
                this.currentUser?.user_id ||
                null,
            username: backendUser.username || this.currentUser?.username,
            // display_name:使用者自訂暱稱。後端 /api/user/me 會回傳;
            // 顯示時優先於不可變的 username(系統預設 TON_<hex>)。
            display_name:
                backendUser.display_name ?? this.currentUser?.display_name ?? null,
            authMethod: backendUser.auth_method || this.currentUser?.authMethod,
            auth_method: backendUser.auth_method || this.currentUser?.auth_method,
            role: backendUser.role || this.currentUser?.role || 'user',
            membership_tier:
                backendUser.membership_tier || this.currentUser?.membership_tier || 'free',
            // 到期日（isoformat）：Settings 卡快取首繪即顯示「· {{date}} 到期」
            membership_expires_at:
                backendUser.membership_expires_at ??
                this.currentUser?.membership_expires_at ??
                null,
            has_wallet:
                typeof backendUser.has_wallet === 'boolean'
                    ? backendUser.has_wallet
                    : this.currentUser?.has_wallet,
            // 舊 TON 身份、沒綁 EVM／Google → legacy-ton-notice.js 顯示綁定提醒
            login_backup_needed: backendUser.login_backup_needed === true,
            // 條款同意狀態 {version, accepted}：accepted=false → legal-consent.js 顯示同意提示卡
            legal: backendUser.legal ?? null,
        });
        // 這一頁剛從 /api/user/me 拿到的欄位（不寫進 localStorage：只代表「這次載入已查過」）。
        // i18n.js 要語言偏好時直接用這份，不必再打一次 /api/user/me（2026-09-26 量測：一頁 2 次）
        this._sessionUserFromBackend = {
            user_id: this.currentUser?.user_id || null,
            language: backendUser.language ?? null,
        };
    },

    restoreSessionFromStorage() {
        if (this.currentUser) {
            return true;
        }

        // 一次性 migration：舊版用 'pi_user' key，搬到 'ton_user'
        const legacy = localStorage.getItem(LEGACY_SESSION_STORAGE_KEY);
        if (legacy && !localStorage.getItem(SESSION_STORAGE_KEY)) {
            localStorage.setItem(SESSION_STORAGE_KEY, legacy);
        }
        localStorage.removeItem(LEGACY_SESSION_STORAGE_KEY);

        const savedUser = localStorage.getItem(SESSION_STORAGE_KEY);
        if (!savedUser) {
            return false;
        }

        try {
            const parsedUser = JSON.parse(savedUser);
            if (!parsedUser || (!parsedUser.user_id && !parsedUser.uid)) {
                localStorage.removeItem(SESSION_STORAGE_KEY);
                return false;
            }

            // Remove bearer tokens written by pre-cookie-only versions immediately.
            delete parsedUser.accessToken;
            delete parsedUser.token;
            delete parsedUser.refreshToken;
            localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(parsedUser));
            this.currentUser = parsedUser;
            pushAuthDiagnostic('restoreSessionFromStorage:success', {
                user_id: parsedUser.user_id || parsedUser.uid || null,
            });
            DebugLog.info('auth.sessionRestoredFromStorage');
            return true;
        } catch (error) {
            pushAuthDiagnostic('restoreSessionFromStorage:invalid', {
                error: error.message,
            });
            DebugLog.warn('auth.restoreSessionFailed', { error: error.message });
            localStorage.removeItem(SESSION_STORAGE_KEY);
            return false;
        }
    },

    /**
     * 清除過期的 token 並導向登入
     */
    clearExpiredToken() {
        if (this.shouldDeferExpiredSessionCleanup()) {
            DebugLog.warn('Skip expired token cleanup during login transition');
            return false;
        }
        DebugLog.warn('auth.tokenExpired');
        pushAuthDiagnostic('clearExpiredToken', {
            hadUser: !!this.currentUser,
        });
        this.currentUser = null;
        localStorage.removeItem(SESSION_STORAGE_KEY);
        this._updateUI(false);

        AppAPI.post('/api/user/logout').catch(() => {
            console.debug('clearExpiredToken: logout API failed (network or server error)');
        });

        // 顯示提示
        if (typeof showToast === 'function') {
            showToast(
                window.I18n?.t('auth.loginExpired') || 'Login expired, please log in again',
                'warning'
            );
        }

        // 重整頁面以顯示登入 modal
        window.location.reload();
        return true;
    },

    getRecentLoginSuccessAt() {
        const raw = sessionStorage.getItem('ton_login_success_at');
        const value = raw ? Number(raw) : 0;
        return Number.isFinite(value) ? value : 0;
    },

    markRecentLoginSuccess() {
        sessionStorage.setItem('ton_login_success_at', String(Date.now()));
    },

    /** 登入（含背景續登）正在進行中 */
    isLoginInFlight() {
        // 旗標由 evm-auth.js 的 _setLoginInFlight() 寫（登入與背景續登都算）。
        // 舊名 tonLoginInProgress 已隨 TON 登入一起移除——它從 2026-08-31 起
        // 就沒有任何寫入者，這個守衛等於空轉了半個月。
        return (
            (typeof AppStore !== 'undefined' && !!AppStore.get('loginInProgress')) ||
            window.__loginInFlight === true
        );
    },

    shouldDeferExpiredSessionCleanup() {
        if (this.isLoginInFlight()) {
            return true;
        }

        const lastSuccessAt = this.getRecentLoginSuccessAt();
        return lastSuccessAt > 0 && Date.now() - lastSuccessAt < LOGIN_RECOVERY_GRACE_MS;
    },

    /**
     * 啟動 token 自動刷新定時器
     * 每 30 分鐘檢查一次，如果 token 快過期則自動刷新
     */
    startTokenRefreshTimer() {
        // 清除舊的定時器
        if (this._refreshTimer) {
            clearInterval(this._refreshTimer);
        }

        // 每 30 分鐘檢查一次（平衡用戶體驗和及時刷新）
        this._refreshTimer = setInterval(
            async () => {
                if (!this.currentUser) return;

                DebugLog.info('Token refresh check', {
                    needsRefresh: this.needsRefresh(),
                    isExpired: this.isTokenExpired(),
                    timeUntilExpiry: this.currentUser.accessTokenExpiry
                        ? Math.round(
                              (this.currentUser.accessTokenExpiry - Date.now()) / 1000 / 60
                          ) + ' minutes'
                        : 'unknown',
                });

                if (this.isTokenExpired() || this.needsRefresh()) {
                    DebugLog.info('auth.tokenNeedsRefreshUsingBackend');
                    await this.maybeRefreshToken();
                }
            },
            30 * 60 * 1000
        ); // check every 30 minutes

        if (this._visibilityHandler) {
            document.removeEventListener('visibilitychange', this._visibilityHandler);
        }
        this._visibilityHandler = async () => {
            if (document.visibilityState === 'visible' && this.currentUser) {
                if (this.shouldDeferExpiredSessionCleanup()) {
                    DebugLog.info('auth.pageVisibleLoginInProgress');
                    return;
                }
                DebugLog.info('auth.pageVisibleCheckToken');
                if (this.isTokenExpired() || this.needsRefresh()) {
                    await this.maybeRefreshToken();
                }
            }
        };

        document.addEventListener('visibilitychange', this._visibilityHandler);

        if (this._pageShowHandler) {
            window.removeEventListener('pageshow', this._pageShowHandler);
        }
        this._pageShowHandler = () => {
            // 2026-09-02 修 bug：後端同步原本整段包在 if (!this.currentUser) 裡，
            // 正常啟動時 currentUser 已從 localStorage 還原，導致 /api/user/me
            // 永遠不打——DB 端 role 變更（如升 admin）永遠不會反映到已登入裝置。
            // 改為：只要有登入態（記憶體或 storage 還原），一律同步一次。
            if (!this.currentUser && !this.restoreSessionFromStorage()) {
                return; // 沒有快取 session = 未登入
            }

            Promise.resolve(this.restoreSessionFromBackend())
                .then((result) => {
                    if (result.success) {
                        pushAuthDiagnostic('pageshow:restoreSessionFromBackend:success');
                        this._updateUI(true);
                        return;
                    }
                    pushAuthDiagnostic('pageshow:restoreSessionFromBackend:failed', {
                        error: result.error,
                    });
                    this.clearExpiredToken();
                })
                .catch((error) => {
                    pushAuthDiagnostic('pageshow:restoreSessionFromBackend:error', {
                        error: error.message,
                    });
                    DebugLog.warn('auth.pageshowRestoreFailed', { error: error.message });
                    this.clearExpiredToken();
                });
        };
        window.addEventListener('pageshow', this._pageShowHandler);

        DebugLog.info('Token refresh timer started (30 min interval + visibility check)');
    },

    /**
     * 使用 refresh cookie 刷新 token
     * @returns {Promise<{success: boolean, error?: string}>}
     */
    // 主動刷新（interval／visibility／pageshow 用）：60 秒冷卻。
    // 單飛 _refreshPromise 只去重「並發」；分頁切換會在短時間內觸發多次
    // 「序列」刷新，實測會撞 /api/user/refresh 限流（2026-08-14 平台測試 429）。
    // 401 自動恢復路徑（api-client）不走這裡，維持無冷卻的強制刷新。
    async maybeRefreshToken() {
        const now = Date.now();
        if (this._lastProactiveRefreshAt && now - this._lastProactiveRefreshAt < 60000) {
            DebugLog.info('auth.proactiveRefreshCooldown');
            return { success: true, cooldown: true };
        }
        this._lastProactiveRefreshAt = now;
        return await this.backendTokenRefresh();
    },

    async backendTokenRefresh() {
        if (_refreshPromise) {
            DebugLog.info('auth.refreshAlreadyInProgress');
            return _refreshPromise;
        }

        _refreshPromise = (async () => {
            try {
                DebugLog.info('auth.attemptingBackendRefresh');
                pushAuthDiagnostic('backendTokenRefresh:start');

                const result = await AppAPI.post(
                    '/api/user/refresh',
                    null,
                    { headers: { Authorization: '' } }
                );

                this._mergeCurrentUser({
                    accessTokenExpiry: Date.now() + this.TOKEN_EXPIRY_MS,
                });
                this.markRecentLoginSuccess();

                DebugLog.info('auth.backendRefreshSuccess', {
                    newExpiry: new Date(this.currentUser.accessTokenExpiry).toISOString(),
                });
                pushAuthDiagnostic('backendTokenRefresh:success', {
                    expiry: this.currentUser.accessTokenExpiry,
                });

                return { success: true };
            } catch (error) {
                pushAuthDiagnostic('backendTokenRefresh:failed', {
                    error: error.message,
                });
                DebugLog.error('auth.backendRefreshFailed', { error: error.message });
                if (this.isMockSession()) {
                    DebugLog.warn('Refresh failed for test-mode session, attempting dev-login recovery', {
                        error: error.message,
                    });
                    return await this.recoverTestModeSession();
                }
                return { success: false, error: error.message };
            }
        })().finally(() => { _refreshPromise = null; });

        return _refreshPromise;
    },

    async recoverTestModeSession() {
        if (!this.isMockSession()) {
            return { success: false, error: 'Not a test-mode session' };
        }

        if (_testSessionRecoveryPromise) {
            return _testSessionRecoveryPromise;
        }

        _testSessionRecoveryPromise = (async () => {
            try {
                const targetUserId =
                    this.currentUser?.user_id || this.currentUser?.uid;
                pushAuthDiagnostic('recoverTestModeSession:start', {
                    user_id: targetUserId || null,
                });

                const result = await AppAPI.post(
                    '/api/user/dev-login',
                    targetUserId ? { user_id: targetUserId } : undefined,
                    { headers: { Authorization: '' }, _authRetried: true }
                );

                this.currentUser = {
                    ...(this.currentUser || {}),
                    uid: result.user.uid,
                    user_id: result.user.uid,
                    username: result.user.username,
                    accessTokenExpiry: Date.now() + this.TOKEN_EXPIRY_MS,
                    authMethod: result.user.authMethod,
                    auth_method: result.user.authMethod,
                };
                this._saveUserSession();
                this._updateUI(true);
                this.markRecentLoginSuccess();

                pushAuthDiagnostic('recoverTestModeSession:success', {
                    user_id: result.user.uid,
                });
                DebugLog.info('Recovered test-mode session via dev-login', {
                    user_id: result.user.uid,
                });
                return { success: true };
            } catch (error) {
                pushAuthDiagnostic('recoverTestModeSession:failed', {
                    error: error.message,
                });
                DebugLog.error('Failed to recover test-mode session', {
                    error: error.message,
                });
                return { success: false, error: error.message };
            }
        })().finally(() => { _testSessionRecoveryPromise = null; });

        return _testSessionRecoveryPromise;
    },

    async restoreSessionFromBackend() {
        if (!this.currentUser) {
            return { success: false, error: 'No cached user' };
        }

        try {
            pushAuthDiagnostic('restoreSessionFromBackend:start');
            const result = await AppAPI.get('/api/user/me', { _skipAuthGate: true });
            this._applyBackendSessionUser(result?.user || {});
            pushAuthDiagnostic('restoreSessionFromBackend:success', {
                user_id: result?.user?.user_id || null,
            });
            // Refresh the httpOnly-cookie session before normal API traffic resumes.
            await this.backendTokenRefresh().catch((e) => {
                DebugLog.warn('Proactive token refresh after session restore failed', { error: e.message });
            });
            return { success: true };
        } catch (error) {
            if (error?.status !== 401) {
                pushAuthDiagnostic('restoreSessionFromBackend:non401', {
                    status: error?.status || 0,
                    error: error.message,
                });
                DebugLog.warn('auth.backendSessionCheckFailed', {
                    status: error?.status || 0,
                    error: error.message,
                });
                return { success: false, error: error.message };
            }

            DebugLog.info('auth.accessTokenInvalidTryingRefresh');
            pushAuthDiagnostic('restoreSessionFromBackend:accessExpired');
            const refreshResult = await this.backendTokenRefresh();
            if (!refreshResult.success) {
                return refreshResult;
            }

            try {
                const result = await AppAPI.get('/api/user/me', { _skipAuthGate: true });
                this._applyBackendSessionUser(result?.user || {});
                pushAuthDiagnostic('restoreSessionFromBackend:successAfterRefresh', {
                    user_id: result?.user?.user_id || null,
                });
                return { success: true };
            } catch (retryError) {
                pushAuthDiagnostic('restoreSessionFromBackend:failedAfterRefresh', {
                    status: retryError?.status || 0,
                    error: retryError.message,
                });
                DebugLog.error('auth.refreshStillFailed', {
                    status: retryError?.status || 0,
                    error: retryError.message,
                });
                return { success: false, error: retryError.message };
            }
        }
    },

    /**
     * 停止 token 自動刷新定時器
     */
    stopTokenRefreshTimer() {
        if (this._refreshTimer) {
            clearInterval(this._refreshTimer);
            this._refreshTimer = null;
            DebugLog.info('Token refresh timer stopped');
        }
        if (this._visibilityHandler) {
            document.removeEventListener('visibilitychange', this._visibilityHandler);
            this._visibilityHandler = null;
        }
        if (this._pageShowHandler) {
            window.removeEventListener('pageshow', this._pageShowHandler);
            this._pageShowHandler = null;
        }
    },

    isLoggedIn() {
        return !!this.currentUser;
    },

    async loginAsMockUser() {
        window.APP_CONFIG?.DEBUG_MODE &&
            console.log('⚠️ [Dev Mode] Manually triggering Mock Login.');
        showToast(window.I18n.t('auth.devModeLogin'), 'info');

            await new Promise((resolve) => setTimeout(resolve, 500)); // simulate delay

        try {
            // 使用新的 Dev Login Endpoint
            const result = await AppAPI.post('/api/user/dev-login', null, { _skipAuthGate: true });

            this.currentUser = {
                uid: result.user.uid,
                user_id: result.user.uid,
                username: result.user.username,
                accessTokenExpiry: Date.now() + this.TOKEN_EXPIRY_MS,
                authMethod: result.user.authMethod,
            };

            this._saveUserSession();
            this._updateUI(true);

            if (typeof initChat === 'function') initChat();
            return { success: true, user: this.currentUser };
        } catch (e) {
            console.error('Mock login error:', e);
            showToast(window.I18n.t('auth.mockLoginFailed'), 'error');
            return { success: false, error: e.message };
        }
    },

    // 每次頁面載入只跑一次：api-client 的 auth gate 每支請求都 await 這裡，
    // 完成的 promise 要留著。原本 finally 一律清掉 _initPromise，只擋得住「同時」
    // 的呼叫——之後每支請求都重打 /api/user/me＋refresh、重發 auth:initialized
    // （2026-09-25 盤查）。只有失敗或狀態未定才放掉；登入成功由檔尾 auth-success
    // 監聽 resetInit() 重跑；登出／切帳號都會整頁重載。
    async init() {
        if (this._initPromise) {
            return this._initPromise;
        }

        let retryLater = false;
        const initPromise = (async () => {
            // 檢查 URL 參數是否要求強制登出
            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.get('logout') === '1' || urlParams.get('force_logout') === '1') {
                DebugLog.info('auth.urlTriggeredForceLogout');
                localStorage.removeItem(SESSION_STORAGE_KEY);
                window.history.replaceState({}, '', window.location.pathname);
                window.location.reload();
                return false;
            }

            // 優先從 localStorage 載入用戶（同步，確保立即可用）
            if (this.restoreSessionFromStorage()) {
                const restoreResult = await this.restoreSessionFromBackend();
                if (!restoreResult.success) {
                    DebugLog.warn('auth.cannotRestoreBackendSession', {
                        error: restoreResult.error,
                    });
                    // 登入過渡期 clearExpiredToken 會暫緩（回 false）：session 未定，
                    // 這次結果不記住，讓下一個呼叫者重驗
                    retryLater = !this.clearExpiredToken();
                    return false;
                }
                this._updateUI(true);
            } else {
                this._updateUI(false);
            }

            // 檢查測試模式（async，但不影響已登入用戶）— 用共用 getAppConfig() 去重
            try {
                const config = await AppAPI.getAppConfig();

                window.__APP_TEST_MODE = !!config.test_mode;

                const switcher = document.getElementById('dev-user-switcher');
                if (switcher) {
                    if (config.test_mode) {
                        switcher.classList.remove('hidden');
                    } else {
                        switcher.classList.add('hidden');
                    }
                }

                if (config.test_mode && !this.currentUser) {
                    window.APP_CONFIG?.DEBUG_MODE &&
                        console.log(window.I18n.t('testMode.autoLoginTestUser') || '🧪 [Test Mode] 自動登入測試用戶（透過 dev-login endpoint）');
                    const result = await this.loginAsMockUser();
                    if (!result.success) {
                        console.warn(window.I18n.t('testMode.autoLoginFailed') || '🧪 [Test Mode] 自動登入失敗:', result.error);
                    }
                }
            } catch (e) {
                console.warn('Failed to check test mode:', e);
            }

            if (this.currentUser) {
                this.startTokenRefreshTimer();

                if (this.needsRefresh()) {
                    DebugLog.info('auth.tokenExpiringSoonStartupRefresh');
                    this.backendTokenRefresh().catch((e) => {
                        DebugLog.warn('auth.startupRefreshFailedRetry', { error: e.message });
                    });
                }
            }

            if (this.currentUser) {
                window.dispatchEvent(new Event('auth:ready'));
            }

            return !!this.currentUser;
        })();
        this._initPromise = initPromise;

        try {
            const result = await initPromise;
            window.dispatchEvent(
                new CustomEvent('auth:initialized', {
                    detail: { isLoggedIn: !!this.currentUser },
                })
            );
            return result;
        } catch (error) {
            retryLater = true;
            throw error;
        } finally {
            // 已被 resetInit() 換掉就別動新的那個
            if (retryLater && this._initPromise === initPromise) {
                this._initPromise = null;
            }
        }
    },

    // 放掉記住的 init 結果，下一個 init() 呼叫者重跑完整初始化。
    resetInit() {
        this._initPromise = null;
    },

    // 訪客 banner 額度插值：guest.bannerText 的 {{limit}} 需要 data-i18n-args
    // 才會被 i18next 插值（否則原文露出）。限額由後端 env GUEST_DAILY_QUESTIONS
    // 決定，前端不可寫死 → 取 quota 後寫入 args 再顯示；語言切換時
    // updatePageContent 會帶著 args 重渲染。失敗 fallback 與後端 _DEFAULT_DAILY 同值。
    _hydrateGuestBanner(banner) {
        if (banner.dataset.guestBannerHydrated) {
            // 額度已取過（args 已設）→ 直接顯示（如登出後回到訪客態）
            banner.classList.remove('hidden');
            return;
        }
        banner.dataset.guestBannerHydrated = '1';
        const reveal = (limit) => {
            // fetch 期間可能已登入（如 TMA 自動登入）→ 不顯示
            const A = window.AuthManager;
            if (A && typeof A.isLoggedIn === 'function' && A.isLoggedIn()) return;
            const span = banner.querySelector('[data-i18n="guest.bannerText"]');
            if (span && typeof limit === 'number') {
                span.setAttribute('data-i18n-args', JSON.stringify({ limit }));
            }
            if (window.I18n && typeof window.I18n.updatePageContent === 'function') {
                window.I18n.updatePageContent();
            }
            banner.classList.remove('hidden');
        };
        fetch('/api/guest/quota', { credentials: 'include' })
            .then((r) => (r.ok ? r.json() : null))
            .then((d) => {
                // Tier 1（2026-08-27 設計）：唯讀市場數據總開關——spa.js 守門與
                // global-nav 鎖定清單都讀這個值；quota 失敗時保持預設 true。
                if (window.AuthEnvironment) {
                    window.AuthEnvironment.guestDataAccess =
                        !d || d.data_access !== false;
                }
                reveal(d && typeof d.limit === 'number' ? d.limit : 5);
            })
            .catch(() => reveal(5));
    },

    _updateUI(isLoggedIn) {
        // 讓導覽等 UI 同步（底部導覽的 Admin／鎖定分頁靠這個重畫）
        try { window.dispatchEvent(new CustomEvent('auth:changed', { detail: { isLoggedIn: !!isLoggedIn } })); } catch (e) { /* ignore */ }
        // 訪客模式橫幅（2026-08-19）：未登入顯示引導，登入後隱藏。
        // Connect 按鈕打開既有 login modal（CSP-safe：JS 綁定，非 inline）。
        try {
            const guestBanner = document.getElementById('guest-banner');
            if (guestBanner) {
                if (isLoggedIn) {
                    guestBanner.classList.add('hidden');
                } else {
                    // 額度取回前不顯示，避免 {{limit}} 未插值原文閃現
                    this._hydrateGuestBanner(guestBanner);
                }
                const connectBtn = document.getElementById('guest-connect-btn');
                if (connectBtn && !connectBtn.dataset.boundGuestConnect) {
                    connectBtn.dataset.boundGuestConnect = '1';
                    connectBtn.addEventListener('click', () => {
                        const modal = document.getElementById('login-modal');
                        if (modal) modal.classList.remove('hidden');
                    });
                }
            }
        } catch (e) {
            console.warn('[Auth] guest banner toggle failed:', e);
        }

        // 顯示名優先用自訂暱稱 display_name,沒設才 fallback 到系統預設
        // username(TON_<hex>),最後才是佔位文字。
        const username =
            this.currentUser?.display_name ||
            this.currentUser?.username ||
            (window.I18n ? window.I18n.t('auth.loginRequired') : 'Login Required');
        const uid =
            this.currentUser?.uid || this.currentUser?.user_id || '--';
        // 不知道登入方式就留空——以前預設 ton_wallet，Google／Telegram 帳號會被標成 TON 錢包
        const authMethod =
            this.currentUser?.authMethod ||
            this.currentUser?.auth_method ||
            (this.currentUser ? '' : 'guest');

        // 控制 auth-only 和 guest-only 元素的顯示
        document.querySelectorAll('.auth-only').forEach((el) => {
            if (isLoggedIn) {
                el.classList.remove('hidden');
            } else {
                el.classList.add('hidden');
            }
        });
        document.querySelectorAll('.guest-only').forEach((el) => {
            el.classList.add('hidden');
        });

        // 更新所有可能存在的使用者名稱欄位
        // ⚠️ sidebar-user-name 帶 data-i18n="auth.loginRequired"。登入後即使這裡把
        // textContent 設成使用者名稱,只要之後任一次 I18n.updatePageContent() 掃過
        // [data-i18n](切語言 / i18n 晚初始化 / 重繪),就會把名稱蓋回「請先登入」。
        // 所以登入時必須「暫時卸掉 data-i18n」,登出時再還原,讓 i18n 不再覆寫。
        const applyUserNameField = (el) => {
            if (!el) return;
            if (isLoggedIn) {
                const key = el.getAttribute('data-i18n');
                if (key) {
                    el.dataset.i18nUserRestore = key; // 記住原 key 供登出還原
                    el.removeAttribute('data-i18n');
                }
                el.textContent = username;
            } else {
                const key = el.dataset.i18nUserRestore || el.getAttribute('data-i18n');
                if (key) {
                    el.setAttribute('data-i18n', key);
                    delete el.dataset.i18nUserRestore;
                    el.textContent = window.I18n?.t ? window.I18n.t(key) : username;
                } else {
                    el.textContent = username;
                }
            }
        };
        ['sidebar-user-name', 'forum-user-name', 'profile-username', 'nav-username'].forEach(
            (id) => applyUserNameField(document.getElementById(id))
        );

        // 更新所有有 user-display-name class 的元素
        document.querySelectorAll('.user-display-name').forEach(applyUserNameField);

        // 更新 UID 顯示
        const uidEl = document.getElementById('profile-uid');
        if (uidEl) {
            const _uidPfx = (window.I18n ? window.I18n.t('auth.uidPrefix') : 'UID: '); uidEl.textContent = isLoggedIn ? `${_uidPfx}${uid}` : `${_uidPfx}--`;
        }

        // 更新登入方式顯示
        const methodEl = document.getElementById('profile-method');
        if (methodEl) {
            // 登入方式徽章：2026-09-11 DANNY 回報 EVM 帳號顯示「LOCKED」——
            // 舊映射是 TON 時代寫的，evm_wallet／telegram 全部落到 LOCKED 分支。
            // 改由語言檔單一來源解析：auth_method（snake_case）轉 camelCase
            // 對應 auth.method.<camel> key；語言檔沒有的方式直顯原始值大寫，
            // 絕不誤標（JS 內不留任何方法名清單——Mimosa 曾把 'password'
            // 字串誤判為硬編碼憑證）。
            const camel = String(authMethod || '').replace(/_([a-z])/g, (_, c) => c.toUpperCase());
            const key = 'auth.method.' + camel;
            const translated = window.I18n ? window.I18n.t(key) : '';
            methodEl.textContent = !translated || translated === key
                ? String(authMethod || '').toUpperCase()
                : translated;
        }

        // 更新頭像
        ['sidebar-user-avatar', 'forum-user-avatar', 'profile-avatar', 'nav-avatar'].forEach(
            (id) => {
                const el = document.getElementById(id);
                if (el) {
                    if (isLoggedIn) {
                        el.textContent = username[0].toUpperCase();
                        el.classList.add(
                            'bg-gradient-to-br',
                            'from-primary',
                            'to-accent',
                            'text-background'
                        );
                    } else {
                        el.innerHTML = '<i data-lucide="user" class="w-4 h-4"></i>';
                        el.classList.remove(
                            'bg-gradient-to-br',
                            'from-primary',
                            'to-accent',
                            'text-background'
                        );
                    }
                }
            }
        );

        // 控制登入 Modal 顯示
        // 訪客模式（2026-08-19 設計）：未登入不再自動彈全屏 modal——
        // 引導改由 chat 頂部 guest banner（本函式開頭已切換）。
        // modal 保留入口：banner Connect / 升級卡 CTA / 受保護 tab 守門。
        const modal = document.getElementById('login-modal');
        if (modal && isLoggedIn) {
            modal.classList.add('hidden');
        }

        // 更新會員狀態顯示
        if (isLoggedIn && typeof loadPremiumStatus === 'function') {
            loadPremiumStatus({ shared: true });
        }

        // 登入後觸發 auth:ready 事件，由 NotificationService 監聽並初始化
        if (isLoggedIn) {
            window.dispatchEvent(new Event('auth:ready'));
        }

        // 重新渲染側欄功能選單（根據 role 顯示/隱藏 admin tab）
        if (typeof renderNavButtons === 'function') renderNavButtons();
        if (window.GlobalNav && typeof GlobalNav.renderNavButtons === 'function')
            GlobalNav.renderNavButtons();

        AppUtils.refreshIcons();
    },

    logout() {
        this.stopTokenRefreshTimer();
        this.currentUser = null;
        localStorage.removeItem(SESSION_STORAGE_KEY);
        // EVM 對稱處理（2026-09-05 DANNY「要徹底解決」）：登出＝錢包連線一起
        // 真斷，AppKit 面板不再顯示「已連接」。fire-and-forget——不得延遲登出
        // （否則重蹈「點登入沒反應」）。動態 import：auth.js 也被論壇頁載入，
        // 靜態 import 會把 evm-auth 拉進 forum chunk 圖；機制與閉環見
        // evm-auth.js prepareEvmWalletForLogout。
        try {
            import('./evm-auth.js')
                .then((m) => {
                    if (m.prepareEvmWalletForLogout) m.prepareEvmWalletForLogout();
                })
                .catch((e) => {
                    console.debug('logout: evm disconnect skipped', e);
                });
        } catch (e) { /* noop */ }
        AppAPI.post('/api/user/logout').finally(() => {
            this._updateUI(false);
            window.location.reload();
        });
    },
};

// 身份本身就是錢包 → 地址：EVM 登入（evm_0x…）或舊 TON 登入（user_id 即地址）。
// Telegram／Google 帳號回 null——以前一律拿 user_id 當地址，卡片會顯示「tg_12…3456」。
function _identityWalletAddress(user) {
    const uid = String(user?.user_id || user?.uid || '');
    if (/^evm_0x[0-9a-fA-F]{40}$/.test(uid)) return uid.slice(4).toLowerCase();
    const method = user?.authMethod || user?.auth_method;
    if (method === 'ton_wallet' && (uid.length === 48 || /^-?\d+:[0-9a-fA-F]{64}$/.test(uid))) {
        return uid;
    }
    return null;
}

// 獲取錢包狀態（從後端 API）- 含超時機制
async function getWalletStatus() {
    const currentUser = AuthManager.currentUser || {};
    const identityAddress = currentUser.wallet_address || _identityWalletAddress(currentUser);
    const cachedWalletLinked = !!currentUser.has_wallet || !!identityAddress;
    const cachedStatus = {
        has_wallet: cachedWalletLinked,
        auth_method: currentUser.authMethod || currentUser.auth_method || 'guest',
        wallet_address: identityAddress,
    };
    const uid = currentUser.uid || currentUser.user_id;
    if (!uid) {
        return cachedStatus;
    }

    try {
        const data = await AppAPI.get('/api/user/wallet-status');
        // 後端是準的：身份本身是錢包或 user_wallets 有綁定列（綁定地址優先）
        return {
            has_wallet: typeof data.has_wallet === 'boolean' ? data.has_wallet : cachedWalletLinked,
            auth_method: data.auth_method || cachedStatus.auth_method,
            wallet_address: data.wallet_address || identityAddress,
        };
    } catch (e) {
        console.error('getWalletStatus error:', e);
        return {
            ...cachedStatus,
            auth_method: cachedStatus.auth_method || 'unknown',
        };
    }
}

// 内部：根據 has_wallet 更新錢包 UI 元素
function _applyWalletStatusUI(statusBadge, notLinkedSection, linkedSection, usernameEl, walletIcon, status) {
    const isWalletLinked = !!status.has_wallet;

    if (isWalletLinked) {
        statusBadge.innerHTML = `<i data-lucide="check-circle" class="w-3 h-3"></i> ${window.I18n.t('auth.connected')}`;
        statusBadge.className =
            'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-success/10 text-success shrink-0';
        if (walletIcon) {
            walletIcon.innerHTML = '<i data-lucide="wallet" class="w-5 h-5 text-success"></i>';
            walletIcon.className = 'w-10 h-10 rounded-xl bg-success/10 flex items-center justify-center';
        }
        if (notLinkedSection) notLinkedSection.classList.add('hidden');
        if (linkedSection) linkedSection.classList.remove('hidden');
        if (usernameEl && status.wallet_address) {
            usernameEl.textContent = `${status.wallet_address.slice(0, 6)}…${status.wallet_address.slice(-4)}`;
        }
    } else {
        statusBadge.innerHTML = `<i data-lucide="link-2-off" class="w-3 h-3"></i> ${window.I18n.t('auth.notBound')}`;
        statusBadge.className =
            'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-surfaceHighlight text-textMuted shrink-0';
        if (walletIcon) {
            walletIcon.innerHTML = '<i data-lucide="wallet" class="w-5 h-5 text-primary"></i>';
            walletIcon.className = 'w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center';
        }
        if (notLinkedSection) notLinkedSection.classList.remove('hidden');
        if (linkedSection) linkedSection.classList.add('hidden');
    }
    AppUtils.refreshIcons();
}

// 載入 Settings 頁面的錢包狀態
async function loadSettingsWalletStatus() {
    const statusBadge = document.getElementById('settings-wallet-status-badge');
    const notLinkedSection = document.getElementById('wallet-not-linked');
    const linkedSection = document.getElementById('wallet-linked');
    const usernameEl = document.getElementById('settings-wallet-username');
    const walletIcon = document.getElementById('settings-wallet-icon');

    // 如果元素不存在（Settings 頁面未載入），直接返回
    if (!statusBadge) return;

    // ── 即時顯示：從 currentUser 快取讀取 has_wallet ──
    if (AuthManager.currentUser) {
        _applyWalletStatusUI(statusBadge, notLinkedSection, linkedSection, usernameEl, walletIcon, {
            // 任何一條鏈的錢包登入都算「已連接」。原本只認 ton_wallet，
            // 而登入 2026-08-31 起統一 EVM——EVM 使用者會先閃一下「未綁定」
            // 才被後台 API 修正。
            has_wallet:
                AuthManager.currentUser.has_wallet ||
                ['ton_wallet', 'evm_wallet'].includes(
                    AuthManager.currentUser.authMethod ||
                    AuthManager.currentUser.auth_method
                ),
            wallet_address:
                AuthManager.currentUser.wallet_address ||
                _identityWalletAddress(AuthManager.currentUser),
            auth_method: AuthManager.currentUser.authMethod || AuthManager.currentUser.auth_method || null,
        });
    }

    // ── 後台更新：呼叫 API 取得最新狀態 ──
    try {
        let status = await getWalletStatus();
        // 登入方式不是錢包（如 Telegram）但已綁定付款錢包（user_wallets）：
        // 也算已連接，顯示綁定地址——否則綁完卡片還是「未綁定」（2026-09-11 盤查）
        if (status && !status.has_wallet) {
            try {
                const w = await AppAPI.get('/api/user/wallets');
                const evm = (w && w.evm_addresses) || [];
                if (evm.length) status = { ...status, has_wallet: true, wallet_address: evm[0] };
            } catch (e) { /* 查不到就維持原狀 */ }
        }

        _applyWalletStatusUI(statusBadge, notLinkedSection, linkedSection, usernameEl, walletIcon, status);
    } catch (e) {
        console.error('loadSettingsWalletStatus error:', e);
        if (statusBadge) {
            statusBadge.innerHTML = `
                <i data-lucide="alert-circle" class="w-3 h-3"></i>
                ${window.I18n?.t('common.loadFailed') || 'Load failed'}
            `;
            statusBadge.className =
                'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-danger/10 text-danger';
        }
    }
}

// 原創 Premium 徽章（DANNY 2026-09-11 指定自設計）：切面寶石——
// 頂部桌面亮藍、兩側皇冠切面、亭部三片漸深、右上一顆金色星芒。
// 24×24 向量；品牌色 #2563EB／#7EABF5 兩主題共用（DESIGN_SYSTEM），
// 直接寫死 hex 免 CSS var 通道轉換。idSuffix 讓同頁多實例的漸層
// <defs> id 不互相碰撞。
function _premiumGemSvg(idSuffix, cls) {
    const uid = `cmg-${idSuffix}`;
    return `<svg viewBox="0 0 24 24" class="${cls || 'w-4 h-4 shrink-0'}" aria-hidden="true" focusable="false">
        <defs>
            <linearGradient id="${uid}" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stop-color="#7EABF5"/>
                <stop offset="1" stop-color="#2563EB"/>
            </linearGradient>
        </defs>
        <path d="M7 3h10l5 6-10 12L2 9l5-6z" fill="url(#${uid})"/>
        <path d="M2 9h5.5L12 21 2 9z" fill="#1D4ED8" opacity=".72"/>
        <path d="M22 9h-5.5L12 21 22 9z" fill="#1D4ED8" opacity=".72"/>
        <path d="M7.5 9h9L12 21 7.5 9z" fill="#3B82F6" opacity=".9"/>
        <path d="M7 3h2.5L7.5 9H2L7 3z" fill="#93C5FD" opacity=".85"/>
        <path d="M17 3h-2.5l2 6H22l-5-6z" fill="#93C5FD" opacity=".85"/>
        <path d="M9.5 3h5l2 6h-9l2-6z" fill="#DBEAFE" opacity=".95"/>
        <path d="M20.4 1.2l.5 1.3 1.3.5-1.3.5-.5 1.3-.5-1.3-1.3-.5 1.3-.5.5-1.3z" fill="#FBBF24"/>
    </svg>`;
}
window.PremiumGemSvg = _premiumGemSvg;

// 套用 premium badge UI（抽出共用邏輯）
function _applyPremiumBadgeUI(statusBadge, upgradeBtn, isPro, expiresAt) {
    const sidebarBadge = document.getElementById('sidebar-premium-badge');
    if (isPro) {
        // BCP-47 直用（en/zh-TW/zh-CN/ru）——原「非 en 即 zh-TW」讓 ru 用戶
        // 看到 zh-TW 日期格式（2026-09-10 review）
        const dateLocale = window.I18n?.getLanguage?.() || 'zh-TW';
        const expiryText = expiresAt
            ? ` <span class="opacity-80">· ${window.I18n.t('auth.expiresOn', { date: new Date(expiresAt).toLocaleDateString(dateLocale) })}</span>`
            : '';
        // 原創寶石徽章（DANNY 2026-09-11）——寶石扛識別，文字只留
        // PREMIUM＋到期日； Pill 底色保留設計系統的次按鈕語意。
        statusBadge.innerHTML = `
            ${_premiumGemSvg('st')}
            <span class="font-black uppercase tracking-wider">${window.I18n?.t('premium.premiumBadge') || 'PREMIUM'}</span>${expiryText}
        `;
        statusBadge.className =
            'flex flex-wrap items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-primary/15 text-primary border border-primary/30 shrink-0';
        // inline 鎖定：徽章不參與壓縮、內容可換行——裝置級 CSS 異常免疫
        // （2026-09-11 DANNY 截圖：class 版修復在他裝置不生效）
        statusBadge.style.flex = '0 0 auto';
        statusBadge.style.maxWidth = '100%';
        if (sidebarBadge) sidebarBadge.classList.remove('hidden');
        if (upgradeBtn) {
            // 會員＝可續訂（自到期日順延）——與論壇 premium 頁同一口徑，按鈕
            // 保持可用、只換標籤。不覆寫 className／innerHTML：.upgrade-premium-btn
            // class 必須留著（loadPremiumStatus 靠它重找按鈕），內層
            // data-price span 也要留著（月/年切換仍要更新價格）。2026-09-10
            // review：原實作整包覆寫＋free 分支不還原，過期會員按鈕永久卡死。
            const labelSpan = upgradeBtn.querySelector('span[data-i18n]');
            if (labelSpan) {
                if (!upgradeBtn.dataset.origI18n) {
                    upgradeBtn.dataset.origI18n = labelSpan.getAttribute('data-i18n') || '';
                }
                labelSpan.setAttribute('data-role', 'premiumCtaLabel');
                labelSpan.removeAttribute('data-i18n');
                labelSpan.textContent = window.I18n?.t('premium.renewCta') || 'Renew Premium';
            }
            upgradeBtn.disabled = false;
            upgradeBtn.classList.remove('bg-success', 'cursor-default');
        }
    } else {
        statusBadge.innerHTML = `
            <i data-lucide="user" class="w-3 h-3"></i>
            <span class="font-bold text-textMuted">${window.I18n?.t('auth.freeMember') || 'Free Member'}</span>
        `;
        statusBadge.className =
            'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-surfaceHighlight text-textMuted shrink-0';
        statusBadge.style.flex = '0 0 auto';
        statusBadge.style.maxWidth = '100%';
        if (sidebarBadge) sidebarBadge.classList.add('hidden');
        if (upgradeBtn) {
            // 還原升級按鈕（快取 premium→API 刷新 free 的情境——原本按鈕
            // 停留在 disabled「Already Premium Member」直到重整頁面）
            upgradeBtn.disabled = false;
            const labelSpan = upgradeBtn.querySelector('[data-role="premiumCtaLabel"]');
            if (labelSpan && upgradeBtn.dataset.origI18n) {
                labelSpan.setAttribute('data-i18n', upgradeBtn.dataset.origI18n);
                labelSpan.removeAttribute('data-role');
                labelSpan.textContent = window.I18n?.t(upgradeBtn.dataset.origI18n) || labelSpan.textContent;
            }
        }
    }
    AppUtils.refreshIcons();
}

// 載入 Premium 會員狀態（即時顯示快取值，後台更新精確到期日）
// 切到設定頁時 _updateUI 與 spa 的 settingsInits 會同時各叫一次，各打一次
// /api/premium/status（2026-09-26 量測）。{ shared: true } 的顯示用呼叫共用進行中的那一輪；
// 其他呼叫（付款完成後）遇到進行中的會在跑完後再補抓一次，保證拿到付款後的狀態。
let _premiumStatusRun = null;
let _premiumStatusAgain = false;
function loadPremiumStatus(opts) {
    if (_premiumStatusRun) {
        if (!(opts && opts.shared)) _premiumStatusAgain = true;
        return _premiumStatusRun;
    }
    _premiumStatusRun = (async () => {
        do {
            _premiumStatusAgain = false;
            await loadPremiumStatusOnce();
        } while (_premiumStatusAgain);
    })().finally(() => {
        _premiumStatusRun = null;
    });
    return _premiumStatusRun;
}

async function loadPremiumStatusOnce() {
    const statusBadge = document.getElementById('premium-status-badge');
    const upgradeBtn = document.querySelector('.upgrade-premium-btn');

    if (!statusBadge) return;

    if (!AuthManager.currentUser) {
        const sidebarBadge = document.getElementById('sidebar-premium-badge');
        if (sidebarBadge) sidebarBadge.classList.add('hidden');
        statusBadge.innerHTML = `<i data-lucide="x-circle" class="w-3 h-3"></i> ${window.I18n?.t('login.loginRequired') || 'Not logged in'}`;
        statusBadge.className =
            'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-surfaceHighlight text-textMuted shrink-0';
        if (upgradeBtn) upgradeBtn.disabled = true;
        AppUtils.refreshIcons();
        return;
    }

    // ── 即時顯示：從 currentUser 快取直接讀取，無需等待 API ──
    // membership_expires_at 一併帶入——原本快取首繪只有「Premium Member」
    // 沒有到期日（2026-09-10 DANNY：會員看到付款要求卻查不到自己到期日）。
    const cachedTier = AuthManager.currentUser.membership_tier || 'free';
    _applyPremiumBadgeUI(
        statusBadge,
        upgradeBtn,
        cachedTier === 'premium',
        AuthManager.currentUser.membership_expires_at || null
    );

    // ── 後台更新：呼叫 API 取得精確到期日 ──
    try {
        const result = await AppAPI.get('/api/premium/status');
        const membership = result.membership;
        // Re-apply with accurate expiry date from API
        _applyPremiumBadgeUI(statusBadge, upgradeBtn, membership.is_premium, membership.expires_at);
    } catch (e) {
        // Cached display already shown — silently ignore API errors
        console.warn('loadPremiumStatus API error (cached display retained):', e);
    }
}

// 處理 Premium 會員升級按鈕
async function handleUpgradeToPremium() {
    if (typeof upgradeToPremium === 'function') {
        await upgradeToPremium();
        // 升級後重新載入狀態
        setTimeout(loadPremiumStatus, 2000);
    } else {
        showToast(window.I18n.t('auth.premiumNotLoaded'), 'error');
    }
}

// 暴露全域
window.AuthManager = AuthManager;

function handleLogout() {
    AuthManager.logout();
}
window.handleLogout = handleLogout;

function initializeAuth() {
    return AuthManager.init();
}
window.initializeAuth = initializeAuth;
window.getWalletStatus = getWalletStatus;
window.loadSettingsWalletStatus = loadSettingsWalletStatus;
window.loadPremiumStatus = loadPremiumStatus;
// 登入成功（EVM／Telegram／Google 都發 auth-success）＝身分變了 → 重跑一次 init。
// evm-auth._applyEvmSession 只寫 currentUser、不呼叫 _updateUI：訪客橫幅、
// .auth-only、側欄名稱、auth:ready（NotificationService／toolSettings）都靠這一輪。
window.addEventListener('auth-success', () => {
    AuthManager.resetInit();
    AuthManager.init().catch((e) => {
        DebugLog.warn('auth.reinitAfterLoginFailed', { error: e?.message });
    });
});
// 綁定付款錢包成功（evm-auth safeEvmBind）→ Settings 錢包卡片立刻重繪
window.addEventListener('evm:wallet-bound', () => {
    loadSettingsWalletStatus().catch(() => { /* 重繪失敗不影響綁定 */ });
});
window.handleUpgradeToPremium = handleUpgradeToPremium;

// ========================================
// 訪客模式逃生門（2026-08-20 DANNY）
// login modal 原本只有連錢包一條路——訪客被彈窗（切受保護 tab、banner
// Connect 等）後無法離開，只能重整頁面。此鈕關閉 modal、回到 chat 訪客
// 體驗（訪客唯一可用 tab），並提示访客权益。
// ========================================
window.continueAsGuest = function continueAsGuest() {
    const modal = document.getElementById('login-modal');
    if (modal) modal.classList.add('hidden');
    if (window.AppStore) AppStore.set('forceGuestLandingTab', false);
    // 訪客僅能用 chat tab：從受保護 tab 觸發的 modal 由此導回
    if (typeof switchTab === 'function') switchTab('chat');
    // 消耗 back-handling 推入的 history entry（見 _setupLoginModalBackHandling）
    if (window.history.state && window.history.state.cmLoginModal) {
        window.history.back();
    }
    if (typeof showToast === 'function') {
        showToast(
            window.I18n && window.I18n.isReady && window.I18n.isReady()
                ? window.I18n.t('login.guestToast')
                : 'Guest mode — connect a wallet to unlock everything',
            'info'
        );
    }
};

// ========================================
// Login modal 的手機返回鍵處理（2026-08-21，DANNY 回報）
// modal 打開時 push 一個 history entry：手機返回鍵優先「關 modal」
// 而不是跳出整個網站（原本行為：back → 導離站 → 再回來落在連線流程）。
// 用 MutationObserver 攔截所有開啟路徑（spa.js 守門/CTA/banner…各自
// classList.remove('hidden')，逐一改呼叫端易漏）。
// ========================================
window._setupLoginModalBackHandling = function _setupLoginModalBackHandling() {
    const modal = document.getElementById('login-modal');
    if (!modal || modal._backHandlingReady) return;
    modal._backHandlingReady = true;

    let pushed = false;
    const observer = new MutationObserver(() => {
        const shown = !modal.classList.contains('hidden');
        if (shown && !pushed) {
            pushed = true;
            try {
                window.history.pushState({ cmLoginModal: true }, '', location.href);
            } catch (e) { /* ignore */ }
        } else if (!shown && pushed && !(window.history.state && window.history.state.cmLoginModal)) {
            // 被其他路徑關閉且 history 已不在 modal entry（例如登入成功換頁）
            pushed = false;
        }
    });
    observer.observe(modal, { attributes: true, attributeFilter: ['class'] });

    window.addEventListener('popstate', () => {
        if (!modal.classList.contains('hidden')) {
            // 返回鍵的意圖是「關掉 modal」，不是離開網站
            modal.classList.add('hidden');
            pushed = false;
        } else {
            pushed = false;
        }
    });
};

// ========================================
// Dev Mode: Switch User (Test Mode Only)
// ========================================
async function handleDevSwitchUser(userId) {
    window.APP_CONFIG?.DEBUG_MODE && console.log(`[Dev] Switching to user: ${userId}`);

    try {
        // 使用 dev-login endpoint 並指定用戶 ID
        const result = await AppAPI.post('/api/user/dev-login', { user_id: userId }, { _skipAuthGate: true });

        // 更新 AuthManager
        AuthManager.currentUser = {
            uid: result.user.uid,
            user_id: result.user.uid,
            username: result.user.username,
                accessTokenExpiry: Date.now() + AuthManager.TOKEN_EXPIRY_MS,
            authMethod: result.user.authMethod,
        };

        AuthManager._saveUserSession();

        if (typeof showToast === 'function') {
            showToast(window.I18n.t('auth.switchedTo', { user: result.user.username }), 'success');
        }

        // 重新載入頁面以更新所有狀態
        setTimeout(() => window.location.reload(), 500);
    } catch (e) {
        console.error('Dev switch user error:', e);
        if (typeof showToast === 'function') {
            showToast(window.I18n.t('auth.switchUserFailed'), 'error');
        }
    }
}
// click-delegator（classic script）靠全域名稱呼叫；少這行設定頁的切換鈕按了沒反應
window.handleDevSwitchUser = handleDevSwitchUser;

export {
    DebugLog,
    AuthEnvironment,
    AuthManager,
    handleLogout,
    initializeAuth,
    getWalletStatus,
    loadSettingsWalletStatus,
    loadPremiumStatus,
    handleUpgradeToPremium,
    handleDevSwitchUser,
    _applyPremiumBadgeUI,
};
