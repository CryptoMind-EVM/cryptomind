import { readFileSync } from 'fs';
const src = readFileSync('web/js/layout-debug.js', 'utf8');
const grabFrom = (text, re) => { const m = text.match(re); if (!m) throw new Error('抽不到: ' + re); return m[0]; };
const grab = (re) => grabFrom(src, re);
const parts = [
  grab(/const PRIVATE_IPV4 = new RegExp\([\s\S]*?\);/),
  grab(/function isPrivateIPv4\(host\) \{[\s\S]*?\n\}/),
  grab(/function isLocalHost\(\) \{[\s\S]*?\n\}/)
    .replace('function isLocalHost() {', 'function isLocalHost(hostArg) {')
    .replace("(window.location.hostname || '')", "(hostArg || '')"),
];
const fn = new Function(parts.join('\n') + '; return isLocalHost;')();

// config.js 的 DEBUG_MODE 用同一套判斷（正式站靜音 console）。兩支各寫一份，
// 這裡強制它們對同一組 hostname 得到相同結果，避免日後分岔。
const cfgSrc = readFileSync('web/config.js', 'utf8');
const cfgParts = [
  grabFrom(cfgSrc, /var PRIVATE_IPV4 = new RegExp\([\s\S]*?\);/),
  grabFrom(cfgSrc, /function isPrivateIPv4\(host\) \{[\s\S]*?\n    \}/),
  grabFrom(cfgSrc, /function isLocalHost\(\) \{[\s\S]*?\n    \}/)
    .replace('function isLocalHost() {', 'function isLocalHost(hostArg) {')
    .replace("(window.location.hostname || '')", "(hostArg || '')"),
];
const cfgFn = new Function(cfgParts.join('\n') + '; return isLocalHost;')();
const cases = [
  ['cryptomind-ton.zeabur.app', false, '正式站'],
  ['CRYPTOMIND-TON.ZEABUR.APP', false, '正式站（大寫）'],
  ['evil-localhost.attacker.com', false, '含 localhost 字樣'],
  ['localhost.evil.com', false, 'localhost 開頭'],
  ['127.0.0.1.nip.io', false, 'IP 開頭的公開網域'],
  ['192.168.1.5.evil.com', false, '私網 IP 開頭的公開網域'],
  ['10.0.0.1.attacker.net', false, '10.x 開頭的公開網域'],
  ['192.168.999.1', false, '超出範圍的八位元組'],
  ['localhost', true, '本機'],
  ['127.0.0.1', true, '本機 IP'],
  ['::1', true, 'IPv6 loopback'],
  ['192.168.1.20', true, '區網（實機測試）'],
  ['10.0.0.7', true, '區網 10.x'],
  ['172.16.5.9', true, '區網 172.16'],
  ['172.32.0.1', false, '172.32 不是私網'],
  ['my-mac.local', true, 'mDNS'],
];
let bad = 0;
for (const [host, want, desc] of cases) {
  const got = fn(host), cfg = cfgFn(host);
  const ok = got === want && cfg === want;
  if (!ok) bad++;
  const note = got === cfg ? '' : `  ← layout-debug(${got}) 與 config.js(${cfg}) 不一致`;
  console.log(`  ${ok?'PASS':'FAIL'}  ${String(got).padEnd(5)} (預期 ${String(want).padEnd(5)})  ${host.padEnd(28)} ${desc}${note}`);
}
console.log(bad===0 ? '\n全部符合預期' : `\n${bad} 個不符預期`);
process.exit(bad===0?0:1);
