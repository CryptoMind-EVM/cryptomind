/**
 * AI 回答（markdown，偶爾夾 HTML）→ 好讀的畫面／純文字（2026-10-04 DANNY：分享到聊天室的內容
 * 有 HTML、markdown 表格，使用者看不懂，要自動轉換）。
 *
 * - 一份解析器、兩種輸出：renderAiMarkdown（安全的 HTML，AI 分析卡片與助理抽屜用）、
 *   aiMarkdownToPlain（複製用的純文字，表格變成一列一行）。
 * - 先把回答裡夾的 HTML 轉回 markdown（<br>、<table>、<b>、<li>…），剩下的標籤直接去掉。
 * - 輸出是「先跳脫、再套固定標籤」：原文裡的任何 < > 都不會變成標籤；連結只認 http(s)，
 *   圖片不載入（只留說明文字：別人看到卡片時不能因此對外發請求）。
 * - 不靠 markdown-it：手機私訊頁（forum/messages.html）沒載它；也不用 lookbehind（舊 iOS Safari 會整支解析失敗）。
 */

const BR_MARK = '\u0001'; // 表格儲存格裡的換行（<br>）
const HOLD = '\u0002'; // 行內暫存位置的記號
const BR_RE = /<\s*br\s*\/?\s*>/gi;
const TABLE_RULE = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/;
const FENCE_OPEN = /^\s{0,3}```/;
const FENCE_CLOSE = /^\s{0,3}```\s*$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.+?)(?:\s+#+)?\s*$/; // 結尾的 # 前面要有空白（「學 C#」的 # 是字）
const HR = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const LIST_ITEM = /^(\s*)([-*+•]|\d{1,3}[.)])\s+(.*)$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function decodeEntities(text) {
    return text
        .replace(/&nbsp;/gi, ' ')
        .replace(/&lt;/gi, '<')
        .replace(/&gt;/gi, '>')
        .replace(/&quot;/gi, '"')
        .replace(/&#39;|&apos;/gi, "'")
        .replace(/&amp;/gi, '&');
}

// ── HTML → markdown ─────────────────────────────────────────

/** 表格儲存格的 HTML → 一行 markdown 文字（<br> 留成 BR_MARK，| 跳脫） */
function cellText(html) {
    return decodeEntities(
        html
            .replace(BR_RE, BR_MARK)
            .replace(/<(strong|b)\b[^>]*>([\s\S]*?)<\/\1\s*>/gi, (_m, _t, inner) => `**${inner.trim()}**`)
            .replace(/<\/?[a-zA-Z][^>]*>/g, '')
    )
        .replace(/[ \t\r\n]+/g, ' ')
        .replace(/\|/g, '\\|')
        .trim();
}

function htmlTableToMarkdown(inner) {
    const rows = [...inner.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr\s*>/gi)]
        .map((row) => [...row[1].matchAll(/<t([hd])\b[^>]*>([\s\S]*?)<\/t\1\s*>/gi)].map((cell) => cellText(cell[2])))
        .filter((cells) => cells.length);
    if (!rows.length) return '';
    const width = Math.max(...rows.map((cells) => cells.length));
    const line = (cells) => `| ${Array.from({ length: width }, (_v, i) => cells[i] || ' ').join(' | ')} |`;
    const rule = `| ${Array.from({ length: width }, () => '---').join(' | ')} |`;
    return `\n\n${[line(rows[0]), rule, ...rows.slice(1).map(line)].join('\n')}\n\n`;
}

function htmlToMarkdown(segment) {
    if (!/[<&]/.test(segment)) return segment;
    let s = segment
        // 行內引用編號 <sup>[3]</sup>：連內容刪掉（同 app.js stripInlineHtml）
        .replace(/<(sup|sub)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, '')
        .replace(/<\/?(sup|sub)\b[^>]*>/gi, '')
        .replace(/<table\b[^>]*>([\s\S]*?)<\/table\s*>/gi, (_m, inner) => htmlTableToMarkdown(inner))
        .replace(/<h([1-6])\b[^>]*>([\s\S]*?)<\/h\1\s*>/gi, (_m, n, inner) => `\n\n${'#'.repeat(Number(n))} ${inner.trim()}\n\n`)
        .replace(/<(strong|b)\b[^>]*>([\s\S]*?)<\/\1\s*>/gi, (_m, _t, inner) => `**${inner.trim()}**`)
        .replace(/<(em|i)\b[^>]*>([\s\S]*?)<\/\1\s*>/gi, (_m, _t, inner) => `*${inner.trim()}*`)
        .replace(/<code\b[^>]*>([\s\S]*?)<\/code\s*>/gi, '`$1`')
        .replace(/<li\b[^>]*>/gi, '\n- ')
        .replace(/<\/li\s*>/gi, '')
        .replace(/<\/?(ul|ol)\b[^>]*>/gi, '\n')
        .replace(/<hr\b[^>]*>/gi, '\n\n---\n\n')
        .replace(/<\/(p|div|section|article|blockquote)\s*>/gi, '\n\n')
        .replace(/<(p|div|section|article)\b[^>]*>/gi, '\n\n');
    // <br>：表格列裡是儲存格內換行，其他地方就是換行
    s = s
        .split('\n')
        .map((line) => line.replace(BR_RE, /^\s*\|/.test(line) ? BR_MARK : '\n'))
        .join('\n');
    return decodeEntities(s.replace(/<\/?[a-zA-Z][^>]*>/g, ''));
}

