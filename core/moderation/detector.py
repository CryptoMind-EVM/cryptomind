"""論壇內容檢查模型 v7：CryptoMind-Guard-0.6B（自己微調的 YuFeng-XGuard-Reason-0.6B），llama.cpp 跑 GGUF Q8_0。

2026-10-03 起取代 v4（Laya 322M）：Laya 練到 v6，涉及未成年的性內容 @0.9 只擋 71～76% 就卡住；
v7 同一份考題 @0.8：涉及未成年的性內容擋 93.4%、論壇有害 97.0%、論壇正常文誤擋 0.8%（DANNY 選門檻 0.8）。
訓練在 scripts/moderation_dataset/train_llm.py，照 YuFeng 官方格式：回答的第一個 token 是風險代碼（sec＝安全）。
模型公開在 https://huggingface.co/aaaa47080/CryptoMind-Guard-0.6B（量化版；未量化原版在私人 repo）。

為什麼走 llama.cpp 不在這裡跑 ONNX（實測，2 核 CPU）：Q8_0 跟原版幾乎一樣（門檻 0.8 判斷翻轉 5/1290，
int8 ONNX 23/1290）；每篇都一樣的 299 token 題目 llama.cpp 會快取，中位數 388 ms（int8 ONNX 1.09 s、
最慢 7.3 s 會撞逾時）；搬到主機後改 MODERATION_LLAMA_URL 指向 iGPU（Vulkan）上的 llama-server 就好。

這個容器只做：斷字、長文切頭尾、組 prefix＋貼文＋suffix 的 token、呼叫 llama-server 拿下一個 token 的機率、算分數。
輸入格式不自己拼：照模型附的 readout.json（YuFeng chat template 切成的 prefix／suffix token），
跟訓練、跟官方 template 逐 token 一樣。分數＝1 − P(sec)，P 只在 29 個風險代碼之間正規化。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

VERSION = "v7"
# 斷字與輸入格式的檔案（模型權重 GGUF 由 moderation-model-fetch 下載給 llama-server）。公開 repo，不用 token；
# 有 HF_TOKEN 也會帶上（私人 repo 時要）。SHA256 才是保證：對不上就不載入，發文照常、後台看得到錯誤。
REPO = "aaaa47080/CryptoMind-Guard-0.6B"
REVISION = "f19ca5297ebe3129b8bc50138c7e0709b2ea4793"
FILES = {
    "readout.json": "f6278ef6fb95901898aaf6ad15a28e70e3f14cd332a24cd72765cc9f83a9f283",
    "tokenizer.json": "be75606093db2094d7cd20f3c2f385c212750648bd6ea4fb2bf507a6a4c55506",
}
# llama-server 只回前 N 名 token 的機率；29 個風險代碼在第一個 token 幾乎吃滿機率，60 名綽綽有餘
TOP_N = 60


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def ensure_model(model_dir: Path) -> None:
    """缺檔就從 Hugging Face 下載（釘版本），每個檔都驗 SHA256；不對就刪掉並丟例外。"""
    for rel, digest in FILES.items():
        path = model_dir / rel
        if path.exists() and _sha256(path) == digest:
            continue
        if not REPO or not REVISION:
            raise RuntimeError(f"模型檔 {rel} 不在 {model_dir}，也還沒設定下載來源（detector.REPO／REVISION）")
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{rel}"
        token = os.getenv("HF_TOKEN")
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
        tmp = path.with_suffix(path.suffix + ".part")
        logger.info("[moderation] downloading %s", rel)
        with urllib.request.urlopen(request, timeout=60) as resp, open(tmp, "wb") as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
        if _sha256(tmp) != digest:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"SHA256 mismatch for {rel}")
        tmp.replace(path)


class ModerationModel:
    def __init__(self, model_dir: Path, llama_url: str, timeout: float = 10.0):
        from tokenizers import Tokenizer

        readout = json.loads((model_dir / "readout.json").read_text(encoding="utf-8"))
        self._prefix: List[int] = readout["prefix_ids"]
        self._suffix: List[int] = readout["suffix_ids"]
        self._lead: str = readout["lead"]
        self._labels: Dict[int, str] = dict(zip(readout["label_ids"], readout["label_codes"]))
        self._safe: str = readout["safe_code"]
        self._first: int = readout["first_tokens"]
        self._last: int = readout["last_tokens"]
        self._tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self._url = llama_url.rstrip("/")
        self._timeout = timeout

    def chunks(self, text: str) -> List[List[int]]:
        """長文只看開頭＋結尾（聯絡方式、連結常放最後），兩段各打分數取高；長度跟訓練時一樣切。
        template 對內容做 trim、前面接「Input Text: 」的空白——訓練時就是這樣拼"""
        ids = self._tok.encode(self._lead + (text or "").strip(), add_special_tokens=False).ids
        if len(ids) <= self._first + self._last:
            return [ids]
        return [ids[: self._first], ids[-self._last :]]

    def _top_logprobs(self, ids: List[int]) -> List[dict]:
        body = {"prompt": ids, "n_predict": 1, "n_probs": TOP_N, "temperature": 0, "cache_prompt": True}
        request = urllib.request.Request(f"{self._url}/completion", data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self._timeout) as resp:
            # 欄位名照 llama.cpp v0.5.0（compose 釘的版本）；換版前要重跑真模型校正
            return json.loads(resp.read(1 << 20))["completion_probabilities"][0]["top_logprobs"]

    def _run(self, piece: List[int]) -> Tuple[float, str]:
        top = self._top_logprobs(self._prefix + piece + self._suffix)
        probs = {self._labels[t["id"]]: math.exp(t["logprob"]) for t in top if t["id"] in self._labels}
        total = sum(probs.values())
        if total <= 0:
            raise RuntimeError("llama-server 回傳的前幾名沒有任何風險代碼（模型檔不對？）")
        risk = {code: p for code, p in probs.items() if code != self._safe}
        category = max(risk, key=risk.get) if risk else self._safe
        return 1.0 - probs.get(self._safe, 0.0) / total, category

    def classify(self, text: str) -> Dict:
        """整篇「該擋」的機率＝各段最高；category＝那段最可能的風險代碼（分數低時沒意義）。"""
        started = time.perf_counter()
        pieces = self.chunks(text)
        score, category = max((self._run(piece) for piece in pieces), key=lambda r: r[0])
        return {"block": round(score, 4), "category": category, "chunks": len(pieces),
                "ms": int((time.perf_counter() - started) * 1000)}

    def llama_health(self) -> Tuple[bool, str]:
        """llama-server 載好模型了沒（/health 載入中回 503）"""
        try:
            with urllib.request.urlopen(f"{self._url}/health", timeout=3) as resp:
                return resp.status == 200, ""
        except Exception as exc:  # noqa: BLE001 — 只拿來回報狀態
            return False, f"llama-server: {exc}"[:200]


def default_model_dir() -> Path:
    return Path(
        os.getenv("MODERATION_MODEL_DIR")
        or os.path.expanduser(f"~/.cache/cryptomind-models/moderation-{VERSION}")
    )


def default_llama_url() -> str:
    # compose 內網的 moderation-llm；搬到主機跑 iGPU 時改成主機上 llama-server 的位址
    return os.getenv("MODERATION_LLAMA_URL", "http://moderation-llm:8080")
