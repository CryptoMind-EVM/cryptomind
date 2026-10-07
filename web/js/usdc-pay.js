// ========================================
// usdc-pay.js — USDC on Base 錢包直付共用（premium 訂閱、論壇發文費／打賞）
//
// 2026-09-25 論壇付款從 TON 改成 USDC on Base，跟 premium 走同一條錢包直付：
// 後端簽訂單（收款人＋含唯一尾數的金額）→ 這裡組 ERC-20 transfer calldata、
// eth_sendTransaction → 帶 tx hash 給後端做 receipt 驗證。送錢前的檢查（付款帳號
// 必須是綁定錢包、錯鏈先切鏈）只有這一份，從 premium.js 抽出來共用。
// ========================================
import { withBuilderCode } from './builder-code.js';

const t = (key, fallback) => (window.I18n ? window.I18n.t(key) || fallback : fallback);

// 付款人檢查（2026-09-11 盤查）：後端只認「已綁定地址」送出的 USDC
// （payer binding，fail-closed），以前錢轉出去了才在 claim 階段被擋。
// 這裡在建單前／錢包送款前就判：沒綁定 → 先引導綁定；錢包目前帳號不是
// 綁定地址 → 擋下來講清楚。純函式，node 測試看守。
function resolvePaymentPayer(boundAddresses, account) {
    const bound = (boundAddresses || []).map((a) => String(a).toLowerCase());
    if (!bound.length) return { ok: false, reason: 'no_binding', bound };
    if (!account) return { ok: true, bound }; // 手動轉帳：還沒有帳號可比
    return bound.includes(String(account).toLowerCase())
        ? { ok: true, bound }
        : { ok: false, reason: 'account_mismatch', bound };
}

function shortAddr(a) {
    const s = String(a || '');
    return s.length > 12 ? `${s.slice(0, 6)}…${s.slice(-4)}` : s;
}

// Telegram Mini App 內沒有注入錢包、WC deep link 又是已知死路——付款要
// 引導改用外部瀏覽器（或錢包 App 內建瀏覽器）開本站。
function isTelegramMiniApp() {
    try {
        const wa = window.Telegram && window.Telegram.WebApp;
        return !!(wa && wa.initData);
    } catch (e) {
        return false;
    }
}

function hasInjectedWallet() {
    return !!(window.ethereum && typeof window.ethereum.request === 'function');
}

/**
 * 已綁定的 EVM 地址（小寫）。查不到回 null＝不擋（後端仍 fail-closed）。
 */
async function fetchBoundEvmAddresses() {
    try {
        const r = await AppAPI.get('/api/user/wallets');
        if (!r || !r.success) return null;
        return (r.evm_addresses || []).map((a) => String(a).toLowerCase());
    } catch (e) {
        console.warn('[usdc-pay] bound wallet lookup failed', e);
        return null;
    }
}

/**
 * Telegram 內的付款提示：不建單、不顯示任何 USDC／Base 資訊，只導去外部瀏覽器。
 */
function showOpenInBrowserNotice({ id, title, hint, url }) {
    document.getElementById(id)?.remove();
    const overlay = document.createElement('div');
    overlay.id = id;
    overlay.className = 'fixed inset-0 z-[80] bg-background/80 backdrop-blur-sm flex items-center justify-center p-4';
    overlay.innerHTML = `
            <div class="bg-surface w-full max-w-md p-6 rounded-[2rem] border border-borderLight shadow-2xl space-y-4">
                <h3 data-notice-title class="font-serif text-lg text-secondary text-center"></h3>
                <p data-notice-hint class="text-sm text-textMuted leading-relaxed"></p>
                <button data-notice-open class="w-full px-4 py-3 min-h-11 bg-primary hover:bg-primary/90 text-background font-bold rounded-xl transition">${t('premium.openInBrowser', 'Open in browser')}</button>
                <button data-notice-cancel class="w-full py-2 min-h-11 text-textMuted text-sm font-bold hover:text-secondary transition">${t('common.cancel', 'Cancel')}</button>
            </div>`;
    // 文字一律走 textContent（呼叫端傳的是 i18n 字串，但別讓它有機會變成 HTML）
    overlay.querySelector('[data-notice-title]').textContent = title;
    overlay.querySelector('[data-notice-hint]').textContent = hint;
    document.body.appendChild(overlay);
    overlay.querySelector('[data-notice-open]').addEventListener('click', () => {
        if (typeof window.openExternalLink === 'function') window.openExternalLink(url);
        else window.open(url, '_blank');
    });
    overlay.querySelector('[data-notice-cancel]').addEventListener('click', () => overlay.remove());
}

/**
 * 錢包直付：組 USDC transfer(address,amount) calldata 並以
 * eth_sendTransaction 送出（收款地址與含唯一尾數的精確金額來自訂單）。
 * 送出前強制 chainId 檢查／切鏈——錯鏈直付＝資產損失，不可省。
 */
