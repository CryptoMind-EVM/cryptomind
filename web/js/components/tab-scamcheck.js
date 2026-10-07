// Tab: Scam Check（#scamcheck）— 2026-09-27 取代 /scam-tracker/ 列表頁
// plan：docs/plans/2026-09-27-scam-check-tab-impl.md
//
// 骨架＋靜態文案（data-i18n）；結果卡、社群舉報列表由 scam-check.js 的 ScamCheckTab 畫。
// 按鈕走 data-click、Enter 走 data-enter（click-delegator.js；prod CSP 擋 inline handler）。
// 輸入框手機用 16px 字（text-base）：小於 16px 時 iOS Safari 聚焦會自動放大畫面。
window.Components = window.Components || {};

window.Components.scamcheck = `
    <div class="max-w-2xl mx-auto space-y-5">
        <header class="min-w-0">
            <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-primary/10 text-primary border border-primary/20 shrink-0 whitespace-nowrap">
                <i data-lucide="shield-alert" class="w-3 h-3"></i>
                <span data-i18n="nav.scamcheck">Scam Check</span>
            </span>
            <h2 class="mt-3 h2 text-textMain text-balance" data-i18n="scamcheck.title">Check an address before you send</h2>
            <p class="mt-2 text-sm leading-relaxed text-textMuted" data-i18n="scamcheck.subtitle">Screens wallets and token contracts against GoPlus security data and community reports. A clean result is not a guarantee.</p>
        </header>

        <section class="bg-surface p-4 md:p-6 rounded-3xl border border-borderSubtle">
            <label for="scamcheck-input" class="block text-sm font-medium text-textMain" data-i18n="scamcheck.inputLabel">Wallet or token contract address</label>
            <div class="mt-2 flex flex-col sm:flex-row gap-2">
                <input type="text" id="scamcheck-input" data-enter="ScamCheckTab.submitFromInput"
                    autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="search"
                    aria-describedby="scamcheck-input-error"
                    placeholder="Paste an address (0x…)" data-i18n="scamcheck.placeholder" data-i18n-attr="placeholder"
                    class="flex-1 min-w-0 w-full bg-background border border-borderLight rounded-xl px-4 py-3 font-mono text-base sm:text-sm text-textMain placeholder:font-sans placeholder:text-textMuted/70 focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition">
                <button type="button" id="scamcheck-submit" data-click="ScamCheckTab.check"
                    class="shrink-0 inline-flex items-center justify-center gap-2 px-6 py-3 rounded-xl bg-primary text-background text-sm font-semibold hover:brightness-110 disabled:opacity-60 disabled:cursor-wait transition whitespace-nowrap">
                    <i data-lucide="search" class="w-4 h-4"></i>
                    <span id="scamcheck-submit-label" data-i18n="scamcheck.check">Check</span>
                </button>
            </div>
            <p id="scamcheck-input-error" class="hidden mt-2 text-xs text-danger" role="alert"></p>
            <div class="mt-3 flex flex-wrap items-center gap-2">
                <span class="text-xs text-textMuted" data-i18n="scamcheck.tryExample">Try an example:</span>
                <button type="button" data-click="ScamCheckTab.tryExample"
                    class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-surfaceHighlight/60 hover:bg-surfaceHighlight border border-borderSubtle text-xs text-textMuted hover:text-secondary transition whitespace-nowrap">
                    <i data-lucide="sparkles" class="w-3 h-3"></i>
                    <span data-i18n="scamcheck.exampleUsdcBase">USDC on Base</span>
                </button>
            </div>
        </section>

        <section id="scamcheck-result" class="hidden" aria-live="polite"></section>

        <section id="scamcheck-recent" class="hidden bg-surface p-4 md:p-6 rounded-3xl border border-borderSubtle">
            <div class="flex items-center gap-2">
                <i data-lucide="users" class="w-4 h-4 text-primary"></i>
                <h3 class="h3 text-base text-textMain" data-i18n="scamcheck.recentTitle">Recent community reports</h3>
            </div>
            <ul id="scamcheck-recent-list" class="mt-2 divide-y divide-borderSubtle"></ul>
        </section>
    </div>
`;

export {};