/** 回答 → 純 markdown：夾在裡面的 HTML 轉回 markdown；程式碼區塊原樣不動 */
function normalizeAiMarkdown(text) {
    const source = String(text || '')
        .replace(/\r\n?/g, '\n')
        // 內部記號不能從原文進來；其他控制字元也沒有意義
        .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '');
    const segments = [];
    let buffer = [];
    let inCode = false;
    for (const line of source.split('\n')) {
        if (FENCE_OPEN.test(line) && !inCode) {
            if (buffer.length) segments.push({ code: false, text: buffer.join('\n') });
            buffer = [line];
            inCode = true;
        } else if (inCode && FENCE_CLOSE.test(line)) {
            buffer.push(line);
            segments.push({ code: true, text: buffer.join('\n') });
            buffer = [];
            inCode = false;
        } else {
            buffer.push(line);
        }
    }
    if (buffer.length) segments.push({ code: inCode, text: buffer.join('\n') });
    return segments.map((segment) => (segment.code ? segment.text : htmlToMarkdown(segment.text))).join('\n');
}

// ── markdown → 區塊 ─────────────────────────────────────────

/** 一列表格 → 儲存格（頭尾的 | 去掉，\| 不當分隔） */
function splitRow(line) {
    const cells = [];
    let cell = '';
    const body = line.trim();
    for (let i = 0; i < body.length; i++) {
        const ch = body[i];
        if (ch === '\\' && body[i + 1] === '|') {
            cell += '|';
            i++;
        } else if (ch === '|') {
            cells.push(cell);
            cell = '';
        } else {
            cell += ch;
        }
    }
    cells.push(cell);
    if (body.startsWith('|')) cells.shift();
    if (body.endsWith('|') && !body.endsWith('\\|')) cells.pop();
    return cells.map((c) => c.trim());
}

function alignOf(cell) {
    const c = cell.trim();
    if (c.startsWith(':') && c.endsWith(':')) return 'c';
    return c.endsWith(':') ? 'r' : '';
}

function indentWidth(spaces) {
    return spaces.replace(/\t/g, '    ').length;
}

function startsBlock(line, next) {
    return (
        FENCE_OPEN.test(line) ||
        HEADING.test(line) ||
        HR.test(line) ||
        QUOTE.test(line) ||
        LIST_ITEM.test(line) ||
        (line.includes('|') && next !== undefined && TABLE_RULE.test(next))
    );
}

/** 平的清單項目 → 巢狀（依縮排）。縮排多 2 格以上才算子層，免得多一個空白就錯位 */
function buildList(items) {
    const root = { ordered: items[0].ordered, start: items[0].number, items: [] };
    const stack = [{ indent: items[0].indent, node: root }];
    for (const item of items) {
        let top = stack[stack.length - 1];
        if (item.indent >= top.indent + 2 && top.node.items.length) {
            const parent = top.node.items[top.node.items.length - 1];
            parent.children = { ordered: item.ordered, start: item.number, items: [] };
            top = { indent: item.indent, node: parent.children };
            stack.push(top);
        } else {
            while (stack.length > 1 && item.indent < stack[stack.length - 1].indent) stack.pop();
            top = stack[stack.length - 1];
        }
        top.node.items.push({ text: item.text, children: null });
    }
    return root;
}

