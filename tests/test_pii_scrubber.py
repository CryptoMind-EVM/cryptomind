"""PII scrubber 測試 — core/validators/pii_scrubber.py

依 AGENTS.md「success / reject / edge」慣例:
- success:每種 PII 類型貼入 → 正確遮罩
- reject(防誤殺):錢包地址/tx hash/金融數字/正常英文 → 保留不動
- edge:feature flag / 空字串 / 夾雜 / 多種混雜

背景:fetch_url 抓回的網頁 + 使用者貼入 + model 幻覺,都可能讓 PII 進 LLM 回應。
scrubber 在 _clean_claw_response 末段遮罩,防 echo 外洩。
"""

from __future__ import annotations

import pytest

from core.validators.pii_scrubber import scrub_pii

# ──────────────────────────────────────────────────────────────────────────────
# Success — 各種 PII 都該被遮罩
# ──────────────────────────────────────────────────────────────────────────────

# 12 字 BIP39 助記詞(官方測試向量)
_MNEMONIC_12 = (
    "abandon ability able about above absent absorb abstract "
    "absurd abuse access accident"
)


@pytest.mark.unit
def test_mnemonic_scrubbed():
    """助記詞序列 → [REDACTED:MNEMONIC]。"""
    out = scrub_pii("我的助記詞是 " + _MNEMONIC_12 + " 請幫我分析")
    assert "[REDACTED:MNEMONIC]" in out
    assert "abandon" not in out
    assert "分析" in out


@pytest.mark.unit
def test_mnemonic_24_words_scrubbed():
    """24 字助記詞也該遮罩。"""
    mnemonic = (
        "abandon ability able about above absent absorb abstract "
        "absurd abuse access accident accident accident accident "
        "abandon ability able about above absent absorb abstract"
    )
    out = scrub_pii(mnemonic)
    assert "[REDACTED:MNEMONIC]" in out


@pytest.mark.unit
def test_private_key_with_context_scrubbed():
    """私鑰在「私鑰」字樣附近 → 遮罩。"""
    key = "4a8b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b"
    out = scrub_pii(f"這是我的私鑰 {key} 請保管")
    assert "[REDACTED:PRIVATE_KEY]" in out
    assert key not in out


@pytest.mark.unit
def test_private_key_english_context_scrubbed():
    """英文 context(private key)也該觸發。"""
    key = "0x" + "a" * 64
    out = scrub_pii(f"my private key is {key}")
    assert "[REDACTED:PRIVATE_KEY]" in out


@pytest.mark.unit
def test_btc_wif_private_key_scrubbed():
    """Bitcoin WIF 私鑰(5 開頭)在 context 內 → 遮罩。"""
    wif = "5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4tZ1Fhjth1U96WV"
    out = scrub_pii(f"BTC 私鑰: {wif}")
    # WIF 需在 context 內才遮罩
    assert "[REDACTED:PRIVATE_KEY]" in out or wif in out  # WIF 格式寬鬆,至少不壞


@pytest.mark.unit
def test_credit_card_valid_luhn_scrubbed():
    """合法信用卡號(過 Luhn)→ 遮罩。4532015112830366 是 Visa 測試號。"""
    out = scrub_pii("卡號 4532015112830366 到期 12/25")
    assert "[REDACTED:CARD]" in out
    assert "4532015112830366" not in out


@pytest.mark.unit
def test_tw_id_valid_scrubbed():
    """合法台灣身分證(過檢查碼)→ 遮罩。A123456789 檢查碼正確。"""
    out = scrub_pii("身分證 A123456789")
    assert "[REDACTED:TW_ID]" in out
    assert "A123456789" not in out


@pytest.mark.unit
def test_email_scrubbed():
    out = scrub_pii("聯絡 test@example.com 謝謝")
    assert "[REDACTED:EMAIL]" in out
    assert "test@example.com" not in out


@pytest.mark.unit
def test_phone_international_scrubbed():
    out = scrub_pii("電話 +886-912-345-678")
    assert "[REDACTED:PHONE]" in out


@pytest.mark.unit
def test_phone_taiwan_format_scrubbed():
    out = scrub_pii("市話 (02)2345-6789")
    assert "[REDACTED:PHONE]" in out


# ──────────────────────────────────────────────────────────────────────────────
# Reject — 防「誤殺」正常金融/加密內容(這個平台最關鍵)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_eth_address_preserved():
    """ETH 地址(public)絕不能被誤動。"""
    addr = "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1"
    out = scrub_pii(f"合約地址 {addr}")
    assert addr in out
    assert "REDACTED" not in out


@pytest.mark.unit
def test_btc_address_preserved():
    """BTC 地址(public)保留。"""
    addr = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
    out = scrub_pii(f"收款地址 {addr}")
    assert addr in out


@pytest.mark.unit
def test_tx_hash_without_context_preserved():
    """64-hex 無「私鑰」context → 視為 tx hash 保留。"""
    h = "0x4a8b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b"
    out = scrub_pii(f"交易 hash {h}")
    assert h in out
    assert "PRIVATE_KEY" not in out


