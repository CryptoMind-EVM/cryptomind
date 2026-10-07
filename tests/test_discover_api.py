"""Discover API 測試 — api/routers/discover.py（impl plan Task D3）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from core.tools.manifund_client import ManifundUnavailableError

USER = {"user_id": "u1", "username": "tester", "membership_tier": "free"}


@pytest.fixture
def client():
    from fastapi import FastAPI

    from api.routers import discover as dc_module

    app = FastAPI()
    app.include_router(dc_module.router)
    _override_user(app, USER)
    _override_session(app)

    with patch(
        "api.routers.discover.people_projects_discover_enabled",
        return_value=True,
    ):
        yield TestClient(app)


def _fake_favorite(**overrides):
    data = {
        "fav_id": "fav_1",
        "user_id": "u1",
        "item_type": "manifund_project",
        "item_id": "proj_42",
        "source_url": "https://manifund.org/projects/proj_42",
        "created_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _override_session(app):
    """測試環境無 DB：override session 依賴，避免 wait_for_db_ready 503。

    key 用 router 模組當下符號（免疫 suite 內 module-level importlib.reload
    造成的函式身份漂移，見 tests/e2e/test_settings.py:81）。
    """
    from api.routers import discover as dc_module

    async def fake_session():
        yield None

    app.dependency_overrides[dc_module.get_async_session] = fake_session


def _override_user(app, user):
    from api.routers import discover as dc_module

    async def fake_user():
        return user

    app.dependency_overrides[dc_module.get_current_user] = fake_user


class TestFeatureFlag:
    def test_disabled_returns_404(self):
        from fastapi import FastAPI

        from api.routers import discover as dc_module

        app = FastAPI()
        app.include_router(dc_module.router)
        _override_user(app, USER)
        _override_session(app)
        with patch(
            "api.routers.discover.people_projects_discover_enabled",
            return_value=False,
        ):
            resp = TestClient(app).get("/api/discover/favorites")
        assert resp.status_code == 404


class TestAuthRequired:
    def test_route_requires_authentication(self):
        """Pilot 限登入（決策 11）：auth 依賴拒絕時路由必須跟著 401。

        TEST_MODE 下無 token 會 fallback 成測試使用者（api/deps.py 設計），
        故此處以「拒絕的 auth override」驗證路由確實掛了 get_current_user
        ——若有人移除 Depends(get_current_user)，此測試會失敗。
        """
        from fastapi import FastAPI, HTTPException

        from api.routers import discover as dc_module

        app = FastAPI()
        app.include_router(dc_module.router)
        _override_session(app)

        async def denied_user():
            raise HTTPException(status_code=401, detail="Not authenticated")

        app.dependency_overrides[dc_module.get_current_user] = denied_user
        with patch(
            "api.routers.discover.people_projects_discover_enabled",
            return_value=True,
        ):
            resp = TestClient(app).get("/api/discover/search")
        assert resp.status_code == 401


class TestSearch:
    def test_search_returns_results(self, client):
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(return_value=[{"id": "proj_1", "title": "AI safety"}]),
        ):
            resp = client.get("/api/discover/search", params={"q": "AI safety"})
        assert resp.status_code == 200
        data = resp.json()
        # 聯邦搜尋契約（design 2026-08-17）：頂層 source=federated，
        # 逐筆 results 自帶 source；OC 關閉時 manifund 照常、OC 標 skipped。
        assert data["source"] == "federated"
        assert data["results"][0]["id"] == "proj_1"
        assert data["results"][0]["source"] == "manifund"
        assert data["sources_status"] == {"manifund": "ok", "opencollective": "skipped"}

    def test_search_unavailable_returns_503(self, client):
        """MCP 不可用 → 503 + 可理解降級訊息（design §10.10）。"""
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(side_effect=ManifundUnavailableError("down")),
        ):
            resp = client.get("/api/discover/search", params={"q": "x"})
        assert resp.status_code == 503
        assert "temporarily unavailable" in resp.json()["detail"]

    def test_search_empty_query_ok(self, client):
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(return_value=[]),
        ):
            resp = client.get("/api/discover/search")
        assert resp.status_code == 200


class TestProjectDetail:
    def test_detail(self, client):
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(return_value={"id": "proj_1", "title": "X"}),
        ):
            resp = client.get("/api/discover/projects/proj_1")
        assert resp.status_code == 200
        assert resp.json()["project"]["id"] == "proj_1"

    def test_detail_unavailable_503(self, client):
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(side_effect=ManifundUnavailableError("down")),
        ):
            resp = client.get("/api/discover/projects/proj_1")
        assert resp.status_code == 503


class TestConfig:
    def test_config_degrades_gracefully(self, client):
        """list_causes 失敗 → config 仍 200，causes=null（前端顯示降級橫幅）。"""
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(side_effect=ManifundUnavailableError("down")),
        ):
            resp = client.get("/api/discover/config")
        assert resp.status_code == 200
        assert resp.json()["causes"] is None

    def test_config_with_causes(self, client):
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(return_value={"causes": ["AI safety"]}),
        ):
            resp = client.get("/api/discover/config")
        assert resp.status_code == 200
        assert resp.json()["causes"] == {"causes": ["AI safety"]}


class TestFavorites:
    def test_list_favorites(self, client):
        with patch(
            "api.routers.discover.favorites_repo.list_favorites",
            new=AsyncMock(return_value=[_fake_favorite()]),
        ):
            resp = client.get("/api/discover/favorites")
        assert resp.status_code == 200
        favs = resp.json()["favorites"]
        assert favs[0]["item_id"] == "proj_42"

    def test_add_favorite(self, client):
        with patch(
            "api.routers.discover.favorites_repo.add_favorite",
            new=AsyncMock(return_value=_fake_favorite()),
        ) as mock_add:
            resp = client.post(
                "/api/discover/favorites",
                json={
                    "item_type": "manifund_project",
                    "item_id": "proj_42",
                    "source_url": "https://manifund.org/projects/proj_42",
                },
            )
        assert resp.status_code == 201
        mock_add.assert_awaited_once()
        assert resp.json()["favorite"]["fav_id"] == "fav_1"

    def test_add_favorite_invalid_type_400(self, client):
        resp = client.post(
            "/api/discover/favorites",
            json={"item_type": "random_thing", "item_id": "x"},
        )
        assert resp.status_code == 422  # Literal 驗證

    def test_remove_favorite(self, client):
        with patch(
            "api.routers.discover.favorites_repo.remove_favorite",
            new=AsyncMock(return_value=True),
        ):
            resp = client.delete("/api/discover/favorites/manifund_project/proj_42")
        assert resp.status_code == 200

    def test_remove_favorite_missing_404(self, client):
        with patch(
            "api.routers.discover.favorites_repo.remove_favorite",
            new=AsyncMock(return_value=False),
        ):
            resp = client.delete("/api/discover/favorites/manifund_project/proj_42")
        assert resp.status_code == 404


# ── 出資者旅程 P0-2：興趣推薦（design 2026-08-15）─────────────────────────────


@pytest.fixture(autouse=True)
def _clear_ttl_cache():
    from api.routers import discover as dc_module

    dc_module._ttl_cache.clear()
    yield
    dc_module._ttl_cache.clear()


class TestRecommend:
    def test_recommend_with_input_interests(self, client):
        """使用者輸入 interests → 原樣傳給 recommend_projects。"""
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(return_value={"projects": [{"id": "p1"}]}),
        ) as mock_tool:
            resp = client.get(
                "/api/discover/recommend", params={"interests": "AI safety"}
            )
        assert resp.status_code == 200
        args = mock_tool.await_args.args
        assert args[0] == "recommend_projects"
        assert args[1]["interests"] == "AI safety"
        data = resp.json()
        assert data["distill_source"] == "input"
        assert data["results"][0]["id"] == "p1"

    def test_recommend_distills_from_favorites(self, client):
        """無輸入 → 收藏 slug 蒸餾為 interests；無收藏則不帶 interests。"""
        with patch(
            "api.routers.discover.favorites_repo.list_favorites",
            new=AsyncMock(return_value=[
                _fake_favorite(item_id="ai-safety-video-series"),
                _fake_favorite(item_id="forecast-bench"),
            ]),
        ), patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(return_value={"projects": []}),
        ) as mock_tool:
            resp = client.get("/api/discover/recommend")
        assert resp.status_code == 200
        args = mock_tool.await_args.args
        assert "ai safety video series" in args[1]["interests"]
        assert "forecast bench" in args[1]["interests"]
        assert resp.json()["distill_source"] == "favorites"

    def test_recommend_no_interests_fallback_to_search(self, client):
        """無輸入＋無收藏＋recommend 失敗 → 退回最新專案（fail-soft）。"""
        calls = {"n": 0}

        async def fake_tool(name, args):
            calls["n"] += 1
            if name == "recommend_projects":
                raise ManifundUnavailableError("needs interests")
            return {"projects": [{"id": "recent_1"}]}

        with patch(
            "api.routers.discover.favorites_repo.list_favorites",
            new=AsyncMock(return_value=[]),
        ), patch(
            "api.routers.discover.call_manifund_tool", new=fake_tool
        ):
            resp = client.get("/api/discover/recommend")
        assert resp.status_code == 200
        data = resp.json()
        assert data["distill_source"] == "fallback_recent"
        assert data["results"][0]["id"] == "recent_1"

    def test_recommend_unavailable_with_interests_503(self, client):
        """有 interests 而 MCP 掛掉 → 503（不吞真實故障）。"""
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(side_effect=ManifundUnavailableError("down")),
        ):
            resp = client.get(
                "/api/discover/recommend", params={"interests": "x"}
            )
        assert resp.status_code == 503

    def test_recommend_cached_second_call(self, client):
        """同參數 5 分鐘內 → 快取命中（MCP 不再被呼叫）。"""
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(return_value={"projects": [{"id": "p1"}]}),
        ) as mock_tool:
            r1 = client.get(
                "/api/discover/recommend", params={"interests": "AI safety"}
            )
            r2 = client.get(
                "/api/discover/recommend", params={"interests": "AI safety"}
            )
        assert r1.status_code == 200 and r2.status_code == 200
        assert r2.json()["cached"] is True
        assert mock_tool.await_count == 1


# ── 出資者旅程 P0-3：AI 出資者視角評估（design 2026-08-15）───────────────────

_REVIEW_JSON = (
    '{"highlights": ["strong team"], "risks": ["unclear metrics"], '
    '"funding_outlook": {"summary": "on track", "confidence": "medium", '
    '"rationale": "votes and comments"}, "regrantor_questions": ["q1"], '
    '"similar_context": ["proj-x"]}'
)


def _fake_llm(content: str):
    class _Llm:
        async def ainvoke(self, _messages):
            return SimpleNamespace(content=content)

    return _Llm()


_CREDENTIALS = {
    "provider": "openrouter",
    "api_key": "test-key",
    "model": "test-model",
}


class TestAiReview:
    def _client_with_review_flag(self, enabled=True):
        from fastapi import FastAPI

        from api.routers import discover as dc_module

        app = FastAPI()
        app.include_router(dc_module.router)
        _override_user(app, USER)
        _override_session(app)
        patchers = [
            patch(
                "api.routers.discover.discover_ai_review_enabled",
                return_value=enabled,
            ),
            patch(
                "api.routers.discover.people_projects_discover_enabled",
                return_value=True,
            ),
        ]
        for p_ in patchers:
            p_.start()
        self._patchers = patchers
        return TestClient(app)

    def teardown_method(self):
        for p_ in getattr(self, "_patchers", []):
            p_.stop()

    def test_ai_review_success(self):
        client = self._client_with_review_flag(True)
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(
                side_effect=[
                    {"id": "proj_1", "title": "X"},
                    {"comments": [{"body": "q?"}]},
                ]
            ),
        ), patch(
            "api.routers.discover.resolve_user_llm_credentials",
            new=AsyncMock(return_value=_CREDENTIALS),
        ), patch(
            "api.routers.discover.create_user_llm_client",
            return_value=_fake_llm(_REVIEW_JSON),
        ):
            resp = client.post("/api/discover/projects/proj_1/ai-review")
        assert resp.status_code == 200
        data = resp.json()
        review = data["review"]
        assert review["highlights"] == ["strong team"]
        assert review["funding_outlook"]["confidence"] == "medium"
        assert "regrantor_questions" in review
        assert data["disclaimer"]
        assert data["language"] == "zh-TW"

    def test_ai_review_platform_model_is_shared_across_users(self):
        """平台免費模型的評估全站共用：第二個人不再叫模型。"""
        from api.routers import discover as dc_module

        dc_module._ttl_cache.clear()
        platform = {"provider": "local_llama", "api_key": "local", "model": "cryptomind-lite"}
        llm_calls = []

        def _factory(**kw):
            llm_calls.append(kw)
            return _fake_llm(_REVIEW_JSON)

        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(
                side_effect=[{"id": "proj_s", "title": "S"}, {"comments": []}] * 2
            ),
        ), patch(
            "api.routers.discover.resolve_user_llm_credentials",
            new=AsyncMock(return_value=platform),
        ), patch("api.routers.discover.create_user_llm_client", side_effect=_factory):
            client = self._client_with_review_flag(True)
            first = client.post("/api/discover/projects/proj_s/ai-review")
            app2_user = {**USER, "user_id": "someone_else"}
            from fastapi import FastAPI

            app2 = FastAPI()
            app2.include_router(dc_module.router)
            _override_user(app2, app2_user)
            _override_session(app2)
            second = TestClient(app2).post("/api/discover/projects/proj_s/ai-review")
        assert first.status_code == 200 and second.status_code == 200
        assert second.json().get("cached") is True
        assert len(llm_calls) == 1
        dc_module._ttl_cache.clear()

    def test_ai_review_flag_off_404(self):
        client = self._client_with_review_flag(False)
        resp = client.post("/api/discover/projects/p/ai-review")
        assert resp.status_code == 404

    def test_ai_review_no_llm_key_503(self):
        client = self._client_with_review_flag(True)
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(side_effect=[{"id": "p"}, {"comments": []}]),
        ), patch(
            "api.routers.discover.resolve_user_llm_credentials",
            new=AsyncMock(return_value=None),
        ):
            resp = client.post("/api/discover/projects/p/ai-review")
        assert resp.status_code == 503

    def test_ai_review_manifund_down_503(self):
        client = self._client_with_review_flag(True)
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(side_effect=ManifundUnavailableError("down")),
        ), patch(
            # 2026-09-28：先查模型（平台模型的評估全站共用），再取 Manifund 資料
            "api.routers.discover.resolve_user_llm_credentials",
            new=AsyncMock(return_value=_CREDENTIALS),
        ):
            resp = client.post("/api/discover/projects/p/ai-review")
        assert resp.status_code == 503

    def test_ai_review_unparseable_output_503(self):
        client = self._client_with_review_flag(True)
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(side_effect=[{"id": "p"}, {"comments": []}]),
        ), patch(
            "api.routers.discover.resolve_user_llm_credentials",
            new=AsyncMock(return_value=_CREDENTIALS),
        ), patch(
            "api.routers.discover.create_user_llm_client",
            return_value=_fake_llm("this is not json"),
        ):
            resp = client.post("/api/discover/projects/p/ai-review")
        assert resp.status_code == 503

    def test_ai_review_wraps_untrusted_content(self):
        """硬邊界 #4：專案/留言資料必須包入 EXTERNAL_UNTRUSTED_CONTENT。"""
        from core.tools.external_content import EXTERNAL_UNTRUSTED_START

        client = self._client_with_review_flag(True)
        captured = {}

        class _CaptureLlm:
            async def ainvoke(self, messages):
                captured["user_msg"] = str(messages[-1].content)
                return SimpleNamespace(content=_REVIEW_JSON)

        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(side_effect=[{"id": "p"}, {"comments": []}]),
        ), patch(
            "api.routers.discover.resolve_user_llm_credentials",
            new=AsyncMock(return_value=_CREDENTIALS),
        ), patch(
            "api.routers.discover.create_user_llm_client",
            return_value=_CaptureLlm(),
        ):
            resp = client.post("/api/discover/projects/p/ai-review")
        assert resp.status_code == 200
        assert captured["user_msg"].count(EXTERNAL_UNTRUSTED_START) == 2

    def test_ai_review_cached(self):
        client = self._client_with_review_flag(True)
        llm = _fake_llm(_REVIEW_JSON)
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(side_effect=[{"id": "p"}, {"comments": []}]),
        ), patch(
            "api.routers.discover.resolve_user_llm_credentials",
            new=AsyncMock(return_value=_CREDENTIALS),
        ), patch(
            "api.routers.discover.create_user_llm_client", return_value=llm
        ):
            r1 = client.post("/api/discover/projects/p/ai-review")
            r2 = client.post("/api/discover/projects/p/ai-review")
        assert r1.status_code == 200
        assert r2.json()["cached"] is True

    @pytest.mark.parametrize(
        "language,expected",
        [
            ("en", "en"),
            ("en-US", "en"),
            ("zh-CN", "zh-CN"),
            ("zh-Hans", "zh-CN"),
            ("ru-RU", "ru"),
            ("fr", "zh-TW"),
        ],
    )
    def test_ai_review_language_normalization(self, language, expected):
        from api.routers.discover import _normalize_lang

        assert _normalize_lang(language) == expected


