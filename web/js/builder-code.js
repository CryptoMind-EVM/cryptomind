// ========================================
// builder-code.js — Base Builder Code 交易歸因（ERC-8021，2026-09-13）
// 把 Dashboard 發的代碼編成尾巴接在 calldata 最後：合約照常執行、忽略多出的 bytes，
// Base 的索引器從尾端往回讀（marker 16 bytes → schema 1 byte → 長度 1 byte → 代碼），
// 把這筆交易記到 CryptoMind 名下（Dashboard 交易數、週排行榜、Builder Rewards）。
// 後端驗款看的是 Transfer event，不受 calldata 尾巴影響。
// 規格：docs.base.org/apps/builder-codes；代碼在 store-assets/base/SUBMISSION.md。
// ========================================

export const BASE_BUILDER_CODE = 'bc_udv657t6';
export const ERC8021_MARKER = '80218021802180218021802180218021';

/** 代碼 → schema 0 尾巴（hex，不含 0x）。空字串或非 ASCII 代碼回空字串（不接尾巴）。 */
export function erc8021Suffix(code) {
    const s = String(code || '').trim();
    if (!s || s.length > 255 || !/^[\x21-\x7e]+$/.test(s)) return '';
    let hex = '';
    for (let i = 0; i < s.length; i++) hex += s.charCodeAt(i).toString(16).padStart(2, '0');
    return hex + s.length.toString(16).padStart(2, '0') + '00' + ERC8021_MARKER;
}

/** 把尾巴接到 0x 開頭的 calldata 後面；data 不合法或沒代碼就原樣回傳。 */
export function withBuilderCode(data, code = BASE_BUILDER_CODE) {
    if (typeof data !== 'string' || !/^0x[0-9a-fA-F]*$/.test(data)) return data;
    const suffix = erc8021Suffix(code);
    return suffix ? data + suffix : data;
}
