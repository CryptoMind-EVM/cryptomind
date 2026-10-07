/**
 * Trust Score Manager — 可信分數診斷面板（settings tab，僅自己可見）。
 *
 * 在 settings tab 載入時初始化。呼叫後端 GET /api/trust/score。
 * 分層揭露（design doc）：這裡顯示完整明細給本人；徽章外顯等 forum 開放才做。
 * 使用 fetch（同源 cookie session）。所有顯示經 _esc() HTML escape。
 */
import { errorFromResponse, isUserRejection, userFacingMessage } from './error-message.js';

window.TrustScoreManager = (function () {
    let state = null;
    // 可指定渲染 container（全面重組：Trust tab 用 trust-tab-body，Settings 舊版用 trust-score-body）。
    // 由 init(containerId) 設定；缺省維持舊行為（settings 內的 trust-score-body）。
    let _containerId = 'trust-score-body';

    function _esc(s) {
        if (s === null || s === undefined) return '';
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function _t(key) {
        return window.I18n ? window.I18n.t('trust.' + key) : key;
    }

    function _tierColor(score) {
        if (score >= 80) return 'rgb(34,197,94)'; // green
        if (score >= 60) return 'rgb(52,128,241)'; // blue
        if (score >= 30) return 'rgb(245,158,11)'; // amber
        return 'rgb(148,163,184)'; // slate
    }

    function _tierLabel(tier, fallback) {
        const map = {
            anonymous: _t('tierAnonymous'),
            known_wallet: _t('tierKnown'),
            soft_verified: _t('tierSoft'),
            strong_verified: _t('tierStrong'),
        };
        return map[tier] || fallback || tier;
    }

    /** signal 名稱翻譯:後端回英文 snake_case name,前端映射成使用者語言。 */
    function _signalLabel(name) {
        const map = {
            wallet_ownership: _t('signalOwnership'),
            wallet_age: _t('signalAge'),
            wallet_activity: _t('signalActivity'),
            soft_personhood: _t('signalSoft'),
            strong_personhood: _t('signalStrong'),
        };
        return map[name] || name;
    }

    /** signal detail 翻譯:後端 detail 是英文/數字格式(如 "365 days"、"50 txs"),
        前端翻譯成使用者語言,數字保留。 */
    function _signalDetail(s) {
        const d = s.detail || '';
        // "365 days" / "50 txs" 數字前綴格式
        const numMatch = d.match(/^(\d+)\s*(days|txs)$/);
        if (numMatch) {
            return numMatch[2] === 'days'
                ? _t('signalAgeDetail').replace('{{count}}', numMatch[1])
                : _t('signalActivityDetail').replace('{{count}}', numMatch[1]);
        }
        const map = {
            'wallet verified': _t('signalVerified'),
            // 舊 breakdown（2026-09-25 前）寫的是 TON 專屬字樣——同一個翻譯
            'TON proof verified': _t('signalVerified'),
            'no wallet proof': _t('signalNoProof'),
            missing_history: _t('signalMissingHistory'),
            // 舊版後端(7/29-8/9)無鏈上紀錄時寫的是 "unknown" — 映射成一樣的翻譯
            unknown: _t('signalMissingHistory'),
            'stamps: []': _t('signalNoStamps'),
        };
        if (map[d]) return map[d];
        // "stamps: [world_id, ens]" — 保留 stamp 列表,前綴翻譯
        const stampsMatch = d.match(/^stamps:\s*\[(.*)\]$/);
        if (stampsMatch) {
            return _t('signalStampsPrefix') + stampsMatch[1];
        }
        return d;
    }

    async function load() {
        const bodyEl = document.getElementById(_containerId);
        if (!bodyEl) return;
        // 外層容器（loading/error 狀態也用，對齊 render 的版面）
        const shell = '<div class="max-w-3xl mx-auto px-4 md:px-6 py-6">';
        try {
            bodyEl.innerHTML =
                shell + '<p class="text-sm text-textMuted text-center py-4">' + _t('loadingTrust') + '</p></div>';
            const resp = await fetch('/api/trust/score', { credentials: 'include' });
            if (resp.status === 503) {
                bodyEl.innerHTML =
                    shell + '<p class="text-sm text-textMuted text-center py-4">' + _t('disabled') + '</p></div>';
                return;
            }
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await resp.json();
            state = data;
            render();
        } catch (e) {
            bodyEl.innerHTML =
                shell + '<p class="text-sm text-danger text-center py-4">' + _t('loadFailed') + '</p></div>';
        }
    }

    function render() {
        const bodyEl = document.getElementById(_containerId);
        if (!bodyEl || !state) return;

        const score = state.trust_score || 0;
        const color = _tierColor(score);
        const label = _tierLabel(state.tier, state.badge ? state.badge.label_en : state.tier);

        // base_assessment 的 signals（來自 assess_identity_trust）
        const base = (state.breakdown && state.breakdown.base_assessment) || {};
        const signals = base.signals || [];

        // penalty
        const scam = (state.breakdown && state.breakdown.scam_penalty) || {};
        const penaltyHtml =
            scam.penalty > 0
                ? '<div class="flex items-center justify-between py-3">' +
                  '<span class="text-sm text-danger flex items-center gap-2">' +
                  '<i data-lucide="alert-triangle" class="w-4 h-4"></i>' + _t('scamPenalty') + '</span>' +
                  '<span class="font-medium text-danger tabular-nums">-' +
                  _esc(scam.penalty) +
                  '</span></div>'
                : '';

        // next steps（如何提升）——卡片內 list 版（無 border-t）
        const steps = state.next_steps || [];
        const stepsHtml = steps.length
            ? '<div class="space-y-2">' +
              steps
                  .map(function (st) {
                      const txt = {
                          onchain_history_missing: _t('stepOnchainMissing'),
                          verify_with_human_passport: _t('stepVerifyPassport'),
                          wallet_flagged_in_scam_db: _t('stepWalletFlagged'),
                      }[st];
                      return (
                          '<div class="flex items-start gap-2.5 rounded-xl bg-surfaceHighlight/50 p-3">' +
                          '<i data-lucide="sparkles" class="w-4 h-4 text-primary shrink-0 mt-0.5"></i>' +
                          '<p class="text-sm text-textSecondary leading-relaxed">' +
                          _esc(txt || st) +
                          '</p></div>'
                      );
                  })
                  .join('') +
              '</div>'
            : '<div class="rounded-2xl bg-success/5 border border-success/10 p-4 flex items-center gap-3">' +
              '<i data-lucide="check-circle-2" class="w-5 h-5 text-success shrink-0"></i>' +
              '<p class="text-sm text-success">' + _t('allSignalsActive') + '</p></div>';

        // 訊號明細（進度條版）——依 design system：tabular-nums、去飽和語意色
        const signalsDetailed = (base.signals || []).map(function (s) {
            const pct = Math.min(100, (s.score / (s.weight_applied || 1)) * 100);
            return (
                '<div class="py-3">' +
                '<div class="flex items-center justify-between mb-1.5">' +
                '<span class="text-sm text-textSecondary">' + _esc(_signalLabel(s.name)) + '</span>' +
                '<span class="text-sm font-medium tabular-nums text-textPrimary">+' + _esc(s.score) +
                '<span class="text-xs text-textMuted"> / ' + _esc(s.weight_applied) + '</span></span>' +
                '</div>' +
                '<div class="h-1.5 rounded-full bg-surfaceHighlight overflow-hidden">' +
                '<div class="h-full rounded-full" style="width:' + pct + '%;background:' + color + '"></div>' +
                '</div>' +
                (s.detail ? '<p class="text-xs text-textMuted mt-1">' + _esc(_signalDetail(s)) + '</p>' : '') +
                '</div>'
            );
        }).join('');

        // 徽章等級進度（下一個 tier 還差多少）
        const nextTier = _nextTierInfo(score);

        bodyEl.innerHTML =
            // 外層容器：限制內容寬度並統一卡片間距（對齊 settings/wallet 版面）
            '<div class="max-w-3xl mx-auto space-y-4 px-4 md:px-6 py-6">' +
            // ══ Hero 分數卡（儀表板主視覺）══
            '<div class="rounded-4xl bg-surface border border-borderSubtle p-6 md:p-8 shadow-[0_14px_36px_rgba(0,0,0,.12)]">' +
            '<div class="flex items-center gap-6 md:gap-8">' +
            // 大圓環（底圈用明顯實色，讓整圈輪廓完整；進度弧疊在上面）
            '<div class="relative w-28 h-28 md:w-32 md:h-32 flex items-center justify-center shrink-0">' +
            '<svg class="w-full h-full -rotate-90" viewBox="0 0 120 120">' +
            '<circle cx="60" cy="60" r="52" fill="none" stroke="#292D38" stroke-width="10"/>' +
            '<circle cx="60" cy="60" r="52" fill="none" stroke="' + color +
            '" stroke-width="10" stroke-linecap="round" stroke-dasharray="' +
            (2 * Math.PI * 52 * score) / 100 + ' ' + 2 * Math.PI * 52 + '"/>' +
            '</svg>' +
            '<div class="absolute inset-0 flex flex-col items-center justify-center">' +
            '<span class="text-4xl md:text-5xl font-serif font-semibold tabular-nums leading-none" style="color:' + color + '">' + _esc(score) + '</span>' +
            '<span class="text-xs text-textMuted mt-1 tracking-wider uppercase">/ 100</span>' +
            '</div>' +
            '</div>' +
            // 右側：tier + 徽章 + 下一級
            '<div class="flex-1 min-w-0">' +
            '<div class="flex items-center gap-2 mb-2">' +
            // 徽章盾牌（大）
            '<svg class="w-8 h-8" viewBox="0 0 24 24" fill="none" style="color:' + color + '">' +
            '<path d="M12 2L4 6v6c0 5 3.4 9.4 8 10 4.6-.6 8-5 8-10V6l-8-4z" fill="currentColor" opacity="0.15"/>' +
            '<path d="M12 2L4 6v6c0 5 3.4 9.4 8 10 4.6-.6 8-5 8-10V6l-8-4z" stroke="currentColor" stroke-width="1.5" fill="none"/>' +
            '<text x="12" y="15.5" text-anchor="middle" font-size="9" font-weight="bold" fill="currentColor">' + _esc(state.badge ? state.badge.badge_tier : 0) + '</text></svg>' +
            '<div>' +
            '<p class="text-xl md:text-2xl font-serif text-secondary leading-tight">' + _esc(label) + '</p>' +
            '<p class="text-xs text-textMuted">' + _esc(state.badge ? state.badge.label_en : '') + ' · ' +
            _esc(state.badge ? state.badge.label_zh : '') + '</p>' +
            '</div>' +
            '</div>' +
            // 下一級進度
            (nextTier
                ? '<div class="mt-3">' +
                  // 圓圈旁只有 ~125px：兩段文字各自不換行、擠不下就整段換到下一行
                  '<div class="flex flex-wrap items-center justify-between gap-x-3 text-xs mb-1">' +
                  '<span class="text-textMuted whitespace-nowrap">' + _t('nextTier') + ' ' + _esc(nextTier.label) + '</span>' +
                  '<span class="text-textMuted tabular-nums whitespace-nowrap">' + _esc(nextTier.remaining) + ' ' + _t('remaining') + '</span>' +
                  '</div>' +
                  '<div class="h-1.5 rounded-full bg-surfaceHighlight overflow-hidden">' +
                  '<div class="h-full rounded-full" style="width:' + nextTier.pct + '%;background:' + color + '"></div>' +
                  '</div>' +
                  '</div>'
                : '') +
            '<p class="text-xs text-textMuted mt-3">' + _t('updatedAt') + ' ' + _esc(state.computed_at || '—') + '</p>' +
            '</div>' +
            '</div>' +
            '</div>' +

            // ══ 訊號明細卡 ══
            '<div class="rounded-4xl bg-surface border border-borderSubtle p-6 mt-4">' +
            '<div class="flex items-center justify-between mb-2">' +
            '<h3 class="font-serif text-lg text-secondary">' + _t('signalDetails') + '</h3>' +
            '<span class="text-xs text-textMuted">' + _esc(signals.length) + ' ' + _t('signalCount') + '</span>' +
            '</div>' +
            '<div class="divide-y divide-borderSubtle/50">' +
            signalsDetailed +
            penaltyHtml +
            '</div>' +
            '</div>' +

            // ══ 如何提升卡 ══
            (stepsHtml ? '<div class="rounded-4xl bg-surface border border-borderSubtle p-6 mt-4">' +
            '<h3 class="font-serif text-lg text-secondary mb-3">' + _t('howToImprove') + '</h3>' + stepsHtml + '</div>' : '') +

            // ══ EVM 綁定卡 ══
            '<div class="rounded-4xl bg-surface border border-borderSubtle p-6 mt-4">' +
            _renderEvmBinding(state) +
            '</div>' +  // 關 EVM 綁定卡
            '</div>';  // 關外層 max-w 容器
        // render 後綁定按鈕才存在，此時接事件
        _wireEvmButtons();
        // render 注入的 <i data-lucide> 在 innerHTML 設定後才存在，這裡才觸發替換。
        // 不呼叫的話 icon 永遠是空 <i> 標籤（shell 的 inject 只 refresh 一次靜態 header）。
        if (window.AppUtils) AppUtils.refreshIcons(bodyEl);
    }

    function _nextTierInfo(score) {
        // tier 區間（與 trust.py 一致）：anonymous 0-29 / known 30-59 / soft 60-79 / strong 80+
        const tiers = [
            { min: 30, label: 'Basic' },
            { min: 60, label: 'Member' },
            { min: 80, label: 'Regular' },
            { min: 100, label: _t('maxTier') },
        ];
        for (const t of tiers) {
            if (score < t.min) {
                const remaining = t.min - score;
                const pct = Math.min(100, (score / t.min) * 100);
                return { label: t.label, remaining: remaining, pct: pct };
            }
        }
        return null; // 已滿分
    }

    function _renderEvmBinding(state) {
        // 判斷 Passport 訊號狀態
        var passport = (state.breakdown && state.breakdown.passport) || {};
        var reason = passport.reason || '';
        var boundAddr = passport.evm_address;

        // 卡片 header（獨立卡版本，無 border-t）
        var header =
            '<div class="flex items-center gap-3 mb-4">' +
            '<div class="w-10 h-10 rounded-xl flex items-center justify-center bg-primary/10">' +
            '<i data-lucide="fingerprint" class="w-5 h-5 text-primary"></i>' +
            '</div>' +
            '<div>' +
            '<h3 class="font-serif text-lg text-secondary leading-tight">' + (window.I18n ? window.I18n.t('trust.humanPassport') : 'Human Passport') + '</h3>' +
            '<p class="text-xs text-textMuted">' + _t('uniqueHuman') + '</p>' +
            '</div>' +
            '</div>';

        // 已綁定且通過
        if (reason === 'verified') {
            return header +
                '<div class="rounded-2xl bg-success/5 border border-success/10 p-4">' +
                '<div class="flex items-center justify-between">' +
                '<div class="flex items-center gap-3">' +
                '<div class="w-8 h-8 rounded-full bg-success/15 flex items-center justify-center">' +
                '<i data-lucide="check-circle" class="w-4 h-4 text-success"></i>' +
                '</div>' +
                '<div>' +
                '<p class="text-sm font-medium text-success">' + _t('verified') + '</p>' +
                '<p class="text-xs text-textMuted tabular-nums">' + _t('passportScore') + ' ' + _esc(passport.passport_score) + '</p>' +
                '</div>' +
                '</div>' +
                '<button id="btn-evm-unbind" class="text-xs px-3 py-1.5 rounded-full bg-surfaceHighlight text-textMuted hover:text-danger transition">' + _t('unbind') + '</button>' +
                '</div>' +
                '<p class="text-xs text-textMuted font-mono mt-3 break-all">' + _esc(boundAddr) + '</p>' +
                '</div>';
        }
        // 已綁定但分數不足
        if (reason === 'below_threshold') {
            return header +
                '<div class="rounded-2xl bg-amber-500/5 border border-amber-500/10 p-4">' +
                '<div class="flex items-center justify-between">' +
                '<div class="flex items-center gap-3">' +
                '<div class="w-8 h-8 rounded-full bg-amber-500/15 flex items-center justify-center">' +
                '<i data-lucide="clock" class="w-4 h-4 text-amber-400"></i>' +
                '</div>' +
                '<div>' +
                '<p class="text-sm font-medium text-amber-400">' + _t('boundLowScore') + '</p>' +
                '<p class="text-xs text-textMuted tabular-nums">' + _t('currentScore') + ' ' + _esc(passport.passport_score) + ' / ' + _t('needed') + '</p>' +
                '</div>' +
                '</div>' +
                '<button id="btn-evm-unbind" class="text-xs px-3 py-1.5 rounded-full bg-surfaceHighlight text-textMuted hover:text-danger transition">' + _t('unbind') + '</button>' +
                '</div>' +
                '<p class="text-xs text-textMuted mt-3">' + _t('raiseScoreHint') + '</p>' +
                '</div>';
        }
        // 已綁定但 API 失敗
        if (boundAddr && reason === 'passport_api_unavailable') {
            return header +
                '<div class="rounded-2xl bg-surfaceHighlight/50 p-4">' +
                '<div class="flex items-center justify-between">' +
                '<p class="text-sm text-textMuted">' + _t('boundNoConn') + '</p>' +
                '<button id="btn-evm-unbind" class="text-xs px-3 py-1.5 rounded-full bg-surfaceHighlight text-textMuted hover:text-danger transition">' + _t('unbind') + '</button>' +
                '</div>' +
                '<p class="text-xs text-textMuted font-mono mt-2 break-all">' + _esc(boundAddr) + '</p>' +
                '</div>';
        }
        // 未綁定 → 顯示綁定按鈕(即使 TG WebView 裡 window.ethereum 不存在也顯示按鈕,
        // 點了再提示——因為 TG WebView 抓不到 MetaMask,但不能因此藏住按鈕)
        return header +
            '<button id="btn-evm-bind" class="w-full py-3 rounded-2xl bg-primary/10 text-primary font-medium hover:bg-primary/20 transition flex items-center justify-center gap-2">' +
            '<i data-lucide="plus" class="w-4 h-4"></i> ' + _t('bindEvm') + '</button>' +
            '<p class="text-xs text-textMuted text-center mt-3">' + _t('passportHint') + '</p>';
    }

    async function bindEvm() {
        var btn = document.getElementById('btn-evm-bind');
        if (!btn) return;
        // TG WebView / 無 MetaMask 環境:提示使用者改用瀏覽器開(不靜默 return)
        if (!window.ethereum) {
            if (typeof window.showInfoDialog === 'function') {
                await window.showInfoDialog({
                    title: _t('bindEvm'),
                    message: _t('installMetamask'),
                    tone: 'warning',
                    confirmText: _t('gotIt'),
                });
            } else {
                alert(_t('installMetamask'));
            }
            return;
        }
        btn.disabled = true;
        btn.textContent = _t('bindRequestingNonce');
        try {
            // 1. 取 nonce
            var nonceResp = await fetch('/api/trust/evm/nonce', { credentials: 'include' });
            if (!nonceResp.ok) throw await errorFromResponse(nonceResp);
            var nonceData = await nonceResp.json();

            // 2. 請 MetaMask 簽章
            btn.textContent = _t('bindSignMetamask');
            var provider = new window.ethers.providers.Web3Provider(window.ethereum);
            var accounts = await provider.send('eth_requestAccounts', []);
            var signer = provider.getSigner();
            var signature = await signer.signMessage(nonceData.message);

            // 3. 送綁定
            btn.textContent = _t('bindVerifying');
            var bindResp = await fetch('/api/trust/evm/bind', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    evm_address: accounts[0],
                    signature: signature,
                    payload: nonceData.payload,
                }),
            });
            if (bindResp.status === 403) throw Object.assign(new Error(_t('bindSignatureFailed')), { status: 403 });
            if (bindResp.status === 409) throw Object.assign(new Error(_t('bindAlreadyBound')), { status: 409 });
            if (!bindResp.ok) throw await errorFromResponse(bindResp);

            // 4. 重算 + 重載面板
            btn.textContent = _t('bindRecomputing');
            await fetch('/api/trust/recompute', { method: 'POST', credentials: 'include' });
            await load();
        } catch (e) {
            btn.disabled = false;
            btn.textContent = '+ ' + _t('bindEvm');
            // 使用者在錢包按了取消／拒絕（ethers v5 的 signMessage 不替我們分辨）：不是錯誤，給一則中性提示
            if (isUserRejection(e)) {
                if (typeof window.showToast === 'function') window.showToast(_t('bindCancelled'), 'info');
                return;
            }
            // e.message 是 'Failed to fetch'／'nonce HTTP 502' 這類英文——過顯示層，不直接貼給人看
            var message = userFacingMessage(e);
            if (typeof window.showInfoDialog === 'function') {
                await window.showInfoDialog({
                    title: _t('bindFailed'),
                    message: message,
                    tone: 'error',
                });
            } else {
                alert(_t('bindFailed') + ' ' + message);
            }
        }
    }

    async function unbindEvm() {
        var proceed = false;
        if (typeof window.showConfirmDialog === 'function') {
            proceed = await window.showConfirmDialog({
                title: _t('unbindTitle'),
                message: _t('unbindConfirm'),
                confirmText: _t('unbind'),
                danger: true,
            });
        } else {
            proceed = confirm(_t('unbindConfirm'));
        }
        if (!proceed) return;
        try {
            var resp = await fetch('/api/trust/evm/unbind', { method: 'DELETE', credentials: 'include' });
            if (!resp.ok) throw await errorFromResponse(resp);
            await fetch('/api/trust/recompute', { method: 'POST', credentials: 'include' });
            await load();
        } catch (e) {
            var message = userFacingMessage(e);
            if (typeof window.showInfoDialog === 'function') {
                await window.showInfoDialog({
                    title: _t('unbindFailed'),
                    message: message,
                    tone: 'error',
                });
            } else {
                alert(_t('unbindFailed') + ' ' + message);
            }
        }
    }

    function _wireEvmButtons() {
        var bindBtn = document.getElementById('btn-evm-bind');
        if (bindBtn) bindBtn.addEventListener('click', bindEvm);
        var unbindBtn = document.getElementById('btn-evm-unbind');
        if (unbindBtn) unbindBtn.addEventListener('click', unbindEvm);
    }

    function init(containerId) {
        if (containerId) _containerId = containerId;
        const btn = document.getElementById('btn-refresh-trust-score');
        if (btn) {
            btn.addEventListener('click', function () {
                btn.disabled = true;
                load().finally(function () {
                    setTimeout(function () {
                        btn.disabled = false;
                    }, 800);
                });
            });
        }
        // 初始化即載入（tab 首次開啟就抓資料）
        load();
    }

    return { load: load, init: init, render: render, bindEvm: bindEvm, unbindEvm: unbindEvm };
})();
