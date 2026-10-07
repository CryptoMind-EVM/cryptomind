// SW precache 清單轉換——純函式斷言（2026-09-27 PR-1 載入速度）。
import assert from 'node:assert/strict';
import {
    filterPrecacheManifest,
    isWalletVendorChunk,
    precacheCollector,
} from '../../vite-sw-precache.mjs';

// 1) 只由 node_modules 模組組成的 chunk＝錢包 SDK（全站只有 evm-walletconnect.js 動態 import 套件）
assert.equal(isWalletVendorChunk(['/repo/node_modules/@reown/appkit/dist/esm/w3m-modal.js']), true);
assert.equal(
    isWalletVendorChunk([
        '/repo/node_modules/@walletconnect/core/dist/index.es.js',
        'C:\\repo\\node_modules\\ethers\\lib.esm\\index.js', // Windows 路徑也要認得
    ]),
    true
);
// 混到自家模組就不是（evm-walletconnect 的 chunk 本身、shared-core…）
assert.equal(
    isWalletVendorChunk(['/repo/web/js/evm-walletconnect.js', '/repo/node_modules/buffer/index.js']),
    false
);
assert.equal(isWalletVendorChunk(['/repo/web/js/main.js']), false);
// rolldown runtime 之類的虛擬模組不在 node_modules：保留
assert.equal(isWalletVendorChunk(['\0rolldown/runtime.js']), false);
assert.equal(isWalletVendorChunk([]), false);
assert.equal(isWalletVendorChunk(undefined), false);

// 2) 收集器：記下本次產物全部檔名，並依模組來源挑出錢包 chunk（asset 不是 chunk）
const state = { bundle: new Set(), wallet: new Set() };
precacheCollector(state).writeBundle(
    {},
    {
        'index.html': { type: 'asset' },
        'assets/main-AAAA1111.js': { type: 'chunk', moduleIds: ['/repo/web/js/main.js'] },
        'assets/w3m-modal-BBBB2222.js': {
            type: 'chunk',
            moduleIds: ['/repo/node_modules/@reown/appkit-scaffold-ui/dist/esm/w3m-modal.js'],
        },
        'assets/PhWallet-CCCC3333.js': {
            type: 'chunk',
            moduleIds: ['/repo/node_modules/@phosphor-icons/webcomponents/dist/PhWallet.mjs'],
        },
        'assets/forum/post-DDDD4444.js': { type: 'chunk', moduleIds: ['/repo/web/js/pages/post.js'] },
    }
);
assert.deepEqual([...state.wallet].sort(), ['assets/PhWallet-CCCC3333.js', 'assets/w3m-modal-BBBB2222.js']);
assert.equal(state.bundle.size, 5);

// 3) 清單轉換：拿掉 HTML、錢包 chunk、以及不屬於本次產物的舊檔（emptyOutDir:false 留下的）；
//    網址補上 /static/（sw.js 在根路徑 /sw.js 服務，相對網址會被解析成 /index.html、/assets/…——
//    前者正式站 404 讓 install 整個失敗，後者跟頁面實際請求的 /static/assets/… 對不上）
const entries = [
    { url: 'index.html', revision: 'r1' },
    { url: 'forum/index.html', revision: 'r2' },
    { url: 'assets/main-AAAA1111.js', revision: null },
    { url: 'assets/main-OLDOLD00.js', revision: null }, // 上一次 build 的殘留
    { url: 'assets/w3m-modal-BBBB2222.js', revision: null },
    { url: 'assets/PhWallet-CCCC3333.js', revision: null },
    { url: 'assets/forum/post-DDDD4444.js', revision: null },
];
const out = filterPrecacheManifest(entries, state, '/static/');
assert.deepEqual(out, [
    { url: '/static/assets/main-AAAA1111.js', revision: null },
    { url: '/static/assets/forum/post-DDDD4444.js', revision: null },
]);
// 不改動傳入的 entry
assert.equal(entries[2].url, 'assets/main-AAAA1111.js');

console.log('sw_precache: ok');
