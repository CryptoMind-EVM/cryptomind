"""AI 回答快照分享的純函式（2026-10-05，docs/plans/2026-10-04-growth-handoff.md 任務 D）。

分享的是「使用者選的那一輪問答」的快照，不是整個對話；公開頁免登入，所以：
- token 不可猜（128 位元以上隨機），資料庫只存它的 SHA-256，資料庫外洩也拿不到可用連結；
- 內容在存進去之前先遮蔽錢包地址／交易哈希／Email（確定性、預覽與建立得到同一份）；
  持倉數量這類自然語言沒辦法可靠自動判斷，所以前端一律先給使用者預覽才建立；
- 頁面一律 noindex。
"""

from __future__ import annotations

import hashlib
import html
import re
import secrets
from typing import Optional

TTL_DAYS = 30
QUESTION_MAX = 500
ANSWER_MAX = 8000
DEFAULT_DAILY_LIMIT = 20
MAX_ACTIVE = (
    100  # 每人同時有效的連結上限（每日上限 × 30 天會累積到 600，清單與撤銷顧不來）
)
OG_DESCRIPTION_LEN = 150
_OG_TITLE_LEN = 200
# 先截斷再遮蔽時多留的緩衝：要比最長的位址／金鑰（約 130 字元）長，
# 這樣跨過截斷線的位址整段都在緩衝裡、會被遮蔽，最後才切掉
_MARGIN = 300

_TOKEN_BYTES = 24  # token_urlsafe(24) 剛好 32 個字元
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{32}")


# ── token ────────────────────────────────────────────────────────────────


def new_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def valid_token_shape(token: object) -> bool:
    """格式不對的 token 不必進資料庫查（也不留 404 記錄）。"""
    return isinstance(token, str) and _TOKEN_RE.fullmatch(token) is not None


# ── 遮蔽 ─────────────────────────────────────────────────────────────────

_B58 = "1-9A-HJ-NP-Za-km-z"
_EDGE_L, _EDGE_R = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"
# 順序有意義：越具體的越前面。每條的長度都有上限、頭尾有 lookaround，掃描是線性的
# （沒有「無界字元類 + 後面才看到 @」那種 O(n²) 形狀）。
_REDACTIONS = (
    (
        re.compile(_EDGE_L + r"0[xX][0-9a-fA-F]{40}" + _EDGE_R),
        "[address]",
    ),  # EVM（含大寫 0X）
    (
        re.compile(_EDGE_L + r"(?:0[xX])?[0-9a-fA-F]{40,}" + _EDGE_R),
        "[hash]",
    ),  # 交易哈希、簽章、無前綴位址、64 位私鑰
    (re.compile(_EDGE_L + rf"xprv[{_B58}]{{100,112}}"), "[key]"),  # BIP32 擴充私鑰
    (re.compile(_EDGE_L + rf"[5KL][{_B58}]{{50,51}}" + _EDGE_R), "[key]"),  # WIF 私鑰
    (re.compile(_EDGE_L + r"sk-[A-Za-z0-9_-]{20,}"), "[key]"),  # API key
    (
        re.compile(_EDGE_L + r"(?:bc1|tb1)[ac-hj-np-z02-9]{25,60}" + _EDGE_R, re.I),
        "[address]",
    ),
    (re.compile(_EDGE_L + r"G[A-Z2-7]{55}" + _EDGE_R), "[address]"),  # Stellar／Pi
    (
        re.compile(_EDGE_L + r"(?:EQ|UQ|kQ|0Q)[A-Za-z0-9_-]{46}" + _EDGE_R),
        "[address]",
    ),  # TON
    (
        re.compile(_EDGE_L + rf"[{_B58}]{{32,44}}" + _EDGE_R),
        "[address]",
    ),  # Solana 等 base58
    (
        re.compile(_EDGE_L + rf"[13][{_B58}]{{25,34}}" + _EDGE_R),
        "[address]",
    ),  # BTC 舊式
    # Email：local part 有界（RFC 上限 64）且必須從一段連續字元的開頭起算
    (
        re.compile(
            r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){1,8}"
        ),
        "[email]",
    ),
)


def redact(text: Optional[str]) -> tuple[str, int]:
    """遮蔽錢包地址、交易哈希、金鑰、Email。回傳 (遮蔽後的文字, 處數)。標記用語言中性的短標籤。"""
    out = text or ""
    total = 0
    for pattern, tag in _REDACTIONS:
        out, n = pattern.subn(tag, out)
        total += n
    return out, total


# ── 快照 ─────────────────────────────────────────────────────────────────


_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
_WHITESPACE = re.compile(r"[\x00-\x1f\x7f\s]+")


