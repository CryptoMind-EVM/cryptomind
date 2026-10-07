# 基底映像走 Docker Hub 的鏡像，不直接打 Docker Hub。
#
# Docker Hub 對「未認證的 pull」有速率上限，而 Zeabur builder 走共用出口 IP，
# 額度是跟其他人一起分的。撞到就是這個，建置在第一步（載 metadata）直接死：
#   ERROR: failed to resolve source metadata for docker.io/library/python:3.13-slim:
#          unexpected status from HEAD request ...: 429 Too Many Requests
# 2026-09-07/08 連續失敗、PR #679 因此一直沒上線。重試沒有用——額度不是我們的。
#
# mirror.gcr.io 是 Google 對 Docker Hub 官方映像的 pull-through cache：免認證、
# 不吃 Docker Hub 的匿名額度。已比對過 manifest digest 與 Docker Hub 完全相同
# （sha256:9d2e5553…c00285），是同一個映像不是分支版本。
#
# 要換回 Docker Hub（或換別的鏡像）改這個 ARG 即可，四個 Dockerfile 各自一份：
#   --build-arg PYTHON_IMAGE=python:3.13-slim
ARG PYTHON_IMAGE=mirror.gcr.io/library/python:3.13-slim

FROM ${PYTHON_IMAGE} AS builder

# apt 來源：預設 deb.debian.org（跟 files.pythonhosted.org 同一家 Fastly CDN，線路慢時
# 一起慢）。用 --build-arg APT_MIRROR=https://mirror.twds.com.tw 換鏡像；鏡像須同時
# 提供 /debian 與 /debian-security。留空＝不動。
# ARG 的位置刻意各自貼著用到它的 RUN：改 pip 鏡像不會作廢 apt 那層的快取，反之亦然。
ARG APT_MIRROR=
RUN if [ -n "$APT_MIRROR" ]; then sed -i "s|http://deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; fi

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# --- Python dependencies ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# pip 索引來源：預設官方 PyPI。到 files.pythonhosted.org 的線路慢時（自架環境常見），
# 用 --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple 換鏡像；只影響 build。
# 注意 pip 只會用 HTTP/1.1——挑鏡像要用 curl --http1.1 測，mirrors.aliyun.com 的 h2 快但 1.1 只有 ~80 KB/s。
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL}

COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel \
    && pip wheel --wheel-dir /wheels -r requirements.txt

