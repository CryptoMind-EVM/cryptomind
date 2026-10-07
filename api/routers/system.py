import asyncio
import logging
import os
import re
from logging.handlers import RotatingFileHandler

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

# LangChain Imports
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

import core.config as core_config
from api.deps import get_current_user, require_admin
from api.middleware.rate_limit import limiter
from api.models import KeyValidationRequest, UserSettings
from api.utils import logger, run_sync, update_env_file
from core import auth_google, feature_flags, platform
from core.config import DEFAULT_INTERVAL, DEFAULT_KLINES_LIMIT, SUPPORTED_EXCHANGES
from core.email_brief.provider import email_brief_available
from core.model_config import (
    MODEL_CONFIG,
    PROVIDER_REGISTRY,
    get_default_model,
    get_provider_runtime,
    is_free_input_provider,
)
from core.orm.config_repo import config_repo
from utils.llm_client import LLMClientFactory
from utils.settings import Settings

router = APIRouter()

# /api/settings/validate-key 回傳訊息的多語系文案。
# 這裡是靜態文字（不是 LLM 生成），所以用固定字典而非 PromptRegistry。
_VALIDATE_KEY_MESSAGES = {
    "key_too_short": {
        "zh-TW": "Key 為空或過短",
        "en": "API key is empty or too short",
        "ru": "Ключ пуст или слишком короткий",
    },
    "unknown_provider": {
        "zh-TW": "未知的提供商",
        "en": "Unknown provider",
        "ru": "Неизвестный провайдер",
    },
    "success": {
        "zh-TW": "驗證成功！連接正常。使用模型: {model}",
        "en": "Verification succeeded! Connection is working. Model used: {model}",
        "ru": "Проверка успешна! Соединение работает. Используемая модель: {model}",
    },
    "timeout": {
        "zh-TW": "驗證失敗: 請求超時，請檢查網絡連接或稍後再試。",
        "en": "Verification failed: the request timed out. Please check your network connection or try again later.",
        "ru": "Проверка не удалась: тайм-аут запроса. Проверьте подключение к сети или попробуйте позже.",
    },
    # 金鑰本身已通過認證，只是測試對話沒在時限內跑完（reasoning 模型常態）。
    # 這是「通過」不是「失敗」——金鑰可以存檔照用。
    "auth_ok_model_slow": {
        "zh-TW": "金鑰驗證通過。{model} 是思考型模型，測試對話未在時限內回覆（實際使用時不受此限制），可直接儲存。",
        "en": "API key verified. {model} is a reasoning model and did not finish the test reply in time (normal use is not limited this way) — you can save it.",
        "ru": "Ключ проверен. {model} — рассуждающая модель и не успела ответить в отведённое время (обычное использование не ограничено) — можно сохранять.",
    },
    "auth_ok_model_failed": {
        "zh-TW": "金鑰驗證通過，但用 {model} 測試對話失敗：{error}。請確認模型名稱，或換一個模型再試。",
        "en": "API key verified, but the test chat with {model} failed: {error}. Check the model name or try another model.",
        "ru": "Ключ проверен, но тестовый запрос к {model} не удался: {error}. Проверьте имя модели или выберите другую.",
    },
    "auth_failed": {
        "zh-TW": "認證失敗 (401)，請檢查 Key 是否正確。",
        "en": "Authentication failed (401). Please check that the key is correct.",
        "ru": "Ошибка авторизации (401). Проверьте правильность ключа.",
    },
    "quota_exceeded": {
        "zh-TW": "額度不足或請求過多 (429)，請檢查您的計費詳情和用量限制。",
        "en": "Insufficient quota or too many requests (429). Please check your billing details and rate limits.",
        "ru": "Недостаточно квоты или слишком много запросов (429). Проверьте детали биллинга и лимиты.",
    },
    "model_unavailable": {
        "zh-TW": "模型目前不可用 (404)。可能原因：① 模型名稱錯誤 ② Free tier 暫時無負載（稍後重試）③ 模型已下架。",
        "en": "Model is currently unavailable (404). Possible reasons: 1) wrong model name 2) free tier temporarily has no capacity (try again later) 3) model was deprecated.",
        "ru": "Модель сейчас недоступна (404). Возможные причины: 1) неверное имя модели 2) на бесплатном тарифе временно нет мощностей (повторите позже) 3) модель устарела.",
    },
    "invalid_key": {
        "zh-TW": "API Key 無效，請檢查 Key 是否正確。",
        "en": "Invalid API key. Please check that the key is correct.",
        "ru": "Недействительный API ключ. Проверьте правильность ключа.",
    },
    "service_not_enabled": {
        "zh-TW": "API 服務未啟用，請確保已在 Google Cloud Console 中啟用 Generative Language API。",
        "en": "The API service is not enabled. Please make sure the Generative Language API is enabled in Google Cloud Console.",
        "ru": "API-сервис не включён. Убедитесь, что Generative Language API включён в Google Cloud Console.",
    },
    "bad_request": {
        "zh-TW": "請求參數錯誤 (400)。請檢查模型名稱是否正確。",
        "en": "Bad request (400). Please check that the model name is correct.",
        "ru": "Неверный запрос (400). Проверьте правильность имени модели.",
    },
    "connection_error": {
        "zh-TW": "連接超時或網絡問題。請檢查網絡連接。",
        "en": "Connection timed out or a network issue occurred. Please check your network connection.",
        "ru": "Тайм-аут соединения или проблема с сетью. Проверьте подключение к сети.",
    },
    "verification_failed_prefix": {
        "zh-TW": "驗證失敗: {error}",
        "en": "Verification failed: {error}",
        "ru": "Проверка не удалась: {error}",
    },
    # --- /api/settings/discover-models（載入模型清單）文案 ---
    "discover_no_key": {
        "zh-TW": "缺少 API Key，請先輸入金鑰再載入模型。",
        "en": "Missing API key. Please enter a key before loading models.",
        "ru": "Отсутствует API ключ. Введите ключ, прежде чем загружать модели.",
    },
    "discover_no_models": {
        "zh-TW": "未取得任何模型，請改用手動輸入模型名稱。",
        "en": "No models returned. Please enter a model name manually instead.",
        "ru": "Модели не получены. Введите название модели вручную.",
    },
    "discover_timeout": {
        "zh-TW": "載入模型逾時，請稍後再試。",
        "en": "Loading models timed out. Please try again later.",
        "ru": "Тайм-аут загрузки моделей. Попробуйте позже.",
    },
    "discover_auth_failed": {
        "zh-TW": "認證失敗，請檢查 API Key 是否正確。",
        "en": "Authentication failed. Please check that the API key is correct.",
        "ru": "Ошибка авторизации. Проверьте правильность API ключа.",
    },
    "discover_no_list_support": {
        "zh-TW": "此供應商不支援列出模型，請改用手動輸入。",
        "en": "This provider does not support listing models. Please enter one manually.",
        "ru": "Этот провайдер не поддерживает список моделей. Введите вручную.",
    },
    "discover_rate_limited": {
        "zh-TW": "請求過於頻繁 (429)，請稍後再試。",
        "en": "Too many requests (429). Please try again later.",
        "ru": "Слишком много запросов (429). Попробуйте позже.",
    },
    "discover_http_error": {
        "zh-TW": "載入模型失敗 (HTTP {code})。",
        "en": "Failed to load models (HTTP {code}).",
        "ru": "Не удалось загрузить модели (HTTP {code}).",
    },
    "discover_failed": {
        "zh-TW": "載入模型失敗，請改用手動輸入模型名稱。",
        "en": "Failed to load models. Please enter a model name manually instead.",
        "ru": "Не удалось загрузить модели. Введите название модели вручную.",
    },
}


