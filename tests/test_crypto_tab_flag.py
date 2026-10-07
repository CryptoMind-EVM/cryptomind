"""加密貨幣分頁旗標（2026-10-05，docs/plans/2026-10-04-growth-handoff.md 任務 B）。

兩層：平台能力表（Play 版一律沒有）＋環境變數 CRYPTO_TAB_ENABLED（預設開，部署端一鍵關）。
前端只讀 /api/config 的 capabilities.crypto_tab。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import platform

pytestmark = pytest.mark.unit

FLAG = "CRYPTO_TAB_ENABLED"


def _config(platform_header: str | None = None) -> dict:
    from api.routers import system

    app = FastAPI()
    app.include_router(system.router)
    headers = {"X-Platform": platform_header} if platform_header else {}
    return TestClient(app).get("/api/config", headers=headers).json()


@pytest.mark.parametrize(
    "plat, expected",
    [("web", True), ("tma", True), ("baseapp", True), ("play", False)],
)
def test_platform_table(plat, expected):
    assert platform.capabilities(plat)["crypto_tab"] is expected


def test_default_on(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert _config()["capabilities"]["crypto_tab"] is True


@pytest.mark.parametrize("raw", ["true", "1", "yes"])
def test_env_on(monkeypatch, raw):
    monkeypatch.setenv(FLAG, raw)
    assert _config()["capabilities"]["crypto_tab"] is True


@pytest.mark.parametrize("raw", ["false", "0", "", "off"])
def test_env_off_closes_every_platform(monkeypatch, raw):
    monkeypatch.setenv(FLAG, raw)
    for plat in (None, "tma", "baseapp"):
        assert _config(plat)["capabilities"]["crypto_tab"] is False


def test_play_is_closed_even_when_env_is_on(monkeypatch):
    monkeypatch.setenv(FLAG, "true")
    assert _config("play")["capabilities"]["crypto_tab"] is False


def test_documented_in_env_example():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(
        encoding="utf-8"
    )
    assert FLAG in text
