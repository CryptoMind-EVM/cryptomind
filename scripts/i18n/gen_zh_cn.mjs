#!/usr/bin/env node
/**
 * 簡體中文詞表由繁體中文自動生成（OpenCC，台灣正體＋詞彙 → 大陸簡體）。
 *
 * 2026-09-25 起多語系改「zh-TW 單一來源」：只手改 zh-TW，zh-CN 一律由這支產生，
 * 不要手改 zh-CN。OpenCC 轉錯或需要不同說法的，寫進 overrides 檔。
 *
 *   node scripts/i18n/gen_zh_cn.mjs          # 重新產生並寫回
 *   node scripts/i18n/gen_zh_cn.mjs --check  # 只比對，不一致就 exit 1（測試用）
 *
 * 涵蓋：
 *   web/js/i18n/zh-TW.json → zh-CN.json（巢狀；例外：scripts/i18n/zh-CN.web-overrides.json，扁平 key）
 *   core/i18n/*.json 的 "zh-TW" 區塊 → "zh-CN" 區塊（例外：scripts/i18n/zh-CN.core-overrides.json，
 *   {"<檔名>": {key: value}}）
 *   例外檔放 scripts/i18n/，不放詞表目錄（詞表目錄的 json 會被當成詞表檢查）
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import * as OpenCC from 'opencc-js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const CHECK = process.argv.includes('--check');
const openccCN = OpenCC.Converter({ from: 'twp', to: 'cn' });
// OpenCC 的詞彙轉換有些偏舊或用錯（複製→拷贝、登出→注销、離線→脱机…），再套一份自己維護的用詞表
const TABLE = JSON.parse(fs.readFileSync(path.join(ROOT, 'scripts/i18n/zh-CN.phrases.json'), 'utf8'));
const byLength = (list) => list.slice().sort((a, b) => b[0].length - a[0].length);
const PRE = byLength(TABLE.pre || []);
const PHRASES = byLength(TABLE.phrases || []);
function replaceAll(text, table) {
    let out = text;
    for (const [from, to] of table) out = out.split(from).join(to);
    return out;
}
// pre 的詞先換成私用區佔位符（OpenCC 不認得、不會拆），轉完再換成目標說法
const PLACEHOLDER = (i) => `\uE000${i}\uE001`;
export function toCN(text) {
    let guarded = text;
    PRE.forEach(([from], i) => {
        guarded = guarded.split(from).join(PLACEHOLDER(i));
    });
    let out = openccCN(guarded);
    PRE.forEach(([, to], i) => {
        out = out.split(PLACEHOLDER(i)).join(to);
    });
    return replaceAll(out, PHRASES);
}

function readJson(rel) {
    return JSON.parse(fs.readFileSync(path.join(ROOT, rel), 'utf8'));
}

function readOverrides(rel) {
    const p = path.join(ROOT, rel);
    return fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf8')) : {};
}

function render(obj) {
    return JSON.stringify(obj, null, 2) + '\n';
}

// 巢狀物件逐值轉換，key 順序照 zh-TW；overrides 用 "a.b.c" 扁平 key
function convertTree(node, overrides, prefix = '') {
    if (typeof node === 'string') {
        return Object.prototype.hasOwnProperty.call(overrides, prefix) ? overrides[prefix] : toCN(node);
    }
    if (Array.isArray(node)) return node.map((v, i) => convertTree(v, overrides, `${prefix}[${i}]`));
    if (node && typeof node === 'object') {
        const out = {};
        for (const [k, v] of Object.entries(node)) {
            out[k] = convertTree(v, overrides, prefix ? `${prefix}.${k}` : k);
        }
        return out;
    }
    return node;
}

function main() {
    const results = [];

    // 1) 前端詞表
    {
        const src = readJson('web/js/i18n/zh-TW.json');
        const overrides = readOverrides('scripts/i18n/zh-CN.web-overrides.json');
        results.push({ rel: 'web/js/i18n/zh-CN.json', text: render(convertTree(src, overrides)) });
    }

    // 2) 後端詞表（同一個檔裡四語並列）
    {
        const coreOverrides = readOverrides('scripts/i18n/zh-CN.core-overrides.json');
        const dir = path.join(ROOT, 'core/i18n');
        for (const name of fs.readdirSync(dir).filter((f) => f.endsWith('.json')).sort()) {
            const rel = `core/i18n/${name}`;
            const data = readJson(rel);
            if (!data['zh-TW']) continue;
            const next = {};
            for (const [k, v] of Object.entries(data)) {
                next[k] = k === 'zh-CN' ? convertTree(data['zh-TW'], coreOverrides[name] || {}) : v;
            }
            if (!('zh-CN' in data)) next['zh-CN'] = convertTree(data['zh-TW'], coreOverrides[name] || {});
            results.push({ rel, text: render(next) });
        }
    }

    let stale = 0;
    for (const { rel, text } of results) {
        const p = path.join(ROOT, rel);
        const current = fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : '';
        if (current === text) continue;
        stale += 1;
        if (CHECK) {
            console.error(`✗ ${rel} 不是由 zh-TW 生成的最新結果——跑 npm run i18n:zh-cn`);
        } else {
            fs.writeFileSync(p, text);
            console.log(`✓ 已更新 ${rel}`);
        }
    }
    if (CHECK && stale) process.exit(1);
    if (!stale) console.log('zh-CN 已是最新');
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main();
