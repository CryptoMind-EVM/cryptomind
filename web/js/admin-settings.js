// ========================================
// admin-settings.js — 後台「設定中心」（2026-09-27）
// GET /api/admin/settings-center：所有功能開關與數值參數的**實際值**
// （core/admin_settings.py）。設定是開、但缺環境變數或前置開關時標成「未生效」並列出原因；
// api／analysis-worker／cron-worker 各自回報的值不一致時標紅。
// 白名單內的開關／額度可以直接在這裡覆寫（PUT／DELETE /api/admin/settings-center/overrides/{key}，
// core/setting_overrides.py）：約 15 秒內所有服務生效、記進稽核紀錄；「用預設」＝回到環境變數。
// ========================================

function _t(key, fallback) {
    const text = window.I18n ? window.I18n.t(key) : '';
    return text && text !== key ? text : fallback;
}

function _esc(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function _args(...values) {
    return encodeURIComponent(JSON.stringify(values));
}

function _btn(label, action, args, active) {
    const cls = active
        ? 'bg-primary/15 text-primary border-primary/30'
        : 'bg-surfaceHighlight/60 text-textMuted border-transparent hover:text-textMain';
    return `<button type="button" data-click="${action}" data-click-args="${args}"
        class="px-2.5 py-1 rounded-lg text-[11px] font-medium border transition ${cls}">${_esc(label)}</button>`;
}

function _overrideSource(row) {
    if (row.override === null || row.override === undefined) return '';
    const who = row.override_by ? ` · ${_esc(row.override_by)}` : '';
    let when = '';
    try {
        when = row.override_at ? ` · ${_esc(new Date(row.override_at).toLocaleString())}` : '';
    } catch (_) {
        when = '';
    }
    return `<div class="text-[10px] text-primary mt-1">${_esc(window.I18n.t('admin.settingsCenter.sourceOverride'))} = <span class="font-mono">${_esc(row.override)}</span>${who}${when}</div>`;
}

function _flagControls(row) {
    if (!row.overridable) {
        return `<div class="text-[10px] text-textMuted/60 mt-1">${_esc(window.I18n.t('admin.settingsCenter.envOnly'))}</div>`;
    }
    const ov = row.override;
    return `<div class="flex items-center gap-1.5 mt-2 flex-wrap">
        ${_btn(window.I18n.t('admin.settingsCenter.on'), 'AdminSettingsCenter.setOverride', _args(row.name, 'true'), ov === 'true')}
        ${_btn(window.I18n.t('admin.settingsCenter.off'), 'AdminSettingsCenter.setOverride', _args(row.name, 'false'), ov === 'false')}
        ${_btn(window.I18n.t('admin.settingsCenter.useDefault'), 'AdminSettingsCenter.clearOverride', _args(row.name), ov === null || ov === undefined)}
    </div>`;
}

function _paramControls(row) {
    if (!row.overridable) {
        return `<div class="text-[10px] text-textMuted/60 mt-1">${_esc(window.I18n.t('admin.settingsCenter.envOnly'))}</div>`;
    }
    const [lo, hi] = row.bounds || [0, ''];
    const hasOverride = row.override !== null && row.override !== undefined;
    return `<div class="flex items-center gap-1.5 mt-2 flex-wrap">
        <input id="settings-param-${_esc(row.name)}" type="number" inputmode="numeric" min="${_esc(lo)}" max="${_esc(hi)}" step="1"
            value="${_esc(row.value ?? '')}"
            class="w-28 bg-background border border-borderLight rounded-lg px-2 py-1 text-xs text-secondary font-mono">
        ${_btn(window.I18n.t('admin.settingsCenter.save'), 'AdminSettingsCenter.saveParam', _args(row.name), false)}
        ${_btn(window.I18n.t('admin.settingsCenter.useDefault'), 'AdminSettingsCenter.clearOverride', _args(row.name), !hasOverride)}
    </div>`;
}

function _groupBy(rows) {
    const groups = new Map();
    for (const row of rows) {
        if (!groups.has(row.group)) groups.set(row.group, []);
        groups.get(row.group).push(row);
    }
    return groups;
}

function _card(title, count, body) {
    return `
        <div class="bg-surface rounded-2xl border border-borderSubtle p-5 mb-4">
            <h3 class="font-bold text-secondary mb-3 flex items-center gap-2 text-sm">
                ${_esc(title)} <span class="text-[10px] text-textMuted font-normal">(${count})</span>
            </h3>
            <div class="space-y-2">${body}</div>
        </div>`;
}

function _statusPill(row) {
    if (row.effective) {
        return `<span class="text-[10px] px-2 py-0.5 rounded-full bg-success/10 text-success font-medium">${_esc(window.I18n.t('admin.settingsCenter.on'))}</span>`;
    }
    if (row.value) {
        return `<span class="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-600 font-medium">${_esc(window.I18n.t('admin.settingsCenter.notEffective'))}</span>`;
    }
    return `<span class="text-[10px] px-2 py-0.5 rounded-full bg-surfaceHighlight text-textMuted font-medium">${_esc(window.I18n.t('admin.settingsCenter.off'))}</span>`;
}

function _source(env, defaultText) {
    if (env !== null && env !== undefined) {
        return `${_esc(window.I18n.t('admin.settingsCenter.sourceEnv'))} = <span class="font-mono">${_esc(env)}</span>`;
    }
    return `${_esc(window.I18n.t('admin.settingsCenter.sourceDefault'))}${defaultText ? `（${_esc(defaultText)}）` : ''}`;
}

function _flagRow(row) {
    const unmet = row.unmet && row.unmet.length
        ? `<div class="text-[11px] text-amber-600 mt-1">${_esc(row.unmet.join('、'))}</div>`
        : '';
    const services = Object.entries(row.services || {});
    const inconsistent = !row.consistent && services.length
        ? `<div class="text-[11px] text-danger mt-1">${_esc(window.I18n.t('admin.settingsCenter.inconsistent'))}：${services
              .map(([svc, v]) => `${_esc(svc)}=${v ? 'on' : 'off'}`)
              .join('、')}</div>`
        : '';
    return `
        <div class="p-3 bg-background/50 rounded-xl border border-borderSubtle" data-flag="${_esc(row.name)}">
            <div class="flex items-center gap-2 flex-wrap">
                <span class="text-xs font-medium text-secondary font-mono break-all">${_esc(row.name)}</span>
                ${_statusPill(row)}
            </div>
            <div class="text-[11px] text-textMuted mt-1">${_esc(row.description)}</div>
            <div class="text-[10px] text-textMuted/70 mt-1">${_source(row.env, row.default)}</div>
            ${_overrideSource(row)}${unmet}${inconsistent}
            ${_flagControls(row)}
        </div>`;
}

function _paramRow(row) {
    const value = row.value === null || row.value === undefined ? '—' : row.value;
    return `
        <div class="p-3 bg-background/50 rounded-xl border border-borderSubtle" data-param="${_esc(row.name)}">
            <div class="flex items-center gap-2 flex-wrap">
                <span class="text-xs font-medium text-secondary">${_esc(row.description)}</span>
                <span class="text-sm text-primary font-mono">${_esc(value)} <span class="text-[10px] text-textMuted">${_esc(row.unit)}</span></span>
            </div>
            <div class="text-[10px] text-textMuted/70 mt-1">
                <span class="font-mono">${_esc(row.name)}</span> · ${row.env_key ? _source(row.env, '') : _esc(window.I18n.t('admin.settingsCenter.sourceCode'))}
            </div>
            ${_overrideSource(row)}
            ${_paramControls(row)}
        </div>`;
}

function _servicesLine(services) {
    const entries = Object.entries(services || {});
    if (!entries.length) return '';
    const fmt = (iso) => {
        try {
            return new Date(iso).toLocaleString();
        } catch (_) {
            return iso;
        }
    };
    return `<p class="text-[11px] text-textMuted mt-1">${_esc(window.I18n.t('admin.settingsCenter.servicesReported'))}：${entries
        .map(([svc, at]) => `${_esc(svc)} ${_esc(fmt(at))}`)
        .join('、')}</p>`;
}

const AdminSettingsCenter = {
    data: null,

    render() {
        const container = document.getElementById('admin-subpage-content');
        if (!container) return;
        container.innerHTML = `
            <div class="flex items-start justify-between gap-3 mb-4 flex-wrap">
                <div>
                    <h2 class="text-sm font-semibold text-secondary">${_esc(window.I18n.t('admin.settingsCenter.title'))}</h2>
                    <p class="text-xs text-textMuted mt-0.5">${_esc(window.I18n.t('admin.settingsCenter.subtitle'))}</p>
                    <div id="settings-center-services"></div>
                </div>
                <button data-click="AdminSettingsCenter.load"
                        class="px-3 py-1.5 bg-surfaceHighlight hover:bg-surfaceHighlight/80 rounded-lg text-xs text-textMuted transition">
                    ${_esc(window.I18n.t('admin.settingsCenter.reload'))}
                </button>
            </div>
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div>
                    <h3 class="text-xs font-semibold text-textMuted uppercase tracking-wider mb-2">${_esc(window.I18n.t('admin.settingsCenter.flagsTitle'))}</h3>
                    <div id="settings-center-flags"><div class="text-center text-textMuted text-sm py-8">…</div></div>
                </div>
                <div>
                    <h3 class="text-xs font-semibold text-textMuted uppercase tracking-wider mb-2">${_esc(window.I18n.t('admin.settingsCenter.paramsTitle'))}</h3>
                    <div id="settings-center-params"></div>
                </div>
            </div>`;
        this.load();
    },

    async _confirm(name, valueLabel) {
        const message = window.I18n.t('admin.settingsCenter.confirmBody', { name, value: valueLabel });
        if (typeof window.showConfirmDialog === 'function') {
            return window.showConfirmDialog({
                title: window.I18n.t('admin.settingsCenter.confirmTitle'),
                message,
                confirmText: window.I18n.t('admin.settingsCenter.save'),
            });
        }
        return typeof window.confirm === 'function' ? window.confirm(message) : true;
    },

    _toast(ok, detail) {
        if (typeof window.showToast !== 'function') return;
        const text = ok
            ? window.I18n.t('admin.settingsCenter.saved')
            : `${window.I18n.t('admin.settingsCenter.saveFailed')}${detail ? `：${detail}` : ''}`;
        window.showToast(text, ok ? 'success' : 'error');
    },

    async setOverride(name, value) {
        const label = value === 'true' ? window.I18n.t('admin.settingsCenter.on')
            : value === 'false' ? window.I18n.t('admin.settingsCenter.off') : value;
        if (!(await this._confirm(name, label))) return false;
        try {
            await window.AppAPI.put(`/api/admin/settings-center/overrides/${encodeURIComponent(name)}`, { value: String(value) });
            this._toast(true);
            await this.load();
            return true;
        } catch (e) {
            this._toast(false, e && e.message);
            return false;
        }
    },

    async saveParam(name) {
        const input = document.getElementById(`settings-param-${name}`);
        const value = input ? String(input.value).trim() : '';
        if (!/^\d+$/.test(value)) {
            this._toast(false, window.I18n.t('admin.settingsCenter.invalidNumber'));
            return false;
        }
        return this.setOverride(name, value);
    },

    async clearOverride(name) {
        if (!(await this._confirm(name, window.I18n.t('admin.settingsCenter.useDefault')))) return false;
        try {
            await window.AppAPI.delete(`/api/admin/settings-center/overrides/${encodeURIComponent(name)}`);
            this._toast(true);
            await this.load();
            return true;
        } catch (e) {
            this._toast(false, e && e.message);
            return false;
        }
    },

    async load() {
        const flagsEl = document.getElementById('settings-center-flags');
        const paramsEl = document.getElementById('settings-center-params');
        if (!flagsEl || !paramsEl) return;
        try {
            const data = await window.AppAPI.get('/api/admin/settings-center');
            this.data = data;
            flagsEl.innerHTML = [..._groupBy(data.flags || [])]
                .map(([group, rows]) => _card(group, rows.length, rows.map(_flagRow).join('')))
                .join('');
            paramsEl.innerHTML = [..._groupBy(data.params || [])]
                .map(([group, rows]) => _card(group, rows.length, rows.map(_paramRow).join('')))
                .join('');
            const servicesEl = document.getElementById('settings-center-services');
            if (servicesEl) servicesEl.innerHTML = _servicesLine(data.services);
        } catch (e) {
            flagsEl.innerHTML = `<div class="text-danger text-sm py-4 text-center">${_esc(
                _t('admin.settingsCenter.loadFailed', 'Failed to load settings')
            )}</div>`;
        }
    },
};

window.AdminSettingsCenter = AdminSettingsCenter;
export { AdminSettingsCenter };
