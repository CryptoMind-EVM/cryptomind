// 市場頁 Ticker WebSocket 更新（2026-09-25 市場頁盤查）：
// 1) 24h 漲跌 0（含 "0.00" 字串、-0、四捨五入後是 0.00）要中性色，不能標綠／紅；
//    字串輸入不能讓 toFixed 丟例外；更新顏色時不能洗掉卡片原本的 class（font-mono）。
// 2) oversold／overbought 清單的 .ticker-change 放的是 RSI——tick 只能更新價格，不能蓋掉 RSI。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadModuleUrl } from './_load.mjs';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

function makeEl(text = '', className = '') {
    const el = {
        textContent: text,
        dataset: {},
        _classes: new Set(className.split(/\s+/).filter(Boolean)),
        get className() {
            return [...this._classes].join(' ');
        },
        set className(v) {
            this._classes = new Set(String(v).split(/\s+/).filter(Boolean));
        },
    };
    el.classList = {
        add: (...c) => c.forEach((x) => el._classes.add(x)),
        remove: (...c) => c.forEach((x) => el._classes.delete(x)),
        contains: (c) => el._classes.has(c),
    };
    return el;
}

// 仿 market-screener.js renderList 的卡片結構
function makeCard(symbol, changeText, changeClass) {
    const price = makeEl('$1.00', 'text-[11px] text-textMuted font-mono opacity-80 ticker-price');
    const change = makeEl(changeText, `text-base font-black font-mono ticker-change ${changeClass}`);
    const card = {
        dataset: { symbol },
        querySelector: (sel) => (sel === '.ticker-price' ? price : sel === '.ticker-change' ? change : null),
    };
    return { card, price, change };
}

const containers = {};
function setContainer(id, cards) {
    containers[id] = { querySelectorAll: () => cards };
}

globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.document = {
    addEventListener() {},
    getElementById: (id) => containers[id] || null,
};

const mod = await import(await loadModuleUrl(path.join(REPO, 'web/js/market-ws.js')));
assert.equal(typeof mod.updateMarketWatchItem, 'function', 'market-ws.js 要 export updateMarketWatchItem');

// ── 1) 0% 中性色 ──────────────────────────────────────────────
const top = makeCard('BTC', '+1.00%', 'text-success');
setContainer('top-list', [top.card]);

function tick(change24h, last = 50000) {
    mod.updateMarketWatchItem('BTC-USDT', { last, change24h });
}
const colour = (el) => ['text-success', 'text-danger', 'text-textMuted'].filter((c) => el.classList.contains(c));

tick(1.234);
assert.equal(top.change.textContent, '+1.23%');
assert.deepEqual(colour(top.change), ['text-success']);

for (const zero of [0, -0, '0', '0.00', '-0.00', -0.004, '0.001']) {
    tick(zero);
    assert.equal(top.change.textContent, '0.00%', `change24h=${JSON.stringify(zero)} 應顯示 0.00%，實際 ${top.change.textContent}`);
    assert.deepEqual(colour(top.change), ['text-textMuted'], `change24h=${JSON.stringify(zero)} 應為中性色，實際 ${top.change.className}`);
}

tick('-2.5');
assert.equal(top.change.textContent, '-2.50%', '字串負值要能顯示');
assert.deepEqual(colour(top.change), ['text-danger']);

tick('3.10');
assert.equal(top.change.textContent, '+3.10%', '字串正值要能顯示');
assert.deepEqual(colour(top.change), ['text-success']);
// 只換顏色，不能洗掉 renderList 給的其他 class
assert.ok(top.change.classList.contains('font-mono'), `不能洗掉 font-mono，實際 ${top.change.className}`);
assert.ok(top.change.classList.contains('ticker-change'), '不能洗掉 ticker-change');

// 缺值／非數字：保留原本內容
const before = top.change.textContent;
tick(undefined);
tick(null);
tick('abc');
assert.equal(top.change.textContent, before, '缺值或非數字不能覆寫漲跌幅');

// ── 2) RSI 不被 tick 蓋掉 ─────────────────────────────────────
setContainer('top-list', []);
const oversold = makeCard('ETH', '25.00', 'text-success');
const overbought = makeCard('SOL', '78.40', 'text-danger');
setContainer('oversold-list', [oversold.card]);
setContainer('overbought-list', [overbought.card]);

mod.updateMarketWatchItem('ETH-USDT', { last: 2500, change24h: -3.2 });
mod.updateMarketWatchItem('SOL', { last: 150, change24h: 0 });

assert.equal(oversold.price.textContent, '$2,500.00', 'RSI 清單的價格仍要即時更新');
assert.equal(overbought.price.textContent, '$150.00', 'RSI 清單的價格仍要即時更新');
assert.equal(oversold.change.textContent, '25.00', `oversold RSI 被 tick 蓋掉：${oversold.change.textContent}`);
assert.deepEqual(colour(oversold.change), ['text-success'], `oversold RSI 顏色被改：${oversold.change.className}`);
assert.equal(overbought.change.textContent, '78.40', `overbought RSI 被 tick 蓋掉：${overbought.change.textContent}`);
assert.deepEqual(colour(overbought.change), ['text-danger'], `overbought RSI 顏色被改：${overbought.change.className}`);

console.error('market_ws_ticker: ok');
