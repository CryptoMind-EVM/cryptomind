// ========================================
// evm-walletconnect.js — Reown AppKit（WalletConnect）橋
// multichain design 決策點 2（方案 B）。由 evm-auth.js 動態 import，
// 只有使用者點「WalletConnect 掃碼」時才載入整個 AppKit bundle，
// 不拖累 shared-core 首屏。
//
// 角色：初始化 AppKit（ethers adapter）→ 開啟連線彈窗 → 回傳
// EIP-1193 provider 給 evm-auth 的 _completeEvmLogin 走既有
// SIWE 流程（nonce → personal_sign → /api/user/evm-login）。
//
// projectId 為 Reown Cloud 公開識別碼（非機密），依 AppKit 慣例
// 內嵌於前端 bundle。
// ========================================

import { createSiwxConfig } from './evm-siwx.js';
import { resolveSocialLogin } from './social-login.js';
import { createSocialLoginWatchdog } from './social-login-watchdog.js';

// projectId（2026-09-08 回切）：#673 曾換到 9c88ef0c…（宣稱 cryptomind 組織
// 自管），但該專案不存在於任何可及帳號——反而成了新的孤兒。真正的舊專案
// af1d… 帳號已尋回（cryptomind 團隊可管理），回切它：網域驗證與 Explorer
// 上架都需要「登得進後台的專案」。換 ID＝既有 WC 配對失效一次（重掃即可）。
const PROJECT_ID = 'af1d2fb078465000943d5caa4959538e';
const EVM_NAMESPACE = 'eip155';

// One-Click Auth（wallet_authenticate）目前與 Trust Wallet 不相容：AppKit 送出
// authenticate 後等錢包回應，Trust 不支援就靜默忽略——連 session proposal 都
// 不會走完，wc-debug 面板永遠「尚未連上（appkit=0 session=0）」，使用者看著
// Trust 顯示已連線、網頁卻永遠等不到 session，也等不到簽名（2026-09-01 手機
// 實測）。上游仍在修（canary 1.8.24-wc-oneclick-ns / siwx-context），這裡先
// 預設走回經典兩步流程（連線 → personal_sign），?wc-oneclick=1 可暫時打開
// 做實測對照。
const ONE_CLICK_DEFAULT = false;

// 開彈窗後 proposal publish 還在飛的保護窗：期間回前景只做軟重連，不 force
// 重開 transport（force 會把 in-flight 的 proposal publish 砍丟且不重試）。
const PROPOSAL_GRACE_MS = 20000;

// 死 QR 判定窗（2026-09-05 線上實測）：開彈窗超過此時間仍 pairing=0——
// proposal 從未發佈到 relay，使用者掃的是一張死碼，錢包端永遠不會出現
// 簽署畫面，頁面只能無聲轉圈到 120~180s 逾時。屆時換發新碼並提示重掃。
const DEAD_QR_MS = 18000;
const QR_REGEN_MAX = 2;
// 關窗後「已連線但 provider 尚未可讀」的再探預算：連上瞬間 account 事件
// 連發 connected=true、readEvmConnection 卻慢 1~2 秒（2026-09-05 線上
// 15:41:53：1.2 秒一次定生死把剛連上的登入誤判成「使用者取消」收掉）。
const CLOSE_WAIT_BUDGET_MS = 10000;

function _oneClickEnabled() {
    try {
        return ONE_CLICK_DEFAULT || String(location.search).includes('wc-oneclick=1');
    } catch (e) {
        return ONE_CLICK_DEFAULT;
    }
}

// ---- 2026-09-05：三個無聲卡死的對策（決策抽成純函數，Node 可測：
//      tests/js/evm_wc_recovery.mjs）----

const WC_EVENT_ENDPOINT = '/api/user/wc-connect-event';

/**
 * 失敗模式回報——fire-and-forget。遙測絕不可影響登入流程：任何錯誤都吞。
 * summary 上限與後端 WcConnectEventRequest 對齊（300）。
 */
function reportWcConnectEvent(mode, summary) {
    try {
        if (typeof window === 'undefined' || !window.AppAPI || typeof window.AppAPI.post !== 'function') return;
        window.AppAPI.post(WC_EVENT_ENDPOINT, {
            mode,
            summary: String(summary || '').slice(0, 300),
        }).catch(() => {});
    } catch (e) { /* 遙測失敗不算事 */ }
}

/**
 * A｜死 QR 判定：未連上＋proposal 從未發佈（pairing=0）＋超過 DEAD_QR_MS。
 * pairing 有發佈（pairing=1...）不算死碼——那可能是錢包端還沒批准。
 */
function shouldRegenerateQr(opts) {
    const o = opts || {};
    if (o.connected) return false;
    if ((o.regenCount || 0) >= (o.maxRegen || QR_REGEN_MAX)) return false;
    if (!((o.elapsedMs || 0) >= DEAD_QR_MS)) return false;
    return typeof o.pairingSummary === 'string' && o.pairingSummary.startsWith('pairing=0');
}

/**
 * B｜關窗後的判定：帳號連線中絕不判「使用者取消」。
 *   'connected'   → probe 已讀到連線（成功）
 *   'keep-waiting' → 連線中但 provider 尚未可讀——繼續重探
 *   'give-up'      → 連線中但超過預算仍不可讀——照逾時處理交給續登
 *   'cancel'       → 沒連線——真的取消
 *
 * sessionConnected（2026-09-07 新增）：AppKit 的 account state 對某些錢包
 * （Trust 實測，見 readWcSession 的註解）連上後仍是空的——只看
 * accountConnected 會把「WC session 已經建立」判成取消。兩個訊號任一
 * 為真都算連線中。
 */
function closeConfirmationAction(opts) {
    const o = opts || {};
    if (o.probeSeesConnection) return 'connected';
    if (o.accountConnected || o.sessionConnected) {
        return (o.waitedMs || 0) < (o.waitBudgetMs || CLOSE_WAIT_BUDGET_MS)
            ? 'keep-waiting'
            : 'give-up';
    }
    return 'cancel';
}

let _initPromise = null;

// 手機接不上 devtools，前幾輪都在猜。?wc-debug=1 把連線過程直接畫在畫面上（本機開發，
// 或支援人員先設 localStorage.cm_wc_debug='1'）；本機開發時連線卡超過 25 秒也會自動把面板
// 叫出來。正式站一律不現形（2026-09-02、2026-09-22 DANNY 兩次回報面板跑出來）。
const _debugLines = [];
let _debugBox = null;
let _debugForced = false;

