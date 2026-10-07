// ========================================
// scam-check.js - 詐騙檢查分頁（#scamcheck）
// 2026-09-27：取代 /scam-tracker/ 列表頁（正式站舉報清單是空的，看起來像被棄置）。
// 核心是公開免登入的 GET /api/scam-tracker/reports/check（社群舉報＋GoPlus＋TonAPI 合成判定）。
// 合規：只有 verdict=no_red_flags 走綠色，而且一定帶「不是保證」；查不完整、請求失敗、
// 看不懂的回應一律中性「無法完成檢查」，任何狀態都不寫「安全」。
// 骨架在 components/tab-scamcheck.js，這裡畫結果卡、社群舉報列表與動作。
// plan：docs/plans/2026-09-27-scam-check-tab-impl.md
// ========================================

// Base 上的 USDC 合約：「試試範例」用
export const EXAMPLE_ADDRESS = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913';

// 與後端 api/routers/scam_tracker/reports.py 的 _detect_family 同一組格式
const EVM_RE = /^0x[0-9a-fA-F]{40}$/;
const TON_FRIENDLY_RE = /^[EUQ]Q[0-9A-Za-z_-]{46}$/;
const TON_RAW_RE = /^0:[0-9a-fA-F]{64}$/;

// ── 純邏輯（tests/js/scam_check.mjs） ──

/** 輸入 → { ok, value(已 trim), family } 或 { ok:false, value, error:'empty'|'format' }。 */
export function validateAddress(raw) {
    const value = String(raw == null ? '' : raw).trim();
    if (!value) return { ok: false, value: '', error: 'empty' };
    if (EVM_RE.test(value)) return { ok: true, value, family: 'evm' };
    if (TON_FRIENDLY_RE.test(value) || TON_RAW_RE.test(value)) return { ok: true, value, family: 'ton' };
    return { ok: false, value, error: 'format' };
}

function sourceRows(sources) {
    const s = sources && typeof sources === 'object' ? sources : {};
    const rows = [];
    const community = s.community;
    if (community && typeof community === 'object') {
        if (community.status === 'error') rows.push({ id: 'community', status: 'unavailable' });
        else if (community.found) {
            rows.push({ id: 'community', status: 'reported', reportId: community.report_id ?? null });
        } else rows.push({ id: 'community', status: 'noReports' });
    }
    const goplus = s.goplus;
    if (goplus && typeof goplus === 'object') {
        let status = 'noFlags';
        if (goplus.status === 'error') status = 'unavailable';
        else if (Array.isArray(goplus.flags) && goplus.flags.length) status = 'flagged';
        rows.push({ id: 'goplus', status });
    }
    const tonapi = s.tonapi;
    if (tonapi && typeof tonapi === 'object') {
        let status = 'noFlags';
        if (tonapi.status === 'error') status = 'unavailable';
        else if (tonapi.verification === 'blacklist') status = 'blacklisted';
        // 代幣合約有管理員權限＝上面的 jetton_admin_privilege 訊號；這裡不能再寫「沒有標記」
        else if (tonapi.has_admin) status = 'adminRights';
        rows.push({ id: 'tonapi', status });
    }
    return rows;
}

/**
 * 健診 API 回應 → 畫面狀態。
 * high_risk → danger；no_red_flags → clean；caution 有具體訊號 → caution，
 * caution 沒有訊號（＝某個來源查詢失敗）→ incomplete；其餘一律 incomplete（絕不變綠）。
 */
export function mapCheckResult(data) {
    const ok = !!data && typeof data === 'object' && data.success === true;
    if (!ok) return { state: 'incomplete', family: null, flags: [], sources: [] };
    const flags = Array.isArray(data.reasons)
        ? [...new Set(data.reasons.filter((r) => typeof r === 'string' && r))]
        : [];
    let state = 'incomplete';
    if (data.verdict === 'high_risk') state = 'danger';
    else if (data.verdict === 'no_red_flags') state = 'clean';
    else if (data.verdict === 'caution' && flags.length) state = 'caution';
    const family = data.family === 'evm' || data.family === 'ton' ? data.family : null;
    return { state, family, flags, sources: sourceRows(data.sources) };
}

