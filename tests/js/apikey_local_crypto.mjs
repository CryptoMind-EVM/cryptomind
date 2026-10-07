// apiKeyManager.js localStorage 降級加密（2026-09-25）：每筆隨機 salt 的 enc:v3、
// 舊 enc:v2（固定 salt）照樣解得開並就地改寫成 v3、解不開不刪且留 warn。
// LEGACY_LOCAL_CIPHERTEXT 是改版前的 _encrypt 實際產出的；user id／明文皆為測試假值。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

const LEGACY_USER_ID = 'unit-test-fixture-user-0001';
const LEGACY_PLAINTEXT = 'sk-unit-test-legacy-local-0001';
const LEGACY_LOCAL_CIPHERTEXT =
    'enc:v2:pOFkxFUiT97fWCG1YMQzrTOKVPLL64myYwx6aJUp+0EZB9k9b5XuyK1KhWjX/LfK5F6En5Db/1Bh3w==';

const store = new Map();
const warnings = [];
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.document = { addEventListener() {} };
globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
};
globalThis.console.log = () => {};
globalThis.console.warn = (...args) => warnings.push(args.map(String).join(' '));
globalThis.AuthManager = { currentUser: { user_id: LEGACY_USER_ID } };

const { APIKeyManager: M } = await import(
    await loadModuleUrl(new URL('../../web/js/apiKeyManager.js', import.meta.url).pathname)
);

const b64bytes = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const toB64 = (bytes) => btoa(String.fromCharCode(...bytes));

// 1) 舊格式（改版前產出）仍解得開
assert.equal(await M._decrypt(LEGACY_LOCAL_CIPHERTEXT), LEGACY_PLAINTEXT, 'legacy enc:v2 must still decrypt');

// 2) 新格式：enc:v3: + base64(salt16 | iv12 | ct+tag)，round-trip
const a = await M._encrypt('sk-unit-test-v3-roundtrip');
assert.ok(a.startsWith('enc:v3:'), `new writes use enc:v3, got ${a.slice(0, 7)}`);
assert.equal(await M._decrypt(a), 'sk-unit-test-v3-roundtrip');
const rawA = b64bytes(a.slice(7));
assert.equal(rawA.length, 16 + 12 + 'sk-unit-test-v3-roundtrip'.length + 16, 'salt|iv|ct|tag layout');

// 3) 同一明文兩次加密不同，salt 也不同
const b = await M._encrypt('sk-unit-test-v3-roundtrip');
assert.notEqual(a, b);
assert.notDeepEqual(rawA.slice(0, 16), b64bytes(b.slice(7)).slice(0, 16), 'fresh salt per encryption');

// 4) 竄改／截斷：乾淨失敗（null、不丟例外）、不刪 localStorage、留 warn（不含金鑰）
for (const [label, mutate] of [
    ['salt', (r) => { r[2] ^= 1; return r; }],
    ['iv', (r) => { r[17] ^= 1; return r; }],
    ['ciphertext', (r) => { r[30] ^= 1; return r; }],
    ['tag', (r) => { r[r.length - 1] ^= 1; return r; }],
    ['truncated', (r) => r.slice(0, 20)],
    ['empty', () => new Uint8Array(0)],
]) {
    const bad = 'enc:v3:' + toB64(mutate(Uint8Array.from(rawA)));
    store.set('user_openai_api_key', bad);
    warnings.length = 0;
    assert.equal(await M._getKeyLocalStorage('openai'), null, `tampered (${label}) must fail cleanly`);
    assert.equal(store.get('user_openai_api_key'), bad, `tampered (${label}) value must be kept, not dropped`);
    assert.ok(warnings.length >= 1, `tampered (${label}) must log a warning`);
    assert.ok(!warnings.join(' ').includes('sk-unit-test'), 'warning must not leak key material');
}
assert.equal(await M._decrypt('enc:v3:%%%not-base64%%%'), null, 'malformed base64 fails cleanly');

// 5) 讀取時遷移：舊 enc:v2 → 解開後就地改寫成 enc:v3，內容不變
store.set('user_openai_api_key', LEGACY_LOCAL_CIPHERTEXT);
assert.equal(await M._getKeyLocalStorage('openai'), LEGACY_PLAINTEXT);
const migrated = store.get('user_openai_api_key');
assert.ok(migrated.startsWith('enc:v3:'), 'legacy value rewritten as enc:v3');
assert.equal(await M._decrypt(migrated), LEGACY_PLAINTEXT);
// 再讀一次：已是 v3，不再改寫
assert.equal(await M._getKeyLocalStorage('openai'), LEGACY_PLAINTEXT);
assert.equal(store.get('user_openai_api_key'), migrated);

// 6) 登出狀態（沒有 user id 可派生金鑰）讀到舊值：回 null 但保留原值，不改寫成明文
globalThis.AuthManager = { currentUser: null };
store.set('user_openai_api_key', LEGACY_LOCAL_CIPHERTEXT);
assert.equal(await M._getKeyLocalStorage('openai'), null);
assert.equal(store.get('user_openai_api_key'), LEGACY_LOCAL_CIPHERTEXT, 'logged-out read keeps the value');
globalThis.AuthManager = { currentUser: { user_id: LEGACY_USER_ID } };

// 7) 登入後把本地金鑰搬到後端：後端存失敗（setKey 退回 localStorage）時不能把本地金鑰刪掉
globalThis.AppAPI = { post: async () => { throw new Error('backend down'); } };
store.set('user_openai_api_key', LEGACY_LOCAL_CIPHERTEXT);
await M._migrateLegacyLocalKeyIfNeeded('openai');
const kept = store.get('user_openai_api_key');
assert.ok(kept, 'local key must survive a failed backend migration');
assert.equal(await M._decrypt(kept), LEGACY_PLAINTEXT);
// 後端存成功 → 才移除本地副本
let posted = null;
globalThis.AppAPI = { post: async (_url, body) => { posted = body; return { success: true }; } };
await M._migrateLegacyLocalKeyIfNeeded('openai');
assert.equal(posted?.api_key, LEGACY_PLAINTEXT);
assert.equal(store.get('user_openai_api_key'), undefined, 'local copy removed only after backend save');

process.stderr.write('apikey_local_crypto: ok\n');
