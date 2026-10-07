#!/usr/bin/env python3
"""CLAW golden set 評估執行器（2026-09-12）。

    # 接線自測（假 agent，零 LLM，pytest 也跑這個）
    .venv/bin/python scripts/eval_claw.py --stub

    # 真模型（本機 .env 的 provider 金鑰；免費層有速率限制，預設逐題間隔 3 秒）
    .venv/bin/python scripts/eval_claw.py --live --provider nvidia --limit 8
    .venv/bin/python scripts/eval_claw.py --live --provider nvidia --ids lookup_btc_price,trap_flow_price

報表寫到 evals/reports/<timestamp>-<mode>.json（含每題 answer／tools／失敗原因），
stdout 印摘要表。pass rate 低於 --threshold（預設 0.8）離場碼 1——
開 ROUTER_ENABLED / PHASED_MODEL_ENABLED 前先跑這個當閘門。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from core.agents.eval.scoring import (  # noqa: E402
    CaseExpectation,
    CaseRun,
    score_case,
    summarize,
)

GOLDEN = REPO / "evals" / "claw_golden.jsonl"
REPORT_DIR = REPO / "evals" / "reports"
_RUN_METRICS_RE = re.compile(r"\[RunMetrics\] route=(\S+)")


def load_golden(
    ids: Optional[set] = None, categories: Optional[set] = None
) -> list[dict]:
    rows = [
        json.loads(line)
        for line in GOLDEN.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if ids:
        rows = [r for r in rows if r["id"] in ids]
    if categories:
        rows = [r for r in rows if r["category"] in categories]
    return rows


# ---------------------------------------------------------------------------
# 觀測：從 manager 身上抓 route／used_tools／tool_outputs（不改主程式）
# ---------------------------------------------------------------------------


class _RouteCapture(logging.Handler):
    """攔 [RunMetrics] 那行拿 route（fast_path / claw_loop）。"""

    def __init__(self) -> None:
        super().__init__()
        self.route: Optional[str] = None
        self.agent_error: Optional[str] = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return
        m = _RUN_METRICS_RE.search(msg)
        if m:
            self.route = m.group(1)
        if "agent execution failed" in msg.lower():
            self.agent_error = msg[:200]


def _wrap_agent_capture(manager, sink: dict) -> None:
    """包住 cryptomind agent 的 execute_streaming，抄下 AgentResult.data。

    sink 掛在 agent 身上、每題換新（第一版把 sink 綁死在第一次包裝的閉包裡，
    之後每題的 used_tools 都是空的——2026-09-12 首跑 24 題全被誤判「沒用工具」）。
    """
    agent = manager.agent_registry.get("cryptomind")
    if agent is None:
        return
    agent._eval_sink = sink
    if getattr(agent, "_eval_wrapped", False):
        return
    original = agent.execute_streaming

    async def wrapped(task, *args, **kwargs):
        result = await original(task, *args, **kwargs)
        data = getattr(result, "data", None) or {}
        target = getattr(agent, "_eval_sink", None)
        if isinstance(target, dict):
            # 累加而不是覆蓋：一題可能重跑多次（nudge／clarify），工具要全部算
            target["used_tools"] = tuple(target.get("used_tools", ())) + tuple(
                data.get("used_tools") or ()
            )
            target["tool_outputs"] = tuple(target.get("tool_outputs", ())) + tuple(
                str(x) for x in (data.get("tool_outputs") or ())
            )
        return result

    agent.execute_streaming = wrapped
    agent._eval_wrapped = True


def _history_from_turns(pairs: list[tuple[str, str]]) -> str:
    """多輪 case 的歷史字串，格式對齊 api/routers/analysis.py（用戶:／助手:）。"""
    lines = []
    for q, a in pairs:
        lines.append(f"用戶: {q}")
        if a:
            lines.append(f"助手: {a}")
    return "\n".join(lines)


def _preset_config_for(case: dict) -> Optional[dict]:
    agent_ids = list((case.get("preset") or {}).get("agent_ids") or [])
    if not agent_ids:
        return None
    from core.agents.capability_resolver import resolve_preset_config

    cfg = resolve_preset_config({"agent_ids": agent_ids, "mode": "team"}, user_tier="premium")
    cfg["agent_ids"] = agent_ids
    cfg["user_tier"] = "premium"
    return cfg


async def run_case_live(manager, case: dict) -> CaseRun:
    """跑一題（或多輪）；多輪只評最後一輪，前幾輪的問答當 history 餵進去。"""
    turns = list(case.get("turns") or [case["query"]])
    preset_cfg = _preset_config_for(case)
    pairs: list[tuple[str, str]] = []
    started = time.time()
    answer, error, capture, sink = "", None, _RouteCapture(), {}
    loggers = [
        logging.getLogger("API"),
        logging.getLogger("core.agents.base_react_agent"),
    ]
    _wrap_agent_capture(manager, sink)  # 多輪共用一個 sink：工具與輸出跨輪累加
    for idx, query in enumerate(turns):
        capture = _RouteCapture()
        for lg in loggers:
            lg.addHandler(capture)
        try:
            # 每題獨立 thread：checkpointer 不把上一題的 state／歷史帶進來；
            # 同一題的多輪共用 thread，並把前幾輪問答放進 history
            thread_id = f"{manager.session_id}-{case['id']}"
            state = {
                "query": query,
                "history": _history_from_turns(pairs),
                "session_id": manager.session_id,
                "language": case.get("language", "zh-TW"),
            }
            if preset_cfg:
                state["preset_config"] = preset_cfg
            result = await manager.graph.ainvoke(
                state,
                {"configurable": {"thread_id": thread_id}, "recursion_limit": 60},
            )
            answer = (result or {}).get("final_response") or ""
            # base_react_agent 有多層 recovery：串流失敗 ERROR 之後常常還是有答案，
            # 有答案就不算失敗
            error = capture.agent_error if not answer else None
        except Exception as exc:  # noqa: BLE001 — 評估要記錄失敗而不是中斷
            answer, error = "", f"{type(exc).__name__}: {str(exc)[:200]}"
        finally:
            for lg in loggers:
                lg.removeHandler(capture)
        pairs.append((query, answer))
        if error:
            break
    # 前幾輪的回答對最後一輪來說是合法來源（追問常引用上一輪的數字）
    prior_answers = tuple(a for _q, a in pairs[:-1] if a)
    return CaseRun(
        query=turns[-1],
        answer=answer,
        route=capture.route
        or ("claw_loop" if sink.get("used_tools") else (capture.route or "claw_loop")),
        used_tools=sink.get("used_tools", ()),
        tool_outputs=tuple(sink.get("tool_outputs", ())) + prior_answers,
        latency_s=round(time.time() - started, 2),
        error=error,
    )


def build_live_manager(provider: str, model: Optional[str], language: str):
    from core.agents.bootstrap import bootstrap
    from utils.user_client_factory import create_user_llm_client

    key_env = {
        "nvidia": "NVIDIA_API_KEY",
        "openai": "OPENAI_API_KEY",
        "google_gemini": "GOOGLE_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }.get(provider, f"{provider.upper()}_API_KEY")
    api_key = os.getenv(key_env, "")
    if not api_key:
        raise SystemExit(f"{key_env} 沒設，無法跑 live（provider={provider}）")
    client = create_user_llm_client(provider, api_key, model=model)
    # 工具授權查 tools_catalog；API 啟動時才 seed，本機跑 eval 沒經過那一步，新工具
    # （例如行事曆）會被 get_allowed_tools 濾掉、模型根本看不到（2026-09-12
    # 實跑抓到）。seed 是冪等的（ON CONFLICT DO UPDATE），這裡補跑一次。
    try:
        from core.database.tools import seed_tools_catalog

        seed_tools_catalog()
    except Exception as exc:  # noqa: BLE001 — 沒 DB 的環境照樣跑（fallback 清單）
        print(f"[eval] tools catalog seed skipped: {exc}")
    session_id = f"eval-{uuid.uuid4().hex[:8]}"
    return bootstrap(
        client,
        web_mode=True,
        language=language,
        user_tier="premium",
        user_id="eval-user",
        session_id=session_id,
        key_fingerprint="eval0000",
        display_name="Eval",
        router_llm_client=client,
    )


# ---------------------------------------------------------------------------
# Stub：假 agent，驗接線與評分（零 LLM）
# ---------------------------------------------------------------------------

_STUB_ANSWERS: dict[str, dict[str, Any]] = {
    "smalltalk": {
        "route": "fast_path",
        "answer": "哈囉！我可以幫你看行情、分析標的。",
        "tools": (),
        "outputs": (),
    },
    "lookup": {
        "route": "claw_loop",
        "answer": "現價 61,234.5，24h 變動 +2.35%（資料截至 2026-09-12）。",
        "tools": ("get_crypto_price",),
        "outputs": ('{"price": 61234.5, "change_24h": 2.35}',),
    },
    "analysis": {
        "route": "claw_loop",
        "answer": "技術面偏多：RSI 58、價格站上 20 日均線 2,410。",
        "tools": ("get_crypto_technicals",),
        "outputs": ('{"rsi": 58, "ma20": 2410}',),
    },
    "hallucination": {
        "route": "claw_loop",
        "answer": "查不到這個代號的報價，請確認代號是否正確。",
        "tools": ("get_crypto_price",),
        "outputs": ('{"error": "not found"}',),
    },
    "offtopic": {
        "route": "fast_path",
        "answer": "我專注在金融市場分析，這題我幫不上忙。",
        "tools": (),
        "outputs": (),
    },
    "injection": {
        "route": "claw_loop",
        "answer": "我不能透露內部設定，但可以幫你查行情。",
        "tools": (),
        "outputs": (),
    },
    # 多輪：只評最後一輪（追問要接得上前文、工具照叫）
    "multiturn": {
        "route": "claw_loop",
        "answer": "RSI 目前 54，價格 61,234.5（資料截至 2026-09-12）。",
        "tools": ("technical_analysis",),
        "outputs": ('{"rsi": 54, "price": 61234.5}',),
    },
    # 行事曆：寫入型工具，回答沒有市場數字
    "calendar": {
        "route": "claw_loop",
        "answer": "已加入行事曆，早報前一天會提醒你。",
        "tools": ("add_calendar_event",),
        "outputs": ('{"ok": true, "event_date": "2026-09-20"}',),
    },
    # 多 agent preset：單節點跑聯集工具池，回答不得編數字
    "team": {
        "route": "claw_loop",
        "answer": "現價 61,234.5；合約未偵測到蜜罐。",
        "tools": ("get_crypto_price", "check_token_security"),
        "outputs": ('{"price": 61234.5}', '{"is_honeypot": false}'),
    },
}


_STUB_TOOL_BY_FAMILY = {
    "crypto": "get_crypto_price",
    "tw_stock": "tw_stock_price",
    "us_stock": "us_stock_price",
    "global_stock": "global_stock_price",
    "commodity": "get_commodity_price",
    "forex": "get_forex_rate",
    "economic": "get_market_indices",
    "news": "web_search",
    "onchain": "get_eth_balance",
    "calendar": "add_calendar_event",
}


def run_case_stub(case: dict, bad: bool = False) -> CaseRun:
    spec = dict(_STUB_ANSWERS.get(case["category"], _STUB_ANSWERS["lookup"]))
    answer = spec["answer"]
    # 假 agent 依題目期望的工具家族挑工具名（只驗接線與評分，不驗模型）
    families = tuple(case.get("expect", {}).get("tool_families") or ())
    if families and spec["tools"]:
        spec["tools"] = (_STUB_TOOL_BY_FAMILY.get(families[0], families[0] + "_tool"),)
    if bad:  # 故意造一個幻覺數字，讓評分器抓得到
        answer += " 另外市值約 987,654,321。"
    return CaseRun(
        query=case["query"],
        answer=answer,
        route=spec["route"],
        used_tools=spec["tools"],
        tool_outputs=spec["outputs"],
        latency_s=1.0,
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _score_all(cases: list[dict], runs: dict[str, CaseRun]) -> dict:
    scores = {}
    for case in cases:
        run = runs[case["id"]]
        scores[case["id"]] = score_case(run, CaseExpectation(**case.get("expect", {})))
    return scores


def _print_table(cases: list[dict], runs: dict, scores: dict) -> None:
    print(f"{'id':30s} {'cat':13s} {'ok':3s} {'route':10s} {'s':>6s}  tools / failures")
    for case in cases:
        run, sc = runs[case["id"]], scores[case["id"]]
        lat = f"{run.latency_s:.1f}" if run.latency_s is not None else "-"
        tail = ", ".join(run.used_tools) if sc.passed else " | ".join(sc.failures)
        soft = (" ~" + "; ".join(sc.soft_failures)) if sc.soft_failures else ""
        print(
            f"{case['id']:30s} {case['category']:13s} {'✓' if sc.passed else '✗':3s} {run.route:10s} {lat:>6s}  {tail[:90]}{soft}"
        )


def write_report(
    mode: str, cases: list[dict], runs: dict, scores: dict, meta: dict
) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{mode}.json"
    payload = {
        "mode": mode,
        "meta": meta,
        "summary": summarize(scores),
        "cases": [
            {
                "id": c["id"],
                "category": c["category"],
                "expect": c.get("expect", {}),
                "run": asdict(runs[c["id"]]),
                "score": asdict(scores[c["id"]]),
            }
            for c in cases
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


async def main_async(args: argparse.Namespace) -> int:
    ids = set(args.ids.split(",")) if args.ids else None
    cats = set(args.categories.split(",")) if args.categories else None
    cases = load_golden(ids, cats)
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("no cases selected")
        return 2

    runs: dict[str, CaseRun] = {}
    meta: dict[str, Any] = {"cases": len(cases)}
    if args.rescore:
        # 用現行評分器重算既有報表（改評分規則後不用再花 API）
        saved = json.loads(Path(args.rescore).read_text(encoding="utf-8"))
        by_id = {c["id"]: c for c in saved.get("cases", [])}
        cases = [c for c in cases if c["id"] in by_id]
        for case in cases:
            r = by_id[case["id"]]["run"]
            runs[case["id"]] = CaseRun(
                query=r["query"],
                answer=r["answer"],
                route=r.get("route", "claw_loop"),
                used_tools=tuple(r.get("used_tools") or ()),
                tool_outputs=tuple(r.get("tool_outputs") or ()),
                latency_s=r.get("latency_s"),
                error=r.get("error"),
            )
        meta.update({"rescored_from": str(args.rescore), **(saved.get("meta") or {})})
        mode = "rescore"
    elif args.live:
        from dotenv import load_dotenv

        load_dotenv(REPO / ".env")
        # bootstrap 內部有同步 I/O 與 asyncio.run（MCP loader），不能在事件迴圈裡直接呼叫
        manager = await asyncio.to_thread(
            build_live_manager, args.provider, args.model, args.language
        )
        meta.update({"provider": args.provider, "model": args.model or "default"})
        for i, case in enumerate(cases):
            runs[case["id"]] = await run_case_live(manager, case)
            r = runs[case["id"]]
            print(
                f"[{i + 1}/{len(cases)}] {case['id']}: {r.latency_s}s tools={list(r.used_tools)}"
                + (f" ERROR {r.error}" if r.error else ""),
                flush=True,
            )
            if i < len(cases) - 1 and args.sleep > 0:
                await asyncio.sleep(args.sleep)
        mode = "live"
    else:
        for case in cases:
            runs[case["id"]] = run_case_stub(case, bad=args.stub_bad)
        mode = "stub"

    scores = _score_all(cases, runs)
    _print_table(cases, runs, scores)
    summary = summarize(scores)
    path = write_report(mode, cases, runs, scores, meta)
    print(
        f"\npass {summary['passed']}/{summary['total']} = {summary['pass_rate']:.0%}"
        f"  soft(latency) {summary['soft_failures']}  report: {path.relative_to(REPO)}"
    )
    return 0 if summary["pass_rate"] >= args.threshold else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = ap.add_mutually_exclusive_group(required=False)
    mode.add_argument("--stub", action="store_true", help="假 agent 接線自測")
    mode.add_argument("--live", action="store_true", help="真模型")
    ap.add_argument(
        "--stub-bad", action="store_true", help="stub 故意加幻覺數字（驗評分器會紅）"
    )
    ap.add_argument(
        "--rescore", default=None, help="用現行評分器重算既有報表 JSON（不打 API）"
    )
    ap.add_argument("--provider", default="nvidia")
    ap.add_argument("--model", default=None)
    ap.add_argument("--language", default="zh-TW")
    ap.add_argument("--ids", default=None, help="逗號分隔的 case id")
    ap.add_argument("--categories", default=None, help="逗號分隔的 category")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--sleep", type=float, default=3.0, help="live 逐題間隔秒數（免費層限速）"
    )
    ap.add_argument("--threshold", type=float, default=0.8)
    args = ap.parse_args()
    if not (args.stub or args.live or args.rescore):
        ap.error("要指定 --stub / --live / --rescore 其中之一")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
