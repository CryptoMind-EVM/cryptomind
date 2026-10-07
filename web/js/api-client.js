/**
 * Centralized API Client
 *
 * Single source of truth for all API communication.
 * Provides auth headers, error handling, timeout, and retry.
 *
 * Usage:
 *   const data = await AppAPI.get('/api/forum/boards');
 *   const result = await AppAPI.post('/api/forum/posts', { title: '...' });
 *   const result = await AppAPI.delete('/api/alerts/123');
 *
 * 錯誤的 .message 是給程式比對的原文（premium.js／usdc-pay.js／filter.js 都靠它），不要直接貼給
 * 使用者；要顯示請過 userFacingMessage(err, { fallbackKey })（error-message.js，main.js 載入並掛在
 * window.ErrorMessage 給 classic script 用）。
 * 這支刻意不 import 任何模組：tests/js 的 node 閘門把它的原始碼直接包成 data: URL 載入。
 */
var DEFAULT_TIMEOUT = 15000;
var MAX_RETRIES = 3;
var RETRY_DELAY = 1000;

// 「ok + application/json + 空 body（或切一半的壞 JSON）」＝回應在傳輸途中
// 被截斷（FastAPI 的 response_model 不可能產出空 200）。實測案例
// （2026-09-09）：Service Worker 更新（skipWaiting+clientsClaim、precache
// 全量重抓）在頁面運行中接管，落在窗口內的 API 回應被截斷——表現成
// 「第一次點失敗、第二次正常」，api-client 的 response.json() 直接炸出英文
// 技術名詞 toast。冪等請求與 retry-safe POST（再產一張 token 無副作用）
// 自動重試一次；其餘丟在地化友善錯誤。console.error 會被 error-boundary
// 批量上報，補足「發生當下拿不到任何遙測」的缺口。
var TRUNCATION_RETRY_SAFE_POST = [
    '/api/line/link-token',
    '/api/telegram/link-token',
];

// 401 對這些端點＝憑證被拒（簽章錯／nonce 過期），不是 session 過期——
// 沒有 session 可刷新。走了 refresh→retry 不僅白打兩個請求，失敗後還會把
// 伺服器的真實 detail 換成「登入已過期，請重新整理頁面」，對剛要登入的
// 人是誤導（2026-09-10 iOS 實測：正確指引是再試一次，不是整理頁面）。
var AUTH_401_NO_REFRESH_URLS = [
    '/api/user/evm-login',
    '/api/user/dev-login',
    // evm-nonce 是登入前哨：401 = nonce 問題，非 session 過期
    '/api/user/evm-nonce',
];

function _isLoginEndpoint(url) {
    return AUTH_401_NO_REFRESH_URLS.some(function (s) {
        return url.indexOf(s) !== -1;
    });
}

function _truncationError() {
    var err = new Error(
        window.I18n
            ? window.I18n.t('common.networkUnstable', 'Network hiccup — please try again')
            : 'Network hiccup — please try again'
    );
    err.truncated = true;
    return err;
}

async function _handleTruncatedResponse(method, url, options, response) {
    console.error('[api-client] truncated response', {
        url: url,
        method: method,
        status: response.status,
        contentLength: response.headers.get('content-length'),
        swControlled: !!(
            navigator.serviceWorker && navigator.serviceWorker.controller
        ),
    });
    var retryable =
        method === 'GET' ||
        method === 'HEAD' ||
        (method === 'POST' && TRUNCATION_RETRY_SAFE_POST.indexOf(url) !== -1);
    if (retryable && !options._truncationRetried) {
        var retryOptions = Object.assign({}, options, { _truncationRetried: true });
        return await request(method, url, retryOptions);
    }
    var err = _truncationError();
    err.status = response.status;
    throw err;
}

function buildHeaders(customHeaders) {
    var headers = {
        'Content-Type': 'application/json',
    };
    // 平台情境（web／tma／baseapp／play）：後端能力表依此擋付款軌道（core/platform.py）
    try {
        if (window.CMPlatform && typeof window.CMPlatform.get === 'function') {
            headers['X-Platform'] = window.CMPlatform.get();
        }
    } catch (e) { /* ignore */ }
    if (customHeaders) {
        Object.keys(customHeaders).forEach(function (key) {
            headers[key] = customHeaders[key];
        });
    }
    return headers;
}

