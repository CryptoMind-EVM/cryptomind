// error-message.js：錯誤的「顯示層」文案（2026-10-06）。
// 要守的是：使用者在手機內建瀏覽器看到的不再是 'Failed to fetch'／'Status 502: Bad Gateway'／
// 'pin_limit_reached'／'body.x: field required' 這類原始英文，但後端本來就給人看的中文 detail 照樣顯示；
// 錢包取消（User rejected／denied／cancel／4001）一律認得出來；AppAPI 的 .message 不被改動（只是不顯示它）。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { loadModuleUrl } from './_load.mjs';

const LANGS = ['zh-TW', 'zh-CN', 'en', 'ru'];
const dicts = Object.fromEntries(
    LANGS.map((l) => [l, JSON.parse(readFileSync(new URL(`../../web/js/i18n/${l}.json`, import.meta.url), 'utf8'))])
);
const resolve = (dict, key) =>
    key.split('.').reduce((cur, part) => (cur && typeof cur === 'object' ? cur[part] : undefined), dict);

let lang = 'zh-TW';
globalThis.window = globalThis;
globalThis.I18n = {
    t: (key) => {
        const v = resolve(dicts[lang], key);
        return typeof v === 'string' ? v : key;
    },
    getLanguage: () => lang,
};
const T = (key) => resolve(dicts[lang], key);

const mod = await import(
    await loadModuleUrl(new URL('../../web/js/error-message.js', import.meta.url).pathname)
);
const { userFacingMessage, presentableMessage, isUserRejection, errorKind, errorFromResponse } = mod;

const http = (status, message) => Object.assign(new Error(message), { status });

// ── 網路層：原生 fetch 的 TypeError 不管瀏覽器措辭 ──
for (const msg of ['Failed to fetch', 'Load failed', 'NetworkError when attempting to fetch resource.', 'Network request failed']) {
    assert.equal(userFacingMessage(new TypeError(msg)), T('common.networkUnstable'), msg);
    assert.equal(errorKind(new TypeError(msg)), 'network');
}
// 截斷回應（api-client 已經是在地化訊息，但一樣歸網路不穩）
assert.equal(userFacingMessage(Object.assign(new Error('x'), { truncated: true, status: 200 })), T('common.networkUnstable'));

// ── 逾時：api-client 的 'Request timeout (15000ms)'（status=0）與 AbortError ──
assert.equal(userFacingMessage(Object.assign(new Error('Request timeout (15000ms)'), { status: 0 })), T('error.timeout'));
assert.equal(userFacingMessage(Object.assign(new Error('whatever'), { status: 0, timeout: true })), T('error.timeout'));
assert.equal(userFacingMessage(new DOMException('aborted', 'AbortError')), T('error.timeout'));
assert.equal(userFacingMessage(http(504, 'Gateway Timeout')), T('error.timeout'));

// ── HTTP：後端本來就給人看的 detail（中文）照顯示 ──
assert.equal(userFacingMessage(http(400, '今天的發文次數已用完')), '今天的發文次數已用完');
assert.equal(userFacingMessage(http(403, '只有作者可以編輯')), '只有作者可以編輯');
assert.equal(userFacingMessage(http(500, '伺服器忙碌中，請稍後')), '伺服器忙碌中，請稍後');
// 登入過期（api-client 自己換成在地化訊息）
assert.equal(userFacingMessage(http(401, T('auth.tokenExpired'))), T('auth.tokenExpired'));

// ── HTTP：英文 detail 在非英文介面不外露 ──
assert.equal(userFacingMessage(http(404, 'Something odd happened')), T('error.notFound'));
assert.equal(
    userFacingMessage(http(400, 'Something odd happened'), { fallbackKey: 'friends.sendRequestFailed' }),
    T('friends.sendRequestFailed')
);
assert.equal(userFacingMessage(http(400, 'Something odd happened')), T('error.requestFailed'));
assert.equal(userFacingMessage(http(403, 'Not authorized to do that')), T('error.forbidden'));
assert.equal(userFacingMessage(http(401, 'Invalid wallet signature')), T('error.unauthorized'));
assert.equal(
    userFacingMessage(http(401, 'Invalid wallet signature'), { fallbackKey: 'evmAuth.verifyFailed' }),
    T('evmAuth.verifyFailed')
);
// 5xx／429 不論 caller 有沒有 fallback，都講「稍後再試」類的訊息
assert.equal(userFacingMessage(http(500, 'Failed to send request, please try again later'), { fallbackKey: 'friends.sendRequestFailed' }), T('error.serverError'));
assert.equal(userFacingMessage(http(429, 'Too Many Requests')), T('error.rateLimit'));
assert.equal(userFacingMessage(http(429, 'Slow down please')), T('error.rateLimit'));
// 但 429 的中文 detail（例如「每日留言額度」）照顯示
assert.equal(userFacingMessage(http(429, '留言太快了，請 30 秒後再試')), '留言太快了，請 30 秒後再試');

