"""
Forum API request models.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class CreatePostRequest(BaseModel):
    """Create post payload."""

    board_slug: str = Field(..., description="Board slug")
    category: str = Field(
        ..., description="Post category: analysis/question/tutorial/news/chat/insight"
    )
    title: str = Field(..., max_length=200, description="Post title")
    content: str = Field(..., max_length=10000, description="Post content")
    tags: Optional[List[str]] = Field(None, max_length=5, description="Post tags")
    # 發文費（免費會員）：USDC on Base，/api/forum/posts/payment-order 簽的訂單
    order_token: Optional[str] = Field(
        None,
        max_length=4096,
        description="Signed USDC order from /api/forum/posts/payment-order",
    )
    payment_tx_hash: Optional[str] = Field(
        None,
        max_length=128,
        description="0x hash of the USDC transfer (verified on-chain with the order)",
    )


class CheckPostRequest(BaseModel):
    """發文前的內容檢查（發文頁即時呼叫）。"""

    title: str = Field("", max_length=200)
    content: str = Field("", max_length=10000)


class UpdatePostRequest(BaseModel):
    """Update post payload."""

    title: Optional[str] = Field(None, max_length=200)
    content: Optional[str] = Field(None, max_length=10000)
    category: Optional[str] = None


class AddCommentRequest(BaseModel):
    """Add comment payload."""

    type: str = Field(..., description="Comment type: push/boo/comment")
    content: Optional[str] = Field(None, max_length=100, description="Comment content")
    parent_id: Optional[int] = Field(None, description="Parent comment ID")


class CreateTipRequest(BaseModel):
    # USDC on Base：/tip/payment-order 簽的訂單（金額、收款人都在裡面）
    order_token: Optional[str] = Field(
        None,
        max_length=4096,
        description="Signed USDC order from /api/forum/posts/{id}/tip/payment-order",
    )
    tx_hash: Optional[str] = Field(
        None, max_length=128, description="0x hash of the USDC transfer"
    )
    # 只有 TEST_MODE 讀；正式環境金額以訂單為準
    amount: Optional[float] = Field(None, gt=0, allow_inf_nan=False)


class TipOrderRequest(BaseModel):
    """Request a USDC (Base) payment order for tipping a post."""

    amount: Optional[float] = Field(
        None,
        gt=0,
        allow_inf_nan=False,
        description="Tip amount in USD (FORUM_TIP_MIN_USD..FORUM_TIP_MAX_USD; default 1)",
    )
