// ========================================
// wallet.js - Wallet & Transaction History
// ========================================

// 2026-09-12 DANNY 截圖：EVM 使用者的付款紀錄全部印成「-1 TON」，admin 授予
// 也被當成一筆 TON 付款。後端現在每筆帶 asset／display_amount／is_admin_grant，
// 這裡照資產別印，授予不印金額。純函式抽出來給 node 測試。
const TX_ASSET_DECIMALS = { TON: 2, USDC: 2, USDT: 2 };

function formatTxAmount(tx) {
    if (!tx || tx.type === 'admin_grant' || !tx.asset) return '—';
    const decimals = TX_ASSET_DECIMALS[tx.asset] ?? 2;
    const value = Number(tx.amount) || 0;
    return `${value > 0 ? '+' : ''}${Math.abs(value).toFixed(decimals)} ${tx.asset}`;
}

// 付款／打賞列的資產別：後端有帶 asset 就用；沒帶（舊回應）看 tx hash 形狀——
// 0x＋64 hex＝USDC on Base（論壇 2026-09-25 起），其餘＝舊的 TON 紀錄
function paymentAsset(row) {
    if (row && row.asset) return row.asset;
    return /^0x[0-9a-fA-F]{64}$/.test(String((row && row.tx_hash) || '')) ? 'USDC' : 'TON';
}

function summarizeTotals(list) {
    const out = {};
    const inn = {};
    for (const tx of list || []) {
        if (!tx || tx.type === 'admin_grant' || !tx.asset) continue;
        const value = Number(tx.amount) || 0;
        if (value < 0) out[tx.asset] = (out[tx.asset] || 0) + Math.abs(value);
        else if (value > 0) inn[tx.asset] = (inn[tx.asset] || 0) + value;
    }
    const fmt = (m) =>
        Object.keys(m).length
            ? Object.entries(m)
                  .map(([asset, v]) => `${v.toFixed(TX_ASSET_DECIMALS[asset] ?? 2)} ${asset}`)
                  .join(' · ')
            : '0';
    return { out: fmt(out), in: fmt(inn) };
}

