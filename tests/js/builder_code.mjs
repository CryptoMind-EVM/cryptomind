// Base Builder Code（ERC-8021）尾巴——純函式斷言。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const m = await import(await loadModuleUrl(new URL('../../web/js/builder-code.js', import.meta.url).pathname));

// 1) Dashboard 發給我們的編碼一字不差（store-assets/base/SUBMISSION.md）
const DASHBOARD = '62635f75647636353774360b0080218021802180218021802180218021';
assert.equal(m.erc8021Suffix('bc_udv657t6'), DASHBOARD);
assert.equal(m.BASE_BUILDER_CODE, 'bc_udv657t6');

// 2) 版面：代碼 → 長度 1 byte → schema 0x00 → marker 16 bytes（從尾端往回讀）
const s = m.erc8021Suffix('baseapp');
assert.equal(s.slice(-32), m.ERC8021_MARKER);
assert.equal(s.slice(-34, -32), '00');
assert.equal(s.slice(-36, -34), '07');
assert.equal(Buffer.from(s.slice(0, -36), 'hex').toString('ascii'), 'baseapp');

// 3) 接到 transfer calldata 後面；原 calldata 原封不動
const transfer = '0xa9059cbb' + '0'.repeat(24) + 'cfdcc17a70bd1c7c69256ad4d488e08bb8f1c51c' + '0'.repeat(63) + '1';
const out = m.withBuilderCode(transfer);
assert.ok(out.startsWith(transfer));
assert.equal(out.length, transfer.length + DASHBOARD.length);
assert.equal(out.slice(transfer.length), DASHBOARD);

// 4) 防呆：沒代碼／非 ASCII／不合法 data 都不動
assert.equal(m.erc8021Suffix(''), '');
assert.equal(m.erc8021Suffix('中文'), '');
assert.equal(m.withBuilderCode(transfer, ''), transfer);
assert.equal(m.withBuilderCode('not-hex'), 'not-hex');
assert.equal(m.withBuilderCode(undefined), undefined);

console.log('builder_code: ok');