// 正式站不准靠網址旗標現形（2026-09-22 DANNY：?wc-debug=1 被瀏覽器／Mini App 記住之後
// 一直黏在畫面上）。網址旗標只在本機開發（APP_CONFIG.DEBUG_MODE）有效；正式站要取證，
// 支援人員在 devtools 設 localStorage.cm_wc_debug = '1'，看完 removeItem。
function _wcDebugAllowedByUrl() {
    try {
        if (typeof location === 'undefined' || !String(location.search).includes('wc-debug=1')) return false;
        const devLike = typeof window !== 'undefined' && window.APP_CONFIG && window.APP_CONFIG.DEBUG_MODE === true;
        const optIn = typeof localStorage !== 'undefined' && localStorage.getItem('cm_wc_debug') === '1';
        return devLike || optIn;
    } catch (e) {
        return false;
    }
}

function _renderWcDebug() {
    try {
        if (typeof document === 'undefined') return;
        if (!_debugForced && !_wcDebugAllowedByUrl()) return;
        if (!_debugBox) {
            _debugBox = document.createElement('div');
            _debugBox.style.cssText =
                'position:fixed;left:0;right:0;bottom:0;max-height:42vh;overflow:auto;z-index:2147483647;' +
                'background:rgba(0,0,0,.92);color:#7CFC9B;font:11px/1.45 ui-monospace,monospace;padding:8px 10px;' +
                'white-space:pre-wrap;word-break:break-all;';
            document.body.appendChild(_debugBox);
        }
        _debugBox.textContent = _debugLines.join('\n');
        _debugBox.scrollTop = _debugBox.scrollHeight;
    } catch (e) { /* debug 面板壞掉不能影響登入 */ }
}

function wcDebug(msg) {
    console.debug('[evm-wc]', msg);
    const t = new Date().toTimeString().slice(0, 8);
    _debugLines.push(`${t} ${msg}`);
    if (_debugLines.length > 200) _debugLines.shift();
    _renderWcDebug();
}

// 卡住時自己現形：把先前緩衝的紀錄一次補上，截圖才有完整脈絡。
// 只准在本機開發自動展開（APP_CONFIG.DEBUG_MODE 由 hostname 推導、
// fail-closed，同 layout-debug 的 production guard 教訓）；正式站一律
// 不自動現形，取證走 ?wc-debug=1。回傳是否已展開，供測試斷言。
function enableWcDebug() {
    if (_debugForced) return true;
    const devLike =
        typeof window !== 'undefined' &&
        window.APP_CONFIG &&
        window.APP_CONFIG.DEBUG_MODE === true;
    if (!devLike) return false;
    _debugForced = true;
    _renderWcDebug();
    return true;
}

/**
 * 不依賴 AppKit 全域 activeChain 讀出 EVM 連線。
 * 手機 WebView 恢復期間 eip155 的 account/provider 可能已就緒，activeChain
 * 卻還沒恢復——那時不帶 namespace 的 getWalletProvider() 會回 null。
 */
function readEvmConnection(appKit, providersState) {
    let account;
    let caipAddress;
    let provider;
    try {
        account = appKit.getAccount(EVM_NAMESPACE);
    } catch (e) {
        account = undefined;
    }
    try {
        caipAddress = appKit.getCaipAddress(EVM_NAMESPACE);
    } catch (e) {
        caipAddress = undefined;
    }
    provider = (providersState && providersState[EVM_NAMESPACE]) || null;
    if (!provider && typeof appKit.getProvider === 'function') {
        try {
            provider = appKit.getProvider(EVM_NAMESPACE);
        } catch (e) { /* AppKit 尚未完成 provider restore */ }
    }
    if (!provider && typeof appKit.getWalletProvider === 'function') {
        try {
            provider = appKit.getWalletProvider();
        } catch (e) { /* 舊版 AppKit fallback 尚未就緒 */ }
    }
    const resolvedCaipAddress = caipAddress || (account && account.caipAddress);
    const address =
        (account && account.address) ||
        (resolvedCaipAddress && String(resolvedCaipAddress).split(':').pop());
    if (!address || !provider) return null;
    const conn = { address, provider };
    // 內嵌錢包（Email／Google 建立，PR-6）：provider 是 AppKit 的 W3mFrameProvider，
    // 只收白名單內的 RPC——eth_requestAccounts 不在裡面，送了會跳「Action not allowed」
    // 並被拒。標記出來讓上層直接用這裡的地址，簽名（personal_sign）照常。
    if (account && account.embeddedWalletInfo) {
        conn.embeddedWallet = true;
        // email／google：登入時回報給後台統計（Email 本身不帶）
        conn.authProvider = account.embeddedWalletInfo.authProvider || 'email';
    }
    return conn;
}

/**
 * 內嵌錢包連線狀態：'connected'／'pending'（頁面剛載入，AppKit 還在跟
 * secure.walletconnect.org 的 iframe 恢復）／'none'。
 */
function readEmbeddedState(appKit) {
    let account;
    try {
        account = appKit.getAccount(EVM_NAMESPACE);
    } catch (e) {
        return 'none';
    }
    if (!account) return 'none';
    if (account.isConnected && account.embeddedWalletInfo) return 'connected';
    if (account.status === 'connecting' || account.status === 'reconnecting') return 'pending';
    return 'none';
}

async function waitEmbeddedSettled(appKit, opts) {
    const timeoutMs = (opts && opts.timeoutMs) || 8000;
    const intervalMs = (opts && opts.intervalMs) || 250;
    const deadline = Date.now() + timeoutMs;
    let state = readEmbeddedState(appKit);
    while (state === 'pending' && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, intervalMs));
        state = readEmbeddedState(appKit);
    }
    return state;
}

/**
 * WalletConnect session 才是唯一可信來源。
 * 手機實測（2026-09-01）：Trust Wallet 批准後回到瀏覽器，AppKit 彈窗卻永遠停在
 * 「Continue in Trust Wallet」轉圈——AppKit 的 controller state（account /
 * provider / activeChain）全空，但 UniversalProvider 身上的 session 已經存在。
 * 繞過 AppKit 直接讀 session。AppKit 的 getUniversalProvider() 回的就是各
 * adapter 共用的那一顆（appkit-base-client.js:1126），所以這裡讀到的是真的。
 * UniversalProvider 本身也是 EIP-1193 provider（eth_requestAccounts 由它就地回
 * session accounts，不會再跳一次錢包），可直接餵給 personal_sign 流程。
 */
