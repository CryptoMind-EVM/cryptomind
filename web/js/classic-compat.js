/* Classic 子頁相容橋（scam-tracker / governance — 不在 Vite 多頁 build input）。
   2026-08-14 SPA module 化後，ui-shell / i18n 等共用檔的頂層宣告變 module-scoped：
   classic <script> 直接引用會因 export 語法無法執行，裸全域呼叫（showToast）也找不到。
   本模組以 ES import 載入共用模組，把 classic 頁需要的符號掛回 window。
   頁面必須以 <script type="module"> 載入本檔（deferred，先於 DOMContentLoaded 執行）。
   ⚠ 相依檔案必須列入 Dockerfile raw-js 保留白名單（tests/test_frontend_static_refs.py 看守）。*/
import { showToast } from './ui-shell.js';
import './i18n.js'; // 自掛 window.I18n
import './utils.js'; // 自掛 window.AppUtils / escapeHtml / _t（auth._updateUI 用）
import './api-client.js'; // 自掛 window.AppAPI（auth session restore 用）
import './nav-config.js'; // 自掛 window.NAV_ITEMS / NavPreferences（site-sidebar 功能選單用）
import './components/LanguageSwitcher.js'; // 自掛 window.LanguageSwitcher（site-sidebar 底部的語系切換）
import './nav-badges.js'; // 自掛 window.NavBadges（site-sidebar 功能選單的未讀標示；governance、scam-tracker 都載 site-sidebar）

window.showToast = showToast;
