"""SW 更新檢查失敗不該變成使用者看到的紅色 toast（2026-10-06 部署後實際發生）。

行為在 tests/js/sw_register_update_failure.mjs（node 實跑 sw-register.js），這裡包成 pytest，
並守住 rejection-filter.js（main.js 的全域處理器用它分類）／error-boundary.js 的第二層過濾。
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_update_rejection_is_swallowed_by_sw_register():
    proc = subprocess.run(
        ["node", "tests/js/sw_register_update_failure.mjs"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ok" in proc.stdout


@pytest.mark.parametrize("rel", ["web/js/rejection-filter.js", "web/js/error-boundary.js"])
def test_global_handlers_ignore_service_worker_update_noise(rel):
    src = (REPO / rel).read_text(encoding="utf-8")
    assert "ServiceWorker" in src and "update|register|script" in src, rel