# --- Frontend dependencies (Node.js + Vite) ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs npm \
    && rm -rf /var/lib/apt/lists/*

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY web/ web/
COPY vite.config.js vite-sw-precache.mjs tailwind.config.js ./
# 強制每次 image build 都重跑 Vite 前端打包。
# Zeabur 會過度快取 `RUN npm run build` 這層，導致改了 web/js 後前端 bundle 卻沒更新
# （JSON 與後端有更新、JS bundle 卻停在舊版）。當部署後前端沒更新時，bump 此值。
ARG FRONTEND_REV=2026-09-13g-nav-auth
RUN echo "frontend build rev: $FRONTEND_REV" && npm run build

FROM ${PYTHON_IMAGE}

# 同 builder 階段
ARG APT_MIRROR=
RUN if [ -n "$APT_MIRROR" ]; then sed -i "s|http://deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; fi

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    XDG_CACHE_HOME=/tmp \
    MPLCONFIGDIR=/tmp \
    NUMBA_CACHE_DIR=/tmp

WORKDIR /app

# --- Python dependencies ---
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# --- MCP server (Python, stdio) — crypto-trader via CoinGecko ---
# git clone 進 image，固定 commit 確保可重現 build。MCP_ENABLED=1 時由
# core/tools/mcp_loader.py 以 `python main.py` 啟動。免 Node、免 API key。
# 同 builder 階段；這裡裝 MCP server 的 requirements 也會打索引
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL}
RUN apt-get update && apt-get install -y --no-install-recommends git cron \
    && git clone https://github.com/SaintDoresh/Crypto-Trader-MCP-ClaudeDesktop.git \
        /app/mcp-servers/crypto-trader \
    && cd /app/mcp-servers/crypto-trader \
    && git checkout d3f5d1d236bff244a77cd205e37ffc097696b83c \
    && pip install -r requirements.txt \
    && rm -rf /var/lib/apt/lists/*

# --- Application code ---
COPY . .

# 確保 entrypoint 可執行（部署時自動跑 alembic upgrade head 再起服務）。
RUN chmod +x /app/docker-entrypoint.sh

# --- Overlay Vite-built frontend assets ---
COPY --from=builder /build/dist/static/index.html web/index.html
# 多頁 build（2026-08-14）：forum/*.html 是獨立頁面，Vite 已把它們的 module script
# 打包進 assets/ 並改寫 script tag。必須覆蓋 raw HTML，否則 module src（/js/*.js）
# 指向的 raw 檔會被下方刪除 → prod 404。
COPY --from=builder /build/dist/static/forum/ web/forum/
COPY --from=builder /build/dist/static/assets/ web/assets/
# Service worker 相關檔在 dist/static/ 根目錄（非 assets/），index.html 會用
# <script src="/static/sw-register.js"> 引用，workbox-*.js 是 sw.js 的依賴。
# 這些檔經由 /static mount 服務在 /static/sw.js 等路徑；後端 /sw.js 路由
# 另外服務同一份（送 Service-Worker-Allowed header）。
COPY --from=builder /build/dist/static/sw.js web/sw.js
COPY --from=builder /build/dist/static/sw-register.js web/sw-register.js
COPY --from=builder /build/dist/static/workbox-*.js web/

# index.html 以 classic <script> 載入 early-init.js / click-delegator.js /
# memory-manager.js / skill-manager.js / tma-mode.js /
# miniapp-host.js（必須先於/獨立於 bundle 執行，Vite 不打包），
# 清理時必須保留這幾支。logger.js 由 forum/*.html 以 classic script 載入，同樣保留。
# scam-tracker/*.html 與 governance/index.html 不在 Vite 多頁 build input，
# 以 classic 頁相容橋 classic-compat{,-app}.js（module）載入共用層並掛回
# window（2026-08-20 線上 404 事故後補；app.js 不在橋內——其 top-level
# AppStore 依賴在子頁會炸，且 8/14 前行為即是如此。tests/test_frontend_static_refs.py
# 會持續看守一致性）。
# 其餘 raw js（SPA 與 forum 多頁 build 的 module）已由 Vite 打包進 assets/。
#
# ⚠️ 只清 web/js 的頂層檔（-maxdepth 1）。web/js/components/ 等子目錄整個不在
#    清理範圍，所以下面的 ! -name 一律寫 basename——`find -name` 不吃路徑，
#    寫成 "components/foo.js" 會是一發空包彈（看起來有保護、其實沒有）。
#    tests/test_dockerfile_prune_guard.py 會擋下這種寫法。
RUN find /app/web/js -maxdepth 1 -type f -name "*.js" \
        ! -name "early-init.js" ! -name "click-delegator.js" ! -name "memory-manager.js" \
        ! -name "skill-manager.js" \
                ! -name "error-boundary.js" \
                ! -name "offline-banner.js" ! -name "logger.js" \
                ! -name "tma-mode.js" ! -name "miniapp-host.js" ! -name "platform-context.js" \
        ! -name "ui-shell.js" ! -name "auth.js" \
        ! -name "apiKeyManager.js" ! -name "i18n.js" \
        ! -name "utils.js" ! -name "api-client.js" \
        ! -name "classic-compat.js" ! -name "classic-compat-app.js" \
        ! -name "subpage-boot.js" \
        ! -name "nav-config.js" ! -name "nav-badges.js" \
        ! -name "site-sidebar.js" -delete \
    && find /app -type d -name "__pycache__" -prune -exec rm -rf {} + \
    && find /app -type f -name "*.py[co]" -delete \
    && addgroup --system app \
    && adduser --system --ingroup app --home /app appuser \
    && mkdir -p /app/data /app/config/keys /tmp/pycache /app/asset-archive \
    && chown -R appuser:app /app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)" || exit 1

USER appuser

# 透過 entrypoint 先套用 DB migrations，再啟動 gunicorn。
#
# ⚠️ 這行 CMD 是 **API service 專用**。docker-compose.prod.yml 裡：
#    - telegram-bot 用自己的 Dockerfile.bot（極簡 image）
#    - analysis-worker／cron-worker 共用這顆 image，但在 compose 用 command 覆蓋掉
#      這行 CMD——migration 只該由 app 跑一次。
#    （Zeabur 時代的 Dockerfile.cron／.analysis-worker 與 zbpack.*.json 已於
#     2026-09-25 移除；2026-09-04 「自訂啟動指令掉了→fallback 回這行 CMD 起整包 API」
#     的事故背景見 docker-entrypoint.sh 開頭的守衛。）
CMD ["sh", "/app/docker-entrypoint.sh"]