def _squash(raw: Optional[str]) -> str:
    """不可見字元（零寬、雙向控制）直接拿掉——它們會把一個位址切成兩半躲過遮蔽、或做視覺欺騙；
    控制字元與連續空白壓成一格；去頭尾。不截斷。"""
    return _WHITESPACE.sub(" ", _INVISIBLE.sub("", raw or "")).strip()


def clean_question(raw: Optional[str]) -> str:
    """清理過、最多 QUESTION_MAX 字元（以字元計，不是 byte）。find_turn 用它比對問題。"""
    return _squash(raw)[:QUESTION_MAX]


_PII_MARK = re.compile(r"\[REDACTED:")


def _scrub(text: str) -> tuple[str, int]:
    """助記詞、私鑰、信用卡、身分證、電話等（core.validators.pii_scrubber，原本只洗 AI 的回應；
    使用者貼在問題裡的助記詞也會被分享出去，所以兩邊都洗）。回傳 (文字, 新增的處數)。"""
    from core.validators.pii_scrubber import scrub_pii

    out = scrub_pii(text)
    return out, max(0, len(_PII_MARK.findall(out)) - len(_PII_MARK.findall(text)))


def prepare_snapshot(question: Optional[str], answer: Optional[str]) -> Optional[dict]:
    """要存進去（也是預覽給使用者看的）那份內容。問題或答案是空的回 None。

    順序：先截到「上限＋緩衝」（避免超長輸入拖慢掃描）→ 洗 PII → 遮蔽位址等 → 最後才截到上限。
    先遮蔽再截斷：先截斷可能把一個位址切成兩半，剩下的前半段就遮不到了。
    """
    q = _squash(question)
    a = _INVISIBLE.sub("", answer or "").strip()  # 答案要保留換行，只拿掉不可見字元
    if not q or not a:
        return None
    truncated = len(a) > ANSWER_MAX
    q, a = q[: QUESTION_MAX + _MARGIN], a[: ANSWER_MAX + _MARGIN]
    q, p_q = _scrub(q)
    a, p_a = _scrub(a)
    q, n_q = redact(q)
    a, n_a = redact(a)
    if len(a) > ANSWER_MAX:
        truncated = True
        a = a[:ANSWER_MAX].rstrip() + "…"
    return {
        "question": q[:QUESTION_MAX],
        "answer": a,
        "redactions": p_q + p_a + n_q + n_a,
        "truncated": truncated,
    }


# ── 連結預覽（OG）────────────────────────────────────────────────────────

_FENCE = re.compile(r"```.*?```", re.S)
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]{3,}\|?\s*$", re.M)
_LINE_MARK = re.compile(r"^\s{0,3}(?:#{1,6}|>|[-*+]|\d+[.)])\s+", re.M)


_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")


def defang(text: str) -> str:
    """網址換成 [link]。連結預覽卡（Telegram、X、Discord）會把標題與描述顯示在官方網域的名下，
    攻擊者能控制問題與答案的文字，不能讓它變成釣魚網址的載體。"""
    return _URL.sub("[link]", text)


def og_snippet(markdown: Optional[str], limit: int = OG_DESCRIPTION_LEN) -> str:
    """答案前 limit 字的純文字（拿掉 markdown 標記、網址換成 [link]），給連結預覽的描述用。"""
    t = _FENCE.sub(" ", markdown or "")
    t = _LINK.sub(r"\1", t)
    t = _TABLE_RULE.sub(" ", t)
    t = t.replace("|", " ")
    t = _LINE_MARK.sub("", t)
    t = re.sub(r"[*_`~]+", "", t)
    t = defang(_INVISIBLE.sub("", t))
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= limit else t[:limit].rstrip() + "…"


_DEFAULT_DESCRIPTION = "An AI market analyst's answer, shared from CryptoMind. Analysis, not financial advice."


def apply_share_meta(page: str, question: Optional[str], snippet: Optional[str]) -> str:
    """把 ``<!--OG-->`` 換成 noindex ＋（有內容時）連結預覽 meta。全部 HTML 跳脫。

    question 為 None（連結無效／已撤銷）時只放 noindex，不洩漏任何內容。
    """
    block = '<meta name="robots" content="noindex">'
    if question is not None:
        title = html.escape(
            f"“{defang(question)[:_OG_TITLE_LEN]}” — CryptoMind AI", quote=True
        )
        desc = html.escape(snippet or _DEFAULT_DESCRIPTION, quote=True)
        block += (
            f'\n    <meta property="og:type" content="article">'
            f'\n    <meta property="og:title" content="{title}">'
            f'\n    <meta property="og:description" content="{desc}">'
            f'\n    <meta name="twitter:card" content="summary">'
            f'\n    <meta name="twitter:title" content="{title}">'
            f'\n    <meta name="twitter:description" content="{desc}">'
        )
    return page.replace("<!--OG-->", block, 1)