function parseBlocks(markdown) {
    const lines = markdown.split('\n');
    const blocks = [];
    let i = 0;
    while (i < lines.length) {
        const line = lines[i];
        if (!line.trim()) {
            i++;
            continue;
        }
        if (FENCE_OPEN.test(line)) {
            const code = [];
            i++;
            while (i < lines.length && !FENCE_CLOSE.test(lines[i])) code.push(lines[i++]);
            i++;
            blocks.push({ type: 'code', text: code.join('\n') });
            continue;
        }
        const heading = HEADING.exec(line);
        if (heading) {
            blocks.push({ type: 'heading', level: heading[1].length, text: heading[2] });
            i++;
            continue;
        }
        if (HR.test(line)) {
            blocks.push({ type: 'hr' });
            i++;
            continue;
        }
        if (line.includes('|') && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1])) {
            const header = splitRow(line);
            const align = splitRow(lines[i + 1]).map(alignOf);
            const rows = [];
            i += 2;
            while (i < lines.length && lines[i].trim() && lines[i].includes('|')) rows.push(splitRow(lines[i++]));
            blocks.push({ type: 'table', header, align, rows });
            continue;
        }
        if (QUOTE.test(line)) {
            const quoted = [];
            while (i < lines.length && QUOTE.test(lines[i])) quoted.push(QUOTE.exec(lines[i++])[1]);
            blocks.push({ type: 'quote', lines: quoted });
            continue;
        }
        if (LIST_ITEM.test(line)) {
            const items = [];
            while (i < lines.length) {
                const m = LIST_ITEM.exec(lines[i]);
                if (m) {
                    items.push({
                        indent: indentWidth(m[1]),
                        ordered: /\d/.test(m[2]),
                        number: parseInt(m[2], 10) || 1,
                        text: m[3],
                    });
                    i++;
                } else if (!lines[i].trim()) {
                    // 項目之間的空行：下一個非空行還是清單項目就接著，不然清單結束
                    let j = i;
                    while (j < lines.length && !lines[j].trim()) j++;
                    if (j < lines.length && LIST_ITEM.test(lines[j])) i = j;
                    else break;
                } else if (/^\s+\S/.test(lines[i]) && !startsBlock(lines[i], lines[i + 1])) {
                    items[items.length - 1].text += `\n${lines[i].trim()}`; // 縮排的接續行
                    i++;
                } else {
                    break;
                }
            }
            blocks.push({ type: 'list', root: buildList(items) });
            continue;
        }
        const para = [line];
        i++;
        while (i < lines.length && lines[i].trim() && !startsBlock(lines[i], lines[i + 1])) para.push(lines[i++]);
        blocks.push({ type: 'para', lines: para.map((l) => l.trim()) });
    }
    return blocks;
}

// ── 行內 ───────────────────────────────────────────────────

/** 已跳脫的文字 → 粗體／斜體／刪除線 */
function emphasis(escaped) {
    return escaped
        .replace(/\*\*([^\n]+?)\*\*/g, '<strong>$1</strong>')
        .replace(/__([^\n]+?)__/g, '<strong>$1</strong>')
        .replace(/~~([^\n]+?)~~/g, '<del>$1</del>')
        .replace(/(^|[^*\w])\*([^\s*](?:[^*\n]*?[^\s*])?)\*(?!\*)/g, '$1<em>$2</em>');
}

