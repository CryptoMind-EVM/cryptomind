// studio-share.html 的頁面腳本（2026-09-25 自 inline <script> 原樣移出：正式站 CSP
// script-src 不再放行 'unsafe-inline'）。classic script——頂層宣告維持全域，
// 執行時機與原 inline 相同（解析到該 <script> 時同步執行）。

(function () {
    'use strict';
    var L = {
        'zh-TW': { title: '提案版本歷史 — CryptoMind Studio', sub: '版本軌跡（唯讀分享）· 依時間排序', curve: '字數成長曲線', chars: '字', content: '內容', diff: 'diff', close: '收合', versions: '個版本', updated: '更新', err: '連結無效或已撤銷。', footer: '此稿件由作者透過 CryptoMind 提案工作台分享 · 唯讀 · 不含帳號資訊', human: '人工', pasteN: '貼上', heavyPaste: '大量貼上', exchange: '教練對話', reviewOnly: '看稿', questions: '教練反問', adopted: '✓ 已採納', dismissed: '✕ 已忽略', onSentence: '針對原句', ai: '採納建議', auto: '自動', adoptedHere: '本版本採納的 AI 建議', aiMarkLegend: '內文反白處＝採納的建議文字；其餘為作者原文。' },
        'zh-CN': { title: '提案版本历史 — CryptoMind Studio', sub: '版本轨迹（唯读分享）· 依时间排序', curve: '字数成长曲线', chars: '字', content: '内容', diff: 'diff', close: '收合', versions: '个版本', updated: '更新', err: '链接无效或已撤销。', footer: '此稿件由作者透过 CryptoMind 提案工作台分享 · 唯读 · 不含账号信息', human: '人工', pasteN: '粘贴', heavyPaste: '大量粘贴', exchange: '教练对话', reviewOnly: '看稿', questions: '教练反问', adopted: '✓ 已采纳', dismissed: '✕ 已忽略', onSentence: '针对原句', ai: '采纳建议', auto: '自动', adoptedHere: '本版本采纳的 AI 建议', aiMarkLegend: '内文反白处＝采纳的建议文字；其余为作者原文。' },
        'en': { title: 'Proposal Version History — CryptoMind Studio', sub: 'Version history (read-only share) · chronological', curve: 'Growth curve', chars: 'chars', content: 'Content', diff: 'diff', close: 'Collapse', versions: 'versions', updated: 'updated', err: 'Link invalid or revoked.', footer: 'Shared via CryptoMind Proposal Studio · read-only · no account info', human: 'Human', pasteN: 'paste', heavyPaste: 'Heavy paste', exchange: 'Coach dialogue', reviewOnly: 'Review', questions: 'Questions', adopted: '✓ Adopted', dismissed: '✕ Dismissed', onSentence: 'on sentence', ai: 'AI adopted', auto: 'Auto', adoptedHere: 'AI suggestions adopted in this version', aiMarkLegend: 'Highlighted text = adopted suggestion; the rest is the author\'s own writing.' },
        'ru': { title: 'История версий предложения — CryptoMind Studio', sub: 'История версий (только чтение)', curve: 'Кривая роста', chars: 'зн.', content: 'Содержание', diff: 'diff', close: 'Свернуть', versions: 'версий', updated: 'обновлено', err: 'Ссылка недействительна или отозвана.', footer: 'Опубликовано через CryptoMind Proposal Studio · только чтение', human: 'Автор', pasteN: 'вставка', heavyPaste: 'Массовая вставка', exchange: 'Диалог с коучем', reviewOnly: 'Разбор', questions: 'Вопросы', adopted: '✓ Принято', dismissed: '✕ Отклонено', onSentence: 'к предложению', ai: 'Принят совет ИИ', auto: 'Авто', adoptedHere: 'Советы ИИ, принятые в этой версии', aiMarkLegend: 'Выделенный текст — принятый совет; остальное — текст автора.' }
    };
    function lang() {
        var n = (navigator.language || 'zh-TW');
        if (n.indexOf('zh') === 0) return n.indexOf('CN') >= 0 || n.indexOf('Hans') >= 0 ? 'zh-CN' : 'zh-TW';
        if (n.indexOf('ru') === 0 || n.indexOf('be') === 0 || n.indexOf('uk') === 0) return 'ru';
        if (n.indexOf('en') === 0) return 'en';
        return 'zh-TW';
    }
    var t = L[lang()];
    document.title = t.title;
    var esc = function (s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    };
    var qs = new URLSearchParams(location.search);
    var token = qs.get('t') || '';

    function srcBadge(source) {
        var cls = source === 'ai_applied' ? 'src-ai' : source === 'autosave' ? 'src-auto' : 'src-human';
        var label = source === 'ai_applied' ? t.ai : source === 'autosave' ? t.auto : t.human;
        return '<span class="badge ' + cls + '">' + esc(label) + '</span>';
    }

    function renderCurve(versions) {
        if (versions.length < 2) return;
        var cum = 0, points = versions.map(function (v) {
            cum += v.char_delta || 0;
            return { no: v.version_no, chars: Math.max(0, cum) };
        });
        // 以實際容器寬度計算座標（等比、無拉伸變形）
        var curveBox = document.getElementById('curve');
        var W = Math.max(240, Math.floor(curveBox.clientWidth || 640));
        var H = 80, PAD_X = 10, PAD_Y = 10;
        var max = Math.max.apply(null, points.map(function (p) { return p.chars; })) || 1;
        var min = Math.min.apply(null, points.map(function (p) { return p.chars; }));
        var span = Math.max(1, max - min);
        var xy = points.map(function (p, i) {
            return [PAD_X + (i / (points.length - 1)) * (W - 2 * PAD_X), H - PAD_Y - ((p.chars - min) / span) * (H - 2 * PAD_Y), p];
        });
        var line = xy.map(function (a) { return a[0].toFixed(1) + ',' + a[1].toFixed(1); }).join(' ');
        var area = 'M' + PAD_X + ',' + (H - PAD_Y) + ' L' + line.split(' ').join(' L') + ' L' + (W - PAD_X) + ',' + (H - PAD_Y) + ' Z';
        var dots = xy.map(function (a, i) {
            var last = i === xy.length - 1;
            return '<circle cx="' + a[0].toFixed(1) + '" cy="' + a[1].toFixed(1) + '" r="' + (last ? 4 : 2.5) + '" fill="' + (last ? 'rgb(var(--color-primary))' : 'rgb(var(--color-surface))') + '" stroke="rgb(var(--color-primary))" stroke-width="1.5"><title>v' + a[2].no + ' · ' + a[2].chars + ' ' + t.chars + '</title></circle>';
        }).join('');
        curveBox.innerHTML = '<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '" style="max-width:100%;height:' + H + 'px"><path d="' + area + '" fill="rgb(var(--color-primary))" fill-opacity="0.08"/><polyline points="' + line + '" fill="none" stroke="rgb(var(--color-primary))" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>' + dots + '</svg>';
        // 縮放重繪
        if (!renderCurve._bound) {
            renderCurve._bound = true;
            var tmr = null;
            window.addEventListener('resize', function () {
                clearTimeout(tmr);
                tmr = setTimeout(function () { renderCurve(window.__shareVersions || []); }, 300);
            });
        }
        window.__shareVersions = versions;
        document.getElementById('curve-label').textContent = t.curve;
        document.getElementById('curve-range').textContent = 'v' + points[0].no + ' → v' + points[points.length - 1].no + ' · ' + points[points.length - 1].chars + ' ' + t.chars;
    }

    function loadDiff(a, b) {
        var box = document.getElementById('diff-box');
        fetch('/api/public/studio/shared/' + encodeURIComponent(token) + '?a=' + a + '&b=' + b)
            .then(function (r) { return r.json(); })
            .then(function (d) {
                box.classList.remove('hidden');
                box.innerHTML = '<p class="text-xs opacity-60 mb-2">v' + a + ' → v' + b + '</p><pre class="diff">' + esc(d.diff || '—') + '</pre>';
            });
    }

    fetch('/api/public/studio/shared/' + encodeURIComponent(token))
        .then(function (r) { if (!r.ok) throw new Error('http ' + r.status); return r.json(); })
        .then(function (data) {
            document.title = data.draft.title + ' — CryptoMind Studio';
            document.getElementById('title').textContent = data.draft.title;
            document.getElementById('subtitle').textContent =
                (data.draft.cause ? data.draft.cause + ' · ' : '') + data.draft.version_count + ' ' + t.versions + ' · ' + t.updated + ' ' + String(data.draft.updated_at || '').slice(0, 10);
            document.getElementById('footer-note').textContent = t.footer;
            document.getElementById('content').classList.remove('hidden');
            var list = document.getElementById('versions');
            var vs = data.versions || [];
            var exs = data.exchanges || [];
            // version_no → 該版本採納的建議（採納記錄掛 version_no；建議全文從對話找）
            var adoptedByVer = {};
            exs.forEach(function (e) {
                var sug = ((e.coach || {}).suggestions) || [];
                (e.outcomes || []).forEach(function (o) {
                    if (o.outcome !== 'adopted' || !o.version_no) return;
                    for (var k = 0; k < sug.length; k++) {
                        if (sug[k].quote === o.quote) {
                            (adoptedByVer[o.version_no] = adoptedByVer[o.version_no] || []).push(sug[k]);
                            break;
                        }
                    }
                });
            });
            // 採納建議獨立區塊（與作者原文視覺分離）
            function adoptedBlock(v, adopted) {
                if (!adopted || !adopted.length) return '';
                return '<div class="mt-2 p-2 rounded-lg" style="background:rgb(var(--color-primary) / .06);border:1px solid rgb(var(--color-primary) / .25)">' +
                    '<p class="text-[10px] mb-1" style="color:rgb(var(--color-primary))">✦ ' + t.adoptedHere + '</p>' +
                    adopted.map(function (s) {
                        return '<div class="text-xs mt-1.5 pl-2" style="border-left:2px solid rgb(var(--color-primary) / .4)">' +
                            (s.kind ? '<span class="badge" style="background:rgb(var(--color-primary) / .15);color:rgb(var(--color-primary))">' + esc(s.kind) + '</span> ' : '') +
                            esc(s.suggestion || '') +
                            '<div class="text-[10px] opacity-50 mt-0.5">' + t.onSentence + '：' + esc(String(s.quote || '').slice(0, 40)) + (String(s.quote || '').length > 40 ? '…' : '') + '</div>' +
                            '</div>';
                    }).join('') +
                    '<p class="text-[10px] opacity-50 mt-1.5">' + t.aiMarkLegend + '</p>' +
                    '</div>';
            }
            // 內容預覽：採納的建議文字反白（每則只標第一次出現；作者採納前可編輯建議，比對失敗就不標，區塊仍完整列出）
            function markedContent(contentMd, adopted) {
                var html = esc(contentMd || '');
                (adopted || []).forEach(function (s) {
                    var needle = s && s.suggestion ? String(s.suggestion) : '';
                    if (needle.length <= 6) return;
                    var eNeedle = esc(needle);
                    var i = html.indexOf(eNeedle);
                    if (i < 0) return;
                    html = html.slice(0, i) + '<mark class="aimark">' + eNeedle + '</mark>' + html.slice(i + eNeedle.length);
                });
                return html;
            }
            renderCurve(vs);
            function exchangeCard(e) {
                var coach = e.coach || {};
                var outcomes = e.outcomes || [];
                var html = '<div class="flex items-center gap-2 flex-wrap">' +
                    '<span class="text-xs" style="color:rgb(var(--color-primary))">🎓 ' + t.exchange + '</span>' +
                    '<span class="text-xs opacity-60 flex-1 truncate">' + esc(e.question ? e.question.slice(0, 40) : t.reviewOnly) + '</span>' +
                    '<span class="text-[10px] opacity-50">' + (coach.clarifying_questions || []).length + 'Q/' + (coach.suggestions || []).length + 'S</span>' +
                    '<span class="text-[10px] opacity-50">' + esc(String(e.created_at || '').slice(5, 16).replace('T', ' ')) + '</span>' +
                    '</div>';
                if ((coach.clarifying_questions || []).length) {
                    html += '<div class="mt-1.5"><p class="text-[10px] opacity-60 mb-0.5">' + t.questions + '</p><ul style="margin:0;padding-left:1rem">' +
                        coach.clarifying_questions.map(function (q) { return '<li class="text-xs">' + esc(q) + '</li>'; }).join('') + '</ul></div>';
                }
                (coach.suggestions || []).forEach(function (s) {
                    var o = null;
                    for (var k = 0; k < outcomes.length; k++) if (outcomes[k].quote === s.quote) o = outcomes[k];
                    var badge = o
                        ? (o.outcome === 'adopted'
                            ? '<span class="badge" style="background:rgb(var(--color-primary) / .15);color:rgb(var(--color-primary))">' + t.adopted + '</span>'
                            : '<span class="badge" style="background:rgb(var(--color-surface-highlight));color:rgb(var(--color-text-muted))">' + t.dismissed + '</span>')
                        : '';
                    html += '<div class="text-xs mt-1 pl-2" style="border-left:2px solid rgb(var(--color-border-subtle) / .2)">' +
                        '<span class="opacity-60">' + t.onSentence + '：</span><span class="italic opacity-60">' + esc(s.quote.slice(0, 44)) + (s.quote.length > 44 ? '…' : '') + '</span> ' + badge + '</div>';
                });
                return '<div class="card p-3" style="background:rgb(var(--color-primary) / .04)">' + html + '</div>';
            }
            var merged = vs.map(function (v) { return { kind: 'v', at: v.created_at || '', v: v }; })
                .concat(exs.map(function (e) { return { kind: 'e', at: e.created_at || '', e: e }; }))
                .sort(function (a, b) { return a.at < b.at ? 1 : a.at > b.at ? -1 : 0; });
            merged.forEach(function (m) {
                if (m.kind === 'e') {
                    list.insertAdjacentHTML('beforeend', exchangeCard(m.e));
                    return;
                }
                var v = m.v;
                var prevNo = v.version_no > 1 ? v.version_no - 1 : null;
                var delta = (v.char_delta || 0) > 0 ? '+' + v.char_delta : String(v.char_delta || 0);
                var fp = [];
                if ((v.edit_seconds || 0) > 0) fp.push(Math.max(1, Math.round(v.edit_seconds / 60)) + 'm');
                if ((v.paste_events || 0) > 0) fp.push(t.pasteN + '×' + v.paste_events);
                var heavy = (v.pasted_chars || 0) > 500 || ((v.char_delta || 0) > 0 && (v.pasted_chars || 0) > Math.abs(v.char_delta) * 0.5);
                var fpHtml = (heavy ? '<span class="badge" style="background:rgba(245,158,11,.15);color:rgb(245,158,11)">' + t.heavyPaste + '</span>' : '') +
                    (fp.length ? '<span class="text-[9px] opacity-50">' + fp.join(' · ') + '</span>' : '');
                var el = document.createElement('div');
                el.className = 'card p-3';
                el.innerHTML =
                    '<div class="flex items-center gap-2 flex-wrap">' +
                    '<span class="text-xs font-mono opacity-60">v' + v.version_no + '</span>' +
                    srcBadge(v.source) +
                    '<span class="text-xs opacity-60 flex-1 truncate">' + esc(v.change_summary || '') + '</span>' +
                    '<span class="text-[10px] font-mono opacity-50">' + delta + '</span>' +
                    fpHtml +
                    '<span class="text-[10px] opacity-50">' + esc(String(v.created_at || '').slice(0, 16).replace('T', ' ')) + '</span>' +
                    (prevNo ? '<button class="vbtn" data-diff="' + prevNo + ',' + v.version_no + '">' + t.diff + '</button>' : '') +
                    '</div>' +
                    adoptedBlock(v, adoptedByVer[v.version_no]) +
                    '<details class="mt-2"><summary class="text-xs opacity-60 hover:opacity-90">' + t.content + ' ▾</summary>' +
                    '<pre class="md mt-2 opacity-90">' + markedContent(v.content_md, adoptedByVer[v.version_no]) + '</pre></details>';
                list.appendChild(el);
            });
            list.addEventListener('click', function (e) {
                var btn = e.target.closest('[data-diff]');
                if (btn) {
                    var parts = btn.getAttribute('data-diff').split(',');
                    loadDiff(Number(parts[0]), Number(parts[1]));
                    document.getElementById('diff-box').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
            });
        })
        .catch(function () {
            var el = document.getElementById('error');
            el.textContent = t.err;
            el.classList.remove('hidden');
        });
})();
