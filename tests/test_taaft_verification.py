"""/taaft.txt：There's An AI For That 的網域驗證檔（2026-09-24）。

驗證過才能改 TAAFT 自動生成的介紹（要拿掉 signals／買賣建議這類字眼）。
內容要跟 TAAFT 發的碼一字不差、不能多換行。
"""

from __future__ import annotations

import asyncio

import pytest

from api_server import taaft_txt

pytestmark = pytest.mark.unit


def test_serves_exact_code():
    body = asyncio.run(taaft_txt())
    assert body == (
        "taaft-verification-code-"
        "02f5ae9ad0ea51dee814a711316e8a826ae59c909c7ca6545f1f52ff39ff524d"
    )
