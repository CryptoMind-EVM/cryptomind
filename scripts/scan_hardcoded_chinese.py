#!/usr/bin/env python3
"""Scan JS/HTML for hardcoded Chinese text that bypasses the i18n system.

Excludes legitimate Chinese usage:
  - i18n JSON locale files (web/js/i18n/*.json)
  - Comments (// /* */ and HTML <!-- -->), including multi-line ones
  - console.{log,warn,error,info,debug} statements and custom *Debug() loggers
  - Signal matching: .includes('突破'), .indexOf('賣出'), .match(...)
  - Regex literals containing Chinese (including `re: /外匯|匯率/i` object values)
  - Market data maps: ticker → company/index/currency-pair/central-bank name
  - JSDoc tags (@param, @returns)
  - Legal pages' four-language attribute system: elements carrying data-en
    (with data-zh / data-zh-cn / data-ru) are already translated in place
  - Language endonyms in language pickers (繁體中文 / 简体中文)

Flags: any string literal or HTML text content containing Chinese characters
that should be routed through window.I18n.t() or data-i18n attribute.

Exit code 0 = clean, 1 = hardcoded Chinese found (CI gate).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")

# Line-level exclusion patterns. If a line matches ANY of these, it is skipped.
EXCLUDE_PATTERNS = [
    re.compile(r"^\s*//"),  # JS line comment
    re.compile(r"^\s*/\*"),  # JS block comment open
    re.compile(r"^\s*\*"),  # JS block comment body
    re.compile(r"^\s*<!--"),  # HTML comment
    re.compile(r"^\s*\*/"),  # JS block comment close
    re.compile(r"console\.(log|warn|error|info|debug|trace)\s*\("),
    # 自訂除錯記錄器（wcDebug()、debugLog()…）——與 console.* 同性質，不是 UI 字串。
    # 只認名字裡帶 Debug 的：`dialog(` 之類結尾是 log 的函式可能真的面向使用者。
    re.compile(r"\b\w*[Dd]ebug\w*\s*\("),
    re.compile(r"\.includes\s*\(\s*['\"][^'\"]*[\u4e00-\u9fff]"),  # signal match
    re.compile(r"\.indexOf\s*\(\s*['\"][^'\"]*[\u4e00-\u9fff]"),  # signal match
    re.compile(r"\.startsWith\s*\(\s*['\"][^'\"]*[\u4e00-\u9fff]"),
    re.compile(r"\.endsWith\s*\(\s*['\"][^'\"]*[\u4e00-\u9fff]"),
    re.compile(r"\.match\s*\(\s*[/']"),  # regex match
    re.compile(r"\.test\s*\(\s*"),  # regex test
    # 正規式字面值（含中文字元類）。docstring 本來就聲明要排除「Regex patterns
    # containing Chinese ranges」，但上面兩條只認 .match(/ 與 .test(——像
    # chat-analysis.js 的 `{ tab: 'forex', re: /外匯|匯率|.../i }` 這種把正規式
    # 當物件值的意圖比對就漏掉了。那是訊號比對，不是 UI 字串。
    re.compile(r"[=:(,\[]\s*/(?:[^/\n\\]|\\.)*[一-鿿]"),
    # ── 市場資料：代號 → 名稱的對照表 ────────────────────────────────
    # 公司名／指數名／貨幣對／央行名是專有名詞與市場資料，不是可翻譯的 UI 文案
    # ——這是 repo 既有的判定（twstock／hkstock／commodity 已整檔排除）。這裡
    # 改用「行級」樣式而非整檔排除：整檔排除會連同該檔真正的 UI 字串一起遮蔽。
    # 關鍵：中文必須落在「名稱本身」才排除。不能只認行的形狀——usstock.js 的
    # `{ symbol:'AAPL', name:'Apple', group:'科技' }` 名稱是英文、中文在 group，
    # 那是**類別標籤**（科技／金融／消費／醫療／能源），是真的該翻譯的 UI 字串。
    re.compile(r"symbol:\s*['\"][^'\"]+['\"]\s*,\s*name:\s*['\"][^'\"]*[一-鿿]"),
    re.compile(
        r"['\"]?zh['\"]?\s*:\s*['\"][^'\"]*[一-鿿][^'\"]*['\"]\s*,\s*['\"]?en['\"]?\s*:"
    ),
    re.compile(
        r"^\s*['\"](?:\^[A-Z0-9]+|\d{4,}(?:\.[A-Z]{1,4})?|[A-Z]{1,6}\.[A-Z]{1,4})['\"]"
        r"\s*:\s*['\"][^'\"]*[一-鿿]"
    ),
    re.compile(
        r"\bbank:\s*['\"][^'\"]*[一-鿿]"
    ),  # { bank: 'Fed (美國)', rate: '5.25%' }
    re.compile(r"^\s*#\s*(region|endregion|pragma)"),  # region markers
    re.compile(r"@(param|returns?|type|example|throws?|deprecated|see)"),
    re.compile(r"'\\u[0-9a-fA-F]{4}'"),  # unicode escape literals
    re.compile(r'"\.\.\."|\u2026'),  # ellipsis as separator
    re.compile(r"^\s*/\*[\s\S]*?\*/\s*$"),  # inline JS block comment /* ... */
]

# Files to skip entirely (by relative path suffix)
SKIP_PATHS = {
    "web/js/i18n/en.json",
    "web/js/i18n/zh-TW.json",
}

# Directories to skip
SKIP_DIRS = {"node_modules", "vendor", "dist", ".vite", "__pycache__"}

# Files to skip entirely (by filename pattern, checked after directory skip)
SKIP_FILE_PATTERNS = [
    "web/js/twstock.js",  # stock data: Chinese company names are data, not UI strings
    "web/js/hkstock.js",  # stock data: Chinese company names
    "web/js/commodity.js",  # commodity data: Chinese commodity names
    "web/js/notification-service.js",  # demo notification templates (development fixture)
]


def should_skip_path(path: Path) -> bool:
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    if rel in SKIP_PATHS:
        return True
    if any(part in SKIP_DIRS for part in path.parts):
        return True
    if "i18n" in path.parts and path.suffix == ".json":
        return True
    if any(pattern in rel for pattern in SKIP_FILE_PATTERNS):
        return True
    return False


def strip_comments(line: str) -> str:
    """Remove JS/HTML comments from line so Chinese inside comments isn't flagged."""
    # Remove inline /* ... */ block comments
    line = re.sub(r"/\*.*?\*/", "", line)
    # Remove trailing // comments (but not URI schemes)
    if "://" not in line:
        line = re.sub(r"//.*$", "", line)
    # Remove HTML comments
    line = re.sub(r"<!--.*?-->", "", line)
    return line


