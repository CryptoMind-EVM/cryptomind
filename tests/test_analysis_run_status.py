"""分析執行狀態（run registry）與狀態查詢 endpoint。

L1 讓分析在斷線後繼續跑完並存檔，但使用者切回來只能乾等到全部完成。
L2 讓伺服器保留一份輸出快照，切回來就能看到「目前跑到哪」。

安全性是重點：run_id 是隨機字串，但狀態內容屬於特定使用者，
不可以讓別人拿 run_id 查到內容，也不可以讓別人試探 run_id 是否存在。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from api.routers import analysis

ANALYSIS_SOURCE = Path(analysis.__file__).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_runs():
    analysis._local_analysis_runs.clear()
    yield
    analysis._local_analysis_runs.clear()


def test_run_starts_in_running_state():
    run = analysis._create_analysis_run("sess-1", "user-1")
    assert run["status"] == "running"
    assert run["content"] == ""
    assert run["session_id"] == "sess-1"
    assert len(run["run_id"]) >= 16, "run_id 要夠長才不容易被猜到"


def test_load_run_hides_internal_fields():
    """回傳給呼叫端的快照不可帶內部欄位（如同步節流時間戳）。"""
    run = analysis._create_analysis_run("sess-1", "user-1")
    loaded = analysis._load_analysis_run(run["run_id"])
    assert loaded is not None
    assert not any(k.startswith("_") for k in loaded), loaded


def test_finish_run_records_terminal_state():
    run = analysis._create_analysis_run("sess-1", "user-1")
    analysis._finish_analysis_run(run, "error", error="boom")

    loaded = analysis._load_analysis_run(run["run_id"])
    assert loaded["status"] == "error"
    assert loaded["error"] == "boom"
    assert loaded["finished_at"] is not None


def test_local_run_registry_is_bounded():
    """記憶體中的 run 數量要有上限，長時間執行不可無限成長。"""
    for i in range(analysis._MAX_LOCAL_RUNS + 25):
        analysis._create_analysis_run(f"sess-{i}", "user-1")

    assert len(analysis._local_analysis_runs) <= analysis._MAX_LOCAL_RUNS


def test_sync_is_throttled(monkeypatch):
    """每個 token 都寫 Redis 會打爆它，必須節流。"""
    writes = []
    monkeypatch.setattr(
        analysis.shared_cache,
        "set_json",
        lambda key, value, ttl: writes.append(key),
    )

    run = analysis._create_analysis_run("sess-1", "user-1")  # force=True 寫一次
    baseline = len(writes)

    for _ in range(50):
        run["content"] += "token"
        analysis._sync_analysis_run(run)

    assert len(writes) == baseline, "節流失效：短時間內重複寫入共用快取"

    analysis._sync_analysis_run(run, force=True)
    assert len(writes) == baseline + 1, "force 應該要能強制寫入"


def test_status_endpoint_rejects_other_users():
    """接線檢查：狀態查詢必須比對 user_id，且找不到與無權一律 404。

    回 403 等於告訴對方「這個 run_id 存在」，會變成試探入口。
    """
    endpoint = re.search(
        r"async def get_analysis_run_status\(.*?\n    return \{",
        ANALYSIS_SOURCE,
        re.S,
    )
    assert endpoint, "找不到狀態查詢 endpoint —— 測試需要更新"

    body = endpoint.group(0)
    assert 'run.get("user_id") != current_user.get("user_id")' in body, (
        "狀態查詢沒有比對 user_id：別人拿到 run_id 就能讀走分析內容"
    )
    assert "status_code=404" in body, "無權存取應回 404，不可用 403 洩漏存在性"
    assert "Depends(get_current_user)" in body, "狀態查詢沒有要求登入"


def test_frontend_captures_and_uses_run_id():
    """前端接線：run_started 要存下 run_id，斷線後拿它查部分輸出。

    伺服器端已經在保留輸出快照，如果前端不接住 run_id，L2 等於白做 ——
    使用者切回來仍然只能乾等到分析全部完成。
    """
    source = (
        Path(__file__).resolve().parents[1] / "web" / "js" / "chat-analysis.js"
    ).read_text(encoding="utf-8")

    assert "'run_started'" in source, "前端沒有處理 run_started 事件"
    assert "activeRunId = data.run_id" in source, "沒有存下 run_id"
    assert "/api/analyze/status/" in source, "斷線後沒有查詢執行狀態"
    # L3 之後斷線改走 resumeAnalysisStream（EventSource 續傳），
    # 它在 EventSource 不可用時退回 watchForBackgroundResult 的輪詢。
    assert "runId: activeRunId" in source, "斷線處理沒有把 run_id 傳進去"


def test_detached_run_status_is_marked():
    """接線檢查：斷線交接時要把 run 標成 detached，客戶端才知道還在跑。"""
    # 截到交接背景收尾（_spawn_detached）為止：函式開頭有「已交接過就
    # return True」的冪等短路，不能停在第一個 return True
    generator = re.search(
        r"def detach_analysis\(reason: str\) -> bool:(.*?)_spawn_detached\(",
        ANALYSIS_SOURCE,
        re.S,
    )
    assert generator, "找不到 detach_analysis —— 測試需要更新"
    assert '"detached"' in generator.group(1), (
        "斷線時沒有標記 run 狀態，客戶端查詢會以為還在正常串流"
    )
