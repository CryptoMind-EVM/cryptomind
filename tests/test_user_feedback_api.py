from api.routers import user as user_router


async def _fake_run_sync(fn, *args):
    return fn(*args)


async def test_submit_user_feedback_success(client, monkeypatch):
    captured = {}

    def fake_save_user_feedback(user_id, username, message):
        captured["user_id"] = user_id
        captured["username"] = username
        captured["message"] = message

    monkeypatch.setattr(user_router, "run_sync", _fake_run_sync)
    monkeypatch.setattr(
        user_router,
        "get_user_by_id",
        lambda user_id: {"user_id": user_id, "username": "TestUser_001"},
    )
    monkeypatch.setattr(user_router, "save_user_feedback", fake_save_user_feedback)
    monkeypatch.setattr(user_router, "audit_log", lambda *args, **kwargs: None)

    response = await client.post(
        "/api/user-feedback",
        json={"message": "Great platform, please add more market filters."},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["user_id"] == "test-user-001"
    assert captured["message"] == "Great platform, please add more market filters."


async def test_submit_user_feedback_rejects_blank_message(client):
    response = await client.post("/api/user-feedback", json={"message": ""})

    assert response.status_code == 422


async def test_submit_user_feedback_creates_missing_user_before_save(
    client, monkeypatch
):
    captured = {}

    async def fake_run_sync(fn, *args):
        return fn(*args)

    def fake_get_user_by_id(user_id):
        captured["checked_user_id"] = user_id
        return None

    def fake_create_or_get_user(identity, username=None, auth_method="ton_wallet"):
        captured["created_identity"] = identity
        captured["created_username"] = username
        captured["created_auth_method"] = auth_method
        return {"user_id": identity, "username": username or "TestUser_001"}

    def fake_save_user_feedback(user_id, username, message):
        captured["saved_user_id"] = user_id
        captured["saved_username"] = username
        captured["saved_message"] = message

    monkeypatch.setattr(user_router, "run_sync", fake_run_sync)
    monkeypatch.setattr(user_router, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(user_router, "create_or_get_user", fake_create_or_get_user)
    monkeypatch.setattr(user_router, "save_user_feedback", fake_save_user_feedback)
    monkeypatch.setattr(user_router, "audit_log", lambda *args, **kwargs: None)

    response = await client.post(
        "/api/user-feedback",
        json={"message": "Need a test-mode user bootstrap before feedback save."},
    )

    assert response.status_code == 200
    assert captured["checked_user_id"] == "test-user-001"
    assert captured["created_identity"] == "test-user-001"
    assert captured["created_username"] == "TestUser_001"
    assert captured["created_auth_method"] == "dev_test"
    assert captured["saved_user_id"] == "test-user-001"
