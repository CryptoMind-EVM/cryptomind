#!/usr/bin/env python
"""Router / T0+T1 路由 eval harness（Model Mixer Step 3 前置：先寫路由 eval）.

設計：docs/plans/2026-09-03-model-mixer-step3-router.md §6
用法（在本機 .venv 或 CI 的 python 下）：

  # 1) 純規則層報告（零 LLM）：T0 快取與金融訊號否決各自吃掉多少、
  #    多少要付 LLM——詞表時代的結構性成績單
  python scripts/eval_router.py --mode rules

  # 2) Router 回放（CI／無 key）：以 canned replies 驗證 harness 與解析邏輯
  python scripts/eval_router.py --mode router --replay tests/eval/router_replay.jsonl

  # 3) 真模型（BYOK：key 只從環境變數讀，URL 限 https 且拒絕私有位址）
  python scripts/eval_router.py --mode router --provider openrouter \
      --model vendor/model --base-url https://openrouter.ai/api/v1 \
      --api-key-env OPENROUTER_API_KEY --out artifacts/eval/router-$(date +%Y%m%d).md

  # 4) T1 baseline（#617 分類器）真模型評測——與 Router 同集對比
  python scripts/eval_router.py --mode t1 --provider ...（同上參數）

評分指標：
  depth / domains / gate 精確率；**危險錯誤單獨列**——expected 有資料需求
  （domains 非空或 depth≥analysis）卻被判成 lookup+[]（＝金融內容被送上
  無工具小模型的路徑）。per-source 分桶：t0_cache／veto 桶由構造應為
  100%，router 桶才是模型真實成績。
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.agents.router import route_query  # noqa: E402
from core.agents.triage import (  # noqa: E402
    DEEP_ANALYSIS,
    SIMPLE_QA,
    classify_query,
    has_finance_signal,
    is_smalltalk,
)

DATASET_DEFAULT = ROOT / "tests/eval/router_dataset.jsonl"
VALID_DEPTHS = ("lookup", "analysis", "research")
VALID_DOMAINS = {
    "finance_markets",
    "general_research",
    "onchain_security",
    "people_projects",
}


# ── 資料載入 ─────────────────────────────────────────────────────────────────

def load_dataset(path: Path) -> list:
    entries = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            assert set(e) >= {"id", "query", "language", "expected"}
            exp = e["expected"]
            assert exp["depth"] in VALID_DEPTHS, exp["depth"]
            assert set(exp["domains"]) <= VALID_DOMAINS, exp["domains"]
            assert exp["gate"] in (None, "risk_first")
            entries.append(e)
        except (ValueError, AssertionError, KeyError) as exc:
            raise SystemExit(f"{path}:{lineno}: invalid entry: {exc}") from exc
    return entries


def load_replay(path: Path) -> dict:
    """{id: reply}——router 模式 reply 是 JSON 字串；t1 模式是單字。"""
    replay = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        replay[row["id"]] = row["reply"]
    return replay


# ── LLM 注入 ─────────────────────────────────────────────────────────────────

def validate_base_url(base_url: str) -> str:
    """僅允許 https 且拒絕 localhost／環回／私有／保留位址。"""
    parsed = urlparse(base_url)
    if parsed.scheme != "https":
        raise SystemExit(f"base-url 必須是 https：{base_url}")
    host = parsed.hostname or ""
    if host in ("localhost", "0.0.0.0", "::1"):
        raise SystemExit("拒絕 localhost 位址")
    try:
        addr = ipaddress.ip_address(socket.gethostbyname(host))
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
        ):
            raise SystemExit(f"拒絕私有／保留位址：{host}")
    except socket.gaierror as exc:
        raise SystemExit(f"無法解析 host：{host} ({exc})") from exc
    return base_url.rstrip("/")


def build_live_invoke(
    base_url: str,
    model: str,
    api_key: str,
    http_timeout: float = 15.0,
    max_tokens: int = 120,
    no_thinking: bool = False,
):
    """真模型呼叫端。

    ``max_tokens`` / ``http_timeout`` / ``no_thinking`` 可調的理由：reasoning
    模型（deepseek-v4、r1 系）會先吐一段思考才給答案。用 120 tokens 的預設
    去評測它們，量到的不是路由準確率而是「JSON 被截斷」——解析失敗一律
    fail-closed 成 research，報表會長得像「模型很保守」。要嘛把額度放大，
    要嘛用 ``--no-thinking`` 請供應商關掉思考（NVIDIA NIM／部分 OpenAI 相容
    端點支援 ``chat_template_kwargs.thinking``；不支援的端點會忽略此欄）。
    """
    import httpx

    # 呼叫失敗計數。route_query 對任何例外都 fail-closed 成 research，所以
    # 「端點整個掛掉」與「模型很保守」在報表上長得一模一樣——2026-09-04 實測
    # 踩到：上游 no_available_workers，73 題全 500，報表卻印出 51.1% depth
    # 準確率像是一份有效成績。失敗必須單獨計數並印在報表最上面。
    stats = {"calls": 0, "failures": 0, "last_error": ""}

    async def invoke(prompt: str) -> str:
        # Keep the event loop cancellable: route_query/classify_query own the
        # decision budget, including cancellation of an outstanding HTTP request.
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        # max_tokens=0 ＝**不送這個欄位**，對齊 production：
        # core/agents/manager/llm.py 的 _create_model_instance 只設
        # {"model", "temperature"}，從不限制輸出長度。
        # 2026-09-05 實測：harness 原本一律送 max_tokens，於是 reasoning 模型
        # 的思考吃掉額度、JSON 被截斷 → 32 題判成 no_json。那是**測試設定造成
        # 的假失敗**，production 不會發生。harness 必須能重現 production，
        # 否則量到的是自己。
        if max_tokens > 0:
            payload["max_tokens"] = max_tokens
        if no_thinking:
            # 兩種旗標一起送：端點只認得其中一個時另一個會被忽略。
            # 2026-09-06 實測 DeepSeek：chat_template_kwargs 沒有真的關掉
            #（仍產 26 個 reasoning token），reasoning_effort=none 才歸零。
            payload["chat_template_kwargs"] = {"thinking": False}
            payload["reasoning_effort"] = "none"
        async with httpx.AsyncClient(timeout=http_timeout) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
        stats["calls"] += 1
        try:
            resp.raise_for_status()
        except Exception as exc:
            stats["failures"] += 1
            stats["last_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            raise
        return resp.json()["choices"][0]["message"]["content"]

    invoke.stats = stats
    return invoke


def build_replay_invoke(replay: dict, entry_id: str):
    def invoke(_prompt: str) -> str:
        return replay[entry_id]

    return invoke


def make_async(sync_invoke):
    async def wrapper(prompt: str) -> str:
        return sync_invoke(prompt)

    return wrapper


# ── 評分 ─────────────────────────────────────────────────────────────────────

def is_dangerous(expected: dict, decision) -> bool:
    """金融內容被送上無工具小模型路徑（lookup＋空 domains）。"""
    data_needed = bool(expected["domains"]) or expected["depth"] in (
        "analysis",
        "research",
    )
    return data_needed and decision.is_fast_path


def score_router(entries: list, decisions: dict) -> dict:
    per_source = {}
    total = {"n": 0, "depth_ok": 0, "domains_ok": 0, "gate_ok": 0, "dangerous": 0}
    misses = []
    for e in entries:
        d = decisions[e["id"]]
        exp = e["expected"]
        bucket = per_source.setdefault(
            d.source, {"n": 0, "depth_ok": 0, "domains_ok": 0, "gate_ok": 0, "dangerous": 0}
        )
        depth_ok = d.depth == exp["depth"]
        domains_ok = tuple(sorted(exp["domains"])) == d.domains
        gate_ok = d.gate == exp["gate"]
        dangerous = is_dangerous(exp, d)
        for acc in (total, bucket):
            acc["n"] += 1
            acc["depth_ok"] += depth_ok
            acc["domains_ok"] += domains_ok
            acc["gate_ok"] += gate_ok
            acc["dangerous"] += dangerous
        if not depth_ok or dangerous:
            misses.append(
                f'  {e["id"]}: expected {exp["depth"]}/{exp["domains"] or "-"}/'
                f'{exp["gate"] or "-"} got {d.depth}/{list(d.domains) or "-"}/'
                f'{d.gate or "-"} ({d.source}) :: {e["query"][:40]}'
            )
    return {"total": total, "per_source": per_source, "misses": misses}


def t1_expected_depth(entry: dict) -> str:
    """T1 baseline 的推導期望：simple_qa ≈ lookup＋無 domain（無資料需求）。"""
    exp = entry["expected"]
    if exp["depth"] == "lookup" and not exp["domains"]:
        return SIMPLE_QA
    return DEEP_ANALYSIS


def score_t1(entries: list, replies: dict) -> dict:
    total = {"n": 0, "ok": 0, "dangerous": 0}
    misses = []
    for e in entries:
        got = replies[e["id"]]
        want = t1_expected_depth(e)
        ok = got == want
        dangerous = got == SIMPLE_QA and want == DEEP_ANALYSIS
        total["n"] += 1
        total["ok"] += ok
        total["dangerous"] += dangerous
        if not ok:
            misses.append(f'  {e["id"]}: expected {want} got {got} :: {e["query"][:40]}')
    return {"total": total, "misses": misses}


def rules_report(entries: list) -> dict:
    counts = {"t0_cache": 0, "veto": 0, "needs_llm": 0}
    by_lang = {}
    for e in entries:
        if is_smalltalk(e["query"]):
            key = "t0_cache"
        elif has_finance_signal(e["query"]):
            key = "veto"
        else:
            key = "needs_llm"
        counts[key] += 1
        lang = by_lang.setdefault(e["language"], {"t0_cache": 0, "veto": 0, "needs_llm": 0})
        lang[key] += 1
    return {"counts": counts, "by_language": by_lang, "n": len(entries)}


# ── 報表 ─────────────────────────────────────────────────────────────────────

def _pct(ok: int, n: int) -> str:
    return f"{(100.0 * ok / n):5.1f}%" if n else "  n/a"


def render_router_report(
    mode_label: str, scored: dict, elapsed: float, call_stats: dict = None
) -> str:
    lines = [f"# Router eval — {mode_label}", ""]
    # 呼叫失敗置頂：失敗一律 fail-closed 成 research，不標出來的話整份
    # 失敗的跑會印出看似正常的準確率（2026-09-04 實測踩過）。
    if call_stats and call_stats.get("failures"):
        lines.append(
            f"> ⚠️ **本次結果無效**：{call_stats['failures']}/{call_stats['calls']} "
            f"次模型呼叫失敗，全部 fail-closed 成 research。"
            f"最後一個錯誤：`{call_stats.get('last_error', '')}`"
        )
        lines.append("")
    t = scored["total"]
    lines.append(
        f"- entries: {t['n']}  depth: {_pct(t['depth_ok'], t['n'])}  "
        f"domains: {_pct(t['domains_ok'], t['n'])}  gate: {_pct(t['gate_ok'], t['n'])}"
    )
    lines.append(
        f"- **dangerous (data→no-tool path): {t['dangerous']}**  "
        f"elapsed: {elapsed:.1f}s"
    )
    lines.append("")
    lines.append("| source | n | depth | domains | gate | dangerous |")
    lines.append("|---|---|---|---|---|---|")
    for source in sorted(scored["per_source"]):
        b = scored["per_source"][source]
        lines.append(
            f"| {source} | {b['n']} | {_pct(b['depth_ok'], b['n'])} | "
            f"{_pct(b['domains_ok'], b['n'])} | {_pct(b['gate_ok'], b['n'])} | "
            f"{b['dangerous']} |"
        )
    if scored["misses"]:
        lines.append("")
        lines.append("## Misses (depth 不中或危險錯誤)")
        lines.extend(scored["misses"])
    return "\n".join(lines) + "\n"


# ── 執行模式 ─────────────────────────────────────────────────────────────────

async def run_router(
    entries: list, invoke_for, timeout_s=None, delay_s: float = 0.0
) -> tuple:
    """``timeout_s=None`` ＝吃 route_query 的預設（ROUTER_TIMEOUT_SECONDS）。

    量準確率時通常要放大：用 4 秒的正式預算去評測較慢的模型，量到的是
    逾時率不是路由品質（逾時一律 fail-closed 成 research）。延遲是另一個
    獨立指標，報表的 elapsed 已經在記。
    """
    decisions = {}
    started = time.monotonic()
    kwargs = {} if timeout_s is None else {"timeout_s": timeout_s}
    for i, e in enumerate(entries):
        # delay_s：供應商限流時，這是唯一能拿到有效報表的辦法。2026-09-04 實測
        # AMD Radeon 端點在 ~0.75 req/s 下第 8 題就 429，56/64 次失敗、報表作廢。
        # 只影響 wall clock，不影響準確率。
        if delay_s > 0 and i:
            await asyncio.sleep(delay_s)
        decisions[e["id"]] = await route_query(e["query"], invoke_for(e), **kwargs)
    return decisions, time.monotonic() - started


async def run_t1(entries: list, invoke_for) -> tuple:
    replies = {}
    started = time.monotonic()
    for e in entries:
        # Match #617 runtime order: T0 precedes T1's ticker/finance veto.
        replies[e["id"]] = (
            SIMPLE_QA if is_smalltalk(e["query"])
            else await classify_query(e["query"], invoke_for(e))
        )
    return replies, time.monotonic() - started


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["rules", "router", "t1"], required=True)
    parser.add_argument("--dataset", type=Path, default=DATASET_DEFAULT)
    parser.add_argument("--replay", type=Path, help="{id, reply} jsonl（router/t1 回放）")
    parser.add_argument("--provider", help="live 模式：provider 名（僅供報表標示）")
    parser.add_argument("--model", help="live 模式：模型名")
    parser.add_argument("--base-url", help="live 模式：https OpenAI 相容端點")
    parser.add_argument(
        "--api-key-env", default="ROUTER_EVAL_API_KEY", help="live 模式：key 的環境變數名"
    )
    parser.add_argument("--out", type=Path, help="報表輸出檔（預設 stdout）")
    # 以下三個是給「不是中階 mini」的模型用的。預設值對應 router 的
    # 4 秒級距；reasoning 模型請放大額度或關掉思考，否則量到的是截斷不是準確率。
    parser.add_argument(
        "--max-tokens", type=int, default=120,
        help="live 模式：回覆 token 上限。**0＝不送此欄位（對齊 production，建議值）**；預設 120 是歷史值，會讓 reasoning 模型的 JSON 被截斷",
    )
    parser.add_argument(
        "--http-timeout", type=float, default=15.0,
        help="live 模式：單次 HTTP 逾時秒數（預設 15）",
    )
    parser.add_argument(
        "--delay", type=float, default=0.0,
        help="live 模式：每題之間的間隔秒數（供應商限流時用；只影響耗時不影響準確率）",
    )
    parser.add_argument(
        "--router-timeout", type=float, default=None,
        help="覆寫 route_query 的決策預算（預設吃 ROUTER_TIMEOUT_SECONDS）",
    )
    parser.add_argument(
        "--no-thinking", action="store_true",
        help="live 模式：請供應商關閉思考（同時送 reasoning_effort=none 與 "
             "chat_template_kwargs.thinking=False；DeepSeek 只認前者）",
    )
    args = parser.parse_args(argv)

    entries = load_dataset(args.dataset)
    call_stats = None

    if args.mode == "rules":
        report = json.dumps(rules_report(entries), ensure_ascii=False, indent=2)
        print("# Router eval — rules layer (zero LLM)\n\n" + report)
        return 0

    if args.replay:
        replay = load_replay(args.replay)
        missing = [e["id"] for e in entries if e["id"] not in replay]
        if missing:
            raise SystemExit(f"replay 缺 {len(missing)} 筆：{missing[:5]} ...")
        invoke_for = lambda e: make_async(build_replay_invoke(replay, e["id"]))  # noqa: E731
        label = f"replay ({args.replay.name})"
    else:
        if not (args.base_url and args.model):
            raise SystemExit("live 模式需要 --base-url 與 --model")
        api_key = os.getenv(args.api_key_env, "")
        if not api_key:
            raise SystemExit(f"{args.api_key_env} 未設——key 只從環境變數讀")
        base = validate_base_url(args.base_url)
        live_invoke = build_live_invoke(
            base,
            args.model,
            api_key,
            http_timeout=args.http_timeout,
            max_tokens=args.max_tokens,
            no_thinking=args.no_thinking,
        )
        invoke_for = lambda e: live_invoke  # noqa: E731
        call_stats = getattr(live_invoke, "stats", None)
        label = f"live {args.provider or ''}/{args.model}".strip()

    if args.mode == "router":
        decisions, elapsed = asyncio.run(
            run_router(entries, invoke_for, timeout_s=args.router_timeout, delay_s=args.delay)
        )
        scored = score_router(entries, decisions)
        report = render_router_report(label, scored, elapsed, call_stats)
    else:
        replies, elapsed = asyncio.run(run_t1(entries, invoke_for))
        scored = score_t1(entries, replies)
        t = scored["total"]
        report = (
            f"# T1 baseline eval — {label}\n\n"
            f"- entries: {t['n']}  accuracy: {_pct(t['ok'], t['n'])}  "
            f"**dangerous (deep→simple): {t['dangerous']}**  elapsed: {elapsed:.1f}s\n"
        )
        if scored["misses"]:
            report += "\n## Misses\n" + "\n".join(scored["misses"]) + "\n"

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"report → {args.out}")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
