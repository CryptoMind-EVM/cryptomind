"""Regression checks for silent markup/CSS failures in the web shell.

兩類 bug 都不會噴錯、只會讓樣式默默失效，所以只能靠掃描抓：

1. 重複的 HTML 屬性 — 瀏覽器只認第一個，後面的整個被忽略。
   曾經讓 #chat-messages 的 shell-safe-area-x（safe-area 內距）與
   Telegram 登入鈕的全部樣式失效，表現為手機底部被導覽列切掉。
2. 定義了卻沒人用的版面變數 — 代表有兩套間距系統並存，
   一套是 CSS 硬編碼、一套是 JS 量測，遲早對不上而互相遮擋。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

# 一個開始標籤，連同它的屬性區段
TAG_RE = re.compile(r"<(\w[\w-]*)((?:\s+[^<>]*?)?)\s*/?>", re.S)


def _html_files():
    return sorted(WEB_DIR.rglob("*.html"))


def _duplicate_attributes(attr_section: str):
    """回傳 [(屬性名, 出現次數)]，只看重複的。"""
    names = re.findall(r"(?:^|\s)([\w-]+)\s*=", attr_section)
    seen = {}
    for name in names:
        key = name.lower()
        seen[key] = seen.get(key, 0) + 1
    return [(name, count) for name, count in seen.items() if count > 1]


def test_no_duplicate_html_attributes():
    """同一個標籤不可有重複屬性 — 第二個之後會被瀏覽器丟掉。"""
    failures = []

    for path in _html_files():
        source = path.read_text(encoding="utf-8")
        for match in TAG_RE.finditer(source):
            duplicates = _duplicate_attributes(match.group(2))
            if not duplicates:
                continue
            line = source[: match.start()].count("\n") + 1
            detail = ", ".join(f"{name} x{count}" for name, count in duplicates)
            failures.append(
                f"{path.relative_to(WEB_DIR.parent)}:{line} <{match.group(1)}> 重複屬性: {detail}"
            )

    assert not failures, "重複屬性會讓後面那個整組失效:\n" + "\n".join(failures)


def test_no_duplicate_element_ids():
    """同一份 HTML 不可有重複 id — getElementById 只會回傳第一個。

    forum/index.html 曾經有兩組篩選指示器共用同一組 id，於是主內容區那組
    永遠不會顯示篩選文字、清除鈕也拿不到 listener；JS 端還留著
    `clearPostFiltersAlt !== clearPostFilters` 這種恆為 false 的守衛。
    """
    id_pattern = re.compile(r'\bid="([^"]+)"')
    failures = []

    for path in _html_files():
        ids = id_pattern.findall(path.read_text(encoding="utf-8"))
        seen = {}
        for value in ids:
            seen[value] = seen.get(value, 0) + 1
        duplicates = sorted(name for name, count in seen.items() if count > 1)
        if duplicates:
            failures.append(
                f"{path.relative_to(WEB_DIR.parent)}: {', '.join(duplicates)}"
            )

    assert not failures, (
        "重複的 id 會讓 getElementById 只取到第一個，其餘元素靜默失效"
        "（要綁多個元素請改用 class + querySelectorAll）:\n" + "\n".join(failures)
    )


def test_no_orphaned_mobile_layout_variables():
    """styles.css 裡宣告的版面變數必須有人用，否則就是遺留的第二套間距系統。"""
    css_path = WEB_DIR / "styles.css"
    source = css_path.read_text(encoding="utf-8")

    declared = set(
        re.findall(r"^\s*(--mobile-[\w-]+|--shell-[\w-]+)\s*:", source, re.M)
    )
    referenced = set(re.findall(r"var\(\s*(--[\w-]+)", source))

    # JS 端寫入的變數在 CSS 只會被讀、不會被宣告，這裡只查 CSS 自己宣告的
    orphans = sorted(declared - referenced)

    assert not orphans, (
        "以下版面變數宣告了卻沒有任何 var() 使用，代表有並存的間距系統:\n  "
        + "\n  ".join(orphans)
    )


# ── 帳本彙總的單位一致性 ──────────────────────────────────────────
# converted_amount 在後端寫入時就是「price x quantity x rate」的總額
# （core/orm/trade_journal_repo.py:160 與 :254）。前端若再乘一次 quantity，
# 總支出/總收入會放大 quantity 倍——數字看起來仍是合理的金額，不會噴錯，
# 只有對帳時才會發現（2026-08-24 review：3 份 100 USD 記成 28,800）。


def test_journal_summary_does_not_double_count_quantity():
    src = (WEB_DIR / "js" / "components" / "tab-journal.js").read_text(encoding="utf-8")
    offenders = re.findall(
        r"converted_amount[^\n]*\*\s*parseFloat\(\s*e\.quantity", src
    )
    assert not offenders, (
        "tab-journal.js：converted_amount 已含 quantity，不可再乘一次——"
        f"發現 {offenders}"
    )


# ── 地址健診入口（chat banner → scam-tracker）─────────────────────
# 2026-08-24 DANNY 回報三件事：banner 關不掉、scam-tracker 排版壞、
# 返回鍵退到還沒公開的論壇。三個都是靜態 markup 問題，可以掃出來。

SCAM_PAGES = ["submit.html", "detail.html"]  # 列表頁 2026-09-27 改成 SPA 分頁 #scamcheck


def test_scam_tracker_does_not_link_back_to_unreleased_forum():
    """論壇尚未公開；scam-tracker 的返回鍵不可以把使用者丟進去。"""
    offenders = []
    for name in SCAM_PAGES:
        p = WEB_DIR / "scam-tracker" / name
        if not p.exists():
            continue
        html = p.read_text(encoding="utf-8")
        if "index.html#forum" in html or "safety.backToForum" in html:
            offenders.append(name)
    assert not offenders, f"scam-tracker 這些頁的返回鍵指向未公開的論壇：{offenders}"


def test_scam_tracker_nav_reserves_space_for_fixed_theme_toggle():
    """ThemeToggle 是 position:fixed 右上角圓鈕（z-index 9999），
    nav 右側必須留出空間，否則會蓋住按鈕文字（實測：提交檢舉被裁掉）。"""
    offenders = []
    for name in SCAM_PAGES:
        p = WEB_DIR / "scam-tracker" / name
        if not p.exists():
            continue
        html = p.read_text(encoding="utf-8")
        if "ThemeToggle.js" not in html:
            continue
        nav = re.search(r"<nav\b.*?</nav>", html, re.S)
        assert nav, f"{name}: 找不到 <nav>"
        if "data-theme-toggle-gap" not in nav.group(0):
            offenders.append(name)
    assert not offenders, (
        f"這些頁的 nav 沒有替 fixed ThemeToggle 留位（缺 data-theme-toggle-gap）：{offenders}"
    )


# ── layout-debug 面板不得在正式站出現 ─────────────────────────────
# 2026-08-25 DANNY 第二次回報（第一次是 2026-08-21）：正式站跑出 debug 面板。
# 兩個根因：
#   1. armTapGesture() 在 initialize() 裡無條件執行，且直接呼叫 mount()，
#      完全繞過 isEnabled()——頁首連點 5 下就開（頁首正是國旗/主題鈕/通知鈴）。
#   2. `window.APP_CONFIG && window.APP_CONFIG.DEBUG_MODE !== true` 在
#      APP_CONFIG 不存在時短路，return false 永遠不執行；而 web/index.html
#      從未載入 config.js，所以主 SPA 上 APP_CONFIG 恆為 undefined。


def _strip_js_comments(src: str) -> str:
    """去掉 JS 註解——註解裡常引用舊的錯誤寫法作為說明，
    不剝掉的話守衛會誤判成「bug 還在」。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)  # 區塊註解
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)  # 行註解
    return src