# list-models 端點是否真的在驗金鑰（每個 provider 探一次就快取，process 生命週期）。
# 不能預設「有回 200 就代表金鑰有效」——有些 provider 的模型目錄是公開的，
# 那樣會把無效金鑰判成有效。
_LIST_MODELS_AUTH_GATED: dict[str, bool] = {}
_BOGUS_KEY_PROBE = "sk-invalid-key-probe-0000000000000000"


async def _models_endpoint_is_auth_gated(provider: str, runtime: dict) -> bool:
    """
    拿一把明顯無效的金鑰打一次 list-models：被擋（401/403）才證明這個端點真的
    在驗金鑰，之後才敢把「list-models 成功」當成「金鑰有效」的證據。
    端點是公開目錄（無效金鑰也回 200）時回 False，驗證就退回原本的測試對話。
    """
    if provider in _LIST_MODELS_AUTH_GATED:
        return _LIST_MODELS_AUTH_GATED[provider]
    gated = False
    try:
        await asyncio.wait_for(
            _discover_provider_models(provider, runtime, _BOGUS_KEY_PROBE), timeout=10.0
        )
        gated = False  # 假金鑰也拿得到清單 → 這個端點不驗金鑰
    except httpx.HTTPStatusError as e:
        gated = e.response.status_code in (401, 403)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        # 逾時／網路問題：無法判定，這次不快取，下次再探
        return False
    _LIST_MODELS_AUTH_GATED[provider] = gated
    return gated


