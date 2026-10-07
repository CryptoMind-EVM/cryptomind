"""Connections（連結）板塊的接線回歸（2026-09-08）。

design：docs/plans/2026-09-08-connections-tab-design.md

分頁註冊分散在四處（index.html 容器、spa.js 的 VALID_TABS/_TAB_MODULES、
nav-config.js 的 NAV_ITEMS、components/tab-*.js 模板），漏一處就是「分頁
不可達」而且沒有任何錯誤訊息——前端無單元測試框架，用原始碼接線斷言鎖住。

2026-09-08 事故：#702 漏了隱藏的第五處——executeTabSwitch 的 inject 白名單，
connections 分頁整頁空白（線上回報）。根治後注入改由模板存在性資料驅動，
白名單不再存在；本檔同時鎖死「白名單不得復活」與其餘兩份清單的一致性。

搬遷本身的不變量：TelegramLinkApp 靠 DOM-ID 驅動（#telegram-link-content、
#telegram-status-badge），整卡搬家時那兩個 ID 必須原封不動，否則 App 掛不上。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
INDEX_HTML = REPO / "web" / "index.html"
SPA_JS = REPO / "web" / "js" / "spa.js"
NAV_CONFIG = REPO / "web" / "js" / "nav-config.js"
TAB_CONNECTIONS = REPO / "web" / "js" / "components" / "tab-connections.js"
TAB_SETTINGS = REPO / "web" / "js" / "components" / "tab-settings.js"
CONNECTIONS_JS = REPO / "web" / "js" / "connections.js"
CLICK_DELEGATOR = REPO / "web" / "js" / "click-delegator.js"
MAIN_JS = REPO / "web" / "js" / "main.js"
LOCALES = ("zh-TW", "zh-CN", "en", "ru")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_tab_registered_in_all_four_places():
    """四處齊備，少一處分頁就到不了。"""
    assert 'id="connections-tab"' in _read(INDEX_HTML), "index.html 缺 #connections-tab 容器"

    spa = _read(SPA_JS)
    valid = re.search(r"var VALID_TABS = \[(.*?)\];", spa, re.S)
    assert valid and "'connections'" in valid.group(1), "spa.js VALID_TABS 缺 connections"
    modules = re.search(r"const _TAB_MODULES = \{(.*?)\n\};", spa, re.S)
    assert modules and re.search(r"\n    connections:", modules.group(1)), (
        "spa.js _TAB_MODULES 缺 connections——模板不會被載入，Components.inject 會失敗"
    )

    nav = _read(NAV_CONFIG)
    assert re.search(r"id:\s*'connections'", nav), "nav-config.js NAV_ITEMS 缺 connections"

    assert "window.Components.connections" in _read(TAB_CONNECTIONS)


def test_components_injection_is_data_driven():
    """注入由模板存在性決定，不由人工白名單（2026-09-09 根治）。

    #702 的 connections 漏列 executeTabSwitch 的 inject 白名單 → 分頁整頁
    空白、無任何錯誤訊息。白名單移除後，新分頁只要照 _TAB_MODULES 載入
    components/tab-*.js 模板就自動獲得注入，漏列在結構上不可能。
    """
    spa = _read(SPA_JS)
    assert re.search(r"typeof window\.Components\[tabId\] === 'string'", spa), (
        "executeTabSwitch 應以 window.Components[tabId] 模板存在性決定是否注入"
    )
    block = re.search(
        r"_ensureTabModules\(tabId\);(.*?)Components\.inject", spa, re.S
    )
    assert block, "executeTabSwitch 找不到 inject 呼叫"
    assert not re.search(r"\[\s*'[a-z-]+'(?:\s*,\s*'[a-z-]+')+\s*,?\s*\]", block.group(1)), (
        "inject 前不該再出現分頁 id 列舉——那是白名單復活，同類事故會再犯"
    )


def test_every_tab_template_registers_under_a_valid_tab():
    """模板鍵必須等於檔名且是 VALID_TABS——鍵打錯＝資料驅動注入靜默
    跳過，分頁空白（同白名單漏列的症狀，換個地方發生）。

    components/ 混住模板模組與邏輯模組（tab-journal.js 是 JournalTab
    渲染器、無模板）——只對有註冊 window.Components 的檔案做檢查。
    """
    spa = _read(SPA_JS)
    valid_match = re.search(r"var VALID_TABS = \[(.*?)\];", spa, re.S)
    assert valid_match
    valid = set(re.findall(r"'([a-z-]+)'", valid_match.group(1)))

    registered_any = False
    for path in sorted((REPO / "web" / "js" / "components").glob("tab-*.js")):
        registered = re.search(
            r"window\.Components(?:\.([A-Za-z-]+)|\['([a-z-]+)'\])\s*=", _read(path)
        )
        if not registered:
            continue
        registered_any = True
        tab_id = registered.group(1) or registered.group(2)
        assert tab_id in valid, f"{path.name} 註冊的 '{tab_id}' 不在 VALID_TABS"
        assert path.name == f"tab-{tab_id}.js", (
            f"{path.name} 與註冊鍵 '{tab_id}' 不一致——檔名必須是 tab-<id>.js"
        )
    assert registered_any, "沒有任何模板註冊——解析方式失效了"


def test_lazy_module_groups_are_all_valid_tabs():
    """_TAB_MODULES 的鍵必須都在 VALID_TABS——鍵打錯 switchTab 進不去，
    模組永遠不會被載入。"""
    spa = _read(SPA_JS)
    valid_match = re.search(r"var VALID_TABS = \[(.*?)\];", spa, re.S)
    assert valid_match
    valid = set(re.findall(r"'([a-z-]+)'", valid_match.group(1)))

    modules = re.search(r"const _TAB_MODULES = \{(.*?)\n\};", spa, re.S)
    assert modules
    keys = re.findall(r"^    ([a-z-]+): \(\)", modules.group(1), re.M)
    assert keys, "解析不到 _TAB_MODULES 的鍵"
    drift = set(keys) - valid
    assert not drift, f"_TAB_MODULES 有鍵不在 VALID_TABS：{sorted(drift)}"


def test_resume_tab_check_uses_valid_tabs_directly():
    """resume 的有效性檢查必須直接用 VALID_TABS——原本的第二份手工副本
    已漂掉 trust／wallet-monitor／ai-studio／discover／studio，那些分頁
    恢復時 UI 不還原（2026-09-09 合一根治）。"""
    spa = _read(SPA_JS)
    fn = re.search(r"function restoreUiStateAfterResume\(\)\s*\{.*?\n\}", spa, re.S)
    assert fn, "找不到 restoreUiStateAfterResume"
    body = fn.group(0)
    assert "VALID_TABS.includes(savedTab)" in body, (
        "resume 必須直接以 VALID_TABS 判斷，不得維護第二份清單"
    )
    assert "new Set([" not in body, "resume 內不可再有 tab id 的手工列舉"


def test_connections_module_loads_telegram_link():
    """telegram-link.js 已從 main.js 移出，只能靠這個 lazy 群組載入。"""
    spa = _read(SPA_JS)
    block = re.search(r"connections: \(\) => Promise\.all\(\[(.*?)\]\)", spa, re.S)
    assert block, "找不到 connections 的 _TAB_MODULES 群組"
    for mod in (
        "./connections.js",
        "./telegram-link.js",
        "./line-link.js",
        "./components/tab-connections.js",
    ):
        assert mod in block.group(1), f"connections 的模組群組缺 {mod}"

    assert "import './telegram-link.js'" not in _read(MAIN_JS), (
        "telegram-link.js 不該再靜態掛在首屏 bundle"
    )


def test_telegram_app_mount_points_survived_the_move():
    """TelegramLinkApp 以 DOM-ID 驅動——ID 改了就整卡失效。"""
    tab = _read(TAB_CONNECTIONS)
    assert 'id="telegram-link-content"' in tab
    assert 'id="telegram-status-badge"' in tab


def test_line_card_mount_points_and_delegation():
    """LineLinkApp 同樣以 DOM-ID 驅動（2026-09-08 上線）。"""
    tab = _read(TAB_CONNECTIONS)
    assert 'id="line-link-content"' in tab
    assert 'id="line-status-badge"' in tab
    assert "connections.lineSoon" not in tab, "「即將支援」佔位已被真正的卡取代"

    delegator = _read(REPO / "web" / "js" / "click-delegator.js")
    roots = re.search(r"allowedRoots = new Set\(\[(.*?)\]\)", delegator, re.S)
    assert roots and "'LineLinkApp'" in roots.group(1), (
        "LineLinkApp 不在白名單＝卡片上的按鈕全部靜默失效"
    )

    conn = _read(REPO / "web" / "js" / "connections.js")
    assert "LineLinkApp" in conn, "進分頁時沒有叫起 LineLinkApp"


def test_line_app_degrades_when_backend_disabled():
    """後端沒設 LINE_CHANNEL_SECRET 時 /api/line/status 回 404——
    那是「還沒開放」不是壞掉，不可以渲染成紅字錯誤。"""
    src = _read(REPO / "web" / "js" / "line-link.js")
    assert "renderUnavailable" in src
    assert "404" in src


def test_line_app_uses_english_fallbacks():
    """棘輪慣例：_t() 的 fallback 一律英文，中文交給 i18n 檔。"""
    src = _read(REPO / "web" / "js" / "line-link.js")
    # 註解裡的中文是說明，不是使用者看得到的字串——// 與 /* */ 兩種都要剝掉。
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("//")
    )
    offenders = [line.strip() for line in code.splitlines() if re.search(r"[一-鿿]", line)]
    assert not offenders, (
        "line-link.js 的可執行碼不該有中文——fallback 用英文，翻譯放 i18n："
        f"{offenders[:3]}"
    )


def test_settings_no_longer_owns_the_telegram_card():
    settings = _read(TAB_SETTINGS)
    assert "settings-telegram-card" not in settings, "Settings 仍留著舊的 Telegram 卡"
    assert 'id="telegram-link-content"' not in settings, (
        "掛載點在兩個分頁同時存在＝getElementById 只會抓到第一個，行為不確定"
    )
    assert "settings-connections-card" in settings, "Settings 少了前往 Connections 的引導卡"


def test_settings_guide_card_uses_a_first_screen_handler():
    """引導卡在 Settings 上被點——ConnectionsTab 那時還沒 lazy 載入，
    click-delegator 會靜默吞掉（typeof fn !== 'function' 就 return）。"""
    settings = _read(TAB_SETTINGS)
    card = re.search(r'id="settings-connections-card".*?</div>\s*</div>', settings, re.S)
    assert card, "找不到引導卡"
    assert 'data-click="GlobalNav.navigateToTab"' in card.group(0)
    assert 'data-click-arg="connections"' in card.group(0)
    assert 'data-click="ConnectionsTab' not in card.group(0), (
        "引導卡不能綁 ConnectionsTab.*——那是 connections 分頁的 lazy chunk，"
        "在 Settings 上還沒載入，點了不會有任何反應也不會報錯。"
    )


def test_connections_tab_root_is_allowlisted_in_click_delegator():
    """返回鍵走 data-click='ConnectionsTab.goBack'，root 沒進白名單就無效。"""
    delegator = _read(CLICK_DELEGATOR)
    roots = re.search(r"allowedRoots = new Set\(\[(.*?)\]\)", delegator, re.S)
    assert roots and "'ConnectionsTab'" in roots.group(1)
    assert "ConnectionsTab.goBack" in _read(TAB_CONNECTIONS)
    assert "goBack()" in _read(CONNECTIONS_JS)


def test_connections_init_is_reentrant():
    """每次進分頁都該重抓綁定狀態；一次性守衛正是 AI Studio 那個坑。"""
    js = _read(CONNECTIONS_JS)
    assert not re.search(r"if\s*\(\s*this\._initialized\s*\)\s*return", js), (
        "ConnectionsTab 不該有一次性守衛——重進分頁要重抓 /api/telegram/status"
    )
    assert "TelegramLinkApp" in js


def test_new_i18n_keys_present_in_every_locale():
    keys = (
        ("nav", "connections"),
        ("settings", "connectionsGuide"),
        ("connections", "title"),
        ("connections", "subtitle"),
        ("connections", "back"),
    )
    for lang in LOCALES:
        data = json.loads((REPO / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8"))
        for section, key in keys:
            assert key in data.get(section, {}), f"{lang}.json 缺 {section}.{key}"


def test_connections_stays_out_of_the_default_nav_slots():
    """底部導覽只有 5 格（MAX_ENABLED_ITEMS）——新板塊預設不搶既有使用者的位置。"""
    nav = _read(NAV_CONFIG)
    entry = re.search(r"\{\s*id:\s*'connections'.*?\},", nav, re.S)
    assert entry, "找不到 connections 的 NAV_ITEMS 條目"
    assert "defaultEnabled: false" in entry.group(0)
