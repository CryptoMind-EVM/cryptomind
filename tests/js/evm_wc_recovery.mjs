// 2026-09-05 三個無聲卡死的對策——決策函數行為測試。
// 對應線上實測（?wc-debug=1 取證）：
//   A 死 QR：pairing=0 超過寬限 → 換發新碼（掃死碼的錢包端永遠不會有簽署畫面）
//   B 誤判取消：連上瞬間 connected=true 但 provider 慢 1~2 秒——絕不判取消
//   beacon：fire-and-forget、payload 與後端模型對齊
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const moduleUrl = await loadModuleUrl('web/js/evm-walletconnect.js');
const {
    shouldRegenerateQr,
    closeConfirmationAction,
    reportWcConnectEvent,
} = await import(moduleUrl);

// ---- A｜死 QR 判定 ----
assert.equal(
    shouldRegenerateQr({ elapsedMs: 19000, pairingSummary: 'pairing=0', connected: false, regenCount: 0 }),
    true,
    '超過寬限且 proposal 從未發佈 → 換發',
);
assert.equal(
    shouldRegenerateQr({ elapsedMs: 19000, pairingSummary: 'pairing=1(active=false,alive=280s,uri=match,wallet=online)', connected: false, regenCount: 0 }),
    false,
    'pairing 有發佈 ≠ 死碼——那可能是錢包端還沒批准',
);
assert.equal(
    shouldRegenerateQr({ elapsedMs: 5000, pairingSummary: 'pairing=0', connected: false, regenCount: 0 }),
    false,
    '寬限內（proposal 還在飛）不換',
);
assert.equal(
    shouldRegenerateQr({ elapsedMs: 19000, pairingSummary: 'pairing=0', connected: true, regenCount: 0 }),
    false,
    '已連上不換',
);
assert.equal(
    shouldRegenerateQr({ elapsedMs: 19000, pairingSummary: 'pairing=0', connected: false, regenCount: 2 }),
    false,
    '換發次數上限（防無限循環）',
);
assert.equal(
    shouldRegenerateQr({ elapsedMs: 19000, pairingSummary: null, connected: false, regenCount: 0 }),
    false,
    '摘要在異常時不算死碼——fail-safe 不動作',
);

// ---- B｜關窗判定：連線中絕不判取消 ----
assert.equal(closeConfirmationAction({ accountConnected: true, probeSeesConnection: true }), 'connected');
assert.equal(
    closeConfirmationAction({ accountConnected: true, probeSeesConnection: false, waitedMs: 1000 }),
    'keep-waiting',
    '線上 15:41:53 誤殺場景：connected=true 但 provider 未就緒 → 續等',
);
assert.equal(
    closeConfirmationAction({ accountConnected: true, probeSeesConnection: false, waitedMs: 11000 }),
    'give-up',
    '等滿預算仍不可讀 → 照逾時交給續登',
);
assert.equal(closeConfirmationAction({ accountConnected: false, probeSeesConnection: false }), 'cancel');
assert.equal(closeConfirmationAction({}), 'cancel', 'undefined 輸入當取消（fail-safe）');
// 2026-09-07：AppKit account state 對 Trust 連上後仍是空的——WC session 存在
// 就不准判取消（桌機掃碼連上後電腦端毫無反應、要點第二次才跳簽名的成因）
assert.equal(
    closeConfirmationAction({ accountConnected: false, sessionConnected: true, waitedMs: 0 }),
    'keep-waiting',
    'WC session 已建立 → 續等 provider，不得判取消',
);
assert.equal(
    closeConfirmationAction({ accountConnected: false, sessionConnected: true, waitedMs: 11000 }),
    'give-up',
    'session 在但等滿預算 → 交給續登（仍不是取消）',
);

// ---- beacon：fire-and-forget、payload 截斷 ----
{
    const calls = [];
    globalThis.window = {
        AppAPI: {
            post: (url, body) => {
                calls.push({ url, body });
                return Promise.resolve();
            },
        },
    };
    reportWcConnectEvent('pairing-dead', 'x'.repeat(999));
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, '/api/user/wc-connect-event');
    assert.equal(calls[0].body.mode, 'pairing-dead');
    assert.equal(calls[0].body.summary.length, 300, 'summary 要截到後端上限 300');
    delete globalThis.window;
}
// 無 window / AppAPI 環境（Node）不得拋錯
reportWcConnectEvent('cancel-close', '');

