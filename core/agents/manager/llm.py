"""
Manager Agent - LLM Routing and Invocation

Contains LLM invocation and model routing:
- _llm_invoke: Invoke LLM with task_type routing
- _get_routed_llm: Get appropriate LLM for task type
- _create_model_instance: Create new LLM instance for a model name
- _parse_json_response: Parse and validate JSON response
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from cachetools import TTLCache
from langchain_core.messages import HumanMessage

from api.utils import logger
from core.agents.context_budget import CONTEXT_CHAR_BUDGET
from core.agents.prompt_guard import parse_and_validate_json_response
from utils.user_client_factory import explain_llm_exception

from .mixin_base import ManagerAgentMixin


def _reasoning_effort_for(task_type: Optional[str]) -> str:
    """該 task_type 要不要壓低推理預算。空字串＝不動（預設）。

    只認 ``router``：那是一次性的 JSON 分類，不是要給人讀的推理。而且它走
    獨立的單次 ``_llm_invoke``（沒有對話歷史），所以不會踩到 DeepSeek
    「同一段對話不准混用推理／不推理」那個限制。

    2026-09-06 量測（``scripts/eval_router.py``，n=131，deepseek-v4-flash）：

    ==========  ======  =======  ======  ==========
    設定        depth   domains  gate    中位延遲
    ==========  ======  =======  ======  ==========
    推理開      93.1%   90.8%    99.2%   3.06s
    關推理      93.9%   87.8%    98.5%   0.74s
    ==========  ======  =======  ======  ==========

    只看真的打了 LLM 的 router bucket，domains 是 84.3% → 78.1%（-6.2pp）。
    dangerous 兩邊都是 0。**這是拿準確度換速度的交換，不是免費的**，所以
    預設不開——要開請先在自己的 provider 上重跑一次 eval。
    """
    import os

    if task_type != "router":
        return ""
    return os.getenv("ROUTER_REASONING_EFFORT", "").strip()


def _with_reasoning_budget(llm: Any, task_type: Optional[str]) -> Any:
    """依 task_type 綁上推理預算；沒設定就原樣回傳。"""
    effort = _reasoning_effort_for(task_type)
    if not effort or not hasattr(llm, "bind"):
        return llm
    try:
        return llm.bind(reasoning_effort=effort)
    except Exception as e:
        # 端點不吃這個參數就照原樣跑，不要讓一個調速旋鈕擋住主流程
        logger.warning("[Manager] reasoning_effort=%s 綁定失敗: %s", effort, e)
        return llm


class LLMInvokeMixin(ManagerAgentMixin):
    """LLM routing and invocation for ManagerAgent."""

    async def _llm_invoke(self, prompt: str, task_type: Optional[str] = None) -> str:
        """調用 LLM，支援根據 task_type 路由到不同模型。

        Args:
            prompt: The prompt to send to the LLM.
            task_type: Optional task type for model routing (e.g. "simple_qa",
                "deep_analysis"). When provided, ModelRouter selects the
                appropriate model. Falls back to self.llm when routing fails.
        """
        # Resolve the LLM to use based on task_type
        llm = self._get_routed_llm(task_type) if task_type else self.llm
        llm = _with_reasoning_budget(llm, task_type)

        messages = [HumanMessage(content=prompt)]
        last_exc = None
        for attempt in range(2):
            try:
                if hasattr(llm, "ainvoke"):
                    response = await llm.ainvoke(messages)
                else:
                    response = await asyncio.to_thread(llm.invoke, messages)
                content = response.content
                if isinstance(content, list):
                    content = "".join(
                        part.get("text", "") if isinstance(part, dict) else str(part)
                        for part in content
                    )
                if len(content) >= CONTEXT_CHAR_BUDGET * 0.95:
                    logger.warning(
                        f"[Manager] Response near context budget: {len(content)} chars (budget: {CONTEXT_CHAR_BUDGET})"
                    )

                # 記錄 token usage
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    from ..token_tracker import TokenUsage
                    from ._main import _extract_model_name_for_manager

                    usage = response.usage_metadata
                    model_name = _extract_model_name_for_manager(llm)
                    self._token_tracker.record(
                        TokenUsage(
                            model=model_name,
                            prompt_tokens=usage.get("input_tokens", 0),
                            completion_tokens=usage.get("output_tokens", 0),
                            total_tokens=usage.get("total_tokens", 0),
                        )
                    )
                if self._token_tracker.is_over_budget():
                    logger.warning(
                        f"[Manager] Token budget exceeded: ${self._token_tracker.total_cost():.4f}"
                    )

                return content
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                last_exc = e
                err_str = str(e).lower()
                if any(x in err_str for x in ("401", "unauthorized", "invalid api key", "forbidden")):
                    raise RuntimeError(explain_llm_exception(e)) from e
                if attempt == 0:
                    logger.warning(f"[Manager] LLM call failed (attempt 1/2), retrying in 2s: {e}")
                    await asyncio.sleep(2)
                    continue
        raise RuntimeError(explain_llm_exception(last_exc)) from last_exc

    def _get_routed_llm(self, task_type: str):
        """Return an LLM instance appropriate for the given task type.

        Uses ModelRouter.resolve_model() for cost-aware routing with tier
        gating and budget enforcement. Falls back to get_model() for
        backward compatibility.
        """
        # Router 模型可指派（2026-09-09 設計 B2）：使用者明確指定的 Router
        # 模型優先於一切分流感測——API 層已用對應 provider 金鑰建好獨立
        # client 掛在 manager 上（含 BYOK 換 provider 的情況）。
        router_client = getattr(self, "_router_llm_client", None)
        if task_type == "router" and router_client is not None:
            return router_client

        from ..model_router import ModelRouter

        try:
            target_model = ModelRouter.resolve_model(
                task_type=task_type,
                user_tier=getattr(self, "user_tier", "free"),
                user_preference=getattr(self, "_user_model_preference", None),
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            target_model = ModelRouter.get_model(task_type)

        # Resolve the current underlying model name
        inner_llm = getattr(self.llm, "_llm", self.llm)
        current_model = (
            getattr(inner_llm, "model_name", None)
            or getattr(inner_llm, "model", None)
            or ""
        )

        # If the user's model is NOT one of our platform defaults, they are on
        # a custom provider (NVIDIA, OpenRouter, DeepSeek, Groq, etc.) with a
        # specific model. Routing to a platform default would hit a non-existent
        # model on their endpoint → 404. Always use the user's configured model.
        #
        # ⚠️ 這代表對 BYOK 使用者（＝絕大多數人）task_type 分流**完全沒有發生**。
        # 2026-09-06 量過，結論是不值得補：換模型這件事本身沒有價值。
        # Router 分類呼叫（n=6，DeepSeek）：
        #
        #     pro（強）      中位 3.13s / 158 tok
        #     flash（快）    中位 3.06s / 360 tok   ← 換了等於沒換
        #     flash 關推理   中位 0.74s /  21 tok   ← 有效的是這個，不是換模型
        #
        # 有效槓桿是推理預算（見 ROUTER_REASONING_EFFORT），不是模型級距。
        # 要重做 TASK_MODEL_MAP 式的分流之前先看 scripts/eval_router.py 的數字。
        platform_models = set(ModelRouter.TASK_MODEL_MAP.values()) | {ModelRouter.DEFAULT_MODEL}
        if current_model and current_model not in platform_models:
            logger.debug(
                "[Manager] task_type=%s 分流略過：使用者用自訂模型 %s（BYOK）",
                task_type,
                current_model,
            )
            return self.llm

        if target_model == current_model:
            return self.llm

        # Need a different model — create or retrieve cached instance
        if not hasattr(self, "_routed_llm_cache"):
            self._routed_llm_cache: Dict[str, Any] = TTLCache(maxsize=10, ttl=600)

        cached = self._routed_llm_cache.get(target_model)
        if cached is not None:
            return cached

        try:
            routed = self._create_model_instance(target_model, inner_llm)
            # Wrap with the same LanguageAwareLLM to preserve language injection
            lang_msg = getattr(self.llm, "_lang_msg", None)
            if lang_msg:
                from ..bootstrap import LanguageAwareLLM

                routed = LanguageAwareLLM(routed)
                routed._lang_msg = lang_msg

            self._routed_llm_cache[target_model] = routed
            logger.info(
                f"[Manager] Model routed: task_type={task_type} → {target_model}"
            )
            return routed
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(
                f"[Manager] Model routing failed for {target_model}: {e}, "
                f"falling back to default model"
            )
            return self.llm

    @staticmethod
    def _create_model_instance(model_name: str, reference_llm: Any) -> Any:
        """Create a new LLM instance with *model_name* using config from *reference_llm*.

        This infers the provider and credentials from the existing LLM so the
        caller does not need to know about provider-specific setup.
        """

        kwargs: dict = {"model": model_name, "temperature": 0.5}

        # Infer provider — prefer model_provider attribute
        provider = getattr(reference_llm, "model_provider", None)
        if provider:
            kwargs["model_provider"] = provider
        else:
            # Fallback: infer from class name
            cls_name = type(reference_llm).__name__.lower()
            if "gemini" in cls_name or "google" in cls_name:
                kwargs["model_provider"] = "google_genai"
            else:
                kwargs["model_provider"] = "openai"

        # Copy credentials
        for attr in ("openai_api_key", "api_key", "google_api_key"):
            key = getattr(reference_llm, attr, None)
            if key:
                kwargs[attr] = key

        base_url = getattr(reference_llm, "openai_api_base", None) or getattr(reference_llm, "base_url", None)
        if base_url:
            kwargs["base_url"] = base_url

        # 與 user_client_factory 同一條路徑：相容端點要保住 reasoning_content
        from core.agents.reasoning_client import build_chat_model

        return build_chat_model(**kwargs)

    # NOTE: 結構化輸出的實際使用版本是 nodes.py 的 _llm_invoke_structured()。
    # 先前這裡另有 create_structured_llm()/invoke_structured() 兩個重複實作且
    # 無呼叫者（死碼），已移除以免分歧。

    def _parse_json_response(
        self,
        response: str,
        context: str = "intent",
        fallback_query: str = "",
    ) -> dict:
        """
        解析 JSON 回應並進行 schema 驗證。

        委派給 prompt_guard.parse_and_validate_json_response，
        確保返回的 dict 包含必要欄位，失敗時回傳安全預設值。
        """
        return parse_and_validate_json_response(
            response, context=context, fallback_query=fallback_query
        )

    async def _llm_invoke_streaming(
        self, prompt: str, task_type: Optional[str] = None
    ):
        """Stream LLM tokens. Yields string chunks as they arrive.

        Falls back to single invoke + chunked yield if streaming unavailable.
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [
            SystemMessage(content=self._system_prompt or ""),
            HumanMessage(content=prompt),
        ]
        routed_llm = self._get_routed_llm(task_type) if task_type else self.llm

        # Try streaming first
        if hasattr(routed_llm, "astream"):
            try:
                full_text: list[str] = []
                async for chunk in routed_llm.astream(messages):
                    # LangChain AIMessageChunk has .content
                    token = chunk.content if hasattr(chunk, "content") else str(chunk)
                    if token:
                        full_text.append(token)
                        yield token
                # Record token usage for the completed stream
                full_response = "".join(full_text)
                try:
                    self._record_token_usage_for_response(full_response, prompt, task_type)
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    pass
                return
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass  # fall through to fallback

        # Fallback: single invoke, then yield in small chunks
        response = await self._llm_invoke(prompt, task_type=task_type)
        chunk_size = 5  # small chunks for typing feel
        for i in range(0, len(response), chunk_size):
            yield response[i:i + chunk_size]
            await asyncio.sleep(0.01)  # tiny delay to simulate streaming

    def _record_token_usage_for_response(
        self, response_text: str, prompt: str, task_type: Optional[str]
    ) -> None:
        """Record token usage for a response string."""
        try:
            routed_llm = self._get_routed_llm(task_type) if task_type else self.llm
            inner_llm = getattr(routed_llm, "_llm", routed_llm)
            model_name = (
                getattr(inner_llm, "model_name", None)
                or getattr(inner_llm, "model", None)
                or "unknown"
            )
            # Estimate tokens (rough: 1 token ≈ 4 chars)
            prompt_tokens = len(prompt) // 4
            completion_tokens = len(response_text) // 4
            total_tokens = prompt_tokens + completion_tokens
            from ..token_tracker import TokenUsage

            self._token_tracker.record(
                TokenUsage(
                    model=model_name,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                )
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass
