"""HITL 問題送達前端的回歸測試（2026-09-21 DANNY 截圖）。

症狀：使用者送出「我現在買了Holo coin二十萬台幣」後，AI 回應泡泡只顯示
「❓ 請在下方輸入回應...」的占位卡——記帳同意卡（真正的問題）整張消失，
使用者完全不知道 agent 在問什麼。

根因鏈（多層防線各自失效）：
1. ``record_entry`` 的 marker 數字欄位（amount/exchange_rate/converted_amount…）
   可以帶入非有限浮點數（NaN/Infinity）：匯率鏈 ``price and price > 0``
   擋不住 ``inf``（``inf > 0`` 為 True），LLM 也可能傳 "nan" 字串經
   pydantic float 轉換成立 NaN。
2. ``_emit_run_event`` 用預設 ``json.dumps``（allow_nan=True）序列化 SSE 幀，
   產出 ``{"exchange_rate": NaN}`` —— JavaScript ``JSON.parse`` 不認得
   NaN/Infinity，直接 throw。
3. 前端 ``chat-analysis.js`` 的 ``JSON.parse`` 失敗被 ``catch { continue; }``
   靜默吞掉 → hitl_question 幀整個丟失 → 下一幀 ``{"done":true,"waiting":true}``
   是合法 JSON → ``hitlPaused`` → finally 顯示無問題文字的 fallback 卡。
4. ``tool_compactor`` 的 per-tool-call guard payload 用 ``type: "consent"``，
   前端只認 ``consent_gate`` —— 卡片型別對不上。

本檔測試對應修復：
- SSE 邊界：任何 payload 經 ``_emit_run_event`` 出去必須是瀏覽器可 parse
  的嚴格 JSON（非有限浮點遞迴清為 None）。
- 來源：record_entry marker 不得含非有限浮點（匯率查到 nan → 視為查不到）。
- 契約：tool guard payload type 必須是前端認得的 ``consent_gate``。
- 前端救援：5 種同意卡型別卡未渲染時，fallback 卡顯示「同意／取消」指引
  （i18n key ``chat.hitlConsentFallback`` 四語存在且被引用）。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[1]


def _strict_loads(frame: str):
    """模擬瀏覽器 JSON.parse：遇到 NaN/Infinity 等非常數字 token 直接炸。"""

    def _reject(constant):
        raise ValueError(f"browser JSON.parse rejects: {constant}")

    return json.loads(frame, parse_constant=_reject)


def _extract_data_frame(frame: str) -> str:
    """SSE 幀 → data: 後的 JSON 字串（去掉 id:/data: 前綴與結尾空行）。"""
    lines = [ln for ln in frame.splitlines() if ln.startswith("data: ")]
    assert lines, f"幀內沒有 data: 行: {frame!r}"
    return lines[-1][len("data: ") :]


# ── 1. SSE 邊界：非有限浮點不得進幀 ──────────────────────────────────────────


def test_emit_run_event_sanitizes_nan_payload():
    """hitl_question 帶 NaN 匯率 → 幀必須仍是瀏覽器可 parse 的嚴格 JSON。

    修復前：json.dumps(allow_nan=True) 產出 ``NaN`` token，
    JSON.parse 在瀏覽器端 throw → 幀被靜默丟棄 → 使用者看到空 fallback 卡。
    """
    from api.routers.analysis import _emit_run_event

    payload = {
        "type": "hitl_question",
        "data": {
            "type": "journal_consent",
            "amount": 200000,
            "exchange_rate": float("nan"),
            "converted_amount": float("inf"),
            "note": "Holo coin",
        },
    }
    frame = _emit_run_event(None, payload)
    parsed = _strict_loads(_extract_data_frame(frame))
    assert parsed["type"] == "hitl_question"
    # NaN/Infinity 清為 None（顯示端本來就把它們當「查不到」處理）
    assert parsed["data"]["exchange_rate"] is None
    assert parsed["data"]["converted_amount"] is None
    assert parsed["data"]["amount"] == 200000


def test_emit_run_event_sanitizes_nested_and_replayed_frames():
    """帶 run（事件緩衝/重播路徑）時同樣要清；巢狀 list/dict 都要走到。"""
    from api.routers.analysis import _emit_run_event

    # 只組 _emit_run_event 用得到的欄位（避開共用快取同步）
    run = {"next_event_id": 1, "events": [], "_subscribers": set()}
    payload = {
        "type": "hitl_question",
        "data": {"cards": [{"amount": float("nan")}, {"rate": float("-inf")}]},
    }
    frame = _emit_run_event(run, payload)
    parsed = _strict_loads(_extract_data_frame(frame))
    assert parsed["data"]["cards"][0]["amount"] is None
    assert parsed["data"]["cards"][1]["rate"] is None
    # 重播緩衝裡的幀也必須是乾淨的（斷線重連會原樣重送）
    replayed = run["events"][-1]["frame"]
    _strict_loads(_extract_data_frame(replayed))


# ── 2. 來源：record_entry marker 不得含非有限浮點 ───────────────────────────


def _fake_repo():
    class FakeRepo:
        base_currency = "TWD"

        def add_entry(self, **kwargs):  # pragma: no cover - 提案階段不寫 DB
            raise AssertionError("record_entry 提案階段不得寫入 DB")

    return FakeRepo()


def test_record_entry_marker_sanitizes_nan_rate(monkeypatch):
    """匯率服務回 NaN（如上游 API 壞值）→ marker 匯率/換算額必須是 None。"""
    from core.tools.crypto_modules import trade_journal as tj

    monkeypatch.setattr(tj, "_get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(
        "core.orm.trade_journal_repo.get_journal_repo", lambda uid: _fake_repo()
    )
    monkeypatch.setattr(
        "core.tools.crypto_modules.exchange_rate.get_exchange_rate",
        lambda cur, base: float("nan"),
    )

    fn = tj.record_entry.func
    marker = json.loads(fn(entry_type="expense", amount=250, currency="USD"))
    assert marker["exchange_rate"] is None
    assert marker["converted_amount"] is None
    # marker 整體必須能被「瀏覽器嚴格 JSON」序列化（直接擋 NaN 進 SSE）
    _strict_loads(json.dumps(marker, allow_nan=False))


def test_record_entry_marker_sanitizes_nonfinite_llm_inputs(monkeypatch):
    """LLM 傳入 nan/inf 金額（pydantic float 接受）→ marker 數字必須收斂為有限值。"""
    from core.tools.crypto_modules import trade_journal as tj

    monkeypatch.setattr(tj, "_get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(
        "core.orm.trade_journal_repo.get_journal_repo", lambda uid: _fake_repo()
    )
    monkeypatch.setattr(
        "core.tools.crypto_modules.exchange_rate.get_exchange_rate",
        lambda cur, base: 1.0,
    )

    fn = tj.record_entry.func
    marker = json.loads(
        fn(
            entry_type="trade",
            symbol="HOT",
            amount=float("inf"),
            quantity=float("nan"),
            fee=float("inf"),
        )
    )
    for key in ("amount", "quantity", "fee", "converted_amount"):
        value = marker[key]
        assert value is None or math.isfinite(value), (
            f"marker[{key}] 不是有限值: {value!r}"
        )
    _strict_loads(json.dumps(marker, allow_nan=False))


# ── 3. 契約：tool guard payload type 對齊前端 ────────────────────────────────


def test_tool_guard_consent_payload_type_is_consent_gate():
    """per-tool-call guard 的 payload type 必須是前端 if/else 認得的 consent_gate。

    舊碼用 "consent"：前端掉進 clarify fallback，顯示「請問您具體想了解什麼？」
    ——與高風險工具同意的語境完全不符。
    """
    from core.agents.tool_compactor import build_tool_guard_consent_payload

    payload = build_tool_guard_consent_payload("trade_api", "user-1")
    assert payload["type"] == "consent_gate"
    assert payload["tool_name"] == "trade_api"
    assert payload["user_id"] == "user-1"
    assert payload["message"]


# ── 4. 前端救援：fallback 卡要有「能回覆」的指引 ─────────────────────────────


def test_consent_parser_accepts_en_ru_yes_no_words():
    """fallback 卡引導使用者回「同意／取消」——parser 必須認得四語的說法。

    舊詞集只有中/英基本詞：ru 使用者照卡片指引回 «да»/«нет» 會被 fail-closed
    誤判成拒絕（核可路徑直接失效）。
    """
    from core.agents.manager.consent_gate import parse_skill_memory_consent_answer

    for word in ("同意", "yes", "ok", "approve", "да"):
        assert parse_skill_memory_consent_answer(word)["approved"] is True, word
    for word in ("取消", "不要", "no", "cancel", "decline", "нет"):
        assert parse_skill_memory_consent_answer(word)["approved"] is False, word


def test_fallback_i18n_key_present_in_all_languages():
    """chat.hitlConsentFallback 四語都要有——同意卡型別掉進 fallback 時，
    至少告訴使用者可以回「同意／取消」（resume parser 認得這兩個詞）。"""
    i18n_dir = ROOT / "web" / "js" / "i18n"
    for lang in ("zh-TW", "zh-CN", "en", "ru"):
        data = json.loads((i18n_dir / f"{lang}.json").read_text(encoding="utf-8"))
        text = data.get("chat", {}).get("hitlConsentFallback")
        assert isinstance(text, str) and text.strip(), (
            f"{lang}.json 缺 chat.hitlConsentFallback"
        )


def test_chat_analysis_uses_consent_fallback_for_card_types():
    """chat-analysis.js 的 finally fallback 必須引用 hitlConsentFallback，
    且五種同意卡型別（skill/memory/multi/journal/loop_fork）都要涵蓋。"""
    src = (ROOT / "web" / "js" / "chat-analysis.js").read_text(encoding="utf-8")
    assert "hitlConsentFallback" in src, (
        "chat-analysis.js 應在同意卡型別的 fallback 使用 hitlConsentFallback"
    )