/** 請求失敗：422＝格式不支援（輸入框下方提示），其他（離線、逾時、429、5xx、API 關閉）＝無法完成。 */
export function errorView(err) {
    return err && err.status === 422 ? { state: 'invalid' } : { state: 'error' };
}

export function askPrompt(t, address) {
    return t(
        'scamcheck.askPrompt',
        'Check this address for scam risk signals and explain what you find: {{address}}',
        { address }
    );
}

export function reportUrl(address) {
    return `/static/scam-tracker/submit.html?address=${encodeURIComponent(address)}`;
}

export function detailUrl(id) {
    return `/static/scam-tracker/detail.html?id=${encodeURIComponent(id)}`;
}

export function shortAddress(address) {
    const s = String(address == null ? '' : address);
    return s.length > 14 ? `${s.slice(0, 6)}…${s.slice(-4)}` : s;
}

/** 舊網址導過來的 ?address=：取出來並回傳拿掉它之後的 search（其他參數保留）。 */
export function takeAddressParam(search) {
    const original = search || '';
    const params = new URLSearchParams(original);
    if (!params.has('address')) return { address: null, search: original };
    const address = (params.get('address') || '').trim() || null;
    params.delete('address');
    const rest = params.toString();
    return { address, search: rest ? `?${rest}` : '' };
}

// ── 畫面（純字串，node 測得到） ──

const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ESC_MAP[c]);
}

const TONE = {
    clean: {
        card: 'border-success/25 bg-success/5',
        iconBox: 'bg-success/10 text-success',
        icon: 'shield-check',
        title: 'text-success',
        flag: 'text-success',
    },
    caution: {
        card: 'border-amber-500/30 bg-amber-500/5',
        iconBox: 'bg-amber-500/10 text-amber-700 dark:text-amber-400',
        icon: 'triangle-alert',
        title: 'text-amber-700 dark:text-amber-400',
        flag: 'text-amber-700 dark:text-amber-400',
    },
    danger: {
        card: 'border-danger/30 bg-danger/5',
        iconBox: 'bg-danger/10 text-danger',
        icon: 'shield-x',
        title: 'text-danger',
        flag: 'text-danger',
    },
    neutral: {
        card: 'border-borderLight bg-surface',
        iconBox: 'bg-surfaceHighlight text-textMuted',
        icon: 'circle-help',
        title: 'text-textMain',
        flag: 'text-textMuted',
    },
};

const STATE_FALLBACK = {
    clean: [
        'No red flags found',
        "None of the sources we checked flagged this address. That's not a guarantee — new or unreported scams won't show up.",
    ],
    caution: [
        'Some risk signals found',
        "Review the signals below and verify who you're dealing with before you send anything.",
    ],
    danger: [
        'High risk: reported or flagged',
        'Community reports or security data flagged this address. We strongly advise against sending funds to it.',
    ],
    incomplete: [
        "Couldn't complete the check",
        "A data source didn't respond, so we can't give a result this time. Try again in a moment.",
    ],
    error: ["Couldn't complete the check", "The check service didn't respond. Try again in a moment."],
};

const SOURCE_FALLBACK = {
    community: 'Community reports',
    goplus: 'GoPlus security data',
    tonapi: 'TonAPI token data',
};

const PILL = 'px-2 py-0.5 rounded-full text-[11px] font-medium shrink-0 whitespace-nowrap';
const SOURCE_STATUS = {
    reported: ['bg-danger/10 text-danger', 'Reported'],
    flagged: ['bg-danger/10 text-danger', 'Flagged'],
    blacklisted: ['bg-danger/10 text-danger', 'Blacklisted'],
    adminRights: ['bg-amber-500/10 text-amber-700 dark:text-amber-400', 'Admin privileges'],
    noReports: ['bg-surfaceHighlight text-textMuted', 'No reports'],
    noFlags: ['bg-surfaceHighlight text-textMuted', 'No flags'],
    unavailable: ['bg-amber-500/10 text-amber-700 dark:text-amber-400', 'Unavailable'],
};

const BTN = 'inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm transition whitespace-nowrap';

function reasonLabel(t, reason) {
    return t(`safety.checkup.reason.${reason}`, reason.replace(/_/g, ' '));
}

function sectionTitle(text) {
    return `<p class="text-[11px] font-semibold uppercase tracking-wide text-textMuted">${esc(text)}</p>`;
}

