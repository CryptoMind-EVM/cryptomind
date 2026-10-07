"""AI Studio 切語系仍顯示中文的回歸守門（2026-09-08 DANNY 回報）。

根因不是硬編字串——`ai-studio.js` 的 49 處中文全部已有 i18n key，130 個
key 在 zh-TW/zh-CN/en/ru 四語系也都齊、en.json 的 aiStudio.* 值沒有殘留
中文。真正的原因是 `AIStudioTab.init()` 的一次性 `_initialized` 守衛：

  spa.js 的 languageChanged 靠「重跑 executeTabSwitch＝切走再切回」讓
  JS 動態產生（沒有 data-i18n）的內容套用新語言，註解還寫明「分頁 init
  本就需可重入」。但 AIStudioTab.init() 第一行就 `if (this._initialized)
  return;`，於是 `_t()` 拼出來的字串永遠停在初次渲染的語言，只有靜態
  data-i18n 節點被 i18n.js 的 updatePageContent 換掉——「多處是中文、
  但不是全部」正是這個組合。

修法：AIStudioTab 自行監聽 languageChanged，以快取重畫（不重打 API），
並登記進 spa.js 的 SELF_HANDLED 避免 spa.js 再空跑一次分頁切換。

同 test_ai_studio_skills_wiring.py 模式——前端無單元測試框架，用原始碼
接線斷言取代。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
STUDIO_JS = REPO / "web" / "js" / "ai-studio.js"
TAB_JS = REPO / "web" / "js" / "components" / "tab-ai-studio.js"
SPA_JS = REPO / "web" / "js" / "spa.js"
LOCALES = ("zh-TW", "zh-CN", "en", "ru")


def _js() -> str:
    return STUDIO_JS.read_text(encoding="utf-8")


def test_ai_studio_listens_for_language_change():
    """沒有這個監聽器，切語系時動態字串不會重畫。"""
    js = _js()
    assert "addEventListener('languageChanged'" in js, (
        "ai-studio.js 必須自行監聽 languageChanged——"
        "spa.js 的重跑會被 _initialized 守衛吃掉。"
    )
    assert "_rerenderForLanguage" in js


def test_language_rerender_uses_cache_not_refetch():
    """語言換了資料沒換：重畫不該再打一輪 API（refresh 會 await load*）。"""
    js = _js()
    m = re.search(r"_rerenderForLanguage\(\)\s*\{(.*?)\n    \},", js, re.S)
    assert m, "找不到 _rerenderForLanguage 方法本體"
    body = m.group(1)
    for renderer in ("renderOverview()", "renderProfiles()", "renderPresets()"):
        assert renderer in body, f"_rerenderForLanguage 少了 {renderer}"
    assert "this.refresh()" not in body, (
        "_rerenderForLanguage 不該呼叫 refresh()——那會重打 loadProfiles／"
        "loadPresets／loadConfigs 三輪 API，語言切換不需要重抓資料。"
    )
    assert "loadProfiles" not in body and "loadPresets" not in body


def test_init_registers_listener_exactly_once():
    """監聽器必須在 _initialized 守衛之後註冊，否則每次進分頁都多一個。"""
    js = _js()
    guard = js.index("if (this._initialized) return;")
    listener = js.index("addEventListener('languageChanged'")
    assert guard < listener, (
        "languageChanged 監聽器註冊在 _initialized 守衛之前＝每次 "
        "executeTabSwitch('ai-studio') 都會再掛一個，造成監聽器洩漏。"
    )


def test_spa_marks_ai_studio_self_handled():
    """已自行監聽的分頁要進 SELF_HANDLED，spa.js 才不會再空跑一次切換。"""
    spa = SPA_JS.read_text(encoding="utf-8")
    m = re.search(r"SELF_HANDLED = new Set\(\[(.*?)\]\)", spa, re.S)
    assert m, "spa.js 找不到 SELF_HANDLED"
    assert "'ai-studio'" in m.group(1)


def test_ai_studio_i18n_keys_complete_across_locales():
    """守住『key 齊全』這個前提——它一旦破，root cause 分析就不再成立。"""
    used: set[str] = set()
    for path in (STUDIO_JS, TAB_JS):
        src = path.read_text(encoding="utf-8")
        used |= set(re.findall(r"_t\(\s*'([\w.]+)'", src))
        used |= set(re.findall(r'data-i18n="([\w.]+)"', src))
    assert len(used) > 100, f"只抓到 {len(used)} 個 key，正規式可能失效了"

    def flatten(obj: dict, prefix: str = "") -> dict:
        out: dict[str, object] = {}
        for key, value in obj.items():
            full = f"{prefix}{key}"
            if isinstance(value, dict):
                out.update(flatten(value, full + "."))
            else:
                out[full] = value
        return out

    for lang in LOCALES:
        table = flatten(json.loads((REPO / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8")))
        missing = sorted(k for k in used if k not in table)
        assert not missing, f"{lang}.json 缺 {len(missing)} 個 AI Studio key：{missing[:10]}"


def test_english_locale_has_no_chinese_in_ai_studio_values():
    """key 在、值卻是中文的話，切英文照樣看到中文。"""
    en = json.loads((REPO / "web/js/i18n/en.json").read_text(encoding="utf-8"))
    cjk = re.compile(r"[一-鿿]")
    offenders = [
        f"aiStudio.{k}"
        for k, v in en.get("aiStudio", {}).items()
        if isinstance(v, str) and cjk.search(v)
    ]
    assert not offenders, f"en.json 的 aiStudio.* 殘留中文：{offenders}"
