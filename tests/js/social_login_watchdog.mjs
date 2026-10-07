// Email／Google 社群登入卡死看門狗（web/js/social-login-watchdog.js）的行為閘門。
// 由 tests/test_evm_wc_js_gates.py 包進 pytest。計時器與時鐘全用假的，不真的等。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const wd = await import(await loadModuleUrl('web/js/social-login-watchdog.js'));
const { createSocialLoginWatchdog, STARTED_MS, RESUME_MS, USER_DATA_MS } = wd;

// 假計時器：tick(ms) 把時間往前推並依序觸發到期的 callback
function harness(opts = {}) {
    let t = 0;
    let seq = 0;
    const timers = new Map();
    const stalls = [];
    let visible = true;
    const dog = createSocialLoginWatchdog({
        onStall: (why) => stalls.push(why),
        isVisible: () => visible,
        setTimer: (fn, ms) => {
            const id = ++seq;
            timers.set(id, { at: t + ms, fn });
            return id;
        },
        clearTimer: (id) => timers.delete(id),
        now: () => t,
        ...opts,
    });
    const tick = (ms) => {
        const end = t + ms;
        for (;;) {
            let next = null;
            for (const [id, tm] of timers) if (tm.at <= end && (!next || tm.at < next.tm.at)) next = { id, tm };
            if (!next) break;
            timers.delete(next.id);
            t = next.tm.at;
            next.tm.fn();
        }
        t = end;
    };
    return { dog, tick, stalls, setVisible: (v) => { visible = v; }, pending: () => timers.size };
}

// ---- 1. 沒有社群事件：永遠不武裝（一般錢包登入不受影響）----
{
    const h = harness();
    h.dog.onEvent('MODAL_OPEN');
    h.dog.onEvent('CONNECT_SUCCESS');
    h.dog.onEvent(undefined);
    h.dog.onEvent(42);
    assert.equal(h.pending(), 0, '無關事件不排計時器');
    h.tick(10 * 60 * 1000);
    assert.deepEqual(h.stalls, [], '沒有社群登入就不會判卡死');
}

// ---- 2. STARTED 後沒下文 → STARTED_MS 判卡死，且只判一次 ----
{
    const h = harness();
    h.dog.onEvent('SOCIAL_LOGIN_STARTED');
    h.tick(STARTED_MS - 1);
    assert.equal(h.stalls.length, 0, '時間未到不判');
    h.tick(1);
    assert.equal(h.stalls.length, 1);
    assert.match(h.stalls[0], /social-started/);
    h.tick(10 * 60 * 1000);
    assert.equal(h.stalls.length, 1, '判過就不再判');
    assert.equal(h.dog.phase, '');
}

// ---- 3. 正常完成（SUCCESS / ERROR / CANCELED）→ 解除 ----
for (const done of ['SOCIAL_LOGIN_SUCCESS', 'SOCIAL_LOGIN_ERROR', 'SOCIAL_LOGIN_CANCELED']) {
    const h = harness();
    h.dog.onEvent('SOCIAL_LOGIN_STARTED');
    h.tick(5000);
    h.dog.onEvent(done);
    assert.equal(h.pending(), 0, `${done} 要清掉計時器`);
    h.tick(10 * 60 * 1000);
    assert.deepEqual(h.stalls, [], `${done} 之後不判卡死`);
}

// ---- 4. REQUEST_USER_DATA 改用較短的等待；其後 SUCCESS 解除 ----
{
    const h = harness();
    h.dog.onEvent('SOCIAL_LOGIN_STARTED');
    h.tick(60000); // 使用者在 Google 頁面花了 60 秒——不該被 STARTED 的計時殺掉
    h.dog.onEvent('SOCIAL_LOGIN_REQUEST_USER_DATA');
    assert.equal(h.dog.phase, 'userData');
    h.tick(USER_DATA_MS - 1);
    assert.equal(h.stalls.length, 0);
    h.tick(1);
    assert.equal(h.stalls.length, 1);
    assert.match(h.stalls[0], /social-user-data/);

    const ok = harness();
    ok.dog.onEvent('SOCIAL_LOGIN_STARTED');
    ok.dog.onEvent('SOCIAL_LOGIN_REQUEST_USER_DATA');
    ok.tick(8000);
    ok.dog.onEvent('SOCIAL_LOGIN_SUCCESS');
    ok.tick(10 * 60 * 1000);
    assert.deepEqual(ok.stalls, [], '換錢包成功就不判');
}

