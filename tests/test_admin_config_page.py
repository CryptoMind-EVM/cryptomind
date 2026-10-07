"""後台「系統配置」頁：功能開關排第一、Edit 手機也點得到（node 實際組一次畫面）。"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
def test_config_page_render():
    out = subprocess.run(
        ["node", "tests/js/admin_config.mjs"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, (out.stderr or out.stdout)[-3000:]