async function readWcSession(appKit) {
    if (!appKit || typeof appKit.getUniversalProvider !== 'function') return null;
    let up;
    try {
        up = await appKit.getUniversalProvider();
    } catch (e) {
        return null;
    }
    const session = up && up.session;
    if (!session) return null;
    // 過期 session 撿回來只會讓 personal_sign 空等 150 秒——寧可回 null 讓
    // 使用者重連
    if (session.expiry && Number(session.expiry) * 1000 <= Date.now()) {
        wcDebug('WC session 已過期，忽略');
        return null;
    }
    const ns = session.namespaces && session.namespaces[EVM_NAMESPACE];
    const caip = ns && ns.accounts && ns.accounts[0];
    if (!caip) return null;
    const address = String(caip).split(':').pop();
    if (!address) return null;
    return { address, provider: up };
}

/**
 * pairing 活性探測：對最新 pairing topic 發標準 wc ping（節流 10s）。
 *   wallet=online  → Trust 有訂閱在這個 topic 上——proposal 是丟了（dapp 端可修：重發）
 *   wallet=offline → Trust 從未連上這個 topic——deep link 沒被消化／錢包端網路問題
 *   wallet=?       → SignClient 不支援 ping 或尚無 pairing
 * ping 是 WalletConnect 規範方法（多數錢包支援），內建逾時，結果快取到下次探測。
 */
const _pingState = { at: 0, result: '?' };

function _resetPingProbeForTests() {
    _pingState.at = 0;
    _pingState.result = '?';
}

function _refreshPairingLiveness(appKit, pairingTopic) {
    return (async () => {
        try {
            const up = await appKit.getUniversalProvider();
            if (!up || !up.client || typeof up.client.ping !== 'function') {
                _pingState.result = '?';
                return;
            }
            await up.client.ping({ topic: pairingTopic });
            _pingState.result = 'online';
        } catch (e) {
            // ping 逾時（預設 5s）或錢包不回——都代表 topic 上沒有活著的錢包
            _pingState.result = 'offline';
        }
    })();
}

async function probePairingLiveness(appKit, pairingTopic) {
    if (!pairingTopic) return _pingState.result;
    if (Date.now() - _pingState.at < 10000) return _pingState.result;
    _pingState.at = Date.now();
    // 刻意不 await（2026-09-07）：ping 內建 5 秒逾時,await 它會讓整支 probe
    // 停 5 秒。而「關窗後確認是否已連上」很容易正好落在這個窗口——那次探測
    // 被擠掉,流程就改判「使用者取消」（桌機掃碼連上後電腦端毫無反應、要再
    // 點一次才跳簽名的成因）。診斷值晚一輪更新無所謂,它只印在除錯面板上。
    _refreshPairingLiveness(appKit, pairingTopic);
    return _pingState.result;
}

/**
 * pairing 診斷摘要：proposal 是否有送達 relay 端註冊（pairing 存在）、錢包
 * 是否回應過（active）。除錯面板一行就能分辨三種死法：
 *   pairing=0                → proposal publish 沒完成（傳送中被砍／relay 沒收到）
 *   pairing=1 active=false   → Trust 收到但從未回應（錢包側沒收到或沒批准）
 *   pairing=1 active=true    → 錢包回了 pairing，但 session settle 迷路（回應遞送問題）
 * uri 比對（2026-09-02 實測後新增）：deep link 帶給錢包的是 UniversalProvider.uri，
 * 錢包會訂閱在那個 topic 上等 proposal。若 uri topic ≠ 最新 pairing topic，代表
 * 錢包在舊 topic 等、我們在新 topic 發——經典 stale-URI 競態，面板直接印 uri≠topic!。
 */
async function readPairingSummary(appKit) {
    if (!appKit || typeof appKit.getUniversalProvider !== 'function') return 'pairing=?';
    try {
        const up = await appKit.getUniversalProvider();
        const pairings =
            (up && up.client && up.client.core && up.client.core.pairing &&
                typeof up.client.core.pairing.getPairings === 'function'
                ? up.client.core.pairing.getPairings()
                : []) || [];
        if (!pairings.length) return 'pairing=0';
        const p = pairings[pairings.length - 1];
        const liveSec = p.expiry
            ? Math.max(0, Math.round((Number(p.expiry) * 1000 - Date.now()) / 1000))
            : '?';
        let uriTopic = null;
        try {
            const uri = up && up.uri ? String(up.uri) : '';
            uriTopic = uri.startsWith('wc:') ? (uri.split(':')[1] || '').split('@')[0] : null;
        } catch (e) { /* uri 讀不到就只印 ? */ }
        const uriTag = !uriTopic
            ? 'uri=?'
            : uriTopic === p.topic
                ? 'uri=match'
                : 'uri≠topic!';
        const liveness = await probePairingLiveness(appKit, p.topic);
        return `pairing=${pairings.length}(active=${!!p.active},alive=${liveSec}s,${uriTag},wallet=${liveness})`;
    } catch (e) {
        return 'pairing=?';
    }
}

/**
 * Android Chrome 會凍結背景分頁，deep-link 去錢包期間 relay 的 WebSocket
 * 常被切斷；回到瀏覽器後 socket 不會自己活過來，錢包批准的 session settle
 * 就永遠送不到，前端只能一直轉圈。
 * force：凍結後的 socket 常是殭屍——relayer.connected 還報 true、訊息卻永遠
 * 收不到。回到前景時不能信那個旗標，直接重開。
 */
async function ensureRelayConnected(appKit, opts) {
    if (!appKit || typeof appKit.getUniversalProvider !== 'function') return;
    const force = !!(opts && opts.force);
    try {
        const up = await appKit.getUniversalProvider();
        const relayer = up && up.client && up.client.core && up.client.core.relayer;
        if (!relayer) return;
        if (!force && relayer.connected) return;
        wcDebug(`重開 relay transport（connected=${!!relayer.connected} force=${force}）`);
        await relayer.restartTransport();
        wcDebug('relay transport 已重開');
    } catch (e) {
        wcDebug('relay 重連失敗：' + (e && e.message));
    }
}

