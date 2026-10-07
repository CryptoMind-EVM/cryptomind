// error-message.js — 錯誤的「顯示層」文案（2026-10-06）
//
// 為什麼有這支：很多 catch 直接把 e.message 貼進 toast／alert／innerHTML。使用者（手機內建瀏覽器為主）
// 就看到原始英文：'Failed to fetch'、'Load failed'、'Status 502: Bad Gateway'、'body.x: field required'、
// 'pin_limit_reached'、錢包的 'User denied message signature'……
// 這些字串有的是程式比對的依據（premium.js／usdc-pay.js／filter.js 靠 AppAPI 錯誤的 .message 做 regex），
// 所以 **不改 .message**，只在「要給人看的那一刻」多過一層：userFacingMessage(err, opts)。
//
// 規則（由上到下，先中先贏）：
//   錢包取消（4001／ACTION_REJECTED／User rejected／denied／cancel…）→ error.cancelled（呼叫端通常先用
//                                  isUserRejection 判斷、改顯示中性提示，不要當錯誤）
//   fetch 網路層失敗／截斷回應     → common.networkUnstable
//   逾時（api-client 的 timeout、AbortError、504）→ error.timeout
//   原生程式錯誤／瀏覽器 DOMException → opts.fallbackKey 或 app.unexpectedError
//   HTTP／一般 Error：訊息「可以給人看」就原文；否則依狀態碼給通用訊息：
//     可以給人看＝不是 sentinel code（pin_limit_reached）、不是技術字串（Status 502: …／原始 JSON／
//     body.x: …／Not Found）、而且在非英文介面時不是純英文（後端 friends.py 這類 detail 是英文）。
//   sentinel code／已知英文 detail 有對照表，對得到就講人話。
//
// 回傳的是純文字（不 escape）——innerHTML 的呼叫端照舊自己 escape。

import { NATIVE_ERROR_NAMES, NETWORK_MESSAGE } from './rejection-filter.js';

// 後端 sentinel code → i18n key（detail 是 snake_case 識別字，不是給人看的）。
// 值優先重用既有 key；沒有的才加在 error.codes.*。
const CODE_KEYS = {
    pin_limit_reached: 'friends.pin.limit',
    quota_exhausted: 'assistant.errors.quota_exhausted',
    no_model: 'assistant.errors.no_model',
    assistant_busy_self: 'assistant.errors.assistant_busy_self',
    already_reported: 'messages.report.already',
    recall_window_expired: 'messages.recallExpired',
    database_unavailable: 'error.serverError',
    database_initializing: 'error.serverError',
    invalid_query: 'error.badRequest',
    unknown_error: 'app.unexpectedError',
    pro_required: 'error.codes.pro_required',
    content_blocked: 'error.codes.content_blocked',
    not_found: 'error.codes.not_found',
    blocked: 'error.codes.blocked',
};

// 後端寫死的英文 detail（api/routers/friends.py、forum/posts.py、帳號停權）→ i18n key。
// 比對時小寫、去掉結尾標點。對不到的英文在非英文介面一律當外文處理（走通用訊息）。
const KNOWN_DETAIL_KEYS = {
    'you cannot add yourself as a friend': 'error.details.cannotAddSelf',
    'you are already friends': 'error.details.alreadyFriends',
    'a friend request is already pending': 'error.details.requestPending',
    'cannot send a request to this user': 'error.details.cannotSendRequest',
    'you have blocked this user. please unblock them first': 'error.details.youBlockedUser',
    'you cannot block yourself': 'error.details.cannotBlockSelf',
    'friend request not found': 'error.details.requestNotFound',
    'you are not friends': 'error.details.notFriends',
    'this user is not blocked': 'error.details.notBlocked',
    'user not found': 'error.details.userNotFound',
    'target user not found': 'error.details.userNotFound',
    'account has been suspended': 'error.details.accountSuspended',
    'post not found': 'error.details.postNotFound',
    'post has been hidden': 'error.details.postHidden',
    'this google account is already linked to another cryptomind account': 'error.details.googleAlreadyLinked',
    'this account signs in with google; link a wallet before unlinking': 'error.details.googleLinkWalletFirst',
    'google email is not verified': 'error.details.googleEmailUnverified',
};
// 帶變數的英文 detail（wallet_monitor.py：Invalid address for chain 'ton'／Maximum 10 monitored wallets allowed）
const KNOWN_DETAIL_PATTERNS = [
    [/^invalid address for chain\b/i, 'error.details.invalidAddress'],
    [/^maximum \d+ monitored wallets allowed/i, 'error.details.walletLimitReached'],
];