# ── 深掘問答（design 2026-08-17 §1）───────────────────────────────────────────


class TestProjectAsk:
    def _ask_client(self):
        from fastapi import FastAPI

        from api.routers import discover as dc_module

        app = FastAPI()
        app.include_router(dc_module.router)
        _override_user(app, USER)
        _override_session(app)
        patcher = patch("api.routers.discover.discover_ai_review_enabled", return_value=True)
        patcher2 = patch("api.routers.discover.people_projects_discover_enabled", return_value=True)
        patcher.start()
        patcher2.start()
        self._patchers = [patcher, patcher2]
        return TestClient(app)

    def teardown_method(self):
        for p in getattr(self, "_patchers", []):
            p.stop()

    def test_ask_success_wraps_untrusted(self):
        from core.tools.external_content import EXTERNAL_UNTRUSTED_START

        captured = {}

        class _CaptureLlm:
            async def ainvoke(self, messages):
                captured["user_msg"] = str(messages[-1].content)
                return SimpleNamespace(content="Based on the comments, the team...")

        client = self._ask_client()
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(side_effect=[{"id": "p"}, {"comments": []}]),
        ), patch(
            "api.routers.discover.resolve_user_llm_credentials",
            new=AsyncMock(return_value={"provider": "x", "api_key": "k", "model": "m"}),
        ), patch(
            "api.routers.discover.create_user_llm_client", return_value=_CaptureLlm()
        ):
            resp = client.post(
                "/api/discover/projects/p/ask",
                json={"question": "What is the team track record?", "history": []},
            )
        assert resp.status_code == 200
        assert "team" in resp.json()["answer"]
        assert captured["user_msg"].count(EXTERNAL_UNTRUSTED_START) == 2  # project + comments

    def test_ask_history_sanitized(self):
        """history 非法項（role 不明/空內容）被清；多於 6 輪截尾。"""

        class _Llm:
            async def ainvoke(self, messages):
                return SimpleNamespace(content="ok")

        client = self._ask_client()
        history = [{"role": "bogus", "content": "x"}, {"role": "user", "content": ""}] + [
            {"role": "user", "content": f"q{i}"} for i in range(8)
        ]
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(side_effect=[{"id": "p"}, {"comments": []}]),
        ), patch(
            "api.routers.discover.resolve_user_llm_credentials",
            new=AsyncMock(return_value={"provider": "x", "api_key": "k", "model": "m"}),
        ), patch(
            "api.routers.discover.create_user_llm_client", return_value=_Llm()
        ):
            resp = client.post(
                "/api/discover/projects/p/ask",
                json={"question": "final", "history": history},
            )
        assert resp.status_code == 200  # sanitize 不致 422

    def test_ask_no_key_503(self):
        client = self._ask_client()
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(side_effect=[{"id": "p"}, {"comments": []}]),
        ), patch(
            "api.routers.discover.resolve_user_llm_credentials",
            new=AsyncMock(return_value=None),
        ):
            resp = client.post("/api/discover/projects/p/ask", json={"question": "q"})
        assert resp.status_code == 503

    def test_ask_requires_question(self):
        client = self._ask_client()
        resp = client.post("/api/discover/projects/p/ask", json={"question": ""})
        assert resp.status_code == 422