/**
 * AppKit 設定。networks 由呼叫端注入，方便測試不必載入整包 AppKit。
 *
 * metadata.redirect 一定要填：WalletConnect 錢包是靠它決定簽完把使用者送回
 * 哪裡。沒填的話 Trust Wallet 驗完指紋就停在錢包 App，使用者得自己切回瀏覽器
 * （2026-09-01 手機回報）。universal 用 dapp 網址，跟 metadata.url 一致。
 */
// SIWX 完成登入時要通知兩邊：evm-auth（套用 session、關彈窗）與正在等待的
// connectViaWalletConnect（讓它結束，而不是再自己跑一次 personal_sign）。
let _siwxOnLogin = null;
const _siwxWaiters = [];

function setSiwxLoginHandler(fn) {
    _siwxOnLogin = fn;
}

function _notifySiwxLogin(result, address) {
    wcDebug(`SIWX 登入完成 ${String(address).slice(0, 10)}`);
    _siwxWaiters.splice(0).forEach((fn) => {
        try { fn({ siwxLoggedIn: true, result, address }); } catch (e) { /* noop */ }
    });
    // handler 拋錯會沿 AppKit 內部 async 鏈變 unhandled rejection——
    // 使用者拿不到任何回饋，這裡與 waiters 同款吞掉記錄（2026-09-10 徹查）
    try {
        if (_siwxOnLogin) _siwxOnLogin(result, address);
    } catch (e) {
        console.warn('[evm-walletconnect] siwx login handler failed', e);
    }
}

// SIWX 的 getSessions 要回答「我們後端有沒有這個位址的登入」——AuthManager
// 的 currentUser 就是答案。localStorage 裡的舊簽章不算數。
function _isLoggedInAs(address) {
    const user = window.AuthManager && window.AuthManager.currentUser;
    const bound = user && (user.wallet_address || '');
    return !!bound && String(bound).toLowerCase() === String(address || '').toLowerCase();
}

function _buildSiwx(origin) {
    return createSiwxConfig({
        origin,
        async fetchNonce(address) {
            const q = address ? `?address=${encodeURIComponent(address)}` : '';
            return window.AppAPI.get(`/api/user/evm-nonce${q}`);
        },
        async submitLogin(body) {
            return window.AppAPI.post('/api/user/evm-login', body);
        },
        storage: {
            get: (k) => localStorage.getItem(k),
            set: (k, v) => localStorage.setItem(k, v),
        },
        isLoggedInAs: _isLoggedInAs,
        onLogin: _notifySiwxLogin,
    });
}

// WalletConnect 底層（One-Click Auth 的 authenticate 走的那條 crypto 路徑）用到
// Node 的 Buffer / global / process，瀏覽器沒有，會噴「Buffer is not defined」——
// 而且是被 pino logger 吞成 log 行，畫面只停在轉圈（2026-09-01 本機重現）。
//
// 注意不能只寫 `await import('@reown/appkit-polyfills')`：那是純副作用 import，
// bundler 會直接 tree-shake 掉（打包後的 chunk 裡整個消失）。這裡自己把值取出來
// 賦到 window 上，賦值本身是可觀察的副作用，砍不掉。
async function installNodeGlobals() {
    if (typeof window === 'undefined') return;
    if (!window.global) window.global = window;
    if (!window.process) window.process = { env: {} };
    else if (!window.process.env) window.process.env = {};
    if (window.Buffer) return;
    try {
        const mod = await import('buffer');
        const BufferImpl = mod.Buffer || (mod.default && mod.default.Buffer);
        if (BufferImpl) {
            window.Buffer = BufferImpl;
            wcDebug('已補上 Buffer polyfill');
        }
    } catch (e) {
        wcDebug('Buffer polyfill 載入失敗：' + (e && e.message));
    }
}

/**
 * opts.socialLogin（PR-6，REOWN_SOCIAL_LOGIN_ENABLED 開＋一般網頁）：
 *   - 只開 Email 與 Google；錢包清單照列（emailShowWallets）
 *   - 內嵌錢包固定 EOA：AppKit 預設是 smartAccount。後端雖然也收 EIP-1271／6492
 *     （api/evm_verification.py verify_evm_signature），但那條要打鏈上 RPC、Reown 智慧帳戶
 *     的簽章格式與部署鏈也沒實測過；EOA 走零 RPC 的 ecrecover 最穩（設計 §5 PR-6 定案）
 *   - onramp 開（Premium「刷卡買 USDC」用）、swaps 關（不做兌換）
 * 沒開時 features 與上線前逐字相同。
 * 注意：這幾項是 Reown「遠端功能」——後台該項沒指定清單（config=null）時才用這裡的值；
 * 後台一旦指定，這裡整個被忽略（node_modules/@reown/appkit/dist/esm/src/utils/ConfigUtil.js）。
 */
function buildAppKitConfig(origin, projectId, networks, siwx, opts) {
    const socialLogin = !!(opts && opts.socialLogin);
    const config = {
        networks: networks || [],
        projectId,
        metadata: {
            name: 'CryptoMind',
            description: 'AI-driven crypto & stock analysis platform',
            url: origin,
            icons: [`${origin}/static/img/title_icon.png`],
            redirect: { universal: origin },
        },
        themeMode: 'dark',
        features: socialLogin
            ? {
                  analytics: false,
                  email: true,
                  socials: ['google'],
                  emailShowWallets: true,
                  onramp: true,
                  swaps: false,
              }
            : {
                  analytics: false,
                  email: false,
                  socials: false,
              },
    };
    if (socialLogin) {
        config.defaultAccountTypes = { eip155: 'eoa' };
        // 建錢包的那一刻就讓人看得到條款（3.2：Email／Google 錢包的安全取決於該帳號）
        config.termsConditionsUrl = `${origin}/legal/terms-of-service.html`;
        config.privacyPolicyUrl = `${origin}/legal/privacy-policy.html`;
    }
    // One-Click Auth 開關：siwx 不給＝經典流程（連線 → eth_requestAccounts →
    // personal_sign，由 evm-auth 的 _completeEvmLogin 走）
    if (siwx) config.siwx = siwx;
    return config;
}

// 本頁 AppKit 是不是以 Email／Google 模式初始化的（一頁只初始化一次，之後改不了）
let _socialLoginAtInit = false;

