// ========================================
// chat-hitl.js - Human-in-the-Loop 互動
// 職責：Pre-Research Card、Plan Card、同意卡、submitHITLAnswer
// 依賴：chat-state.js
// ========================================


// ── HITL Web Mode ─────────────────────────────────────────────────────────────
// Stores context needed to resume the graph after user answers a HITL question

function renderLoopForkCard(idata, botMsgDiv) {
    // Mixer Step 6（HITL 第 9 種，loop_fork）：軟門檻分岔卡——附實際呼叫
    // 過的工具（模型自述的分岔可能是編的，讓人自己判斷）
    const _dt = (key, fallback) => (window.I18n ? window.I18n.t(key, fallback) : fallback);
    const esc = (s) => (window.escapeHtml ? window.escapeHtml(String(s ?? '')) : String(s ?? ''));
    const tools = Array.isArray(idata.tools_used) ? idata.tools_used : [];
    const toolsHtml = tools.length
        ? '<ul class="text-xs text-textMuted mt-2 space-y-1">' + tools.map((n) => `<li>• ${esc(n)}</li>`).join('') + '</ul>'
        : '';
    const options = Array.isArray(idata.options) ? idata.options : [];
    const btns = options.map((opt, i) => {
        const label = typeof opt === 'string' ? opt : (opt.label || '');
        const hint = typeof opt === 'object' ? (opt.hint || '') : '';
        return `<button data-fork-opt="${i}" data-fork-hint="${esc(hint)}"
            class="px-3 py-1.5 rounded-lg bg-primary text-background text-xs">${esc(label)}</button>`;
    }).join('');
    const card = document.createElement('div');
    card.className = 'rounded-xl border border-amber-400/40 bg-amber-400/10 p-4 my-3';
    card.innerHTML = `
        <div class="flex items-center gap-2 mb-1">
            <i data-lucide="git-branch" class="w-4 h-4 text-amber-400"></i>
            <span class="text-sm font-medium text-secondary">${_dt('hitl.loopForkTitle', 'Your instruction is needed to continue')}</span>
            <span class="text-xs text-textMuted ml-auto">${esc(idata.steps || '')} steps</span>
        </div>
        <p class="text-sm text-secondary whitespace-pre-wrap">${esc(idata.question || '')}</p>
        ${toolsHtml}
        <div class="flex gap-2 mt-3">${btns}</div>
        <p class="text-xs text-textMuted mt-2">${_dt('hitl.loopForkHint', 'Or type an extra hint in the input box (④) and send')}</p>`;
    botMsgDiv.appendChild(card);
    if (window.lucide) window.lucide.createIcons({ nodes: [card] });
    card.querySelectorAll('[data-fork-opt]').forEach((btn) => {
        btn.addEventListener('click', () => {
            // ③（空 hint）＝直接以現有結果收斂；hint 非空＝使用者補充
            const hint = btn.getAttribute('data-fork-hint') || '';
            if (typeof window.submitHITLAnswer === 'function') window.submitHITLAnswer(hint);
        });
    });
}

window.renderLoopForkCard = renderLoopForkCard;

// ── Pre-Research Card (pre_research HITL) ────────────────────────────────────

// ── Consent Gate Card (consent_gate HITL — Trustworthy AI Hackathon) ─────────
// 高風險工具執行前的 explicit consent。展示 Policy Gate 支柱。
function renderConsentCard(idata, targetDiv) {
    if (!targetDiv) return;
    const message = idata.message || (window.I18n.t('hitl.consentWarning') || 'The agent is about to use a high-risk tool and needs your consent to continue.');
    const tools = idata.tools || [];
    const trust = idata.trust || {};

    const toolsList = tools
        .map(
            (t) =>
                `<li class="flex items-start gap-2"><i data-lucide="alert-triangle" class="w-4 h-4 text-amber-600 mt-0.5 shrink-0"></i><span class="text-sm"><strong>${t.display_name || t.name}</strong> <code class="text-xs text-textMuted">${t.name}</code></span></li>`
        )
        .join('');

    const trustTier = trust.tier || 'unknown';
    const trustScore = trust.trust_score != null ? trust.trust_score : '?';

    const card = document.createElement('div');
    card.className = 'consent-card mt-3 rounded-2xl border border-amber-600/30 bg-amber-500/5 p-4';
    card.id = 'active-consent-card';
    card.innerHTML = `
        <div class="flex items-start gap-3 mb-3">
            <i data-lucide="shield-alert" class="w-5 h-5 text-amber-600 shrink-0 mt-0.5"></i>
            <div class="flex-1">
                <div class="font-medium text-sm text-amber-600 mb-1">${window.I18n ? window.I18n.t('hitl.consentTitle') || 'High-risk action consent' : 'High-risk action consent'}</div>
                <div class="text-sm text-secondary">${message}</div>
            </div>
        </div>
        ${toolsList ? `<ul class="space-y-1.5 mb-3 pl-1">${toolsList}</ul>` : ''}
        <div class="flex items-center gap-2 mb-3 text-xs text-textMuted">
            <i data-lucide="fingerprint" class="w-3.5 h-3.5"></i>
            <span>${window.I18n ? window.I18n.t('hitl.trustScore') || 'Identity trust' : 'Identity trust'}: <strong>${trustScore}/100</strong> (${trustTier})</span>
        </div>
        <div class="flex gap-2 border-t border-amber-600/20 pt-3">
            <button data-click="submitConsent" data-click-arg="true" class="flex-1 px-4 py-2 rounded-xl bg-amber-500 text-black text-sm font-medium hover:bg-amber-500/90 transition flex items-center justify-center gap-1.5">
                <i data-lucide="check" class="w-4 h-4"></i>
                <span>${window.I18n ? window.I18n.t('hitl.consentApprove') || 'Approve' : 'Approve'}</span>
            </button>
            <button data-click="submitConsent" data-click-arg="false" class="flex-1 px-4 py-2 rounded-xl bg-background border border-borderLight text-secondary text-sm hover:border-danger/50 hover:text-danger transition flex items-center justify-center gap-1.5">
                <i data-lucide="x" class="w-4 h-4"></i>
                <span>${window.I18n ? window.I18n.t('hitl.consentDecline') || 'Cancel' : 'Cancel'}</span>
            </button>
        </div>
    `;
    targetDiv.appendChild(card);

    if (window.lucide && typeof window.createIconsIn === 'function') {
        window.createIconsIn(card);
    }
}
window.renderConsentCard = renderConsentCard;

// Consent Gate 的同意/拒絕送回（走既有 submitHITLAnswer 的 resume 路徑）
// 接收字串 arg（data-click-arg="true"/"false"，由 click-delegator 傳入）
function submitConsent(approved) {
    const isApproved = approved === true || approved === 'true';
    const payload = JSON.stringify({ action: 'consent', approved: isApproved });
    window.submitHITLAnswer(payload);
}
window.submitConsent = submitConsent;