/** 結果卡。view 來自 mapCheckResult／errorView；address 是使用者查的（已驗證）地址。 */
export function resultHtml(view, address, t) {
    const state = view && STATE_FALLBACK[view.state] ? view.state : 'error';
    const neutral = state === 'incomplete' || state === 'error';
    const tone = neutral ? TONE.neutral : TONE[state];
    const [titleFb, descFb] = STATE_FALLBACK[state];
    const flags = (view && view.flags) || [];
    const sources = (view && view.sources) || [];
    const chain = view && view.family ? view.family.toUpperCase() : '';

    const flagList = flags.length
        ? `
        <div class="mt-4">
            ${sectionTitle(t('scamcheck.flagsTitle', 'Risk signals found'))}
            <ul class="mt-2 space-y-1.5">${flags
                .map(
                    (r) => `
                <li class="flex items-start gap-2 text-sm text-textMain">
                    <i data-lucide="flag" class="w-3.5 h-3.5 mt-1 shrink-0 ${tone.flag}"></i>
                    <span class="min-w-0">${esc(reasonLabel(t, r))}</span>
                </li>`
                )
                .join('')}
            </ul>
        </div>`
        : '';

    const sourceList = sources.length
        ? `
        <div class="mt-4">
            ${sectionTitle(t('scamcheck.sourcesTitle', 'Sources checked'))}
            <ul class="mt-2 rounded-2xl border border-borderSubtle divide-y divide-borderSubtle">${sources
                .map((s) => {
                    const [pill, statusFb] = SOURCE_STATUS[s.status] || SOURCE_STATUS.unavailable;
                    const link =
                        s.reportId != null
                            ? `<a href="${esc(detailUrl(s.reportId))}" class="text-xs text-primary underline underline-offset-2 whitespace-nowrap">${esc(t('scamcheck.viewReport', 'View report'))}</a>`
                            : '';
                    return `
                <li class="flex items-center justify-between gap-3 px-3 py-2.5">
                    <span class="min-w-0 truncate text-sm text-textMain">${esc(t(`scamcheck.source.${s.id}`, SOURCE_FALLBACK[s.id] || s.id))}</span>
                    <span class="flex items-center gap-2 shrink-0">
                        ${link}
                        <span class="${PILL} ${pill}">${esc(t(`scamcheck.sourceStatus.${s.status}`, statusFb))}</span>
                    </span>
                </li>`;
                })
                .join('')}
            </ul>
        </div>`
        : '';

    const retry = neutral
        ? `<button type="button" data-click="ScamCheckTab.retry" class="${BTN} bg-primary text-background font-semibold hover:brightness-110">
                <i data-lucide="rotate-cw" class="w-4 h-4"></i>
                <span>${esc(t('scamcheck.retry', 'Try again'))}</span>
            </button>`
        : '';

    return `
    <div data-scamcheck-state="${esc(state)}" class="rounded-3xl border ${tone.card} p-4 md:p-6">
        <div class="flex items-start gap-3">
            <span class="w-10 h-10 shrink-0 rounded-2xl ${tone.iconBox} flex items-center justify-center">
                <i data-lucide="${tone.icon}" class="w-5 h-5"></i>
            </span>
            <div class="min-w-0 flex-1">
                <h3 class="h3 text-base md:text-lg ${tone.title}">${esc(t(`scamcheck.state.${state}.title`, titleFb))}</h3>
                <p class="mt-1 text-sm leading-relaxed text-textMuted">${esc(t(`scamcheck.state.${state}.desc`, descFb))}</p>
            </div>
        </div>
        <div class="mt-4 flex items-center gap-2 min-w-0 rounded-xl bg-surfaceHighlight/60 px-3 py-2">
            ${chain ? `<span class="${PILL} bg-surface text-textMuted border border-borderSubtle">${esc(chain)}</span>` : ''}
            <code class="min-w-0 font-mono text-xs text-textMain break-all">${esc(address)}</code>
        </div>
        ${flagList}
        ${sourceList}
        <p class="mt-4 text-[11px] leading-relaxed text-textMuted">${esc(t('scamcheck.note', 'Not a guarantee. Results are automated screening of third-party security data and community reports, for reference only — not investment advice.'))}</p>
        <div class="mt-4 flex flex-col sm:flex-row sm:flex-wrap gap-2">
            ${retry}
            <button type="button" data-click="ScamCheckTab.askAI" class="${BTN} bg-primary/10 text-primary border border-primary/20 hover:bg-primary/15 font-semibold">
                <i data-lucide="message-circle" class="w-4 h-4"></i>
                <span>${esc(t('scamcheck.askAi', 'Ask the AI about this address'))}</span>
            </button>
            <button type="button" data-click="ScamCheckTab.report" class="${BTN} border border-borderLight text-textMain hover:bg-surfaceHighlight font-medium">
                <i data-lucide="flag" class="w-4 h-4"></i>
                <span>${esc(t('scamcheck.report', 'Report this address'))}</span>
            </button>
        </div>
    </div>`;
}

