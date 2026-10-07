// ========================================
// evm-login-feedback.js — 登入流程進行中的使用者回饋（純邏輯，Node 可測）
//
// 2026-09-05 回報「登出後點錢包好幾次都沒反應」：safeEvmLogin 的防重入閘
// 在流程卡在 personal_sign 期間（含逾時自動重送，最長 ~300s）靜默吞掉
// 所有點擊——按鈕不會 disabled、畫面沒有任何變化，使用者只能判定按鈕壞了。
// 這裡把「該不該給回饋」的節流判定抽成純函數：回饋要有（不再無聲），
// 但連點不能洗版（4 秒最多一則）。
// ========================================

const IN_FLIGHT_HINT_THROTTLE_MS = 4000;

/**
 * 防重入期間的點擊要不要給回饋。
 * 回傳 true＝這次該提示（呼叫端顯示 toast 並更新 lastHintAt）。
 */
export function shouldShowInFlightHint(lastHintAt, now, throttleMs) {
    if (!lastHintAt) return true; // 從未提示過——一定回饋
    const throttle = typeof throttleMs === 'number' ? throttleMs : IN_FLIGHT_HINT_THROTTLE_MS;
    return !(now - lastHintAt < throttle);
}

// ---- 登出真斷錢包連線的旗標操作（2026-09-05 DANNY「要徹底解決」）----
// 兩層分離（網站 session／WC 連線）下，登出後 AppKit 面板仍顯示「已連接」
// 讓使用者矛盾。決策：登出即真斷。但登出後立刻 reload，當下的斷線呼叫
// （relay 往返）常被截斷——旗標讓下次頁面載入把斷線收尾，形成閉環。
// 鍵名與 evm-auth.js 的 WC_RESUME_FLAG / 本檔共用字面值（兩處皆以字串
// 存取，測試同時看守）。
export const WC_RESUME_FLAG_KEY = 'evmWcResumeAt';
export const WC_PENDING_DISCONNECT_KEY = 'evmWcPendingDisconnect';

/**
 * 登出時的旗標操作：廢續登旗標＋標記待斷線。
 * 回傳 false＝storage 不可用（隱私模式）——沒有既有 session 可斷，安靜放棄。
 */
export function applyLogoutDisconnectFlags(storage, now) {
    if (!storage || typeof storage.removeItem !== 'function' || typeof storage.setItem !== 'function') {
        return false;
    }
    try {
        // 順序即語義：先廢續登旗標（重載後不得自動接手舊 session 再登入），
        // 再標記待斷線。
        storage.removeItem(WC_RESUME_FLAG_KEY);
        storage.setItem(WC_PENDING_DISCONNECT_KEY, String(now || Date.now()));
        return true;
    } catch (e) {
        return false;
    }
}

/** 是否有待收尾的斷線（頁面載入時消化，見 evm-auth._consumePendingDisconnect）。 */
export function hasPendingDisconnect(storage) {
    try {
        return !!(storage && storage.getItem && storage.getItem(WC_PENDING_DISCONNECT_KEY));
    } catch (e) {
        return false;
    }
}

// ---- 簽名請求的前景重送判定（2026-09-05 Trust 三連漏實證）----
// 手機錢包 App 背景凍結會漏掉 relay 上的 personal_sign：連線全程健康、
// 請求都送達 relay，錢包只在「發佈當下恰好在前景」的那一發彈窗。
// 使用者切回頁面＝錢包剛被喚醒過的時刻——此刻立刻重發最有效。
export const SIGN_FOREGROUND_RESEND_MAX = 2;

/**
 * visibilitychange→visible 時要不要重發 personal_sign。
 * 上限防彈窗洗版：重發會在錢包端各自產生提示（RPC id 各自獨立）。
 */
export function shouldResendSignOnForeground({ visible, waiting, resendCount, max }) {
    const cap = typeof max === 'number' ? max : SIGN_FOREGROUND_RESEND_MAX;
    return !!visible && !!waiting && Number(resendCount || 0) < cap;
}

/**
 * 登入時回報「用什麼登入」（2026-09-30，後台看 Email／Google 人數，對照 Reown 免費方案上限）。
 * 外部錢包＝wallet；Reown 內嵌錢包照 authProvider（email／google，其他社群登入歸 social）。
 * 只拿來統計，Email 本身不帶。
 */
export function loginViaFor(opts) {
    if (!(opts && opts.embeddedWallet)) return 'wallet';
    const provider = String(opts.authProvider || 'email').toLowerCase();
    return provider === 'email' || provider === 'google' ? provider : 'social';
}
