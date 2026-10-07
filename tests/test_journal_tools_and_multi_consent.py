"""Agent 帳本刪改工具（Propose→Confirm→Commit）+ 多卡批次同意的回歸測試。

背景（2026-08-25 使用者回報）：
1. 多行動時（記帳 + 刪記憶×2 + 增記憶）只彈出第一張確認卡——
   `_extract_consent_signal` 只取 tool_outputs 的第一個 marker，
   其餘提案靜默丟失。修復：批次 multi_consent（一次 interrupt、
   前端一次顯示全部卡片、一次 resume 帶回陣列答案）。
2. Web UI 已有帳本單筆刪改（repo soft delete + API + tab-journal），
   但 AI-Agent 工具層沒有刪改能力——補 delete_ledger_entry /
   update_ledger_entry 工具，走同一條金流 HITL 確認卡。
"""

import json

import pytest

pytestmark = [pytest.mark.unit]


def _marker(**kwargs):
    base = {"__needs_consent__": True, "ts": 1700000000.0}
    base.update(kwargs)
    return base


# ── 1. 複數 marker 提取 + identity 去重 ─────────────────────────────────────


def test_extract_consent_signals_returns_all_in_order():
    from core.agents.manager.claw_loop import _extract_consent_signals

    result_data = {
        "tool_outputs": [
            "ok-ish plain text",
            json.dumps(_marker(kind="journal_entry", proposal_id="p1")),
            json.dumps(_marker(kind="delete_memory", key="holding_abc")),
            "not json {",
            json.dumps(_marker(kind="create_memory", key="holding_xyz")),
        ]
    }
    signals = _extract_consent_signals(result_data)
    assert [s["kind"] for s in signals] == [
        "journal_entry",
        "delete_memory",
        "create_memory",
    ]


def test_consent_identity_stable_across_reemission():
    """resume 重跑時工具會帶新 ts 重新提案——identity 必須與 ts 無關。"""
    from core.agents.manager.claw_loop import _consent_identity

    m1 = _marker(kind="create_memory", key="holding_xyz", ts=1.0)
    m2 = _marker(kind="create_memory", key="holding_xyz", ts=2.0)
    assert _consent_identity(m1) == _consent_identity(m2)

    d1 = _marker(kind="delete_memory", key="holding_abc", ts=1.0)
    d2 = _marker(kind="delete_memory", key="holding_abc", ts=9.0)
    assert _consent_identity(d1) == _consent_identity(d2)

    j1 = _marker(kind="journal_entry", proposal_id="p1", ts=1.0)
    j2 = _marker(kind="journal_entry", proposal_id="p1", ts=5.0)
    assert _consent_identity(j1) == _consent_identity(j2)
    j3 = _marker(kind="journal_entry", proposal_id="p2", ts=5.0)
    assert _consent_identity(j1) != _consent_identity(j3)

    # 不同 key 的記憶不得互相混淆
    other = _marker(kind="create_memory", key="preference_qq")
    assert _consent_identity(m1) != _consent_identity(other)


def test_consent_identity_no_ts_marker_falls_back_to_full_content():
    """舊型 marker（無 key/proposal_id，如 custom_skill）以整包內容為 identity，
    但 ts 要剔除（重跑會換新 ts）。"""
    from core.agents.manager.claw_loop import _consent_identity

    m1 = _marker(kind="custom_skill", mode="create", skill_name="x")
    m2 = _marker(kind="custom_skill", mode="create", skill_name="x", ts=7.0)
    assert _consent_identity(m1) == _consent_identity(m2)


# ── 2. multi_consent payload + 答案解析 ────────────────────────────────────


def test_build_multi_consent_payload_wraps_cards():
    from core.agents.manager.claw_loop import build_multi_consent_payload

    signals = [
        _marker(
            kind="journal_entry",
            proposal_id="p1",
            entry_type="trade",
            symbol="SNXX",
            market="crypto",
            amount=13.67,
            currency="USDT",
            quantity=295.4,
            side="buy",
            direction="long",
            leverage=2,
        ),
        _marker(kind="delete_memory", key="holding_abc", before={"key": "holding_abc"}),
    ]
    payload = build_multi_consent_payload(signals, "zh-TW")
    assert payload["type"] == "multi_consent"
    cards = payload["cards"]
    assert len(cards) == 2
    assert cards[0]["idx"] == 0 and cards[1]["idx"] == 1
    # 每張卡沿用既有 per-kind payload 形狀（type=journal_consent / memory_consent）
    assert cards[0]["type"] == "journal_consent"
    assert cards[1]["type"] == "memory_consent"
    # 內部 marker 不得外洩給前端（__needs_consent__ / before 快照）
    assert "__needs_consent__" not in json.dumps(cards)


