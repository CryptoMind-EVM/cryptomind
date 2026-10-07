// ========================================
// evm-auth.js — EVM 錢包登入（SIWE personal_sign）
// multichain design Part B 前端。兩條路徑：
//   - 手機一般瀏覽器（無注入錢包）：WC deep-link 往返會丟 proposal／簽章
//     回應（Trust pairing 永不 active，2026-09-01/02 多錢包實測）——主路徑
//     引導帶本站 URL 跳去錢包 App 內建瀏覽器（EIP-6963 直連一鍵登入）；
//     WalletConnect 掃碼留為 fallback
//   - 其餘環境（桌機、錢包 App 內建瀏覽器）：直接開 Reown AppKit 官方彈窗
// 流程：連線 → GET /evm-nonce → personal_sign → POST /evm-login
//       （後端 eth_account 還原簽章者並發同一組 JWT cookie）
// ========================================

import {
    buildWalletBrowserLinks,
    detectMobileLike,
    isAndroid,
    isInsideWalletBrowser,
    isIos,
    shouldShowOpenInWallet,
    shouldSwallowGuideClick,
    toAndroidIntentHref,
} from './evm-mobile-guide.js';
import {
    discoverInjectedProviders,
    injectedWalletName,
    pickInjectedProvider,
    startInjectedDiscovery,
} from './evm-injected.js';
import {
    applyLogoutDisconnectFlags,
    hasPendingDisconnect,
    loginViaFor,
    shouldResendSignOnForeground,
    shouldShowInFlightHint,
    WC_PENDING_DISCONNECT_KEY,
} from './evm-login-feedback.js';
import { errorKind, isUserRejection, presentableMessage, userFacingMessage } from './error-message.js';
import { isStaleChunkError, recoverFromStaleChunk } from './stale-chunk-recovery.js';
import { applySocialLoginButton, resolveSocialLogin } from './social-login.js';

// 錢包在頁面載入當下就會主動廣播 EIP-6963——監聽器要在這裡（模組載入）掛上，
// 等使用者按按鈕才掛就漏接，注入錢包會被誤判成「沒有」。
startInjectedDiscovery();

let _evmLoginInFlight = false;
// 背景續登另計旗標：跟使用者主動按的流程共用一個旗標時，續登那 25 秒輪詢會
// 讓「連接錢包」按鈕完全沒反應（2026-09-01 手機回報）。使用者一按就要求續登
// 讓路，避免兩條流程各跑一次 nonce＋personal_sign。
let _wcResumeInFlight = false;
let _wcResumeAbort = false;

function _t(key, fallback) {
    if (window.I18n && typeof window.I18n.t === 'function') {
        const v = window.I18n.t(key);
        if (v && v !== key) return v;
    }
    return fallback;
}

// Email／Google 登入卡住被看門狗收掉：告訴使用者可以重試，而不是留一個無盡的轉圈
function _notifySocialStalled() {
    if (typeof showToast === 'function') {
        showToast(
            _t('evmAuth.socialStalled', 'Email / Google sign-in did not respond — please try again, or use a wallet'),
            'error'
        );
    }
}

function _utf8ToHex(str) {
    const bytes = new TextEncoder().encode(String(str));
    let out = '0x';
    for (let i = 0; i < bytes.length; i++) out += bytes[i].toString(16).padStart(2, '0');
    return out;
}

function _errText(e) {
    if (!e) return 'unknown error';
    return String(e.message || e.reason || e);
}

// 各家錢包的取消措辭（4001／ACTION_REJECTED／5000／'User denied'／imToken 的 'cancel'…）見 error-message.js；
// 登入、綁定、續登的 catch 全部走這支，不再各自用 `code === 4001 || /user rejected/i` 判斷。
function _isUserRejection(e) {
    return isUserRejection(e);
}

// 登入收場的遙測＋提示（2026-09-24 Base App：簽署視窗只顯示「錯誤狀態」，前端
// toast 完、後端一片空白，事後查不到原因；使用者重新整理後就登得進去）。
function _reportLoginOutcome(e) {
    const code = e && e.code != null ? ` code=${e.code}` : '';
    const st = e && e.status ? ` status=${e.status}` : '';
    _reportWcEvent(
        _isUserRejection(e) ? 'login-cancelled' : 'login-failed',
        `${_errText(e)}${code}${st}`
    );
}

// 失敗訊息一律帶「重新整理頁面後再試」；錢包給的原始訊息只在「看得懂」時附在括號裡方便回報
// （英文 SDK 訊息在非英文介面不外露——同一段原文已進 _reportLoginOutcome 的遙測）。
function _loginFailedMessage(e) {
    if (e && e.staleChunk)
        return _t('app.staleChunkRefresh', 'The app was updated — refresh the page and try again');
    const base = _t('evmAuth.verifyFailed', 'EVM login failed — refresh the page and try again');
    // 網路層失敗／逾時：直接說「網路不穩」，不要附 'Failed to fetch'
    const kind = errorKind(e);
    if (kind === 'network' || kind === 'timeout') return userFacingMessage(e);
    // api-client 的錯誤帶 status（逾時/5xx/原始 JSON）——不直出技術訊息（2026-09-10 徹查）
    if (!e || e.status || !e.message) return base;
    const detail = presentableMessage(e);
    return detail ? `${base}（${detail.slice(0, 120)}）` : base;
}

// WC 連線遙測（與 evm-walletconnect.js 的 reportWcConnectEvent 同款——這裡
// 不靜態 import 它，避免 Vite 把整個 AppKit 橋併進登入 chunk）。fire-and-
// forget：遙測絕不可影響登入流程。
function _reportWcEvent(mode, summary) {
    try {
        if (window.AppAPI && typeof window.AppAPI.post === 'function') {
            window.AppAPI.post('/api/user/wc-connect-event', {
                mode,
                summary: String(summary || '').slice(0, 300),
            }).catch(() => {});
        }
    } catch (e) { /* 遙測失敗不算事 */ }
}

// ---- 登入流程進行中的可見回饋（2026-09-05「點好幾次都沒反應」）----
let _lastInFlightHintAt = 0;

/**
 * 登入按鈕的三階段狀態（2026-09-06「空窗期瘋狂點」回報）：
 *   null＝正常（ETH 圖示＋「連接 EVM 錢包」）
 *   'connecting'＝點擊後全程（轉圈＋「連接錢包中…」）——點擊到任何可見
 *     回饋之間有數秒空窗（EIP-6963 探測／彈窗初始化），不反灰使用者會
 *     瘋狂點，防重入閘只回 toast 治標；按鈕本身要說話。
 *   'signing'＝簽名等待期（轉圈＋「等待錢包簽署…」，由 _setSignStatus 驅動）
 * 元素都在 index.html 靜態接線，這裡只切 hidden。壞掉不得影響登入。
 */
function _setEvmLoginButtonPhase(phase) {
    try {
        const icon = document.getElementById('evm-login-icon');
        const spinner = document.getElementById('evm-login-spinner');
        const label = document.getElementById('evm-login-label');
        const labelConnecting = document.getElementById('evm-login-label-connecting');
        const labelWait = document.getElementById('evm-login-label-wait');
        const busy = phase === 'connecting' || phase === 'signing';
        if (icon) icon.classList.toggle('hidden', busy);
        if (spinner) spinner.classList.toggle('hidden', !busy);
        if (label) label.classList.toggle('hidden', busy);
        if (labelConnecting) labelConnecting.classList.toggle('hidden', phase !== 'connecting');
        if (labelWait) labelWait.classList.toggle('hidden', phase !== 'signing');
        const btn = document.getElementById('evm-login-btn');
        if (btn) {
            btn.classList.toggle('opacity-70', busy);
            btn.classList.toggle('pointer-events-none', busy);
            btn.setAttribute('aria-busy', busy ? 'true' : 'false');
        }
    } catch (e) { /* noop */ }
}

