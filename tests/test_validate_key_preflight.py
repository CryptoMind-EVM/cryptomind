"""
/api/settings/validate-key 的認證前置探測（preflight）行為。

背景（2026-09-02 DANNY 回報「模型測試都不會通過，明明都是正確的」「一直卡在
測試中」）：驗證原本只靠一次測試對話，把「金鑰對不對」和「這顆模型此刻回得
夠不夠快」綁在同一個時限裡。minimaxai/minimax-m3 這種思考型模型在 NVIDIA 公開
端點上光 reasoning 就可能跑掉一分鐘，時限一到就報「驗證失敗」——正確的金鑰
永遠測不過，前端還因此鎖住儲存。

這裡釘死三件事：
  1. list-models 端點必須先證明自己會擋無效金鑰，才能拿它當「金鑰有效」的證據
     （公開目錄型端點不能用，否則假金鑰會被判成有效）。
  2. 端點確實有擋 → 回 ok 並帶出模型清單。
  3. 認證被拒（401/403）→ auth_failed，不必再跑測試對話。
"""

import asyncio

import httpx
import pytest

from api.routers import system as sys_router


def _run(coro):
    return asyncio.run(coro)


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.invalid/v1/models")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.fixture(autouse=True)
def _clear_gate_cache():
    sys_router._LIST_MODELS_AUTH_GATED.clear()
    yield
    sys_router._LIST_MODELS_AUTH_GATED.clear()


RUNTIME = {"lc_provider": "openai", "base_url": "https://integrate.api.nvidia.com/v1"}


def test_auth_gated_endpoint_confirms_key_and_returns_models(monkeypatch):
    """端點會擋無效金鑰時，成功列出模型 == 金鑰有效。"""

    async def fake_discover(provider, runtime, api_key):
        if api_key == sys_router._BOGUS_KEY_PROBE:
            raise _http_error(401)
        return [{"value": "minimaxai/minimax-m3", "display": "minimax-m3"}]

    monkeypatch.setattr(sys_router, "_discover_provider_models", fake_discover)

    state, models = _run(
        sys_router._preflight_key_auth("nvidia", RUNTIME, "nvapi-real")
    )

    assert state == "ok"
    assert "minimaxai/minimax-m3" in models


def test_public_model_catalog_never_proves_key_is_valid(monkeypatch):
    """目錄公開（假金鑰也拿得到清單）時不能判金鑰有效——否則假金鑰會過關。"""

    async def fake_discover(provider, runtime, api_key):
        return [{"value": "minimaxai/minimax-m3", "display": "minimax-m3"}]

    monkeypatch.setattr(sys_router, "_discover_provider_models", fake_discover)

    state, models = _run(sys_router._preflight_key_auth("nvidia", RUNTIME, "any-key"))

    assert state == "unknown", "公開目錄不能當成金鑰有效的證據"
    assert "minimaxai/minimax-m3" in models, "清單仍要帶回去給模型名稱比對"


def test_rejected_key_reports_auth_failed(monkeypatch):
    async def fake_discover(provider, runtime, api_key):
        raise _http_error(401)

    monkeypatch.setattr(sys_router, "_discover_provider_models", fake_discover)

    state, models = _run(sys_router._preflight_key_auth("nvidia", RUNTIME, "nvapi-bad"))

    assert state == "auth_failed"
    assert models == set()


@pytest.mark.parametrize("status", [404, 429, 500])
def test_non_auth_http_errors_are_inconclusive(monkeypatch, status):
    """端點沒有 /models、限流、掛掉——都不能拿來判金鑰無效。"""

    async def fake_discover(provider, runtime, api_key):
        raise _http_error(status)

    monkeypatch.setattr(sys_router, "_discover_provider_models", fake_discover)

    state, _ = _run(sys_router._preflight_key_auth("openai", RUNTIME, "sk-real"))

    assert state == "unknown"


def test_preflight_timeout_is_inconclusive_not_failure(monkeypatch):
    async def fake_discover(provider, runtime, api_key):
        raise TimeoutError()

    monkeypatch.setattr(sys_router, "_discover_provider_models", fake_discover)

    state, _ = _run(sys_router._preflight_key_auth("nvidia", RUNTIME, "nvapi-real"))

    assert state == "unknown", "探測失敗不等於金鑰無效"


