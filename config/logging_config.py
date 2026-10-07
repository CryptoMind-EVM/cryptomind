import json
import logging
import re


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


def setup_json_logging(level: int = logging.WARNING) -> None:
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(JSONFormatter())
    logging.root.addHandler(handler)
    logging.root.setLevel(level)


# 網址裡的金鑰一律遮掉：httpx 在 INFO 會印完整請求網址（FRED 的 api_key、Etherscan 的
# apikey 都放在 query string），HTTPStatusError 的訊息與 traceback 也帶網址；
# Telegram bot token 則在路徑裡。2026-09-27 cron-worker 的 log 印出過 FRED 金鑰。
_SECRET_QUERY_RE = re.compile(
    r"(?i)([?&](?:api_?key|key|token|secret|access_token|auth)=)[^&\s\"'<>]+"
)
_TELEGRAM_BOT_RE = re.compile(r"(api\.telegram\.org/(?:file/)?bot)[^/\s\"'<>]+")


def redact_secrets(text: str) -> str:
    text = _SECRET_QUERY_RE.sub(r"\1***", text)
    return _TELEGRAM_BOT_RE.sub(r"\1***", text)


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — 格式化失敗交給 handler 原本的處理
            return True
        redacted = redact_secrets(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)
        return True


_REDACTION_FILTER = SecretRedactionFilter()
# 自己發 log、handler 可能不經 root 的 logger（存取紀錄的網址可能帶 ?token= 確認連結）
_DIRECT_LOGGERS = (
    "httpx",
    "httpcore",
    "uvicorn.access",
    "uvicorn.error",
    "gunicorn.access",
    "gunicorn.error",
)


def install_secret_redaction() -> None:
    """logging 設定完之後呼叫：root 的每個 handler＋上面幾個 logger 都掛遮蔽 filter。可重複呼叫。"""
    targets = list(logging.getLogger().handlers)
    targets += [logging.getLogger(name) for name in _DIRECT_LOGGERS]
    for target in targets:
        if _REDACTION_FILTER not in target.filters:
            target.addFilter(_REDACTION_FILTER)
