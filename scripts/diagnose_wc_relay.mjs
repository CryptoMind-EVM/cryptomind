// diagnose_wc_relay.mjs — 雙 process 迴環測試：分別以 --dapp / --wallet 啟動，
// 各自持有一條獨立的 relay WebSocket（node 下同 process 的兩個 SignClient 會
// 共用 core 單例，事件混流、測不準）。dapp 產生 pairing URI 寫到臨時檔，
// 錢包端讀檔 pair → approve；dapp 的 approval() settle＝projectId／relay／
// proposal／settle 全鏈路通——問題必在特定錢包 App 或手機網路環境。
//
// 用法：node scripts/diagnose_wc_relay.mjs run      （一次跑完雙端）
//       node scripts/diagnose_wc_relay.mjs --dapp   （手動分開跑也行）
//       node scripts/diagnose_wc_relay.mjs --wallet
import { writeFile, readFile, rm } from 'node:fs/promises';
import path from 'node:path';
import SignClientMod from '@walletconnect/sign-client';

const SignClient = SignClientMod.default || SignClientMod;
const PROJECT_ID = 'af1d2fb078465000943d5caa4959538e';
const URI_FILE = path.join(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')), '..', 'outputs', 'wc_diag_uri.txt');
const METADATA = {
    name: 'diag-loopback',
    description: 'relay loopback diagnosis',
    url: 'https://getcryptomind.com',
    icons: [],
};
const REQUIRED = {
    eip155: {
        chains: ['eip155:1'],
        methods: ['personal_sign', 'eth_requestAccounts'],
        events: ['accountsChanged', 'chainChanged'],
    },
};
const t0 = Date.now();
const log = (m) => console.log(`[${String(Date.now() - t0).padStart(6)}ms] ${m}`);
const fail = (code, msg) => { log(msg); process.exit(code); };

async function runDapp() {
    const dapp = await SignClient.init({ projectId: PROJECT_ID, metadata: METADATA });
    log('dapp init ok');
    const { uri, approval } = await dapp.connect({ requiredNamespaces: REQUIRED });
    log(`URI 產生（topic=${String(uri).split(':')[1]?.split('@')[0]?.slice(0, 12)}…）`);
    await writeFile(URI_FILE, uri, 'utf8');
    const session = await Promise.race([
        approval(),
        new Promise((_, rej) => setTimeout(() => rej(new Error('90s 內 session 沒有 settle')), 90000)),
    ]);
    log(`✅ dapp approval settle：${session.namespaces.eip155.accounts.join(',')}`);
    process.exit(0); // WebSocket 會吊住 event loop，明確退出
}

async function runWallet() {
    const wallet = await SignClient.init({ projectId: PROJECT_ID, metadata: METADATA });
    log('wallet init ok');
    let uri = null;
    for (let i = 0; i < 60 && !uri; i += 1) {
        try { uri = await readFile(URI_FILE, 'utf8'); } catch (e) { await new Promise((r) => setTimeout(r, 500)); }
    }
    if (!uri) fail(3, '❌ 30s 內沒等到 dapp 的 URI 檔');
    log('讀到 URI，pair 中');
    let settled = false;
    wallet.on('session_proposal', async (event) => {
        log(`收到 session_proposal (id=${event.id})`);
        try {
            await wallet.approve({
                id: event.id,
                namespaces: {
                    eip155: {
                        accounts: ['eip155:1:0x1111111111111111111111111111111111111111'],
                        methods: REQUIRED.eip155.methods,
                        events: REQUIRED.eip155.events,
                    },
                },
            });
            settled = true;
            log('已 approve（approve resolve＝settle 完成；錢包端沒有 session_settle 事件，別等）');
        } catch (e) {
            fail(2, '❌ approve 失敗：' + e.message);
        }
    });
    await wallet.pair({ uri });
    await new Promise((resolve) => {
        const timer = setInterval(() => { if (settled) { clearInterval(timer); resolve(); } }, 250);
        setTimeout(() => { clearInterval(timer); resolve(); }, 90000);
    });
    if (!settled) fail(1, '❌ 錢包端 90s 沒收到 session_proposal——relay publish／projectId 層問題');
    await rm(URI_FILE, { force: true });
    process.exit(0); // SignClient 的 WebSocket 會吊住 event loop，明確退出
}

const mode = process.argv[2] || 'run';
if (mode === '--dapp') await runDapp();
else if (mode === '--wallet') await runWallet();
else if (mode === 'run') {
    const { spawn } = await import('node:child_process');
    const wallet = spawn(process.execPath, [new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'), '--wallet'], { stdio: 'inherit' });
    let dappCode = 0;
    try {
        await runDapp();
    } catch (e) {
        dappCode = 1;
        log('❌ dapp 端：' + e.message);
    }
    const walletCode = await new Promise((r) => wallet.on('exit', r));
    await rm(URI_FILE, { force: true });
    if (dappCode === 0 && walletCode === 0) {
        log('結論：projectId／relay／proposal／settle 全鏈路通——問題在特定錢包 App 或手機網路環境。');
    }
    process.exit(dappCode || walletCode || 0);
} else {
    console.error('unknown mode: ' + mode);
    process.exit(64);
}
