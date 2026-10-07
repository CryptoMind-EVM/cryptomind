#!/usr/bin/env bash
# 部署後 HTTP 冒煙（curl 版，VM 上不用裝 python／playwright）。
# 部署完可執行；也可以單獨對任何環境跑：
#
#   API_URL=http://127.0.0.1:8080 bash scripts/post_deploy_smoke.sh      # VM 上
#   API_URL=https://getcryptomind.com bash scripts/post_deploy_smoke.sh  # 從外面
#
# 環境變數：
#   API_URL           目標（預設 http://127.0.0.1:8080）
#   SMOKE_HOST        選填：送自訂 Host header（用 IP 打、但 ALLOWED_HOSTS 只放網域時）
#   SMOKE_AUTH_TOKEN  選填：帶登入 token 時，驗證 /api/user/me 回 200
#   TIMEOUT_SECONDS   單一請求逾時（預設 10）
#
# 瀏覽器層級的冒煙（按鈕、i18n、資源 404）在 scripts/smoke_postdeploy.py。
#
# 2026-09-25：拿掉 Pi 時代的 /validation-key.txt 與早已不存在的
# /api/analyze/modes（兩個都會回 404，這支腳本原本對現行版本必定失敗）。
set -euo pipefail

API_URL="${API_URL:-http://127.0.0.1:8080}"
API_URL="${API_URL%/}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-10}"
SMOKE_AUTH_TOKEN="${SMOKE_AUTH_TOKEN:-}"
SMOKE_HOST="${SMOKE_HOST:-}"

tmp_body="$(mktemp)"
trap 'rm -f "${tmp_body}"' EXIT

request() {
  local method="$1"
  local path="$2"
  local extra=()
  if [[ -n "${SMOKE_AUTH_TOKEN}" && "${3:-}" == "auth" ]]; then
    extra+=(-H "Authorization: Bearer ${SMOKE_AUTH_TOKEN}")
  fi
  if [[ -n "${SMOKE_HOST}" ]]; then
    extra+=(-H "Host: ${SMOKE_HOST}")
  fi

  curl -sS \
    -o "${tmp_body}" \
    -w "%{http_code}" \
    --max-time "${TIMEOUT_SECONDS}" \
    -X "${method}" \
    ${extra[@]+"${extra[@]}"} \
    "${API_URL}${path}" || echo "000"
}

fail() {
  echo "FAIL: $*"
  echo "Body (first 300 bytes):"
  head -c 300 "${tmp_body}" || true
  echo
  exit 1
}

echo "Smoke target: ${API_URL}"

# /health 只代表程序活著（目前由 api/routers/system.py 回 {"status":"ok"}）；
# DB 是否可用看 /ready（真的 SELECT 1）。
code="$(request GET /health)"
[[ "${code}" == "200" ]] || fail "/health expected 200, got ${code}（400 多半是 ALLOWED_HOSTS 沒放這個 Host，可設 SMOKE_HOST）"
echo "PASS: /health"

code="$(request GET /ready)"
[[ "${code}" == "200" ]] || fail "/ready expected 200, got ${code}（DB 連不上？）"
grep -qE '"database":[[:space:]]*true' "${tmp_body}" || fail "/ready reports database not ready"
echo "PASS: /ready (database ok)"

code="$(request GET /)"
[[ "${code}" == "200" ]] || fail "/ expected 200, got ${code}"
grep -qi '<html' "${tmp_body}" || fail "/ did not return HTML"
echo "PASS: / (SPA index)"

if [[ -n "${SMOKE_AUTH_TOKEN}" ]]; then
  code="$(request GET /api/user/me auth)"
  [[ "${code}" == "200" ]] || fail "/api/user/me with token expected 200, got ${code}"
  echo "PASS: /api/user/me (authenticated)"
else
  code="$(request GET /api/user/me)"
  [[ "${code}" == "401" || "${code}" == "403" ]] || fail "/api/user/me without auth expected 401/403, got ${code}"
  echo "PASS: /api/user/me access control (unauth blocked)"
fi

echo "Post-deploy smoke checks passed."
