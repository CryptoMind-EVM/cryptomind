"""Lifespan event handlers — startup and shutdown logic for the FastAPI app."""

import asyncio
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

import api.globals as globals
from api.alert_checker import price_alert_check_task
from api.services import (
    funding_rate_update_task,
    load_market_pulse_cache_async,
    update_market_pulse_task,
    update_screener_task,
)
from api.utils import logger
from core.database import init_db
from core.db_ready import (
    mark_db_failed,
    mark_db_init_started,
    mark_db_ready,
    reset_db_ready_state,
)
from core.feature_flags import log_flag_state


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown lifecycle."""
    startup_t0 = time.perf_counter()

    def _startup_mark(step: str, status: str = "ok"):
        elapsed_ms = int((time.perf_counter() - startup_t0) * 1000)
        logger.info(f"🚦 STARTUP[{status}] +{elapsed_ms}ms | {step}")

    _startup_mark("lifespan_enter")
    reset_db_ready_state()
    log_flag_state("api", logger)

    # Langfuse observability（未設 keys / TEST_MODE=true 時自動 no-op）。
    # 早期初始化，讓後續背景 LLM 呼叫也能被追蹤。
    try:
        from utils.langfuse_init import init_langfuse

        init_langfuse()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"⚠️ Langfuse startup hook failed: {e}")

    async def _init_database_background():
        """Run DB initialization in background to avoid blocking readiness on startup."""
        # 標記「lifespan 正在管理 init」——讓 get_connection() 知道要等待這個
        # 背景 init 完成，而不是自己在 request 路徑重跑 init_db（會競態 + 阻塞）。
        # 必須在任何 DB 連線嘗試之前呼叫。
        mark_db_init_started()

        skip_db_init = os.getenv("SKIP_DB_INIT", "false").lower() == "true"
        if skip_db_init:
            logger.info("⏭️ 跳過資料庫初始化 (SKIP_DB_INIT=true)")
            mark_db_ready()
            return

        logger.info("🔄 Initializing database in background...")
        loop = asyncio.get_running_loop()

        # Serialize ALL DB setup across workers via a Postgres advisory lock.
        # With WEB_CONCURRENCY>1, concurrent init_db (CREATE TABLE IF NOT
        # EXISTS) AND Alembic DDL deadlock on AccessExclusiveLocks between
        # workers (seen: Process 244/245 mutual block on relations
        # 16458/16477). pg_advisory_lock is session-scoped + blocking: the
        # first worker runs the full setup; others queue, then their setup
        # is a no-op (init_db is idempotent via IF NOT EXISTS, Alembic tracks
        # version in alembic_version, seeding skips existing rows).
        import psycopg2

        _DB_INIT_LOCK_KEY = 0x4442494E  # "DBIN" — stable across deploys

        # H2 修復（2026-07-20）：psycopg2.connect + pg_advisory_lock 是 sync I/O，
        # 過去直接在 event loop 上跑。advisory_lock 是 blocking call（其他 worker
        # 持有時會等到釋放）— 在 event loop 上等同 deadlock，心跳停止 → SIGABRT。
        # 把 connect + acquire 包進 executor；unlock 同理。
        def _acquire_init_lock():
            conn = psycopg2.connect(os.getenv("DATABASE_URL"))
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_lock(%s)", (_DB_INIT_LOCK_KEY,))
            return conn

        def _release_init_lock(conn):
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (_DB_INIT_LOCK_KEY,))
            finally:
                conn.close()

        _lock_conn = await loop.run_in_executor(None, _acquire_init_lock)
        try:
            logger.info("🔒 Acquired DB init advisory lock (key=%s)", _DB_INIT_LOCK_KEY)

            try:
                # init_db 內部已有重試機制（10次，每次間隔3秒）
                _db_step_t0 = time.perf_counter()
                await loop.run_in_executor(None, init_db)
                logger.info(
                    "✅ Database initialized (%.2fs)",
                    time.perf_counter() - _db_step_t0,
                )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.error(f"⚠️ 資料庫初始化失敗: {e}")
                logger.warning("⏭️ 應用程式將繼續運行，部分功能可能無法使用")
                mark_db_failed(e)
                return  # outer finally releases the lock

            # ORM migration: run Alembic upgrade to head
            #
            # 必須走 run_in_executor——command.upgrade 是同步 DDL 操作（CREATE INDEX
            # 或變更資料表結構可能花 10-60s），直接在 event loop 上跑會凍結整個
            # loop，導致 gunicorn heartbeat 偵測不到 worker 活著 → SIGABRT（cold-start
            # 視窗內 in-flight requests 也會跟著 hang → 前端 15s timeout）。對齊上方
            # init_db 與下方 seed_tools_catalog 的處理方式。
            #
            # **Zeabur 部署可設 LIFESPAN_SKIP_ALEMBIC=true 跳過此步驟**——
            # docker-entrypoint.sh 已在 gunicorn 啟動前同步跑過 alembic upgrade head，
            # lifespan 再跑一次通常只是 no-op（alembic_version 已是 head）但會佔用
            # advisory_lock 期間的時間，導致 cold-start 多 5-30s。設 true 可省下這段。
            #
            # 本機開發預設 false（不跑 docker-entrypoint.sh，靠 lifespan 補）。
            if os.getenv("LIFESPAN_SKIP_ALEMBIC", "false").lower() == "true":
                logger.info(
                    "⏭️  跳過 lifespan Alembic migration (LIFESPAN_SKIP_ALEMBIC=true) "
                    "— 預期 docker-entrypoint.sh 已同步執行過"
                )
            else:
                try:
                    from alembic.config import Config

                    from alembic import command

                    alembic_cfg = Config("alembic.ini")
                    _alembic_t0 = time.perf_counter()
                    await loop.run_in_executor(
                        None, lambda: command.upgrade(alembic_cfg, "head")
                    )
                    logger.info(
                        "ORM Alembic migration complete (head) (%.2fs)",
                        time.perf_counter() - _alembic_t0,
                    )
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception as e:
                    logger.warning("ORM Alembic migration skipped: %s", e)

            # 先 mark_db_ready — seed_tools_catalog 內部呼叫 get_connection()，
            # 而 get_connection 在 server 環境會 wait_for_db_ready_sync() 等 ready。
            # 過去 mark_db_ready() 放在 seed 之後，導致 seed 自己被自己的 init gate
            # 卡住（log: "Database initialization is still in progress"）。
            #
            # 重排後：init_db + alembic 完成 → 立刻 mark_db_ready（schema 已就緒）→
            # seed 才能正常跑。seed 失敗不視為 critical（idempotent，下次啟動會補）。
            mark_db_ready()

            # Seed tools catalog (idempotent — skips existing rows)
            try:
                from core.database.tools import seed_tools_catalog

                _seed_t0 = time.perf_counter()
                await loop.run_in_executor(None, seed_tools_catalog)
                logger.info(
                    "✅ Tools catalog seeded (%.2fs)",
                    time.perf_counter() - _seed_t0,
                )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.warning(f"⚠️ Tools catalog seeding failed: {e}")
        finally:
            await loop.run_in_executor(None, _release_init_lock, _lock_conn)

    # 不阻塞 startup，避免被平台 readiness probe 提前判斷失敗
    asyncio.create_task(_init_database_background())
    _startup_mark("db_init_background_scheduled")

    from core.config import TEST_MODE

    if TEST_MODE:
        logger.warning(
            "⚠️⚠️⚠️ TEST_MODE IS ENABLED! THIS SHOULD NOT BE ON IN PRODUCTION! ⚠️⚠️⚠️"
        )
        logger.warning("Test-only endpoints (e.g., /dev-login) are active.")

    # 預熱 Redis cache connection（避免第一波 request 卡 30-64 秒）。
    # 過去 ``_init_redis`` 是 lazy — 第一個 ``get_cache`` 才觸發同步 connect+ping
    # （最壞 4 秒）。第一波 user request 各自等 Redis 連線，response time 累積爆增。
    # 啟動時就觸發，把這 4 秒吸收進 cold-start 視窗。
    try:
        from core.database.cache import warmup_redis_async

        await warmup_redis_async()
        _startup_mark("redis_cache_warmed")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"Redis cache warmup failed (non-fatal): {e}")
        _startup_mark("redis_cache_warmup_failed", status="warn")

    # 預熱 V4 bootstrap（純載入 PromptRegistry + AgentRegistry，不建立 LLM）
    # 實際 LLM client 由各請求的使用者設定決定，所以 startup 僅驗證模組可 import
    try:
        from core.agents.bootstrap import bootstrap as _v4_bootstrap  # noqa: F401

        logger.info("✅ V4 ManagerAgent 模組載入成功（LLM 將在首次請求時初始化）")
        _startup_mark("v4_manager_module_loaded")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"⚠️ V4 ManagerAgent 模組載入失敗（將 fallback 至 V1 bot）: {e}")
        _startup_mark("v4_manager_module_failed", status="warn")

    globals.v4_manager = None  # 實際 manager 按需在 analysis.py 中建立

    # Startup: 嘗試載入快取
    # [Optimization] Screener/Funding are now In-Memory Only, no DB load needed
    #
    # **必須走 async wrapper**（H1 修復, 2026-07-20）：
    # load_market_pulse_cache 內部呼叫 sync get_cache → get_connection → 若 DB init
    # 仍在進行會 wait_for_db_ready_sync(120s)。但 _init_database_background 是 queue
    # 在同一 event loop，lifespan body 不 yield 它就無法 run → mark_db_ready 永不被
    # 呼叫 → 等 120s timeout → 期間 gunicorn heartbeat 停止 → SIGABRT（worker 每
    # 3-6 分鐘被殺一次的真正 root cause）。
    await load_market_pulse_cache_async()
    _startup_mark("market_pulse_cache_loaded")

    # Startup: 背景預載完整 ticker 列表（台股/美股，含中文名）
    # fire-and-forget，絕不阻塞 lifespan yield（同步重活若在 startup 直接跑，
    # 會卡住 gunicorn worker 心跳 → SIGABRT 無限重啟，歷史教訓）。
    try:
        from core.tools.ticker_preloader import preload_ticker_lists

        asyncio.create_task(preload_ticker_lists())
        _startup_mark("ticker_preload_scheduled")
    except Exception:
        logger.warning("[Startup] ticker preload schedule failed (non-fatal)")
        _startup_mark("ticker_preload_failed")

    # Startup: 啟動背景篩選器更新任務
    # 環境變數 SCREENER_WORKER=1 時,API 不啟動此任務(由獨立 worker 處理)
    # 背景:screener 持有 pandas DataFrame + OKX WS 連線,是 API 容器的主要記憶體消耗者之一
    if not os.getenv("SCREENER_WORKER"):
        asyncio.create_task(update_screener_task())
        _startup_mark("screener_task_scheduled")
    else:
        logger.info("📊 Screener handled by external worker (SCREENER_WORKER=1)")
        _startup_mark("screener_task_external")

    # Market Pulse 任務：檢查是否由獨立 Worker 處理
    # 設置環境變數 MARKET_PULSE_WORKER=1 時，API 不啟動此任務（由獨立 Worker 處理）
    if not os.getenv("MARKET_PULSE_WORKER"):
        logger.info("📊 Starting Market Pulse task in API process...")
        asyncio.create_task(update_market_pulse_task())
        _startup_mark("market_pulse_task_scheduled")
    else:
        logger.info(
            "📊 Market Pulse handled by external worker (MARKET_PULSE_WORKER=1)"
        )
        _startup_mark("market_pulse_task_external")

    # Startup: 啟動 Funding Rate 定期更新任務
    # 環境變數 FUNDING_RATE_WORKER=1 時,API 不啟動此任務
    if not os.getenv("FUNDING_RATE_WORKER"):
        asyncio.create_task(funding_rate_update_task())
        _startup_mark("funding_rate_task_scheduled")
    else:
        logger.info(
            "💸 Funding Rate handled by external worker (FUNDING_RATE_WORKER=1)"
        )
        _startup_mark("funding_rate_task_external")

    # Startup: 啟動價格警報檢查任務
    # 環境變數 PRICE_ALERT_WORKER=1 時,API 不啟動此任務
    if not os.getenv("PRICE_ALERT_WORKER"):
        asyncio.create_task(price_alert_check_task())
        logger.info("Price alert checker task started")
        _startup_mark("price_alert_task_scheduled")
    else:
        logger.info(
            "🔔 Price alert handled by external worker (PRICE_ALERT_WORKER=1)"
        )
        _startup_mark("price_alert_task_external")

    # Startup: 啟動審計日誌清理任務 (Stage 2 Security)
    # 每天凌晨 3 點自動清理超過 90 天的舊日誌
    try:
        from core.audit import audit_log_cleanup_task

        asyncio.create_task(audit_log_cleanup_task())
        logger.info("✅ Audit log cleanup task scheduled (daily at 3 AM UTC)")
        _startup_mark("audit_cleanup_task_scheduled")
    except ImportError:
        logger.warning("⚠️ Audit log cleanup task not available")
        _startup_mark("audit_cleanup_task_unavailable", status="warn")

    # Startup: 對話附圖 30 天保留清理（vision Phase 2，每天 03:30 UTC）
    try:
        from core.orm.chat_attachments_repo import chat_attachments_cleanup_task

        asyncio.create_task(chat_attachments_cleanup_task())
        logger.info("✅ Chat attachments cleanup task scheduled (daily 03:30 UTC)")
        _startup_mark("chat_attachments_cleanup_task_scheduled")
    except ImportError:
        logger.warning("⚠️ Chat attachments cleanup task not available")
        _startup_mark("chat_attachments_cleanup_task_unavailable", status="warn")

    # Startup: 啟動 revoked tokens 定期清理任務(每小時)
    # revoke_token() 已經會順手 prune,但若使用者很少 logout,
    # in-memory dict + disk JSON 會無上限生長。
    try:
        from api.deps import revoked_tokens_cleanup_task

        asyncio.create_task(revoked_tokens_cleanup_task())
        logger.info("✅ Revoked tokens cleanup task scheduled (hourly)")
        _startup_mark("revoked_tokens_cleanup_scheduled")
    except ImportError:
        logger.warning("⚠️ Revoked tokens cleanup task not available")
        _startup_mark("revoked_tokens_cleanup_unavailable", status="warn")

    # 後台覆寫（設定中心，core/setting_overrides.py）：每 60 秒 DB → Redis 重新發佈
    #（Redis 重啟、key 被清掉時自動補回），順便更新 api 的旗標快照
    from core.setting_overrides import republish_task

    asyncio.create_task(republish_task())
    _startup_mark("setting_overrides_republish_scheduled")

    # 行情快取預熱：部署／worker recycle 後第一個開台股、美股頁的人不用等外部 API
    from api.market_warmup import warm_market_caches, warmup_enabled

    if warmup_enabled():
        asyncio.create_task(warm_market_caches())
        _startup_mark("market_warmup_scheduled")

    _startup_mark("startup_ready")

    yield

    # Shutdown: Clean up resources
    logger.info("🛑 Shutting down application...")

    # 關閉 Screener Ticker WebSocket
    try:
        from data.okx_websocket import okx_ticker_ws_manager

        await okx_ticker_ws_manager.stop()
        logger.info("✅ OKX Screener Ticker WebSocket 已關閉")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"❌ 關閉 OKX Ticker WebSocket 時出錯: {e}")

    # 關閉 Binance WebSocket
    try:
        from data.binance_websocket import (
            binance_ticker_ws_manager,
            binance_ws_manager,
        )

        await binance_ws_manager.stop()
        await binance_ticker_ws_manager.stop()
        logger.info("✅ Binance WebSocket 已關閉")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"❌ 關閉 Binance WebSocket 時出錯: {e}")

    # 關閉數據庫連接池
    try:
        from core.database import close_all_connections

        close_all_connections()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"❌ 關閉連接池時出錯: {e}")

    # ORM: Close async engine
    try:
        from core.orm.session import close_async_engine

        await close_async_engine()
        logger.info("✅ ORM async engine closed")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"❌ 關閉 ORM async engine 時出錯: {e}")

    # Langfuse：排空尚未上報的 trace（batched async upload）
    try:
        from utils.langfuse_init import shutdown_langfuse

        shutdown_langfuse()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"⚠️ Langfuse shutdown hook failed: {e}")