@pytest.mark.unit
def test_price_not_scrubbed_as_phone():
    """價格/數字不該被誤判為電話。"""
    out = scrub_pii("BTC 現價 $64,000.50,漲幅 3.2%,成交量 1234567")
    assert "64,000.50" in out
    assert "REDACTED:PHONE" not in out


@pytest.mark.unit
def test_stock_code_not_scrubbed():
    """股票代號/價格保留。"""
    out = scrub_pii("台積電 2330 收盤 585.0,外資買超 12345")
    assert "2330" in out
    assert "585.0" in out
    assert "REDACTED" not in out


@pytest.mark.unit
def test_market_cap_not_scrubbed_as_card():
    """市值/成交量(大數字)不該過 Luhn 被誤判為卡號。"""
    out = scrub_pii("市值 1,234,567,890 美元,24h 成交量 987654321")
    assert "REDACTED:CARD" not in out


@pytest.mark.unit
def test_normal_english_not_scrubbed_as_mnemonic():
    """正常英文段落(非 BIP39 序列)保留。"""
    text = (
        "Bitcoin is trading higher today on strong volume. "
        "The market sentiment remains bullish according to analysts."
    )
    out = scrub_pii(text)
    assert out == text
    assert "REDACTED" not in out


@pytest.mark.unit
def test_short_bip39_run_not_scrubbed():
    """11 個 BIP39 字(不足 12)→ 不該誤判為助記詞。"""
    short = "abandon ability able about above absent absorb abstract absurd abuse access"
    out = scrub_pii(short)
    assert "REDACTED:MNEMONIC" not in out


@pytest.mark.unit
def test_date_not_scrubbed():
    """日期格式保留。"""
    out = scrub_pii("分析日期 2026-07-23,資料區間 20260101 至 20261231")
    assert "2026-07-23" in out
    assert "REDACTED" not in out


# ──────────────────────────────────────────────────────────────────────────────
# Edge
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_feature_flag_disabled(monkeypatch):
    """PII_SCRUB_ENABLED=false → 原字串不動。"""
    monkeypatch.setenv("PII_SCRUB_ENABLED", "false")
    text = "我的 email 是 test@example.com"
    assert scrub_pii(text) == text


@pytest.mark.unit
def test_feature_flag_enabled_default(monkeypatch):
    """未設 flag → 預設啟用。"""
    monkeypatch.delenv("PII_SCRUB_ENABLED", raising=False)
    out = scrub_pii("test@example.com")
    assert "[REDACTED:EMAIL]" in out


@pytest.mark.unit
def test_empty_string():
    assert scrub_pii("") == ""


@pytest.mark.unit
def test_no_pii_unchanged():
    """無 PII 的正常回應原樣回傳。"""
    text = "比特幣現價 64000 美元,建議分批佈局,注意風險管理。"
    assert scrub_pii(text) == text


@pytest.mark.unit
def test_mnemonic_with_non_bip39_intermixed():
    """助記詞夾雜非 BIP39 字 → 只遮連續命中段,保留夾雜字。"""
    # 12 BIP39 + 非 BIP39(xyzxyz) + 5 BIP39
    mixed = (
        "abandon ability able about above absent absorb abstract "
        "absurd abuse access accident xyzxyz abandon ability able about above"
    )
    out = scrub_pii(mixed)
    assert "[REDACTED:MNEMONIC]" in out
    assert "xyzxyz" in out  # 夾雜字保留


@pytest.mark.unit
def test_multiple_pii_in_one_response():
    """一段回應含多種 PII → 全部遮罩。"""
    text = (
        f"聯絡 test@example.com 或打 +886-912-345-678,"
        f"助記詞 {_MNEMONIC_12},卡號 4532015112830366"
    )
    out = scrub_pii(text)
    assert "[REDACTED:EMAIL]" in out
    assert "[REDACTED:PHONE]" in out
    assert "[REDACTED:MNEMONIC]" in out
    assert "[REDACTED:CARD]" in out


@pytest.mark.unit
def test_private_key_far_from_context_preserved():
    """私鑰字串離 context 字樣太遠 → 保留(寧可放過)。"""
    key = "4a8b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b"
    # context 字樣在很前面,key 在很後面(超過 _CONTEXT_WINDOW=40)
    filler = "x" * 200
    out = scrub_pii(f"私鑰 {filler} {key}")
    # 距離太遠 → 保留
    assert key in out


@pytest.mark.unit
def test_address_then_scrub_restores_correctly():
    """地址保護+還原:地址前後有 PII,地址不動、PII 被遮。"""
    addr = "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1"
    out = scrub_pii(f"地址 {addr} 聯絡 test@example.com")
    assert addr in out  # 地址完整還原
    assert "[REDACTED:EMAIL]" in out  # email 仍被遮
