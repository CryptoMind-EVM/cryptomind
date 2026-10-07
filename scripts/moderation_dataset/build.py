"""論壇內容檢查的微調資料集：過＝0、不過＝1（2026-10-01）。

DANNY：不寫死關鍵字規則，改微調 Laya 判斷「這篇該不該擋」。不過＝腥羶色（色情、血腥暴力）、
詐騙詐欺，以及其他違法事項（犯罪教學、毒品槍械與人頭帳戶／卡料買賣、威脅、仇恨、騷擾、肉搜、
鼓勵自殘、非法博弈）。微調達標前，core/moderation/rules.py 的規則照舊在線上擋。

組成：公開資料集（只取允許商用的授權）＋ seed/ 裡我們自己寫的台灣情境資料。
ScamShield 英文簡訊的「不過」其實是一般垃圾廣告（不在範圍），不收。
- 公開資料集原本的「不安全」比我們的範圍大：政治、版權、未經授權的建議（投資意見！）、
  輕微髒話這些在論壇是正常內容，整行丟掉不硬標，免得模型學到「給投資意見＝要擋」。
- 簡體中文約七成轉繁體（OpenCC s2twp）；4～400 字、像貼文的才收。
- 同一句不同來源標籤衝突就整句丟掉；考題（test）跟訓練完全隔開。
- 訓練集約 8% 另外做「字元間穿插符號」版本，過與不過兩邊都做，免得學成「有符號就擋」。
- v5：涉及未成年的性內容（sexual_minor）全部收進訓練、另外固定切一份當獨立考題；提到兒少的
  正常內容收當反例（DANNY 2026-10-02：v4 線上放行這類貼文）。
- v6：Nemotron 有附 AI 回答的那筆，類別可能是看回答標的（中文這種只有 8% 提到兒少，沒附回答的 48～64%）
  → 只有沒附回答的才算 sexual_minor；有附回答的提問仍是不過，但細類改成 unclear。

用法（只有建資料集要這些套件，不進 requirements.txt）：
  pip install huggingface_hub pandas pyarrow opencc-python-reimplemented
  python scripts/moderation_dataset/build.py
輸出 data/moderation_dataset/：train.jsonl、val.jsonl、test.jsonl、DATASET_CARD.md、zip（上傳 Kaggle 用，附 train.py、train_llm.py）。
每行 {"id", "text", "label", "lang", "source", "kind"}；label 1＝不過。kind 是細類（scam、sexual…），
只練「過／不過」可以不管它，要練選擇題（a: 正常 b: 詐騙 c: 色情…）就拿它當選項。
"""

from __future__ import annotations

import collections
import hashlib
import json
import random
import re
import unicodedata
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SEED = Path(__file__).resolve().parent / "seed"
# 考題固定成 v1 那份（626 題）：之後改訓練資料配額不會連帶改到考題，各版成績才能直接比
FROZEN_TEST = SEED / "test_frozen_v1.jsonl"
OUT = REPO / "data" / "moderation_dataset"
CACHE = Path.home() / ".cache" / "cryptomind-datasets"
VERSION = "v7"  # 資料同 v6；zip 多附 train_llm.py（v7 改微調 YuFeng-XGuard-Reason-0.6B）
RNG_SEED = 20261001

SOURCES = {
    # name: (HF dataset, file)
    "nemotron_zh": ("nvidia/Nemotron-Safety-Guard-Dataset-v3", "zh/train.jsonl"),
    "nemotron_zh_test": ("nvidia/Nemotron-Safety-Guard-Dataset-v3", "zh/valid.jsonl"),
    "nemotron_en": ("nvidia/Nemotron-Safety-Guard-Dataset-v3", "en/train.jsonl"),
    "polyguard": ("ToxicityPrompts/PolyGuardMix", "data/train-00000-of-00008.parquet"),
    "cold": ("thu-coai/cold", "train.csv"),
    "fgrc": ("Abooooo/FGRC-SCD", "FGRC-SCD-sms.zip"),
}

