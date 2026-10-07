/* 從 web/styles.css 讀出實際 token 值，算 WCAG 對比。
   靜態掃描看不出「這個顏色在這個主題下讀不到」，只能真的算。 */
import { readFileSync } from 'fs';

const css = readFileSync('web/styles.css', 'utf8');

function scope(selector) {
  const i = css.indexOf(selector);
  if (i < 0) throw new Error('找不到 ' + selector);
  return css.slice(i, css.indexOf('}', i));
}
function tokens(block, fallback) {
  const get = (name) => {
    const m = block.match(new RegExp('--color-' + name + ':\\s*([\\d\\s]+);'));
    if (m) return m[1].trim().split(/\s+/).map(Number);
    if (fallback) return fallback(name);
    throw new Error('找不到 --color-' + name);
  };
  return get;
}
const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
const L = ([r, g, b]) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
const cr = (a, b) => { const [x, y] = [L(a), L(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

const root = scope(':root {');
const dark = scope('html.dark {');
const lightGet = tokens(root);
const darkGet = tokens(dark, (n) => lightGet(n));   // html.dark 沒覆寫就沿用 :root

const AA = 4.5;
let bad = 0;
for (const [theme, get] of [['淺色 :root', lightGet], ['深色 html.dark', darkGet]]) {
  const bg = get('background'), surf = get('surface');
  console.log(`\n=== ${theme} ===`);
  for (const name of ['primary', 'accent', 'success', 'danger']) {
    const c = get(name);
    const checks = {
      '文字 on background': cr(c, bg),
      '文字 on surface': cr(c, surf),
      // 實心按鈕慣例是 text-background（主題感知：淺色給近白、深色給近黑）
      '實心鈕 text-background': cr(bg, c),
    };
    for (const [what, v] of Object.entries(checks)) {
      const ok = v >= AA;
      if (!ok) bad++;
      console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name.padEnd(8)} ${what.padEnd(24)} ${v.toFixed(2)}:1`);
    }
  }
}
console.log(bad === 0 ? '\n全部達 WCAG AA (4.5:1)' : `\n${bad} 項未達 AA`);
process.exit(bad === 0 ? 0 : 1);
