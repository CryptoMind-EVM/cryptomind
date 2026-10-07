"""記憶／Skill／工具接線審查（2026-10-06，DANNY：「長短期記憶是否有正確接入到系統中……Skill 或者 tool 也是」）。

用真實模型逐條路徑實測後找到的缺口，這裡逐一守住：
1. 記憶／技能核准後要有回饋（原本按「同意」後畫面完全沒有回應，使用者不知道存了沒有）；
2. 提前 return 的路徑（快取命中／T0／T1／查價快速通道）也要走 _track_conversation——否則使用者說的個人資訊
   不會被抽進長期記憶、經驗不記錄、consolidation 計數漏算；
3. 「記憶／技能／提醒／判斷紀錄」這類要動用 agent 功能的問題不能被 T1 當閒聊攔去無工具、無記憶的快速通道；
4. 工具：registry 的 required_tier 要和 DB 的單一真相源（_TOOLS_SEED）一致（DB 離線時的 fallback 靠它）；
5. 查價快速通道要套用使用者的工具勾選／preset（主路徑有，快速通道原本漏接）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agents import triage
from core.agents.manager import claw_loop as cl
from core.agents.manager import consent_gate as cg

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
LANGS = ("zh-TW", "zh-CN", "en", "ru")


# ── 1. 記憶／技能核准後的回饋 ─────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["create_memory", "delete_memory", "custom_skill"])
def test_every_language_has_all_three_outcomes(kind):
    by_lang = cg._OUTCOME_MSGS[kind]
    for lang in LANGS:
        assert set(by_lang[lang]) == {"done", "failed", "declined"}, (kind, lang)
        for text in by_lang[lang].values():
            assert "{detail}" in text, (kind, lang, text)


def _outcome(kind, approved, ok, **signal):
    return [
        {
            "signal": {"kind": kind, **signal},
            "approved": approved,
            "ok": ok,
            "edited": {},
        }
    ]


def test_memory_outcome_reports_what_was_remembered():
    sig = {"content": "投資者偏好：保守型，以技術面為主", "category": "preference"}
    done = cg.build_consent_outcome_text(
        _outcome("create_memory", True, True, **sig), "zh-TW"
    )
    assert "✅ 已記住" in done and "保守型" in done and "preference" in done
    failed = cg.build_consent_outcome_text(
        _outcome("create_memory", True, False, **sig), "zh-TW"
    )
    assert failed.startswith("\n\n⚠️") and "沒有存進記憶" in failed
    declined = cg.build_consent_outcome_text(
        _outcome("create_memory", False, False, **sig), "zh-TW"
    )
    assert "已取消" in declined and "沒有記住" in declined


def test_memory_detail_is_truncated_and_other_kinds_are_unchanged():
    long = cg.build_consent_outcome_text(
        _outcome("create_memory", True, True, content="很長" * 80, category="fact"),
        "zh-TW",
    )
    assert "…" in long and len(long) < 140
    skill = cg.build_consent_outcome_text(
        _outcome("custom_skill", True, True, skill_name="my-dca"), "en"
    )
    assert "Skill change applied" in skill and "my-dca" in skill
    # 沒有登記的 kind 仍然不報（不會因為這次改動多出奇怪的句子）
    assert (
        cg.build_consent_outcome_text(_outcome("unknown_kind", True, True), "zh-TW")
        == ""
    )


def test_successful_memory_and_skill_writes_return_true(monkeypatch):
    """ok 靠 _apply_consent_write 的回傳值：記憶／技能成功要回 True，否則會被誤報成「失敗」。"""
    written = []

    class FakeStore:
        def __init__(self, **kwargs):
            pass

        def write_facts(self, facts):
            written.extend(facts)

        def delete_fact(self, key):
            written.append(("deleted", key))

    from core.database import memory as memory_mod

    monkeypatch.setattr(memory_mod, "MemoryStore", FakeStore)
    sig = {
        "kind": "create_memory",
        "content": "偏好短線",
        "category": "preference",
        "key": "style",
    }
    assert cl._apply_consent_write("u1", sig, {}, {}) is True
    assert written[0]["value"] == "偏好短線"
    assert (
        cl._apply_consent_write("u1", {"kind": "delete_memory", "key": "style"}, {}, {})
        is True
    )

    class Boom(FakeStore):
        def write_facts(self, facts):
            raise RuntimeError("db down")

    monkeypatch.setattr(memory_mod, "MemoryStore", Boom)
    assert (
        cl._apply_consent_write("u1", sig, {}, {}) is None
    )  # 失敗＝None＝回報「沒有存進記憶」


# ── 2. 提前 return 的路徑也要記進長期記憶流程 ─────────────────────────────────


def test_every_early_return_path_tracks_the_turn():
    src = (REPO / "core/agents/manager/claw_loop.py").read_text(encoding="utf-8")
    assert src.count("self._track_turn_in_background(") >= 3
    cache = src.index("[ClawLoop] response cache hit")
    fast = src.index("[ClawLoop] %s fast path hit")
    lookup = src.index("[LookupFastPath] %s src=%s")
    for anchor in (cache, fast, lookup):
        window = src[anchor : anchor + 2600]
        assert "_track_turn_in_background(" in window, anchor
    # 完整路徑原本就有
    assert "await self._track_conversation(" in src


def test_background_tracking_schedules_track_conversation_without_blocking():
    mgr = cl.ClawLoopMixin.__new__(cl.ClawLoopMixin)
    calls = []

    async def fake_track(user_message, assistant_response, tools_used=None):
        calls.append((user_message, assistant_response, tools_used))

    mgr._track_conversation = fake_track

    async def run():
        mgr._track_turn_in_background("我喜歡短線", "收到", ["get_crypto_price"])
        assert calls == []  # 先回答、後記錄：呼叫當下還沒執行
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert calls == [("我喜歡短線", "收到", ["get_crypto_price"])]


def test_background_tracking_never_breaks_the_answer():
    mgr = cl.ClawLoopMixin.__new__(cl.ClawLoopMixin)

    def broken(**kwargs):
        raise RuntimeError("boom")

    mgr._track_conversation = broken
    mgr._track_turn_in_background("hi", "hello")  # 沒有 running loop／例外都只記 log


# ── 2b. 確認卡之前不能記進長期記憶（實測：按「取消」事實照樣被存下）───────────────


def test_full_path_tracks_the_turn_only_after_the_consent_card_is_resolved():
    """原本 _track_conversation 在 Phase G（確認卡 interrupt）之前：使用者還沒決定，事實抽取就把
    「請記住…」寫進記憶，按取消也擋不住；resume 重跑時又記一次（重複抽取、訊息計數多算）。"""
    src = (REPO / "core/agents/manager/claw_loop.py").read_text(encoding="utf-8")
    interrupt = src.index(
        "answer = interrupt(payload)  # pauses graph; resume lands here"
    )
    discard = src.index("_discard_resume_result(_resume_key)")
    full_track = src.index("await self._track_conversation(")
    assert interrupt < discard < full_track
    # 完整路徑只剩這一個 await 呼叫（其餘都是提前 return 路徑的背景追蹤）
    assert src.count("await self._track_conversation(") == 1
    # post-response hooks 同理：在追蹤之後、return 之前，而不是確認卡之前
    hooks = src.index("self._invoke_response_hooks(state, response)", full_track)
    assert hooks - full_track < 400


def test_no_auto_extraction_when_the_remember_tool_handled_the_turn(monkeypatch):
    """用了 remember（提案→使用者核准才寫入）的那一輪，不再另外自動抽取——否則會繞過使用者的決定。"""
    from core.database import memory as memory_mod

    called = []

    class FakeLLM:
        def invoke(self, messages):
            called.append(messages)
            return SimpleNamespace(content='{"facts": []}')

    store = memory_mod.MemoryStore.__new__(memory_mod.MemoryStore)
    monkeypatch.setattr(memory_mod.MemoryStore, "facts_to_text", lambda self: "")
    msg = "我偏好保守投資，請記住這點"

    skipped = asyncio.run(
        store.extract_facts_from_turn(msg, "ok", 1, FakeLLM(), tools_used=["remember"])
    )
    assert skipped is False and called == []
    assert "remember" in memory_mod.CONSENT_GATED_MEMORY_TOOLS

    # 沒走 remember 的同一句話照舊會抽（自動抽取機制本身沒壞）
    asyncio.run(
        store.extract_facts_from_turn(
            msg, "ok", 1, FakeLLM(), tools_used=["get_crypto_price"]
        )
    )
    assert len(called) == 1


# ── 2c. 新建的 manager 實例要把 DB 對話補回緩衝（consolidation 才不會在重建後停擺）──────────


def _memory_mixin(session_id="s1"):
    from core.agents.manager.memory import MemoryMixin

    mgr = MemoryMixin.__new__(MemoryMixin)
    mgr.session_id = session_id
    mgr._memory_cache = {}
    mgr._message_count = 0
    return mgr


def _rows(*pairs):
    return [
        {"role": r, "content": c, "metadata": None, "timestamp": "2026-10-06 00:00:00"}
        for r, c in pairs
    ]


def test_a_rebuilt_manager_rehydrates_its_buffer_and_aligns_the_counter(monkeypatch):
    """實測推演：DB 記著『已整合到第 10 則』，manager 因 TTL／部署重建後緩衝與計數歸零，
    unconsolidated＝0−10 變負數、12 則門檻永遠到不了，真的到了又拿舊 index 切全新的訊息。"""
    from core.database import chat as chat_mod

    history = _rows(
        ("user", "q1"),
        ("assistant", "a1"),
        ("user", "q2"),
        ("assistant", "a2"),
        ("user", "現在這句"),
    )
    monkeypatch.setattr(
        chat_mod,
        "get_chat_history",
        lambda session_id, limit=20, before_timestamp=None: history,
    )
    mgr = _memory_mixin()
    mgr._hydrate_short_term_from_db("現在這句")
    mem = mgr._get_memory("s1")
    # 當前這句 API 已先存進 DB，要略過（_track_conversation 馬上會自己加一次）
    assert [m["content"] for m in mem.conversation_history] == ["q1", "a1", "q2", "a2"]
    assert mgr._message_count == 4


def test_hydration_never_clobbers_a_live_buffer_and_runs_once(monkeypatch):
    from core.database import chat as chat_mod

    calls = []

    def fake(session_id, limit=20, before_timestamp=None):
        calls.append(session_id)
        return _rows(("user", "舊"), ("assistant", "舊答"))

    monkeypatch.setattr(chat_mod, "get_chat_history", fake)
    mgr = _memory_mixin()
    mgr._get_memory("s1").add_message("user", "行程內已經有的對話")
    mgr._hydrate_short_term_from_db("x")
    assert [m["content"] for m in mgr._get_memory("s1").conversation_history] == [
        "行程內已經有的對話"
    ]
    mgr._hydrate_short_term_from_db("x")
    assert calls == []  # 緩衝已有內容就不查 DB；同一個 session 也不重複查


def test_hydration_is_best_effort_and_skips_unsafe_cases(monkeypatch):
    from core.database import chat as chat_mod

    def boom(session_id, limit=20, before_timestamp=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_mod, "get_chat_history", boom)
    mgr = _memory_mixin()
    mgr._hydrate_short_term_from_db("x")  # DB 掛了不丟例外
    assert mgr._get_memory("s1").conversation_history == [] and mgr._message_count == 0

    # session 為 default／空：不查（get_chat_history 會直接 ValueError）
    mgr = _memory_mixin(session_id="default")
    mgr._hydrate_short_term_from_db("x")
    assert mgr._message_count == 0

    # 對話超過視窗：不水合（index 會錯位，寧可不整合）
    big = _rows(*[("user" if i % 2 == 0 else "assistant", f"m{i}") for i in range(400)])
    monkeypatch.setattr(
        chat_mod,
        "get_chat_history",
        lambda session_id, limit=20, before_timestamp=None: big,
    )
    mgr = _memory_mixin()
    mgr._hydrate_short_term_from_db("x")
    assert mgr._get_memory("s1").conversation_history == []

    # 其他角色（system／tool）不進緩衝
    mixed = _rows(
        ("system", "notice"), ("user", "u"), ("assistant", "a"), ("tool", "t")
    )
    monkeypatch.setattr(
        chat_mod,
        "get_chat_history",
        lambda session_id, limit=20, before_timestamp=None: mixed,
    )
    mgr = _memory_mixin()
    mgr._hydrate_short_term_from_db("zzz")
    assert [m["role"] for m in mgr._get_memory("s1").conversation_history] == [
        "user",
        "assistant",
    ]


def test_track_conversation_hydrates_before_it_counts_the_turn():
    src = (REPO / "core/agents/manager/memory.py").read_text(encoding="utf-8")
    hydrate = src.index("self._hydrate_short_term_from_db(user_message)")
    add = src.index('memory.add_message("user", user_message)')
    assert hydrate < add


# ── 3. 要動用 agent 功能的問題不走 T1 ─────────────────────────────────────────

AGENT_FEATURE_QUERIES = [
    "你還記得我的偏好嗎",
    "你記得我之前說過什麼嗎",
    "幫我記住我喜歡短線",
    "忘記我剛剛說的",
    "我是誰你知道嗎",
    "你有什麼記憶",
    "幫我新增一個自訂技能",
    "我想建立 skill",
    "明天提醒我看財報",
    "幫我加行事曆",
    "幫我記下這個判斷",
    "我看空這檔",
    "do you remember me",
    "remember I prefer swing trading",
    "remind me tomorrow",
    "запомни мои предпочтения",
]


@pytest.mark.parametrize("query", AGENT_FEATURE_QUERIES)
def test_agent_feature_questions_take_the_full_path(query):
    assert triage.has_finance_signal(query), query
    assert triage.is_smalltalk(query) is False


@pytest.mark.parametrize(
    "query",
    ["你好", "謝謝", "你是誰", "你能做什麼", "怎麼用", "hi", "thanks", "who are you"],
)
def test_plain_smalltalk_is_still_t0(query):
    assert triage.is_smalltalk(query) is True, query


def test_t1_is_skipped_for_memory_questions():
    """_resolve_fast_route：有『要動用 agent 功能』語彙＝視同有訊號，連 T1 分類都不跑。"""
    calls = []

    async def t1(prompt):
        calls.append(prompt)
        return "simple_qa"

    async def boom(prompt):
        raise AssertionError("router 不該被呼叫")

    route, _ = asyncio.run(cl._resolve_fast_route("你還記得我的偏好嗎", boom, t1))
    assert route is None and calls == []


# ── 4. 工具：registry tier 與 seed 一致 ───────────────────────────────────────


def test_registry_tiers_match_the_db_seed():
    from core.agents.bootstrap import build_tool_registry
    from core.agents.capability_resolver import build_tier_risk_lookup

    tiers, _risks = build_tier_risk_lookup()
    registry = build_tool_registry()
    mismatched = [
        (m.name, m.required_tier, tiers[m.name])
        for m in registry.list_all_tools()
        if m.name in tiers and str(m.required_tier) != str(tiers[m.name])
    ]
    assert not mismatched, (
        f"registry 與 seed 的 tier 不一致（DB 離線 fallback 會放寬權限）：{mismatched}"
    )
    # 這批原本是 registry=free／seed=premium
    by_name = {m.name: m for m in registry.list_all_tools()}
    for name in (
        "get_forex_rate",
        "get_vix_index",
        "get_commodity_price",
        "global_stock_price",
    ):
        assert by_name[name].required_tier == "premium", name


def test_db_outage_fallback_no_longer_hands_premium_tools_to_free_users(monkeypatch):
    """base_react_agent 在 DB 不可用時改依 meta.required_tier 過濾——現在 premium 工具不會漏給 free。"""
    from core.agents.base_react_agent import _TIER_LEVELS
    from core.agents.bootstrap import build_tool_registry
    from core.database.tools import normalize_membership_tier

    free = _TIER_LEVELS.get("free", 0)
    visible = [
        m.name
        for m in build_tool_registry().list_all_tools()
        if _TIER_LEVELS.get(normalize_membership_tier(m.required_tier), 0) <= free
    ]
    for name in (
        "get_forex_rate",
        "get_vix_index",
        "get_market_indices",
        "global_stock_snapshot",
    ):
        assert name not in visible, name
    assert "get_crypto_price" in visible  # 核心免費工具照舊


# ── 5. 查價快速通道套用工具勾選／preset ───────────────────────────────────────


def test_lookup_fastpath_applies_the_users_tool_selection(monkeypatch):
    from core.agents import lookup_fastpath as lf

    seen = {}
    tool = SimpleNamespace(
        name="get_crypto_price", invoke=lambda args: {"price": 85795.4}
    )
    meta = SimpleNamespace(name="get_crypto_price", handler=tool)

    def get_tool_metas(task):
        seen["context"] = task.context
        return [meta]

    mgr = cl.ClawLoopMixin.__new__(cl.ClawLoopMixin)
    mgr.agent_registry = SimpleNamespace(
        get=lambda name: SimpleNamespace(_get_tool_metas=get_tool_metas)
    )
    mgr.llm = object()
    mgr._emit_progress = lambda *a, **k: None

    async def llm_invoke(prompt, task_type=None):
        return "BTC 目前價格為 $85,795.40。"

    mgr._llm_invoke = llm_invoke
    monkeypatch.setattr(lf, "local_llama_endpoint", lambda llm: ("http://x/v1", "m"))

    async def verify(*a, **k):
        return 0.99

    monkeypatch.setattr(lf, "verify_with_local_llama", verify)
    state = {
        "enabled_tools": ["get_crypto_price"],
        "preset_config": {"tool_names": ["get_crypto_price"]},
    }
    out = asyncio.run(mgr._lookup_fast_path("BTC 現在多少", "zh-TW", "", state))
    assert out.hit
    assert seen["context"]["enabled_tools"] == ["get_crypto_price"]
    assert seen["context"]["preset_config"] == {"tool_names": ["get_crypto_price"]}
    # 沒帶 state 也不炸（單元呼叫端）
    out = asyncio.run(mgr._lookup_fast_path("BTC 現在多少", "zh-TW"))
    assert out.hit and seen["context"]["enabled_tools"] == []