def _layout_debug_src() -> str:
    return _strip_js_comments(
        (WEB_DIR / "js" / "layout-debug.js").read_text(encoding="utf-8")
    )


def test_layout_debug_has_no_ungated_tap_gesture():
    """連點手勢已移除——它會讓一般使用者在正式站誤觸。"""
    src = _layout_debug_src()
    assert "armTapGesture" not in src, (
        "layout-debug 仍有連點手勢；它繞過 isEnabled() 直接 mount()，正式站會誤觸"
    )


def test_layout_debug_gate_is_fail_closed_on_hostname():
    """守衛必須以 hostname 判斷且 fail-closed：非本機一律關閉。

    不可再依賴 window.APP_CONFIG——主 SPA 從不載入 config.js，那個條件恆為
    undefined，`APP_CONFIG && ...` 會短路成「不擋」。
    """
    src = _layout_debug_src()
    assert "hostname" in src, "isEnabled 沒有依 hostname 判斷，正式站擋不住"
    assert "window.APP_CONFIG &&" not in src, (
        "仍在用 `window.APP_CONFIG && ...`——APP_CONFIG 不存在時會短路，守衛失效"
    )


def test_layout_debug_hostname_gate_behaviour():
    """實際執行 isLocalHost，對真實與惡意 hostname 比對預期。

    純靜態掃描看不出 `/^192\\.168\\./` 這種只錨定開頭的正則會放行
    `192.168.1.5.evil.com`（2026-08-25 寫這組 case 時抓到）。
    node 不在時 skip——CI 有 node，本機沒有也不該擋住整套測試。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用")
    result = subprocess.run(
        [node, "tests/js/layout_debug_gate.mjs"],
        cwd=WEB_DIR.parent,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        "layout-debug hostname 守衛行為不符預期：\n" + result.stdout + result.stderr
    )


# ── 離線橫幅不可卡住 ──────────────────────────────────────────────
# 2026-08-25 DANNY 回報：手機切到其他 App 再切回來就變成「離線中」。
# 伺服器沒事（正式站 HTTP 200）。真凶是 offline-banner 只監聽 online/offline
# 事件：頁面被凍結／進 bfcache 期間網路恢復所送出的 online 事件收不到，
# 回到前景後沒有人重新檢查 navigator.onLine，橫幅就永遠留在畫面上。
# 同專案的 auth.js / market-ws.js / forum-app.js 都有處理 pageshow /
# visibilitychange，就這支漏了。


def test_offline_banner_rechecks_on_resume():
    src = (WEB_DIR / "js" / "offline-banner.js").read_text(encoding="utf-8")
    missing = [name for name in ("pageshow", "visibilitychange") if name not in src]
    assert not missing, (
        "offline-banner 沒有在回到前景時重新檢查連線狀態，"
        f"缺少 {missing} 監聽——切 App 回來會卡在「離線中」"
    )


# ── 主題色必須在兩個主題下都可讀 ──────────────────────────────────
# 2026-08-25：原本 primary/accent/success/danger 兩個主題共用一組值
# （styles.css 明寫「沿用 :root（不覆寫）」），結果每個顏色都在其中一端
# 不合格——深色 text-primary 3.14:1、text-danger 3.36:1，而 text-danger
# 用了 276 次、text-success 214 次，全是使用者要讀的損益數字。
# 靜態掃描看不出「這個顏色在這個主題下讀不到」，只能真的算 WCAG。


def test_theme_token_contrast_meets_aa():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用")
    result = subprocess.run(
        [node, "tests/js/theme_contrast.mjs"],
        cwd=WEB_DIR.parent,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        "主題色未達 WCAG AA：\n" + result.stdout + result.stderr
    )


def test_solid_semantic_buttons_use_theme_aware_text():
    """實心 bg-primary/success/danger 上不可用固定的 text-white / text-black。

    深色主題把這些底色調亮之後，白字只剩 2.5–2.8:1。text-background 會隨主題
    翻轉（淺色近白、深色近黑），兩邊都達 AA。
    """
    offenders = []
    # 全 web 掃：forum / scam-tracker / governance 的 .html 也有實心鈕
    # （第一版只掃 js + index.html，漏了它們——DOM 實測才抓到）
    pat = re.compile(
        r"bg-(?:primary|success|danger)(?:[ ]+[a-z0-9:/\[\]\.\-]+)*[ ]+text-(?:white|black)\b"
    )
    targets = sorted(set(WEB_DIR.rglob("*.js")) | set(WEB_DIR.rglob("*.html")))
    for path in targets:
        if pat.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(WEB_DIR)))
    assert not offenders, (
        f"這些檔案在實心語意色底上用了固定文字色，深色主題會讀不到：{sorted(set(offenders))}"
    )


# ── 正式站不該把 console 噴滿內部訊息 ─────────────────────────────
# 2026-08-25：logger.js（生產模式把 console.log/debug/info 換成 noop）只掛在
# forum×7 與 scam-tracker×3，主 SPA 從來沒載過——141 處 console.log 在正式站
# 全部照噴。而且 config.js 也沒載入主 SPA，window.APP_CONFIG 恆為 undefined，
# 所有 `APP_CONFIG && APP_CONFIG.DEBUG_MODE` 形式的判斷都是短路（見
# layout-debug 那次事故）。從 config.js 這個單一真相源修起。


def test_main_spa_loads_config_and_logger():
    """主 SPA 必須載入 config.js 與 logger.js，否則 DEBUG_MODE 判斷永遠是 undefined。"""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    # 必須比對真正的 <script src>：註解裡也會出現檔名，用 `in html` 會被
    # 自己的說明文字滿足（2026-08-25 驗守衛時抓到）。
    srcs = set(re.findall(r'<script[^>]*\bsrc="([^"?]+)', html))
    for name in ("config.js", "logger.js"):
        assert any(src.endswith("/" + name) for src in srcs), (
            f"index.html 沒有以 <script src> 載入 {name}——正式站的 console 靜音不會生效"
        )


def test_debug_mode_is_hostname_derived():
    """DEBUG_MODE 必須由 hostname 推導並 fail-closed，不可寫死。

    寫死 false 的話本機開發也沒有 log；寫死 true 就等於正式站全開。
    """
    src = _strip_js_comments((WEB_DIR / "config.js").read_text(encoding="utf-8"))
    assert "hostname" in src, "config.js 的 DEBUG_MODE 沒有依 hostname 推導"
    assert "DEBUG_MODE: false" not in src, (
        "DEBUG_MODE 仍然寫死 false——本機開發也沒有 log"
    )


def test_logger_silences_only_in_production():
    """實際跑 logger.js：正式站靜音 log/debug/info，warn/error 一律保留。

    靜態掃描看不出「重新指派有沒有真的發生」，用 vm 隔離跑一次最直接。
    warn/error 必須留著——正式站出事時那是唯一的線索；window._console
    也保留原始方法作為緊急出口。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用")
    result = subprocess.run(
        [node, "tests/js/logger_gate.mjs"],
        cwd=WEB_DIR.parent,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        "logger.js 行為不符預期：\n" + result.stdout + result.stderr
    )