function _ensureAppKit() {
    if (_initPromise) return _initPromise;
    _initPromise = (async () => {
        await installNodeGlobals();
        const socialLogin = await resolveSocialLogin();
        const { createAppKit } = await import('@reown/appkit');
        const { EthersAdapter } = await import('@reown/appkit-adapter-ethers');
        const { mainnet, base } = await import('@reown/appkit/networks');

        _socialLoginAtInit = socialLogin;
        return createAppKit({
            adapters: [new EthersAdapter()],
            ...buildAppKitConfig(
                window.location.origin,
                PROJECT_ID,
                [mainnet, base],
                _oneClickEnabled() ? _buildSiwx(window.location.origin) : undefined,
                { socialLogin }
            ),
        });
    })();
    // 一次暫時性失敗（弱網斷線、CDN 5xx）不得毒化整頁生命週期——原版把
    // rejected promise 永久快取，之後每次點登入都秒失敗、無自癒，直到手動
    // 重新整理（2026-09-10 徹查，兩位審查者獨立發現）。清快取讓下次重試，
    // 當次呼叫者仍拿到原 rejection。
    _initPromise.catch(() => { _initPromise = null; });
    return _initPromise;
}

/**
 * 開啟 AppKit 連線彈窗並等待使用者完成連線。
 * 成功：回傳 { address, provider }（EIP-1193，供 personal_sign）。
 * 使用者關閉彈窗未連線（或逾時）：resolve(null)，不視為錯誤。
 *
 * 手機 deep-link 往返：跳去錢包 App 期間頁面被凍結，setTimeout 的牆鐘時間
 * 照走——回到瀏覽器時若 180s 已耗盡會直接判 null，比 relay 訊號更快。
 * 因此 visibilitychange 恢復可見時把逾時重新起算。
 */
// 上層（evm-auth）要分辨 connectViaWalletConnect 回 null 的原因：使用者
// 主動取消 vs 逾時/deep-link 往返——只有後者該踢續登，取消也踢會在 27 秒後
// 彈「接手失敗」的紅色誤導 toast（2026-09-10 徹查）。
let _lastConnectFinishReason = '';

function getConnectFinishReason() {
    return _lastConnectFinishReason;
}

