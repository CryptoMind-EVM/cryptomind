"""Model routing strategy — select model by task type and user preference.

In a BYOK (Bring Your Own Key) model, users pay their own provider.
The platform's role is to pick a sensible default model for each task type,
and respect user-level overrides. No cost tracking needed.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ModelRouter:
    """Model router — select model by task type.

    All methods are ``@classmethod`` so callers never need to instantiate.
    """

    # Default model mapping: task_type → model name
    # Updated 2026/05 — aligned with latest model_config.py models
    # 2026/09 — "router"（Model Mixer Step 3）：中階 mini 起步（D2 決策：
    # frontier 讓 Router 比回答還貴、flash 對三欄結構輸出風險未知），
    # eval 量測後再調。BYOK 使用者沿用自家模型（_get_routed_llm 偵測）。
    TASK_MODEL_MAP: dict[str, str] = {
        "simple_qa": "gemini-3.5-flash",
        "market_data": "gpt-5.4-mini",
        "deep_analysis": "gpt-5.4",
        "router": "gpt-5.4-mini",
    }

    DEFAULT_MODEL = "gpt-5.4-mini"

    @classmethod
    def get_model(cls, task_type: str, user_preference: Optional[str] = None) -> str:
        """Return model name for *task_type*.

        ``manager/llm.py`` calls this directly.
        """
        if user_preference:
            return user_preference
        return cls.TASK_MODEL_MAP.get(task_type, cls.DEFAULT_MODEL)

    @classmethod
    def resolve_model(
        cls,
        task_type: str,
        user_tier: str = "free",
        user_preference: Optional[str] = None,
        **_kwargs,
    ) -> str:
        """High-level resolver: user preference → default model.

        Extra keyword args (e.g. ``spent_usd``, ``budget_usd``) are accepted
        for backward compatibility and silently ignored.

        Priority:
        1. ``user_preference`` — if set, use it verbatim.
        2. Default model for task type via ``get_model()``.
        """
        # 1. User preference always wins
        if user_preference:
            return user_preference

        # 2. Default model for task type
        return cls.get_model(task_type)