/**
 * 簽名等待期的持久回饋（2026-09-05「點好幾次都沒反應」）——toast 幾秒就
 * 消失，但 personal_sign 可能等 150s＋自動重送 150s；這段期間登入 modal
 * 必須持續告訴使用者「流程活著、卡在等你簽」。
 *
 * 呈現（二版，DANNY 回饋「不要黃色大色塊、手機怎麼辦」）：狀態吃進
 * #evm-login-btn 本身——ETH 圖示換轉圈、文案換「等待錢包簽署…」，按鈕
 * 本來就 w-full 置中，手機自然適配；細節（逾時自動重送等）由按鈕下一行
 * muted 小字（#evm-sign-status）說明。等待是進行中資訊而非警告，不用
 * amber。元素都在 index.html 靜態接線，這裡只切 hidden。壞掉不影響登入。
 */
function _setSignStatus(textOrNull) {
    try {
        const statusEl = document.getElementById('evm-sign-status');
        if (statusEl) {
            const textEl = document.getElementById('evm-sign-status-text');
            if (textEl && textOrNull) textEl.textContent = textOrNull;
            statusEl.classList.toggle('hidden', !textOrNull);
        }
        _setEvmLoginButtonPhase(textOrNull ? 'signing' : null);
    } catch (e) { /* noop */ }
}

