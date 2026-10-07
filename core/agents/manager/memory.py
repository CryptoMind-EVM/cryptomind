"""
Manager Agent - Memory System

Contains short-term and long-term memory management:
- _get_memory: Get or create short-term memory
- get_long_term_memory_context: Get long-term memory context for LLM prompt
- _track_conversation: Track conversation and trigger consolidation
- _extract_facts_background: Background fact extraction (nanoclaw style)
- _record_experience_background: Background experience recording
- check_idle_consolidation: Check if idle consolidation is needed
- _background_memory_consolidation: Background memory consolidation
- _do_consolidation: Actual consolidation logic

Consolidation triggers (switching-conversation consolidation is wired up at the endpoint layer):
  - Auto-trigger after 12 accumulated turns (_track_conversation → _background_memory_consolidation_unlocked)
  - An idle check at the start of each request (claw_loop → _background_memory_consolidation, idle ≥ MEMORY_IDLE_TIMEOUT)
  - On conversation switch, triggered fire-and-forget by the endpoint
    (POST /api/chat/current-session + POST /api/chat/sessions →
    api.routers.analysis._trigger_session_consolidation → this module's
    _background_memory_consolidation; the affected session manager is invalidated afterwards)
- _get_memory_store: Lazy-init MemoryStore
- _get_agents_description: Get all agents description
"""

from __future__ import annotations

import asyncio
import time
from typing import List, Optional

from api.utils import logger, run_sync

from ..models import ShortTermMemory
from .mixin_base import ManagerAgentMixin

# Tool-name prefix → market family. Used to derive task_family (when task_results is missing).
# Lets experience/skill retrieval work by market family, instead of everything falling into "chat".
_TOOL_FAMILY_PREFIXES = {
    "crypto": ("get_crypto", "get_fear_and_greed", "get_trending_token",
               "get_defillama", "get_token_", "get_dex_", "get_eth_",
               "get_erc20", "get_address_", "get_contract_", "check_token",
               "get_cmc_", "get_futures"),
    "tw_stock": ("tw_",),
    "us_stock": ("us_",),
    "global_stock": ("global_stock",),
    "commodity": ("get_commodity", "get_all_commodities"),
    "forex": ("get_forex",),
    "economic": ("get_central_bank", "get_market_indices", "get_vix",
                 "get_economic_calendar"),
}


def _infer_task_family_from_tools(tools_used: List[str]) -> str:
    """Derive task_family from the prefix of the tool names used.

    Returns the first matching market family (e.g., "crypto"); returns "" if no match.
    Conservative strategy: only recognize high-confidence prefixes to avoid misclassification.
    """
    if not tools_used:
        return ""
    for tool in tools_used:
        tool_lower = tool.lower()
        for family, prefixes in _TOOL_FAMILY_PREFIXES.items():
            if any(tool_lower.startswith(p) for p in prefixes):
                return family
    return ""