# ── 前瞻評測記錄＋記分板（design 2026-08-17 §3）─────────────────────────────


class TestProspectLog:
    def test_scoreboard_requires_admin(self):
        from fastapi import FastAPI

        from api.routers import discover as dc_module

        app = FastAPI()
        app.include_router(dc_module.router)
        _override_user(app, USER)  # role=user
        _override_session(app)
        resp = TestClient(app).get("/api/admin/prospect-logs")
        assert resp.status_code == 403

    def test_scoreboard_admin_ok(self):
        from fastapi import FastAPI

        from api.routers import discover as dc_module

        app = FastAPI()
        app.include_router(dc_module.router)
        _override_user(app, {"user_id": "admin1", "role": "admin"})
        _override_session(app)

        class _FakeLog:
            log_id = "pl_1"
            project_slug = "demo-slug"
            cause = "ai-safety"
            stage_at_eval = "proposal"
            evaluation_json = {"funding_outlook": {"confidence": "medium", "summary": "s"}}
            content_snapshot = {"total_raised": 100, "funding_goal": 1000, "title": "T"}
            model = "m1"
            created_at = None

        class _ExecResult:
            def scalars(self):
                return self

            def all(self):
                return [_FakeLog()]

        class _FakeSession:
            async def execute(self, _stmt):
                return _ExecResult()

        async def fs():
            yield _FakeSession()

        app.dependency_overrides[dc_module.get_async_session] = fs
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(return_value={"total_raised": 500, "stage": "active"}),
        ):
            resp = TestClient(app).get("/api/admin/prospect-logs")
        assert resp.status_code == 200
        row = resp.json()["logs"][0]
        assert row["predicted_confidence"] == "medium"
        assert row["current_raised"] == 500
        assert row["raised_at_eval"] == 100


