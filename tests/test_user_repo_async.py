"""UserRepository.get_language / get_display_name async 測試。

這兩個 method 是 2026-08-12 GET /api/user/me timeout 修復的一部分：取代走
sync thread pool 的 run_sync(get_user_language/display_name)，改走 asyncpg，
避免搶 shared DB executor。完全容錯（任何錯誤回 None），與 legacy 一致。

用 mock AsyncSession（不打真 DB），可在無 postgres 環境跑。
"""

from unittest.mock import AsyncMock, MagicMock

from core.orm import repositories as repos


class _FakeCM:
    """假 async context manager，回傳預先準備好的 session。"""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_):
        return False


def _patch_session(monkeypatch, fake_session):
    monkeypatch.setattr(
        repos, "using_session", lambda session=None: _FakeCM(fake_session)
    )


def _make_session(scalar_value):
    """造一個假 session，execute 回傳 scalar_one_or_none() = scalar_value。"""
    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = scalar_value
    fake_session = MagicMock()
    fake_session.execute = AsyncMock(return_value=fake_result)
    return fake_session


async def test_get_language_returns_value(monkeypatch):
    _patch_session(monkeypatch, _make_session("zh-TW"))
    assert await repos.user_repo.get_language("u1") == "zh-TW"


async def test_get_language_returns_none_when_unset(monkeypatch):
    _patch_session(monkeypatch, _make_session(None))
    assert await repos.user_repo.get_language("u1") is None


async def test_get_language_empty_string_collapses_to_none(monkeypatch):
    """與 legacy 一致：row or None → 空字串也當 None。"""
    _patch_session(monkeypatch, _make_session(""))
    assert await repos.user_repo.get_language("u1") is None


async def test_get_language_fault_tolerant_on_db_error(monkeypatch):
    fake_session = MagicMock()
    fake_session.execute = AsyncMock(side_effect=RuntimeError("db down"))
    _patch_session(monkeypatch, fake_session)
    # 容錯：DB 掛掉回 None，不 raise（與 legacy get_user_language 一致）
    assert await repos.user_repo.get_language("u1") is None


async def test_get_display_name_returns_value(monkeypatch):
    _patch_session(monkeypatch, _make_session("Danny"))
    assert await repos.user_repo.get_display_name("u1") == "Danny"


async def test_get_display_name_fault_tolerant_on_db_error(monkeypatch):
    fake_session = MagicMock()
    fake_session.execute = AsyncMock(side_effect=RuntimeError("db down"))
    _patch_session(monkeypatch, fake_session)
    assert await repos.user_repo.get_display_name("u1") is None


def test_methods_are_async():
    import inspect

    assert inspect.iscoroutinefunction(repos.user_repo.get_language)
    assert inspect.iscoroutinefunction(repos.user_repo.get_display_name)
