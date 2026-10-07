"""明確的詐騙句型＋字元間穿插符號的還原。

2026-10-02 起論壇發文／留言只用微調模型判斷（service.py），這裡的句型只剩防詐回報
（core/validators/content_filter.py）在用；論壇那邊只用 normalize／obfuscated（還原穿插符號
給模型再看一次）和 leaked_secret（貼出自己的助記詞／私鑰）。

句型前面有否定詞（不要、千萬別、never…）就不算，「任何人跟你要助記詞都不要給」這種提醒不會被擋。
"""

from __future__ import annotations

import re
import unicodedata
from typing import List

from core.validators.pii_scrubber import (
    _detect_and_scrub_mnemonic,
    _detect_and_scrub_private_keys,
)

# ── 還原「字元間穿插符號」：助@記@詞、s.e.e.d、L I N E、零寬字元、全形、混西里爾字母 ──
# 規則和詐騙鉤子對原文和還原後的文字各比一次，任一命中就算。只給比對用，存的、給模型看的還是原文。
_CJK = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_GAP = r"(?:[^\w\n]|_)"  # 被塞在字和字中間的東西：符號、標點、空白、表情（換行本來就是斷句，不算）
# 斷句標點和引號不當成穿插（全形經 NFKC 多半變半形）：「下方加「我已閱讀」接起來會變「加我」
_PUNCT = "，。、；：？！「」『』（）《》〈〉【】〔〕…‥—–,;:?!()[]{}<>\"'“”‘’"
_FILLER = rf"(?:[^\w\n{re.escape(_PUNCT)}]|_)"
_CJK_GAP = re.compile(rf"(?<=[{_CJK}]){_FILLER}+(?=[{_CJK}0-9])|(?<=[0-9]){_FILLER}+(?=[{_CJK}])")
# 用標點也一樣：單字之間一直插同一個東西（「助,記,詞」）
_CJK_INTERLEAVED = re.compile(rf"[{_CJK}](?P<gap>{_GAP}{{1,3}})[{_CJK}](?:(?P=gap)[{_CJK}])+")
_SPACED_LETTERS = re.compile(rf"(?<![A-Za-z0-9])[A-Za-z0-9](?:{_GAP}{{1,3}}[A-Za-z0-9](?![A-Za-z0-9])){{2,}}")
_GAP_ONE = re.compile(_GAP)
_INVISIBLE = re.compile("[\u115f\u1160\u3164\uffa0]")  # 韓文填充字：看不見但不是格式字元
_LATIN_OR_LOOKALIKE = re.compile(r"[A-Za-z\u0370-\u03ff\u0400-\u04ff]+")
# 左邊是長得跟英文字母一樣的西里爾／希臘字母（sееd 的 е 是西里爾字母）
_LOOKALIKES = str.maketrans(
    {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d",
        "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X",
        "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t",
        "Α": "A", "Β": "B", "Ε": "E", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Χ": "X",
    }
)


def _unlookalike(m: re.Match) -> str:
    word = m.group(0)
    # 只換「英文字裡混了西里爾／希臘字母」的字；整個字都是俄文的不動
    if re.search(r"[A-Za-z]", word) and re.search(r"[\u0370-\u04ff]", word):
        return word.translate(_LOOKALIKES)
    return word


def _join_letters(m: re.Match) -> str:
    run = m.group(0)
    # 插的是符號（s@e@e@d p@h@r@a@s@e）：拿掉符號、單字之間的空白留著，模型和 \b 規則才讀得懂；
    # 整串都用空白隔開（s e e d）分不出單字，空白全拿掉
    if re.search(r"[^\w\s]|_", run):
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s]|_", "", run))
    return _GAP_ONE.sub("", run)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")  # 零寬空白、方向控制字元…
    text = _INVISIBLE.sub("", text)
    text = _LATIN_OR_LOOKALIKE.sub(_unlookalike, text)
    text = _SPACED_LETTERS.sub(_join_letters, text)
    text = _CJK_GAP.sub("", text)
    return _CJK_INTERLEAVED.sub(lambda m: m.group(0).replace(m.group("gap"), ""), text)


def obfuscated(text: str) -> bool:
    """有沒有字元間穿插（助＠記＠詞、L I N E、零寬字元…）：還原後跟「只做全形轉半形」不一樣才算。
    決定要不要多問一次模型——不能直接跟原文比，中文貼文幾乎都有全形標點，會變成每篇都問兩次。"""
    text = text or ""
    return normalize(text) != unicodedata.normalize("NFKC", text)


def _variants(text: str) -> tuple:
    normalized = normalize(text)
    return (text,) if normalized == text else (text, normalized)


