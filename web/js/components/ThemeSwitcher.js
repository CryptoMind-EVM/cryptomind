// ========================================
// ThemeSwitcher.js - 深淺主題切換器（仿 LanguageSwitcher 結構）
// ========================================
// 搭配 web/js/early-init.js（首次載入防 FOUC）與 styles.css 的 :root / html.dark 雙 token。
// localStorage key: 'selectedTheme'，值 'light' | 'dark' | 'system'。預設 'light'。

class ThemeSwitcher {
    constructor(containerSelector = '.theme-switcher-container') {
        if (typeof containerSelector === 'string') {
            this.container = document.querySelector(containerSelector);
        } else if (containerSelector instanceof HTMLElement) {
            this.container = containerSelector;
        } else {
            this.container = null;
        }

        this.currentTheme = this.getSavedTheme() || 'light';
        this.isOpen = false;

        if (this.container) {
            this.init();
        }
    }

    static init(container) {
        return new ThemeSwitcher(container);
    }

    getSavedTheme() {
        try {
            return localStorage.getItem('selectedTheme');
        } catch (e) {
            return null;
        }
    }

    /** 目前實際生效的 theme（system 會解析成 light/dark） */
    getEffectiveTheme() {
        if (this.currentTheme === 'system') {
            return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
                ? 'dark' : 'light';
        }
        return this.currentTheme;
    }

    init() {
        this.render();
        this.attachEvents();
        // 確保初次渲染時 <html> 的 dark class 與儲存偏好一致
        // （early-init 已處理首次載入，這裡補防 ThemeSwitcher 載入順序邊角案例）
        this.applyTheme(this.currentTheme, false);
        // 監聽系統深淺偏好變化（僅 system 模式生效）
        if (window.matchMedia) {
            window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
                if (this.currentTheme === 'system') this.applyTheme('system', false);
            });
        }
    }

    render() {
        if (!this.container) return;

        const icons = { light: 'sun', dark: 'moon', system: 'monitor' };
        const names = { light: 'Light', dark: 'Dark', system: 'System' };

        this.container.innerHTML = `
            <div class="theme-switcher">
                <button class="theme-trigger" type="button" aria-label="Theme selector" data-i18n="theme.selectorAria" data-i18n-attr="aria-label" aria-expanded="false">
                    <i data-lucide="${icons[this.currentTheme] || 'sun'}" class="w-5 h-5"></i>
                </button>
                <div class="theme-dropdown hidden" role="menu">
                    <div class="theme-option ${this.currentTheme === 'light' ? 'active' : ''}" role="menuitem" data-theme="light" tabindex="0">
                        <i data-lucide="sun" class="w-4 h-4"></i>
                        <span class="theme-option-name" data-i18n="theme.light">Light</span>
                    </div>
                    <div class="theme-option ${this.currentTheme === 'dark' ? 'active' : ''}" role="menuitem" data-theme="dark" tabindex="0">
                        <i data-lucide="moon" class="w-4 h-4"></i>
                        <span class="theme-option-name" data-i18n="theme.dark">Dark</span>
                    </div>
                    <div class="theme-option ${this.currentTheme === 'system' ? 'active' : ''}" role="menuitem" data-theme="system" tabindex="0">
                        <i data-lucide="monitor" class="w-4 h-4"></i>
                        <span class="theme-option-name" data-i18n="theme.system">System</span>
                    </div>
                </div>
            </div>
        `;
        // 初始化 lucide icon（與 LanguageSwitcher 同機制）
        if (window.AppUtils && window.AppUtils.refreshIcons) {
            window.AppUtils.refreshIcons(this.container);
        }
    }

    attachEvents() {
        const switcher = this.container.querySelector('.theme-switcher');
        if (!switcher) return;

        const trigger = switcher.querySelector('.theme-trigger');
        const dropdown = switcher.querySelector('.theme-dropdown');
        const options = switcher.querySelectorAll('.theme-option');

        trigger.addEventListener('click', (e) => {
            e.stopPropagation();
            this.isOpen = !this.isOpen;
            dropdown.classList.toggle('hidden', !this.isOpen);
            trigger.setAttribute('aria-expanded', this.isOpen ? 'true' : 'false');
        });

        trigger.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                trigger.click();
            }
        });

        options.forEach(option => {
            option.addEventListener('click', (e) => {
                e.stopPropagation();
                this.changeTheme(e.currentTarget.dataset.theme);
            });
            option.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    this.changeTheme(e.currentTarget.dataset.theme);
                }
            });
        });

        this._outsideClickHandler = () => {
            if (this.isOpen) this._closeDropdown();
        };
        document.addEventListener('click', this._outsideClickHandler);
    }

    /**
     * 套用 theme 到 <html>（toggle dark class）。
     * @param {string} theme - 'light' | 'dark' | 'system'
     * @param {boolean} persist - 是否寫入 localStorage（初次同步設 false）
     */
    applyTheme(theme, persist = true) {
        this.currentTheme = theme;
        const effective = theme === 'system'
            ? (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
            : theme;

        const root = document.documentElement;
        root.classList.toggle('dark', effective === 'dark');

        // 同步行動瀏覽器網址列／狀態列顏色，使其符合「實際生效」的主題
        // （而非 OS 的 prefers-color-scheme），避免 app 亮/暗與瀏覽器 chrome 不一致
        const themeColorMeta = document.querySelector('meta[name="theme-color"]');
        if (themeColorMeta) {
            themeColorMeta.setAttribute('content', effective === 'dark' ? '#14161F' : '#FFFDF9');
        }

        if (persist) {
            try {
                localStorage.setItem('selectedTheme', theme);
            } catch (e) {
                console.warn('Failed to save theme preference:', e);
            }
        }

        // 通知其他模組（如未來需依 theme 重算圖表配色）
        window.dispatchEvent(new CustomEvent('themeChanged', { detail: { theme, effective } }));
        this.updateDisplay();
    }

    changeTheme(theme) {
        if (theme === this.currentTheme) {
            this._closeDropdown();
            return;
        }
        this.applyTheme(theme, true);
        this._closeDropdown();
    }

    updateDisplay() {
        if (!this.container) return;
        const icons = { light: 'sun', dark: 'moon', system: 'monitor' };
        const iconEl = this.container.querySelector('.theme-trigger i[data-lucide]');
        // lucide 渲染後會把 data-lucide 換成 svg，改 data-lucide 後需 refresh
        if (iconEl) iconEl.setAttribute('data-lucide', icons[this.currentTheme] || 'sun');
        // 清掉舊 svg 重渲染
        const trigger = this.container.querySelector('.theme-trigger');
        if (trigger) {
            trigger.innerHTML = `<i data-lucide="${icons[this.currentTheme] || 'sun'}" class="w-5 h-5"></i>`;
            if (window.AppUtils && window.AppUtils.refreshIcons) {
                window.AppUtils.refreshIcons(this.container);
            }
        }
        this.container.querySelectorAll('.theme-option').forEach(opt => {
            opt.classList.toggle('active', opt.dataset.theme === this.currentTheme);
        });
    }

    _closeDropdown() {
        this.isOpen = false;
        if (!this.container) return;
        const dropdown = this.container.querySelector('.theme-dropdown');
        const trigger = this.container.querySelector('.theme-trigger');
        if (dropdown) dropdown.classList.add('hidden');
        if (trigger) trigger.setAttribute('aria-expanded', 'false');
    }
}

window.ThemeSwitcher = ThemeSwitcher;
export { ThemeSwitcher };