// 錢包名稱來自瀏覽器擴充功能／App 的 EIP-6963 廣播（非本站可控），
// 進 innerHTML 前一律轉義。
function _escapeHtml(str) {
    return String(str == null ? '' : str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function _applyEvmSession(syncResult, address) {
    const A = window.AuthManager;
    if (!A || !syncResult || !syncResult.user) return false;
    A.currentUser = {
        uid: syncResult.user.user_id,
        user_id: syncResult.user.user_id,
        username: syncResult.user.username,
        accessTokenExpiry: Date.now() + (A.TOKEN_EXPIRY_MS || 86400000),
        authMethod: syncResult.user.auth_method || 'evm_wallet',
        role: syncResult.user.role || 'user',
        membership_tier: syncResult.user.membership_tier || 'free',
        has_wallet: true,
        wallet_address: address,
    };
    A._saveUserSession();
    if (typeof A.markRecentLoginSuccess === 'function') A.markRecentLoginSuccess();
    if (typeof A.startTokenRefreshTimer === 'function') A.startTokenRefreshTimer();
    window.dispatchEvent(new Event('auth-success'));
    return true;
}

// One-Click Auth 走完時 AppKit 會回頭呼叫這裡：連線與簽名都已經在錢包那一趟
// 做完了，我們只剩把後端發回來的 session 套上去。
function _onSiwxLogin(result, address) {
    _setWcResumeFlag(false);
    if (_applyEvmSession(result, (result.user && result.user.wallet_address) || address)) {
        const modal = document.getElementById('login-modal');
        if (modal) modal.classList.add('hidden');
        if (typeof showToast === 'function')
            showToast(_t('evmAuth.loginSuccess', 'EVM wallet connected'), 'success');
    }
}

// 動態載入 WC 模組時一律先掛上 SIWX 收尾——AppKit 一初始化就可能從既有
// session 直接完成登入，晚掛就漏接。
async function _loadWalletConnect() {
    let mod;
    try {
        mod = await import('./evm-walletconnect.js');
    } catch (e) {
        // 部署後殘留的舊頁懶載入舊 hash chunk 會 404（2026-09-02 回報
        // exports-*.js Failed to fetch）。重載一次換新版（續登旗標在
        // localStorage，活得過重載）；救不回來時標記讓 catch 顯示友善訊息。
        const outcome = recoverFromStaleChunk(e);
        if (outcome === 'reloaded') e.handled = true; // 重載中，catch 別再 toast
        else if (outcome === 'blocked') e.staleChunk = true;
        throw e;
    }
    if (mod.setSiwxLoginHandler) mod.setSiwxLoginHandler(_onSiwxLogin);
    return mod;
}

// ---- WalletConnect 預載（使用者表現出意圖才載）----
// AppKit 是一整套錢包 SDK（約 60 個 chunk＋遠端設定），只在點擊時才載入會讓
// 第一次 EVM 登入卡在網路下載。以前開頁 2.5 秒後就對所有人背景預載——第一次來
// 的訪客多半用不到，卻跟首屏搶頻寬（2026-09-27 正式站訪客：~120 個請求、77 個
// 在 4 秒後）。改成滑到／按到／focus 登入或連錢包按鈕、或登入視窗打開時才載。
// 點擊路徑（safeEvmLogin → connectViaWalletConnect）本來就 await _ensureAppKit，
// 沒預載一樣連得上，只是多等下載。
let _wcWarmStarted = false;

function _warmWalletConnect() {
    if (_wcWarmStarted) return;
    _wcWarmStarted = true;
    // 登出當下被 reload 截斷的斷線在這裡收尾。順序關鍵：必須等 preload
    // 把 AppKit 初始化後才消費旗標——否則 disconnectWalletConnect 的
    // 未初始化防護直接回 false，旗標卻被清掉（閉環斷裂，session 殘留）。
    _loadWalletConnect()
        .then((m) => (m && m.preloadAppKit ? m.preloadAppKit() : null))
        .then(() => _consumePendingDisconnect())
        .catch(() => { /* 點擊時會重試 */ });
}

// Telegram Mini App 不能用 EVM 錢包（EVM 按鈕 data-tma-hide）；Base App／Farcaster
// 用宿主 provider 直接簽（safeEvmLogin 的 miniapp 分支）——兩邊都用不到 AppKit。
function _appKitUnused() {
    const cls = document.documentElement.classList;
    return cls.contains('tma') || cls.contains('miniapp');
}

(function _attachWalletConnectWarmers() {
    const onIntent = () => {
        if (!_appKitUnused()) _warmWalletConnect();
    };
    const attach = () => {
        // 登入視窗的 EVM 鈕、訪客橫幅的 Connect Wallet（會打開登入視窗）
        ['evm-login-btn', 'guest-connect-btn'].forEach((id) => {
            const btn = document.getElementById(id);
            if (!btn) return;
            btn.addEventListener('pointerenter', onIntent, { once: true });
            btn.addEventListener('focus', onIntent, { once: true });
            btn.addEventListener('touchstart', onIntent, { once: true, passive: true });
        });
        // 登入視窗打開就預載：開啟入口有十幾個（側欄、鎖定分頁、各頁登入提示…），
        // 做法一律是拿掉 hidden——看 class 變化一次涵蓋
        const modal = document.getElementById('login-modal');
        if (modal && typeof MutationObserver === 'function') {
            const obs = new MutationObserver(() => {
                if (modal.classList.contains('hidden')) return;
                obs.disconnect();
                onIntent();
            });
            obs.observe(modal, { attributes: true, attributeFilter: ['class'] });
        }
        // 上次登出沒斷完的錢包連線：AppKit 要先初始化才斷得掉（見
        // _consumePendingDisconnect），只有這種情況開頁就在閒置時預載
        let pendingDisconnect = false;
        try {
            pendingDisconnect = hasPendingDisconnect(window.localStorage);
        } catch (e) { /* sandbox iframe 讀 localStorage 會丟例外＝沒有待斷線 */ }
        if (pendingDisconnect) {
            if (window.requestIdleCallback) {
                requestIdleCallback(_warmWalletConnect, { timeout: 6000 });
            } else {
                setTimeout(_warmWalletConnect, 3000);
            }
        }
    };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', attach);
    } else {
        attach();
    }
})();

// ---- 錢包連線（官方 AppKit 彈窗）----

// ---- 手機「在錢包 App 內開啟」引導 ----
// 手機一般瀏覽器的主路徑（2026-09-02 定案）：WC deep link 往返會丟
// proposal／簽章回應（Trust 連不上、Coinbase 無限重簽），而錢包內建瀏覽器
// 開本站走 EIP-6963 直連一鍵登入。視覺複刻 AppKit 官方彈窗：白底圓角
// logo 磚＋扁平列表行（官方 logo 取自 App Store 圖標，web/img/wallets/）。
function _showOpenInWalletGuidance(injected) {
    const hasInjected = !!injected;
    const injectedName = injectedWalletName(injected, '');
    // 已在錢包內建瀏覽器（偵測到注入 provider，或 UA 特徵顯示在錢包
    // webview 內但偵測失敗）時，「跳去錢包 App」清單本身就是回圈：錢包列
    // 的 universal link 在錢包瀏覽器內只會重新開啟本站，「點錢包→開網頁→
    // 再點錢包」無限循環（2026-09-10 DANNY 朋友 iOS 實測）。
    const inWalletBrowser = hasInjected || isInsideWalletBrowser();
    return new Promise((resolve) => {
        // 任何殘留面板（例如流程被中斷）先清掉，避免兩層疊在一起
        const stale = document.getElementById('evm-open-in-wallet-guide');
        if (stale) stale.remove();
        const links = inWalletBrowser ? [] : buildWalletBrowserLinks(window.location.href, null, { ios: isIos() });
        const overlay = document.createElement('div');
        overlay.id = 'evm-open-in-wallet-guide';
        // 手機：底部滑出面板（AppKit 官方彈窗同款）；桌機：置中卡片
        overlay.className =
            'fixed inset-0 bg-background/95 backdrop-blur-xl z-[96] overflow-y-auto flex flex-col justify-end sm:justify-center px-0 sm:px-6 py-0 sm:py-10';
        // 已在錢包內建瀏覽器（有注入 provider）時，第一列是直接連接——
        // 使用者已經在錢包裡了，再引導跳別家錢包是鬼打牆
        const directRow = hasInjected
            ? `
            <button id="evm-guide-direct"
                class="evm-guide-direct-row group w-auto mx-4 mt-3 mb-2 flex items-center gap-4 px-4 py-3 bg-primary/10 hover:bg-primary/20 border border-primary/30 rounded-2xl text-left transition-colors duration-150">
                <span class="w-11 h-11 rounded-[0.7rem] bg-primary flex items-center justify-center shrink-0">
                    <svg class="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg">
                        <path d="M13 2 4.5 13.5H11L9.5 22 19 9.5h-6.5L13 2Z"/>
                    </svg>
                </span>
                <span>
                    <span class="block font-semibold text-textMain text-[15px]">${_t('evmAuth.directConnect', 'Connect the wallet in this browser directly')}</span>
                    <span class="block text-textMuted text-[11px]">${
                        injectedName ? `${_escapeHtml(injectedName)} · ` : ''
                    }${_t('evmAuth.directConnectDesc', 'A wallet is built into this browser — sign in without leaving')}</span>
                </span>
            </button>`
            : '';
        // UA 顯示在錢包內建瀏覽器、但注入偵測失敗（舊版錢包不支援 EIP-6963
        // 也不掛 window.ethereum）——講清楚並導向更新／掃碼，不出會回圈的清單
        const noInjectionNote = inWalletBrowser && !hasInjected
            ? `<div class="mx-4 mt-3 mb-1 px-4 py-3 bg-surfaceHighlight rounded-2xl">
                   <p class="text-textMuted text-xs leading-relaxed">${_t(
                       'evmAuth.inWalletNoInjection',
                       'You are inside a wallet browser, but no wallet connection was detected. Please update your wallet app and reload; or open this site in an outside browser and use the QR code below.'
                   )}</p>
               </div>`
            : '';
        // unsupportedNote：該錢包在這個平台沒有內建 dApp 瀏覽器（iOS 的
        // Trust），點下去只會停在錢包首頁。照列但講清楚，並提示改走掃碼。
        const noteText = _t(
            'evmAuth.iosNoInAppBrowser',
            'No in-app browser on iOS — use WalletConnect below instead'
        );
        const rows = links
            .map(
                (w) => `
            <button data-wallet-href="${w.href}" data-wallet-name="${w.name}" data-android-package="${w.androidPackage || ''}"
                class="evm-open-in-wallet-option group w-full flex items-center gap-4 px-5 py-3 text-left transition-colors duration-150 hover:bg-surfaceHighlight active:bg-surfaceHighlight${w.unsupportedNote ? ' opacity-60' : ''}">
                <span class="w-11 h-11 rounded-[0.7rem] bg-white shadow-sm flex items-center justify-center shrink-0 overflow-hidden">
                    <img src="/static/img/wallets/${w.id}.png" alt="" class="w-8 h-8 object-contain" loading="lazy">
                </span>
                <span class="min-w-0">
                    <span class="block font-semibold text-textMain text-[15px]">${w.name}</span>
                    ${w.unsupportedNote ? `<span class="block text-textMuted text-[11px] leading-snug">${noteText}</span>` : ''}
                </span>
                <svg class="w-4 h-4 text-textMuted ml-auto shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg">
                    <path d="M7 17L17 7M17 7H8M17 7v9"/>
                </svg>
            </button>`
            )
            .join('');
        overlay.innerHTML = `
            <style>
                @keyframes evmSheetUp { from { transform: translateY(28px); opacity: 0 } to { transform: translateY(0); opacity: 1 } }
                #evm-open-in-wallet-guide > div { animation: evmSheetUp .22s ease-out }
            </style>
            <div class="bg-surface w-full max-w-md mx-auto rounded-t-[1.75rem] sm:rounded-[1.75rem] shadow-2xl overflow-hidden">
                <div class="w-9 h-1 rounded-full bg-borderLight mx-auto mt-3 mb-1 sm:hidden"></div>
                <div class="px-5 pt-3 pb-3 sm:pt-6 flex items-start gap-3">
                    <div class="min-w-0">
                        <h3 class="font-serif text-lg font-semibold text-textMain leading-snug">${_t('evmAuth.openInWallet', 'Open inside your wallet app')}</h3>
                        <p class="text-textMuted text-xs mt-1 leading-relaxed">${_t(
                            'evmAuth.openInWalletDesc',
                            'Mobile browsers cannot complete the wallet signature round-trip safely. Open CryptoMind in the built-in browser of your wallet to connect directly.'
                        )}</p>
                    </div>
                    <button id="evm-guide-cancel" aria-label="${_t('common.cancel', 'Cancel')}"
                        class="w-11 h-11 -m-3 flex items-center justify-center rounded-xl text-textMuted hover:text-textMain transition shrink-0">
                        <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" xmlns="http://www.w3.org/2000/svg">
                            <path d="M6 6l12 12M18 6L6 18"/>
                        </svg>
                    </button>
                </div>
                <div class="px-5 pb-2 ${inWalletBrowser ? 'hidden' : ''}">
                    <div class="flex items-center gap-2.5 bg-surfaceHighlight rounded-xl px-3.5 py-2.5">
                        <svg class="w-4 h-4 text-textMuted shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg">
                            <circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>
                        </svg>
                        <input id="evm-guide-search" type="text" autocomplete="off"
                            placeholder="${_t('evmAuth.searchWallet', 'Search wallets…')}"
                            class="bg-transparent outline-none w-full text-sm text-textMain placeholder:text-textMuted">
                    </div>
                </div>
                <div id="evm-wallet-list" class="pb-1">${directRow}${noInjectionNote}${rows}</div>
                <div id="evm-guide-notfound" class="hidden px-5 pb-2 pt-1 ${inWalletBrowser ? 'hidden' : ''}">
                    <p class="text-textMuted text-xs leading-relaxed">${_t('evmAuth.walletNotFound', 'Do not see your wallet? Copy this site URL and open it in the built-in browser of your wallet app.')}</p>
                    <button id="evm-guide-copy-url"
                        class="mt-2 text-primary text-xs font-semibold hover:underline transition">
                        ${_t('evmAuth.copyUrlShort', 'Copy URL')}
                    </button>
                </div>
                <div class="px-5 py-3.5 ${hasInjected ? 'hidden' : ''}">
                    <button id="evm-guide-wc-fallback"
                        class="mx-auto block text-primary text-xs font-semibold hover:underline transition">
                        ${_t('evmAuth.useWalletConnect', 'Use WalletConnect (scan QR) instead')}
                    </button>
                </div>
            </div>`;
        document.body.appendChild(overlay);
        // 殘影 click 吞點：本面板是在 click-delegator 於 pointerup 合成派發的
        // 流程裡插進 DOM 的，跟在後面的原生 click 會 hit-test 到剛冒出來的面板，
        // 直接選中落點下的那一列——iOS 上登入按鈕正好壓在第一列 MetaMask，
        // 於是「點連接錢包直接跳小狐狸、選單完全沒出現」，回到錢包內建瀏覽器
        // 再點又跳同一個連結，變成無限循環（2026-09-08 DANNY 錄影）。
        // 沒在面板上按下過的 click 一律不算選擇，見 shouldSwallowGuideClick。
        let pressedInside = false;
        overlay.addEventListener('pointerdown', () => { pressedInside = true; }, true);
        overlay.addEventListener(
            'click',
            (e) => {
                if (!shouldSwallowGuideClick(pressedInside, e.detail)) return;
                e.preventDefault();
                e.stopPropagation();
            },
            true
        );
        const directBtn = overlay.querySelector('#evm-guide-direct');
        if (directBtn)
            directBtn.addEventListener('click', () => {
                overlay.remove();
                resolve({ direct: true });
            });
        overlay.querySelectorAll('.evm-open-in-wallet-option').forEach((btn) => {
            btn.addEventListener('click', () => {
                // Android：App Links 驗證在部分機型失效會落到官網下載頁——改用
                // intent:// 帶 package 直呼 App（沒安裝才落 fallback https 連結）。
                // iOS／桌機：universal link 原樣。
                const pkg = btn.dataset.androidPackage;
                const target =
                    pkg && isAndroid()
                        ? toAndroidIntentHref(btn.dataset.walletHref, pkg)
                        : btn.dataset.walletHref;
                // _self 導轉：錢包 universal link 會接管開啟內建瀏覽器
                window.location.href = target;
                // 導轉後本頁流程即結束——面板收掉、promise 放掉。原本刻意不
                // resolve 以「保持 in-flight」，代價是錢包沒被喚起（沒安裝／
                // intent 被擋）或使用者按返回回到本頁時，_evmLoginInFlight 永遠
                // 卡在 true，「連接錢包」按鈕從此完全沒反應（2026-09-02 回報
                // 「連結錢包都聯接不上」）。
                // 面板一起收掉是刻意的：留著的話它的「改用 WalletConnect」按鈕
                // 會對著已結束的 promise 空按一次沒反應——正是要修掉的那種死鍵。
                // 使用者返回本頁時看到的是登入視窗，再按一次就是全新一輪。
                overlay.remove();
                resolve(null);
            });
        });
        // 搜尋過濾：打不到的字就藏起來；全部藏光時顯示「複製網址」兜底
        const searchInput = overlay.querySelector('#evm-guide-search');
        const listEl = overlay.querySelector('#evm-wallet-list');
        const notFoundEl = overlay.querySelector('#evm-guide-notfound');
        searchInput.addEventListener('input', () => {
            const q = searchInput.value.trim().toLowerCase();
            let visible = 0;
            listEl.querySelectorAll('.evm-open-in-wallet-option').forEach((btn) => {
                const hit = !q || btn.dataset.walletName.toLowerCase().includes(q);
                btn.style.display = hit ? '' : 'none';
                if (hit) visible += 1;
            });
            notFoundEl.classList.toggle('hidden', visible > 0);
        });
        overlay.querySelector('#evm-guide-copy-url').addEventListener('click', async () => {
            try {
                await navigator.clipboard.writeText(window.location.href);
                if (typeof showToast === 'function')
                    showToast(_t('evmAuth.urlCopied', 'Copied — paste it into the built-in browser of your wallet app'), 'success');
            } catch (e) {
                if (typeof showToast === 'function')
                    showToast(window.location.href, 'info');
            }
        });
        overlay.querySelector('#evm-guide-wc-fallback').addEventListener('click', () => {
            overlay.remove();
            resolve({ wc: true });
        });
        overlay.querySelector('#evm-guide-cancel').addEventListener('click', () => {
            overlay.remove();
            resolve(null);
        });
    });
}

// provider.request 沒有內建逾時——手機 deep-link 往返中頁面凍結會錯過
// 回應，promise 永遠掛著讓流程卡死。逾時後放棄該次嘗試，請使用者再按
// 一次登入（既有 session 下連線瞬間完成，只重走失敗的那一步）。
function _withTimeout(promise, ms, message) {
    let timer = null;
    const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => {
            const err = new Error(message);
            err.code = 'EVM_TIMEOUT';
            reject(err);
        }, ms);
    });
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

