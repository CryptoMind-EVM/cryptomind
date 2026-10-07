/**
 * window.I18n.t('premium.premiumMemberLabel') || 'Premium Member'功能模組
 * 處理升級到 window.I18n.t('premium.premiumMemberLabel') || 'Premium Member'的支付和狀態管理
 */
// 錢包直付的送款、付款人檢查、Telegram 判斷 2026-09-25 抽到 usdc-pay.js（論壇
// 發文費／打賞改 USDC on Base 後共用同一份）。
import {
    fetchBoundEvmAddresses,
    isTelegramMiniApp,
    resolvePaymentPayer,
    shortAddr,
    showOpenInBrowserNotice,
    walletSendUsdc,
} from './usdc-pay.js';
import { resolveSocialLogin } from './social-login.js';

// 穩定幣優先排序（2026-09-02 DANNY：付款以穩定幣為主、EVM 優先）。原地排序
// 並回傳，供 rail 解析與測試使用。2026-09-09 訂閱統一 USDC on Base 後僅剩單一
// rail（TON 兩軌 2026-09-25 移除），保留排序以防未來恢復多軌。
function sortRailsStableFirst(rails) {
    const ORDER = { evm_usdc: 0 };
    return rails.sort((a, b) => (ORDER[a.rail] ?? 9) - (ORDER[b.rail] ?? 9));
}

class PremiumManager {
    constructor() {
        this.premiumPrice = null; // initialized to null, entirely dependent on backend
        this.selectedPlan = 'premium_monthly'; // 'premium_monthly' | 'premium_yearly'
        this._usdPricing = null; // /pricing 的 USD 錨定價（穩定幣為主的顯示來源）
        this._rails = null; // /pricing 的 rail 清單（含可用性與各 rail 金額）
        this._verifySeq = 0; // 付款面板輪詢序號：cancel/開新面板即作廢舊 poller
        this.initEventListeners();
    }

    /**
     * 取得 /pricing 並快取 USD 錨定價與 rail 清單（fail-soft：失敗回 null）。
     */
    async _ensurePricing() {
        if (this._usdPricing) return this._usdPricing;
        // 跟論壇價格（forum-config.js）共用同一個請求；回傳內容與直接 GET 完全相同
        const p =
            typeof AppAPI.getPremiumPricing === 'function'
                ? await AppAPI.getPremiumPricing()
                : await AppAPI.get('/api/premium/pricing');
        this._usdPricing = (p && p.pricing && p.pricing.premium) || null;
        this._rails = (p && p.rails) || null;
        return this._usdPricing;
    }

    /**
     * 已綁定的 EVM 地址（小寫）。查不到回 null＝不擋（後端仍 fail-closed），
     * 但面板會提示「必須從綁定錢包送出」。
     */
    async _fetchBoundEvmAddresses() {
        return fetchBoundEvmAddresses();
    }

    /**
     * 建單前確保有付款錢包可用：沒綁定就先引導綁定（走 evm-auth 的
     * safeEvmBind）。回傳綁定清單；[] 表示沒綁也沒完成綁定 → 呼叫端不建單。
     */
    async _ensurePayerBound() {
        const bound = await this._fetchBoundEvmAddresses();
        if (bound === null || bound.length) return bound;
        const t = (key, fallback) => (window.I18n ? window.I18n.t(key) || fallback : fallback);
        let go = false;
        if (typeof window.showConfirmDialog === 'function') {
            go = await window.showConfirmDialog({
                title: t('premium.bindBeforePayTitle', 'Bind your payment wallet first'),
                message: t(
                    'premium.bindBeforePayMessage',
                    'Subscriptions are only credited when the USDC is sent from a wallet bound to your account. Bind the wallet you will pay from, then continue.'
                ),
                confirmText: t('premium.bindWalletCta', 'Bind wallet'),
                icon: 'wallet',
            });
        } else {
            go = window.confirm(t('premium.bindBeforePayTitle', 'Bind your payment wallet first'));
        }
        if (!go || typeof window.safeEvmBind !== 'function') return [];
        const addr = await window.safeEvmBind();
        return addr ? [String(addr).toLowerCase()] : [];
    }

    /**
     * 取得指定方案的 USD 錨定價（來自後端 /pricing）。
     * 2026-09-02 DANNY：付款以穩定幣為主，方案卡不再顯示 TON 浮動計價
     * （TON 軌 2026-09-25 已移除）。
     */
    _priceForPlan(plan) {
        if (!this._usdPricing) return null;
        return plan === 'premium_yearly'
            ? this._usdPricing.yearly ?? null
            : this._usdPricing.monthly ?? null;
    }

