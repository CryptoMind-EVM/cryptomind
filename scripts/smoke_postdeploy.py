#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署後冒煙測試（Post-deploy Smoke Test）

對指定環境跑最小但有效的瀏覽器驗證——涵蓋 2026-08-20 五個「上線才抓到」
的 bug 類型（按鈕沒反應/active 標記/讓位爆增/key 閃現/資源 404）。

用法：
    # 未登入（訪客）冒煙
    BASE_URL=https://getcryptomind.com .venv/bin/python scripts/smoke_postdeploy.py

    # 帶登入態（完整冒煙；state 檔由 Playwright storage_state 產生）
    BASE_URL=... AUTH_STATE=~/.cryptomind-playwright-state.json \\
        .venv/bin/python scripts/smoke_postdeploy.py

退出碼：0=全過、1=有失敗（供 CI/部署管線判斷）。
"""
from __future__ import annotations

import os
import pathlib
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get('BASE_URL', 'https://getcryptomind.com').rstrip('/')
STATE = os.environ.get('AUTH_STATE', '')

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = '') -> None:
    results.append((name, ok, detail))
    print(('PASS ' if ok else 'FAIL ') + name + (f' — {detail}' if detail else ''))


def guest_checks(browser) -> None:
    ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
    page = ctx.new_page()
    errs: list[str] = []
    page.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
    page.on('response', lambda r: errs.append(f'{r.status} {r.url}') if r.status >= 400 and 'favicon' not in r.url else None)

    # 1. SPA 載入：sidebar 導覽渲染 + 訪客鎖定可視化 + guest banner
    page.goto(BASE + '/')
    page.wait_for_timeout(5000)
    nav_count = page.evaluate("() => document.querySelectorAll('#sidebar-nav-items [data-tab]').length")
    check('guest: sidebar 導覽渲染', nav_count >= 1, f'{nav_count} 項')
    cta = page.evaluate("() => [...document.querySelectorAll('#sidebar-nav-items button')].some(b => /解鎖|unlock/i.test(b.textContent))")
    check('guest: 鎖定 CTA 存在', bool(cta))
    banner = page.evaluate("() => { const b = document.getElementById('guest-banner'); return b && !b.classList.contains('hidden') && /\\d/.test(b.textContent); }")
    check('guest: banner 顯示額度數字', bool(banner))

    # 2. 訪客點鎖定分頁 → 登入窗 + 「以訪客身份繼續」逃生門
    #
    # 2026-09-05：這裡原本用 'crypto'，但 2026-08-27 的「訪客模式 Tier 1」
    # 把 chat ＋ 唯讀市場頁全部開放訪客（nav-config.js 的 guestAllowed: true），
    # crypto 早就不是鎖定分頁了——檢查在守一個一週後就被改掉的行為。
    # 本 workflow 因語法錯誤從沒真正執行過（見 smoke.yml），所以這個過期
    # 一直沒被發現；修好語法後第一次跑就是它報 FAIL。
    #
    # 改用 'journal'：guestAllowed 未設，是真正需要登入的分頁。
    page.evaluate("() => switchTab('journal')")
    page.wait_for_timeout(800)
    modal = page.evaluate("() => !document.getElementById('login-modal').classList.contains('hidden')")
    check('guest: 鎖定分頁彈登入窗', bool(modal))
    if modal:
        page.click('#login-modal [data-click="continueAsGuest"]')
        page.wait_for_timeout(800)
        closed = page.evaluate("() => document.getElementById('login-modal').classList.contains('hidden')")
        check('guest: 訪客逃生門關閉 modal', bool(closed))

    # 3. 舊詐騙查詢網址導到 SPA 分頁（2026-09-27）；子頁無致命錯誤（i18n/靜態資源完整）
    page.goto(BASE + '/scam-tracker/')
    page.wait_for_timeout(4000)
    check('scam-tracker: 舊網址導到 #scamcheck', page.url.endswith('#scamcheck'), page.url)
    page.goto(BASE + '/static/scam-tracker/detail.html?id=1')
    page.wait_for_timeout(4000)
    no_key = page.evaluate("() => !/\\b(safety|scamcheck|chat|governance)\\.[a-zA-Z]+/.test(document.title + document.body.innerText.slice(0, 500))")
    check('scam-tracker: 無原始 i18n key 露出', bool(no_key))
    ctx.close()


def authed_checks(browser) -> None:
    if not STATE:
        check('authed: （跳過 — 未提供 AUTH_STATE）', True)
        return
    path = pathlib.Path(STATE).expanduser()
    if not path.exists():
        check('authed: state 檔不存在，跳過', True, str(path))
        return
    ctx = browser.new_context(storage_state=str(path), viewport={'width': 1440, 'height': 900})
    page = ctx.new_page()
    page.goto(BASE + '/')
    page.wait_for_timeout(6000)
    page.wait_for_selector('#sidebar-nav-items [data-tab]:not([data-tab="chat"])', state='visible', timeout=10000)

    # 4. 登入態：無鎖、切頁 + active 標記跟上（#517 類型）
    lock_count = page.evaluate("() => document.querySelectorAll('#sidebar-nav-items svg.lucide-lock').length")
    check('authed: 導覽無鎖定', lock_count == 0)
    page.click('#sidebar-nav-items [data-tab="crypto"]')
    page.wait_for_timeout(900)
    tab = page.evaluate("() => document.querySelector('.tab-content:not(.hidden)')?.id")
    active = page.evaluate("() => document.querySelector('#sidebar-nav-items [data-tab=\"crypto\"]')?.className.includes('text-primary')")
    check('authed: 切頁 + active 標記', tab == 'crypto-tab' and bool(active), f'tab={tab} active={active}')

    # 5. 讓位 sanity：非 chat 分頁桌機不爆量（#513/#514 類型）
    pad = page.evaluate("() => parseInt(getComputedStyle(document.getElementById('crypto-tab')).paddingBottom) || 0")
    check('authed: 桌機讓位正常（<200px）', pad < 200, f'{pad}px')
    ctx.close()


def mobile_checks(browser) -> None:
    """手機版關鍵量測（2026-08-20 #527 事故教訓：aside 的 relative 蓋掉
    fixed，chat 被擠成 134px 窄柱——桌機量測全綠也抓不到）。"""
    state = ''
    if STATE:
        p = pathlib.Path(STATE).expanduser()
        if p.exists():
            state = str(p)
    ctx = browser.new_context(
        viewport={'width': 390, 'height': 844},
        is_mobile=True,
        storage_state=(state or None),
    )
    page = ctx.new_page()
    page.goto(BASE + '/#chat')
    page.wait_for_timeout(5000)
    d = page.evaluate("""() => {
        const aside = document.getElementById('chat-sidebar');
        const main = document.getElementById('main-content');
        const msgs = document.getElementById('chat-messages');
        return {
            asidePos: aside ? getComputedStyle(aside).position : null,
            mainW: main ? Math.round(main.getBoundingClientRect().width) : 0,
            msgsW: msgs ? Math.round(msgs.getBoundingClientRect().width) : 0,
            vw: window.innerWidth,
        };
    }""")
    check('mobile: aside 維持 fixed（off-canvas）', d['asidePos'] == 'fixed', f"pos={d['asidePos']}")
    check(
        'mobile: chat 內容寬度正常（≥90% 視窗）',
        d['msgsW'] >= d['vw'] * 0.9,
        f"msgs={d['msgsW']}px / vw={d['vw']}px",
    )
    # 貼底導覽：登入者必須顯示；訪客由 early-init 刻意隱藏（僅 chat 可用）
    pill = page.evaluate("() => { const el = document.getElementById('global-nav-container'); return el && getComputedStyle(el).display !== 'none'; }")
    if state:
        check('mobile: 貼底導覽顯示（登入者）', bool(pill))
    else:
        check('mobile: 訪客無貼底導覽（early-init 設計）', not pill)
    ctx.close()


def forum_checks(browser) -> None:
    ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
    page = ctx.new_page()
    # 2026-09-26 論壇首頁合併進 App 的 #forum：舊網址只剩 meta refresh 轉址
    #（原本檢查的 #global-nav-container 早就不存在了，這條檢查一直是壞的）
    page.goto(BASE + '/static/forum/index.html')
    page.wait_for_timeout(5000)
    check('forum: 舊首頁轉到 /static/index.html#forum', page.url.endswith('/static/index.html#forum'), page.url)
    # 2026-09-27 上市準備 design §3：論壇對訪客隱藏（真人文章累積前），這個 context 沒登入——
    # 訪客到 #forum 要彈登入窗、論壇分頁不顯示
    modal = page.evaluate("() => !document.getElementById('login-modal').classList.contains('hidden')")
    check('forum: 訪客到 #forum 彈登入窗（論壇對訪客隱藏）', bool(modal), modal)
    ctx.close()


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        guest_checks(browser)
        authed_checks(browser)
        forum_checks(browser)
        mobile_checks(browser)
        browser.close()

    fails = [n for n, ok, _ in results if not ok]
    print('\n' + ('ALL PASS (%d checks)' % len(results) if not fails else 'FAILED: %s' % fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
