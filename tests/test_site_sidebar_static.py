"""論壇等子頁共用側欄（web/js/site-sidebar.js）的版面回歸測試（2026-09-26 回報）。"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _fn(src: str, name: str) -> str:
    body = src[src.index(f"function {name}(") :]
    return body[: body.index("\n    function ", 1)]


def test_history_panel_stacks_sessions_vertically():
    """面板是 display:flex；少了 flex-direction:column，每筆對話會排成橫向一整排、擠成窄條"""
    src = (ROOT / "web/js/site-sidebar.js").read_text(encoding="utf-8")
    body = _fn(src, "renderHistory")
    assert "display:flex" in body
    assert "flex-direction:column" in body


def test_logged_in_user_card_shows_initial_not_placeholder():
    """登入後左下角要顯示名字首字母（同 SPA 側欄），不能一律畫訪客的人形圖示"""
    src = (ROOT / "web/js/site-sidebar.js").read_text(encoding="utf-8")
    body = _fn(src, "renderUserCard")
    assert "👤" not in body
    assert "charAt(0).toUpperCase()" in body


_IMPORT_RE = re.compile(r"""import\s+(?:[^'"]*from\s+)?['"](\.{1,2}/[^'"?]+)""")
_MODULE_SRC_RE = re.compile(r'<script[^>]*type="module"[^>]*src="([^"?]+)')


def _sidebar_pages():
    for html in sorted((ROOT / "web").rglob("*.html")):
        text = html.read_text(encoding="utf-8")
        if "/js/site-sidebar.js" in text:
            yield html, text


def _imports(entry: Path, module: str) -> bool:
    seen, todo = set(), [entry]
    while todo:
        path = todo.pop()
        if path in seen or not path.exists():
            continue
        seen.add(path)
        src = path.read_text(encoding="utf-8")
        for spec in _IMPORT_RE.findall(src):
            dep = (path.parent / spec).resolve()
            if dep.name == module:
                return True
            todo.append(dep)
    return False


def _pages_missing(module: str) -> list[str]:
    missing = []
    for html, text in _sidebar_pages():
        entries = [
            ROOT / "web" / src.removeprefix("/static/").removeprefix("/")
            for src in _MODULE_SRC_RE.findall(text)
        ]
        if not any(_imports(e, module) for e in entries):
            missing.append(html.relative_to(ROOT).as_posix())
    return missing


def test_every_sidebar_page_loads_nav_preferences():
    """功能選單讀 window.NavPreferences；頁面 entry 沒 import nav-config.js 時選單是空的
    （論壇發文／文章／私訊／個人頁／Premium 都缺過）"""
    assert _pages_missing("nav-config.js") == []


def test_every_sidebar_page_can_switch_language():
    """側欄底部的語系切換用 window.LanguageSwitcher；以前子頁沒有，發文時切不了語言"""
    src = (ROOT / "web/js/site-sidebar.js").read_text(encoding="utf-8")
    assert "new window.LanguageSwitcher(" in _fn(src, "renderFooter")
    assert _pages_missing("LanguageSwitcher.js") == []


def test_sidebar_pages_expose_bottom_safe_area():
    """側欄底部用 env(safe-area-inset-bottom) 讓出系統導覽列；少了 viewport-fit=cover
    這個值在 Android 延伸到系統列底下的瀏覽器裡是 0，使用者卡會被蓋住"""
    missing = [
        html.relative_to(ROOT).as_posix()
        for html, text in _sidebar_pages()
        if "viewport-fit=cover" not in text
    ]
    assert missing == []


def test_mobile_drawer_not_closed_on_every_resize():
    """手機網址列收合、鍵盤彈出都會觸發 resize；每次都關抽屜 → 一打開就自己縮回去"""
    src = (ROOT / "web/js/site-sidebar.js").read_text(encoding="utf-8")
    body = _fn(src, "updateLayout")
    assert "if (lastDesktop !== false) closeDrawer();" in body


def test_burger_shares_the_nav_row():
    """漢堡插在 nav 最前面；nav 不是 flex 時漢堡自己佔一行，手機頂欄變兩行高"""
    src = (ROOT / "web/js/site-sidebar.js").read_text(encoding="utf-8")
    body = _fn(src, "buildSkeleton")
    assert "nav.style.display = 'flex'" in body


def test_create_submit_row_wraps_instead_of_overlapping():
    """英文「Post for Free (PREMIUM)」太寬時，按鈕要換行，不能壓住每日上限"""
    html = (ROOT / "web/forum/create.html").read_text(encoding="utf-8")
    assert 'id="create-submit-row" class="flex flex-wrap' in html
    assert "#create-submit-row > button" in html


def test_create_category_chips_cover_every_backend_category():
    """分類改成 chip（radio）；少一顆就發不出那個分類，JS 也要改讀勾選的 radio"""
    from api.routers.forum.posts import VALID_CATEGORIES

    html = (ROOT / "web/forum/create.html").read_text(encoding="utf-8")
    values = re.findall(r'<input type="radio" name="category" value="([a-z]+)"', html)
    assert sorted(values) == sorted(VALID_CATEGORIES)
    assert 'id="input-category"' not in html
    js = (ROOT / "web/js/forum-app.js").read_text(encoding="utf-8")
    assert 'input[name="category"]:checked' in js
    assert "getElementById('input-category')" not in js


def test_create_preview_uses_the_post_page_renderer():
    """預覽要跟文章頁同一套：markdown-it＋DOMPurify＋SecurityUtils.renderMarkdownSafely"""
    html = (ROOT / "web/forum/create.html").read_text(encoding="utf-8")
    post = (ROOT / "web/forum/post.html").read_text(encoding="utf-8")
    for lib in ("markdown-it@14.1.0", "dompurify@3.0.6"):
        tag = re.search(rf'<script src="[^"]*{re.escape(lib)}[^"]*"[^>]*>', post).group(
            0
        )
        assert tag in html, lib  # 同一個網址與 SRI
    assert 'id="content-preview"' in html
    assert _imports(ROOT / "web/js/pages/forum-create.js", "security-utils.js")
    js = (ROOT / "web/js/forum-app.js").read_text(encoding="utf-8")
    assert "SecurityUtils.renderMarkdownSafely(contentEl.value)" in js
