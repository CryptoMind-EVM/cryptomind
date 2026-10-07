"""web/ 靜態頁 classic script 引用 ↔ Dockerfile raw-js 保留白名單一致性。

2026-08-20 線上事故：scam-tracker/（3 頁）與 governance/（1 頁）不在 Vite
多頁 build input 裡，以 classic <script src="/static/js/..."> 引用共用腳本；
Dockerfile 尾段為省 image 把 web/js raw 檔刪光（僅留白名單）→ 正式環境
404、頁面卡死在 Loading reports（window.I18n/showToast 未載入）。

此測試在同類事故進 CI 前攔截：任何 web/**.html 引用的 /static/js/*.js
都必須存在於 Dockerfile find ... ! -name "x.js" ... -delete 的保留清單。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _dockerfile_keep_whitelist() -> set[str]:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    return set(re.findall(r'! -name "([^"]+\.js)"', text))


def _classic_script_refs() -> dict[str, set[str]]:
    """掃所有 web 子頁 HTML 的 <script src="/static/js/xxx.js">（含 module）。"""
    refs: dict[str, set[str]] = {}
    for html in ROOT.glob("web/**/*.html"):
        content = html.read_text(encoding="utf-8")
        names = set(re.findall(r'<script[^>]*src="/static/js/([^"?]+\.js)', content))
        if names:
            # as_posix：Windows 下 relative_to 產生反斜線路徑，斷言用正斜線
            refs[html.relative_to(ROOT).as_posix()] = names
    return refs


def _compat_module_imports() -> dict[str, set[str]]:
    """相容橋 module（classic-compat*.js）的相對 import 也必須存在於 image。"""
    imports: dict[str, set[str]] = {}
    for js in ROOT.glob("web/js/classic-compat*.js"):
        content = js.read_text(encoding="utf-8")
        names = set(
            re.findall(r"""(?:from\s*|import\s*)['"]\.(/?[^'"]+\.js)['"]""", content)
        )
        if names:
            imports[js.name] = {n.lstrip("/") for n in names}
    return imports


def test_affected_pages_present():
    """事故頁面必須持續被掃描到（防止 glob 失效讓主測試空轉綠燈）。"""
    refs = _classic_script_refs()
    for page in (
        "web/scam-tracker/detail.html",  # 列表頁 2026-09-27 改成 SPA 分頁，只剩 detail／submit
        "web/governance/index.html",
    ):
        assert page in refs, f"{page} 應有 /static/js classic script 引用"


def test_classic_script_refs_covered_by_dockerfile_whitelist():
    keep = _dockerfile_keep_whitelist()
    refs = _classic_script_refs()
    missing = {
        page: sorted(names - keep) for page, names in refs.items() if names - keep
    }
    # Dockerfile 的 find 只清 web/js 頂層（-maxdepth 1），components/ 等子目錄整個保留
    # （子頁本來就從 /js/components/ThemeToggle.js 載入）；子目錄檔只要存在就好
    compat_imports = _compat_module_imports()
    for names in compat_imports.values():
        for name in names:
            if "/" in name:
                assert (ROOT / "web/js" / name).is_file(), name
    compat_missing = {
        mod: sorted(n for n in names - keep if "/" not in n)
        for mod, names in compat_imports.items()
        if any("/" not in n for n in names - keep)
    }
    missing.update(compat_missing)
    assert not missing, (
        "以下 HTML/相容橋引用的 /static/js 檔會在 Docker image 裡被刪除（線上 404）："
        f"{missing}。請把檔案加入 Dockerfile 的保留白名單（find ! -name ...），"
        "或將該頁面納入 Vite 多頁 build input。"
    )


def test_dockerfile_prunes_only_top_level_js():
    """上面「子目錄檔不會被刪」的前提：清理指令必須維持 -maxdepth 1"""
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"find /app/web/js -maxdepth 1 -type f", text)
