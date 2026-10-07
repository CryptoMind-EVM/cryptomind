import sqlite3

from api.routers import user as user_router
from core.database import user_feedback as user_feedback_db


async def _fake_run_sync(fn, *args):
    return fn(*args)


async def test_submit_user_feedback_persists_to_sqlite(client, monkeypatch, tmp_path):
    db_path = tmp_path / "feedback_test.sqlite3"

    monkeypatch.setenv("FEEDBACK_SQLITE_FALLBACK", "true")
    monkeypatch.setattr(user_router, "run_sync", _fake_run_sync)
    monkeypatch.setattr(user_router, "audit_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        user_router,
        "get_user_by_id",
        lambda user_id: {"user_id": user_id, "username": "TestUser_001"},
    )
    monkeypatch.setattr(user_feedback_db, "SQLITE_DB_PATH", db_path)
    monkeypatch.setattr(user_feedback_db, "get_database_url", lambda: None)

    response = await client.post(
        "/api/user-feedback",
        json={"message": "  SQLite feedback smoke test message.  "},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True

    verify_conn = sqlite3.connect(db_path)
    row = verify_conn.execute(
        'SELECT user_id, username, message FROM user_feedback'
    ).fetchone()
    verify_conn.close()

    assert row is not None
    assert row[0] == "test-user-001"
    assert row[1] == "TestUser_001"
    assert row[2] == "SQLite feedback smoke test message."


async def test_submit_user_feedback_renames_legacy_sqlite_table(
    client, monkeypatch, tmp_path
):
    db_path = tmp_path / "feedback_legacy.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE "user-massege" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            username TEXT,
            message TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("FEEDBACK_SQLITE_FALLBACK", "true")
    monkeypatch.setattr(user_router, "run_sync", _fake_run_sync)
    monkeypatch.setattr(user_router, "audit_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        user_router,
        "get_user_by_id",
        lambda user_id: {"user_id": user_id, "username": "TestUser_001"},
    )
    monkeypatch.setattr(user_feedback_db, "SQLITE_DB_PATH", db_path)
    monkeypatch.setattr(user_feedback_db, "get_database_url", lambda: None)

    response = await client.post(
        "/api/user-feedback",
        json={"message": "Legacy rename verification"},
    )

    assert response.status_code == 200

    verify_conn = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in verify_conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    row = verify_conn.execute(
        'SELECT message FROM user_feedback ORDER BY id DESC LIMIT 1'
    ).fetchone()
    verify_conn.close()

    assert "user_feedback" in tables
    assert "user-massege" not in tables
    assert row[0] == "Legacy rename verification"
