"""error-boundary.js 離開頁面前用 sendBeacon 補送錯誤：要送成 application/json。

直接給字串會是 text/plain，FastAPI 不解析 JSON body、回 422——使用者離開頁面前
最後一批前端錯誤（例如登入失敗後跳頁）以前全部丟失。
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SRC = (Path(__file__).resolve().parents[1] / "web/js/error-boundary.js").read_text(
    encoding="utf-8"
)


def test_beacon_sends_json_blob():
    call = re.search(
        r"sendBeacon\(\s*'/api/frontend-errors',\s*([^;]+?)\)\s*;", SRC, re.S
    )
    assert call, "找不到 frontend-errors 的 sendBeacon 呼叫"
    payload = call.group(1)
    assert "new Blob(" in payload and "application/json" in payload, payload
