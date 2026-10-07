"""test_vision.py — 圖片辨識 Tier 1（docs/plans/2026-08-27-vision-image-analysis-design.md）

涵蓋：
- validate_image_data_url：mime 白名單 / 大小上限 / 非 data URL / flag off
- vision_supported：provider/model 啟發式
- describe_image：多模態 content blocks 結構（fake LLM 捕捉）、
  不支援 provider 先擋（不建 client）、LLM 報錯映射 VISION_UNSUPPORTED_MODEL
- augment_query_with_image：query 增強格式
"""

from __future__ import annotations

import base64

import pytest

from api import vision
from api.vision import (
    VisionError,
    augment_query_with_image,
    describe_image,
    validate_image_data_url,
    vision_supported,
)

pytestmark = pytest.mark.unit


def _png_data_url(size_hint: int = 100) -> str:
    raw = b"\x89PNG\r\n" + b"x" * size_hint
    b64 = base64.b64encode(raw).decode()
    return f"data:image/png;base64,{b64}"


@pytest.fixture(autouse=True)
def _vision_flag_on(monkeypatch):
    monkeypatch.setenv("VISION_ENABLED", "true")
    monkeypatch.delenv("VISION_MAX_IMAGE_BYTES", raising=False)


class TestValidateImageDataUrl:
    def test_accepts_png(self):
        url = _png_data_url()
        assert validate_image_data_url(url) == url

    def test_accepts_jpeg_and_webp(self):
        for mime in ("jpeg", "webp"):
            url = f"data:image/{mime};base64,{base64.b64encode(b'x' * 64).decode()}"
            assert validate_image_data_url(url) == url

    def test_rejects_gif_mime(self):
        url = f"data:image/gif;base64,{base64.b64encode(b'x' * 64).decode()}"
        with pytest.raises(VisionError) as ei:
            validate_image_data_url(url)
        assert ei.value.code == "VISION_INVALID_FORMAT"

    def test_rejects_non_data_url(self):
        with pytest.raises(VisionError) as ei:
            validate_image_data_url("https://example.com/a.png")
        assert ei.value.code == "VISION_INVALID_FORMAT"

    def test_rejects_empty(self):
        with pytest.raises(VisionError):
            validate_image_data_url("")

    def test_rejects_oversize(self, monkeypatch):
        monkeypatch.setenv("VISION_MAX_IMAGE_BYTES", str(64 * 1024))
        url = _png_data_url(200_000)  # 解碼後 200KB > 64KB
        with pytest.raises(VisionError) as ei:
            validate_image_data_url(url)
        assert ei.value.code == "VISION_IMAGE_TOO_LARGE"

    def test_flag_off_raises_disabled(self, monkeypatch):
        monkeypatch.setenv("VISION_ENABLED", "false")
        with pytest.raises(VisionError) as ei:
            validate_image_data_url(_png_data_url())
        assert ei.value.code == "VISION_DISABLED"
        assert ei.value.status_code == 403


class TestVisionSupported:
    def test_openai_gpt4o_yes(self):
        assert vision_supported("openai", "gpt-4o")

    def test_openai_gpt56_luna_yes(self):
        assert vision_supported("openai", "gpt-5.6-luna-pro")

    def test_openai_gpt35_no(self):
        assert not vision_supported("openai", "gpt-3.5-turbo")

    def test_gemini_37_yes(self):
        assert vision_supported("google_gemini", "gemini-3.7-flash")

    def test_anthropic_claude3_yes(self):
        assert vision_supported("anthropic", "claude-3-5-sonnet")

    def test_anthropic_opus5_yes(self):
        assert vision_supported("anthropic", "claude-opus-5")

    def test_zhipu_glm53_flash_yes(self):
        assert vision_supported("zhipu", "glm-5.3-flash")

    def test_zhipu_glm53_text_no(self):
        assert not vision_supported("zhipu", "glm-5.3")

    def test_moonshot_kimi_k3_yes(self):
        assert vision_supported("moonshot", "kimi-k3")

    def test_deepseek_vision_exp_yes(self):
        assert vision_supported("deepseek", "deepseek-v4-flash-vision-exp")

    def test_deepseek_plain_flash_no(self):
        assert not vision_supported("deepseek", "deepseek-v4-flash")

    def test_deepseek_flash_v41_yes(self):
        # 2026-09-10：官方 deepseek-flash（V4.1）原生支援圖片
        assert vision_supported("deepseek", "deepseek-flash")
        assert not vision_supported("deepseek", "deepseek-v4-pro")

    def test_openrouter_deepseek_no(self):
        assert not vision_supported("openrouter", "deepseek/deepseek-chat")

    def test_deepseek_provider_no(self):
        assert not vision_supported("deepseek", "deepseek-chat")

    def test_openrouter_nemotron_ultra_no(self):
        """2026-08-28 E2E 實測：nemotron-3-ultra 上游拒收圖片（純文字模型）。"""
        assert not vision_supported(
            "openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"
        )

    def test_openrouter_nano_omni_yes(self):
        assert vision_supported(
            "openrouter", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
        )