function trimUrl(url) {
    const trimmed = url.replace(/(?:&(?:gt|lt|quot|#39);|[.,;:!?)\]}，。；：！？）」』])+$/, '');
    return [trimmed, url.slice(trimmed.length)];
}

function anchor(url, labelHtml) {
    return `<a href="${url}" target="_blank" rel="noopener noreferrer nofollow">${labelHtml}</a>`;
}

function inlineHtml(raw) {
    const holds = [];
    const hold = (html) => {
        holds.push(html);
        return `${HOLD}${holds.length - 1}${HOLD}`;
    };
    let s = String(raw)
        .split(BR_MARK)
        .join(' ') // 表格以外不會有，保險
        .replace(/`([^`\n]+)`/g, (_m, code) => hold(`<code>${escapeHtml(code)}</code>`))
        // 圖片：不載入遠端圖，只留說明文字
        .replace(/!\[([^\]\n]*)\]\([^)\n]*\)/g, (_m, alt) => alt)
        .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_m, label, url) =>
            hold(anchor(escapeHtml(url), emphasis(escapeHtml(label))))
        );
    s = escapeHtml(s).replace(/(^|[\s(（])(https?:\/\/[^\s<>"']+)/g, (_m, lead, found) => {
        const [url, tail] = trimUrl(found);
        return `${lead}${hold(anchor(url, url))}${tail}`;
    });
    s = emphasis(s);
    for (let round = 0; round < 3 && s.includes(HOLD); round++) {
        s = s.replace(new RegExp(`${HOLD}(\\d+)${HOLD}`, 'g'), (_m, n) => holds[Number(n)]);
    }
    return s;
}

function inlinePlain(raw) {
    return String(raw)
        .replace(/!\[([^\]\n]*)\]\([^)\n]*\)/g, '$1')
        .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_m, label, url) => (label === url ? url : `${label} (${url})`))
        .replace(/`([^`\n]+)`/g, '$1')
        .replace(/(\*\*|__|~~)(.+?)\1/g, '$2')
        .replace(/(^|[^*\w])\*([^\s*](?:[^*\n]*?[^\s*])?)\*(?!\*)/g, '$1$2')
        .split(BR_MARK)
        .join(' ');
}

// ── 輸出 ───────────────────────────────────────────────────

function cellHtml(text) {
    return String(text).split(BR_MARK).map(inlineHtml).join('<br>');
}

function listHtml(node) {
    const tag = node.ordered ? 'ol' : 'ul';
    const start = node.ordered && node.start > 1 ? ` start="${node.start}"` : '';
    const items = node.items
        .map((item) => `<li>${item.text.split('\n').map(inlineHtml).join('<br>')}${item.children ? listHtml(item.children) : ''}</li>`)
        .join('');
    return `<${tag}${start}>${items}</${tag}>`;
}

function tableHtml(block) {
    const cols = Math.max(block.header.length, ...block.rows.map((row) => row.length));
    const cls = (i) => (block.align[i] ? ` class="ai-md-${block.align[i]}"` : '');
    const head = Array.from({ length: cols }, (_v, i) => `<th${cls(i)}>${cellHtml(block.header[i] || '')}</th>`).join('');
    const body = block.rows
        .map((row) => `<tr>${Array.from({ length: cols }, (_v, i) => `<td${cls(i)}>${cellHtml(row[i] || '')}</td>`).join('')}</tr>`)
        .join('');
    return `<div class="ai-md-table-wrap"><table class="ai-md-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function blockHtml(block) {
    switch (block.type) {
        case 'heading':
            return `<p class="ai-md-h ai-md-h${Math.min(block.level, 3)}">${inlineHtml(block.text)}</p>`;
        case 'para':
            return `<p>${block.lines.map(inlineHtml).join('<br>')}</p>`;
        case 'list':
            return listHtml(block.root);
        case 'table':
            return tableHtml(block);
        case 'quote':
            return `<blockquote>${block.lines.map(inlineHtml).join('<br>')}</blockquote>`;
        case 'code':
            return `<pre class="ai-md-code"><code>${escapeHtml(block.text)}</code></pre>`;
        default:
            return '<hr>';
    }
}

/** 回答 → 安全的 HTML（卡片、助理抽屜用） */
function renderAiMarkdown(text) {
    return parseBlocks(normalizeAiMarkdown(text)).map(blockHtml).join('');
}

function listPlain(node, depth = 0) {
    return node.items.flatMap((item, index) => {
        const mark = node.ordered ? `${node.start + index}.` : '•';
        const own = `${'  '.repeat(depth)}${mark} ${item.text.split('\n').map(inlinePlain).join(' ')}`;
        return [own, ...(item.children ? listPlain(item.children, depth + 1) : [])];
    });
}

/** 表格 → 一列一行：兩欄「第一欄：第二欄」，多欄「第一欄｜欄名：值｜欄名：值」 */
function tablePlain(block) {
    const header = block.header.map(inlinePlain);
    return block.rows.map((raw) => {
        const row = raw.map(inlinePlain);
        if (header.length === 2 && header[1]) return `• ${row[0] || ''}：${row[1] || ''}`;
        const parts = row.map((cell, i) => (i > 0 && header[i] && cell ? `${header[i]}：${cell}` : cell)).filter(Boolean);
        return `• ${parts.join('｜')}`;
    });
}

function blockPlain(block) {
    switch (block.type) {
        case 'heading':
            return inlinePlain(block.text);
        case 'para':
            return block.lines.map(inlinePlain).join('\n');
        case 'list':
            return listPlain(block.root).join('\n');
        case 'table':
            return tablePlain(block).join('\n');
        case 'quote':
            return block.lines.map((l) => `> ${inlinePlain(l)}`).join('\n');
        case 'code':
            return block.text;
        default:
            return '———';
    }
}

/** 回答 → 純文字（複製、無法顯示卡片的地方）：表格一列一行、標題粗體符號都拿掉 */
function aiMarkdownToPlain(text) {
    return parseBlocks(normalizeAiMarkdown(text))
        .map(blockPlain)
        .join('\n\n')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
}

export { normalizeAiMarkdown, renderAiMarkdown, aiMarkdownToPlain };
