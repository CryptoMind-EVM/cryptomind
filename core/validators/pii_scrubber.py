"""LLM 輸出 PII scrubber — 在 agent 回應送出前遮罩敏感個人資料。

職責
====
防止 PII(尤其加密資產最高風險的助記詞/私鑰)經 LLM 回應外洩。風險來源:
1. 使用者在聊天框貼入自己的助記詞/私鑰,LLM echo 回來
2. fetch_url 抓回的網頁(論壇貼文、釣魚文)含外洩助記詞,LLM 整理後輸出
3. self-host model 訓練資料含助記詞,幻覺吐出

插入點:core/agents/manager/claw_loop.py 的 _clean_claw_response 末段,單點
chokepoint 同時覆蓋 chat final response、DB 持久化、pulse 端點。

設計:三層防誤殺
================
這個平台是加密/股票分析,agent 回應充滿「長得像 PII」的內容(錢包地址、tx hash、
金融數字)。過度 scrub 會毀掉正常分析內容。因此:
1. **白名單先行**:先 placeholder 保護 public 錢包地址(0x+40hex、bc1、UQ/EQ、
   T+base58),scrub 完再還原。crypto_modules 工具輸出的地址絕不誤動。
2. **context-aware**:純 64-hex 只在前後文有「私鑰/private key/secret」字樣時 scrub,
   否則視為 tx hash 保留(兩者格式相同,無法用 regex 區分)。
3. **強校驗**:信用卡必過 Luhn、身分證過台灣檢查碼、助記詞要求 12+ 連續 BIP39 單字。

不做:銀行帳號(12-14 碼與成交量/市值嚴重重疊,誤殺風險遠 > 收益)。

feature flag:PII_SCRUB_ENABLED=true(預設),誤殺時可關閉。
"""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Feature flag
# ──────────────────────────────────────────────────────────────────────────────


def _scrub_enabled() -> bool:
    """PII scrubber 是否啟用。預設啟用;設 PII_SCRUB_ENABLED=false 關閉。"""
    return os.environ.get("PII_SCRUB_ENABLED", "true").lower() not in {
        "false",
        "0",
        "no",
        "off",
    }


# ──────────────────────────────────────────────────────────────────────────────
# BIP39 wordlist(公開標準字典,用於「辨識」助記詞,非儲存)
#
# 我們不持有、不要求、不儲存任何使用者的助記詞。這份 wordlist 是比特幣公開標準
# (BIP39),用途是:若助記詞意外出現在 LLM 回應裡,偵測連續 12+ 個 BIP39 單字序列
# 並遮罩,不讓它送到使用者眼前。wordlist 本身不是機密(GitHub 公開)。
# ──────────────────────────────────────────────────────────────────────────────

_WORDLIST_PATH = Path(__file__).parent / "bip39_english.txt"
# 標準 BIP39 助記詞長度
_MNEMONIC_LENGTHS = (12, 15, 18, 21, 24)


@lru_cache(maxsize=1)
def _load_bip39_words() -> frozenset[str]:
    """載入 BIP39 英文 wordlist。檔案缺失時回空 set(scrubber 該項 no-op 不擋主流程)。"""
    try:
        text = _WORDLIST_PATH.read_text(encoding="utf-8")
        words = frozenset(line.strip() for line in text.splitlines() if line.strip())
        if len(words) < 2000:  # 標準應為 2048;過少視為損毀
            logger.warning(
                "[PIIScrubber] BIP39 wordlist 疑似損毀(%d words),mnemonic 偵測停用",
                len(words),
            )
            return frozenset()
        return words
    except OSError as e:
        logger.warning("[PIIScrubber] 無法載入 BIP39 wordlist(%s),mnemonic 偵測停用", e)
        return frozenset()