async def _preflight_key_auth(
    provider: str, runtime: dict, api_key: str
) -> tuple[str, set[str]]:
    """
    先用 provider 的 list-models 端點確認「金鑰本身」有效，再決定要不要跑測試對話。

    為什麼要這一步（2026-09-02 DANNY 回報「模型測試都不會通過，明明都是正確的」
    ＋「一直卡在測試中」）：驗證只靠一次測試對話，把「金鑰對不對」跟「這顆模型
    此刻回得夠不夠快」綁死了。思考型模型（minimaxai/minimax-m3、nemotron、GLM）
    在 NVIDIA 公開端點上光 reasoning 就可能跑掉一分鐘以上，前端等到 abort 就報
    失敗——金鑰完全正確卻永遠測不過，連存檔都被擋（前端要 test 通過才開放儲存）。
    list-models 是一次幾百毫秒的認證探測，能把兩件事拆開來判，而且順手拿到該
    金鑰可用的模型清單。

    Returns: (state, model_ids)
      state = "ok" 金鑰有效 / "auth_failed" 認證被拒 / "unknown" 無法判定
              （端點不支援列模型、逾時、網路問題——不能因此判金鑰無效）
      model_ids = 該金鑰可用的模型 ID 集合（unknown 時為空集合）
    """
    try:
        models = await asyncio.wait_for(
            _discover_provider_models(provider, runtime, api_key), timeout=12.0
        )
        ids = {m["value"] for m in models if m.get("value")}
        # 成功不等於「金鑰有效」——端點得先證明它真的會擋無效金鑰
        if not await _models_endpoint_is_auth_gated(provider, runtime):
            return "unknown", ids
        return "ok", ids
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code in (401, 403):
            return "auth_failed", set()
        # 404（端點沒有 /models）、429、5xx 都不能拿來判金鑰無效
        return "unknown", set()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # 逾時／連線失敗／回應格式非預期
        logger.debug(f"Key preflight inconclusive for {provider}: {e}")
        return "unknown", set()


def _validate_key_msg(key: str, language: str, **kwargs) -> str:
    """依 language 取得 /api/settings/validate-key 的靜態文案；language 不支援時退回 zh-TW。"""
    entry = _VALIDATE_KEY_MESSAGES[key]
    template = entry.get(language, entry["zh-TW"])
    return template.format(**kwargs) if kwargs else template


# Get project root from sys.path or os.getcwd
project_root = os.getcwd()

# Reuse the same RotatingFileHandler logger from api_server.py
_frontend_debug_logger = logging.getLogger("frontend_debug")

# NOTE: POST and GET /api/debug/log are removed — api_server.py already
# provides safer versions at /api/debug-log (RotatingFileHandler, max length).


@router.delete("/api/debug/log", dependencies=[Depends(require_admin)])
async def clear_debug_log():
    """清空 frontend_debug.log (Admin only) — resets the rotating handler."""
    try:
        # Remove all handlers from the logger and close them
        for handler in list(_frontend_debug_logger.handlers):
            handler.close()
            _frontend_debug_logger.removeHandler(handler)

        # Truncate the log file
        log_path = os.path.join(project_root, "frontend_debug.log")
        if os.path.exists(log_path):
            os.truncate(log_path, 0)

        # Re-create the RotatingFileHandler
        rotating_handler = RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        rotating_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        _frontend_debug_logger.addHandler(rotating_handler)

        return {"success": True, "message": "Log cleared"}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Failed to clear debug log: {e}")
        return {"success": False, "error": "Failed to clear logs"}


@router.get("/health")
async def health_check():
    """健康檢查端點"""
    return {"status": "ok", "service": "Crypto Trading API"}