class MemoryMixin(ManagerAgentMixin):
    """Memory system for ManagerAgent."""

    def _get_memory(self, session_id: str) -> ShortTermMemory:
        """Get or create short-term memory."""
        if session_id not in self._memory_cache:
            self._memory_cache[session_id] = ShortTermMemory()
        return self._memory_cache[session_id]

    # 重新水合時最多撈幾則：consolidation 的 last_consolidated_index 是「已整合到第幾則」，
    # 要對得上同一串訊息；超過就不水合（寧可不整合，也不要用錯位的 index 整合錯的片段）
    _HYDRATE_LIMIT = 400

    def _hydrate_short_term_from_db(self, current_user_message: str = "") -> None:
        """manager 實例是新建的（TTL 1 小時、重新部署、換金鑰／語言都會重建）時，行程內的對話緩衝是空的，
        但 DB 的 last_consolidated_index 還記著舊進度。

        2026-10-06 審查：緩衝歸零而進度還在 → unconsolidated＝_message_count − db_index 變負數（閒置／12 則
        門檻都不會再觸發 consolidation），真的觸發時又拿舊 index 去切一串全新的訊息（整合到錯的片段）。
        長對話 consolidation 因此在第一次重建後就悄悄停擺。這裡把該 session 在 DB 的對話（API 層本來就逐則
        存）撈回來補齊緩衝並對齊計數。當前這句使用者訊息 API 已經先存進 DB，要略過（_track_conversation
        馬上會自己加一次）。任何失敗都不影響回答。
        """
        session_id = self.session_id
        done = getattr(self, "_hydrated_sessions", None)
        if done is None:
            done = self._hydrated_sessions = set()
        if not session_id or session_id == "default" or session_id in done:
            return
        done.add(session_id)
        memory = self._get_memory(session_id)
        if memory.conversation_history:
            return  # 行程內已經有（正常連續對話），不要覆蓋
        try:
            from core.database.chat import get_chat_history

            rows = get_chat_history(session_id=session_id, limit=self._HYDRATE_LIMIT)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[Manager] short-term hydrate skipped: {exc}")
            return
        if len(rows) >= self._HYDRATE_LIMIT:
            logger.info(
                "[Manager] short-term hydrate skipped: history longer than the window"
            )
            return
        current = (current_user_message or "").strip()
        if rows and current:
            last = rows[-1]
            if (
                last.get("role") == "user"
                and (last.get("content") or "").strip() == current
            ):
                rows = rows[:-1]
        for row in rows:
            role = row.get("role")
            content = (row.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                memory.add_message(role, content)
        self._message_count = max(self._message_count, len(memory.conversation_history))

    def get_long_term_memory_context(
        self, agent_id: Optional[str] = None
    ) -> str:
        """Get the long-term memory context (for the LLM prompt).

        Mixer Step 4：帶 agent_id 時以 agent 視角讀（共享＋自身私有 facts；
        long_term blob 目前皆共享——Step 5 私有 blob 起用時再分層）。不帶＝
        共享視角（現行為）。agent 視圖 store 走 get_memory_store factory
        （per-agent instance cache），不污染 self._memory_store（系統寫入
        用的共享視角 store）。
        """
        try:
            if agent_id:
                from core.config import TEST_MODE

                if TEST_MODE and not getattr(self, "_force_memory_in_test", False):
                    return ""
                from core.database.memory import get_memory_store

                memory_store = get_memory_store(
                    self.user_id, session_id=self.session_id, agent_id=agent_id
                )
            else:
                memory_store = self._get_memory_store()
            if not memory_store:
                return ""
            return memory_store.get_memory_context(
                include_history=True, history_limit=10
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[Manager] Failed to get long-term memory: {e}")
            return ""

    def _get_memory_store(self):
        """
        Lazily initialize MemoryStore.

        ✅ Cross-session design: memory is keyed by user_id, so opening a new
        conversation still reads historical memory.
        ✅ Sync last_consolidated_index on startup: avoids state loss after a server restart.
        """
        from core.config import TEST_MODE

        if TEST_MODE and not getattr(self, "_force_memory_in_test", False):
            return None

        if self._memory_store is None:
            try:
                from core.database.memory import MemoryStore

                self._memory_store = MemoryStore(self.user_id, self.session_id)

                # Sync the index on startup (avoids re-consolidation after a server restart)
                if self._last_consolidated_index == 0:
                    db_index = self._memory_store.get_last_consolidated_index()
                    if db_index > 0:
                        self._last_consolidated_index = db_index
                        logger.info(
                            f"[Manager] Synced consolidation index from DB: {db_index}"
                        )

            except ImportError as e:
                import logging

                logging.getLogger(__name__).warning(f"Memory module not available: {e}")
                self._memory_store = False
        return self._memory_store

    async def _track_conversation(
        self,
        user_message: str,
        assistant_response: str,
        tools_used: Optional[List[str]] = None,
    ) -> None:
        """
        Track conversation history and auto-trigger memory consolidation when the
        threshold is reached (nanoclaw-style two-layer).

        Args:
            user_message: user message
            assistant_response: assistant response
            tools_used: list of tools used
        """
        import sys

        from ._main import MEMORY_CONSOLIDATION_THRESHOLD

        facade = sys.modules.get("core.agents.manager")
        _rb = facade._run_background if facade else None
        _es = facade._experience_store if facade else None

        # Update the activity time
        self._last_activity_time = time.time()

        # 新建的 manager 實例：先把 DB 裡這個 session 的對話補回緩衝並對齊計數（見 _hydrate_short_term_from_db）
        try:
            await run_sync(lambda: self._hydrate_short_term_from_db(user_message))
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[Manager] short-term hydrate failed: {exc}")

        # Add to short-term memory
        memory = self._get_memory(self.session_id)
        memory.add_message("user", user_message)
        memory.add_message("assistant", assistant_response)

        self._message_count += 2
        turn_index = self._message_count // 2  # turn number (1 turn = 1 user + 1 assistant)

        # ✅ nanoclaw extract_memory: extract structured facts immediately each turn (background)
        # Lightweight; does not need to wait for the consolidation threshold.
        # Bug #6 fix: add exception handling so background task failures don't affect the main flow.
        if _rb:
            try:
                _rb(
                    self._extract_facts_background(
                        user_message, assistant_response, turn_index, tools_used
                    )
                )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.warning(f"[Manager] Failed to schedule fact extraction: {e}")
            try:
                _rb(
                    self._record_experience_background(
                        user_message, assistant_response, tools_used
                    )
                )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.warning(
                    f"[Manager] Failed to schedule experience recording: {e}"
                )
        else:
            logger.warning(
                "[Manager] _run_background not available, skipping async tasks"
            )

        # Count unconsolidated messages (read the index from DB for consistency).
        # _get_memory_store() + get_last_consolidated_index() are sync DB I/O;
        # inside an async function they must go through run_sync (worker thread),
        # otherwise they block the event loop.
        def _read_db_index() -> int:
            store = self._get_memory_store()
            if not store:
                return 0
            return store.get_last_consolidated_index()

        try:
            db_index = await run_sync(_read_db_index)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            # DB read failure should not interrupt response tracking; fall back to the short-term memory's local count
            logger.warning(f"[Manager] read consolidated index failed: {e}")
            db_index = 0
        unconsolidated = self._message_count - db_index

        # Only trigger the heavyweight consolidation (summary + long-term memory update) when the threshold is reached.
        # Bug fix: check first, then set the flag, to ensure atomicity.
        if unconsolidated >= MEMORY_CONSOLIDATION_THRESHOLD:
            async with self._consolidation_lock:
                if self._consolidating:
                    logger.debug(
                        "[Manager] Consolidation already in progress, skipping"
                    )
                    return
                self._consolidating = True

                # Bug #3 fix: cancel the old task
                if self._consolidation_task and not self._consolidation_task.done():
                    self._consolidation_task.cancel()
                    try:
                        await self._consolidation_task
                    except asyncio.CancelledError:
                        pass

                logger.info(
                    f"[Manager] Triggering memory consolidation: "
                    f"{unconsolidated} unconsolidated messages"
                )
                # Create the task inside the lock to ensure only one is running
                self._consolidation_task = asyncio.create_task(
                    self._background_memory_consolidation_unlocked()
                )

    async def _extract_facts_background(
        self,
        user_message: str,
        assistant_response: str,
        turn_index: int,
        tools_used: Optional[List[str]] = None,
    ) -> None:
        """Run nanoclaw fact extraction in the background, without blocking the response."""
        try:
            memory_store = self._get_memory_store()
            if not memory_store:
                return
            await memory_store.extract_facts_from_turn(
                user_message=user_message,
                assistant_message=assistant_response,
                turn_index=turn_index,
                llm=self.llm,
                tools_used=tools_used,
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"[Manager] extract_facts_background failed: {e}")

    async def _record_experience_background(
        self,
        user_message: str,
        assistant_response: str,
        tools_used: Optional[List[str]],
        task_results: Optional[dict] = None,
    ) -> None:
        """Fire-and-forget: record task trajectory after each turn.

        Fix (2026-07-25): _track_conversation originally did not pass task_results,
        so task_family was always "chat" and outcome always "success" — experience/skill
        retrieval could not work by market family. Now, when task_results is missing,
        task_family is derived from tools_used (tool names carry a market prefix), so
        the written data is actually usable for retrieval.
        """
        from ._main import _experience_store

        try:
            # Determine task_family: prefer task_results' agent_name;
            # when missing (the current production flow), derive it from the tools_used prefix.
            task_family = "chat"
            agent_used_str = ""
            if task_results:
                agents_used = [
                    v.get("agent_name", "")
                    for v in task_results.values()
                    if isinstance(v, dict)
                ]
                for agent in agents_used:
                    if agent in (
                        "crypto",
                        "tw_stock",
                        "us_stock",
                        "forex",
                        "commodity",
                        "economic",
                    ):
                        task_family = agent
                        break
                agent_used_str = ",".join(
                    set(a for a in agents_used if a)
                )

            if task_family == "chat" and tools_used:
                inferred = _infer_task_family_from_tools(tools_used)
                if inferred:
                    task_family = inferred
                    if not agent_used_str:
                        agent_used_str = inferred

            # Determine outcome: prefer task_results; otherwise judge by "is there a substantive response".
            # (An empty string or very short response is treated as a possible failure.)
            outcome = "success"
            quality = None
            if task_results:
                qualities = [
                    v.get("quality")
                    for v in task_results.values()
                    if isinstance(v, dict)
                ]
                if "fail" in qualities:
                    outcome = "failure"
                    quality = "fail"
                else:
                    quality = "pass"
            else:
                quality = "fail" if len(assistant_response.strip()) < 10 else "pass"
                if quality == "fail":
                    outcome = "failure"

            # M3 fix (2026-07-20): record_experience / record_skill are sync DB
            # INSERTs; calling them directly would freeze the event loop. Wrap in run_sync.
            from api.utils import run_sync

            await run_sync(
                lambda: _experience_store.record_experience(
                    user_id=self.user_id or "anonymous",
                    session_id=self.session_id,
                    task_family=task_family,
                    query=user_message,
                    tools_used=tools_used or [],
                    agent_used=agent_used_str,
                    outcome=outcome,
                    quality_score=quality,
                    failure_reason=None,
                    response_chars=len(assistant_response),
                )
            )
            # LearnedSkillStore writes have been removed (c014 memory convergence) — it is dead code
            # (record_skill writes but retrieve_relevant is never read in production). ExperienceStore
            # already records the same trajectory info (query→tools→outcome), so the function is not duplicated.
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            logger.debug("[Manager] _record_experience_background failed: %s", exc)

    def check_idle_consolidation(self) -> bool:
        """
        Check whether memory consolidation is needed due to idleness.

        Returns:
            True if consolidation is needed.
        """
        from ._main import MEMORY_IDLE_TIMEOUT

        if self._consolidating:
            return False

        idle_time = time.time() - self._last_activity_time
        # Bug #2 fix: read the index from DB for consistency
        memory_store = self._get_memory_store()
        db_index = memory_store.get_last_consolidated_index() if memory_store else 0
        unconsolidated = self._message_count - db_index

        # Idle for more than 5 minutes AND there are unconsolidated messages
        if idle_time >= MEMORY_IDLE_TIMEOUT and unconsolidated > 0:
            logger.info(
                f"[Manager] Idle consolidation triggered: "
                f"idle={idle_time:.0f}s, unconsolidated={unconsolidated}"
            )
            return True
        return False

    async def _background_memory_consolidation(self) -> bool:
        """
        Background memory consolidation (nanobot style) — standalone-call version.

        - Runs async, does not block the conversation
        - Uses a lock to prevent duplicate consolidation
        - Tracks the consolidated index

        Returns:
            True if consolidation succeeded.
        """
        if self._consolidating:
            return False

        async with self._consolidation_lock:
            self._consolidating = True
            try:
                return await self._do_consolidation(archive_all=False)
            finally:
                self._consolidating = False

    async def _background_memory_consolidation_unlocked(self) -> bool:
        """
        Bug #1 fix: background memory consolidation (the lock is already held by the caller).

        Called by _track_conversation while it holds _consolidation_lock.
        Do not acquire the lock again, or it will deadlock.

        Returns:
            True if consolidation succeeded.
        """
        try:
            return await self._do_consolidation(archive_all=False)
        finally:
            self._consolidating = False

    async def _do_consolidation(self, archive_all: bool = False) -> bool:
        """
        Perform the actual memory consolidation.

        Separation of concerns:
        - ManagerAgent computes which messages to consolidate
        - MemoryStore performs the LLM consolidation and writes to DB

        Args:
            archive_all: whether to consolidate all messages

        Returns:
            True on success.
        """
        from ._main import MEMORY_CONSOLIDATION_THRESHOLD

        try:
            memory = self._get_memory(self.session_id)
            memory_store = self._get_memory_store()
            if not memory_store:
                return False

            if not memory.conversation_history:
                return True

            # === ManagerAgent computes the slice ===
            if archive_all:
                messages_to_consolidate = memory.conversation_history
            else:
                keep_count = MEMORY_CONSOLIDATION_THRESHOLD // 2
                # M5 fix (2026-07-20): wrap the sync DB SELECT in run_sync
                from api.utils import run_sync

                start_idx = await run_sync(memory_store.get_last_consolidated_index)
                end_idx = len(memory.conversation_history) - keep_count

                if end_idx <= start_idx:
                    logger.info("[Manager] No new messages to consolidate")
                    return True

                messages_to_consolidate = memory.conversation_history[start_idx:end_idx]

                if not messages_to_consolidate:
                    return True

                logger.info(
                    f"[Manager] Consolidating {len(messages_to_consolidate)} messages "
                    f"(offset={start_idx}, keep={keep_count})"
                )

            # Prepare the message format
            formatted_messages = []
            for msg in messages_to_consolidate:
                from datetime import datetime, timezone

                formatted_messages.append(
                    {
                        "role": msg.get("role", "unknown"),
                        "content": msg.get("content", ""),
                        "timestamp": datetime.now(timezone.utc).strftime(
                            "%Y-%m-%d %H:%M"
                        ),
                        "tools_used": msg.get("tools_used", []),
                    }
                )

            # === MemoryStore performs the consolidation ===
            success = await memory_store.consolidate(
                messages_to_consolidate=formatted_messages,
                llm=self.llm,
            )

            if success:
                # Update the local index (syncing to DB is done by MemoryStore)
                # M5 fix (2026-07-20): wrap the sync DB UPDATE in run_sync
                from api.utils import run_sync

                self._last_consolidated_index = len(memory.conversation_history)
                await run_sync(
                    memory_store.set_last_consolidated_index,
                    self._last_consolidated_index,
                )
                logger.info(
                    f"[Manager] Consolidation done, index={self._last_consolidated_index}"
                )

            return success

        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.error(f"[Manager] Memory consolidation failed: {e}")
            return False