export function loadingHtml(t) {
    return `
    <div data-scamcheck-state="loading" class="rounded-3xl border border-borderSubtle bg-surface p-4 md:p-6 flex items-center gap-3">
        <i data-lucide="loader-circle" class="w-5 h-5 shrink-0 text-primary animate-spin"></i>
        <p class="text-sm text-textMuted">${esc(t('scamcheck.loading', 'Checking GoPlus security data and community reports…'))}</p>
    </div>`;
}

const TYPE_KEYS = {
    fake_official: ['safety.fakeOfficial', 'Fake official'],
    investment_scam: ['safety.investmentScam', 'Investment scam'],
    fake_airdrop: ['safety.fakeAirdrop', 'Fake airdrop'],
    trading_fraud: ['safety.tradingFraud', 'Trading fraud'],
    gambling: ['safety.gambling', 'Gambling'],
    phishing: ['safety.phishing', 'Phishing'],
    other: ['safety.otherScam', 'Other'],
};
const REPORT_STATUS = {
    verified: ['bg-danger/10 text-danger', 'safety.statusVerified', 'Verified'],
    pending: ['bg-amber-500/10 text-amber-700 dark:text-amber-400', 'safety.statusPending', 'Pending review'],
    disputed: ['bg-surfaceHighlight text-textMuted', 'safety.statusDisputed', 'Disputed'],
};

function relativeTime(iso, lang, now) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    const seconds = (date.getTime() - now.getTime()) / 1000;
    const abs = Math.abs(seconds);
    try {
        if (abs >= 30 * 86400) {
            return new Intl.DateTimeFormat(lang, { month: 'short', day: 'numeric' }).format(date);
        }
        const rtf = new Intl.RelativeTimeFormat(lang, { numeric: 'auto' });
        if (abs < 3600) return rtf.format(Math.round(seconds / 60), 'minute');
        if (abs < 86400) return rtf.format(Math.round(seconds / 3600), 'hour');
        return rtf.format(Math.round(seconds / 86400), 'day');
    } catch (_e) {
        return '';
    }
}

/** 最近的社群舉報（<li> 清單）；沒有資料回空字串，整段由呼叫端隱藏。 */
export function recentHtml(reports, t, lang, now = new Date()) {
    const rows = Array.isArray(reports) ? reports.filter((r) => r && r.scam_wallet_address) : [];
    return rows
        .slice(0, 5)
        .map((r) => {
            const [typeKey, typeFb] = TYPE_KEYS[r.scam_type] || [];
            const type = typeKey ? t(typeKey, typeFb) : String(r.scam_type || '').replace(/_/g, ' ');
            const [cls, statusKey, statusFb] = REPORT_STATUS[r.verification_status] || REPORT_STATUS.pending;
            const when = relativeTime(r.created_at, lang, now);
            const meta = [type, when].filter(Boolean).map(esc).join(' · ');
            return `
            <li>
                <a href="${esc(detailUrl(r.id))}" class="flex items-center gap-3 py-3 rounded-xl hover:bg-surfaceHighlight/50 transition">
                    <span class="w-8 h-8 shrink-0 rounded-xl bg-danger/10 text-danger flex items-center justify-center">
                        <i data-lucide="flag" class="w-4 h-4"></i>
                    </span>
                    <span class="min-w-0 flex-1">
                        <span class="block truncate font-mono text-sm text-textMain">${esc(shortAddress(r.scam_wallet_address))}</span>
                        <span class="block truncate mt-0.5 text-[11px] text-textMuted">${meta}</span>
                    </span>
                    <span class="${PILL} ${cls}">${esc(t(statusKey, statusFb))}</span>
                    <i data-lucide="chevron-right" class="w-4 h-4 shrink-0 text-textMuted"></i>
                </a>
            </li>`;
        })
        .join('');
}