// 通用 key（給 userFacingMessage 內部用；集中列出讓測試能驗四語都有）
const KEY = {
    cancelled: 'error.cancelled',
    network: 'common.networkUnstable',
    timeout: 'error.timeout',
    unexpected: 'app.unexpectedError',
    badRequest: 'error.badRequest',
    requestFailed: 'error.requestFailed',
    serverError: 'error.serverError',
    rateLimit: 'error.rateLimit',
    unauthorized: 'error.unauthorized',
    forbidden: 'error.forbidden',
    notFound: 'error.notFound',
};

export const I18N_KEYS_USED = Object.freeze([
    ...new Set([...Object.values(KEY), ...Object.values(CODE_KEYS), ...Object.values(KNOWN_DETAIL_KEYS), ...KNOWN_DETAIL_PATTERNS.map(([, key]) => key)]),
]);

// HTTP 狀態文字（FastAPI／反向代理的預設 detail）——不是給人看的
const HTTP_PHRASES = new Set([
    'bad request',
    'unauthorized',
    'forbidden',
    'not found',
    'method not allowed',
    'request timeout',
    'conflict',
    'gone',
    'payload too large',
    'unprocessable entity',
    'too many requests',
    'internal server error',
    'bad gateway',
    'service unavailable',
    'gateway timeout',
    'not authenticated',
    'validation error',
    'network error',
]);

