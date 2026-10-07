// Email／Google 社群登入的卡死看門狗。
//
// 問題（2026-10-05 DANNY：點了 Gmail 連接、進到錢包連結後一直轉圈、不會跳回畫面，
// 要重新整理才行）：Reown AppKit 1.8.21 的社群登入沒有自癒——
//   - SocialsUtil.connectSocial 的 45 秒逾時是丟在 setTimeout 裡的 Error，
//     不會被它自己的 catch 接住（uncaught），畫面停在 ConnectingSocial；
//   - 彈窗跳回主視窗的 postMessage 沒到（行動瀏覽器／內建瀏覽器常見）或
//     connectExternal 掛住時，沒有任何逾時，轉圈到天荒地老；
//   - W3mFrameProvider 的 iframe 20 秒逾時只 abort、不 reject。
//
// 這裡只看 AppKit 的事件流（appKit.subscribeEvents）與它那條 uncaught 逾時，
// 卡住就呼叫 onStall——由 evm-walletconnect 關掉彈窗、回報遙測、讓使用者重試。
// 計時器、時鐘、可見性都可注入，Node 可測（tests/js/social_login_watchdog.mjs）。

// 使用者要在 Google 頁面輸入帳密、可能還有兩步驟驗證：給足時間
export const STARTED_MS = 100000;
// 從別的分頁／App 回來後，彈窗訊息應該秒到；沒到就不用再等太久
export const RESUME_MS = 45000;
// 已拿到 Google 的授權、正在向 Reown 換內嵌錢包：這步只需幾秒
export const USER_DATA_MS = 30000;
// AppKit 自己的逾時是 45 秒；離 STARTED 超過這麼久才認得是這一輪的
const APPKIT_TIMEOUT_MIN_ELAPSED_MS = 40000;
const APPKIT_TIMEOUT_RE = /Social login timed out/i;
// 不可見時不判死（跳去 Google／錢包 App 期間計時器照走），改短延遲重排
const HIDDEN_RECHECK_MS = 2000;

const DONE_EVENTS = new Set([
    'SOCIAL_LOGIN_SUCCESS',
    'SOCIAL_LOGIN_ERROR',
    'SOCIAL_LOGIN_CANCELED',
]);

export function createSocialLoginWatchdog({
    onStall,
    isVisible = () => true,
    setTimer = (fn, ms) => setTimeout(fn, ms),
    clearTimer = (id) => clearTimeout(id),
    now = () => Date.now(),
} = {}) {
    let timer = null;
    let phase = ''; // '' | 'started' | 'userData'
    let startedAt = 0;
    let disposed = false;

    const clear = () => {
        if (timer !== null) clearTimer(timer);
        timer = null;
    };
    const stall = (reason) => {
        clear();
        phase = '';
        if (disposed) return;
        try {
            if (typeof onStall === 'function') onStall(reason);
        } catch (e) { /* 看門狗絕不能再拋例外 */ }
    };
    const arm = (ms, reason) => {
        clear();
        timer = setTimer(function fire() {
            timer = null;
            if (disposed || !phase) return;
            if (!isVisible()) {
                timer = setTimer(fire, HIDDEN_RECHECK_MS);
                return;
            }
            stall(reason);
        }, ms);
    };

    return {
        /** AppKit 事件名（event.data.event）。無關事件一律忽略。 */
        onEvent(name) {
            if (disposed || typeof name !== 'string') return;
            if (name === 'SOCIAL_LOGIN_STARTED') {
                phase = 'started';
                startedAt = now();
                arm(STARTED_MS, 'social-started: no popup result');
            } else if (name === 'SOCIAL_LOGIN_REQUEST_USER_DATA') {
                phase = 'userData';
                arm(USER_DATA_MS, 'social-user-data: connect never finished');
            } else if (DONE_EVENTS.has(name)) {
                clear();
                phase = '';
            }
        },
        /**
         * window 'error' 的訊息。只認 AppKit 自己丟的那條 45 秒逾時，而且要離
         * STARTED 夠久——上一輪失敗後它遺留的 45 秒計時器，不能殺掉使用者剛
         * 重試的這一輪。
         */
        onWindowError(message) {
            if (disposed || phase !== 'started') return;
            if (!APPKIT_TIMEOUT_RE.test(String(message || ''))) return;
            if (now() - startedAt < APPKIT_TIMEOUT_MIN_ELAPSED_MS) return;
            stall('appkit-timeout: ' + String(message).slice(0, 80));
        },
        /** 分頁回到前景：使用者剛從 Google／錢包回來，重新起算較短的等待。 */
        onVisible() {
            if (disposed || !phase) return;
            arm(phase === 'userData' ? USER_DATA_MS : RESUME_MS, 'resume: still waiting after return');
        },
        dispose() {
            disposed = true;
            clear();
            phase = '';
        },
        /** 測試／除錯用 */
        get phase() {
            return phase;
        },
    };
}
