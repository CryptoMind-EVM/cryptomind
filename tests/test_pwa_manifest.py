"""Tests for the /manifest.webmanifest endpoint (PWA installability).

用 api/public_base 的 base-URL 修復邏輯（以前跟 /tonconnect-manifest.json 共用），因為
同一個 Zeabur proxy 陷阱：TLS 在 proxy 層終止，容器收到的是純 HTTP，
gunicorn 的 forwarded_allow_ips 預設只信 127.0.0.1 → request.base_url 是
http://。PWA 的 start_url / scope / icons 若發出 http://，安裝提示與
iOS「加到主畫面」會出問題（與 tonconnect 登入斷線同根因）。
"""

import struct
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from api_server import app

    return TestClient(app)


def _manifest(client, **headers):
    res = client.get("/manifest.webmanifest", headers=headers)
    assert res.status_code == 200
    return res


def test_manifest_returns_correct_mime_type(client):
    """nosniff 已開，MIME 必須是 application/manifest+json，否則瀏覽器拒收。"""
    res = _manifest(client, **{"Host": "localhost:8080"})
    assert "manifest+json" in res.headers.get("content-type", "")


def test_manifest_has_required_pwa_fields(client):
    """可安裝 PWA 必備欄位齊全。"""
    data = _manifest(client, **{"Host": "localhost:8080"}).json()
    for key in (
        "name",
        "short_name",
        "description",
        "start_url",
        "scope",
        "display",
        "theme_color",
        "background_color",
        "icons",
    ):
        assert key in data, key
    assert data["display"] in ("standalone", "fullscreen", "minimal-ui")


def test_manifest_icons_include_192_512_and_maskable(client):
    """Chrome 安裝提示需要 192 + 512；maskable 確保 Android 自適應圖示。"""
    data = _manifest(client, **{"Host": "localhost:8080"}).json()
    icons = data["icons"]
    sizes = {i["sizes"] for i in icons}
    assert "192x192" in sizes
    assert "512x512" in sizes
    # 至少有一個 maskable purpose
    assert any("maskable" in i.get("purpose", "") for i in icons)


def test_manifest_uses_forwarded_proto_when_behind_a_tls_proxy(client):
    """X-Forwarded-Proto 勝過容器內的純 HTTP 連線。"""
    data = _manifest(
        client,
        **{"X-Forwarded-Proto": "https", "Host": "cryptomind-ton.zeabur.app"},
    ).json()

    assert data["start_url"].startswith("https://cryptomind-ton.zeabur.app")
    assert data["scope"].startswith("https://cryptomind-ton.zeabur.app")
    for icon in data["icons"]:
        assert icon["src"].startswith("https://cryptomind-ton.zeabur.app"), icon


def test_manifest_handles_multi_hop_forwarded_proto(client):
    """proxy chain 給逗號分隔清單，第一跳才是 client 真實協定。"""
    data = _manifest(
        client,
        **{"X-Forwarded-Proto": "https, http", "Host": "cryptomind-ton.zeabur.app"},
    ).json()

    assert data["start_url"].startswith("https://")


def test_manifest_stays_http_for_local_dev(client):
    """本機無 proxy header → 維持 http，開發不受影響。"""
    data = _manifest(client, **{"Host": "localhost:8080"}).json()

    assert data["start_url"].startswith("http://localhost:8080")
    assert data["scope"] == "http://localhost:8080/"


def test_manifest_forces_https_in_production_without_forwarded_proto(
    client, monkeypatch
):
    """保底：production 缺 X-Forwarded-Proto 也絕不發 http manifest。"""
    import api.public_base as public_base

    monkeypatch.setattr(public_base, "_IS_PRODUCTION", True)

    data = _manifest(client, **{"Host": "cryptomind-ton.zeabur.app"}).json()

    assert data["start_url"].startswith("https://cryptomind-ton.zeabur.app")


def _png_alpha_bytes(path: Path) -> bytes:
    """解 8-bit RGBA、非交錯 PNG，回傳每個像素的 alpha 值（不靠 Pillow）。"""
    raw = path.read_bytes()
    assert raw[1:4] == b"PNG"  # 完整簽章是 \x89PNG\r\n\x1a\n，驗字樣即可
    pos, idat, width, height = 8, b"", 0, 0
    while pos < len(raw):
        (length,) = struct.unpack(">I", raw[pos : pos + 4])
        kind = raw[pos + 4 : pos + 8]
        body = raw[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(
                ">IIBBBBB", body
            )
            assert (depth, ctype, interlace) == (8, 6, 0), (
                "expected 8-bit RGBA non-interlaced"
            )
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    data = zlib.decompress(idat)
    stride, bpp = width * 4, 4
    prev = bytearray(stride)
    alphas = bytearray()
    for row in range(height):
        base = row * (stride + 1)
        ftype, line = data[base], bytearray(data[base + 1 : base + 1 + stride])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if ftype == 1:
                line[i] = (line[i] + a) & 255
            elif ftype == 2:
                line[i] = (line[i] + b) & 255
            elif ftype == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 255
        alphas += line[3::4]
        prev = line
    return bytes(alphas)


@pytest.mark.parametrize("name", ["icon-192-maskable.png", "icon-512-maskable.png"])
def test_maskable_icons_are_fully_opaque(name):
    """maskable 圖的透明留白會被 Android 填成純黑——啟動畫面在 logo 外多一圈黑圓
    （2026-10-06 DANNY 截圖）。整張必須不透明，底色同 manifest background_color。"""
    alphas = _png_alpha_bytes(
        Path(__file__).resolve().parents[1] / "web" / "img" / name
    )
    assert min(alphas) == 255, f"{name} 有透明像素"


def test_maskable_icon_urls_are_versioned(client):
    """圖換了、網址不變的話，已安裝的 PWA 會一直用舊圖。"""
    icons = _manifest(client, **{"Host": "localhost:8080"}).json()["icons"]
    maskable = [i["src"] for i in icons if i.get("purpose") == "maskable"]
    assert maskable and all("?v=" in src for src in maskable)