// ── Skill / Memory 自主管理 HITL（docs/plans/2026-08-10-agent-self-managed-skills-memory-design.md）──
// 三張卡：建立/修改 skill、刪除 skill（紅色二次確認）、記憶 memory。
// 機制同 consent gate：data-click → submitSkillConsent → submitHITLAnswer(resume)。
function _esc(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _hitl(key, fallback) {
    return window.I18n ? window.I18n.t(key) || fallback : fallback;
}

// 通用：渲染一段可摺疊的長文 body（讓使用者看得到完整內容再決定）
function _bodyPreviewHtml(body, label) {
    if (!body) return '';
    var safe = _esc(body);
    return '<div class="mt-2 rounded-lg bg-surfaceHighlight/50 p-2 border border-borderSubtle">'
        + '<div class="text-xs text-textMuted mb-1">' + _esc(label) + '</div>'
        + '<pre class="text-xs whitespace-pre-wrap break-words max-h-40 overflow-y-auto custom-scrollbar">' + safe + '</pre>'
        + '</div>';
}

// Skill 建立/修改同意卡（顯示完整 name/desc/keywords/body + 理由）
function renderSkillConsentCard(idata, targetDiv) {
    if (!targetDiv) return;
    var mode = idata.mode || 'create';
    var name = idata.skill_name || '';
    var desc = idata.description || '';
    var keywords = idata.trigger_keywords || '';
    var body = idata.body_preview || idata.body || '';
    var reason = idata.reason || '';
    var isDelete = mode === 'delete';

    var title = isDelete
        ? _hitl('hitl.skillDeleteTitle', 'Confirm deleting the method')
        : (mode === 'update' ? _hitl('hitl.skillUpdateTitle', 'Confirm updating the method') : _hitl('hitl.skillCreateTitle', 'Confirm creating the method'));
    var msg = idata.message || _hitl('hitl.skillConsentWarning', 'The agent proposes saving a personal analysis method and needs your consent.');
    var tone = isDelete ? 'border-red-600/30 bg-red-500/5' : 'border-amber-600/30 bg-amber-500/5';
    var icon = isDelete ? 'trash-2' : 'file-plus-2';

    var card = document.createElement('div');
    card.className = 'consent-card skill-consent-card mt-3 rounded-2xl border ' + tone + ' p-4';
    card.id = 'active-consent-card';
    card.innerHTML =
        '<div class="flex items-start gap-3 mb-3">'
        + '<i data-lucide="' + icon + '" class="w-5 h-5 ' + (isDelete ? 'text-red-600' : 'text-amber-600') + ' mt-0.5 shrink-0"></i>'
        + '<div class="flex-1 min-w-0">'
        + '<div class="font-semibold ' + (isDelete ? 'text-red-700 dark:text-red-400' : '') + '">' + _esc(title) + '</div>'
        + '<div class="text-sm text-secondary">' + _esc(msg) + '</div>'
        + '</div></div>'
        + '<div class="space-y-1 mb-2">'
        + '<div class="text-sm"><span class="text-textMuted">' + _hitl('hitl.skillNameLabel', 'Name: ') + '</span><code class="text-xs">' + _esc(name) + '</code></div>'
        + (desc ? '<div class="text-sm"><span class="text-textMuted">' + _hitl('hitl.skillDescLabel', 'Description: ') + '</span>' + _esc(desc) + '</div>' : '')
        + (keywords ? '<div class="text-sm"><span class="text-textMuted">' + _hitl('hitl.skillKeywordsLabel', 'Keywords: ') + '</span>' + _esc(keywords) + '</div>' : '')
        + '</div>'
        + (reason ? '<div class="text-xs text-textMuted italic mb-2">💡 ' + _esc(reason) + '</div>' : '')
        + _bodyPreviewHtml(body, isDelete ? _hitl('hitl.pendingDeleteContent', 'Content to be deleted') : _hitl('hitl.methodContent', 'Method content'))
        + (isDelete ? '<div class="mt-2 text-xs text-red-600 font-medium">⚠️ ' + _hitl('hitl.skillDeleteIrreversible', 'This cannot be undone') + '</div>' : '')
        + '<div class="flex gap-2 border-t ' + (isDelete ? 'border-red-600/20' : 'border-amber-600/20') + ' pt-3 mt-3">'
        + '<button data-click="submitSkillConsent" data-click-arg="true" class="flex-1 px-4 py-2 rounded-xl ' + (isDelete ? 'bg-red-600 hover:brightness-110' : 'bg-amber-500 hover:brightness-110') + ' text-white text-sm font-medium transition flex items-center justify-center gap-1.5">'
        + '<i data-lucide="check" class="w-4 h-4"></i><span>' + _hitl('hitl.skillConsentApprove', 'Approve') + '</span></button>'
        + '<button data-click="submitSkillConsent" data-click-arg="false" class="flex-1 px-4 py-2 rounded-xl bg-background border border-borderSubtle text-sm font-medium transition flex items-center justify-center gap-1.5">'
        + '<i data-lucide="x" class="w-4 h-4"></i><span>' + _hitl('hitl.skillConsentDecline', 'Cancel') + '</span></button>'
        + '</div>';
    targetDiv.appendChild(card);
    if (window.lucide && typeof window.createIconsIn === 'function') {
        window.createIconsIn(card);
    }
}
window.renderSkillConsentCard = renderSkillConsentCard;

// Memory 記憶同意卡（create=藍色 / delete=紅色二次確認）
function renderMemoryConsentCard(idata, targetDiv) {
    if (!targetDiv) return;
    var content = idata.content || '';
    var category = idata.category || 'fact';
    var isDelete = idata.mode === 'delete';
    var msg = idata.message || (isDelete
        ? _hitl('hitl.memoryDeleteWarning', 'The agent proposes deleting a memory and needs your consent.')
        : _hitl('hitl.memoryConsentWarning', 'The agent proposes remembering something and needs your consent.'));

    var tone = isDelete ? 'border-red-600/30 bg-red-500/5' : 'border-blue-600/30 bg-blue-500/5';
    var iconColor = isDelete ? 'text-red-600' : 'text-blue-600';
    var iconName = isDelete ? 'trash-2' : 'brain';
    var title = isDelete
        ? _hitl('hitl.memoryDeleteTitle', 'Confirm deleting the memory')
        : _hitl('hitl.memoryConsentTitle', 'Confirm remembering this');
    var approveClass = isDelete ? 'bg-red-600 hover:brightness-110' : 'bg-blue-600 hover:brightness-110';
    var contentLabel = isDelete ? _hitl('hitl.pendingDeleteContent', 'Content to be deleted') : _hitl('hitl.content', 'Content');

    var card = document.createElement('div');
    card.className = 'consent-card memory-consent-card mt-3 rounded-2xl border ' + tone + ' p-4';
    card.id = 'active-consent-card';
    card.innerHTML =
        '<div class="flex items-start gap-3 mb-3">'
        + '<i data-lucide="' + iconName + '" class="w-5 h-5 ' + iconColor + ' mt-0.5 shrink-0"></i>'
        + '<div class="flex-1 min-w-0">'
        + '<div class="font-semibold ' + (isDelete ? 'text-red-700 dark:text-red-400' : '') + '">' + _esc(title) + '</div>'
        + '<div class="text-sm text-secondary">' + _esc(msg) + '</div>'
        + '</div></div>'
        + '<div class="rounded-lg bg-surfaceHighlight/50 p-2 border border-borderSubtle mb-2">'
        + '<div class="text-xs text-textMuted mb-1">' + _hitl('hitl.category', 'Category') + '：' + _esc(category) + '</div>'
        + '<div class="text-xs text-textMuted mb-1">' + contentLabel + '</div>'
        + '<div class="text-sm">' + _esc(content) + '</div>'
        + '</div>'
        + (isDelete ? '<div class="mt-2 text-xs text-red-600 font-medium">⚠️ ' + _hitl('hitl.memoryDeleteIrreversible', 'This cannot be undone') + '</div>' : '')
        + '<div class="flex gap-2 border-t ' + (isDelete ? 'border-red-600/20' : 'border-blue-600/20') + ' pt-3 mt-3">'
        + '<button data-click="submitMemoryConsent" data-click-arg="true" class="flex-1 px-4 py-2 rounded-xl ' + approveClass + ' text-white text-sm font-medium transition flex items-center justify-center gap-1.5">'
        + '<i data-lucide="check" class="w-4 h-4"></i><span>' + _hitl('hitl.memoryConsentApprove', 'Approve') + '</span></button>'
        + '<button data-click="submitMemoryConsent" data-click-arg="false" class="flex-1 px-4 py-2 rounded-xl bg-background border border-borderSubtle text-sm font-medium transition flex items-center justify-center gap-1.5">'
        + '<i data-lucide="x" class="w-4 h-4"></i><span>' + _hitl('hitl.memoryConsentDecline', 'Cancel') + '</span></button>'
        + '</div>';
    targetDiv.appendChild(card);
    if (window.lucide && typeof window.createIconsIn === 'function') {
        window.createIconsIn(card);
    }
}
window.renderMemoryConsentCard = renderMemoryConsentCard;

// ── Multi Consent 批次同意卡（2026-08-25）────────────────────────────────
// 一輪多個提案（記帳＋刪記憶＋增記憶…）一次全部顯示：每張子卡 ✅/❌，
// 底部「全部核准 / 全部拒絕 / 送出」。答案以陣列一次 resume（graph 不必
// 每張卡重跑一次）。未答的子卡視為拒絕（後端 fail-closed）。
var _multiConsentState = null;

function renderMultiConsentCard(idata, targetDiv) {
    if (!targetDiv) return;
    var cards = Array.isArray(idata.cards) ? idata.cards : [];
    if (!cards.length) return;

    _multiConsentState = {
        answers: cards.map(function () { return null; }),
        submitted: false,
    };

    var card = document.createElement('div');
    card.className = 'consent-card multi-consent-card mt-3 rounded-2xl border border-primary/20 bg-primary/5 p-4';
    card.id = 'active-consent-card';

    var itemsHtml = '';
    for (var i = 0; i < cards.length; i++) {
        var c = cards[i];
        var tone = c.danger ? 'border-danger/30 bg-danger/5' : 'border-borderSubtle bg-surfaceHighlight/50';
        var lines = (c.lines || []).map(function (l) {
            return '<div class="text-xs text-textMuted">' + _esc(l) + '</div>';
        }).join('');
        itemsHtml +=
            '<div class="multi-consent-item rounded-xl border ' + tone + ' p-3" data-idx="' + i + '">'
            + '<div class="flex items-start justify-between gap-2">'
            + '<div class="min-w-0">'
            + '<div class="text-sm font-semibold text-secondary">' + _esc((c.icon || '') + ' ' + (c.title || '')) + (c.danger ? ' <span class="text-danger">⚠️</span>' : '') + '</div>'
            + lines
            + '</div>'
            + '<div class="flex gap-1 shrink-0" data-role="toggles">'
            + '<button data-click="multiConsentToggle" data-click-arg="' + i + ':approve" class="multi-approve px-2.5 py-1.5 rounded-lg border border-borderSubtle text-xs font-medium transition">✅ ' + _hitl('hitl.multiApprove', 'Approve') + '</button>'
            + '<button data-click="multiConsentToggle" data-click-arg="' + i + ':deny" class="multi-deny px-2.5 py-1.5 rounded-lg border border-borderSubtle text-xs font-medium transition">❌ ' + _hitl('hitl.multiDeny', 'Reject') + '</button>'
            + '</div>'
            + '</div>'
            + '</div>';
    }

    card.innerHTML =
        '<div class="flex items-start gap-3 mb-3">'
        + '<i data-lucide="list-checks" class="w-5 h-5 text-primary mt-0.5 shrink-0"></i>'
        + '<div class="flex-1 min-w-0">'
        + '<div class="font-semibold">' + _hitl('hitl.multiConsentTitle', 'Confirm multiple actions') + '</div>'
        + '<div class="text-sm text-secondary">' + _esc(idata.message || '') + '</div>'
        + '</div></div>'
        + '<div class="space-y-2 mb-3">' + itemsHtml + '</div>'
        + '<div class="flex flex-wrap gap-2 border-t border-borderSubtle pt-3">'
        + '<button data-click="multiConsentAll" data-click-arg="approve" class="px-3 py-2 rounded-xl bg-primary hover:brightness-110 text-background text-xs font-bold transition">✅ ' + _hitl('hitl.multiApproveAll', 'Approve all') + '</button>'
        + '<button data-click="multiConsentAll" data-click-arg="deny" class="px-3 py-2 rounded-xl bg-surfaceHighlight border border-borderSubtle text-xs font-medium transition">❌ ' + _hitl('hitl.multiDenyAll', 'Reject all') + '</button>'
        + '<button data-click="multiConsentSubmit" class="ml-auto px-4 py-2 rounded-xl bg-primary/10 border border-primary/30 text-primary text-xs font-bold transition">' + _hitl('hitl.multiSubmit', 'Submit') + '</button>'
        + '</div>';

    targetDiv.appendChild(card);
    if (window.lucide && typeof window.createIconsIn === 'function') {
        window.createIconsIn(card);
    }
    _multiConsentUpdateUi();
}
window.renderMultiConsentCard = renderMultiConsentCard;

function _multiConsentSet(idx, val) {
    if (!_multiConsentState || _multiConsentState.submitted) return;
    _multiConsentState.answers[idx] = val;
    _multiConsentUpdateUi();
}

function _multiConsentSetAll(val) {
    if (!_multiConsentState || _multiConsentState.submitted) return;
    _multiConsentState.answers = _multiConsentState.answers.map(function () { return val; });
    _multiConsentUpdateUi();
}

function _multiConsentUpdateUi() {
    var root = document.querySelector('#active-consent-card.multi-consent-card');
    if (!root || !_multiConsentState) return;
    var items = root.querySelectorAll('.multi-consent-item');
    for (var i = 0; i < items.length; i++) {
        var item = items[i];
        var idx = parseInt(item.getAttribute('data-idx'), 10);
        var val = _multiConsentState.answers[idx];
        var appBtn = item.querySelector('.multi-approve');
        var denyBtn = item.querySelector('.multi-deny');
        var set = function (btn, active, tone) {
            btn.classList.toggle('bg-primary', false);
            btn.classList.toggle('text-background', false);
            btn.classList.toggle('bg-danger', false);
            btn.classList.toggle('text-white', false);
            if (active) {
                btn.classList.add(tone === 'approve' ? 'bg-primary' : 'bg-danger');
                btn.classList.add(tone === 'approve' ? 'text-background' : 'text-white');
            }
        };
        set(appBtn, val === 'approve', 'approve');
        set(denyBtn, val === 'deny', 'deny');
        item.style.opacity = val ? '1' : '0.75';
    }
}

function multiConsentToggle(arg) {
    if (!_multiConsentState) return;
    var parts = String(arg || '').split(':');
    var idx = parseInt(parts[0], 10);
    var val = parts[1] === 'approve' ? 'approve' : 'deny';
    if (Number.isFinite(idx)) _multiConsentSet(idx, val);
}
window.multiConsentToggle = multiConsentToggle;

function multiConsentAll(val) {
    _multiConsentSetAll(val === 'approve' ? 'approve' : 'deny');
}
window.multiConsentAll = multiConsentAll;

function multiConsentSubmit() {
    if (!_multiConsentState || _multiConsentState.submitted) return;
    // 未作答的子卡不得靜默送出——2026-08-25 線上事故：使用者只答了部分子卡，
    // 未答的被後端 fail-closed 視為拒絕，#27 從此卡死。這裡擋下並提示。
    var unanswered = [];
    _multiConsentState.answers.forEach(function (a, idx) {
        if (a === null || a === undefined) unanswered.push(idx);
    });
    if (unanswered.length > 0) {
        var root = document.querySelector('#active-consent-card.multi-consent-card');
        if (root) {
            unanswered.forEach(function (idx) {
                var item = root.querySelector('.multi-consent-item[data-idx="' + idx + '"]');
                if (item) {
                    item.classList.add('ring-2', 'ring-primary/50', 'animate-pulse');
                    setTimeout(function () {
                        item.classList.remove('ring-2', 'ring-primary/50', 'animate-pulse');
                    }, 1800);
                }
            });
        }
        var msg = window.I18n
            ? window.I18n.t('hitl.multiUnanswered', { n: unanswered.length })
            : '還有 ' + unanswered.length + ' 張卡未確認，請先逐張核准或拒絕';
        if (window.showToast) {
            window.showToast(msg, 'warning');
        } else {
            console.warn('[multiConsent]', msg);
        }
        return;
    }
    _multiConsentState.submitted = true;
    var answers = _multiConsentState.answers.map(function (a) {
        return { action: a === 'approve' ? 'approve' : 'deny', edited_fields: {} };
    });
    window.submitHITLAnswer(JSON.stringify(answers));
}
window.multiConsentSubmit = multiConsentSubmit;

// ── Journal 記帳確認卡（金流 HITL：Propose → Confirm → Commit）──────────
// record_entry 工具只提案（凍結匯率），本卡核准後才寫入統一帳本。
// expense 紅 / income 綠 / trade 藍；支援「先編輯再核准」（amount/currency/category/note）。
var _JOURNAL_CATS = [
    { id: 'food', icon: 'utensils', label: 'Food' },
    { id: 'transport', icon: 'car', label: 'Transport' },
    { id: 'housing', icon: 'home', label: 'Housing' },
    { id: 'shopping', icon: 'shopping-bag', label: 'Shopping' },
    { id: 'entertainment', icon: 'film', label: 'Entertainment' },
    { id: 'medical', icon: 'heart-pulse', label: 'Medical' },
    { id: 'education', icon: 'book-open', label: 'Education' },
    { id: 'investment', icon: 'trending-up', label: 'Investment' },
    { id: 'income', icon: 'banknote', label: 'Income' },
    { id: 'other', icon: 'package', label: 'Other' },
];
var _JOURNAL_CAT_KEY = function (id) { return 'journal.cat_' + id; };
var _journalConsentState = { editing: false, data: null, target: null };

function _journalCatMeta(catId) {
    for (var i = 0; i < _JOURNAL_CATS.length; i++) {
        if (_JOURNAL_CATS[i].id === catId) return _JOURNAL_CATS[i];
    }
    return _JOURNAL_CATS[_JOURNAL_CATS.length - 1]; // other
}

function _journalTypeMeta(entryType) {
    if (entryType === 'income') {
        return { icon: 'banknote', tone: 'border-green-600/30 bg-green-500/5', iconColor: 'text-green-600', approveClass: 'bg-green-600 hover:brightness-110' };
    }
    if (entryType === 'trade') {
        return { icon: 'trending-up', tone: 'border-blue-600/30 bg-blue-500/5', iconColor: 'text-blue-600', approveClass: 'bg-blue-600 hover:brightness-110' };
    }
    return { icon: 'credit-card', tone: 'border-amber-600/30 bg-amber-500/5', iconColor: 'text-amber-600', approveClass: 'bg-amber-600 hover:brightness-110' };
}

function _journalFmtAmount(n) {
    var v = Number(n);
    if (!isFinite(v)) return String(n);
    return v % 1 === 0 ? String(v) : v.toFixed(Math.abs(v) < 1 ? 6 : 2).replace(/0+$/, '').replace(/\.$/, '');
}

function renderJournalConsentCard(idata, targetDiv) {
    if (!targetDiv) return;
    _journalConsentState = { editing: false, data: idata, target: targetDiv };
    _renderJournalCard();
}
window.renderJournalConsentCard = renderJournalConsentCard;

function _renderJournalCard() {
    var st = _journalConsentState;
    var idata = st.data || {};
    var targetDiv = st.target;
    if (!targetDiv) return;

    var entryType = idata.entry_type || 'expense';
    var meta = _journalTypeMeta(entryType);
    var cat = _journalCatMeta(idata.category || 'other');
    var msg = idata.message || _hitl('hitl.journalPending', 'Written to the ledger only after you confirm.');

    var isTrade = entryType === 'trade';
    var amountStr = _journalFmtAmount(idata.amount) + ' ' + (idata.currency || 'TWD');
    var converted = idata.converted_amount;
    var rate = idata.exchange_rate;
    var base = idata.base_currency || 'TWD';
    var convStr = converted != null && Number(converted) > 0
        ? '~NT$' + Number(converted).toLocaleString(undefined, { maximumFractionDigits: 0 })
        : '';
    var rateStr = rate
        ? '1 ' + (idata.currency || '') + ' ≈ NT$' + _journalFmtAmount(rate)
        : '';

    var sideLabel = idata.side === 'sell'
        ? _hitl('hitl.journalSell', 'Sell')
        : _hitl('hitl.journalBuy', 'Buy');

    // 舊卡移除（編輯模式重繪用）
    var old = document.getElementById('active-consent-card');
    if (old && old.parentNode) old.parentNode.removeChild(old);

    var card = document.createElement('div');
    card.className = 'consent-card journal-consent-card mt-3 rounded-2xl border ' + meta.tone + ' p-4';
    card.id = 'active-consent-card';

    var bodyHtml;
    if (!st.editing) {
        // ── 檢視模式 ──
        var tradeRows = '';
        if (isTrade) {
            var dirLabel = idata.direction === 'short'
                ? ' ' + _hitl('hitl.journalShort', 'short')
                : (idata.direction === 'long' ? ' ' + _hitl('hitl.journalLong', 'long') : '');
            var levStr = Number(idata.leverage) > 1 ? ' ' + Number(idata.leverage) + 'x' : '';
            tradeRows =
                '<div class="text-xs text-textMuted mt-1">'
                + _hitl('hitl.journalSymbol', 'Symbol') + '：<span class="text-sm text-primary font-medium">' + _esc(idata.symbol || '') + '</span>'
                + ' · ' + _esc(sideLabel) + ' ' + _journalFmtAmount(idata.quantity)
                + (dirLabel ? ' · ' + _esc(dirLabel) : '')
                + (levStr ? ' · ' + levStr : '')
                + '</div>';
        }
        bodyHtml =
            '<div class="rounded-lg bg-surfaceHighlight/50 p-3 border border-borderSubtle mb-2">'
            + '<div class="flex items-baseline gap-2 flex-wrap">'
            + '<span class="text-xl font-bold">' + _esc(amountStr) + '</span>'
            + (convStr ? '<span class="text-sm text-secondary">' + _esc(convStr) + '</span>' : '')
            + '</div>'
            + '<div class="flex items-center gap-1.5 mt-2">'
            + '<i data-lucide="' + cat.icon + '" class="w-4 h-4 text-textMuted"></i>'
            + '<span class="text-sm text-secondary">' + _esc(_hitl(_JOURNAL_CAT_KEY(cat.id), cat.label)) + '</span>'
            + '</div>'
            + (idata.note ? '<div class="text-xs text-textMuted mt-1">📝 ' + _esc(idata.note) + '</div>' : '')
            + tradeRows
            + (rateStr ? '<div class="text-xs text-textMuted mt-2">💱 ' + _esc(rateStr) + ' <span class="text-textMuted/70">(' + _esc(_hitl('hitl.journalRateFrozen', 'frozen at proposal time')) + ')</span></div>' : '')
            + '</div>';
    } else {
        // ── 編輯模式：amount / currency / category / note ──
        var catOptions = '';
        for (var i = 0; i < _JOURNAL_CATS.length; i++) {
            var c = _JOURNAL_CATS[i];
            catOptions += '<option value="' + c.id + '"' + (c.id === cat.id ? ' selected' : '') + '>'
                + _esc(_hitl(_JOURNAL_CAT_KEY(c.id), c.label)) + '</option>';
        }
        bodyHtml =
            '<div class="rounded-lg bg-surfaceHighlight/50 p-3 border border-borderSubtle mb-2 space-y-2">'
            + '<div class="grid grid-cols-2 gap-2">'
            + '<div><label class="text-xs text-textMuted block mb-1">' + _esc(_hitl('hitl.journalAmount', 'Amount')) + '</label>'
            + '<input id="jc-amount" type="number" step="any" min="0" value="' + _esc(String(idata.amount != null ? idata.amount : '')) + '" class="w-full px-2.5 py-1.5 rounded-lg bg-background border border-borderSubtle text-sm"></div>'
            + '<div><label class="text-xs text-textMuted block mb-1">' + _esc(_hitl('hitl.journalCurrency', 'Currency')) + '</label>'
            + '<input id="jc-currency" type="text" maxlength="8" value="' + _esc(idata.currency || 'TWD') + '" class="w-full px-2.5 py-1.5 rounded-lg bg-background border border-borderSubtle text-sm uppercase"></div>'
            + '</div>'
            + '<div><label class="text-xs text-textMuted block mb-1">' + _esc(_hitl('hitl.journalCategory', 'Category')) + '</label>'
            + '<select id="jc-category" class="w-full px-2.5 py-1.5 rounded-lg bg-background border border-borderSubtle text-sm">' + catOptions + '</select></div>'
            + '<div><label class="text-xs text-textMuted block mb-1">' + _esc(_hitl('hitl.journalNote', 'Note')) + '</label>'
            + '<input id="jc-note" type="text" maxlength="200" value="' + _esc(idata.note || '') + '" class="w-full px-2.5 py-1.5 rounded-lg bg-background border border-borderSubtle text-sm"></div>'
            + '</div>'
            + '<div class="text-xs text-textMuted">' + _esc(_hitl('hitl.journalEditHint', 'Changing the amount or currency re-converts at the latest rate')) + '</div>';
    }

    var editBtnLabel = st.editing
        ? _hitl('hitl.journalEditDone', 'Done editing')
        : _hitl('hitl.journalEdit', 'Edit');

    card.innerHTML =
        // 「編輯」是在改上面的內容，放卡片右上角；底下只剩決定：左取消、右確認記入（常見的左次要右主要）。
        // 三顆擠一行時手機上「確認記入」會折成兩行；上下堆疊（確認一整行、編輯／取消在下）則次要動作
        // 孤零零擺在最底下、位置怪（2026-10-05 DANNY 兩次手機截圖）。
        '<div class="flex items-start gap-3 mb-3">'
        + '<i data-lucide="' + meta.icon + '" class="w-5 h-5 ' + meta.iconColor + ' mt-0.5 shrink-0"></i>'
        + '<div class="flex-1 min-w-0">'
        + '<div class="font-semibold">' + _esc(_hitl('hitl.journalTitle', 'Confirm ledger entry')) + '</div>'
        + '<div class="text-sm text-secondary">' + _esc(msg) + '</div>'
        + '</div>'
        + '<button data-click="toggleJournalEdit" class="shrink-0 px-2.5 py-1.5 rounded-lg bg-background border border-borderSubtle text-xs font-medium whitespace-nowrap text-textMuted hover:text-textMain transition inline-flex items-center gap-1">'
        + '<i data-lucide="pencil" class="w-3.5 h-3.5"></i><span>' + _esc(editBtnLabel) + '</span></button>'
        + '</div>'
        + bodyHtml
        // 容器維持 .flex.gap-2.border-t（送出後的清除邏輯靠這組 class 找按鈕列）
        + '<div class="flex gap-2 border-t border-borderSubtle pt-3 mt-3">'
        + '<button data-click="submitJournalConsent" data-click-arg="false" class="flex-1 px-3 py-2.5 rounded-xl bg-background border border-borderSubtle text-sm font-medium whitespace-nowrap transition flex items-center justify-center gap-1.5">'
        + '<i data-lucide="x" class="w-4 h-4"></i><span>' + _esc(_hitl('hitl.journalDecline', 'Cancel')) + '</span></button>'
        + '<button data-click="submitJournalConsent" data-click-arg="true" class="flex-1 px-3 py-2.5 rounded-xl ' + meta.approveClass + ' text-white text-sm font-medium whitespace-nowrap transition flex items-center justify-center gap-1.5">'
        + '<i data-lucide="check" class="w-4 h-4"></i><span>' + _esc(_hitl('hitl.journalApprove', 'Confirm')) + '</span></button>'
        + '</div>';

    targetDiv.appendChild(card);
    if (window.lucide && typeof window.createIconsIn === 'function') {
        window.createIconsIn(card);
    }
}

function toggleJournalEdit() {
    var st = _journalConsentState;
    st.editing = !st.editing;
    _renderJournalCard();
}
window.toggleJournalEdit = toggleJournalEdit;

function submitJournalConsent(approved) {
    var st = _journalConsentState;
    var isApproved = approved === true || approved === 'true';
    var payload = { action: 'journal_entry', approved: isApproved };

    // 編輯模式下核准 → 收集使用者改過的欄位（後端以 edited_fields 覆蓋提案）
    if (isApproved && st.editing) {
        var edited = {};
        var amountEl = document.getElementById('jc-amount');
        var currencyEl = document.getElementById('jc-currency');
        var categoryEl = document.getElementById('jc-category');
        var noteEl = document.getElementById('jc-note');
        if (amountEl && amountEl.value !== '' && isFinite(Number(amountEl.value))) {
            edited.amount = Number(amountEl.value);
        }
        if (currencyEl && currencyEl.value.trim()) {
            edited.currency = currencyEl.value.trim().toUpperCase();
        }
        if (categoryEl && categoryEl.value) {
            edited.category = categoryEl.value;
        }
        if (noteEl) {
            edited.note = noteEl.value.trim();
        }
        payload.edited_fields = edited;
    }
    window.submitHITLAnswer(JSON.stringify(payload));
}
window.submitJournalConsent = submitJournalConsent;

// Skill/Memory 同意/拒絕送回（resume 路徑）
function submitSkillConsent(approved) {
    var isApproved = approved === true || approved === 'true';
    var payload = JSON.stringify({ action: 'skill_consent', approved: isApproved });
    window.submitHITLAnswer(payload);
}
window.submitSkillConsent = submitSkillConsent;

function submitMemoryConsent(approved) {
    var isApproved = approved === true || approved === 'true';
    var payload = JSON.stringify({ action: 'memory_consent', approved: isApproved });
    window.submitHITLAnswer(payload);
}
window.submitMemoryConsent = submitMemoryConsent;

function renderPreResearchCard(idata, targetDiv) {
    console.log('[renderPreResearchCard] idata:', idata);
    if (!targetDiv) return;
    const summary = idata.research_summary || '';
    const message = idata.message || (window.I18n ? window.I18n.t('hitl.dataReady') : 'Real-time data has been compiled for your reference:');
    const question = idata.question || (window.I18n ? window.I18n.t('hitl.deeperDirection') : 'Is there a specific direction you would like to explore in depth?');
    // 問題文字來自後端／模型，當純文字轉義後再進 innerHTML
    const questionHtml = window.SecurityUtils ? window.SecurityUtils.escapeHTML(question) : String(question).replace(/</g, '&lt;');

    // 若後端有 Q&A 回答，在主聊天顯示（純問答泡泡，不加 AI War Room 按鈕）
    if (idata.qa_question && idata.qa_answer) {
        const container = document.getElementById('chat-messages');
        if (container) {
            const qaDiv = document.createElement('div');
            qaDiv.className = 'message-bubble bot-bubble prose';
            // XSS Fix: 使用 SecurityUtils 清理 HTML
            const qRaw = window.md ? window.md.renderInline(idata.qa_question) : idata.qa_question;
            const aRaw = window.md ? window.md.render(idata.qa_answer) : idata.qa_answer;
            const qHtml = window.SecurityUtils ? window.SecurityUtils.sanitizeHTML(qRaw) : qRaw.replace(/</g, '&lt;');
            const aHtml = window.SecurityUtils ? window.SecurityUtils.sanitizeHTML(aRaw) : aRaw.replace(/</g, '&lt;');
            qaDiv.innerHTML = `<p class="text-xs text-textMuted/60 mb-1">💬 ${qHtml}</p>${aHtml}`;
            container.appendChild(qaDiv);
            container.scrollTop = container.scrollHeight;
        }
    }

    // XSS Fix: 使用 SecurityUtils 清理 HTML
    const summaryRaw =
        summary && window.md
            ? window.md.render(summary)
            : summary
              ? summary.replace(/\n/g, '<br>')
              : '';
    const summaryHtml = window.SecurityUtils
        ? window.SecurityUtils.sanitizeHTML(summaryRaw)
        : summaryRaw.replace(/</g, '&lt;');
    const messageRaw = window.md ? window.md.renderInline(message) : message;
    const messageHtml = window.SecurityUtils
        ? window.SecurityUtils.sanitizeHTML(messageRaw)
        : messageRaw.replace(/</g, '&lt;');

    // Use a compact card if no summary is provided (e.g., follow-up Q&A)
    if (!summary) {
        targetDiv.innerHTML = `
            <div class="pre-research-card-compact rounded-2xl border border-blue-500/20 bg-blue-500/5 overflow-hidden">
                <div class="px-5 pt-4 pb-2">
                    <p class="text-sm text-secondary mb-2">${messageHtml}</p>
                    <p class="text-xs text-textMuted mb-2">${questionHtml}</p>
                </div>
                <div class="flex gap-2 px-5 py-3 border-t border-borderSubtle bg-background/30">
                    <button data-click="submitPreResearch"
                        class="flex-1 bg-primary/20 hover:bg-primary/30 text-primary border
                               border-primary/30 rounded-xl py-2 text-sm font-medium transition">
                        ${window.I18n ? window.I18n.t('hitl.confirmStartPlanning') : 'Confirm to start planning'}
                    </button>
                    <!-- No cancel button needed in compact mode usually, but can keep -->
                </div>
            </div>`;
    } else {
        // Full card with summary
        targetDiv.innerHTML = `
            <div class="pre-research-card rounded-2xl border border-blue-500/20 bg-blue-500/5 overflow-hidden">
                <div class="px-5 pt-5 pb-4">
                    <p class="text-sm text-secondary mb-3">${messageHtml}</p>
                    <div class="prose prose-sm prose-invert max-w-none text-secondary leading-relaxed
                                max-h-72 overflow-y-auto bg-surfaceHighlight rounded-xl px-4 py-3 mb-4">
                        ${summaryHtml}
                    </div>
                    <p class="text-sm text-secondary mb-2">${questionHtml}</p>
                    <!-- Pre-Research Input Removed: Use main chat input -->
                </div>
                <div class="flex gap-2 px-5 py-4 border-t border-borderSubtle bg-background/30">
                    <button data-click="submitPreResearch"
                        class="flex-1 bg-primary/20 hover:bg-primary/30 text-primary border
                               border-primary/30 rounded-xl py-2.5 text-sm font-medium transition">
                        ${window.I18n ? window.I18n.t('hitl.startPlanning') : 'Start planning'}
                    </button>
                    <button data-click="submitHITLAnswer" data-click-arg="cancel"
                        class="px-4 bg-surfaceHighlight hover:bg-surfaceHighlight text-secondary border
                               border-borderLight rounded-xl py-2.5 text-sm transition">
                        ${window.I18n ? window.I18n.t('common.cancel') : 'Cancel'}
                    </button>
                </div>
            </div>`;
    }

    if (window.lucide) createIconsIn(targetDiv);
}
window.renderPreResearchCard = renderPreResearchCard;

function submitPreResearch() {
    // No specific input from card anymore, just confirm.
    // If user wants to specify, they type in main chat.
    window.submitHITLAnswer('confirm');
}
window.submitPreResearch = submitPreResearch;

// ── Removed client-side _isDiscussionQuestion check to rely on backend ──

// ── Plan Card (confirm_plan HITL) ──────────────────────────────────────────────

function renderPlanCard(interruptData, targetDiv) {
    if (!targetDiv) return;
    const plan = interruptData.plan || [];

    // 計畫為空時顯示錯誤訊息，不渲染空計畫卡
    if (plan.length === 0) {
        targetDiv.innerHTML = `
            <div class="rounded-2xl border border-red-500/20 bg-red-500/5 px-5 py-4 text-sm text-textMuted">
                ⚠️ ${window.I18n ? window.I18n.t('hitl.cannotCreatePlan') : 'Unable to create an execution plan for this query. Please try describing your question differently.'}
            </div>`;
        return;
    }

    const message = interruptData.message || (window.I18n ? window.I18n.t('hitl.plannedSteps') : 'For your question, I have planned the following analysis steps:');

    const stepsHtml = plan
        .map(
            (t) => `
        <div class="plan-step flex items-center gap-3 py-2.5 px-3 rounded-xl hover:bg-surfaceHighlight transition"
             data-step="${t.step}" data-selected="true">
            <div class="plan-check w-5 h-5 rounded border border-primary/30 bg-primary/10
                        flex items-center justify-center flex-shrink-0">
                <i data-lucide="check" class="w-3 h-3 text-primary"></i>
            </div>
            <span class="text-base leading-none">${t.icon || '🔧'}</span>
            <span class="text-sm text-secondary flex-1">${t.description || t.agent}</span>
        </div>`
        )
        .join('');

    // Negotiation Response as a clearer "Chat" element BEFORE the card
    const negotiationResponse = interruptData.negotiation_response
        ? `<div class="mb-4 text-base text-secondary leading-relaxed border-l-2 border-primary/40 pl-3">
             <span class="text-xs font-bold text-primary block mb-1">${window.I18n ? window.I18n.t('hitl.description') : '🤖 Description:'}</span>
             ${interruptData.negotiation_response}
           </div>`
        : '';

    targetDiv.innerHTML = `
        ${negotiationResponse}
        <div class="plan-card rounded-2xl border border-primary/20 bg-primary/5 overflow-hidden"
             id="active-plan-card">
            <div class="px-5 pt-5 pb-3">
                <div class="flex items-center gap-2 mb-3">
                    <div class="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center">
                        <i data-lucide="list-checks" class="w-4 h-4 text-primary"></i>
                    </div>
                    <span class="text-sm font-medium text-primary">${window.I18n ? window.I18n.t('hitl.aiExecutionPlan') : 'AI Execution Plan'}</span>
                </div>
                
                <p class="text-sm text-textMuted mb-3">${message}</p>
                <div class="plan-steps space-y-0.5">${stepsHtml}</div>
                
                <!-- Negotiation Instructions (Shown in custom mode) -->
                <div id="plan-negotiate-container" class="hidden mt-3 pt-3 border-t border-borderSubtle animate-fade-in-up">
                    <p class="text-xs text-textMuted bg-surfaceHighlight px-3 py-2 rounded-lg border border-borderLight">
                        <i data-lucide="info" class="w-3 h-3 inline mr-1"></i>
                        ${window.I18n ? window.I18n.t('hitl.adjustPlanHint') : 'To adjust the plan, type in the chat input below.'}
                    </p>
                </div>
            </div>
            <div class="plan-actions flex gap-2 px-5 py-4 border-t border-borderSubtle bg-background/30">
                <button id="plan-execute-btn" data-click="executePlan" data-click-arg="all"
                    class="flex-1 py-2.5 bg-primary hover:bg-primary/80 text-background font-bold
                           rounded-xl text-sm transition flex items-center justify-center gap-1.5">
                    <i data-lucide="play" class="w-4 h-4"></i>${window.I18n ? window.I18n.t('hitl.executeAll') : 'Execute All'}
                </button>
                <button id="plan-customize-btn" data-click="togglePlanCustomize"
                    class="px-4 py-2.5 bg-surfaceHighlight hover:bg-surfaceHighlight text-textMuted rounded-xl
                           text-sm transition flex items-center gap-1.5 ${interruptData.negotiation_limit_reached ? 'hidden' : ''}"
                    ${interruptData.negotiation_limit_reached ? 'disabled' : ''}>
                    <i data-lucide="settings-2" class="w-4 h-4"></i>${window.I18n ? window.I18n.t('hitl.customSelect') : 'Custom Select'}
                </button>
                <button data-click="executePlan" data-click-arg="cancel"
                    class="px-4 py-2.5 bg-surfaceHighlight hover:bg-surfaceHighlight text-textMuted
                           hover:text-danger rounded-xl text-sm transition" data-i18n="common.cancel">
                    Cancel
                </button>
            </div>
        </div>`;
    if (window.lucide) createIconsIn(targetDiv);
}
window.renderPlanCard = renderPlanCard;

function togglePlanCustomize() {
    const card = document.getElementById('active-plan-card');
    if (!card) return;

    // Toggle class
    const isCustom = card.classList.toggle('plan-custom-mode');

    const executeBtn = document.getElementById('plan-execute-btn');
    const customizeBtn = document.getElementById('plan-customize-btn');
    const negotiateContainer = document.getElementById('plan-negotiate-container');

    if (isCustom) {
        // Show negotiation instruction
        if (negotiateContainer) negotiateContainer.classList.remove('hidden');

        // Enable clicking steps to toggle
        card.querySelectorAll('.plan-step').forEach((step) => {
            step.style.cursor = 'pointer';
            step.onclick = () => window.togglePlanStep(step);
        });

        // Update Buttons
        if (customizeBtn) {
            customizeBtn.innerHTML = `<i data-lucide="rotate-ccw" class="w-4 h-4"></i>${window.I18n ? window.I18n.t('hitl.reset') : 'Reset'}`;
            customizeBtn.classList.add('bg-primary/10', 'text-primary');
        }

        // Initial button state update
        window.updateCustomExecuteButton();
    } else {
        // Hide negotiation instruction
        if (negotiateContainer) {
            negotiateContainer.classList.add('hidden');
        }

        // Reset all steps to selected
        card.querySelectorAll('.plan-step').forEach((step) => {
            step.dataset.selected = 'true';
            step.style.cursor = '';
            step.onclick = null;
            step.classList.remove('opacity-40');
            const check = step.querySelector('.plan-check');
            if (check) {
                check.className =
                    'plan-check w-5 h-5 rounded border border-primary/30 bg-primary/10 flex items-center justify-center flex-shrink-0';
                check.innerHTML = '<i data-lucide="check" class="w-3 h-3 text-primary"></i>';
            }
        });

        // Reset Buttons
        if (executeBtn) {
            executeBtn.onclick = () => window.executePlan('all');
            executeBtn.classList.remove('bg-surfaceHighlight', 'text-background');
            executeBtn.classList.add('bg-primary', 'text-background');
            executeBtn.innerHTML = `<i data-lucide="play" class="w-4 h-4"></i>${window.I18n ? window.I18n.t('hitl.executeAll') : 'Execute All'}`;
        }
        if (customizeBtn) {
            customizeBtn.innerHTML = `<i data-lucide="settings-2" class="w-4 h-4"></i>${window.I18n ? window.I18n.t('hitl.customPick') : 'Customize/Pick'}`;
            customizeBtn.classList.remove('bg-primary/10', 'text-primary');
        }
    }
    if (lucide) createIconsIn(document.getElementById('chat-messages'));
}
window.togglePlanCustomize = togglePlanCustomize;

function updateCustomExecuteButton() {
    const executeBtn = document.getElementById('plan-execute-btn');
    if (!executeBtn) return;

    // Main chat input is used for negotiation now, but execute button here is for "selected steps".
    // "execute_custom" action sends { selected_steps: [...] }.

    executeBtn.onclick = () => window.executePlan('custom');

    // Style remains Primary for execution
    executeBtn.classList.remove('bg-surfaceHighlight', 'text-background');
    executeBtn.classList.add('bg-primary', 'text-background');
    executeBtn.innerHTML = `<i data-lucide="play" class="w-4 h-4"></i>${window.I18n ? window.I18n.t('hitl.executeSelected') : 'Execute Selected Steps'}`;

    createIconsIn(executeBtn);
}
window.updateCustomExecuteButton = updateCustomExecuteButton;

function togglePlanStep(step) {
    const wasSelected = step.dataset.selected === 'true';
    const nowSelected = !wasSelected;
    step.dataset.selected = String(nowSelected);
    step.classList.toggle('opacity-40', !nowSelected);
    const check = step.querySelector('.plan-check');
    if (!check) return;
    if (nowSelected) {
        check.className =
            'plan-check w-5 h-5 rounded border border-primary/30 bg-primary/10 flex items-center justify-center flex-shrink-0';
        check.innerHTML = '<i data-lucide="check" class="w-3 h-3 text-primary"></i>';
        if (lucide) lucide.createIcons({ nodes: [check] });
    } else {
        check.className =
            'plan-check w-5 h-5 rounded border border-borderLight flex items-center justify-center flex-shrink-0';
        check.innerHTML = '';
    }
}
window.togglePlanStep = togglePlanStep;

function executePlan(mode) {
    if (mode === 'cancel') {
        window.submitHITLAnswer(JSON.stringify({ action: 'cancel' }));
        return;
    }
    if (mode === 'all') {
        window.submitHITLAnswer(JSON.stringify({ action: 'execute' }));
        return;
    }

    if (mode === 'custom') {
        // Check negotiation text first
        const input = document.getElementById('plan-negotiate-input');
        const text = input ? input.value.trim() : '';

        if (text) {
            window.submitHITLAnswer(JSON.stringify({ action: 'modify_request', text: text }));
            return;
        }

        const card = document.getElementById('active-plan-card');
        if (!card) {
            window.submitHITLAnswer('execute');
            return;
        }

        const selected = [];
        card.querySelectorAll('.plan-step').forEach((step) => {
            if (step.dataset.selected === 'true') selected.push(parseInt(step.dataset.step, 10));
        });

        if (selected.length === 0) {
            // Hint user to select something or cancel
            alert(window.I18n ? window.I18n.t('chat.hitlSelectStep') : 'Please select at least one step, or click "Cancel"');
            return;
        }

        window.submitHITLAnswer(
            JSON.stringify({ action: 'execute_custom', selected_steps: selected })
        );
    }
}
window.executePlan = executePlan;

async function submitHITLAnswer(answer) {
    if (!answer || !answer.trim()) return;
    if (!_hitlContext) return;

    const ctx = _hitlContext;
    _hitlContext = null;

    // consent gate resume 不建新框——consent 不是 plan，舊框只是 loading 狀態，
    // 建新框會導致「兩個分析框」（線上 #錢包地址案例）。直接更新原本的框。
    const isConsentResume = answer.includes('"action":"consent"') || answer.includes("'action':'consent'");

    // ── History Preservation ──
    // Instead of overwriting the old bot message, we mark it as "done" by REMOVING the buttons
    // and create a NEW bot message for the response/next step.
    // ── History Preservation ──
    // Instead of overwriting the old bot message, we mark it as "done" by REMOVING the buttons

    // 1. Clean up specific context message if it exists
    if (ctx.botMsgDiv) {
        const oldBtns = ctx.botMsgDiv.querySelectorAll('button');
        oldBtns.forEach((b) => b.remove());
        const btnContainer = ctx.botMsgDiv.querySelector('.flex.gap-2.border-t');
        if (btnContainer) btnContainer.remove();

        const oldCard = ctx.botMsgDiv.querySelector('#active-plan-card');
        if (oldCard) oldCard.removeAttribute('id');
    }

    // 2. Force Clean: Remove ALL persistence buttons from previous HITL cards in the chat
    // This ensures even if context was lost, we don't leave active buttons.
    // 2026-08-22：補 .consent-card（skill/memory/journal 三款同意卡）——
    // 送出/拒絕後按鈕列要收掉，否則卡片還能再按（殘留可雙擊重送）。
    document
        .querySelectorAll(
            '.pre-research-card button, .plan-card button, .pre-research-card-compact button, .consent-card button'
        )
        .forEach((btn) => {
            // If the button is not in the NEW botMsgDiv (which isn't created yet), remove it.
            // Since we haven't created the new div yet, ALL existing buttons are "old".
            const parent = btn.closest('.flex');
            if (
                parent &&
                parent.className.includes('gap-2') &&
                parent.className.includes('border-t')
            ) {
                parent.remove();
            } else {
                btn.remove();
            }
        });

    // consent gate：不建新框（避免「兩個分析框」），直接用原本的 ctx.botMsgDiv。
    // plan confirmation 才建新框（舊框=計畫，新框=執行結果）。
    const botMsgDiv = isConsentResume && ctx.botMsgDiv
        ? ctx.botMsgDiv
        : appendMessage('bot', '');
    ctx.botMsgDiv = botMsgDiv; // Update context to point to div for streaming

    // Initial "Thinking" UI
    botMsgDiv.innerHTML = `
        <div class="process-container" style="border-style: dashed; opacity: 0.7;">
            <div class="flex items-center gap-2 px-4 py-3">
                <i data-lucide="loader-2" class="w-4 h-4 animate-spin text-primary"></i>
                <span class="font-medium text-sm text-textMuted">${window.I18n ? window.I18n.t('hitl.thinkingResearch') : 'AI is researching...'}</span>
            </div>
        </div>`;
    if (window.lucide) createIconsIn(botMsgDiv);

    if (!AuthManager.currentUser) {
        if (typeof showToast === 'function') showToast(window.I18n ? window.I18n.t('auth.loginRequired') : 'Please log in first', 'warning');
        return;
    }
    let fullContent = '';
    let reasoningContent = '';
    const HITL_REQUEST_TIMEOUT_MS = 180000;
    const HITL_STREAM_IDLE_TIMEOUT_MS = 90000;
    let requestTimeoutId = null;
    let streamIdleTimeoutId = null;

    const controller = new AbortController();

    const clearHitlTimeouts = () => {
        if (requestTimeoutId) {
            clearTimeout(requestTimeoutId);
            requestTimeoutId = null;
        }
        if (streamIdleTimeoutId) {
            clearTimeout(streamIdleTimeoutId);
            streamIdleTimeoutId = null;
        }
    };

    const armHitlStreamIdleTimeout = () => {
        if (streamIdleTimeoutId) clearTimeout(streamIdleTimeoutId);
        streamIdleTimeoutId = setTimeout(() => {
            controller.abort(new Error('HITL_STREAM_IDLE_TIMEOUT'));
        }, HITL_STREAM_IDLE_TIMEOUT_MS);
    };

    // 正文＋思考區塊（思考擺在答案前面，同 sendMessage 的 flushStreamRender）
    const renderResume = (isStreaming, elapsed) => {
        if (!ctx.botMsgDiv) return;
        ctx.botMsgDiv.innerHTML = renderStoredBotMessage(fullContent, isStreaming, elapsed);
        if (reasoningContent && window.renderThinkingBlock) {
            ctx.botMsgDiv.insertAdjacentHTML(
                'afterbegin',
                window.renderThinkingBlock(reasoningContent, isStreaming)
            );
            if (window.lucide) createIconsIn(ctx.botMsgDiv);
        }
    };

    // 續傳期間比照 sendMessage 標記分析中：Stop 鈕停得了，也不能趁機再送一題
    const sessionId = ctx.sessionId;
    setSessionAnalyzing(true, sessionId);
    setAnalysisController(controller, sessionId);
    if (window.currentSessionId === sessionId) syncChatUIForCurrentSession();

    try {
        requestTimeoutId = setTimeout(() => {
            controller.abort(new Error('HITL_ANALYSIS_TIMEOUT'));
        }, HITL_REQUEST_TIMEOUT_MS);

        const response = await fetch('/api/analyze', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            credentials: 'include',
            body: JSON.stringify({
                message: ctx.originalMessage,
                session_id: ctx.sessionId,
                user_provider: ctx.userProvider,
                user_model: ctx.userSelectedModel,
                preset_id: ctx.presetId || null,
                resume_run_id: ctx.runId || null,
                language: window.I18n?.getLanguage() || 'zh-TW',
                // Ensure resume_answer is an object if it's a JSON string
                resume_answer: (() => {
                    const trimmed = answer.trim();
                    if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
                        try {
                            return JSON.parse(trimmed);
                        } catch (e) {
                            return trimmed;
                        }
                    }
                    return trimmed;
                })(),
            }),
            signal: controller.signal,
        });

        if (!response.ok) {
            const errData = await response.json().catch(() => ({}));
            throw new Error(errData.detail || `Server Error (${response.status})`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let pendingBuffer = '';

        armHitlStreamIdleTimeout();

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            armHitlStreamIdleTimeout();
            const chunk = decoder.decode(value, { stream: true });
            const parsed = window.ChatStreamUI.consumeChunk(pendingBuffer, chunk);
            const lines = parsed.lines;
            pendingBuffer = parsed.pending;

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                let data;
                try {
                    data = JSON.parse(line.substring(6));
                } catch {
                    continue;
                }

                if (data.type === 'run_started') {
                    ctx.runId = data.run_id || null;
                    setSessionRunId(ctx.runId, sessionId);
                    continue;
                }
                if (data.type === 'hitl_question') {
                    // Nested HITL（Q&A 循環／下一張同意卡）：跟即時串流、斷線續傳共用
                    // renderHitlQuestion——這裡原本抄了一份，漏掉 consent_gate，
                    // 同意卡會掉進 clarify fallback。
                    // 問題掛在這個對話自己名下；人已切走（或泡泡已被 loadChatHistory
                    // 換掉）就先不畫，切回來由 resumePendingHitl 走續傳路徑重畫
                    const idata = data.data || {};
                    ctx.hitlType = idata.type;
                    setSessionHitlContext(ctx, sessionId);
                    if (window.currentSessionId === sessionId) {
                        if (ctx.botMsgDiv && ctx.botMsgDiv.isConnected) {
                            if (typeof window.renderHitlQuestion === 'function') {
                                // 先把「思考中」轉圈換成已串流的前言再畫卡（同意卡是 appendChild）
                                const priorHtml =
                                    (reasoningContent && window.renderThinkingBlock
                                        ? window.renderThinkingBlock(reasoningContent, false)
                                        : '') + (fullContent ? renderStoredBotMessage(fullContent, false) : '');
                                ctx.botMsgDiv.innerHTML = priorHtml;
                                window.renderHitlQuestion(idata, ctx.botMsgDiv, priorHtml);
                            }
                        } else if (typeof window.resumePendingHitl === 'function') {
                            window.resumePendingHitl(sessionId);
                        }
                    }
                    return;
                }
                if (data.waiting) return;

                if (data.type === 'progress') {
                    if (window.ChatStreamUI) {
                        window.ChatStreamUI.applyProgress(ctx.botMsgDiv, data.data || {});
                    }
                }

                // 思考 token 獨立累積，不能落到下面的 data.content 分支混進正文
                if (data.type === 'reasoning') {
                    if (data.content) {
                        reasoningContent += data.content;
                        renderResume(true, null);
                    }
                    continue;
                }

                if (data.content) {
                    // type==='final' 是後端的權威完整回覆，要取代累積內容，
                    // 否則和先前串流的 token 接在一起、整段顯示兩次（同 sendMessage）
                    if (data.type === 'final') {
                        fullContent = data.content;
                    } else {
                        fullContent += data.content;
                    }
                    renderResume(true, null);
                }
                if (data.done) {
                    clearHitlTimeouts();
                    if (ctx.botMsgDiv) {
                        const totalTime = ((Date.now() - ctx.startTime) / 1000).toFixed(1);
                        renderResume(false, totalTime);
                        const badge = document.createElement('div');
                        badge.className = 'mt-4 text-xs text-textMuted/60 font-mono';
                        badge.textContent = window.I18n.t('chat.analysisDone', { time: totalTime });
                        ctx.botMsgDiv.appendChild(badge);
                        if (lucide) createIconsIn(ctx.botMsgDiv);
                    }
                    setSessionAnalyzing(false, sessionId);
                    loadSessions();
                }
                if (data.error) {
                    clearHitlTimeouts();
                    if (ctx.botMsgDiv) {
                        ctx.botMsgDiv.innerHTML = `<span class="text-red-400">Error: ${escapeHtml(data.error)}</span>`;
                    }
                    setSessionAnalyzing(false, sessionId);
                }
            }
        }
    } catch (err) {
        console.error('[HITL resume error]', err);
        if (ctx.botMsgDiv) {
            // Fix [object Object] by properly stringifying error detail if it's an object
            // XSS Fix: 使用 escapeHtml 转义错误消息
            let rawError =
                typeof err.message === 'object'
                    ? JSON.stringify(err.message)
                    : err.message || String(err);
            // 手機網路斷線（切 App/網路切換）→ 不要顯示紅字「resume failed」。
            // 後端分析仍在背景跑並會存進對話紀錄，顯示友善提示 + 自動輪詢取回結果。
            if (window.isConnectionLostError && window.isConnectionLostError(err)) {
                console.warn('[HITL resume] connection lost, analysis continues on server:', err);
                if (ctx.botMsgDiv) {
                    ctx.botMsgDiv.innerHTML = `<span class="text-amber-400">${escapeHtml(
                        window.I18n
                            ? window.I18n.t('chat.analysisContinuesInBackground')
                            : 'Connection dropped. The analysis is still running and the result will appear here shortly.'
                    )}</span>`;
                }
                // 輪詢取回背景結果（watchForBackgroundResult 是全域函式）
                if (window.watchForBackgroundResult && ctx.sessionId) {
                    window.watchForBackgroundResult(ctx.sessionId, null, ctx.botMsgDiv);
                }
                return;
            }
            if (err.name === 'AbortError') {
                const abortReason = controller.signal.reason;
                if (abortReason instanceof Error && abortReason.message === 'HITL_ANALYSIS_TIMEOUT') {
                    rawError = window.I18n ? window.I18n.t('hitl.resumeTimeout') : 'Resume analysis timed out. Please narrow down the question and try again.';
                } else if (
                    abortReason instanceof Error &&
                    abortReason.message === 'HITL_STREAM_IDLE_TIMEOUT'
                ) {
                    rawError = window.I18n ? window.I18n.t('hitl.resumeStreamInterrupted') : 'Stream interrupted for too long during resume. Please retry.';
                } else {
                    // 使用者按了 Stop（stopAnalysis 已補「已終止」提示），不是失敗
                    ctx.botMsgDiv.innerHTML = '<span class="text-orange-400">' + (window.I18n ? window.I18n.t('chat.analysisCancelled') : 'Analysis cancelled.') + '</span>';
                    return;
                }
            }
            const errorMsg = escapeHtml(rawError);
            ctx.botMsgDiv.innerHTML = `<span class="text-red-400">${window.I18n ? window.I18n.t('hitl.resumeFailed', { error: errorMsg }) : 'Failed to resume analysis: ' + errorMsg}</span>`;
        }
    } finally {
        clearHitlTimeouts();
        // 與開頭的標記對稱：續傳結束（完成／錯誤／中止／又停下來問）就收掉
        // 這個對話的分析狀態。又停下來問時輸入框要開著，使用者才能回覆
        clearAnalysisController(sessionId);
        setSessionAnalyzing(false, sessionId);
        if (ctx.runId && getSessionRunId(sessionId) === ctx.runId) setSessionRunId(null, sessionId);
        if (window.currentSessionId === sessionId) syncChatUIForCurrentSession();
    }
}
window.submitHITLAnswer = submitHITLAnswer;
// ── End HITL ─────────────────────────────────────────────────────────────────

export {
    renderPreResearchCard,
    submitPreResearch,
    renderPlanCard,
    togglePlanCustomize,
    updateCustomExecuteButton,
    togglePlanStep,
    executePlan,
    submitHITLAnswer,
    renderSkillConsentCard,
    renderMemoryConsentCard,
    submitSkillConsent,
    submitMemoryConsent,
};
