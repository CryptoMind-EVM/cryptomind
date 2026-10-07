/* 需要完整共用層的 classic 頁用（scam-tracker 的 detail／submit；列表頁已改成 SPA 分頁）。
   ⚠ 不載 app.js：其 top-level 的 AppStore.set(...)（SPA store.js 才有）
   在無 store 的子頁會炸，且 8/14 module 化前 classic 頁的 app.js 就是在
   該行中止（window.md=null）——頁面從未依賴其後半段，維持同樣行為。
   apiKeyManager 僅副作用接線；auth 的 AuthManager / initializeAuth 在此
   補裸全域橋（頁面 inline 腳本以 typeof initializeAuth === 'function' 偵測）。
   基礎橋（ui-shell + i18n）見 classic-compat.js。*/
import './classic-compat.js';
import './apiKeyManager.js';
import { AuthManager, initializeAuth } from './auth.js';

window.AuthManager = AuthManager;
window.initializeAuth = initializeAuth;