// ── 綁定錢包與鏈上餘額（2026-09-12 EVM 優先）：純函式抽出給 node 測試 ──
function formatUsd(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
    const n = Number(value);
    return `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatAssetAmount(amount) {
    const n = Number(amount) || 0;
    if (n === 0) return '0';
    if (n >= 1000) return n.toLocaleString('en-US', { maximumFractionDigits: 2 });
    if (n >= 1) return n.toLocaleString('en-US', { maximumFractionDigits: 4 });
    return n.toLocaleString('en-US', { maximumFractionDigits: 6 });
}

const CHAIN_LABELS = { base: 'Base', ethereum: 'Ethereum', arbitrum: 'Arbitrum', optimism: 'Optimism', polygon: 'Polygon', ton: 'TON', evm: 'EVM' };

function chainLabel(network) {
    const key = String(network || '').toLowerCase();
    return CHAIN_LABELS[key] || (key ? key.toUpperCase() : '');
}

// EVM 在前（主要錢包先），TON 在後——後端已排，這裡再保一次給舊回應
function sortWallets(wallets) {
    const rank = (w) => (w.chain === 'ton' ? 1 : 0);
    return [...(wallets || [])].sort((a, b) => rank(a) - rank(b) || (b.is_primary ? 1 : 0) - (a.is_primary ? 1 : 0));
}

function summarizeSyncResult(result, t) {
    if (!result) return '';
    const parts = [t('wallet.sync.added', 'Added {n} entries').replace('{n}', String(result.added ?? 0))];
    if (result.duplicates) parts.push(t('wallet.sync.duplicates', '{n} already recorded').replace('{n}', String(result.duplicates)));
    if (Array.isArray(result.skipped_unpriced) && result.skipped_unpriced.length) {
        parts.push(t('wallet.sync.unpriced', 'skipped (no price): {list}').replace('{list}', result.skipped_unpriced.slice(0, 5).join(', ')));
    }
    if (result.errors) parts.push(t('wallet.sync.errors', '{n} wallets failed').replace('{n}', String(result.errors)));
    if ((result.notes || []).some((n) => String(n).startsWith('rpc_logs_only'))) {
        parts.push(t('wallet.sync.rpcOnly', 'ERC-20 only on this network (native ETH transfers need an Etherscan key)'));
    }
    return parts.join(' · ');
}

const WalletApp = {
    initialized: false,

    async init() {
        this.log('Init called');

        // Ensure AuthManager is ready
        if (typeof AuthManager === 'undefined') {
            this.log('AuthManager undefined');
            this.renderError('System Error: Auth module missing');
            return;
        }

        if (!AuthManager.isLoggedIn()) {
            this.log('User not logged in');
            this.renderEmptyState(window.I18n ? window.I18n.t('wallet.loginToViewHistory') : 'Please login to view wallet history');
            return;
        }

        this.initialized = true;
        await Promise.all([this.loadData(), this.loadWallets(), this.loadSyncStatus()]);
    },

    _t(key, fallback) {
        return (window.I18n ? window.I18n.t(key) : '') || fallback;
    },

    _esc(str) {
        return String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    },

    // ── 我的錢包（綁定錢包＋鏈上餘額） ──────────────────────────
    async loadWallets() {
        const list = document.getElementById('wallet-bound-list');
        if (!list || typeof AppAPI === 'undefined') return;
        try {
            const data = await AppAPI.get('/api/wallet/holdings');
            this.holdings = data;
            this.renderWallets(data);
        } catch (e) {
            console.warn('[WalletApp] holdings failed:', e);
            list.innerHTML = `<div class="text-center text-textMuted py-6 text-sm">${this._esc(this._t('wallet.holdingsFailed', 'Could not load on-chain balances'))}</div>`;
        }
    },

    renderWallets(data) {
        const list = document.getElementById('wallet-bound-list');
        const total = document.getElementById('wallet-holdings-total');
        if (!list) return;
        const wallets = sortWallets((data && data.wallets) || []);
        if (total) total.textContent = wallets.length ? formatUsd(data.usd_total) : '—';
        if (!wallets.length) {
            list.innerHTML = `
                <div class="text-center py-6 space-y-3">
                    <p class="text-sm text-textMuted">${this._esc(this._t('wallet.noWallets', 'No wallet bound yet. Bind an EVM wallet to see verified holdings and sync transfers to your ledger.'))}</p>
                    <button data-click="GlobalNav.navigateToTab" data-click-arg="settings" data-tma-hide class="px-4 py-3 min-h-11 bg-primary hover:bg-primary/90 text-background font-bold rounded-xl text-sm transition">
                        ${this._esc(this._t('wallet.bindWallet', 'Bind a wallet in Settings'))}
                    </button>
                </div>`;
            if (window.AppUtils) window.AppUtils.refreshIcons();
            return;
        }
        const primaryLabel = this._esc(this._t('wallet.primary', 'primary'));
        const copyLabel = this._esc(this._t('wallet.copyAddress', 'Copy address'));
        const unavailable = this._esc(this._t('wallet.balanceUnavailable', 'balances unavailable'));
        const noAssets = this._esc(this._t('wallet.noAssets', 'no assets found'));
        list.innerHTML = wallets.map((w) => {
            const assets = (w.assets || []).map((a) => `
                <div class="flex items-center justify-between gap-2 text-sm">
                    <span class="font-medium text-textMain truncate">${this._esc(a.symbol)}${a.verified === false ? ` <span class="text-[10px] text-textMuted">(${this._esc(this._t('wallet.unverified', 'unverified'))})</span>` : ''}</span>
                    <span class="shrink-0 text-right">
                        <span class="text-textMain">${this._esc(formatAssetAmount(a.amount))}</span>
                        <span class="text-xs text-textMuted ml-2">${this._esc(a.usd === null || a.usd === undefined ? '—' : formatUsd(a.usd))}</span>
                    </span>
                </div>`).join('');
            const body = w.ok === false ? `<div class="text-xs text-textMuted">${unavailable}</div>` : (assets || `<div class="text-xs text-textMuted">${noAssets}</div>`);
            return `
                <div class="rounded-2xl border border-borderSubtle bg-surfaceHighlight/40 p-4 space-y-2">
                    <div class="flex flex-wrap items-center justify-between gap-2">
                        <div class="flex flex-wrap items-center gap-2 min-w-0">
                            <span class="shrink-0 text-[10px] px-2 py-0.5 rounded-full bg-primary/10 text-primary font-bold whitespace-nowrap">${this._esc(chainLabel(w.network || w.chain))}</span>
                            <span class="shrink-0 font-mono text-sm text-secondary whitespace-nowrap">${this._esc(w.short || w.address)}</span>
                            ${w.is_primary ? `<span class="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full bg-success/15 text-success whitespace-nowrap">${primaryLabel}</span>` : ''}
                        </div>
                        <div class="flex items-center gap-2 shrink-0">
                            <span class="text-sm font-bold text-secondary">${this._esc(formatUsd(w.usd_total))}</span>
                            <button data-click="walletCopyAddress" data-click-arg="${this._esc(w.address)}" class="min-w-11 min-h-11 flex items-center justify-center rounded-full text-textMuted hover:text-primary hover:bg-surfaceHighlight transition" aria-label="${copyLabel}" title="${copyLabel}">
                                <i data-lucide="copy" class="w-4 h-4"></i>
                            </button>
                        </div>
                    </div>
                    <div class="space-y-1">${body}</div>
                </div>`;
        }).join('');
        if (window.AppUtils) window.AppUtils.refreshIcons();
    },

    async copyAddress(address) {
        try {
            await navigator.clipboard.writeText(String(address || ''));
            if (window.showToast) window.showToast(this._t('wallet.copied', 'Address copied'), 'success');
        } catch (e) {
            if (window.showToast) window.showToast(this._t('wallet.copyFailed', 'Copy failed'), 'error');
        }
    },

    // ── 鏈上同步（轉帳 → 帳本） ─────────────────────────────────
    async loadSyncStatus() {
        if (!document.getElementById('wallet-sync-card') || typeof AppAPI === 'undefined') return;
        try {
            const status = await AppAPI.get('/api/journal/onchain/status');
            this.renderSyncStatus(status);
        } catch (e) {
            console.warn('[WalletApp] sync status failed:', e);
        }
    },

    renderSyncStatus(status) {
        const toggle = document.getElementById('wallet-sync-enabled');
        const last = document.getElementById('wallet-sync-last');
        const result = document.getElementById('wallet-sync-result');
        if (toggle) toggle.checked = status.enabled !== false;
        if (last) {
            last.textContent = status.last_synced_at
                ? `${this._t('wallet.sync.last', 'Last sync')}: ${this.formatDate(status.last_synced_at)}`
                : this._t('wallet.sync.never', 'Not synced yet');
        }
        if (result && status.last_result) result.textContent = summarizeSyncResult(status.last_result, (k, f) => this._t(k, f));
    },

    async toggleSync(el) {
        try {
            const status = await AppAPI.put('/api/journal/onchain/status', { enabled: !!(el && el.checked) });
            this.renderSyncStatus(status);
        } catch (e) {
            console.warn('[WalletApp] toggle sync failed:', e);
            if (el) el.checked = !el.checked;
        }
    },

    async syncNow() {
        const btn = document.getElementById('wallet-sync-now');
        const result = document.getElementById('wallet-sync-result');
        if (btn) btn.disabled = true;
        if (result) result.textContent = this._t('wallet.sync.running', 'Syncing…');
        try {
            const res = await AppAPI.post('/api/journal/onchain/sync', {});
            this.renderSyncStatus(res);
            if (result) result.textContent = summarizeSyncResult(res.result, (k, f) => this._t(k, f));
            if (window.JournalTab && typeof window.JournalTab.refresh === 'function') window.JournalTab.refresh().catch(() => {});
        } catch (e) {
            const status = e && (e.status || (e.response && e.response.status));
            let msg = this._t('wallet.sync.failed', 'Sync failed, please try again later');
            if (status === 429) msg = this._t('wallet.sync.tooSoon', 'Please wait a minute before syncing again');
            else if (status === 400) msg = this._t('wallet.noWallets', 'No wallet bound yet. Bind an EVM wallet to see verified holdings and sync transfers to your ledger.');
            if (result) result.textContent = msg;
        } finally {
            if (btn) btn.disabled = false;
        }
    },

    log(msg, data) {
        console.log(`[WalletApp] ${msg}`, data || '');
        // Optional: Send to backend for remote debugging
        // fetch('/api/debug-log', { method: 'POST', body: JSON.stringify({ level: 'info', message: `[WalletApp] ${msg}`, data }) }).catch(() => {});
    },

    async loadData() {
        this.log('Loading data...');
        const container = document.getElementById('wallet-tx-list');

        if (!container) {
            this.log('Container not found');
            return;
        }

        // Loading State
        container.innerHTML =
            '<div class="text-center text-textMuted py-10 opacity-50"><i data-lucide="loader-2" class="w-6 h-6 animate-spin mx-auto mb-2"></i>' + (window.I18n ? window.I18n.t('wallet.loadingHistory') : 'Loading history...') + '</div>';
        if (window.AppUtils) window.AppUtils.refreshIcons();

        try {
            // Check ForumAPI
            if (typeof ForumAPI === 'undefined') {
                throw new Error('ForumAPI library not loaded');
            }

            this.log('Fetching API data...');

            // Fetch Data in Parallel
            const results = await Promise.allSettled([
                ForumAPI.getMyPayments(), // 0: Out: Post fees
                ForumAPI.getMyTipsSent(), // 1: Out: Tips sent
                ForumAPI.getMyTipsReceived(), // 2: In: Tips received
            ]);

            this.log('API Results:', {
                payments: results[0].status,
                tipsSent: results[1].status,
                tipsReceived: results[2].status,
            });

            // Extract data with comprehensive error handling
            const paymentsData =
                results[0].status === 'fulfilled' ? results[0].value : { payments: [] };
            const tipsSentData =
                results[1].status === 'fulfilled' ? results[1].value : { tips: [] };
            const tipsReceivedData =
                results[2].status === 'fulfilled' ? results[2].value : { tips: [] };

            // Log any rejected promises for debugging
            results.forEach((result, index) => {
                if (result.status === 'rejected') {
                    const apiNames = ['getMyPayments', 'getMyTipsSent', 'getMyTipsReceived'];
                    console.warn(`[WalletApp] ${apiNames[index]} failed:`, result.reason);
                }
            });

            // Process Outgoing
            const payments = (paymentsData.payments || []).map((p) => {
                let type = 'post_payment';
                let icon = 'file-text';

                if (p.is_admin_grant) {
                    type = 'admin_grant';
                    icon = 'badge-check';
                } else if (p.type === 'membership') {
                    type = 'membership_payment';
                    icon = 'crown';
                }

                // 金額以後端的 display_amount（依資產別）為準；舊後端沒帶就退回 amount
                const raw = p.display_amount ?? p.amount ?? 0;
                return {
                    ...p,
                    type: type,
                    asset: type === 'admin_grant' ? null : paymentAsset(p),
                    amount: type === 'admin_grant' ? 0 : -(Number(raw) || 0), // Ensure expense is negative
                    icon: icon,
                };
            });

            const tipsSent = (tipsSentData.tips || []).map((t) => ({
                ...t,
                type: 'tip_sent',
                asset: paymentAsset(t),
                amount: -(t.amount || 0),
                title: `Tip: ${t.post_title || 'Post'}`,
            }));

            // Process Incoming
            const tipsReceived = (tipsReceivedData.tips || []).map((t) => ({
                ...t,
                type: 'tip_received',
                asset: paymentAsset(t),
                amount: t.amount || 0,
                title: `Tip from ${t.from_username || 'User'}`,
            }));

            // Combine & Sort & Store globally - ALWAYS initialize even if empty
            this.allTransactions = [...payments, ...tipsSent, ...tipsReceived].sort(
                (a, b) => new Date(b.created_at) - new Date(a.created_at)
            );

            this.log(`Processed ${this.allTransactions.length} transactions`);

            // Apply default filters (All) which will also render the list
            this.applyFilters();
        } catch (e) {
            console.error('[WalletApp] Load Error:', e);
            // Initialize empty array to prevent undefined errors
            this.allTransactions = [];
            this.renderError(e.message || (window.I18n ? window.I18n.t('wallet.unknownError') : 'Unknown error occurred'));
        }
    },

    handleTimeFilterChange(selectEl) {
        const customRangeEl = document.getElementById('wallet-custom-date-range');
        if (!customRangeEl) return;

        if (selectEl.value === 'custom') {
            customRangeEl.classList.remove('hidden');
        } else {
            customRangeEl.classList.add('hidden');
        }
        this.applyFilters();
    },

    applyFilters() {
        // Initialize as empty array if undefined (defensive programming)
        if (!this.allTransactions) {
            this.allTransactions = [];
        }

        const typeFilter = document.getElementById('wallet-filter-type')?.value || 'all';
        const timeFilter = document.getElementById('wallet-filter-time')?.value || 'all';

        const now = Date.now();
        const oneDay = 24 * 60 * 60 * 1000;

        const filtered = this.allTransactions.filter((tx) => {
            // 1. Filter by Type
            let typeMatch = true;
            if (typeFilter === 'in') typeMatch = tx.amount > 0;
            else if (typeFilter === 'out') typeMatch = tx.amount < 0;
            else if (typeFilter === 'membership')
                typeMatch = tx.type === 'membership_payment' || tx.type === 'admin_grant';
            else if (typeFilter === 'tip')
                typeMatch = tx.type === 'tip_sent' || tx.type === 'tip_received';
            else if (typeFilter === 'post') typeMatch = tx.type === 'post_payment';

            // 2. Filter by Time
            let timeMatch = true;
            const txTime = new Date(tx.created_at).getTime();

            if (timeFilter === 'custom') {
                const startStr = document.getElementById('wallet-date-start')?.value;
                const endStr = document.getElementById('wallet-date-end')?.value;

                if (startStr) {
                    const startDate = new Date(startStr);
                    startDate.setHours(0, 0, 0, 0);
                    if (txTime < startDate.getTime()) timeMatch = false;
                }
                if (endStr) {
                    const endDate = new Date(endStr);
                    endDate.setHours(23, 59, 59, 999);
                    if (txTime > endDate.getTime()) timeMatch = false;
                }
            } else if (timeFilter !== 'all') {
                const days = parseInt(timeFilter);
                timeMatch = now - txTime < days * oneDay;
            }

            return typeMatch && timeMatch;
        });

        // Calculate Totals based on Filtered Data
        // This gives users insight into "How much did I spend on TIPS in the last 7 DAYS"
        const totalOutEl = document.getElementById('wallet-total-out');
        const totalInEl = document.getElementById('wallet-total-in');

        const totals = summarizeTotals(filtered);
        if (totalOutEl) totalOutEl.textContent = totals.out;
        if (totalInEl) totalInEl.textContent = totals.in;

        this.renderList(filtered);
    },

    renderList(list) {
        const container = document.getElementById('wallet-tx-list');
        if (!container) return;

        container.innerHTML = '';

        if (list.length === 0) {
            this.renderEmptyState((window.I18n ? window.I18n.t('wallet.noMatchingTx') : 'No transactions found matching filters'));
            return;
        }

        list.forEach((tx) => {
            const el = document.createElement('div');
            el.className =
                'flex items-center justify-between border-b border-borderSubtle py-4 hover:bg-surfaceHighlight px-3 rounded-2xl transition cursor-pointer last:border-0 group animate-fade-in-up';

            let icon = 'circle-dollar-sign';
            const title = this.typeLabel(tx);
            let subtext = '';
            let colorClass =
                tx.amount < 0 ? 'bg-danger/10 text-danger' : 'bg-success/10 text-success';

            if (tx.type === 'post_payment') {
                subtext = tx.title || 'Forum Post';
                icon = 'file-text';
            } else if (tx.type === 'membership_payment') {
                subtext = tx.title || 'Membership';
                icon = 'crown';
                colorClass = 'bg-primary/10 text-primary';
            } else if (tx.type === 'admin_grant') {
                subtext = tx.title || 'Membership';
                icon = 'badge-check';
                colorClass = 'bg-primary/10 text-primary';
            } else if (tx.type === 'tip_sent') {
                subtext = tx.title || 'Tip Support';
                icon = 'gift';
            } else if (tx.type === 'tip_received') {
                subtext = tx.title || 'Content Reward';
                icon = 'trophy';
            }


            el.onclick = () => this.showDetail(tx);

            el.innerHTML = `
               <div class="flex items-center gap-4 overflow-hidden">
                    <div class="w-12 h-12 rounded-full ${colorClass} flex items-center justify-center shrink-0 group-hover:scale-110 transition">
                       <i data-lucide="${icon}" class="w-5 h-5"></i>
                    </div>
                    <div class="overflow-hidden">
                        <div class="font-bold text-textMain truncate">${title}</div>
                        <div class="text-xs text-textMuted mt-0.5 truncate">${subtext}</div>
                    </div>
               </div>
               <div class="text-right shrink-0">
                   <div class="font-bold text-base ${tx.type === 'membership_payment' || tx.type === 'admin_grant' ? 'text-primary' : tx.amount < 0 ? 'text-textMain' : 'text-success'}">
                       ${formatTxAmount(tx)}
                   </div>
                   <div class="text-[10px] text-textMuted opacity-50 mt-1">${this.formatDate(tx.created_at)}</div>
               </div>
            `;
            container.appendChild(el);
        });

        if (window.AppUtils) window.AppUtils.refreshIcons();
    },

    renderEmptyState(msg = (window.I18n ? window.I18n.t('wallet.noTxYet') : 'No transactions yet')) {
        const container = document.getElementById('wallet-tx-list');
        if (container) {
            container.innerHTML = `
                <div class="flex flex-col items-center justify-center py-12 text-textMuted opacity-60">
                    <div class="w-16 h-16 bg-surfaceHighlight rounded-full flex items-center justify-center mb-4">
                        <i data-lucide="history" class="w-8 h-8"></i>
                    </div>
                    <p>${msg}</p>
                </div>
            `;
            if (window.AppUtils) window.AppUtils.refreshIcons();
        }
    },

    renderError(msg) {
        const container = document.getElementById('wallet-tx-list');
        if (container) {
            container.innerHTML = `
                <div class="text-center py-10">
                    <div class="text-danger font-bold mb-2" data-i18n="wallet.loadFailed">Failed to load history</div>
                    <div class="text-xs text-textMuted">${msg}</div>
                    <button data-click="WalletApp.loadData" class="mt-4 px-4 py-2 bg-surfaceHighlight hover:bg-surfaceHighlight rounded-lg text-sm transition"><span data-i18n="common.retry">Retry</span></button>
                </div>
            `;
        }
    },

    typeLabel(tx) {
        const t = (key, fallback) => (window.I18n ? window.I18n.t(key) : '') || fallback;
        switch (tx.type) {
            case 'post_payment':
                return t('wallet.txTypePostFee', 'Post Fee');
            case 'membership_payment':
                return t('wallet.txTypePremiumUpgrade', 'Premium Upgrade');
            case 'admin_grant':
                return t('wallet.txTypeAdminGrant', 'Admin grant');
            case 'tip_sent':
                return t('wallet.txTypeTipSent', 'Tip Sent');
            case 'tip_received':
                return t('wallet.txTypeTipReceived', 'Tip Received');
            default:
                return t('wallet.txTypeTransaction', 'Transaction');
        }
    },
    // 明細走平台的 ContentModal（手機版面一致），不再用瀏覽器原生 alert
    //（DANNY 截圖：alert 連換行符號都原樣印出來）
    showDetail(tx) {
        const t = (key, fallback) => (window.I18n ? window.I18n.t(key) : '') || fallback;
        const amountLine =
            tx.type === 'admin_grant'
                ? t('wallet.grantNoAmount', 'Granted by admin, no payment')
                : formatTxAmount(tx);
        const lines = [
            `${t('wallet.detailType', 'Type')}: ${this.typeLabel(tx)}`,
            `${t('wallet.detailAmount', 'Amount')}: ${amountLine}`,
            tx.chain ? `${t('wallet.detailChain', 'Network')}: ${String(tx.chain).toUpperCase()}` : null,
            `${t('wallet.detailDate', 'Date')}: ${this.formatDate(tx.created_at)}`,
            `${t('wallet.detailHash', 'Reference')}: ${tx.tx_hash || '-'}`,
        ].filter(Boolean);
        const body = lines.join('\n');
        if (window.ContentModal && typeof window.ContentModal.open === 'function') {
            window.ContentModal.open({
                title: t('wallet.detailTitle', 'Transaction details'),
                subtitle: this.typeLabel(tx),
                body,
                editable: false,
            });
            return;
        }
        if (typeof ForumApp !== 'undefined' && typeof ForumApp.showTransactionDetail === 'function') {
            ForumApp.showTransactionDetail(tx);
            return;
        }
        window.alert(body);
    },

    formatDate(dateStr) {
        try {
            const date = new Date(dateStr);
            const lang = (window.I18n && typeof window.I18n.getLanguage === 'function' && window.I18n.getLanguage()) || undefined;
            return date.toLocaleDateString(lang, {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
            });
        } catch (e) {
            return dateStr;
        }
    },
};

// Auto-init if tab is already active (e.g. reload)
document.addEventListener('DOMContentLoaded', () => {
    // Wait for auth to be ready
    setTimeout(() => {
        if (
            document.getElementById('wallet-tab') &&
            !document.getElementById('wallet-tab').classList.contains('hidden')
        ) {
            WalletApp.init();
        }
    }, 1000);
});

// ========================================
// CRITICAL: Export to window for global access
// ========================================
window.WalletApp = WalletApp;

export { WalletApp, formatTxAmount, paymentAsset, summarizeTotals, formatUsd, formatAssetAmount, chainLabel, sortWallets, summarizeSyncResult };