@router.post("/api/settings/validate-key")
@limiter.limit("10/minute")  # 🔒 Security: 防止滥用 LLM 验证
async def validate_key(
    body: KeyValidationRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """測試 API Key 是否有效，並嘗試進行對話"""
    provider = body.provider
    key = body.api_key
    user_model = body.model  # 用戶選擇的模型
    language = body.language  # 用戶介面語言，決定回傳訊息語言

    # Audit log for sensitive operation
    try:
        from core.audit import audit_log

        audit_log(
            action="api_key_validation",
            user_id=current_user.get("user_id"),
            metadata={"provider": provider},
        )
    except ImportError:
        logger.warning("core.audit module not available, audit logging disabled")

    if not key or len(key) < 5:
        return {"valid": False, "message": _validate_key_msg("key_too_short", language)}

    # 極短、答案明確的 prompt：只需確認連線與金鑰有效，不需長回覆。
    # reasoning 模型（GLM-5.2 / nemotron / gpt-oss）會對開放性問題展開完整思考鏈，
    # 浪費時間與額度；「Say ok」這類閉合式 prompt 能讓它快速收斂。
    test_prompt = 'Reply with the single word "ok" to confirm the connection works.'

    # preflight 之前就爆掉時 except 區塊也讀得到（否則 NameError 蓋掉真正的錯）
    auth_state = "unknown"
    model_to_test = user_model or ""

    try:
        reply_text = ""

        # 統一使用 LangChain init_chat_model 進行驗證
        # 路由完全來自 PROVIDER_REGISTRY（單一真實來源），新增 provider 不必改這裡。
        runtime = get_provider_runtime(provider)
        if not runtime:
            return {
                "valid": False,
                "message": _validate_key_msg("unknown_provider", language),
            }

        lc_provider = runtime["lc_provider"]
        base_url = runtime["base_url"]

        # 決定模型名稱：用戶指定優先，否則用該 provider 的預設模型
        model_to_test = user_model or get_default_model(provider)

        # 先做一次快速認證探測（見 _preflight_key_auth）：金鑰錯就立刻回，
        # 不必等一輪慢吞吞的測試對話；金鑰對的話，後面測試對話逾時／模型出錯
        # 都不會再把「金鑰無效」的帽子扣到使用者頭上。
        auth_state, known_models = await _preflight_key_auth(provider, runtime, key)
        if auth_state == "auth_failed":
            return {
                "valid": False,
                "message": _validate_key_msg("auth_failed", language),
            }

        # 金鑰有效、而且 provider 自己列出了這顆模型 → 兩件要驗的事都成立了，
        # 直接放行。不必再叫思考型模型生一句「ok」讓使用者對著轉圈等一分鐘
        # （2026-09-02 回報「一直卡在測試中」的主因）。
        if auth_state == "ok" and model_to_test in known_models:
            return {
                "valid": True,
                "message": _validate_key_msg("success", language, model=model_to_test),
                "reply": "",
                "provider": provider,
                "model": model_to_test,
                "verified_via": "models_endpoint",
            }

        # 嘗試初始化 LLM
        # max_tokens=256：驗證只需一句簡短回覆，取值需在兩個約束間取平衡。
        #   1) 不能不設：未指定時 OpenRouter 會以模型上限(常達 65536)預扣額度，
        #      免費/低餘額帳號「付不起」而驗證失敗——這是「時好時壞」的主因。
        #   2) 不能太小：reasoning 模型（如 NVIDIA openai/gpt-oss-120b）會先花 token
        #      思考，上限太小(如 16)整個額度被 reasoning 吃光，content 回傳 None、
        #      finish_reason=length——表現為「沒有任何回應」。256 對極短閉合式 prompt
        #      足夠（之前 1024 偏大、徒增 reasoning 模型的思考時間）。
        # 註：無法在此統一關掉思考——reasoning_effort 等參數各家不同，且對 OpenAI
        #     非推理模型(gpt-4o)傳入會 400，反而弄壞其他 provider 的驗證。故改以
        #     足夠寬裕的 max_tokens 確保各家都能吐出 content（一次性呼叫，成本可忽略）。
        # request_timeout=60：NVIDIA 端點（integrate.api.nvidia.com）回應速度極不穩定
        # （GLM-5.2 實測 0.8s~27s），加上 Zeabur 跨洋部署的網路延遲，預設 SDK timeout
        # 可能不夠。設 60s 上限避免 SDK 內部先斷，外層 asyncio.wait_for 另有 45s 保護。
        llm = init_chat_model(
            model=model_to_test,
            model_provider=lc_provider,
            temperature=0,
            api_key=key,
            base_url=base_url,
            max_tokens=256,
            request_timeout=60,
        )

        # 60s timeout — reasoning 模型（minimax-m3/GLM/nemotron）在 NVIDIA 公開端點
        # ＋跨洋部署下回應慢且不穩定（實測 0.8s~27s，思考鏈長時更久）。前端配合
        # 設 90s（要大於 preflight 12s + 這裡 60s，否則前端先 abort 誤報失敗）。
        response = await asyncio.wait_for(
            run_sync(lambda: llm.invoke([HumanMessage(content=test_prompt)])),
            timeout=60.0,
        )
        reply_text = response.content
        if isinstance(reply_text, list):
            reply_text = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in reply_text
            )

        return {
            "valid": True,
            "message": _validate_key_msg("success", language, model=model_to_test),
            "reply": reply_text,
            "provider": provider,
            "model": model_to_test,
        }

    except asyncio.TimeoutError:
        logger.warning(f"Key validation timed out for {provider}")
        # 認證已經過了，只是這顆模型思考太久——這是通過，不是失敗。
        # 否則使用者拿著完全正確的金鑰永遠測不過、連儲存都被前端擋住。
        if auth_state == "ok":
            return {
                "valid": True,
                "message": _validate_key_msg(
                    "auth_ok_model_slow", language, model=model_to_test
                ),
                "provider": provider,
                "model": model_to_test,
                "model_verified": False,
            }
        return {
            "valid": False,
            "message": _validate_key_msg("timeout", language),
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"Key validation failed for {provider}: {e}")
        error_msg = str(e)
        is_auth_error = False
        if (
            "401" in error_msg
            or "auth" in error_msg.lower()
            or "unauthorized" in error_msg.lower()
        ):
            error_msg = _validate_key_msg("auth_failed", language)
            is_auth_error = True
        elif (
            "429" in error_msg
            or "quota" in error_msg.lower()
            or "rate" in error_msg.lower()
            or "exceeded your current quota" in error_msg.lower()
        ):
            error_msg = _validate_key_msg("quota_exceeded", language)
        elif "404" in error_msg or "not found" in error_msg.lower():
            error_msg = _validate_key_msg("model_unavailable", language)
        elif "400" in error_msg:
            if "API_KEY_INVALID" in error_msg or "API key not valid" in error_msg:
                error_msg = _validate_key_msg("invalid_key", language)
                is_auth_error = True
            elif "access not configured" in error_msg.lower():
                error_msg = _validate_key_msg("service_not_enabled", language)
            else:
                error_msg = _validate_key_msg("bad_request", language)
        elif "connection" in error_msg.lower() or "timeout" in error_msg.lower():
            error_msg = _validate_key_msg("connection_error", language)

        # 金鑰已被 preflight 證實有效時，錯的是「這顆模型」而不是金鑰——
        # 訊息要講清楚是哪一邊壞了，別讓使用者一直重打正確的金鑰。
        # 但對話本身回 401 時以對話為準（preflight 可能只驗了目錄權限）。
        if auth_state == "ok" and not is_auth_error:
            return {
                "valid": False,
                "message": _validate_key_msg(
                    "auth_ok_model_failed",
                    language,
                    model=model_to_test or "-",
                    error=error_msg,
                ),
                "key_verified": True,
            }

        return {
            "valid": False,
            "message": _validate_key_msg(
                "verification_failed_prefix", language, error=error_msg
            ),
        }


