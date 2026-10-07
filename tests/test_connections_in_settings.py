"""Connections 入口併進 Settings（2026-09-12 盤點 §13）：既有引導卡加狀態徽章、模組、init hook、nav 隱藏、四語。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def test_settings_card_and_wiring():
    settings = (REPO / "web" / "js" / "components" / "tab-settings.js").read_text(
        encoding="utf-8"
    )
    for needle in (
        'id="settings-connections-card"',
        'id="settings-connections-telegram"',
        'id="settings-connections-line"',
        'data-click="GlobalNav.navigateToTab" data-click-arg="connections"',
    ):
        assert needle in settings, needle
    assert settings.count('id="settings-connections-card"') == 1, (
        "引導卡只能有一張（重複 id 會讓徽章填錯）"
    )
    spa = (REPO / "web" / "js" / "spa.js").read_text(encoding="utf-8")
    assert "import('./connections-settings.js')" in spa
    assert "window.loadConnectionsSummary()" in spa
    js = (REPO / "web" / "js" / "connections-settings.js").read_text(encoding="utf-8")
    assert "/api/telegram/status" in js and "/api/line/status" in js
    # 不在 Settings 重複注入 telegram-link／line-link 的 DOM（getElementById 只抓第一個）
    assert 'id="telegram-link-content"' not in settings


def test_connections_tab_hidden_but_reachable():
    nav = (REPO / "web" / "js" / "nav-config.js").read_text(encoding="utf-8")
    block = nav.split("id: 'connections',", 1)[1].split("},", 1)[0]
    assert "hidden: true" in block
    # 分頁本身還在（deep link／GlobalNav.navigateToTab 可到），引導卡才有地方去
    spa = (REPO / "web" / "js" / "spa.js").read_text(encoding="utf-8")
    assert "'connections'" in spa


@pytest.mark.parametrize("lang", ["en", "zh-TW", "zh-CN", "ru"])
def test_locales(lang):
    d = json.loads(
        (REPO / "web" / "js" / "i18n" / f"{lang}.json").read_text(encoding="utf-8")
    )["settings"]["connections"]
    for key in ("bound", "notBound", "loadFailed"):
        assert key in d, f"{lang} 缺 settings.connections.{key}"


def _web(*parts):
    return (REPO / "web" / "js" / Path(*parts)).read_text(encoding="utf-8")


def test_email_lives_on_connections_page():
    """2026-09-28：綁定集中在 Connections——Email 訂閱卡搬過去，Settings 早報只留「送到」勾選。"""
    settings = _web("components", "tab-settings.js")
    connections = _web("components", "tab-connections.js")
    for eid in (
        "brief-email-input",
        "brief-email-send",
        "brief-email-status",
        "brief-email-remove",
    ):
        assert f'id="{eid}"' in connections, eid
        assert f'id="{eid}"' not in settings, (
            f"{eid} 不該同時留在 Settings（getElementById 只抓第一個）"
        )
    assert 'id="connections-email-card"' in connections
    assert 'id="brief-channel-email-row"' in settings
    assert 'id="settings-connections-email"' in settings
    assert "loadEmailBrief()" in _web("connections.js")
    assert "/api/user/email-brief" in _web("connections-settings.js")
    service = (REPO / "core" / "email_brief" / "service.py").read_text(encoding="utf-8")
    assert 'manage_url=f"{base}/#connections"' in service, (
        "信裡的「Email 設定」要指到 Connections"
    )


def test_unbound_channels_link_to_connections():
    """早報「送到」沒綁的管道要能一鍵跳去綁；handler 必須在 Settings 首屏就載入（不能用 ConnectionsTab.*）。"""
    settings = _web("components", "tab-settings.js")
    for target in ("telegram", "email"):
        assert (
            f'data-click="SettingsConnections.open" data-click-arg="{target}"'
            in settings
        )
    assert "window.SettingsConnections = { open: openConnection }" in _web(
        "connections-settings.js"
    )
    delegator = _web("click-delegator.js")
    assert (
        "'SettingsConnections'"
        in delegator.split("allowedRoots = new Set([", 1)[1].split("])", 1)[0]
    )


def test_line_card_says_no_brief_and_google_unlink_confirms():
    assert 'data-i18n="line.noBrief"' in _web("components", "tab-connections.js")
    unlink = _web("google-auth.js").split("async unlink()", 1)[1].split("async ", 1)[0]
    assert "showConfirmDialog" in unlink, "解除 Google 綁定前要先確認"


@pytest.mark.parametrize("lang", ["en", "zh-TW", "zh-CN", "ru"])
def test_new_locale_keys(lang):
    d = json.loads(
        (REPO / "web" / "js" / "i18n" / f"{lang}.json").read_text(encoding="utf-8")
    )
    assert d["line"]["noBrief"] and d["connections"]["googleUnlinkConfirm"]
    assert (
        d["settings"]["brief"]["telegramLink"] and d["settings"]["brief"]["emailLink"]
    )
    for key in ("badgeActive", "badgePending", "badgeExpired", "badgeUnsubscribed"):
        assert d["settings"]["emailBrief"][key], f"{lang} 缺 settings.emailBrief.{key}"


def test_connections_split_by_purpose():
    """2026-09-28：Email（收早報的信箱）跟 Google（登入方式）分兩區，不再混成一串。"""
    conn = _web("components", "tab-connections.js")
    notify, signin = conn.split('id="connections-signin-section"', 1)
    for card in ("telegram", "email", "line"):
        assert f'id="connections-{card}-card"' in notify, (
            f"{card} 應在「收早報與通知」區"
        )
    assert 'id="connections-google-card"' in signin, "Google 應在「登入方式」區"
    assert 'data-i18n="connections.googleTitle"' in conn
    assert 'data-lucide="mail"' not in signin, "Google 不再用信封圖示（跟 Email 撞）"
    ga = _web("google-auth.js")
    assert "section.classList.toggle('hidden', !enabled)" in ga, (
        "伺服器沒開 Google 登入就整區藏起來"
    )
    notice = _web("legacy-ton-notice.js")
    assert "legacy-ton-link-google" in notice and "google_client_id" in notice
    index = (REPO / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="legacy-ton-link-google"' in index
