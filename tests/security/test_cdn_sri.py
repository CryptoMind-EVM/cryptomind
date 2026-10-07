"""守護測試:確保所有外部 CDN <script> 都帶 SRI integrity(防供應鏈竄改)。

背景(GAP-5):CSP 允許 cdn.jsdelivr.net / unpkg.com / cdnjs.cloudflare.com 等 CDN
整個 origin。CDN 一旦被供應鏈攻擊或帳號被盜(jsdelivr/unpkg 都出過事),任意 JS
可注入。SRI(subresource integrity)讓瀏覽器驗證檔案 hash,不符就拒載。

唯一例外:telegram.org/js/telegram-web-app.js — 版本由 Telegram 控制,內容非我們
pin,加 SRI 反而會在 Telegram 更新時壞掉。此例外在測試中明確允許。

本測試是「繼承式保障」:未來新增任何 HTML 引用外部 CDN script,若忘了加 integrity,
本測試會失敗,強制補上。

另設一個「hash 正確性」測試:`test_integrity_hash_matches_actual_cdn_content`。
歷史教訓 — 2026-07 GAP-5 修補中,lucide@0.577.0 的 SRI hash 被填成一個對不上檔案
內容的值,瀏覽器驗 SRI 失敗 → 拒載 lucide → `window.lucide` 為 undefined →
`AppUtils.refreshIcons()` 靜默 early-return → 全站所有 lucide 圖示(含底部導覽列)
全部消失。既有測試只檢查「有沒有 integrity 屬性」,完全沒擋下錯誤 hash。此測試
補上「實際抓 CDN 內容比對 hash」這一層,在連網環境(CI / 本機有網路)執行;
離線時 skip 以免 CI 因 CDN 暫時不可用而 flaky。
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from pathlib import Path

import pytest

# telegram-web-app.js 例外:版本由 Telegram 控制,內容非固定
_EXEMPT_URLS = ("telegram.org/js/telegram-web-app.js",)

# 所有 HTML 檔(排除 vendor/第三方範例)
WEB_ROOT = Path(__file__).resolve().parents[2] / "web"


def _collect_html_files():
    return sorted(WEB_ROOT.rglob("*.html"))


def _extract_external_scripts(content: str):
    """從 HTML 內容抽出所有外部 <script src="https://..."> 的 (tag, url)。"""
    scripts = []
    for m in re.finditer(r'<script\b[^>]*\bsrc="(https?://[^"]+)"[^>]*>', content, re.IGNORECASE):
        url = m.group(1)
        if any(exempt in url for exempt in _EXEMPT_URLS):
            continue
        scripts.append((m.group(0), url))
    return scripts


def _extract_integrity(tag: str) -> str | None:
    """從 script tag 抽出 integrity 屬性值(如 'sha384-abc...');無則 None。"""
    m = re.search(r'\bintegrity="([^"]+)"', tag, re.IGNORECASE)
    return m.group(1) if m else None


def _compute_sri(content: bytes) -> str:
    """計算 bytes 內容的 sha384 SRI 字串(瀏覽器 / OpenSSL 同演算法)。"""
    digest = hashlib.sha384(content).digest()
    return "sha384-" + base64.b64encode(digest).decode("ascii")


def _have_network() -> bool:
    """偵測是否要跑連網 SRI 比對。

    - 明確設 `RUN_SRI_NETWORK_CHECK=0` 時一律 skip(離線 CI / 想關掉時)。
    - 否則嘗試連 CDN;連不上就 skip(避免 CDN 暫時故障造成 flaky)。
    """
    if os.getenv("RUN_SRI_NETWORK_CHECK", "1") == "0":
        return False
    try:
        import urllib.request

        urllib.request.urlopen("https://unpkg.com", timeout=5).close()
    except Exception:
        return False
    return True


@pytest.mark.unit
def test_all_cdn_scripts_have_sri_integrity():
    """每個外部 CDN script(除 telegram 例外)都必須帶 integrity 屬性。"""
    html_files = _collect_html_files()
    assert html_files, "should find HTML files under web/"

    missing = []
    for hf in html_files:
        content = hf.read_text(encoding="utf-8")
        for tag, url in _extract_external_scripts(content):
            if "integrity=" not in tag.lower():
                missing.append(f"{hf.relative_to(WEB_ROOT)}: {url}")

    assert not missing, (
        "以下外部 CDN script 缺少 SRI integrity 屬性(供應鏈風險):\n  "
        + "\n  ".join(missing)
        + "\n請計算該檔案的 sha384 hash 並加上 integrity + crossorigin=\"anonymous\""
    )


@pytest.mark.unit
def test_all_cdn_scripts_have_crossorigin():
    """帶 integrity 的 script 必須也帶 crossorigin(SRI 要求)。"""
    html_files = _collect_html_files()
    missing = []
    for hf in html_files:
        content = hf.read_text(encoding="utf-8")
        for tag, url in _extract_external_scripts(content):
            if "integrity=" in tag.lower() and "crossorigin" not in tag.lower():
                missing.append(f"{hf.relative_to(WEB_ROOT)}: {url}")

    assert not missing, (
        "以下 script 有 integrity 但缺 crossorigin(SRI 會失效):\n  "
        + "\n  ".join(missing)
    )


@pytest.mark.unit
def test_no_unpinned_versions():
    """外部 script 不可用 @latest/@next 等移動靶版本(SRI hash 會失效)。"""
    html_files = _collect_html_files()
    unpinned = []
    for hf in html_files:
        content = hf.read_text(encoding="utf-8")
        for tag, url in _extract_external_scripts(content):
            if re.search(r"@(latest|next|dev|beta|alpha)\b", url):
                unpinned.append(f"{hf.relative_to(WEB_ROOT)}: {url}")

    assert not unpinned, (
        "以下外部 script 用了移動靶版本(@latest 等),SRI hash 會失效:\n  "
        + "\n  ".join(unpinned)
        + "\n請 pin 具體版本號"
    )


@pytest.mark.unit
def test_integrity_hash_format_is_valid():
    """每個 integrity 屬性必須是 'sha384-<base64>' 格式(瀏覽器才認得)。"""
    html_files = _collect_html_files()
    bad = []
    for hf in html_files:
        content = hf.read_text(encoding="utf-8")
        for tag, _url in _extract_external_scripts(content):
            integrity = _extract_integrity(tag)
            if integrity is None:
                continue  # 缺 integrity 由其他測試處理
            if not re.fullmatch(r"sha(256|384|512)-[A-Za-z0-9+/]+={0,2}", integrity):
                bad.append(f"{hf.relative_to(WEB_ROOT)}: {integrity!r}")

    assert not bad, (
        "以下 integrity 屬性格式不正確,瀏覽器會視為無效而拒載 script:\n  "
        + "\n  ".join(bad)
    )


@pytest.mark.unit
def test_integrity_hash_matches_actual_cdn_content():
    """連網時:實際抓 CDN 檔案計算 hash,跟 HTML 宣告的 integrity 比對。

    防止「有 integrity 但 hash 填錯」——瀏覽器會因 SRI 不符而拒載 script,
    導致依賴該 script 的功能(如 lucide 圖示)靜默失效。離線 / CDN 不可用時 skip。
    """
    if not _have_network():
        pytest.skip("無網路或 RUN_SRI_NETWORK_CHECK=0,跳過 CDN 內容比對")

    import urllib.request

    # 收集所有 (url -> 宣告的 integrity) 並去重(同一 URL 跨檔案應一致)
    url_to_integrity: dict[str, set[str]] = {}
    url_to_files: dict[str, list[str]] = {}
    for hf in _collect_html_files():
        content = hf.read_text(encoding="utf-8")
        for tag, url in _extract_external_scripts(content):
            integrity = _extract_integrity(tag)
            if integrity is None:
                continue
            url_to_integrity.setdefault(url, set()).add(integrity)
            url_to_files.setdefault(url, []).append(str(hf.relative_to(WEB_ROOT)))

    # 先驉同 URL 跨檔案宣告必須一致(否則必有某份是錯的)
    inconsistent = {u: v for u, v in url_to_integrity.items() if len(v) > 1}
    assert not inconsistent, (
        "同一 CDN URL 在不同檔案宣告了不同的 integrity,必定有人填錯:\n"
        + "\n".join(f"  {u}: {sorted(v)}" for u, v in inconsistent.items())
    )

    mismatches = []
    for url, declared_set in url_to_integrity.items():
        declared = next(iter(declared_set))
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                content = resp.read()
        except Exception as exc:  # noqa: BLE001 - CDN 暫時故障要 skip 不是 fail
            pytest.skip(f"無法抓取 {url} 比對 SRI({exc});離線/CDN 故障,跳過")
        actual = _compute_sri(content)
        if actual != declared:
            mismatches.append(
                f"{url}\n    宣告: {declared}\n    實際: {actual}\n    用於: {url_to_files[url]}"
            )

    assert not mismatches, (
        "以下 CDN script 的 SRI integrity 與實際檔案內容不符 —— 瀏覽器會拒載,\n"
        "導致依賴該 script 的功能靜默失效(例如 lucide 圖示全部消失)。請用實際\n"
        "檔案內容重算 sha384 hash 並更新 integrity:\n  "
        + "\n  ".join(mismatches)
    )