LICENSES = {
    "nemotron": "nvidia/Nemotron-Safety-Guard-Dataset-v3 — CC BY 4.0（NVIDIA）",
    "polyguard": "ToxicityPrompts/PolyGuardMix — CC BY 4.0（Kumar et al., PolyGuard）",
    "cold": "thu-coai/cold — Apache-2.0（Deng et al., COLD）",
    "fgrc": "Abooooo/FGRC-SCD — MIT",
    "seed": "CryptoMind 自寫（scripts/moderation_dataset/seed/）",
    "ui": "CryptoMind 介面文字（web/js/i18n）",
}

# 公開資料集的危害類別 → 我們的細類；不在表裡的「不安全」類別（政治、版權、投資建議、髒話…）整行丟掉
NEMOTRON_BLOCK = {
    "Fraud/Deception": "scam",
    "Sexual (minor)": "sexual_minor",
    "Sexual": "sexual",
    "Suicide and Self Harm": "self_harm",
    "Threat": "threat",
    "Guns and Illegal Weapons": "weapons",
    "Controlled/Regulated Substances": "drugs",
    "Hate/Identity Hate": "hate",
    "Harassment": "harassment",
    "PII/Privacy": "privacy",
    "Violence": "violence",
    "Criminal Planning/Confessions": "crime",
    "Illegal Activity": "crime",
    "Malware": "crime",
}
POLYGUARD_BLOCK = {
    "S4": "sexual_minor", "S12": "sexual", "S3": "sexual", "S11": "self_harm", "S9": "weapons",
    "S10": "hate", "S7": "privacy", "S1": "violence", "S2": "crime",
}
POLYGUARD_LANG = {"Chinese": "zh", "Russian": "ru", "English": "en"}
# v4 只讀第一個分片；v5 為了多收 sexual_minor 全部讀（其他類別照 QUOTA 抽，數量不變）
POLYGUARD_SHARDS = 8

# v5：v4 對「涉及未成年的性內容」只擋 60%（中文 52%）——訓練只抽到 796 則、考題只有 7 則，所以 benchmark
# 沒量到（DANNY 2026-10-02 發文實測放行）。這類不受 QUOTA 限制全部收，另外按原文 hash 固定切一份當獨立考題。
# 提到兒少的正常內容（育兒、教育、兒保宣導、新聞）也收當反例，免得學成「提到小孩就擋」。
# _MINOR_TERMS 只用來從公開資料挑反例，不是線上判斷規則。
MINOR_KIND = "sexual_minor"
MINOR_OK_KIND = "minor_topic_ok"
MINOR_TEST_SHARE = 0.15
MINOR_OK_TRAIN = 1500
MINOR_TEST_CAP = 400  # 每邊考題上限（hash 最小的先收，換版也是同一份）
_MINOR_TERMS = re.compile(
    r"兒童|儿童|孩子|小孩|小朋友|青少年|未成年|少女|少年|幼兒|幼儿|幼童|學生|学生|女兒|女儿|兒子|儿子|國中|初中|高中生|小學|小学"
    r"|\b(child|children|kids?|teens?|teenagers?|minors?|underage|daughters?|sons?|students?|boys?|girls?)\b"
    r"|ребен|ребён|дети|детей|детям|подрост|несовершеннолет|школьн|дочь|дочери|сын",
    re.IGNORECASE,
)

