"""/static/assets 帶內容 hash 的打包檔可以長期快取；HTML 維持 no-store；其他靜態檔每次重驗。

2026-09-26：所有 JS/CSS 都被 security_headers_middleware 設成 no-store，
每次開頁都重新下載約 650 KB、而且每個請求都得進到 VM（Cloudflare BYPASS）。
vite 輸出的檔名帶內容 hash（內容一變檔名就換），可以 immutable 快取一年。
9/11 付款頁事件的 no-store 是針對 HTML／未帶 hash 的檔案，必須維持。
"""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "web" / "assets"
IMMUTABLE = "public, max-age=31536000, immutable"


@pytest.fixture
def client():
    from api_server import app

    return TestClient(app)


@pytest.fixture
def built_assets():
    """在 web/assets 放幾個假的打包檔（本機沒 build 時這個目錄不存在），測完刪掉。"""
    created_dir = not ASSETS.exists()
    ASSETS.mkdir(exist_ok=True)
    (ASSETS / "forum").mkdir(exist_ok=True)
    files = {
        "main-BtnEPJcA.js": "export{};",
        "main-BtnEPJcA.js.map": "{}",
        "style-Ab3dE_9-.css": "body{}",
        "forum/post-CQHqpEti.js": "export{};",
        "plain.js": "export{};",  # 沒有 hash：不能長期快取
    }
    for name, body in files.items():
        (ASSETS / name).write_text(body, encoding="utf-8")
    yield
    for name in files:
        (ASSETS / name).unlink(missing_ok=True)
    if created_dir:
        (ASSETS / "forum").rmdir()
        ASSETS.rmdir()


@pytest.mark.parametrize(
    "path",
    [
        "/static/assets/main-BtnEPJcA.js",
        "/static/assets/main-BtnEPJcA.js.map",
        "/static/assets/style-Ab3dE_9-.css",
        "/static/assets/forum/post-CQHqpEti.js",
    ],
)
def test_hashed_build_assets_are_immutable(client, built_assets, path):
    res = client.get(path)
    assert res.status_code == 200
    assert res.headers.get("cache-control") == IMMUTABLE


@pytest.mark.parametrize(
    ("path", "hashed"),
    [
        # /assets/ 是同一個目錄的第二個掛載點；api_server 只在啟動時 web/assets 已存在才掛
        #（正式 image 一定有，本機沒 build 就沒有），所以這裡直接測判斷式
        ("/assets/main-BtnEPJcA.js", True),
        ("/static/assets/PhCaretLeft--3o0VZOa.js", True),  # hash 本身可以以 - 開頭
        ("/static/assets/dashboard-CEuLW5-5.js.map", True),
        ("/static/assets/plain.js", False),
        ("/static/assets/main-BtnEPJcA.png", False),  # 只放行 js / css / map
        ("/static/js/main-BtnEPJcA.js", False),  # 不在 assets 底下
        ("/static/index.html", False),
        ("/static/assets/../index.html", False),
        ("/static/assets/../js/main-BtnEPJcA.js", False),  # 跳出 assets 目錄
        ("/static/assets/.hidden-BtnEPJcA.js", False),
    ],
)
def test_hashed_asset_matcher(path, hashed):
    from api.middleware_setup import _is_hashed_build_asset

    assert _is_hashed_build_asset(path) is hashed


def test_unhashed_file_under_assets_is_revalidated_not_immutable(client, built_assets):
    res = client.get("/static/assets/plain.js")
    assert res.status_code == 200
    assert res.headers.get("cache-control") == "private, no-cache"


def test_missing_hashed_chunk_is_not_cached(client, built_assets):
    """部署後舊頁面 lazy-load 已被換掉的 chunk 會 404——不能被快取一年。"""
    res = client.get("/static/assets/gone-Zz9Zz9Zz.js")
    assert res.status_code == 404
    assert "immutable" not in res.headers.get("cache-control", "")


@pytest.mark.parametrize(
    "path",
    ["/static/index.html", "/static/forum/index.html", "/static/forum/premium.html", "/scam-tracker/detail.html"],
)
def test_html_stays_no_store(client, path):
    """HTML 維持 9/11 事件後的 no-store：BFCache 只看主文件的 no-store，付款頁不能被還原成舊版。"""
    res = client.get(path)
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
    cc = res.headers.get("cache-control", "")
    assert "no-store" in cc and "immutable" not in cc


@pytest.mark.parametrize(
    "path", ["/static/js/nav-config.js", "/js/logger.js", "/static/css/tailwind-built.css", "/static/js/i18n/en.json"]
)
def test_unbundled_static_is_revalidated_every_time(client, path):
    """非 HTML、未帶 hash 的靜態檔：no-cache（每次重驗，不會拿到舊版），不再 no-store 每頁重下載。"""
    res = client.get(path)
    assert res.status_code == 200
    assert res.headers.get("cache-control") == "private, no-cache"
    etag = res.headers.get("etag")
    assert etag, "要有 ETag 才能重驗"
    again = client.get(path, headers={"If-None-Match": etag})
    assert again.status_code == 304, "內容沒變時要回 304（不重傳內容）"
    assert not again.content