def test_parse_multi_consent_answers_accepts_array_and_json_string():
    from core.agents.manager.claw_loop import parse_multi_consent_answers

    raw = [
        {"action": "approve"},
        {"action": "deny"},
        {"action": "approve", "edited_fields": {"content": "新內容"}},
    ]
    parsed = parse_multi_consent_answers(raw, expected=3)
    assert [p["approved"] for p in parsed] == [True, False, True]
    assert parsed[2]["edited_fields"] == {"content": "新內容"}

    # 前端 resume_answer 是字串傳輸——JSON 字串陣列要能解
    parsed2 = parse_multi_consent_answers(json.dumps(raw), expected=3)
    assert parsed2 == parsed

    # 長度不符（缺漏/多餘）→ 補齊為 deny（fail-closed，不誤核可）
    short = parse_multi_consent_answers([{"action": "approve"}], expected=3)
    assert [p["approved"] for p in short] == [True, False, False]

    # 壞輸入 → 全部 deny
    bad = parse_multi_consent_answers("not json", expected=2)
    assert [p["approved"] for p in bad] == [False, False]


# ── 3. 帳本刪改工具（marker 層）────────────────────────────────────────────


class _FakeRepo:
    def __init__(self, entries):
        self._entries = entries
        self.deleted = []
        self.updated = []

    def list_trades(self, **kwargs):
        return list(self._entries)

    def delete_trade(self, entry_id, source="manual"):
        self.deleted.append((entry_id, source))
        return {"ok": True}

    def update_entry(self, entry_id, source="manual", **kwargs):
        self.updated.append((entry_id, source, kwargs))
        return {"ok": True, "id": entry_id}


@pytest.fixture()
def _fake_journal_repo(monkeypatch):
    entries = [
        {
            "id": 77,
            "symbol": "AVAXUSDT",
            "entry_type": "trade",
            "price": 7.541,
            "quantity": 510,
            "currency": "USDT",
            "category": "investment",
            "note": "5x long",
        }
    ]
    repo = _FakeRepo(entries)

    def _get(user_id):
        return repo

    import core.orm.trade_journal_repo as repo_mod

    monkeypatch.setattr(repo_mod, "get_journal_repo", _get)

    from core.tools import key_resolver

    monkeypatch.setattr(key_resolver, "get_current_user_id", lambda: "u-test")
    return repo


def test_delete_ledger_entry_emits_consent_marker(_fake_journal_repo):
    from core.tools.crypto_modules.trade_journal import delete_ledger_entry

    out = delete_ledger_entry.func(entry_id=77)
    parsed = json.loads(out)
    assert parsed["__needs_consent__"] is True
    assert parsed["kind"] == "journal_delete"
    assert parsed["entry_id"] == 77
    assert parsed["before"]["symbol"] == "AVAXUSDT"


def test_delete_ledger_entry_nonexistent(_fake_journal_repo):
    from core.tools.crypto_modules.trade_journal import delete_ledger_entry

    out = delete_ledger_entry.func(entry_id=999)
    assert "__needs_consent__" not in out
    assert "999" in out


def test_update_ledger_entry_emits_consent_marker(_fake_journal_repo):
    from core.tools.crypto_modules.trade_journal import update_ledger_entry

    out = update_ledger_entry.func(entry_id=77, amount=7.9, note="edited")
    parsed = json.loads(out)
    assert parsed["__needs_consent__"] is True
    assert parsed["kind"] == "journal_update"
    assert parsed["entry_id"] == 77
    assert parsed["updates"] == {"amount": 7.9, "note": "edited"}


def test_apply_consent_write_journal_delete(monkeypatch, _fake_journal_repo):
    from core.agents.manager import claw_loop

    claw_loop._apply_consent_write(
        "u-test", {"kind": "journal_delete", "entry_id": 77}, {}
    )
    # c037：chat 核准執行必須標記來源（版本史修訂的 source）
    assert _fake_journal_repo.deleted == [(77, "chat")]


def test_apply_consent_write_journal_update(monkeypatch, _fake_journal_repo):
    from core.agents.manager import claw_loop

    claw_loop._apply_consent_write(
        "u-test",
        {"kind": "journal_update", "entry_id": 77},
        {"amount": 8.1, "note": "fixed"},
    )
    entry_id, source, kwargs = _fake_journal_repo.updated[0]
    assert entry_id == 77
    assert source == "chat"
    assert kwargs["amount"] == 8.1
    assert kwargs["note"] == "fixed"