// ── 執行期 ──

// 輸出一律再經 esc()，所以插值不讓 i18next 先跳脫一次
function t(key, fallback, vars) {
    const options = vars ? { ...vars, interpolation: { escapeValue: false } } : undefined;
    if (window.I18n && typeof window.I18n.t === 'function') {
        const value = window.I18n.t(key, options);
        if (value && value !== key) return value;
    }
    return vars ? fallback.replace(/\{\{(\w+)\}\}/g, (m, k) => (k in vars ? vars[k] : m)) : fallback;
}

function lang() {
    try {
        return (window.I18n && window.I18n.getLanguage && window.I18n.getLanguage()) || 'en';
    } catch (_e) {
        return 'en';
    }
}

function refreshIcons(root) {
    if (!root) return;
    if (typeof window.createIconsIn === 'function') window.createIconsIn(root);
    else if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons();
}

const RECENT_TTL_MS = 5 * 60 * 1000;

const ScamCheckTab = {
    _listening: false,
    _address: '', // 最近一次送出查詢的地址（已驗證）
    _view: null, // null｜{ state:'loading' }｜mapCheckResult／errorView 的結果
    _seq: 0, // 連按兩次時只採用最新那次的回應
    _inputError: null,
    _busy: false,
    _recent: null,
    _recentAt: 0,

    init() {
        this._listenOnce();
        this.render();
        this._loadRecent();
        // 舊網址（/scam-tracker/?address=…）導過來：讀出來自動查，並從網址拿掉
        const { address, search } = takeAddressParam(window.location.search);
        if (search !== window.location.search) {
            try {
                history.replaceState(
                    history.state,
                    '',
                    window.location.pathname + search + window.location.hash
                );
            } catch (_e) {
                /* 網址改不了不影響查詢 */
            }
        }
        if (address) {
            const input = document.getElementById('scamcheck-input');
            if (input) input.value = address;
            this.check();
        }
    },

    _listenOnce() {
        if (this._listening || typeof window.addEventListener !== 'function') return;
        this._listening = true;
        // 語言切換：spa.js 不重跑本分頁（SELF_HANDLED），自己重畫動態內容
        window.addEventListener('languageChanged', () => {
            if (this._isVisible()) this.render();
        });
    },

    _isVisible() {
        const tab = document.getElementById('scamcheck-tab');
        return !!tab && !tab.classList.contains('hidden');
    },

    render() {
        this.renderResult();
        this.renderRecent();
        this._renderInputError();
        this._renderBusy();
    },

    renderResult() {
        const box = document.getElementById('scamcheck-result');
        if (!box) return;
        if (!this._view) {
            box.innerHTML = '';
            box.classList.add('hidden');
            return;
        }
        box.innerHTML =
            this._view.state === 'loading' ? loadingHtml(t) : resultHtml(this._view, this._address, t);
        box.classList.remove('hidden');
        refreshIcons(box);
    },

    renderRecent() {
        const section = document.getElementById('scamcheck-recent');
        const list = document.getElementById('scamcheck-recent-list');
        if (!section || !list) return;
        const html = recentHtml(this._recent, t, lang(), new Date());
        list.innerHTML = html;
        // 沒有舉報就整段不出現（不顯示「目前沒有舉報」這種看起來像棄置的空狀態）
        section.classList.toggle('hidden', !html);
        if (html) refreshIcons(section);
    },

    async _loadRecent() {
        if (this._recent && Date.now() - this._recentAt < RECENT_TTL_MS) return;
        try {
            if (!window.AppAPI) throw new Error('AppAPI unavailable');
            const data = await window.AppAPI.get('/api/scam-tracker/reports?limit=5&sort_by=latest', {
                retries: 1,
                _skipAuthGate: true,
            });
            this._recent = Array.isArray(data && data.reports) ? data.reports : [];
            this._recentAt = Date.now();
        } catch (_e) {
            // 列表只是附加資訊：失敗就不顯示，下次進分頁再試
            this._recent = null;
            this._recentAt = 0;
        }
        this.renderRecent();
    },

    _renderInputError() {
        const el = document.getElementById('scamcheck-input-error');
        const input = document.getElementById('scamcheck-input');
        if (!el) return;
        if (!this._inputError) {
            el.textContent = '';
            el.classList.add('hidden');
            if (input) input.removeAttribute('aria-invalid');
            return;
        }
        el.textContent =
            this._inputError === 'empty'
                ? t('scamcheck.errorEmpty', 'Paste an address to check.')
                : t(
                      'scamcheck.errorFormat',
                      "That doesn't look like a valid address. EVM addresses start with 0x followed by 40 hex characters."
                  );
        el.classList.remove('hidden');
        if (input) input.setAttribute('aria-invalid', 'true');
    },

    _renderBusy() {
        const btn = document.getElementById('scamcheck-submit');
        const label = document.getElementById('scamcheck-submit-label');
        if (btn) btn.disabled = this._busy;
        if (label) {
            // 同步 data-i18n：語言切換的全頁重掃才不會把「檢查中」洗回「檢查」（反之亦然）
            const key = this._busy ? 'scamcheck.checking' : 'scamcheck.check';
            label.dataset.i18n = key;
            label.textContent = t(key, this._busy ? 'Checking…' : 'Check');
        }
    },

    async check() {
        const input = document.getElementById('scamcheck-input');
        const parsed = validateAddress(input ? input.value : '');
        if (input && input.value !== parsed.value) input.value = parsed.value;
        this._inputError = parsed.ok ? null : parsed.error;
        this._renderInputError();
        if (!parsed.ok) {
            if (input) input.focus();
            return;
        }

        const seq = ++this._seq;
        this._address = parsed.value;
        this._view = { state: 'loading' };
        this._busy = true;
        this._renderBusy();
        this.renderResult();

        let view;
        try {
            if (!window.AppAPI) throw new Error('AppAPI unavailable');
            // GoPlus 單次最多 15 秒；不自動重試（公開端點 30 次／分鐘，重試會吃掉額度）
            const data = await window.AppAPI.get(
                `/api/scam-tracker/reports/check?address=${encodeURIComponent(parsed.value)}`,
                { retries: 0, timeout: 30000, _skipAuthGate: true }
            );
            view = mapCheckResult(data);
        } catch (err) {
            view = errorView(err);
        }
        if (seq !== this._seq) return;

        this._busy = false;
        this._renderBusy();
        if (view.state === 'invalid') {
            this._view = null;
            this._inputError = 'format';
            this._renderInputError();
        } else {
            this._view = view;
        }
        this.renderResult();
    },

    /** data-enter：Enter 送出（委派器傳入輸入框的值，這裡直接讀輸入框）。 */
    submitFromInput() {
        this.check();
    },

    tryExample() {
        const input = document.getElementById('scamcheck-input');
        if (input) input.value = EXAMPLE_ADDRESS;
        this.check();
    },

    retry() {
        const input = document.getElementById('scamcheck-input');
        if (input && this._address) input.value = this._address;
        this.check();
    },

    /** 切到聊天、把在地化的提問填進輸入框（不送出，讓使用者自己按）。 */
    async askAI() {
        const address = this._address;
        if (!address) return;
        const prompt = askPrompt(t, address);
        try {
            if (typeof window.switchTab === 'function') await window.switchTab('chat');
        } catch (_e) {
            /* 切換失敗仍嘗試填入 */
        }
        const input = document.getElementById('user-input');
        if (input) {
            input.value = prompt;
            input.focus();
        }
    },

    /** 登入：沿用既有舉報頁（帶地址預填）；訪客：開登入窗。 */
    report() {
        const address = this._address;
        if (!address) return;
        const loggedIn = !!(
            window.AuthManager &&
            typeof window.AuthManager.isLoggedIn === 'function' &&
            window.AuthManager.isLoggedIn()
        );
        if (!loggedIn) {
            const modal = document.getElementById('login-modal');
            if (modal) modal.classList.remove('hidden');
            return;
        }
        window.location.href = reportUrl(address);
    },
};

window.ScamCheckTab = ScamCheckTab;
export { ScamCheckTab };
