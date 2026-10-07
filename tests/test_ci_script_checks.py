"""CI 那四個 script 檢查的 pytest 包裝。

2026-09-06 的事故說明為什麼需要這層：bump `styles.css?v=` 時我用的是
`web/*.html`，沒有遞迴到子目錄，11 個子頁面（forum/ governance/
scam-tracker/）停在舊版本——回訪使用者會拿到快取的舊 CSS，看不到新樣式。

`check_asset_versions.py` 抓得到，但它**只在 CI 跑**，而 CI 因帳單全掛。
當天的全量 4581 passed 完全沒反應，因為這些檢查是 script 不是 pytest。
包成測試之後，本機 pytest 才是完整的閘門。

執行成本：四個加起來 0.26 秒。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]

#: 硬編中文的現況上限（棘輪）。這是 i18n 舊債不是新缺陷，所以不要求歸零，
#: 但**不准變更糟**——CI 那邊是 continue-on-error，等於完全沒有防線。
#: 真的清掉一批之後把這個數字調低，讓它守在新的水位。
#: 960 → 959（2026-09-08 Preset 編輯 UI）：新增的 _t 呼叫點改用英文
#: fallback（key 有四語系測試守護），順手把舊 createFailed 那行也換掉。
#: 959 → 905（2026-09-08 掃描器修誤判）：多行 HTML 註解的續行、自訂 *Debug()
#: 記錄器不再被計數。**這 54 行不是清掉的字串，是本來就不該被數進來的。**
#: 905 → 845（2026-09-08 市場資料行級排除）：代號→公司／指數／貨幣對／央行
#: 名稱的對照表，以及當成物件值的意圖比對正規式（`re: /外匯|匯率/i`）。同樣
#: 不是清掉字串。注意排除條件是「中文落在名稱本身」——usstock.js 的
#: `{ symbol:'AAPL', name:'Apple', group:'科技' }` 中文在 group，那是類別標籤
#: （科技／金融／消費／醫療／能源），仍然計數，因為它真的該翻譯。
#: 695 → 291（2026-09-24 掃描器認得 legal 頁四語屬性系統）：695 之後 develop
#: 一路紅在 706，多出的 11 行全在 web/legal/——268200d 服務條款 9.9/9.10（+8）、
#: 4732d49 語言鈕改 <option> 直選（+3，「繁體中文／简体中文」是語言自稱）。
#: 那些行本來就帶齊 data-zh／data-zh-cn／data-en／data-ru（四語齊全由
#: test_legal_i18n_parity 看守；DANNY 2026-08-20 定案法律長文不進主 i18n JSON），
#: 等於每次改法律頁都會踩這個棘輪。少掉的 415 行＝legal 頁已有四語譯文的 412 行
#: ＋LanguageSwitcher.js 的語言自稱 3 行。**不是清掉的字串，是本來就有譯文或不該翻。**
#: 元素外面沒譯文的中文照樣計數（legal 還剩 6 行：生效日期的「2026年9月14日」、
#: 社群守則的「1 分／3 分」），規則邊界由 test_scanner_skips_only_translated_chinese 守。
#: 2026-09-24 手機底部導覽列拔除，少 1 行 → 290。
#: （develop 其後降到 285）2026-09-25 legal.js 兩份重複的四語標題對照併成一份，少 3 行 → 282。
#: 2026-09-29 私訊 WS 重寫、舊 i18n log 字串清掉，少 1 行 → 281。
HARDCODED_CHINESE_BASELINE = 281


def _run(script: str) -> subprocess.CompletedProcess:
    # encoding 必須明講：這些腳本印多語系文字，父行程在 Windows 上會用 cp950
    # 解碼子行程的 UTF-8 輸出並炸 UnicodeDecodeError（stdout 變 None）。
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
    )


@pytest.mark.parametrize(
    "script",
    ["check_static_refs.py", "check_asset_versions.py", "check_i18n_keys.py"],
)
def test_hard_gate_scripts_pass(script: str):
    """這三個是真閘門，失敗代表線上會壞。"""
    r = _run(script)
    assert r.returncode == 0, f"{script} 失敗：\n{r.stdout[-2000:]}\n{r.stderr[-1000:]}"


def test_hardcoded_chinese_does_not_regress():
    """硬編中文只准變少不准變多。"""
    r = _run("scan_hardcoded_chinese.py")
    # 路徑分隔符在 Windows 上是反斜線（web\js\ai-studio.js:216:）——字元類少了
    # 反斜線的話這裡一行都比對不到，hits 恆為 0，然後測試會反過來叫你把基準
    # 調成 0，那等於親手拆掉 Linux CI 上唯一有效的防線。
    hits = len(re.findall(r"^[\w./\\-]+:\d+:", r.stdout, re.M))
    assert hits > 0, (
        "一行都沒比對到——多半是掃描器中斷或輸出格式變了，不是真的清零。"
        f"stdout 前 300 字：\n{r.stdout[:300]}"
    )
    assert hits <= HARDCODED_CHINESE_BASELINE, (
        f"硬編中文從 {HARDCODED_CHINESE_BASELINE} 增加到 {hits} 行。"
        f"新加的字串請走 i18n。"
    )
    if hits < HARDCODED_CHINESE_BASELINE:
        pytest.fail(
            f"硬編中文已降到 {hits} 行（基準 {HARDCODED_CHINESE_BASELINE}）——"
            f"請把 HARDCODED_CHINESE_BASELINE 調成 {hits} 鎖住成果，否則會回彈。"
        )


def test_scanner_skips_only_translated_chinese():
    """legal 頁四語屬性的排除規則不能變成大赦：有譯文的才剝，元素外的中文照算。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "scan_hardcoded_chinese", ROOT / "scripts" / "scan_hardcoded_chinese.py"
    )
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)
    excluded = scan.should_exclude_line

    t = 'data-zh="不得" data-zh-cn="不得" data-en="NOT" data-ru="НЕ"'
    assert excluded(f"<li><span {t}>不得</span></li>")
    # 譯文元素裡夾 <strong>：非 zh-TW 時整個 textContent 被譯文取代
    assert excluded(f'<p><span {t}>您同意<strong class="x">不得</strong>：</span></p>')
    assert excluded(f"<strong {t}>不得</strong>")
    assert excluded('<option value="zh-TW">繁體中文</option>')

    # 以下都沒有譯文，必須繼續計數
    assert not excluded(
        f"<p><strong><span {t}>生效日期</span>：</strong>2026年9月14日</p>"
    )
    assert not excluded(
        f'<li><span class="severity-mild">1 分</span> - <span {t}>x</span></li>'
    )
    assert not excluded('<span data-zh="不得">不得</span>')  # 只有 data-zh 不算有譯文
    assert not excluded("<strong>不得</strong>")
    assert not excluded("<p>本服務提供繁體中文介面</p>")  # 語言自稱之外的中文照算


def test_css_version_is_uniform_across_every_page():
    """子目錄的頁面最容易被漏掉——`web/*.html` 這種 glob 不會遞迴。"""
    versions = set()
    for html in ROOT.glob("web/**/*.html"):
        versions |= set(
            re.findall(r"styles\.css\?v=(\d+)", html.read_text(encoding="utf-8"))
        )
    assert len(versions) <= 1, (
        f"styles.css 版本不一致：{sorted(versions)}。"
        f"回訪使用者會拿到快取的舊 CSS。bump 時記得用 `find web -name '*.html'`，"
        f"不是 `web/*.html`。"
    )