/**
 * 前景重送簽名（2026-09-05 Trust 三連漏實證後的機制修正）。
 *
 * 手機 WC 流程：錢包 App 背景凍結會漏掉 relay 上的 personal_sign——連線
 * 全程健康、每發都送達 relay，錢包只在「發佈當下恰好在前景」的那一發
 * 彈窗（2026-09-05 14:45–14:51 線上：三發兩逾時，最後一發恰好在錢包
 * 醒著時送達才彈）。與其呆等 150s 逾時，偵測到使用者切回本頁
 * （visibilitychange→visible＝錢包剛被喚醒過的時刻）就立刻重發。
 *
 * 重發是新的 RPC id：await 必須跟著「最新一發」走，否則使用者簽了重發
 * 的提示、我們等的卻是舊 promise——永遠等不到。race() 內所有發佈都
 * attach 到同一個 settle（先完成者勝，舊的自然淘汰）。
 */
function _makeForegroundSignResender(requestSign) {
    let resendCount = 0;

    const race = (timeoutMs) =>
        new Promise((resolve, reject) => {
            let done = false;
            let timer = null;
            let listener = null;
            const settle = (fn, arg) => {
                if (done) return;
                done = true;
                if (timer) clearTimeout(timer);
                if (listener) document.removeEventListener('visibilitychange', listener);
                listener = null;
                fn(arg);
            };
            const attach = (p) => {
                p.then(
                    (v) => settle(resolve, v),
                    (e) => settle(reject, e)
                );
            };
            timer = setTimeout(() => {
                const err = new Error(
                    _t('evmAuth.signTimeout', 'Signature timed out — please tap EVM login and sign again')
                );
                err.code = 'EVM_TIMEOUT';
                settle(reject, err);
            }, timeoutMs);
            listener = () => {
                if (
                    !shouldResendSignOnForeground({
                        visible: document.visibilityState === 'visible',
                        waiting: !done,
                        resendCount,
                    })
                ) {
                    return;
                }
                resendCount++;
                _reportWcEvent('sign-resent', `visibilitychange foreground resend #${resendCount}`);
                _setSignStatus(_t(
                    'evmAuth.signResent',
                    'You are back — the signature request was re-sent, switch to your wallet app to sign'
                ));
                if (typeof showToast === 'function') {
                    showToast(_t('evmAuth.signHintMobile', 'Switch to your wallet app to sign'), 'info');
                }
                attach(requestSign());
            };
            document.addEventListener('visibilitychange', listener);
            attach(requestSign());
        });

    return { race };
}

