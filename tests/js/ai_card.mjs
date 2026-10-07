// AI 分析卡片（web/js/ai-card.js）與排版轉換（web/js/ai-markdown.js）。
//
// 2026-10-04 DANNY：AI 回答分享到聊天室，內容有 HTML、markdown 表格，使用者看不懂 → 自動轉換。
// 這裡守：表格／清單／標題照原樣顯示、夾在裡面的 HTML 轉回 markdown、輸出一定安全（別人會看到這張卡片）。
import assert from 'node:assert/strict';
import { loadModuleUrl } from './_load.mjs';

globalThis.window = globalThis;
globalThis.I18n = { t: (key) => key };
let clickHandler = null;
globalThis.document = {
    addEventListener: (type, fn) => {
        if (type === 'click') clickHandler = fn;
    },
    getElementById: () => null,
};

const { renderAiMarkdown, aiMarkdownToPlain, normalizeAiMarkdown } = await import(
    await loadModuleUrl(new URL('../../web/js/ai-markdown.js', import.meta.url).pathname)
);
const { AI_CARD_TYPE, isAiCard, aiCardPreviewText, aiCardRowHtml } = await import(
    await loadModuleUrl(new URL('../../web/js/ai-card.js', import.meta.url).pathname)
);

// ── 1. markdown 表格：欄位對齊、補齊缺的儲存格、\| 不當分隔、儲存格內 <br> ──────────────
{
    const html = renderAiMarkdown('| 指標 | 數值 | 解讀 |\n|---|:-:|---:|\n| RSI | 72 | 偏熱<br>留意回檔 |\n| MACD | 金叉 |\n| a\\|b | 1 | 2 |');
    assert.ok(html.startsWith('<div class="ai-md-table-wrap"><table class="ai-md-table">'), html);
    assert.ok(html.includes('<th>指標</th><th class="ai-md-c">數值</th><th class="ai-md-r">解讀</th>'), html);
    assert.ok(html.includes('<td class="ai-md-r">偏熱<br>留意回檔</td>'), '儲存格內換行');
    assert.ok(html.includes('<td>MACD</td><td class="ai-md-c">金叉</td><td class="ai-md-r"></td>'), '缺的儲存格補空');
    assert.ok(html.includes('<td>a|b</td>'), '跳脫的 | 是字');
}

// ── 2. 夾在回答裡的 HTML：轉回 markdown（表格、粗體、清單、<br>），引用編號 <sup> 整個拿掉 ──
{
    const html = renderAiMarkdown(
        '<h3>重點</h3><p>短線<b>偏多</b><sup>[1]</sup></p><ul><li>支撐 60,000</li><li>壓力 70,000</li></ul>' +
            '<table><tr><th>價位</th><th>說明</th></tr><tr><td>60,000</td><td>支撐<br/>前低</td></tr></table>'
    );
    assert.ok(html.includes('<p class="ai-md-h ai-md-h3">重點</p>'), html);
    assert.ok(html.includes('<strong>偏多</strong>') && !html.includes('[1]'), html);
    assert.ok(html.includes('<ul><li>支撐 60,000</li><li>壓力 70,000</li></ul>'), html);
    assert.ok(html.includes('<th>價位</th><th>說明</th>') && html.includes('<td>支撐<br>前低</td>'), html);
    assert.ok(!/<(table|tr|td|h3|li|b|sup)\b[^>]*>/.test(aiMarkdownToPlain('<b>x</b>')), '純文字不能留標籤');
    // 程式碼區塊裡的 HTML 是字面文字，不轉換
    const code = renderAiMarkdown('```\n<b>x</b><br>\n```');
    assert.ok(code.includes('&lt;b&gt;x&lt;/b&gt;&lt;br&gt;'), code);
    assert.equal(normalizeAiMarkdown('a<br>b'), 'a\nb');
}

// ── 3. 標題、巢狀清單、有序清單起始號、引用、分隔線、段落內換行 ──────────────────────────
{
    const html = renderAiMarkdown('# 大\n## 中\n\n1. **進場**：分批\n   - 第一批\n   - 第二批\n2. 停損\n\n3. 接著\n\n> 僅供參考\n\n---\n\n第一行\n第二行');
    assert.ok(html.includes('<p class="ai-md-h ai-md-h1">大</p><p class="ai-md-h ai-md-h2">中</p>'), html);
    assert.ok(html.includes('<ol><li><strong>進場</strong>：分批<ul><li>第一批</li><li>第二批</li></ul></li><li>停損</li><li>接著</li></ol>'), html);
    assert.ok(html.includes('<blockquote>僅供參考</blockquote><hr>'), html);
    assert.ok(html.includes('<p>第一行<br>第二行</p>'), html);
    assert.ok(renderAiMarkdown('3. 從三開始\n4. 四').startsWith('<ol start="3">'));
    // 結尾 # 前有空白才是標題的收尾符號（「學 C#」的 # 是字）
    assert.ok(renderAiMarkdown('## 學 C#').includes('學 C#'));
}

