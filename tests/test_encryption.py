import pytest


@pytest.fixture(autouse=True)
def _use_temp_key_dir(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.encryption.KEYS_DIR", tmp_path)
    monkeypatch.setattr(
        "utils.encryption.KEYS_FILE", tmp_path / "api_key_encryption.json"
    )
    monkeypatch.setattr("utils.encryption._encryption_key_cache", None)


@pytest.mark.unit
class TestEncryptDecryptRoundtrip:
    def test_encrypt_decrypt_roundtrip(self):
        from utils.encryption import decrypt_api_key, encrypt_api_key

        plaintext = "sensitive-api-key-12345"
        encrypted = encrypt_api_key(plaintext)
        assert encrypted != plaintext
        assert decrypt_api_key(encrypted) == plaintext

    def test_encrypt_empty_string_returns_empty(self):
        from utils.encryption import encrypt_api_key

        assert encrypt_api_key("") == ""

    def test_production_never_creates_a_file_backed_key(self, monkeypatch):
        """A missing production secret must fail closed, not create a new key."""
        from utils.encryption import KEYS_FILE, encrypt_api_key

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("API_KEY_ENCRYPTION_SECRET", raising=False)

        with pytest.raises(RuntimeError, match="API_KEY_ENCRYPTION_SECRET"):
            encrypt_api_key("sensitive-api-key")

        assert not KEYS_FILE.exists()

    def test_production_rejects_short_external_encryption_secret(self, monkeypatch):
        from utils.encryption import encrypt_api_key

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("API_KEY_ENCRYPTION_SECRET", "too-short")

        with pytest.raises(RuntimeError, match="at least 32 characters"):
            encrypt_api_key("sensitive-api-key")

    def test_decrypt_empty_string_returns_empty(self):
        from utils.encryption import decrypt_api_key

        assert decrypt_api_key("") == ""


@pytest.mark.unit
class TestEncryptDifferentEachTime:
    def test_encrypt_produces_different_ciphertext(self):
        from utils.encryption import encrypt_api_key

        plaintext = "same-input-every-time"
        encrypted_a = encrypt_api_key(plaintext)
        encrypted_b = encrypt_api_key(plaintext)
        assert encrypted_a != encrypted_b


@pytest.mark.unit
class TestDecryptInvalidInput:
    def test_decrypt_without_configured_key_does_not_create_one(self):
        from utils.encryption import KEYS_FILE, decrypt_api_key

        result = decrypt_api_key("not-valid-base64!!!")
        assert result == ""
        assert not KEYS_FILE.exists()

    def test_decrypt_invalid_base64_returns_empty(self):
        from utils.encryption import decrypt_api_key

        result = decrypt_api_key("not-valid-base64!!!")
        assert result == ""

    def test_decrypt_valid_base64_wrong_content_returns_empty(self):
        import base64

        from utils.encryption import decrypt_api_key

        garbage = base64.urlsafe_b64encode(b"this-is-not-fernet-encrypted").decode()
        result = decrypt_api_key(garbage)
        assert result == ""


@pytest.mark.unit
class TestMaskApiKey:
    def test_mask_long_key(self):
        from utils.encryption import mask_api_key

        key = "sk-proj-abcdefghijklmnop-qrstuvwxyz"
        masked = mask_api_key(key)
        assert "****" in masked
        assert key not in masked

    def test_mask_short_key_returns_asterisks(self):
        from utils.encryption import mask_api_key

        assert mask_api_key("abc") == "****"

    def test_mask_empty_key_returns_asterisks(self):
        from utils.encryption import mask_api_key

        assert mask_api_key("") == "****"


@pytest.mark.unit
class TestKeyRotationStatus:
    def test_status_when_no_key_file(self, monkeypatch, tmp_path):
        monkeypatch.delenv("API_KEY_ENCRYPTION_SECRET", raising=False)
        import utils.encryption as enc
        from utils.encryption import get_key_rotation_status

        original_keys_file = enc.KEYS_FILE
        enc.KEYS_FILE = tmp_path / "nonexistent_keys.json"
        try:
            status = get_key_rotation_status()
            assert status["exists"] is False
            assert status["should_rotate"] is True
        finally:
            enc.KEYS_FILE = original_keys_file

    def test_status_after_key_creation(self, monkeypatch, tmp_path):
        monkeypatch.delenv("API_KEY_ENCRYPTION_SECRET", raising=False)
        import utils.encryption as enc

        original_keys_file = enc.KEYS_FILE
        enc.KEYS_FILE = tmp_path / "test_keys.json"
        try:
            from utils.encryption import (
                _load_or_create_encryption_key,
                get_key_rotation_status,
            )

            _load_or_create_encryption_key()
            status = get_key_rotation_status()
            assert status["exists"] is True
            assert "created_at" in status
            assert "last_rotation" in status
        finally:
            enc.KEYS_FILE = original_keys_file

    def test_status_with_env_var_set(self, monkeypatch):
        """env var 模式（生產建議）應回報 exists=True，否則 ops 監控盲點。"""
        monkeypatch.setenv(
            "API_KEY_ENCRYPTION_SECRET", "production-style-secret-32-chars-min!"
        )
        from utils.encryption import get_key_rotation_status

        status = get_key_rotation_status()
        assert status["exists"] is True
        assert status.get("source") == "env_var"

    def test_file_rotation_is_rejected_when_secret_is_externally_managed(
        self, monkeypatch
    ):
        """Writing a file key after env-key rotation would corrupt stored keys."""
        from utils.encryption import rotate_encryption_key

        monkeypatch.setenv(
            "API_KEY_ENCRYPTION_SECRET", "production-style-secret-32-chars-min!"
        )

        with pytest.raises(RuntimeError, match="externally managed"):
            rotate_encryption_key()


# ---------------------------------------------------------------------------
# v2 格式（每筆隨機 salt）＋舊格式相容
#
# 下面兩筆 LEGACY_* 密文是「改版前的 encrypt_api_key」實際產出的（固定 salt
# b"PiCryptoMiner_API_Key_Derivation"、無前綴）。secret／明文都是測試專用假值。
# 它們必須永遠解得開——線上既有使用者的金鑰就是這個格式。
# ---------------------------------------------------------------------------

LEGACY_SECRET = "unit-test-only-byok-master-secret-000001"
LEGACY_PLAINTEXT = "sk-unit-test-legacy-fixture-0001"
LEGACY_ENV_CIPHERTEXT = (
    "Z0FBQUFBQnF0ZzkyTTMyZ2JtQUQtVGdOTE52X2hyUjktbFZCbFM3T1V1Mkt0ekZOTEhUSDRvRmtB"
    "R1lrcEQtbFRTOUtuRWFpdXNUcWJQYTFpdjdnNl95dHY4MUk3bWdxZUNxQmpWeUFMZFlEZDlGb2FQ"
    "T0lyQTdMZjhBWkpicUhpTHhkU3lsQUNmY3o="
)
LEGACY_FILE_KEY = "dW5pdC10ZXN0LW9ubHktZmlsZS1rZXktMDAwMDAwMDE="
LEGACY_FILE_CIPHERTEXT = (
    "Z0FBQUFBQnF0ZzkyLVc2VFRzaTlINzlzZGZJaXQ1Sk5obEZoSC0ycllPSWpMbDQxUVNHNTBMSkds"
    "Sk5Lb29QQlFKVzBXRE5rOXJSMnpvbU9tdUVvOHU0dFBNSy1hUmNVU1hJbjBpTTFTVWZCREpGenlY"
    "NS1nbmhmdGVhQTBVWjZqR1BucG9Call5TGU="
)


@pytest.fixture
def env_secret(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("API_KEY_ENCRYPTION_SECRET", LEGACY_SECRET)


@pytest.fixture
def file_key(monkeypatch):
    import base64
    import json

    import utils.encryption as enc

    monkeypatch.delenv("API_KEY_ENCRYPTION_SECRET", raising=False)
    enc.KEYS_FILE.write_text(
        json.dumps({"key": base64.urlsafe_b64encode(LEGACY_FILE_KEY.encode()).decode()})
    )


def _v2_payload(stored: str) -> bytes:
    import base64

    assert stored.startswith("v2:")
    return base64.urlsafe_b64decode(stored[3:].encode())


@pytest.mark.unit
class TestLegacyCiphertextStillDecrypts:
    def test_legacy_env_secret_ciphertext(self, env_secret):
        from utils.encryption import decrypt_api_key

        assert decrypt_api_key(LEGACY_ENV_CIPHERTEXT) == LEGACY_PLAINTEXT

    def test_legacy_file_key_ciphertext(self, file_key):
        from utils.encryption import decrypt_api_key

        assert decrypt_api_key(LEGACY_FILE_CIPHERTEXT) == LEGACY_PLAINTEXT

    def test_legacy_is_detected_as_legacy(self):
        from utils.encryption import is_legacy_ciphertext

        assert is_legacy_ciphertext(LEGACY_ENV_CIPHERTEXT) is True
        assert is_legacy_ciphertext("v2:AAAA") is False
        assert is_legacy_ciphertext("") is False


@pytest.mark.unit
class TestV2Format:
    @pytest.mark.parametrize("mode", ["env_secret", "file_key"])
    def test_v2_roundtrip(self, request, mode):
        request.getfixturevalue(mode)
        from utils.encryption import decrypt_api_key, encrypt_api_key

        stored = encrypt_api_key("sk-unit-test-v2-roundtrip")
        assert stored.startswith("v2:")
        assert decrypt_api_key(stored) == "sk-unit-test-v2-roundtrip"

    def test_payload_starts_with_random_16_byte_salt(self, env_secret):
        from utils.encryption import encrypt_api_key

        a = _v2_payload(encrypt_api_key("same-plaintext"))
        b = _v2_payload(encrypt_api_key("same-plaintext"))
        # salt(16) | Fernet token（version 1 + ts 8 + iv 16 + ct ≥16 + hmac 32）
        assert len(a) >= 16 + 1 + 8 + 16 + 16 + 32
        assert a[:16] != b[:16], "每次加密都要換新的 salt"
        assert a[16] == 0x80, "salt 後面接的是 Fernet token（version byte 0x80）"

    def test_two_encryptions_of_same_plaintext_differ(self, env_secret):
        from utils.encryption import encrypt_api_key

        assert encrypt_api_key("same-plaintext") != encrypt_api_key("same-plaintext")

    def test_wrong_secret_fails_cleanly(self, env_secret, monkeypatch):
        import utils.encryption as enc

        stored = enc.encrypt_api_key("sk-unit-test-wrong-secret")
        monkeypatch.setenv(
            "API_KEY_ENCRYPTION_SECRET", "another-unit-test-secret-xxxxxxxxxxxx"
        )
        monkeypatch.setattr("utils.encryption._encryption_key_cache", None)
        assert enc.decrypt_api_key(stored) == ""


@pytest.mark.unit
class TestV2TamperAndTruncation:
    def _flip(self, stored: str, index: int) -> str:
        import base64

        raw = bytearray(_v2_payload(stored))
        raw[index] ^= 0x01
        return "v2:" + base64.urlsafe_b64encode(bytes(raw)).decode()

    @pytest.mark.parametrize("where", ["salt", "iv", "ciphertext", "hmac"])
    def test_tampered_v2_returns_empty(self, env_secret, where):
        from utils.encryption import decrypt_api_key, encrypt_api_key

        stored = encrypt_api_key("sk-unit-test-tamper")
        index = {"salt": 3, "iv": 16 + 9 + 2, "ciphertext": 16 + 25 + 1, "hmac": -1}[
            where
        ]
        assert decrypt_api_key(self._flip(stored, index)) == ""

    @pytest.mark.parametrize(
        "bad",
        ["v2:", "v2:AAAA", "v2:!!!not-base64!!!", "v2:" + "A" * 24],
    )
    def test_truncated_or_malformed_v2_returns_empty(self, env_secret, bad):
        from utils.encryption import decrypt_api_key

        assert decrypt_api_key(bad) == ""

    def test_truncated_real_v2_returns_empty(self, env_secret):
        import base64

        from utils.encryption import decrypt_api_key, encrypt_api_key

        raw = _v2_payload(encrypt_api_key("sk-unit-test-truncate"))
        cut = "v2:" + base64.urlsafe_b64encode(raw[:-10]).decode()
        assert decrypt_api_key(cut) == ""

    def test_failure_log_never_contains_plaintext(self, env_secret, caplog):
        from utils.encryption import decrypt_api_key, encrypt_api_key

        stored = encrypt_api_key("sk-unit-test-never-logged")
        with caplog.at_level("DEBUG"):
            decrypt_api_key(stored[:-8] + "AAAAAAAA")
        assert "sk-unit-test-never-logged" not in caplog.text


@pytest.mark.unit
class TestExplicitSecretHelpers:
    """rotate_api_key_encryption_secret.py 用：不走 process 快取、明確指定 secret。"""

    def test_decrypt_with_secret_accepts_both_formats(self):
        from utils.encryption import decrypt_with_secret, encrypt_with_secret

        assert (
            decrypt_with_secret(LEGACY_ENV_CIPHERTEXT, LEGACY_SECRET)
            == LEGACY_PLAINTEXT
        )
        stored = encrypt_with_secret("sk-unit-test-explicit", LEGACY_SECRET)
        assert stored.startswith("v2:")
        assert decrypt_with_secret(stored, LEGACY_SECRET) == "sk-unit-test-explicit"

    def test_decrypt_with_wrong_secret_raises(self):
        from cryptography.fernet import InvalidToken

        from utils.encryption import decrypt_with_secret

        with pytest.raises((InvalidToken, ValueError)):
            decrypt_with_secret(
                LEGACY_ENV_CIPHERTEXT, "wrong-unit-test-secret-xxxxxxxxxxxxxx"
            )


@pytest.mark.unit
def test_repeated_v2_decrypt_derives_key_once(env_secret):
    """每筆 salt 都跑 PBKDF2(480k) 要 ~0.2s；同一筆重複解密必須吃快取，不能每個請求重算。"""
    import utils.encryption as enc

    stored = enc.encrypt_api_key("sk-unit-test-cache")
    misses = enc._pbkdf2_fernet_key.cache_info().misses
    for _ in range(3):
        assert enc.decrypt_api_key(stored) == "sk-unit-test-cache"
    assert enc._pbkdf2_fernet_key.cache_info().misses == misses


@pytest.mark.unit
def test_file_rotation_rewrites_legacy_and_v2_rows_as_v2(file_key, monkeypatch):
    """dev 檔案金鑰輪換：兩種格式都要用舊金鑰解開、用新金鑰重寫成 v2，不能漏掉 v2 列。"""
    import utils.encryption as enc

    v2_row = enc.encrypt_api_key("sk-unit-test-rotate-v2")
    rows = [(1, LEGACY_FILE_CIPHERTEXT), (2, v2_row)]
    written: dict = {}

    class _Result:
        def fetchall(self):
            return rows

    class _Session:
        async def execute(self, stmt):
            if stmt.is_select:
                return _Result()
            params = stmt.compile().params
            written[params["id_1"]] = params["encrypted_key"]

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        "core.orm.session.get_session_factory", lambda: lambda: _Session()
    )
    result = enc.rotate_encryption_key()

    assert result["re_encrypted_count"] == 2
    assert all(v.startswith("v2:") for v in written.values())
    assert enc.decrypt_api_key(written[1]) == LEGACY_PLAINTEXT
    assert enc.decrypt_api_key(written[2]) == "sk-unit-test-rotate-v2"