// ── api-client 的技術性訊息：HTTP 狀態文字、原始 JSON、422 的 body.x ──
assert.equal(userFacingMessage(http(502, 'Status 502: Bad Gateway')), T('error.serverError'));
assert.equal(userFacingMessage(http(503, 'Status 503: ')), T('error.serverError'));
assert.equal(userFacingMessage(http(404, 'Status 404: Not Found')), T('error.notFound'));
assert.equal(userFacingMessage(http(404, 'Status 404: Not Found'), { fallbackKey: 'friends.getProfileFailed' }), T('friends.getProfileFailed'));
assert.equal(userFacingMessage(http(500, 'Internal Server Error')), T('error.serverError'));
assert.equal(userFacingMessage(http(500, '{"error":"boom","trace":"x"}')), T('error.serverError'));
assert.equal(userFacingMessage(http(400, '{"error":"boom"}'), { fallbackKey: 'friends.searchFailed' }), T('friends.searchFailed'));
assert.equal(userFacingMessage(http(422, 'body.display_name: field required\nbody.bio: too long')), T('error.badRequest'));
assert.equal(userFacingMessage(http(422, 'query.limit: Input should be a valid integer')), T('error.badRequest'));
assert.equal(userFacingMessage(http(500, 'HTTP 500')), T('error.serverError'));
assert.equal(userFacingMessage(http(500, '')), T('error.serverError'));

// ── 後端 sentinel code：對照表；沒對到的退回 fallback，不原樣露出 ──
assert.equal(userFacingMessage(http(400, 'pin_limit_reached')), T('friends.pin.limit'));
assert.equal(userFacingMessage(http(400, 'content_blocked')), T('error.codes.content_blocked'));
assert.equal(userFacingMessage(http(403, 'pro_required')), T('error.codes.pro_required'));
assert.equal(userFacingMessage(http(404, 'not_found')), T('error.codes.not_found'));
assert.equal(userFacingMessage(http(400, 'recall_window_expired')), T('messages.recallExpired'));
assert.equal(userFacingMessage(http(400, 'NOT_BOUND')), T('error.requestFailed'));
assert.equal(
    userFacingMessage(http(400, 'some_new_backend_code'), { fallbackKey: 'friends.blockFailed' }),
    T('friends.blockFailed')
);
// caller 自己的對照優先
assert.equal(
    userFacingMessage(http(400, 'some_new_backend_code'), { map: { some_new_backend_code: 'friends.pin.limit' } }),
    T('friends.pin.limit')
);
// 好友 API 的英文 detail（friends.py）有人話對照
assert.equal(userFacingMessage(http(400, 'You are already friends')), T('error.details.alreadyFriends'));
assert.equal(userFacingMessage(http(400, 'You have blocked this user. Please unblock them first')), T('error.details.youBlockedUser'));
assert.equal(userFacingMessage(http(404, 'Target user not found')), T('error.details.userNotFound'));
assert.equal(userFacingMessage(http(400, 'Friend request not found')), T('error.details.requestNotFound'));
assert.equal(userFacingMessage(http(403, 'Account has been suspended')), T('error.details.accountSuspended'));

// 英文句子裡有一個非 ASCII 標點（— … ’）仍是外文，不能被當成在地化文字原樣顯示
assert.equal(userFacingMessage(http(401, 'Nonce expired — please retry')), T('error.unauthorized'));
assert.equal(userFacingMessage(http(400, 'Unsupported address format (expect EVM 0x…)'), { fallbackKey: 'evmAuth.bindFailed' }), T('evmAuth.bindFailed'));
assert.equal(userFacingMessage(http(400, 'You can’t do that')), T('error.requestFailed'));
// 但有中日韓／西里爾字元的就是在地化文字
assert.equal(userFacingMessage(http(400, 'USDC 付款已被使用 — 請換一筆')), 'USDC 付款已被使用 — 請換一筆');
// Google／錢包監測的英文 detail（可據以行動）
assert.equal(userFacingMessage(http(409, 'This Google account is already linked to another CryptoMind account')), T('error.details.googleAlreadyLinked'));
assert.equal(userFacingMessage(http(400, 'This account signs in with Google; link a wallet before unlinking')), T('error.details.googleLinkWalletFirst'));
assert.equal(userFacingMessage(http(400, "Invalid address for chain 'ton'")), T('error.details.invalidAddress'));
assert.equal(userFacingMessage(http(400, 'Maximum 10 monitored wallets allowed')), T('error.details.walletLimitReached'));

