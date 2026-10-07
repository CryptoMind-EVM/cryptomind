"""
工具輸入模型定義 (Pydantic Schema)
所有 LangChain 工具的輸入參數結構
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class TechnicalAnalysisInput(BaseModel):
    """技術分析工具的輸入參數"""

    symbol: str = Field(description="Cryptocurrency trading pair symbol. Do not append the trading pair suffix.")
    interval: str = Field(
        default="1d",
        description="Candlestick time interval. Options: '1m', '5m', '15m', '1h', '4h', '1d', '1w'. Defaults to daily '1d'.",
    )
    exchange: Optional[str] = Field(
        default=None, description="Exchange name. Options: 'okx' (default), 'binance'."
    )


class NewsAnalysisInput(BaseModel):
    """新聞分析工具的輸入參數"""

    symbol: str = Field(description="Cryptocurrency symbol.")
    include_sentiment: bool = Field(
        default=True, description="Whether to include sentiment analysis. Defaults to True."
    )


class PriceInput(BaseModel):
    """價格查詢工具的輸入參數"""

    symbol: str = Field(description="Cryptocurrency symbol.")
    exchange: Optional[str] = Field(
        default=None, description="Exchange name. Options: 'okx' (default), 'binance'."
    )


class CurrentTimeInput(BaseModel):
    """當前時間查詢工具的輸入參數"""

    timezone: str = Field(
        default="Asia/Taipei",
        description="Timezone name, e.g. 'Asia/Taipei', 'UTC', 'America/New_York'. Defaults to Taipei time.",
    )


class MarketPulseInput(BaseModel):
    """市場脈動分析工具的輸入參數"""

    symbol: str = Field(description="Cryptocurrency symbol.")


class ClarifyInput(BaseModel):
    """釐清工具（clarify）的輸入參數 — Hermes 式 model-driven 釐清。

    模型判斷使用者問題範圍/標的不明確時呼叫此工具向使用者釐清。
    工具回傳結構化訊號（不自己 interrupt），由 claw_loop 的 node-level
    攔截後呼叫 interrupt()（已驗證的 HITL 模式）。
    """

    question: str = Field(
        description=(
            "The clarifying question to ask the user. Keep it short and specific, e.g.: "
            "'When you say Taiwan stocks, do you mean the overall market, a specific sector, or a particular stock?'"
        )
    )
    options: List[str] = Field(
        default_factory=list,
        description=(
            "Optional list of clarifying options (each a short description). "
            "Fit them to what the user actually asked — do not reuse a fixed "
            "market/stock/sector set for unrelated questions. "
            "The user can tap an option or type freely. Optional — leave empty "
            "when there are no clear options so the user can answer freely."
        ),
    )


class RememberInput(BaseModel):
    """記憶工具（remember）的輸入參數 — Hermes 式主動記憶。

    模型判斷「這個用戶資訊值得記住」時呼叫此工具，寫入 per-user 記憶庫。
    下次對話自動帶入 system prompt。mode='delete' 時刪除已記住的資訊（HITL 同意）。
    """

    content: str = Field(
        default="",
        description=(
            "The information to remember. Concise and specific, e.g.: "
            "'User prefers technical analysis', 'User mainly invests in crypto', 'User holds 2.3 BTC'. "
            "Required for mode='create'. For mode='delete', use 'key' instead."
        ),
    )
    category: str = Field(
        default="fact",
        description=(
            "Memory category. preference = investment preferences (technical/conservative/crypto-focused); "
            "holding = holdings (assets the user mentioned); context = conversation context; "
            "fact = other general facts. preference/holding are prioritized for injection into the next conversation."
        ),
    )
    mode: str = Field(
        default="create",
        description=(
            "create (default) = remember new info; delete = remove an existing memory. "
            "For delete, pass 'key' (obtained from list_my_skills_memory)."
        ),
    )
    key: str = Field(
        default="",
        description=(
            "The memory key to delete (mode='delete' only). Obtain it by calling "
            "list_my_skills_memory first — each memory is shown as '[key] (category) value'."
        ),
    )


class ListMySkillsMemoryInput(BaseModel):
    """列出使用者自己的 custom skills 與 memory（唯讀查詢，無 HITL）。

    使用者問「我有哪些分析方法 / 記憶」時呼叫，回傳結構化清單供 agent 整理回覆。
    """

    kind: str = Field(
        default="all",
        description=(
            "What to list: 'skill' = custom skills only, "
            "'memory' = remembered facts only, 'all' = both (default)."
        ),
    )


class ProposeCustomSkillInput(BaseModel):
    """提議建立 / 修改 / 刪除一個 custom skill（寫入前必過 HITL consent）。

    工具本身不寫 DB —— 只回傳 __needs_consent__ marker，由 claw_loop 攔截後
    interrupt() 暫停等使用者同意，核准後才寫入。這仿照 clarify 工具的 marker 模式。
    """

    mode: str = Field(
        default="create",
        description=(
            "Action mode: 'create' = new skill, 'update' = modify existing, 'delete' = remove. "
            "Default 'create'."
        ),
    )
    skill_name: str = Field(
        description=(
            "Skill identifier (letters, digits, underscore, hyphen; <=50 chars). "
            "For create this is the new name; for update/delete it must match an existing custom skill."
        ),
    )
    description: str = Field(
        default="",
        description="One-line summary of what this analysis method does (required for create/update).",
    )
    trigger_keywords: str = Field(
        default="",
        description="Comma-separated keywords that auto-trigger this skill, e.g. 'india,NIFTY,SENSEX'.",
    )
    body: str = Field(
        default="",
        description="The full method body (steps + output format). <=3000 chars. Required for create/update.",
    )
    reason: str = Field(
        default="",
        description="Why the agent proposes this change, in the user's language (shown on the consent card).",
    )


class ExtractCryptoSymbolsInput(BaseModel):
    """從用戶查詢中提取加密貨幣符號的工具輸入參數"""

    user_query: str = Field(
        description="The user's query text, which may contain one or more cryptocurrency symbols."
    )