function connectViaWalletConnect() {
    return new Promise((resolve, reject) => {
        _ensureAppKit()
            .then((appKit) => {
                let settled = false;
                let accountState = null;
                let providersState = null;
                let timer = null;
                let deadlineTimer = null;
                const unsubs = [];
                // Email／Google 社群登入的卡死看門狗（見 social-login-watchdog.js）。
                // 只在本頁 AppKit 是以社群模式初始化時才掛；錢包登入流程完全不受影響。
                const socialWatchdog = _socialLoginAtInit
                    ? createSocialLoginWatchdog({
                        isVisible: () => document.visibilityState === 'visible',
                        onStall: (why) => {
                            if (settled) return;
                            wcDebug('社群登入無回應：' + why);
                            reportWcConnectEvent('social-stalled', why);
                            finish(null, 'social-stalled');
                        },
                    })
                    : null;
                let openedAt = Date.now();
                let qrRegenCount = 0;
                let lastPairingInfo = '';

                const cleanupTimers = () => {
                    if (timer) clearInterval(timer);
                    if (deadlineTimer) clearTimeout(deadlineTimer);
                    document.removeEventListener('visibilitychange', onVisible);
                };
                const finish = (result, reason) => {
                    if (settled) return;
                    settled = true;
                    _lastConnectFinishReason = reason || (result ? 'connected' : 'timeout');
                    cleanupTimers();
                    const waiterIdx = _siwxWaiters.indexOf(onSiwxLogin);
                    if (waiterIdx >= 0) _siwxWaiters.splice(waiterIdx, 1);
                    unsubs.forEach((fn) => {
                        try { fn(); } catch (e) { /* noop */ }
                    });
                    // 成功或放棄都收掉彈窗——放棄不關會讓使用者對著無盡的
                    // 連線轉圈（2026-09-01 手機實測回報）；遲到的 session 由
                    // evm-auth 的斷線續登接手
                    try { appKit.close(); } catch (e) { /* noop */ }
                    resolve(result);
                };
                // SIWX 開著時，這條 promise 等的是「登入完成」而不是「連上了」。
                // 連上就結束的話，evm-auth 會接著自己再跑一次 personal_sign——
                // 使用者要簽兩次，正好是 One-Click Auth 要消滅的東西。
                const onSiwxLogin = (payload) => finish(payload);
                _siwxWaiters.push(onSiwxLogin);

                // 只回報狀態、不搶著結束：AppKit state 與 WC session 兩條線都看，
                // 卡住時這些行就是除錯面板上的證據。
                const runProbe = async (tag) => {
                    if (settled) return;
                    const fromAppKit = readEvmConnection(appKit, providersState);
                    if (fromAppKit) {
                        // 已經是登入狀態時 AppKit 不會再要簽名（它的
                        // initializeIfEnabled 看 getSessions 就 return），
                        // 這裡不收工就會空等到逾時
                        if (_isLoggedInAs(fromAppKit.address)) {
                            wcDebug(`${tag}: 已連上且已登入 ${fromAppKit.address.slice(0, 10)}`);
                            finish({ siwxLoggedIn: true, address: fromAppKit.address });
                            return;
                        }
                        if (!_oneClickEnabled()) {
                            // 經典流程：連線本身就是成果，交給 evm-auth 走
                            // nonce → personal_sign（ Trust 不回
                            // wallet_authenticate，等 SIWX 只會等到逾時）
                            wcDebug(`${tag}: 已連上 ${fromAppKit.address.slice(0, 10)}，走經典 personal_sign 登入`);
                            // provider 就是 UniversalProvider 本身＝WalletConnect
                            // 連線（簽名要跳錢包 App）。沒帶這旗標的話
                            // _completeEvmLogin 不會顯示「切換到錢包」提示——
                            // 2026-09-05 線上：續登接手後簽名靜默卡 150 秒的
                            // 直接原因。
                            let viaWc = false;
                            try {
                                viaWc = fromAppKit.provider === (await appKit.getUniversalProvider());
                            } catch (e) { /* 比對失敗當非 WC，最多少一則提示 */ }
                            finish({
                                address: fromAppKit.address,
                                provider: fromAppKit.provider,
                                viaWalletConnect: viaWc,
                                // Email／Google 內嵌錢包：上層不能打 eth_requestAccounts
                                embeddedWallet: !!fromAppKit.embeddedWallet,
                                authProvider: fromAppKit.authProvider,
                            });
                            return;
                        }
                        wcDebug(`${tag}: 已連上 ${fromAppKit.address.slice(0, 10)}，等 SIWX 簽名`);
                        return;
                    }
                    const fromSession = await readWcSession(appKit);
                    if (fromSession) {
                        if (!_oneClickEnabled()) {
                            wcDebug(`${tag}: WC session 已建立 ${fromSession.address.slice(0, 10)}（AppKit state 仍空），走經典 personal_sign 登入`);
                            // viaWalletConnect：簽名要跳錢包 App，_completeEvmLogin
                            // 依此顯示「切換到錢包」提示（注入錢包的原地簽名不用）
                            finish({
                                address: fromSession.address,
                                provider: fromSession.provider,
                                viaWalletConnect: true,
                            });
                            return;
                        }
                        wcDebug(`${tag}: WC session 已建立 ${fromSession.address.slice(0, 10)}（AppKit state 仍空），等 SIWX 簽名`);
                        return;
                    }
                    const pairingInfo = await readPairingSummary(appKit);
                    wcDebug(`${tag}: 尚未連上（appkit=0 session=0 ${pairingInfo}）`);
                    lastPairingInfo = pairingInfo;
                    // A｜死 QR 換發：proposal 從未發佈且超過寬限——重開
                    // relay、關開彈窗換發新 URI，並告訴使用者重掃。否則
                    // 使用者掃的是死碼：錢包端永遠不會有簽署畫面，頁面
                    // 無聲轉圈到逾時（2026-09-05 線上實測）。
                    if (shouldRegenerateQr({
                        elapsedMs: Date.now() - openedAt,
                        pairingSummary: pairingInfo,
                        connected: false,
                        regenCount: qrRegenCount,
                    })) {
                        qrRegenCount += 1;
                        openedAt = Date.now(); // 重置寬限，讓新 proposal 有時間飛
                        wcDebug(`死 QR（pairing=0 超過 ${DEAD_QR_MS / 1000}s）——重開 relay 並換發新 QR（第 ${qrRegenCount} 次）`);
                        reportWcConnectEvent('pairing-dead', pairingInfo);
                        ensureRelayConnected(appKit, { force: true }).then(() => {
                            // 判定後、relay 重開完成前若使用者剛好連上（finish 已
                            // 跑、evm-auth 已進入簽名），這裡重開彈窗會疊在簽名
                            // 流程上——重查 settled 直接收手。
                            if (settled) return;
                            try { appKit.close(); } catch (e) { /* noop */ }
                            try { appKit.open(); } catch (e) { /* noop */ }
                            const hint = (window.I18n && typeof window.I18n.t === 'function')
                                ? window.I18n.t('evmAuth.qrRefreshed')
                                : '原 QR 碼已失效，已自動換發新碼——請重新掃描';
                            if (typeof showToast === 'function') showToast(hint, 'info');
                            probe('QR 已換發');
                        });
                    }
                };

                // probe 絕不能被「已經有一支在跑」給丟掉（2026-09-07）。
                // 舊版是 `if (probing) return`：關窗確認那一次探測整個沒跑就
                // 進了 .then()，緊接著判「使用者取消」——而 WC session 其實
                // 已經建立。桌機掃碼、手機批准後電腦端毫無反應、要再點一次
                // 才跳出簽署畫面，直接成因就是這裡（第二次點之所以「秒跳
                // 簽名」，正是因為第一次留下的 session 還活著）。
                // 改成串接：同時只跑一支，後來者共用同一次「補跑」，不堆疊。
                let probeRunning = null;
                let probeFollowUp = null;
                const probe = (tag) => {
                    if (settled) return Promise.resolve();
                    if (!probeRunning) {
                        probeRunning = runProbe(tag)
                            .catch((e) => wcDebug('probe error: ' + (e && e.message)))
                            .finally(() => {
                                probeRunning = null;
                            });
                        return probeRunning;
                    }
                    if (!probeFollowUp) {
                        probeFollowUp = probeRunning.then(() => {
                            probeFollowUp = null;
                            return probe(tag);
                        });
                    }
                    return probeFollowUp;
                };

                // 逾時只在可見時起算：跳去錢包 App 期間不算時間。
                const armDeadline = (ms) => {
                    if (deadlineTimer) clearTimeout(deadlineTimer);
                    deadlineTimer = setTimeout(() => {
                        // iOS bfcache 回來時，到期 timer 可能搶在 visibilitychange
                        // 之前派發——剛回來的使用者不判死，改短延遲等 onVisible
                        // 重算（2026-09-10 徹查）。
                        if (document.visibilityState !== 'visible') {
                            armDeadline(2000);
                            return;
                        }
                        wcDebug('逾時放棄，交給續登接手');
                        finish(null, 'timeout');
                    }, ms);
                };
                let lastRelayKick = 0;
                const onVisible = () => {
                    if (settled || document.visibilityState !== 'visible') return;
                    armDeadline(120000);
                    if (socialWatchdog) socialWatchdog.onVisible();
                    // 從錢包 App 回來的關鍵一步。Android 凍結分頁後 WebSocket
                    // 常是殭屍——relayer.connected 還報 true、訊息卻永遠收不到，
                    // 所以這裡重開（節流 5s，避免連續切換時狂踢）。
                    // 但「第一次」回來不能 force：彈窗剛開沒多久時 proposal 的
                    // publish 可能還在飛，restartTransport 是斷線+subscriber.stop()
                    // 且不重試 in-flight 訊息——2026-09-01 22:13 實測就是開彈窗後
                    // 2 秒的 force 重開把 proposal 砍丟，Trust 永遠收不到提案、
                    // session 永遠 0。殭屍 socket 只發生在長時間凍結之後，所以
                    // force 前先過 20 秒寬限；寬限期內只用軟重連（沒連上才重開）。
                    const force =
                        Date.now() - openedAt > PROPOSAL_GRACE_MS &&
                        Date.now() - lastRelayKick > 5000;
                    if (force) lastRelayKick = Date.now();
                    ensureRelayConnected(appKit, { force }).then(() => probe('回到前景'));
                };
                document.addEventListener('visibilitychange', onVisible);
                armDeadline(180000);

                unsubs.push(
                    appKit.subscribeAccount((state) => {
                        accountState = state || {};
                        wcDebug(`account 事件 connected=${!!accountState.isConnected}`);
                        probe('account 事件');
                    }, EVM_NAMESPACE)
                );
                // providers（複數）與全域 state 作為備援訊號
                unsubs.push(
                    appKit.subscribeProviders((state) => {
                        providersState = state || {};
                        probe('providers 事件');
                    })
                );
                // 使用者按 X 關掉 AppKit 彈窗時要立刻收工。不收的話這個 promise
                // 會掛到 120~180s 逾時為止，期間 evm-auth 的 _evmLoginInFlight
                // 一直是 true——再按一次「連接錢包」完全沒反應（2026-09-01 回報）。
                if (typeof appKit.subscribeState === 'function') {
                    let sawOpen = false;
                    let closeConfirmTimer = null;
                    unsubs.push(
                        appKit.subscribeState((state) => {
                            if (settled || !state) return;
                            if (state.open) {
                                sawOpen = true;
                                // 死 QR 換發的程式化 close() 也會走這裡的 open=false，
                                // 排入 1.2s 後的取消確認；緊接的 open() 必須把那顆
                                // 排程炸掉——否則 1.2s 後 confirmClose 把剛換發、
                                // 使用者還沒掃的新 QR 彈窗整個收掉、flow 判死
                                // （2026-09-10 徹查：自動換發功能自我抵消）。
                                if (closeConfirmTimer) {
                                    clearTimeout(closeConfirmTimer);
                                    closeConfirmTimer = null;
                                }
                                return;
                            }
                            if (!sawOpen) return;
                            // 關閉與 session settle 可能前後腳發生——但 2026-09-05
                            // 線上實測：連上瞬間 account 事件連發 connected=true、
                            // provider 卻慢 1~2 秒才可讀，「1.2 秒一次定生死」把剛
                            // 連上的登入誤判成「使用者取消」收掉（續登 2 秒後才
                            // 救回）。改走決策函數：連線中絕不判取消，持續重探到
                            // 可讀或 CLOSE_WAIT_BUDGET_MS 上限。
                            wcDebug('彈窗關閉，確認是否已連上');
                            closeConfirmTimer = setTimeout(() => {
                                const confirmStartedAt = Date.now();
                                const confirmClose = async () => {
                                    if (settled) return;
                                    // AppKit 的 account state 對某些錢包（Trust
                                    // 實測）連上後仍是空的——只信它就會把「已經
                                    // 連上」判成取消。關窗當下直接問一次 WC
                                    // session 才是可信來源。
                                    let sessionConnected = false;
                                    try {
                                        sessionConnected = !!(await readWcSession(appKit));
                                    } catch (e) { /* 讀不到就當沒有 */ }
                                    if (settled) return;
                                    const action = closeConfirmationAction({
                                        accountConnected: !!(accountState && accountState.isConnected),
                                        sessionConnected,
                                        probeSeesConnection: false, // probe() 可讀時會自己 finish
                                        waitedMs: Date.now() - confirmStartedAt,
                                    });
                                    if (action === 'keep-waiting') {
                                        probe('彈窗關閉（已連線，等 provider 就緒）');
                                        setTimeout(confirmClose, 500);
                                    } else if (action === 'give-up') {
                                        wcDebug('已連線但 provider 逾時未就緒——交給續登');
                                        reportWcConnectEvent('connected-stalled', lastPairingInfo || 'provider unreadable after close');
                                        finish(null, 'stalled');
                                    } else {
                                        probe('彈窗關閉').then(() => {
                                            if (!settled) {
                                                wcDebug('使用者取消連線');
                                                reportWcConnectEvent('cancel-close', lastPairingInfo || 'not connected');
                                                finish(null, 'cancel');
                                            }
                                        });
                                    }
                                };
                                confirmClose();
                            }, 1200);
                        })
                    );
                }

                // 社群登入卡死看門狗：看 AppKit 事件流，加上它自己那條不會被 catch 的
                // 45 秒逾時（丟在 setTimeout 裡，只會以 window error 現身）
                if (socialWatchdog && typeof appKit.subscribeEvents === 'function') {
                    const onWindowError = (ev) =>
                        socialWatchdog.onWindowError(ev && (ev.message || (ev.error && ev.error.message)));
                    window.addEventListener('error', onWindowError);
                    unsubs.push(
                        appKit.subscribeEvents((ev) => {
                            socialWatchdog.onEvent(ev && ev.data && ev.data.event);
                        }),
                        () => {
                            window.removeEventListener('error', onWindowError);
                            socialWatchdog.dispose();
                        }
                    );
                }

                appKit.open();
                wcDebug('已開啟 AppKit 彈窗');

                // 卡超過 25 秒請除錯面板現形——僅本機開發會自動展開；
                // 正式站要取證就手動加 ?wc-debug=1
                setTimeout(() => {
                    if (!settled) enableWcDebug();
                }, 25000);

                // 輪詢兜底：部分錢包連線後事件不觸發（session Active 但前端
                // 收不到 state 更新）——每秒直接問 AppKit 與 WC session
                timer = setInterval(() => probe('輪詢'), 1000);
            })
            .catch((err) => {
                console.error('[evm-walletconnect] init failed', err);
                reject(err);
            });
    });
}

