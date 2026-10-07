"""index.html 的 classic pre-bundle scripts 必須在正式 image 中存活。

Vite 只打包 type=module 的進入點；early-init.js / click-delegator.js 以
classic <script> 先於 bundle 執行（主題初始化、CSP 相容的委派點擊處理）。
Dockerfile 清理 web/js 時若把它們刪掉，正式站會 404，所有依賴事件委派的
按鈕（含登入）都會失效。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PRE_BUNDLE_SCRIPTS = {
    "early-init.js",
    "click-delegator.js",
    "error-boundary.js",
    "logger.js",  # 2026-08-25 主 SPA 也要靜音正式站 console（依 APP_CONFIG.DEBUG_MODE）
    "offline-banner.js",  # 2026-08-23 全域 JS 錯誤捕捉（Phase 2）
    "tma-mode.js",  # 2026-09-13 Telegram「TON 模式」：EVM 入口在 Telegram 內藏起來
    "miniapp-host.js",  # 2026-09-13 Base App／Farcaster 宿主：ready＋provider
    "platform-context.js",  # 2026-09-13 Google Play 版：?platform=play → html.play、X-Platform
    "memory-manager.js",
    "skill-manager.js",
    # swapHistory.js 已改為 spa.js 的 ES import(PR #413),Vite 會 bundle,
    # 不再需要 classic <script> + Dockerfile 保留。
    "components/content-modal.js",  # 子目錄——Dockerfile 的 maxdepth 1 清理不會動它
}

# 獨立頁（forum×7 / scam-tracker×2 / governance）載入的 classic scripts——
# 不在 index.html，但同樣必須在正式 image 存活（site-sidebar 全站側欄，
# 設計 docs/plans/2026-08-24-site-sidebar-unification.md）。
SUBPAGE_PAGES = [
    "web/forum/post.html",
    "web/forum/create.html",
    "web/forum/dashboard.html",
    "web/forum/profile.html",
    "web/forum/premium.html",
    "web/scam-tracker/detail.html",
    "web/scam-tracker/submit.html",
    "web/governance/index.html",
]

SUBPAGE_CLASSIC_SCRIPTS = {"site-sidebar.js"}


def _subpage_classic_refs() -> set[str]:
    refs = set()
    for page in SUBPAGE_PAGES:
        html = (PROJECT_ROOT / page).read_text(encoding="utf-8")
        for match in re.finditer(
            r'<script(?![^>]*type="module")[^>]*src="/js/([^"?]+)', html
        ):
            refs.add(match.group(1))
    return refs


def _classic_js_refs() -> set[str]:
    html = (PROJECT_ROOT / "web/index.html").read_text(encoding="utf-8")
    refs = set()
    for match in re.finditer(
        r'<script(?![^>]*type="module")[^>]*src="/js/([^"?]+)', html
    ):
        refs.add(match.group(1))
    return refs


def test_index_classic_scripts_are_exactly_the_preserved_set():
    assert _classic_js_refs() == PRE_BUNDLE_SCRIPTS


def test_pre_bundle_scripts_exist():
    for name in PRE_BUNDLE_SCRIPTS:
        assert (PROJECT_ROOT / "web/js" / name).is_file(), name


def test_dockerfile_preserves_pre_bundle_scripts():
    """兩種保留機制，各自用對的方式驗。

    2026-09-04：本測試原本一律要求 `! -name "<name>"` 出現在 Dockerfile，
    連子目錄的 components/content-modal.js 也要求——於是逼出了一行
    `! -name "components/content-modal.js"`。那行是空包彈：`find -name`
    只比對 basename、不吃路徑。測試綠著，保護是假的（真正保住它的是
    -maxdepth 1）。守衛被自己要求寫出來的那行滿足了。

    現在分開驗：頂層檔看白名單，子目錄檔看 -maxdepth 1 還在不在。
    存不存活由 tests/test_dockerfile_prune_guard.py 實跑那條 find 驗證。
    """
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    prune = dockerfile[dockerfile.index("RUN find /app/web/js") :]
    prune = prune[: prune.index("\n\n")]

    for name in PRE_BUNDLE_SCRIPTS:
        if "/" in name:
            # 子目錄——靠 -maxdepth 1 把整個子目錄排除在清理範圍外。
            # 寫成 ! -name "components/x.js" 不會有任何作用。
            assert "-maxdepth 1" in prune, (
                f"{name} 在子目錄，只有 -maxdepth 1 保得住它；"
                "清理改成遞迴的話必須改用 ! -path 並同步更新本測試"
            )
        else:
            assert f'! -name "{name}"' in prune, (
                f"Dockerfile cleanup must keep {name} "
                "(index.html loads it as a classic script)"
            )




def test_subpage_classic_scripts_are_exactly_site_sidebar():
    """獨立頁的 classic /js/ 引用允許：site-sidebar（本功能）＋既有 early-init /
    components/*（Dockerfile 白名單與 maxdepth 1 已分別保障）。新增其他頂層
    classic 引用時，必須同步補 Dockerfile 白名單與本允許集合。"""
    refs = _subpage_classic_refs()
    allowed = SUBPAGE_CLASSIC_SCRIPTS | {"early-init.js"}
    unexpected = {
        r for r in refs if r not in allowed and not r.startswith("components/")
    }
    assert not unexpected, (
        f"unexpected classic /js/ refs on standalone pages: {sorted(unexpected)}"
    )
    assert SUBPAGE_CLASSIC_SCRIPTS <= refs, (
        "site-sidebar.js missing from some standalone page"
    )


def test_dockerfile_preserves_subpage_classic_scripts():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    for name in SUBPAGE_CLASSIC_SCRIPTS:
        assert f'! -name "{name}"' in dockerfile, (
            f"Dockerfile cleanup must keep {name} (standalone pages load it)"
        )


@pytest.mark.parametrize("name", sorted(PRE_BUNDLE_SCRIPTS | SUBPAGE_CLASSIC_SCRIPTS))
def test_pre_bundle_scripts_have_no_top_level_return(name):
    """These load as classic <script>, where a top-level return silently kills
    the whole file (exactly the bug that broke wallet login). `node --check
    <path>` wraps the file in a CommonJS function and would let it pass, so
    feed it via stdin as a module — that reproduces the browser's parse."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    path = PROJECT_ROOT / "web/js" / name
    result = subprocess.run(
        [node, "--input-type=module", "--check"],
        stdin=path.open("rb"),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"{name} fails browser-equivalent parse:\n{result.stderr}"
    )


# ── Dockerfile 與 deploy.yml 的白名單必須一致 ──────────────────────
# 2026-08-25：兩邊各抄一份，早就分岔了——deploy.yml 的 allowed 只有 8 項、
# Dockerfile 實際保留 20 項，`assert not extra` 一定失敗。
# 而且 deploy.yml 是拿 os.listdir(js_dir) 比對（只列第一層），
# 'components/content-modal.js' 永遠不會出現在裡面，卻被列進 allowed，
# `assert not missing` 也一定失敗。Dockerfile 是實際造 image 的那個，
# 以它為單一真相源。

DEPLOY_WORKFLOW = PROJECT_ROOT / ".github/workflows/deploy.yml"


def _dockerfile_preserved() -> set[str]:
    """Dockerfile 清理步驟保留的檔名。"""
    text = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    block = text[text.index("RUN find /app/web/js") :]
    block = block[: block.index("\n\n")]
    return set(re.findall(r'! -name "([^"]+)"', block))


def _deploy_allowed() -> set[str]:
    """deploy.yml 的 raw-JS 檢查白名單。"""
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r"allowed = \{(.*?)\}", text, re.S)
    assert match, "deploy.yml 找不到 allowed 集合"
    return set(re.findall(r"'([^']+)'", match.group(1)))


