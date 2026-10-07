import { readFileSync } from 'fs';
import vm from 'vm';

const src = readFileSync('web/js/logger.js', 'utf8');

function run(debugMode) {
  const fakeConsole = {
    log(){}, debug(){}, info(){}, warn(){}, error(){},
  };
  for (const k of Object.keys(fakeConsole)) fakeConsole[k].__native = true;
  const win = { APP_CONFIG: { DEBUG_MODE: debugMode } };
  const ctx = vm.createContext({ console: fakeConsole, window: win });
  vm.runInContext(src, ctx);
  const c = ctx.console;
  return {
    log靜音: !c.log.__native, debug靜音: !c.debug.__native, info靜音: !c.info.__native,
    warn保留: !!c.warn.__native, error保留: !!c.error.__native,
    緊急出口: typeof win._console,
  };
}

console.log('DEBUG_MODE=true（本機）  ', JSON.stringify(run(true)));
console.log('DEBUG_MODE=false（正式站）', JSON.stringify(run(false)));

const prod = run(false), dev = run(true);
const ok = prod.log靜音 && prod.debug靜音 && prod.info靜音 && prod.warn保留 && prod.error保留
        && !dev.log靜音 && dev.緊急出口 === 'object';
console.log(ok ? '\n行為符合預期' : '\n行為不符預期');
process.exit(ok ? 0 : 1);