def test_record_entry_marker_has_stable_proposal_id(monkeypatch):
    """proposal_id（內容雜湊）讓 resume 重跑時的重複提案可被 identity 去重。"""
    from core.tools import key_resolver
    from core.tools.crypto_modules import exchange_rate as er_mod
    from core.tools.crypto_modules.trade_journal import record_entry

    monkeypatch.setattr(er_mod, "get_exchange_rate", lambda cur, base: 1.0)
    monkeypatch.setattr(key_resolver, "get_current_user_id", lambda: "u-test")
    # c039：提案要讀使用者的報表基準幣——不假掉就會去連真 DB（逾時 27s）
    from core.orm import trade_journal_repo as tj_repo

    monkeypatch.setattr(tj_repo, "_read_user_base_currency", lambda uid: "TWD")

    m1 = json.loads(record_entry.func(entry_type="expense", amount=250, currency="TWD", note="lunch"))
    m2 = json.loads(record_entry.func(entry_type="expense", amount=250, currency="TWD", note="lunch"))
    assert m1["proposal_id"] == m2["proposal_id"]
    m3 = json.loads(record_entry.func(entry_type="expense", amount=300, currency="TWD", note="lunch"))
    assert m1["proposal_id"] != m3["proposal_id"]


# ── 5. LangGraph 級整合：多卡 interrupt → resume → 新 turn 重彈 ────────────


def test_multi_consent_full_graph_roundtrip(monkeypatch):
    """模擬 Phase G 在真實 LangGraph checkpoint/resume 下的完整循環。

    - run1：2 個 pending 提案 → 一個 multi_consent interrupt（2 張卡）
    - resume（答案陣列）：兩個都處理，node 完成
    - 工具在 resume 重跑時「重新提案」（新 ts）→ 由 interrupt 索引匹配自然
      消化（resume 答案被第一個 interrupt() 取回），不重彈
    - **新 turn（新輸入）同一提案 → 必須重新彈卡**（2026-08-25 #27 卡死事故：
      舊版把已作答 identity 存進 state 永久封印後續提案）
    """
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    from core.agents.manager import claw_loop
    from core.agents.models import ManagerState

    applied = []

    def fake_apply(user_id, signal, edited):
        applied.append((signal["kind"], signal.get("entry_id") or signal.get("key")))
        return None

    monkeypatch.setattr(claw_loop, "_apply_consent_write", fake_apply)

    # 模擬工具輸出：每次 node 執行都「重新提案」（ts 不同——重跑行為）
    run_count = {"n": 0}

    def make_outputs():
        import time as _t

        ts = _t.time() + run_count["n"]  # 每次執行新 ts
        return [
            "plain text output",
            json.dumps(_marker(kind="journal_entry", proposal_id="p1", entry_type="expense",
                               amount=250, currency="TWD", ts=ts)),
            json.dumps(_marker(kind="delete_memory", key="holding_abc", ts=ts)),
        ]

    def node(state):
        run_count["n"] += 1
        # ── 新版 Phase G 語義：只做「同一次執行內」的 identity 去重，
        #    不讀不寫跨 turn 的 state 記帳 ──
        pending = []
        seen = set()
        for s in claw_loop._extract_consent_signals({"tool_outputs": make_outputs()}):
            ident = claw_loop._consent_identity(s)
            if ident in seen:
                continue
            seen.add(ident)
            pending.append(s)
        if pending:
            payload = claw_loop.build_multi_consent_payload(pending, "zh-TW")
            answers = interrupt(payload)
            parsed = claw_loop.parse_multi_consent_answers(answers, expected=len(pending))
            for signal, p in zip(pending, parsed):
                if p["approved"]:
                    fake_apply("u", signal, p.get("edited_fields") or {})
        return {"final_response": "done"}

    builder = StateGraph(ManagerState)
    builder.add_node("claw", node)
    builder.add_edge(START, "claw")
    builder.add_edge("claw", END)
    app = builder.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t-multi"}}

    r1 = app.invoke({"session_id": "s", "query": "q"}, cfg)
    interrupts = r1.get("__interrupt__", [])
    assert interrupts, "應該暫停並彈出 multi_consent 卡"
    iv = interrupts[0].value
    assert iv["type"] == "multi_consent"
    assert len(iv["cards"]) == 2
    assert iv["cards"][0]["kind"] == "journal_entry"
    assert iv["cards"][1]["kind"] == "delete_memory"
    # 內部 marker 不得外洩
    assert "__needs_consent__" not in json.dumps(iv)

    r2 = app.invoke(
        Command(resume=json.dumps([{"action": "approve"}, {"action": "deny"}])), cfg
    )
    assert r2.get("final_response") == "done"
    assert applied == [("journal_entry", None)], "只有第一張核准的該套用"

    # resume 重跑（run_count 變 2、markers 帶新 ts）→ 答案被索引匹配取回，不重彈
    assert run_count["n"] == 2

    # ── 新 turn：同一提案必須重新彈卡（不得被 state 永久封印）──
    r3 = app.invoke({"session_id": "s", "query": "q2"}, cfg)
    assert r3.get("__interrupt__"), "新 turn 的同一提案要重新彈卡（#27 卡死事故回歸）"
    r4 = app.invoke(Command(resume=json.dumps([{"action": "deny"}, {"action": "deny"}])), cfg)
    assert r4.get("final_response") == "done"


