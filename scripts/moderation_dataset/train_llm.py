"""v7：微調 YuFeng-XGuard-Reason-0.6B 判斷論壇貼文「該不該擋」，只讀第一個 token 的 logits（2026-10-03）。

為什麼換：Laya（322M 編碼器）練到 v6，涉及未成年的性內容 @0.9 只擋 71～76%，加資料、清標註都卡住；
同一份考題上 YuFeng-0.6B 不微調就擋 88%（繁中 @0.5 109/117），但它沒練過詐騙、會把投資討論當「不當建議」。
→ 用我們的資料微調它：保留它的安全知識，教它論壇的界線（詐騙要擋、投資討論不擋）。DANNY 選這條（Jev 的做法）。

做法照官方（YuFeng-XGuard 論文 arXiv:2601.15588、模型卡）：
- 官方訓練＝SFT「第一個 token＝類別代碼、後面接 <explanation>」，一般的 LM 損失；我們沒有解釋文字，
  只練第一個 token：過＝sec，不過＝我們細類對應的代碼（詐騙→ec、涉及未成年的性內容→ma、色情→pc…，
  見 KIND_TO_CODE）。對應不明確的細類（泛犯罪、暴力、unclear…）不硬塞類別，用二元損失
  z＝logsumexp(風險代碼) − logit(sec)，讓模型自己選類別
- 官方評估＝第一個 token 最高的風險類別機率 ≥ 0.5（提問類）就算有害；我們線上用 1 − P(sec)
  （P 只在 29 個代碼 token 之間正規化）再在 val 上挑門檻。metrics 兩種都報
- 輸入用 YuFeng 自己的 chat template（policy=None、reason_first=False），貼文放 Input Text 那格（template 會 trim）
- 線上不用 transformers：template 切成 prefix／suffix 的 token id 存進 readout.json，推論＝prefix＋貼文 token＋suffix
  （訓練也用同一種拼法，跟線上一個 token 都不差）；長文跟線上一樣只看開頭＋結尾
- 每輪在 val 上挑門檻（過被誤擋 ≤ --max-false-block），留最好的一輪；最後考 test 並對照達標線（沿用 train.py）
- 輸出合併後的模型（fp16 safetensors）＋ readout.json ＋ metrics.json；ONNX 匯出回本機做（Kaggle 少一個會壞的步驟）

用法：
  Kaggle（GPU T4、Internet 開）：資料集 zip 裡附這支腳本和 train.py
    !pip uninstall -y -q torchao
    !pip install -q -U "transformers>=4.56" peft
    !python /kaggle/input/<dataset>/train_llm.py --data /kaggle/input/<dataset> --out /kaggle/working/yufeng-moderation
  先確認整條流程跑得通：加 --smoke（各取少量、1 輪）
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")  # 長短不一的批次少一點記憶體碎片
sys.path.insert(0, str(Path(__file__).resolve().parent))
from train import (  # noqa: E402  門檻與達標線跟 Laya 版共用
    acceptance,
    curve,
    load_rows,
    pick_threshold,
    rates,
    report,
)

BASE_REPO = "Alibaba-AAIG/YuFeng-XGuard-Reason-0.6B"
BASE_REVISION = "9016029653fa2994e8efe5f2c007fc2ac172287d"  # 2026-10-03
PLACEHOLDER = "\u2063CRYPTOMIND_POST\u2063"
# 我們的細類 → YuFeng 的風險代碼（tokenizer_config 的 id2risk）。不在表裡的不過類別用二元損失
KIND_TO_CODE = {
    **{k: "ec" for k in ("scam", "invest_scam", "fake_official", "airdrop_giveaway", "job_scam", "fake_exchange",
                         "recovery_scam", "pyramid", "gambling", "romance_invest", "loan_scam", "account_card_trade",
                         "asks_payment", "詐騙")},
    "sexual_minor": "ma", "sexual": "pc", "色情": "pc", "self_harm": "mh", "自傷": "mh", "drugs": "dc",
    "weapons": "dw", "privacy": "pp", "doxxing": "pp", "hate": "ac", "harassment": "cy", "threat": "ti", "威脅騷擾": "ti",
}
SAFE_CODE = "sec"
# 線上 detector 的長文規則：開頭 300＋結尾 150 token；訓練時超過 MAX_POST 一樣切開頭＋結尾
FIRST_TOKENS, LAST_TOKENS = 300, 150
MAX_POST = FIRST_TOKENS + LAST_TOKENS


def prompt_contract(tok):
    """YuFeng 的 chat template 切成 prefix／suffix 的 token id；貼文前面的空白併進貼文（跟整串一起斷字時一樣）"""
    rendered = tok.apply_chat_template([{"role": "user", "content": PLACEHOLDER}], policy=None, reason_first=False, tokenize=False)
    before, after = rendered.split(PLACEHOLDER)
    lead = before[len(before.rstrip()):]
    prefix = tok(before.rstrip(), add_special_tokens=False).input_ids
    suffix = tok(after, add_special_tokens=False).input_ids
    return prefix, suffix, lead


def post_ids(tok, text, lead):
    ids = tok(lead + (text or "").strip(), add_special_tokens=False).input_ids  # template 對內容做 trim
    return ids if len(ids) <= MAX_POST else ids[:FIRST_TOKENS] + ids[-LAST_TOKENS:]


def check_contract(tok, texts, prefix, suffix, lead):
    """拼起來的 token 跟 template 整串斷字比對（貼文開頭的斷字邊界可能差一點，只報告、不擋）"""
    same = 0
    for t in texts:
        full = tok.apply_chat_template([{"role": "user", "content": t}], policy=None, reason_first=False, tokenize=False)
        same += tok(full, add_special_tokens=False).input_ids == prefix + tok(lead + t.strip(), add_special_tokens=False).input_ids + suffix
    return same / max(1, len(texts))


def collate(torch, seqs, idx, device, pad_id):
    items = [seqs[i] for i in idx]
    width = max(len(s) for s in items)
    ids = torch.tensor([s + [pad_id] * (width - len(s)) for s in items], device=device)
    mask = torch.tensor([[1] * len(s) + [0] * (width - len(s)) for s in items], device=device)
    last = torch.tensor([len(s) - 1 for s in items], device=device)
    return ids, mask, last


def block_logit(torch, causal_lm, ids, mask, last, label_w, safe_index):
    """z＝logsumexp(風險代碼 logit) − logit(sec)；只算最後一個位置的 29 個代碼，不算整個詞表"""
    hidden = causal_lm.model(input_ids=ids, attention_mask=mask).last_hidden_state
    h = hidden[torch.arange(ids.shape[0], device=ids.device), last]
    logits = h.float() @ label_w.float().T
    risk = torch.cat([logits[:, :safe_index], logits[:, safe_index + 1:]], dim=1)
    return torch.logsumexp(risk, dim=1) - logits[:, safe_index], logits


def predict(torch, causal_lm, seqs, device, pad_id, batch_size, amp, label_w, safe_index):
    """回傳 (1 − P(sec), 官方分數＝最高風險類別的機率)，P 都只在 29 個代碼之間正規化"""
    causal_lm.eval()
    order = sorted(range(len(seqs)), key=lambda i: len(seqs[i]))
    probs, official = [0.0] * len(seqs), [0.0] * len(seqs)
    with torch.no_grad():
        for s in range(0, len(order), batch_size):
            idx = order[s : s + batch_size]
            with torch.autocast("cuda", dtype=torch.float16, enabled=amp):
                _, logits = block_logit(torch, causal_lm, *collate(torch, seqs, idx, device, pad_id), label_w, safe_index)
            p = torch.softmax(logits.float(), dim=1)
            risk = torch.cat([p[:, :safe_index], p[:, safe_index + 1:]], dim=1)
            for i, a, b in zip(idx, (1 - p[:, safe_index]).tolist(), risk.max(dim=1).values.tolist()):
                probs[i], official[i] = a, b
    causal_lm.train()
    if device == "mps":
        torch.mps.empty_cache()
    return probs, official


def train_loss(torch, z, logits, code_target, labels, smooth):
    """有對應代碼的（過＝sec、不過＝對應類別）：29 個代碼上的交叉熵＝官方的第一個 token SFT；
    沒對應的不過：二元損失（風險代碼合計 vs sec）"""
    f = torch.nn.functional
    has_code = code_target >= 0
    loss = torch.zeros_like(z, dtype=torch.float32)
    if has_code.any():
        loss[has_code] = f.cross_entropy(logits[has_code].float(), code_target[has_code], label_smoothing=smooth, reduction="none")
    if (~has_code).any():
        y = labels[~has_code].float() * (1 - smooth) + smooth / 2
        loss[~has_code] = f.binary_cross_entropy_with_logits(z[~has_code].float(), y, reduction="none")
    return loss.mean()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="有 train/val/test.jsonl 的資料夾")
    ap.add_argument("--base-path", default=None, help="本機已下載的 YuFeng 資料夾（不給就從 Hugging Face 下載釘住的版本）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=int, default=2, help="每輪都在 val 比，取最好的那輪")
    ap.add_argument("--label-smoothing", type=float, default=0.05)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--grad-accum", type=int, default=4, help="等效批次＝batch×accum")
    ap.add_argument("--grad-checkpoint", choices=["on", "off"], default="on",
                    help="開著省記憶體、慢約三成。T4 實測關掉會 OOM：每篇帶 299 token 題目、長文到 ~760 token，"
                         "批次 8 × 28 層的中間結果吃滿 14.5 GB（2026-10-03）")
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--lora-alpha", type=int, default=32)
    ap.add_argument("--max-false-block", type=float, default=0.02, help="val 上挑門檻時允許的誤擋率")
    ap.add_argument("--max-train", type=int, default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--smoke", action="store_true", help="各取少量、1 輪，只確認整條流程跑得通")
    args = ap.parse_args()
    if args.smoke:
        args.epochs, args.max_train = 1, 128

    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.manual_seed(args.seed)
    random.seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    amp = device == "cuda"
    data, out = Path(args.data), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"裝置 {device}（混合精度 {amp}）；參數 {vars(args)}")

    train = load_rows(data / "train.jsonl", args.max_train, args.seed)
    val = load_rows(data / "val.jsonl", 96 if args.smoke else None, args.seed)
    test = load_rows(data / "test.jsonl", 96 if args.smoke else None, args.seed)
    print(f"train {len(train)}、val {len(val)}、test {len(test)}")

    print(f"\n載入 {BASE_REPO}@{BASE_REVISION[:8]}…")
    src = {"pretrained_model_name_or_path": args.base_path} if args.base_path else {"pretrained_model_name_or_path": BASE_REPO, "revision": BASE_REVISION}
    tok = AutoTokenizer.from_pretrained(**src)
    model = AutoModelForCausalLM.from_pretrained(**src, dtype=torch.float32).to(device)
    pad_id = tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id
    codes = list(tok.init_kwargs["id2risk"])
    label_ids = [tok.encode(c, add_special_tokens=False) for c in codes]
    assert all(len(x) == 1 for x in label_ids), "風險代碼要是單一 token"
    label_ids = [x[0] for x in label_ids]
    safe_index = codes.index(SAFE_CODE)
    # lm_head 凍結不訓（跟 embedding 共用權重）：只取 29 個代碼那幾列
    label_w = model.get_output_embeddings().weight[label_ids].detach().clone()

    prefix, suffix, lead = prompt_contract(tok)
    match = check_contract(tok, [r["text"] for r in test[:200]], prefix, suffix, lead)
    print(f"題目 prefix {len(prefix)}＋suffix {len(suffix)} token；跟 template 整串斷字完全一樣的比例 {match:.0%}")

    def seqs_of(rows):
        return [prefix + post_ids(tok, r["text"], lead) + suffix for r in rows]

    t0 = time.time()
    enc = {"train": seqs_of(train), "val": seqs_of(val), "test": seqs_of(test)}
    print(f"斷字完成（{time.time() - t0:.0f}s）")

    # 微調前（YuFeng 直接問）的成績當對照；門檻一樣在 val 上挑
    lw = label_w.to(device)
    base_val, _ = predict(torch, model, enc["val"], device, pad_id, args.batch_size * 2, amp, lw, safe_index)
    base_t = pick_threshold(base_val, [r["label"] for r in val], args.max_false_block)
    base_test, base_official = predict(torch, model, enc["test"], device, pad_id, args.batch_size * 2, amp, lw, safe_index)
    base_metrics = report("微調前（YuFeng 直接問）test", base_test, test, base_t)
    base_metrics["official_0.5"] = report("微調前 test，官方規則（最高風險類別 ≥ 0.5）", base_official, test, 0.5)

    if args.grad_checkpoint == "on":
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
    lora = LoraConfig(r=args.lora_r, lora_alpha=args.lora_alpha, lora_dropout=0.05, bias="none",
                      target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
    peft_model = get_peft_model(model, lora)
    peft_model.train()
    causal_lm = peft_model.get_base_model()
    params = [p for p in peft_model.parameters() if p.requires_grad]
    print(f"可訓練參數：LoRA {sum(p.numel() for p in params) / 1e6:.2f}M")
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.01)
    steps_per_epoch = max(1, len(train) // (args.batch_size * args.grad_accum))
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=steps_per_epoch * args.epochs,
                                                pct_start=0.06, anneal_strategy="cos")
    scaler = torch.amp.GradScaler("cuda", enabled=amp)
    smooth = args.label_smoothing
    code_index = {c: i for i, c in enumerate(codes)}
    code_target = torch.tensor([safe_index if r["label"] == 0 else code_index.get(KIND_TO_CODE.get(r["kind"], ""), -1)
                                for r in train], device=device)
    labels = torch.tensor([r["label"] for r in train], device=device)
    mapped = sum(1 for r in train if r["label"] == 1 and r["kind"] in KIND_TO_CODE)
    print(f"不過 {int(labels.sum())} 篇：{mapped} 篇有對應代碼（交叉熵），其餘用二元損失")
    val_labels = [r["label"] for r in val]
    by_len = sorted(range(len(train)), key=lambda i: len(enc["train"][i]))  # 長度相近的放同一批（少補零）

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
                z, logits = block_logit(torch, causal_lm, *collate(torch, enc["train"], idx, device, pad_id), lw, safe_index)
            loss = train_loss(torch, z, logits, code_target[idx], labels[idx], smooth)
            scaler.scale(loss / args.grad_accum).backward()
            loss_sum += loss.item()
            if b % args.grad_accum == 0 or b == len(batches):
                scaler.unscale_(opt)
                torch.nn.utils.clip_grad_norm_(params, 1.0)
                scaler.step(opt)
                scaler.update()
                opt.zero_grad()
                if step < steps_per_epoch * args.epochs:
                    sched.step()
                step += 1
                if step % 50 == 0:
                    print(f"  第 {epoch} 輪 step {step} loss {loss_sum / b:.4f}（{b * args.batch_size / (time.time() - t_epoch):.1f} 篇/秒）", flush=True)
        val_probs, _ = predict(torch, causal_lm, enc["val"], device, pad_id, args.batch_size * 2, amp, lw, safe_index)
        threshold = pick_threshold(val_probs, val_labels, args.max_false_block)
        block_rate, false_block, _ = rates(val_probs, val, threshold)
        print(f"\n[第 {epoch} 輪] loss {loss_sum / len(batches):.4f}；val 門檻 {threshold:.4f} → 不過擋下 {block_rate:.1%}、過被誤擋 {false_block:.1%}（{time.time() - t_epoch:.0f}s）", flush=True)
        if block_rate > best["score"]:
            best = {"score": block_rate, "epoch": epoch, "threshold": threshold,
                    "state": {n: p.detach().cpu().clone() for n, p in peft_model.named_parameters() if p.requires_grad}}
            print("  ✓ 目前最好")

    print(f"\n用第 {best['epoch']} 輪（val 不過擋下 {best['score']:.1%}）")
    with torch.no_grad():
        named = dict(peft_model.named_parameters())
        for n, v in best["state"].items():
            named[n].copy_(v.to(device))
    test_probs, test_official = predict(torch, causal_lm, enc["test"], device, pad_id, args.batch_size * 2, amp, lw, safe_index)
    metrics = report("微調後 test", test_probs, test, best["threshold"])
    metrics["curve"] = curve(test_probs, test, best["threshold"])
    metrics["official_0.5"] = report("微調後 test，官方規則（最高風險類別 ≥ 0.5）", test_official, test, 0.5)
    passed = acceptance(metrics)

    print("\n合併 LoRA、存模型（fp16）…")
    merged = peft_model.merge_and_unload()
    merged.half().save_pretrained(str(out / "model"), safe_serialization=True)
    tok.save_pretrained(str(out / "model"))
    readout = {
        "base_model": BASE_REPO, "base_revision": BASE_REVISION, "format": "yufeng-first-token",
        "kind_to_code": KIND_TO_CODE,
        "prefix_ids": prefix, "suffix_ids": suffix, "lead": lead,
        "label_codes": codes, "label_ids": label_ids, "safe_code": SAFE_CODE,
        "first_tokens": FIRST_TOKENS, "last_tokens": LAST_TOKENS,
        "threshold": best["threshold"], "score": "1 - P(sec)，P 只在 label_ids 之間正規化",
    }
    (out / "readout.json").write_text(json.dumps(readout, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps({
        "args": vars(args), "best_epoch": best["epoch"], "val_block_rate": best["score"],
        "before_finetune": base_metrics, "test": metrics, "passed": passed, "template_match": match,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = shutil.make_archive(str(out), "zip", root_dir=str(out))
    print(f"\n輸出 {out}（model/、readout.json、metrics.json）；打包 {archive}")
    print("達標 ✓ 可以準備換上線" if passed else "還沒達標：看上面哪幾條沒過")


if __name__ == "__main__":
    main()
