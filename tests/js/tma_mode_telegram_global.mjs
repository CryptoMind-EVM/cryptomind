// tma-mode.js：一般瀏覽器要清掉 telegram-web-app.js 建的 window.Telegram
//（Reown AppKit 看到它就當作在 Telegram 裡，iOS 會拿掉 Google 登入）；Mini App 內要留著。
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile('web/js/tma-mode.js', 'utf8');

function run(initData) {
    const classes = new Set();
    let onReady = null;
    const win = {
        document: {
            documentElement: { classList: { add: (c) => classes.add(c), contains: (c) => classes.has(c) } },
            addEventListener: (type, fn) => {
                if (type === 'DOMContentLoaded') onReady = fn;
            },
        },
    };
    win.window = win;
    vm.createContext(win);
    vm.runInContext(source, win);
    // telegram-web-app.js（defer）在 DOMContentLoaded 前跑完：建立 window.Telegram
    win.Telegram = { WebApp: { initData } };
    onReady();
    return { win, classes };
}

// 一般瀏覽器：initData 空 → 清掉，不加 .tma
{
    const { win, classes } = run('');
    assert.equal(Boolean(win.Telegram), false, '一般瀏覽器不能留著 window.Telegram');
    assert.equal(classes.has('tma'), false);
    assert.equal(win.isTelegramMiniAppMode(), false);
}

// Telegram Mini App：有 initData → 留著，並加 .tma
{
    const { win, classes } = run('query_id=AAE&user=%7B%7D&hash=abc');
    assert.ok(win.Telegram && win.Telegram.WebApp, 'Mini App 內要保留 window.Telegram');
    assert.equal(classes.has('tma'), true);
    assert.equal(win.isTelegramMiniAppMode(), true);
}

console.log('tma_mode_telegram_global ok');