/**
 * 輪詢等待可用連線：AppKit state 與 WC session 兩條線都問。
 * shouldAbort 回 true 時立刻收手——使用者在續登輪詢期間主動按登入，
 * 兩條流程各跑一次 nonce＋personal_sign 會讓錢包連跳兩次簽名。
 */
async function pollForConnection(appKit, opts) {
    const timeoutMs = (opts && opts.timeoutMs) || 25000;
    const intervalMs = (opts && opts.intervalMs) || 500;
    const shouldAbort = (opts && opts.shouldAbort) || null;
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        if (shouldAbort && shouldAbort()) {
            wcDebug('續登被使用者主動登入接手，收手');
            return null;
        }
        const connection = readEvmConnection(appKit);
        if (connection) {
            wcDebug('續登：AppKit 就緒');
            return connection;
        }
        const fromSession = await readWcSession(appKit);
        if (fromSession) {
            wcDebug('續登：WC session 就緒（AppKit state 仍空）');
            return fromSession;
        }
        await new Promise((r) => setTimeout(r, intervalMs));
    }
    wcDebug('續登：逾時內沒有可用 session');
    return null;
}

/**
 * 從既有 AppKit session 取得連線（不開彈窗、不觸發新授權）。
 * 行動版 deep-link 往返後頁面可能已被重載——原本的 connectViaWalletConnect
 * promise 已死，但 AppKit 會從 localStorage 恢復 session；這裡輪詢等它就緒
 * （含 relay 重新同步的時間），找到回 { address, provider }，找不到回 null。
 */
