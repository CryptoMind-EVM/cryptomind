"""
模型配置文件 - 統一管理所有 LLM 模型配置

在此修改模型名稱，全系統自動同步（後端 + 前端）。
"""

# ============================================================================
# 具名常數 —— 在此修改即可全系統生效
# ============================================================================

# 2026-09-21 校準（DANNY：DeepSeek V4.1 Flash / GPT-6 / Claude Fable 5.1 上線）——
# 對照 OpenAI 模型文件、Gemini API 模型頁、DeepSeek 定價頁、Anthropic 模型表與
# OpenRouter 目錄逐筆驗證；未能對官方文件驗證的 provider（DashScope／智譜／Kimi／
# MiniMax／SiliconFlow／Groq）維持 2026-08-28 的清單。
OPENAI_DEFAULT_MODEL = (
    "gpt-5.6-luna"  # OpenAI 預設：成本最佳化（$0.2/M），BYOK 用戶不會意外燒錢
)
OPENAI_FLAGSHIP_MODEL = (
    "gpt-6-astra"  # OpenAI 當代旗艦（2026-09-04；官方文件的建議起點）
)
OPENAI_PRO_MODEL = "gpt-6-astra-pro"  # OpenAI 最強（深度分析）
OPENAI_FRONTIER_MODEL = (
    "gpt-5.6-sol"  # 前一代旗艦（別名 gpt-5.6；gpt-5.4/5.5 已列 deprecated）
)
OPENAI_LEGACY_MODEL = (
    "gpt-4.1-mini"  # 舊版相容：僅供 utils/llm_client.py fallback，不在前端清單
)
GEMINI_DEFAULT_MODEL = (
    "gemini-3.8-flash"  # Google Gemini 預設（2026-09-02 GA，官方建議預設）
)
DEEPSEEK_DEFAULT_MODEL = (
    "deepseek-flash"  # DeepSeek V4.1 Flash（官方新名；deepseek-v4-flash 仍收但已退役）
)
DEEPSEEK_PRO_MODEL = "deepseek-v4-pro"  # DeepSeek V4 Pro（0813）

# ============================================================================
# Provider 註冊表（單一真實來源 / Single Source of Truth）
#
# 後端建立 client、驗證 key，以及前端下拉，全部讀這張表。
# 要新增一個 provider：在 PROVIDER_REGISTRY 加一行 + 在 MODEL_CONFIG 補模型清單即可，
# 路由（create_client / validate_key / _get_api_key / 前端下拉）會自動生效。
#
# 欄位說明：
#   lc_provider   — LangChain init_chat_model 的 model_provider 值
#                   ("openai" / "google_genai" / "anthropic")
#   base_url      — OpenAI 相容端點網址；官方 SDK（openai/gemini/anthropic）留 None
#   api_key_envs  — 從環境變數讀 server key 時的候選名稱（依序 fallback）
#
# NVIDIA / MiniMax / DeepSeek / Kimi / 通義 / GLM / 豆包 等都提供 OpenAI 相容端點，
# 因此共用 lc_provider="openai" + base_url 即可，無需各自的 SDK 套件。
# ============================================================================


def _env(name: str, default: str) -> str:
    import os

    return (os.getenv(name) or "").strip() or default