// ---- 5. 不可見時不判死：回到前景才判 ----
{
    const h = harness();
    h.dog.onEvent('SOCIAL_LOGIN_STARTED');
    h.setVisible(false);
    h.tick(STARTED_MS + 30000);
    assert.equal(h.stalls.length, 0, '使用者人在 Google／錢包 App 時不判死');
    h.setVisible(true);
    h.tick(2000);
    assert.equal(h.stalls.length, 1, '回到前景後的下一次檢查才判');
}

// ---- 6. onVisible：回到前景重新起算較短的等待 ----
{
    const h = harness();
    h.dog.onEvent('SOCIAL_LOGIN_STARTED');
    h.tick(STARTED_MS - 1000);
    h.dog.onVisible();
    h.tick(RESUME_MS - 1);
    assert.equal(h.stalls.length, 0, '剛回來給 RESUME_MS 的時間');
    h.tick(1);
    assert.equal(h.stalls.length, 1);

    const idle = harness();
    idle.dog.onVisible();
    assert.equal(idle.pending(), 0, '沒有進行中的社群登入，onVisible 不武裝');
}

// ---- 7. AppKit 自己那條 45 秒 uncaught 逾時 ----
{
    const h = harness();
    h.dog.onEvent('SOCIAL_LOGIN_STARTED');
    h.tick(45000);
    h.dog.onWindowError('Uncaught Error: Social login timed out. Please try again.');
    assert.equal(h.stalls.length, 1, '離 STARTED 45 秒的逾時錯誤 → 立刻判卡死，不必等 100 秒');
    assert.match(h.stalls[0], /appkit-timeout/);

    // 上一輪失敗遺留的 45 秒計時器，在使用者剛重試的 10 秒後才炸——不能殺掉新一輪
    const retry = harness();
    retry.dog.onEvent('SOCIAL_LOGIN_STARTED');
    retry.tick(10000);
    retry.dog.onWindowError('Social login timed out. Please try again.');
    assert.equal(retry.stalls.length, 0, '太早出現的逾時錯誤是舊一輪的殘骸');
    assert.equal(retry.dog.phase, 'started', '新一輪照常進行');

    // 無關的 window error、沒在進行中時的逾時錯誤都忽略
    const other = harness();
    other.dog.onEvent('SOCIAL_LOGIN_STARTED');
    other.tick(50000);
    other.dog.onWindowError('ResizeObserver loop limit exceeded');
    other.dog.onWindowError(undefined);
    assert.equal(other.stalls.length, 0);
    const idle = harness();
    idle.dog.onWindowError('Social login timed out. Please try again.');
    assert.equal(idle.stalls.length, 0);
}

// ---- 8. dispose 之後全部靜音；onStall 丟例外不外洩 ----
{
    const h = harness();
    h.dog.onEvent('SOCIAL_LOGIN_STARTED');
    h.dog.dispose();
    assert.equal(h.pending(), 0);
    h.dog.onEvent('SOCIAL_LOGIN_STARTED');
    h.tick(10 * 60 * 1000);
    assert.deepEqual(h.stalls, []);

    const boom = harness({ onStall: () => { throw new Error('handler bug'); } });
    boom.dog.onEvent('SOCIAL_LOGIN_STARTED');
    assert.doesNotThrow(() => boom.tick(STARTED_MS));
}

// ---- 9. 重新 STARTED 會取代舊計時器（使用者點第二次 Google）----
{
    const h = harness();
    h.dog.onEvent('SOCIAL_LOGIN_STARTED');
    h.tick(90000);
    h.dog.onEvent('SOCIAL_LOGIN_STARTED');
    h.tick(20000); // 距第一次 110 秒，但距第二次才 20 秒
    assert.equal(h.stalls.length, 0);
    h.tick(STARTED_MS - 20000);
    assert.equal(h.stalls.length, 1);
}

console.log('social login watchdog: all assertions passed');
