"""
Analysis Worker — 獨立進程，從 Redis queue 取分析任務並執行。

徹底脫離 gunicorn worker recycle 威脅：分析跑在這個獨立進程裡，
gunicorn 怎麼 recycle 都不影響正在跑的分析。

啟動：python -m scripts.analysis_worker（docker-compose.prod.yml 的 analysis-worker command）
環境變數：DATABASE_URL、REDIS_URL（compose 注入，與 API 服務同一組）

循環：
    while True:
        job = dequeue_job(timeout=30)  # BRPOP analysis:queue
        if job:
            asyncio.run(_run_job(job))
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import traceback
from typing import Any, Dict, Optional

from core.analysis_queue import run_status_ttl_seconds, safe_analysis_error_message

logger = logging.getLogger("analysis_worker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
from config.logging_config import install_secret_redaction  # noqa: E402

install_secret_redaction()

# 分析超時（跟 API 端 ANALYSIS_TIMEOUT_SECONDS 對齊）
_ANALYSIS_TIMEOUT = int(os.getenv("ANALYSIS_TIMEOUT_SECONDS", "3600"))
# run 狀態的 TTL 要撐過整個分析：此前寫死 900 秒，跑超過 15 分鐘 key 就過期，
# 結束時寫回的狀態少了 session_id／user_id → HITL 續傳被當成別人的 run（404）。
# 規則與 API 端共用（它也寫同一把 key，不能把 TTL 縮回去）。
_RUN_STATUS_TTL = run_status_ttl_seconds()


def _rebuild_graph_input(job: Dict[str, Any]):
    """把 job envelope 還原成 LangGraph 的 Command。

    resume 分支同樣要帶 ``update``：resume 輪若帶了 state 更新，漏掉就與
    同步路徑不一致。
    """
    from langgraph.types import Command

    raw = job.get("graph_input", {}) or {}
    update = raw.get("update") or {}
    resume_answer = job.get("resume_answer")
    if resume_answer is None:
        resume_answer = raw.get("resume")
    if resume_answer is not None:
        return Command(resume=resume_answer, update=update)
    return Command(goto=raw.get("goto", "claw_loop"), update=update)


async def _run_job(job: Dict[str, Any]) -> None:
    """執行單一分析任務（從 Redis queue 取出的 job envelope）。"""
    run_id = job.get("run_id", "unknown")
    session_id = job.get("session_id", "")
    user_id = job.get("user_id", "")
    language = job.get("language", "zh-TW")

    logger.info(
        "[Worker] Starting job run_id=%s session=%s user=%s", run_id, session_id, user_id
    )

    # 同步 run 狀態到 Redis（讓 API 端知道在跑了）
    from core.shared_cache import set_json

    # 身分欄位另存一份：共用快取的 key 被 API 端以較短 TTL 覆寫後仍可能過期，
    # 結束時寫回狀態不能只靠讀回舊值。
    run_base = {
        "run_id": run_id,
        "session_id": session_id,
        "user_id": user_id,
        "started_at": time.time(),
        "llm_selection": job.get("llm_selection"),
        "user_model_preference": job.get("user_model_preference"),
    }
    set_json(
        f"analysis:run:{run_id}",
        {
            **run_base,
            "status": "running",
            "content": "",
            "error": None,
            "finished_at": None,
        },
        ttl=_RUN_STATUS_TTL,
    )

    # 發 run_started 事件
    from core.analysis_queue import publish_event

    publish_event(run_id, {"type": "run_started", "run_id": run_id})

    invoke_task: Optional[asyncio.Task] = None
    control_task: Optional[asyncio.Task] = None

    try:
        # ── 重建 manager（跟 API 端 bootstrap 完全一樣的參數）──────────────
        from core.agents.bootstrap import bootstrap
        from utils.user_client_factory import create_user_llm_client

        credentials = job["credentials"]
        user_client = create_user_llm_client(
            provider=credentials["provider"],
            api_key=credentials["api_key"],
            model=credentials["model"],
        )

        # Router 模型可指派（2026-09-09 設計 B2）：API 端在 dispatch 前解析好
        # 的 router_selection（含該 provider 的金鑰——與 credentials.api_key
        # 同一信任邊界，只走 transient job envelope）→ worker 重建專用
        # client。缺漏/失敗 → None（Router 跑使用者預設模型＝現行為）。
        router_llm_client = None
        router_selection = job.get("router_selection")
        if isinstance(router_selection, dict) and router_selection.get("provider"):
            try:
                router_llm_client = create_user_llm_client(
                    provider=router_selection["provider"],
                    api_key=router_selection["api_key"],
                    model=router_selection["model"],
                )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:  # noqa: BLE001 — 降級不阻斷分析
                logger.warning(
                    "[Worker] router client 重建失敗（降級為使用者預設模型）: %s",
                    exc,
                )
                router_llm_client = None

        # bootstrap() 是同步函式（內部 PromptRegistry.load、skill 載入、MCP 載入等
        # sync I/O），且 MCP loader 內部 asyncio.run() 會因「已在 running loop」而失敗
        # （RuntimeError: asyncio.run() cannot be called from a running event loop）。
        # 走 run_sync bridge 丟到 thread executor——thread 內無 running loop，
        # asyncio.run() 才合法（對齊 api/routers/analysis.py:_do_bootstrap 的修法）。
        from api.utils import run_sync

        def _do_bootstrap():
            return bootstrap(
                llm_client=user_client,
                web_mode=job.get("web_mode", "web") == "web",
                language=language,
                user_tier=job.get("user_tier", "free"),
                user_id=user_id,
                session_id=session_id,
                key_fingerprint=job.get("key_fingerprint", ""),
                display_name=job.get("display_name"),
                wallet_address=job.get("wallet_address"),
                evm_address=job.get("evm_address"),
                user_model_preference=job.get("user_model_preference"),
                router_llm_client=router_llm_client,
            )

        manager = await run_sync(_do_bootstrap)

        # ── 設 progress_callback → 發事件到 Redis pub/sub ──────────────────
        loop = asyncio.get_running_loop()
        accumulated_content = {"value": ""}
        # 思考內容另外累積（不能併進 content——那是答案本身）。存進
        # metadata.reasoning，讓重整後的歷史還原得出思考區塊。
        accumulated_reasoning: list[str] = []

        def _on_progress(event: Dict[str, Any]) -> None:
            """agent 的 progress callback → 發到 Redis。"""
            event_type = event.get("type", "")
            # token 事件要累積 content（讓 API 端 resync 時有完整快照）
            if event_type == "token":
                chunk = event.get("data", {}).get("chunk", "")
                if chunk:
                    accumulated_content["value"] += chunk
            elif event_type == "reasoning":
                chunk = event.get("data", {}).get("chunk", "")
                if chunk:
                    accumulated_reasoning.append(chunk)

            publish_event(run_id, {"type": "progress", "data": event})

        def _on_progress_threadsafe(event: Dict[str, Any]) -> None:
            loop.call_soon_threadsafe(_on_progress, event)

        manager.progress_callback = _on_progress_threadsafe

        # ── 重建 graph_input（LangGraph Command）────────────────────────────
        graph_input = _rebuild_graph_input(job)

        config = job.get("config", {})
        config.setdefault("configurable", {})
        config["configurable"].setdefault("thread_id", session_id)

        # ── 背景監聽撤銷 ────────────────────────────────────────────────────
        from core.analysis_queue import listen_for_control

        invoke_task = asyncio.create_task(
            asyncio.wait_for(
                manager.graph.ainvoke(graph_input, config),
                timeout=_ANALYSIS_TIMEOUT,
            )
        )

        control_task = asyncio.create_task(
            listen_for_control(run_id, lambda: invoke_task.cancel())
        )

        # ── 執行 ────────────────────────────────────────────────────────────
        result = await invoke_task

        # ── 處理結果 ────────────────────────────────────────────────────────
        interrupt_events = result.get("__interrupt__", []) if isinstance(result, dict) else []
        if interrupt_events:
            # HITL interrupt（consent gate 等）→ 發 hitl_question 事件
            iv = interrupt_events[0].value
            # 問題要存進共用快取：客戶端斷線後 API 端的轉發協程跟著結束，
            # 下面 publish 的 hitl_question 沒人收——續傳只能靠這份重送同意卡
            _update_run_status(
                run_id,
                "waiting",
                content=accumulated_content["value"],
                pending_hitl=iv,
                base=run_base,
            )
            publish_event(run_id, {"type": "hitl_question", "data": iv})
            publish_event(run_id, {"done": True, "waiting": True})
            logger.info("[Worker] Job paused for HITL: run_id=%s", run_id)
            return

        response = result.get("final_response") if isinstance(result, dict) else None
        if not response:
            response = "（分析完成但無最終回覆）"

        # ── 存 DB ───────────────────────────────────────────────────────────
        from api.utils import run_sync
        from core.database.chat import clip_reasoning, save_chat_message

        response_metadata = result.get("response_metadata", {}) if isinstance(result, dict) else {}
        scam_ev = response_metadata.get("scam_evidence")

        # 回答下方顯示「由哪個模型回答」（平台模型只給品牌名；與 API in-process 路徑一致）
        from core.model_config import answer_model_label

        model_label = answer_model_label(
            credentials.get("provider"), credentials.get("model")
        )
        response_metadata = {**response_metadata, "model_label": model_label}
        meta: Dict[str, Any] = {"model_label": model_label}
        if scam_ev:
            meta["scam_evidence"] = scam_ev
        reasoning = clip_reasoning("".join(accumulated_reasoning))
        if reasoning:
            # 歷史還原用：重整後思考區塊才不會整段消失
            meta["reasoning"] = reasoning

        await run_sync(
            lambda: save_chat_message(
                "assistant",
                response,
                session_id=session_id,
                user_id=user_id,
                metadata=meta or None,
            )
        )

        # ── 發 final + done 事件 ────────────────────────────────────────────
        publish_event(
            run_id,
            {"content": response, "type": "final"},
        )
        publish_event(
            run_id,
            {
                "type": "response_metadata",
                "data": response_metadata,
            },
        )
        publish_event(run_id, {"done": True})

        _update_run_status(run_id, "completed", content=response, base=run_base)
        logger.info(
            "[Worker] Job completed: run_id=%s response_len=%d",
            run_id,
            len(response),
        )

    except asyncio.CancelledError:
        logger.info("[Worker] Job cancelled (revoke): run_id=%s", run_id)
        publish_event(
            run_id,
            {
                "type": "revoked",
                "message": "授權已撤銷，Agent 已停止執行。",
                "done": True,
            },
        )
        _update_run_status(run_id, "revoked", base=run_base)
        raise

    except asyncio.TimeoutError:
        logger.error("[Worker] Job timed out: run_id=%s", run_id)
        # 要帶 type：沒 type 的 {"error", "done"} 幀在 API 端會落進一般 done
        # 分支被記成 completed，使用者看不到錯誤
        publish_event(
            run_id,
            {
                "type": "error",
                "error": f"分析超時（超過 {_ANALYSIS_TIMEOUT} 秒）",
                "done": True,
            },
        )
        _update_run_status(
            run_id, "timeout", error="analysis_timeout", base=run_base
        )

    except Exception as exc:
        logger.error("[Worker] Job failed: run_id=%s\n%s", run_id, traceback.format_exc())
        # 原始例外只進 log；推給使用者／寫進狀態的是過濾過的訊息（同 in-process）
        safe_msg = safe_analysis_error_message(exc)
        publish_event(
            run_id,
            {"type": "error", "error": safe_msg, "done": True},
        )
        _update_run_status(run_id, "error", error=safe_msg, base=run_base)

    finally:
        # 撤銷監聽在每條路徑都要收掉（超時／例外路徑以前會留著它空轉到 loop 關閉）
        if control_task is not None:
            control_task.cancel()


def _update_run_status(
    run_id: str,
    status: str,
    *,
    content: str = "",
    error: Optional[str] = None,
    pending_hitl: Any = None,
    base: Optional[Dict[str, Any]] = None,
) -> None:
    """更新 run 狀態到 Redis shared_cache。

    ``base``＝job 自帶的身分欄位：key 過期讀不回舊值時，至少 session_id／
    user_id 還在（續傳靠它們比對擁有者）。
    """
    from core.shared_cache import get_json, set_json

    previous = get_json(f"analysis:run:{run_id}") or {}
    payload = {
        **(base or {}),
        **previous,
        "run_id": run_id,
        "status": status,
        "content": content,
        "error": error,
        "finished_at": time.time(),
    }
    if pending_hitl is not None:
        payload["pending_hitl"] = pending_hitl

    set_json(f"analysis:run:{run_id}", payload, ttl=_RUN_STATUS_TTL)


def _wait_for_dependencies(max_retries: int = 30, interval: int = 5) -> bool:
    """啟動時檢查核心依賴（Redis、DB、agent modules）。

    不直接 crash——失敗就等待重試，讓 compose 有時間把 DB/Redis 起好。
    回傳 True = 依賴就緒，False = 放棄（max_retries 用完）。
    """
    for attempt in range(1, max_retries + 1):
        try:
            # 測試 Redis
            from core.analysis_queue import _get_sync_client

            client = _get_sync_client()
            if client is None:
                raise RuntimeError("Redis not available")
            client.ping()

            # 測試 DB
            from core.database.connection import get_connection

            conn = get_connection()
            conn.close()

            # 測試 agent imports
            from core.agents.bootstrap import bootstrap  # noqa: F401
            from utils.user_client_factory import create_user_llm_client  # noqa: F401

            logger.info(
                "[Worker] Dependencies ready (attempt %d/%d)", attempt, max_retries
            )
            return True

        except KeyboardInterrupt:
            raise
        except Exception as exc:
            logger.warning(
                "[Worker] Dependencies not ready (attempt %d/%d): %s — retrying in %ds",
                attempt,
                max_retries,
                exc,
                interval,
            )
            import time as _time

            _time.sleep(interval)

    logger.error("[Worker] Dependencies failed after %d attempts — giving up", max_retries)
    return False


def _wait_for_schema(timeout_s: int = 300, interval_s: int = 10) -> bool:
    """等 API 端 entrypoint 的遷移跑完（db 版本 == 程式碼 head）再拍快照。

    auto-deploy 會同時滾 API 與 worker——worker 常比 API 的 alembic 早幾分鐘
    就緒（2026-09-05 線上實測：早 2 分鐘），開機快照因此噴出過期的
    SchemaDrift。依賴就緒（連得上 DB）不等於 schema 就緒（遷移跑完），
    這裡補上那段差：等到追上、或 timeout 後照常啟動——那時 SchemaDrift
    說的是真話，本來就該大聲。
    """
    from core.feature_flags import schema_version_pair

    deadline = time.monotonic() + timeout_s
    while True:
        db, head = schema_version_pair()
        if db is not None and head is not None and db == head:
            return True
        if time.monotonic() >= deadline:
            logger.warning(
                "[Worker] schema 未就緒 db=%s head=%s（等了 %ds）——照常啟動，"
                "SchemaDrift 快照說的是真話，查 API 的 entrypoint log",
                db,
                head,
                timeout_s,
            )
            return False
        time.sleep(interval_s)


def main() -> None:
    """Worker 主循環：等待依賴 → BRPOP queue → asyncio.run(job) → 重複。

    設計：
    - 啟動時等待 Redis/DB 就緒（不 crash，符合「待機機制」要求）
    - BRPOP 是阻塞操作——閒置時不佔 CPU（符合「排隊不搶資源」要求）
    - job 失敗不 crash——log + 繼續等下一個 job
    """
    # Zeabur 時代的 worker image（已移除的 Dockerfile.analysis-worker）不含 mcp-servers
    # 目錄——MCP server 是主 Dockerfile build 時 git clone 的。現在 compose 讓 worker
    # 共用 app image，檔案通常都在；但換 image 時仍可能沒有，那樣載入必定失敗。這裡依「檔案實際存在與否」決定，
    # 而非盲從 env：檔案不存在就強制關，避免每個 job 都噴 WARNING。
    if os.getenv("MCP_ENABLED", "").lower() in ("1", "true", "yes"):
        mcp_path = None
        try:
            from core.tools.mcp_loader import _CRYPTO_TRADER_PATH as mcp_path
        except ImportError:
            pass
        if not mcp_path or not os.path.isfile(mcp_path):
            os.environ["MCP_ENABLED"] = "0"
            logger.info(
                "[Worker] MCP server 檔案不存在（worker image 無 mcp-servers），"
                "自動關閉 MCP。這是預期行為——worker 不需要 MCP。"
            )

    logger.info("[Worker] Analysis worker starting...")

    # 等待依賴就緒（不 crash）
    if not _wait_for_dependencies():
        # 依賴始終無法就緒——不 exit（避免 k8s BackOff），進入慢速重試
        logger.warning("[Worker] Entering slow retry mode (60s intervals)")
        while True:
            import time as _time

            _time.sleep(60)
            if _wait_for_dependencies(max_retries=1):
                break
        logger.info("[Worker] Dependencies recovered, entering main loop")

    # 依賴就緒 ≠ schema 就緒：API 的 entrypoint 遷移可能還在跑（auto-deploy
    # 同時滾兩個服務）。先等 schema 追上，開機快照才不會噴過期的 SchemaDrift。
    _wait_for_schema()

    # 依賴就緒後才比對（要用到 Redis）
    from core.feature_flags import log_flag_state

    log_flag_state("analysis-worker", logger)

    from core.analysis_queue import dequeue_job

    logger.info(
        "[Worker] Ready. Waiting for jobs on 'analysis:queue' (idle, no CPU usage)..."
    )

    while True:
        try:
            # BRPOP 阻塞等待——閒置時不佔 CPU
            job = dequeue_job(timeout=30)
            if job is None:
                continue

            logger.info(
                "[Worker] Dequeued job: run_id=%s query=%s",
                job.get("run_id"),
                str(job.get("graph_input", {}).get("update", {}).get("query", ""))[:50],
            )

            asyncio.run(_run_job(job))

        except KeyboardInterrupt:
            logger.info("[Worker] Shutting down (keyboard interrupt)")
            break
        except SystemExit:
            raise
        except Exception as exc:
            logger.error("[Worker] Main loop error: %s — continuing", exc)
            # 不要掛掉——繼續等下一個 job
            import time as _time

            _time.sleep(2)


if __name__ == "__main__":
    main()