// ── 英文介面：英文 detail 本來就是介面語言，照顯示；但 sentinel／技術字串仍不外露 ──
lang = 'en';
assert.equal(userFacingMessage(http(400, 'Daily post limit reached')), 'Daily post limit reached');
assert.equal(userFacingMessage(http(400, 'pin_limit_reached')), T('friends.pin.limit'));
assert.equal(userFacingMessage(http(502, 'Status 502: Bad Gateway')), T('error.serverError'));
assert.equal(userFacingMessage(http(422, 'body.x: field required')), T('error.badRequest'));
assert.equal(userFacingMessage(new TypeError('Failed to fetch')), T('common.networkUnstable'));
assert.equal(userFacingMessage(http(400, 'You are already friends')), T('error.details.alreadyFriends'));
// ru 介面：英文 detail 一樣視為外文
lang = 'ru';
assert.equal(userFacingMessage(http(404, 'Post not found')), T('error.details.postNotFound'));
assert.equal(userFacingMessage(http(400, 'Something odd happened')), T('error.requestFailed'));
lang = 'zh-TW';

// ── 原生程式錯誤／瀏覽器 DOMException：訊息是給工程師看的 ──
assert.equal(userFacingMessage(new TypeError("Cannot read properties of undefined (reading 'x')")), T('app.unexpectedError'));
assert.equal(
    userFacingMessage(new TypeError("Cannot read properties of undefined (reading 'x')"), { fallbackKey: 'friends.loadFailed' }),
    T('friends.loadFailed')
);
assert.equal(userFacingMessage(new ReferenceError('foo is not defined')), T('app.unexpectedError'));
assert.equal(userFacingMessage(new SyntaxError("Unexpected token '<', \"<html>\" is not valid JSON")), T('app.unexpectedError'));
assert.equal(userFacingMessage(new DOMException('Write permission denied.', 'NotAllowedError')), T('app.unexpectedError'));

// ── 一般 Error：程式自己丟的在地化訊息照顯示；外文（錢包 SDK 的英文）不外露 ──
assert.equal(userFacingMessage(new Error('簽名逾時——請再按一次')), '簽名逾時——請再按一次');
assert.equal(userFacingMessage(new Error('Connection request reset. Please try again.')), T('app.unexpectedError'));
assert.equal(
    userFacingMessage(new Error('Connection request reset. Please try again.'), { fallbackKey: 'evmAuth.bindFailed' }),
    T('evmAuth.bindFailed')
);
assert.equal(userFacingMessage(null), T('app.unexpectedError'));
assert.equal(userFacingMessage(undefined, { fallbackKey: 'friends.loadFailed' }), T('friends.loadFailed'));
assert.equal(userFacingMessage('some string failure'), T('app.unexpectedError'));
assert.equal(userFacingMessage(new Error('')), T('app.unexpectedError'));
// fallback 字串（key 不存在時）
assert.equal(userFacingMessage(new Error('English only'), { fallbackKey: 'no.such.key', fallback: 'Fallback text' }), 'Fallback text');