    /**
     * 月／年方案切換（design §8：月 30 天 / 年 365 天使用權）。
     */
    initPlanToggle() {
        // 事件委派（document 層）：SPA settings 分頁每次造訪都可能重渲染卡片
        // HTML，per-button 監聽器會隨舊 DOM 失效；委派一次永久有效，論壇
        // premium 頁（靜態 HTML）也一體適用。
        if (this._planToggleDelegated) return;
        this._planToggleDelegated = true;
        document.addEventListener('click', (e) => {
            const btn =
                e.target && e.target.closest
                    ? e.target.closest('[data-plan-toggle]')
                    : null;
            if (!btn) return;
            this.selectedPlan = btn.dataset.planToggle || 'premium_monthly';
            document.querySelectorAll('[data-plan-toggle]').forEach((b) => {
                const active = b === btn;
                b.setAttribute('aria-pressed', active ? 'true' : 'false');
                b.classList.toggle('ring-2', active);
                b.classList.toggle('ring-primary', active);
                b.classList.toggle('text-primary', active);
                b.classList.toggle('bg-primary/10', active);
                b.classList.toggle('text-textMuted', !active);
            });
            this.updatePriceDisplay();
        });
    }

    /**
     * 發送 log 到後端服務器（TON Connect 流程專用）——2026-09-09 訂閱統一
     * USDC on Base 後隨流程移除；/api/client/log 通道仍在（frontend_errors）。
     */

