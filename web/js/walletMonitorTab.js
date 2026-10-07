/**
 * Wallet Monitor Tab — 錢包監測 dashboard
 *
 * 持倉總覽 + 最近事件（轉入/轉出）+ 可配置警示設定 + 新增/移除監測錢包。
 * 用戶可監測任何 TON 地址（自己或他人——鏈上資料公開），自選監測原因。
 * 使用 fetch（同源 cookie session）。所有顯示經 _esc()。
 *
 * 重新設計對齊 Trust tab（rounded-4xl + shadow + max-w-3xl），i18n 走 walletMonitor.*。
 */
import { errorFromResponse, userFacingMessage } from './error-message.js';

window.WalletMonitorTab = (function () {
    let overviewData = null;
    let settings = null;
    // Telegram 綁定狀態(null = 尚未檢查 / false = 未綁定 / true = 已綁定)。
    // 未綁定時 telegram 通知 channel 不給勾選。
    let telegramBound = null;

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
        return window.I18n ? window.I18n.t('walletMonitor.' + key) : key;
    }

    function _short(addr) {
        if (!addr) return '';
        return addr.length > 14 ? addr.slice(0, 6) + '...' + addr.slice(-4) : addr;
    }

    function _fmtTime(ts) {
        if (!ts) return '—';
        const d = new Date(ts * 1000);
        const p = (n) => String(n).padStart(2, '0');
        return p(d.getMonth() + 1) + '/' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
    }

    function _fmtTon(n) {
        return (Number(n) || 0).toLocaleString('en-US', { maximumFractionDigits: 2 }) + ' TON';
    }

    /** 取得登入錢包地址（給「我的錢包」快捷鈕用）。 */
    function _loginWallet() {
        try {
            var u = (window.AuthManager && window.AuthManager.currentUser) || {};
            return u.wallet_address || u.user_id || '';
        } catch (e) {
            return '';
        }
    }

    async function load() {
        const body = document.getElementById('wallet-monitor-body');
        if (!body) return;
        try {
            // 抓 overview(含監測錢包清單 + 餘額)。最近事件改在各錢包明細頁看,
            // 首頁只列錢包清單——避免多錢包事件混在一起。
            const ovResp = await fetch('/api/wallet-monitor/overview', { credentials: 'include' });
            if (ovResp.status === 503) {
                body.innerHTML =
                    '<p class="text-sm text-textMuted text-center py-8">' + _t('disabled') + '</p>';
                return;
            }
            const ov = await ovResp.json();
            overviewData = ov;
            settings = (ov && ov.alert_settings) || null;

            // 查 Telegram 綁定狀態(決定 telegram channel 可否勾選)。失敗不阻擋渲染。
            try {
                const tgResp = await fetch('/api/telegram/status', { credentials: 'include' });
                if (tgResp.ok) {
                    const tg = await tgResp.json();
                    telegramBound = Boolean(tg && tg.bound);
                } else {
                    telegramBound = false;
                }
            } catch (e) {
                telegramBound = false;
            }

            render();
        } catch (e) {
            body.innerHTML =
                '<p class="text-sm text-danger text-center py-8">' + _t('loadFailed') + '</p>';
        }
    }

    /** 卡片樣式（對齊 Trust tab 的 rounded-4xl + shadow）。 */
    const _CARD =
        'rounded-4xl bg-surface border border-borderSubtle p-6 shadow-[0_14px_36px_rgba(0,0,0,.12)]';
    const _CARD_MT = _CARD + ' mt-4';

    /**
     * 升級橫幅：只在 gate 開啟且使用者無排程監測權限（Free／過期）時顯示。
     * gate 關閉時不顯示（功能尚未限制，顯示反而誤導）。
     */
    function _renderUpgradeBanner() {
        var ent = overviewData && overviewData.entitlement;
        var gateOn = overviewData && overviewData.monitoring_gate_enabled;
        if (!gateOn || !ent || ent.can_use_scheduled_monitoring) return '';
        return (
            '<div class="' + _CARD + '" style="border-color:var(--primary)">' +
            '<div class="flex items-start gap-3">' +
            '<div class="w-9 h-9 rounded-xl bg-primary/15 flex items-center justify-center shrink-0">' +
            '<i data-lucide="lock" class="w-4 h-4 text-primary"></i></div>' +
            '<div class="flex-1 min-w-0">' +
            '<p class="text-sm font-medium text-secondary">' + _esc(_t('upgradeTitle')) + '</p>' +
            '<p class="text-xs text-textMuted mt-1 leading-relaxed">' + _esc(_t('upgradeDesc')) + '</p>' +
            '<a href="/static/forum/premium.html" class="inline-block mt-2.5 px-3 py-1.5 rounded-lg bg-primary text-background text-xs font-bold hover:brightness-110 transition">' +
            _esc(_t('upgradeCta')) + '</a>' +
            '</div></div></div>'
        );
    }

    /** 解析 wallet-monitor API 失敗回應；回傳 upgrade_required 時顯示升級提示。 */
    async function _isUpgradeBlock(resp) {
        if (resp.status !== 403) return false;
        // clone：body 只能讀一次，讀完的話後面 errorFromResponse 就拿不到 detail
        var body = await resp.clone().json().catch(function () { return {}; });
        var d = body && body.detail;
        return !!(d && typeof d === 'object' && d.upgrade_required);
    }

    function render() {
        const body = document.getElementById('wallet-monitor-body');
        if (!body) return;

        // ── 總覽卡片(錢包清單 — 點擊進明細)──
        const wallets = (overviewData && overviewData.wallets) || [];
        const walletHtml = wallets.length
            ? wallets
                  .map(function (w) {
                      // 自訂名稱優先顯示(大字),地址其次(小字 mono)
                      var label = w.label || _short(w.address);
                      var addrLine = w.label ? _short(w.address) : '';
                      var reasonTag = w.reason
                          ? '<span class="text-[10px] text-primary/80 ml-1.5">' + _esc(_t(w.reason)) + '</span>'
                          : '';
                      return (
                          '<div class="flex items-center justify-between py-3 border-b border-borderSubtle/50 last:border-0 cursor-pointer hover:bg-surfaceHighlight/50 rounded-xl px-2.5 -mx-2.5 transition group" data-wallet-detail="' + _esc(w.address) + '">' +
                          '<div class="min-w-0 flex-1">' +
                          '<div class="flex items-center gap-2 min-w-0">' +
                          '<div class="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center text-primary shrink-0">' +
                          '<i data-lucide="wallet" class="w-4 h-4"></i>' +
                          '</div>' +
                          '<div class="min-w-0">' +
                          '<p class="text-sm font-medium text-textPrimary truncate">' + _esc(label) + '</p>' +
                          (addrLine
                              ? '<p class="text-xs text-textMuted font-mono truncate">' + _esc(addrLine) + '</p>'
                              : '') +
                          '</div>' +
                          reasonTag +
                          '</div>' +
                          '</div>' +
                          '<div class="flex items-center gap-1 shrink-0 ml-3">' +
                          '<div class="text-right">' +
                          '<p class="text-sm font-medium text-textPrimary tabular-nums">' +
                          _esc(_fmtTon(w.balance_ton)) + (w.symbol ? ' ' + _esc(w.symbol) : '') +
                          '</p>' +
                          '<p class="text-[10px] text-textMuted uppercase">' + _esc(w.chain || 'ton') + '</p>' +
                          '<p class="text-[10px] text-textMuted">' + _t('viewDetail') + '</p>' +
                          '</div>' +
                          '<i data-lucide="chevron-right" class="w-4 h-4 text-textMuted group-hover:text-primary transition"></i>' +
                          '</div></div>'
                      );
                  })
                  .join('')
            : '<p class="text-sm text-textMuted py-2">' + _t('noWallets') + '</p>';

        // ── 警示設定區塊 ──
        const settingsHtml = _renderSettings();

        body.innerHTML =
            '<div class="max-w-3xl mx-auto space-y-4 px-4 md:px-6 py-6">' +
            _renderUpgradeBanner() +
            // 錢包清單卡片
            '<div class="' + _CARD + '">' +
            '<div class="flex items-center justify-between mb-2">' +
            '<div class="flex items-center gap-2">' +
            '<i data-lucide="wallet" class="w-5 h-5 text-primary"></i>' +
            '<h3 class="font-serif text-lg text-secondary">' + _t('overview') + '</h3>' +
            '</div>' +
            '</div>' +
            '<p class="text-xs text-textMuted mb-1">' + _t('clickToDetail') + '</p>' +
            walletHtml +
            '</div>' +
            // 警示設定
            settingsHtml +
            '</div>';

        _wireSettingsButtons();
        // render 後刷新注入的 lucide icon(同 Trust tab 修法)
        if (window.AppUtils) AppUtils.refreshIcons(body);
    }

    // ── 警示設定（用戶自己選）+ 新增監測錢包 ──

    function _renderSettings() {
        const s = settings || {};
        const alerts = s.alerts || {};
        const channels = s.channels || { in_app: true, telegram: true, discord: false };
        // monitored_wallets 可能是舊格式(純字串)或新格式({address,label,reason}),正規化
        const wallets = (s.monitored_wallets || []).map(function (w) {
            if (typeof w === 'string') return { address: w, label: '', reason: '' };
            return { address: w.address || '', label: w.label || '', reason: w.reason || '' };
        });

        function toggle(id, cfg, label, sub) {
            const on = cfg && cfg.enabled;
            return (
                '<div class="flex items-start justify-between py-2 border-b border-borderSubtle/50 last:border-0">' +
                '<div>' +
                '<p class="text-sm text-textPrimary">' + label + '</p>' +
                (sub ? '<p class="text-xs text-textMuted">' + sub + '</p>' : '') +
                '</div>' +
                '<label class="inline-flex items-center justify-center w-11 h-11 -my-3 -mr-2 cursor-pointer shrink-0">' +
                '<input type="checkbox" data-alert-key="' + id + '" ' + (on ? 'checked' : '') +
                ' class="w-5 h-5 accent-primary">' +
                '</label></div>'
            );
        }

        // telegram 未綁定 → checkbox disabled 不可勾選,顯示提示(不誤導使用者)
        const tgBound = telegramBound === true;
        const tgLabel = tgBound
            ? 'Telegram'
            : _t('chTelegramNotBound');
        const channelsHtml =
            '<div class="flex gap-3 mt-2">' +
            '<label class="text-xs text-textSecondary flex items-center gap-1.5 py-2">' +
            '<input type="checkbox" data-channel-key="in_app" ' + (channels.in_app !== false ? 'checked' : '') +
            ' class="w-4 h-4 accent-primary"> ' + _t('chInApp') + '</label>' +
            '<label class="text-xs ' + (tgBound ? 'text-textSecondary' : 'text-textMuted') + ' flex items-center gap-1.5 py-2">' +
            '<input type="checkbox" data-channel-key="telegram" ' + (channels.telegram && tgBound ? 'checked' : '') +
            (tgBound ? '' : ' disabled') +
            ' class="w-4 h-4 accent-primary"> ' + tgLabel + '</label>' +
            '</div>' +
            (tgBound ? '' : '<p class="text-[11px] text-textMuted mt-1.5">' + _t('chTelegramHint') + '</p>');

        // 新增錢包區塊(B1)
        // 註:不再有獨立的 reason 欄位——自訂原因統一由上方警示設定的
        // 「其他」勾勾控制(勾了才顯示輸入框),避免兩處重複衝突。
        const addWalletHtml =
            '<div class="mt-3 pt-3 border-t border-borderSubtle/50">' +
            '<p class="text-xs font-bold text-textMuted uppercase tracking-wider mb-2">' + _t('addWallet') + '</p>' +
            '<p class="text-[11px] text-textMuted/80 mb-2">' + _t('addWalletHint') + '</p>' +
            '<div class="space-y-2">' +
            '<div class="flex gap-2">' +
            '<select id="wm-add-chain" class="bg-background/50 border border-borderSubtle rounded-xl px-2 py-2 text-sm text-secondary focus:outline-none focus:border-primary/40 transition">' +
            '<option value="ton">TON</option>' +
            '<option value="eth">Ethereum</option>' +
            '<option value="polygon">Polygon</option>' +
            '<option value="arbitrum">Arbitrum</option>' +
            '</select>' +
            '<input id="wm-add-address" type="text" placeholder="EQ... / UQ... / 0x..." data-i18n="walletMonitor.addressPlaceholder" data-i18n-attr="placeholder" ' +
            'class="flex-1 bg-background/50 border border-borderSubtle rounded-xl px-3 py-2 text-sm text-secondary focus:outline-none focus:border-primary/40 transition font-mono" />' +
            '</div>' +
            '<input id="wm-add-label" type="text" placeholder="' + _t('labelPlaceholder') + '" ' +
            'class="w-full bg-background/50 border border-borderSubtle rounded-xl px-3 py-2 text-sm text-secondary focus:outline-none focus:border-primary/40 transition" />' +
            '<div class="flex items-center gap-2">' +
            '<button id="btn-add-wallet" class="flex-1 px-4 py-2 rounded-xl bg-primary text-background text-sm font-medium hover:bg-primary/90 transition">' + _t('addBtn') + '</button>' +
            '<button id="btn-fill-my-wallet" class="px-3 py-2 rounded-xl bg-primary/10 text-primary text-xs font-medium hover:bg-primary/20 transition">' + _t('myWallet') + '</button>' +
            '</div>' +
            '</div>' +
            '</div>';

        // 「其他」勾勾:勾了才顯示自訂原因輸入框(統一替代新增錢包的 reason 欄位)
        // 注意:輸入框放 toggle 容器「內」(mt-2 往下推),不用負 margin,
        // 避免手機上跟勾勾列重疊遮擋。
        const otherOn = Boolean(alerts.other && alerts.other.enabled);
        const otherReason = (alerts.other && alerts.other.reason) || '';
        const otherHtml =
            '<div class="py-2 border-b border-borderSubtle/50 last:border-0">' +
            '<div class="flex items-start justify-between">' +
            '<div>' +
            '<p class="text-sm text-textPrimary">✏️ ' + _t('other') + '</p>' +
            '<p class="text-xs text-textMuted">' + _t('otherSub') + '</p>' +
            '</div>' +
            '<label class="inline-flex items-center justify-center w-11 h-11 -my-3 cursor-pointer shrink-0 ml-2">' +
            '<input type="checkbox" data-alert-key="other" ' + (otherOn ? 'checked' : '') +
            ' class="w-5 h-5 accent-primary">' +
            '</label></div>' +
            (otherOn
                ? '<div class="mt-2">' +
                  '<input id="wm-other-reason" type="text" maxlength="60" value="' + _esc(otherReason) + '" ' +
                  'placeholder="' + _t('reasonPlaceholder') + '" ' +
                  'class="w-full bg-background/50 border border-borderSubtle rounded-xl px-3 py-2 text-sm text-secondary focus:outline-none focus:border-primary/40 transition" />' +
                  '</div>'
                : '') +
            '</div>';

        return (
            '<div class="' + _CARD_MT + '">' +
            '<div class="flex items-center justify-between mb-3">' +
            '<div class="flex items-center gap-2">' +
            '<i data-lucide="bell" class="w-5 h-5 text-primary"></i>' +
            '<h3 class="font-serif text-lg text-secondary">' + _t('alertSettings') + '</h3>' +
            '</div>' +
            '<button id="btn-save-alert-settings" class="text-xs px-3 py-1.5 rounded-lg bg-primary/10 text-primary hover:bg-primary/20 transition">' + _t('save') + '</button>' +
            '</div>' +
            '<p class="text-xs text-textMuted mb-2">' + _t('alertHint') + '</p>' +
            toggle('incoming', alerts.incoming, '📥 ' + _t('incoming'), _t('incomingSub')) +
            toggle('outgoing', alerts.outgoing, '📤 ' + _t('outgoing'), _t('outgoingSub')) +
            toggle('scam', alerts.scam, '⚠️ ' + _t('scamAlert'), _t('scamSub')) +
            toggle('large_out', alerts.large_out, '🚨 ' + _t('largeOut'), _t('largeOutSub')) +
            otherHtml +
            '<div class="mt-2 pt-2 border-t border-borderSubtle/50">' +
            '<p class="text-xs text-textSecondary mb-1">' + _t('pushChannels') + '</p>' +
            channelsHtml +
            '</div>' +
            '<div class="mt-2 pt-2 border-t border-borderSubtle/50">' +
            '<p class="text-xs text-textSecondary mb-1">' + _t('monitoredWallets') + '</p>' +
            (wallets.length
                ? wallets
                      .map(function (w) {
                          return '<div class="flex items-center justify-between py-1">' +
                              '<div class="min-w-0">' +
                              '<span class="text-xs font-mono text-textMuted">' + _esc(_short(w.address)) + '</span>' +
                              (w.label ? '<span class="text-xs text-textSecondary ml-2">' + _esc(w.label) + '</span>' : '') +
                              (w.reason ? '<span class="text-[10px] text-primary/80 ml-1">(' + _esc(_t(w.reason)) + ')</span>' : '') +
                              '</div>' +
                              '<button data-remove-wallet="' + _esc(w.address) + '" class="text-xs text-textMuted hover:text-danger shrink-0 ml-2">✕</button>' +
                              '</div>';
                      })
                      .join('')
                : '<p class="text-xs text-textMuted">' + _t('noExtraWallets') + '</p>') +
            '</div>' +
            addWalletHtml +
            '</div>'
        );
    }

    function _wireSettingsButtons() {
        const saveBtn = document.getElementById('btn-save-alert-settings');
        if (saveBtn) saveBtn.addEventListener('click', saveSettings);
        document.querySelectorAll('[data-remove-wallet]').forEach(function (btn) {
            btn.addEventListener('click', function (e) {
                e.stopPropagation();
                removeWallet(btn.getAttribute('data-remove-wallet'));
            });
        });
        var addBtn = document.getElementById('btn-add-wallet');
        if (addBtn) addBtn.addEventListener('click', addWallet);
        var myBtn = document.getElementById('btn-fill-my-wallet');
        if (myBtn) myBtn.addEventListener('click', function () {
            var addr = _loginWallet();
            var inp = document.getElementById('wm-add-address');
            if (addr && inp) inp.value = addr;
        });
        // 「其他」勾勾:勾選/取消時即時重渲染(顯示/隱藏自訂原因輸入框)
        var otherBox = document.querySelector('[data-alert-key="other"]');
        if (otherBox) {
            otherBox.addEventListener('change', function () {
                if (settings && settings.alerts) {
                    if (!settings.alerts.other) settings.alerts.other = { enabled: false, reason: '' };
                    settings.alerts.other.enabled = otherBox.checked;
                }
                render();
            });
        }
        // 點擊錢包行 → 明細檢視
        document.querySelectorAll('[data-wallet-detail]').forEach(function (row) {
            row.addEventListener('click', function () {
                showDetail(row.getAttribute('data-wallet-detail'));
            });
        });
    }

    // ── 錢包明細檢視 ──

    async function showDetail(address) {
        var body = document.getElementById('wallet-monitor-body');
        if (!body) return;
        // Loading 狀態
        body.innerHTML = '<div class="max-w-3xl mx-auto px-4 md:px-6 py-6">' +
            '<p class="text-sm text-textMuted text-center py-8">' + _t('loading') + '</p></div>';
        try {
            var resp = await fetch('/api/wallet-monitor/wallet/' + encodeURIComponent(address) + '/detail', {
                credentials: 'include',
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            var data = await resp.json();
            if (data.success) {
                renderDetail(data);
            }
        } catch (e) {
            // 失敗回到列表
            load();
        }
    }

    function renderDetail(d) {
        var body = document.getElementById('wallet-monitor-body');
        if (!body) return;

        // ── 標頭 ──（eyebrow label + 大標 + 地址 + 右側大數字 USD 估值）
        var labelOrAddr = d.label || _short(d.address);
        var usdStr = d.ton_usd_value != null
            ? _esc(d.ton_usd_value.toLocaleString('en-US', { maximumFractionDigits: 0 }))
            : '';
        var header =
            '<div class="' + _CARD + '">' +
            '<button id="btn-wm-back" class="flex items-center gap-1 text-sm text-textMuted hover:text-primary transition mb-6 -ml-1">' +
            '<i data-lucide="arrow-left" class="w-4 h-4"></i> ' + _t('back') + '</button>' +
            '<div class="flex items-start justify-between gap-4">' +
            '<div class="min-w-0">' +
            (d.label ? '<p class="text-[11px] font-semibold uppercase tracking-wider text-textMuted mb-1">' + _esc(d.label) + '</p>' : '') +
            '<h3 class="text-xl font-serif text-secondary truncate">' + _esc(labelOrAddr) + '</h3>' +
            '<p class="text-xs text-textMuted font-mono mt-1.5 break-all">' + _esc(d.address) + '</p>' +
            (d.reason ? '<span class="inline-flex items-center gap-1 text-[11px] text-primary/80 mt-2 px-2 py-0.5 rounded-full bg-primary/10">' + _esc(_t(d.reason)) + '</span>' : '') +
            '</div>' +
            '<div class="text-right shrink-0">' +
            '<p class="text-2xl font-serif font-semibold text-secondary tabular-nums tracking-tight">' +
            _esc(d.ton_balance != null ? _fmtTon(d.ton_balance) : '—') + '</p>' +
            (usdStr ? '<p class="text-xs text-textMuted tabular-nums mt-0.5">≈ $' + usdStr + ' USD</p>' : '') +
            '</div>' +
            '</div>' +
            '</div>';

        // ── 持倉 ──（token symbol 大字 + 名稱小字,右側數量主要 + USD 次要,行背景+圓角區隔）
        var tonUsd = d.ton_usd_value != null && d.ton_balance != null
            ? '$' + _esc(Number(d.ton_usd_value).toLocaleString('en-US', { maximumFractionDigits: 2 }))
            : '';
        var tonRow =
            '<div class="flex items-center justify-between rounded-xl bg-surfaceHighlight/40 px-3 py-2.5">' +
            '<div class="flex items-center gap-2.5">' +
            '<div class="w-8 h-8 rounded-full bg-primary/15 flex items-center justify-center text-primary text-xs font-bold">TON</div>' +
            '<div>' +
            '<p class="text-sm font-semibold text-textPrimary">Toncoin</p>' +
            '</div>' +
            '</div>' +
            '<div class="text-right">' +
            '<p class="text-sm font-medium text-textPrimary tabular-nums">' + _esc(d.ton_balance != null ? d.ton_balance : '—') + '</p>' +
            (tonUsd ? '<p class="text-[11px] text-textMuted tabular-nums">' + tonUsd + '</p>' : '') +
            '</div>' +
            '</div>';
        var jettonRows = (d.jettons || []).map(function (j) {
            var sym = _esc(j.symbol || '?');
            var symInitials = sym.length > 4 ? sym.slice(0, 3) : sym;
            var usd = j.usd_value != null
                ? '<p class="text-[11px] text-textMuted tabular-nums">$' + _esc(Number(j.usd_value).toLocaleString('en-US', { maximumFractionDigits: 2 })) + '</p>'
                : '';
            return '<div class="flex items-center justify-between rounded-xl bg-surfaceHighlight/40 px-3 py-2.5">' +
                '<div class="flex items-center gap-2.5 min-w-0">' +
                '<div class="w-8 h-8 rounded-full bg-textMuted/15 flex items-center justify-center text-textSecondary text-[10px] font-bold shrink-0">' + symInitials + '</div>' +
                '<div class="min-w-0">' +
                '<p class="text-sm font-semibold text-textPrimary truncate">' + sym + '</p>' +
                (j.name ? '<p class="text-[11px] text-textMuted truncate">' + _esc(j.name) + '</p>' : '') +
                '</div>' +
                '</div>' +
                '<div class="text-right shrink-0 ml-2">' +
                '<p class="text-sm font-medium text-textPrimary tabular-nums">' + _esc(j.balance || '0') + '</p>' +
                usd +
                '</div>' +
                '</div>';
        }).join('');
        var holdings =
            '<div class="' + _CARD_MT + '">' +
            _sectionHeader('coins', _t('holdings'), (d.jettons && d.jettons.length ? (d.jettons.length + 1) : 1) + '') +
            '<div class="space-y-1.5">' +
            tonRow +
            (jettonRows || '<p class="text-xs text-textMuted py-3 text-center">' + _t('noJettons') + '</p>') +
            '</div>' +
            '</div>';

        // ── 流入/流出統計 ──（比例條 + 大數字 + 淨額 hero 數字）
        var inflow = Number(d.inflow_ton || 0);
        var outflow = Number(d.outflow_ton || 0);
        var total = inflow + outflow;
        var inflowPct = total > 0 ? Math.round((inflow / total) * 100) : 0;
        var outflowPct = total > 0 ? 100 - inflowPct : 0;
        var netPositive = d.net_flow_ton >= 0;
        var netSign = netPositive ? '+' : '−'; // U+2212 minus sign (more readable than ASCII -)
        var stats =
            '<div class="' + _CARD_MT + '">' +
            _sectionHeader('bar-chart-3', _t('flowStats')) +
            '<div class="grid grid-cols-2 gap-3">' +
            '<div class="rounded-xl bg-success/5 border border-success/15 p-3.5">' +
            '<div class="flex items-center gap-1.5 mb-2">' +
            '<i data-lucide="arrow-down-to-line" class="w-3.5 h-3.5 text-success"></i>' +
            '<p class="text-[11px] font-medium text-success">' + _t('inflow') + '</p>' +
            '</div>' +
            '<p class="text-lg font-bold text-success tabular-nums tracking-tight">+' + _esc(inflow.toFixed(2)) + '</p>' +
            '<p class="text-[10px] text-textMuted mt-0.5">' + _esc(d.inflow_count) + ' ' + _t('txCount') + '</p>' +
            '</div>' +
            '<div class="rounded-xl bg-danger/5 border border-danger/15 p-3.5">' +
            '<div class="flex items-center gap-1.5 mb-2">' +
            '<i data-lucide="arrow-up-from-line" class="w-3.5 h-3.5 text-danger"></i>' +
            '<p class="text-[11px] font-medium text-danger">' + _t('outflow') + '</p>' +
            '</div>' +
            '<p class="text-lg font-bold text-danger tabular-nums tracking-tight">−' + _esc(outflow.toFixed(2)) + '</p>' +
            '<p class="text-[10px] text-textMuted mt-0.5">' + _esc(d.outflow_count) + ' ' + _t('txCount') + '</p>' +
            '</div>' +
            '</div>' +
            // 比例條（流入 vs 流出）
            (total > 0
                ? '<div class="mt-3">' +
                  '<div class="flex h-1.5 rounded-full overflow-hidden bg-surfaceHighlight">' +
                  '<div class="bg-success transition-all" style="width:' + inflowPct + '%"></div>' +
                  '<div class="bg-danger transition-all" style="width:' + outflowPct + '%"></div>' +
                  '</div>' +
                  '<div class="flex justify-between mt-1.5">' +
                  '<span class="text-[10px] text-success tabular-nums">' + inflowPct + '%</span>' +
                  '<span class="text-[10px] text-danger tabular-nums">' + outflowPct + '%</span>' +
                  '</div>' +
                  '</div>'
                : '') +
            // 淨額（hero）
            '<div class="mt-3 flex items-center justify-between rounded-xl p-3.5 ' +
            (netPositive ? 'bg-success/10 border border-success/15' : 'bg-danger/10 border border-danger/15') + '">' +
            '<span class="text-xs font-medium ' + (netPositive ? 'text-success' : 'text-danger') + '">' + _t('netFlow') + '</span>' +
            '<span class="text-base font-bold tabular-nums tracking-tight ' + (netPositive ? 'text-success' : 'text-danger') + '">' +
            netSign + _esc(Math.abs(d.net_flow_ton).toFixed(2)) + ' TON</span>' +
            '</div>' +
            '</div>';

        // ── 活動時間軸(含 jetton + comment) ──
        var eventsHtml = (d.events || []).length
            ? d.events.map(function (ev) {
                var acts = (ev.actions || []).map(function (a) {
                    if (a.type === 'jetton') {
                        var isInJet = a.is_in;
                        var jetDir = isInJet ? '+' : '−';
                        var jetColor = isInJet ? 'text-success' : 'text-danger';
                        var amt = _esc(a.amount) + ' ' + _esc(a.symbol || '?');
                        return '<div class="flex items-center justify-between py-2">' +
                            '<div class="flex items-center gap-2 min-w-0">' +
                            '<i data-lucide="' + (isInJet ? 'arrow-down-to-line' : 'arrow-up-from-line') + '" class="w-3.5 h-3.5 ' + jetColor + ' shrink-0"></i>' +
                            '<span class="text-sm ' + jetColor + ' font-medium tabular-nums shrink-0">' + jetDir + amt + '</span>' +
                            '</div>' +
                            '<span class="text-xs text-textMuted font-mono ml-2 truncate">' + _esc(_short(a.is_in ? a.sender : a.recipient)) + '</span>' +
                            '</div>';
                    }
                    // ton
                    var isIn = a.is_in;
                    var dirSign = isIn ? '+' : '−';
                    var dirColor = isIn ? 'text-success' : 'text-danger';
                    var amt = _esc(a.amount_ton) + ' TON';
                    var comment = a.comment
                        ? '<p class="text-[11px] text-textMuted ml-6 mt-1 flex items-center gap-1">' +
                          '<i data-lucide="message-circle" class="w-3 h-3"></i>' + _esc(a.comment) + '</p>'
                        : '';
                    return '<div class="py-1">' +
                        '<div class="flex items-center justify-between py-1">' +
                        '<div class="flex items-center gap-2 min-w-0">' +
                        '<i data-lucide="' + (isIn ? 'arrow-down-to-line' : 'arrow-up-from-line') + '" class="w-3.5 h-3.5 ' + dirColor + ' shrink-0"></i>' +
                        '<span class="text-sm ' + dirColor + ' font-medium tabular-nums shrink-0">' + dirSign + amt + '</span>' +
                        '</div>' +
                        '<span class="text-xs text-textMuted font-mono ml-2 truncate">' + _esc(_short(a.is_in ? a.sender : a.recipient)) + '</span>' +
                        '</div>' + comment + '</div>';
                }).join('');
                return '<div class="rounded-xl bg-surfaceHighlight/30 px-3 py-2.5 border-b border-borderSubtle/40 last:border-0">' +
                    '<div class="flex items-center justify-between mb-1">' +
                    '<span class="text-[11px] text-textMuted font-medium tabular-nums">' + _esc(_fmtTime(ev.timestamp)) + '</span>' +
                    (ev.is_scam ? '<span class="inline-flex items-center gap-1 text-[10px] text-danger font-semibold px-1.5 py-0.5 rounded bg-danger/10">' +
                     '<i data-lucide="alert-triangle" class="w-3 h-3"></i>' + _t('scamTag') + '</span>' : '') +
                    '</div>' + acts + '</div>';
            }).join('')
            : '<p class="text-sm text-textMuted py-6 text-center">' + _t('noEvents') + '</p>';
        var activity =
            '<div class="' + _CARD_MT + '">' +
            _sectionHeader('activity', _t('activityDetail')) +
            '<div class="space-y-1.5">' +
            eventsHtml +
            '</div>' +
            '</div>';

        body.innerHTML =
            '<div class="max-w-3xl mx-auto space-y-5 px-4 md:px-6 py-6">' +
            header + holdings + stats + activity +
            '</div>';

        // 返回按鈕
        var backBtn = document.getElementById('btn-wm-back');
        if (backBtn) backBtn.addEventListener('click', function () { load(); });
        // 刷新 icon
        if (window.AppUtils) AppUtils.refreshIcons(body);
    }

    /** 章節標頭(eyebrow icon + 標題 + 可選計數 pill)。 */
    function _sectionHeader(icon, title, count) {
        var countPill = count
            ? '<span class="ml-2 text-[10px] text-textMuted px-1.5 py-0.5 rounded-full bg-surfaceHighlight/80 tabular-nums">' + _esc(count) + '</span>'
            : '';
        return '<div class="flex items-center gap-2 mb-4">' +
            '<i data-lucide="' + icon + '" class="w-4 h-4 text-primary"></i>' +
            '<h3 class="text-base font-semibold text-secondary tracking-tight">' + title + '</h3>' +
            countPill +
            '</div>';
    }

    async function addWallet() {
        var addrEl = document.getElementById('wm-add-address');
        var labelEl = document.getElementById('wm-add-label');
        var chainEl = document.getElementById('wm-add-chain');
        if (!addrEl || !addrEl.value.trim()) {
            _toast(_t('enterAddress'), 'warning');
            return;
        }
        var chain = chainEl ? chainEl.value : 'ton';
        try {
            var resp = await fetch('/api/wallet-monitor/wallets', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    address: addrEl.value.trim(),
                    label: labelEl ? labelEl.value.trim() : '',
                    // reason 統一由警示設定的「其他」管理,新增錢包不再自填
                    reason: '',
                    chain: chain,
                }),
            });
            if (!resp.ok) {
                if (await _isUpgradeBlock(resp)) {
                    _toast(_t('upgradeRequired'), 'warning');
                    return;
                }
                throw await errorFromResponse(resp);
            }
            await load();
        } catch (e) {
            _toast(_t('addFailed') + ': ' + userFacingMessage(e), 'error');
        }
    }

    async function saveSettings() {
        if (!settings) return;
        const alerts = settings.alerts || {};
        const channels = settings.channels || {};

        ['incoming', 'outgoing', 'scam', 'large_out', 'other'].forEach(function (key) {
            const el = document.querySelector('[data-alert-key="' + key + '"]');
            if (el && alerts[key]) alerts[key].enabled = el.checked;
            // 「其他」勾選時,一併存自訂原因輸入框的內容
            if (key === 'other' && alerts.other) {
                const reasonEl = document.getElementById('wm-other-reason');
                alerts.other.reason = reasonEl ? reasonEl.value.trim() : '';
            }
        });
        ['in_app', 'telegram'].forEach(function (key) {
            const el = document.querySelector('[data-channel-key="' + key + '"]');
            // disabled 的 checkbox 值不可信(telegram 未綁定時恆為 false),
            // 跳過不覆寫,保留原本設定。
            if (el && !el.disabled) channels[key] = el.checked;
        });

        try {
            const resp = await fetch('/api/wallet-monitor/settings', {
                method: 'PUT',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ settings: settings }),
            });
            if (!resp.ok) {
                if (await _isUpgradeBlock(resp)) {
                    if (typeof window.showToast === 'function') {
                        window.showToast(_t('upgradeRequired'), 'warning');
                    } else {
                        alert(_t('upgradeRequired'));
                    }
                    return;
                }
                throw await errorFromResponse(resp);
            }
            if (typeof window.showToast === 'function') {
                window.showToast(_t('saved'), 'success');
            } else {
                alert(_t('saved'));
            }
        } catch (e) {
            var reason = userFacingMessage(e);
            if (typeof window.showToast === 'function') {
                window.showToast(_t('saveFailed') + ': ' + reason, 'error');
            } else {
                alert(_t('saveFailed') + ': ' + reason);
            }
        }
    }

    async function removeWallet(address) {
        try {
            const resp = await fetch('/api/wallet-monitor/wallets/' + encodeURIComponent(address), {
                method: 'DELETE',
                credentials: 'include',
            });
            if (!resp.ok) throw await errorFromResponse(resp);
            await load();
        } catch (e) {
            _toast(_t('removeFailed') + ': ' + userFacingMessage(e), 'error');
        }
    }

    /** toast 包裝(showToast 存在就用,否則 fallback alert)。 */
    function _toast(msg, type) {
        if (typeof window.showToast === 'function') {
            window.showToast(msg, type);
        } else {
            alert(msg);
        }
    }

    function refresh() {
        load();
    }

    function init() {
        load();
    }

    return { load: load, init: init, refresh: refresh, render: render };
})();