# legal 頁的四語屬性系統（data-zh／data-zh-cn／data-en／data-ru）：DANNY 2026-08-20
# 定案法律長文不進主 i18n JSON，四語齊全由 tests/test_legal_i18n_parity.py 看守。
# 帶 data-en 的元素，文字節點只是 zh-TW 的預設渲染，切語言時被對應屬性值蓋掉——
# 已翻譯，不是硬編。剝掉這些元素與 data-zh* 屬性值後還剩中文才算數：ToS「生效日期」
# 那行元素外面的「2026年9月14日」沒有譯文，照樣計數。
_INLINE_TRANSLATED_ELEMENT = re.compile(
    r'<(\w+)((?:\s+[\w:.-]+(?:\s*=\s*"[^"]*")?)*)\s*>[^<]*</\1>'
)
# 譯文元素裡的 <strong>／<a> 等（`您同意<strong>不得</strong>：`）：非 zh-TW 時整個
# 元素的 textContent 被譯文取代，所以先把「本身沒帶 data-zh」的行內格式標籤拆掉，
# 外層譯文元素才會變成純文字、被上面那條剝掉。由內往外反覆做到不再變。
_INLINE_FORMATTING = re.compile(
    r"<(strong|b|em|i|u|a|code)\b(?![^>]*\bdata-zh=)[^>]*>([^<]*)</\1>"
)
_INLINE_ZH_ATTR = re.compile(r'\bdata-zh(?:-cn)?="[^"]*"')
# 語言選單的自稱（endonym）：各語言用自己的文字寫名字，本來就不翻譯。
_LANGUAGE_ENDONYMS = re.compile(r"繁體中文|简体中文")


