// evm-mobile-guide — 手機瀏覽器「在錢包 App 內開啟」引導的純函式測試。
// 背景：WC deep link 往返丟 proposal／簽章回應（2026-09-02 三錢包實測），
// 手機主路徑改為引導錢包內建瀏覽器；連結格式錯了使用者會被帶到錯的地方，
// 釘死每一家的格式。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const moduleUrl = await loadModuleUrl('web/js/evm-mobile-guide.js');
const { buildWalletBrowserLinks, shouldShowOpenInWallet, detectMobileLike, toAndroidIntentHref, isAndroid, isIos, shouldSwallowGuideClick, isInsideWalletBrowser, ANDROID_PACKAGES } = await import(
    moduleUrl
);

const DAPP = 'https://cryptomind-ton.zeabur.app/?x=1';
const b64 = (s) => Buffer.from(s, 'latin1').toString('base64');
const links = buildWalletBrowserLinks(DAPP, b64);
const byId = Object.fromEntries(links.map((l) => [l.id, l]));

// Trust：官方 open_url 格式，coin_id=60（EVM）
assert.match(
    byId.trust.href,
    /^https:\/\/link\.trustwallet\.com\/open_url\?coin_id=60&url=https%3A%2F%2Fcryptomind-ton\.zeabur\.app%2F%3Fx%3D1$/,
    'Trust 連結要走官方 open_url 帶完整編碼 URL'
);

// Bitget：BKConnect 官方格式（web3.bitget.com/docs/configuration/deeplink）
assert.match(
    byId.bitget.href,
    /^https:\/\/bkcode\.vip\?action=dapp&url=https%3A%2F%2Fcryptomind-ton\.zeabur\.app%2F%3Fx%3D1$/,
    'Bitget 連結要走 BKConnect 官方 action=dapp 格式'
);

// MetaMask：dapp/<host>/<path>，不帶 protocol/query
assert.equal(
    byId.metamask.href,
    'https://metamask.app.link/dapp/cryptomind-ton.zeabur.app',
    'MetaMask dapp 連結只要 host+path（根路徑無尾斜線）'
);

// Coinbase：官方 cb-w.com/dapp 帶 cb_url
assert.match(
    byId.coinbase.href,
    /^https:\/\/go\.cb-w\.com\/dapp\?cb_url=https%3A%2F%2F/,
    'Coinbase 連結要走 go.cb-w.com/dapp 帶 cb_url'
);

// 幣安：AppKit 同款 _dp=base64(bnc:// mini-app)，解開要能還原出本站 URL
const binanceUrl = new URL(byId.binance.href);
assert.equal(binanceUrl.host, 'app.binance.com');
const dp = binanceUrl.searchParams.get('_dp');
const inner = new URL(Buffer.from(dp, 'base64').toString('latin1'));
assert.equal(inner.protocol, 'bnc:');
assert.equal(
    inner.searchParams.get('startPagePath'),
    b64('/pages/browser/index'),
    'startPagePath 要是 base64 的 /pages/browser/index'
);
const query = Buffer.from(inner.searchParams.get('startPageQuery'), 'base64').toString('latin1');
assert.ok(query.includes(`url=${encodeURIComponent(DAPP)}`), '幣安瀏覽器最終要打開本站 URL');

// 壞 URL 時 MetaMask 條目要被過濾，不能給空 href
const noMeta = buildWalletBrowserLinks('not-a-url', b64);
assert.ok(noMeta.every((l) => l.href && /^https:/.test(l.href)), '所有連結都要是合法 https');
assert.ok(!noMeta.some((l) => l.id === 'metamask' && l.href === ''), 'metaMask 壞 URL 時要移除');

// 分流：手機＋無注入才引導；錢包內建瀏覽器（有注入）永不引導
assert.equal(shouldShowOpenInWallet(true, 0), true, '手機無注入 → 引導');
assert.equal(shouldShowOpenInWallet(true, 1), false, '錢包內建瀏覽器（有注入）→ 直連');
assert.equal(shouldShowOpenInWallet(false, 0), false, '桌機 → WalletConnect 掃碼');

