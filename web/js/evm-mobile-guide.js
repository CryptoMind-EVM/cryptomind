// ========================================
// evm-mobile-guide.js — 手機瀏覽器「改在錢包 App 內建瀏覽器開啟」引導（純函式）
//
// 背景（2026-09-02 DANNY 三錢包實測）：手機瀏覽器走 WalletConnect deep link
// 是結構性死結——連線 proposal 與 personal_sign 簽章回應都會在「跳去錢包
// App→頁面凍結/重載」的往返中丟失（Trust：pairing 永不 active；Coinbase：
// 連上但無限重複簽章）。同一支手機在錢包 App 內建瀏覽器開啟本站則
// EIP-6963 注入直連、全程不跳 App，一鍵登入（幣安實測通）。
// 業界標準（Uniswap 等）：手機瀏覽器引導「在錢包內開啟」為主路徑。
//
// 本模組不碰 DOM，供 evm-auth.js 與 node 測試共用。
// ========================================

// 各錢包 Android App 的 package name——intent:// 直呼用。App Links（https
// universal link）在部分 Android 機型（尤其中文 ROM）會驗證失效，fallback
// 到官網下載頁，使用者以為我們說他沒裝錢包（2026-09-02 回報）。intent 帶
// package 直呼已安裝的 App 不需任何驗證；沒安裝才落 S.browser_fallback_url。
export const ANDROID_PACKAGES = {
    trust: 'com.wallet.crypto.trustapp',
    metamask: 'io.metamask',
    bitget: 'com.bitkeep.wallet',
    coinbase: 'org.toshi',
    binance: 'com.binance.dev',
};

// 把 https universal link 包成 Android intent:// 形式：
// 同一個 https URL 改由指定 App 開啟（scheme=https + package），
// App 沒安裝時瀏覽器自动落 S.browser_fallback_url（原 https 連結）。
export function toAndroidIntentHref(httpsHref, androidPackage) {
    if (!httpsHref || !androidPackage || !httpsHref.startsWith('https://')) {
        return httpsHref;
    }
    const withoutScheme = httpsHref.slice('https://'.length);
    const fallback = encodeURIComponent(httpsHref);
    return (
        `intent://${withoutScheme}#Intent;scheme=https;package=${androidPackage};` +
        `S.browser_fallback_url=${fallback};end`
    );
}

export function isAndroid(navigatorLike) {
    const nav = navigatorLike || (typeof navigator !== 'undefined' ? navigator : null);
    if (!nav) return false;
    return /Android/i.test(String(nav.userAgent || ''));
}

// iPadOS 13+ 的 Safari 把自己報成 Macintosh，只有觸控點數分得出來。
export function isIos(navigatorLike) {
    const nav = navigatorLike || (typeof navigator !== 'undefined' ? navigator : null);
    if (!nav) return false;
    const ua = String(nav.userAgent || '');
    if (/iPhone|iPad|iPod/i.test(ua)) return true;
    return /Macintosh/.test(ua) && Number(nav.maxTouchPoints || 0) > 1;
}

// 錢包 App 內建瀏覽器的 UA 特徵（webview 會帶自家 token）。
// 用途是「注入偵測失敗」時的兜底：UA 顯示已在錢包內建瀏覽器，就算
// EIP-6963／window.ethereum 都沒偵測到，「跳去錢包 App」的清單也不能列
// ——錢包列的 universal link 在錢包瀏覽器內只會重新開啟本站，形成
// 「點錢包→開網頁→再點錢包」的無限回圈（2026-09-10 DANNY 朋友 iOS
// 實測）。清單缺某個錢包的 token 只是該錢包少一層保護，不會誤傷。
export const WALLET_UA_TOKENS = ['MetaMask', 'Bitget', 'CoinbaseWallet'];

export function isInsideWalletBrowser(navigatorLike) {
    const nav = navigatorLike || (typeof navigator !== 'undefined' ? navigator : null);
    if (!nav) return false;
    const ua = String(nav.userAgent || '');
    return WALLET_UA_TOKENS.some((t) => ua.includes(t));
}

