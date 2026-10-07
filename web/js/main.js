// ========================================
// main.js - Application Entry Point
// ========================================
// Loaded via <script type="module"> in index.html.
// Import order matters: dependencies first, then consumers.
//
// Side-effect-only modules (no exports needed):
//   logger.js, ton-auth.js, i18n.js
//
// Simple modules (already converted to ES module format):
//   store.js, utils.js, api-client.js, security-utils.js, ui-shell.js,
//   legal.js, filter.js, testMode.js, wallet.js, alerts.js
//
// Complex modules (NOT yet converted internally — imported for side-effects only):
//   app.js, apiKeyManager.js, auth.js, friends.js, messages.js,
//   forum-app.js, admin.js, spa.js, components/*, chat-*.js, etc.
// ========================================

// ─── Phase 0: Telegram Mini App integration (MUST be first) ──────────────
// Sets window.TWA_RETURN_URL + WebApp.ready()/expand() before TON Connect
// initialises. Imported here (not a standalone <script>) so the Vite build
// actually bundles + ships it — a plain /js/ script tag 404s in production.
import './telegram-webapp.js';
import { recoverFromStaleChunk } from './stale-chunk-recovery.js';
import { classifyRejection } from './rejection-filter.js';

// ─── Phase 1: Core utilities (MUST load first) ───────────────────────────
import './store.js';           // AppStore (pub/sub state)

AppStore.restore();

window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    // 分類規則（取消的請求、錢包 SDK 的 Operation aborted、Service worker 更新失敗、瀏覽器 API 的
    // DOMException 一律安靜忽略；網路失敗與原生程式錯誤改顯示在地化訊息，不貼英文原文）
    // 全部在 rejection-filter.js，這裡只負責依分類行動——不要再往這裡加 allow-list。
    const kind = classifyRejection(reason, { online: navigator.onLine !== false });
    if (kind === 'ignore') {
        console.debug('[Unhandled Rejection] ignored:', reason);
        return;
    }
    // 部署後殘留的舊頁懶載入舊 hash chunk 會 404（Failed to fetch dynamically
    // imported module）。整頁重載一次換新版，別把英文瀏覽器錯誤丟給使用者；
    // 20 秒守衛內重載過仍失敗才改顯示「請重新整理」。
    if (kind === 'stale-chunk') {
        const outcome = recoverFromStaleChunk(reason);
        if (outcome === 'reloaded') return; // 重載中，安靜等頁面換新
        if (outcome === 'blocked') {
            if (typeof showToast === 'function')
                showToast(
                    window.I18n
                        ? window.I18n.t('app.staleChunkRefresh')
                        : '系統已更新，請重新整理頁面後再試一次',
                    'error'
                );
            return;
        }
    }
    // 用 warn 不用 error：error-boundary 已經在 unhandledrejection 上報一次，
    // 它也會攔 console.error(Error)，用 error 會讓每則 rejection 報兩次。
    console.warn('[Unhandled Rejection]', reason);
    const t = (key, fallback) => (window.I18n ? window.I18n.t(key) : fallback);
    let message;
    if (kind === 'network') message = t('common.networkUnstable', 'Network hiccup — please try again');
    else if (kind === 'generic') message = t('app.unexpectedError', 'An unexpected error occurred');
    else
        message =
            (typeof reason?.message === 'string' && reason.message.trim()) ||
            (typeof reason === 'string' && reason.trim()) ||
            t('app.unexpectedError', 'An unexpected error occurred');
    window.__lastUnhandledReasonMessage = message;
    if (typeof showToast === 'function') showToast(message, 'error');
});

import './utils.js';           // AppUtils (shared helpers)
import './api-client.js';      // AppAPI (HTTP client)
import './error-message.js';   // userFacingMessage；也掛 window.ErrorMessage 給 classic script（skill-manager 等）用

// ─── Phase 2: Side-effect-only modules ────────────────────────────────────
// ton-auth.js: Telegram Mini App login only (the name is historical). Wallet
// login is evm-auth.js since 2026-08-31; TON Connect was removed 2026-09-26.
import './ton-auth.js';
// evm-auth.js sets up window.safeEvmLogin (SIWE personal_sign, injected wallets)
import './evm-auth.js';
import './google-auth.js';   // Google 登入（2026-09-13）
import './legacy-ton-notice.js'; // 舊 TON 身份提醒綁 EVM／Google（聽 auth:changed，要在 auth.js 前）
import './legal-consent.js'; // 條款改版後的同意提示（聽 auth:changed，要在 auth.js 前）
import './watchlist-sync.js'; // 各市場分頁的自選 ↔ 伺服器（/api/watchlist）