// JWT lives in an httpOnly cookie; the frontend can only know whether a
// session user exists, not read the token itself.
function hasSession() {
    if (typeof AuthManager !== 'undefined' && AuthManager.currentUser) {
        var user = AuthManager.currentUser;
        return !!(user.user_id || user.uid);
    }
    return false;
}

function sleep(ms) {
    return new Promise(function (resolve) {
        setTimeout(resolve, ms);
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Auth gate — 根治「refresh 成功還 401」的時序競態
//
// 問題：AuthManager.init() 是 async（需 network round-trip restore/refresh），
// 但頁面上的業務請求（如 /api/user/api-keys）常在 DOMContentLoaded /
// setTimeout 搶跑，帶著舊的/過期的 cookie 出門 → 401。即使 AppAPI 有
// 401→refresh→retry 鏈，那支搶跑的請求會在 refresh 完成前就送出。
//
// 根治：在 request() 最前面 ensureAuthReady()，等待 init() 完成才放行。
// init 是冪等的（_initPromise 去重），所有走 AppAPI 的請求自動繼承此保證，
// 不需每個模組記得 await auth:ready。
//
// opt-out（避免死鎖）：init 鏈上的 API 呼叫（/api/user/me、/api/config、
// /api/user/dev-login）必須傳 _skipAuthGate: true；refresh endpoint 自動 skip。
// ─────────────────────────────────────────────────────────────────────────────
async function ensureAuthReady(options, url) {
    // opt-out：明確 skip flag（init 內部呼叫用）
    if (options && options._skipAuthGate) return;
    // opt-out：refresh endpoint 本身（backendTokenRefresh 打它，自動 skip 避免死鎖）
    if (url && url.indexOf('/api/user/refresh') !== -1) return;
    if (typeof AuthManager !== 'undefined' && typeof AuthManager.init === 'function') {
        try {
            await AuthManager.init();
        } catch (e) {
            // init 失敗不擋請求，讓既有的 401→refresh→retry 鏈處理
        }
    }
}

function parseErrorResponse(response) {
    return response.text().then(function (text) {
        try {
            var json = JSON.parse(text);
            if (typeof json.detail === 'string') {
                return json.detail;
            }
            if (Array.isArray(json.detail)) {
                return json.detail
                    .map(function (e) {
                        return (e.loc ? e.loc.join('.') + ': ' : '') + e.msg;
                    })
                    .join('\n');
            }
            if (json.message) {
                return json.message;
            }
            return JSON.stringify(json);
        } catch (e) {
            return 'Status ' + response.status + ': ' + response.statusText;
        }
    });
}

async function request(method, url, options) {
    options = options || {};
    var timeout = options.timeout || DEFAULT_TIMEOUT;
    var retries = options.retries !== undefined ? options.retries : MAX_RETRIES;
    var isIdempotent = method === 'GET' || method === 'HEAD' || method === 'OPTIONS';
    if (!isIdempotent) retries = 0;
    var body = options.body;
    var customHeaders = options.headers;
    var noContentType = options.noContentType || false;
    var lastError = null;

    var headers = buildHeaders(customHeaders);
    if (noContentType) {
        delete headers['Content-Type'];
    }

    // Auth gate：確保 auth 初始化完成後才送出請求（根治時序競態 401）
    await ensureAuthReady(options, url);

    for (var attempt = 0; attempt <= retries; attempt++) {
        try {
            var controller = new AbortController();
            var timer = setTimeout(function () {
                controller.abort();
            }, timeout);

            var fetchOptions = {
                method: method,
                headers: headers,
                signal: controller.signal,
                credentials: 'include',
            };
            if (body !== undefined && body !== null) {
                fetchOptions.body = typeof body === 'string' ? body : JSON.stringify(body);
            }

            var response = await fetch(url, fetchOptions);
            clearTimeout(timer);

            if (!response.ok) {
                var errorMsg = await parseErrorResponse(response);
                lastError = new Error(errorMsg);
                lastError.status = response.status;

                // Auto-refresh on 401: skip for /api/user/refresh itself to avoid deadlock;
                // login endpoints 401 = 憑證被拒，直出伺服器 detail（見上方清單註記）
                if (
                    response.status === 401 &&
                    !_isLoginEndpoint(url) &&
                    !options._authRetried &&
                    !url.includes('/api/user/refresh')
                ) {
                    if (typeof AuthManager !== 'undefined' && typeof AuthManager.backendTokenRefresh === 'function') {
                        try {
                            var refreshResult = await AuthManager.backendTokenRefresh();
                            if (refreshResult && refreshResult.success) {
                                var retryOptions = Object.assign({}, options, {
                                    _authRetried: true,
                                    _skipAuthGate: true,
                                });
                                return await request(method, url, retryOptions);
                            }
                        } catch (_refreshErr) {}
                    }
                    if (
                        !options._testModeRecovered &&
                        typeof AuthManager !== 'undefined' &&
                        typeof AuthManager.recoverTestModeSession === 'function'
                    ) {
                        try {
                            var recoveryResult = await AuthManager.recoverTestModeSession();
                            if (recoveryResult && recoveryResult.success) {
                                var recoveredRetryOptions = Object.assign({}, options, {
                                    _authRetried: true,
                                    _testModeRecovered: true,
                                    _skipAuthGate: true,
                                });
                                return await request(method, url, recoveredRetryOptions);
                            }
                        } catch (_recoveryErr) {}
                    }
                    // refresh 也失敗＝session 疑似失效（例如另一個分頁登出）。init 每頁只跑
                    // 一次，這裡放掉記住的結果，讓下一支請求的 auth gate 重驗（/me 確認失效
                    // 才清 session 並重載）。init 鏈自己的請求（_skipAuthGate）與訪客不觸發。
                    if (
                        !options._skipAuthGate &&
                        hasSession() &&
                        typeof AuthManager.resetInit === 'function'
                    ) {
                        AuthManager.resetInit();
                    }
                    // Refresh failed — make error friendlier
                    lastError = new Error(window.I18n ? window.I18n.t('auth.tokenExpired') : 'Login expired, please refresh the page');
                    lastError.status = 401;
                    throw lastError;
                }

                if (
                    response.status === 401 ||
                    response.status === 403 ||
                    response.status === 422
                ) {
                    throw lastError;
                }

                if (attempt < retries) {
                    await sleep(RETRY_DELAY * (attempt + 1));
                    continue;
                }
                throw lastError;
            }

            var contentType = response.headers.get('content-type') || '';
            if (contentType.includes('application/json')) {
                var text = await response.text();
                if (!text) {
                    return await _handleTruncatedResponse(method, url, options, response);
                }
                // body 切一半＝截斷的另一種形態——parse 失敗同樣進防禦（重試或在地化錯誤）
                try {
                    return JSON.parse(text);
                } catch (_parseErr) {
                    return await _handleTruncatedResponse(method, url, options, response);
                }
            }
            return await response.text();
        } catch (err) {
            // 截斷重試已在 _handleTruncatedResponse 內完成（單次）——不得再
            // 進外層 attempt 迴圈：GET（retries=3）會相乘成最壞 20 次請求／
            // 約 30 秒卡死，還可能撞 rate limit（code review 2026-09-10）。
            if (err && err.truncated) throw err;
            lastError = err;
            if (err.name === 'AbortError') {
                lastError = new Error('Request timeout (' + timeout + 'ms)');
                lastError.status = 0;
                // .message 維持英文（有地方靠它比對）；顯示層看這個旗標改講在地化的「請求逾時」
                lastError.timeout = true;
            }
            if (
                err.status === 401 ||
                err.status === 403 ||
                err.status === 422
            ) {
                throw err;
            }
            if (attempt < retries) {
                await sleep(RETRY_DELAY * (attempt + 1));
                continue;
            }
        }
    }
    throw lastError;
}

const AppAPI = {
    get: function (url, options) {
        return request('GET', url, options);
    },
    post: function (url, body, options) {
        options = options || {};
        options.body = body;
        return request('POST', url, options);
    },
    put: function (url, body, options) {
        options = options || {};
        options.body = body;
        return request('PUT', url, options);
    },
    patch: function (url, body, options) {
        options = options || {};
        options.body = body;
        return request('PATCH', url, options);
    },
    delete: function (url, options) {
        return request('DELETE', url, options);
    },
    hasSession: hasSession,
    buildHeaders: buildHeaders,
};

// ─────────────────────────────────────────────────────────────────────────────
// 共用 config 快取 — 解決首頁載入時 /api/config 被多個模組重複呼叫的效能問題。
// 多個呼叫者共享同一支 in-flight request（不論呼叫順序都只打一次網路）；
// TTL 內重複呼叫直接回快取。force=true 強制重取（設定面板儲存後用）。
// ─────────────────────────────────────────────────────────────────────────────
var _appConfigCache = { ts: 0, payload: null };
var _appConfigInFlight = null;
var _APP_CONFIG_TTL_MS = 30000;

function getAppConfig(force) {
    var now = Date.now();
    if (!force && _appConfigCache.payload && now - _appConfigCache.ts < _APP_CONFIG_TTL_MS) {
        return Promise.resolve(_appConfigCache.payload);
    }
    if (!force && _appConfigInFlight) {
        return _appConfigInFlight;
    }
    _appConfigInFlight = AppAPI.get('/api/config', { _skipAuthGate: true })
        .then(function (cfg) {
            _appConfigCache = { ts: Date.now(), payload: cfg };
            return cfg;
        })
        .finally(function () {
            _appConfigInFlight = null;
        });
    return _appConfigInFlight;
}

var _modelConfigCache = { ts: 0, payload: null };
var _modelConfigInFlight = null;

function getModelConfig(force) {
    var now = Date.now();
    if (!force && _modelConfigCache.payload && now - _modelConfigCache.ts < _APP_CONFIG_TTL_MS) {
        return Promise.resolve(_modelConfigCache.payload);
    }
    if (!force && _modelConfigInFlight) {
        return _modelConfigInFlight;
    }
    _modelConfigInFlight = AppAPI.get('/api/model-config', { _skipAuthGate: true })
        .then(function (data) {
            var cfg = data?.model_config || data;
            _modelConfigCache = { ts: Date.now(), payload: cfg };
            return cfg;
        })
        .finally(function () {
            _modelConfigInFlight = null;
        });
    return _modelConfigInFlight;
}

// GET /api/premium/pricing：論壇價格（forum-config.js）與會員方案／付款管道（premium.js）
// 同一頁各抓一次（2026-09-26 量測：premium 頁 2 次）。並發共用、5 秒內沿用——premium.js
// 原本就把結果存在實例上整頁沿用，這裡的沿用時間比它短，付款時的價格不會因此更舊。
// 失敗不沿用（下一次重抓）。
var _PRICING_REUSE_MS = 5000;
var _pricingCache = { ts: 0, payload: null };
var _pricingInFlight = null;

function getPremiumPricing() {
    if (_pricingCache.payload && Date.now() - _pricingCache.ts < _PRICING_REUSE_MS) {
        return Promise.resolve(_pricingCache.payload);
    }
    if (_pricingInFlight) return _pricingInFlight;
    _pricingInFlight = AppAPI.get('/api/premium/pricing')
        .then(function (data) {
            _pricingCache = { ts: Date.now(), payload: data };
            return data;
        })
        .finally(function () {
            _pricingInFlight = null;
        });
    return _pricingInFlight;
}

AppAPI.getPremiumPricing = getPremiumPricing;
AppAPI.getAppConfig = getAppConfig;
AppAPI.getModelConfig = getModelConfig;

window.AppAPI = AppAPI;
export { AppAPI };