async function _completeEvmLogin(provider, opts) {
    const viaWalletConnect = !!(opts && opts.viaWalletConnect);
    // Email／Google 內嵌錢包（PR-6）：AppKit 已經給了地址，而它的 provider 不收
    // eth_requestAccounts（會跳「Action not allowed」）——直接用；簽名照走 personal_sign
    const embeddedWallet = !!(opts && opts.embeddedWallet && opts.address);
    // 1. 取得使用者地址（觸發錢包授權彈窗）
    let accounts;
    if (embeddedWallet) {
        accounts = [opts.address];
    } else {
        try {
            accounts = await _withTimeout(
                provider.request({ method: 'eth_requestAccounts' }),
                45000,
                _t('evmAuth.requestTimeout', 'Wallet connection timed out — please tap EVM login again')
            );
        } catch (e) {
            if (e && e.code === 4001) throw e; // 使用者拒絕 → 上層靜默處理
            throw e;
        }
    }
    const address = accounts && accounts[0];
    if (!address) {
        throw new Error(_t('evmAuth.noAccount', 'Wallet returned no account'));
    }

    // 2. 向後端取得一次性 nonce 與要簽名的訊息（503 = MULTICHAIN_ENABLED 關閉）
    const challenge = await AppAPI.get(
        `/api/user/evm-nonce?address=${encodeURIComponent(address)}`
    );
    if (!challenge || !challenge.success || !challenge.message) {
        throw new Error(
            (challenge && challenge.detail) ||
                _t('evmAuth.nonceFailed', 'Unable to get login challenge')
        );
    }

    // 3. 錢包簽名（personal_sign / EIP-191）——簽名要 deep-link 跳去錢包，
    //    來回最久給 150 秒；頁面凍結錯過回應時逾時放棄，避免無限等待
    if (viaWalletConnect && typeof showToast === 'function') {
        // WC 手機流程：簽名請求進 relay 後，錢包 App 多半在背景凍結——不提
        // 示的話使用者盯著瀏覽器空等 150 秒直到逾時（2026-09-01 手機實測）
        showToast(_t('evmAuth.signHintMobile', 'Switch to your wallet app to sign'), 'info');
    }
    // toast 幾秒就消失，簽名卻可能等最長 ~300s（逾時＋自動重送）——modal
    // 內要有持久的狀態條，否則使用者會以為按鈕死了（2026-09-05「點好幾次
    // 都沒反應」回報）。WC 要去別的 App 簽；直連（錢包內建瀏覽器）的簽名
    // 彈窗就在本頁上方，但等待期一樣要有「在等錢包」的指示——直連只顯示
    // 「連接錢包中…」的話，150 秒空窗與卡死無法區分（2026-09-10 iOS 回報）。
    // 錢包自家瀏覽器（Base App／Coinbase Wallet）：簽名視窗直接疊在本頁，「請開啟錢包 App 看通知」
    // 那句會誤導（2026-09-13 DANNY：「這個視窗太醜」——按鈕與狀態列重複塞同一長句）
    // 內嵌錢包的簽名確認也疊在本頁（AppKit 彈窗），跟錢包自家瀏覽器同一句
    const inWalletBrowser = !!(opts && opts.inWalletBrowser) || embeddedWallet;
    _setSignStatus(
        viaWalletConnect
            ? _t(
                  'evmAuth.signWaiting',
                  'Signature request sent — complete it in your wallet app (it re-sends once on timeout)'
              )
            : inWalletBrowser
                ? _t('evmAuth.signWaitingInline', 'Confirm the sign-in request shown by your wallet')
                : _t('evmAuth.signWaitingShort', 'Waiting for the wallet signature…')
    );
    // personal_sign 的 message 依 EIP-191 JSON-RPC 慣例是 hex（MetaMask 也接受純字串，
    // 但 Base App／Coinbase Wallet 的原生 provider 只認 hex，純字串會回「Invalid message」，
    // 2026-09-13 手機實測）。先送 hex；錢包若不吃 hex（非使用者拒簽）再退回純字串一次。
    const signOnce = (msg) =>
        provider.request({ method: 'personal_sign', params: [msg, address] });
    const requestSign = async () => {
        try {
            return await signOnce(_utf8ToHex(challenge.message));
        } catch (e) {
            if (_isUserRejection(e)) throw e;
            _reportWcEvent('sign-hex-fallback', `hex personal_sign failed: ${_errText(e)}`);
            try {
                return await signOnce(challenge.message);
            } catch (e2) {
                // 兩種編碼都不收：把錢包的錯誤碼帶進訊息，畫面上才分得出是錢包還是後端
                const err = new Error(`${_errText(e2)} [personal_sign${e2 && e2.code != null ? ' ' + e2.code : ''}]`);
                err.code = e2 && e2.code;
                throw err;
            }
        }
    };
    // WC 路徑：前景重送（latest-wins race）；injected 直連不存在凍結漏包，
    // 維持原 _withTimeout。
    // resender 只建一次：每次 signWait 都重建會把 resendCount 歸零——逾時
    // 自動重試那一輪又有全新的 1+2 發預算，單次登入最多對錢包發 6 個
    // personal_sign（各自彈一次提示＝彈窗洗版，2026-09-10 徹查）。
    const fgResender = viaWalletConnect
        ? _makeForegroundSignResender(requestSign)
        : null;
    const signWait = viaWalletConnect
        ? (ms) => fgResender.race(ms)
        : (ms) => _withTimeout(
            requestSign(),
            ms,
            _t('evmAuth.signTimeout', 'Signature timed out — please tap EVM login and sign again')
        );
    let signature;
    try {
        try {
            signature = await signWait(150000);
        } catch (e) {
            // 逾時多半是簽署畫面沒有在錢包端顯示（2026-09-05 線上：簽名請求已
            // 送達 relay，使用者 150 秒看不到任何提示，只能盲重試）。連線是既有
            // session——自動重送一次＝再敲一次錢包，別讓使用者從頭再按一輪。
            if (!e || e.code !== 'EVM_TIMEOUT') throw e;
            _reportWcEvent('sign-timeout', 'personal_sign 150s timeout, auto retry once');
            _setSignStatus(viaWalletConnect
                ? _t(
                    'evmAuth.signRetry',
                    'The previous signature timed out — re-sent, please sign again in your wallet app'
                )
                : _t('evmAuth.signWaitingShort', 'Waiting for the wallet signature…'));
            if (viaWalletConnect && typeof showToast === 'function') {
                showToast(_t('evmAuth.signHintMobile', 'Switch to your wallet app to sign'), 'info');
            }
            signature = await signWait(150000);
        }
    } finally {
        _setSignStatus(null);
    }

    // 4. 後端驗章 + 發 JWT（回傳格式與 ton-login 一致）
    const result = await AppAPI.post('/api/user/evm-login', {
        address: address,
        signature: signature,
        nonce_token: challenge.nonce_token,
        login_via: loginViaFor(opts),
    });
    if (result && result.success) {
        if (_applyEvmSession(result, result.user.wallet_address)) {
            // 經典路徑成功也要清續登旗標——留著的話 15 分鐘內的頁面重載可能
            // 觸發一次多餘的續登輪詢（2026-09-10 徹查：狀態衛生）
            _setWcResumeFlag(false);
            const modal = document.getElementById('login-modal');
            if (modal) modal.classList.add('hidden');
            if (typeof showToast === 'function')
                showToast(_t('evmAuth.loginSuccess', 'EVM wallet connected'), 'success');
            return true;
        }
        throw new Error(_t('evmAuth.authNotReady', 'Session manager not ready'));
    }
    throw new Error(
        (result && result.detail) || _t('evmAuth.verifyFailed', 'EVM login failed')
    );
}

