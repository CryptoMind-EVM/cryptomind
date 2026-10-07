"""歡迎畫面「設定 API Key 開始使用」卡的顯示條件（2026-09-24）。

正式站 BYOK_FALLBACK_ENABLED=true：登入用戶沒綁 key 也有 CryptoMind Lite（免費每日 20 次）
可用，訪客走 guest 額度（下方訪客列已說明）。舊邏輯只看「有沒有自己的 key」，訪客與
有平台模型的人都看到「需要 OpenAI／Gemini／OpenRouter Key 才能啟動」——說法不實、
把新用戶嚇跑。只有「登入、沒 key、平台也沒給模型」才該顯示。

判斷邏輯抽成 shouldShowApiKeyBanner，這裡用 node 實際執行它（不是比對字串）。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SRC = (Path(__file__).resolve().parents[1] / "web/js/chat-sessions.js").read_text(
    encoding="utf-8"
)


def _fn_source(name: str) -> str:
    m = re.search(rf"^function {name}\(.*?^\}}", SRC, re.M | re.S)
    assert m, f"{name} 不存在"
    return m.group(0)


CASES = [
    # (loggedIn, hasKey, platformModel, expected)
    (False, False, None, False),  # 訪客：走 guest 額度，不顯示
    (False, False, {"available": True}, False),
    (True, True, None, False),  # 自己有 key
    (True, False, {"available": True, "label": "CryptoMind Lite"}, False),  # 有平台模型
    (True, False, {"available": False}, True),  # 平台模型掛了又沒 key → 才要引導
    (True, False, None, True),  # 查不到平台模型狀態 → 維持舊行為
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
def test_banner_decision_runs_in_node():
    fn = _fn_source("shouldShowApiKeyBanner")
    script = (
        fn
        + "\nconst cases = "
        + json.dumps(CASES)
        + ";\nconsole.log(JSON.stringify(cases.map(([l, k, p]) => shouldShowApiKeyBanner(l, k, p))));"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [c[3] for c in CASES]


def test_welcome_screen_gates_banner_on_decision():
    body = SRC[SRC.index("async function showWelcomeScreen") :]
    body = body[: body.index("container.innerHTML")]
    assert "fetchPlatformModelStatus" in body
    assert "shouldShowApiKeyBanner(" in body
    assert "const onboardingBanner = showKeyBanner ?" in body
