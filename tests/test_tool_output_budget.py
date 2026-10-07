"""工具輸出預算與分頁（core/agents/tool_compactor.py，2026-10-06，DANNY：「不該截斷這樣會有問題吧」）。

舊行為：每個工具輸出超過 2000 字只給前 2000 字，後面丟掉、也不給取回的 key
（us_stock_snapshot 5.3K 字只看得到 38%）。新行為（只有 claw 主路徑 execute_streaming 會掛預算）：
額度依本輪 prompt 實際大小算；超過額度不丟、回「前一頁」＋續讀指引，模型用 read_more_result 分頁讀。
守的是：
1. 預算數學：完整 prompt 的額度小、櫥窗縮窄後額度大；雲端模型寬額度；用完仍有保底；
2. 分頁：JSON 陣列在完整項目處切（每頁可 parse、接起來等於原資料）、其他文字在換行／空白處切、一字不漏；
3. wrapper：沒掛預算 ＝ 舊行為不變；掛了預算 ＝ 小輸出原樣、大輸出分頁且保持原型別；
4. 續讀工具：照 ref／offset 讀得到後面的內容、跨使用者讀不到、讀完有明確訊息；
5. 接線：flag 一鍵關閉、base agent 掛預算並把續讀工具放進展示集合、sanitizer 認得工具名。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool as lc_tool

from core.agents import tool_compactor as tc

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("TOOL_OUTPUT_BUDGET", raising=False)
    tc._reset_for_testing()
    tc._slot_tokens_cache.clear()
    yield
    tc._reset_for_testing()


# ── 1. 預算數學 ──────────────────────────────────────────────────────────────


def test_budget_allowance_per_call_total_and_floor():
    b = tc.TurnOutputBudget(total_chars=10_000, per_call_chars=8_000)
    assert b.allowance() == 8_000
    b.consume(8_000)
    assert b.allowance() == 2_000  # 剩 2000＝保底
    b.consume(5_000)
    assert b.allowance() == tc.FLOOR_CHARS  # 預算用完仍有保底，不會變 0
    b.consume(-5)  # 負數不會倒扣
    assert b.used == 13_000


def test_full_prompt_gets_a_small_budget_and_narrowed_prompt_a_big_one():
    # 完整路徑：system 18.4K＋工具 38.7K 字（實測 ≈ 2.4 萬 token）
    full = tc.make_turn_budget(57_000, 2_000, local=True, slot_tokens=32_768)
    # 工具櫥窗：system 20K＋工具 13K 字
    narrow = tc.make_turn_budget(33_000, 2_000, local=True, slot_tokens=32_768)
    assert full.total < narrow.total / 3
    assert full.total <= 8_000  # 完整 prompt 剩的 context 很少（約 4K 字），不能放行大輸出
    assert narrow.total >= 25_000  # 縮窄後約 2.8 萬字：5K 字的快照整份放得下，還能再讀幾次
    assert narrow.per_call == tc.PER_CALL_LOCAL_CHARS
    assert full.total >= tc.FLOOR_CHARS and full.per_call >= tc.FLOOR_CHARS


def test_budget_never_negative_even_if_prompt_exceeds_the_slot():
    b = tc.make_turn_budget(200_000, 50_000, local=True, slot_tokens=32_768)
    assert b.total == tc.FLOOR_CHARS and b.allowance() == tc.FLOOR_CHARS


def test_non_local_models_get_a_generous_fixed_budget():
    b = tc.make_turn_budget(57_000, 2_000, local=False)
    assert (b.total, b.per_call) == (tc.TURN_CLOUD_CHARS, tc.PER_CALL_CLOUD_CHARS)


def test_slot_tokens_clamp_and_larger_slot_gives_more():
    small = tc.make_turn_budget(33_000, 0, local=True, slot_tokens=16_384)
    big = tc.make_turn_budget(33_000, 0, local=True, slot_tokens=65_536)
    assert big.total > small.total
    assert big.total <= tc.TURN_LOCAL_MAX_CHARS


def test_slot_context_tokens_reads_props_and_falls_back(monkeypatch):
    class _Resp:
        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    class _Client:
        payload = {"default_generation_settings": {"n_ctx": 32_768}}
        boom = False

        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            assert url == "http://llm.local:8080/props"  # /v1 要去掉
            if _Client.boom:
                raise RuntimeError("down")
            return _Resp(_Client.payload)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    assert asyncio.run(tc.slot_context_tokens("http://llm.local:8080/v1")) == 32_768
    # 問到的有快取；之後即使連線壞掉也沿用
    _Client.boom = True
    assert asyncio.run(tc.slot_context_tokens("http://llm.local:8080/v1")) == 32_768
    # 問不到（沒快取）→ 保守預設值，不拋例外
    assert asyncio.run(tc.slot_context_tokens("http://other:8080/v1")) == tc.DEFAULT_SLOT_TOKENS
    assert asyncio.run(tc.slot_context_tokens("")) == tc.DEFAULT_SLOT_TOKENS
    # 離譜的值不採信
    _Client.boom = False
    _Client.payload = {"default_generation_settings": {"n_ctx": 12}}
    assert asyncio.run(tc.slot_context_tokens("http://weird:1/v1")) == tc.DEFAULT_SLOT_TOKENS


# ── 2. 分頁 ──────────────────────────────────────────────────────────────────


def _news(n: int, body: int = 120) -> str:
    return json.dumps(
        [{"title": f"新聞 {i}", "source": "x", "body": "字" * body} for i in range(n)],
        ensure_ascii=False,
    )


def _read_all(text: str, limit: int) -> list[str]:
    pages, offset = [], 0
    while True:
        page, cut = tc.page_text(text, offset, limit)
        pages.append(page)
        if cut >= len(text):
            return pages
        assert cut > offset, "分頁必須前進，否則續讀會無限迴圈"
        offset = cut


def test_json_array_pages_cut_at_item_boundaries_and_reassemble_losslessly():
    text = _news(40)
    original = json.loads(text)
    pages = _read_all(text, limit=1_500)
    assert len(pages) > 3
    items = []
    for i, page in enumerate(pages):
        body = page.strip()
        if i == 0:
            assert body.startswith("[")
            body = body[1:]
        if i == len(pages) - 1:
            assert body.endswith("]")
            body = body[:-1]
        items.append(body.strip())
    rebuilt = json.loads("[" + ",".join(p for p in items if p) + "]")
    assert rebuilt == original  # 一項不漏、一項不重複、每項完整
    assert all(len(p) <= 1_500 + 5 for p in pages)


def test_plain_text_pages_cover_everything_and_prefer_line_breaks():
    text = "\n".join(f"第 {i} 行：" + "資料" * 30 for i in range(80))
    pages = _read_all(text, limit=1_000)
    assert "".join(pages) == text
    assert all(p.endswith("\n") for p in pages[:-1])  # 在換行處切


def test_single_huge_item_still_advances():
    text = json.dumps([{"blob": "x" * 5_000}])
    pages = _read_all(text, limit=1_000)
    assert "".join(pages) == text  # 單項過大只能硬切，但一字不漏、一定前進


def test_short_text_is_one_page_and_offsets_are_clamped():
    page, cut = tc.page_text("abc", 0, 1_000)
    assert (page, cut) == ("abc", 3)
    assert tc.page_text("abc", 99, 1_000) == ("", 3)
    assert tc.page_text("abc", -5, 2)[0] == "ab"


def test_footer_tells_the_model_how_to_continue_without_a_uuid():
    f = tc.page_footer("abc123", 0, 2_000, 5_300)
    assert 'read_more_result(ref="abc123", offset=2000)' in f and "5,300" in f
    done = tc.page_footer("abc123", 2_000, 5_300, 5_300)
    assert "已讀完" in done and "read_more_result" not in done


# ── 3. wrapper ───────────────────────────────────────────────────────────────


def _tool(out, name="t"):
    @lc_tool(name)
    def _t(q: str = "") -> str:
        """test tool"""
        return out

    return _t


def test_without_budget_the_legacy_preview_is_unchanged():
    big = "資" * 6_000
    w = tc.wrap_tool(_tool(big), owner_id="u1", session_id="s1")
    out = w.invoke({"q": ""})
    assert "已截斷" in out and len(out) < 2_400
    assert "read_more_result" not in out


def test_with_budget_small_outputs_pass_through_untouched_and_are_counted():
    w = tc.wrap_tool(_tool("ok" * 100), owner_id="u1")
    b = tc.TurnOutputBudget(20_000, 8_000)
    tc.attach_budget([w], b)
    assert w.invoke({"q": ""}) == "ok" * 100
    assert b.used == 200


def test_with_budget_a_5k_output_is_delivered_whole_not_cut_at_2000():
    five_k = "資" * 5_300
    w = tc.wrap_tool(_tool(five_k), owner_id="u1")
    tc.attach_budget([w], tc.TurnOutputBudget(40_000, 8_000))
    out = w.invoke({"q": ""})
    assert out == five_k  # 以前只看得到 2000 字（38%）


def test_with_budget_big_output_is_paged_with_a_ref_and_consumes_only_the_page():
    big = _news(60)  # 約 11K 字
    w = tc.wrap_tool(_tool(big), owner_id="u1", session_id="s1")
    b = tc.TurnOutputBudget(40_000, 4_000)
    tc.attach_budget([w], b)
    out = w.invoke({"q": ""})
    assert len(out) < 4_400 and out.startswith("[")
    assert "read_more_result(ref=" in out and "UUID" not in out
    assert 0 < b.used <= 4_000
    # 第一頁的每個項目都是完整的
    first_items = out[: out.rindex("}") + 1] + "]"
    assert json.loads(first_items)[0]["title"] == "新聞 0"


def test_with_budget_the_return_type_is_preserved_for_toolmessage_and_dict():
    big = "資" * 12_000

    class _TM:
        name = "tm"
        description = "d"

        def invoke(self, *_a, **_k):
            return ToolMessage(content=big, name="tm", tool_call_id="c1")

    class _D:
        name = "dd"
        description = "d"

        def invoke(self, *_a, **_k):
            return {"content": big, "x": 1}

    for orig, check in ((_TM(), lambda r: isinstance(r, ToolMessage) and r.tool_call_id == "c1"),
                        (_D(), lambda r: isinstance(r, dict) and r["x"] == 1)):
        w = tc.wrap_tool(orig, owner_id="u1")
        tc.attach_budget([w], tc.TurnOutputBudget(20_000, 3_000))
        res = w.invoke({})
        assert check(res)
        text = res.content if isinstance(res, ToolMessage) else res["content"]
        assert "read_more_result" in text and len(text) < 3_400


def test_budget_is_shared_across_calls_and_runs_down_to_the_floor():
    w = tc.wrap_tool(_tool("資" * 3_000), owner_id="u1")
    b = tc.TurnOutputBudget(total_chars=7_000, per_call_chars=3_500)
    tc.attach_budget([w], b)
    assert w.invoke({"q": ""}) == "資" * 3_000  # 還有額度：整份給
    assert w.invoke({"q": ""}) == "資" * 3_000  # 剩 4000 ≥ 3000：整份給
    third = w.invoke({"q": ""})  # 剩 1000 → 保底 2000：開始分頁
    assert "read_more_result" in third and len(third) < 2_400


def test_attach_budget_none_restores_the_legacy_behavior():
    w = tc.wrap_tool(_tool("資" * 6_000), owner_id="u1")
    tc.attach_budget([w], tc.TurnOutputBudget(40_000, 8_000))
    assert w.invoke({"q": ""}) == "資" * 6_000
    tc.attach_budget([w], None)
    assert "已截斷" in w.invoke({"q": ""})


def test_async_path_pages_too():
    big = _news(60)

    @lc_tool("at")
    async def _at(q: str = "") -> str:
        """async tool"""
        return big

    w = tc.wrap_tool(_at, owner_id="u1")
    tc.attach_budget([w], tc.TurnOutputBudget(40_000, 4_000))
    out = asyncio.run(w.ainvoke({"q": ""}))
    assert "read_more_result(ref=" in out and len(out) < 4_400


# ── 4. 續讀工具 ──────────────────────────────────────────────────────────────


def _ref_of(text: str) -> str:
    import re

    return re.search(r'ref="([0-9a-f]+)"', text).group(1)


def test_read_more_result_walks_the_rest_and_reassembles_the_original():
    big = _news(60)
    b = tc.TurnOutputBudget(100_000, 4_000)
    w = tc.wrap_tool(_tool(big), owner_id="u1", session_id="s1")
    tc.attach_budget([w], b)
    first = w.invoke({"q": ""})
    ref = _ref_of(first)
    reader = tc.build_read_more_tool(b, requester_id="u1", session_id="s1")
    import re

    pages = [first[: first.rindex("\n[")]]
    nxt = int(re.search(r"offset=(\d+)", first).group(1))
    while True:
        page = reader.invoke({"ref": ref, "offset": nxt})
        body, _, foot = page.rpartition("\n[")
        pages.append(body)
        m = re.search(r"offset=(\d+)", foot)
        if not m:
            assert "已讀完" in foot
            break
        nxt = int(m.group(1))
    joined = ",".join(p.strip().lstrip("[").rstrip("]").strip() for p in pages)
    assert json.loads("[" + joined + "]") == json.loads(big)


def test_read_more_result_is_scoped_to_the_owner_and_handles_bad_input():
    big = "資" * 9_000
    b = tc.TurnOutputBudget(50_000, 3_000)
    w = tc.wrap_tool(_tool(big), owner_id="alice", session_id="s1")
    tc.attach_budget([w], b)
    ref = _ref_of(w.invoke({"q": ""}))
    mine = tc.build_read_more_tool(b, requester_id="alice", session_id="s1")
    other = tc.build_read_more_tool(b, requester_id="mallory", session_id="s9")
    guest = tc.build_read_more_tool(b, requester_id=None)
    assert "資" in mine.invoke({"ref": ref, "offset": 3_000})
    assert other.invoke({"ref": ref, "offset": 3_000}).startswith("[ERROR]")
    assert guest.invoke({"ref": ref, "offset": 3_000}).startswith("[ERROR]")
    assert mine.invoke({"ref": "nope", "offset": 0}).startswith("[ERROR]")
    assert "已經讀完" in mine.invoke({"ref": ref, "offset": 99_999})
    assert "資" in mine.invoke({"ref": ref, "offset": -7})  # 負的 offset 當 0
    assert "資" in mine.invoke({"ref": ref, "offset": "3000"})  # 模型常把數字寫成字串


# ── 5. 接線 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "FALSE"])
def test_flag_off(monkeypatch, raw):
    monkeypatch.setenv("TOOL_OUTPUT_BUDGET", raw)
    assert tc.output_budget_enabled() is False


def test_flag_defaults_on_and_junk_keeps_it_on(monkeypatch):
    assert tc.output_budget_enabled() is True
    monkeypatch.setenv("TOOL_OUTPUT_BUDGET", "maybe")
    assert tc.output_budget_enabled() is True


def test_base_agent_attaches_the_budget_and_shows_the_reader_with_the_showcase():
    src = (REPO / "core" / "agents" / "base_react_agent.py").read_text(encoding="utf-8")
    assert "output_budget_enabled()" in src and "attach_budget(tools, _budget)" in src
    assert "build_read_more_tool(" in src
    # 櫥窗縮窄時續讀工具一定要在展示集合裡，否則模型看得到「請呼叫 read_more_result」卻沒有這個工具
    assert '_always = {"read_more_result"}' in src
    assert "ToolShowcaseMiddleware(_show_handlers | _always)" in src
    # 預算只是品質優化：壞了就回舊行為，不能弄壞一題
    assert "[ToolOutputBudget] skipped (fail-open)" in src and "attach_budget(tools, None)" in src


def test_sanitizer_knows_the_reader_tool_name():
    from core.agents import tool_name_registry as reg

    assert "read_more_result" in reg._FALLBACK_NAMES
    src = (REPO / "core" / "agents" / "bootstrap.py").read_text(encoding="utf-8")
    assert '"read_more_result"' in src


def test_env_example_documents_the_flag():
    assert "TOOL_OUTPUT_BUDGET" in (REPO / ".env.example").read_text(encoding="utf-8")