async def _discover_provider_models(
    provider: str, runtime: dict, api_key: str
) -> list[dict]:
    """
    即時向 provider 查詢可用模型清單。

    路由依 PROVIDER_REGISTRY 的 lc_provider：
      - openai 相容：GET {base_url}/models（Bearer 認證）
      - google_genai：Generative Language API list models
      - anthropic：GET /v1/models（x-api-key 認證）

    Returns: [{"value": <model_id>, "display": <顯示名>}, ...]
    """
    lc_provider = runtime["lc_provider"]
    timeout = httpx.Timeout(15.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        if lc_provider == "google_genai":
            resp = await client.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": api_key},
            )
            resp.raise_for_status()
            out = []
            for m in resp.json().get("models", []):
                # 只保留支援 generateContent 的對話模型
                if "generateContent" not in m.get("supportedGenerationMethods", []):
                    continue
                name = (m.get("name") or "").split("/")[-1]
                if name:
                    out.append({"value": name, "display": m.get("displayName") or name})
            out.sort(key=lambda x: x["value"])
            return out

        if lc_provider == "anthropic":
            resp = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            resp.raise_for_status()
            return [
                {"value": m["id"], "display": m.get("display_name") or m["id"]}
                for m in resp.json().get("data", [])
                if m.get("id")
            ]

        # OpenAI 相容（含 openrouter / deepseek / groq / nvidia ... ）
        base = (runtime["base_url"] or "https://api.openai.com/v1").rstrip("/")
        resp = await client.get(
            f"{base}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data") or payload.get("models") or []
        out = []
        for m in data:
            mid = m.get("id") if isinstance(m, dict) else None
            if mid:
                out.append({"value": mid, "display": mid})
        out.sort(key=lambda x: x["value"])
        return out


