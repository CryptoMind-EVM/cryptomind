// ========================================
// content-modal.js — 共用內容檢視/編輯彈窗
// 職責：memory / skill 長內容的完整顯示 + 編輯 + 刪除
// 沿用專案既有的 overlay 模式（fixed inset-0 z-[60] bg-background/95 backdrop-blur）
// ========================================

const ContentModal = {
    _current: null, // { type, title, subtitle, badge, body, editable, onSave, onDelete }

    /**
     * 開啟彈窗。
     * @param {Object} opts
     *   - title: 標題（memory key label 或 skill name）
     *   - subtitle: 副標題（如「AI Memory」「Custom Skill」）
     *   - badge: { label, color } 類別標籤
     *   - body: 完整內容文字
     *   - editable: bool 是否可編輯（official skill = false）
     *   - maxLength: textarea 上限
     *   - onSave: async (newValue) => bool  儲存回呼，回傳 true=成功
     *   - onDelete: async () => bool  刪除回呼，回傳 true=成功
     */
    open(opts) {
        this._current = opts;
        const overlay = document.getElementById('content-modal');
        if (!overlay) return;

        // 填入內容
        const badgeHtml = opts.badge
            ? `<span class="px-1.5 py-0.5 rounded text-[10px] font-medium" style="background:${opts.badge.color}20;color:${opts.badge.color};">${this._esc(opts.badge.label)}</span>`
            : '';
        const subtitleHtml = opts.subtitle
            ? `<span class="text-xs text-textMuted/60">${this._esc(opts.subtitle)}</span>`
            : '';

        overlay.querySelector('#content-modal-title').innerHTML = badgeHtml + subtitleHtml;
        overlay.querySelector('#content-modal-subtitle').textContent = opts.title || '';

        const bodyEl = overlay.querySelector('#content-modal-body');
        bodyEl.value = opts.body || '';
        bodyEl.readOnly = true;
        bodyEl.maxLength = opts.maxLength || 5000;

        // 按鈕顯示控制
        overlay.querySelector('#content-modal-edit-btn').classList.toggle('hidden', !opts.editable);
        overlay.querySelector('#content-modal-delete-btn').classList.toggle('hidden', !opts.onDelete);
        overlay.querySelector('#content-modal-save-btn').classList.add('hidden');
        overlay.querySelector('#content-modal-cancel-btn').classList.add('hidden');

        overlay.classList.remove('hidden');
        document.body.style.overflow = 'hidden';

        if (window.lucide) window.lucide.createIcons({ nodes: [overlay] });
    },

    close() {
        const overlay = document.getElementById('content-modal');
        if (overlay) overlay.classList.add('hidden');
        document.body.style.overflow = '';
        this._current = null;
    },

    _enterEditMode() {
        const overlay = document.getElementById('content-modal');
        if (!overlay) return;
        const bodyEl = overlay.querySelector('#content-modal-body');
        bodyEl.readOnly = false;
        bodyEl.focus();
        overlay.querySelector('#content-modal-edit-btn').classList.add('hidden');
        overlay.querySelector('#content-modal-delete-btn').classList.add('hidden');
        overlay.querySelector('#content-modal-save-btn').classList.remove('hidden');
        overlay.querySelector('#content-modal-cancel-btn').classList.remove('hidden');
    },

    _exitEditMode() {
        const overlay = document.getElementById('content-modal');
        if (!overlay || !this._current) return;
        const bodyEl = overlay.querySelector('#content-modal-body');
        bodyEl.value = this._current.body || '';
        bodyEl.readOnly = true;
        overlay.querySelector('#content-modal-edit-btn').classList.remove('hidden');
        overlay.querySelector('#content-modal-delete-btn').classList.toggle('hidden', !this._current.onDelete);
        overlay.querySelector('#content-modal-save-btn').classList.add('hidden');
        overlay.querySelector('#content-modal-cancel-btn').classList.add('hidden');
    },

    async _save() {
        if (!this._current || !this._current.onSave) return;
        const overlay = document.getElementById('content-modal');
        const bodyEl = overlay.querySelector('#content-modal-body');
        const newValue = bodyEl.value.trim();
        if (!newValue) return;
        const saveBtn = overlay.querySelector('#content-modal-save-btn');
        saveBtn.disabled = true;
        try {
            const ok = await this._current.onSave(newValue);
            if (ok) {
                this._current.body = newValue;
                bodyEl.readOnly = true;
                this.close();
            }
        } catch (e) {
            // onSave 自己處理 toast
        } finally {
            saveBtn.disabled = false;
        }
    },

    async _delete() {
        if (!this._current || !this._current.onDelete) return;
        const ok = await this._current.onDelete();
        if (ok) this.close();
    },

    _esc(s) {
        if (typeof window.escapeHtml === 'function') return window.escapeHtml(s);
        return String(s == null ? '' : s).replace(/</g, '&lt;');
    },

    _bindEvents() {
        const overlay = document.getElementById('content-modal');
        if (!overlay || overlay.dataset.bound) return;
        overlay.dataset.bound = '1';

        overlay.querySelector('#content-modal-close-btn').addEventListener('click', () => this.close());
        overlay.querySelector('#content-modal-edit-btn').addEventListener('click', () => this._enterEditMode());
        overlay.querySelector('#content-modal-save-btn').addEventListener('click', () => this._save());
        overlay.querySelector('#content-modal-cancel-btn').addEventListener('click', () => this._exitEditMode());
        overlay.querySelector('#content-modal-delete-btn').addEventListener('click', () => this._delete());

        // ESC 關閉（編輯模式中 ESC 取消編輯）
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && overlay && !overlay.classList.contains('hidden')) {
                const bodyEl = overlay.querySelector('#content-modal-body');
                if (!bodyEl.readOnly) {
                    this._exitEditMode();
                } else {
                    this.close();
                }
            }
        });
    },
};

window.ContentModal = ContentModal;
// 綁定事件（在 DOM ready 後）
if (document.readyState !== 'loading') {
    ContentModal._bindEvents();
} else {
    document.addEventListener('DOMContentLoaded', () => ContentModal._bindEvents());
}