PROVIDER_REGISTRY: dict[str, dict] = {
    "openai": {
        "lc_provider": "openai",
        "base_url": None,
        "api_key_envs": ["OPENAI_API_KEY"],
    },
    # openai_server：只吃 SERVER_OPENAI_API_KEY，不 fallback 到 OPENAI_API_KEY，
    # 避免干擾 BYOK 模式。
    "openai_server": {
        "lc_provider": "openai",
        "base_url": None,
        "api_key_envs": ["SERVER_OPENAI_API_KEY"],
    },
    "google_gemini": {
        "lc_provider": "google_genai",
        "base_url": None,
        "api_key_envs": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    },
    "anthropic": {
        "lc_provider": "anthropic",
        "base_url": None,
        "api_key_envs": ["ANTHROPIC_API_KEY"],
    },
    "openrouter": {
        "lc_provider": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_envs": ["OPENROUTER_API_KEY"],
    },
    "deepseek": {
        "lc_provider": "openai",
        "base_url": "https://api.deepseek.com",
        "api_key_envs": ["DEEPSEEK_API_KEY"],
    },
    "siliconflow": {
        "lc_provider": "openai",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key_envs": ["SILICONFLOW_API_KEY"],
    },
    "groq": {
        "lc_provider": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_envs": ["GROQ_API_KEY"],
    },
    "moonshot": {
        "lc_provider": "openai",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_envs": ["MOONSHOT_API_KEY"],
    },
    "dashscope": {
        "lc_provider": "openai",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_envs": ["DASHSCOPE_API_KEY"],
    },
    "zhipu": {
        "lc_provider": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_envs": ["ZHIPU_API_KEY"],
    },
    "minimax": {
        "lc_provider": "openai",
        "base_url": "https://api.minimax.io/v1",
        "api_key_envs": ["MINIMAX_API_KEY"],
    },
    "volcengine": {
        "lc_provider": "openai",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key_envs": ["VOLCENGINE_API_KEY", "ARK_API_KEY"],
    },
    "nvidia": {
        "lc_provider": "openai",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key_envs": ["NVIDIA_API_KEY"],
    },
    # local_llama：平台自架的 llama-server（OpenAI 相容），免費層／訪客的預設模型。
    # 2026-09-22 DANNY：本地 NeoHorse-1-9B（Qwen3.5-9B agent 後訓練版，Apache-2.0）
    # 對外一律用平台品牌名 PLATFORM_FREE_MODEL_LABEL，不露底層模型名。
    # - 不在 MODEL_CONFIG（BYOK 下拉不列，使用者不能「選」它；它只透過平台 fallback／訪客鏈進來）
    # - api_key_optional：llama-server 不驗 key，factory 用佔位 key 建 client
    # - request_extra_body：關掉 Qwen3.5 的 thinking（iGPU 上想 3–5K token 要兩三分鐘才吐第一個字）
    "local_llama": {
        "lc_provider": "openai",
        "base_url": _env("LOCAL_LLAMA_BASE_URL", "http://localhost:8080/v1"),
        "api_key_envs": ["LOCAL_LLAMA_API_KEY"],
        "api_key_optional": True,
        "request_extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    },
}

# 免費層／訪客模型對使用者顯示的名字（平台品牌，不露底層模型）。
PLATFORM_FREE_MODEL_LABEL = _env("PLATFORM_FREE_MODEL_LABEL", "CryptoMind Lite")
LOCAL_LLAMA_DEFAULT_MODEL = _env("LOCAL_LLAMA_MODEL", "neohorse-1-9b")


def is_local_provider(provider: str | None) -> bool:
    """平台自架、不花錢的 provider（免費層不設平台金鑰日額）。"""
    return (provider or "").strip().lower() == "local_llama"


# OpenAI-compatible providers 的 base_url 對照表（從註冊表自動衍生，保留向後相容）。
# 既有程式（user_client_factory）以此判斷是否為 OpenAI 相容 provider。
OPENAI_COMPATIBLE_BASE_URLS: dict[str, str] = {
    name: cfg["base_url"]
    for name, cfg in PROVIDER_REGISTRY.items()
    if cfg["lc_provider"] == "openai" and cfg["base_url"]
}

# ============================================================================
# 前端模型清單（供 /api/model-config 端點使用）
#
# 欄位（2026-09-02 DANNY：選項描述不放 emoji／硬編碼中文，多語系在前端做，
# 缺翻譯以英文為標準）：
#   value  — 送給 provider API 的模型 ID
#   name   — 語系中立的模型名（品牌名，不翻譯）
#   tag    — 定位標籤 key（最新/快速/深度…），前端以 settings.ai.modelTag.<tag>
#            顯示；缺翻譯時用下方 TAG_EN 英文
#   vision — 是否支援圖片輸入（取代舊 display 的 👁 emoji）
#   display— 英文標準整串（向後相容、無 i18n 時的 fallback），由 _model() 自動組出
#
# free_input=True 的 provider 在前端改用文字輸入框，讓用戶自填任意模型名
# （適合模型眾多/更新快的聚合平台，如 OpenRouter / NVIDIA / 火山方舟）。
# ============================================================================

TAG_EN: dict[str, str] = {
    "latest": "latest",
    "latest_flagship": "latest flagship",
    "flagship": "flagship",
    "advanced": "advanced",
    "fast": "fast",
    "ultrafast": "ultrafast",
    "highspeed": "high speed",
    "deep": "deep",
    "strongest": "strongest",
    "experimental": "experimental",
    "free": "free",
    "auto_route": "auto routing",
}


def _model(value: str, name: str, tag: str | None = None, vision: bool = False) -> dict:
    """組出單一模型條目（display 為英文標準 fallback，自動由 name/tag/vision 組出）。"""
    label = name + (f" ({TAG_EN[tag]})" if tag else "")
    if vision:
        label += " · vision"
    entry: dict = {"value": value, "name": name, "display": label}
    if tag:
        entry["tag"] = tag
    if vision:
        entry["vision"] = True
    return entry


MODEL_CONFIG = {
    "openai": {
        "display": "OpenAI",
        "default_model": OPENAI_DEFAULT_MODEL,
        # 2026-09-21：GPT-6 Astra 系列上線；gpt-5.4 / 5.5 官方已列 deprecated，移出清單
        "available_models": [
            _model(
                OPENAI_FLAGSHIP_MODEL, "GPT-6 Astra", tag="latest_flagship", vision=True
            ),
            _model(OPENAI_PRO_MODEL, "GPT-6 Astra Pro", tag="strongest", vision=True),
            _model(OPENAI_FRONTIER_MODEL, "GPT-5.6 Sol", tag="flagship", vision=True),
            _model("gpt-5.6-terra", "GPT-5.6 Terra", tag="advanced", vision=True),
            _model("gpt-5.6-luna-pro", "GPT-5.6 Luna Pro", vision=True),
            _model(OPENAI_DEFAULT_MODEL, "GPT-5.6 Luna", tag="fast", vision=True),
        ],
    },
    "google_gemini": {
        "display": "Google Gemini",
        "default_model": GEMINI_DEFAULT_MODEL,
        "available_models": [
            _model(GEMINI_DEFAULT_MODEL, "Gemini 3.8 Flash", tag="latest", vision=True),
            _model("gemini-3.7-flash", "Gemini 3.7 Flash", vision=True),
            _model("gemini-3.6-flash", "Gemini 3.6 Flash", vision=True),
            _model(
                "gemini-3.5-flash-lite",
                "Gemini 3.5 Flash Lite",
                tag="ultrafast",
                vision=True,
            ),
            _model(
                "gemini-3.1-pro-preview", "Gemini 3.1 Pro", tag="advanced", vision=True
            ),
        ],
    },
    "anthropic": {
        "display": "Anthropic Claude",
        "default_model": "claude-sonnet-5",
        "available_models": [
            _model(
                "claude-fable-5-1", "Claude Fable 5.1", tag="strongest", vision=True
            ),
            _model(
                "claude-opus-5", "Claude Opus 5", tag="latest_flagship", vision=True
            ),
            _model("claude-sonnet-5", "Claude Sonnet 5", vision=True),
            _model("claude-fable-5", "Claude Fable 5", vision=True),
            _model("claude-haiku-4-5", "Claude Haiku 4.5", tag="fast", vision=True),
        ],
    },
    "groq": {
        "display": "Groq",
        "default_model": "openai/gpt-oss-120b",
        "available_models": [
            _model("openai/gpt-oss-120b", "GPT-OSS 120B"),
            _model("openai/gpt-oss-20b", "GPT-OSS 20B", tag="fast"),
            _model(
                "meta-llama/llama-4-scout-17b-16e-instruct",
                "Llama 4 Scout 17B",
            ),
            _model("qwen/qwen3-32b", "Qwen3 32B"),
        ],
    },
    "openrouter": {
        "display": "OpenRouter",
        # 預設免費視覺模型：開箱即有圖片分析（vision Tier 1），也不會意外產生費用
        "default_model": "google/gemma-4-31b-it:free",
        "free_input": True,  # 保留自由輸入；下方清單以前端 datalist 呈現為建議選項
        # 2026-08-28 對 OpenRouter 官方目錄逐筆驗證（input_modalities 含 image → vision=True）
        "available_models": [
            _model(
                "google/gemma-4-31b-it:free", "Gemma 4 31B", tag="free", vision=True
            ),
            _model(
                "google/gemma-4-26b-a4b-it:free",
                "Gemma 4 26B A4B",
                tag="free",
                vision=True,
            ),
            _model(
                "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
                "Nemotron 3 Nano Omni 30B",
                tag="free",
                vision=True,
            ),
            _model("openrouter/free", "OpenRouter Free", tag="auto_route", vision=True),
            _model(
                "nvidia/nemotron-3-ultra-550b-a55b:free",
                "Nemotron 3 Ultra 550B",
                tag="free",
            ),
            _model("openai/gpt-6-astra", "GPT-6 Astra", tag="latest", vision=True),
            _model(
                "openai/gpt-6-astra-pro",
                "GPT-6 Astra Pro",
                tag="strongest",
                vision=True,
            ),
            _model("openai/gpt-5.6-luna-pro", "GPT-5.6 Luna Pro", vision=True),
            _model("openai/gpt-5.6-luna", "GPT-5.6 Luna", vision=True),
            _model(
                "anthropic/claude-fable-5.1",
                "Claude Fable 5.1",
                tag="strongest",
                vision=True,
            ),
            _model(
                "anthropic/claude-opus-5", "Claude Opus 5", tag="latest", vision=True
            ),
            _model("anthropic/claude-sonnet-5", "Claude Sonnet 5", vision=True),
            _model(
                "google/gemini-3.8-flash", "Gemini 3.8 Flash", tag="latest", vision=True
            ),
            _model("x-ai/grok-4.6", "Grok 4.6", tag="latest", vision=True),
            _model(
                "deepseek/deepseek-v4.1-flash",
                "DeepSeek V4.1 Flash",
                tag="latest",
                vision=True,
            ),
            _model("z-ai/glm-5.3-flash", "GLM-5.3 Flash", tag="latest", vision=True),
            _model("moonshotai/kimi-k3", "Kimi K3", tag="latest", vision=True),
            _model(
                "bytedance-seed/seed-2-1-turbo",
                "Seed 2-1 Turbo",
                tag="latest",
                vision=True,
            ),
            _model(
                "meta/muse-spark-1.3", "Meta Muse Spark 1.3", tag="latest", vision=True
            ),
            _model("minimax/minimax-m3", "MiniMax M3", tag="latest", vision=True),
            _model("qwen/qwen3.8-flash", "Qwen3.8 Flash", tag="latest", vision=True),
            _model("anthropic/claude-fable-5", "Claude Fable 5", vision=True),
            _model("deepseek/deepseek-v4-pro", "DeepSeek V4 Pro"),
        ],
    },
    "deepseek": {
        "display": "DeepSeek",
        "default_model": DEEPSEEK_DEFAULT_MODEL,
        # 2026-09-21：官方 model 名改為 deepseek-flash（= V4.1 Flash，1M context、支援圖片）
        "available_models": [
            _model(
                DEEPSEEK_DEFAULT_MODEL, "DeepSeek V4.1 Flash", tag="latest", vision=True
            ),
            _model(DEEPSEEK_PRO_MODEL, "DeepSeek V4 Pro", tag="deep"),
        ],
    },
    "siliconflow": {
        "display": "SiliconFlow",
        "default_model": "deepseek-ai/DeepSeek-V3.2",
        "available_models": [
            _model("deepseek-ai/DeepSeek-V3.2", "DeepSeek V3.2"),
            _model("Qwen/Qwen3-235B-A22B-Thinking-2507", "Qwen3 235B Thinking"),
            _model("Qwen/Qwen3-235B-A22B-Instruct-2507", "Qwen3 235B Instruct"),
            _model("THUDM/glm-5.1", "GLM-5.1"),
        ],
    },
    "moonshot": {
        "display": "Kimi (Moonshot)",
        "default_model": "kimi-k2.6",
        "available_models": [
            _model("kimi-k2.6", "Kimi K2.6"),
        ],
    },
    "dashscope": {
        "display": "Qwen (DashScope)",
        "default_model": "qwen3.6-plus",
        "available_models": [
            _model("qwen3.6-plus", "Qwen3.6 Plus", tag="deep"),
            _model("qwen3.6-flash", "Qwen3.6 Flash", tag="fast"),
        ],
    },
    "zhipu": {
        "display": "Zhipu GLM",
        "default_model": "glm-5.1",
        "available_models": [
            _model("glm-5.1", "GLM-5.1"),
        ],
    },
    "minimax": {
        "display": "MiniMax",
        "default_model": "MiniMax-M2.7",
        "available_models": [
            _model("MiniMax-M2.7", "MiniMax M2.7"),
            _model("MiniMax-M2.7-highspeed", "MiniMax M2.7 Highspeed", tag="highspeed"),
        ],
    },
    "volcengine": {
        "display": "Doubao (Volcengine)",
        "default_model": "doubao-seed-1-6-251015",
        "free_input": True,  # 豆包 endpoint/模型 ID 因開通地域而異，自行輸入較穩
        "available_models": [],
    },
    "nvidia": {
        "display": "NVIDIA NIM",
        # 2026-09-12：minimaxai/minimax-m3 於 2026-09-09 下架（410 Gone，所有
        # NVIDIA 使用者每題失敗）。改 deepseek-v4-flash（工具原生、中文強、
        # 2026-09-06 延遲量測就是用它）。舊名見 MODEL_ALIASES。
        "default_model": "deepseek-ai/deepseek-v4-flash-0731",
        "free_input": True,  # NVIDIA NIM 模型眾多，讓用戶自行輸入
        "available_models": [],
    },
    # 平台自架模型（2026-09-22 DANNY）：每個使用者預設就有的一條綁定（repo 合成，見
    # user_api_keys_repo.platform_binding），放最後＝解析順序上有真金鑰的 provider 優先；
    # 前端下拉／綁定清單另外把 keyless 排最前面。
    # keyless=True → 前端不顯示金鑰欄、不打 validate-key；後端 resolve_user_llm_credentials
    # 看到 preferred=local_llama 直接回平台憑證。模型值用品牌別名，底層模型名不出現在前端。
    "local_llama": {
        "display": PLATFORM_FREE_MODEL_LABEL,
        "default_model": "cryptomind-lite",
        "keyless": True,
        "available_models": [
            _model("cryptomind-lite", PLATFORM_FREE_MODEL_LABEL, tag="free")
        ],
    },
}


def get_provider_runtime(provider: str) -> dict | None:
    """
    取得 provider 的執行期路由設定（lc_provider / base_url / api_key_envs）。

    這是所有後端 factory 的路由依據——拿不到（回 None）代表不支援該 provider。
    """
    return PROVIDER_REGISTRY.get(provider)


def resolve_server_api_key(provider: str) -> str:
    """
    依註冊表的 api_key_envs，從環境變數依序解析 server 端 API key。

    Returns:
        str: 找到的第一個非空 key，否則空字串。
    """
    import os

    runtime = PROVIDER_REGISTRY.get(provider)
    if not runtime:
        return ""
    for env_name in runtime.get("api_key_envs", []):
        value = os.getenv(env_name)
        if value:
            return value
    if runtime.get("api_key_optional"):
        return "local"  # llama-server 不驗 key；SDK 要求非空字串
    return ""


def is_keyless_provider(provider: str) -> bool:
    """平台提供、不需使用者金鑰的 provider（前端不顯示金鑰欄）。"""
    return bool(MODEL_CONFIG.get(provider, {}).get("keyless", False))


def keyless_providers() -> list[str]:
    return [p for p, cfg in MODEL_CONFIG.items() if cfg.get("keyless")]


def is_free_input_provider(provider: str) -> bool:
    """該 provider 是否讓用戶自由輸入模型名（前端用文字框而非下拉）。"""
    return bool(MODEL_CONFIG.get(provider, {}).get("free_input", False))


def get_available_models(provider: str) -> list[dict[str, str]]:
    """
    獲取指定提供商的可用模型列表

    Args:
        provider (str): 提供商名稱

    Returns:
        list: 模型配置列表，每個項目包含 'value' 和 'display' 鍵性
    """
    return MODEL_CONFIG.get(provider, {}).get("available_models", [])


# 已下架／改名的模型 → 現行替代品。使用者存過的模型名不會自己更新，
# 下架後每題都 410；在 client factory 這一層靜默改走替代模型並記 log。
MODEL_ALIASES: dict[str, dict[str, str]] = {
    # 品牌別名 → 本地 llama-server 的模型名（前端只看得到 cryptomind-lite）
    "local_llama": {"cryptomind-lite": LOCAL_LLAMA_DEFAULT_MODEL},
    "nvidia": {
        "minimaxai/minimax-m3": "deepseek-ai/deepseek-v4-flash-0731",
    },
}


def resolve_model_alias(provider: str, model: str | None) -> str | None:
    """下架模型名 → 替代模型；沒有別名就原樣回傳。"""
    if not model:
        return model
    return MODEL_ALIASES.get(provider, {}).get(model, model)


def get_default_model(provider: str) -> str:
    """
    獲取指定提供商的默認模型

    Args:
        provider (str): 提供商名稱

    Returns:
        str: 默認模型名稱
    """
    if is_local_provider(provider):
        return (
            LOCAL_LLAMA_DEFAULT_MODEL  # 不在 MODEL_CONFIG（不讓使用者選），預設模型另給
        )
    return MODEL_CONFIG.get(provider, {}).get("default_model", OPENAI_DEFAULT_MODEL)


def get_all_providers():
    """
    獲取所有支持的提供商列表

    Returns:
        list: 支持的提供商名稱列表
    """
    return list(MODEL_CONFIG.keys())


def is_valid_model(provider, model_name):
    """
    檢查指定提供商是否支持特定模型

    Args:
        provider (str): 提供商名稱
        model_name (str): 模型名稱

    Returns:
        bool: 模型是否有效
    """
    available_models = get_available_models(provider)
    return any(model["value"] == model_name for model in available_models)


def answer_model_label(provider: str | None, model_value: str | None) -> str:
    """回答下方「由哪個模型回答」的名稱。

    平台自架模型一律顯示品牌名（PLATFORM_FREE_MODEL_LABEL），底層模型名不外露；
    其他 provider 用清單上的顯示名稱，查不到就用模型 ID。
    """
    if is_local_provider(provider) or is_keyless_provider(provider or ""):
        return PLATFORM_FREE_MODEL_LABEL
    if model_value:
        return get_model_display_name(provider, model_value) or model_value
    return MODEL_CONFIG.get(provider or "", {}).get("display") or (provider or "")


def get_model_display_name(provider, model_value):
    """
    獲取模型的顯示名稱

    Args:
        provider (str): 提供商名稱
        model_value (str): 模型值

    Returns:
        str: 模型的顯示名稱，如果找不到則返回模型值本身
    """
    available_models = get_available_models(provider)
    for model in available_models:
        if model["value"] == model_value:
            return model["display"]
    return model_value  # 如果找不到顯示名稱，返回模型值本身
