"""全域 unhandledrejection 的分類器已接進 main.js（2026-10-06）。

分類行為在 tests/js/rejection_filter.mjs（node 實跑），這裡包成 pytest 並守住接線：
main.js 必須用 classifyRejection，不能再把任何 rejection 的 reason.message 原文貼成紅色 toast。
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MAIN = (REPO / "web/js/main.js").read_text(encoding="utf-8")


def test_classifier_behaviour():
    proc = subprocess.run(
        ["node", "tests/js/rejection_filter.mjs"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ok" in proc.stdout


def test_main_imports_and_uses_classifier():
    assert "from './rejection-filter.js'" in MAIN
    assert "classifyRejection(" in MAIN


@pytest.mark.parametrize("kind", ["ignore", "stale-chunk", "network", "generic"])
def test_main_handles_every_classification(kind):
    assert f"'{kind}'" in MAIN, f"main.js 沒處理分類 {kind}"


def test_main_uses_localized_messages_for_non_show_kinds():
    assert "common.networkUnstable" in MAIN
    assert "app.unexpectedError" in MAIN


def test_main_no_longer_double_reports_via_console_error():
    """error-boundary 會攔 console.error(Error)；main.js 再 console.error 會讓每則 rejection 報兩次。"""
    assert "console.error('[Unhandled Rejection]'" not in MAIN


def test_sw_noise_rule_lives_in_the_classifier():
    src = (REPO / "web/js/rejection-filter.js").read_text(encoding="utf-8")
    assert "ServiceWorker" in src and "update|register|script" in src