_SECRET = r"(助記詞|助记词|註記詞|注记词|恢復詞|恢复词|私鑰|私钥|12\s*個?\s*(單字|单词|英文字)|24\s*個?\s*(單字|单词|英文字))"
_SECRET_EN = r"(seed\s*phrase|recovery\s*phrase|secret\s*phrase|mnemonic|private\s*key|1[2]-?\s*word|24-?\s*word)"
# 中文句子裡夾英文關鍵字（「請提供你的 seed phrase」）
_SECRET_ANY = r"(" + _SECRET + r"|(?i:" + _SECRET_EN + r"))"

# 公開版只附最基本的樣式作為示範；正式站使用更完整、持續更新的規則集（防繞過，不公開）。
_ASK_SECRET = [
    re.compile(r"(提供|輸入|输入|貼上|粘贴|傳給我|发给我|發給我|給我|告訴我|告诉我).{0,15}" + _SECRET_ANY),
    re.compile(_SECRET_ANY + r".{0,10}(傳給我|发给我|發給我|給我|告訴我|告诉我)"),
    re.compile(r"\b(send|share|enter|provide|paste|dm|give|tell)\b.{0,30}\b" + _SECRET_EN, re.IGNORECASE),
]

_ASK_PAYMENT = [
    re.compile(r"\b(send|transfer|wire)\b.{0,20}\b(to\s+)?my\s+(account|wallet|address)\b", re.IGNORECASE),
]

_DOUBLING = [
    re.compile(r"\bdouble\s+your\s+(eth|btc|crypto|coins?|money|usdt)\b", re.IGNORECASE),
]

# 「轉 0.1 ETH … 返還 0.2 ETH」：寫出轉多少、回多少，回的比轉的多才算（手續費退回不算）
_SEND_BACK_AMOUNTS = re.compile(
    r"(?:轉|转|發送|发送|打|send)\s*(\d+(?:\.\d+)?)\s*(ETH|BTC|USDT|USDC|SOL|BNB|U)\b"
    r".{0,40}?(?:返還|返还|退回|送回|返|receive|get)\s*(\d+(?:\.\d+)?)\s*(ETH|BTC|USDT|USDC|SOL|BNB|U)\b",
    re.IGNORECASE,
)


def _sends_back_more(text: str) -> bool:
    for m in _SEND_BACK_AMOUNTS.finditer(text):
        sent, sent_unit, back, back_unit = m.group(1), m.group(2), m.group(3), m.group(4)
        if sent_unit.lower() == back_unit.lower() and float(back) > float(sent):
            before = text[max(0, m.start() - _NEGATION_WINDOW) : m.start()]
            if not _NEGATION.search(before):
                return True
    return False


_NEGATION = re.compile(
    r"(不要|不會|不会|不能|別|别|勿|切勿|千萬|千万|絕不|绝不|絕對不|永遠不|從不|拒絕|沒有|没有|never|don'?t|do\s+not|won'?t|\bno\b|\bnot\b)",
    re.IGNORECASE,
)
_NEGATION_WINDOW = 12


def _hit(patterns, text: str) -> bool:
    for pattern in patterns:
        for m in pattern.finditer(text):
            before = text[max(0, m.start() - _NEGATION_WINDOW) : m.start()]
            if not _NEGATION.search(before) and not _NEGATION.search(m.group(0)):
                return True
    return False


def _rule_reasons(text: str) -> List[str]:
    reasons: List[str] = []
    if _detect_and_scrub_mnemonic(text.lower())[1] or _detect_and_scrub_private_keys(text)[1]:
        reasons.append("leaked_secret")
    if _hit(_ASK_SECRET, text):
        reasons.append("asks_secret")
    if _hit(_DOUBLING, text) or _sends_back_more(text):
        reasons.append("doubling")
    if _hit(_ASK_PAYMENT, text):
        reasons.append("asks_payment")
    return reasons


_REASON_ORDER = ("leaked_secret", "asks_secret", "doubling", "asks_payment")


def leaked_secret(text: str) -> bool:
    """內文貼了助記詞（12+ 個 BIP39 單字）或私鑰（旁邊有「私鑰」字樣的 64 hex）。
    論壇檢查 2026-10-02 起只用模型判斷，這個保留：不是判斷內容好壞，是防止貼文的人把自己的資產交出去。"""
    return any(
        _detect_and_scrub_mnemonic(v.lower())[1] or _detect_and_scrub_private_keys(v)[1]
        for v in _variants(text or "")
    )


def rule_reasons(text: str) -> List[str]:
    """回傳命中的理由代碼（空＝沒命中；原文或還原穿插符號後任一命中就算）：
    leaked_secret — 內文貼了助記詞（12+ 個 BIP39 單字）或私鑰（旁邊有「私鑰」字樣的 64 hex）
    asks_secret   — 要對方交出助記詞／私鑰／含提幣權限的 API 金鑰
    doubling      — 轉幣後加倍返還
    asks_payment  — 叫人把錢匯到發文者的帳戶／錢包
    """
    found = {r for v in _variants(text or "") for r in _rule_reasons(v)}
    return [r for r in _REASON_ORDER if r in found]