// 「登入進行中」的全域旗標。AuthManager.shouldDeferExpiredSessionCleanup() 與
// toolSettings 的 401 重試都靠它決定「現在別把過期 session 清掉／別當成真的
// 沒登入」。
//
// 2026-09-08：這個機制在 2026-08-31 登入改走 EVM 之後就斷了半個月——唯一的
// 寫入者是 ton-auth.js 的 safeTonLogin，而 TON 登入入口那天就被移除了。讀取端
// 兩處都還在，只是永遠讀到 undefined。沒有任何測試守它，所以沒人發現。
// EVM 登入用的 _evmLoginInFlight 是模組內變數，從來沒掛到 window 上。
//
// 舊名 _tonLoginInProgress 一併正名：登入早就跟 TON 無關了。
function _setLoginInFlight(on) {
    try {
        window.__loginInFlight = !!on;
        if (typeof AppStore !== 'undefined') AppStore.set('loginInProgress', !!on);
    } catch (e) { /* 旗標壞掉不能影響登入本身 */ }
}

window.safeEvmLogin = async function (opts) {
    // opts.social（PR-6）：從「用 Email 或 Google 繼續」進來——使用者多半沒有錢包，
    // 不彈手機「在錢包 App 內開啟」引導、也不走錢包自家瀏覽器直連，直接開 AppKit
    // 彈窗（Email／Google 在最上面）。簽名在 AppKit 的 iframe 內完成，不需要 deep link。
    // 只認 { social: true }：click-delegator 以無參數呼叫，不會誤觸。
    const social = !!(opts && opts.social === true);
    if (_evmLoginInFlight) {
        // 防重入閘不再是無聲的：流程卡在簽名（含自動重送，最長 ~300s）時，
        // 使用者點按鈕至少要知道流程還活著、卡在哪一步（節流 4s 防洗版）。
        if (shouldShowInFlightHint(_lastInFlightHintAt, Date.now())) {
            _lastInFlightHintAt = Date.now();
            if (typeof showToast === 'function') {
                showToast(
                    _t('evmAuth.loginInFlight', 'Sign-in in progress — return to your wallet app to sign, or try again later'),
                    'info'
                );
            }
        }
        return false;
    }
    if (_wcResumeInFlight) _wcResumeAbort = true; // 背景續登讓路給使用者
    _evmLoginInFlight = true;
    _setLoginInFlight(true);
    // 點擊瞬間反灰轉圈（2026-09-06「空窗期瘋狂點」）：從點擊到任何可見
    // 回饋（引導層／彈窗／簽名狀態）之間有數秒空窗，按鈕必須自己說話。
    // 所有出口都會走到底下的 finally 復原。
    _setEvmLoginButtonPhase('connecting');
    try {
        // 注入錢包偵測：EIP-6963 廣播優先，window.ethereum 只當退路。
        // 只看 window.ethereum 會漏掉 Bitget／幣安 Web3 等只走 6963 的錢包，
        // 於是在它們的內建瀏覽器裡「直接連接」列不出現，使用者只剩跳去別家
        // 錢包的清單、跳回自己 → 鬼打牆連不上（2026-09-02 回報）。
        const injected = pickInjectedProvider(
            await discoverInjectedProviders(),
            window.ethereum
        );

        // 手機類裝置一律先彈「在錢包 App 內開啟」引導（#603 驗證可用的主
        // 路徑）。不能拿有沒有注入當跳過依據：部分手機瀏覽器環境有注入
        // provider，跳過後就會落到 WC deep-link 死路（2026-09-02 回報
        // 「不會自動帶入跳轉到錢包內開我們網站」）。有注入（=已在錢包內建
        // 瀏覽器）時引導最上方加「直接連接」列，避免請使用者再跳一次錢包。
        // 桌機直接開官方彈窗。
        // Base App／Farcaster mini app 宿主給的 provider：不彈「在錢包 App 內開啟」、
        // 也不開 AppKit 彈窗（那裡沒有 WalletConnect 可用），直接用宿主 provider 走 SIWE
        if (
            injected &&
            (injected.source === 'miniapp' || (!social && injected.source === 'wallet-browser'))
        ) {
            return await _completeEvmLogin(injected.provider, { inWalletBrowser: true });
        }
        if (!social && shouldShowOpenInWallet(detectMobileLike(), 0)) {
            const choice = await _showOpenInWalletGuidance(injected);
            if (!choice) return false; // 使用者關閉引導
            if (choice.direct) {
                return await _completeEvmLogin(injected.provider);
            }
        }
        // 官方 AppKit 彈窗：注入錢包與 WalletConnect 都在同一個官方 UI 列出，
        // 連線建立後統一走經典 SIWE（nonce → personal_sign → evm-login）。
        _markWcResumeIntent();
        let wcModule;
        try {
            wcModule = await _loadWalletConnect();
        } catch (e) {
            // AppKit 是約 7MB 的動態 chunk：離線、CDN 卡住或部署後殘留舊 hash
            // 都會讓它載不起來，桌機使用者就整個「連不上錢包」。這時瀏覽器
            // 明明有注入錢包可用——直接走它，別讓一顆載不到的 chunk 擋死登入。
            // e.handled = recoverFromStaleChunk 已觸發整頁重載，這裡不要再起流程
            if (injected && !(e && e.handled)) {
                console.warn('[evm-auth] AppKit 載入失敗，改用注入錢包直連', e);
                _setWcResumeFlag(false);
                return await _completeEvmLogin(injected.provider);
            }
            throw e;
        }
        const { connectViaWalletConnect } = wcModule;
        const conn = await connectViaWalletConnect();
        if (!conn) {
            // 放棄 ≠ 失敗：session 可能只是慢到（deep-link 往返、
            // relay 重連）。踢一次續登輪詢 25s 接手；使用者真的沒
            // 連上時輪詢落空、旗標自清，不會循環。
            // 但「使用者按 X 關掉彈窗」的主動取消除外——取消後 2 秒跳
            // 「正在接手」、27 秒跳紅色「接手失敗」是誤導（2026-09-10 徹查）。
            const finishReason = wcModule.getConnectFinishReason
                ? wcModule.getConnectFinishReason()
                : '';
            if (finishReason === 'social-stalled') {
                // Email／Google 彈窗卡死、被看門狗收掉（2026-10-05）：沒有「晚到的
                // session」可接手，不踢續登；直接提示重試——重試時若 AppKit 其實
                // 已連上，會秒登入。
                _setWcResumeFlag(false);
                _notifySocialStalled();
            } else if (finishReason !== 'cancel') {
                setTimeout(() => {
                    _tryResumeWalletConnectLogin();
                }, 2000);
            }
            return false;
        }
        // One-Click Auth（?wc-oneclick=1 實驗開關）走完時連線與簽名都已在
        // 錢包那一趟做完，收尾也跑過了
        if (conn.siwxLoggedIn) {
            const m = document.getElementById('login-modal');
            if (m) m.classList.add('hidden');
            return true;
        }
        return await _completeEvmLogin(conn.provider, {
            viaWalletConnect: !!conn.viaWalletConnect,
            embeddedWallet: !!conn.embeddedWallet,
            authProvider: conn.authProvider,
            address: conn.address,
        });
    } catch (e) {
        if (!(e && e.handled)) _reportLoginOutcome(e);
        if (_isUserRejection(e)) {
            if (typeof showToast === 'function')
                showToast(_t('evmAuth.loginCancelled', 'Login cancelled'), 'info');
            return false;
        }
        if (e && e.handled) return false; // 過期 chunk：已觸發整頁重載，不用再提示
        console.error('[evm-auth] login failed', e);
        if (typeof showToast === 'function') showToast(_loginFailedMessage(e), 'error');
        return false;
    } finally {
        _evmLoginInFlight = false;
        _setLoginInFlight(false);
        _setEvmLoginButtonPhase(null);
    }
};

// 登入視窗「用 Email 或 Google 繼續」（index.html #social-login-btn，data-click 委派）
window.safeSocialLogin = function () {
    return window.safeEvmLogin({ social: true });
};

