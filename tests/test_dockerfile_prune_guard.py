"""Dockerfile 的 web/js 清理步驟——擋下「看起來有保護、其實沒有」的排除項.

2026-09-04 發現 `! -name "components/content-modal.js"` 是一發空包彈：
`find -name` 只比對 basename、不吃路徑，而且該步驟有 -maxdepth 1，子目錄
本來就不在候選範圍。檔案今天是安全的，但靠的是 -maxdepth 1，不是那行。
哪天有人把 -maxdepth 1 拿掉改成遞迴，這道假防護會安靜地失效。

所以這裡不比對字串，而是**把 Dockerfile 裡那條 find 真的跑一遍**。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_DOCKERFILE = _ROOT / "Dockerfile"
_WEB_JS = _ROOT / "web" / "js"


def _prune_command() -> str:
    """抓出 Dockerfile 裡清 web/js 的那條 find（到第一個 && 為止）。"""
    src = _DOCKERFILE.read_text(encoding="utf-8")
    src = src.replace("\\\n", " ")  # 續行攤平
    for line in src.splitlines():
        if line.startswith("RUN find /app/web/js"):
            return line[len("RUN ") :].split("&&")[0].strip()
    pytest.fail("Dockerfile 裡找不到 web/js 的清理步驟")


def _protected_names() -> list[str]:
    return re.findall(r'!\s+-name\s+"([^"]+)"', _prune_command())


class TestExclusionsAreRealNotDecorative:
    def test_no_exclusion_contains_a_path_separator(self):
        """`find -name` 只吃 basename——含 / 的排除項永遠不會命中。"""
        bogus = [n for n in _protected_names() if "/" in n]
        assert not bogus, (
            f"這些排除項含路徑，find -name 不吃路徑＝空包彈：{bogus}。"
            "要保護子目錄請用 ! -path，或確認 -maxdepth 1 已經涵蓋。"
        )

    def test_every_protected_file_actually_exists(self):
        """名字打錯／檔案改名，排除項一樣是空包彈——只是換個方式沒保護到。"""
        missing = [n for n in _protected_names() if not (_WEB_JS / n).is_file()]
        assert not missing, f"排除項指向不存在的檔案：{missing}"


class TestPruneActuallyRuns:
    """把那條 find 真的跑在 web/js 的副本上，看誰活下來。"""

    @pytest.fixture
    def pruned(self, tmp_path):
        app = tmp_path / "app"
        (app / "web").mkdir(parents=True)
        shutil.copytree(_WEB_JS, app / "web" / "js")
        # as_posix()：str() 在 Windows 給的是反斜線路徑，塞進 sh -c 之後反斜線
        # 被當成跳脫字元吃掉（C:\Users\... → C:Users...），find 找不到目錄。
        cmd = _prune_command().replace("/app/web/js", (app / "web" / "js").as_posix())
        r = subprocess.run(
            ["sh", "-c", cmd], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        assert r.returncode == 0, r.stderr
        return app / "web" / "js"

    def test_protected_top_level_files_survive(self, pruned):
        for name in _protected_names():
            assert (pruned / name).is_file(), f"{name} 被清掉了"

    def test_components_subdir_untouched(self, pruned):
        """子目錄整個不在清理範圍——content-modal.js 靠的是 -maxdepth 1。"""
        before = {p.name for p in (_WEB_JS / "components").glob("*.js")}
        after = {p.name for p in (pruned / "components").glob("*.js")}
        assert before == after
        assert "content-modal.js" in after

    def test_prune_still_deletes_bundled_modules(self, pruned):
        """守衛不能寬到把清理本身變成 no-op——確認確實有東西被清掉。"""
        before = len(list(_WEB_JS.glob("*.js")))
        after = len(list(pruned.glob("*.js")))
        assert after < before, "清理步驟什麼都沒刪，image 會多帶一份未打包的 raw js"