// 4001＝EIP-1193 使用者拒絕；ACTION_REJECTED＝ethers v5.7／v6。WalletConnect v2 的 5000 不單獨採信
// （別的函式庫也拿 5000 當一般錯誤碼），它的 'User rejected.' 靠文字比對。
const REJECTION_CODES = new Set([4001, 'ACTION_REJECTED', 'USER_REJECTED']);
// 文字比對要偏錢包措辭：'Permission denied'、'Order cancelled'、'Failed to cancel subscription'、
// 'Request rejected due to rate limit' 是真的失敗，不能被吃成「已取消」。
const REJECTION_TEXT = [
    // User rejected the request. / User denied message signature / user rejected signing (…) / The user cancelled…
    /\buser (rejected|denied|declined|disapproved|refused|cancell?ed)\b/i,
    // Cancelled by user
    /\b(rejected|denied|declined|cancell?ed) by (the )?user\b/i,
    // 整句就是一個動詞（imToken 的 'cancel'）或「Request rejected」
    /^((the )?(request|signature|transaction|action|connection)( was)? )?(rejected|denied|declined|cancel|cancell?ed)\.?$/i,
];
const isRejectionText = (text) => REJECTION_TEXT.some((re) => re.test(String(text).trim()));
const TIMEOUT_MESSAGE = /^request timeout\b/i;
// 'Status 502: Bad Gateway'（api-client 非 JSON 回應）、'HTTP 502'、'nonce HTTP 502'
const STATUS_TEXT = /(^|\s)(status|http)\s*\d{3}\b/i;
// 'body.x: field required'（api-client 組的 422 訊息）
const VALIDATION_TEXT = /^(body|query|path|header|cookie)(\.|\[|:)/i;
// snake_case／dotted 識別字，或單一小寫單字：後端 sentinel code，不是句子
const SENTINEL = /^(?:[A-Za-z][A-Za-z0-9]*(?:[_.-][A-Za-z0-9]+)+|[a-z][a-z0-9]*)$/;

function translate(key, fallback) {
    if (!key) return fallback;
    try {
        if (window.I18n && typeof window.I18n.t === 'function') {
            const v = window.I18n.t(key);
            if (typeof v === 'string' && v && v !== key) return v;
        }
    } catch (_e) {
        /* i18n 壞掉不得影響錯誤顯示 */
    }
    return fallback;
}

function currentLang() {
    try {
        return (window.I18n && window.I18n.getLanguage && window.I18n.getLanguage()) || 'en';
    } catch (_e) {
        return 'en';
    }
}

function messageOf(err) {
    if (typeof err === 'string') return err;
    if (typeof err?.message === 'string') return err.message;
    if (typeof err?.reason === 'string') return err.reason;
    return '';
}

function isDomException(err) {
    return typeof DOMException !== 'undefined' && err instanceof DOMException;
}

function httpStatus(err) {
    const s = err && err.status;
    return Number.isInteger(s) && s >= 400 ? s : 0;
}

/**
 * 使用者在錢包裡按了取消／拒絕。各家措辭不一：MetaMask 4001＋'User denied message signature'、
 * ethers v5.7 的 ACTION_REJECTED、WalletConnect v2 的 5000、imToken 只丟 'cancel'。
 * 後端回的 HTTP 錯誤（有 status）與瀏覽器 DOMException 不算。
 */
export function isUserRejection(err) {
    if (!err || httpStatus(err) || isDomException(err)) return false;
    if (typeof err === 'string') return isRejectionText(err);
    for (const e of [err, err.error, err.cause, err.info && err.info.error]) {
        if (e && REJECTION_CODES.has(e.code)) return true;
    }
    return [err.message, err.reason, err.shortMessage, err.error && err.error.message].some(
        (text) => typeof text === 'string' && isRejectionText(text)
    );
}

/** 錯誤分類：cancelled／network／timeout／http／native／plain／unknown */
export function errorKind(err) {
    if (!err) return 'unknown';
    if (isUserRejection(err)) return 'cancelled';
    if (err.truncated) return 'network';
    const msg = messageOf(err).trim();
    if (err.timeout === true || err.name === 'AbortError' || TIMEOUT_MESSAGE.test(msg)) return 'timeout';
    if (NETWORK_MESSAGE.test(msg)) return 'network';
    if (httpStatus(err)) return 'http';
    if (isDomException(err) || NATIVE_ERROR_NAMES.has(err.name)) return 'native';
    return 'plain';
}

function isTechnical(msg) {
    return (
        STATUS_TEXT.test(msg) ||
        VALIDATION_TEXT.test(msg) ||
        /^[[{]/.test(msg) ||
        /\[object \w+\]/.test(msg) ||
        /^\d+$/.test(msg) ||
        HTTP_PHRASES.has(msg.toLowerCase())
    );
}

// 含拉丁字母、又沒有任何中日韓／西里爾字元＝英文（或其他拉丁字母）句子；非英文介面看到會是外文。
// 不能用「純 ASCII」判斷：英文句子裡一個 — … ’ 就會讓它被當成在地化文字原樣顯示。
function isForeignText(msg) {
    return /[A-Za-z]/.test(msg) && !/[\u0400-\u04FF\u3000-\u9FFF\uAC00-\uD7AF\uFF00-\uFFEF]/.test(msg);
}

/** 訊息本身可以給人看就回原文（或對照表翻出的文案）；否則回空字串 */
function presentable(rawMessage, opts) {
    const msg = String(rawMessage || '').trim();
    if (!msg) return '';
    const lower = msg.toLowerCase();

    const mapKey =
        (opts.map && (opts.map[msg] || opts.map[lower])) ||
        KNOWN_DETAIL_KEYS[lower.replace(/[.!\s]+$/, '')] ||
        (KNOWN_DETAIL_PATTERNS.find(([re]) => re.test(msg)) || [])[1] ||
        (SENTINEL.test(msg) ? CODE_KEYS[lower] : '');
    if (mapKey) {
        const text = translate(mapKey, '');
        if (text) return text;
    }
    if (SENTINEL.test(msg) || isTechnical(msg)) return '';
    if (isForeignText(msg) && !/^en\b/i.test(currentLang())) return '';
    return msg;
}

/**
 * 訊息可以給人看就回原文，否則空字串（不含通用 fallback）。
 * 給「原文附在括號裡方便回報」這類只想要原文的呼叫端用（evm-auth 的登入失敗訊息）。
 */
export function presentableMessage(err, opts = {}) {
    const kind = errorKind(err);
    if (kind !== 'http' && kind !== 'plain') return '';
    return presentable(messageOf(err), opts);
}

function byStatus(status, opts, fallback) {
    const has = !!opts.fallbackKey;
    if (status === 408 || status === 504) return translate(KEY.timeout, 'The request timed out — please try again');
    if (status === 429) return translate(KEY.rateLimit, 'Too many requests. Please wait a moment.');
    if (status >= 500) return translate(KEY.serverError, 'Server error. Please try again later.');
    if (status === 422) return translate(KEY.badRequest, "Some of the input isn't valid — please check and try again");
    if (has) return fallback;
    if (status === 401) return translate(KEY.unauthorized, 'Please login to continue.');
    if (status === 403) return translate(KEY.forbidden, "You don't have permission to access this.");
    if (status === 404) return translate(KEY.notFound, 'Content not found.');
    return translate(KEY.requestFailed, "That didn't go through — please try again");
}

/**
 * 錯誤 → 給人看的訊息（永遠回非空字串）。
 * opts.fallbackKey／opts.fallback：呼叫情境的通用訊息（「儲存失敗」）；沒給就用 app.unexpectedError。
 * opts.map：這個呼叫點自己的 { 後端訊息或 code: i18n key } 對照，優先於內建對照表。
 */
export function userFacingMessage(err, opts = {}) {
    const fallback = translate(opts.fallbackKey, opts.fallback || '') || translate(KEY.unexpected, 'An unexpected error occurred');
    switch (errorKind(err)) {
        case 'cancelled':
            return translate(KEY.cancelled, 'Cancelled');
        case 'network':
            return translate(KEY.network, 'Network hiccup — please try again');
        case 'timeout':
            return translate(KEY.timeout, 'The request timed out — please try again');
        case 'http': {
            const text = presentable(messageOf(err), opts);
            return text || byStatus(httpStatus(err), opts, fallback);
        }
        case 'plain':
            return presentable(messageOf(err), opts) || fallback;
        default:
            return fallback;
    }
}

/**
 * raw fetch 的失敗回應 → 帶 status 的 Error（訊息格式同 api-client 的 parseErrorResponse）。
 * 以前各處自己 `res.json().catch(...)`：422 的 detail 陣列變 '[object Object]'，
 * 502 的 HTML 被 JSON.parse 炸成 SyntaxError。
 */
export async function errorFromResponse(response) {
    let message = '';
    try {
        const text = await response.text();
        try {
            const json = JSON.parse(text);
            if (typeof json.detail === 'string') message = json.detail;
            else if (Array.isArray(json.detail))
                message = json.detail.map((e) => (e && e.loc ? e.loc.join('.') + ': ' : '') + (e && e.msg)).join('\n');
            else if (typeof json.message === 'string') message = json.message;
        } catch (_parseErr) {
            /* 不是 JSON（反向代理的 HTML 錯誤頁） */
        }
    } catch (_readErr) {
        /* body 讀不到 */
    }
    const err = new Error(message || 'HTTP ' + response.status);
    err.status = response.status;
    return err;
}

// classic script（memory-manager／skill-manager／forum profile-page）不能 import——掛 window 給它們用
if (typeof window !== 'undefined') {
    window.ErrorMessage = { userFacingMessage, errorFromResponse, isUserRejection, errorKind, presentableMessage };
}
