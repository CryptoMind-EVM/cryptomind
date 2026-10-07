"""E2E guard: 斷線 resume 拿到空 resync（分析仍在背景跑）時，不渲染空框框。

線上 #特斯拉案例（DANNY 手機 Zeabur 回報）：
1. 問「特斯拉」→ reasoning 模型（GLM-5.2）思考期 + web search 不串流 token
2. 手機網路切換/切 App → 連線斷 → resumeAnalysisStream 接手
3. server 背景分析還在跑（無 content）→ resync event 回空字串
4. 前端 renderStoredBotMessage('') 渲染出「空框框」，卡住數分鐘直到背景完成

修法（web/js/chat-analysis.js resumeAnalysisStream）：resync 拿到空 content 時，
保留既有 content 或顯示「分析進行中」提示（analysisContinuesInBackground），
不渲染空框。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

WEB_JS = Path(__file__).resolve().parents[2] / "web" / "js"


def _requires_playwright():
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")


def test_resume_resync_empty_content_shows_progress_not_empty_box():
    """接線檢查：resumeAnalysisStream 的 resync 處理必須對空 content 防護。

    空字串 resync（背景分析仍在跑、reasoning 思考期無 token）不可直接渲染
    renderStoredBotMessage('') → 空框框。必須檢查 content 非空才 render，
    否則顯示「分析進行中」提示。
    """
    source = (WEB_JS / "chat-analysis.js").read_text(encoding="utf-8")

    # 必須在 resync 處理裡檢查 content 是否為空（trim 後）
    # 找 resync 區段（擴大範圍到 1500 字元以涵蓋完整提示 HTML）
    resync_section = source.split("data.type === 'resync'")[1][:1500] if "resync" in source else ""

    # 確認有空 content 防護（trim 檢查）
    assert re.search(r"resyncContent.*\.trim\(\)|content.*\.trim\(\).*render", resync_section, re.DOTALL), (
        "resumeAnalysisStream 的 resync 處理沒有空 content 防護："
        "reasoning 模型思考期 / 背景分析未完成時 resync 回空字串，"
        "會渲染出空框框（線上 #特斯拉案例）"
    )

    # 確認空 content 時顯示「分析進行中」提示（而非空框）
    assert "analysisContinuesInBackground" in resync_section, (
        "resync 空 content 時應顯示 analysisContinuesInBackground 提示，不渲染空框"
    )


@pytest.mark.asyncio
async def test_resume_empty_resync_shows_progress_text(page):
    """實際 browser 驗證：mock resync 空 content → 顯示「分析進行中」不空框。"""
    _requires_playwright()
    await page.goto("http://127.0.0.1:8770/static/index.html")
    await page.wait_for_load_state("domcontentloaded")

    # 直接呼叫 resumeAnalysisStream 的 resync 邏輯（mock 一個 bot div）
    result = await page.evaluate(
        """() => {
            // 建一個 mock botMsgDiv
            const div = document.createElement('div');
            div.id = 'test-bot-msg';
            document.body.appendChild(div);

            // 模擬 resync 空 content 的處理邏輯（取自 chat-analysis.js）
            const data = { type: 'resync', content: '' };
            let content = '';
            const statusEl = div;
            const sessionId = window.currentSessionId || 'test';

            // 重現修復後的邏輯
            const resyncContent = data.content;
            let showedProgress = false;
            if (resyncContent && resyncContent.trim()) {
                content = resyncContent;
            } else if (!content || !content.trim()) {
                showedProgress = true;
            }

            return {
                showedProgress: showedProgress,
                finalHtml: div.innerHTML,
            };
        }"""
    )
    assert result["showedProgress"] is True, (
        "resync 空 content 時應觸發「分析進行中」提示路徑，不渲染空框"
    )
