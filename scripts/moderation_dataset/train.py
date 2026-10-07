"""微調 Laya 判斷論壇貼文「該不該擋」（過＝0／不過＝1），練完直接匯出線上用的 ONNX（2026-10-01）。

改寫自 chaceli/laya-scam-detector 的 scripts/train_local_lora.py、scripts/export_local_onnx.py
（Apache-2.0），差異：
- 只練一題 noul「該不該擋」，不練詐騙類別（DANNY：只要過／不過）
- 起點是官方 convaiinnovations/laya 的 multilingual（釘版本），跟線上那顆同架構，換上去資源不用改
- LoRA 掛在編碼器的 Wqkv／Wo，決策頭（head、scorer）全訓
- 訓練用 Laya 原生的 build_sequence 格式，並把實際的題目 token 存進 readout.json，線上照抄：
  原作訓練用「noul question: …」、推論卻用「choice question: …」，格式不一致，這裡從設計上消掉
- 每輪在 val 上挑門檻（過被誤擋 ≤ --max-false-block），留最好的一輪；最後考 test 並對照達標線
- 匯出 ONNX（fp16，跟線上一樣是 model.onnx＋model.onnx.data）＋ tokenizer ＋ readout.json，
  並用線上的方式（tokenizers＋onnxruntime）重算一次，確認跟 PyTorch 一致

用法：
  Kaggle（GPU T4、Internet 開）：把 cryptomind-moderation-v1.zip 上傳成 dataset（裡面附這支腳本）
    !pip uninstall -y -q torchao   # Kaggle 預裝 0.10 太舊，新版 peft 偵測到會直接報錯
    !pip install -q laya peft onnx onnxruntime
    !python /kaggle/input/<dataset>/train.py --data /kaggle/input/<dataset> --out /kaggle/working/laya-moderation
  本機（Apple Silicon 會用 MPS）：
    python scripts/moderation_dataset/train.py --data data/moderation_dataset --out data/laya-moderation
  先確認整條流程跑得通：加 --smoke（各取少量、1 輪，幾分鐘跑完）
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

BASE_REPO = "convaiinnovations/laya"
BASE_SUBFOLDER = "multilingual"
BASE_REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"  # laya.PINNED_REVISIONS，2026-10-01
QUESTION = {
    "t": "noul",
    "ins": "Should this forum post be blocked for sexual content, gore, a scam or fraud, or other illegal activity?",
    "crit": None,
}
QTYPE_NOUL = 2

# 達標線（拔掉 rules.py 寫死規則的條件，見 PR #969）
TARGET_BLOCK = 0.90
TARGET_SOURCE_BLOCK = 0.80
TARGET_FALSE_BLOCK = 0.02
TARGET_HELDOUT_FALSE_BLOCKS = 3
# v5：涉及未成年的性內容要比整體更嚴（v4 只擋 60%）；提到兒少的正常內容不能因此被擋
TARGET_MINOR_BLOCK = 0.90
TARGET_MINOR_TOPIC_FALSE_BLOCK = 0.03


def load_rows(path: Path, limit: int | None, seed: int):
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    if limit:
        random.Random(seed).shuffle(rows)
        rows = rows[:limit]
    return rows


def encode(tok, build_sequence, texts, max_len, head_max_len):
    return [build_sequence(tok, t, QUESTION, max_len=max_len, head_max_len=head_max_len) for t in texts]


def collate(torch, encoded, idx, device, pad_id):
    items = [encoded[i] for i in idx]
    width = max(len(ids) for ids, _ in items)
    n_opts = max(len(m) for _, m in items)
    input_ids, attn, marker_pos, marker_mask = [], [], [], []
    for ids, markers in items:
        pad = width - len(ids)
        input_ids.append(ids + [pad_id] * pad)
        attn.append([1] * len(ids) + [0] * pad)
        marker_pos.append(list(markers) + [0] * (n_opts - len(markers)))
        marker_mask.append([True] * len(markers) + [False] * (n_opts - len(markers)))
    return {
        "input_ids": torch.tensor(input_ids, device=device),
        "attention_mask": torch.tensor(attn, device=device),
        "marker_pos": torch.tensor(marker_pos, device=device),
        "marker_mask": torch.tensor(marker_mask, device=device),
        "qtype": torch.full((len(items),), QTYPE_NOUL, dtype=torch.long, device=device),
    }


def predict(torch, model, encoded, device, pad_id, batch_size, amp):
    """每篇的 P(不過)"""
    model.eval()
    order = sorted(range(len(encoded)), key=lambda i: len(encoded[i][0]))
    probs = [0.0] * len(encoded)
    with torch.no_grad():
        for s in range(0, len(order), batch_size):
            idx = order[s : s + batch_size]
            with torch.autocast("cuda", dtype=torch.float16, enabled=amp):
                logits, _ = model(**collate(torch, encoded, idx, device, pad_id))
            p = torch.softmax(logits.float(), dim=-1)[:, 1].tolist()
            for i, v in zip(idx, p):
                probs[i] = v
    model.train()
    if device == "mps":
        torch.mps.empty_cache()
    return probs


def pick_threshold(probs, labels, max_false_block):
    """在「過被誤擋 ≤ max_false_block」的條件下擋最多的門檻"""
    neg = sorted((p for p, y in zip(probs, labels) if y == 0), reverse=True)
    allowed = int(math.floor(max_false_block * len(neg)))
    if not neg:
        return 0.5
    # 比第 allowed+1 高的正常文才會被擋 → 門檻取在它上面一點
    return min(0.999999, neg[allowed] + 1e-6) if allowed < len(neg) else 0.0


def rates(probs, rows, threshold):
    harm = [p >= threshold for p, r in zip(probs, rows) if r["label"] == 1]
    ok = [p >= threshold for p, r in zip(probs, rows) if r["label"] == 0]
    return (sum(harm) / max(1, len(harm)), sum(ok) / max(1, len(ok)), sum(ok))


def report(title, probs, rows, threshold):
    block, false_block, n_false = rates(probs, rows, threshold)
    lines = [f"\n== {title}（門檻 {threshold:.4f}）：不過擋下 {block:.1%}  過被誤擋 {false_block:.1%}（{n_false} 篇）"]
    by = {}
    for p, r in zip(probs, rows):
        hit, n = by.get((r["source"], r["label"]), (0, 0))
        by[(r["source"], r["label"])] = (hit + (p >= threshold), n + 1)
    for (source, label), (hit, n) in sorted(by.items()):
        lines.append(f"   {source:28} {'不過 擋下' if label else '過   誤擋'} {hit}/{n}")
    print("\n".join(lines))
    return {"threshold": threshold, "block_rate": block, "false_block_rate": false_block,
            "by_source": {f"{s}|{lab}": [h, n] for (s, lab), (h, n) in by.items()}}


def curve(probs, rows, chosen):
    """不同門檻下的擋下率／誤擋率：看門檻放寬能多擋多少、代價多少誤擋"""
    points = []
    print("\n== 門檻取捨（test）")
    for t in sorted({0.5, 0.7, 0.8, 0.9, 0.95, round(chosen, 4)}):
        block, false_block, n_false = rates(probs, rows, t)
        points.append({"threshold": t, "block_rate": block, "false_block_rate": false_block})
        print(f"   門檻 {t:.4f}：不過擋下 {block:.1%}  過被誤擋 {false_block:.1%}（{n_false} 篇）{'  ← val 挑的' if t == round(chosen, 4) else ''}")
    return points


def acceptance(metrics):
    by = metrics["by_source"]
    source_rates = {k.split("|")[0]: h / n for k, (h, n) in by.items() if k.endswith("|1") and n}
    heldout = by.get("seed:test_heldout|0", [0, 0])[0]
    minor_hit, minor_n = by.get("minor_test|1", [0, 0])
    topic_fp, topic_n = by.get("minor_topic_test|0", [0, 0])
    checks = [
        (f"不過擋下 ≥ {TARGET_BLOCK:.0%}", metrics["block_rate"] >= TARGET_BLOCK, f"{metrics['block_rate']:.1%}"),
        (f"每個來源不過擋下 ≥ {TARGET_SOURCE_BLOCK:.0%}", all(v >= TARGET_SOURCE_BLOCK for v in source_rates.values()),
         "、".join(f"{k} {v:.0%}" for k, v in sorted(source_rates.items()))),
        (f"過被誤擋 ≤ {TARGET_FALSE_BLOCK:.0%}", metrics["false_block_rate"] <= TARGET_FALSE_BLOCK, f"{metrics['false_block_rate']:.1%}"),
        (f"嚇人但正常的考題誤擋 ≤ {TARGET_HELDOUT_FALSE_BLOCKS} 篇", heldout <= TARGET_HELDOUT_FALSE_BLOCKS, f"{heldout} 篇"),
        (f"涉及未成年的性內容擋下 ≥ {TARGET_MINOR_BLOCK:.0%}", minor_n > 0 and minor_hit / minor_n >= TARGET_MINOR_BLOCK,
         f"{minor_hit}/{minor_n}"),
        (f"提到兒少的正常內容誤擋 ≤ {TARGET_MINOR_TOPIC_FALSE_BLOCK:.0%}",
         topic_n > 0 and topic_fp / topic_n <= TARGET_MINOR_TOPIC_FALSE_BLOCK, f"{topic_fp}/{topic_n}"),
    ]
    print("\n== 達標線")
    for name, ok, value in checks:
        print(f"   {'✓' if ok else '✗'} {name}：{value}")
    return all(ok for _, ok, _ in checks)


def head_contract(tok, build_sequence, max_len, head_max_len):
    """線上推論要照抄的題目部分：[CLS] 題目 [SEP] [MASK] false… [MASK] true… [SEP]，後面接貼文 token＋[SEP]"""
    ids, markers = build_sequence(tok, "", QUESTION, max_len=max_len, head_max_len=head_max_len)
    return ids[:-1], markers


def check_contract(tok, build_sequence, texts, head_ids, max_len, head_max_len, tokenizer_json):
    """用線上的方式（tokenizers 函式庫）組輸入，必須跟 Laya 的 build_sequence 一模一樣"""
    from tokenizers import Tokenizer

    online = Tokenizer.from_file(str(tokenizer_json))
    room = max_len - len(head_ids) - 1
    for t in texts:
        expected, _ = build_sequence(tok, t, QUESTION, max_len=max_len, head_max_len=head_max_len)
        state = online.encode(t.replace(tok.mask_token, " "), add_special_tokens=False).ids[:room]
        got = head_ids + state + [tok.sep_token_id]
        if got != expected:
            raise SystemExit(f"推論格式跟訓練不一致：{t[:40]!r}")


def export_onnx(torch, model, out: Path, head_ids, markers, sample_texts, tok, max_len, probs_torch):
    import numpy as np
    import onnx
    import onnxruntime as ort
    from onnxruntime.transformers.float16 import convert_float_to_float16
    from tokenizers import Tokenizer

    class Wrapper(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask, marker_pos, marker_mask, qtype):
            return self.m(input_ids=input_ids, attention_mask=attention_mask, marker_pos=marker_pos,
                          marker_mask=marker_mask, qtype=qtype)[0]

    wrapper = Wrapper(model.float().cpu().eval())
    n = len(head_ids) + 8
    dummy = (
        torch.tensor([head_ids + [5] * 7 + [tok.sep_token_id]]),
        torch.ones((1, n), dtype=torch.long),
        torch.tensor([markers]),
        torch.ones((1, len(markers)), dtype=torch.bool),
        torch.tensor([QTYPE_NOUL]),
    )
    tmp = out / "fp32"
    tmp.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        wrapper, dummy, str(tmp / "model.onnx"), dynamo=False, opset_version=17,
        input_names=["input_ids", "attention_mask", "marker_pos", "marker_mask", "qtype"], output_names=["logits"],
        dynamic_axes={"input_ids": {0: "batch", 1: "seq_len"}, "attention_mask": {0: "batch", 1: "seq_len"},
                      "marker_pos": {0: "batch", 1: "n_opts"}, "marker_mask": {0: "batch", 1: "n_opts"},
                      "qtype": {0: "batch"}, "logits": {0: "batch", 1: "n_opts"}},
    )
    # 用 onnxruntime 自帶的轉換器（onnxconverter_common 版會把 attention 裡的 Cast 輸出標錯型別，載入失敗）
    fp16 = convert_float_to_float16(onnx.load(str(tmp / "model.onnx")), keep_io_types=True)
    # 外部資料檔是用附加模式寫的：同一個 Kaggle 工作階段先跑過（例如 --smoke）時，新的權重會接在舊檔後面
    # （v2 實際 643MB 的模型變成 1.93GB 的檔）→ 先刪掉舊檔
    for stale in ("model.onnx", "model.onnx.data"):
        (out / stale).unlink(missing_ok=True)
    onnx.save(fp16, str(out / "model.onnx"), save_as_external_data=True, all_tensors_to_one_file=True,
              location="model.onnx.data", size_threshold=1024)
    shutil.rmtree(tmp)

    # 用線上的方式重算（tokenizers＋onnxruntime），跟 PyTorch 比
    online = Tokenizer.from_file(str(out / "tokenizer" / "tokenizer.json"))
    sess = ort.InferenceSession(str(out / "model.onnx"), providers=["CPUExecutionProvider"])
    room = max_len - len(head_ids) - 1
    worst = 0.0
    for t, p_torch in zip(sample_texts, probs_torch):
        ids = head_ids + online.encode(t, add_special_tokens=False).ids[:room] + [tok.sep_token_id]
        logits = sess.run(None, {
            "input_ids": np.array([ids], dtype=np.int64),
            "attention_mask": np.ones((1, len(ids)), dtype=np.int64),
            "marker_pos": np.array([markers], dtype=np.int64),
            "marker_mask": np.ones((1, len(markers)), dtype=bool),
            "qtype": np.array([QTYPE_NOUL], dtype=np.int64),
        })[0][0]
        e = np.exp(logits - logits.max())
        worst = max(worst, abs(float(e[1] / e.sum()) - p_torch))
    print(f"   ONNX（fp16）跟 PyTorch 的 P(不過) 最大差 {worst:.4f}（{len(sample_texts)} 篇）")
    if worst > 0.02:
        raise SystemExit("ONNX 輸出跟 PyTorch 差太多，不要拿去上線")
    return worst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="有 train/val/test.jsonl 的資料夾")
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=int, default=3,
                    help="v3 三輪第 3 輪過擬合、最好的是第 2 輪；v5 兩輪最好的是最後一輪（還沒練飽）。每輪都在 val 比，取最好的那輪")
    ap.add_argument("--label-smoothing", type=float, default=0.05,
                    help="v3 對沾到錢／詐騙話題的正常文打 1.000，標籤平滑讓分數不要推到極端")
    ap.add_argument("--batch-size", type=int, default=None, help="預設：GPU 16、Mac／CPU 8（16 GB 的 Mac 一次 16 篇會吃到 15 GB 爆 swap）")
    ap.add_argument("--grad-accum", type=int, default=None, help="預設讓等效批次＝32")
    ap.add_argument("--grad-checkpoint", choices=["auto", "on", "off"], default="auto",
                    help="中間結果不全留、反向時重算：記憶體大減、慢約三成；auto＝Mac／CPU 開、GPU 關")
    ap.add_argument("--lr", type=float, default=2e-4, help="LoRA 的學習率")
    ap.add_argument("--head-lr", type=float, default=5e-5, help="決策頭（head、scorer）的學習率")
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--lora-alpha", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=512, help="跟線上 detector 的 MAX_TOKENS 一樣")
    ap.add_argument("--head-max-len", type=int, default=192)
    ap.add_argument("--max-false-block", type=float, default=TARGET_FALSE_BLOCK, help="val 上挑門檻時允許的誤擋率")
    ap.add_argument("--max-train", type=int, default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--smoke", action="store_true", help="各取少量、1 輪，只確認整條流程跑得通")
    args = ap.parse_args()
    if args.smoke:
        args.epochs, args.max_train = 1, 256

    import laya
    import torch
    from laya.common import build_sequence
    from peft import LoraConfig, get_peft_model

    torch.manual_seed(args.seed)
    random.seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    amp = device == "cuda"
    args.batch_size = args.batch_size or (16 if device == "cuda" else 8)
    args.grad_accum = args.grad_accum or max(1, 32 // args.batch_size)
    checkpointing = args.grad_checkpoint == "on" or (args.grad_checkpoint == "auto" and device != "cuda")
    data, out = Path(args.data), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"裝置 {device}（混合精度 {amp}）；參數 {vars(args)}")

    train = load_rows(data / "train.jsonl", args.max_train, args.seed)
    val = load_rows(data / "val.jsonl", 128 if args.smoke else None, args.seed)
    test = load_rows(data / "test.jsonl", 128 if args.smoke else None, args.seed)
    print(f"train {len(train)}、val {len(val)}、test {len(test)}")

    print(f"\n載入 {BASE_REPO}/{BASE_SUBFOLDER}@{BASE_REVISION[:8]}…")
    agent = laya.load(BASE_REPO, subfolder=BASE_SUBFOLDER, revision=BASE_REVISION, device=device)
    model, tok = agent.model.float(), agent.tok
    pad_id = tok.pad_token_id
    tok.save_pretrained(str(out / "tokenizer"))

    head_ids, markers = head_contract(tok, build_sequence, args.max_len, args.head_max_len)
    check_contract(tok, build_sequence, [r["text"] for r in test], head_ids, args.max_len, args.head_max_len,
                   out / "tokenizer" / "tokenizer.json")
    print(f"題目 {len(head_ids)} token；線上推論格式跟訓練一致（{len(test)} 篇逐 token 比對）")

    t0 = time.time()
    enc = {name: encode(tok, build_sequence, [r["text"] for r in rows], args.max_len, args.head_max_len)
           for name, rows in (("train", train), ("val", val), ("test", test))}
    print(f"斷字完成（{time.time() - t0:.0f}s）")

    # 微調前（官方 Laya 直接問）的成績當對照；門檻一樣在 val 上挑
    model.to(device)
    base_val = predict(torch, model, enc["val"], device, pad_id, args.batch_size * 2, amp)
    base_t = pick_threshold(base_val, [r["label"] for r in val], args.max_false_block)
    base_metrics = report("微調前（官方 Laya 直接問）test", predict(torch, model, enc["test"], device, pad_id, args.batch_size * 2, amp), test, base_t)

    if checkpointing:
        model.encoder.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        print("gradient checkpointing 開啟")
    lora = LoraConfig(r=args.lora_r, lora_alpha=args.lora_alpha, lora_dropout=0.05, bias="none",
                      target_modules=["Wqkv", "Wo", "Wi"], modules_to_save=["head", "scorer"])  # v2 多掛 MLP 的 Wi
    peft_model = get_peft_model(model, lora)
    peft_model.train()
    lora_params = [p for n, p in peft_model.named_parameters() if p.requires_grad and "lora_" in n]
    head_params = [p for n, p in peft_model.named_parameters() if p.requires_grad and "lora_" not in n]
    print(f"可訓練參數：LoRA {sum(p.numel() for p in lora_params) / 1e6:.2f}M、決策頭 {sum(p.numel() for p in head_params) / 1e6:.2f}M")
    opt = torch.optim.AdamW([{"params": lora_params, "lr": args.lr}, {"params": head_params, "lr": args.head_lr}], weight_decay=0.01)
    steps_per_epoch = max(1, len(train) // (args.batch_size * args.grad_accum))
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=[args.lr, args.head_lr], total_steps=steps_per_epoch * args.epochs,
                                                pct_start=0.06, anneal_strategy="cos")
    scaler = torch.amp.GradScaler("cuda", enabled=amp)
    labels = torch.tensor([r["label"] for r in train], device=device)
    val_labels = [r["label"] for r in val]
    # 長度相近的放同一批（少補零）；每輪在大區塊內打亂
    by_len = sorted(range(len(train)), key=lambda i: len(enc["train"][i][0]))

    best = {"score": -1.0}
    step = 0
    for epoch in range(1, args.epochs + 1):
        order = by_len[:]
        block = args.batch_size * 32
        for s in range(0, len(order), block):
            chunk = order[s : s + block]
            random.shuffle(chunk)
            order[s : s + block] = chunk
        batches = [order[s : s + args.batch_size] for s in range(0, len(order), args.batch_size)]
        random.shuffle(batches)
        t_epoch, loss_sum = time.time(), 0.0
        opt.zero_grad()
        for b, idx in enumerate(batches, 1):
            with torch.autocast("cuda", dtype=torch.float16, enabled=amp):
                logits, _ = peft_model(**collate(torch, enc["train"], idx, device, pad_id))
            loss = torch.nn.functional.cross_entropy(logits.float(), labels[idx], label_smoothing=args.label_smoothing)
            scaler.scale(loss / args.grad_accum).backward()
            loss_sum += loss.item()
            if b % args.grad_accum == 0 or b == len(batches):
                scaler.unscale_(opt)
                torch.nn.utils.clip_grad_norm_(lora_params + head_params, 1.0)
                scaler.step(opt)
                scaler.update()
                opt.zero_grad()
                if step < steps_per_epoch * args.epochs:
                    sched.step()
                step += 1
                if step % 50 == 0:
                    print(f"  第 {epoch} 輪 step {step} loss {loss_sum / b:.4f}（{b * args.batch_size / (time.time() - t_epoch):.0f} 篇/秒）")
        val_probs = predict(torch, peft_model, enc["val"], device, pad_id, args.batch_size * 2, amp)
        threshold = pick_threshold(val_probs, val_labels, args.max_false_block)
        block_rate, false_block, _ = rates(val_probs, val, threshold)
        print(f"\n[第 {epoch} 輪] loss {loss_sum / len(batches):.4f}；val 門檻 {threshold:.4f} → 不過擋下 {block_rate:.1%}、過被誤擋 {false_block:.1%}（{time.time() - t_epoch:.0f}s）")
        if block_rate > best["score"]:
            best = {"score": block_rate, "epoch": epoch, "threshold": threshold,
                    "state": {n: p.detach().cpu().clone() for n, p in peft_model.named_parameters() if p.requires_grad}}
            print("  ✓ 目前最好")

    print(f"\n用第 {best['epoch']} 輪（val 不過擋下 {best['score']:.1%}）")
    with torch.no_grad():
        params = dict(peft_model.named_parameters())
        for n, v in best["state"].items():
            params[n].copy_(v.to(device))
    test_probs = predict(torch, peft_model, enc["test"], device, pad_id, args.batch_size * 2, amp)
    metrics = report("微調後 test", test_probs, test, best["threshold"])
    metrics["curve"] = curve(test_probs, test, best["threshold"])
    passed = acceptance(metrics)

    print("\n合併 LoRA、匯出 ONNX…")
    merged = peft_model.merge_and_unload()
    sample = list(range(min(24, len(test))))
    max_diff = export_onnx(torch, merged, out, head_ids, markers, [test[i]["text"] for i in sample], tok,
                           args.max_len, [test_probs[i] for i in sample])

    readout = {
        "base_model": f"{BASE_REPO}/{BASE_SUBFOLDER}", "base_revision": BASE_REVISION,
        "question": QUESTION, "qtype": QTYPE_NOUL, "head_ids": head_ids, "markers": markers,
        "sep_id": tok.sep_token_id, "max_len": args.max_len, "head_max_len": args.head_max_len,
        "threshold": best["threshold"], "label_index": {"pass": 0, "block": 1},
    }
    (out / "readout.json").write_text(json.dumps(readout, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps({
        "args": vars(args), "best_epoch": best["epoch"], "val_block_rate": best["score"],
        "before_finetune": base_metrics, "test": metrics, "passed": passed, "onnx_max_diff": max_diff,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = shutil.make_archive(str(out), "zip", root_dir=str(out))
    print(f"\n輸出 {out}（model.onnx、model.onnx.data、tokenizer/、readout.json、metrics.json）；打包 {archive}")
    print("達標 ✓ 可以準備換上線" if passed else "還沒達標：看上面哪幾條沒過，調資料或參數再練")


if __name__ == "__main__":
    main()