// ---- 防重入回饋節流（2026-09-05「點好幾次都沒反應」）----
{
    const fbUrl = await loadModuleUrl('web/js/evm-login-feedback.js');
    const { shouldShowInFlightHint } = await import(fbUrl);
    assert.equal(shouldShowInFlightHint(0, 1000), true, '首次點擊要回饋');
    assert.equal(shouldShowInFlightHint(1000, 3000), false, '節流內（<4s）連點不洗版');
    assert.equal(shouldShowInFlightHint(1000, 5200), true, '過節流後要再回饋');
    assert.equal(shouldShowInFlightHint(1000, 2000, 3000), false, '自訂節流視窗要生效');
    assert.equal(shouldShowInFlightHint(1000, 4100, 3000), true);
}

// ---- 登出真斷（2026-09-05 DANNY「要徹底解決」）：旗標閉環 ----
{
    const fbUrl = await loadModuleUrl('web/js/evm-login-feedback.js');
    const { applyLogoutDisconnectFlags, hasPendingDisconnect } = await import(fbUrl);
    const mkStorage = () => {
        const m = new Map([['evmWcResumeAt', '123']]);
        return {
            getItem: (k) => (m.has(k) ? m.get(k) : null),
            setItem: (k, v) => m.set(k, String(v)),
            removeItem: (k) => m.delete(k),
        };
    };
    const st = mkStorage();
    assert.equal(applyLogoutDisconnectFlags(st, 1000), true);
    assert.equal(st.getItem('evmWcResumeAt'), null, '續登旗標必須先廢——否則重載後自動再登入');
    assert.equal(st.getItem('evmWcPendingDisconnect'), '1000', '待斷線旗標要設（時間戳）');
    assert.equal(hasPendingDisconnect(st), true);
    st.removeItem('evmWcPendingDisconnect');
    assert.equal(hasPendingDisconnect(st), false, '收尾後旗標要清');

    // 邊界：storage 不可用／寫入炸掉——不得拋（登out路徑絕不能炸）
    assert.equal(applyLogoutDisconnectFlags(null), false, '無 localStorage（隱私模式）安靜放棄');
    assert.equal(hasPendingDisconnect(null), false);
    const throwing = {
        getItem: () => null,
        removeItem: () => {},
        setItem: () => { throw new Error('quota'); },
    };
    assert.equal(applyLogoutDisconnectFlags(throwing, 1), false, '寫入失敗要回 false 不炸');
}

// ---- 前景重送判定（2026-09-05 Trust 三連漏：錢包凍結漏包對策）----
{
    const fbUrl = await loadModuleUrl('web/js/evm-login-feedback.js');
    const { shouldResendSignOnForeground } = await import(fbUrl);
    assert.equal(
        shouldResendSignOnForeground({ visible: true, waiting: true, resendCount: 0 }),
        true,
        '切回頁面且還在等——要立刻重發（別等 150s）',
    );
    assert.equal(
        shouldResendSignOnForeground({ visible: false, waiting: true, resendCount: 0 }),
        false,
        '頁面不可見（離開中）不重發',
    );
    assert.equal(
        shouldResendSignOnForeground({ visible: true, waiting: false, resendCount: 0 }),
        false,
        '簽名已結束不重發',
    );
    assert.equal(
        shouldResendSignOnForeground({ visible: true, waiting: true, resendCount: 2 }),
        false,
        '重送上限（防錢包端彈窗洗版）',
    );
    assert.equal(
        shouldResendSignOnForeground({ visible: true, waiting: true, resendCount: 1, max: 2 }),
        true,
        '自訂上限：已重發 1 次、上限 2——還能再發一次',
    );
    assert.equal(
        shouldResendSignOnForeground({ visible: true, waiting: true, resendCount: 2, max: 2 }),
        false,
        '自訂上限：已達 2/2——不再發',
    );
    assert.equal(
        shouldResendSignOnForeground({ visible: true, waiting: true, resendCount: 0, max: 1 }),
        true,
    );
}

console.log('evm_wc_recovery: all assertions passed');
