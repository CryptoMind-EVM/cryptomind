"""對話列表分頁與分類（2026-10-01）。行為在 tests/js/*.mjs（node 實跑），這裡包成 pytest。

以前社群列表與聊天側欄「對話歷史」都只抓 20 筆、沒有下一頁：第 21 個對話起從清單消失。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def _run(name: str) -> subprocess.CompletedProcess:
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    return subprocess.run(
        ["node", str(REPO / "tests" / "js" / f"{name}.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        cwd=str(REPO),
    )


def test_social_list_paging_and_filter_node_gate():
    """社群列表：一次 20、捲到底多 20、超過 100 分段抓、舊群組不插隊、全部／私訊／群組分類"""
    proc = _run("social_list_paging")
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "social_list_paging: ok" in proc.stderr


def test_chat_sessions_paging_node_gate():
    """聊天側欄對話歷史：一次 20、捲到底多 20、超過 100 分段抓、重畫不跳回頂端"""
    proc = _run("chat_sessions_paging")
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "chat_sessions_paging: ok" in proc.stderr


def test_chat_pins_node_gate():
    """社群置頂：置頂的照順序在最上面、2 個以上才有調整順序、排序模式只列置頂、分類裡排不動其他類的位置、上限提示"""
    proc = _run("chat_pins")
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "chat_pins: ok" in proc.stderr


def test_social_search_node_gate():
    """社群搜尋：標記關鍵字、打字停 300ms 才搜、三區結果、搜尋中不被重畫洗掉、點訊息跳到那則（手機帶 ?msg=）"""
    proc = _run("social_search")
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "social_search: ok" in proc.stderr


def test_chat_order_node_gate():
    """一般對話自訂順序：依最新訊息／自訂順序、order=custom 分頁、群組不插隊、兩區排序模式、拖其他對話自動改自訂"""
    proc = _run("chat_order")
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "chat_order: ok" in proc.stderr


def test_social_header_badge_and_refresh_node_gate():
    """社群標題列：沒有沒標示的「0」、好友邀請數掛在好友分頁鈕；重整要轉圈、鎖住、連按不重打"""
    proc = _run("social_header_refresh")
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "social_header_refresh: ok" in proc.stderr
