from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from core.config import DEFAULT_INTERVAL, DEFAULT_KLINES_LIMIT, SUPPORTED_EXCHANGES
from core.model_config import GEMINI_DEFAULT_MODEL


def _dedup_doubled_message(text: str) -> str:
    """去重「完整訊息重複兩次」的 query（如 'ABAB' → 'AB'）。

    線上觀察到前端（尤其 TMA 環境）偶爾把同一句訊息拼接兩次送出
    （後端收到 '幫我查...能拿到多少幫我查...能拿到多少'）。重複 query 會讓
    LLM 困惑、跑更多輪 = 更慢更易出錯。這裡在後端入口防禦性去重：
    把字串對半切比對，前後半完全相等（含中英文）就取前半。
    只處理偶數長度且 ≥ 8 字元的訊息，避免誤傷正常的對稱/回文訊息。
    """
    if not text or len(text) < 8:
        return text
    stripped = text.strip()
    n = len(stripped)
    if n % 2 != 0 or n < 8:
        return text
    mid = n // 2
    if stripped[:mid] == stripped[mid:]:
        return stripped[:mid]
    return text


# 定義請求模型
class QueryRequest(BaseModel):
    message: str = Field(..., max_length=4000)
    system_prompt: Optional[str] = Field(
        None, max_length=2000
    )  # Premium: 使用者自訂 system prompt
    enabled_tools: Optional[List[str]] = None  # Premium: 使用者啟用的工具 ID 清單
    interval: str = DEFAULT_INTERVAL
    limit: int = DEFAULT_KLINES_LIMIT
    manual_selection: Optional[List[str]] = None
    auto_execute: bool = False
    market_type: str = "spot"
    user_provider: Optional[str] = None  # preferred provider, if any
    user_model: Optional[str] = None  # 用戶選擇的模型名稱
    session_id: str = "default"  # 會話 ID
    resume_answer: Optional[Any] = None  # HITL 回答（Accepts str or dict）
    # Bind a resume to its server-side model snapshot (never accepts credentials).
    resume_run_id: Optional[str] = Field(None, pattern=r"^[a-f0-9]{32}$")
    language: str = "zh-TW"  # 用戶語言偏好（"zh-TW" | "en"）
    # 附圖（vision Tier 1，docs/plans/2026-08-27-vision-image-analysis-design.md）：
    # data:image/(png|jpeg|webp);base64,... 僅登入用戶；伺服器驗證 mime/大小後
    # 轉為文字描述併入 query，圖片本身不落庫不進 log。
    image_data_url: Optional[str] = Field(None, max_length=5_600_000)
    # Agent preset（Phase 2）：server 端解析；指向他人 preset 視同未提供。
    # AGENT_PRESETS_ENABLED=off 時忽略。
    preset_id: Optional[str] = Field(None, max_length=100)
    analysis_mode: Optional[str] = Field(None, max_length=20)  # quick/verified/research

    @field_validator("message")
    @classmethod
    def dedup_message(cls, v: str) -> str:
        """去重前端可能拼接的重複訊息（見 _dedup_doubled_message）。"""
        return _dedup_doubled_message(v)


class ScreenerRequest(BaseModel):
    exchange: str = SUPPORTED_EXCHANGES[0]
    symbols: Optional[List[str]] = None
    refresh: bool = False


class UserRegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, description="用戶名")
    password: str = Field(..., min_length=8, max_length=128, description="密碼")


class UserLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50, description="用戶名")
    password: str = Field(..., min_length=1, max_length=128, description="密碼")


class KlineRequest(BaseModel):
    symbol: str
    exchange: str = SUPPORTED_EXCHANGES[0]
    interval: str = "1d"
    limit: int = 100


class UserSettings(BaseModel):
    """用戶動態設置"""

    openai_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None

    # 模型選擇
    primary_model_provider: str = "google_gemini"  # openai, google_gemini, openrouter
    primary_model_name: str = GEMINI_DEFAULT_MODEL  # 默認為 Google Gemini


class RefreshPulseRequest(BaseModel):
    symbols: Optional[List[str]] = None


class KeyValidationRequest(BaseModel):
    provider: str  # openai, google_gemini, openrouter
    api_key: str
    model: Optional[str] = None  # 用戶選擇的模型名稱
    language: str = "zh-TW"  # 用戶介面語言（"zh-TW" | "en" | "ru"），決定回傳訊息語言


# ============================================================================
# 社群治理系統 Models (Community Governance System)
# ============================================================================


