// stale-chunk-recovery.js — 部署後舊頁面懶載入 404 的自我修復
//
// 現象（2026-09-02 回報）：部署會整包換掉 assets/ 的 hash 檔名，瀏覽器裡
// 殘留的舊頁（PWA 回流、Telegram TWA deep-link 往返、背景分頁）接著跑
// 動態 import（EVM 續登的 evm-walletconnect、各 tab 模組）就會
// 「Failed to fetch dynamically imported module」→ 404。SW 端
// skipWaiting+clientsClaim 啟用新版時也會清掉舊 precache，舊頁連快取
// 後路都沒有。
//
// 業界標準解法：偵測這類錯誤 → 整頁重載一次（重載後拿到新 index.html →
// 新 hash chunk）。用 sessionStorage 時間戳守衛擋無限重載迴圈：20 秒內
// 只自動重載一次，仍失敗就交還呼叫端顯示「請手動重新整理」。
//
// EVM 續登旗標在 localStorage（見 evm-auth.js），活得過這次重載——重載後
// 續登流程會用新版 chunk 重新接手。

const GUARD_KEY = 'staleChunkReloadAt';
const GUARD_WINDOW_MS = 20_000;

// Chrome/Edge、Safari、Firefox 對動態 import 失敗的三種措辭。
// 故意不匹配裸的「Failed to fetch」——一般網路失敗不該觸發整頁重載。
const STALE_CHUNK_PATTERNS = [
    /failed to fetch dynamically imported module/i, // Chrome / Edge
    /importing a module script failed/i, // Safari
    /error loading dynamically imported module/i, // Firefox
];

export function isStaleChunkError(err) {
    const msg =
        typeof err?.message === 'string' ? err.message : typeof err === 'string' ? err : '';
    return STALE_CHUNK_PATTERNS.some((re) => re.test(msg));
}

// 回傳值給呼叫端決定後續：
//   'reloaded'  — 已觸發整頁重載（標 e.handled，別再 toast）
//   'blocked'   — 守衛視窗內已重載過仍失敗（顯示「請重新整理」）
//   'not-stale' — 不是這類錯誤，照舊處理
export function recoverFromStaleChunk(err) {
    if (!isStaleChunkError(err)) return 'not-stale';
    try {
        const last = Number(sessionStorage.getItem(GUARD_KEY));
        if (last && Date.now() - last < GUARD_WINDOW_MS) return 'blocked';
        sessionStorage.setItem(GUARD_KEY, String(Date.now()));
    } catch (e) {
        // 隱私模式等 sessionStorage 不可用——擋不了迴圈，但重載一次通常救得回來
    }
    window.location.reload();
    return 'reloaded';
}
