"""Regression tests for user-scoped provider/model credential selection."""

from api.user_llm import select_user_llm_model


def test_saved_nvidia_model_wins_over_stale_frontend_model():
    credentials = {
        "provider": "nvidia",
        "api_key": "test-only-placeholder",  # pragma: allowlist secret
        "model": "minimaxai/minimax-m3",
    }

    selected = select_user_llm_model(
        credentials,
        requested_provider="nvidia",
        requested_model="gpt-5.4-mini",
    )

    assert selected == "minimaxai/minimax-m3"


def test_request_model_is_only_fallback_for_matching_provider():
    credentials = {
        "provider": "nvidia",
        "api_key": "test-only-placeholder",  # pragma: allowlist secret
        "model": None,
    }

    assert (
        select_user_llm_model(credentials, "nvidia", "minimaxai/minimax-m3")
        == "minimaxai/minimax-m3"
    )
    assert select_user_llm_model(credentials, "openai", "gpt-5.4-mini") is None