// 手機判定：UA 或 pointer:coarse 任一命中
assert.equal(detectMobileLike({ userAgent: 'Mozilla/5.0 (Linux; Android 14) Chrome/128 Mobile' }), true);
assert.equal(detectMobileLike({ userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64)' }), false);
assert.equal(
    detectMobileLike({ userAgent: 'Mozilla/5.0 (iPad; CPU OS 17)' }),
    true,
    'iPad 也要算（同樣有 deep link 往返問題）'
);
assert.equal(
    detectMobileLike({ userAgent: 'Mozilla/5.0 (X11; Linux x86_64)' }, (q) => ({ matches: q === '(pointer: coarse)' })),
    true,
    'UA 不像手機但 pointer:coarse（大型觸控板）也要引導'
);
assert.equal(detectMobileLike(null), false, '無 navigator 環境寧可不引導');

console.log('evm mobile guide tests passed');

// ===== Android intent:// 直呼（2026-09-02：App Links 驗證失效會落到下載頁，
// 使用者以為我們說他沒裝錢包）=====

// intent 形式：帶 package 直呼 App、fallback 回原 universal link
const intent = toAndroidIntentHref(byId.trust.href, ANDROID_PACKAGES.trust);
assert.ok(
    intent.startsWith('intent://link.trustwallet.com/open_url?coin_id=60&url='),
    'intent 要保留完整 host+path'
);
assert.ok(
    intent.includes('#Intent;scheme=https;package=com.wallet.crypto.trustapp;'),
    'intent 要帶 scheme=https 與 Trust 的 package'
);
assert.ok(
    intent.includes(`S.browser_fallback_url=${encodeURIComponent(byId.trust.href)}`),
    'fallback 要是編碼過的原 https 連結'
);

// 非 https 或缺 package：原樣退回，不亂包
assert.equal(toAndroidIntentHref('ftp://x', 'a.b'), 'ftp://x');
assert.equal(toAndroidIntentHref(byId.trust.href, ''), byId.trust.href);

// isAndroid：UA 判定
assert.ok(isAndroid({ userAgent: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) Mobile Safari/537.36' }));
assert.ok(!isAndroid({ userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile Safari' }));
assert.ok(!isAndroid({ userAgent: '' }));

console.log('intent 直呼與 isAndroid 釘死測試通過');

// ===== iOS：沒有內建 dApp 瀏覽器的錢包要排到最後並標註（2026-09-07
// 使用者截圖：iPhone 點 Trust 後只是把錢包 App 叫起來停在錢包首頁）=====

assert.ok(isIos({ userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile Safari' }));
assert.ok(
    isIos({ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari', maxTouchPoints: 5 }),
    'iPadOS 13+ 報 Macintosh，要靠 maxTouchPoints 認出來',
);
assert.ok(
    !isIos({ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari', maxTouchPoints: 0 }),
    '真的桌機 Mac 不算 iOS',
);
assert.ok(!isIos({ userAgent: 'Mozilla/5.0 (Linux; Android 14) Mobile' }));
assert.ok(!isIos(null), '無 navigator 環境不算 iOS');

const iosLinks = buildWalletBrowserLinks(DAPP, b64, { ios: true });
const iosTrust = iosLinks.find((l) => l.id === 'trust');
assert.ok(iosTrust, 'Trust 不從清單移除——使用者認得這個名字，拿掉只會以為我們不支援');
assert.equal(iosTrust.unsupportedNote, 'iosNoInAppBrowser', 'iOS 的 Trust 要標註沒有內建瀏覽器');
assert.equal(iosLinks[iosLinks.length - 1].id, 'trust', '標註過的錢包排到最後');
assert.equal(iosLinks.length, links.length, 'iOS 不減少可選錢包數量');
assert.ok(
    iosLinks.filter((l) => l.unsupportedNote).length === 1,
    '只有 Trust 有這個限制（MetaMask／Coinbase／Bitget／幣安 iOS 都有內建瀏覽器）',
);

// 非 iOS（預設）維持原樣：不標註、不重排
assert.ok(
    buildWalletBrowserLinks(DAPP, b64).every((l) => !l.unsupportedNote),
    'Android／桌機不加註記',
);
assert.equal(byId.trust.id, links[0].id, 'Android／桌機的順序不動');

console.log('iOS 內建瀏覽器限制標註測試通過');

// ---- 殘影 click（2026-09-08 iOS 錄影：點連接錢包直接跳 MetaMask）----
// 觸控是 pointerdown 就派發 safeEvmLogin，面板 250ms 後才插進 DOM，放手的
// 原生 click hit-test 到剛出現的第一列。判準是因果不是時間：click 之前
// 有沒有在面板上按下過。
assert.equal(
    shouldSwallowGuideClick(false, 1),
    true,
    '面板出現前就按下的那一下——放手的 click 不算選錢包',
);
assert.equal(
    shouldSwallowGuideClick(true, 1),
    false,
    '在面板上按下再放手，是真的選了一列',
);
assert.equal(
    shouldSwallowGuideClick(false, 0),
    false,
    'detail=0 是鍵盤 Enter/Space，沒有 pointer 階段，不能吞',
);
assert.equal(
    shouldSwallowGuideClick(false, undefined),
    false,
    '拿不到 detail 的合成 click 一律放行——寧可漏擋也不要讓面板變成死鍵',
);

console.log('殘影 click 吞點測試通過');

// ---- isInsideWalletBrowser：錢包 webview UA 兜底（2026-09-10 iOS 回圈） ----
// 用途：注入偵測失敗時，UA 顯示已在錢包內建瀏覽器 → 不得列「跳去錢包」清單
assert.equal(
    isInsideWalletBrowser({ userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1 MetaMask/7.8.0' }),
    true,
    'MetaMask 內建瀏覽器 UA 要認得'
);
assert.equal(
    isInsideWalletBrowser({ userAgent: 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/126 Mobile Safari/537.36 Bitget/xyz' }),
    true,
    'Bitget webview UA 要認得'
);
assert.equal(
    isInsideWalletBrowser({ userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1' }),
    false,
    '一般 iOS Safari 不得誤判'
);
assert.equal(
    isInsideWalletBrowser({ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36' }),
    false,
    '桌機 Chrome 不得誤判'
);
assert.equal(isInsideWalletBrowser(null), false, '無 navigator 環境回 false');

console.log('錢包 webview UA 判定測試通過');
console.log('all assertions passed');
