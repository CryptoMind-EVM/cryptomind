"""Studio API 測試 — api/routers/studio.py（design 2026-08-16）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

USER = {"user_id": "u1", "username": "tester", "membership_tier": "free"}


def _override_session(app):

    from api.routers import studio as st_module

    class _FakeSession:
        def __init__(self):
            self.added = []
            self._store = {}

        def add(self, obj):
            self.added.append(obj)
            pk = getattr(obj, "exchange_id", None) or getattr(obj, "outcome_id", None)
            if pk:
                self._store[pk] = obj

        async def commit(self):
            return None

        async def get(self, model, pk):
            return self._store.get(pk)

        async def execute(self, _stmt):
            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return []

            return _R()

    async def fake_session():
        yield _FakeSession()

    app.dependency_overrides[st_module.get_async_session] = fake_session


def _override_user(app, user):
    from api.routers import studio as st_module

    async def fake_user():
        return user

    app.dependency_overrides[st_module.get_current_user] = fake_user


def _client(flag=True):
    from fastapi import FastAPI

    from api.routers import studio as st_module

    app = FastAPI()
    app.include_router(st_module.router)
    _override_user(app, USER)
    _override_session(app)
    patcher = patch(
        "api.routers.studio.proposal_studio_enabled", return_value=flag
    )
    patcher.start()
    try:
        yield TestClient(app)
    finally:
        patcher.stop()


def _fake_draft(**overrides):
    data = {
        "draft_id": "pd_1",
        "user_id": "u1",
        "title": "AI safety videos",
        "cause": "ai-safety",
        "target_usd": None,
        "status": "draft",
        "current_version_no": 2,
        "share_token": None,
        "updated_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _fake_version(**overrides):
    data = {
        "version_id": "dv_1",
        "draft_id": "pd_1",
        "version_no": 2,
        "content_md": "new content",
        "change_summary": "manual",
        "source": "human",
        "char_delta": 4,
        "typed_chars": 0,
        "paste_events": 0,
        "pasted_chars": 0,
        "edit_seconds": 0,
        "created_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class TestFeatureFlagAndAuth:
    def test_flag_off_404(self):
        for client in _client(flag=False):
            assert client.get("/api/studio/drafts").status_code == 404

    def test_auth_required_401(self):
        from fastapi import FastAPI, HTTPException

        from api.routers import studio as st_module

        app = FastAPI()
        app.include_router(st_module.router)
        _override_session(app)

        async def denied_user():
            raise HTTPException(status_code=401, detail="Not authenticated")

        app.dependency_overrides[st_module.get_current_user] = denied_user
        with patch("api.routers.studio.proposal_studio_enabled", return_value=True):
            resp = TestClient(app).get("/api/studio/drafts")
        assert resp.status_code == 401


class TestDrafts:
    def test_list_drafts(self):
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.list_drafts",
                new=AsyncMock(return_value=[_fake_draft()]),
            ):
                resp = client.get("/api/studio/drafts")
        assert resp.status_code == 200
        assert resp.json()["drafts"][0]["draft_id"] == "pd_1"

    def test_create_draft(self):
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.create_draft",
                new=AsyncMock(return_value=_fake_draft(status="draft", current_version_no=1)),
            ) as mock_create:
                resp = client.post(
                    "/api/studio/drafts",
                    json={"title": "AI safety videos", "cause": "ai-safety"},
                )
        assert resp.status_code == 201
        args = mock_create.await_args.kwargs
        assert args["title"] == "AI safety videos"
        assert resp.json()["draft"]["draft_id"] == "pd_1"

    def test_create_draft_quota_409(self):
        from core.orm.studio_repo import QuotaExceeded

        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.create_draft",
                new=AsyncMock(side_effect=QuotaExceeded("limit")),
            ):
                resp = client.post("/api/studio/drafts", json={"title": "x"})
        assert resp.status_code == 409

    def test_other_users_draft_404(self):
        """ownership：他人草稿與不存在同樣 404（不洩漏存在性）。"""
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft(user_id="someone_else")),
            ):
                resp = client.get("/api/studio/drafts/pd_1")
        assert resp.status_code == 404

    def test_update_draft_invalid_status_400(self):
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft()),
            ):
                resp = client.patch(
                    "/api/studio/drafts/pd_1", json={"status": "bogus"}
                )
        assert resp.status_code == 400

    def test_delete_draft(self):
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft()),
            ), patch(
                "api.routers.studio.studio_repo.delete_draft", new=AsyncMock()
            ) as mock_del:
                resp = client.delete("/api/studio/drafts/pd_1")
        assert resp.status_code == 200
        mock_del.assert_awaited_once()


class TestVersions:
    def test_add_version(self):
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft()),
            ), patch(
                "api.routers.studio.studio_repo.add_version",
                new=AsyncMock(return_value=_fake_version()),
            ) as mock_add:
                resp = client.post(
                    "/api/studio/drafts/pd_1/versions",
                    json={"content_md": "new content", "source": "human"},
                )
        assert resp.status_code == 200
        assert resp.json()["version"]["source"] == "human"
        assert mock_add.await_args.kwargs["source"] == "human"

    def test_add_version_invalid_source_400(self):
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft()),
            ):
                resp = client.post(
                    "/api/studio/drafts/pd_1/versions",
                    json={"content_md": "x", "source": "bogus"},
                )
        assert resp.status_code == 400

    def test_list_versions_excludes_content(self):
        """軌跡列表不回全文（設計：列表只有中繼資料）。"""
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft()),
            ), patch(
                "api.routers.studio.studio_repo.list_versions",
                new=AsyncMock(
                    return_value=[
                        SimpleNamespace(
                            version_no=2,
                            change_summary="manual",
                            source="human",
                            char_delta=4,
                            typed_chars=100,
                            paste_events=0,
                            pasted_chars=0,
                            edit_seconds=600,
                            created_at=None,
                        )
                    ]
                ),
            ):
                resp = client.get("/api/studio/drafts/pd_1/versions")
        assert resp.status_code == 200
        body = resp.json()["versions"][0]
        assert "content_md" not in body
        assert body["version_no"] == 2

    def test_diff_versions(self):
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft()),
            ), patch(
                "api.routers.studio.studio_repo.get_version",
                new=AsyncMock(
                    side_effect=[
                        _fake_version(version_no=1, content_md="old line\nsame"),
                        _fake_version(version_no=2, content_md="new line\nsame"),
                    ]
                ),
            ):
                resp = client.get("/api/studio/drafts/pd_1/diff?a=1&b=2")
        assert resp.status_code == 200
        assert "-old line" in resp.json()["diff"]
        assert "+new line" in resp.json()["diff"]

    def test_diff_missing_version_404(self):
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft()),
            ), patch(
                "api.routers.studio.studio_repo.get_version",
                new=AsyncMock(return_value=None),
            ):
                resp = client.get("/api/studio/drafts/pd_1/diff?a=1&b=2")
        assert resp.status_code == 404

    def test_get_single_version(self):
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft()),
            ), patch(
                "api.routers.studio.studio_repo.get_version",
                new=AsyncMock(return_value=_fake_version(content_md="full text")),
            ):
                resp = client.get("/api/studio/drafts/pd_1/versions/2")
        assert resp.status_code == 200
        assert resp.json()["content_md"] == "full text"


class TestExport:
    def test_export_returns_markdown_and_marks_exported(self):
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft()),
            ), patch(
                "api.routers.studio.studio_repo.get_current_version",
                new=AsyncMock(return_value=_fake_version(content_md="body")),
            ), patch(
                "api.routers.studio.studio_repo.update_draft_meta",
                new=AsyncMock(return_value=_fake_draft(status="exported")),
            ):
                resp = client.post("/api/studio/drafts/pd_1/export")
        assert resp.status_code == 200
        data = resp.json()
        assert "# AI safety videos" in data["markdown"]
        assert "body" in data["markdown"]
        assert data["submit_url"].startswith("https://manifund.org")


_COACH_JSON = (
    '{"clarifying_questions": ["budget basis?"], '
    '"suggestions": [{"quote": "we will make videos", '
    '"suggestion": "specify how many videos and their topics", "kind": "evidence"}], '
    '"strength_notes": ["clear mission"]}'
)


class TestCoach:
    def _coach_run(self, content, llm, credentials=None):
        """共用的 coach 呼叫器（正確縮排版）。"""
        for client in _client():
            version = _fake_version(content_md=content)
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft()),
            ), patch(
                "api.routers.studio.studio_repo.get_current_version",
                new=AsyncMock(return_value=version),
            ), patch(
                "api.routers.studio.resolve_user_llm_credentials",
                new=AsyncMock(return_value=credentials),
            ), patch(
                "api.routers.studio.create_user_llm_client", return_value=llm
            ), patch(
                "api.routers.studio._coach_context", new=AsyncMock(return_value="")
            ):
                resp = client.post(
                    "/api/studio/drafts/pd_1/coach", json={"language": "en"}
                )
            return resp

    def test_coach_success_with_quote_validation(self):
        class _Llm:
            async def ainvoke(self, _messages):
                return SimpleNamespace(content=_COACH_JSON)

        resp = self._coach_run(
            "we will make videos about AI safety",
            _Llm(),
            credentials={"provider": "openrouter", "api_key": "k", "model": "m"},
        )
        assert resp.status_code == 200
        suggestions = resp.json()["coach"]["suggestions"]
        assert len(suggestions) == 1
        assert suggestions[0]["kind"] == "evidence"

    def test_coach_drops_hallucinated_quotes(self):
        """quote 不在稿件中 → 建議丟棄（防幻覺引用，design 硬約束）。"""
        class _Llm:
            async def ainvoke(self, _messages):
                return SimpleNamespace(content=_COACH_JSON)

        resp = self._coach_run(
            "totally different text",
            _Llm(),
            credentials={"provider": "openrouter", "api_key": "k", "model": "m"},
        )
        assert resp.status_code == 200
        assert resp.json()["coach"]["suggestions"] == []

    def test_coach_no_key_503(self):
        class _Never:
            async def ainvoke(self, _messages):
                raise AssertionError("should not be called")

        resp = self._coach_run("any content", _Never(), credentials=None)
        assert resp.status_code == 503

    def test_coach_unparseable_503(self):
        class _BadLlm:
            async def ainvoke(self, _messages):
                return SimpleNamespace(content="not json")

        resp = self._coach_run(
            "we will make videos",
            _BadLlm(),
            credentials={"provider": "openrouter", "api_key": "k", "model": "m"},
        )
        assert resp.status_code == 503

    def test_coach_cached(self):
        from api.routers import studio as st_module

        st_module._COACH_CACHE.clear()

        class _Llm:
            def __init__(self):
                self.calls = 0

            async def ainvoke(self, _messages):
                self.calls += 1
                return SimpleNamespace(content=_COACH_JSON)

        llm = _Llm()
        creds = {"provider": "openrouter", "api_key": "k", "model": "m"}
        r1 = self._coach_run("we will make videos", llm, credentials=creds)
        r2 = self._coach_run("we will make videos", llm, credentials=creds)
        assert r1.status_code == 200
        assert r2.json().get("cached") is True
        assert llm.calls == 1
        st_module._COACH_CACHE.clear()

    @pytest.mark.parametrize(
        "language,expected",
        [
            ("en", "en"),
            ("en-US", "en"),
            ("zh-CN", "zh-CN"),
            ("ru-RU", "ru"),
            ("fr", "zh-TW"),
        ],
    )
    def test_language_normalization(self, language, expected):
        from api.routers.studio import _normalize_lang

        assert _normalize_lang(language) == expected


class TestParseCoachJson:
    def test_strips_markdown_fences(self):
        from api.routers.studio import _parse_coach_json

        parsed = _parse_coach_json("```json\n" + _COACH_JSON + "\n```")
        assert parsed["clarifying_questions"] == ["budget basis?"]

    def test_invalid_returns_none(self):
        from api.routers.studio import _parse_coach_json

        assert _parse_coach_json("garbage") is None

    def test_suggestion_kind_normalized(self):
        from api.routers.studio import _parse_coach_json

        parsed = _parse_coach_json(
            '{"suggestions": [{"quote": "q", "suggestion": "s", "kind": "weird"}]}'
        )
        assert parsed["suggestions"][0]["kind"] == "clarity"


class TestParseCoachJsonRobust:
    def test_extracts_json_from_chatty_output(self):
        from api.routers.studio import _parse_coach_json

        parsed = _parse_coach_json(
            "Sure! Here is my coaching review:\n" + _COACH_JSON + "\nHope this helps!"
        )
        assert parsed is not None
        assert parsed["clarifying_questions"] == ["budget basis?"]


class TestOptimizations:
    def test_export_skips_title_when_content_has_h1(self):
        """內容自帶 H1 時匯出不重複加標題行（避免雙 H1）。"""
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft()),
            ), patch(
                "api.routers.studio.studio_repo.get_current_version",
                new=AsyncMock(
                    return_value=_fake_version(content_md="# Own Title\n\nbody")
                ),
            ), patch(
                "api.routers.studio.studio_repo.update_draft_meta",
                new=AsyncMock(return_value=_fake_draft(status="exported")),
            ):
                resp = client.post("/api/studio/drafts/pd_1/export")
        md = resp.json()["markdown"]
        assert md.count("\n# ") + md.startswith("# ") == 1
        assert "# Own Title" in md
        assert "# AI safety videos" not in md

    def test_export_adds_title_when_no_h1(self):
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft()),
            ), patch(
                "api.routers.studio.studio_repo.get_current_version",
                new=AsyncMock(return_value=_fake_version(content_md="plain body")),
            ), patch(
                "api.routers.studio.studio_repo.update_draft_meta",
                new=AsyncMock(return_value=_fake_draft(status="exported")),
            ):
                resp = client.post("/api/studio/drafts/pd_1/export")
        assert "# AI safety videos" in resp.json()["markdown"]

    def test_export_adds_title_when_content_starts_with_h2(self):
        """`##` 開頭不是 H1——startswith("#") 誤判會讓匯出整篇漏標題行。"""
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft()),
            ), patch(
                "api.routers.studio.studio_repo.get_current_version",
                new=AsyncMock(
                    return_value=_fake_version(content_md="## 二級標題\n\nbody")
                ),
            ), patch(
                "api.routers.studio.studio_repo.update_draft_meta",
                new=AsyncMock(return_value=_fake_draft(status="exported")),
            ):
                resp = client.post("/api/studio/drafts/pd_1/export")
        md = resp.json()["markdown"]
        assert md.startswith("# AI safety videos")
        assert "## 二級標題" in md

    def test_coach_excludes_heading_line_quotes(self):
        """教練建議引用 markdown 標題行 → 丟棄（標題是結構非論述句）。"""
        from api.routers.studio import _filter_suggestions

        full_text = "# 標題行\n\n我們會做研究。"
        suggestions = [
            {"quote": "# 標題行", "suggestion": "改標題", "kind": "clarity"},
            {"quote": "我們會做研究。", "suggestion": "補充方法", "kind": "evidence"},
            {"quote": "不存在的句子", "suggestion": "幻覺", "kind": "clarity"},
        ]
        kept = _filter_suggestions(suggestions, full_text)
        assert [s["quote"] for s in kept] == ["我們會做研究。"]


class TestShare:
    def test_share_lifecycle_and_public_access(self):
        """分享（opt-in）→ 公開讀取（免 auth、無 PII）→ 撤銷 → 404。"""
        draft = _fake_draft(share_token=None)

        def _draft_with_token(**kw):
            return _fake_draft(**kw)

        # 1) 分享
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=draft),
            ), patch(
                "api.routers.studio.studio_repo.update_draft_meta",
                new=AsyncMock(return_value=_draft_with_token(share_token="sh_tok1")),
            ):
                resp = client.post("/api/studio/drafts/pd_1/share")
        assert resp.status_code == 200
        assert resp.json()["share_token"] == "sh_tok1"
        assert resp.json()["share_url"].startswith("/static/studio-share.html?t=")

        # 2) 公開讀取（無 auth；patch session 直接回）
        from fastapi import FastAPI

        from api.routers import studio as st_module

        app = FastAPI()
        app.include_router(st_module.router)
        _override_session(app)

        class _FakeResult:
            def __init__(self, draft=None):
                self._draft = draft if draft is not None else _draft_with_token(share_token="sh_tok1")

            def scalar_one_or_none(self):
                return self._draft

        class _VersionsResult:
            def scalars(self):
                return self

            def all(self):
                return [
                    _fake_version(version_no=1, content_md="v1 text"),
                    _fake_version(version_no=2, content_md="v2 text"),
                ]

        class _EmptyResult:
            def scalars(self):
                return self

            def all(self):
                return []

        async def fake_execute(stmt):
            compiled = str(stmt)
            if "coach_exchanges" in compiled or "suggestion_outcomes" in compiled:
                return _EmptyResult()
            if "draft_versions" in compiled:
                return _VersionsResult()
            # token 不符 → None（模擬查無此分享；掃描綁定值）
            if "never_exists" in set(stmt.compile().params.values()):
                return _FakeResult(draft=None)
            return _FakeResult()

        import types

        fake_session_obj = types.SimpleNamespace(execute=fake_execute)

        async def fake_session():
            yield fake_session_obj

        from unittest.mock import patch as _patch

        app.dependency_overrides[st_module.get_async_session] = fake_session
        with _patch(
            "api.routers.studio.proposal_studio_enabled", return_value=True
        ):
            pub = TestClient(app)
            resp2 = pub.get("/api/public/studio/shared/sh_tok1")
            assert resp2.status_code == 200
            data = resp2.json()
            # 無 PII：不含 user_id/username
            body_text = str(data)
            assert "u1" not in body_text and "tester" not in body_text
            assert data["draft"]["title"] == "AI safety videos"
            assert len(data["versions"]) == 2
            assert data["versions"][0]["content_md"] == "v1 text"

            # 3) diff 模式
            resp3 = pub.get("/api/public/studio/shared/sh_tok1?a=1&b=2")
            assert resp3.status_code == 200
            assert "+v2 text" in resp3.json()["diff"]

            # 4) 撤銷後 404
            # 未知 token 的 404 由獨立測試覆蓋（TestShareNotFound；
            # SQLAlchemy 編譯快取下 fake 無法依綁定值分流）

    def test_unshare_requires_auth_ownership(self):
        draft_other = _fake_draft(user_id="someone_else", share_token="sh_x")
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=draft_other),
            ):
                resp = client.delete("/api/studio/drafts/pd_1/share")
        assert resp.status_code == 404


class TestShareNotFound:
    def test_unknown_token_404(self):
        """未知／已撤銷 token → 404（獨立 app：查詢一律查無）。"""
        from fastapi import FastAPI

        from api.routers import studio as st_module

        app = FastAPI()
        app.include_router(st_module.router)
        _override_session(app)

        class _NoneResult:
            def scalar_one_or_none(self):
                return None

        async def fake_execute(_stmt):
            return _NoneResult()

        import types

        so = types.SimpleNamespace(execute=fake_execute)

        async def fs():
            yield so

        app.dependency_overrides[st_module.get_async_session] = fs
        with patch("api.routers.studio.proposal_studio_enabled", return_value=True):
            resp = TestClient(app).get("/api/public/studio/shared/whatever")
        assert resp.status_code == 404


class TestCoachAuditChain:
    @pytest.fixture(autouse=True)
    def _reset_coach_rate_limit(self):
        from api.middleware.rate_limit import limiter

        limiter.reset()
        from api.routers import studio as st_module

        st_module._COACH_CACHE.clear()
        yield
        limiter.reset()

    def test_coach_persists_exchange(self):
        """coach 呼叫存檔完整往返並回 exchange_id。"""
        class _Llm:
            async def ainvoke(self, _messages):
                return SimpleNamespace(content=_COACH_JSON)

        for client in _client():
            version = _fake_version(content_md="we will make videos")
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft()),
            ), patch(
                "api.routers.studio.studio_repo.get_current_version",
                new=AsyncMock(return_value=version),
            ), patch(
                "api.routers.studio.resolve_user_llm_credentials",
                new=AsyncMock(
                    return_value={"provider": "openrouter", "api_key": "k", "model": "m"}
                ),
            ), patch(
                "api.routers.studio.create_user_llm_client", return_value=_Llm()
            ), patch(
                "api.routers.studio._coach_context", new=AsyncMock(return_value="")
            ):
                resp = client.post(
                    "/api/studio/drafts/pd_1/coach",
                    json={"question": "how to justify budget?", "language": "en"},
                )
        assert resp.status_code == 200
        assert resp.json()["exchange_id"]

    def test_outcome_validation(self):
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft()),
            ):
                r1 = client.post(
                    "/api/studio/drafts/pd_1/coach/outcomes",
                    json={"exchange_id": "cx_1", "quote": "q", "outcome": "bogus"},
                )
                assert r1.status_code == 400
                r2 = client.post(
                    "/api/studio/drafts/pd_1/coach/outcomes",
                    json={"exchange_id": "cx_1", "quote": "q", "outcome": "adopted"},
                )
                assert r2.status_code == 400  # adopted 需 version_no

    def test_outcome_missing_exchange_404(self):
        """exchange 不存在 → 404（防跨稿掛鏈）。"""
        import types

        from fastapi import FastAPI

        from api.routers import studio as st_module

        app = FastAPI()
        app.include_router(st_module.router)
        _override_user(app, USER)

        async def fake_session():
            so = types.SimpleNamespace()
            so.add = lambda _x: None

            async def _commit():
                return None

            async def _get(_model, _pk):
                return None

            so.commit = _commit
            so.get = _get
            yield so

        app.dependency_overrides[st_module.get_async_session] = fake_session
        with patch("api.routers.studio.proposal_studio_enabled", return_value=True):
            resp = TestClient(app).post(
                "/api/studio/drafts/pd_1/coach/outcomes",
                json={"exchange_id": "cx_missing", "quote": "q", "outcome": "dismissed"},
            )
        assert resp.status_code == 404


# ── Discover→Studio 參考動線（design 2026-08-17-discover-multisource §Phase 2）──


class TestReferences:
    def test_add_reference_roundtrip(self):
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft()),
            ), patch(
                "api.routers.studio.studio_repo.update_references",
                new=AsyncMock(return_value=_fake_draft()),
            ):
                resp = client.post(
                    "/api/studio/drafts/pd_1/references",
                    json={
                        "source": "manifund",
                        "url": "https://manifund.org/projects/x",
                        "title": "Similar Project",
                        "snippet": "raised 50k for AI safety videos",
                    },
                )
        assert resp.status_code == 201
        assert resp.json()["success"] is True

    def test_add_reference_snippet_truncated_300(self):
        draft = _fake_draft()
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=draft),
            ), patch(
                "api.routers.studio.studio_repo.update_references",
                new=AsyncMock(return_value=draft),
            ) as upd:
                resp = client.post(
                    "/api/studio/drafts/pd_1/references",
                    json={
                        "source": "oc",
                        "url": "https://opencollective.com/a",
                        "title": "A",
                        "snippet": "x" * 500,
                    },
                )
        assert resp.status_code == 201
        refs = upd.call_args[1]["references"]
        assert len(refs[0]["snippet"]) == 300

    def test_add_reference_rejects_when_full(self):
        """上限 20 筆（design：防 references 無限膨脹灌爆教練上下文）。"""
        draft = _fake_draft(
            references_json=[
                {"source": "manifund", "url": f"https://x/{i}", "title": f"T{i}", "snippet": ""}
                for i in range(20)
            ]
        )
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=draft),
            ), patch(
                "api.routers.studio.studio_repo.update_references",
                new=AsyncMock(return_value=draft),
            ):
                resp = client.post(
                    "/api/studio/drafts/pd_1/references",
                    json={
                        "source": "manifund",
                        "url": "https://x/new",
                        "title": "New",
                        "snippet": "",
                    },
                )
        assert resp.status_code == 400

    def test_add_reference_dedupes_by_url(self):
        draft = _fake_draft(
            references_json=[{"source": "manifund", "url": "https://x/1", "title": "T", "snippet": ""}]
        )
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=draft),
            ), patch(
                "api.routers.studio.studio_repo.update_references",
                new=AsyncMock(return_value=draft),
            ) as upd:
                resp = client.post(
                    "/api/studio/drafts/pd_1/references",
                    json={
                        "source": "manifund",
                        "url": "https://x/1",
                        "title": "T2",
                        "snippet": "",
                    },
                )
        assert resp.status_code == 200  # 冪等：已存在不重複加
        assert upd.call_args is None  # 冪等路徑不應再寫入
        assert len(resp.json()["references"]) == 1

    def test_reference_url_must_be_https(self):
        for client in _client():
            resp = client.post(
                "/api/studio/drafts/pd_1/references",
                json={"source": "manifund", "url": "http://evil.example", "title": "T"},
            )
        assert resp.status_code == 422

    def test_delete_reference(self):
        draft = _fake_draft(
            references_json=[{"source": "manifund", "url": "https://x/1", "title": "T", "snippet": ""}]
        )
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=draft),
            ), patch(
                "api.routers.studio.studio_repo.update_references",
                new=AsyncMock(return_value=_fake_draft()),
            ):
                resp = client.delete(
                    "/api/studio/drafts/pd_1/references",
                    params={"url": "https://x/1"},
                )
        assert resp.status_code == 200

    def test_delete_reference_missing_404(self):
        draft = _fake_draft(references_json=[])
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=draft),
            ):
                resp = client.delete(
                    "/api/studio/drafts/pd_1/references",
                    params={"url": "https://x/none"},
                )
        assert resp.status_code == 404

    def test_references_visible_in_draft_meta(self):
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(
                    return_value=_fake_draft(
                        references_json=[
                            {"source": "oc", "url": "https://oc/1", "title": "A", "snippet": "s"}
                        ]
                    )
                ),
            ), patch(
                "api.routers.studio.studio_repo.get_current_version",
                new=AsyncMock(return_value=_fake_version(content_md="x")),
            ):
                resp = client.get("/api/studio/drafts/pd_1")
        assert resp.status_code == 200
        assert resp.json()["draft"]["references"][0]["source"] == "oc"


class TestReferencesExportAndCoach:
    def test_export_appends_references_section(self):
        refs = [
            {"source": "manifund", "url": "https://manifund.org/projects/sim",
             "title": "Similar", "snippet": "s"},
            {"source": "oc", "url": "https://opencollective.com/a",
             "title": "OC proj", "snippet": "s2"},
        ]
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft(references_json=refs)),
            ), patch(
                "api.routers.studio.studio_repo.get_current_version",
                new=AsyncMock(return_value=_fake_version(content_md="body")),
            ), patch(
                "api.routers.studio.studio_repo.update_draft_meta",
                new=AsyncMock(return_value=_fake_draft(status="exported")),
            ):
                resp = client.post("/api/studio/drafts/pd_1/export")
        md = resp.json()["markdown"]
        assert "## References" in md
        assert "https://manifund.org/projects/sim" in md
        assert "https://opencollective.com/a" in md

    def test_export_without_references_has_no_section(self):
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft(references_json=[])),
            ), patch(
                "api.routers.studio.studio_repo.get_current_version",
                new=AsyncMock(return_value=_fake_version(content_md="body")),
            ), patch(
                "api.routers.studio.studio_repo.update_draft_meta",
                new=AsyncMock(return_value=_fake_draft(status="exported")),
            ):
                resp = client.post("/api/studio/drafts/pd_1/export")
        assert "## References" not in resp.json()["markdown"]

    def test_coach_optin_references_in_context(self):
        """use_references=true → 教練上下文含參考（opt-in）。"""

        class _Llm:
            def __init__(self):
                self.received = None

            async def ainvoke(self, messages):
                self.received = messages[1].content
                return SimpleNamespace(content=_COACH_JSON)

        llm = _Llm()
        refs = [{"source": "manifund", "url": "https://m/1",
                 "title": "Ref Project", "snippet": "raised 40k"}]
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft(references_json=refs)),
            ), patch(
                "api.routers.studio.studio_repo.get_current_version",
                new=AsyncMock(return_value=_fake_version(content_md="my draft text")),
            ), patch(
                "api.routers.studio.resolve_user_llm_credentials",
                new=AsyncMock(return_value={"provider": "openrouter", "api_key": "k", "model": "m"}),
            ), patch(
                "api.routers.studio.create_user_llm_client", return_value=llm
            ), patch(
                "api.routers.studio._coach_context", new=AsyncMock(return_value="")
            ):
                resp = client.post(
                    "/api/studio/drafts/pd_1/coach",
                    json={"language": "en", "use_references": True},
                )
        assert resp.status_code == 200
        assert "Ref Project" in llm.received

    def test_coach_default_excludes_references(self):
        class _Llm:
            def __init__(self):
                self.received = None

            async def ainvoke(self, messages):
                self.received = messages[1].content
                return SimpleNamespace(content=_COACH_JSON)

        llm = _Llm()
        refs = [{"source": "manifund", "url": "https://m/1",
                 "title": "Ref Project", "snippet": "raised 40k"}]
        for client in _client():
            with patch(
                "api.routers.studio.studio_repo.get_draft",
                new=AsyncMock(return_value=_fake_draft(references_json=refs)),
            ), patch(
                "api.routers.studio.studio_repo.get_current_version",
                new=AsyncMock(return_value=_fake_version(content_md="my draft text")),
            ), patch(
                "api.routers.studio.resolve_user_llm_credentials",
                new=AsyncMock(return_value={"provider": "openrouter", "api_key": "k", "model": "m"}),
            ), patch(
                "api.routers.studio.create_user_llm_client", return_value=llm
            ), patch(
                "api.routers.studio._coach_context", new=AsyncMock(return_value="")
            ):
                resp = client.post(
                    "/api/studio/drafts/pd_1/coach", json={"language": "en"}
                )
        assert resp.status_code == 200
        assert "Ref Project" not in llm.received
