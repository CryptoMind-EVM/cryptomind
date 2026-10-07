// ========================================
// connections.js — Connections（連結）板塊
// design：docs/plans/2026-09-08-connections-tab-design.md
// ========================================
//
// 這個板塊本身沒有狀態——內容由各連接自己的 App 渲染（Telegram／Google／LINE
// 各自的 LinkApp，Email 走 email-brief-settings.js）。本物件只負責兩件事：
//   1. 進分頁時把各連接的 App 叫起來（可重入：每次進來都重抓綁定狀態）
//   2. 返回鍵（本板塊 defaultEnabled:false，常不在底部導覽列上，
//      主要入口是 Settings 的引導卡——沒有返回鍵會讓人卡在這裡）

import { loadEmailBrief } from './email-brief-settings.js';

const ConnectionsTab = {
    init() {
        // 不設一次性守衛：重抓綁定狀態正是使用者重進這頁想看到的東西，
        // 而且 loadStatus 只有一發 GET /api/telegram/status。
        //（AI Studio 那道 _initialized 守衛讓語言切換失效，見
        //  2026-09-08-ai-studio-i18n-hardcoded-chinese-design.md）
        if (window.TelegramLinkApp && typeof window.TelegramLinkApp.init === 'function') {
            Promise.resolve(window.TelegramLinkApp.init()).catch((e) =>
                console.warn('TelegramLinkApp init failed:', e)
            );
        }
        if (window.GoogleLinkApp && typeof window.GoogleLinkApp.init === 'function') {
            Promise.resolve(window.GoogleLinkApp.init()).catch((e) =>
                console.warn('GoogleLinkApp init failed:', e)
            );
        }
        if (window.LineLinkApp && typeof window.LineLinkApp.init === 'function') {
            Promise.resolve(window.LineLinkApp.init()).catch((e) =>
                console.warn('LineLinkApp init failed:', e)
            );
        }
        // Email 早報訂閱（2026-09-28 自 Settings 早報卡搬來）
        loadEmailBrief().catch((e) => console.warn('Email brief init failed:', e));
    },

    goBack() {
        // 連接的設定入口在 Settings，回那裡比回 chat 合理。
        if (typeof window.switchTab === 'function') window.switchTab('settings');
    },
};

window.ConnectionsTab = ConnectionsTab;
export { ConnectionsTab };
