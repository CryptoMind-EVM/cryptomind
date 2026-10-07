// ========================================
// i18n.js - 國際化初始化模組
// ========================================

(function () {
    'use strict';

    let i18n = null;

    // 從 LocalStorage 讀取儲存的語言偏好
    function getSavedLanguage() {
        try {
            return localStorage.getItem('selectedLanguage');
        } catch (e) {
            console.warn('LocalStorage not available:', e);
            return null;
        }
    }

    // 偵測瀏覽器語言
    function detectBrowserLanguage() {
        const lang = navigator.language || navigator.userLanguage || 'en';
        const baseLang = lang.split('-')[0];
        if (baseLang === 'zh') {
            // zh-CN, zh-Hans, zh-SG → 简体中文；zh-TW, zh-HK, zh-Hant, zh → 繁體中文
            const region = lang.split('-')[1] || '';
            if (region === 'CN' || region === 'Hans' || region === 'SG') {
                return 'zh-CN';
            }
            return 'zh-TW';
        }
        if (baseLang === 'ru') return 'ru';
        return 'en';
    }

    // ---- 語言檔：只下載用得到的（目前語言＋後備 en），切換時才補抓 ----
    // 以前一開頁就把四種全抓（壓縮後共約 230 KB；/static/js 是 no-store，每頁都重抓），
    // 實際只用得到一種（2026-09-26 量測）。
    const SUPPORTED_LANGUAGES = ['zh-TW', 'zh-CN', 'en', 'ru'];
    const FALLBACK_LANGUAGE = 'en';
    const I18N_FILE_VERSION = 55;

    // window 層快取：多頁 build 會把本模組內嵌進多個 chunk，同一個語言檔只抓一次
    function fetchLanguageFile(lng) {
        const cache = (window.__i18nFiles = window.__i18nFiles || {});
        if (!cache[lng]) {
            cache[lng] = fetch(`/static/js/i18n/${lng}.json?v=${I18N_FILE_VERSION}`)
                .then((r) => r.json())
                .catch(() => ({}));
        }
        return cache[lng];
    }

    // 切換到還沒載入的語言前先補抓；不支援的語言交給 i18next 的 fallbackLng
    async function ensureLanguage(lng) {
        if (!i18n || !SUPPORTED_LANGUAGES.includes(lng)) return;
        if (i18n.hasResourceBundle(lng, 'translation')) return;
        i18n.addResourceBundle(lng, 'translation', await fetchLanguageFile(lng), true, true);
    }

    // ---- 伺服器端語言偏好同步（登入後跨裝置一致 + 給 Telegram 共用）----
    function isLoggedIn() {
        const u = window.AuthManager?.currentUser;
        return !!(u && (u.user_id || u.uid));
    }

    // 把使用者選的語言寫回後端（fire-and-forget，未登入則略過）
    async function persistLanguageToServer(lang) {
        if (!isLoggedIn() || !window.AppAPI?.put) return;
        try {
            await window.AppAPI.put('/api/user/language', { language: lang });
        } catch (e) {
            window.APP_CONFIG?.DEBUG_MODE &&
                console.warn('[i18n] persist language failed:', e?.message || e);
        }
    }

    // 登入後若後端有存語言且與目前不同，套用之（不再回寫，避免迴圈）
    // 一頁會收到 auth:ready ×2 ＋ auth:initialized，以前每次都打一次 /api/user/me（2026-09-26
    // 量測：同一頁 3 次）。同一個使用者只查一次；並發呼叫共用同一個 promise；換帳號會重查。
    // 用 window 層記錄：多頁 build 會把本模組內嵌進多個 chunk，各副本要共用同一份狀態。
    function applyServerLanguage() {
        if (!isLoggedIn() || !window.AppAPI?.get || !i18n) return Promise.resolve();
        const u = window.AuthManager.currentUser;
        const uid = u.user_id || u.uid;
        const state = (window.__i18nServerLang = window.__i18nServerLang || {});
        if (state.uid === uid && state.promise) return state.promise;
        state.uid = uid;
        state.promise = fetchAndApplyServerLanguage(uid);
        return state.promise;
    }

    async function fetchAndApplyServerLanguage(uid) {
        try {
            // auth 還原 session 時已經打過 /api/user/me：同一頁、同一個使用者就直接用那份
            const fresh = window.AuthManager?._sessionUserFromBackend;
            const data =
                fresh && fresh.user_id === uid
                    ? { user: fresh }
                    : await window.AppAPI.get('/api/user/me');
            const serverLang = data?.user?.language;
            if (!serverLang && data?.user) {
                // 後端還沒有偏好（從沒手動切過語言）→ 把目前介面語言寫回，
                // Telegram 回覆與每日早報才知道該用哪種語言（2026-09-13）
                persistLanguageToServer(i18n.language);
                return;
            }
            if (serverLang && serverLang !== i18n.language) {
                // 先存偏好再切：languageChanged 在 changeLanguage 途中就由 i18n.on 派發
                //（見 initI18n），監聽者讀 localStorage 要拿到新值
                try {
                    localStorage.setItem('selectedLanguage', serverLang);
                } catch (e) {
                    /* ignore */
                }
                await ensureLanguage(serverLang);
                await i18n.changeLanguage(serverLang);
            }
        } catch (e) {
            // 失敗就清掉記錄，讓下一個登入事件可以重試
            window.__i18nServerLang = {};
            window.APP_CONFIG?.DEBUG_MODE &&
                console.warn('[i18n] applyServerLanguage failed:', e?.message || e);
        }
    }

    // 登入完成的各種事件都嘗試套用後端語言
    ['auth:ready', 'auth-success', 'auth:initialized'].forEach((ev) => {
        window.addEventListener(ev, () => applyServerLanguage());
    });

    // 更新頁面所有帶 data-i18n 的元素
    function updatePageContent() {
        const elements = document.querySelectorAll('[data-i18n]');
        elements.forEach((el) => {
            const key = el.getAttribute('data-i18n');
            if (!i18n || !key) return;

            const argsAttr = el.getAttribute('data-i18n-args');
            let translation;

            try {
                if (argsAttr) {
                    const args = JSON.parse(argsAttr);
                    translation = i18n.t(key, args);
                } else {
                    translation = i18n.t(key);
                }
            } catch (e) {
                console.warn(`Translation error for key "${key}":`, e);
                return;
            }

            // 檢查是否需要更新特定屬性
            const targetAttr = el.getAttribute('data-i18n-attr');

            if (targetAttr === 'placeholder') {
                el.placeholder = translation;
            } else if (targetAttr === 'title') {
                el.title = translation;
            } else if (targetAttr) {
                el.setAttribute(targetAttr, translation);
            } else {
                el.textContent = translation;
            }
        });

        // 更新 html lang 屬性
        if (i18n) {
            document.documentElement.lang = i18n.language;
        }
    }

    // 初始化 i18next
    async function initI18n() {
        // 多頁 build 會把本模組內嵌進多個 chunk（i18n-*.js），同一頁可能重複執行；
        // i18next.init() 被呼叫兩次會打壞內部狀態（language=null、資源清空），
        // 因此以 window 層單例保證只初始化一次，其餘副本共用同一個實例
        if (window.__i18nInitDone) {
            i18n = window.i18next || i18n;
            updatePageContent();
            return;
        }
        if (window.__i18nInitPromise) {
            await window.__i18nInitPromise;
            i18n = window.i18next || i18n;
            updatePageContent();
            return;
        }
        const doInit = (async () => {
        // 確定使用的語言
        const savedLang = getSavedLanguage();
        const browserLang = detectBrowserLanguage();
        const language = savedLang || browserLang;

        // 只載入目前語言與後備語言（改翻譯檔時調 I18N_FILE_VERSION 讓客端重抓）
        const initialLanguages = [...new Set([language, FALLBACK_LANGUAGE])].filter((l) =>
            SUPPORTED_LANGUAGES.includes(l)
        );
        const bundles = await Promise.all(initialLanguages.map(fetchLanguageFile));
        const resources = {};
        initialLanguages.forEach((l, i) => {
            resources[l] = { translation: bundles[i] };
        });

        // 初始化 i18next
        i18n = window.i18next;

        if (!i18n) {
            console.warn(
                'i18next library not found on window. ' +
                'Ensure the i18next CDN script is loaded before this module.'
            );
            // Fallback: expose a no-op t() function so callers don't crash
            window.I18n = {
                init: initI18n,
                t: function (key, options) { return key; },
                isReady: function () { return false; },
                changeLanguage: async function () {},
                getLanguage: function () { return 'en'; },
                updatePageContent: updatePageContent,
            };
            return;
        }

        try {
            await i18n.init({
                lng: language,
                fallbackLng: FALLBACK_LANGUAGE,
                resources,
                interpolation: {
                    escapeValue: false, // Frontend hardcoded translation resources, no user input
                },
            });

            // 等待 DOM 準備好後更新內容
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', updatePageContent);
            } else {
                updatePageContent();
            }

            // 監聽語言切換事件——window 的 languageChanged 只從這裡發（外加下面初始化一次）。
            // 所有切換路徑（I18n.changeLanguage／applyServerLanguage）都經過 i18next，
            // 別在那些地方再手動 dispatch：以前一次切換發兩次，監聽者只好各自去重
            i18n.on('languageChanged', (lng) => {
                updatePageContent();
                // 觸發自定義事件，讓其他組件知道語言已切換
                window.dispatchEvent(
                    new CustomEvent('languageChanged', { detail: { language: lng } })
                );
            });

            // 觸發初始化完成事件，讓其他組件知道 i18n 已準備好
            window.dispatchEvent(
                new CustomEvent('languageChanged', { detail: { language: language } })
            );

            window.APP_CONFIG?.DEBUG_MODE &&
                console.log('i18n initialized with language:', language);
        } catch (error) {
            console.error('Failed to initialize i18n:', error);
        }
        })();

        window.__i18nInitPromise = doInit;
        try {
            await doInit;
            window.__i18nInitDone = true;
        } finally {
            window.__i18nInitPromise = null;
        }
    }

    // 暴露給外部的 API
    window.I18n = {
        init: initI18n,
        t: function (key, options) {
            return i18n ? i18n.t(key, options) : key;
        },
        // 檢查 i18n 是否已完全初始化
        isReady: function () {
            return i18n !== null;
        },
        changeLanguage: async function (lang) {
            if (i18n) {
                // 先存偏好再切：languageChanged 在 changeLanguage 途中就由 i18n.on 派發
                try {
                    localStorage.setItem('selectedLanguage', lang);
                } catch (e) {
                    console.warn('Failed to save language preference:', e);
                }
                await ensureLanguage(lang);
                await i18n.changeLanguage(lang);
                // 登入時把偏好寫回後端（跨裝置 + Telegram 共用）
                persistLanguageToServer(lang);
            }
        },
        getLanguage: function () {
            return i18n ? i18n.language : 'en';
        },
        // 供組件動態注入後呼叫，更新新加入的 data-i18n 元素
        updatePageContent: updatePageContent,
    };
})();

// Side-effect module — I18n is on window for backward compatibility.
export {};
