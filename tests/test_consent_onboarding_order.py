"""同意卡與新手清單不重疊：同意前清單讓路、同意後再出現（2026-10-06 截圖）。

行為在 tests/js/consent_onboarding_order.mjs（node 實跑），這裡包成 pytest。
"""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_onboarding_yields_to_legal_consent():
    proc = subprocess.run(
        ["node", "tests/js/consent_onboarding_order.mjs"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "consent_onboarding_order ok" in proc.stdout
