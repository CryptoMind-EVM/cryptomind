"""群組聊天前端共用（Part C1）。行為在 tests/js/group_chat.mjs（node 實跑），這裡包成 pytest。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def test_group_chat_node_gate():
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "group_chat.mjs")],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "group_chat: ok" in proc.stderr


def test_social_groups_node_gate():
    """好友頁接上群組（Part C2）：列表混排、開關關著、WS 事件、送出走群組 API、切回私訊"""
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "social_groups.mjs")],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "social_groups: ok" in proc.stderr


def test_group_notifications_node_gate():
    """通知中心的群組通知（Part C3）：文字、正看著不跳 toast、鈴鐺接受／拒絕、點了打開群組"""
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "group_notifications.mjs")],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "group_notifications: ok" in proc.stderr


def test_group_info_panel_member_actions_node_gate():
    """群組資訊面板：群主在其他成員旁有「轉讓群主」＋「移除」，一般成員沒有"""
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "group_info_panel.mjs")],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "group_info_panel: ok" in proc.stderr


def test_group_mentions_node_gate():
    """@提及：訊息裡的提及樣式（@ 到我另外標）、列表「有人提及你」、輸入框選單的查詢／候選／插入"""
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "group_mentions.mjs")],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "group_mentions: ok" in proc.stderr


def test_lucide_guard_node_gate():
    """lucide 防整頁重畫：畫好的不重畫、換名字照換、{ nodes } 當範圍（2026-10-02 切回視窗會閃）"""
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "lucide_guard.mjs")],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "lucide_guard: ok" in proc.stderr
