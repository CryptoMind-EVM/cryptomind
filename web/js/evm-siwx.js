// ========================================
// evm-siwx.js — One-Click Auth（SIWX）設定
//
// 為什麼存在：手機瀏覽器走 WalletConnect 時，原本的做法要兩趟錢包往返
// （批准 session 一趟、personal_sign 再一趟）。每一趟都跨越一個 Android 會
// 凍結、甚至丟棄的分頁，還依賴一條在背景會死掉的 relay WebSocket——兩次機會
// 出錯。WalletConnect 的 One-Click Auth（wallet_authenticate）把兩件事併成
// 一趟：AppKit 只跟我們要 nonce / domain / uri，最終的 ERC-4361 字串由錢包
// 自己組、簽完連同 session 一起回來。
//
// 代價是後端不能再用 nonce_token 重建訊息比對，必須驗證錢包送回來的 message
// 本身（見 api/evm_verification.py 的 verify_siwe_wallet_message）。
//
// 這裡全部走依賴注入，好讓 Node 測得到，不必載入整包 AppKit 或瀏覽器環境。
// ========================================

const SIWX_SESSION_KEY = 'evmSiwxSession';

/**
 * 組 ERC-4361 訊息。錢包不支援 One-Click Auth 時 AppKit 會退回
 * personal_sign 這份字串，所以格式必須跟後端解析器對得起來：
 * 第一行是 `<domain> wants you to sign in with your Ethereum account:`，
 * 第二行是地址，其餘欄位一行一個。
 */
function buildErc4361Message(data) {
    const lines = [
        `${data.domain} wants you to sign in with your Ethereum account:`,
        data.accountAddress,
        '',
        data.statement || 'Sign in to CryptoMind',
        '',
        `URI: ${data.uri}`,
        `Version: ${data.version || '1'}`,
        // Chain ID 要的是數字，caip 全名（eip155:1）會讓錢包與後端各自解讀
        `Chain ID: ${String(data.chainId || '').split(':').pop()}`,
        `Nonce: ${data.nonce}`,
        `Issued At: ${data.issuedAt}`,
    ];
    if (data.expirationTime) lines.push(`Expiration Time: ${data.expirationTime}`);
    if (data.notBefore) lines.push(`Not Before: ${data.notBefore}`);
    if (data.requestId) lines.push(`Request ID: ${data.requestId}`);
    if (data.resources && data.resources.length) {
        lines.push('Resources:');
        data.resources.forEach((r) => lines.push(`- ${r}`));
    }
    return lines.join('\n');
}

// One-Click Auth 時 AppKit 用空的 accountAddress 呼叫 createMessage（它還不
// 知道地址——地址是錢包批准後才有的），所以地址要能從訊息第二行認回來。
function _addressFromMessage(message) {
    const lines = String(message || '').split('\n');
    const candidate = (lines[1] || '').trim();
    return /^0x[0-9a-fA-F]{40}$/.test(candidate) ? candidate : '';
}

/**
 * 產生給 createAppKit({ siwx }) 用的設定。
 *
 * deps:
 *   origin        — dapp 網址（domain / uri 由此推導）
 *   fetchNonce    — (address) => { nonce_token }；address 可能是空字串
 *   submitLogin   — ({address, message, signature}) => 後端 /evm-login 回應
 *   storage       — Map 風格的 get/set/delete（正式環境包 localStorage）
 *   onLogin       — 登入成功後的收尾（套用 session、關彈窗）
 *   isLoggedInAs  — (address) => bool；我們後端是否已用這個位址登入。
 *                   getSessions 的唯一真相，沒給就一律回空。
 */
function createSiwxConfig(deps) {
    const origin = deps.origin;
    const host = String(origin).replace(/^https?:\/\//, '').replace(/\/.*$/, '');
    const storage = deps.storage;

    const readSessions = () => {
        try {
            const raw = storage.get(SIWX_SESSION_KEY);
            const parsed = raw ? JSON.parse(raw) : [];
            return Array.isArray(parsed) ? parsed : [];
        } catch (e) {
            return [];
        }
    };
    const writeSessions = (sessions) => {
        try {
            storage.set(SIWX_SESSION_KEY, JSON.stringify(sessions));
        } catch (e) { /* 隱私模式：這輪登入照樣有效，只是下次要重簽 */ }
    };

    return {
        async createMessage(input) {
            const accountAddress = (input && input.accountAddress) || '';
            const challenge = await deps.fetchNonce(accountAddress);
            const nonce = challenge && challenge.nonce_token;
            if (!nonce) throw new Error('Unable to get login challenge');
            const data = {
                accountAddress,
                chainId: (input && input.chainId) || '',
                domain: host,
                uri: origin,
                version: '1',
                nonce,
                statement: 'Sign in to CryptoMind',
                issuedAt: new Date().toISOString(),
            };
            return { ...data, toString: () => buildErc4361Message(data) };
        },

        async addSession(session) {
            const message = session && session.message;
            const address =
                ((session && session.data && session.data.accountAddress) || '') ||
                _addressFromMessage(message);
            if (!address) throw new Error('Wallet returned no account');
            const result = await deps.submitLogin({
                address,
                message,
                signature: session.signature,
            });
            if (!result || !result.success) {
                throw new Error((result && result.detail) || 'EVM login failed');
            }
            const stored = {
                data: { ...(session.data || {}), accountAddress: address },
                message,
                signature: session.signature,
            };
            writeSessions([stored]);
            if (typeof deps.onLogin === 'function') deps.onLogin(result, address);
        },

        // AppKit 的 initializeIfEnabled 看這裡的回傳決定要不要請使用者簽名：
        // 回非空就直接 `return`，完全不發簽名請求（SIWXUtil.js:45-51）。
        // 所以這裡的真相必須是「我們後端有沒有這個位址的登入」，不是
        // localStorage 的殘影——拿殘影冒充的話，錢包打開什麼都不跳、
        // addSession 永遠不被呼叫、連線 promise 永遠等下去，畫面就是無限
        // 轉圈（2026-09-01 手機回報＋本機重現）。
        async getSessions(chainId, address) {
            const wanted = String(address || '').toLowerCase();
            if (!wanted) return [];
            // 沒有辦法確認登入狀態時保守處理：回空，寧可多簽一次也不要卡死
            if (typeof deps.isLoggedInAs !== 'function') return [];
            if (!deps.isLoggedInAs(wanted)) return [];
            return readSessions().filter(
                (s) =>
                    s &&
                    s.data &&
                    String(s.data.accountAddress || '').toLowerCase() === wanted
            );
        },

        async revokeSession(chainId, address) {
            const wanted = String(address || '').toLowerCase();
            writeSessions(
                readSessions().filter(
                    (s) =>
                        !s ||
                        !s.data ||
                        String(s.data.accountAddress || '').toLowerCase() !== wanted
                )
            );
        },

        async setSessions(sessions) {
            writeSessions(Array.isArray(sessions) ? sessions : []);
        },
    };
}

export { buildErc4361Message, createSiwxConfig };