# 訓練池每個（來源, 標籤）抽多少；過與不過大致各半
QUOTA = {
    ("nemotron_zh", 1): 4500, ("nemotron_zh", 0): 3500,
    ("nemotron_en", 1): 1500, ("nemotron_en", 0): 1200,
    ("polyguard_zh", 1): 1500, ("polyguard_zh", 0): 1500,
    ("polyguard_ru", 1): 1200, ("polyguard_ru", 0): 1200,
    ("polyguard_en", 1): 500, ("polyguard_en", 0): 500,
    # COLD 有些「抱怨地域黑」也標冒犯，跟「談歧視≠歧視」的反例打架，只取一部分
    ("cold", 1): 800, ("cold", 0): 800,
    ("fgrc", 1): 3000, ("fgrc", 0): 1500,
    # v2：介面文字是論壇風格的「過」，v1 放 600 句讓論壇風格的過／不過約 19:1，模型學成「像論壇就放行」
    ("ui", 0): 250,
}
TEST_QUOTA = {("nemotron_zh_test", 1): 150, ("nemotron_zh_test", 0): 100, ("fgrc_test", 1): 60, ("fgrc_test", 0): 40}
VAL_SHARE = 0.08
OBFUSCATE_SHARE = 0.08
OBFUSCATE_SHARE_SEED = 0.3  # 自寫的論壇風格資料多做一些穿插符號版本
# v2：論壇風格的「不過」太少（v1 測試：本站詐騙只擋 28%），自寫的有害貼文在訓練集重複幾次加權
# v4：×3 時（v3）論壇風格的過／不過約 2:1，模型對「沾到錢／詐騙話題」過度自信（資產配置文打 1.000）→ ×2
SEED_REPEAT = {"seed:scam_domain": 2, "seed:forum_positives": 2, "seed:scam_more": 2}
# 跟考題只差幾個字也算洩題（v3 的俄文詐騙範例跟考題只差結尾）：字元 3-gram Jaccard 超過就排除
NEAR_DUP_JACCARD = 0.5
TRAD_SHARE = 0.7

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_FILLERS = ["＠", "@", ".", "★", " ", "\u200b", "·", "_", "*", "🔥", "💰", "#", "~"]


def fetch(name: str, file: str | None = None) -> Path:
    from huggingface_hub import hf_hub_download

    repo, default = SOURCES[name]
    file = file or default
    return Path(hf_hub_download(repo, file, repo_type="dataset", local_dir=str(CACHE / repo.replace("/", "__"))))


