import socket

import pytest

from utils.user_client_factory import create_user_llm_client, explain_llm_exception


def test_explain_llm_exception_for_dns_failure():
    exc = RuntimeError("Connection error.")
    exc.__cause__ = socket.gaierror(8, "nodename nor servname provided, or not known")

    message = explain_llm_exception(exc)

    assert "DNS" in message
    assert "可連外網" in message


def test_explain_llm_exception_for_auth_failure():
    message = explain_llm_exception(RuntimeError("401 Unauthorized"))
    assert message == "API Key 無效或已過期"


def test_explain_llm_exception_for_missing_authentication_header():
    message = explain_llm_exception(RuntimeError("missing authentication header"))
    assert message == "API Key 無效或已過期"


def test_nvidia_client_uses_nvidia_integrate_endpoint():
    """NVIDIA provider 應走 integrate.api.nvidia.com，並把 api_key 帶進 client。

    歷史：c63cbdb 寫過 ``default_headers.Authorization`` 檢查，但產品 code 從未
    有那層 explicit Authorization（OpenAI SDK 在 request 時自動加 Bearer header）。
    此 test 改為驗證實際的 invariants：base_url + model + api_key 有設好。
    """

    # 用現行的模型：minimaxai/minimax-m3 已於 2026-09-09 下架，factory 會靜默改走替代模型
    # （別名行為由 tests/test_model_alias_eol.py 守著），這裡驗證的是 base_url／model／api_key。
    model = "deepseek-ai/deepseek-v4-flash-0731"
    client = create_user_llm_client(
        "nvidia",
        "nvapi-test-placeholder",  # pragma: allowlist secret
        model,
    )

    assert str(client.openai_api_base).rstrip("/") == "https://integrate.api.nvidia.com/v1"
    assert client.model_name == model
    # api_key 必須有設（OpenAI SDK 在 request 時自動以此產生 Authorization: Bearer ...）
    api_key = getattr(client, "openai_api_key", None)
    assert api_key
    # pydantic SecretStr 會遮罩 str()，要 get_secret_value() 才能看到原值
    secret_value = api_key.get_secret_value() if hasattr(api_key, "get_secret_value") else str(api_key)
    assert secret_value.startswith("nvapi-")


def test_nvidia_client_rejects_empty_key():
    with pytest.raises(ValueError, match="API key"):
        create_user_llm_client("nvidia", "", "minimaxai/minimax-m3")