def _detect_and_scrub_mnemonic(text: str) -> tuple[str, int]:
    """偵測並遮罩 BIP39 助記詞序列。

    策略:抓連續的小寫英文單字序列(≥12 個),在序列內找最長的「全部命中 BIP39」子序列。
    若子序列長度 ≥12 → 視為助記詞並遮罩。
    誤殺極低:正常英文不太可能連續 12 字都命中 BIP39(字典只 2048 字,大量常用詞不在內)。

    用 sliding window 找 match 內連續 BIP39 段,而非要求整個 match 都是 BIP39
    (避免序列夾雜非 BIP39 字時,貪婪 regex 不回溯而漏判)。

    Returns: (scrubbed_text, count)
    """
    words_set = _load_bip39_words()
    if not words_set:
        return text, 0

    min_len = _MNEMONIC_LENGTHS[0]  # 12

    def _replace_run(m: re.Match[str]) -> str:
        nonlocal count
        tokens = m.group(0).split()
        # 找出 tokens 內連續 BIP39 的最長子序列
        # best: (start_idx, end_idx) 連續命中段
        runs: list[tuple[int, int]] = []
        run_start = None
        for i, tok in enumerate(tokens):
            if tok in words_set:
                if run_start is None:
                    run_start = i
            else:
                if run_start is not None:
                    runs.append((run_start, i))
                    run_start = None
        if run_start is not None:
            runs.append((run_start, len(tokens)))

        # 若任一連續段 ≥ min_len → 遮罩該段,保留前後非 BIP39 字
        result_tokens = list(tokens)
        replaced = False
        for start, end in runs:
            if end - start >= min_len:
                result_tokens[start:end] = ["[REDACTED:MNEMONIC]"]
                count += 1
                replaced = True
        return " ".join(result_tokens) if replaced else m.group(0)

    # 連續小寫英文單字(單字間空白);抓 ≥12 字的序列
    count = 0
    cleaned = re.sub(r"(?:[a-z]{2,8}\s+){11,}[a-z]{2,8}", _replace_run, text)
    return cleaned, count


# ──────────────────────────────────────────────────────────────────────────────
# 私鑰(context-aware,避免誤殺 tx hash)
# ──────────────────────────────────────────────────────────────────────────────

