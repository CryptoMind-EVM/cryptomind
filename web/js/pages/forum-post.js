/* forum-post.js — 論壇 post 頁的單一薄 entry（2026-08-24 chunk 隔離修復）。

   此前各論壇頁把 10+ 個共用模組（app.js/auth.js/...）各自宣告為 HTML
   <script type=module> entry；這些模組同時是 SPA main 入口的依賴，
   rolldown 分組時可能把它們併進 main chunk → 論壇頁 import main →
   整個 SPA 在論壇頁執行（messages.html#chat 排版崩壞事故）。
   改為每頁一個薄 entry、共用模組退為純依賴（manualChunks shared-core
   可穩定隔離），entry 之間永遠不會互相 import。

   ⚠ import 順序 = 原 HTML script 順序（模組多為 side-effect 全域接線，
   順序是行為契約，勿重排）。 */
import '../security-utils.js';
import '../utils.js';
import '../store.js';
import '../ui-shell.js';
import '../nav-config.js'; // site-sidebar.js 的功能選單讀 window.NavPreferences（缺了選單是空的）
import '../nav-badges.js'; // site-sidebar.js 功能選單的未讀標示
import '../i18n.js';
import '../components/LanguageSwitcher.js'; // site-sidebar.js 底部的語系切換
import '../app.js';
import '../api-client.js';
import '../apiKeyManager.js';
import '../auth.js';
import '../ton-auth.js';
import '../forum-config.js';
import '../forum-api.js';
import '../forum-app.js';