// 按鈕預設 hidden：旗標開＋一般網頁才顯示（判定與 AppKit 設定共用 resolveSocialLogin）
(function _attachSocialLoginButton() {
    const apply = () => {
        resolveSocialLogin()
            .then((on) => applySocialLoginButton(on))
            .catch(() => { /* 判定失敗＝不顯示 */ });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', apply);
    else apply();
})();

// ---- 綁定額外 EVM 錢包（付款人白名單）----
// 付款驗證只認「已綁定地址」送出的 USDC（api/routers/premium.py 的 payer
// binding，fail-closed）。EVM 登入者登入的那顆地址會自動綁；Telegram 登入者
// 以前完全沒有入口——Trust 分頁那顆「綁定」寫的是信任護照欄位，不是
// user_wallets，付款不認。這裡走跟登入一樣的連線路徑（注入直連／錢包內
// 建瀏覽器引導／AppKit），只是最後打 /api/user/wallets/bind 而不是
// evm-login（2026-09-11 盤查）。
async function _completeEvmBind(provider, embeddedAddress) {
    // 內嵌錢包（Email／Google）不收 eth_requestAccounts——用 AppKit 給的地址（同登入）
    const accounts = embeddedAddress
        ? [embeddedAddress]
        : await _withTimeout(
              provider.request({ method: 'eth_requestAccounts' }),
              45000,
              _t('evmAuth.requestTimeout', 'Wallet connection timed out — please tap again')
          );
    const address = accounts && accounts[0];
    if (!address) throw new Error(_t('evmAuth.noAccount', 'Wallet returned no account'));

    const challenge = await AppAPI.get(
        `/api/user/evm-nonce?address=${encodeURIComponent(address)}`
    );
    if (!challenge || !challenge.success || !challenge.message) {
        throw new Error(
            (challenge && challenge.detail) || _t('evmAuth.nonceFailed', 'Unable to get login challenge')
        );
    }
    if (typeof showToast === 'function')
        showToast(_t('evmAuth.bindSignHint', 'Sign in your wallet to bind this address'), 'info');
    const signature = await _withTimeout(
        provider.request({ method: 'personal_sign', params: [challenge.message, address] }),
        150000,
        _t('evmAuth.signTimeout', 'Signature timed out — please try again')
    );
    const result = await AppAPI.post('/api/user/wallets/bind', {
        address: address,
        signature: signature,
        nonce_token: challenge.nonce_token,
    });
    if (!result || !result.success) {
        throw new Error((result && result.detail) || _t('evmAuth.bindFailed', 'Wallet binding failed'));
    }
    const bound = (result.wallet && result.wallet.address) || address;
    if (typeof showToast === 'function')
        showToast(_t('evmAuth.bindSuccess', 'Wallet bound — you can pay from it now'), 'success');
    try {
        window.dispatchEvent(new CustomEvent('evm:wallet-bound', { detail: { address: bound } }));
    } catch (e) { /* 事件派發失敗不影響綁定結果 */ }
    return bound;
}

// 回傳綁定成功的地址；取消／失敗回 false。與登入共用防重入閘。
window.safeEvmBind = async function () {
    if (_evmLoginInFlight) {
        if (shouldShowInFlightHint(_lastInFlightHintAt, Date.now())) {
            _lastInFlightHintAt = Date.now();
            if (typeof showToast === 'function')
                showToast(_t('evmAuth.loginInFlight', 'Login in progress — finish signing in your wallet, or try again later'), 'info');
        }
        return false;
    }
    _evmLoginInFlight = true;
    _setLoginInFlight(true);
    try {
        const injected = pickInjectedProvider(await discoverInjectedProviders(), window.ethereum);
        let provider = null;
        let embeddedAddress = null;
        if (injected && (injected.source === 'miniapp' || injected.source === 'wallet-browser')) {
            provider = injected.provider; // mini app 宿主／錢包自家瀏覽器：直接用，不彈引導
        } else if (shouldShowOpenInWallet(detectMobileLike(), 0)) {
            const choice = await _showOpenInWalletGuidance(injected);
            if (!choice) return false; // 關閉引導，或已導去錢包 App（回來再按一次）
            if (choice.direct) provider = injected.provider;
        }
        if (!provider) {
            let wcModule = null;
            try {
                wcModule = await _loadWalletConnect();
            } catch (e) {
                // AppKit chunk 載不到但有注入錢包：直接用（同登入路徑）
                if (injected && !(e && e.handled)) provider = injected.provider;
                else throw e;
            }
            if (!provider) {
                const conn = await wcModule.connectViaWalletConnect();
                if (!conn) {
                    if (wcModule.getConnectFinishReason && wcModule.getConnectFinishReason() === 'social-stalled') {
                        _notifySocialStalled();
                    }
                    return false;
                }
                provider = conn.provider;
                if (conn.embeddedWallet) embeddedAddress = conn.address;
            }
        }
        return await _completeEvmBind(provider, embeddedAddress);
    } catch (e) {
        if (_isUserRejection(e)) {
            if (typeof showToast === 'function') showToast(_t('evmAuth.loginCancelled', 'Cancelled'), 'info');
            return false;
        }
        if (e && e.handled) return false;
        console.error('[evm-auth] bind failed', e);
        if (typeof showToast === 'function') {
            let msg;
            if (e && e.status === 409) msg = _t('evmAuth.bindConflict', 'This address is already bound to another account');
            else if (e && e.status) msg = _t('evmAuth.bindFailed', 'Wallet binding failed, please retry');
            // 沒有 status＝網路層（'Failed to fetch'）、逾時、錢包 SDK 的英文訊息：過一層顯示文案
            else msg = userFacingMessage(e, { fallbackKey: 'evmAuth.bindFailed', fallback: 'Wallet binding failed, please retry' });
            showToast(msg, 'error');
        }
        return false;
    } finally {
        _evmLoginInFlight = false;
        _setLoginInFlight(false);
    }
};

// ---- WalletConnect 行動版斷線續登 ----
// 手機 deep-link 跳去錢包 App 批准後回到瀏覽器，頁面常被重載（或凍結後
// 逾時窗口已耗盡），原本的 connectViaWalletConnect promise 已死；AppKit
// 會從 localStorage 恢復 session，卻沒人接手後續 SIWE 登入 → 使用者看著
// 已連線的錢包卻登不進來。解法：進 WC 流程前寫入 localStorage 意圖
// 旗標（sessionStorage 會隨 webview 銷毀蒸發，localStorage 才活得過
// deep-link 往返）；頁面啟動與恢復可見時，若旗標在＋尚未登入＋有既有
// session，自動接手 _completeEvmLogin（nonce → personal_sign → evm-login）。
// 注意：personal_sign 一樣要跳去錢包籤一次；若該往返中頁面又被重載，
// 簽名回應無法回收（WalletConnect 限制），重按登入即可——連線已是
// 既有 session，只剩簽名一步。
// 旗標必須放 localStorage 而非 sessionStorage：Telegram TMA／手機瀏覽器在
// deep-link 往返時常銷毀 WebView，sessionStorage 是記憶體型、隨 webview 蒸發；
// 頁面回來後 AppKit 能從 localStorage 恢復 session，續登旗標卻已不在——
// 使用者看著已連線的錢包永遠卡在錢包頁（2026-09-01 手機實測）。帶時間戳
// ＋TTL，廢棄意圖 15 分鐘自清，不會幽靈殘留。
const WC_RESUME_FLAG = 'evmWcResumeAt';
const WC_RESUME_TTL_MS = 15 * 60 * 1000;

function _setWcResumeFlag(on) {
    try {
        if (on) localStorage.setItem(WC_RESUME_FLAG, String(Date.now()));
        else localStorage.removeItem(WC_RESUME_FLAG);
    } catch (e) { /* 隱私模式等 localStorage 不可用——放棄續登能力 */ }
}

function _hasWcResumeFlag() {
    try {
        const at = Number(localStorage.getItem(WC_RESUME_FLAG));
        if (!at) return false;
        if (Date.now() - at > WC_RESUME_TTL_MS) {
            localStorage.removeItem(WC_RESUME_FLAG);
            return false;
        }
        return true;
    } catch (e) {
        return false;
    }
}

function _markWcResumeIntent() {
    _setWcResumeFlag(true);
}

// ---- 登出真斷錢包連線（2026-09-05 DANNY「要徹底解決」，方案 B＋閉環）----
// 決策記錄：docs/plans/2026-09-05-logout-wallet-disconnect-design.md。
// 登出後 AppKit 面板不得再顯示「已連接」；但登出隨即 reload，當下的斷線
// 呼叫（relay 往返）常被截斷——以 pending 旗標在下次頁面載入收尾，閉環。

function _clearPendingDisconnect() {
    try {
        localStorage.removeItem(WC_PENDING_DISCONNECT_KEY);
    } catch (e) { /* noop */ }
}

/**
 * 登出前呼叫（fire-and-forget，絕不延遲登出——登出必須即時，否則重蹈
 * 「點了沒反應」）：
 * 1) 廢續登旗標＋設待斷線旗標（applyLogoutDisconnectFlags，純函數有測）
 * 2) 盡力現在就斷——AppKit 已 preload 時只是一個 relay 往返，通常來得及
 * 3) 確認斷線完成 → 清旗標；被 reload 截斷或失敗 → 旗標留給
 *    _consumePendingDisconnect 下次收尾
 *
 * 刻意用裸 import 而非 _loadWalletConnect()：後者的過期 chunk 救濟會
 * 觸發整頁 reload，可能攔截登出的後端 POST（cookie 清不掉→變沒登出）。
 */
export function prepareEvmWalletForLogout() {
    applyLogoutDisconnectFlags(
        typeof localStorage !== 'undefined' ? localStorage : null
    );
    import('./evm-walletconnect.js')
        .then((m) => (m.disconnectWalletConnect ? m.disconnectWalletConnect() : false))
        .then((done) => {
            if (done === true) _clearPendingDisconnect();
        })
        .catch(() => { /* 旗標留著——下次頁面載入收尾 */ });
}

/**
 * 頁面載入時消化待斷線旗標（由 _warmWalletConnect 在 preload 完成後呼叫
 * ——AppKit 已初始化，disconnect 不必再等 chunk 下載）。
 * 只有確認斷線完成（回 true）才清旗標；AppKit 初始化失敗／離線等場景
 * 旗標留著下次再試，滯留無副作用（重複 disconnect 冪等）。
 */
async function _consumePendingDisconnect() {
    if (!hasPendingDisconnect(typeof localStorage !== 'undefined' ? localStorage : null)) return;
    try {
        const m = await import('./evm-walletconnect.js');
        const done = m.disconnectWalletConnect
            ? await m.disconnectWalletConnect()
            : false;
        if (done === true) _clearPendingDisconnect();
    } catch (e) {
        return; // 旗標留著，下次再試
    }
}

function _evmAlreadyLoggedIn() {
    const u = window.AuthManager && window.AuthManager.currentUser;
    return !!(u && (u.user_id || u.uid));
}

async function _tryResumeWalletConnectLogin() {
    if (_evmLoginInFlight || _wcResumeInFlight) return; // 原流程還活著——不要雙軌開火
    if (!_hasWcResumeFlag()) return;
    if (_evmAlreadyLoggedIn()) {
        // 已登入（例如另一個分頁完成）——旗標任務結束
        _setWcResumeFlag(false);
        return;
    }
    _wcResumeInFlight = true;
    _wcResumeAbort = false;
    _setLoginInFlight(true);
    // 續登全程原本無聲：回跳後頁面重載、AppKit 還要在 4G 下載七 MB chunk、
    // relay 重連、輪詢等 session——加起來幾十秒，使用者只看到靜止畫面（慢網
    // 路下是黑屏），不知道系統正在接手，只能斷定「根本沒解決」。至少讓接手
    // 這件事被看見；卡超過 12 秒展開 wc-debug 面板（僅本機開發會自動展開，
    // 正式站取證走 ?wc-debug=1，見 evm-walletconnect.js 的 enableWcDebug）。
    let stuckTimer = null;
    try {
        const { resumeWalletConnectSession, enableWcDebug } = await _loadWalletConnect();
        if (typeof showToast === 'function')
            showToast(_t('evmAuth.resuming', 'Resuming your previous wallet sign-in…'), 'info');
        stuckTimer = setTimeout(() => enableWcDebug(), 12000);
        const conn = await resumeWalletConnectSession(() => _wcResumeAbort);
        if (_wcResumeAbort) return; // 使用者已自己接手，旗標留給那條流程
        if (!conn) {
            // 沒有既有 session（使用者其實沒批准／session 過期）——放掉旗標
            _setWcResumeFlag(false);
            if (typeof showToast === 'function')
                showToast(
                    _t('evmAuth.resumeFailed', 'Could not resume the previous wallet sign-in, tap EVM login again'),
                    'error'
                );
            return;
        }
        // 進入登入收尾（nonce＋簽名最長可達 5 分鐘）：佔用 _evmLoginInFlight，
        // 讓使用者此時點「連接錢包」被防重入閘擋下（提示流程進行中），而不是
        // 兩條流程並行各跳一次簽名、互踩全域旗標（2026-09-10 徹查）。同時把
        // 隱藏中的 login modal 打開——deep-link 回來頁面已重載，簽名進度寫在
        // modal 裡，不開等於全程無聲。
        if (_evmLoginInFlight) return; // 競態尾窗：使用者流程剛起，讓路
        _evmLoginInFlight = true;
        const modal = document.getElementById('login-modal');
        if (modal) modal.classList.remove('hidden');
        _setEvmLoginButtonPhase('connecting');
        try {
            // 內嵌錢包（Email／Google）也可能在這裡被接手：它不是 WC、不收 eth_requestAccounts
            await _completeEvmLogin(conn.provider, {
                viaWalletConnect: !conn.embeddedWallet,
                embeddedWallet: !!conn.embeddedWallet,
                authProvider: conn.authProvider,
                address: conn.address,
            });
        } finally {
            _evmLoginInFlight = false;
            _setEvmLoginButtonPhase(null);
        }
        _setWcResumeFlag(false);
    } catch (e) {
        if (e && e.handled) {
            // 過期 chunk：recoverFromStaleChunk 已觸發整頁重載，
            // 重載後續登旗標還在（localStorage），新版 chunk 會重新接手——
            // 這裡絕不能清旗標，否則重載後沒人接手（2026-09-10 徹查：原版
            // 第一行無條件清掉，閉環是斷的）。
            return;
        }
        _setWcResumeFlag(false);
        _reportLoginOutcome(e);
        if (_isUserRejection(e)) {
            if (typeof showToast === 'function')
                showToast(_t('evmAuth.loginCancelled', 'Login cancelled'), 'info');
        } else {
            console.error('[evm-auth] WC resume failed', e);
            if (typeof showToast === 'function') showToast(_loginFailedMessage(e), 'error');
        }
    } finally {
        _wcResumeInFlight = false;
        _setLoginInFlight(false);
        if (stuckTimer) clearTimeout(stuckTimer);
    }
}

(function _attachWcResumeHooks() {
    // 啟動接手：等 AuthManager 的 /api/user/me 判定先跑（AppKit 由接手流程自己載）
    const kick = (delay) => {
        setTimeout(() => {
            _tryResumeWalletConnectLogin();
        }, delay);
    };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => kick(3500));
    } else {
        kick(3500);
    }
    // 從錢包 App 切回來（頁面沒被重載、但原 promise 死掉／逾時的場景）
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') kick(300);
    });
    window.addEventListener('focus', () => kick(300));
})();
