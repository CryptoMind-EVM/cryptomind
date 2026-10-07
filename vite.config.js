import { defineConfig } from 'vite';
import { VitePWA } from 'vite-plugin-pwa';
import { filterPrecacheManifest, precacheCollector } from './vite-sw-precache.mjs';

// 本次 build 的產物與其中的錢包 SDK chunk：collector 在 writeBundle 填，PWA 產 sw.js（closeBundle）時用
const precacheState = { bundle: new Set(), wallet: new Set() };

export default defineConfig({
    root: 'web',
    base: '/static/',
    plugins: [
        precacheCollector(precacheState),
        // Service worker — 只快取 app shell（金融平台絕不快取 API/WebSocket 即時資料）。
        // - generateSW：用 Workbox 自動生成 SW（業界標準，處理 precache/版本更新）
        // - manifest:false：manifest 已由後端 /manifest.webmanifest 動態產生，不重複
        // - injectRegister:false：自訂註冊腳本（web/public/sw-register.js），處理
        //   scope:'/'（base 是 /static/，預設 scope 只到 /static/，要控制全站 / 需
        //   後端 /sw.js 路由送 Service-Worker-Allowed: / + 註冊時 scope:'/'）
        VitePWA({
            strategies: 'generateSW',
            registerType: 'autoUpdate',
            injectRegister: false,
            manifest: false,
            // sw.js 產到 publicDir（web/public/），build 後在 dist/static/sw.js。
            // 後端 /sw.js 路由會服務這份檔並送正確 header。
            filename: 'sw.js',
            workbox: {
                // 只 precache 自家的 JS/CSS chunk。不快取圖片/字體（走 HTTP cache）。
                // HTML 不 precache（2026-09-27）：index.html 由後端在 / 服務、forum 頁在
                // /static/forum/，清單裡的 /index.html、/forum/*.html 在正式站 404 →
                // install 失敗、SW 從沒接管過；而且 HTML 一進快取就會重演 #790
                // 「部署後第一次開仍是舊版」。HTML 永遠走網路。
                globPatterns: ['**/*.{js,css}'],
                // 註冊腳本不進 precache：它存在是為了「安裝 SW」，每次都該
                // 跑新的；且清單會以根路徑請求它（base 是 /static/）→ 線上
                // 每次 SW 更新都打一次 /sw-register.js 404（2026-09-07 掃到）
                globIgnores: ['**/sw-register.js'],
                // 錢包 SDK chunk 不 precache（近 200 個檔，第一次來的訪客用不到）——
                // 使用者真的連錢包時由下方 runtime CacheFirst 快取。清單網址補 /static/：
                // sw.js 在根路徑 /sw.js 服務，相對網址會被解析成 /assets/…，跟頁面
                // 實際請求的 /static/assets/… 對不上。見 vite-sw-precache.mjs。
                manifestTransforms: [
                    async (entries) => ({
                        manifest: filterPrecacheManifest(entries, precacheState, '/static/'),
                        warnings: [],
                    }),
                ],
                // 嚴格上限：app shell 不該超過 5MB
                maximumFileSizeToCacheInBytes: 5 * 1024 * 1024,
                // 不註冊 navigation route：vite-plugin-pwa 預設 navigateFallback='index.html'，
                // 不明寫 null 就會用快取的 index.html 回應導航。
                navigateFallback: null,
                // 內容帶 hash 的 build 產物內容永不變（改檔必換檔名），CacheFirst
                // 下載一次就留在裝置上，活過殺分頁與重載。
                // 關鍵動機（2026-09-01 手機黑屏）：AppKit vendor chunk 約 7MB，超過
                // precache 單檔 5MB 上限被排除在手動 precache 之外——WC 深鏈往返把
                // 分頁殺掉後，Chrome 重載時這顆 chunk 每次都走 4G 冷下載（十秒級），
                // 期間回跳接手流程停擺、使用者面對黑屏。CacheFirst 讓它第一次下載
                // 後就地快取。/api、/ws 不在 pattern 內，金融即時資料照樣絕不快取。
                runtimeCaching: [
                    {
                        urlPattern: /\/static\/assets\/[^?]+\.(?:js|css)/,
                        handler: 'CacheFirst',
                        options: {
                            cacheName: 'immutable-assets',
                            expiration: { maxEntries: 160, maxAgeSeconds: 60 * 60 * 24 * 30 },
                            cacheableResponse: { statuses: [200] },
                        },
                    },
                ],
                // SW 更新：新版本接管
                skipWaiting: true,
                clientsClaim: true,
            },
            devOptions: {
                enabled: false, // dev 不啟用 SW（避免干擾 HMR）
            },
        }),
    ],
    build: {
        outDir: '../dist/static',
        emptyOutDir: false,
        sourcemap: true,
        chunkSizeWarningLimit: 800,
        // 關閉 modulepreload：Vite 的 __vitePreload wrapper 會在 dynamic import 前
        // 建立 <link rel=modulepreload> 並等待 onload，但在 Zeabur 線上環境對「已預載
        // 的依賴（rolldown-runtime/forum/premium）」這個 onload 永遠不觸發，導致
        // _ensureTabModules 的 import() Promise 永久 pending → tab 模板載不到 →
        // settings/各股市/admin 等「動態注入」tab 全白屏（chat 首屏靜態不受影響）。
        // 關閉後 dynamic import 走原生 import()（實測 105ms 成功），不再 hang。
        // 代價：首次切 tab 時依賴 chunk 不預載（稍慢幾十 ms），換來不再卡死。
        modulePreload: false,
        rollupOptions: {
            // 多頁 build（2026-08-14）：
            // forum/*.html 與 premium.html 是獨立頁面，以 type="module" 載入 raw
            // /static/js/*.js。Dockerfile 會刪除 raw web/js/*.js（只留豁免清單），
            // 因此這些頁必須各自成為 Vite 多頁 entry，讓它們的 module script 被打包
            // 進 assets/ 並把 HTML 改寫成 hashed asset URL，否則 prod 會 404。
            input: {
                main: 'web/index.html',
                'forum/index': 'web/forum/index.html',
                'forum/post': 'web/forum/post.html',
                'forum/create': 'web/forum/create.html',
                'forum/dashboard': 'web/forum/dashboard.html',
                'forum/profile': 'web/forum/profile.html',
                'forum/premium': 'web/forum/premium.html',
            },
            output: {
                // rolldown 正規分 chunk API（manualChunks 為相容層、行為不保證——
                // 2026-08-24 事故：共用模組被併進 SPA main 入口 chunk，論壇頁
                // import main → 整個 SPA 在論壇頁執行、排版崩壞。見 forum/*.html
                // 的單一薄 entry 重構與 web/js/pages/ 說明）。
                codeSplitting: {
                    groups: [
                        // premium（shared-core）與 forum-app 共用的 USDC 錢包直付（2026-09-25）。
                        // 自成一塊且優先權高於 forum-app-core：不然 forum-app-core 會連同依賴把它
                        // 吃進去（includeDependenciesRecursively 預設 true），shared-core 反過來
                        // import forum-app-core＝SPA 開 Settings 就把整個論壇模組載進來。
                        { name: 'usdc-pay', priority: 20, minSize: 0, test: /web[\\/]js[\\/](usdc-pay|builder-code)\.js$/ },
                        // 錯誤訊息的顯示層（error-message → rejection-filter → stale-chunk-recovery，2026-10-06）：
                        // forum-app、friends（shared-core）、main 都 import。同 usdc-pay 的道理，不自成高優先權
                        // 的一塊，forum-app-core 會連同依賴吃掉它，shared-core／main 反過來 import forum-app-core。
                        {
                            name: 'error-message',
                            priority: 20,
                            minSize: 0,
                            test: /web[\\/]js[\\/](error-message|rejection-filter|stale-chunk-recovery)\.js$/,
                        },
                        { name: 'forum-app-core', priority: 10, test: /web[\\/]js[\\/]forum-app\.js$/ },
                        {
                            // 論壇多頁與 SPA 共用的模組一律收進 shared-core，
                            // entry（main 與各 forum boot）之間永遠不可能互相 import
                            name: 'shared-core',
                            priority: 10,
                            minSize: 0,
                            test: /web[\\/]js[\\/](store|utils|api-client|ton-auth|ui-shell|security-utils|app|apiKeyManager|auth|nav-config|global-nav|i18n|messages|friends|premium|forum-config|forum-api)\.js$/,
                        },
                        { name: 'shared-lang', priority: 10, minSize: 0, test: /web[\\/]js[\\/]components[\\/]LanguageSwitcher\.js$/ },
                    ],
                },
            },
        },
    },
    server: {
        proxy: {
            '/api': 'http://localhost:8080',
            '/ws': {
                target: 'ws://localhost:8080',
                ws: true,
            },
            '/static': 'http://localhost:8080',
        },
    },
    // `npm run preview` 服務 dist/static 的真實 bundle。本機的 api_server 只
    // mount web/ 原始碼，bare specifier（@reown/appkit…）在瀏覽器解析不了，
    // WalletConnect 那條路在本機等於測不到——只能一路往線上丟才看得到結果。
    // 這裡把 API 轉給 8080，就能在本機用打包後的檔案完整走一次錢包登入。
    preview: {
        proxy: {
            '/api': 'http://localhost:8080',
            '/ws': {
                target: 'ws://localhost:8080',
                ws: true,
            },
        },
    },
});