// 各錢包「用內建瀏覽器開啟 dapp」的 universal link。
// 格式來源：各錢包官方 deep link 文檔；幣安的 _dp 構造照 AppKit
// MobileWallet.js 的做法（bnc:// mini-app 包 base64，appId 為幣安官方公開值）。
// b64 參數只在測試注入（node Buffer），瀏覽器走預設 btoa。
export function buildWalletBrowserLinks(dappUrl, b64, opts) {
    const ios = !!(opts && opts.ios);
    const encode = b64 || ((s) => window.btoa(s));
    let hostPath = '';
    try {
        const u = new URL(dappUrl);
        hostPath = (u.host + u.pathname).replace(/\/$/, '');
    } catch (e) {
        hostPath = '';
    }
    const enc = encodeURIComponent(dappUrl);
    const links = [
        {
            id: 'trust',
            name: 'Trust Wallet',
            androidPackage: ANDROID_PACKAGES.trust,
            href: `https://link.trustwallet.com/open_url?coin_id=60&url=${enc}`,
            // iOS 版 Trust 沒有內建 dApp 瀏覽器（App Store 政策），open_url
            // 只會把 App 叫起來停在錢包首頁——使用者以為卡住了
            // （2026-09-07 使用者截圖實證）。
            noInAppBrowserOnIos: true,
        },
        {
            id: 'metamask',
            name: 'MetaMask',
            androidPackage: ANDROID_PACKAGES.metamask,
            href: hostPath ? `https://metamask.app.link/dapp/${hostPath}` : '',
        },
        {
            id: 'bitget',
            name: 'Bitget Wallet',
            androidPackage: ANDROID_PACKAGES.bitget,
            // BKConnect 官方格式（web3.bitget.com/docs/configuration/deeplink）：
            // https universal link，Android 僅支援 https 版
            href: `https://bkcode.vip?action=dapp&url=${enc}`,
        },
        {
            id: 'coinbase',
            name: 'Coinbase Wallet',
            androidPackage: ANDROID_PACKAGES.coinbase,
            href: `https://go.cb-w.com/dapp?cb_url=${enc}`,
        },
    ];
    try {
        const deeplink = new URL('bnc://app.binance.com/mp/app');
        deeplink.searchParams.set('appId', 'yFK5FCqYprrXDiVFbhyRx7');
        deeplink.searchParams.set('startPagePath', encode('/pages/browser/index'));
        deeplink.searchParams.set(
            'startPageQuery',
            encode(`url=${enc}&defaultChainId=1`)
        );
        const universal = new URL('https://app.binance.com/en/download');
        universal.searchParams.set('_dp', encode(deeplink.toString()));
        links.push({ id: 'binance', name: 'Binance Web3', androidPackage: ANDROID_PACKAGES.binance, href: universal.toString() });
    } catch (e) {
        // URL/btoa 不可用的環境（極舊瀏覽器）——略過幣安，其餘仍可用
    }
    const usable = links.filter((l) => l.href);
    if (!ios) return usable;
    // iOS：沒有內建瀏覽器的錢包不從清單移除（使用者認得名字，拿掉只會讓人
    // 以為我們不支援），但排到最後並標上 unsupportedNote，讓 UI 印一行說明，
    // 免得一路點進死巷、看著錢包首頁不知道發生什麼事。
    return usable
        .map((l) => (l.noInAppBrowserOnIos ? { ...l, unsupportedNote: 'iosNoInAppBrowser' } : l))
        .sort((a, b) => (a.unsupportedNote ? 1 : 0) - (b.unsupportedNote ? 1 : 0));
}

// 手機瀏覽器（無注入錢包）才引導「在錢包內開啟」：
// 錢包內建瀏覽器裡一定有注入 provider，會走 EIP-6963 直連，不進本引導。
export function shouldShowOpenInWallet(isMobileLike, injectedProviderCount) {
    return !!isMobileLike && !injectedProviderCount;
}

// 粗略手機判定：UA 加 pointer:coarse 雙訊號，任一命中即視為手機類裝置
// （平板也該引導——同樣有 deep link 往返問題）。
export function detectMobileLike(navigatorLike, matchMediaLike) {
    const nav = navigatorLike || (typeof navigator !== 'undefined' ? navigator : null);
    if (!nav) return false;
    const ua = String(nav.userAgent || '');
    const uaMobile = /Android|iPhone|iPad|iPod|Mobile/i.test(ua);
    let coarse = false;
    const mm =
        matchMediaLike || (typeof window !== 'undefined' && window.matchMedia
            ? window.matchMedia
            : null);
    if (mm) {
        try {
            coarse = mm('(pointer: coarse)').matches;
        } catch (e) {
            coarse = false;
        }
    }
    return uaMobile || coarse;
}

// 開面板那一下的「殘影 click」判定。
//
// click-delegator 在觸控時於 pointerup 合成 click 提早派發動作（不等原生 click），
// 引導面板卻要等注入錢包偵測（250ms）才插進 DOM——跟在後面的原生 click 於是
// hit-test 到剛出現的面板，直接「選中」落點下的那一列錢包。iOS 上登入按鈕
// 正好壓在第一列，使用者看到的是「點連接錢包就跳去 MetaMask，選單連畫都
// 沒畫出來」（2026-09-08 DANNY 錄影）。click-delegator 自己的手勢級吞點只
// 保護 [data-click] 委派路徑，面板的列是自己掛的 listener，得自己擋。
//
// 判準不看時間看因果：click 前有沒有在面板上按下過。按下發生在面板出現之前
// 的，一律不算選擇。detail===0 是鍵盤 Enter/Space 觸發的 click，沒有 pointer
// 階段，放行。
export function shouldSwallowGuideClick(pressedInside, clickDetail) {
    if (pressedInside) return false;
    if (!clickDetail) return false;
    return true;
}
