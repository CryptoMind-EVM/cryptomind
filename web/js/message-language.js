// ========================================
// message-language.js — 聊天回覆語言判定（純函式，2026-09-13）
// 原則：回覆語言＝使用者「這句話」的語言；句子看不出語言（只有代號、數字、
// 一兩個英文字，像「BTC?」「2330」「eth vs sol」）就跟著使用者的介面語言走，
// 不再一律回英文。介面語言＝users.language（登入後由 i18n 同步）或瀏覽器語言。
// 明確的英文句子（≥4 個拉丁字）仍回英文，中文介面的人打英文問也拿得到英文答案。
// ========================================

export const SUPPORTED = ['zh-TW', 'zh-CN', 'en', 'ru'];
const CJK = /[一-鿿㐀-䶿]/;
const CYRILLIC = /[Ѐ-ӿ]/;
const LATIN_WORD = /[A-Za-z]{2,}/g;
const ENGLISH_MIN_WORDS = 4;

/** 介面語言正規化到支援清單；zh-* 未知變體→zh-TW，其餘未知→en。 */
export function normalizeUiLanguage(lang) {
    const s = String(lang || '').trim();
    if (SUPPORTED.includes(s)) return s;
    const low = s.toLowerCase();
    if (low.startsWith('zh')) return low.includes('cn') || low.includes('hans') || low.includes('sg') ? 'zh-CN' : 'zh-TW';
    if (low.startsWith('ru')) return 'ru';
    return 'en';
}

/** 句子語言：CJK→中文（依介面繁／簡）、西里爾→ru、明確英文句→en、其餘→介面語言。 */
export function detectMessageLanguage(text, uiLanguage) {
    const ui = normalizeUiLanguage(uiLanguage);
    const s = String(text || '');
    if (CJK.test(s)) return ui === 'zh-CN' ? 'zh-CN' : 'zh-TW';
    if (CYRILLIC.test(s)) return 'ru';
    const words = s.match(LATIN_WORD) || [];
    if (words.length >= ENGLISH_MIN_WORDS) return 'en';
    return ui;
}
