// web/js/*.js 在 Node 眼中是 CommonJS（package.json 沒有 "type": "module"），
// 直接 import 會噴 "Cannot use import statement outside a module"。改成讀原始碼
// 包成 data: URL 當 ESM 載入——但 data: URL 沒有 base 路徑，模組之間的相對
// import（evm-walletconnect → evm-siwx）解析不了，所以要先把相依也遞迴包好、
// 把 specifier 換成對應的 data: URL。
import { readFile } from 'node:fs/promises';
import path from 'node:path';

// ./ 與 ../ 都要換（components/tab-journal.js 從 '../journal-calendar.js' 引入）
const RELATIVE_IMPORT_RE = /from\s+'(\.{1,2}\/[^']+)'/g;

// 呼叫端慣用 new URL(rel, import.meta.url).pathname 當路徑。Windows 上那會是 "/C:/repo/web/…"，
// readFile 會把它當成 "<目前磁碟>:\C:\repo\…"（C:\C:\… 找不到檔，整批 node gate 測試本機全紅）。
// 開頭是 "/<磁碟機>:" 才去掉前導斜線；Linux／macOS 的 "/home/…" 原樣不動。
function toFsPath(filePath) {
    return /^\/[A-Za-z]:[\\/]/.test(filePath) ? filePath.slice(1) : filePath;
}

export async function loadModuleUrl(filePath) {
    filePath = toFsPath(filePath);
    let source = await readFile(filePath, 'utf8');
    const dir = path.dirname(filePath);
    const specifiers = [...source.matchAll(RELATIVE_IMPORT_RE)].map((m) => m[1]);
    for (const specifier of new Set(specifiers)) {
        const depUrl = await loadModuleUrl(path.join(dir, specifier));
        source = source.split(`'${specifier}'`).join(`'${depUrl}'`);
    }
    return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
}