def test_auth_gate_probe_is_cached_per_provider(monkeypatch):
    """探測「端點會不會擋假金鑰」每個 provider 只做一次，不是每次驗證都多打一輪。"""
    calls = {"bogus": 0}

    async def fake_discover(provider, runtime, api_key):
        if api_key == sys_router._BOGUS_KEY_PROBE:
            calls["bogus"] += 1
            raise _http_error(401)
        return [{"value": "m", "display": "m"}]

    monkeypatch.setattr(sys_router, "_discover_provider_models", fake_discover)

    _run(sys_router._preflight_key_auth("nvidia", RUNTIME, "k1"))
    _run(sys_router._preflight_key_auth("nvidia", RUNTIME, "k2"))

    assert calls["bogus"] == 1


# ---------------------------------------------------------------------------
# 端點層行為：正確的金鑰一定要能通過
# ---------------------------------------------------------------------------

from api.models import KeyValidationRequest  # noqa: E402


class _DummyRequest:
    """slowapi 的 limiter 只需要 request.client / request.headers。"""

    client = type("C", (), {"host": "127.0.0.1"})()
    headers: dict = {}
    scope: dict = {"type": "http"}
    url = httpx.URL("http://testserver/api/settings/validate-key")
    method = "POST"
    state = type("S", (), {})()


def _validate(body):
    return _run(
        sys_router.validate_key.__wrapped__(
            body=body, request=_DummyRequest(), current_user={"user_id": "u1"}
        )
    )


def test_listed_model_passes_without_waiting_for_a_slow_reasoning_model(monkeypatch):
    """
    金鑰有效 ＋ provider 自己列出這顆模型 → 立刻通過，不必叫 minimax-m3 生一句
    「ok」讓使用者對著轉圈等一分鐘（「一直卡在測試中」的主因）。
    """

    async def fake_discover(provider, runtime, api_key):
        if api_key == sys_router._BOGUS_KEY_PROBE:
            raise _http_error(401)
        return [{"value": "minimaxai/minimax-m3", "display": "minimax-m3"}]

    def _boom(*args, **kwargs):  # 測試對話根本不該被呼叫
        raise AssertionError("模型已在清單中就不該再打一次測試對話")

    monkeypatch.setattr(sys_router, "_discover_provider_models", fake_discover)
    monkeypatch.setattr(sys_router, "init_chat_model", _boom)

    result = _validate(
        KeyValidationRequest(
            provider="nvidia", api_key="nvapi-real", model="minimaxai/minimax-m3"
        )
    )

    assert result["valid"] is True
    assert result["model"] == "minimaxai/minimax-m3"
    assert result["verified_via"] == "models_endpoint"


def test_slow_model_still_passes_when_key_is_proven_valid(monkeypatch):
    """
    模型不在清單中、測試對話又逾時，但金鑰已被證實有效 → 判通過。
    這正是 DANNY 的情境：金鑰完全正確，卻因為模型思考太久而永遠測不過、
    連儲存都被前端擋住。
    """

    async def fake_discover(provider, runtime, api_key):
        if api_key == sys_router._BOGUS_KEY_PROBE:
            raise _http_error(401)
        return [{"value": "other/model", "display": "other"}]

    async def fake_wait_for(coro, timeout):
        # preflight（list-models）那幾次照跑，只讓測試對話那次逾時
        if asyncio.iscoroutine(coro) and "discover" in coro.cr_code.co_name:
            return await coro
        coro.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(sys_router, "_discover_provider_models", fake_discover)
    monkeypatch.setattr(sys_router, "init_chat_model", lambda **kw: object())
    monkeypatch.setattr(sys_router.asyncio, "wait_for", fake_wait_for)

    result = _validate(
        KeyValidationRequest(
            provider="nvidia", api_key="nvapi-real", model="minimaxai/minimax-m3"
        )
    )

    assert result["valid"] is True, "金鑰有效時，模型回應慢不該被判成驗證失敗"
    assert result["model_verified"] is False


def test_bad_key_is_still_rejected(monkeypatch):
    """放寬逾時判定不能放水給無效金鑰。"""

    async def fake_discover(provider, runtime, api_key):
        raise _http_error(401)

    monkeypatch.setattr(sys_router, "_discover_provider_models", fake_discover)
    monkeypatch.setattr(
        sys_router,
        "init_chat_model",
        lambda **kw: pytest.fail("金鑰已被拒就不該再打對話"),
    )

    result = _validate(
        KeyValidationRequest(
            provider="nvidia", api_key="nvapi-bad", model="minimaxai/minimax-m3"
        )
    )

    assert result["valid"] is False
