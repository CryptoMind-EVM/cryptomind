"""全站 i18n 完整性稽查——所有被引用的 key 必須在四語（zh-TW/zh-CN/en/ru）
全部存在，佔位符（{{var}}）必須一致。

2026-08-24 DANNY 回報「帳本不支援多語言」後建立。掃描：
- ``web/**/*.html`` 的 ``data-i18n="key"``（i18n.js updatePageContent 消費）
- ``web/js/**/*.js`` 的 ``t('key')`` / ``.t('key')`` 字面值
- 已知前綴 helper：chat-analysis.js 的 ``t(k)`` → ``chat.scamEvidence.*``、
  skill-manager.js 的 ``_t(k)`` → ``settings.skills.*``

不掃動態組合 key（``journal.cat_ + id`` 之類）——由各元件行為測試覆蓋。
切換語言的重渲染責任：spa.js 的 languageChanged handler + 各分頁
SELF_HANDLED 監聽（tab-journal 已補，見 TestJournalLanguageRerender）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
LANGS = ("zh-TW", "zh-CN", "en", "ru")

# 檔名 → 該檔案 t()/_t() 字面 key 的命名空間前綴（各檔 helper 定義）
PREFIXED_HELPERS = {
    "chat-analysis.js": "chat.scamEvidence",
    "skill-manager.js": "settings.skills",
    "trustScoreManager.js": "trust",
    "walletMonitorTab.js": "walletMonitor",
    "admin-stats.js": "admin",
    "admin-visitors.js": "admin",
    "admin-audit.js": "admin",
    "admin-wallet-monitor.js": "admin",
    "memory-manager.js": "settings.memory",
    "chat-preset.js": "chatPreset",
    "studio.js": "studio",
}

_JS_T_RE = re.compile(r"""\bt\(\s*['"]([A-Za-z0-9_][A-Za-z0-9_.\-]*)['"]\s*[,)]""")
_JS_UNDERSCORE_T_RE = re.compile(
    r"""_t\(\s*['"]([A-Za-z0-9_][A-Za-z0-9_.\-]*)['"]\s*[,)]"""
)
_HTML_RE = re.compile(r'data-i18n="([^"]+)"')
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _load_locales():
    return {
        lang: json.loads((WEB / f"js/i18n/{lang}.json").read_text(encoding="utf-8"))
        for lang in LANGS
    }


def _resolve(d, key):
    cur = d
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur if isinstance(cur, str) else None


def _collect_keys():
    """回傳 [(key, 來源檔案描述)] —— HTML data-i18n + JS t() 字面值。

    有前綴 helper 的檔案：``_t('x')`` 一律加前綴（helper 必加）；
    ``t('a.b.c')`` 帶點 = 完整 key 照用；``t('x')`` 無點 = 區域 helper key，
    加前綴（同檔的 window.I18n.t('full.key') 不受影響）。
    """
    keys = []
    for f in WEB.rglob("*.html"):
        for m in _HTML_RE.finditer(f.read_text(encoding="utf-8")):
            keys.append((m.group(1), str(f.relative_to(ROOT))))
    for f in (WEB / "js").rglob("*.js"):
        if "i18n" in f.parts or f.name == "i18n.js":
            continue  # 語言檔自身與 i18n runtime 不掃
        src = f.read_text(encoding="utf-8")
        prefix = PREFIXED_HELPERS.get(f.name)
        for m in _JS_UNDERSCORE_T_RE.finditer(src):
            keys.append(
                (
                    f"{prefix}.{m.group(1)}" if prefix else m.group(1),
                    str(f.relative_to(ROOT)),
                )
            )
        for m in _JS_T_RE.finditer(src):
            key = m.group(1)
            if prefix and "." not in key:
                key = f"{prefix}.{key}"
            keys.append((key, str(f.relative_to(ROOT))))
    return keys


def _walk(d, path=""):
    for k, v in d.items():
        if isinstance(v, dict):
            yield from _walk(v, f"{path}.{k}" if path else k)
        else:
            yield (f"{path}.{k}" if path else k), v


class TestI18nCompleteness:
    def test_all_referenced_keys_exist_in_all_locales(self):
        locales = _load_locales()
        keys = _collect_keys()
        assert len(keys) > 800, "掃描規模異常——regex 可能失效"

        problems = []
        seen = set()
        for key, src in keys:
            if key in seen:
                continue
            seen.add(key)
            missing = [lang for lang, d in locales.items() if _resolve(d, key) is None]
            if missing:
                problems.append(f"{key}（缺 {','.join(missing)}）← {src}")
        assert not problems, (
            f"{len(problems)} 個 key 未在四語齊備（切換語言會顯示 key 或舊語言）：\n"
            + "\n".join(problems[:30])
        )

    def test_placeholders_consistent_across_locales(self):
        locales = _load_locales()
        zh = dict(_walk(locales["zh-TW"]))
        problems = []
        for key, val in zh.items():
            ph = sorted(_PLACEHOLDER_RE.findall(val))
            if not ph:
                continue
            for lang in LANGS[1:]:
                other = dict(_walk(locales[lang])).get(key)
                if other is None:
                    problems.append(f"{key} 缺 {lang}")
                elif sorted(_PLACEHOLDER_RE.findall(other)) != ph:
                    problems.append(
                        f"{key}: zh-TW={ph} {lang}={sorted(_PLACEHOLDER_RE.findall(other))}"
                    )
        assert not problems, "佔位符不一致（插值會顯示原始 {{var}}）：\n" + "\n".join(
            problems[:20]
        )

    def test_four_locale_files_exist_and_parse(self):
        for lang in LANGS:
            data = json.loads(
                (WEB / f"js/i18n/{lang}.json").read_text(encoding="utf-8")
            )
            assert isinstance(data, dict) and len(data) > 10


class TestJournalLanguageRerender:
    """帳本切換語言重渲染（2026-08-24 修復的回歸鎖）。"""

    def test_journal_tab_listens_language_changed(self):
        src = (WEB / "js/components/tab-journal.js").read_text(encoding="utf-8")
        assert "languageChanged" in src, (
            "tab-journal 必須監聽 languageChanged——spa.js 的 SELF_HANDLED "
            "把它排除在全域重渲染外，沒有自行監聽 = 切語言不會更新"
        )
        # 防監聽器洩漏：init 可重入，必須有綁定旗標
        assert "_langBound" in src

    def test_journal_still_listed_self_handled(self):
        """spa.js 的 SELF_HANDLED 集合仍含 journal（契約：自行處理）。"""
        src = (WEB / "js/spa.js").read_text(encoding="utf-8")
        m = re.search(r"SELF_HANDLED = new Set\(\[(.*?)\]", src, re.DOTALL)
        assert m and "'journal'" in m.group(1)


class TestJournalPositionLabels:
    """持倉卡的數量單位與市場標籤（2026-09-24 拍商店截圖時發現英文介面露出
    「0.1張」「12股」與原始 key「tw_stock」）。"""

    _CJK_RE = re.compile(r"[぀-ヿ㐀-鿿]")

    def test_en_units_have_no_cjk(self):
        units = _load_locales()["en"]["journal"]["units"]
        bad = {k: v for k, v in units.items() if self._CJK_RE.search(v)}
        assert not bad, f"en 的 journal.units 不能是中日文：{bad}"

    def test_position_card_uses_market_label_not_raw_key(self):
        src = (WEB / "js/components/tab-journal.js").read_text(encoding="utf-8")
        assert "${this._esc(p.market)}" not in src, (
            "持倉卡直接印 p.market 會露出 tw_stock/us_stock 這種內部 key——"
            "要走 _marketLabel（journal.scorecard.markets.*）"
        )
        assert "this._marketLabel(p.market)" in src


class TestLocaleScriptSanity:
    """四語的值要用對文字系統（2026-09-25 盤查）。

    16 個 key 整排錯位一格：en 放繁中、zh-TW 放簡中、zh-CN 放俄文、ru 放英文——
    英文介面的技能確認卡寫「名稱：／說明：」，俄文使用者看到英文。每語各自看都
    「有值」，完整性測試抓不到；只有看文字系統才看得出來。
    """

    _CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿＀-￯]")
    _CYR = re.compile(r"[Ѐ-ӿ]")
    # 刻意混用：語言切換鈕顯示的是「要切過去的那個語言」的自稱（en 介面顯示「中文」）
    _ALLOW = {("en", "safety.languageToggle")}

    def test_values_use_the_expected_script(self):
        locales = _load_locales()
        rules = {
            "en": (self._CJK, self._CYR),
            "zh-TW": (self._CYR,),
            "zh-CN": (self._CYR,),
            "ru": (self._CJK,),
        }
        bad = []
        for lang, patterns in rules.items():
            for key, val in _walk(locales[lang]):
                if (lang, key) in self._ALLOW or not isinstance(val, str):
                    continue
                if any(p.search(val) for p in patterns):
                    bad.append(f"{lang} {key} = {val[:40]!r}")
        assert not bad, (
            f"{len(bad)} 個值的文字系統不對（多半是整排錯位一格，或譯文貼錯檔）：\n"
            + "\n".join(bad[:30])
        )

    def test_allow_list_is_still_needed(self):
        """允許清單過期就拿掉，免得變成日後錯位的藏身處。"""
        locales = _load_locales()
        for lang, key in self._ALLOW:
            val = _resolve(locales[lang], key)
            assert val and (self._CJK.search(val) or self._CYR.search(val)), (
                f"{lang} {key} 已不含異語文字，從 _ALLOW 拿掉"
            )
