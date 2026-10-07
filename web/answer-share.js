// answer-share.html 的頁面腳本（AI 回答快照分享，2026-10-05）。classic script，不含 inline handler（CSP）。
// 內容一律用 textContent 放進頁面（不經 HTML 解析）：問題與答案是使用者／模型產生的文字，不能被當成 HTML。
// 頁面字串：英文內建當備援，其他語言讀 /static/js/i18n/<lang>.json 的 answerShare.page（跟 App 同一份翻譯，
// 沒載到就用英文）。公開頁不載入 SPA 的 i18n 系統，所以這裡自己讀一個 key。

(function () {
    'use strict';
    var EN = {
        title: 'Shared AI answer — CryptoMind',
        note: 'A friend shared an AI answer',
        question: 'Question',
        answer: "The AI's answer",
        disclaimer: 'This is an AI analysis snapshot shared by a user. CryptoMind does not endorse its content and it is not investment advice. It may be out of date or incomplete — do not click or trust links or addresses in it; investment decisions are yours. For privacy, wallet addresses, transaction hashes and emails are masked.',
        cta: 'Ask your own question',
        expiry: 'This snapshot does not update. The link expires 30 days after sharing, and the sharer can revoke it any time.',
        err: 'This link is invalid, expired, or has been revoked.'
    };

    function lang() {
        var n = navigator.language || 'zh-TW';
        if (n.indexOf('zh') === 0) return n.indexOf('CN') >= 0 || n.indexOf('Hans') >= 0 ? 'zh-CN' : 'zh-TW';
        if (n.indexOf('ru') === 0 || n.indexOf('be') === 0 || n.indexOf('uk') === 0) return 'ru';
        if (n.indexOf('en') === 0) return 'en';
        return 'zh-TW';
    }

    var $ = function (id) { return document.getElementById(id); };
    var code = lang();
    document.documentElement.lang = code;

    function loadStrings() {
        if (code === 'en') return Promise.resolve(EN);
        return fetch('/static/js/i18n/' + code + '.json')
            .then(function (res) { return res.ok ? res.json() : null; })
            .then(function (all) {
                var page = all && all.answerShare && all.answerShare.page;
                if (!page) return EN;
                var out = {};
                Object.keys(EN).forEach(function (k) { out[k] = page[k] || EN[k]; });
                return out;
            })
            .catch(function () { return EN; });
    }

    // /s/<token>：token 取網址最後一段（格式由伺服器驗，這裡只負責帶過去）。
    // 畸形的 percent-encoding 會讓 decodeURIComponent 丟錯，當作無效連結。
    var token = '';
    try {
        token = decodeURIComponent(location.pathname.split('/').filter(Boolean).pop() || '');
    } catch (_err) {
        token = '';
    }

    var data = token
        ? fetch('/api/public/share/answers/' + encodeURIComponent(token), { headers: { Accept: 'application/json' } })
            .then(function (res) { return res.ok ? res.json() : null; })
            .catch(function () { return null; })
        : Promise.resolve(null);

    Promise.all([loadStrings(), data]).then(function (done) {
        var t = done[0];
        var d = done[1];
        document.title = t.title;
        $('shared-note').textContent = t.note;
        if (!d || !d.question) {
            var box = $('error');
            box.textContent = t.err;
            box.classList.remove('hidden');
            return;
        }
        $('label-q').textContent = t.question;
        $('label-a').textContent = t.answer;
        $('question').textContent = d.question;
        $('answer').textContent = d.answer;
        $('disclaimer').textContent = t.disclaimer;
        $('expiry-note').textContent = t.expiry;
        var cta = $('cta');
        cta.textContent = t.cta;
        // 問題帶去首頁預填（跟 /?ask= 分享連結同一套：不自動送出、訪客直接可用）
        var ask = Array.from(d.question).slice(0, 200).join('');
        cta.setAttribute('href', '/?ask=' + encodeURIComponent(ask) + '&utm_source=share');
        $('content').classList.remove('hidden');
    });
})();
