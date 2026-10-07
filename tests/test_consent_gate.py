"""Consent Gate 單元測試（Trustworthy AI Hackathon — Policy Gate 支柱）。

測 core/agents/manager/consent_gate.py：
- scan_high_risk_tools 從工具池挑 high-risk
- should_require_consent 決策邏輯
- build_consent_payload 送給前端的結構
- parse_consent_answer 解析前端回應（含 fail-closed）
- log_consent_decision / log_high_risk_tool_execution 記 audit
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.agents.manager.consent_gate import (
    append_guard_audit_receipt,
    assess_user_trust,
    build_consent_payload,
    log_consent_decision,
    log_high_risk_tool_execution,
    parse_consent_answer,
    scan_high_risk_tools,
    should_require_consent,
)
from core.agents.tool_registry import ToolMetadata


def _meta(name: str, risk_level: str = "low") -> ToolMetadata:
    """建一個測試用 ToolMetadata。"""
    return ToolMetadata(
        name=name,
        description=f"test tool {name}",
        input_schema={},
        handler=lambda: None,
        risk_level=risk_level,
    )


class TestScanHighRiskTools:
    def test_returns_only_high_risk(self):
        """只挑 risk_level='high' 的。"""
        tools = [
            _meta("safe_query", "low"),
            _meta("news", "medium"),
            _meta("submit_kyc", "high"),
            _meta("get_balance", "high"),
            _meta("another_low", "low"),
        ]
        high = scan_high_risk_tools(tools)
        assert [m.name for m in high] == ["submit_kyc", "get_balance"]

    def test_empty_when_no_high_risk(self):
        tools = [_meta("a", "low"), _meta("b", "medium")]
        assert scan_high_risk_tools(tools) == []

    def test_empty_pool_returns_empty(self):
        assert scan_high_risk_tools([]) == []

    def test_missing_risk_level_defaults_low(self):
        """ToolMetadata 預設 risk_level='low'，不會被誤判 high。"""
        m = ToolMetadata(name="x", description="d", input_schema={}, handler=None)
        assert scan_high_risk_tools([m]) == []


class TestShouldRequireConsent:
    def test_no_high_risk_tools_no_consent(self):
        """池中無 high-risk tool → 不需 consent。"""
        assessment = assess_user_trust(wallet_verified=True)
        assert should_require_consent([], assessment, already_granted=False) is False

    def test_high_risk_low_trust_requires_consent(self):
        """有 high-risk tool + 信任度不足 → 需 consent。"""
        high_risk = [_meta("submit_kyc", "high")]
        # 未驗錢包 → trust 0 < 50
        assessment = assess_user_trust(wallet_verified=False)
        assert should_require_consent(high_risk, assessment, already_granted=False) is True

    def test_high_risk_high_trust_still_requires_explicit_consent(self):
        """身分信任分數不能取代高風險 action 的明確授權。"""
        high_risk = [_meta("submit_kyc", "high")]
        # 已過 World ID → trust 65 >= 50
        assessment = assess_user_trust(
            wallet_verified=True, verified_stamps={"world_id": True}
        )
        assert should_require_consent(high_risk, assessment, already_granted=False) is True

    def test_already_granted_skips_consent(self):
        """本次 session 已同意過 → 不重彈。"""
        high_risk = [_meta("submit_kyc", "high")]
        assessment = assess_user_trust(wallet_verified=False)
        assert should_require_consent(high_risk, assessment, already_granted=True) is False


class TestBuildConsentPayload:
    def test_payload_has_required_fields(self):
        high_risk = [_meta("submit_kyc", "high")]
        assessment = assess_user_trust(wallet_verified=True)
        payload = build_consent_payload(high_risk, assessment, "zh-TW")
        assert payload["type"] == "consent_gate"
        assert "message" in payload
        assert len(payload["tools"]) == 1
        assert payload["tools"][0]["name"] == "submit_kyc"
        assert payload["tools"][0]["risk_level"] == "high"
        assert "trust" in payload
        assert payload["trust"]["trust_score"] == assessment.trust_score

    def test_payload_multilingual_message(self):
        high_risk = [_meta("submit_kyc", "high")]
        assessment = assess_user_trust(wallet_verified=True)
        for lang in ["zh-TW", "zh-CN", "en", "ru"]:
            payload = build_consent_payload(high_risk, assessment, lang)
            assert payload["message"]  # 非空

    def test_payload_falls_back_for_unknown_language(self):
        high_risk = [_meta("submit_kyc", "high")]
        assessment = assess_user_trust(wallet_verified=True)
        payload = build_consent_payload(high_risk, assessment, "fr")
        assert payload["message"]  # fallback 到 zh-TW，不報錯


class TestParseConsentAnswer:
    def test_approve_dict(self):
        result = parse_consent_answer({"action": "consent", "approved": True})
        assert result["approved"] is True

    def test_decline_dict(self):
        result = parse_consent_answer({"action": "consent", "approved": False})
        assert result["approved"] is False

    def test_cancel_action_is_decline(self):
        result = parse_consent_answer({"action": "cancel"})
        assert result["approved"] is False

    def test_execute_action_is_approve(self):
        result = parse_consent_answer({"action": "execute"})
        assert result["approved"] is True

    def test_yes_string_approves(self):
        for s in ["yes", "Y", "ok", "同意", "好"]:
            assert parse_consent_answer(s)["approved"] is True

    def test_no_string_declines(self):
        for s in ["no", "n", "cancel", "否", "不要", "取消"]:
            assert parse_consent_answer(s)["approved"] is False

    def test_unparseable_fails_closed(self):
        """無法判讀 → 視為拒絕（高風險動作不該被模糊同意）。"""
        result = parse_consent_answer("maybe later")
        assert result["approved"] is False
        result2 = parse_consent_answer(None)
        assert result2["approved"] is False
        result3 = parse_consent_answer(12345)
        assert result3["approved"] is False


class TestAuditLogging:
    @patch("core.agents.manager.consent_gate.audit_log")
    def test_log_consent_decision_calls_audit(self, mock_audit):
        high_risk = [_meta("submit_kyc", "high")]
        assessment = assess_user_trust(wallet_verified=True)
        log_consent_decision(
            user_id="u1",
            username="alice",
            high_risk_tools=high_risk,
            assessment=assessment,
            approved=True,
        )
        mock_audit.assert_called_once()
        call_args = mock_audit.call_args
        assert call_args.args[0] == "consent_high_risk_action"
        call_kwargs = call_args.kwargs
        assert call_kwargs["user_id"] == "u1"
        assert call_kwargs["resource_type"] == "tool"
        assert call_kwargs["success"] is True
        assert "consent" in call_kwargs["metadata"]
        assert call_kwargs["metadata"]["consent"]["granted"] is True

    @patch("core.agents.manager.consent_gate.audit_log")
    def test_log_high_risk_tool_execution_calls_audit(self, mock_audit):
        log_high_risk_tool_execution(
            user_id="u1",
            tool_name="submit_kyc_application",
            consent_granted=True,
        )
        mock_audit.assert_called_once()
        call_args = mock_audit.call_args
        assert call_args.args[0] == "high_risk_tool_executed"
        assert call_args.kwargs["resource_id"] == "submit_kyc_application"

    @pytest.mark.asyncio
    async def test_append_guard_audit_receipt_uses_immutable_store(self):
        with patch(
            "core.orm.action_guard_repo.action_guard_repo.append_internal_receipt",
            new=AsyncMock(return_value={"receipt_hash": "a" * 64}),
        ) as append:
            receipt = await append_guard_audit_receipt(
                user_id="u1",
                event_type="agent.consent",
                payload={"approved": True, "tools": ["wallet_lookup"]},
            )

        assert receipt["receipt_hash"] == "a" * 64
        append.assert_awaited_once_with(
            user_id="u1",
            event_type="agent.consent",
            payload={"approved": True, "tools": ["wallet_lookup"]},
        )

    @pytest.mark.asyncio
    async def test_append_guard_audit_receipt_skips_anonymous_user(self):
        assert await append_guard_audit_receipt(
            user_id=None,
            event_type="agent.consent",
            payload={"approved": False},
        ) is None


# ──────────────────────────────────────────────────────────────────────────────
# GraphInterrupt 不該被 claw_loop 的 except Exception 誤捕成「故障」
# （正式站 log 出現 "[ClawLoop] Consent Gate 故障" 的根因）
# ──────────────────────────────────────────────────────────────────────────────


def test_graph_interrupt_is_exception_subclass():
    """GraphInterrupt 是 Exception 子類——這正是它被 except Exception 誤捕的根因。

    確認這個事實，以 claw_loop.py 加 except GraphInterrupt: raise 來放行。
    """
    from langgraph.errors import GraphInterrupt

    assert issubclass(GraphInterrupt, Exception)


def test_claw_loop_imports_graph_interrupt():
    """確認 claw_loop.py 有匯入 GraphInterrupt（except 分支要用）。"""
    from core.agents.manager import claw_loop

    assert hasattr(claw_loop, "GraphInterrupt")


def test_consent_interrupt_not_swallowed_as_fault():
    """模擬 claw_loop 的 except 序列：GraphInterrupt 應上拋，不落「故障」分支。

    重現 claw_loop.py:883 的 except 結構（含 GraphInterrupt 放行），
    確認 interrupt 不被 except Exception 捕成故障 WARNING。
    """
    import asyncio

    from langgraph.errors import GraphInterrupt

    fault_logged = False

    def simulate_consent_block():
        """重現 claw_loop consent 區塊的 except 序列。"""
        nonlocal fault_logged
        try:
            # 模擬 interrupt() 內部 raise GraphInterrupt
            raise GraphInterrupt(("consent_gate", {"tools": []}))
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except GraphInterrupt:
            # 正常 control-flow，原樣上拋（這是修法新增的分支）
            raise
        except Exception:  # noqa: BLE001
            fault_logged = True

    # GraphInterrupt 應該上拋，不是被吞
    raised = False
    try:
        simulate_consent_block()
    except GraphInterrupt:
        raised = True

    assert raised is True
    assert fault_logged is False  # 沒被記成故障


# ──────────────────────────────────────────────────────────────────────────────
# Consent Gate 意圖過濾（避免過度觸發）
# ──────────────────────────────────────────────────────────────────────────────


def _hr_tool(name):
    """建一個模擬的 high-risk tool dict（scan_high_risk_tools 的輸出格式）。"""
    return {"name": name, "risk_level": "high"}


def test_intent_filter_pure_query_returns_empty():
    """純查詢（價格/新聞/分析）不匹配任何 high-risk 意圖 → 不彈 consent。"""
    from core.agents.manager.claw_loop import _filter_high_risk_by_intent

    all_tools = [
        _hr_tool("submit_kyc_application"),
        _hr_tool("get_eth_balance"),
        _hr_tool("get_whale_alerts"),
    ]
    # 問比特幣價格 — 不涉及錢包/KYC/交易
    result = _filter_high_risk_by_intent(all_tools, "比特幣價格目前是多少")
    assert result == []


def test_intent_filter_kyc_query_keeps_kyc():
    """query 含 KYC/開戶意圖 → 保留 submit_kyc_application。"""
    from core.agents.manager.claw_loop import _filter_high_risk_by_intent

    all_tools = [
        _hr_tool("submit_kyc_application"),
        _hr_tool("get_eth_balance"),
    ]
    result = _filter_high_risk_by_intent(all_tools, "我想申請開戶 KYC")
    kept_names = [t["name"] for t in result]
    assert "submit_kyc_application" in kept_names
    assert "get_eth_balance" not in kept_names  # 不相關的不保留


def test_intent_filter_wallet_address_keeps_balance_tools():
    """query 含 0x 地址 → 保留錢包餘額/交易相關 tool。"""
    from core.agents.manager.claw_loop import _filter_high_risk_by_intent

    all_tools = [
        _hr_tool("get_eth_balance"),
        _hr_tool("get_erc20_token_balance"),
        _hr_tool("get_address_transactions"),
        _hr_tool("submit_kyc_application"),
    ]
    result = _filter_high_risk_by_intent(
        all_tools, "查 0x1234567890abcdef1234567890abcdef12345678 的 ETH 餘額"
    )
    kept_names = [t["name"] for t in result]
    assert "get_eth_balance" in kept_names
    assert "submit_kyc_application" not in kept_names  # KYC 不相關


def test_intent_filter_whale_query_keeps_whale_tool():
    """query 含鯨魚/大額轉帳 → 保留 get_whale_alerts。"""
    from core.agents.manager.claw_loop import _filter_high_risk_by_intent

    result = _filter_high_risk_by_intent(
        [_hr_tool("get_whale_alerts")], "最近有哪些鯨魚大額轉帳"
    )
    assert len(result) == 1


def test_intent_filter_empty_tools_returns_empty():
    """沒有 high-risk tool → 空。"""
    from core.agents.manager.claw_loop import _filter_high_risk_by_intent

    assert _filter_high_risk_by_intent([], "anything") == []


def test_intent_filter_empty_query_returns_empty():
    """空 query → 不匹配 → 空（保守：不知意圖就不彈，因為可能還在初始化）。"""
    from core.agents.manager.claw_loop import _filter_high_risk_by_intent

    assert _filter_high_risk_by_intent([_hr_tool("submit_kyc_application")], "") == []


# ══════════════════════════════════════════════════════════════════
# Journal 記帳確認卡（金流 HITL — Propose → Confirm → Commit）
# ══════════════════════════════════════════════════════════════════


class TestJournalConsentPayload:
    def _signal(self, entry_type="expense"):
        return {
            "__needs_consent__": True,
            "kind": "journal_entry",
            "entry_type": entry_type,
            "symbol": "",
            "market": "",
            "amount": 250,
            "currency": "TWD",
            "quantity": 1,
            "side": "buy",
            "category": "food",
            "note": "lunch",
            "exchange_rate": 1.0,
            "converted_amount": 250.0,
            "base_currency": "TWD",
        }

    def test_payload_type_and_fields(self):
        from core.agents.manager.consent_gate import build_journal_consent_payload

        p = build_journal_consent_payload(self._signal(), "zh-TW")
        assert p["type"] == "journal_consent"
        assert p["entry_type"] == "expense"
        assert p["amount"] == 250
        assert p["currency"] == "TWD"
        assert p["category"] == "food"
        assert p["exchange_rate"] == 1.0
        assert p["converted_amount"] == 250.0
        assert "帳本" in p["message"]

    def test_payload_messages_all_four_languages(self):
        from core.agents.manager.consent_gate import build_journal_consent_payload

        for lang, fragment in [
            ("zh-TW", "帳本"), ("zh-CN", "帐本"),
            ("en", "until you confirm"), ("ru", "подтверждения"),
        ]:
            p = build_journal_consent_payload(self._signal(), lang)
            assert fragment in p["message"], f"{lang} 訊息應含 {fragment}"

    def test_payload_trade_type(self):
        from core.agents.manager.consent_gate import build_journal_consent_payload

        sig = self._signal("trade")
        sig.update(symbol="BTC", market="crypto", quantity=0.5, leverage=10,
                   direction="short", instrument_type="futures")
        p = build_journal_consent_payload(sig, "en")
        assert p["entry_type"] == "trade"
        assert p["symbol"] == "BTC"
        assert p["leverage"] == 10

    def test_consent_type_map_has_journal(self):
        from core.agents.manager.consent_gate import _CONSENT_TYPE_MAP

        assert _CONSENT_TYPE_MAP["journal_entry"] == "journal_consent"


class TestJournalConsentParse:
    def test_parse_approved_with_edited_fields(self):
        from core.agents.manager.consent_gate import (
            parse_skill_memory_consent_answer,
        )

        parsed = parse_skill_memory_consent_answer({
            "action": "journal_entry",
            "approved": True,
            "edited_fields": {"amount": 300, "category": "food"},
        })
        assert parsed["approved"] is True
        assert parsed["edited_fields"]["amount"] == 300

    def test_parse_declined(self):
        from core.agents.manager.consent_gate import (
            parse_skill_memory_consent_answer,
        )

        parsed = parse_skill_memory_consent_answer({
            "action": "journal_entry", "approved": False,
        })
        assert parsed["approved"] is False
        assert parsed["edited_fields"] == {}

    def test_parse_unrecognizable_fails_closed(self):
        """無法判讀 → 拒絕（金流 fail-closed）。"""
        from core.agents.manager.consent_gate import (
            parse_skill_memory_consent_answer,
        )

        parsed = parse_skill_memory_consent_answer({"action": "journal_entry"})
        assert parsed["approved"] is False
        parsed2 = parse_skill_memory_consent_answer(42)
        assert parsed2["approved"] is False


class TestJournalConsentApply:
    def test_apply_writes_with_frozen_rate(self, monkeypatch):
        """核准後寫入：未編輯 → 沿用提案凍結匯率。"""
        from core.agents.manager import claw_loop

        captured = {}

        class FakeRepo:
            def add_entry(self, **kwargs):
                captured.update(kwargs)
                return {"ok": True, "id": 42}

        monkeypatch.setattr(
            "core.orm.trade_journal_repo.get_journal_repo",
            lambda uid: FakeRepo(),
        )
        signal = {
            "kind": "journal_entry", "entry_type": "expense", "symbol": "",
            "market": "", "amount": 250, "currency": "TWD", "quantity": 1,
            "side": "buy", "category": "food", "note": "lunch",
            "fee": 0, "instrument_type": "spot", "direction": "long",
            "leverage": 1, "exchange_rate": 1.0, "converted_amount": 250.0,
        }
        entry_id = claw_loop._apply_consent_write("user-1", signal, {})
        assert entry_id == 42
        assert captured["price"] == 250
        assert captured["exchange_rate"] == 1.0
        assert captured["converted_amount"] == 250.0
        assert captured["rate_source"] == "frozen"
        assert captured["source"] == "chat"

    def test_apply_edited_amount_drops_frozen_rate(self, monkeypatch):
        """使用者改了金額 → 放棄凍結匯率（repo 以核可當下重算）。"""
        from core.agents.manager import claw_loop

        captured = {}

        class FakeRepo:
            def add_entry(self, **kwargs):
                captured.update(kwargs)
                return {"ok": True, "id": 43}

        monkeypatch.setattr(
            "core.orm.trade_journal_repo.get_journal_repo",
            lambda uid: FakeRepo(),
        )
        signal = {
            "kind": "journal_entry", "entry_type": "expense",
            "amount": 250, "currency": "USD", "category": "food",
            "exchange_rate": 32.0, "converted_amount": 8000.0,
        }
        entry_id = claw_loop._apply_consent_write(
            "user-1", signal, {"amount": "300", "note": "edited"}
        )
        assert entry_id == 43
        # 字串 "300" 防禦性轉型為 300；匯率改 auto 重算
        assert captured["price"] == 300.0
        assert captured["exchange_rate"] is None
        assert captured["converted_amount"] is None
        assert captured["rate_source"] == "auto"
        assert captured["note"] == "edited"

    def test_apply_write_failure_returns_none(self, monkeypatch):
        from core.agents.manager import claw_loop

        class FakeRepo:
            def add_entry(self, **kwargs):
                return {"ok": False, "error": "db down"}

        monkeypatch.setattr(
            "core.orm.trade_journal_repo.get_journal_repo",
            lambda uid: FakeRepo(),
        )
        entry_id = claw_loop._apply_consent_write(
            "user-1", {"kind": "journal_entry", "amount": 1}, {}
        )
        assert entry_id is None

    def test_apply_no_user_id_skips(self):
        from core.agents.manager import claw_loop

        assert claw_loop._apply_consent_write(None, {"kind": "journal_entry"}, {}) is None


# ──────────────────────────────────────────────────────────────────────────────
# Consent Gate 檢查本身噴例外 → fail closed（claw_loop._claw_loop_node）
# ──────────────────────────────────────────────────────────────────────────────
# 舊碼只在「high_risk_tools 已經算出來」時才擋；例外若發生在掃描工具池的
# 階段（_filter_tool_metas／scan_high_risk_tools／意圖過濾），high_risk_tools
# 還是 []，except 分支當成「沒有高風險工具」→ agent 帶著完整工具池照跑，
# 高風險工具不經同意就能被呼叫。


def _consent_manager():
    from unittest.mock import MagicMock

    from core.agents.manager._main import ManagerAgent
    from core.agents.models import AgentResult

    manager = ManagerAgent(
        llm_client=MagicMock(),
        agent_registry=MagicMock(),
        tool_registry=MagicMock(),
        user_id="test-user",
        session_id="test-session",
    )
    manager.language = "en"
    agent = MagicMock()
    agent._filter_tool_metas.return_value = [_meta("submit_kyc_application", "high")]
    agent.execute_streaming = AsyncMock(
        return_value=AgentResult(
            success=True,
            message="done",
            agent_name="cryptomind",
            data={"used_tools": ["submit_kyc_application"]},
        )
    )
    manager.agent_registry.get.return_value = agent
    manager._track_conversation = AsyncMock()
    manager.check_idle_consolidation = MagicMock(return_value=False)
    return manager, agent


_KYC_STATE = {
    "query": "幫我用 TON 錢包申請數位帳戶 KYC",
    "history": "",
    "session_id": "test-session",
}


def _assert_blocked(result, agent):
    from core.i18n import t

    agent.execute_streaming.assert_not_awaited()
    assert result["final_response"] == t(
        "ui_messages.consent.high_risk_check_failed", "en"
    )
    assert result.get("consent_granted") is False


class TestConsentGateFailClosed:
    async def test_tool_pool_scan_exception_blocks_run(self):
        manager, agent = _consent_manager()
        agent._filter_tool_metas.side_effect = RuntimeError("registry down")

        result = await manager._claw_loop_node(dict(_KYC_STATE))

        _assert_blocked(result, agent)

    async def test_high_risk_scan_exception_blocks_run(self):
        manager, agent = _consent_manager()
        with patch(
            "core.agents.manager.consent_gate.scan_high_risk_tools",
            side_effect=RuntimeError("bad metadata"),
        ):
            result = await manager._claw_loop_node(dict(_KYC_STATE))

        _assert_blocked(result, agent)

    async def test_intent_filter_exception_blocks_run(self):
        manager, agent = _consent_manager()
        with patch(
            "core.agents.manager.claw_loop._filter_high_risk_by_intent",
            side_effect=RuntimeError("regex blew up"),
        ):
            result = await manager._claw_loop_node(dict(_KYC_STATE))

        _assert_blocked(result, agent)

    async def test_decision_exception_after_scan_blocks_run(self):
        """掃描完、決策時才噴（原本就擋的情境）——守住不退化。"""
        manager, agent = _consent_manager()
        with patch(
            "core.agents.manager.consent_gate.should_require_consent",
            side_effect=RuntimeError("trust layer down"),
        ):
            result = await manager._claw_loop_node(dict(_KYC_STATE))

        _assert_blocked(result, agent)

    async def test_failure_is_logged_with_traceback_and_user(self, caplog):
        import logging

        manager, agent = _consent_manager()
        agent._filter_tool_metas.side_effect = RuntimeError("registry down")

        with caplog.at_level(logging.ERROR):
            await manager._claw_loop_node(dict(_KYC_STATE))

        records = [
            r for r in caplog.records if "Consent Gate failure" in r.getMessage()
        ]
        assert records, "Consent Gate 例外沒有記 log"
        assert records[0].exc_info is not None, "要帶 traceback"
        assert "test-user" in records[0].getMessage()
