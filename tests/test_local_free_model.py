"""平台免費模型（local_llama / 本地 NeoHorse，對外叫 PLATFORM_FREE_MODEL_LABEL）與分層 fallback 鏈。

2026-09-22 DANNY：訪客 3/天、免費會員 20/天走本地模型；進階會員走平台雲端 key、不限次；
本地掛了自動切雲端；UI 不露底層模型名。
"""

import pytest

from core.agents import fallback as fb
from core.model_config import (
    MODEL_CONFIG,
    PLATFORM_FREE_MODEL_LABEL,
    PROVIDER_REGISTRY,
    get_provider_runtime,
    is_local_provider,
    resolve_model_alias,
    resolve_server_api_key,
)


@pytest.fixture
def chain_env(monkeypatch):
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setenv("BYOK_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("BYOK_FALLBACK_PROVIDER", "local_llama")
    monkeypatch.delenv("BYOK_FALLBACK_API_KEY", raising=False)
    monkeypatch.delenv("BYOK_FALLBACK_MODEL", raising=False)
    monkeypatch.setenv("BYOK_FALLBACK_2_PROVIDER", "deepseek")
    monkeypatch.setenv("BYOK_FALLBACK_2_MODEL", "deepseek-flash")
    monkeypatch.setenv("BYOK_FALLBACK_2_API_KEY", "sk-platform")
    fb._local_health.clear()


def test_local_provider_registered_but_not_user_selectable():
    rt = get_provider_runtime("local_llama")
    assert rt and rt["lc_provider"] == "openai" and rt["base_url"].endswith("/v1")
    assert rt["request_extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}
    }
    assert MODEL_CONFIG["local_llama"]["keyless"] is True  # 下拉可選，但不顯示金鑰欄
    assert MODEL_CONFIG["local_llama"]["default_model"] == "cryptomind-lite"
    assert resolve_model_alias("local_llama", "cryptomind-lite") == "neohorse-1-9b"
    assert is_local_provider("local_llama") and not is_local_provider("deepseek")
    assert resolve_server_api_key("local_llama") == "local"  # 不驗 key 的佔位


def test_brand_label_hides_underlying_model():
    assert (
        PLATFORM_FREE_MODEL_LABEL
        and "neohorse" not in PLATFORM_FREE_MODEL_LABEL.lower()
    )
    assert "qwen" not in PLATFORM_FREE_MODEL_LABEL.lower()


def test_chain_local_then_cloud_and_enabled_without_local_key(chain_env):
    chain = fb.fallback_chain()
    assert [(e["provider"], e["model"], e["local"]) for e in chain] == [
        ("local_llama", "neohorse-1-9b", True),
        ("deepseek", "deepseek-flash", False),
    ]
    assert fb.is_fallback_enabled()


def test_every_tier_gets_local_model(chain_env, monkeypatch):
    """2026-09-22 DANNY：進階會員也不給雲端（要花錢），一律本地；差別只在日額。"""
    monkeypatch.setattr(fb, "local_provider_healthy", lambda p, force=False: True)
    for tier in ("free", "premium"):
        creds = fb.fallback_credentials(tier=tier)
        assert creds["provider"] == "local_llama" and creds["local"]
        assert creds["fallback"] is True


def test_local_down_falls_back_to_cloud(chain_env, monkeypatch):
    monkeypatch.setattr(fb, "local_provider_healthy", lambda p, force=False: False)
    creds = fb.fallback_credentials(tier="free")
    assert creds["provider"] == "deepseek" and creds["local"] is False


def test_only_local_and_down_gives_nothing(chain_env, monkeypatch):
    monkeypatch.delenv("BYOK_FALLBACK_2_PROVIDER")
    monkeypatch.setattr(fb, "local_provider_healthy", lambda p, force=False: False)
    assert fb.fallback_credentials(tier="premium") is None


def test_health_probe_parses_llama_server_health(monkeypatch):
    calls = []

    class R:
        status_code = 200

        def json(self):
            return {"status": "ok"}

    import httpx

    monkeypatch.setattr(httpx, "get", lambda url, **kw: calls.append(url) or R())
    fb._local_health.clear()
    assert fb.local_provider_healthy("local_llama", force=True)
    assert calls and calls[0].endswith("/health") and "/v1" not in calls[0]
    assert fb.local_provider_healthy("deepseek")  # 非本地一律健康


def test_local_client_gets_placeholder_key_and_extra_body():
    from utils.user_client_factory import create_user_llm_client

    llm = create_user_llm_client("local_llama", "", None)
    inner = getattr(llm, "_llm", llm)
    assert str(
        getattr(inner, "openai_api_base", "") or getattr(inner, "base_url", "")
    ).endswith("/v1")
    assert inner.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
    assert inner.model_name == "neohorse-1-9b"


def test_guest_chain_parses_provider_prefix(monkeypatch):
    from api.routers import guest as g

    monkeypatch.delenv("GUEST_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("GUEST_LLM_MODEL", raising=False)
    monkeypatch.delenv("GUEST_LLM_FALLBACK_MODELS", raising=False)
    assert g._model_chain() == [
        ("local_llama", "neohorse-1-9b"),
        ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"),
    ]
    monkeypatch.setenv(
        "GUEST_LLM_FALLBACK_MODELS", "backup/model:free, deepseek:deepseek-flash"
    )
    assert g._model_chain()[1:] == [
        ("local_llama", "backup/model:free"),
        ("deepseek", "deepseek-flash"),
    ]
    assert g._is_switchable_error(RuntimeError("connection refused"), "local_llama")
    assert not g._is_switchable_error(RuntimeError("connection refused"), "openrouter")
    assert g._is_switchable_error(RuntimeError("429 rate limit"), "openrouter")


def test_registry_invariant_every_provider_has_runtime_fields():
    for name, cfg in PROVIDER_REGISTRY.items():
        assert cfg["lc_provider"] in {"openai", "google_genai", "anthropic"}, name
        assert isinstance(cfg.get("api_key_envs"), list), name


class TestPlatformModelEndpoint:
    """GET /api/platform-model：前端據此解鎖輸入框、顯示品牌名與剩餘次數。"""

    @pytest.fixture
    def enabled(self, chain_env, monkeypatch):
        monkeypatch.setattr(fb, "local_provider_healthy", lambda p, force=False: True)
        monkeypatch.setattr(
            "api.routers.analysis._count_chat_messages_today", lambda user_id: 7
        )

    @pytest.fixture
    def as_tier(self, app, monkeypatch):
        """釘住登入者等級。不能用 TEST_USER_TIER：get_current_user 先查 DB，查到就
        回 DB 的有效等級（正確行為），TEST_USER_TIER 只在 DB 查不到時才生效。本機
        test DB 的 test-user-001 是 premium，免費會員那題因此拿到 premium——結果
        取決於跑測試那台機器的 DB 內容，不是程式對錯。"""
        from api.deps import get_current_user

        def _set(tier: str) -> None:
            user = {
                "user_id": "test-user-001",
                "membership_tier": tier,
                "is_premium": tier == "premium",
            }
            monkeypatch.setitem(
                app.dependency_overrides, get_current_user, lambda: user
            )

        return _set

    async def test_free_member_local_model_with_daily_limit(
        self, client, auth_headers, enabled, as_tier, monkeypatch
    ):
        as_tier("free")
        monkeypatch.setattr("core.config.FREE_DAILY_CHAT_LIMIT", 20)
        resp = await client.get("/api/platform-model", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] and body["local"] and body["tier"] == "free"
        assert body["label"] == PLATFORM_FREE_MODEL_LABEL
        assert (body["daily_limit"], body["used_today"], body["remaining"]) == (
            20,
            7,
            13,
        )
        assert "neohorse" not in resp.text.lower()

    async def test_expired_premium_in_db_is_free_member(
        self, client, auth_headers, enabled, monkeypatch
    ):
        """走真的 get_current_user DB 路徑：premium 過期的使用者要拿免費日額，
        不能因為 DB 欄位還寫 premium 就不限次（真正會花錢的那種錯）。"""
        from datetime import datetime, timezone

        import api.deps
        from core.orm.models import User
        from core.orm.repositories import _user_to_dict

        expired = _user_to_dict(
            User(
                user_id="test-user-001",
                username="Expired",
                membership_tier="premium",
                membership_expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
                is_active=True,
            )
        )

        async def _get_by_id(user_id, session=None):
            return expired

        monkeypatch.setattr(api.deps.user_repo, "get_by_id", _get_by_id)
        monkeypatch.setattr("core.config.FREE_DAILY_CHAT_LIMIT", 20)
        resp = await client.get("/api/platform-model", headers=auth_headers)
        body = resp.json()
        assert body["tier"] == "free"
        assert (body["daily_limit"], body["remaining"]) == (20, 13)

    async def test_premium_on_cloud_backup_gets_sanity_cap(
        self, client, auth_headers, enabled, as_tier, monkeypatch
    ):
        """只有本地掛了、鏈上又設了雲端備援時才會用到雲端 → 才套理智上限。"""
        as_tier("premium")
        monkeypatch.setenv("BYOK_FALLBACK_DAILY_LIMIT", "200")
        monkeypatch.setattr(fb, "local_provider_healthy", lambda p, force=False: False)
        resp = await client.get("/api/platform-model", headers=auth_headers)
        body = resp.json()
        assert body["available"] and not body["local"] and body["tier"] == "premium"
        assert (body["daily_limit"], body["remaining"]) == (200, 193)

    async def test_premium_on_local_is_unlimited(
        self, client, auth_headers, enabled, as_tier
    ):
        as_tier("premium")
        resp = await client.get("/api/platform-model", headers=auth_headers)
        body = resp.json()
        assert (
            body["available"]
            and body["local"]
            and body["daily_limit"] is None
            and body["remaining"] is None
        )

    async def test_disabled_reports_unavailable(
        self, client, auth_headers, monkeypatch
    ):
        monkeypatch.setenv("BYOK_FALLBACK_ENABLED", "false")
        resp = await client.get("/api/platform-model", headers=auth_headers)
        assert resp.status_code == 200 and resp.json()["available"] is False


class TestPlatformDefaultBinding:
    """平台模型 = 每個使用者預設就有的一條綁定（repo 合成）：Agent 配置、模型切換、聊天都選得到。"""

    @pytest.fixture
    def enabled(self, chain_env):
        yield

    async def test_repo_synthesizes_binding_without_db(self, enabled):
        from core.orm.user_api_keys_repo import platform_binding, user_api_keys_repo

        assert platform_binding("local_llama") == {
            "api_key": "local",
            "model": "cryptomind-lite",
        }
        assert platform_binding("openai") is None
        # 這兩條不碰 DB（session=None 也不會開連線）
        assert await user_api_keys_repo.get_user_api_key("u1", "local_llama") == "local"
        assert await user_api_keys_repo.get_user_api_key_with_model(
            "u1", "local_llama"
        ) == {
            "api_key": "local",
            "model": "cryptomind-lite",
        }
        masked = await user_api_keys_repo.get_user_api_key_masked("u1", "local_llama")
        assert (
            masked["has_key"]
            and masked["platform"]
            and masked["model"] == "cryptomind-lite"
        )

    async def test_binding_absent_when_platform_model_disabled(self, monkeypatch):
        from core.orm.user_api_keys_repo import platform_binding

        monkeypatch.setenv("BYOK_FALLBACK_ENABLED", "false")
        assert platform_binding("local_llama") is None

    async def test_all_keys_listing_includes_platform_binding(
        self, enabled, monkeypatch
    ):
        """GET /api/user/api-keys 的合成列：has_key=True、platform=True，前端 savedKeys 自然看到。"""
        import sys

        m = sys.modules[
            "core.orm.user_api_keys_repo"
        ]  # 模組（core.orm 匯出的同名物件是 repo 實例）

        class _Res:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

        class _Session:
            async def execute(self, stmt):
                return _Res([])

        class _Ctx:
            async def __aenter__(self):
                return _Session()

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(m, "using_session", lambda s=None: _Ctx())
        keys = await m.user_api_keys_repo.get_all_user_api_keys("u1", kind="llm")
        assert keys["local_llama"] == {
            "has_key": True,
            "masked_key": None,
            "model": "cryptomind-lite",
            "models": ["cryptomind-lite"],
            "updated_at": None,
            "platform": True,
        }
        assert keys["openai"]["has_key"] is False

    async def test_credentials_resolve_to_platform_binding(self, enabled):
        """聊天／Agent 配置／Router 全走 resolve_user_llm_credentials → repo → 合成綁定。"""
        from api import user_llm

        creds = await user_llm.resolve_user_llm_credentials(
            {"user_id": "u1"}, "local_llama"
        )
        assert creds == {
            "provider": "local_llama",
            "api_key": "local",
            "model": "cryptomind-lite",
        }

    async def test_key_endpoints_reject_keyless(self, client, auth_headers, enabled):
        r = await client.post(
            "/api/user/api-keys",
            json={
                "provider": "local_llama",
                "api_key": "x",
                "model": "cryptomind-lite",
            },
            headers=auth_headers,
        )
        assert r.status_code == 400 and "platform" in r.json()["detail"]
        r = await client.delete("/api/user/api-keys/local_llama", headers=auth_headers)
        assert r.status_code == 400
        r = await client.post(
            "/api/user/api-keys/local_llama/test", json={}, headers=auth_headers
        )
        assert r.status_code == 400

    def test_local_provider_is_last_in_resolution_order(self):
        """有真金鑰的 provider 先（舊使用者不會被默默切到平台模型）；UI 排序由前端另做。"""
        assert list(MODEL_CONFIG)[-1] == "local_llama"