@router.post("/api/settings/discover-models")
@limiter.limit("10/minute")  # 🔒 防止濫用 provider list-models API
async def discover_models(
    body: KeyValidationRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """即時向 provider 抓取可用模型清單（動態，不寫死）。"""
    provider = body.provider
    language = body.language  # 用戶介面語言，決定回傳訊息語言
    # 優先用前端帶入的 key，否則 fallback 到 server 端設定的 key
    key = (body.api_key or "").strip() or LLMClientFactory._get_api_key(provider)

    runtime = get_provider_runtime(provider)
    if not runtime:
        return {
            "success": False,
            "message": _validate_key_msg("unknown_provider", language),
        }
    if not key:
        return {
            "success": False,
            "message": _validate_key_msg("discover_no_key", language),
        }

    try:
        models = await asyncio.wait_for(
            _discover_provider_models(provider, runtime, key), timeout=20.0
        )
        if not models:
            return {
                "success": False,
                "message": _validate_key_msg("discover_no_models", language),
            }
        return {"success": True, "models": models, "count": len(models)}
    except asyncio.TimeoutError:
        return {
            "success": False,
            "message": _validate_key_msg("discover_timeout", language),
        }
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code in (401, 403):
            msg = _validate_key_msg("discover_auth_failed", language)
        elif code == 404:
            msg = _validate_key_msg("discover_no_list_support", language)
        elif code == 429:
            msg = _validate_key_msg("discover_rate_limited", language)
        else:
            msg = _validate_key_msg("discover_http_error", language, code=code)
        logger.warning(f"discover_models {provider} HTTP {code}")
        return {"success": False, "message": msg}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"discover_models failed for {provider}: {e}")
        return {
            "success": False,
            "message": _validate_key_msg("discover_failed", language),
        }


_TG_NAME_RE = re.compile(r"[A-Za-z0-9_]{3,64}")


def _telegram_miniapp_share() -> dict | None:
    """分享連結改用 ``https://t.me/<bot>/<short_name>?startapp=…`` 所需的兩個公開名稱。

    都來自環境變數；任一沒設或格式不對（會被拼進網址）就回 None，前端維持網頁連結。
    只放公開資訊（bot username、Mini App short name），沒有任何密鑰。
    """
    bot = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
    short_name = os.getenv("TELEGRAM_MINIAPP_SHORT_NAME", "").strip()
    if _TG_NAME_RE.fullmatch(bot) and _TG_NAME_RE.fullmatch(short_name):
        return {"bot_username": bot, "short_name": short_name}
    return None


@router.get("/api/config")
async def get_config(request: Request):
    """回傳前端需要的配置資訊"""
    current_provider = core_config.PRIMARY_MODEL.get("provider", "openai")

    # Helper to check key existence using Factory logic
    def has_key(provider):
        return bool(LLMClientFactory._get_api_key(provider))

    plat = platform.from_request(request)
    caps = platform.capabilities(plat)
    # 環境旗標與平台能力表取交集：任一邊關就關
    caps["crypto_tab"] = caps["crypto_tab"] and feature_flags.crypto_tab_enabled()

    response = {
        "supported_exchanges": SUPPORTED_EXCHANGES,
        "default_interval": DEFAULT_INTERVAL,
        "default_limit": DEFAULT_KLINES_LIMIT,
        "current_settings": {
            "primary_model_provider": current_provider,
            "primary_model_name": core_config.PRIMARY_MODEL.get("model"),
            "has_openai_key": has_key("openai"),
            "has_google_key": has_key("google_gemini"),
            "has_openrouter_key": has_key("openrouter"),
            "has_current_provider_key": has_key(current_provider),
        },
        "test_mode": core_config.TEST_MODE,
        # 平台情境（Play 版 TWA、Telegram、Base App、網頁）：前端照能力表顯示／隱藏入口
        "platform": plat,
        "capabilities": caps,
        # Telegram 內分享連結直接開 Mini App（沒設 env 就是 None，前端維持網頁連結）
        "telegram_miniapp": _telegram_miniapp_share(),
        # AI 回答快照分享（預設關）：前端只在這個為 true 且已登入時才顯示「分享這則回答」
        "answer_share": feature_flags.answer_share_enabled(),
        # Google 登入（公開的 Client ID；沒設就是空字串，前端不顯示按鈕）
        "google_client_id": auth_google.client_id(),
        # Email 早報（PR-8）：旗標開而且寄信設定齊全才 true，前端才顯示 Email 區塊
        "email_brief_enabled": email_brief_available(),
        # Email／Google 登入（Reown 內嵌錢包）＋刷卡買 USDC（PR-6，預設關）；
        # Telegram／Base App／Play 由前端 social-login.js 另外擋
        "reown_social_login": feature_flags.reown_social_login_enabled(),
    }

    return response


