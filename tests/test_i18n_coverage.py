from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
I18N_DIR = WEB / "js" / "i18n"


def flatten(data: dict, prefix: str = "") -> dict[str, str]:
    items: dict[str, str] = {}
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            items.update(flatten(value, full_key))
        else:
            items[full_key] = value
    return items


def load_translations(name: str) -> dict[str, str]:
    payload = json.loads((I18N_DIR / name).read_text(encoding="utf-8"))
    return flatten(payload)


def collect_i18n_keys() -> set[str]:
    keys: set[str] = set()
    html_pattern = re.compile(r'data-i18n="([^"]+)"')
    js_pattern = re.compile(r"""I18n\.t\(\s*['"]([^'"]+)['"]""")

    for path in list(WEB.rglob("*.html")) + list((WEB / "js").rglob("*.js")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        keys.update(html_pattern.findall(text))
        keys.update(js_pattern.findall(text))

    # Drop dynamic-concatenation prefixes: when JS builds a key at runtime via
    # I18n.t('forexBanks.' + symbol, { defaultValue: ... }), the regex captures
    # only the trailing-dot string literal ('forexBanks.'). A real i18n key
    # never ends with a dot, so these prefixes are false positives, not missing
    # translations. The runtime defaultValue fallback handles unknown keys.
    return {k for k in keys if not k.endswith(".")}


def test_translation_files_parse_and_match():
    zh = load_translations("zh-TW.json")
    en = load_translations("en.json")
    assert set(zh) == set(en)


def test_four_language_files_have_identical_key_sets():
    """四語 key 結構必須完全一致（治理規則，DANNY 2026-08-20）。

    不一致代表：某 key 翻譯漏了一兩語、或文本寫死在前端根本沒進 i18n
    體系。任何新增 key 必須四語同步（zh-TW / zh-CN / en / ru）。
    """
    base = load_translations("zh-TW.json")
    for name in ("zh-CN.json", "en.json", "ru.json"):
        other = load_translations(name)
        missing = set(base) - set(other)
        extra = set(other) - set(base)
        assert not missing and not extra, (
            f"{name} 與 zh-TW key 不一致：缺 {sorted(missing)[:5]}、"
            f"多 {sorted(extra)[:5]}——新 key 必須四語同步新增"
        )


def test_all_referenced_i18n_keys_exist():
    zh = load_translations("zh-TW.json")
    en = load_translations("en.json")
    used = collect_i18n_keys()
    assert used - set(zh) == set()
    assert used - set(en) == set()


def test_scam_tracker_pages_load_i18n_assets():
    # 列表頁 2026-09-27 改成 SPA 分頁 #scamcheck（舊網址由 api_server 導過去）
    pages = [
        WEB / "scam-tracker" / "detail.html",
        WEB / "scam-tracker" / "submit.html",
    ]
    for page in pages:
        text = page.read_text(encoding="utf-8")
        # PR #498 起，共用層（含 i18n.js）由 classic-compat-app module 載入，
        # 頁面不再直接引用 /static/js/i18n.js（classic 引用 module 檔會語法錯誤）
        assert "/static/js/classic-compat-app.js" in text
        assert "i18next" in text  # i18next CDN（classic-compat 的 i18n 依賴）
        assert "/static/scam-tracker/js/scam-tracker-i18n.js" in text
        # 四語切換器掛載點（以前是中文／EN 兩態鈕 lang-toggle-label）
        assert 'id="scam-lang-switcher"' in text


# ── 2026-09-11 DANNY 全站稽核擴充 ──────────────────────────────────
# （本地 _t helper 前綴、動態前綴、data-i18n 洗掉模式、squeeze 防回潮）

LOCALES = ("en", "zh-TW", "zh-CN", "ru")
JS_FILES = sorted(set((WEB / "js").rglob("*.js")))


def _js_blob() -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in JS_FILES)


def test_local_t_helper_prefixed_keys_exist():
    """本地 `_t('bare')` helper（`I18n.t('ns.' + key)` 形態）補前綴後
    四語齊備——walletMonitorTab 的 52 個 bare key 即此形態。"""
    translations = {name: load_translations(f"{name}.json") for name in LOCALES}
    missing = []
    for path in JS_FILES:
        s = path.read_text(encoding="utf-8", errors="ignore")
        ns_m = re.search(r"""I18n\??\.t\(\s*['"]([a-z0-9_.]+?)\.\s*['"]\s*\+""", s)
        prefix = ns_m.group(1) + "." if ns_m else ""
        if not prefix:
            continue
        for m in re.finditer(r"""(?<![\w.])_t\(\s*['"]([a-zA-Z0-9_.]+)['"]""", s):
            key = m.group(1)
            if key.endswith(".") or "." in key:
                continue
            full = prefix + key
            absent = [lang for lang in LOCALES if full not in translations[lang]]
            if absent:
                missing.append((path.name, full, absent))
    assert not missing, f"本地 _t 前綴 key 缺失：{missing[:10]}"