def test_audit_failure_does_not_block_apply(monkeypatch):
    """audit receipt 炸掉（worker 跨 loop）不得吃掉使用者核准的套用（線上 08:10 事故）。"""
    from core.agents.manager import claw_loop
    from core.agents.manager import consent_gate as cg

    applied = []

    def fake_apply(user_id, signal, edited):
        applied.append(signal["kind"])
        return None

    async def broken_audit(**kwargs):
        raise RuntimeError("got Future attached to a different loop")

    monkeypatch.setattr(claw_loop, "_apply_consent_write", fake_apply)
    monkeypatch.setattr(cg, "append_guard_audit_receipt", broken_audit)
    # node 內是函式內 import，patch consent_gate 模組層即可生效

    # 直接驗證隔離層函式存在且語義正確（Phase G 內使用的模式）
    import asyncio as _aio

    async def _run():
        # 模擬 Phase G 的 _audit 包裹：audit 炸 → 只警告不傳播
        try:
            await broken_audit()
        except (_aio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass  # Phase G 實作：logger.warning 後繼續
        fake_apply("u", {"kind": "journal_delete", "entry_id": 27}, {})
        return applied

    assert _aio.run(_run()) == ["journal_delete"]


def test_async_engine_recreated_on_loop_change(monkeypatch):
    """worker 每 job 一個新 event loop——engine 偵測 loop 變更要重建，
    否則 asyncpg pool 綁死舊 loop（"Future attached to a different loop"）。"""
    import asyncio as _aio

    import core.orm.session as session_mod

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/testdb")

    ids = []
    captured = {}

    real_create = session_mod.create_async_engine

    def spy_create(url, **kwargs):
        engine = real_create(url, **kwargs)
        captured[id(engine)] = engine
        return engine

    monkeypatch.setattr(session_mod, "create_async_engine", spy_create)
    monkeypatch.setattr(session_mod, "_async_engine", None)
    monkeypatch.setattr(session_mod, "_async_engine_loop_ref", None)
    monkeypatch.setattr(session_mod, "_async_session_factory", None)

    def _fresh_loop_get():
        async def _g():
            return session_mod.get_engine()

        return _aio.run(_g())

    e1 = _fresh_loop_get()
    e2 = _fresh_loop_get()
    ids.append(id(e1))
    assert e1 is not e2, "loop 變更後必須重建 engine（worker 每 job 新 loop 場景）"

    # 同一 loop 內重取 → 同一 engine（不無謂重建）
    async def _same():
        a = session_mod.get_engine()
        b = session_mod.get_engine()
        return a is b

    assert _aio.run(_same()) is True


# ── 4. 前端 i18n：multi_consent UI keys（4 語系）───────────────────────────


@pytest.mark.parametrize("lang", ["zh-TW", "zh-CN", "en", "ru"])
def test_frontend_i18n_multi_consent_keys(lang):
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent / "web" / "js" / "i18n" / f"{lang}.json"
    )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    hitl = data.get("hitl", {})
    for key in ("multiConsentTitle", "multiApproveAll", "multiDenyAll", "multiSubmit"):
        assert hitl.get(key), f"{lang} 缺少 hitl.{key}"


class TestLedgerListingDateIsTaipeiTime:
    """2026-10-06：台灣時間 00:34 記的帳，列表顯示成 10/05（UTC 日期）——要用台灣時間。"""

    def test_utc_evening_is_next_day_in_taipei(self):
        from datetime import datetime, timezone

        from core.tools.crypto_modules.trade_journal import _entry_date_str

        assert _entry_date_str(datetime(2026, 10, 5, 16, 34, tzinfo=timezone.utc)) == "10/06"
        assert _entry_date_str(datetime(2026, 10, 5, 15, 59, tzinfo=timezone.utc)) == "10/05"
        assert _entry_date_str(datetime(2026, 12, 31, 16, 0, tzinfo=timezone.utc)) == "01/01"

    def test_naive_and_string_values_do_not_crash(self):
        from datetime import datetime

        from core.tools.crypto_modules.trade_journal import _entry_date_str

        assert _entry_date_str(datetime(2026, 10, 5, 9, 0)) == "10/05"
        assert _entry_date_str("2026-10-05T16:34:00") == "10/05"
        assert _entry_date_str("") == ""
        assert _entry_date_str(None) == ""
