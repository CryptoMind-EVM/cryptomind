"""/llms.txt 內容守衛（2026-09-24）：給 AI 搜尋／agent 讀的平台說明不能寫已不存在的功能。

- TON 錢包登入 2026-09-08 移除；登入 = EVM 錢包（SIWE）或 Telegram Mini App。
- 會員只收 Base 上的 USDC（TON 訂單端點回 410）。
- 定位是資料與分析工具：非投資建議、不代管資金、不代為交易。
"""

from __future__ import annotations

import asyncio

import pytest

from api_server import llms_txt

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def body() -> str:
    return asyncio.run(llms_txt())


@pytest.mark.parametrize(
    "stale",
    [
        "Tonkeeper",
        "TON Connect login",
        "JWT cookie from TON Connect",
        "paid in TON",
        "5 AI chat messages",
        "zeabur",
    ],
)
def test_no_stale_claims(body, stale):
    assert stale not in body


@pytest.mark.parametrize(
    "fact",
    [
        "https://getcryptomind.com/",
        "Sign-In with Ethereum",
        "USDC on Base",
        "not financial advice",
        "never holds",
    ],
)
def test_states_current_facts(body, fact):
    assert fact in body


def test_stays_short(body):
    # llmstxt.org 建議精簡；超過 3KB 代表該拆去別的頁面了
    assert len(body.encode("utf-8")) < 3072