def test_dynamic_key_prefix_namespaces_resolve():
    """`` ns.${x} ``／`` 'ns.' + x `` 的前綴命名空間必須真有 key——
    前綴寫錯＝整段 UI 吃 fallback 或顯示 key 本身。"""
    en = load_translations("en.json")
    blob = _js_blob()
    bad = set()
    for m in re.finditer(r"""\.t\(\s*[`'"]([a-z0-9_]+)\.""", blob):
        ns = m.group(1) + "."
        if not any(k.startswith(ns) for k in en):
            bad.add(ns)
    assert not bad, f"動態 key 前綴命名空間不存在：{sorted(bad)}"


def test_no_js_write_wipes_data_i18n_elements():
    """JS 對帶 data-i18n 的元素寫 textContent/innerHTML 而未拆 data-i18n
    ＝語言切換時動態值被洗回預設文案（2026-09-10 論壇狀態卡實例）。"""
    html_files = (
        list(WEB.glob("*.html"))
        + list((WEB / "forum").glob("*.html"))
        + list((WEB / "scam-tracker").glob("*.html"))
    )
    flagged = []
    for f in html_files:
        s = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r'<[^>]*id="([^"]+)"[^>]*data-i18n="([^"]+)"[^>]*>', s):
            flagged.append((f, m.group(1)))
    blob = _js_blob()
    risks = []
    for f, eid in flagged:
        for m in re.finditer(
            r"""getElementById\(['"]""" + re.escape(eid) + r"""['"]\)\.?(textContent|innerHTML)\s*=""",
            blob,
        ):
            ctx = blob[m.start() : m.start() + 300]
            if "removeAttribute" not in ctx and "data-i18n" not in ctx:
                risks.append((f.name, eid, m.group(1)))
    assert not risks, f"JS 寫入會洗掉 data-i18n 動態值：{risks[:10]}"


# 寫入是暫態、結束後寫回同一個 key 的譯文（重掃蓋回去也是對的字），不算風險。
_TRANSIENT_I18N_WRITES = {
    "btn-save-tool-prefs",  # 儲存中 → 完成後寫回 toolSettings.confirmSave
    "submit-reply",  # 送出中 → finally 寫回 forum.submitReplyBtn
}


def test_no_js_write_via_variable_wipes_data_i18n_elements():
    """上一條只抓 `getElementById('x').textContent =` 直寫；實際更常見的是
    先存變數再寫（`const s = getElementById('x'); … s.textContent = …`）。
    2026-09-25 盤查漏網的：圖表 LIVE／OFF、帳本月份鈕、確認框標題／內文、
    論壇錢包狀態——語言一切換（updatePageContent 全頁重掃）就被洗回 HTML 預設文案。
    寫入時要同步改 `dataset.i18n`，或 `removeAttribute('data-i18n')`。"""
    html_files = (
        list(WEB.glob("*.html"))
        + list((WEB / "forum").glob("*.html"))
        + list((WEB / "scam-tracker").glob("*.html"))
    )
    ids = set()
    for f in html_files:
        for m in re.finditer(r"<[^>]*\bid=\"([^\"]+)\"[^>]*>", f.read_text(encoding="utf-8")):
            tag = m.group(0)
            # data-i18n-attr 只改屬性，寫 textContent 不會被重掃蓋掉
            if 'data-i18n="' in tag and "data-i18n-attr" not in tag:
                ids.add(m.group(1))
    risks = []
    for path in JS_FILES:
        src = path.read_text(encoding="utf-8", errors="ignore")
        for eid in ids - _TRANSIENT_I18N_WRITES:
            decl = re.compile(
                r"""(?:const|let|var)\s+(\w+)\s*=\s*document\.getElementById\(['"]"""
                + re.escape(eid)
                + r"""['"]\)"""
            )
            for m in decl.finditer(src):
                var = re.escape(m.group(1))
                scope = src[m.end() : m.end() + 3000]
                if re.search(r"\b" + var + r"\.(textContent|innerHTML|innerText)\s*=", scope) and not re.search(
                    var + r"""\.(dataset\.i18n|removeAttribute\(['"]data-i18n)""", scope
                ):
                    risks.append((path.name, eid))
    assert not risks, f"JS 經變數寫入會被語系重掃洗掉：{sorted(set(risks))[:10]}"


def test_paid_section_squeeze_guards():
    """直式瀑布防回潮（2026-09-10~11 Settings 個人卡＋付款面板兩例）：
    break-all 長文所在列必須 flex-wrap＋min-w-0，永不塌縮成一字直排。"""
    premium = (WEB / "js" / "premium.js").read_text(encoding="utf-8")
    assert "flex flex-wrap items-center gap-2 bg-background" in premium
    assert "min-w-0 select-all" in premium
    tab_settings = (WEB / "js" / "components" / "tab-settings.js").read_text(encoding="utf-8")
    assert '<div class="flex flex-wrap items-center gap-3">' in tab_settings
    assert "flex flex-wrap items-center gap-3 mb-6" in tab_settings
