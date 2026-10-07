"""SW precache 設定守衛（2026-09-07 掃到的 /sw-register.js 404）。

現象：線上每次 SW 更新都會 GET /sw-register.js → 404（access log 可見）。
根因：Workbox precache glob（**/*.{js,...}）把 publicDir 的註冊腳本
sw-register.js 掃進清單，清單以根路徑請求它（base 是 /static/）——
而該檔根本不該 precache：它的職責是「安裝 SW」，每次都該跑新的。

行為驗證靠 build 產物（npm run build 後 dist/static/sw.js 不得再引用
sw-register——本測試若 dist 存在則順手檢查；CI 不 build，故僅在有產物
時檢查）。本檔主要守住設定不被回退。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def test_glob_ignores_sw_register():
    vite = (REPO / "vite.config.js").read_text(encoding="utf-8")
    assert "globIgnores" in vite, "拔掉 globIgnores 會讓 /sw-register.js 404 回來"
    assert "sw-register.js" in vite.split("globIgnores")[1].split("]")[0]


def test_built_sw_manifest_clean_if_present():
    """本地/部署 build 有產物時：sw.js 不得再引用 sw-register（precache
    清單）。dist 不存在（乾淨 checkout）時跳過。"""
    sw = REPO / "dist" / "static" / "sw.js"
    if not sw.exists():
        pytest.skip("dist 未建置")
    content = sw.read_text(encoding="utf-8")
    assert "sw-register" not in content, (
        "precache 清單仍含 sw-register——globIgnores 失效或被繞過"
    )


# ---- 2026-09-27 PR-1：SW 裝不起來＋錢包 SDK 被 precache ----
# sw.js 在根路徑 /sw.js 服務，precache 的相對網址被解析成 /index.html、/forum/*.html
# （正式站 404 → install 失敗、SW 從沒接管過），assets 則變成 /assets/…（跟頁面實際
# 請求的 /static/assets/… 對不上）。另外近 200 個 AppKit chunk 會讓第一次來的訪客
# 背景下載一整套用不到的錢包 SDK。


def _vite() -> str:
    return (REPO / "vite.config.js").read_text(encoding="utf-8")


def test_html_never_precached():
    """HTML 永遠走網路：precache 過 HTML 就會重演 #790「部署後第一次開仍是舊版」。"""
    vite = _vite()
    globs = vite.split("globPatterns:")[1].split("]")[0]
    assert "html" not in globs, f"globPatterns 不可含 html：{globs}"
    assert "navigateFallback: null" in vite, (
        "沒有明寫 null 時 vite-plugin-pwa 預設 navigateFallback='index.html'——"
        "導航會被 SW 用快取的 index.html 回應"
    )


def test_precache_filter_wired():
    """錢包 chunk 排除與 /static/ 前綴靠 manifestTransforms＋收集器 plugin，兩個都要接上。"""
    vite = _vite()
    assert "precacheCollector(" in vite
    assert "manifestTransforms:" in vite
    assert "filterPrecacheManifest(" in vite.split("manifestTransforms:")[1]


def test_precache_filter_node_assertions():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "sw_precache.mjs")],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_built_precache_list_if_present():
    """有 build 產物時檢查實際清單：無 html、無錢包 chunk、網址都在 /static/assets/。
    dist 是舊的（改設定前 build）會紅——重跑 npm run build。"""
    sw = REPO / "dist" / "static" / "sw.js"
    if not sw.exists():
        pytest.skip("dist 未建置")
    urls = re.findall(r'url:"([^"]+)"', sw.read_text(encoding="utf-8"))
    assert urls, "precache 清單是空的"
    assert not [u for u in urls if u.endswith(".html")], "precache 不得含 HTML"
    wallet = [u for u in urls if re.search(r"/(w3m|wui)-", u)]
    assert not wallet, f"錢包 SDK chunk 不該 precache：{wallet[:5]}"
    assert all(u.startswith("/static/assets/") for u in urls), (
        "precache 網址要跟頁面請求的 /static/assets/… 一致"
    )


def test_first_install_does_not_reload():
    """SW 能裝起來之後，新訪客第一次來 clientsClaim 也會觸發 controllerchange——
    原本沒有 controller（第一次安裝）不是「換版」，不可以整頁重整。"""
    reg = (REPO / "web" / "public" / "sw-register.js").read_text(encoding="utf-8")
    assert "navigator.serviceWorker.controller" in reg
    handler = reg.split("addEventListener('controllerchange'")[1]
    assert "if (!hadController)" in handler, "要是可執行的判斷，不是註解裡提到"
    assert handler.index("if (!hadController)") < handler.index(
        "showToast"
    ), "提示新版之前要先確認頁面載入時已有舊 SW 在控制"
    assert "location.reload" not in reg, "換版不自動重整（會打斷進行中的登入，2026-09-27）"


def test_vite_config_local_imports_reach_docker_builder():
    """vite.config.js import 的本地檔都要被 Dockerfile builder 階段 COPY 進去——
    builder 只 COPY web/ 與點名的根目錄設定檔，漏一個 image build 就在 npm run build 炸掉。"""
    local = re.findall(r"from '\./([^']+)'", _vite())
    assert local, "預期 vite.config.js 至少 import vite-sw-precache.mjs"
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    builder = dockerfile.split("\nFROM ")[1]  # 第一個 stage（builder）
    copied = " ".join(ln for ln in builder.splitlines() if ln.startswith("COPY "))
    missing = [f for f in local if f not in copied]
    assert not missing, f"Dockerfile builder 沒有 COPY：{missing}"
