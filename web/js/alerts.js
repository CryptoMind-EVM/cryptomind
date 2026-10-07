// ========================================
// alerts.js - 價格警告與提醒 UI 邏輯
// ========================================

let _alertSymbol = '';
let _alertMarket = '';

function openAlertModal(symbol, market) {
    _alertSymbol = symbol;
    _alertMarket = market;
    document.getElementById('alert-symbol-label').textContent = symbol + ' (' + market + ')';
    document.getElementById('alert-target').value = '';
    document.getElementById('alert-repeat').checked = false;
    document.getElementById('alert-modal').classList.remove('hidden');
    // Re-init Lucide icons (X button inside modal)
    if (window.AppUtils) window.AppUtils.refreshIcons();
    // Re-apply i18n translations
    if (window.I18n && typeof window.I18n.updatePageContent === 'function')
        window.I18n.updatePageContent();
}

function closeAlertModal() {
    document.getElementById('alert-modal').classList.add('hidden');
}

let _alertSubmitting = false;

async function submitAlert() {
    // 連點「建立警報」會送出多筆一樣的警報
    if (_alertSubmitting) return;
    const condition = document.getElementById('alert-condition').value;
    const target = parseFloat(document.getElementById('alert-target').value);
    const repeat = document.getElementById('alert-repeat').checked;

    const t = (key) => (window.I18n ? window.I18n.t(key) : key);

    if (!target || isNaN(target) || target <= 0) {
        if (typeof window.showToast === 'function')
            window.showToast(t('modals.priceAlert.invalidTarget'), 'error');
        return;
    }

    _alertSubmitting = true;
    try {
        await window.AppAPI.post('/api/alerts', {
            symbol: _alertSymbol,
            market: _alertMarket,
            condition,
            target,
            repeat,
        });
        if (typeof window.showToast === 'function')
            window.showToast('✅ ' + t('modals.priceAlert.confirm'));
        closeAlertModal();
        loadUserAlerts();
    } catch (e) {
        if (typeof window.showToast === 'function')
            window.showToast(e.message || t('modals.priceAlert.setFailed'), 'error');
    } finally {
        _alertSubmitting = false;
    }
}

async function loadUserAlerts() {
    try {
        if (!window.AppAPI || !window.AppAPI.hasSession()) return;
        const data = await window.AppAPI.get('/api/alerts');
        renderAlertList(data.alerts || []);
        // 設定頁「我的自選」也列警報（web/js/watchlist-settings.js）
        window.dispatchEvent(new CustomEvent('alerts:changed', { detail: { alerts: data.alerts || [] } }));
    } catch (e) {
        // silent fail — user may not be logged in
    }
}

async function deleteUserAlert(alertId) {
    const t = (key) => (window.I18n ? window.I18n.t(key) : key);
    try {
        await window.AppAPI.delete('/api/alerts/' + alertId);
        loadUserAlerts();
    } catch (e) {
        if (typeof window.showToast === 'function')
            window.showToast(t('modals.priceAlert.deleteFailed'), 'error');
    }
}

function renderAlertList(alerts) {
    const t = (key) => (window.I18n ? window.I18n.t(key) : key);
    // condition labels come from i18n
    const condMap = {
        above: t('modals.priceAlert.above'),
        below: t('modals.priceAlert.below'),
        change_pct_up: t('modals.priceAlert.changePctUp'),
        change_pct_down: t('modals.priceAlert.changePctDown'),
    };
    const noAlertsHtml =
        '<p class="text-textMuted text-xs py-1">' + t('modals.priceAlert.noAlerts') + '</p>';
    const deleteLabel = t('modals.priceAlert.deleteAlert');
    const esc = (v) => (window.escapeHtml ? window.escapeHtml(String(v)) : String(v));

    // 各分頁只列自己市場的警報（market 值與 twstock/usstock.js 的 openAlert 參數一致）
    const containers = { 'alert-list-twstock': 'tw_stock', 'alert-list-usstock': 'us_stock' };
    Object.keys(containers).forEach(function (cid) {
        const container = document.getElementById(cid);
        if (!container) return;
        // 區塊預設隱藏（未登入不顯示），拿到清單才打開
        const section = document.getElementById(cid.replace('alert-list-', 'alert-list-section-'));
        if (section) section.classList.remove('hidden');
        const mine = alerts.filter(function (a) {
            return a.market === containers[cid];
        });
        if (mine.length === 0) {
            container.innerHTML = noAlertsHtml;
            return;
        }
        container.innerHTML = mine
            .map(function (a) {
                const label = condMap[a.condition] || a.condition;
                return (
                    '<div class="flex justify-between items-center py-1.5 border-b border-borderSubtle last:border-0">' +
                    '<span class="text-secondary text-xs font-mono">' +
                    esc(a.symbol) +
                    ' <span class="text-primary">' +
                    esc(label) +
                    '</span> ' +
                    esc(a.target) +
                    (a.repeat
                        ? ' <span class="text-textMuted">' +
                          t('modals.priceAlert.repeatBadge') +
                          '</span>'
                        : '') +
                    '</span>' +
                    '<button data-click="deleteUserAlert" data-click-arg="' +
                    encodeURIComponent(a.id) +
                    '" ' +
                    'class="text-danger hover:brightness-125 text-xs ml-2 transition">' +
                    deleteLabel +
                    '</button>' +
                    '</div>'
                );
            })
            .join('');
    });
}

// Expose alert functions globally so onclick handlers and tab init can call them
window.openAlertModal = openAlertModal;
window.closeAlertModal = closeAlertModal;
window.submitAlert = submitAlert;
window.loadUserAlerts = loadUserAlerts;
window.deleteUserAlert = deleteUserAlert;
window.renderAlertList = renderAlertList;

export { openAlertModal, closeAlertModal, submitAlert, loadUserAlerts, deleteUserAlert, renderAlertList };
