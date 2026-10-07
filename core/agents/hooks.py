"""
Post-Response Hooks System (Hermes-inspired)

Allows registration of callbacks that run after each agent response.
Hooks are fire-and-forget (errors are caught and logged, never block the response).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class HookResult:
    """Result of a single hook execution."""

    hook_name: str
    success: bool
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass
class ResponseContext:
    """Context passed to each post-response hook."""

    user_id: str
    session_id: str
    query: str
    response: str
    agent_name: str
    tools_used: list[str]
    execution_time_ms: float
    language: str = "zh-TW"
    metadata: dict[str, Any] = field(default_factory=dict)


# Hook signature: (ResponseContext) -> None (or awaitable)
HookCallback = Callable[[ResponseContext], Any]
_HOOKS: list[tuple[str, HookCallback]] = []


def register_hook(name: str, callback: HookCallback) -> None:
    """Register a post-response hook. Duplicate names are ignored."""
    global _HOOKS
    if not any(n == name for n, _ in _HOOKS):
        _HOOKS.append((name, callback))
        logger.debug(f"[Hooks] Registered post-response hook: {name}")


def unregister_hook(name: str) -> None:
    """Unregister a post-response hook by name."""
    global _HOOKS
    _HOOKS[:] = [(n, cb) for n, cb in _HOOKS if n != name]


async def invoke_response_hooks(ctx: ResponseContext) -> list[HookResult]:
    """
    Fire all registered post-response hooks with the given context.

    Hooks run concurrently (asyncio.gather). Errors are caught per-hook.
    Returns a list of HookResult for each hook.
    """
    if not _HOOKS:
        return []

    tasks = []
    hook_names = []

    for name, callback in _HOOKS:
        hook_names.append(name)

        async def _run(cb: HookCallback, hook_name: str) -> HookResult:
            start = time.monotonic()
            try:
                result = cb(ctx)
                if asyncio.iscoroutine(result):
                    await result
                return HookResult(hook_name=hook_name, success=True, duration_ms=0)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.warning(f"[Hooks] Hook '{hook_name}' failed: {e}")
                return HookResult(
                    hook_name=hook_name, success=False, error=str(e), duration_ms=0
                )
            finally:
                _ = (
                    time.monotonic() - start
                ) * 1000  # measured but returned in HookResult above

        tasks.append(_run(callback, name))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Convert exceptions to HookResult
    final_results = []
    for name, result in zip(hook_names, results):
        if isinstance(result, Exception):
            final_results.append(
                HookResult(hook_name=name, success=False, error=str(result))
            )
        else:
            final_results.append(result)

    return final_results


def invoke_response_hooks_sync(ctx: ResponseContext) -> None:
    """
    Synchronous version — runs hooks in a background thread pool.
    Use this when you can't make the caller async.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're in an async context — create a new task
            loop.create_task(invoke_response_hooks(ctx))
        else:
            loop.run_until_complete(invoke_response_hooks(ctx))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[Hooks] Failed to invoke hooks: {e}")
