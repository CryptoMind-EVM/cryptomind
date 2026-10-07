"""論壇發文內容檢查（2026-10-01 試驗，core/moderation）。

DANNY：發文前先檢測，通過亮綠燈才能發。2026-10-02 起只用一顆自己微調的模型（不寫死關鍵字）；
2026-10-03 起 v7 CryptoMind-Guard-0.6B（llama.cpp）：≥0.8 擋、0.5～0.8 照常發文但送管理員看；
貼出自己的助記詞／私鑰一律擋；檢查服務掛了照常發文。
rules.py 的詐騙句型規則現在只給防詐回報用（保護受害者，見 test_scam_tracker_*）。
前端（綠燈才能按發文）在 tests/js/forum_moderation.mjs。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
SAMPLES = json.loads((REPO / "tests" / "fixtures" / "moderation_samples.json").read_text(encoding="utf-8"))


# ── 規則：明確詐騙句型 ────────────────────────────────────────────────────────




@pytest.mark.parametrize(
    "text",
    [
        "防詐提醒：任何人跟你要助記詞都不要給，官方客服絕對不會私訊你",
        "千萬別把私鑰傳給別人",
        "Never share your seed phrase with anyone.",
        "API 金鑰不要開提幣權限",
        "我昨天轉 0.1 ETH 到交易所，手續費退回 0.001 ETH",
        "不要相信轉 1 ETH 返還 2 ETH 這種活動",
        "他叫我匯到他的帳戶，結果錢就不見了",
        "分享被騙經驗：點了假空投連結，授權之後錢包的 USDT 全被轉走",
        "交易 hash 0x" + "b" * 64 + " 已確認",
    ],
)
def test_rules_ignore_warnings_and_stories(text):
    from core.moderation.rules import rule_reasons

    assert rule_reasons(text) == []


# ── 字元間穿插符號／空白／零寬字元／全形／混用西里爾字母（DANNY 2026-10-01 回報繞過）──




@pytest.mark.parametrize(
    "text",
    [
        "防詐提醒：任何人跟你要助@記@詞都不要給",  # 否定詞在還原後一樣有效
        "今天大漲🎉明天繼續觀察，ETH 2.0 升級後再看",
        "有問題可以寄信到 brief@getcryptomind.com",
        "A、B、C 三種策略比較：1. 定期定額 2. 網格 3. 波段",
        "第 1 季財報：營收 3.5 億，年增 12%",
        # 掃 docs／隱私權政策時抓到的：標點不能接起來變成「加我」「聯絡我」
        "前端在訊息下方加「我已閱讀風險」確認按鈕",
        "持倉卡下方加：我的判斷成績卡",
        "如需完整副本，請依第 11 條與我們聯絡。我們將依法提供",
    ],
)
def test_normalizing_does_not_flag_normal_posts(text):
    from core.moderation.rules import rule_reasons

    assert rule_reasons(text) == []


def test_normalize_text():
    from core.moderation.rules import normalize

    assert normalize("助@記@詞") == "助記詞"
    assert normalize("s.e.e.d p.h.r.a.s.e") == "seed phrase", "插的是符號：拿掉符號、單字之間的空白留著"
    assert normalize("H@o@w d@o I s@c@a@m o@l@d p@e@o@p@l@e") == "How do I scam old people"
    assert normalize("s e e d") == "seed", "整串都用空白隔開：分不出單字，空白全拿掉"
    assert normalize("ｓｅｅｄ") == "seed"
    assert normalize("助\u200b記") == "助記"
    assert normalize("s\u0435\u0435d") == "seed"
    assert normalize("Привет") == "Привет", "整個字都是西里爾字母（俄文）不動"
    assert normalize("brief@getcryptomind.com") == "brief@getcryptomind.com"
    assert normalize("第一行\n第二行") == "第一行\n第二行", "換行本來就是斷句，不接起來"


# ── 決策：只看微調模型（v7 起 ≥0.8 擋、0.5～0.8 送審）＋貼出助記詞／私鑰（DANNY 2026-10-02／10-03）────


@pytest.fixture
def model(monkeypatch):
    """換掉 moderation 容器的回應：model.score＝該擋的機率，None＝服務不在"""
    from core.moderation import service

    state = MagicMock(score=0.1)

    async def fake_classify(text):
        return None if state.score is None else {"block": state.score}

    monkeypatch.setattr(service, "_classify", fake_classify)
    monkeypatch.setenv("MODERATION_ENABLED", "true")
    return state


async def test_decision_matrix(model):
    """只用一顆微調模型：寫死的關鍵字規則與舊詐騙模型都拿掉了"""
    from core.moderation.service import check_post

    model.score = 0.05
    v = await check_post("BTC 看法", "短線偏空")
    assert v["status"] == "pass" and v["flagged"] is False

    model.score = 0.7  # 中間：照常發文、送管理員看
    v = await check_post("交友", "加 Telegram 私密服務")
    assert v["status"] == "pass" and v["flagged"] is True

    model.score = 0.85  # v7 門檻 0.8（DANNY 選）：v4 時代的 0.9 會放行這篇
    assert (await check_post("VIP 帶單", "老師帶單"))["status"] == "block"

    model.score = 0.93
    v = await check_post("VIP 帶單", "穩賺不賠，加 LINE 進群")
    assert v["status"] == "block" and v["reasons"] == ["harmful_model"] and v["score"] == 0.93

    model.score = 0.01  # 以前「請提供你的助記詞」是關鍵字規則擋的，現在交給模型判斷
    assert (await check_post("通知", "請提供你的助記詞"))["status"] == "pass"


async def test_leaked_secret_is_still_blocked(model):
    """貼出自己的助記詞：不是判斷內容好壞，是防止貼文的人把資產交出去——模型分數再低也擋"""
    from core.moderation.service import check_post

    model.score = 0.01
    words = "abandon ability able about above absent absorb abstract absurd abuse access accident"
    v = await check_post("救命", f"我的助記詞是 {words} 錢不見了")
    assert v["status"] == "block" and v["reasons"] == ["leaked_secret"]


async def test_thresholds_come_from_env(model, monkeypatch):
    from core.moderation.service import check_post

    monkeypatch.setenv("MODERATION_BLOCK_SCORE", "0.95")
    monkeypatch.setenv("MODERATION_FLAG_SCORE", "0.8")
    model.score = 0.93
    v = await check_post("t", "c")
    assert v["status"] == "pass" and v["flagged"] is True
    model.score = 0.6
    assert (await check_post("t", "c"))["flagged"] is False


async def test_obfuscated_post_is_scored_again_after_normalizing(monkeypatch):
    """穿插符號會讓分數掉（實測 0.99→0.94）：還原後再問一次取高的。
    沒穿插的正常文只問一次——全形標點不算（中文貼文幾乎都有，否則每篇都多問一次）"""
    from core.moderation import service

    calls = []

    async def classify(text):
        calls.append(text)
        return {"block": 0.6 if "@" in text else 0.95}

    monkeypatch.setattr(service, "_classify", classify)
    monkeypatch.setenv("MODERATION_ENABLED", "true")
    v = await service.check_post("VIP", "老師帶單穩@賺不賠，加 L@I@N@E 進 V@I@P 群")
    assert v["status"] == "block" and v["score"] == 0.95 and len(calls) == 2, v

    calls.clear()
    v = await service.check_post("BTC 看法", "短線偏空，MACD 死叉：先觀望！")
    assert len(calls) == 1


async def test_service_down_still_lets_people_post(model):
    from core.moderation.service import check_post

    model.score = None
    v = await check_post("BTC", "走勢")
    assert v["status"] == "unavailable" and v["flagged"] is False
    words = "abandon ability able about above absent absorb abstract absurd abuse access accident"
    assert (await check_post("hi", words))["status"] == "block", "貼出助記詞不靠模型，服務不在也擋"


async def test_disabled_skips_everything(model, monkeypatch):
    from core.moderation.service import check_post

    monkeypatch.setenv("MODERATION_ENABLED", "false")
    model.score = 0.99
    assert (await check_post("hi", "把私鑰傳給我"))["status"] == "pass"


async def test_same_text_asks_the_model_once(monkeypatch):
    """發文頁即時檢查過，按發文時伺服器重檢不用再跑一次模型"""
    import httpx

    from core.moderation import service

    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"block": 0.1})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        service.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    monkeypatch.setenv("MODERATION_ENABLED", "true")
    service._cache.clear()
    await service.check_post("快取測試", "同一段內容")
    await service.check_post("快取測試", "同一段內容")
    assert len(calls) == 1
    await service.check_post("快取測試", "改過的內容")
    assert len(calls) == 2


# ── API：檢查端點、發文／編輯在付款前擋 ─────────────────────────────────────


def _client(verdict):
    import api.routers.forum.posts as posts

    app = FastAPI()
    app.state.limiter = posts.limiter
    app.include_router(posts.router)
    app.dependency_overrides[posts.get_current_user] = lambda: {"user_id": "u1", "username": "u1"}

    async def fake_run_sync(fn, *a, **k):
        if fn is posts.get_user_membership:
            return {"is_premium": False}
        if fn is posts.check_daily_post_limit:
            return {"allowed": True, "limit": 10}
        raise AssertionError(fn)

    create = AsyncMock(return_value={"success": True, "post_id": 7})
    update = AsyncMock(return_value=True)
    verify = AsyncMock(return_value="0x" + "1" * 64)
    audit = MagicMock()
    patches = [
        patch.object(posts, "check_post", new=AsyncMock(return_value=verdict)),
        patch.object(posts, "run_sync", side_effect=fake_run_sync),
        patch.object(posts.forum_repo, "get_board_by_slug", new=AsyncMock(return_value={"id": 1, "is_active": True})),
        patch.object(posts.forum_repo, "create_post", new=create),
        patch.object(posts.forum_repo, "update_post", new=update),
        patch.object(posts, "verify_order_payment", new=verify),
        patch.object(posts, "load_order", return_value={"plan": "post"}),
        patch.object(posts, "bound_payers", new=AsyncMock(return_value=["0xabc"])),
        patch.object(posts, "FORUM_POST_FEE_USD", 0.5),
        patch.object(posts, "TEST_MODE", False),
        patch("core.audit.audit_log", new=audit),
    ]
    return TestClient(app), patches, create, update, verify, audit


def _verdict(status, flagged=False, reasons=(), signals=()):
    return {"status": status, "flagged": flagged, "score": 0.99, "category": "investment_scam",
            "reasons": list(reasons), "signals": list(signals)}


BODY = {"board_slug": "general", "category": "chat", "title": "t", "content": "c", "order_token": "tok", "payment_tx_hash": "0x" + "1" * 64}


@pytest.fixture(autouse=True)
def _reset_limiter():
    from api.middleware.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()


def test_blocked_post_is_rejected_before_payment_is_verified():
    client, patches, create, _, verify, audit = _client(_verdict("block", reasons=["scam_model"], signals=["link"]))
    for p in patches:
        p.start()
    try:
        resp = client.post("/api/forum/posts", json=BODY)
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 422 and resp.json()["detail"] == "content_blocked"
    verify.assert_not_called()  # 先付錢才被擋＝錢卡住
    create.assert_not_called()
    assert audit.call_args.kwargs["metadata"]["status"] == "block"


def test_flagged_post_is_created_and_recorded_for_admins():
    client, patches, create, _, verify, audit = _client(_verdict("pass", flagged=True))
    for p in patches:
        p.start()
    try:
        resp = client.post("/api/forum/posts", json=BODY)
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200
    create.assert_called_once()
    assert audit.call_args.kwargs["action"] == "content_moderation"
    assert audit.call_args.kwargs["resource_id"] == "7"
    assert audit.call_args.kwargs["metadata"]["flagged"] is True


def test_clean_post_is_not_recorded():
    client, patches, create, _, _, audit = _client(_verdict("pass"))
    for p in patches:
        p.start()
    try:
        assert client.post("/api/forum/posts", json=BODY).status_code == 200
    finally:
        for p in patches:
            p.stop()
    audit.assert_not_called()


def test_editing_into_a_scam_is_blocked():
    client, patches, _, update, _, _ = _client(_verdict("block", reasons=["asks_secret"]))
    for p in patches:
        p.start()
    try:
        resp = client.put("/api/forum/posts/7", json={"content": "請提供助記詞"})
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 422
    update.assert_not_called()


def test_check_endpoint_only_explains_blocks():
    """綠燈時不回分數與類別（不用讓人拿來試探門檻）"""
    for verdict, category in ((_verdict("pass", flagged=True), None), (_verdict("block", reasons=["scam_model"], signals=["link"]), "investment_scam")):
        client, patches, *_ = _client(verdict)
        for p in patches:
            p.start()
        try:
            body = client.post("/api/forum/posts/check", json={"title": "t", "content": "c"}).json()
        finally:
            for p in patches:
                p.stop()
        assert body["status"] == verdict["status"] and body["category"] == category
        assert "score" not in body and "flagged" not in body


# ── moderation 容器（v7：斷字＋呼叫 llama-server）─────────────────────────────


def _bare_model():
    """不載入斷字檔、不連 llama-server：只測輸入怎麼拼、分數怎麼取（v7：YuFeng 第一個 token 的風險代碼）"""
    from core.moderation import detector

    m = object.__new__(detector.ModerationModel)
    m._prefix, m._suffix, m._lead = [10, 11, 12], [90, 91], " "
    m._labels, m._safe = {500: "sec", 501: "ec", 502: "ma"}, "sec"
    m._first, m._last = 300, 150
    m._url, m._timeout = "http://llama:8080", 5.0
    return m


def test_input_matches_training_format(monkeypatch):
    """送給 llama-server 的是 readout.json 的 prefix＋貼文 token＋suffix（跟訓練、官方 template 逐 token 一樣），
    只要下一個 token 的機率、開題目快取；分數＝1 − P(sec)，只在風險代碼之間正規化（其他 token 不算）"""
    import io
    import json as _json
    import math

    from core.moderation import detector

    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"], seen["body"] = request.full_url, _json.loads(request.data)
        top = [{"id": 502, "logprob": math.log(0.6)}, {"id": 500, "logprob": math.log(0.2)},
               {"id": 777, "logprob": math.log(0.15)}, {"id": 501, "logprob": math.log(0.05)}]
        return io.BytesIO(_json.dumps({"completion_probabilities": [{"top_logprobs": top}]}).encode())

    monkeypatch.setattr(detector.urllib.request, "urlopen", fake_urlopen)
    score, code = _bare_model()._run([5, 6])
    assert score == pytest.approx(1 - 0.2 / 0.85) and code == "ma"
    assert seen["url"] == "http://llama:8080/completion"
    assert seen["body"]["prompt"] == [10, 11, 12, 5, 6, 90, 91]
    assert seen["body"]["n_predict"] == 1 and seen["body"]["cache_prompt"] is True and seen["body"]["temperature"] == 0


def test_score_is_the_highest_chunk():
    m = _bare_model()
    m.chunks = lambda text: [[1], [2]]
    m._run = MagicMock(side_effect=[(0.2, "ec"), (0.97, "ma")])
    r = m.classify("很長的貼文")
    assert r["block"] == 0.97 and r["category"] == "ma" and r["chunks"] == 2


def test_long_posts_check_head_and_tail():
    m = _bare_model()
    m._tok = MagicMock()
    m._tok.encode.return_value = MagicMock(ids=list(range(1000)))
    pieces = m.chunks("long")
    assert [len(p) for p in pieces] == [300, 150]
    assert pieces[1][-1] == 999, "結尾那段要包含最後一個 token（聯絡方式、連結常放最後）"
    m._tok.encode.return_value = MagicMock(ids=list(range(100)))
    assert m.chunks("short") == [list(range(100))]
    m.chunks("  前後有空白 \n")
    assert m._tok.encode.call_args.args[0] == " 前後有空白", "跟 template 一樣先 trim，前面接 Input Text: 的空白"


def test_llama_server_failure_is_an_error_not_a_pass(monkeypatch):
    """llama-server 連不到要丟例外（classify 回 500 → app 當作「暫時無法使用」），不能當成 0 分放行；
    回傳裡完全沒有風險代碼（模型檔不對）也一樣"""
    import io
    import json as _json

    from core.moderation import detector

    def down(request, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(detector.urllib.request, "urlopen", down)
    with pytest.raises(OSError):
        _bare_model()._run([1])
    monkeypatch.setattr(detector.urllib.request, "urlopen", lambda request, timeout: io.BytesIO(
        _json.dumps({"completion_probabilities": [{"top_logprobs": [{"id": 777, "logprob": -0.1}]}]}).encode()))
    with pytest.raises(RuntimeError):
        _bare_model()._run([1])


def test_model_files_are_pinned():
    from core.moderation import detector

    assert detector.REPO == "aaaa47080/CryptoMind-Guard-0.6B" and len(detector.REVISION) == 40
    assert set(detector.FILES) == {"readout.json", "tokenizer.json"}
    assert all(len(v) == 64 for v in detector.FILES.values())


def test_download_sends_hf_token_and_rejects_wrong_files(tmp_path, monkeypatch):
    """私人 repo：有 HF_TOKEN 就帶上；下載下來 SHA256 不對就丟掉、不留半個檔"""
    import io

    from core.moderation import detector

    seen = []

    def fake_urlopen(request, timeout):
        seen.append(request.get_header("Authorization"))
        return io.BytesIO(b"not the model")

    monkeypatch.setattr(detector, "FILES", {"readout.json": "0" * 64})
    monkeypatch.setattr(detector.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    with pytest.raises(RuntimeError, match="SHA256"):
        detector.ensure_model(tmp_path)
    assert seen == ["Bearer hf_test"]
    assert list(tmp_path.iterdir()) == []




# ── 真模型（本機有下載才跑；CI 沒有模型會 skip）──────────────────────────────


async def test_calibration_samples_with_the_real_model(monkeypatch):
    from core.moderation import detector, service

    model_dir = detector.default_model_dir()
    if not all((model_dir / f).exists() for f in detector.FILES):
        pytest.skip(f"本機沒有斷字檔（{model_dir}）")
    real = detector.ModerationModel(model_dir, detector.default_llama_url())
    if not real.llama_health()[0]:
        pytest.skip(f"llama-server 沒開（{detector.default_llama_url()}）")

    async def classify(text):
        return real.classify(text)

    monkeypatch.setattr(service, "_classify", classify)
    monkeypatch.setenv("MODERATION_ENABLED", "true")
    wrong, fixed = [], []
    for s in SAMPLES:
        v = await service.check_post(s["title"], s["content"])
        ok = {
            "pass": v["status"] != "block",
            "block": v["status"] == "block",
            "flag": v["status"] == "block" or v["flagged"],
        }[s["expect"]]
        if s.get("known_miss"):  # 已知漏網：記著，換新版模型擋下來了就提醒把標記拿掉
            if ok:
                fixed.append(s["title"])
        elif not ok:
            wrong.append((s["title"], s["expect"], v["status"], v["score"]))
    assert not wrong, wrong
    assert not fixed, f"這些已經判對了，把 fixture 的 known_miss 拿掉：{fixed}"


def test_frontend_gate_node():
    import subprocess

    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "forum_moderation.mjs")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "forum_moderation: ok" in proc.stderr


def test_create_page_wires_the_gate_before_payment():
    js = (REPO / "web" / "js" / "forum-app.js").read_text(encoding="utf-8")
    handler = js[js.index("const moderationGate = createModerationGate(") :]
    assert handler.index("moderationGate.ensureChecked()") < handler.index("/api/forum/posts/payment-order"), (
        "要先確定檢查通過才進付款"
    )
    html = (REPO / "web" / "forum" / "create.html").read_text(encoding="utf-8")
    assert 'id="moderation-status"' in html


async def test_any_service_error_means_unavailable_not_500(monkeypatch):
    """檢查服務出任何事（不只 httpx 錯誤）都當作不在：發文不能因為檢查壞掉而 500"""
    from core.moderation import service

    def boom(**kw):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(service.httpx, "AsyncClient", boom)
    monkeypatch.setenv("MODERATION_ENABLED", "true")
    service._cache.clear()
    assert (await service.check_post("t", "其他錯誤也要放行"))["status"] == "unavailable"


def test_title_only_edit_is_checked_together_with_existing_content():
    """review 2026-10-01：詐騙拆在標題、內文兩次編輯——只改一半時要跟原本的另一半合起來檢查"""
    import api.routers.forum.posts as posts

    client, patches, _, update, _, _ = _client(_verdict("pass"))
    seen = {}

    async def fake_check(title, content):
        seen["args"] = (title, content)
        return _verdict("pass")

    existing = AsyncMock(return_value={"title": "舊標題", "content": "原本的內文：加 LINE 私訊我"})
    for p in patches:
        p.start()
    try:
        with patch.object(posts, "check_post", new=fake_check), patch.object(posts.forum_repo, "get_post_by_id", new=existing):
            assert client.put("/api/forum/posts/7", json={"title": "穩賺不賠"}).status_code == 200
            assert seen["args"] == ("穩賺不賠", "原本的內文：加 LINE 私訊我")
            assert existing.await_args.kwargs.get("increment_view") is False, "檢查不能灌瀏覽數"
            client.put("/api/forum/posts/7", json={"title": "新標題", "content": "新內文"})
            assert seen["args"] == ("新標題", "新內文")
            assert existing.await_count == 1, "兩個都改就不用再讀舊的"
    finally:
        for p in patches:
            p.stop()


async def test_service_health_for_admins(monkeypatch):
    """2026-10-01 線上一直「暫時無法使用」、要進 VM 才看得到原因（volume 權限）——後台直接看"""
    import httpx

    from core.moderation import service

    real = httpx.AsyncClient

    def client_with(handler):
        return lambda **kw: real(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setenv("MODERATION_ENABLED", "true")
    monkeypatch.setattr(service.httpx, "AsyncClient", client_with(lambda r: httpx.Response(200, json={"ok": True, "revision": "41cd8842abc"})))
    assert await service.service_health() == {"enabled": True, "reachable": True, "ok": True, "error": None, "revision": "41cd8842abc"}

    err = "[Errno 13] Permission denied: '/models/moderation-v7'"
    monkeypatch.setattr(service.httpx, "AsyncClient", client_with(lambda r: httpx.Response(503, json={"ok": False, "error": err})))
    h = await service.service_health()
    assert h["reachable"] is True and h["ok"] is False and h["error"] == err

    def down(r):
        raise httpx.ConnectError("Name or service not known")

    monkeypatch.setattr(service.httpx, "AsyncClient", client_with(down))
    h = await service.service_health()
    assert h["reachable"] is False and "not known" in h["error"]


def test_moderation_status_endpoint_requires_admin():
    from api.deps import require_admin
    from api.routers.admin.stats import router

    route = next(r for r in router.routes if r.path.endswith("/stats/moderation"))
    assert require_admin in {d.call for d in route.dependant.dependencies}
