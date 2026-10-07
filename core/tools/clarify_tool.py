"""clarify 工具 — Hermes 式 model-driven 釐清（hybrid 設計）。

設計（見 PR #317 + 本 PR）：
- **強模型**（Claude/GPT）主動判斷問題範圍/標的不明確時呼叫此工具，由 model-driven
  觸發釐清。比 rule 後衛更靈活（模型判斷語境，不限於範圍問題）。
- **弱模型**（線上 DeepSeek/Qwen）通常不會主動呼叫 → 由 PR #317 的
  `should_clarify_wrong_scope` rule 後衛兜底。兩層雙保險。

技術關鍵（不呼叫 interrupt）：
  純 tool-level `interrupt()` 在這個 codebase 不可行——內層 create_react_agent 沒
  checkpointer / thread_id，resume 進不去內層 agent。所以本工具採 hybrid：
  回傳結構化訊號（`{"__needs_clarify__": True, ...}`），由 claw_loop 的 Phase F
  在 node level 偵測 `used_tools` 含 "clarify" 後，由已驗證的 node-level
  `interrupt()` 接手（接收器零改動）。

訊號傳遞：
  - 工具被呼叫 → `result.data["used_tools"]` 含 "clarify"（既有收集機制）
  - 工具 args（question/options）透過回傳字串進 `result.data["tool_outputs"]`
  - claw_loop `_extract_clarify_signal` 掃 tool_outputs 找 `__needs_clarify__` 標記
"""
import json

from langchain_core.tools import tool

from .schemas import ClarifyInput

# 訊號標記常數（claw_loop Phase F 偵測用）
_CLARIFY_SIGNAL_KEY = "__needs_clarify__"


@tool(args_schema=ClarifyInput)
def clarify(question: str, options: list = None) -> str:
    """Ask the user a clarifying question when the query's target or scope is
    ambiguous. Pauses the conversation and resumes with the user's reply.

    當使用者的問題沒有指明特定標的、或範圍不明確時，呼叫此工具向使用者釐清——
    不要自己猜一個特定標的回答。

    適用情境（呼叫此工具，不要直接回答）：
    - 市場範圍問題但無特定股票：「台股適合買嗎」「美股會跌嗎」「陸股值得投資嗎」
      → 先問使用者指整體大盤、某產業、還是某支特定股票。
    - 無標的意圖：「我想要投資」「我想進場」「該買嗎」（只講意圖沒講標的，
      且沒有任何財務脈絡）→ 先問投資什麼（加密/美股/台股/...）。
    - 多重指代不明：「幫我分析一下」（分析什麼？）

    不適用（直接回答，不要呼叫此工具）：
    - 已指名特定標的：「台積電值得買嗎」「BTC 適合進場嗎」
    - 明確的範圍 + 明確意圖：「台股大盤現在適合進場嗎」
    - 個人理財、預算、還款、儲蓄、資產配置規劃類問題：「月薪六萬，又想還款又想
      投資」「我有 50 萬想規劃一下」→ 先直接回答（一般性框架 + 可用的分析資料），
      需要使用者補充的資訊（收入、負債、期限、風險承受度）在回答裡一併詢問。

    參數：
      question: 向使用者提出的釐清問題（簡短明確）。
      options:  可選的釐清方向清單（選填）。要貼合使用者實際問的內容，
                不要套用固定的「大盤／個股／產業」組合。
    """
    # 回傳結構化訊號——自己不 interrupt。
    # claw_loop Phase F 會偵測此訊號並由 node-level interrupt 接手。
    marker = {
        _CLARIFY_SIGNAL_KEY: True,
        "question": question,
        "options": list(options) if options else [],
    }
    return json.dumps(marker, ensure_ascii=False)


def is_clarify_signal(text: str) -> bool:
    """判斷工具輸出字串是否為 clarify 訊號。"""
    if not text or not isinstance(text, str):
        return False
    return _CLARIFY_SIGNAL_KEY in text and '"question"' in text


def parse_clarify_signal(text: str) -> dict:
    """解析 clarify 訊號字串回 dict。失敗回空 dict。"""
    if not text or not isinstance(text, str):
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, dict) and data.get(_CLARIFY_SIGNAL_KEY):
            return data
    except (ValueError, TypeError):
        pass
    return {}