async function walletSendUsdc(order, bound) {
    const eth = window.ethereum;
    let accounts = await eth.request({ method: 'eth_accounts' });
    if (!accounts || !accounts.length) {
        accounts = await eth.request({ method: 'eth_requestAccounts' });
    }
    const from = accounts && accounts[0];
    if (!from) throw new Error(t('premium.walletNoAccount', 'No wallet account available'));

    // 付款帳號必須是已綁定地址——錢包切到別的帳號付了也不算數，送出前先擋。
    // bound === null（清單查不到）時不擋，交給後端的 fail-closed。
    if (bound !== null && bound !== undefined) {
        const payer = resolvePaymentPayer(bound, from);
        if (!payer.ok) {
            throw new Error(
                payer.reason === 'account_mismatch'
                    ? t('premium.payerMismatch', 'Wallet account {{from}} is not bound to your account — switch to {{bound}} in your wallet, or bind this account first')
                          .replace('{{from}}', shortAddr(from))
                          .replace('{{bound}}', payer.bound.map(shortAddr).join(' / '))
                    : t('premium.payerMustBeBound', 'Bind the wallet you pay from first (Settings → Wallet)')
            );
        }
    }

    const isSepolia = order.network === 'base_sepolia';
    const wantChain = isSepolia ? '0x14a34' : '0x2105';
    const curChain = await eth.request({ method: 'eth_chainId' });
    if (curChain !== wantChain) {
        try {
            await eth.request({
                method: 'wallet_switchEthereumChain',
                params: [{ chainId: wantChain }],
            });
        } catch (sw) {
            if (sw && (sw.code === 4902 || /unrecognized chain/i.test(sw.message || ''))) {
                await eth.request({
                    method: 'wallet_addEthereumChain',
                    params: [{
                        chainId: wantChain,
                        chainName: isSepolia ? 'Base Sepolia' : 'Base',
                        nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
                        rpcUrls: [isSepolia ? 'https://sepolia.base.org' : 'https://mainnet.base.org'],
                        blockExplorerUrls: [isSepolia ? 'https://sepolia.basescan.org' : 'https://basescan.org'],
                    }],
                });
            } else {
                throw sw;
            }
        }
        const after = await eth.request({ method: 'eth_chainId' });
        if (after !== wantChain) {
            throw new Error(t('premium.chainSwitchNeeded', 'Please switch to the Base network in your wallet, then try again'));
        }
    }

    // 地址要是完整的 0x＋40 hex 才組 calldata（padStart 會把壞地址默默補成別的地址）
    const isAddr = (a) => /^0x[0-9a-fA-F]{40}$/.test(String(a || ''));
    if (!order.micro || !isAddr(order.token_contract) || !isAddr(order.receiving_address)) {
        throw new Error(t('premium.verifyFailed', 'Verification failed'));
    }
    const padAddr = (a) => a.toLowerCase().replace(/^0x/, '').padStart(64, '0');
    const padAmount = (m) => BigInt(m).toString(16).padStart(64, '0');
    // ERC-20 transfer(address,uint256)：selector a9059cbb；
    // 尾巴接 Base Builder Code（ERC-8021 歸因，合約忽略、索引器讀）
    const data = withBuilderCode('0xa9059cbb' + padAddr(order.receiving_address) + padAmount(order.micro));
    return await eth.request({
        method: 'eth_sendTransaction',
        params: [{ from, to: order.token_contract, value: '0x0', data }],
    });
}

/**
 * 領取時哪些錯誤值得再問一次：交易還沒上鏈／確認數不夠／還沒掃到、5xx、網路或
 * 逾時、限流。其餘 4xx（金額不符、付款人未綁定、訂單過期、已被使用、每日額度
 * 用完…）重問也不會變，立刻停。
 */
function isRetryableClaimError(e) {
    const status = e && typeof e.status === 'number' ? e.status : 0;
    const msg = (e && e.message) || '';
    if (status === 429) return !/daily/i.test(msg);
    if (status === 0 || status >= 500) return true;
    return /not confirmed|awaiting confirmations|no matching payment found yet/i.test(msg);
}

function _cancelled() {
    const err = new Error('verification cancelled');
    err.cancelled = true;
    return err;
}

/**
 * 錢包送出後輪詢後端領取（交易要幾個區塊確認）。節奏同 premium：7 秒 × 15 次
 * （≈8.6/min，在各端點限流內），429 限流多等一下。isAlive() 回 false＝面板已
 * 關／被取代，丟 cancelled 哨兵（呼叫端靜默結束）。
 */
async function pollUsdcClaim(submit, { total = 15, intervalMs = 7000, onProgress, isAlive, sleep } = {}) {
    const wait = sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));
    let lastErr = null;
    for (let i = 0; i < total; i++) {
        if (onProgress) onProgress(i + 1, total);
        if (isAlive && !isAlive()) throw _cancelled();
        try {
            return await submit();
        } catch (e) {
            lastErr = e;
            if (!isRetryableClaimError(e)) {
                if (isAlive && !isAlive()) throw _cancelled();
                throw e;
            }
            if (e && e.status === 429) {
                await wait(9000);
                continue;
            }
        }
        await wait(intervalMs);
    }
    if (isAlive && !isAlive()) throw _cancelled();
    throw lastErr || new Error(t('wallet.paymentTimeout', 'Payment verification timed out. Please check the records later.'));
}

export {
    resolvePaymentPayer,
    shortAddr,
    isTelegramMiniApp,
    hasInjectedWallet,
    fetchBoundEvmAddresses,
    showOpenInBrowserNotice,
    walletSendUsdc,
    isRetryableClaimError,
    pollUsdcClaim,
};
