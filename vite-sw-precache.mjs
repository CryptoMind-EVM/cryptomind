// vite-sw-precache.mjs — Workbox precache 清單的過濾（vite.config.js 用；測試：tests/js/sw_precache.mjs）
// 放在 repo 根目錄跟 vite.config.js 一起：Dockerfile builder 只 COPY web/ 與根目錄設定檔，
// scripts/ 被 .dockerignore 排除——放那裡 image build 會找不到。
//
// 2026-09-27 正式站量測：SW 從沒裝起來過。sw.js 在根路徑 /sw.js 服務（scope 要涵蓋 /），
// 清單卻是相對網址——index.html、forum/*.html 被解析成 /index.html、/forum/*.html（404，
// install 整個失敗）；assets 被解析成 /assets/…，跟頁面實際請求的 /static/assets/… 對不上。
// 清單裡還有近 200 個錢包 SDK chunk，第一次來的訪客用不到卻要背景下載。

// 只由 node_modules 模組組成的 chunk＝錢包 SDK：全站只有 evm-walletconnect.js 動態
// import 套件（@reown/appkit…），其餘前端都是自家原始碼或 CDN。用模組來源判斷，
// 不用檔名比對——AppKit 的 chunk 名稱（w3m-*、wui-*、Ph*、dist、exports、utils…）
// 太雜，而且會跟自家檔名撞。
export function isWalletVendorChunk(moduleIds) {
    if (!Array.isArray(moduleIds) || moduleIds.length === 0) return false;
    return moduleIds.every((id) => id.replace(/\\/g, '/').includes('/node_modules/'));
}

// Vite plugin：記下本次 build 的產物檔名（相對 outDir，例如 assets/main-XXXX.js）與其中的
// 錢包 chunk。writeBundle 時 bundle 已定案，vite-plugin-pwa 在 closeBundle 產 sw.js 時已收齊。
// 要記「本次產物」是因為 build.emptyOutDir 是 false：dist 裡留著前幾次 build 的舊 hash 檔，
// 只靠 glob 會把它們（含舊的錢包 chunk）一起塞進清單。
export function precacheCollector(state) {
    return {
        name: 'sw-precache-collector',
        apply: 'build',
        writeBundle(_options, bundle) {
            for (const [fileName, output] of Object.entries(bundle)) {
                state.bundle.add(fileName);
                if (output.type === 'chunk' && isWalletVendorChunk(output.moduleIds)) {
                    state.wallet.add(fileName);
                }
            }
        },
    };
}

// 只留本次產物、拿掉 HTML（HTML 永遠走網路，見 #790）與錢包 chunk，網址補上 base 前綴。
// 回傳新陣列、不改動傳入的 entry。
export function filterPrecacheManifest(entries, state, base) {
    return entries
        .filter((e) => state.bundle.has(e.url) && !state.wallet.has(e.url) && !e.url.endsWith('.html'))
        .map((e) => ({ ...e, url: base + e.url }));
}