def clean(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def keep(text: str, max_len: int = 400) -> bool:
    if not text or text == "REDACTED":
        return False
    return (4 if _CJK.search(text) else 8) <= len(text) <= max_len


def key(text: str) -> str:
    """去重用：全形半形、大小寫、標點空白都不算差異"""
    t = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[\W_]+", "", t)


def row(text, label, lang, source, kind):
    return {"text": text, "label": label, "lang": lang, "source": source, "kind": kind}


# ── 各來源 ────────────────────────────────────────────────────────────────────


def load_nemotron(name: str, lang: str):
    for line in open(fetch(name), encoding="utf-8"):
        r = json.loads(line)
        text = clean(r["prompt"])
        if not keep(text):
            continue
        if r["prompt_label"] == "safe":
            yield row(text, 0, lang, name, "normal")
            continue
        cats = [c.strip() for c in (r.get("violated_categories") or "").split(",")]
        kind = nemotron_kind(cats, has_response=bool((r.get("response") or "").strip()))
        if kind:
            yield row(text, 1, lang, name, kind)


def nemotron_kind(cats, has_response: bool):
    """依表的優先順序取細類；None＝範圍外（整行丟掉）。
    有附 AI 回答時 violated_categories 可能是看回答標的：sexual_minor 這時不可信，改成下一個類別或 unclear"""
    kinds = [NEMOTRON_BLOCK[c] for c in NEMOTRON_BLOCK if c in cats]
    if not kinds:
        return None
    if has_response and kinds[0] == MINOR_KIND:
        rest = [k for k in kinds if k != MINOR_KIND]
        return rest[0] if rest else "unclear"
    return kinds[0]


def load_polyguard():
    import pandas as pd

    for shard in range(POLYGUARD_SHARDS):
        file = f"data/train-{shard:05d}-of-{POLYGUARD_SHARDS:05d}.parquet"
        df = pd.read_parquet(fetch("polyguard", file), columns=["prompt", "prompt_harm_label", "prompt_safety_categories", "metadata"])
        yield from _polyguard_rows(df)


def _polyguard_rows(df):
    for prompt, harm, cats, meta in df.itertuples(index=False):
        lang = POLYGUARD_LANG.get((meta or {}).get("language"))
        text = clean(prompt)
        if not lang or not keep(text):
            continue
        name = f"polyguard_{lang}"
        if harm == "no":
            yield row(text, 0, lang, name, "normal")
            continue
        codes = (cats or "").split()
        kinds = [POLYGUARD_BLOCK[c] for c in POLYGUARD_BLOCK if c in codes]
        if harm == "yes" and kinds:
            yield row(text, 1, lang, name, kinds[0])


def load_cold():
    import pandas as pd

    df = pd.read_csv(fetch("cold"))
    for text, label in zip(df["TEXT"], df["label"]):
        text = clean(text)
        if keep(text):
            # 針對族群／性別／地區：冒犯的算仇恨；同主題但不冒犯的是很好的反例
            yield row(text, int(label), "zh", "cold", "hate" if int(label) == 1 else "group_topic_ok")


def load_fgrc(member: str, name: str):
    z = zipfile.ZipFile(fetch("fgrc"))
    for r in json.loads(z.read(member)):
        text = clean(r.get("文本"))
        if not keep(text):
            continue
        normal = r.get("风险类别") == "无风险"
        out = row(text, 0 if normal else 1, "zh", name, "normal_sms" if normal else "scam")
        out["_case"] = r.get("案件编号")
        yield out


def load_ui_strings():
    """我們自己的介面文字（四語系的句子），不少是防詐提醒，剛好是反例。"""
    def walk(d):
        if isinstance(d, dict):
            for v in d.values():
                yield from walk(v)
        elif isinstance(d, str):
            yield d

    for lang, file in (("zh-TW", "zh-TW.json"), ("en", "en.json"), ("ru", "ru.json")):
        for s in walk(json.load(open(REPO / "web" / "js" / "i18n" / file, encoding="utf-8"))):
            s = clean(re.sub(r"<[^>]+>|\{\{\w+\}\}", "", s))
            if len(s) >= 15 and keep(s):
                yield row(s, 0, lang, "ui", "ui_text")


def load_seed(file: str):
    path = SEED / file
    if not path.exists():
        print(f"  （{file} 不存在，略過）")
        return
    for line in open(path, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            yield row(clean(r["text"]), int(r["label"]), r.get("lang", "zh-TW"), f"seed:{path.stem}", r.get("kind", ""))


def load_fixture():
    """tests/fixtures/moderation_samples.json（線上規則的校正集）→ 考題"""
    for s in json.load(open(REPO / "tests" / "fixtures" / "moderation_samples.json", encoding="utf-8")):
        text = clean(f"{s['title']}\n{s['content']}")
        yield row(text, 0 if s["expect"] == "pass" else 1, "zh-TW", "fixture", "calibration")


# ── 組裝 ────────────────────────────────────────────────────────────────────


def to_traditional(rows, rng):
    from opencc import OpenCC

    cc = OpenCC("s2twp")
    for r in rows:
        if r["lang"] == "zh":
            if rng.random() < TRAD_SHARE:
                r["text"], r["lang"] = cc.convert(r["text"]), "zh-TW"
            else:
                r["lang"] = "zh-CN"
    return rows


# v3：公開資料的「不過」按細類平均抽。v2 照來源整批抽，犯罪／仇恨佔大宗，自殘只有 2.9%、毒品 4%，
# 測試時這幾類只擋下 14～25%（Nemotron 中文還有自殘 1,608、毒品 2,588 題沒用到）
BALANCE_BY_KIND = {("nemotron_zh", 1), ("nemotron_en", 1), ("polyguard_zh", 1), ("polyguard_ru", 1), ("polyguard_en", 1)}


def balanced(items, n, rng):
    """各細類輪流抽，少的類別抽完就讓給其他類，總數 n"""
    by_kind = collections.defaultdict(list)
    for r in items:
        by_kind[r["kind"]].append(r)
    for group in by_kind.values():
        rng.shuffle(group)
    picked, kinds = [], sorted(by_kind)
    while len(picked) < n and kinds:
        for k in list(kinds):
            if not by_kind[k]:
                kinds.remove(k)
                continue
            picked.append(by_kind[k].pop())
            if len(picked) == n:
                break
    return picked


def sample(rows, quota, rng):
    by = collections.defaultdict(list)
    for r in rows:
        by[(r["source"], r["label"])].append(r)
    picked = []
    for k, items in by.items():
        if k not in quota:
            continue
        if k in BALANCE_BY_KIND:
            picked += balanced(items, quota[k], rng)
        else:
            rng.shuffle(items)
            picked += items[: quota[k]]
    return picked


def dedupe(rows):
    """同一句只留一筆；標籤互相衝突的整句丟掉"""
    labels = collections.defaultdict(set)
    for r in rows:
        labels[key(r["text"])].add(r["label"])
    seen, out, conflicts = set(), [], 0
    for r in rows:
        k = key(r["text"])
        if len(labels[k]) > 1:
            conflicts += 1
            continue
        if k and k not in seen:
            seen.add(k)
            out.append(r)
    return out, conflicts


def obfuscate(text: str, rng) -> str:
    """字元間穿插符號：整句或其中一段的中文字（或英文字母）之間塞同一個符號"""
    filler = rng.choice(_FILLERS)
    chars = list(text)
    start = rng.randrange(len(chars))
    span = range(start, min(len(chars), start + rng.randint(4, 16))) if rng.random() < 0.6 else range(len(chars))
    out = []
    for i, ch in enumerate(chars):
        out.append(ch)
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if i in span and ((_CJK.match(ch) and _CJK.match(nxt)) or (ch.isascii() and ch.isalpha() and nxt.isascii() and nxt.isalpha())):
            out.append(filler)
    return "".join(out)


def _grams(text: str) -> set:
    k = key(text)
    return {k[i : i + 3] for i in range(max(1, len(k) - 2))}


def drop_near_test(rows, test):
    """跟任一題考題字元 3-gram Jaccard ≥ NEAR_DUP_JACCARD 的排除（倒排索引找候選，不用兩兩比）"""
    test_grams = [_grams(t["text"]) for t in test]
    index = collections.defaultdict(list)
    for i, g in enumerate(test_grams):
        for x in g:
            index[x].append(i)
    kept, dropped = [], 0
    for r in rows:
        g = _grams(r["text"])
        shared = collections.Counter(i for x in g for i in index.get(x, ()))
        near = any(n / len(g | test_grams[i]) >= NEAR_DUP_JACCARD for i, n in shared.most_common(5))
        if near:
            dropped += 1
        else:
            kept.append(r)
    return kept, dropped


def _minor_hash(r) -> int:
    # 跟 split_train_val 用不同的鹽：同一個 hash 的話，切走考題後驗證集會一則這類都沒有
    return int(hashlib.sha1(("minor:" + key(r["text"])).encode()).hexdigest(), 16) % 1000


def split_minor(pool):
    """回傳 (訓練用不過, 訓練用反例, 考題)。在繁簡轉換前用原文 hash 切：之後各版考同一份，也不會漏進訓練"""
    harmful = [r for r in pool if r["label"] == 1 and r["kind"] == MINOR_KIND]
    benign = [r for r in pool if r["label"] == 0 and _MINOR_TERMS.search(r["text"])]
    test, train_harmful, train_benign = [], [], []
    for rows, train_out, source, kind in ((harmful, train_harmful, "minor_test", MINOR_KIND),
                                          (benign, train_benign, "minor_topic_test", MINOR_OK_KIND)):
        # hash 只取到千分位會撞，加上原文當第二排序鍵：輸入順序不同，選出的考題也一樣
        held = sorted((r for r in rows if _minor_hash(r) < MINOR_TEST_SHARE * 1000), key=lambda r: (_minor_hash(r), key(r["text"])))
        held_keys = {key(r["text"]) for r in held}
        test += [{**r, "source": source, "kind": kind} for r in held[:MINOR_TEST_CAP]]
        train_out += [{**r, "kind": kind} if r["label"] == 0 else r for r in rows if key(r["text"]) not in held_keys]
    return train_harmful, train_benign, test


def split_train_val(rows):
    train, val = [], []
    for r in rows:
        h = int(hashlib.sha1(key(r["text"]).encode()).hexdigest(), 16) % 1000
        (val if h < VAL_SHARE * 1000 else train).append(r)
    return train, val


def finalize(rows):
    out = []
    for r in rows:
        r = {k: v for k, v in r.items() if not k.startswith("_")}
        r["id"] = hashlib.sha1(f"{r['source']}|{r['text']}".encode()).hexdigest()[:12]
        out.append({k: r[k] for k in ("id", "text", "label", "lang", "source", "kind")})
    return out


def write_jsonl(path: Path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def table(rows, field):
    c = collections.Counter((r[field], r["label"]) for r in rows)
    names = sorted({k for k, _ in c})
    lines = [f"| {field} | 過（0） | 不過（1） |", "|---|---:|---:|"]
    lines += [f"| {n} | {c[(n, 0)]} | {c[(n, 1)]} |" for n in names]
    return "\n".join(lines)


def card(train, val, test, conflicts, leaked):
    parts = [
        f"# CryptoMind 內容檢查資料集 {VERSION}",
        "",
        "論壇貼文該不該擋的二元資料集，給 Laya（convaiinnovations/laya multilingual）LoRA 微調用。",
        "",
        "- **label 1（不過）**：腥羶色（色情、血腥暴力）、詐騙詐欺、其他違法事項（犯罪教學、毒品槍械、"
        "人頭帳戶／卡料買賣、威脅、仇恨、騷擾、肉搜、鼓勵自殘、非法博弈）。",
        "- **label 0（過）**：一般討論，以及「嚇人但正常」的貼文（防詐提醒、被害求助、新聞、行情俚語…）。",
        "- 公開資料集裡我們範圍外的「不安全」類別（政治、版權、未經授權的建議、輕微髒話…）整行丟掉。",
        f"- 去重後標籤衝突丟掉 {conflicts} 筆；跟考題重複而從訓練移除 {leaked} 筆。",
        f"- 訓練集約 {int(OBFUSCATE_SHARE * 100)}% 另做穿插符號版本（source 結尾 +obf），過與不過兩邊都有。",
        "",
        "| 分割 | 筆數 | 過（0） | 不過（1） |",
        "|---|---:|---:|---:|",
    ]
    for name, rows in (("train", train), ("val", val), ("test", test)):
        n1 = sum(r["label"] for r in rows)
        parts.append(f"| {name} | {len(rows)} | {len(rows) - n1} | {n1} |")
    parts += ["", "## train 來源", "", table(train, "source"), "", "## train 語言", "", table(train, "lang"),
              "", "## train 細類（kind）", "", table(train, "kind"), "", "## test 來源", "", table(test, "source"),
              "", "## 授權與出處（CC BY 要在模型卡註明）", ""]
    parts += [f"- {v}" for v in LICENSES.values()]
    parts += ["", "## 欄位", "", "`id`、`text`、`label`（1＝不過）、`lang`（zh-TW／zh-CN／en／ru）、`source`、`kind`（細類，選擇題才用得到）。", ""]
    return "\n".join(parts)


def main():
    rng = random.Random(RNG_SEED)
    OUT.mkdir(parents=True, exist_ok=True)

    print("讀公開資料集…")
    pool = []
    pool += list(load_nemotron("nemotron_zh", "zh"))
    pool += list(load_nemotron("nemotron_en", "en"))
    pool += list(load_polyguard())
    pool += list(load_cold())
    pool += list(load_fgrc("message/finetuning_initial.json", "fgrc"))
    pool += list(load_ui_strings())
    minor_pool = pool + [r for r in load_nemotron("nemotron_zh_test", "zh") if r["kind"] == MINOR_KIND]
    minor_harmful, minor_benign, minor_test = split_minor(minor_pool)
    held = {key(r["text"]) for r in minor_test}
    picked = [r for r in sample(pool, QUOTA, rng) if key(r["text"]) not in held]
    have = {key(r["text"]) for r in picked}
    picked += [r for r in minor_harmful if key(r["text"]) not in have]
    rng.shuffle(minor_benign)
    picked += [r for r in minor_benign if key(r["text"]) not in have][:MINOR_OK_TRAIN]
    print(f"  sexual_minor 訓練 {len(minor_harmful)}、兒少主題反例 {min(len(minor_benign), MINOR_OK_TRAIN)}／{len(minor_benign)}、"
          f"獨立考題 {len(minor_test)}")

    print("讀考題…")
    if FROZEN_TEST.exists():
        test = [json.loads(line) for line in open(FROZEN_TEST, encoding="utf-8") if line.strip()]
    else:
        test = sample_test(picked, rng)
    test += dedupe(to_traditional(minor_test, random.Random(RNG_SEED + 5)))[0]  # 自己的 rng：換版考題不變

    seeds = list(load_seed("scam_domain.jsonl")) + list(load_seed("forum_positives.jsonl")) + list(load_seed("scam_more.jsonl"))
    seeds += list(load_seed("hard_negatives.jsonl")) + list(load_seed("hard_negatives_minor.jsonl"))
    rows = to_traditional(picked + seeds, rng)
    rows, conflicts = dedupe(rows)
    test_keys = {key(r["text"]) for r in test}
    leaked = sum(1 for r in rows if key(r["text"]) in test_keys)
    rows = [r for r in rows if key(r["text"]) not in test_keys]
    rows, near = drop_near_test(rows, test)
    print(f"  跟考題高度相似而排除 {near} 筆")
    finish(rows, test, conflicts, leaked + near, rng)


def sample_test(picked, rng):
    """第一次（還沒有固定考題時）抽考題；之後都用 FROZEN_TEST"""
    used_cases = {r.get("_case") for r in picked if r["source"] == "fgrc"}
    test_pool = list(load_nemotron("nemotron_zh_test", "zh"))
    # FGRC 的 eval 跟 finetuning 有同一案件的變體，訓練抽到的案件不放進考題
    test_pool += [r for r in load_fgrc("message/eval_initial.json", "fgrc_test") if r.get("_case") not in used_cases]
    test = sample(test_pool, TEST_QUOTA, rng)
    test += list(load_seed("test_heldout.jsonl")) + list(load_seed("test_domain_block.jsonl"))
    test += list(load_seed("test_bake50.jsonl")) + list(load_fixture())
    test, _ = dedupe(to_traditional(test, rng))
    return test


def finish(rows, test, conflicts, leaked, rng):
    train, val = split_train_val(rows)
    train += [dict(r) for r in train for _ in range(SEED_REPEAT.get(r["source"], 1) - 1)]
    extra = []
    for r in train:
        share = OBFUSCATE_SHARE_SEED if r["source"].startswith("seed:") else OBFUSCATE_SHARE
        if rng.random() < share:
            t = obfuscate(r["text"], rng)
            if t != r["text"]:
                extra.append({**r, "text": t, "source": r["source"] + "+obf"})
    train += extra
    rng.shuffle(train)

    train, val, test = finalize(train), finalize(val), finalize(test)
    for name, data in (("train", train), ("val", val), ("test", test)):
        write_jsonl(OUT / f"{name}.jsonl", data)
    (OUT / "DATASET_CARD.md").write_text(card(train, val, test, conflicts, leaked), encoding="utf-8")
    zpath = OUT / f"cryptomind-moderation-{VERSION}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("train.jsonl", "val.jsonl", "test.jsonl", "DATASET_CARD.md"):
            z.write(OUT / name, name)
        here = Path(__file__).resolve().parent
        z.write(here / "train.py", "train.py")  # Kaggle 上傳一個檔就有資料＋訓練腳本
        z.write(here / "train_llm.py", "train_llm.py")  # v7：YuFeng 版（門檻與達標線 import train.py）
    for name, data in (("train", train), ("val", val), ("test", test)):
        n1 = sum(r["label"] for r in data)
        print(f"  {name}: {len(data)}（過 {len(data) - n1}／不過 {n1}）")
    print(f"  標籤衝突丟掉 {conflicts}、跟考題重複移除 {leaked}；輸出 {OUT}")


if __name__ == "__main__":
    main()
