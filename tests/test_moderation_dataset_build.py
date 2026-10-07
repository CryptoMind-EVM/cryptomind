"""內容檢查微調資料集的整理邏輯（scripts/moderation_dataset/build.py，2026-10-01）。

只測不用下載、不用 OpenCC 的純函式：範圍外的「不安全」類別要丟掉、標籤衝突要丟掉、
穿插符號版本不改標籤也不碰英數以外的字。
"""

from __future__ import annotations

import importlib.util
import json
import random
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "scripts" / "moderation_dataset" / "seed"


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("moderation_dataset_build", REPO / "scripts" / "moderation_dataset" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_out_of_scope_unsafe_categories_are_not_labelled_block(build):
    """「未經授權的建議」（投資意見）、政治、髒話在論壇是正常內容：不能標成不過"""
    for category in ("Unauthorized Advice", "Political/Misinformation/Conspiracy", "Profanity", "Copyright/Trademark/Plagiarism"):
        assert category not in build.NEMOTRON_BLOCK
    for code in ("S5", "S6", "S8", "S13", "S14"):
        assert code not in build.POLYGUARD_BLOCK
    assert build.NEMOTRON_BLOCK["Fraud/Deception"] == "scam"


def test_dedupe_drops_conflicting_labels(build):
    rows = [
        build.row("老師帶單穩賺", 1, "zh-TW", "a", "scam"),
        build.row("老師帶單，穩賺！", 0, "zh-TW", "b", "normal"),  # 標點不同還是同一句
        build.row("BTC 今天跌了", 0, "zh-TW", "a", "normal"),
        build.row("btc今天跌了", 0, "zh-TW", "b", "normal"),
    ]
    out, conflicts = build.dedupe(rows)
    assert conflicts == 2
    assert [r["text"] for r in out] == ["BTC 今天跌了"]


def test_obfuscate_only_inserts_between_letters_or_hanzi(build):
    rng = random.Random(0)
    for _ in range(50):
        text = "請將虛擬貨幣轉到我帳戶 send USDT now 100%"
        out = build.obfuscate(text, rng)
        assert build.key(out) == build.key(text), "拿掉穿插的符號後要跟原文一樣"
        for ch in "0123456789%":
            assert out.count(ch) == text.count(ch), "數字和符號不動"


def test_seed_files_are_valid():
    for path in SEED.glob("*.jsonl"):
        texts = set()
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            r = json.loads(line)
            assert r["label"] in (0, 1), f"{path.name}:{n}"
            assert r["lang"] in ("zh-TW", "zh-CN", "en", "ru"), f"{path.name}:{n}"
            assert r["text"].strip() and r["text"] not in texts, f"{path.name}:{n} 空白或重複"
            texts.add(r["text"])


# ── 訓練腳本的門檻與達標判斷（scripts/moderation_dataset/train.py；torch 只在 main 裡才載入）──


@pytest.fixture(scope="module")
def train():
    spec = importlib.util.spec_from_file_location("moderation_train", REPO / "scripts" / "moderation_dataset" / "train.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_threshold_keeps_false_blocks_under_the_limit(train):
    rng = random.Random(1)
    probs = [rng.random() for _ in range(1000)]
    labels = [rng.randint(0, 1) for _ in range(1000)]
    t = train.pick_threshold(probs, labels, 0.02)
    rows = [{"label": y} for y in labels]
    _, false_block, _ = train.rates(probs, rows, t)
    assert false_block <= 0.02
    # 門檻再低一點點就會超過：挑的是允許範圍內擋最多的
    assert train.rates(probs, rows, t - 2e-6)[1] > 0.02


def test_acceptance_needs_every_line(train):
    good = {"block_rate": 0.95, "false_block_rate": 0.01,
            "by_source": {"fgrc_test|1": [50, 60], "seed:test_domain_block|1": [24, 25], "seed:test_heldout|0": [2, 160],
                          "minor_test|1": [370, 400], "minor_topic_test|0": [8, 400]}}
    assert train.acceptance(good) is True
    weak_source = {**good, "by_source": {**good["by_source"], "fgrc_test|1": [40, 60]}}
    assert train.acceptance(weak_source) is False, "整體夠高但某個來源低於 80% 也不算達標"
    noisy = {**good, "by_source": {**good["by_source"], "seed:test_heldout|0": [4, 160]}}
    assert train.acceptance(noisy) is False


def test_minor_content_has_its_own_stricter_bar(train):
    """v5：涉及未成年的性內容擋下要 ≥90%（整體來源線只要 80%）；提到兒少的正常內容誤擋 ≤3%；考題缺這兩份就不算達標"""
    base = {"block_rate": 0.95, "false_block_rate": 0.01,
            "by_source": {"seed:test_heldout|0": [2, 160], "minor_test|1": [370, 400], "minor_topic_test|0": [8, 400]}}
    assert train.acceptance(base) is True
    assert train.acceptance({**base, "by_source": {**base["by_source"], "minor_test|1": [340, 400]}}) is False, "85% 不夠"
    assert train.acceptance({**base, "by_source": {**base["by_source"], "minor_topic_test|0": [20, 400]}}) is False
    missing = {k: v for k, v in base["by_source"].items() if not k.startswith("minor")}
    assert train.acceptance({**base, "by_source": missing}) is False


def test_minor_test_split_is_fixed_and_kept_out_of_training(build):
    """v5：考題用原文 hash 切（不受抽樣順序影響），訓練拿不到；提到兒少的正常內容當反例"""
    rows = [build.row(f"minor harmful sample {i}", 1, "en", "s", "sexual_minor") for i in range(400)]
    rows += [build.row(f"my daughter goes to school {i}", 0, "en", "s", "normal") for i in range(400)]
    rows += [build.row(f"BTC price {i}", 0, "en", "s", "normal") for i in range(50)]
    harmful, benign, test = build.split_minor(rows)
    harmful2, benign2, test2 = build.split_minor(list(reversed(rows)))
    assert [r["text"] for r in test] == [r["text"] for r in test2], "順序不同考題也一樣"
    test_keys = {build.key(r["text"]) for r in test}
    assert not test_keys & {build.key(r["text"]) for r in harmful + benign}
    assert {r["source"] for r in test} == {"minor_test", "minor_topic_test"}
    assert 30 < sum(r["label"] for r in test) < 100, "約 15%"
    assert all("BTC" not in r["text"] for r in benign + test), "沒提到兒少的正常文不算反例"
    assert {r["kind"] for r in benign} == {"minor_topic_ok"}


def test_balanced_sampling_spreads_across_kinds(build):
    """v3：公開資料的「不過」按細類輪流抽，少的類別抽完讓給其他類"""
    rows = [build.row(f"crime {i}", 1, "zh", "s", "crime") for i in range(100)]
    rows += [build.row(f"self harm {i}", 1, "zh", "s", "self_harm") for i in range(5)]
    rows += [build.row(f"drugs {i}", 1, "zh", "s", "drugs") for i in range(40)]
    picked = build.balanced(rows, 60, random.Random(0))
    kinds = [r["kind"] for r in picked]
    assert len(picked) == 60
    assert kinds.count("self_harm") == 5, "少的類別全收"
    assert abs(kinds.count("drugs") - kinds.count("crime")) <= 1, "剩下的兩類平均分"


def test_near_duplicates_of_test_items_are_dropped(build):
    """v4：只差幾個字的（「…пишите в личку」vs 考題「…ссылка в профиле」）也算洩題，不能留在訓練／驗證"""
    test = [build.row("Инвестиции без риска, 5% в день, вывод в любое время, ссылка в профиле", 1, "ru", "t", "x")]
    rows = [
        build.row("Инвестиции без риска, 5% в день, вывод в любое время, пишите в личку", 1, "ru", "s", "x"),
        build.row("BTC 今天跌破 60000，短線偏空", 0, "zh-TW", "s", "normal"),
    ]
    kept, dropped = build.drop_near_test(rows, test)
    assert [r["text"] for r in kept] == ["BTC 今天跌破 60000，短線偏空"] and dropped == 1


def test_nemotron_minor_label_only_trusted_without_a_response(build):
    """v6：有附 AI 回答時「Sexual (minor)」可能是看回答標的（中文這種只有 8% 提到兒少）→ 不算 sexual_minor"""
    assert build.nemotron_kind(["Sexual (minor)"], has_response=False) == "sexual_minor"
    assert build.nemotron_kind(["Sexual (minor)"], has_response=True) == "unclear", "提問仍是不過，只是細類不明"
    assert build.nemotron_kind(["Sexual (minor)", "Sexual"], has_response=True) == "sexual"
    assert build.nemotron_kind(["Fraud/Deception"], has_response=True) == "scam", "其他類別不受影響"
    assert build.nemotron_kind(["Political/Misinformation/Conspiracy"], has_response=False) is None



# ── v7：微調 YuFeng-XGuard-Reason-0.6B（scripts/moderation_dataset/train_llm.py；torch 只在 main 裡才載入）──

YUFENG_CODES = {"sec", "pc", "dc", "dw", "pi", "ec", "ac", "def", "ti", "cy", "ph", "mh", "se", "sci", "pp", "cs",
                "acc", "mc", "ha", "ps", "ter", "sd", "ext", "fin", "med", "law", "cm", "ma", "md"}  # 模型的 id2risk


@pytest.fixture(scope="module")
def train_llm():
    spec = importlib.util.spec_from_file_location("moderation_train_llm", REPO / "scripts" / "moderation_dataset" / "train_llm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kind_to_code_uses_real_yufeng_categories(train_llm):
    """照官方格式練第一個 token＝類別代碼：對應的代碼一定要是模型認得的；過＝sec 不在對應表裡"""
    assert set(train_llm.KIND_TO_CODE.values()) <= YUFENG_CODES - {"sec"}
    assert train_llm.KIND_TO_CODE["sexual_minor"] == "ma", "涉及未成年的性內容＝未成年人侵害"
    assert train_llm.KIND_TO_CODE["scam"] == train_llm.KIND_TO_CODE["invest_scam"] == "ec", "詐騙＝經濟犯罪"
    for unclear in ("crime", "violence", "unclear", "calibration"):
        assert unclear not in train_llm.KIND_TO_CODE, "類別不明確的用二元損失，不硬塞代碼"


def test_post_tokens_trim_and_keep_head_and_tail(train_llm):
    """template 會 trim 內容；長文跟線上 detector 一樣只留開頭＋結尾"""

    class Tok:
        def __call__(self, text, add_special_tokens=False):
            return type("E", (), {"input_ids": [ord(c) for c in text]})()

    assert train_llm.post_ids(Tok(), "  hi \n", " ") == [ord(" "), ord("h"), ord("i")]
    long = "x" * 2000
    ids = train_llm.post_ids(Tok(), long, "")
    assert len(ids) == train_llm.FIRST_TOKENS + train_llm.LAST_TOKENS