// ── 4. 安全：別人會看到這張卡片，原文裡的標籤、危險連結、追蹤圖都不能生效 ─────────────────
{
    const evil = [
        '<script>alert(1)</script><img src=x onerror=alert(1)><iframe src=//evil></iframe>',
        '[點我](javascript:alert(1)) [data](data:text/html,<script>1</script>)',
        '[ok](https://example.com/a?b=1&c=2) https://example.com/"onmouseover="alert(1)',
        '[引號](https://example.com/"onmouseover="alert(1)) [角括號](https://example.com/<b>x</b>)',
        '![追蹤](https://evil.example/pixel.png)',
        // 實體編碼的標籤：解碼在去標籤「之後」，所以一定要靠最後的跳脫擋住
        '&lt;img src=x onerror=alert(1)&gt; 1 &lt; 2 &amp;lt;b&amp;gt; <3',
        '**a<b>c</b>** `<img src=x>` \u0002 0 \u0002',
        '| <img src=x onerror=1> | b |\n|---|---|\n| <svg onload=1> | `x` |',
    ].join('\n\n');
    const html = renderAiMarkdown(evil);
    const ALLOWED = new Set(['p', 'br', 'strong', 'em', 'del', 'code', 'pre', 'ul', 'ol', 'li', 'blockquote', 'hr', 'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'div']);
    for (const m of html.matchAll(/<\/?([a-zA-Z0-9]+)/g)) assert.ok(ALLOWED.has(m[1].toLowerCase()), `不該出現的標籤 <${m[1]}>：${html}`);
    const anchors = [...html.matchAll(/<a\b[^>]*>/g)].map((m) => m[0]);
    assert.ok(anchors.length >= 3, `連結要有（http(s) 的才轉）：${anchors.length}`);
    for (const tag of anchors) {
        // 整個標籤只能是固定形狀：網址（http/https、不含引號和角括號）＋ target ＋ rel，不能多出任何屬性
        assert.match(tag, /^<a href="https?:\/\/[^"\s<>]*" target="_blank" rel="noopener noreferrer nofollow">$/, tag);
    }
    assert.ok(!/<[^>]*\son[a-z]+=/i.test(html), '標籤屬性裡不能有事件處理');
    assert.ok(!html.includes('javascript:alert(1)</a>') && !/href="javascript:/i.test(html), 'javascript: 連結');
    assert.ok(!html.includes('evil.example'), '圖片不載入、網址也不留在輸出');
    assert.ok(html.includes('追蹤'), '圖片只留說明文字');
    assert.ok(!html.includes('\u0002') && !html.includes('\u0001'), '內部記號不能漏出去');
    assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'), '實體編碼的標籤解碼後只能是字面文字');
    // 單獨的 < 只是字；雙重編碼（&amp;lt;）使用者看到的是字面的「&lt;」，所以 HTML 裡是 &amp;lt;
    assert.ok(html.includes('1 &lt; 2') && html.includes('&amp;lt;b&amp;gt;') && html.includes('&lt;3'), html);
    // 不認得的標籤要消失（不是顯示成「&lt;span&gt;」），裡面的字留著
    const stripped = renderAiMarkdown('<span class="x">字</span><font color=red>紅</font><custom-tag>藍</custom-tag>');
    assert.equal(stripped, '<p>字紅藍</p>');
    // 純文字版：連結寫成「文字 (網址)」，圖片只留說明
    assert.equal(aiMarkdownToPlain('[報告](https://example.com/a) ![圖](https://evil.example/p.png)'), '報告 (https://example.com/a) 圖');
}

// ── 5. 純文字（複製用）：表格一列一行，標題粗體符號拿掉，巢狀清單縮排 ───────────────────
{
    const plain = aiMarkdownToPlain(
        '## 📊 BTC\n\n**結論**：偏多\n\n| 指標 | 數值 | 解讀 |\n|---|---|---|\n| RSI | 72 | 偏熱<br>留意回檔 |\n\n| 項目 | 內容 |\n|---|---|\n| 支撐 | 60,000 |\n\n1. 進場\n   - 分批'
    );
    assert.equal(
        plain,
        '📊 BTC\n\n結論：偏多\n\n• RSI｜數值：72｜解讀：偏熱 留意回檔\n\n• 支撐：60,000\n\n1. 進場\n  • 分批'
    );
    assert.equal(aiMarkdownToPlain(''), '');
    assert.equal(renderAiMarkdown(''), '');
    assert.equal(renderAiMarkdown(null), '');
}

// ── 6. 長回答不卡：六千字（後端上限）在合理時間內轉完 ───────────────────────────────────
{
    const big = Array.from({ length: 150 }, (_, i) => `- **第${i}點**：${'內容'.repeat(15)} <b>x</b>`).join('\n') + '\n\n' + '| a | b |\n|---|---|\n' + '| 1 | 2 |\n'.repeat(100);
    const started = Date.now();
    renderAiMarkdown(big.slice(0, 6000));
    aiMarkdownToPlain(big.slice(0, 6000));
    assert.ok(Date.now() - started < 500, `轉換太慢：${Date.now() - started}ms`);
}

// ── 7. 卡片整列：自己／對方的結構、長的預設收合、複製用的純文字、回覆引用與表情 ───────────
{
    assert.equal(AI_CARD_TYPE, 'ai_card');
    assert.equal(isAiCard({ message_type: 'ai_card' }), true);
    assert.equal(isAiCard({ message_type: 'text' }), false);
    assert.equal(isAiCard(null), false);
    assert.equal(aiCardPreviewText(), '✨ messages.aiCard.title');

    const base = { rowOpen: '<div class="row">', meta: '<i meta>', tools: '<i tools>', myId: 'u1' };
    const short = { id: 5, message_type: 'ai_card', content: '## 重點\n\n| a | b |\n|---|---|\n| 1 | 2 |', reply_to: null, reactions: [] };
    const mine = aiCardRowHtml({ ...base, msg: short, isMine: true });
    assert.ok(mine.startsWith('<div class="row"><i meta><i tools><div class="msg-bubble ai-card '), mine);
    assert.ok(mine.endsWith('</div></div>'));
    assert.ok(mine.includes('class="ai-card-head"') && mine.includes('messages.aiCard.title'), mine);
    assert.ok(mine.includes('<table class="ai-md-table">'), '表格照原樣');
    assert.ok(mine.includes('class="msg-text ai-card-body ai-md"'), '短的不收合');
    assert.ok(!mine.includes('data-ai-card-toggle'), '短的沒有展開鈕');
    assert.ok(mine.includes('data-plain="重點&#10;'.replace('&#10;', '\n')) || mine.includes('data-plain="重點\n'), '複製用純文字（表格一列一行）');
    assert.ok(mine.includes('• 1：2'), mine);

    const other = aiCardRowHtml({ ...base, msg: short, isMine: false, avatar: '<i avatar>', sender: '<i sender>' });
    assert.ok(other.startsWith('<div class="row"><i avatar><div class="msg-bubble ai-card '), other);
    assert.ok(other.includes('<i sender>') && other.endsWith('</div><i tools><i meta></div>'), other);

    const long = { ...short, content: '重點。'.repeat(120) };
    const folded = aiCardRowHtml({ ...base, msg: long, isMine: false });
    assert.ok(folded.includes('ai-card-collapsed') && folded.includes('data-ai-card-toggle'), folded);
    assert.ok(folded.includes('aria-expanded="false"'));

    // data-plain 要跳脫（內容裡的 " 和 < 不能跳出屬性）
    const sneaky = aiCardRowHtml({ ...base, msg: { ...short, content: '"><img src=x onerror=1> & 引號' }, isMine: false });
    assert.ok(!/<img\b/.test(sneaky), sneaky);
    const attr = /data-plain="([^"]*)"/.exec(sneaky);
    assert.ok(attr && !attr[1].includes('<') && !attr[1].includes('"'), '屬性值不能含 < 或 "');

    // 回覆引用：卡片也能被回覆、也能回覆別人
    const quoted = aiCardRowHtml({
        ...base,
        isMine: true,
        msg: { ...short, reply_to: { id: 3, from_display_name: '小明', snippet: '你怎麼看', recalled: false } },
    });
    assert.ok(quoted.includes('data-dm-jump="3"') && quoted.includes('你怎麼看'), quoted);
}

// ── 8. 展開／收合：文件層的事件代理，切換 class、按鈕文字、aria ─────────────────────────
{
    assert.equal(typeof clickHandler, 'function', '要有 click 代理');
    const classes = new Set(['ai-card-collapsed']);
    const body = {
        classList: {
            toggle: (name) => {
                if (classes.has(name)) classes.delete(name);
                else classes.add(name);
                return classes.has(name);
            },
        },
    };
    const attrs = {};
    const button = {
        dataset: { more: '展開全文', less: '收合' },
        textContent: '展開全文',
        setAttribute: (k, v) => (attrs[k] = v),
        closest: (sel) => (sel === '.ai-card' ? { querySelector: () => body } : null),
    };
    const event = { target: { closest: (sel) => (sel === '[data-ai-card-toggle]' ? button : null) } };
    clickHandler(event);
    assert.ok(!classes.has('ai-card-collapsed') && button.textContent === '收合' && attrs['aria-expanded'] === 'true');
    clickHandler(event);
    assert.ok(classes.has('ai-card-collapsed') && button.textContent === '展開全文' && attrs['aria-expanded'] === 'false');
    clickHandler({ target: { closest: () => null } }); // 點別的地方：什麼都不做
}

console.log('ai_card ok');