async function resumeWalletConnectSession(shouldAbort) {
    let appKit;
    try {
        appKit = await _ensureAppKit();
    } catch (e) {
        return null;
    }
    // 續登的典型觸發點（逾時收工後 2 秒／切回前景）頁面沒有重載——socket
    // 是凍結過的殭屍，relayer.connected 還報 true、訊息卻永遠收不到。
    // 此刻沒有 in-flight proposal（彈窗也沒開），onVisible 那 20 秒寬限的
    // 顧慮不成立，直接 force 重開，否則 personal_sign 送進死 socket，
    // 300 秒後才失敗（2026-09-10 徹查）。
    await ensureRelayConnected(appKit, { force: true });
    // 25s：init 完成後 relay 重同步（WS 重連、session settle 補送）在手機
    // 網路下可達十餘秒——10s 會在 session 剛要就緒時放棄，造成「錢包已連
    // 線、網頁卻沒接手」的卡死（2026-09-01 手機實測）
    return pollForConnection(appKit, { timeoutMs: 25000, shouldAbort });
}

/**
 * 背景預載：下載並初始化 AppKit（不開彈窗）。
 * 由 evm-auth.js 在使用者表現出登入意圖時（滑到／focus 登入鈕、登入視窗打開）
 * 呼叫，讓點下去時 AppKit 多半已就緒。初始化失敗靜默吞掉（點擊時再重試）。
 */
function preloadAppKit() {
    return _ensureAppKit().catch((err) => {
        console.warn('[evm-walletconnect] preload failed', err);
    });
}

/**
 * 真斷 AppKit/WalletConnect 連線（2026-09-05 DANNY「要徹底解決」：登出後
 * AppKit 面板不得再顯示「已連接」）。session 存 localStorage，須由 AppKit
 * API 清。AppKit 未初始化過（本頁生命週期內沒有活連線、chunk 也沒載）時
 * 不做任何事、回 false——persisted session 交由 pending 旗標在下次頁面
 * 載入（preload 初始化後）收尾，避免登出當下白載 7MB 又被 reload 截斷。
 */
async function disconnectWalletConnect() {
    if (!_initPromise) return false;
    const appKit = await _initPromise;
    const session = await readWcSession(appKit);
    // 內嵌錢包（Email／Google，PR-6）沒有 WC session，但 AppKit 仍連著——不斷的話
    // 同一台裝置下一個人按登入，會直接接上前一位的內嵌錢包（只剩簽名一步）。
    // 頁面剛載入時 AppKit 還在跟 iframe 恢復連線，要等它恢復完才判斷得準；
    // 等不到（pending）也先斷一次（本分頁別留著活的內嵌錢包），再回 false 把待斷線
    // 旗標留給下次確認。只有 social 模式才走這條，旗標關時邏輯跟以前一樣。
    const embedded = !session && _socialLoginAtInit ? await waitEmbeddedSettled(appKit) : 'none';
    if (embedded === 'pending') {
        try {
            if (typeof appKit.disconnect === 'function') await appKit.disconnect();
        } catch (e) { /* 還沒恢復完可能沒東西可斷——旗標留著下次收 */ }
        return false;
    }
    if (!session && embedded !== 'connected') return true; // 沒有活 session——已是乾淨狀態
    if (typeof appKit.disconnect === 'function') await appKit.disconnect();
    return true;
}

/**
 * Premium「刷卡買 USDC」（AppKit onramp，供應商 Meld）。
 * 買到的 USDC 會直接送進 AppKit 目前連著的地址——那個地址必須是綁定的付款錢包
 * （isPayer 由呼叫端用 resolvePaymentPayer 判），否則等於買錯地方、付款對不上帳。
 * 回傳 { ok:true, address } 或 { ok:false, reason:'disabled'|'not_connected'|'not_payer' }。
 */
async function openOnrampWith(appKit, opts) {
    const o = opts || {};
    if (!o.enabled) return { ok: false, reason: 'disabled' };
    const conn = readEvmConnection(appKit);
    if (!conn) return { ok: false, reason: 'not_connected' };
    if (typeof o.isPayer === 'function' && !o.isPayer(conn.address)) {
        return { ok: false, reason: 'not_payer', address: conn.address };
    }
    await appKit.open({ view: 'OnRampProviders' });
    return { ok: true, address: conn.address };
}

/** 內嵌錢包的帳戶畫面（餘額、Send、Receive）——Email／Google 使用者沒有別的錢包 App。 */
async function openEmbeddedWalletWith(appKit, opts) {
    if (!(opts && opts.enabled)) return { ok: false, reason: 'disabled' };
    const conn = readEvmConnection(appKit);
    if (!conn || !conn.embeddedWallet) return { ok: false, reason: 'not_embedded' };
    await appKit.open({ view: 'Account' });
    return { ok: true, address: conn.address };
}

async function openOnramp(isPayer) {
    const appKit = await _ensureAppKit();
    return openOnrampWith(appKit, { enabled: _socialLoginAtInit, isPayer });
}

async function openEmbeddedWallet() {
    const appKit = await _ensureAppKit();
    return openEmbeddedWalletWith(appKit, { enabled: _socialLoginAtInit });
}

/**
 * 不載 AppKit 的前提下看本頁是否已連著內嵌錢包（Premium 面板決定要不要給
 * 「開啟我的錢包」）。AppKit 沒初始化過就回 false——不為了這個下載 7MB。
 */
async function hasEmbeddedWallet() {
    if (!_initPromise) return false;
    try {
        const appKit = await _initPromise;
        return _socialLoginAtInit && readEmbeddedState(appKit) === 'connected';
    } catch (e) {
        return false;
    }
}

export {
    buildAppKitConfig,
    installNodeGlobals,
    setSiwxLoginHandler,
    connectViaWalletConnect,
    disconnectWalletConnect,
    enableWcDebug,
    _wcDebugAllowedByUrl,
    ensureRelayConnected,
    getConnectFinishReason,
    hasEmbeddedWallet,
    openEmbeddedWallet,
    openEmbeddedWalletWith,
    openOnramp,
    openOnrampWith,
    readEmbeddedState,
    waitEmbeddedSettled,
    pollForConnection,
    preloadAppKit,
    probePairingLiveness,
    readEvmConnection,
    readPairingSummary,
    readWcSession,
    resumeWalletConnectSession,
    reportWcConnectEvent,
    shouldRegenerateQr,
    closeConfirmationAction,
    _resetPingProbeForTests,
};
