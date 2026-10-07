"""Telegram 內直接開啟分享連結（2026-10-05，docs/plans/2026-10-04-growth-handoff.md 任務 C）。

分享連結改用 ``https://t.me/<bot>/<short_name>?startapp=ask_<編碼後的問題>``，Mini App 內開、不跳瀏覽器。
bot username 與 Mini App short name 都來自環境變數；任一沒設（或格式不對）就維持原本的網頁連結。
前端只讀 /api/config 的 ``telegram_miniapp``。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
BOT, APP = "TELEGRAM_BOT_USERNAME", "TELEGRAM_MINIAPP_SHORT_NAME"


def _config() -> dict:
    from api.routers import system

    app = FastAPI()
    app.include_router(system.router)
    return TestClient(app).get("/api/config").json()


def test_absent_by_default(monkeypatch):
    monkeypatch.delenv(BOT, raising=False)
    monkeypatch.delenv(APP, raising=False)
    assert _config()["telegram_miniapp"] is None


def test_needs_both_values(monkeypatch):
    monkeypatch.setenv(BOT, "getcryptomind_bot")
    monkeypatch.delenv(APP, raising=False)
    assert _config()["telegram_miniapp"] is None
    monkeypatch.delenv(BOT, raising=False)
    monkeypatch.setenv(APP, "cmind")
    assert _config()["telegram_miniapp"] is None


def test_both_set(monkeypatch):
    monkeypatch.setenv(BOT, "@getcryptomind_bot")  # 帶 @ 也收（跟 telegram_link 一致）
    monkeypatch.setenv(APP, " cmind ")
    assert _config()["telegram_miniapp"] == {
        "bot_username": "getcryptomind_bot",
        "short_name": "cmind",
    }


@pytest.mark.parametrize(
    "bot, app",
    [
        ("bad bot", "cmind"),
        ("getcryptomind_bot", "c/mind"),
        ("getcryptomind_bot", "cmind?x=1"),
        ("getcryptomind_bot", "<script>"),
        ("ab", "cmind"),
    ],
)
def test_malformed_values_are_ignored(monkeypatch, bot, app):
    """這兩個值會被拼進網址：格式不對就當沒設，不要把奇怪的字串送給前端。"""
    monkeypatch.setenv(BOT, bot)
    monkeypatch.setenv(APP, app)
    assert _config()["telegram_miniapp"] is None


def test_documented_in_env_example():
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    assert APP in text


def test_share_link_tma_node_gate():
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "share_link_tma.mjs")],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "share_link_tma: ok" in proc.stderr
