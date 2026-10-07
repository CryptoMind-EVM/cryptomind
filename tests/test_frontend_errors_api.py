"""前端錯誤上報 — 未登入也要收（2026-09-24）。

登入前的錯誤（例如 Base App 簽署失敗）只有這條路回報；以前要求登入，
一律 401 丟掉，事後查不到原因。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def _client():
    from api.routers import frontend_errors

    frontend_errors._ERROR_BUFFER.clear()
    app = FastAPI()
    app.include_router(frontend_errors.router)
    return TestClient(app), frontend_errors


def _err(**kw):
    return {"id": "e1", "message": "boom", "context": "unhandledrejection", **kw}


def test_anonymous_report_accepted(monkeypatch, caplog):
    client, mod = _client()
    monkeypatch.setattr(mod, "audit_log", lambda *a, **k: None)
    with caplog.at_level("WARNING"):
        resp = client.post("/api/frontend-errors", json={"errors": [_err()]})
    assert resp.status_code == 200
    assert resp.json() == {"received": 1}
    assert mod._ERROR_BUFFER[-1]["user_id"] == "anonymous"
    assert "user=anonymous" in caplog.text
    assert "first=boom" in caplog.text


def test_log_line_cannot_be_split(monkeypatch, caplog):
    """匿名端點：訊息裡的換行不得在 log 裡長出假的一行。"""
    client, mod = _client()
    monkeypatch.setattr(mod, "audit_log", lambda *a, **k: None)
    with caplog.at_level("WARNING"):
        client.post(
            "/api/frontend-errors",
            json={"errors": [_err(message="a\n2026-01-01 [INFO] fake\r\nb")]},
        )
    line = [
        r.getMessage() for r in caplog.records if "[FrontendError]" in r.getMessage()
    ]
    assert line and "\n" not in line[0] and "\r" not in line[0]


def test_empty_batch_ok(monkeypatch):
    client, mod = _client()
    monkeypatch.setattr(mod, "audit_log", lambda *a, **k: None)
    resp = client.post("/api/frontend-errors", json={"errors": []})
    assert resp.status_code == 200
    assert resp.json() == {"received": 0}
