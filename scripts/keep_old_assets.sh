#!/bin/sh
# 部署保留舊版 chunk（2026-10-02）。
#
# 部署後映像只帶新版 hashed chunk：開著的舊頁面一懶載入舊 chunk 就 404，
# stale-chunk-recovery.js 只好整頁重載（一天 merge 幾次，開著的頁面就被刷幾次）。
# app 啟動時：封存（跨部署保留的 volume）裡的舊 chunk 補回這版的 web/assets，
# 這版的存進封存；超過保留天數的封存清掉。檔名帶內容 hash，同名＝同內容，所以都不覆蓋。
#
# 用法：keep_old_assets.sh <這版的 assets 目錄> <封存目錄> [保留天數]
# 任何狀況都 exit 0：這是加分項，不能擋啟動。

ASSETS="${1:-/app/web/assets}"
ARCHIVE="${2:-/app/asset-archive}"
DAYS="${3:-14}"

if [ ! -d "$ASSETS" ]; then
    echo "[assets] $ASSETS 不存在，略過"
    exit 0
fi
if [ ! -d "$ARCHIVE" ] || [ ! -w "$ARCHIVE" ]; then
    echo "[assets] 封存目錄 $ARCHIVE 不存在或不能寫（沒掛 volume？），略過"
    exit 0
fi

# 1) 過期封存先清（時間＝第一次被封存的時候；這版還在用的下面會再存回去）
find "$ARCHIVE" -type f -mtime +"$DAYS" -delete 2>/dev/null
# 2) 舊版補回這版的 assets（不覆蓋這版的檔）
cp -Rn "$ARCHIVE"/. "$ASSETS"/ 2>/dev/null
# 3) 這版存進封存（已經有的不動，保留第一次的時間）
cp -Rn "$ASSETS"/. "$ARCHIVE"/ 2>/dev/null

count=$(find "$ASSETS" -type f | wc -l | tr -d ' ')
echo "[assets] 舊版 chunk 已補回（web/assets 共 $count 個檔，封存保留 $DAYS 天）"
exit 0