# 64-hex(可能有 0x 前綴)— ETH 私鑰 / tx hash 格式相同
_HEX64_RE = re.compile(r"0x[0-9a-fA-F]{64}|(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
# Bitcoin WIF 私鑰:5/K/L 開頭 + base58,~51 碼
_BTC_WIF_RE = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[5KL][1-9A-HJ-NP-Za-km-z]{50,51}(?![1-9A-HJ-NP-Za-km-z])")

# 觸發私鑰 scrub 的 context 字樣(中英文)
_PRIVATE_KEY_CONTEXT_RE = re.compile(
    r"私鑰|私钥|private\s*key|secret\s*key|seed\s*phrase|助記詞|助记词|mnemonic",
    re.IGNORECASE,
)
_CONTEXT_WINDOW = 40  # 前後多少字元內有 context 字樣才算


def _detect_and_scrub_private_keys(text: str) -> tuple[str, int]:
    """context-aware 私鑰遮罩。

    只在 64-hex / WIF 前後 _CONTEXT_WINDOW 字元內出現「私鑰/private key/...」字樣時 scrub。
    否則保留(可能是 tx hash、合約相關資料)。寧可放過不要誤殺。
    """
    count = 0

    def _check_context(m: re.Match[str]) -> str:
        nonlocal count
        start, end = m.span()
        window = text[max(0, start - _CONTEXT_WINDOW) : end + _CONTEXT_WINDOW]
        if _PRIVATE_KEY_CONTEXT_RE.search(window):
            count += 1
            return "[REDACTED:PRIVATE_KEY]"
        return m.group(0)  # 無 context 字樣 → 保留

    cleaned = _HEX64_RE.sub(_check_context, text)
    cleaned = _BTC_WIF_RE.sub(_check_context, cleaned)
    return cleaned, count


# ──────────────────────────────────────────────────────────────────────────────
# 信用卡(Luhn 校驗,避免誤殺金融數字)
# ──────────────────────────────────────────────────────────────────────────────

_CARD_RE = re.compile(r"(?<!\d)(\d[ -]?){13,18}(?!\d)")


def _luhn_check(digits: str) -> bool:
    """Luhn 演算法驗證信用卡號。"""
    cleaned = re.sub(r"[ -]", "", digits)
    if len(cleaned) < 13 or len(cleaned) > 19:
        return False
    total = 0
    reverse = cleaned[::-1]
    for i, ch in enumerate(reverse):
        if not ch.isdigit():
            return False
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _detect_and_scrub_credit_cards(text: str) -> tuple[str, int]:
    count = 0

    def _check_luhn(m: re.Match[str]) -> str:
        nonlocal count
        candidate = m.group(0)
        if _luhn_check(candidate):
            count += 1
            return "[REDACTED:CARD]"
        return candidate

    cleaned = _CARD_RE.sub(_check_luhn, text)
    return cleaned, count


# ──────────────────────────────────────────────────────────────────────────────
# 台灣身分證(檢查碼驗證)
# ──────────────────────────────────────────────────────────────────────────────

_TW_ID_RE = re.compile(r"(?<![A-Z0-9])[A-Z][12]\d{8}(?![A-Z0-9])")
# 身分證首字母對應數值(A=10, B=11, ...)
_TW_ID_LETTER_VALUES = {chr(ord("A") + i): v for i, v in enumerate(
    [10, 11, 12, 13, 14, 15, 16, 17, 34, 18, 19, 20, 21, 22, 35, 23, 24, 25, 26, 27, 28, 29, 32, 30, 31, 33]
)}


def _tw_id_check(candidate: str) -> bool:
    """台灣身分證字號檢查碼驗證。"""
    if len(candidate) != 10:
        return False
    letter = candidate[0]
    if letter not in _TW_ID_LETTER_VALUES:
        return False
    n = _TW_ID_LETTER_VALUES[letter]
    # 第一碼只能是 1(男)或 2(女)
    if candidate[1] not in "12":
        return False
    digits = [int(c) for c in candidate[1:]]
    total = (n // 10) + (n % 10) * 9
    for i, d in enumerate(digits[:-1]):
        total += d * (8 - i)
    check_digit = (10 - (total % 10)) % 10
    return check_digit == digits[-1]


def _detect_and_scrub_tw_id(text: str) -> tuple[str, int]:
    count = 0

    def _check(m: re.Match[str]) -> str:
        nonlocal count
        candidate = m.group(0)
        if _tw_id_check(candidate):
            count += 1
            return "[REDACTED:TW_ID]"
        return candidate

    cleaned = _TW_ID_RE.sub(_check, text)
    return cleaned, count


# ──────────────────────────────────────────────────────────────────────────────
# Email / 電話(regex 來源:core/validators/content_filter.py:34, 41-45)
# ──────────────────────────────────────────────────────────────────────────────

# email regex 抄自 content_filter.py:34
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# 電話 regex 加強版:要求前後非數字邊界,且必須有明確電話特徵(+/括號/特定長度)
# 避免 content_filter 原版會誤抓金融數字的問題
_PHONE_RE = re.compile(
    r"(?<!\d)("
    r"\+?\d{1,3}[-.\s]\(?\d{2,4}\)?[-.\s]\d{3,4}[-.\s]\d{3,4}"  # 帶分隔符的國際/市話
    r"|\(0\d\)\d{4}-\d{4}"  # (02)1234-5678
    r"|\+886[-.\s]?\d{2,3}[-.\s]?\d{3,4}[-.\s]?\d{3,4}"  # +886 國際格式
    r")(?:\D|$)"
)


def _detect_and_scrub_email_phone(text: str) -> tuple[str, int]:
    count = 0
    cleaned = _EMAIL_RE.sub("[REDACTED:EMAIL]", text)
    # email 替換會影響電話偵測的位置,但兩者 pattern 不重疊(email 必含 @),順序無妨
    new_cleaned, phone_count = _scrub_phones(cleaned)
    count += phone_count
    # email 計數:比對替換前後 REDACTED 數量
    email_count = text.count("@") - cleaned.count("@")
    count += email_count
    return new_cleaned, count


def _scrub_phones(text: str) -> tuple[str, int]:
    count = 0

    def _replace(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        # 保留 match 末尾的非數字(避免吃掉句尾標點)
        return "[REDACTED:PHONE]"

    cleaned = _PHONE_RE.sub(_replace, text)
    return cleaned, count


# ──────────────────────────────────────────────────────────────────────────────
# 白名單保護:public 錢包地址 / 合約地址(scrub 前先 placeholder,scrub 後還原)
#
# crypto_modules 工具大量輸出這些地址,絕不能被誤動。先換成唯一 placeholder,
# 全部 scrub 跑完再換回來。
# ──────────────────────────────────────────────────────────────────────────────

# ETH 地址:0x + 40 hex(合約/錢包,public)。後面不能接更多 hex(否則可能是 64hex 私鑰)
_ETH_ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")
# BTC 地址:bc1... / 1... / 3...
_BTC_ADDR_RE = re.compile(r"(?:bc1[ac-hj-np-z02-9]{6,87}|[13][a-km-zA-HJ-NP-Z1-9]{24,33})")
# TON 地址:UQ/EQ/kQ/0: 開頭(對齊 analysis.py 既有判定)
_TON_ADDR_RE = re.compile(r"(?:UQ|EQ|kQ)[A-Za-z0-9_-]{43,48}|0:[A-Za-z0-9_-]{43,64}")
# Tron 地址:T + 33 base58
_TRON_ADDR_RE = re.compile(r"T[1-9A-HJ-NP-Za-km-z]{33}")

_ADDR_PATTERNS = (_ETH_ADDR_RE, _BTC_ADDR_RE, _TON_ADDR_RE, _TRON_ADDR_RE)
_ADDR_PLACEHOLDER = "\x00WALLET_ADDR_{n}\x00"


def _protect_addresses(text: str) -> tuple[str, list[str]]:
    """把 public 地址換成 placeholder,回傳 (protected_text, original_addresses)。"""
    originals: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        originals.append(m.group(0))
        return _ADDR_PLACEHOLDER.format(n=len(originals) - 1)

    protected = text
    for pat in _ADDR_PATTERNS:
        protected = pat.sub(_stash, protected)
    return protected, originals


def _restore_addresses(text: str, originals: list[str]) -> str:
    for i, addr in enumerate(originals):
        text = text.replace(_ADDR_PLACEHOLDER.format(n=i), addr)
    return text


# ──────────────────────────────────────────────────────────────────────────────
# 統一入口
# ──────────────────────────────────────────────────────────────────────────────


def scrub_pii(text: str) -> str:
    """遮罩 LLM 回應中的 PII。

    偵測順序(高風險 → 低風險):助記詞 → 私鑰 → 信用卡 → 身分證 → email → 電話。
    全程先保護 public 錢包地址(scrub 完還原),避免 crypto_modules 輸出被誤動。

    Args:
        text: LLM 原始回應

    Returns:
        遮罩後的字串(PII 換成 [REDACTED:TYPE]);無 PII 則原樣回傳。

    feature flag PII_SCRUB_ENABLED=false 時直接回原字串。
    """
    if not text or not _scrub_enabled():
        return text

    total_count = 0

    # 1. 先保護 public 地址
    protected, originals = _protect_addresses(text)

    # 2. 助記詞(最高風險,先 scrub 避免後續 regex 干擾)
    protected, n = _detect_and_scrub_mnemonic(protected)
    total_count += n

    # 3. 私鑰(context-aware)
    protected, n = _detect_and_scrub_private_keys(protected)
    total_count += n

    # 4. 信用卡(Luhn)
    protected, n = _detect_and_scrub_credit_cards(protected)
    total_count += n

    # 5. 身分證(台灣檢查碼)
    protected, n = _detect_and_scrub_tw_id(protected)
    total_count += n

    # 6. email / 電話
    protected, n = _detect_and_scrub_email_phone(protected)
    total_count += n

    # 7. 還原地址
    result = _restore_addresses(protected, originals)

    if total_count > 0:
        logger.warning(
            "[PIIScrubber] scrubbed %d PII item(s) from LLM response (len=%d)",
            total_count,
            len(text),
        )

    return result