# ── 補助機會表（design 2026-08-17-discover-multisource §Phase 0）────────────


class TestOpportunities:
    def test_opportunities_returns_open_items(self, client):
        """機會表列出 status=open 的項目，每項帶 platform/url 欄位。"""
        with patch(
            "api.routers.discover._load_funding_opportunities",
            return_value={
                "opportunities": [
                    {"id": "a", "name": "Fund A", "platform": "X",
                     "url": "https://x.example", "status": "open",
                     "tags": ["ai-safety"], "cycle": "rolling"},
                    {"id": "b", "name": "Fund B", "platform": "Y",
                     "url": "https://y.example", "status": "closed",
                     "tags": [], "cycle": "annual"},
                ]
            },
        ):
            resp = client.get("/api/discover/opportunities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["source"] == "curated"
        ids = [o["id"] for o in data["results"]]
        assert "a" in ids and "b" not in ids

    def test_opportunities_filters_expired_deadlines(self, client):
        """deadline 已過 → 自動 closed（路由層過濾，不改檔）。"""
        with patch(
            "api.routers.discover._load_funding_opportunities",
            return_value={
                "opportunities": [
                    {"id": "past", "name": "Past", "platform": "X",
                     "url": "https://x", "status": "open",
                     "deadline": "2020-01-01", "tags": [], "cycle": "annual"},
                    {"id": "future", "name": "Future", "platform": "X",
                     "url": "https://x", "status": "open",
                     "deadline": "2099-12-31", "tags": [], "cycle": "annual"},
                    {"id": "nodl", "name": "NoDeadline", "platform": "X",
                     "url": "https://x", "status": "open",
                     "deadline": None, "tags": [], "cycle": "rolling"},
                ]
            },
        ):
            resp = client.get("/api/discover/opportunities")
        ids = [o["id"] for o in resp.json()["results"]]
        assert "past" not in ids
        assert "future" in ids
        assert "nodl" in ids

    def test_opportunities_include_closed_when_requested(self, client):
        with patch(
            "api.routers.discover._load_funding_opportunities",
            return_value={
                "opportunities": [
                    {"id": "b", "name": "Fund B", "platform": "Y",
                     "url": "https://y", "status": "closed",
                     "tags": [], "cycle": "annual"},
                ]
            },
        ):
            resp = client.get("/api/discover/opportunities", params={"include_closed": "true"})
        assert [o["id"] for o in resp.json()["results"]] == ["b"]

    def test_opportunities_missing_file_degrades_to_empty(self, client):
        """JSON 檔缺失/損壞 → 空表 200（fail-soft，不炸 500）。"""
        with patch(
            "api.routers.discover._load_funding_opportunities",
            return_value={"opportunities": []},
        ):
            resp = client.get("/api/discover/opportunities")
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_opportunities_search_source_field(self, client):
        """搜尋回應每筆結果加 source 欄位（Phase 0 多來源正規化）。"""
        with patch(
            "api.routers.discover.call_manifund_tool",
            new=AsyncMock(return_value=[{"id": "proj_1", "title": "AI safety"}]),
        ):
            resp = client.get("/api/discover/search", params={"q": "AI"})
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert results and results[0].get("source") == "manifund"


# ── 聯邦搜尋（design 2026-08-17-discover-multisource §Phase 1）──────────────


class TestFederatedSearch:
    def _client_with_oc(self, oc_on: bool):
        from fastapi import FastAPI

        from api.routers import discover as dc_module

        app = FastAPI()
        app.include_router(dc_module.router)
        _override_user(app, USER)
        _override_session(app)
        patcher = patch(
            "api.routers.discover.discover_oc_enabled", return_value=oc_on
        )
        patcher2 = patch(
            "api.routers.discover.people_projects_discover_enabled",
            return_value=True,
        )
        patcher.start()
        patcher2.start()
        return app, patcher, patcher2

    def test_oc_off_returns_manifund_only(self):
        from fastapi.testclient import TestClient

        from core.tools.opencollective_client import OCUnavailableError

        app, patcher, patcher2 = self._client_with_oc(oc_on=False)
        try:
            oc_spy = AsyncMock(side_effect=OCUnavailableError("should not be called"))
            with patch(
                "api.routers.discover.call_manifund_tool",
                new=AsyncMock(return_value=[{"id": "p1", "title": "T"}]),
            ), patch("api.routers.discover.search_collectives", new=oc_spy):
                resp = TestClient(app).get("/api/discover/search", params={"q": "x"})
        finally:
            patcher.stop()
            patcher2.stop()
        assert resp.status_code == 200
        data = resp.json()
        assert [r["id"] for r in data["results"]] == ["p1"]
        oc_spy.assert_not_awaited()
        assert data["sources_status"]["manifund"] == "ok"
        assert data["sources_status"]["opencollective"] == "skipped"

    def test_oc_on_merges_both_sources(self):
        from fastapi.testclient import TestClient

        app, patcher, patcher2 = self._client_with_oc(oc_on=True)
        try:
            with patch(
                "api.routers.discover.call_manifund_tool",
                new=AsyncMock(return_value=[{"id": "p1", "title": "Manifund T"}]),
            ), patch(
                "api.routers.discover.search_collectives",
                new=AsyncMock(
                    return_value=[{"id": "oc1", "title": "OC T", "source": "oc"}]
                ),
            ):
                resp = TestClient(app).get("/api/discover/search", params={"q": "x"})
        finally:
            patcher.stop()
            patcher2.stop()
        assert resp.status_code == 200
        data = resp.json()
        ids = {r["id"]: r.get("source") for r in data["results"]}
        assert "p1" in ids and "oc1" in ids
        assert ids["p1"] == "manifund" and ids["oc1"] == "oc"
        assert data["sources_status"] == {"manifund": "ok", "opencollective": "ok"}

    def test_oc_down_manifund_still_returns(self):
        """一邊掛掉另一邊照樣回（design §Phase 1 硬需求）。"""
        from fastapi.testclient import TestClient

        from core.tools.opencollective_client import OCUnavailableError

        app, patcher, patcher2 = self._client_with_oc(oc_on=True)
        try:
            with patch(
                "api.routers.discover.call_manifund_tool",
                new=AsyncMock(return_value=[{"id": "p1", "title": "T"}]),
            ), patch(
                "api.routers.discover.search_collectives",
                new=AsyncMock(side_effect=OCUnavailableError("rate limited")),
            ):
                resp = TestClient(app).get("/api/discover/search", params={"q": "x"})
        finally:
            patcher.stop()
            patcher2.stop()
        assert resp.status_code == 200
        data = resp.json()
        assert [r["id"] for r in data["results"]] == ["p1"]
        assert data["sources_status"]["opencollective"] == "error"

    def test_all_sources_down_returns_503(self):
        from fastapi.testclient import TestClient

        from core.tools.opencollective_client import OCUnavailableError

        app, patcher, patcher2 = self._client_with_oc(oc_on=True)
        try:
            with patch(
                "api.routers.discover.call_manifund_tool",
                new=AsyncMock(side_effect=ManifundUnavailableError("down")),
            ), patch(
                "api.routers.discover.search_collectives",
                new=AsyncMock(side_effect=OCUnavailableError("down")),
            ):
                resp = TestClient(app).get("/api/discover/search", params={"q": "x"})
        finally:
            patcher.stop()
            patcher2.stop()
        assert resp.status_code == 503

    def test_favorite_accepts_oc_collective(self):
        """item_type 擴充 oc_collective（Phase 1 migration 後）。"""
        from fastapi import FastAPI

        from api.routers import discover as dc_module

        app = FastAPI()
        app.include_router(dc_module.router)
        _override_user(app, USER)
        _override_session(app)
        with patch(
            "api.routers.discover.people_projects_discover_enabled",
            return_value=True,
        ), patch(
            "api.routers.discover.favorites_repo.add_favorite",
            new=AsyncMock(return_value=_fake_favorite(
                item_type="oc_collective", item_id="venten"
            )),
        ):
            resp = TestClient(app).post(
                "/api/discover/favorites",
                json={"item_type": "oc_collective", "item_id": "venten"},
            )
        assert resp.status_code == 201
