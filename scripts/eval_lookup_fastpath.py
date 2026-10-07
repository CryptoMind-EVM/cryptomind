"""查價快速通道的離線評測（用真的本機 llama-server 把關模型）。

用法（本機）：
    LOCAL_LLAMA_BASE_URL=http://127.0.0.1:8080/v1 python scripts/eval_lookup_fastpath.py
    python scripts/eval_lookup_fastpath.py --rules-only          # 只驗規則抽取（不連模型，CI 友善）

讀 evals/lookup_fastpath_cases.jsonl：``plain=true`` 是單純報價（規則要抽到指定工具、把關要放行）；
``plain=false`` 是「看起來像報價、其實不是」的難例（例如「現在多少員工」）——規則可以抽到候選，
但把關的 P(yes) 必須低於門檻。印出兩組的 P(yes) 分布，用來校準 LOOKUP_FASTPATH_MIN_CONFIDENCE。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from core.agents import lookup_fastpath as lf  # noqa: E402

CASES = REPO / "evals" / "lookup_fastpath_cases.jsonl"


def load_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in CASES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def main(rules_only: bool) -> int:
    base = os.getenv("LOCAL_LLAMA_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/")
    threshold = lf.min_confidence()
    rows = []
    rule_bad = []
    for case in load_cases():
        cand = lf.extract_candidate(case["q"])
        if case["plain"]:
            if cand is None or cand.tool != case["tool"]:
                rule_bad.append((case["q"], case["tool"], cand.tool if cand else None))
        p = None
        if cand is not None and not rules_only:
            p = await lf.verify_with_local_llama(base, "x", case["q"], cand)
        rows.append((case, cand, p))

    print(
        f"規則抽取：{sum(1 for c, _, _ in rows if c['plain']) - len(rule_bad)}/{sum(1 for c, _, _ in rows if c['plain'])} 個單純報價抽對工具"
    )
    for q, want, got in rule_bad:
        print(f"  ✗ {q!r}: 期望 {want}，實際 {got}")
    hard = [(c, cand, p) for c, cand, p in rows if not c["plain"]]
    leaked = [c["q"] for c, cand, _ in hard if cand is not None]
    print(
        f"難例：{len(hard)} 題，其中 {len(leaked)} 題規則也抽得到候選（要靠把關擋下）"
    )
    if rules_only:
        return 1 if rule_bad else 0

    pos = [p for c, cand, p in rows if c["plain"] and p is not None]
    neg = [p for c, cand, p in rows if not c["plain"] and p is not None]
    print(f"\n門檻 {threshold}")
    if pos:
        print(
            f"單純報價 P(yes)：min={min(pos):.3f} p10={sorted(pos)[len(pos) // 10]:.3f} median={statistics.median(pos):.3f}  放行 {sum(1 for p in pos if p >= threshold)}/{len(pos)}"
        )
    if neg:
        print(
            f"難例     P(yes)：max={max(neg):.3f} median={statistics.median(neg):.3f}  誤放行 {sum(1 for p in neg if p >= threshold)}/{len(neg)}"
        )
    for c, cand, p in rows:
        if c["plain"] and (p is None or p < threshold):
            print(f"  單純報價被擋：{c['q']!r} P={p}")
    for c, cand, p in rows:
        if not c["plain"] and p is not None and p >= threshold:
            print(f"  ⚠ 難例誤放行：{c['q']!r} P={p:.3f}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules-only", action="store_true")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.rules_only)))
