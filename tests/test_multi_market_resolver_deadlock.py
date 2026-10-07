"""Regression guard：add_preloaded_aliases 冷啟動 self-deadlock。

``_ALIASES_LOCK`` 是 threading.Lock（不可重入）。``add_preloaded_aliases`` 先
持鎖，再呼叫 ``_load_local_aliases``；後者在「還沒載過」時會自己再取一次同一把
鎖 → 同一條 thread 取兩次 → 永久 deadlock。

這段跑在 event loop thread 上（lifespan 的 ``asyncio.create_task(
preload_ticker_lists())``），所以每次冷啟動都會把整個 event loop 凍死：伺服器
連 /health 都不回，gunicorn heartbeat 停止 → timeout(180s) → SIGABRT → 重啟 →
再次 deadlock。線上「UP 約 30 秒 / DOWN 約 3 分鐘」的無限迴圈就是這樣來的。
"""

import threading

import pytest

from core.tools import multi_market_resolver as mmr


@pytest.fixture
def cold_start(monkeypatch):
    """模擬冷啟動：別名表還沒載過。"""
    monkeypatch.setattr(mmr, "_LOCAL_ALIASES", None)
    # 換一把乾淨的鎖，免得被別的測試留下的狀態干擾
    monkeypatch.setattr(mmr, "_ALIASES_LOCK", threading.Lock())


def _run_with_timeout(fn, timeout=10.0):
    """在獨立 thread 跑 fn；回傳 (是否完成, 結果)。deadlock 時回 (False, None)。"""
    box = {}

    def target():
        box["result"] = fn()

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    return (not t.is_alive()), box.get("result")


def test_add_preloaded_aliases_does_not_deadlock_on_cold_start(cold_start):
    """冷啟動時呼叫必須正常返回，不能卡住。"""
    completed, added = _run_with_timeout(
        lambda: mmr.add_preloaded_aliases(
            {"台積電測試別名": {"symbol": "2330", "market": "tw", "name": "台積電"}}
        )
    )

    assert completed, (
        "add_preloaded_aliases 冷啟動時 deadlock —— "
        "持有 _ALIASES_LOCK 的情況下又呼叫 _load_local_aliases（會再取同一把鎖）"
    )
    assert added == 1


def test_add_preloaded_aliases_merges_into_the_shared_table(cold_start):
    """加進去的別名要真的進到共用表，之後查得到。"""
    completed, _ = _run_with_timeout(
        lambda: mmr.add_preloaded_aliases(
            {"某個不存在的測試公司": {"symbol": "9999", "market": "tw", "name": "測試"}}
        )
    )
    assert completed

    table = mmr._load_local_aliases()
    assert table["某個不存在的測試公司"]["symbol"] == "9999"


def test_existing_aliases_are_not_overwritten(cold_start):
    """已存在的 key 不該被預載蓋掉（curated 表優先）。"""
    completed, _ = _run_with_timeout(
        lambda: mmr.add_preloaded_aliases(
            {"重複鍵測試": {"symbol": "1111", "market": "tw", "name": "先來的"}}
        )
    )
    assert completed

    completed2, added2 = _run_with_timeout(
        lambda: mmr.add_preloaded_aliases(
            {"重複鍵測試": {"symbol": "2222", "market": "tw", "name": "後來的"}}
        )
    )
    assert completed2
    assert added2 == 0
    assert mmr._load_local_aliases()["重複鍵測試"]["symbol"] == "1111"