class TestDescribeImage:
    @pytest.fixture
    def fake_llm_capture(self, monkeypatch):
        captured = {}

        class _FakeLLM:
            async def ainvoke(self, messages):
                captured["messages"] = messages

                class _R:
                    content = "The chart shows BTC uptrend."

                return _R()

        monkeypatch.setattr(
            vision, "create_user_llm_client", lambda *a, **kw: _FakeLLM()
        )
        captured["factory_called"] = True
        return captured

    async def test_builds_multimodal_blocks(self, fake_llm_capture, monkeypatch):
        monkeypatch.setattr(vision, "vision_supported", lambda p, m: True)
        url = _png_data_url()
        desc = await describe_image(url, {"provider": "openai", "api_key": "sk-x"})
        assert desc == "The chart shows BTC uptrend."
        msg = fake_llm_capture["messages"][0]
        blocks = msg.content
        assert isinstance(blocks, list) and len(blocks) == 2
        assert (
            blocks[0]["type"] == "text" and "Describe this image" in blocks[0]["text"]
        )
        assert blocks[1]["type"] == "image_url"
        assert blocks[1]["image_url"]["url"] == url

    async def test_unsupported_provider_short_circuits(self, monkeypatch):
        monkeypatch.setattr(vision, "vision_supported", lambda p, m: False)
        called = {"n": 0}

        def _no_factory(*a, **kw):
            called["n"] += 1
            raise AssertionError("factory must not be called")

        monkeypatch.setattr(vision, "create_user_llm_client", _no_factory)
        with pytest.raises(VisionError) as ei:
            await describe_image(
                _png_data_url(), {"provider": "deepseek", "api_key": "k"}
            )
        assert ei.value.code == "VISION_UNSUPPORTED_MODEL"
        assert called["n"] == 0

    async def test_provider_image_rejection_maps_to_unsupported(self, monkeypatch):
        monkeypatch.setattr(vision, "vision_supported", lambda p, m: True)

        class _Reject:
            async def ainvoke(self, messages):
                raise RuntimeError("This model does not support image input")

        monkeypatch.setattr(
            vision, "create_user_llm_client", lambda *a, **kw: _Reject()
        )
        with pytest.raises(VisionError) as ei:
            await describe_image(
                _png_data_url(), {"provider": "openai", "api_key": "k"}
            )
        assert ei.value.code == "VISION_UNSUPPORTED_MODEL"

    async def test_rate_limited_maps_to_rate_limited(self, monkeypatch):
        monkeypatch.setattr(vision, "vision_supported", lambda p, m: True)

        class _Limited:
            async def ainvoke(self, messages):
                raise RuntimeError(
                    "Error code: 429 - gemma is temporarily rate-limited upstream"
                )

        monkeypatch.setattr(
            vision, "create_user_llm_client", lambda *a, **kw: _Limited()
        )
        with pytest.raises(VisionError) as ei:
            await describe_image(
                _png_data_url(), {"provider": "openai", "api_key": "k"}
            )
        assert ei.value.code == "VISION_RATE_LIMITED"
        assert ei.value.status_code == 429

    async def test_other_llm_errors_map_to_description_failed(self, monkeypatch):
        monkeypatch.setattr(vision, "vision_supported", lambda p, m: True)

        class _NetErr:
            async def ainvoke(self, messages):
                raise RuntimeError("Connection error")

        monkeypatch.setattr(
            vision, "create_user_llm_client", lambda *a, **kw: _NetErr()
        )
        # 任何 LLM 失敗都映射為 VISION_DESCRIPTION_FAILED（502），
        # 避免落入泛用 500 無法引導使用者；原始例外保留在 log
        with pytest.raises(VisionError) as ei:
            await describe_image(
                _png_data_url(), {"provider": "openai", "api_key": "k"}
            )
        assert ei.value.code == "VISION_DESCRIPTION_FAILED"
        assert ei.value.status_code == 502


class TestAugmentQuery:
    def test_format(self):
        out = augment_query_with_image("幫我看這張圖", "It shows BTC 4h chart.")
        assert out.startswith(
            "幫我看這張圖\n\n[Attached image — content description]\n"
        )
        assert "BTC 4h chart." in out
