// Tab: Sample portfolio（訪客專用示範頁）— 2026-09-27 上市準備 PR-3
// design：docs/plans/2026-09-27-launch-readiness-design.md §4 PR-3
//
// 骨架＋靜態文案（data-i18n）；持倉、行事曆、判斷、早報內容由 sample-portfolio.js 的
// SampleTab 畫。每張卡與頁首都有「Make it yours — sign in」（data-click，CSP 擋 inline handler）。
window.Components = window.Components || {};

const SAMPLE_CARD = 'bg-surface p-5 md:p-6 rounded-3xl border border-borderSubtle flex flex-col min-w-0';
const SAMPLE_CARD_CTA = `
    <div class="mt-auto pt-4">
        <button type="button" data-click="openLoginModal"
            class="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-full bg-primary/10 text-primary border border-primary/20 hover:bg-primary/15 text-xs font-semibold transition whitespace-nowrap">
            <span data-i18n="sample.ctaMakeItYours">Make it yours — sign in</span>
            <i data-lucide="arrow-right" class="w-3.5 h-3.5"></i>
        </button>
    </div>`;

window.Components.sample = `
    <div class="max-w-4xl mx-auto space-y-5">
        <div class="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div class="min-w-0">
                <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-500/20 shrink-0 whitespace-nowrap">
                    <i data-lucide="flask-conical" class="w-3 h-3"></i>
                    <span data-i18n="sample.badge">Sample data</span>
                </span>
                <h2 class="mt-3 font-serif text-3xl text-secondary" data-i18n="sample.title">Sample portfolio</h2>
                <p class="mt-1 text-sm text-textMuted max-w-xl" data-i18n="sample.subtitle">What CryptoMind looks like once it knows your portfolio. Every number on this page is made up.</p>
            </div>
            <button type="button" data-click="openLoginModal"
                class="self-start md:self-auto shrink-0 inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-full bg-primary text-background text-sm font-semibold hover:brightness-110 shadow-[0_8px_24px_rgba(37,99,235,.25)] transition whitespace-nowrap">
                <span data-i18n="sample.ctaMakeItYours">Make it yours — sign in</span>
                <i data-lucide="arrow-right" class="w-4 h-4"></i>
            </button>
        </div>

        <div class="grid grid-cols-1 xl:grid-cols-5 gap-5">
            <section class="${SAMPLE_CARD} xl:col-span-3">
                <div class="flex items-center gap-2">
                    <i data-lucide="pie-chart" class="w-4 h-4 text-primary"></i>
                    <h3 class="h3 text-base text-textMain" data-i18n="sample.holdingsTitle">Holdings &amp; P/L</h3>
                </div>
                <div id="sample-summary" class="mt-4 grid grid-cols-2 gap-2.5"></div>
                <ul id="sample-holdings" class="mt-2"></ul>
                <p class="mt-2 text-[11px] text-textMuted" data-i18n="sample.pricesIllustrative">Prices are illustrative, not live quotes.</p>
                ${SAMPLE_CARD_CTA}
            </section>

            <section class="${SAMPLE_CARD} xl:col-span-2">
                <div class="flex items-center gap-2">
                    <i data-lucide="calendar-days" class="w-4 h-4 text-primary"></i>
                    <h3 class="h3 text-base text-textMain" data-i18n="sample.calendarTitle">Next 14 days</h3>
                </div>
                <p class="mt-1 text-xs text-textMuted" data-i18n="sample.calendarSubtitle">Events that touch these holdings</p>
                <ul id="sample-calendar" class="mt-2 divide-y divide-borderSubtle"></ul>
                ${SAMPLE_CARD_CTA}
            </section>

            <section class="${SAMPLE_CARD} xl:col-span-2">
                <div class="flex items-center gap-2">
                    <i data-lucide="target" class="w-4 h-4 text-primary"></i>
                    <h3 class="h3 text-base text-textMain" data-i18n="sample.scorecardTitle">Call scorecard</h3>
                </div>
                <p class="mt-1 text-xs text-textMuted" data-i18n="sample.scorecardSubtitle">Each call is scored against the real close when it expires.</p>
                <div id="sample-score-stats" class="mt-4 grid grid-cols-3 gap-2"></div>
                <ul id="sample-calls" class="mt-3 space-y-2"></ul>
                ${SAMPLE_CARD_CTA}
            </section>

            <section class="${SAMPLE_CARD} xl:col-span-3">
                <div class="flex items-start justify-between gap-3">
                    <div class="flex items-center gap-2">
                        <i data-lucide="sunrise" class="w-4 h-4 text-primary"></i>
                        <h3 class="h3 text-base text-textMain" data-i18n="sample.briefTitle">Morning brief preview</h3>
                    </div>
                    <span id="sample-brief-date" class="text-[11px] text-textMuted text-right"></span>
                </div>
                <div class="mt-4 rounded-2xl bg-surfaceHighlight/60 p-4">
                    <p class="text-sm font-semibold text-textMain" data-i18n="sample.briefHeading">Good morning — here's what matters to your portfolio today.</p>
                    <!-- 「明天」「3 天後」跟行事曆同一組日期算，由 SampleTab 畫 -->
                    <ul id="sample-brief-items" class="mt-3 space-y-2.5"></ul>
                </div>
                <p class="mt-3 text-[11px] text-textMuted" data-i18n="sample.briefDelivery">Arrives at 8:00 every morning on Telegram or in the app.</p>
                ${SAMPLE_CARD_CTA}
            </section>
        </div>

        <div class="pt-1 space-y-1 text-center">
            <p class="text-[11px] text-textMuted" data-i18n="sample.footnote">Everything on this page is sample data for illustration.</p>
            <p class="text-[11px] text-textMuted" data-i18n="chat.disclaimer">AI output is data analysis, not investment advice. Investment decisions are yours.</p>
        </div>
    </div>
`;

export {};
