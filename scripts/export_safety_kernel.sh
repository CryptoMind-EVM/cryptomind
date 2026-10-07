#!/bin/bash
# 把審查核心（safety_kernel/）鏡像到公開 repo（cryptomind-safety-kernel）。
#
# 為什麼這樣做：主平台 repo 是私有的，審查核心必須公開才能被審查員/審計員
# 驗證「線上跑的規則 = 開源的規則」。用 git subtree split 只把 safety_kernel/
# 的歷史切出來推到公開 repo，主平台其餘程式碼（分析引擎、提示詞）絕不外洩。
#
# 用法：
#   ./scripts/export_safety_kernel.sh          # 推到公開 repo main
#   ./scripts/export_safety_kernel.sh --create # 首次：gh repo create --public
#
# 事前條件：gh 已登入（gh auth status）、有 push 權限。
# 版本綁定：safety_kernel/__init__.py 的 __version__ 是單一來源；
# 每次發版請同時打 tag v<version>（git tag v0.1.0 && git push origin v0.1.0），
# 主平台 GET /api/swap/kernel-version 回傳的 version 必須與公開 repo 的 tag 一致。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PUBLIC_REPO="aaaa47080/cryptomind-safety-kernel"
PUBLIC_URL="https://github.com/${PUBLIC_REPO}.git"
BRANCH="export-kernel"

# 1. 確認 gh 已登入。
if ! gh auth status >/dev/null 2>&1; then
    echo "❌ gh 未登入（gh auth login）" >&2
    exit 1
fi

# 2. 首次建立（--create）或確認 repo 存在。
if [ "${1:-}" = "--create" ]; then
    if gh repo view "$PUBLIC_REPO" >/dev/null 2>&1; then
        echo "✅ repo 已存在：$PUBLIC_REPO"
    else
        gh repo create "$PUBLIC_REPO" --public \
            --description "Verifiable risk-gating rules for agent-driven money operations (TON / STON.fi Omniston) — CryptoMind safety kernel" \
            --source . --remote=origin >/dev/null
        echo "✅ 已建立公開 repo：$PUBLIC_REPO"
    fi
else
    gh repo view "$PUBLIC_REPO" >/dev/null 2>&1 || {
        echo "❌ repo 不存在，先跑：$0 --create" >&2
        exit 1
    }
fi

# 3. subtree split 出 safety_kernel/ 的獨立歷史（不帶主平台其他檔案）。
VERSION="$(grep -m1 '^__version__' safety_kernel/__init__.py | sed 's/.*= *"\(.*\)".*/\1/')"
echo "🔖 匯出版本：v$VERSION"
git subtree split --prefix safety_kernel -b "$BRANCH" >/dev/null

# 4. 注入公開 repo 的 CI + ruff 設定（鏡像不含主平台的 .github/ 與 ruff.toml）。
#    公開 repo 是扁平佈局（內容在 repo 根），pyproject 用 package-dir 對映——
#    CI 先 pip install . 再跑測試（不裝的話 tests 的 `from safety_kernel...` 會失敗）。
git checkout "$BRANCH" >/dev/null 2>&1
mkdir -p .github/workflows
cat > .github/workflows/ci.yml <<'CI_EOF'
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with:
          python-version: "3.13"
      - name: Install dependencies
        run: pip install pytest ruff .
      - name: Ruff check
        run: ruff check .
      - name: Unit tests
        run: python -m pytest tests/ -v
CI_EOF
# 與主平台一致的 lint 規則（不含主平台專屬的 per-file-ignores）。
# known-first-party：公開 repo 是扁平佈局（safety_kernel 套件裝在 site-packages，
# 不在 repo 根），明確宣告讓 isort 分組與主平台一致（否則空白行分組會不同）。
cat > ruff.toml <<'RUFF_EOF'
[lint]
select = ["E4", "E7", "E9", "F", "I"]

[lint.isort]
known-first-party = ["safety_kernel"]
RUFF_EOF
git add .github/workflows/ci.yml ruff.toml
git commit -q -m "ci: run kernel tests on push/PR; match platform lint rules" || true

# 5. 推送到公開 repo（--force 以支援重新匯出；公開 repo 的歷史由 tag 對照）。
git push --force "$PUBLIC_URL" "$BRANCH:main"

# 6. 打 tag 並推送（版本綁定的對照點——審查員用 tag v<version> 對照線上版本）。
git tag -f "v${VERSION}" "$BRANCH" >/dev/null 2>&1 || true
git push --force "$PUBLIC_URL" "refs/tags/v${VERSION}:refs/tags/v${VERSION}" || true

# 7. 回到原本分支並清理本地暫存分支。
git checkout - >/dev/null 2>&1 || git checkout develop >/dev/null 2>&1
git branch -D "$BRANCH" >/dev/null 2>&1 || true

echo "✅ 已匯出 $PUBLIC_REPO @ v$VERSION"
echo "   驗證：主平台 GET /api/swap/kernel-version 的 version 應等於 v$VERSION"
