// rejection-filter.js — 全域 unhandledrejection 的分類（2026-10-06）
//
// 為什麼有這支：main.js 的全域處理器以前會把「任何」沒接住的 rejection 的 reason.message 原文
// 貼成紅色錯誤 toast。使用者手機（Telegram／Base App 這類內建瀏覽器為主）就看到
//   Failed to update a ServiceWorker for scope (...) with script ('/sw.js'): Not found
//   Failed to update a ServiceWorker ... with script ('Unknown'): The object is in an invalid state.
// 這類是瀏覽器內部的背景行為，使用者無事可做，原文英文更沒有意義。
//
// 分類（回傳值）：
//   'ignore'      背景雜訊，不跳 toast（只留 console.debug；error-boundary 仍照舊上報供除錯）
//   'stale-chunk' 部署後殘留舊頁的動態 import 404——交給 stale-chunk-recovery 整頁重載
//   'network'     線上時的 fetch 網路失敗——顯示「網路不穩，請再試一次」而不是英文原文
//   'generic'     原生程式錯誤（TypeError／ReferenceError…）——顯示「發生未預期的錯誤」而不是原文
//   'show'        其餘（AppAPI 的友善錯誤、錢包 SDK 的訊息…）——維持原本行為，顯示原文
import { isStaleChunkError } from './stale-chunk-recovery.js';

// 各瀏覽器對「fetch 網路層失敗」的措辭：Chrome／Safari／Firefox／React Native 系 WebView。
// 整句比對（不是子字串）：「Failed to fetch dynamically imported module」是 stale chunk，不能被吃掉。
export const NETWORK_MESSAGE =
    /^(failed to fetch|load failed|networkerror when attempting to fetch resource\.?|network request failed|the network connection was lost\.?|network error)$/i;

// 原生錯誤類型：程式本身的 bug（或網路層 TypeError），訊息是給工程師看的
export const NATIVE_ERROR_NAMES = new Set([
    'TypeError',
    'ReferenceError',
    'RangeError',
    'SyntaxError',
    'EvalError',
    'URIError',
]);

function messageOf(reason) {
    if (typeof reason?.message === 'string') return reason.message;
    if (typeof reason === 'string') return reason;
    return '';
}

function isDomException(reason) {
    return typeof DOMException !== 'undefined' && reason instanceof DOMException;
}

export function classifyRejection(reason, { online = true } = {}) {
    const msg = messageOf(reason).trim();

    // 使用者取消／切頁取消的請求
    if (reason?.name === 'AbortError') return 'ignore';
    // 錢包 SDK 內部 AbortController 的正常行為（tab 切換／取消連線／逾時），SDK 自己沒 catch
    if (msg.includes('Operation aborted')) return 'ignore';
    // Service worker 更新／註冊失敗（部署空窗 404、離線、App 內建瀏覽器 registration 狀態失效）
    if (msg.includes('ServiceWorker') && /update|register|script/i.test(msg)) return 'ignore';
    // 瀏覽器 API 丟的 DOMException（剪貼簿被擋 NotAllowedError、WebView 擋 storage 的 SecurityError、
    // QuotaExceededError、InvalidStateError…）：由瀏覽器而非我們的程式碼拋出，使用者無事可做
    if (isDomException(reason)) return 'ignore';

    if (isStaleChunkError(reason)) return 'stale-chunk';

    if (reason?.name === 'TypeError' && NETWORK_MESSAGE.test(msg)) {
        // 離線時背景輪詢一定會失敗，吵使用者沒有意義；線上才提示一次
        return online ? 'network' : 'ignore';
    }
    if (NATIVE_ERROR_NAMES.has(reason?.name)) return 'generic';
    return 'show';
}
