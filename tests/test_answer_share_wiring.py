"""AI 回答快照分享的接線（2026-10-05 任務 D）：前端 node 測試、四語文案、旗標預設關、路由註冊。"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
LOCALES = ("zh-TW", "zh-CN", "en", "ru")
FLAG = "CONVERSATION_SHARE_ENABLED"


def test_answer_share_ui_node_gate():
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "answer_share_ui.mjs")],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "answer_share_ui: ok" in proc.stderr


@pytest.mark.parametrize("loc", LOCALES)
def test_every_ui_string_exists_in_every_locale(loc):
    src = (REPO / "web/js/answer-share.js").read_text(encoding="utf-8")
    src += (REPO / "web/js/share-link.js").read_text(encoding="utf-8")
    used = set(re.findall(r"'(answerShare\.[A-Za-z]+)'", src))
    assert len(used) >= 15
    data = json.loads((REPO / f"web/js/i18n/{loc}.json").read_text(encoding="utf-8"))[
        "answerShare"
    ]
    missing = {k for k in used if not data.get(k.split(".", 1)[1], "").strip()}
    assert not missing, f"{loc} 缺：{sorted(missing)}"


@pytest.mark.parametrize("loc", LOCALES)
def test_public_notice_does_not_promise_more_than_we_do(loc):
    """公開前的提示要講清楚三件事：誰看得到（不用登入）、幾天失效、可隨時停止。"""
    data = json.loads((REPO / f"web/js/i18n/{loc}.json").read_text(encoding="utf-8"))[
        "answerShare"
    ]
    assert "{{days}}" in data["publicNotice"]
    assert data["stop"].strip() and data["reviewNotice"].strip()


def test_flag_is_off_by_default_and_exposed_to_the_frontend(monkeypatch):
    from api.routers import system

    app = FastAPI()
    app.include_router(system.router)
    monkeypatch.delenv(FLAG, raising=False)
    assert TestClient(app).get("/api/config").json()["answer_share"] is False
    monkeypatch.setenv(FLAG, "true")
    assert TestClient(app).get("/api/config").json()["answer_share"] is True


def test_flag_is_registered_and_overridable_from_the_admin_console():
    from core import setting_overrides
    from core.feature_flags import FLAG_GROUPS, FLAG_REGISTRY

    assert FLAG in FLAG_REGISTRY and FLAG_REGISTRY[FLAG][1] == "off"
    assert FLAG in setting_overrides.OVERRIDABLE_FLAGS
    assert sum(FLAG in names for names in FLAG_GROUPS.values()) == 1


def test_router_is_registered_in_the_app():
    src = (REPO / "api_server.py").read_text(encoding="utf-8")
    assert "app.include_router(answer_share_router)" in src


def test_public_page_assets_exist_and_are_referenced_with_versions():
    html = (REPO / "web/answer-share.html").read_text(encoding="utf-8")
    assert "/static/answer-share.js?v=" in html
    assert (REPO / "web/answer-share.js").exists()


@pytest.mark.parametrize("loc", LOCALES)
def test_public_page_strings_exist_in_every_locale(loc):
    """公開頁讀 /static/js/i18n/<lang>.json 的 answerShare.page；英文內建當備援，所以 key 要跟 EN 字典對齊。"""
    js = (REPO / "web/answer-share.js").read_text(encoding="utf-8")
    en_block = js[js.index("var EN = {") : js.index("};", js.index("var EN = {"))]
    keys = set(re.findall(r"^\s{8}(\w+):", en_block, re.M))
    assert keys == {
        "title",
        "note",
        "question",
        "answer",
        "disclaimer",
        "cta",
        "expiry",
        "err",
    }
    page = json.loads((REPO / f"web/js/i18n/{loc}.json").read_text(encoding="utf-8"))[
        "answerShare"
    ]["page"]
    assert {k for k in keys if not page.get(k, "").strip()} == set()


@pytest.mark.parametrize("loc", LOCALES)
def test_public_disclaimer_says_not_endorsed_and_not_advice(loc):
    page = json.loads((REPO / f"web/js/i18n/{loc}.json").read_text(encoding="utf-8"))[
        "answerShare"
    ]["page"]
    text = page["disclaimer"].lower()
    assert any(
        w in text for w in ("未背書", "未背书", "not endorse", "не поручается")
    ), loc
    assert any(
        w in text
        for w in (
            "投資建議",
            "投资建议",
            "investment advice",
            "инвестиционная рекомендация",
        )
    ), loc


def test_public_page_script_is_safe_against_bad_urls_and_html():
    js = (REPO / "web/answer-share.js").read_text(encoding="utf-8")
    assert "try {\n        token = decodeURIComponent(" in js, (
        "畸形 percent-encoding 不能讓頁面空白"
    )
    assert (
        "innerHTML" not in js and "outerHTML" not in js and "document.write" not in js
    )
    assert "textContent" in js
