"""前端 a11y／i18n 靜默失效守衛（2026-09-25 盤查後建立）。

這批問題都不噴錯，只會讓某些人默默用不了：
- 螢幕閱讀器念不出 icon-only 按鈕在做什麼；
- 全域 Esc 處理器找的 `.modal-overlay`／`[data-close-modal]` 整站一個都沒有 → Esc 沒關過任何 modal；
- <label> 沒 for、也沒包住控制項 → 點標籤不會聚焦、讀屏不知道欄位名；
- governance 頁沒載 Tailwind，toast 容器的 `fixed` 等 class 全失效 → toast 看不到；
- languageChanged 一次切換派發兩次，監聽者各自去重；
- 俄文市值用「除以 1e8 再接 млрд」→ 多 10 倍。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
INDEX = WEB / "index.html"

_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "track",
    "wbr",
}
_CONTROLS = {"input", "select", "textarea"}


def _component_tab_ids() -> set[str]:
    """有 components/tab-*.js 模板的分頁：index.html 裡它們的靜態內容切進分頁時整塊被蓋掉。"""
    ids = set()
    for f in (WEB / "js" / "components").glob("tab-*.js"):
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(
            r"window\.Components(?:\.([\w]+)|\['([\w-]+)'\])\s*=\s*`", src
        ):
            ids.add((m.group(1) or m.group(2)) + "-tab")
    return ids


class _Scan(HTMLParser):
    """收集：沒有可讀名稱的 icon-only 按鈕、沒 for 也沒包控制項的 label。

    skip_ids 內的子樹不看（被元件模板覆蓋的死標記）。"""

    def __init__(self, skip_ids=frozenset()):
        super().__init__(convert_charrefs=True)
        self.skip_ids = skip_ids
        self.stack = []  # [tag, attrs, line, text_parts, skipped]
        self.unlabeled_buttons = []
        self.labels = []  # (line, for, wraps_control)
        self.ids = set()

    def _skipping(self):
        return any(n[4] for n in self.stack)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        if tag in _CONTROLS:
            for n in reversed(self.stack):
                if n[0] == "label":
                    n[5] = True
                    break
        if tag in _VOID:
            return
        self.stack.append(
            [tag, a, self.getpos()[0], [], a.get("id") in self.skip_ids, False]
        )

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] != tag:
                continue
            node = self.stack[i]
            skipped = self._skipping()
            del self.stack[i:]
            if not skipped:
                a = node[1]
                if tag == "button":
                    text = "".join(node[3]).strip()
                    if not text and not (
                        a.get("aria-label")
                        or a.get("title")
                        or a.get("aria-labelledby")
                    ):
                        self.unlabeled_buttons.append(
                            (node[2], a.get("id") or a.get("data-click"))
                        )
                if tag == "label":
                    self.labels.append((node[2], a.get("for"), node[5]))
            if self.stack:
                self.stack[-1][3].extend(node[3])
            return

    def handle_data(self, data):
        if self.stack:
            self.stack[-1][3].append(data)


def _scan(path: Path, skip_ids=frozenset()) -> _Scan:
    s = _Scan(skip_ids)
    s.feed(path.read_text(encoding="utf-8"))
    return s


# ── icon-only 按鈕要有可讀名稱 ───────────────────────────────────────────────


def test_index_icon_only_buttons_have_accessible_name():
    s = _scan(INDEX, frozenset(_component_tab_ids()))
    assert not s.unlabeled_buttons, (
        "icon-only 按鈕沒有 aria-label／title（讀屏只會念「按鈕」）："
        f'{s.unlabeled_buttons}。用 aria-label＋data-i18n＋data-i18n-attr="aria-label"。'
    )


@pytest.mark.parametrize(
    "path",
    [
        *sorted((WEB / "js" / "components").glob("tab-*.js")),
    ],
    ids=lambda p: p.name,
)
def test_other_icon_only_buttons_have_accessible_name(path):
    s = _scan(path)
    assert not s.unlabeled_buttons, (
        f"{path.name} 的 icon-only 按鈕沒有 aria-label：{s.unlabeled_buttons}"
    )


def test_component_tab_ids_premise():
    """死標記判定的前提：市場分頁確實由模板注入（spa.js → Components.inject 蓋 innerHTML）。"""
    ids = _component_tab_ids()
    assert {"hkstock-tab", "astock-tab", "commodity-tab"} <= ids
    assert "journal-tab" not in ids and "wallet-tab" not in ids  # 骨架內建，不能被跳過


class _ChildTags(HTMLParser):
    """記錄 watch_ids 內的容器底下出現過哪些子標籤。"""

    def __init__(self, watch_ids):
        super().__init__(convert_charrefs=True)
        self.watch_ids = watch_ids
        self.stack = []  # [tag, id]
        self.children = {}  # container id -> [(line, tag)]

    def handle_starttag(self, tag, attrs):
        for t, cid in self.stack:
            if cid in self.watch_ids:
                self.children.setdefault(cid, []).append((self.getpos()[0], tag))
        if tag not in _VOID:
            self.stack.append((tag, dict(attrs).get("id")))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


def test_component_tabs_have_no_static_markup_in_index():
    """模板型分頁在 index.html 只能是空容器。

    Components.inject 第一次切進分頁就 `container.innerHTML = 模板`，而分頁要等
    注入完才拿掉 hidden——容器裡寫死的標記沒有任何路徑看得到。2026-09-25 前
    commodity／forex／港／A／日／印／韓股七個分頁各留著一份舊版骨架（~360 行，
    按鈕指向早就不存在的 backToMarket），改版時只改模板、改錯地方也不會發現。
    """
    ids = frozenset(_component_tab_ids())
    p = _ChildTags(ids)
    p.feed(INDEX.read_text(encoding="utf-8"))
    offenders = {cid: tags[:3] for cid, tags in p.children.items()}
    assert not offenders, (
        f"這些模板型分頁在 index.html 裡還有靜態內容（注入時會被整塊蓋掉）：{offenders}"
    )


# ── <label> 要綁到控制項 ─────────────────────────────────────────────────────


def test_index_labels_are_bound_to_controls():
    s = _scan(INDEX, frozenset(_component_tab_ids()))
    loose = [line for line, for_, wraps in s.labels if not for_ and not wraps]
    assert not loose, f"index.html 這些行的 <label> 沒 for 也沒包住控制項：{loose}"
    dangling = [
        (line, for_) for line, for_, _ in s.labels if for_ and for_ not in s.ids
    ]
    assert not dangling, f"<label for> 指向不存在的 id：{dangling}"


# ── 全域 Esc：modal 根與關閉鈕 ─────────────────────────────────────────────────


def _modal_overlay_roots(html: str):
    """回傳 [(id, 從開始標籤到下一個 modal-overlay 根之前的片段)]。"""
    starts = [
        (m.start(), m.group(1))
        for m in re.finditer(
            r'<div id="([\w-]+)"[^>]*?class="modal-overlay\b', html, re.S
        )
    ]
    out = []
    for i, (pos, rid) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(html)
        out.append((rid, html[pos:end]))
    return out


def test_every_modal_overlay_has_a_close_button_for_esc():
    html = INDEX.read_text(encoding="utf-8")
    roots = _modal_overlay_roots(html)
    assert len(roots) >= 10, "modal-overlay 根太少——app.js 的 Esc 處理器又會變成空轉"
    missing = [rid for rid, chunk in roots if "data-close-modal" not in chunk]
    assert not missing, f"這些 modal 沒有 [data-close-modal]，Esc 關不掉：{missing}"


def test_modals_with_own_esc_handler_are_not_double_handled():
    """content-modal.js／feedback.js 自己處理 Esc；再掛 .modal-overlay 會一次 Esc 關兩次。"""
    html = INDEX.read_text(encoding="utf-8")
    roots = {rid for rid, _ in _modal_overlay_roots(html)}
    assert not roots & {"content-modal", "feedback-modal"}
    assert "login-modal" not in roots  # 登入閘門沒有關閉鈕，不能被 Esc 關掉


def test_esc_handler_clicks_close_button_instead_of_hiding():
    src = (WEB / "js" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"Keyboard: Escape.*?\n\}\);", src, re.S)
    assert m, "找不到 app.js 的全域 Esc 處理器"
    block = m.group(0)
    assert ".modal-overlay:not(.hidden)" in block and "[data-close-modal]" in block
    # 直接 hide 的話 showConfirm 的 promise 永遠不 resolve
    assert "classList.add('hidden')" not in block
    assert "closeSidebar" in block, "手機抽屜也要能用 Esc 關"


def test_confirm_dialog_buttons_keep_close_marker_in_js_templates():
    """showAlert 會重寫按鈕區；重寫出來的按鈕也要帶 data-close-modal。"""
    src = (WEB / "js" / "app.js").read_text(encoding="utf-8")
    assert 'id="confirm-modal-ok" data-close-modal' in src
    assert 'id="confirm-modal-cancel" data-close-modal' in src


# ── 手機抽屜鈕 ────────────────────────────────────────────────────────────────


def test_mobile_drawer_toggle_exposes_state():
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r'<button id="mobile-drawer-toggle"[^>]*>', html, re.S)
    assert m, "手機抽屜鈕要有 id（chat-state.js 同步 aria-expanded 用）"
    tag = m.group(0)
    for attr in (
        'data-click="toggleSidebar"',
        'aria-controls="chat-sidebar"',
        'aria-expanded="false"',
        'data-i18n-attr="aria-label"',
    ):
        assert attr in tag, f"抽屜鈕缺 {attr}"
    src = (WEB / "js" / "chat-state.js").read_text(encoding="utf-8")
    assert "setAttribute('aria-expanded', 'true')" in src
    assert "setAttribute('aria-expanded', 'false')" in src


# ── governance 頁的 toast ─────────────────────────────────────────────────────


def test_governance_toast_container_is_styled_without_tailwind():
    page = (WEB / "governance" / "index.html").read_text(encoding="utf-8")
    assert "tailwind-built.css" not in page, (
        "前提變了：governance 若改載 Tailwind，governance.css 的 toast 補丁可以拿掉"
    )
    assert 'id="toast-container"' in page
    css = (WEB / "governance" / "governance.css").read_text(encoding="utf-8")
    m = re.search(r"#toast-container\s*\{([^}]*)\}", css)
    assert m and "position: fixed" in m.group(1), "toast 容器沒 fixed → 掉在頁尾看不到"
    assert re.search(r"#toast-container\s*>\s*div\s*\{", css), "toast 本體沒樣式"


# ── languageChanged 只派發一次 ────────────────────────────────────────────────


def test_language_changed_dispatched_once_per_switch():
    src = (WEB / "js" / "i18n.js").read_text(encoding="utf-8")
    dispatches = src.count("new CustomEvent('languageChanged'")
    # 一處在 i18n.on('languageChanged') handler（所有切換都經過它），一處是初始化完成
    assert dispatches == 2, (
        f"i18n.js 派發 languageChanged {dispatches} 處（應為 2）——一次切換會發兩次"
    )
    m = re.search(
        r"changeLanguage: async function \(lang\) \{(.*?)\n        \},", src, re.S
    )
    assert m and "dispatchEvent" not in m.group(1), (
        "I18n.changeLanguage 不要再手動 dispatch"
    )


# ── 市值單位 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["instock", "jpstock", "astock", "krstock", "twstock"])
def test_market_cap_uses_intl_compact_not_hand_rolled_units(name):
    src = (WEB / "js" / f"{name}.js").read_text(encoding="utf-8")
    assert "notation: 'compact'" in src
    assert "unitHundredMillion" not in src and "unitTrillion" not in src, (
        "自己除以 1e8 再接「億」的譯文：ru 的譯文是 млрд（1e9），市值會多 10 倍"
    )


# ── styles.css 重複定義 ───────────────────────────────────────────────────────


def test_animate_fade_in_up_defined_once():
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    assert len(re.findall(r"^\.animate-fade-in-up\s*\{", css, re.M)) == 1, (
        "同權重重複定義只有後面那條生效，前面那條是誤導人的死碼"
    )
