/**
 * 彩色頭像：依 user_id 固定從 8 色選一，字取暱稱第一個字。
 * 好友頁、私訊頁、個人頁共用；classic script（forum/js/profile-page.js）走 window.Avatar。
 * 顏色定義在 styles.css 的 .avatar-c0～7，AVATAR_COLORS 只給測試核對（兩邊要一致）。
 */

// 紅、琥珀、翠綠、青、藍、紫、洋紅、粉——白字對比都 ≥ 4.5（tests/js/avatar.mjs 實算）
const AVATAR_COLORS = ['#DC2626', '#B45309', '#047857', '#0E7490', '#2563EB', '#7C3AED', '#A21CAF', '#DB2777'];

/** FNV-1a：同一個字串永遠同一個值，分布夠散 */
function _hash(s) {
    let h = 0x811c9dc5;
    for (let i = 0; i < s.length; i += 1) {
        h ^= s.charCodeAt(i);
        h = Math.imul(h, 0x01000193);
    }
    return h >>> 0;
}

function avatarColorClass(userId) {
    const id = String(userId ?? '');
    if (!id) return 'avatar-c0';
    return `avatar-c${_hash(id) % AVATAR_COLORS.length}`;
}

/** 第一個「字」：Array.from 以 code point 切，emoji、BMP 外的罕用字不會被切成半個 */
function avatarInitial(name) {
    const first = Array.from(String(name ?? '').trim())[0];
    return first ? first.toUpperCase() : '?';
}

/** 已存在的頭像元素（聊天標題）：換字、換色，舊的 avatar-c* 拿掉 */
function applyAvatar(el, userId, name) {
    if (!el) return;
    el.textContent = avatarInitial(name);
    const stale = [];
    el.classList.forEach((c) => {
        if (/^avatar-c\d$/.test(c)) stale.push(c);
    });
    if (stale.length) el.classList.remove(...stale);
    el.classList.add(avatarColorClass(userId));
}

window.Avatar = { colorClass: avatarColorClass, initial: avatarInitial, apply: applyAvatar };

export { AVATAR_COLORS, avatarColorClass, avatarInitial, applyAvatar };
