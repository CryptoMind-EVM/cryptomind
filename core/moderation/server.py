"""moderation 容器：python -m core.moderation.server

跟 analysis-worker 一樣共用 app image、只換入口。v7 起模型在旁邊的 moderation-llm（llama.cpp）跑，
這裡只放斷字檔（第一次啟動從 Hugging Face 下載，約 11 MB）。
GET  /health   → 斷字檔載好、而且 llama-server 載好模型才 200，否則 503（帶原因，後台狀態列看得到）
POST /classify → {"text": "..."} → {"block": 0~1（該擋的機率）, "category": 風險代碼, "chunks": n, "ms": n}
只給 compose 內網的 app 呼叫，不對外開 port。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.moderation.detector import (
    VERSION,
    ModerationModel,
    default_llama_url,
    default_model_dir,
    ensure_model,
)

logger = logging.getLogger("moderation")
MAX_TEXT_CHARS = 20_000

_detector = None
_load_error = None


def _load() -> None:
    global _detector, _load_error
    try:
        model_dir = default_model_dir()
        ensure_model(model_dir)
        _detector = ModerationModel(model_dir, default_llama_url())
        logger.info("[moderation] model ready (%s)", VERSION)
    except Exception as exc:  # noqa: BLE001 — 載入失敗只讓 /health 回 503，app 那邊照常發文
        _load_error = str(exc)
        logger.exception("[moderation] model load failed")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path != "/health":
            return self._send(404, {"error": "not found"})
        if _detector is None:
            return self._send(503, {"ok": False, "error": _load_error or "loading"})
        ok, error = _detector.llama_health()
        if not ok:
            return self._send(503, {"ok": False, "error": error or "llama-server loading", "revision": VERSION})
        return self._send(200, {"ok": True, "revision": VERSION})

    def do_POST(self):  # noqa: N802
        if self.path != "/classify":
            return self._send(404, {"error": "not found"})
        if _detector is None:
            return self._send(503, {"error": "model not ready"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(min(length, MAX_TEXT_CHARS * 4)) or b"{}")
            text = str(payload.get("text") or "")[:MAX_TEXT_CHARS]
        except (ValueError, TypeError):
            return self._send(400, {"error": "bad request"})
        try:
            return self._send(200, _detector.classify(text))
        except Exception:  # noqa: BLE001
            logger.exception("[moderation] classify failed")
            return self._send(500, {"error": "classify failed"})

    def log_message(self, *args):  # 每次請求一行 access log 太吵
        pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    threading.Thread(target=_load, daemon=True).start()
    port = int(os.getenv("MODERATION_PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    logger.info("[moderation] listening on :%d", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
