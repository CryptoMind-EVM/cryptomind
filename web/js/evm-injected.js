// ========================================
// evm-injected.js — 注入錢包偵測（EIP-6963 + 傳統 window.ethereum）
//
// 背景（2026-09-02 回報「連結錢包都聯接不上」）：evm-auth 判斷「這個瀏覽器
// 有沒有錢包」只看 window.ethereum，但現代錢包（Bitget／幣安 Web3／部分
// MetaMask 版本、以及同時裝多個錢包的桌機）改走 EIP-6963 廣播、不再保證
// 掛 window.ethereum。結果：
//   - 手機在錢包 App 內建瀏覽器開本站 → 偵測不到注入 → 引導面板不顯示
//     「直接連接」列 → 只剩「跳去別的錢包 App」的清單 → 跳回自己 → 鬼打牆，
//     使用者永遠連不上（引導文案還寫著 EIP-6963 直連，但程式沒實作）。
//   - 桌機多錢包共存時 window.ethereum 被搶走，拿到的不是使用者選的那顆。
//
// EIP-6963 流程：dapp 派發 eip6963:requestProvider，錢包以
// eip6963:announceProvider 回應（detail = { info, provider }）。錢包也會在
// 頁面載入時主動廣播一次，所以監聽器必須在模組載入當下就掛上，晚掛就漏接。
//
// 本模組所有函式都吃可注入的 window-like 物件，供 node 測試直接驗。
// ========================================

const _announced = new Map();

function _win(winLike) {
    if (winLike) return winLike;
    return typeof window !== 'undefined' ? window : null;
}

function _key(info) {
    return (info && (info.uuid || info.rdns || info.name)) || null;
}

function _onAnnounce(event) {
    const detail = event && event.detail;
    if (!detail || !detail.provider || !detail.info) return;
    const key = _key(detail.info);
    if (!key) return;
    // rdns 相同視為同一顆錢包（重複廣播時後到的覆蓋，拿最新的 provider）
    _announced.set(key, { info: detail.info, provider: detail.provider });
}

/** 派發 requestProvider，請已載入的錢包（重新）廣播自己。 */
export function requestInjectedProviders(winLike) {
    const w = _win(winLike);
    if (!w || typeof w.dispatchEvent !== 'function') return false;
    try {
        const EventCtor = w.Event || (typeof Event !== 'undefined' ? Event : null);
        w.dispatchEvent(
            EventCtor ? new EventCtor('eip6963:requestProvider') : { type: 'eip6963:requestProvider' }
        );
        return true;
    } catch (e) {
        return false;
    }
}

/**
 * 掛上廣播監聽並要求一次廣播。模組載入時就要呼叫——錢包在頁面載入當下
 * 主動廣播的那一次，晚掛的監聽器收不到。重複呼叫安全（旗標擋住）。
 */
export function startInjectedDiscovery(winLike) {
    const w = _win(winLike);
    if (!w || typeof w.addEventListener !== 'function') return false;
    if (w.__evmInjectedDiscoveryStarted) return true;
    w.__evmInjectedDiscoveryStarted = true;
    w.addEventListener('eip6963:announceProvider', _onAnnounce);
    requestInjectedProviders(w);
    return true;
}

/** 目前已廣播過的錢包清單（同步，不等待）。 */
export function listInjectedProviders() {
    return [..._announced.values()];
}

export function _resetInjectedDiscoveryForTests(winLike) {
    _announced.clear();
    const w = _win(winLike);
    if (w) w.__evmInjectedDiscoveryStarted = false;
}

/**
 * 重新要求廣播並給錢包一個極短的回應窗（預設 250ms）。
 * 錢包的 announce 是同步派發，250ms 綽綽有餘；再長只是讓使用者按了按鈕
 * 乾等，注入錢包早就在 startInjectedDiscovery 那次收齊了。
 */
export function discoverInjectedProviders(opts) {
    const options = opts || {};
    const w = _win(options.win);
    const waitMs = typeof options.waitMs === 'number' ? options.waitMs : 250;
    startInjectedDiscovery(w);
    requestInjectedProviders(w);
    return new Promise((resolve) => {
        const timer = (w && w.setTimeout) || setTimeout;
        timer(() => resolve(listInjectedProviders()), waitMs);
    });
}

/**
 * 從 EIP-6963 清單與傳統 window.ethereum 挑一顆可用的 provider。
 * 回傳 { provider, info, source } 或 null。
 *
 * 優先序：
 *   1. EIP-6963 廣播（現代錢包唯一保證的管道，多錢包共存時每顆都在）
 *   2. window.ethereum.providers[]（舊版多錢包共存陣列，挑第一顆）
 *   3. window.ethereum 本身
 * 只認得 request() 的物件——部分瀏覽器注入的是不能發請求的殘缺 stub，
 * 當成錢包會讓 eth_requestAccounts 直接爆掉。
 */
export function pickInjectedProvider(announced, legacyEthereum) {
    // Base App／Farcaster mini app 宿主給的 provider（miniapp-host.js）永遠優先：
    // 那個環境沒有 window.ethereum，也不該再彈「在錢包 App 內開啟」
    const host = typeof window !== 'undefined' ? window.__miniAppEthereumProvider : null;
    if (host && typeof host.request === 'function') {
        return { provider: host, info: { name: 'Base App' }, source: 'miniapp' };
    }
    const list = Array.isArray(announced) ? announced : [];
    const usable = list.filter((e) => e && e.provider && typeof e.provider.request === 'function');
    // Base App 內建瀏覽器（2026-04-09 起 Base App 把所有 app 當一般網頁開）：注入的是
    // Coinbase Wallet provider，這裡就是錢包自己的瀏覽器，直接連、不再彈「在錢包 App 內開啟」
    const walletBrowser = [
        ...usable.map((e) => e.provider),
        legacyEthereum,
        ...((legacyEthereum && Array.isArray(legacyEthereum.providers)) ? legacyEthereum.providers : []),
    ].find((p) => p && typeof p.request === 'function' && (p.isCoinbaseWallet || p.isBaseApp));
    if (walletBrowser) {
        return { provider: walletBrowser, info: { name: 'Base App' }, source: 'wallet-browser' };
    }
    if (usable.length) {
        return { provider: usable[0].provider, info: usable[0].info, source: 'eip6963' };
    }
    const legacy = legacyEthereum;
    if (legacy && Array.isArray(legacy.providers)) {
        const first = legacy.providers.find((p) => p && typeof p.request === 'function');
        if (first) return { provider: first, info: null, source: 'legacy-multi' };
    }
    if (legacy && typeof legacy.request === 'function') {
        return { provider: legacy, info: null, source: 'legacy' };
    }
    return null;
}

/** 顯示用名稱：EIP-6963 有 info.name，傳統注入只能給通用字樣。 */
export function injectedWalletName(picked, fallback) {
    if (picked && picked.info && picked.info.name) return String(picked.info.name);
    return fallback || '';
}
