// 聊天回覆語言判定（2026-09-13）：句子看得出語言跟句子，看不出跟介面語言。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const m = await import(await loadModuleUrl(new URL('../../web/js/message-language.js', import.meta.url).pathname));
const d = m.detectMessageLanguage;

// 中文句子：依介面繁／簡
assert.equal(d('台積電怎麼看', 'zh-TW'), 'zh-TW');
assert.equal(d('台积电怎么看', 'zh-CN'), 'zh-CN');
assert.equal(d('台積電怎麼看', 'en'), 'zh-TW', '英文介面打中文 → 繁中');
// 西里爾 → ru
assert.equal(d('Что с биткоином?', 'zh-TW'), 'ru');
// 明確英文句（≥4 個拉丁字）→ en，不管介面
assert.equal(d('How is Bitcoin looking today?', 'zh-TW'), 'en');
assert.equal(d('compare eth and sol please', 'ru'), 'en');
// 看不出語言（代號／數字／一兩個字）→ 介面語言（修 bug：以前一律 en）
assert.equal(d('BTC?', 'zh-TW'), 'zh-TW');
assert.equal(d('2330', 'zh-TW'), 'zh-TW');
assert.equal(d('eth vs sol', 'zh-CN'), 'zh-CN');
assert.equal(d('TSLA', 'ru'), 'ru');
assert.equal(d('BTC?', 'en'), 'en');
assert.equal(d('', 'zh-TW'), 'zh-TW');
// 介面語言正規化
assert.equal(m.normalizeUiLanguage('zh-HK'), 'zh-TW');
assert.equal(m.normalizeUiLanguage('zh-Hans'), 'zh-CN');
assert.equal(m.normalizeUiLanguage('ru-RU'), 'ru');
assert.equal(m.normalizeUiLanguage('ja'), 'en');
assert.equal(m.normalizeUiLanguage(undefined), 'en');
assert.equal(d('BTC?', 'zh-HK'), 'zh-TW');

console.log('message_language: ok');