// ─── Phase 3: UI shell & layout ───────────────────────────────────────────
import './ui-shell.js';        // UIShell, showToast
import './layout-debug.js';    // 底部版面數字面板（只在 #layout-debug 時顯示）

// ─── Phase 4: Security & utilities ────────────────────────────────────────
import './security-utils.js';  // SecurityUtils (XSS, CSP)

// ─── Phase 5: App core ────────────────────────────────────────────────────
import './app.js';             // Main app logic
import './apiKeyManager.js';   // API key management
import './auth.js';            // AuthManager

// ─── Phase 6: Components system (load order matters) ──────────────────────
import './components/css-constants.js';
// telegram-link.js 已改 dynamic import（2026-09-08 隨 Telegram 卡遷入
// connections 分頁）——它只服務那一頁，沒必要留在首屏 bundle。
import './components/core.js';
// 所有 tab-*.js 模板已改 dynamic import（spa.js 切 tab 時按需載入）：
//   hidden:true：tab-friends, tab-forum
//   defaultEnabled:false：tab-commodity, tab-forex, tab-hkstock, tab-astock,
//     tab-jpstock, tab-instock, tab-krstock
//   預設啟用但非首屏：tab-twstock, tab-usstock, tab-admin, tab-crypto, tab-settings

// ─── Phase 7: Notifications ───────────────────────────────────────────────
import './notification-service.js';
import './components/NotificationBell.js';
import './components/NotificationPanel.js';

// ─── Phase 8: Settings & tools ────────────────────────────────────────────
import './llmSettings.js';
import './testMode.js';
import './toolSettings.js';
import './filter.js';

// ─── Phase 9: Chat system ─────────────────────────────────────────────────
import './chat-state.js';
import './chat-sessions.js';
import './chat-stream-ui.js';
import './chat-hitl.js';
import './chat-analysis.js';
import './share-link.js';
import './chat-history.js';
import './chat-init.js';

// ─── Phase 10: Social features ────────────────────────────────────────────
// friends.js / messages.js 已改 dynamic import（friends tab 為 hidden:true，
// messages 由獨立頁面載入）— spa.js 切 friends tab 時載入

// ─── Phase 11: Navigation ─────────────────────────────────────────────────
import './nav-config.js?v=69';
import './global-nav.js';
import './market-hub.js'; // 導覽「市場」群組的子分頁列（各市場分頁）
import './market-ui.js'; // 港日韓陸股、商品、外匯分頁共用的小零件（資料來源標示、卡片補資訊）
import './nav-badges.js'; // 功能選單的未讀標示（社群數字、論壇紅點）

// ─── Phase 12: Market data ────────────────────────────────────────────────
import './market-status.js';   // 被所有 stock tab 依賴，留首屏（共享依賴）
// crypto 相關（market-screener/chart/ws/pulse）已改 dynamic import —
//   spa.js 切 crypto tab 時載入
// twstock / usstock / board 已改 dynamic import（預設啟用但非首屏）
// commodity / forex / hkstock / astock / jpstock / instock / krstock
// 已改 dynamic import（皆 defaultEnabled:false）— spa.js 切對應 tab 時載入

// ─── Phase 13: Forum ──────────────────────────────────────────────────────
// forum-config / forum-api / forum-app 已改 dynamic import（forum tab hidden）
// premium.js 已改 dynamic import（settings tab 用，與 forum-config 綁定）— 
//   spa.js 切 settings tab 時載入

// ─── Phase 14: Wallet & alerts ────────────────────────────────────────────
// wallet.js / alerts.js 已改 dynamic import（wallet 預設關閉；alerts 由
// twstock/usstock tab 用，跟著 stock 模組一起載入）

// ─── Phase 15: Safety & admin ─────────────────────────────────────────────
// admin.js / admin-stats.js 已改 dynamic import（admin tab locked）

// ─── Phase 16: SPA & modals ───────────────────────────────────────────────
import './spa.js?v=68';
import './chat-preset.js';
// modals.js 已刪：它是 alerts.js 的舊副本，函式沒掛 window 也沒 export，
// 載進來什麼都不做（價格警報一律走 alerts.js）
import './legal.js';
import './feedback.js';

// ─── Phase 17: Internationalization (last — translates everything) ────────
import './i18n.js';
import './components/LanguageSwitcher.js';
import './components/ThemeSwitcher.js';
