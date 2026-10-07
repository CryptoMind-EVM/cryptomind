"""各市場分頁的 AI 分析摘要卡（web/js/app.js renderAIAnalysisSection）用到的字串都要有翻譯。

2026-10-01：key 是用 `分頁 namespace + '.aiAnalysisSummary'` 拼出來的，四個從來沒加進詞表，
畫面直接秀出原始 key（uppercase 後變 TWSTOCK.AIANALYSISSUMMARY），又長又不能斷行，把「重新分析」擠成直排；
九個市場分頁全中。拼出來的 key 靜態檢查抓不到，這裡把每個 namespace 都展開來對。
"""

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LANGS = ("zh-TW", "zh-CN", "en", "ru")
# renderAIAnalysisSection 的呼叫端：tabName 去掉 Tab 後轉小寫＝namespace
MARKET_NAMESPACES = (
    "astock",
    "commodity",
    "forex",
    "hkstock",
    "instock",
    "jpstock",
    "krstock",
    "twstock",
    "usstock",
)


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def _render_fn_source() -> str:
    src = _strip_comments((REPO / "web/js/app.js").read_text(encoding="utf-8"))
    start = src.index("function renderAIAnalysisSection(")
    end = src.index("window.renderAIAnalysisSection = renderAIAnalysisSection")
    return src[start:end]


def _lookup(table: dict, key: str):
    cur = table
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur if isinstance(cur, str) else None


@pytest.fixture(scope="module")
def tables():
    return {
        lang: json.loads((REPO / f"web/js/i18n/{lang}.json").read_text(encoding="utf-8"))
        for lang in LANGS
    }


def test_namespaced_keys_exist_in_every_market(tables):
    fn = _render_fn_source()
    keys = sorted(set(re.findall(r"i18nNs \+ '\.(\w+)'", fn)))
    assert keys, "應該找得到 namespace 拼出來的 key"
    missing = [
        f"{lang}:{ns}.{k}"
        for lang in LANGS
        for ns in MARKET_NAMESPACES
        for k in keys
        if _lookup(tables[lang], f"{ns}.{k}") is None
    ]
    assert missing == [], f"這些 key 沒有翻譯，畫面會秀出原始 key：{missing[:20]}"


def test_shared_keys_exist(tables):
    fn = _render_fn_source()
    shared = sorted(set(re.findall(r"_tFn\('(stock\.\w+)'", fn)))
    assert "stock.aiAnalysisSummary" in shared
    missing = [f"{lang}:{k}" for lang in LANGS for k in shared if _lookup(tables[lang], k) is None]
    assert missing == [], missing


def test_header_row_cannot_squeeze_button():
    """標題太長時不能把「重新分析」擠成一字一行：標題可截斷、按鈕不縮不換行"""
    fn = _render_fn_source()
    assert "uppercase tracking-widest min-w-0 truncate" in fn
    assert "shrink-0 whitespace-nowrap" in fn


@pytest.mark.parametrize("tab", ["twstock", "usstock", "hkstock"])
def test_pulse_title_translated_and_not_indented_on_mobile(tab):
    src = _strip_comments((REPO / f"web/js/{tab}.js").read_text(encoding="utf-8"))
    assert "Pulse AI Intelligence Summary" not in src, "標題寫死英文"
    assert "_t('stock.pulseAiSummary')" in src
    assert 'class="ml-11"' not in src, "手機上左縮 44px 會把卡片擠窄：改成 md:ml-11"
