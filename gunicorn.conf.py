"""
Gunicorn 生產環境配置文件
用於多進程部署 Pi Crypto Insight API Server
"""

import os

# ========================================
# Server Socket
# ========================================
bind = "0.0.0.0:8080"
backlog = 2048

# ========================================
# Worker Processes
# ========================================
# 默認 1 worker（雲端小資源環境較穩定，避免啟動期被 OOM/探針誤殺）
# 可再透過 WEB_CONCURRENCY 調整
# 可通過環境變量 WEB_CONCURRENCY 覆蓋
# 直接 int() 會丟出 "invalid literal for int()"，在 gunicorn 設定解析階段
# 噴出來完全看不出是哪個環境變數（2026-09-06 停機一小時就是這樣查了很久）。
# 包一層把變數名講出來；真正的攔截在 docker-entrypoint.sh 的 check_env_sanity。
_web_concurrency = os.getenv("WEB_CONCURRENCY", "1")
try:
    workers = int(_web_concurrency)
except ValueError as exc:
    raise ValueError(
        f"WEB_CONCURRENCY={_web_concurrency!r} 不是整數"
        f"（檢查它有沒有跟別的環境變數黏在同一行）"
    ) from exc

# Uvicorn worker 以支持異步
worker_class = "uvicorn.workers.UvicornWorker"

# Worker 連接數限制
# 降低：async worker 不需要這麼大,每個連線都帶 buffer 佔 RSS
worker_connections = 512

# Worker 最大請求數（防止內存泄漏）
# 10000（2026-09-26，原 1000）：只有 1 個 worker，recycle 時新 worker 還在 import 的那幾秒
# 整站沒人接請求（本機重現：平常 3ms 的請求卡 6.2 秒），記憶體裡的市場資料快取也跟著清空，
# 下一個人要等外部 API 1.5–2 秒。1000 個請求大約是全站 20 次開頁，太常發生。
# VM 上 24h 取樣（~/deploy/memsample.csv）：worker RSS 在 250–420 MB 之間持平、看不出洩漏，
# 容器上限 1536 MB。先調 10 倍，取樣跑完再評估要不要更高。
# 150 會在使用者重整幾次頁面後觸發 recycle，殺掉仍在跑的背景分析（每次重整 ~20 請求）。
max_requests = 10000
max_requests_jitter = 1000

# Worker 超時時間（秒）
# 複雜分析(多輪 LLM + 多工具)可能需要超過 2 分鐘；提高到 180s 讓 Telegram
# 同步聊天端點有時間完成，避免伺服器在運算中途砍掉 worker。
timeout = 180
graceful_timeout = 30
keepalive = 5

# 禁用 preload，降低啟動期共享狀態/連線池問題風險
preload_app = False


# ========================================
# Logging
# ========================================
# 訪問日志 - 預設關閉，避免高流量時大量佔用容器 ephemeral storage
# 若需臨時開啟，可設定 GUNICORN_ACCESSLOG=-
accesslog = os.getenv("GUNICORN_ACCESSLOG")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# 錯誤日志 - 輸出到 stderr（容器友好）
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "warning").lower()

# ========================================
# Process Naming
# ========================================
proc_name = "pi_crypto_insight"

# ========================================
# Server Mechanics
# ========================================
# 守護進程模式（生產環境建議使用 systemd 或 supervisor）
daemon = False

# PID 文件 - 使用 /tmp 以避免權限問題
pidfile = "/tmp/gunicorn.pid"

# 用戶/組（可選，生產環境建議使用非 root 用戶）
# user = "www-data"
# group = "www-data"

# 臨時目錄
tmp_upload_dir = None


# ========================================
# Server Hooks
# ========================================
def on_starting(server):
    """服務器啟動時"""
    print("🚀 Pi Crypto Insight API Server 啟動中...")
    print(f"📊 Workers: {workers}")
    print(f"🔗 Bind: {bind}")


def on_reload(server):
    """重新加載配置時"""
    print("🔄 重新加載配置...")


def when_ready(server):
    """服務器準備就緒時"""
    print("✅ 服務器已就緒，等待請求...")


def post_fork(server, worker):
    """Fork worker 後 - 重置數據庫連接池避免連接衝突"""
    print(f"👷 Worker {worker.pid} 已啟動")

    # 重置連接池，讓每個 worker 創建自己的連接
    # 這是解決 preload_app=True 導致連接共享問題的關鍵
    try:
        from core.database.connection import reset_connection_pool
        from core.orm.session import close_async_engine_sync

        reset_connection_pool()
        close_async_engine_sync()
    except Exception as e:
        print(f"⚠️ Worker {worker.pid} 連接池重置失敗: {e}")

    # 收緊 gc threshold: 預設 (700, 10, 10) 在長壽命 async worker 會延遲回收,
    # 改成 (300, 5, 5) 讓 generation-0 物件更頻繁回收,減少 pandas/numpy
    # 中間 buffer 累積造成的 RSS 高原現象。
    try:
        import gc

        gc.set_threshold(300, 5, 5)
    except Exception:
        pass


def worker_exit(server, worker):
    """Worker 退出時 - 強制 GC 釋放記憶體,避免下次 fork 繼承髒 RSS"""
    print(f"👋 Worker {worker.pid} 已退出")
    try:
        import gc

        gc.collect()
    except Exception:
        pass


def on_exit(server):
    """服務器關閉時"""
    print("🛑 服務器已關閉")
