"""E2E：聊天核心的 per-session 狀態與 HITL 續傳（2026-09-25 前端盤查）。

每個測試對應一條盤查確認過的 bug，修之前都會紅：

1. 新對話（currentSessionId=null）送出後 lazy 建 session，但分析狀態與
   AbortController 掛在 null 上——Stop 鈕按了沒反應。
2. 背景對話的串流結束時，用 isAnalyzing／resetChatUI 動到的是「目前」對話，
   使用者正在看的另一個對話的 Stop 鈕被打回送出鈕。
3. HITL 暫停（done+waiting）後串流關閉，stream-close fallback 把同意卡蓋掉。
4. 核准同意卡後的續傳串流：沒標記分析中（停不了、能再送一題）、
   final 幀被累加（答案出現兩次）、思考 token 混進正文、
   續傳中再來一張 consent_gate 掉進 clarify fallback。
5. clarify 問題（LLM 產生）未轉義直接進 innerHTML。
6. 載入更舊的歷史時切到別的對話，A 的舊訊息被塞進 B。
7. confirm／info dialog 的 Esc 監聽用 once——先按任何別的鍵，Esc 就失效。
8. error-boundary：同一個錯誤被 main.js 的 error 監聽再報一次；
   使用者按 Stop 的 AbortError rejection 也被當錯誤上報。
9. 背景對話與 HITL：_activeRunId 是全域一個——A 還在跑、B 又開了一輪，回 A
   按 Stop 撤銷的是 B 的 run；問題抵達時人在別的對話（或切走再切回來、泡泡
   已被 loadChatHistory 換掉），切回來同意卡永遠不會出現。

/api/analyze 用頁內 fetch 替身模擬可控的 SSE 串流（測試決定何時推幀、何時關閉）。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json

import pytest

BASE_URL = "http://127.0.0.1:8770/static/index.html"

pytestmark = pytest.mark.e2e


def _requires_playwright():
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")


# 可控的 /api/analyze 串流：每次呼叫記一筆，測試用 push()/close() 推幀。
# 中止時比照瀏覽器行為讓 body 以 AbortError 失敗。
FAKE_ANALYZE_STREAM = r"""
(() => {
  const origFetch = window.fetch.bind(window);
  window.__analyze = { calls: [] };
  window.fetch = (url, opts = {}) => {
    const u = String(url);
    if (u.endsWith('/api/analyze') && (opts.method || 'GET').toUpperCase() === 'POST') {
      const enc = new TextEncoder();
      let ctrl;
      const stream = new ReadableStream({ start(c) { ctrl = c; } });
      const entry = {
        body: JSON.parse(opts.body || '{}'),
        aborted: false,
        push(frame) { ctrl.enqueue(enc.encode('data: ' + JSON.stringify(frame) + '\n\n')); },
        close() { try { ctrl.close(); } catch (_) {} },
      };
      if (opts.signal) {
        opts.signal.addEventListener('abort', () => {
          entry.aborted = true;
          try { ctrl.error(new DOMException('aborted', 'AbortError')); } catch (_) {}
        });
      }
      window.__analyze.calls.push(entry);
      return Promise.resolve(new Response(stream, {
        status: 200, headers: { 'Content-Type': 'text/event-stream' },
      }));
    }
    return origFetch(url, opts);
  };
})();
"""


async def _open_chat(page, sessions: tuple[str, ...] = (), current: str | None = None):
    """開聊天頁並等 initChat 跑完。

    initChat 是非同步的，跑完會把 currentSessionId 設成「還原的對話」或 null——
    測試若在它之前自己設 currentSessionId 會被蓋掉。所以要既有對話就走正路：
    stub 對話清單＋伺服器端「目前對話」，讓 initChat 自己還原。
    """
    if sessions:
        listing = [
            {
                "id": sid,
                "title": sid,
                "is_pinned": False,
                "updated_at": "2026-09-25T00:00:00",
            }
            for sid in sessions
        ]

        async def _sessions(route):
            if route.request.method == "GET":
                body = {"sessions": listing}
            else:
                body = {"session_id": "sess-e2e-001", "title": "E2E"}
            await route.fulfill(
                status=200, content_type="application/json", body=json.dumps(body)
            )

        async def _current(route):
            body = {"session_id": current} if route.request.method == "GET" else {}
            await route.fulfill(
                status=200, content_type="application/json", body=json.dumps(body)
            )

        await page.route("**/api/chat/sessions*", _sessions)
        await page.route("**/api/chat/current-session", _current)

    await page.add_init_script(FAKE_ANALYZE_STREAM)
    await page.goto(BASE_URL + "#chat", wait_until="domcontentloaded")
    if current:
        await page.wait_for_function(
            f"() => window.currentSessionId === {json.dumps(current)}", timeout=15_000
        )
    else:
        await page.wait_for_selector(
            "#chat-messages .welcome-title", state="attached", timeout=15_000
        )
    await page.wait_for_function(
        "() => { const i = document.getElementById('user-input'); return i && !i.disabled; }",
        timeout=15_000,
    )


async def _send(page, text: str, expected_calls: int):
    await page.fill("#user-input", text)
    await page.click("#send-btn")
    await page.wait_for_function(
        f"() => window.__analyze.calls.length === {expected_calls}", timeout=10_000
    )


async def _push(page, idx: int, *frames: dict, close: bool = False):
    payload = json.dumps(list(frames))
    await page.evaluate(
        f"""() => {{
            const c = window.__analyze.calls[{idx}];
            for (const f of {payload}) c.push(f);
            {"c.close();" if close else ""}
        }}"""
    )


async def _wait_stream_settled(page, session_id: str):
    """sendMessage／submitHITLAnswer 的 finally 會清掉該對話的 controller。"""
    await page.wait_for_function(
        f"() => !window.getAnalysisController({json.dumps(session_id)})", timeout=10_000
    )
    await page.wait_for_timeout(100)  # 讓待處理的 rAF 跑完


async def _stop_button_shown(page) -> bool:
    return await page.evaluate(
        "() => document.getElementById('send-btn').classList.contains('bg-red-500')"
    )


# ── 1. 新對話的 Stop ─────────────────────────────────────────────────────────


async def test_new_chat_stop_button_aborts_stream(page):
    _requires_playwright()
    revokes: list[str] = []

    async def _revoke(route):
        revokes.append(route.request.url)
        await route.fulfill(status=200, content_type="application/json", body="{}")

    await page.route("**/api/analyze/*/revoke", _revoke)
    await _open_chat(page)
    assert await page.evaluate("() => window.currentSessionId") is None

    await _send(page, "Analyze BTC", 1)
    await _push(page, 0, {"type": "run_started", "run_id": "run-new-1"})
    await page.wait_for_function("() => window._activeRunId === 'run-new-1'")

    sid = await page.evaluate("() => window.currentSessionId")
    assert sid == "sess-e2e-001", "lazy 建立的 session 應成為目前對話"
    assert await page.evaluate(f"() => window.isSessionAnalyzing('{sid}')") is True, (
        "新對話送出後，分析狀態要掛在 lazy 建立的 session 上"
    )
    assert await _stop_button_shown(page)

    await page.click("#send-btn")  # Stop
    await page.wait_for_function(
        "() => window.__analyze.calls[0].aborted === true", timeout=5_000
    )
    await _wait_stream_settled(page, sid)
    assert not await _stop_button_shown(page)
    assert any("run-new-1" in u for u in revokes), "Stop 要撤銷後端 run"


# ── 2. 背景對話結束不可動到目前對話 ───────────────────────────────────────────


@pytest.mark.parametrize(
    "final_frames",
    [
        [{"content": "A answer"}, {"done": True}],
        [{"error": "A failed", "done": True}],
    ],
    ids=["done", "error"],
)
async def test_background_session_finish_keeps_current_session_stop(page, final_frames):
    _requires_playwright()
    await _open_chat(page, sessions=("sess-A", "sess-B"), current="sess-A")
    await _send(page, "question in A", 1)
    await _push(page, 0, {"type": "run_started", "run_id": "run-A"})

    await page.evaluate("() => window.switchSession('sess-B')")
    await page.wait_for_function(
        "() => { const i = document.getElementById('user-input'); return i && !i.disabled; }"
    )
    await _send(page, "question in B", 2)
    await _push(page, 1, {"type": "run_started", "run_id": "run-B"})
    assert await page.evaluate("() => window.isSessionAnalyzing('sess-B')") is True

    await _push(page, 0, *final_frames, close=True)
    await _wait_stream_settled(page, "sess-A")

    assert await page.evaluate("() => window.isSessionAnalyzing('sess-B')") is True, (
        "A 在背景結束，不可把 B 的分析旗標清掉"
    )
    assert await _stop_button_shown(page), (
        "B 還在分析，Stop 鈕不能被 A 的收尾打回送出鈕"
    )
    # 輸入框 disabled 不在這裡斷言：app.js 的 updateChatUIState（API key 檢查，
    # switchTab 會觸發）會非同步把它打開，跟分析狀態無關、也不是這次修的範圍
    assert await page.evaluate("() => window._activeRunId") == "run-B"


# ── 3／4. HITL 暫停與續傳 ─────────────────────────────────────────────────────

CONSENT_FRAME = {
    "type": "hitl_question",
    "data": {
        "type": "consent_gate",
        "message": "Need your consent",
        "tools": [{"name": "risky_tool", "display_name": "Risky"}],
    },
}


async def _reach_consent_card(page, session_id: str = "sess-H"):
    await _open_chat(page, sessions=(session_id,), current=session_id)
    await _send(page, "do the risky thing", 1)
    await _push(
        page,
        0,
        {"type": "run_started", "run_id": "run-H"},
        CONSENT_FRAME,
        {"done": True, "waiting": True},
        close=True,
    )
    await _wait_stream_settled(page, session_id)


async def test_hitl_pause_keeps_consent_card(page):
    _requires_playwright()
    await _reach_consent_card(page)
    approve = page.locator(
        '.consent-card [data-click="submitConsent"][data-click-arg="true"]'
    )
    assert await approve.count() == 1, "暫停後同意卡被 stream-close fallback 蓋掉了"
    assert not await _stop_button_shown(page)


async def _approve(page, expected_calls: int):
    await page.click(
        '.consent-card [data-click="submitConsent"][data-click-arg="true"]'
    )
    await page.wait_for_function(
        f"() => window.__analyze.calls.length === {expected_calls}", timeout=10_000
    )


async def test_resume_stream_marks_session_analyzing(page):
    _requires_playwright()
    await _reach_consent_card(page)
    await _approve(page, 2)
    body = await page.evaluate("() => window.__analyze.calls[1].body")
    assert body["resume_answer"] == {"action": "consent", "approved": True}
    await _push(page, 1, {"type": "run_started", "run_id": "run-H2"})
    await page.wait_for_function("() => window._activeRunId === 'run-H2'")

    assert await page.evaluate("() => window.isSessionAnalyzing('sess-H')") is True, (
        "續傳期間要標記分析中，否則能再送一題、也停不了"
    )
    assert await _stop_button_shown(page)

    await page.click("#send-btn")  # Stop
    await page.wait_for_function(
        "() => window.__analyze.calls[1].aborted === true", timeout=5_000
    )
    await _wait_stream_settled(page, "sess-H")
    assert await page.evaluate("() => window.isSessionAnalyzing('sess-H')") is False
    assert not await _stop_button_shown(page)
    cancelled = await page.evaluate(
        "() => !!document.querySelector('#chat-messages .text-orange-400')"
    )
    assert cancelled, "使用者按 Stop 應顯示「已取消」，不是紅字續傳失敗"


async def test_resume_nested_consent_then_final_renders_once(page):
    _requires_playwright()
    await _reach_consent_card(page)
    await _approve(page, 2)

    # 續傳中又遇到同意閘門 → 要畫真正的同意卡，不是 clarify fallback
    await _push(
        page, 1, {"type": "run_started", "run_id": "run-H2"}, CONSENT_FRAME, close=True
    )
    await _wait_stream_settled(page, "sess-H")
    buttons = page.locator('.consent-card [data-click="submitConsent"]')
    assert await buttons.count() == 2, "續傳中的 consent_gate 沒畫出同意卡"
    assert not await _stop_button_shown(page)

    await _approve(page, 3)
    await _push(
        page,
        2,
        {"type": "run_started", "run_id": "run-H3"},
        {"type": "reasoning", "content": "SECRET-THOUGHT"},
        {"type": "token", "content": "Hello "},
        {"type": "token", "content": "world"},
        {"type": "final", "content": "Hello world"},
        {"done": True},
        close=True,
    )
    await _wait_stream_settled(page, "sess-H")

    answer_text = await page.evaluate(
        """() => {
            const bubbles = document.querySelectorAll('#chat-messages .chat-content-ai');
            const last = bubbles[bubbles.length - 1].cloneNode(true);
            last.querySelectorAll('.thinking-container').forEach((n) => n.remove());
            return last.innerText;
        }"""
    )
    assert answer_text.count("Hello world") == 1, (
        f"final 幀要取代累積內容：{answer_text!r}"
    )
    assert "SECRET-THOUGHT" not in answer_text, "思考 token 不可混進答案正文"
    thinking = await page.evaluate(
        "() => [...document.querySelectorAll('#chat-messages .thinking-body')]"
        ".map((n) => n.textContent).join('|')"
    )
    assert "SECRET-THOUGHT" in thinking, "思考內容應放在可摺疊的思考區塊"
    assert await page.evaluate("() => window.isSessionAnalyzing('sess-H')") is False
    assert not await _stop_button_shown(page)


# ── 5. clarify 問題轉義 ─────────────────────────────────────────────────────


async def test_clarify_question_is_escaped(page):
    _requires_playwright()
    await _open_chat(page, sessions=("sess-X",), current="sess-X")
    await _send(page, "ambiguous", 1)
    await _push(
        page,
        0,
        {"type": "run_started", "run_id": "run-X"},
        {
            "type": "hitl_question",
            "data": {
                "type": "clarify",
                "question": "<img src=x id=xss-probe>which one?",
            },
        },
        {"done": True, "waiting": True},
        close=True,
    )
    await _wait_stream_settled(page, "sess-X")
    assert await page.evaluate("() => !document.getElementById('xss-probe')"), (
        "LLM 產生的 clarify 問題被當 HTML 插入"
    )
    text = await page.evaluate(
        "() => document.getElementById('chat-messages').innerText"
    )
    assert "<img src=x id=xss-probe>which one?" in text


# ── 6. 載入更舊歷史時切對話 ──────────────────────────────────────────────────


async def test_load_more_history_ignores_stale_session(page):
    _requires_playwright()

    async def _history(route):
        url = route.request.url
        if "session_id=sess-A" in url and "before_timestamp" in url:
            await asyncio.sleep(1.5)  # 使用者在這段時間切到 B
            data = {
                "history": [
                    {
                        "role": "user",
                        "content": "A-OLDER-MSG",
                        "timestamp": "2026-09-01T00:00:00",
                    }
                ],
                "has_more": False,
            }
        elif "session_id=sess-A" in url:
            data = {
                "history": [
                    {
                        "role": "user",
                        "content": "A-FIRST-MSG",
                        "timestamp": "2026-09-02T00:00:00",
                    }
                ],
                "has_more": True,
            }
        else:
            data = {
                "history": [
                    {
                        "role": "user",
                        "content": "B-ONLY-MSG",
                        "timestamp": "2026-09-03T00:00:00",
                    }
                ],
                "has_more": False,
            }
        await route.fulfill(
            status=200, content_type="application/json", body=json.dumps(data)
        )

    await page.route("**/api/chat/history*", _history)
    await _open_chat(page)
    await page.evaluate("() => window.loadChatHistory('sess-A')")
    await page.wait_for_function(
        "() => document.getElementById('chat-messages').innerText.includes('A-FIRST-MSG')"
    )

    await page.evaluate("() => { window.loadMoreHistory(); }")  # 不等它
    await page.evaluate("() => window.loadChatHistory('sess-B')")
    await page.wait_for_timeout(2_000)  # 等 A 的舊訊息回來

    text = await page.evaluate(
        "() => document.getElementById('chat-messages').innerText"
    )
    assert "B-ONLY-MSG" in text
    assert "A-OLDER-MSG" not in text, "A 的舊訊息被塞進 B"


# ── 7. dialog 的 Esc ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "opener,expected",
    [
        ("window.showConfirmDialog({ title: 'Confirm?' })", False),
        ("window.showInfoDialog({ title: 'Info' })", None),
    ],
    ids=["confirm", "info"],
)
async def test_dialog_escape_works_after_other_keys(page, opener, expected):
    _requires_playwright()
    await page.goto(BASE_URL + "#chat", wait_until="domcontentloaded")
    await page.wait_for_function("() => typeof window.showConfirmDialog === 'function'")
    await page.evaluate(
        f"() => {{ window.__dlgDone = false; {opener}.then((v) => {{ "
        "window.__dlgResult = v === undefined ? null : v; window.__dlgDone = true; }); }"
    )
    await page.wait_for_selector("[data-dialog-action]")
    await page.keyboard.press("a")
    await page.keyboard.press("Escape")
    await page.wait_for_function("() => window.__dlgDone === true", timeout=3_000)
    assert await page.evaluate("() => window.__dlgResult") == expected


# ── 8. error-boundary 上報 ───────────────────────────────────────────────────


async def test_error_boundary_reports_once_and_ignores_abort(page):
    _requires_playwright()
    reported: list[dict] = []

    async def _collect(route):
        try:
            reported.extend(
                json.loads(route.request.post_data or "{}").get("errors", [])
            )
        except ValueError:
            pass
        await route.fulfill(status=200, content_type="application/json", body="{}")

    await page.route("**/api/frontend-errors", _collect)
    await page.goto(BASE_URL + "#chat", wait_until="domcontentloaded")
    await page.wait_for_function(
        "() => typeof window.__flushFrontendErrors === 'function'"
    )
    # main.js 的監聽是 module 載入後才掛的——等 app 初始化完
    await page.wait_for_function("() => typeof window.sendMessage === 'function'")

    await page.evaluate(
        "() => { setTimeout(() => { throw new Error('E2E-BOOM'); }, 0); }"
    )
    await page.evaluate(
        "() => { Promise.reject(new DOMException('E2E-USER-STOP', 'AbortError')); }"
    )
    await page.wait_for_timeout(300)
    await page.evaluate("() => window.__flushFrontendErrors()")
    await page.wait_for_timeout(300)

    messages = [e.get("message", "") for e in reported]
    assert sum("E2E-BOOM" in m for m in messages) == 1, (
        f"同一個錯誤被報了多次：{messages}"
    )
    assert not any("E2E-USER-STOP" in m for m in messages), (
        "使用者按 Stop 的 AbortError 不是錯誤"
    )


# ── 9. 背景對話：per-session run id 與切回來的 HITL 卡 ─────────────────────────


async def _switch(page, session_id: str):
    await page.evaluate(f"() => window.switchSession({json.dumps(session_id)})")
    await page.wait_for_function(
        f"() => window.currentSessionId === {json.dumps(session_id)}"
        " && !document.querySelector('#chat-messages .animate-spin')",
        timeout=10_000,
    )


async def _route_resume_stream(page) -> list[str]:
    """續傳串流（EventSource）：伺服器對 waiting 的 run 重播問題＋done/waiting。"""
    urls: list[str] = []

    async def _stream(route):
        urls.append(route.request.url)
        body = (
            f"id: 1\ndata: {json.dumps(CONSENT_FRAME)}\n\n"
            f"id: 2\ndata: {json.dumps({'done': True, 'waiting': True})}\n\n"
        )
        await route.fulfill(
            status=200, headers={"Content-Type": "text/event-stream"}, body=body
        )

    await page.route("**/api/analyze/stream/**", _stream)
    return urls


APPROVE = '.consent-card [data-click="submitConsent"][data-click-arg="true"]'


async def test_stop_revokes_current_sessions_run_not_the_latest(page):
    _requires_playwright()
    revokes: list[str] = []

    async def _revoke(route):
        revokes.append(route.request.url)
        await route.fulfill(status=200, content_type="application/json", body="{}")

    await page.route("**/api/analyze/*/revoke", _revoke)
    await _open_chat(page, sessions=("sess-A", "sess-B"), current="sess-A")
    await _send(page, "question in A", 1)
    await _push(page, 0, {"type": "run_started", "run_id": "run-A"})

    await _switch(page, "sess-B")
    await _send(page, "question in B", 2)
    await _push(page, 1, {"type": "run_started", "run_id": "run-B"})

    await _switch(page, "sess-A")
    assert await _stop_button_shown(page), "A 還在跑，切回來要看到 Stop"
    await page.click("#send-btn")  # Stop A
    await page.wait_for_function(
        "() => window.__analyze.calls[0].aborted === true", timeout=5_000
    )
    await page.wait_for_timeout(200)

    assert any("run-A" in u for u in revokes), f"Stop A 沒撤銷 run-A：{revokes}"
    assert not any("run-B" in u for u in revokes), (
        f"在 A 按 Stop 撤銷了 B 的 run：{revokes}"
    )
    assert await page.evaluate("() => window.__analyze.calls[1].aborted") is False
    assert await page.evaluate("() => window.isSessionAnalyzing('sess-B')") is True


async def test_hitl_arriving_in_background_renders_when_switching_back(page):
    _requires_playwright()
    streams = await _route_resume_stream(page)
    await _open_chat(page, sessions=("sess-A", "sess-B"), current="sess-A")
    await _send(page, "do the risky thing in A", 1)
    await _push(page, 0, {"type": "run_started", "run_id": "run-A"})

    await _switch(page, "sess-B")
    await _push(page, 0, CONSENT_FRAME, {"done": True, "waiting": True}, close=True)
    await _wait_stream_settled(page, "sess-A")
    assert await page.locator(APPROVE).count() == 0, "A 的同意卡畫到 B 上了"
    assert await page.evaluate("() => window._hitlContext == null"), (
        "A 的問題掛到了 B：B 下一句話會被當成回答送去續傳 A"
    )

    await _switch(page, "sess-A")
    await page.wait_for_selector(APPROVE, timeout=5_000)
    assert await page.locator(APPROVE).count() == 1
    assert any("/api/analyze/stream/run-A" in u for u in streams), streams

    await _approve(page, 2)
    body = await page.evaluate("() => window.__analyze.calls[1].body")
    assert body["session_id"] == "sess-A"
    assert body["resume_run_id"] == "run-A"
    assert body["resume_answer"] == {"action": "consent", "approved": True}


async def test_hitl_card_comes_back_after_switching_away_and_back(page):
    _requires_playwright()
    await _route_resume_stream(page)
    await _open_chat(page, sessions=("sess-A", "sess-B"), current="sess-A")
    await _send(page, "do the risky thing in A", 1)
    await _push(
        page,
        0,
        {"type": "run_started", "run_id": "run-A"},
        CONSENT_FRAME,
        {"done": True, "waiting": True},
        close=True,
    )
    await _wait_stream_settled(page, "sess-A")
    assert await page.locator(APPROVE).count() == 1

    await _switch(page, "sess-B")
    await _switch(page, "sess-A")
    await page.wait_for_selector(APPROVE, timeout=5_000)
    assert await page.locator(APPROVE).count() == 1, "切回來同意卡不見了"


# 可控的 EventSource：重播串流何時送幀由測試決定（route 替身一連上就整段送完，
# 抓不到「上一條還開著就又切回來」的時序）
FAKE_EVENT_SOURCE = r"""
(() => {
  window.__es = [];
  class FakeEventSource {
    constructor(url) { this.url = url; this.readyState = 0; window.__es.push(this); }
    close() { this.readyState = 2; }
    emit(frame) { if (this.readyState !== 2 && this.onmessage) this.onmessage({ data: JSON.stringify(frame) }); }
  }
  FakeEventSource.CLOSED = 2;
  window.EventSource = FakeEventSource;
})();
"""


async def test_switching_back_again_keeps_one_replay_stream_per_session(page):
    """重播還沒送到就又切走再切回來：舊的那條要關掉，同一個 run 只留一條串流、一張卡。"""
    _requires_playwright()
    await page.add_init_script(FAKE_EVENT_SOURCE)
    await _open_chat(page, sessions=("sess-A", "sess-B"), current="sess-A")
    await _send(page, "do the risky thing in A", 1)
    await _push(
        page,
        0,
        {"type": "run_started", "run_id": "run-A"},
        CONSENT_FRAME,
        {"done": True, "waiting": True},
        close=True,
    )
    await _wait_stream_settled(page, "sess-A")

    await _switch(page, "sess-B")
    await _switch(page, "sess-A")  # 第一條重播（還沒送任何幀）
    await _switch(page, "sess-B")
    await _switch(page, "sess-A")  # 第二條

    streams = await page.evaluate(
        "() => window.__es.filter((s) => s.url.includes('/stream/run-A'))"
        ".map((s) => s.readyState)"
    )
    assert len(streams) == 2, streams
    assert streams.count(2) == 1, (
        f"舊的重播串流沒關，同一個 run 兩條同時開著：{streams}"
    )

    await page.evaluate(
        f"""() => {{
            const s = window.__es.filter((x) => x.readyState !== 2).at(-1);
            s.emit({json.dumps(CONSENT_FRAME)});
            s.emit({{ done: true, waiting: true }});
        }}"""
    )
    await page.wait_for_selector(APPROVE, timeout=5_000)
    assert await page.locator(APPROVE).count() == 1


async def test_hitl_after_returning_mid_run_renders_visible_card(page):
    """切走再切回來（泡泡已被 loadChatHistory 換掉），問題才到：要畫在看得到的地方。"""
    _requires_playwright()
    await _route_resume_stream(page)
    await _open_chat(page, sessions=("sess-A", "sess-B"), current="sess-A")
    await _send(page, "do the risky thing in A", 1)
    await _push(page, 0, {"type": "run_started", "run_id": "run-A"})
    await _switch(page, "sess-B")
    await _switch(page, "sess-A")

    await _push(page, 0, CONSENT_FRAME, {"done": True, "waiting": True}, close=True)
    await _wait_stream_settled(page, "sess-A")
    await page.wait_for_selector(APPROVE, timeout=5_000)
    assert await page.locator(APPROVE).count() == 1