    /**
     * 初始化事件監聽器
     */
    initEventListeners() {
        const onReady = () => {
            this.initUpgradeButtons();
            this.initPlanToggle();
            // 2026-09-02 DANNY：方案卡以 USD 錨定價為主角（穩定幣支付）。
            this._ensurePricing()
                .then(() => this.updatePriceDisplay())
                .catch(() => this.updatePriceDisplay());
        };
        // premium.js 在 SPA 是 settings 分頁的 lazy 模組（spa.js _TAB_MODULES），
        // 載入時 DOMContentLoaded 多半已觸發過——只掛監聽器會永不初始化，
        // _ensurePricing 不跑 → data-price="premium" 永遠停在 Loading。
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', onReady);
        } else {
            onReady();
        }
    }

    /**
     * 更新價格顯示（依當前選擇的方案 monthly/yearly）。
     * 顯示 USD 錨定價（$12.00）；付款統一 USDC on Base（2026-09-09）。
     */
    updatePriceDisplay() {
        const usd = this._priceForPlan(this.selectedPlan);
        this.premiumPrice = usd;

        let displayHtml;
        if (usd !== null && usd !== undefined) {
            displayHtml = `$${Number(usd).toFixed(2)}`;
        } else {
            displayHtml = `<span class="animate-pulse">${window.I18n?.t('common.loading') || 'Loading...'}</span>`;
        }

        // 更新所有顯示價格的元素
        const priceElements = document.querySelectorAll('[data-price="premium"]');
        priceElements.forEach((element) => {
            element.innerHTML = displayHtml;
        });

        // 方案註記（月付/年付）跟著切換——data-plan-note 元素（SPA Settings 卡）
        const noteKey =
            this.selectedPlan === 'premium_yearly'
                ? 'premium.yearlyNote'
                : 'premium.monthlyNote';
        const noteFallback =
            this.selectedPlan === 'premium_yearly'
                ? 'Yearly plan (365 days). Renew manually before expiry.'
                : 'Monthly plan (30 days). Renew manually before expiry.';
        const noteText = window.I18n
            ? window.I18n.t(noteKey) || noteFallback
            : noteFallback;
        document
            .querySelectorAll('[data-plan-note]')
            .forEach((el) => {
                el.textContent = noteText;
            });
    }

    /**
     * 初始化升級按鈕
     */
    initUpgradeButtons() {
        // 查找所有升級按鈕並添加事件監聽器
        const upgradeButtons = document.querySelectorAll('.upgrade-premium-btn');
        upgradeButtons.forEach((button) => {
            button.addEventListener('click', (e) => {
                e.preventDefault();
                this.handleUpgradeClick();
            });
        });
    }

    /**
     * 處理升級按鈕點擊
     */
    async handleUpgradeClick() {
        try {
            // 1. 必須已登入
            if (!window.AuthManager || !window.AuthManager.currentUser) {
                showToast(window.I18n.t('premium.loginRequired'), 'warning');
                return;
            }

            const plan = this.selectedPlan;

            // 1.5 Telegram「TON 模式」（2026-09-13）：Telegram 規定 Mini App 內只能有 TON 的
            // 加密功能，USDC on Base 的訂單不能在裡面建——只給「用瀏覽器開啟」的入口。
            if (isTelegramMiniApp()) {
                this._showTmaUpgradeNotice();
                return;
            }
            // 1.6 Google Play 版：數位服務只能走 Play Billing（Phase 3 接），而且 Google 也禁止
            // 把使用者導去外部付款——這裡只能說「即將上線」，不能給網頁連結。
            if (window.CMPlatform && window.CMPlatform.isPlay && window.CMPlatform.isPlay()) {
                showToast(window.I18n.t('premium.playComingSoon') || 'Subscriptions in the Play version are coming soon', 'info');
                return;
            }

            // 2. 解析付款 rail——2026-09-09 訂閱統一 USDC on Base，後端
            // /pricing.rails 只回單一 rail；不可用（env 未設）即明確報錯，
            // 不再 fallback 到任何 TON 流程。
            const rail = await this.chooseRail();
            if (!rail) {
                showToast(window.I18n.t('premium.railUnavailable') || 'Payment is temporarily unavailable', 'warning');
                return;
            }

            // 3. 付款人白名單：沒綁定錢包就先引導綁定，不建單（錢先轉出去
            //    才被擋＝錢卡住要人工處理）
            const bound = await this._ensurePayerBound();
            if (Array.isArray(bound) && !bound.length) return;

            // 3.5 付款前確認退款規則（2026-09-28 DANNY）：付款後立即開通、開通後依條款 5.3
            //     不退款。消保法「線上服務經消費者事先同意始提供」要有事先同意——每次建單前問一次，
            //     同意才建單；refund_ack 簽進訂單 token、隨訂單落庫
            if (!(await this._confirmRefundPolicy())) return;

            // 4. USDC on Base：錢包直付為主、手動轉帳為輔 + 鏈上驗證
            await this.startStableRailUpgrade(plan, rail, bound);
        } catch (error) {
            console.error(window.I18n.t('premium.upgradeError') || '[Premium] 升級錯誤:', error);
            showToast(window.I18n.t('premium.upgradeErrorMsg', { msg: error.message }), 'error');
        }
    }

    /**
     * 付款前的退款規則確認；按「我同意，繼續付款」才回 true。
     */
    async _confirmRefundPolicy() {
        const t = (key, fallback) => (window.I18n ? window.I18n.t(key) : '') || fallback;
        const message = t(
            'premium.refundConsentMessage',
            'Premium starts as soon as your payment is confirmed. Under section 5.3 of the Terms of Service it is non-refundable once activated, except for a duplicate charge caused by a system error or if the service is unavailable for 7 or more consecutive days. Questions: brief@getcryptomind.com'
        );
        if (typeof window.showConfirmDialog === 'function') {
            return !!(await window.showConfirmDialog({
                title: t('premium.refundConsentTitle', 'Before you pay'),
                message,
                confirmText: t('premium.refundConsentAgree', 'I agree, continue'),
                icon: 'receipt',
            }));
        }
        return window.confirm(message);
    }

    /**
     * Telegram 內的升級提示：不建單、不顯示任何 USDC／Base 資訊，只導去外部瀏覽器。
     */
    _showTmaUpgradeNotice() {
        const t = (key, fallback) => (window.I18n ? window.I18n.t(key) : '') || fallback;
        showOpenInBrowserNotice({
            id: 'premium-tma-notice',
            title: t('premium.tmaUpgradeTitle', 'Upgrade in your browser'),
            hint: t('premium.tmaUpgradeHint', 'Subscriptions are handled on the CryptoMind website. Open it in your browser to upgrade; your account stays the same.'),
            url: window.location.origin + '/#settings',
        });
    }

    /**
     * multichain Part C：付款 rail 解析（僅列後端回報可用的 rail）
     */
    async chooseRail() {
        try {
            await this._ensurePricing();
        } catch (e) {
            console.warn('[premium] pricing fetch failed', e);
        }
        const available = sortRailsStableFirst((this._rails || []).filter((r) => r.available));
        if (!available.length) {
            return null;
        }
        return available[0].rail;
    }

    /**
     * multichain Part C：穩定幣 rail（USDC）手動轉帳 + 鏈上驗證。
     * 金額含唯一尾數（後端用鏈上掃描對應訂單），交易所轉帳（無備註）也可對帳。
     */
    async startStableRailUpgrade(plan, rail, bound) {
        // 走到這裡代表已在 _confirmRefundPolicy 按了同意
        const order = await AppAPI.post('/api/premium/payment-order', { plan, rail, refund_ack: true });
        if (!order || !order.success) {
            throw new Error(window.I18n.t('premium.cannotCreateOrder') || 'Cannot create payment order');
        }
        this.showStablePaymentPanel(order, plan, bound);
    }

    showStablePaymentPanel(order, plan, bound) {
        // 同時最多一張付款面板：重複點擊升級鈕會疊出多張 overlay（每張各帶
        // 一張訂單、各自輪詢——金額尾數不同，付了也對不上帳，還加倍打限流）。
        // 開新面板前先移除舊的＋作廢舊面板的輪詢（_verifySeq）。
        // 2026-09-10 review：原 cancel 只移除 DOM，輪詢照打——cancel 後再開
        // 新面板＝兩個 poller 並發（≈17/min）照樣撞 /upgrade 的 10/min 限流。
        const stale = document.getElementById('stable-pay-overlay');
        if (stale) stale.remove();
        const mySeq = ++this._verifySeq;
        const t = (key, fallback) => (window.I18n ? window.I18n.t(key) || fallback : fallback);
        const overlay = document.createElement('div');
        overlay.id = 'stable-pay-overlay';
        overlay.className = 'fixed inset-0 bg-background/95 backdrop-blur-xl z-[95] overflow-y-auto flex py-10 px-6';
        const copy = async (text) => {
            try {
                await navigator.clipboard.writeText(text);
                if (typeof showToast === 'function') showToast(t('premium.copied', 'Copied'), 'success');
            } catch (e) {
                /* clipboard denied — ignore */
            }
        };
        const row = (label, value, copyable) => `
            <div class="mb-4">
                <div class="text-textMuted text-xs mb-1">${label}</div>
                <div class="flex flex-wrap items-center gap-2 bg-background rounded-xl px-4 py-3 border border-borderLight">
                    <span class="text-secondary text-sm break-all flex-1 min-w-0 select-all">${value}</span>
                    ${copyable ? `<button class="stable-copy text-primary text-xs font-bold shrink-0 px-2 py-1 rounded-lg bg-primary/10 hover:bg-primary/20 transition">${t('common.copy', 'Copy')}</button>` : ''}
                </div>
            </div>`;

        // 共用：送出 claim（輪詢）＋成功／失敗回饋。錢包直付帶 txHash
        // （後端走 receipt 驗證），手動轉帳不帶（getLogs 掃描）。
        const runClaim = async (btn, txHash) => {
            const label = (attempt, total) => {
                const raw = t('premium.verifyingAttempt', 'Verifying ({{current}}/{{total}})…');
                btn.textContent = raw
                    .replace('{{current}}', attempt)
                    .replace('{{total}}', total);
            };
            btn.disabled = true;
            label(1, 15);
            try {
                const isAlive = () => mySeq === this._verifySeq;
                const res = await this.requestTonUpgrade(order, plan, label, isAlive, txHash);
                overlay.remove();
                if (res && res.success) {
                    showToast(res.message || t('premium.upgradeSuccess', 'Upgrade successful'), 'success');
                    const uid = window.AuthManager && window.AuthManager.currentUser && window.AuthManager.currentUser.user_id;
                    if (uid && typeof this.checkMembershipStatus === 'function') {
                        await this.checkMembershipStatus(uid);
                    }
                    // Settings 的會員徽章讀 currentUser 快取——不主動同步的話要等
                    // 下一次 pageshow 才更新（2026-09-11 盤查）
                    try {
                        if (window.AuthManager && typeof window.AuthManager.restoreSessionFromBackend === 'function') {
                            await window.AuthManager.restoreSessionFromBackend();
                        }
                        if (typeof window.loadPremiumStatus === 'function') window.loadPremiumStatus();
                    } catch (e) {
                        console.warn('[premium] post-payment status sync failed', e);
                    }
                    // 論壇 premium 頁：付款成功後刷新狀態卡／CTA／到期日
                    // （原本只 toast，頁面狀態停留「Free Member」直到手動重整）
                    if (typeof window.refreshForumPremiumStatus === 'function') {
                        window.refreshForumPremiumStatus();
                    }
                }
            } catch (e) {
                if (e && e.cancelled) return; // 面板已取消/被新面板取代——靜默結束
                btn.disabled = false;
                btn.textContent = t('premium.stablePayDone', 'I have paid — verify now');
                showToast(e.message || t('premium.verifyFailed', 'Verification failed'), 'error');
            }
        };

        // 手動轉帳區塊（交易所提幣用；無注入錢包時為唯一路徑）
        const manualBlock = `
            ${row(t('premium.stablePayTitle', 'Amount'), `${order.amount} ${order.asset}`, true)}
            ${row(t('premium.stablePayNetwork', 'Network'), 'Base', false)}
            ${row(t('premium.stablePayAddress', 'Receiving address'), order.receiving_address, true)}
            ${order.token_contract
                ? row(t('premium.stablePayTokenContract', 'Token contract (native USDC)'), order.token_contract, true)
                : ''}
            ${row(
                t('premium.payerAddressLabel', 'Send from (your bound wallet)'),
                bound && bound.length ? bound.map(shortAddr).join(' / ') : t('premium.payerUnknown', 'a wallet bound to your account'),
                false
            )}
            <p class="text-textMuted/70 text-xs mb-5">${t('premium.stableEvmHint', 'Native USDC on Base only (not bridged USDC.e), sent from a wallet bound to your account. Exchange withdrawals come from the exchange\'s own address and cannot be matched to you.')}</p>
            <div class="mb-4">
                <div class="text-textMuted text-xs mb-1">${t('premium.txHashLabel', 'Transaction hash (optional — paste it to verify instantly, or if you paid more than a few hours ago)')}</div>
                <input id="stable-tx-hash" type="text" autocomplete="off" spellcheck="false" placeholder="0x…"
                    class="w-full bg-background border border-borderLight rounded-xl px-4 py-3 text-sm font-mono text-secondary placeholder:text-textMuted/50 focus:border-primary/50 focus:outline-none">
            </div>
            <button id="stable-paid-btn"
                class="w-full px-4 py-4 bg-primary hover:bg-primary/90 text-background font-bold rounded-2xl transition-all duration-200 hover:scale-[1.01]">
                ${t('premium.stablePayDone', 'I have paid — verify now')}
            </button>`;

        // 錢包直付（docs/plans/premium-wallet-direct-pay.md，DANNY 2026-09-11
        // 拍板「錢包直付為主＋手動爲輔」）：有注入錢包（SPA／錢包內建瀏覽器）
        // 時為主路徑——錢包彈預填交易、官方檢查餘額與網路；手動轉帳收進
        // <details>。
        const hasInjected = !!(window.ethereum && typeof window.ethereum.request === 'function');
        // Telegram Mini App 內沒有注入錢包、WC deep link 是已知死路：明講並給
        // 「用外部瀏覽器開啟」的出口，別讓使用者對著手動轉帳資訊發呆
        const tmaHint = !hasInjected && isTelegramMiniApp()
            ? `<div class="mb-5 px-4 py-3 bg-surfaceHighlight rounded-xl text-xs text-textMuted leading-relaxed">
                    ${t('premium.tmaPayHint', 'Wallets cannot connect inside Telegram. Open CryptoMind in your phone browser or in your wallet app\'s browser to pay with one tap.')}
                    <button id="stable-open-external" class="mt-2 block text-primary font-bold hover:underline">${t('premium.openInBrowser', 'Open in browser')}</button>
               </div>`
            : '';

        // PR-6：刷卡買 USDC（AppKit onramp，供應商 Meld）＋Email／Google 內嵌錢包的
        // 「開啟我的錢包」（Send／Receive）。預設藏著，旗標開＋一般網頁才顯示——
        // 判定與登入視窗、AppKit 設定共用 resolveSocialLogin。
        const cardBuyBlock = `
            <div id="onramp-block" class="hidden mb-2 pt-4 border-t border-borderLight">
                <button id="onramp-buy-btn"
                    class="w-full px-4 py-3 min-h-11 bg-surfaceHighlight hover:bg-primary/10 text-secondary font-bold rounded-2xl transition">
                    ${t('premium.onrampCta', 'Buy USDC with card')}
                </button>
                <button id="embedded-wallet-btn" class="hidden w-full mt-2 py-2 min-h-11 text-primary text-sm font-bold hover:underline transition">
                    ${t('premium.openWalletCta', 'Open my wallet (send / receive)')}
                </button>
                <p class="text-textMuted/70 text-xs mt-2">${t('premium.onrampHint', 'Card payments are processed by Meld, a third party. Choose USDC on the Base network — it goes straight to the wallet you signed in with. Paying from that wallet also needs a little ETH on Base for the network fee.')}</p>
            </div>`;

        overlay.innerHTML = `
            <div class="bg-surface w-full max-w-md p-8 rounded-[2rem] border border-borderLight shadow-2xl m-auto">
                <h3 class="font-serif text-xl text-secondary mb-1 text-center">${t('premium.stablePayTitle', 'Send exactly this amount')}</h3>
                <p class="text-center text-textMuted text-xs mb-6">${order.asset} · ${order.network || ''}</p>
                ${tmaHint}
                ${hasInjected ? `
                <button id="wallet-pay-btn"
                    class="w-full px-4 py-4 bg-primary hover:bg-primary/90 text-background font-bold rounded-2xl transition-all duration-200 hover:scale-[1.01] mb-3">
                    ${t('premium.walletPayCta', 'Pay with wallet (one click)')}
                </button>
                <p class="text-textMuted/70 text-xs mb-5">${t('premium.walletPayHint', 'Your wallet will pop up with a pre-filled USDC transfer — balance and network are checked by the wallet itself.')}</p>
                <details class="mb-4">
                    <summary class="text-textMuted text-xs cursor-pointer select-none">${t('premium.manualTabTitle', 'Manual transfer from your bound wallet')}</summary>
                    <div class="mt-4">${manualBlock}</div>
                </details>` : manualBlock}
                ${cardBuyBlock}
                <button id="stable-cancel-btn" class="w-full mt-2 py-2 text-textMuted text-sm font-bold hover:text-secondary transition">
                    ${t('common.cancel', 'Cancel')}
                </button>
            </div>`;
        document.body.appendChild(overlay);
        overlay.querySelectorAll('.stable-copy').forEach((btn) => {
            btn.addEventListener('click', () => {
                const value = btn.parentElement.querySelector('span').textContent;
                copy(value);
            });
        });
        const openExternalBtn = overlay.querySelector('#stable-open-external');
        if (openExternalBtn) {
            openExternalBtn.addEventListener('click', () => {
                const url = window.location.href;
                if (typeof window.openExternalLink === 'function') window.openExternalLink(url);
                else window.open(url, '_blank');
            });
        }
        overlay.querySelector('#stable-cancel-btn').addEventListener('click', () => {
            // 取消＝中止輪詢＋關面板（作廢序號讓進行中的 poller 停在下一拍）
            this._verifySeq++;
            overlay.remove();
        });

        overlay.querySelector('#onramp-buy-btn').addEventListener('click', () => this._buyUsdcWithCard(bound));
        overlay.querySelector('#embedded-wallet-btn').addEventListener('click', () => this._openEmbeddedWallet());
        resolveSocialLogin()
            .then(async (on) => {
                if (!on || !overlay.isConnected) return;
                overlay.querySelector('#onramp-block').classList.remove('hidden');
                const wc = await import('./evm-walletconnect.js');
                if (await wc.hasEmbeddedWallet()) {
                    overlay.querySelector('#embedded-wallet-btn').classList.remove('hidden');
                }
            })
            .catch((e) => console.warn('[premium] onramp availability check failed', e));

        if (hasInjected) {
            overlay.querySelector('#wallet-pay-btn').addEventListener('click', async () => {
                const btn = overlay.querySelector('#wallet-pay-btn');
                const restore = t('premium.walletPayCta', 'Pay with wallet (one click)');
                btn.disabled = true;
                btn.textContent = t('premium.walletPaySending', 'Check your wallet…');
                try {
                    const txHash = await this._walletSendUsdc(order, bound);
                    // 錢包已送出——以 receipt 驗證（含確認數等待，可重試）
                    await runClaim(btn, txHash);
                } catch (e) {
                    if (e && e.cancelled) return;
                    btn.disabled = false;
                    btn.textContent = restore;
                    const msg = (e && e.message) || '';
                    if (/reject|denied|cancel/i.test(msg)) {
                        showToast(t('premium.walletRejected', 'Payment was cancelled in the wallet'), 'info');
                    } else {
                        showToast(msg || t('premium.verifyFailed', 'Verification failed'), 'error');
                    }
                }
            });
        }

        const paidBtn = overlay.querySelector('#stable-paid-btn');
        if (paidBtn) {
            paidBtn.addEventListener('click', () => {
                // 貼了 tx hash 就走 receipt 驗證（不受鏈上掃描窗 ~6.7 小時限制，
                // 訂單有效 7 天）；沒貼維持 getLogs 掃描
                const txEl = overlay.querySelector('#stable-tx-hash');
                const raw = ((txEl && txEl.value) || '').trim();
                if (raw && !/^0x[0-9a-fA-F]{64}$/.test(raw)) {
                    showToast(t('premium.txHashInvalid', 'That does not look like a transaction hash (0x + 64 hex characters)'), 'warning');
                    return;
                }
                runClaim(paidBtn, raw || null);
            });
        }
    }

    /**
     * 錢包直付：組 USDC transfer(address,amount) calldata 並以
     * eth_sendTransaction 送出（收款地址與含唯一尾數的精確金額來自訂單）。
     * 送出前強制 chainId 檢查／切鏈——錯鏈直付＝資產損失，不可省。
     */
    async _walletSendUsdc(order, bound) {
        // 送款本體（付款帳號檢查 → 切鏈 → transfer calldata）在 usdc-pay.js，論壇共用
        return walletSendUsdc(order, bound);
    }

    /**
     * PR-6：刷卡買 USDC（AppKit onramp）。USDC 會進 AppKit 目前連著的地址——必須是綁定的
     * 付款錢包才開；綁定清單查不到就不開（跟 walletSendUsdc 的 null 放行不同）。
     * AppKit 的 Meld 網址只帶「USDC」不帶網路，Base 要使用者在 Meld 畫面選——提示文案有講。
     */
    async _buyUsdcWithCard(boundAtOpen) {
        const t = (key, fallback) => (window.I18n ? window.I18n.t(key) || fallback : fallback);
        try {
            // 刷卡是真的花錢：綁定清單一定要確定（開面板時查不到＝null 就再查一次），
            // 查不到或沒綁就不開——跟轉帳不同，這裡沒有後端 fail-closed 可以兜
            const bound = Array.isArray(boundAtOpen) && boundAtOpen.length ? boundAtOpen : await fetchBoundEvmAddresses();
            if (!Array.isArray(bound) || !bound.length) {
                showToast(t('premium.payerMustBeBound', 'Bind the wallet you pay from first (Settings → Wallet)'), 'warning');
                return;
            }
            const wc = await import('./evm-walletconnect.js');
            const res = await wc.openOnramp((address) => resolvePaymentPayer(bound, address).ok);
            if (res.ok) return;
            if (res.reason === 'not_payer') {
                showToast(
                    t('premium.payerMismatch', 'Wallet account {{from}} is not bound to your account — switch to {{bound}} in your wallet, or bind this account first')
                        .replace('{{from}}', shortAddr(res.address))
                        .replace('{{bound}}', (bound || []).map(shortAddr).join(' / ')),
                    'warning'
                );
            } else if (res.reason === 'not_connected') {
                showToast(t('premium.onrampNotConnected', 'Connect your wallet first (sign in again with email, Google, or your wallet), then try again'), 'info');
            } else {
                showToast(t('premium.onrampUnavailable', 'Card purchase is not available here'), 'info');
            }
        } catch (e) {
            console.warn('[premium] onramp failed', e);
            showToast(t('premium.onrampUnavailable', 'Card purchase is not available here'), 'error');
        }
    }

    /** Email／Google 內嵌錢包的帳戶畫面（Send／Receive）——這類使用者沒有別的錢包 App 可開。 */
    async _openEmbeddedWallet() {
        const t = (key, fallback) => (window.I18n ? window.I18n.t(key) || fallback : fallback);
        try {
            const wc = await import('./evm-walletconnect.js');
            const res = await wc.openEmbeddedWallet();
            if (!res.ok) showToast(t('premium.onrampNotConnected', 'Connect your wallet first (sign in again with email, Google, or your wallet), then try again'), 'info');
        } catch (e) {
            console.warn('[premium] open embedded wallet failed', e);
            showToast(t('premium.onrampUnavailable', 'Card purchase is not available here'), 'error');
        }
    }

    /**
     * 顯示升級確認對話框（TON Connect 流程專用）——2026-09-09 訂閱統一
     * USDC on Base 後隨流程一併移除。
     */

    /**
     * 輪詢後端驗證鏈上交易並升級（交易上鏈需時間）。
     *
     * 2026-09-09 訂閱統一 USDC on Base：TON Connect 彈窗簽署流程已移除
     * （startTonUpgrade／executeTonPayment／showUpgradeConfirmation）；
     * 本輪詢器由「我已付款」面板共用，保留原名。
     *
     * 2026-09-10 節奏修正：原本 3 秒×12 次（12 次/40 秒）會自己撞上
     * /upgrade 的 10/min 限流（429 之後每次都失敗，最後吐出限流錯誤），
     * 且期間按鈕只有「Verifying…」無任何進度——改成 7 秒×15 次
     * （≈8.6/min，全程在限流內），每次輪詢回報進度，涵蓋錢包送款
     * 所需的 1-2 分鐘；逾時後按鈕復位可再按一次續查。
     */
    async requestTonUpgrade(order, plan, onProgress, isAlive, txHash) {
        let lastErr = null;
        const TOTAL = 15;
        for (let i = 0; i < TOTAL; i++) {
            if (onProgress) onProgress(i + 1, TOTAL);
            // 面板被取消或被新面板取代 → 靜默中止（cancelled 哨兵不進 toast）
            if (isAlive && !isAlive()) {
                const err = new Error('verification cancelled');
                err.cancelled = true;
                throw err;
            }
            try {
                const res = await AppAPI.post('/api/premium/upgrade', {
                    plan,
                    months: plan === 'premium_yearly' ? 12 : 1,
                    order_token: order.order_token,
                    comment: order.comment,
                    // 錢包直付：帶 tx_hash 走 receipt 驗證；手動轉帳不帶
                    tx_hash: txHash || undefined,
                });
                if (res && res.success) return res;
                lastErr = res;
            } catch (e) {
                // 429＝自家輪詢撞限流（理論上改 7 秒間隔後不應出現）：
                // 拉長等待後繼續，不把它記成失敗原因。
                if (e && e.status === 429) {
                    await new Promise((r) => setTimeout(r, 9000));
                    continue;
                }
                lastErr = e;
                const m = e.message || '';
                // 確定性錯誤（金額不符／已使用／過期／未綁錢包／時間窗）→ 立即停止。
                // 「bound/bind your」（未綁錢包）與「predates」（訂單過期）是後端
                // 實際文案——原正則只比對 belong 比不中，會被靜默重試到逾時。
                if (
                    /amount|already been used|expired|mismatch|belong|bound|bind your|predates/i.test(
                        m
                    )
                ) {
                    // 請求返回時面板可能已被取消/取代——錯誤只屬於舊訂單，
                    // 不得彈到新面板上（第三輪 review：in-flight 出口漏守）
                    if (isAlive && !isAlive()) {
                        const cancelled = new Error('verification cancelled');
                        cancelled.cancelled = true;
                        throw cancelled;
                    }
                    throw e;
                }
                // 其餘（尚未偵測到交易）→ 繼續重試
            }
            await new Promise((r) => setTimeout(r, 7000));
        }
        // 迴圈結束的逾時拋出前同樣檢查存活——最後一輪的 sleep 期間取消的話，
        // 逾時 toast 會彈在已關閉/新開的面板上（第三輪 review）
        if (isAlive && !isAlive()) {
            const cancelled = new Error('verification cancelled');
            cancelled.cancelled = true;
            throw cancelled;
        }
        throw new Error(
            (lastErr && (lastErr.message || lastErr.detail)) ||
                window.I18n.t('premium.paymentVerifyTimeout') || 'Payment verification timed out. If deducted, please refresh later to confirm membership.'
        );
    }

    /**
     * 更新用戶界面以反映新的會員狀態（TON Connect 流程專用）——2026-09-09
     * 訂閱統一後改由 checkMembershipStatus＋重新整理涵蓋，本方法移除。
     */

    /**
     * 檢查用戶當前的會員狀態
     */
    async checkMembershipStatus(userId) {
        try {
            const result = await AppAPI.get('/api/premium/status');

            if (result.success) {
                return result.membership;
            }

            return { tier: 'free', is_premium: false, expires_at: null };
        } catch (error) {
            console.error(window.I18n.t('premium.getMembershipStatusFailed') || '[Premium] 獲取會員狀態失敗:', error);
            return { tier: 'free', is_premium: false, expires_at: null };
        }
    }
}

// 初始化 PremiumManager
const premiumManager = new PremiumManager();
window.PremiumManager = premiumManager;

// 暴露全局函數
const upgradeToPremium = () => premiumManager.handleUpgradeClick();
const checkMembershipStatus = (userId) => premiumManager.checkMembershipStatus(userId);
window.upgradeToPremium = upgradeToPremium;
window.checkMembershipStatus = checkMembershipStatus;

export { PremiumManager, upgradeToPremium, checkMembershipStatus, sortRailsStableFirst, resolvePaymentPayer };

console.log('[Premium] module loaded');