@router.get("/api/model-config")
async def get_model_config():
    """獲取模型配置資訊"""
    return {"model_config": MODEL_CONFIG}


# /api/config/prices（TON 報價）2026-09-26 移除：前端價格改讀 /api/premium/pricing（USDC）


@router.get("/api/config/limits")
async def get_forum_limits():
    """
    獲取論壇限制配置（從數據庫讀取）
    前端使用此 API 獲取動態限制，確保限制與後端驗證一致

    商用化設計：配置存儲在數據庫中，可通過管理 API 即時修改
    """
    return {"limits": await config_repo.get_limits()}


@router.post("/api/settings/update")
@limiter.limit("10/minute")
async def update_user_settings(
    settings: UserSettings,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    """
    更新用戶設置 (LLM API Keys, 模型選擇, 委員會模式)

    ⚠️ 安全改進: OKX API Keys 不再通過此端點處理
    - OKX Keys 現在使用 BYOK (Bring Your Own Keys) 模式
    - 金鑰僅存儲在用戶瀏覽器的 localStorage 中
    - 每次請求時從前端傳遞，後端不存儲
    """
    # Audit log for sensitive operation
    try:
        from core.audit import audit_log

        audit_log(
            action="settings_update",
            user_id=current_user.get("user_id"),
            metadata={"provider": settings.primary_model_provider},
        )
    except ImportError:
        logger.warning("core.audit module not available, audit logging disabled")

    try:
        provider = settings.primary_model_provider
        if provider and provider not in PROVIDER_REGISTRY:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported provider: {provider}",
            )

        if provider and is_free_input_provider(provider):
            model_name = (settings.primary_model_name or "").strip()
            if not model_name:
                raise HTTPException(
                    status_code=400,
                    detail="Model name is required for this provider.",
                )

        env_updates = {}

        # 1. Update LLM API Keys (Only LLM keys are stored in backend)
        if settings.openai_api_key:
            env_updates["OPENAI_API_KEY"] = settings.openai_api_key
            os.environ["OPENAI_API_KEY"] = settings.openai_api_key
            Settings.update(OPENAI_API_KEY=settings.openai_api_key)

        if settings.google_api_key:
            env_updates["GOOGLE_API_KEY"] = settings.google_api_key
            os.environ["GOOGLE_API_KEY"] = settings.google_api_key
            Settings.update(GOOGLE_API_KEY=settings.google_api_key)

        if settings.openrouter_api_key:
            env_updates["OPENROUTER_API_KEY"] = settings.openrouter_api_key
            os.environ["OPENROUTER_API_KEY"] = settings.openrouter_api_key
            Settings.update(OPENROUTER_API_KEY=settings.openrouter_api_key)

        # 2. Update Model Configuration
        new_model_config = {
            "provider": settings.primary_model_provider,
            "model": settings.primary_model_name,
        }

        logger.info(f"Updating model config to: {new_model_config}")

        # 更新核心配置中的模型定義（統一配置，向後兼容）
        core_config.PRIMARY_MODEL = new_model_config
        # 向後兼容：同步更新舊的別名（deprecated）
        core_config.BULL_RESEARCHER_MODEL = new_model_config
        core_config.BEAR_RESEARCHER_MODEL = new_model_config
        core_config.TRADER_MODEL = new_model_config
        core_config.SYNTHESIS_MODEL = new_model_config

        # 4. Save to .env file for persistence
        if env_updates:
            await run_sync(lambda: update_env_file(env_updates, project_root))

        return {
            "success": True,
            "message": "System settings updated! (Mode and model switched)",
        }

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Failed to update settings: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to update settings, please try again later"
        )


_ASK_MAX_LEN = 200


