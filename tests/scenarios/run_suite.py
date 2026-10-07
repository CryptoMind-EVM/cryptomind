"""
Scenario suite runner — 跑 tests/scenarios/multiturn_suite.yaml 內的所有情境。

用法::

    # 預設跑全部
    python tests/scenarios/run_suite.py

    # 只跑某類別
    python tests/scenarios/run_suite.py --category unknown_ticker

    # 只跑某 scenario id
    python tests/scenarios/run_suite.py --id S1-unknown-ticker

    # 跳過 LLM 真實呼叫（只 validate YAML 結構）
    python tests/scenarios/run_suite.py --dry-run

輸出：
- 每題 trace 進 Langfuse（session_id = scenario id + timestamp）
- summary 印 console
- 詳細結果寫 ``tests/scenarios/results_<timestamp>.json``

需要：
- DATABASE_URL（ Neon 或本地 PG）
- LANGFUSE_* keys
- NVIDIA_API_KEY 或其他 BYOK 透過環境變數
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import yaml

# 確保 repo root 在 sys.path（跑器從 tests/scenarios/ 跑）
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv()

from langgraph.types import Command

from core.agents.bootstrap import bootstrap
from core.agents.manager import MANAGER_GRAPH_RECURSION_LIMIT
from utils.langfuse_init import init_langfuse, shutdown_langfuse
from utils.user_client_factory import create_user_llm_client

SUITE_PATH = Path(__file__).parent / "multiturn_suite.yaml"
RESULTS_DIR = Path(__file__).parent

# fallback messages（從 core/i18n/errors.json 抓主要語言）
_FALLBACK_MARKERS = [
    "模型沒有產生回應",
    "模型没有产生回应",
    "No response was generated",
    "Модель не сформировала",
    "執行完成，但沒有有效結果",
    "执行完成，但没有有效结果",
    "暫時無法處理",
    "暂时无法处理",
]


def load_suite() -> dict:
    with open(SUITE_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_history_text(history: list[dict]) -> str:
    """模擬 api/routers/analysis.py 的 history_text 格式。"""
    lines = []
    for h in history:
        role = "助手" if h["role"] == "assistant" else "用戶"
        lines.append(f"{role}: {h['content']}")
    return "\n".join(lines)


def is_fallback_message(text: str) -> bool:
    if not text or not isinstance(text, str):
        return True
    return any(marker in text for marker in _FALLBACK_MARKERS)


async def run_turn(
    client: Any,
    query: str,
    language: str,
    history_text: str,
    session_id: str,
) -> tuple[str, float, str | None]:
    """跑一輪，回傳 (final_response, elapsed_s, error_traceback)。"""
    manager = bootstrap(
        client,
        web_mode=True,
        language=language,
        user_tier="free",
        user_id=f"scenario-{session_id}",
        session_id=session_id,
        key_fingerprint="nvidia-test",
    )
    config = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": MANAGER_GRAPH_RECURSION_LIMIT,
    }
    graph_input = Command(
        goto="claw_loop",
        update={
            "session_id": session_id,
            "query": query,
            "history": history_text,
            "history_load_status": "loaded" if history_text else "empty",
            "history_truncated": False,
            "history_token_count": len(history_text) // 4,
            "history_message_count": history_text.count("\n") + 1 if history_text else 0,
            "system_prompt": "",
            "enabled_tools": [],
            "task_results": {},
            "language": language,
            "execution_mode": "vending",
        },
    )
    t0 = time.perf_counter()
    try:
        result = await manager.graph.ainvoke(graph_input, config)
        elapsed = time.perf_counter() - t0
        fr = result.get("final_response") if isinstance(result, dict) else None
        return (fr or ""), elapsed, None
    except Exception:
        elapsed = time.perf_counter() - t0
        return ("", elapsed, traceback.format_exc())


def evaluate_turn(
    response: str,
    expect: dict,
) -> dict:
    """比對回應 vs 期望，回 verdict + 細節。"""
    verdicts = []

    # expect_nonempty（預設 True）
    if expect.get("expect_nonempty", True):
        if not response or is_fallback_message(response):
            verdicts.append({
                "check": "nonempty",
                "pass": False,
                "reason": "空回應或 fallback message",
                "response_preview": (response[:100] if response else "(empty)"),
            })
        else:
            verdicts.append({"check": "nonempty", "pass": True})

    # expect_keywords
    for kw in expect.get("expect_keywords", []) or []:
        ok = kw in response
        verdicts.append({
            "check": f"keyword:{kw}",
            "pass": ok,
            "reason": "" if ok else f"回應中缺少關鍵詞 {kw!r}",
        })

    # expect_any_keyword（2026-07-19 新增）：列表中任一命中即 pass。
    # 用於 LLM 可能用多種詞形回應的情況（如 Bitcoin/BTC/比特幣）。
    any_kws = expect.get("expect_any_keyword", []) or []
    if any_kws:
        hit = [kw for kw in any_kws if kw in response]
        verdicts.append({
            "check": f"any_keyword:{any_kws}",
            "pass": bool(hit),
            "reason": (
                f"命中 {hit}" if hit
                else f"回應中缺少任一關鍵詞 {any_kws!r}"
            ),
        })

    # expect_not_keywords
    for kw in expect.get("expect_not_keywords", []) or []:
        ok = kw not in response
        verdicts.append({
            "check": f"not_keyword:{kw}",
            "pass": ok,
            "reason": "" if ok else f"回應中不該出現 {kw!r}",
        })

    # expect_tools 在 run_one_scenario 內用 Langfuse trace 拉取後檢查（這層先標記）
    if expect.get("expect_tools"):
        verdicts.append({
            "check": "tools",
            "pass": None,  # 待 trace 拉取後填
            "expected_any_of": expect["expect_tools"],
            "reason": "需從 Langfuse trace 驗證",
        })

    passed = all(v.get("pass") is not False for v in verdicts)  # None（待驗）不算 fail
    return {"passed": passed, "verdicts": verdicts}


async def run_one_scenario(
    client: Any,
    scenario: dict,
    trace_collector: dict,
) -> dict:
    """跑單一 scenario（可能多輪）。"""
    sid = scenario["id"]
    language = scenario["language"]
    session_id = f"{sid}-{int(time.time())}"
    print(f"\n{'=' * 70}")
    print(f"Scenario: {sid}  [{scenario['category']}]  lang={language}")
    print(f"Session: {session_id}")
    print(f"{'=' * 70}")

    history: list[dict] = []
    turn_results = []

    for i, turn in enumerate(scenario["turns"], 1):
        query = turn["query"]
        history_text = build_history_text(history)
        print(f"\n--- Turn {i}/{len(scenario['turns'])} ---")
        print(f"Q: {query}")
        response, elapsed, err = await run_turn(
            client, query, language, history_text, session_id
        )
        if err:
            print(f"❌ 例外 ({elapsed:.1f}s):\n{err[:400]}")
            turn_results.append({
                "turn": i, "query": query, "response": None,
                "elapsed_s": round(elapsed, 2), "error": err,
                "evaluation": {"passed": False, "verdicts": [{"check": "exception", "pass": False}]},
            })
            break

        preview = (response[:200] + "...") if len(response) > 200 else response
        print(f"A ({elapsed:.1f}s): {preview}")

        evaluation = evaluate_turn(response, turn)
        for v in evaluation["verdicts"]:
            tag = "✅" if v.get("pass") else ("⏳" if v.get("pass") is None else "❌")
            reason = v.get("reason", "")
            print(f"  {tag} {v['check']}{f': {reason}' if reason else ''}")

        turn_results.append({
            "turn": i,
            "query": query,
            "response": response,
            "response_length": len(response),
            "elapsed_s": round(elapsed, 2),
            "error": None,
            "evaluation": evaluation,
        })
        history.append({"role": "user", "content": query})
        history.append({"role": "assistant", "content": response})
        await asyncio.sleep(2)  # 避免 NVIDIA rate limit

    # 記錄 session_id 供後續 trace 拉取
    trace_collector[sid] = session_id

    all_passed = all(t["evaluation"]["passed"] for t in turn_results)
    # soft 提升：scenario 層級 soft，或任一 turn 有 soft=True（覆蓋寫在 turn 層的情況）
    is_soft = scenario.get("soft", False) or any(
        turn.get("soft", False) for turn in scenario.get("turns", [])
    )
    return {
        "scenario_id": sid,
        "category": scenario["category"],
        "language": language,
        "session_id": session_id,
        "passed": all_passed,
        "soft": is_soft,  # soft=True 時 fail 算 warning 不算 fail
        "turns": turn_results,
    }


async def main_async(args):
    suite = load_suite()
    scenarios = suite["scenarios"]

    # 過濾
    if args.category:
        scenarios = [s for s in scenarios if s["category"] == args.category]
    if args.id:
        scenarios = [s for s in scenarios if s["id"] == args.id]

    print(f"Suite: {suite['suite_name']}")
    print(f"Scenarios to run: {len(scenarios)}")

    if args.dry_run:
        print("\n--dry-run：只 validate YAML，不跑 LLM")
        for s in scenarios:
            print(f"  ✓ {s['id']}: {len(s['turns'])} turn(s)")
        return

    # 啟用 Langfuse
    init_langfuse()

    # 建 LLM client — key 從環境變數讀，不寫死
    nvidia_key = os.getenv("NVIDIA_API_KEY") or os.getenv("SCENARIO_SUITE_API_KEY")
    if not nvidia_key:
        print(
            "❌ 缺少 LLM API key。請設定 NVIDIA_API_KEY 或 SCENARIO_SUITE_API_KEY"
        )
        sys.exit(2)
    provider = os.getenv("SCENARIO_SUITE_PROVIDER", "nvidia")
    model = os.getenv("SCENARIO_SUITE_MODEL", "nvidia/nemotron-3-super-120b-a12b")
    client = create_user_llm_client(
        provider=provider,
        api_key=nvidia_key,
        model=model,
    )

    trace_collector: dict = {}
    results = []
    for s in scenarios:
        try:
            r = await run_one_scenario(client, s, trace_collector)
            results.append(r)
        except Exception:
            tb = traceback.format_exc()
            print(f"❌ Scenario {s['id']} 例外:\n{tb}")
            results.append({
                "scenario_id": s["id"], "passed": False, "error": tb,
            })

    # summary — soft=True 的 scenario fail 算 warning 不算 fail
    total = len(results)
    hard_passed = sum(1 for r in results if r.get("passed"))
    soft_failed = sum(
        1 for r in results if not r.get("passed") and r.get("soft")
    )
    hard_failed = sum(
        1 for r in results
        if not r.get("passed") and not r.get("soft")
    )
    print(f"\n{'=' * 70}")
    print(
        f"SUMMARY: {hard_passed}/{total} passed"
        + (f", {soft_failed} soft warning(s)" if soft_failed else "")
    )
    print(f"{'=' * 70}")
    for r in results:
        if r.get("passed"):
            tag = "✅"
        elif r.get("soft"):
            tag = "⚠️ "  # soft fail
        else:
            tag = "❌"
        sid = r.get("scenario_id", "?")
        sess = r.get("session_id", "")
        suffix = " (soft)" if r.get("soft") and not r.get("passed") else ""
        print(f"  {tag} {sid}{suffix}  (session: {sess})")

    # 存結果
    timestamp = int(time.time())
    out_path = RESULTS_DIR / f"results_{timestamp}.json"
    payload = {
        "suite_name": suite["suite_name"],
        "timestamp": timestamp,
        "total": total,
        "passed": hard_passed,
        "soft_warnings": soft_failed,
        "hard_failed": hard_failed,
        "scenarios": results,
        "trace_sessions": trace_collector,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n結果已存: {out_path}")

    time.sleep(3)
    shutdown_langfuse()

    # exit code：只 hard fail 才 exit 1（soft fail 不擋）
    if hard_failed > 0:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="CryptoMind scenario suite runner")
    parser.add_argument("--category", help="只跑某類別")
    parser.add_argument("--id", help="只跑某 scenario id")
    parser.add_argument("--dry-run", action="store_true", help="只 validate YAML")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