// ── 錢包取消：各家措辭 ──
const rejected = [
    Object.assign(new Error('x'), { code: 4001 }),
    Object.assign(new Error('x'), { code: 'ACTION_REJECTED' }),
    Object.assign(new Error('User rejected.'), { code: 5000 }), // WalletConnect v2
    new Error('User denied message signature'), // MetaMask／Coinbase
    new Error('User rejected the request.'), // Rainbow／OKX／Phantom
    new Error('MetaMask Message Signature: User denied message signature.'),
    new Error('user rejected signing (action="signMessage", from="0xabc", messageData="x", code=ACTION_REJECTED, version=providers/5.7.2)'), // ethers v5.7
    new Error('Request rejected'),
    new Error('The user cancelled the request'),
    new Error('cancel'), // imToken
    new Error('User canceled'),
    new Error('Cancelled by user'),
    { error: { code: 4001, message: 'x' }, message: 'wrapped' }, // ethers v5 把 provider 錯誤包在 .error
    { reason: 'user rejected transaction', code: 'ACTION_REJECTED' },
    'User rejected the request',
];
for (const e of rejected) {
    assert.equal(isUserRejection(e), true, JSON.stringify(e instanceof Error ? e.message : e));
    assert.equal(errorKind(e), 'cancelled');
    assert.equal(userFacingMessage(e), T('error.cancelled'));
}
const notRejected = [
    null,
    undefined,
    new TypeError('Failed to fetch'),
    new Error('Wallet connection timed out — please tap EVM login again'),
    new Error('insufficient funds for gas'),
    new Error('Connection request reset. Please try again.'),
    Object.assign(new Error('x'), { code: -32002 }), // 請求已在等待中
    http(403, 'Access denied'), // 後端回的 HTTP 錯誤不是錢包取消
    http(401, 'Request rejected by server'),
    // 看起來像、但其實是真的失敗：不能被吃成「已取消」
    new Error('Permission denied'),
    new Error('Order cancelled'),
    new Error('Failed to cancel subscription'),
    new Error('Request rejected due to rate limit'),
    new Error('Access denied for this origin'),
    Object.assign(new Error('Internal error'), { code: 5000 }), // 5000 不單獨採信
];
for (const e of notRejected) assert.equal(isUserRejection(e), false, String(e && e.message));

// ── presentableMessage：只回「可以給人看」的原文，否則空字串 ──
assert.equal(presentableMessage(new Error('簽名逾時——請再按一次')), '簽名逾時——請再按一次');
assert.equal(presentableMessage(new Error('Connection request reset')), '');
assert.equal(presentableMessage(new TypeError('Failed to fetch')), '');
assert.equal(presentableMessage(http(502, 'Status 502: Bad Gateway')), '');
assert.equal(presentableMessage(null), '');
lang = 'en';
assert.equal(presentableMessage(new Error('Connection request reset')), 'Connection request reset');
lang = 'zh-TW';

// ── errorFromResponse：raw fetch 的失敗回應 → 帶 status 的 Error ──
const response = (status, body, statusText = '') => ({ status, statusText, text: async () => body });
let err = await errorFromResponse(response(400, JSON.stringify({ detail: '地址格式不正確' })));
assert.equal(err.status, 400);
assert.equal(err.message, '地址格式不正確');
err = await errorFromResponse(response(422, JSON.stringify({ detail: [{ loc: ['body', 'address'], msg: 'field required' }, { msg: 'bad' }] })));
assert.equal(err.status, 422);
assert.equal(err.message, 'body.address: field required\nbad'); // 不再是 '[object Object]'
assert.equal(userFacingMessage(err), T('error.badRequest'));
err = await errorFromResponse(response(502, '<html><body>Bad Gateway</body></html>', 'Bad Gateway')); // HTML 不能被當 JSON 炸
assert.equal(err.status, 502);
assert.equal(userFacingMessage(err), T('error.serverError'));
err = await errorFromResponse(response(500, ''));
assert.equal(err.status, 500);
assert.equal(userFacingMessage(err), T('error.serverError'));
err = await errorFromResponse(response(403, JSON.stringify({ detail: { upgrade_required: true } })));
assert.equal(err.status, 403);
assert.equal(userFacingMessage(err), T('error.forbidden'));
err = await errorFromResponse(response(409, JSON.stringify({ message: '這個地址已被綁定' })));
assert.equal(err.message, '這個地址已被綁定');
err = await errorFromResponse({ status: 500, statusText: '', text: async () => { throw new Error('stream'); } });
assert.equal(err.status, 500);

// ── 註冊給 classic script（memory-manager／skill-manager／forum profile-page）用 ──
assert.equal(typeof window.ErrorMessage.userFacingMessage, 'function');
assert.equal(typeof window.ErrorMessage.errorFromResponse, 'function');
assert.equal(typeof window.ErrorMessage.isUserRejection, 'function');

// ── 用到的 i18n key 四語都在（zh-CN 由 zh-TW 產生，另有測試守；這裡只確認存在） ──
const keys = new Set(mod.I18N_KEYS_USED);
assert.ok(keys.size > 20, 'I18N_KEYS_USED 應列出對照表用到的全部 key');
for (const key of keys) {
    for (const l of LANGS) {
        assert.equal(typeof resolve(dicts[l], key), 'string', `${l} 缺 ${key}`);
    }
}

console.log('ok');
