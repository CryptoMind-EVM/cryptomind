"""
可疑錢包追蹤系統 - Pydantic 模型
"""

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

_TX_HASH_RE = re.compile(r"(0x)?[0-9a-f]{64}")
_REPORT_TX_HASH_RE = re.compile(r"[A-Za-z0-9+/=_-]{16,128}")


class ScamReportCreate(BaseModel):
    """創建舉報請求"""

    scam_wallet_address: str = Field(..., min_length=32, max_length=90)
    reporter_wallet_address: str = Field(..., min_length=32, max_length=90)
    scam_type: str = Field(..., min_length=1, max_length=50)
    description: str = Field(..., min_length=20, max_length=2000)
    transaction_hash: Optional[str] = Field(None, min_length=16, max_length=128)

    @field_validator("scam_wallet_address", "reporter_wallet_address")
    @classmethod
    def validate_wallet_format(cls, v):
        # 接受任意區塊鏈錢包地址（TON UQ/EQ、Ethereum 0x 等），只做基本清理
        return v.strip()

    @field_validator("transaction_hash")
    @classmethod
    def validate_tx_hash(cls, v):
        if v:
            s = v.strip()
            # 舉報刻意放寬（TON 的 base64 雜湊也收），但只收 hex／base64／base64url 字元：
            # 以前任何 16–128 字元都存，含 HTML
            if not _REPORT_TX_HASH_RE.fullmatch(s):
                raise ValueError("Invalid transaction hash format")
            return s.lower()
        return v


class ScamReportResponse(BaseModel):
    """舉報響應"""

    id: int
    scam_wallet_address: str
    scam_type: str
    description: str
    verification_status: str
    approve_count: int
    reject_count: int
    comment_count: int
    view_count: int
    reporter_wallet_masked: str
    reporter_username: Optional[str]
    created_at: str
    net_votes: int


class ScamReportDetailResponse(ScamReportResponse):
    """舉報詳情響應"""

    transaction_hash: Optional[str]
    updated_at: str
    viewer_vote: Optional[str]


class VoteRequest(BaseModel):
    """投票請求"""

    vote_type: str = Field(..., pattern="^(approve|reject)$")


class CommentCreate(BaseModel):
    """創建評論請求"""

    content: str = Field(..., min_length=10, max_length=1000)
    # TON 64 hex 或 EVM 0x+64 hex（與前端 isValidTxHash 同一規則）
    transaction_hash: Optional[str] = Field(None, max_length=66)

    @field_validator("transaction_hash")
    @classmethod
    def validate_tx_hash(cls, v):
        if v is None:
            return v
        s = v.strip().lower()
        if not s:
            return None
        # 以前只驗長度：任意 64 字元（含 HTML）都會存進去再原樣渲染
        if not _TX_HASH_RE.fullmatch(s):
            raise ValueError("Invalid transaction hash format")
        return s
