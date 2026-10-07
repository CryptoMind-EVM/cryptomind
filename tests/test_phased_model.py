"""分階段模型綁定（core/agents/phased_model.py）的守衛測試。

重點在三件實測踩過的坑：
1. 規劃回合必須強制 ``tool_choice="any"``——否則模型會直接寫散文，那次回合
   被丟棄就是 ~800 token 的純浪費，短題目淨值為負。
2. 回答回合**不能**把 sentinel 從工具清單拿掉——工具定義在 provider 端
   prompt 前綴裡，改清單會讓 88~97% 的快取命中失效。
3. sentinel 的工具呼叫不可以流到 graph（它沒有真的實作，會炸）。
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from core.agents.phased_model import (
    FINALIZE_TOOL_NAME,
    PhasedModelMiddleware,
    _split_finalize,
    phased_model_enabled,
)


class _Resp:
    """ModelResponse 的最小替身（欄位對齊 langchain 的 result/structured_response）。"""

    def __init__(self, result, structured_response=None):
        self.result = result
        self.structured_response = structured_response


class _Req:
    """ModelRequest 的最小替身，override() 回傳新實例。"""

    def __init__(self, **kw):
        self.model = kw.get("model", "main-model")
        self.tools = kw.get("tools", [])
        self.tool_choice = kw.get("tool_choice")
        self.model_settings = kw.get("model_settings", {})
        self.state = kw.get("state", {})
        self.messages = kw.get("messages", [])

    def override(self, **kw):
        base = dict(
            model=self.model,
            tools=self.tools,
            tool_choice=self.tool_choice,
            model_settings=self.model_settings,
            state=self.state,
            messages=self.messages,
        )
        base.update(kw)
        return _Req(**base)


def _ai(*tool_names, content=""):
    return AIMessage(
        content=content,
        tool_calls=[
            {"name": n, "args": {}, "id": f"call_{i}"} for i, n in enumerate(tool_names)
        ],
    )


def _run(mw, req, responses):
    """跑一次 awrap_model_call，回傳 (最終 response, 每次收到的 request)。"""
    seen = []
    queue = list(responses)

    async def handler(r):
        seen.append(r)
        return queue.pop(0)

    out = asyncio.run(mw.awrap_model_call(req, handler))
    return out, seen


# ── 階段判定 ────────────────────────────────────────────────────────────


def test_only_sentinel_means_answer_phase():
    done, _ = _split_finalize(_Resp([_ai(FINALIZE_TOOL_NAME)]))
    assert done is True


def test_real_tools_stay_in_planning_phase():
    done, resp = _split_finalize(_Resp([_ai("technical_analysis")]))
    assert done is False
    assert [c["name"] for c in resp.result[-1].tool_calls] == ["technical_analysis"]


def test_sentinel_mixed_with_real_tools_is_stripped_not_finalized():
    """模型同一回合又叫真工具又叫 sentinel：真工具要跑，sentinel 要被剝掉。

    留著 sentinel 會讓 graph 去找一個沒有實作的工具。
    """
    done, resp = _split_finalize(_Resp([_ai("technical_analysis", FINALIZE_TOOL_NAME)]))
    assert done is False
    names = [c["name"] for c in resp.result[-1].tool_calls]
    assert names == ["technical_analysis"]
    assert FINALIZE_TOOL_NAME not in names


def test_no_tool_calls_at_all_is_not_finalize():
    done, _ = _split_finalize(_Resp([_ai(content="直接回答")]))
    assert done is False


def test_non_ai_last_message_is_ignored():
    done, _ = _split_finalize(_Resp([HumanMessage(content="hi")]))
    assert done is False


# ── request 組裝 ────────────────────────────────────────────────────────


def test_planner_never_forces_a_tool_call():
    """實測：強制每回合都要叫工具＝拿掉模型停下來的能力。

    關掉推理後它連「這個資料剛才抓過了」都判斷不出來，連叫 20 回合重複工具
    撞穿 recursion_limit，整題 72.3 秒——比不開這功能還慢一倍。
    """
    mw = PhasedModelMiddleware(planner_settings={"reasoning_effort": "none"})
    _, seen = _run(mw, _Req(tools=[]), [_Resp([_ai("x")])])
    assert seen[0].tool_choice not in ("any", "required")


def test_planner_turn_cap_forces_answer_phase():
    """模型一直不叫 sentinel 時的保險絲，否則規劃階段會無限延伸。"""
    mw = PhasedModelMiddleware(
        planner_settings={"reasoning_effort": "none"}, max_planner_turns=2
    )
    state = {"messages": [_ai("a"), _ai("b")]}
    _, seen = _run(mw, _Req(state=state), [_Resp([_ai(content="答案")])])
    assert len(seen) == 1
    assert seen[0].tool_choice == "none"
    assert "reasoning_effort" not in seen[0].model_settings


def test_below_cap_still_uses_planner():
    mw = PhasedModelMiddleware(
        planner_settings={"reasoning_effort": "none"}, max_planner_turns=2
    )
    _, seen = _run(mw, _Req(state={"messages": [_ai("a")]}), [_Resp([_ai("x")])])
    assert seen[0].model_settings.get("reasoning_effort") == "none"


def test_planner_prose_is_accepted_not_discarded():
    """模型不叫 sentinel、直接寫答案時要採用它，不能丟棄重跑。

    丟棄要付兩次代價：那些 token 已經串到使用者眼前（實測 middleware 內部
    handler 產生的 token 一樣進 astream），而且是 ~900 token 的純浪費。
    """
    mw = PhasedModelMiddleware(planner_settings={"reasoning_effort": "none"})
    out, seen = _run(mw, _Req(), [_Resp([_ai(content="快設定寫的答案")])])
    assert len(seen) == 1
    assert out.result[-1].content == "快設定寫的答案"


def test_planner_request_carries_planner_settings():
    mw = PhasedModelMiddleware(planner_settings={"reasoning_effort": "none"})
    _, seen = _run(mw, _Req(model_settings={"temperature": 0.5}), [_Resp([_ai("x")])])
    assert seen[0].model_settings == {"temperature": 0.5, "reasoning_effort": "none"}


def test_planner_settings_do_not_leak_into_answer_turn():
    """回答回合必須拿回原本的推理預算，否則整個功能等於全域關推理。"""
    mw = PhasedModelMiddleware(planner_settings={"reasoning_effort": "none"})
    _, seen = _run(
        mw,
        _Req(model_settings={"temperature": 0.5}),
        [_Resp([_ai(FINALIZE_TOOL_NAME)]), _Resp([_ai(content="答案")])],
    )
    assert len(seen) == 2
    assert "reasoning_effort" not in seen[1].model_settings


def test_answer_turn_only_changes_tool_choice_not_the_tool_list():
    """中途改工具清單會動到 provider 端的 prompt 前綴 → 快取全失效。

    而且 langchain 會擋掉沒註冊的動態工具（實測 B 組整條路徑跑不起來），
    所以 sentinel 必須走 ``middleware.tools``。
    """
    mw = PhasedModelMiddleware()
    original = ["real_tool"]
    _, seen = _run(
        mw,
        _Req(tools=original),
        [_Resp([_ai(FINALIZE_TOOL_NAME)]), _Resp([_ai(content="答案")])],
    )
    assert seen[0].tools == original
    assert seen[1].tools == original
    assert seen[1].tool_choice == "none"


def test_sentinel_is_declared_on_the_middleware():
    """沒註冊在 middleware.tools 上，langchain 會拒絕執行動態工具。"""
    names = [getattr(t, "name", None) for t in PhasedModelMiddleware.tools]
    assert FINALIZE_TOOL_NAME in names


def test_planner_model_override_applied_only_when_given():
    mw_same = PhasedModelMiddleware()
    _, seen = _run(mw_same, _Req(), [_Resp([_ai("x")])])
    assert seen[0].model == "main-model"

    mw_diff = PhasedModelMiddleware(planner_model="cheap-model")
    _, seen = _run(mw_diff, _Req(), [_Resp([_ai("x")])])
    assert seen[0].model == "cheap-model"


def test_planning_turn_costs_exactly_one_call():
    """規劃回合不該重跑——重跑的那次會把 token 再串一遍給使用者。"""
    mw = PhasedModelMiddleware()
    _, seen = _run(mw, _Req(), [_Resp([_ai("technical_analysis")])])
    assert len(seen) == 1


# ── 旗標 ────────────────────────────────────────────────────────────────


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("PHASED_MODEL_ENABLED", raising=False)
    assert phased_model_enabled() is False


def test_flag_off_means_middleware_absent(monkeypatch):
    """旗標關的時候 middleware 清單裡不能有它——否則「預設關」只是文件謊言。"""
    from core.agents.base_react_agent import _build_agent_middleware

    monkeypatch.delenv("PHASED_MODEL_ENABLED", raising=False)
    monkeypatch.delenv("DEEP_AGENTS_PLANNING_ENABLED", raising=False)
    assert not any(
        isinstance(m, PhasedModelMiddleware) for m in _build_agent_middleware()
    )

    monkeypatch.setenv("PHASED_MODEL_ENABLED", "true")
    assert any(isinstance(m, PhasedModelMiddleware) for m in _build_agent_middleware())


def test_flag_is_registered_for_audit():
    from core.feature_flags import FLAG_REGISTRY

    assert "PHASED_MODEL_ENABLED" in FLAG_REGISTRY


# ── provider 不准混用推理／不推理時的降級 ────────────────────────────────


_DEEPSEEK_400 = (
    "Error code: 400 - {'error': {'message': 'The `reasoning_content` in the "
    "thinking mode must be passed back to the API.'}}"
)


def _run_seq(mw, req, responses):
    """responses 裡的 Exception 會被 raise，用來模擬 provider 回 400。"""
    seen = []
    queue = list(responses)

    async def handler(r):
        seen.append(r)
        nxt = queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    out = asyncio.run(mw.awrap_model_call(req, handler))
    return out, seen


def test_provider_rejecting_mixed_reasoning_degrades_instead_of_raising():
    """DeepSeek 實測會回這個 400；不降級的話每題都會 400 → 重跑 → 掉到殘缺答案。"""
    mw = PhasedModelMiddleware(planner_settings={"reasoning_effort": "none"})
    out, seen = _run_seq(
        mw,
        _Req(),
        [
            _Resp([_ai(FINALIZE_TOOL_NAME)]),  # 規劃回合宣告收尾
            RuntimeError(_DEEPSEEK_400),  # 回答回合被 provider 擋下
            _Resp([_ai(content="降級後的答案")]),  # 沿用規劃設定重跑
        ],
    )
    assert out.result[-1].content == "降級後的答案"
    # 降級後仍要禁止叫工具，否則模型會把 sentinel 再叫一次才寫答案，白花一次呼叫
    assert seen[-1].model_settings.get("reasoning_effort") == "none"
    assert seen[-1].tool_choice == "none"


def test_degraded_state_sticks_for_the_rest_of_the_run():
    """降級後不該每回合再撞一次 400。"""
    mw = PhasedModelMiddleware(planner_settings={"reasoning_effort": "none"})
    _run_seq(
        mw,
        _Req(),
        [
            _Resp([_ai(FINALIZE_TOOL_NAME)]),
            RuntimeError(_DEEPSEEK_400),
            _Resp([_ai(content="x")]),
        ],
    )
    assert mw._degraded is True

    _, seen = _run_seq(mw, _Req(), [_Resp([_ai(content="後續回合")])])
    assert len(seen) == 1
    assert seen[0].model_settings.get("reasoning_effort") == "none"
    assert seen[0].tool_choice != "none"


def test_degraded_sentinel_does_not_cost_an_extra_round_trip():
    """降級後模型再叫 sentinel，要直接收尾，不能讓它又叫一次。"""
    mw = PhasedModelMiddleware(planner_settings={"reasoning_effort": "none"})
    mw._degraded = True
    out, seen = _run_seq(
        mw,
        _Req(),
        [_Resp([_ai(FINALIZE_TOOL_NAME)]), _Resp([_ai(content="收尾")])],
    )
    assert len(seen) == 2
    assert seen[1].tool_choice == "none"
    assert seen[1].model_settings.get("reasoning_effort") == "none"
    assert out.result[-1].content == "收尾"


def test_other_errors_still_propagate():
    """429／供應商 5xx 要往上拋給既有 retry 層，不能被降級邏輯吞掉。"""
    mw = PhasedModelMiddleware(planner_settings={"reasoning_effort": "none"})
    with pytest.raises(RuntimeError, match="429"):
        _run_seq(
            mw,
            _Req(),
            [
                _Resp([_ai(FINALIZE_TOOL_NAME)]),
                RuntimeError("429 rate limit"),
                _Resp([_ai()]),
            ],
        )
    assert mw._degraded is False


# ── provider 跳過清單：DeepSeek 不能混用推理／不推理 → 保留思考顯示 ────────


class ChatOpenAI:  # 名字要像 langchain 的 class：provider 推斷會看 class 名
    def __init__(self, base_url=None):
        self.openai_api_base = base_url


class ChatAnthropic:
    pass


def test_skip_providers_default_contains_deepseek(monkeypatch):
    """預設就跳過 DeepSeek——它的 400 降級等於全程關推理，前端思考區塊整題空白。"""
    from core.agents.phased_model import phased_skip_providers

    monkeypatch.delenv("PHASED_MODEL_SKIP_PROVIDERS", raising=False)
    assert "deepseek" in phased_skip_providers()


def test_skip_providers_env_overrides_default(monkeypatch):
    from core.agents.phased_model import phased_skip_providers

    monkeypatch.setenv("PHASED_MODEL_SKIP_PROVIDERS", " openrouter, Groq ")
    assert phased_skip_providers() == frozenset({"openrouter", "groq"})
    # 空字串＝不跳過任何 provider（想在 DeepSeek 上硬開的逃生口）
    monkeypatch.setenv("PHASED_MODEL_SKIP_PROVIDERS", "")
    assert phased_skip_providers() == frozenset()


def test_provider_of_llm_is_inferred_from_base_url_or_class():
    from core.agents.bootstrap import LanguageAwareLLM
    from core.agents.phased_model import provider_of_llm

    assert provider_of_llm(ChatOpenAI("https://api.deepseek.com")) == "deepseek"
    assert provider_of_llm(ChatOpenAI("https://openrouter.ai/api/v1")) == "openrouter"
    assert provider_of_llm(ChatOpenAI()) == "openai"
    assert provider_of_llm(ChatAnthropic()) == "anthropic"
    # LanguageAwareLLM 包一層也要看得穿
    wrapped = LanguageAwareLLM(ChatOpenAI("https://api.deepseek.com"), "zh-TW")
    assert provider_of_llm(wrapped) == "deepseek"
    assert provider_of_llm(object()) == ""


def test_skipped_provider_gets_no_phased_middleware(monkeypatch):
    from core.agents.base_react_agent import _build_agent_middleware

    monkeypatch.setenv("PHASED_MODEL_ENABLED", "true")
    monkeypatch.delenv("DEEP_AGENTS_PLANNING_ENABLED", raising=False)
    monkeypatch.delenv("PHASED_MODEL_SKIP_PROVIDERS", raising=False)

    deepseek = ChatOpenAI("https://api.deepseek.com")
    assert not any(
        isinstance(m, PhasedModelMiddleware) for m in _build_agent_middleware(deepseek)
    ), "預設跳過 DeepSeek：flag 開了也不能掛 middleware"

    # 逃生口：清空跳過清單 → DeepSeek 照掛（降級行為由既有測試守）
    monkeypatch.setenv("PHASED_MODEL_SKIP_PROVIDERS", "")
    assert any(
        isinstance(m, PhasedModelMiddleware) for m in _build_agent_middleware(deepseek)
    )


def test_non_skipped_provider_still_gets_phased_middleware(monkeypatch):
    from core.agents.base_react_agent import _build_agent_middleware

    monkeypatch.setenv("PHASED_MODEL_ENABLED", "true")
    monkeypatch.delenv("DEEP_AGENTS_PLANNING_ENABLED", raising=False)
    monkeypatch.delenv("PHASED_MODEL_SKIP_PROVIDERS", raising=False)
    assert any(
        isinstance(m, PhasedModelMiddleware)
        for m in _build_agent_middleware(ChatOpenAI())
    )
    # 推不出 provider（None／未知物件）→ 不跳過（維持現行為）
    assert any(
        isinstance(m, PhasedModelMiddleware) for m in _build_agent_middleware(None)
    )


def test_execute_streaming_passes_llm_to_middleware_builder():
    """provider 推斷靠 llm；呼叫端不傳 llm 的話跳過清單永遠不生效。"""
    import re
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "core" / "agents" / "base_react_agent.py"
    ).read_text(encoding="utf-8")
    calls = re.findall(r"_build_agent_middleware\(([^)]*)\)", src)
    # 定義那行以外，每個呼叫都要帶 llm
    assert all("llm" in c for c in calls if not c.strip().startswith("llm: Any")), calls


# ── 回答回合模型還是想叫工具（2026-10-03：問 SMR 值不值得買，答案是一行搜尋關鍵字）──
#
# Qwen 系 agent 模型（Occamy-1.0）在 tool_choice="none" 時會把工具呼叫寫成文字
# （<tool_call><function=web_search>…），sanitizer 剝掉標籤後只剩參數值。
# 修法：第一次回答加 stop=["<tool_call>"] 得到空回覆，空回覆就重試一次。


class _Tool:
    def __init__(self, name):
        self.name = name


def _blank():
    return _Resp([AIMessage(content="")])


def _run_sync(mw, req, responses):
    seen = []
    queue = list(responses)

    def handler(r):
        seen.append(r)
        return queue.pop(0)

    return mw.wrap_model_call(req, handler), seen


def test_answer_turn_stops_at_tool_markup_and_keeps_the_tool_list():
    """停止詞讓模型一想寫工具呼叫就收手；工具清單不能動（快取）。"""
    tools = [_Tool("web_search"), _Tool(FINALIZE_TOOL_NAME)]
    mw = PhasedModelMiddleware()
    _, seen = _run(
        mw,
        _Req(tools=tools),
        [_Resp([_ai(FINALIZE_TOOL_NAME)]), _Resp([_ai(content="答案")])],
    )
    assert seen[1].model_settings["stop"] == ["<tool_call>"]
    assert seen[1].tools == tools
    assert seen[1].tool_choice == "none"


@pytest.mark.parametrize(
    "given, expected",
    [
        ("</end>", ["</end>", "<tool_call>"]),
        (["a", "b"], ["a", "b", "<tool_call>"]),
        (["<tool_call>"], ["<tool_call>"]),
    ],
)
def test_callers_own_stop_sequences_are_kept_not_replaced(given, expected):
    mw = PhasedModelMiddleware()
    _, seen = _run(
        mw,
        _Req(model_settings={"stop": given}),
        [_Resp([_ai(FINALIZE_TOOL_NAME)]), _Resp([_ai(content="答案")])],
    )
    assert seen[1].model_settings["stop"] == expected


def test_blank_answer_is_retried_once_without_the_sentinel():
    """事故重現：模型想再叫工具 → 空回覆 → 重試一次要寫出正文。"""
    tools = [_Tool("web_search"), _Tool(FINALIZE_TOOL_NAME)]
    history = [HumanMessage(content="請問現在SMR值的購買嗎")]
    mw = PhasedModelMiddleware()
    out, seen = _run(
        mw,
        _Req(tools=tools, messages=history),
        [_Resp([_ai(FINALIZE_TOOL_NAME)]), _blank(), _Resp([_ai(content="完整分析")])],
    )
    assert out.result[-1].content == "完整分析"
    assert len(seen) == 3  # 規劃 + 空回覆 + 重試
    retry = seen[2]
    # sentinel 拿掉（模型一直想叫的就是它），其他工具原封不動
    assert [t.name for t in retry.tools] == ["web_search"]
    assert retry.tool_choice == "none"
    assert retry.model_settings["stop"] == ["<tool_call>"]
    # 原本的對話在前，結尾補一句收尾提示（不能動到前面的內容，快取才吃得到）
    assert retry.messages[0] is history[0]
    assert isinstance(retry.messages[-1], HumanMessage)
    assert "same language as the user" in retry.messages[-1].content


def test_retry_happens_at_most_once():
    """重試還是空 → 照原樣往上交（上游有空回覆兜底訊息），不能無限重試燒 token。"""
    mw = PhasedModelMiddleware()
    out, seen = _run(
        mw,
        _Req(),
        [
            _Resp([_ai(FINALIZE_TOOL_NAME)]),
            _blank(),
            _blank(),
            _Resp([_ai(content="不該被呼叫")]),
        ],
    )
    assert len(seen) == 3
    assert out.result[-1].content == ""


def test_planner_turn_cap_path_also_retries_a_blank_answer():
    """真實事故走的就是這條：規劃回合撞上限 → 回答階段 → 模型還想搜尋。"""
    mw = PhasedModelMiddleware(max_planner_turns=1)
    out, seen = _run(
        mw,
        _Req(state={"messages": [_ai("a")]}, tools=[_Tool(FINALIZE_TOOL_NAME)]),
        [_blank(), _Resp([_ai(content="完整分析")])],
    )
    assert len(seen) == 2
    assert out.result[-1].content == "完整分析"
    assert seen[1].tools == []


def test_sync_path_retries_too():
    mw = PhasedModelMiddleware()
    out, seen = _run_sync(
        mw,
        _Req(tools=[_Tool(FINALIZE_TOOL_NAME)]),
        [_Resp([_ai(FINALIZE_TOOL_NAME)]), _blank(), _Resp([_ai(content="完整分析")])],
    )
    assert out.result[-1].content == "完整分析"
    assert len(seen) == 3


def test_a_real_answer_costs_exactly_one_call():
    """行為正常的模型（DeepSeek、NeoHorse）不能因為這個保險多花一次呼叫。"""
    mw = PhasedModelMiddleware()
    out, seen = _run(
        mw, _Req(), [_Resp([_ai(FINALIZE_TOOL_NAME)]), _Resp([_ai(content="答案")])]
    )
    assert len(seen) == 2
    assert out.result[-1].content == "答案"


@pytest.mark.parametrize(
    "response, blank",
    [
        (_Resp([AIMessage(content="")]), True),
        (_Resp([AIMessage(content="  \n ")]), True),
        (_Resp([AIMessage(content=[{"type": "reasoning", "summary": []}])]), True),
        (_Resp([AIMessage(content=[{"type": "text", "text": "有內容"}])]), False),
        (_Resp([AIMessage(content="有內容")]), False),
        # 有工具呼叫就不是空回覆（那是正常的規劃回合輸出）
        (_Resp([_ai("web_search")]), False),
        # 沒有訊息／最後一則不是 AI：不要亂重試
        (_Resp([]), False),
        (_Resp([HumanMessage(content="")]), False),
    ],
)
def test_blank_answer_detection(response, blank):
    from core.agents.phased_model import _is_blank_answer

    assert _is_blank_answer(response) is blank
