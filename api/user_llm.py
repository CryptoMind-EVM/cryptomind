"""Helpers for resolving user-scoped LLM credentials on the server side."""

from typing import Optional

from api.utils import logger
from core.orm.user_api_keys_repo import LLM_PROVIDERS, user_api_keys_repo


def select_user_llm_model(
    credentials: dict,
    requested_provider: Optional[str] = None,
    requested_model: Optional[str] = None,
) -> Optional[str]:
    """Select a model without mixing a stale frontend model with another key.

    The encrypted server-side binding is authoritative.  A request model is
    only a compatibility fallback for old rows that do not yet have a saved
    model, and only when the requested provider matches the resolved provider.
    """
    saved_model = credentials.get("model")
    if isinstance(saved_model, str) and saved_model.strip():
        return saved_model.strip()

    resolved_provider = str(credentials.get("provider") or "").strip().lower()
    normalized_requested_provider = str(requested_provider or "").strip().lower()
    if resolved_provider != normalized_requested_provider:
        return None

    if isinstance(requested_model, str) and requested_model.strip():
        return requested_model.strip()
    return None


async def resolve_user_llm_credentials(
    current_user: Optional[dict],
    preferred_provider: Optional[str] = None,
) -> Optional[dict]:
    """Resolve an authenticated user's LLM provider + decrypted API key."""
    if not current_user:
        return None

    user_id = current_user.get("user_id")
    if not user_id:
        return None

    providers: list[str] = []
    if preferred_provider:
        normalized = preferred_provider.strip().lower()
        if normalized in LLM_PROVIDERS:
            providers.append(normalized)
        else:
            logger.warning(
                "Ignoring unsupported preferred LLM provider: %s",
                preferred_provider,
            )

    for provider in LLM_PROVIDERS:
        if provider not in providers:
            providers.append(provider)

    for provider in providers:
        result = await user_api_keys_repo.get_user_api_key_with_model(user_id, provider)
        if result:
            return {
                "provider": provider,
                "api_key": result["api_key"],
                "model": result["model"],
            }

    return None