class ReportCreateRequest(BaseModel):
    """創建檢舉請求"""

    content_type: Literal["post", "comment"]
    content_id: int
    report_type: Literal[
        "spam", "harassment", "misinformation", "scam", "illegal", "other"
    ]
    description: Optional[str] = Field(None, max_length=1000)


class ReportResponse(BaseModel):
    """檢舉回應"""

    id: int
    content_type: str
    content_id: int
    reporter_user_id: str
    report_type: str
    description: Optional[str]
    review_status: str  # pending, approved, rejected
    created_at: str
    updated_at: Optional[str] = None


class VoteRequest(BaseModel):
    """投票請求"""

    vote_type: str  # 'approve' (認為違規) 或 'reject' (認為不違規)


class ReportDetailResponse(BaseModel):
    """檢舉詳情回應"""

    id: int
    content_type: str
    content_id: int
    reporter_user_id: str
    reporter_username: Optional[str]
    report_type: str
    description: Optional[str]
    review_status: str
    violation_level: Optional[str]
    approve_count: int
    reject_count: int
    created_at: str
    updated_at: Optional[str]
    votes: Optional[List[Dict]] = None


class ViolationPointsResponse(BaseModel):
    """違規點數回應"""

    user_id: str
    points: int
    total_violations: int
    suspension_count: int
    last_violation_at: Optional[str]
    action_threshold: Optional[str] = None  # 下一步處罰等級


class ViolationRecordResponse(BaseModel):
    """違規記錄回應"""

    id: int
    user_id: str
    violation_level: str
    violation_type: str
    points: int
    action_taken: Optional[str]
    suspended_until: Optional[str]
    created_at: str


class ActivityLogResponse(BaseModel):
    """活動日誌回應"""

    id: int
    user_id: str
    activity_type: str
    resource_type: Optional[str]
    resource_id: Optional[int]
    metadata: Optional[Dict]
    success: bool
    error_message: Optional[str]
    created_at: str


class ReviewStatisticsResponse(BaseModel):
    """審核統計回應"""

    total_reports: int
    pending_reports: int
    approved_reports: int
    rejected_reports: int
    total_votes: int
    avg_approval_rate: float


class AuditReputationResponse(BaseModel):
    """審核聲望回應"""

    user_id: str
    username: Optional[str]
    total_reviews: int
    correct_votes: int
    accuracy_rate: float
    reputation_score: int
    vote_weight: float


class FinalizeReportRequest(BaseModel):
    """完成檢舉請求"""

    decision: str  # 'approved' 或 'rejected'
    violation_level: Optional[str] = None  # mild, medium, severe, critical


class ConsensusResponse(BaseModel):
    """共識檢查回應"""

    has_consensus: bool
    decision: Optional[str] = None  # 'approved', 'rejected', or None
    total_votes: int
    approve_count: int
    reject_count: int
    approve_rate: float
    reason: Optional[str] = None


# Price Alerts
class CreateAlertRequest(BaseModel):
    symbol: str
    # 與 core/database/price_alerts.VALID_MARKETS 一致（2026-09-27 擴到自選清單的 10 個市場）
    market: Literal[
        "crypto",
        "tw_stock",
        "us_stock",
        "hk_stock",
        "jp_stock",
        "kr_stock",
        "cn_stock",
        "in_stock",
        "commodity",
        "forex",
    ]
    condition: Literal["above", "below", "change_pct_up", "change_pct_down"]
    target: float
    repeat: bool = False


class APIErrorResponse(BaseModel):
    """統一 API 錯誤回應格式"""

    code: str = "INTERNAL_ERROR"
    message: str = "An unexpected error occurred"
    details: Optional[Dict] = None


# ============================================================================
# Analysis Preferences Models
# ============================================================================
class AnalysisPreferenceInput(BaseModel):
    """用戶 Analysis Preferences 寫入模型"""

    agent_id: str = Field(
        ..., description="Agent domain: crypto, tw_stock, us_stock, chat"
    )
    system_prompt: Optional[str] = Field(
        None, max_length=2000, description="Custom system prompt"
    )
    enabled_tools: Optional[list[str]] = Field(
        None, description="List of enabled tool IDs"
    )


class AnalysisPreferenceResponse(BaseModel):
    """用戶 Analysis Preferences 回應模型"""

    agent_id: str
    system_prompt: Optional[str] = None
    enabled_tools: Optional[list[str]] = None
    updated_at: Optional[str] = None

