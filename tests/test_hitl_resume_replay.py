"""斷線重連後 HITL 同意卡要重播、續傳串流不可掛住（PR #804 的已知後續）。

症狀：手機上請 AI 記帳／記交易，agent 停在同意卡（hitl_question）等使用者
核准。切到別的 App 再切回來，頁面走 ``resumeAnalysisStream`` 重連——同意卡
不會再出現，而且續傳連線可能永遠掛著（只剩「分析仍在背景進行」）。

根因（後端四處、前端一處）：
1. ``/api/analyze/stream/{run_id}`` 的終態集合不含 ``waiting``：run 暫停等
   回答時，續傳串流重播完就進訂閱迴圈永遠等下去（跨 worker 分支甚至連心跳
   都沒有，每 2 秒輪詢到天荒地老）。
2. 待回答的問題沒跟著 run 存：事件緩衝蓋不到斷點（resync）、重連落在別的
   worker、或客戶端已收過該幀但畫面被斷線提示蓋掉時，都沒東西可重送。
3. in-process 背景收尾（``_finish_analysis_detached``）把 HITL 中斷當成
   ``completed``，問題直接丟掉。
4. worker 模式的本機 run 只是鏡像，靠 ``_stream_from_redis`` 轉發事件更新；
   客戶端一斷線轉發協程跟著被取消，鏡像永遠停在 ``running``——續傳掛住，
   使用者就算看到卡片，核准時 ``_load_resume_model_context`` 也會 409。
5. 前端 ``resumeAnalysisStream`` 不認得 ``hitl_question``，收到
   ``done``（含 ``waiting``）就 ``loadChatHistory`` 整串重繪——卡片不在
   對話紀錄裡，於是消失。
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.routers import analysis

pytestmark = [pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[1]
CHAT_ANALYSIS_JS = (ROOT / "web" / "js" / "chat-analysis.js").read_text(
    encoding="utf-8"
)

USER = {"user_id": "user-1"}
# 帶 NaN：存起來、重送出去都必須是瀏覽器 JSON.parse 認得的嚴格 JSON（#804）
QUESTION = {"type": "journal_consent", "amount": 250, "exchange_rate": float("nan")}


@pytest.fixture(autouse=True)
def shared(monkeypatch):
    """共用快取換成 dict（JSON 來回一次，模擬真的經過 Redis）。"""
    analysis._local_analysis_runs.clear()
    cache: dict = {}

    def _set(key, value, ttl):
        cache[key] = json.loads(json.dumps(value))

    monkeypatch.setattr(analysis.shared_cache, "set_json", _set)
    monkeypatch.setattr(analysis.shared_cache, "get_json", lambda key: cache.get(key))
    yield cache
    analysis._local_analysis_runs.clear()


def _payloads(frames: list[str]) -> list[dict]:
    """SSE 幀 → data JSON（用瀏覽器等級的嚴格 parse：NaN 直接炸）。"""

    def _reject(constant):
        raise ValueError(f"browser JSON.parse rejects: {constant}")

    out = []
    for frame in frames:
        for line in frame.split("\n"):
            if line.startswith("data: "):
                out.append(json.loads(line[len("data: ") :], parse_constant=_reject))
    return out


async def _resume(run_id: str, *, after: int = 0) -> tuple[list[str], asyncio.Task]:
    """開一條續傳串流，回傳 (收到的幀, 讀取 task)。"""
    response = await analysis.resume_analysis_stream(
        run_id, SimpleNamespace(headers={}), after=after, current_user=USER
    )
    frames: list[str] = []

    async def _drain():
        async for chunk in response.body_iterator:
            frames.append(chunk)

    return frames, asyncio.create_task(_drain())


async def _resume_all(run_id: str, *, after: int = 0, timeout: float = 2.0):
    frames, task = await _resume(run_id, after=after)
    try:
        await asyncio.wait_for(task, timeout)
    except asyncio.TimeoutError:
        pytest.fail(f"續傳串流沒有結束（掛住）；已收到：{_payloads(frames)}")
    return _payloads(frames)


def _hitl(payloads: list[dict]) -> list[dict]:
    return [p for p in payloads if p.get("type") == "hitl_question"]


def _assert_ends_waiting(payloads: list[dict]) -> None:
    last = payloads[-1]
    assert last.get("done") is True and last.get("waiting") is True, (
        f"續傳收尾不是 done+waiting，前端無法進入暫停狀態：{last}"
    )


def _paused_run(*, tokens: int = 2) -> dict:
    """照 in-process 串流的順序：token → 暫停（hitl_question）→ done+waiting。"""
    run = analysis._create_analysis_run("sess-1", USER["user_id"])
    for i in range(tokens):
        analysis._emit_run_event(run, {"type": "token", "content": f"t{i}"})
    analysis._pause_run_for_hitl(run, QUESTION)
    analysis._emit_run_event(run, {"done": True, "waiting": True})
    return run


# ── 1. 問題要跟著 run 存（含共用快取）───────────────────────────────────────


def test_pause_persists_question_with_run(shared):
    run = analysis._create_analysis_run("sess-1", USER["user_id"])

    frame = analysis._pause_run_for_hitl(run, QUESTION)

    assert run["status"] == "waiting"
    assert run["pending_hitl"]["amount"] == 250
    assert run["pending_hitl"]["exchange_rate"] is None, "NaN 要在存檔時就清掉"
    stored = shared[analysis._run_cache_key(run["run_id"])]
    assert stored["status"] == "waiting"
    assert stored["pending_hitl"]["amount"] == 250, (
        "問題沒進共用快取：重連落在別的 worker 就沒東西可重送"
    )
    (payload,) = _payloads([frame])
    assert payload["type"] == "hitl_question"
    assert payload["data"]["amount"] == 250


# ── 2. 續傳：waiting 是終態，問題要重送、串流要結束 ─────────────────────────


async def test_resume_replays_question_once_then_ends():
    run = _paused_run()

    payloads = await _resume_all(run["run_id"], after=0)

    assert len(_hitl(payloads)) == 1, f"同一條續傳送了 {len(_hitl(payloads))} 次問題"
    assert _hitl(payloads)[0]["data"]["amount"] == 250
    _assert_ends_waiting(payloads)


async def test_resume_resends_question_when_client_is_already_past_it():
    """客戶端收過 hitl 幀、還沒收到 done 就斷線：catch 會用斷線提示蓋掉卡片，
    ?after= 又已經越過那一幀——必須補送，否則卡片回不來。"""
    run = _paused_run()
    hitl_event_id = run["next_event_id"] - 2  # 最後兩筆：hitl_question、done

    payloads = await _resume_all(run["run_id"], after=hitl_event_id)

    assert len(_hitl(payloads)) == 1, "越過問題幀的重連沒有補送同意卡"
    _assert_ends_waiting(payloads)


async def test_resume_resends_question_when_buffer_needs_resync(monkeypatch):
    monkeypatch.setattr(analysis, "_MAX_REPLAY_EVENTS", 5)
    run = _paused_run(tokens=20)

    payloads = await _resume_all(run["run_id"], after=1)

    assert payloads[0]["type"] == "resync"
    assert len(_hitl(payloads)) == 1, "緩衝蓋不到斷點時沒有重送問題"
    _assert_ends_waiting(payloads)


async def test_resume_on_other_worker_replays_question_and_ends():
    """跨 worker：本機沒有 run，只有共用快取快照。舊碼每 2 秒輪詢到天荒地老。"""
    run = _paused_run()
    analysis._local_analysis_runs.clear()  # 重連落在另一個 worker

    payloads = await _resume_all(run["run_id"])

    assert payloads[0]["type"] == "resync"
    assert len(_hitl(payloads)) == 1
    _assert_ends_waiting(payloads)


async def test_live_resume_subscriber_gets_question_and_ends():
    """重連時還在跑，之後才暫停：訂閱中的續傳要收到問題並結束。"""
    run = analysis._create_analysis_run("sess-1", USER["user_id"])
    frames, task = await _resume(run["run_id"])
    await asyncio.sleep(0.05)  # 讓續傳串流進入訂閱

    analysis._pause_run_for_hitl(run, QUESTION)
    analysis._emit_run_event(run, {"done": True, "waiting": True})

    try:
        await asyncio.wait_for(task, 2.0)
    except asyncio.TimeoutError:
        pytest.fail("run 已暫停等回答，續傳串流卻沒結束（waiting 不是終態）")
    payloads = _payloads(frames)
    assert len(_hitl(payloads)) == 1
    _assert_ends_waiting(payloads)


async def test_resume_ends_for_revoked_run():
    run = analysis._create_analysis_run("sess-1", USER["user_id"])
    analysis._finish_analysis_run(run, "revoked")

    payloads = await _resume_all(run["run_id"])

    assert payloads[-1].get("done") is True


# ── 3. worker 模式：本機鏡像停在 running 時以 worker 寫的狀態為準 ────────────


def _worker_pauses(shared: dict, run: dict) -> None:
    """模擬 worker 的 _update_run_status：只寫共用快取，本機鏡像沒人更新。"""
    key = analysis._run_cache_key(run["run_id"])
    shared[key] = {
        **shared[key],
        "status": "waiting",
        "finished_at": time.time(),
        "pending_hitl": {"type": "journal_consent", "amount": 250},
    }


async def test_resume_with_stale_worker_mirror_uses_shared_status(shared):
    run = analysis._create_analysis_run("sess-1", USER["user_id"])
    _worker_pauses(shared, run)  # 轉發協程已隨原連線被取消，本機仍是 running

    payloads = await _resume_all(run["run_id"])

    assert len(_hitl(payloads)) == 1, "worker 已暫停，續傳卻沒送出問題"
    _assert_ends_waiting(payloads)
    # 核准時 _load_resume_model_context 讀的也是這份——停在 running 會 409
    assert analysis._load_analysis_run(run["run_id"])["status"] == "waiting"


async def test_subscribed_resume_notices_worker_pause_on_heartbeat(shared, monkeypatch):
    monkeypatch.setattr(analysis, "_RESUME_HEARTBEAT_SECONDS", 0.05)
    run = analysis._create_analysis_run("sess-1", USER["user_id"])
    frames, task = await _resume(run["run_id"])
    await asyncio.sleep(0.05)

    _worker_pauses(shared, run)

    try:
        await asyncio.wait_for(task, 2.0)
    except asyncio.TimeoutError:
        pytest.fail("worker 已暫停，但訂閱中的續傳沒有察覺（本機鏡像不會再動）")
    payloads = _payloads(frames)
    assert len(_hitl(payloads)) == 1
    _assert_ends_waiting(payloads)


def test_reconcile_never_moves_a_settled_local_run(shared):
    """in-process：本機先改才同步出去，本機已定案就不該被共用快取改寫。"""
    run = analysis._create_analysis_run("sess-1", USER["user_id"])
    analysis._finish_analysis_run(run, "completed")
    shared[analysis._run_cache_key(run["run_id"])]["status"] = "error"

    assert analysis._load_analysis_run(run["run_id"])["status"] == "completed"


# ── 4. in-process 背景收尾：HITL 中斷要暫停，不是 completed ─────────────────


async def test_detached_interrupt_pauses_run_and_notifies_subscribers(monkeypatch):
    monkeypatch.setattr(
        analysis,
        "save_chat_message",
        lambda *a, **k: pytest.fail("HITL 中斷不可寫進對話紀錄"),
    )
    run = analysis._create_analysis_run("sess-1", USER["user_id"])
    queue: asyncio.Queue = asyncio.Queue()
    run["_subscribers"].add(queue)

    async def interrupted():
        return {"__interrupt__": [SimpleNamespace(value=QUESTION)]}

    await analysis._finish_analysis_detached(
        asyncio.create_task(interrupted()),
        {"at": asyncio.get_running_loop().time()},
        run=run,
        manager=SimpleNamespace(progress_callback=None),
        session_id="sess-1",
        user_id=USER["user_id"],
        language="zh-TW",
    )

    assert run["status"] == "waiting", "背景收尾把 HITL 中斷標成了 completed"
    assert run["pending_hitl"]["amount"] == 250
    sent = _payloads([queue.get_nowait() for _ in range(queue.qsize())])
    assert len(_hitl(sent)) == 1, "已重連的訂閱者沒收到問題"
    _assert_ends_waiting(sent)


# ── 5. worker：暫停時把問題寫進共用快取 ──────────────────────────────────────


async def test_worker_persists_pending_question_in_shared_snapshot(monkeypatch):
    from langgraph.types import Command

    from api.routers.analysis import _build_job_envelope
    from scripts.analysis_worker import _run_job

    cache: dict = {}
    monkeypatch.setattr(
        "core.shared_cache.set_json", lambda key, value, ttl: cache.update({key: value})
    )
    monkeypatch.setattr("core.shared_cache.get_json", lambda key: cache.get(key))
    monkeypatch.setattr("core.analysis_queue.publish_event", lambda *args: None)
    monkeypatch.setattr("core.analysis_queue.listen_for_control", AsyncMock())
    graph = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={"__interrupt__": [SimpleNamespace(value={"amount": 250})]}
        )
    )
    selection = {"provider": "openai", "model": "gpt-5.4-mini"}
    with (
        patch(
            "core.agents.bootstrap.bootstrap", return_value=SimpleNamespace(graph=graph)
        ),
        patch("utils.user_client_factory.create_user_llm_client"),
    ):
        job = _build_job_envelope(
            "run-hitl",
            "session-one",
            "user-one",
            {**selection, "api_key": "fixture"},
            Command(goto="claw_loop", update={}),
            {},
            llm_selection=selection,
        )
        await _run_job(job)

    stored = cache["analysis:run:run-hitl"]
    assert stored["status"] == "waiting"
    assert stored["pending_hitl"] == {"amount": 250}, (
        "worker 沒把問題存進共用快取：轉發斷掉後續傳無從重送"
    )


def _worker_job(run_id: str):
    from langgraph.types import Command

    from api.routers.analysis import _build_job_envelope

    selection = {"provider": "openai", "model": "gpt-5.4-mini"}
    return _build_job_envelope(
        run_id,
        "session-one",
        "user-one",
        {**selection, "api_key": "fixture"},
        Command(goto="claw_loop", update={}),
        {},
        llm_selection=selection,
    )


async def test_worker_status_keeps_owner_after_key_expired(monkeypatch):
    """跑超過 TTL、key 過期後寫回的狀態仍要帶 session_id／user_id。

    回歸鎖：TTL 寫死 900 秒、結束時只靠讀回舊值——長分析暫停在 HITL，
    續傳比對擁有者對不上 → 404。
    """
    from scripts import analysis_worker

    cache: dict = {}
    ttls: list = []

    def _set(key, value, ttl):
        ttls.append(ttl)
        cache[key] = value

    async def _expire_then_pause(*args, **kwargs):
        cache.clear()  # 模擬 key 在分析途中過期
        return {"__interrupt__": [SimpleNamespace(value={"amount": 1})]}

    monkeypatch.setattr("core.shared_cache.set_json", _set)
    monkeypatch.setattr("core.shared_cache.get_json", lambda key: cache.get(key))
    monkeypatch.setattr("core.analysis_queue.publish_event", lambda *args: None)
    monkeypatch.setattr("core.analysis_queue.listen_for_control", AsyncMock())
    graph = SimpleNamespace(ainvoke=_expire_then_pause)
    with (
        patch(
            "core.agents.bootstrap.bootstrap", return_value=SimpleNamespace(graph=graph)
        ),
        patch("utils.user_client_factory.create_user_llm_client"),
    ):
        await analysis_worker._run_job(_worker_job("run-long"))

    stored = cache["analysis:run:run-long"]
    assert stored["status"] == "waiting"
    assert stored["session_id"] == "session-one"
    assert stored["user_id"] == "user-one"
    assert stored["llm_selection"] == {"provider": "openai", "model": "gpt-5.4-mini"}
    assert ttls and all(t > analysis_worker._ANALYSIS_TIMEOUT for t in ttls), ttls


def test_api_sync_does_not_shorten_worker_ttl(monkeypatch):
    """API 與 worker 寫同一把 run key：API 以前固定寫 900 秒，串流連著時每次
    同步都把 worker 設的長 TTL 縮回 15 分鐘，長分析途中 key 就過期。"""
    from scripts import analysis_worker

    ttls: list = []
    monkeypatch.setattr(
        analysis.shared_cache, "set_json", lambda key, value, ttl: ttls.append(ttl)
    )
    run = analysis._create_analysis_run("session-one", "user-one")
    run["content"] = "partial"
    analysis._sync_analysis_run(run, force=True)
    analysis._finish_analysis_run(run, "waiting")

    assert ttls and all(t >= analysis_worker._RUN_STATUS_TTL for t in ttls), ttls
    assert all(t > analysis_worker._ANALYSIS_TIMEOUT for t in ttls), ttls


async def test_worker_cancels_control_listener_when_job_fails(monkeypatch):
    """例外／超時路徑也要收掉撤銷監聽（以前只在成功路徑 cancel）。"""
    from scripts import analysis_worker

    listener_cancelled = asyncio.Event()

    async def _listen(run_id, on_revoke):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            listener_cancelled.set()
            raise

    async def _boom(*args, **kwargs):
        await asyncio.sleep(0.01)  # 讓監聽先跑起來
        raise RuntimeError("boom")

    monkeypatch.setattr("core.shared_cache.set_json", lambda key, value, ttl: None)
    monkeypatch.setattr("core.shared_cache.get_json", lambda key: None)
    monkeypatch.setattr("core.analysis_queue.publish_event", lambda *args: None)
    monkeypatch.setattr("core.analysis_queue.listen_for_control", _listen)
    graph = SimpleNamespace(ainvoke=_boom)
    with (
        patch(
            "core.agents.bootstrap.bootstrap", return_value=SimpleNamespace(graph=graph)
        ),
        patch("utils.user_client_factory.create_user_llm_client"),
    ):
        await analysis_worker._run_job(_worker_job("run-fail"))

    await asyncio.wait_for(listener_cancelled.wait(), timeout=1)


# ── 6. 前端：續傳路徑要渲染同一張卡、進入暫停狀態 ────────────────────────────


def _fn_source(name: str) -> str:
    m = re.search(rf"^function {name}\(.*?^\}}", CHAT_ANALYSIS_JS, re.M | re.S)
    assert m, f"chat-analysis.js 缺 {name}"
    return m.group(0)


RESUME_FRAME_CASES = [
    # (data, hitlShown, expected)；resync 由 onmessage 先處理，不經這裡
    ({"type": "token", "content": "x"}, False, "token"),
    ({"type": "token", "content": ""}, False, "ignore"),
    ({"type": "final", "content": "x"}, False, "final"),
    ({"type": "hitl_question", "data": {"type": "journal_consent"}}, False, "hitl"),
    # 同一條續傳已畫過卡（重播幀＋補送幀、EventSource 自動重連）→ 不再畫
    ({"type": "hitl_question", "data": {"type": "journal_consent"}}, True, "ignore"),
    # 暫停等回答：不可走 finish()（loadChatHistory 整串重繪會把卡片洗掉）
    ({"done": True, "waiting": True, "status": "waiting"}, True, "paused"),
    ({"done": True, "status": "completed"}, False, "finish"),
    ({"error": "boom", "done": True}, False, "finish"),
    ({"type": "revoked", "done": True}, False, "finish"),
    ({"type": "progress", "data": {}}, False, "ignore"),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
def test_resume_frame_classification_runs_in_node():
    fn = _fn_source("classifyResumeFrame")
    script = (
        fn
        + "\nconst cases = "
        + json.dumps(RESUME_FRAME_CASES)
        + ";\nconsole.log(JSON.stringify(cases.map(([d, s]) => classifyResumeFrame(d, s))));"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [c[2] for c in RESUME_FRAME_CASES]


def test_resume_stream_renders_same_card_and_pauses():
    body = _fn_source("resumeAnalysisStream")
    assert "classifyResumeFrame(" in body
    assert "renderHitlQuestion(" in body, "續傳路徑沒有渲染 HITL 卡"
    assert "enterHitlPausedUI(" in body, "續傳路徑收到 waiting 沒有進入暫停狀態"
    paused = body[body.index("action === 'paused'") :]
    paused = paused[: paused.index("return;")]
    # 註解裡寫「不走 finish()」不算——只看程式碼（守衛別被自己的註解觸發）
    paused = re.sub(r"//[^\n]*", "", paused)
    assert "finish()" not in paused and "loadChatHistory" not in paused, (
        "waiting 走了 finish()：整串重繪會把剛畫好的同意卡洗掉"
    )


def test_live_and_resume_paths_share_card_rendering():
    """兩條路徑共用同一份渲染——各寫一份遲早會長歪（卡片型別對不上）。"""
    send = CHAT_ANALYSIS_JS[CHAT_ANALYSIS_JS.index("async function sendMessage") :]
    send = send[: send.index("\nwindow.sendMessage = sendMessage;")]
    assert "renderHitlQuestion(" in send
    assert "enterHitlPausedUI(" in send
    assert "hitlResumeContext: _hitlResumeContext" in send, (
        "斷線續傳沒拿到 HITL 上下文：卡片畫得出來，核准卻送不回去"
    )
    # 型別分派只能有一份（在 renderHitlQuestion 裡）
    assert CHAT_ANALYSIS_JS.count("idata.type === 'journal_consent'") == 1