def strip_translated_chinese(line: str) -> str:
    """剝掉「已經有譯文」或「本來就不翻譯」的中文，剩下的才是硬編。"""

    def _drop_if_translated(m: re.Match) -> str:
        return "" if 'data-en="' in m.group(2) else m.group(0)

    line = re.sub(r"<br\s*/?>", " ", line)
    prev = None
    while prev != line:
        prev = line
        line = _INLINE_FORMATTING.sub(r"\2", line)
        line = _INLINE_TRANSLATED_ELEMENT.sub(_drop_if_translated, line)
    line = _INLINE_ZH_ATTR.sub("", line)
    return _LANGUAGE_ENDONYMS.sub("", line)


def should_exclude_line(line: str) -> bool:
    stripped = strip_comments(line)
    # If no Chinese remains after stripping comments / translated text, skip
    if not CHINESE_RE.search(strip_translated_chinese(stripped)):
        return True
    return any(p.search(stripped) for p in EXCLUDE_PATTERNS)


def iter_target_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in {".js", ".html", ".mjs"}:
            continue
        if should_skip_path(path):
            continue
        yield path


def scan_file(path: Path) -> list[tuple[int, str]]:
    """Return list of (line_number, line_content) with flagged Chinese."""
    issues: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return issues

    in_block_comment = False
    in_html_comment = False
    for i, raw_line in enumerate(text.split("\n"), 1):
        line = raw_line.rstrip()

        # Track JS block comments /* ... */ across lines
        if "/*" in line and "*/" not in line:
            in_block_comment = True
            continue
        if in_block_comment:
            if "*/" in line:
                in_block_comment = False
            continue

        # Track HTML comments <!-- ... --> across lines. 沒有這段的話，多行註解
        # 的「續行」會被當成硬編中文計數——4cd5e26e 就是為了繞開這個誤判才把
        # 驗證用的 meta tag 註解改成單行；那是治標。這裡治本。
        if "<!--" in line and "-->" not in line:
            in_html_comment = True
            continue
        if in_html_comment:
            if "-->" in line:
                in_html_comment = False
            continue

        if not CHINESE_RE.search(line):
            continue
        if should_exclude_line(line):
            continue

        issues.append((i, line.strip()[:240]))
    return issues


def main() -> int:
    # Windows 主控台預設是 cp950，印到第一個非 Big5 字元（簡體／俄文）就
    # UnicodeEncodeError 中斷——輸出被截斷，呼叫端（棘輪測試）於是數到殘缺的
    # 行數。這個腳本的輸出本來就是多語系文字，固定用 UTF-8 輸出。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    total_issues = 0
    files_with_issues = 0
    by_file: list[tuple[Path, int]] = []

    for path in iter_target_files(WEB_DIR):
        issues = scan_file(path)
        if not issues:
            continue
        files_with_issues += 1
        rel = path.relative_to(ROOT)
        by_file.append((rel, len(issues)))
        for line_no, content in issues:
            print(f"{rel}:{line_no}: {content}")
            total_issues += 1

    print()
    print(f"files_with_hardcoded_chinese={files_with_issues}")
    print(f"total_hardcoded_chinese_lines={total_issues}")

    if files_with_issues > 0:
        print()
        print("Top files by issue count:")
        for rel, count in sorted(by_file, key=lambda x: -x[1])[:10]:
            print(f"  {count:4d}  {rel}")

    return 1 if total_issues > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