def test_preset_pill_hidden_when_only_official_default():
    """只有官方預設可選時（訪客／免費層）pill 整組藏起來——點開只有「Default」的按鈕讓人困惑
    （2026-09-13 DANNY Base App 截圖）。"""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="chat-preset-wrap"' in html
    js = (WEB_DIR / "js" / "chat-preset.js").read_text(encoding="utf-8")
    assert "wrap.classList.toggle('hidden', !this._presets.length);" in js


def test_sidebar_menu_rerenders_on_auth_change():
    """訪客看到 Admin 分頁的根因：localStorage 舊使用者先畫了導覽，auth 初始化清掉後沒重畫
    （2026-09-13 Base App 截圖）。底部列 2026-09-24 拔除後，要重畫的是側欄功能選單。"""
    nav = (WEB_DIR / "js" / "global-nav.js").read_text(encoding="utf-8")
    assert "window.addEventListener('auth:changed', rerender);" in nav
    assert "const rerender = () => this.renderSidebarNav();" in nav
    auth = (WEB_DIR / "js" / "auth.js").read_text(encoding="utf-8")
    assert "new CustomEvent('auth:changed'" in auth


def test_chat_input_state_rechecked_on_auth_change():
    """已登入卻仍顯示「免費訪客模式」的根因：以訪客身分載入後才登入，checkApiKeyStatus 不會再跑，
    訪客 placeholder 殘留（2026-09-21 DANNY 截圖）。app.js 須監聽 auth:changed 並清掉 30 秒 TTL 後重判。"""
    app = (WEB_DIR / "js" / "app.js").read_text(encoding="utf-8")
    i = app.index("window.addEventListener('auth:changed'")
    block = app[i : i + 400]
    assert "AppStore.set('lastApiKeyCheck', 0)" in block
    assert "checkApiKeyStatus()" in block