def apply_ask_share_meta(html: str, ask: str | None) -> str:
    """`/?ask=<問題>` 分享連結：把連結預覽（OG/Twitter）換成該問題。

    只改 meta 文字，不讀任何使用者資料；問題長度截斷並 HTML 跳脫，另加 noindex
    避免每個問題變成一個被收錄的薄頁面。
    """
    import html as _html
    import re as _re

    q = _re.sub(r"[\x00-\x1f\x7f\s]+", " ", ask or "").strip()[:_ASK_MAX_LEN]
    if not q:
        return html
    safe = _html.escape(q, quote=True)
    title = f"“{safe}” — CryptoMind AI"
    desc = (
        "Ask the AI market analyst this question for free — live data for crypto, "
        "US and Taiwan stocks. Analysis, not financial advice."
    )
    for prop in ("og:title", "twitter:title"):
        html = _re.sub(
            rf'(<meta (?:property|name)="{prop}" content=")[^"]*(")',
            lambda m: m.group(1) + title + m.group(2),
            html,
            count=1,
        )
    for prop in ("og:description", "twitter:description"):
        html = _re.sub(
            rf'(<meta (?:property|name)="{prop}" content=")[^"]*(")',
            lambda m: m.group(1) + desc + m.group(2),
            html,
            count=1,
        )
    # 頁面本來就有 robots meta（index, follow）時換掉它，不要並存兩個互相矛盾的指令
    noindex = '<meta name="robots" content="noindex">'
    html, replaced = _re.subn(
        r'<meta name="robots" content="[^"]*">', noindex, html, count=1
    )
    if replaced:
        return html
    return html.replace("</head>", noindex + "\n</head>", 1)


@router.get("/")
async def read_index(request: Request):
    """返回主頁面 index.html。

    2026-09-13：Base App／Farcaster 嵌入 meta（fc:miniapp）要帶絕對網址，靜態檔不知道
    自己的網域，所以這裡把 ``<!-- FC_MINIAPP_META -->`` 佔位符換成以請求來源組出的 meta。
    """
    if not os.path.exists("web/index.html"):
        return {"message": "Welcome to Crypto API. Frontend not found."}
    from fastapi.responses import HTMLResponse

    from api.public_base import resolve_public_base
    from core import miniapp

    html = await asyncio.to_thread(
        lambda: open("web/index.html", encoding="utf-8").read()
    )
    if miniapp.enabled():
        html = html.replace(
            "<!-- FC_MINIAPP_META -->",
            miniapp.embed_meta_tags(resolve_public_base(request)),
            1,
        )
    html = apply_ask_share_meta(html, request.query_params.get("ask"))
    return HTMLResponse(
        html, headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


# ============================================================================
# Test Mode Endpoints (僅在 TEST_MODE 啟用時可用)
# ============================================================================


class TestTierRequest(BaseModel):
    tier: str  # "free", "premium"


@router.post("/api/test-mode/switch-tier")
@limiter.limit("5/minute")
async def switch_test_tier(
    request: Request,
    body: TestTierRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    切換測試帳號的會員等級（僅測試模式）

    用於測試不同會員等級的功能：
    - free: 免費會員功能
    - premium: Premium 會員功能
    """
    from core.config import TEST_MODE

    if not TEST_MODE:
        raise HTTPException(
            status_code=403, detail="This feature is only available in test mode"
        )

    valid_tiers = ["free", "premium"]
    if body.tier not in valid_tiers:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tier, must be one of: {', '.join(valid_tiers)}",
        )

    os.environ["TEST_USER_TIER"] = body.tier

    logger.info(f"[TEST_MODE] Tier switched to: {body.tier}")

    return {
        "success": True,
        "tier": body.tier,
        "message": f"Test account switched to {body.tier.upper()} tier",
    }


@router.get("/api/test-mode/current-tier")
async def get_current_test_tier(current_user: dict = Depends(get_current_user)):
    """
    獲取當前測試帳號的會員等級（僅測試模式）。

    非 test mode 時回 200 + is_test_mode=False（而非 403），讓前端正常
    隱藏切換器，不在使用者 console 噴 resource loading error。
    """
    from core.config import TEST_MODE

    if not TEST_MODE:
        return {"tier": None, "is_test_mode": False}

    from core.orm.tools_repo import _normalize_tier as normalize_membership_tier

    current_tier = normalize_membership_tier(
        current_user.get("membership_tier", os.environ.get("TEST_USER_TIER", "premium"))
    )

    return {
        "tier": current_tier,
        "is_test_mode": True,
        "user_id": current_user.get("user_id"),
    }
