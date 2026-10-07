"""worker 開機的 schema 等待——auto-deploy 同時滾 API 與 worker 的時序坑。

2026-09-05 線上實測：worker（03:16:31Z 起）比 API entrypoint 的遷移完成
（03:18:51Z）早 2 分鐘就緒，開機快照因此噴出過期的
``[SchemaDrift] db=c042 head=c044``。_wait_for_schema 讓 worker 等 db 版本
追上程式碼 head 再拍快照；等不到（遷移真的失敗）就照舊大聲說——那時
警告是真的。
"""

from __future__ import annotations

import pytest

import scripts.analysis_worker as worker

pytestmark = pytest.mark.unit


class TestWaitForSchema:
    def test_returns_immediately_when_in_sync(self, monkeypatch):
        """已同步＝零等待——一般重啟不得多付延遲。"""
        import core.feature_flags as ff

        monkeypatch.setattr(ff, "schema_version_pair", lambda: ("c044", "c044"))
        monkeypatch.setattr(
            worker.time,
            "sleep",
            lambda _s: (_ for _ in ()).throw(AssertionError("同步中不該 sleep")),
        )
        assert worker._wait_for_schema(timeout_s=10, interval_s=1) is True

    def test_times_out_loudly_when_never_matching(self, monkeypatch, caplog):
        """遷移真的失敗＝等滿 timeout 後照常啟動，但要大聲說出等了什麼。"""
        import core.feature_flags as ff

        monkeypatch.setattr(ff, "schema_version_pair", lambda: ("c042", "c044"))
        with caplog.at_level("WARNING", logger="analysis_worker"):
            ok = worker._wait_for_schema(timeout_s=0, interval_s=1)
        assert ok is False
        assert "c042" in caplog.text and "c044" in caplog.text

    def test_waits_until_schema_catches_up(self, monkeypatch):
        """API 的遷移晚幾分鐘收尾（線上實測 2 分鐘）——等到了就放行。"""
        import core.feature_flags as ff

        seq = [("c042", "c044"), ("c044", "c044")]
        monkeypatch.setattr(ff, "schema_version_pair", lambda: seq.pop(0))
        slept = []
        monkeypatch.setattr(worker.time, "sleep", slept.append)
        assert worker._wait_for_schema(timeout_s=60, interval_s=5) is True
        assert slept == [5], "第二次檢查前要照 interval 等一輪"

    def test_none_versions_keep_waiting_not_pass(self, monkeypatch):
        """head/db 讀不到（None）不是「同步」——繼續等，絕不放行假 ok。"""
        import core.feature_flags as ff

        seq = [(None, None), ("c044", "c044")]
        monkeypatch.setattr(ff, "schema_version_pair", lambda: seq.pop(0))
        monkeypatch.setattr(worker.time, "sleep", lambda _s: None)
        assert worker._wait_for_schema(timeout_s=60, interval_s=5) is True
