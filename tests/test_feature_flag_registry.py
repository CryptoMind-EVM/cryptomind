"""旗標的單一真相源＋跨服務一致性。

兩個坑的根治：
1. 旗標散在各處、判定寫法還不一樣（config.py 的 `== "true"` 讓 `=1` 靜默
   失效）——現在統一走 env_flag，並用登記處集中列出。
2. API 與 analysis-worker 是同一題的兩條執行路徑，Mixer 旗標只在一邊開
   會行為不一致，而且兩邊各自看都正常——啟動時主動比對並警告。
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
_ENV_RE = re.compile(
    r'(?:getenv|environ\.get|env_flag|_flag)\(\s*"([A-Z][A-Z0-9_]*_ENABLED)"'
)
_SCAN_DIRS = ("core", "api", "bot", "scripts")


def _flags_used_in_code() -> set:
    found = set()
    for d in _SCAN_DIRS:
        for path in (ROOT / d).rglob("*.py"):
            found.update(_ENV_RE.findall(path.read_text(encoding="utf-8")))
    return found


class TestRegistryIsComplete:
    def test_every_flag_in_code_is_registered(self):
        """新增旗標卻忘了登記 → 這條紅。登記處是給人看的單一清單。"""
        from core.feature_flags import FLAG_REGISTRY

        missing = sorted(_flags_used_in_code() - set(FLAG_REGISTRY))
        assert not missing, (
            f"這些 *_ENABLED 在程式碼裡用了但沒登記在 FLAG_REGISTRY：{missing}"
        )

    def test_registry_has_no_phantom_entries(self):
        """登記了但程式碼其實沒在讀 → 是死旗標（BOARD_* 那種）。"""
        from core.feature_flags import FLAG_REGISTRY

        phantom = sorted(set(FLAG_REGISTRY) - _flags_used_in_code())
        assert not phantom, f"登記了但沒有任何程式碼在讀：{phantom}"

    def test_env_example_documents_every_flag(self):
        """.env.example 是唯一進版控的旗標說明書，不能跟登記處漂移。"""
        from core.feature_flags import FLAG_REGISTRY

        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        undocumented = sorted(n for n in FLAG_REGISTRY if n not in text)
        assert not undocumented, f".env.example 沒寫到這些旗標：{undocumented}"

    def test_all_flags_resolve_without_raising(self):
        """getter 指向既有實作，值必須是真的執行期值而不是 None。"""
        from core.feature_flags import all_flags

        values = all_flags()
        unreadable = sorted(k for k, v in values.items() if v is None)
        assert not unreadable, f"讀不到值的旗標：{unreadable}"
        assert len(values) >= 20


class TestUnifiedTruthyParsing:
    """`=1` 在不同旗標上意思相反，是這次要根治的坑。"""

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " 1 "])
    def test_truthy_forms(self, monkeypatch, raw):
        from core.feature_flags import env_flag

        monkeypatch.setenv("X_TEST_FLAG", raw)
        assert env_flag("X_TEST_FLAG") is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "maybe"])
    def test_falsy_forms(self, monkeypatch, raw):
        from core.feature_flags import env_flag

        monkeypatch.setenv("X_TEST_FLAG", raw)
        assert env_flag("X_TEST_FLAG") is False

    def test_config_flags_now_accept_numeric_one(self, monkeypatch):
        """config.py 以前是 `== "true"`——`=1` 會被靜默判成關閉。"""
        import importlib

        monkeypatch.setenv("AGENT_SELF_MANAGE_ENABLED", "1")
        from core import config

        assert importlib.reload(config).AGENT_SELF_MANAGE_ENABLED is True
        monkeypatch.setenv("AGENT_SELF_MANAGE_ENABLED", "0")
        assert importlib.reload(config).AGENT_SELF_MANAGE_ENABLED is False
        # 還原成「沒設 env」的預設值（2026-10-06 起預設開）：reload 會把結果留在模組上，
        # 不還原的話後面的測試看到的旗標會和 bootstrap 開機時讀到的不一致
        monkeypatch.delenv("AGENT_SELF_MANAGE_ENABLED", raising=False)
        assert importlib.reload(config).AGENT_SELF_MANAGE_ENABLED is True

    def test_pii_scrub_keeps_deny_list_semantics(self, monkeypatch):
        """安全功能刻意不改：打錯字要維持啟用，不能靜默關掉。"""
        from core.validators.pii_scrubber import _scrub_enabled

        monkeypatch.setenv("PII_SCRUB_ENABLED", "ture")  # 手殘打錯
        assert _scrub_enabled() is True
        monkeypatch.setenv("PII_SCRUB_ENABLED", "false")
        assert _scrub_enabled() is False


class TestCrossServiceConsistency:
    def test_divergence_is_detected(self, monkeypatch):
        """worker 沒開 ROUTER_ENABLED 而 API 開了 → 必須被抓出來。"""
        import core.feature_flags as ff

        store = {}
        monkeypatch.setattr(
            ff, "all_flags", lambda: {n: n == "ROUTER_ENABLED" for n in ff.CROSS_SERVICE_FLAGS}
        )
        import core.shared_cache as sc

        monkeypatch.setattr(sc, "set_json", lambda k, v, ttl: store.__setitem__(k, v))
        monkeypatch.setattr(sc, "get_json", lambda k: store.get(k))

        assert ff.check_cross_service_flags("api") == []  # 只有自己，沒得比

        monkeypatch.setattr(ff, "all_flags", lambda: dict.fromkeys(ff.CROSS_SERVICE_FLAGS, False))
        out = ff.check_cross_service_flags("analysis-worker")
        assert ("api", "ROUTER_ENABLED", False, True) in out

    def test_no_divergence_when_aligned(self, monkeypatch):
        import core.feature_flags as ff
        import core.shared_cache as sc

        store = {}
        monkeypatch.setattr(ff, "all_flags", lambda: dict.fromkeys(ff.CROSS_SERVICE_FLAGS, True))
        monkeypatch.setattr(sc, "set_json", lambda k, v, ttl: store.__setitem__(k, v))
        monkeypatch.setattr(sc, "get_json", lambda k: store.get(k))
        ff.check_cross_service_flags("api")
        assert ff.check_cross_service_flags("analysis-worker") == []

    def test_missing_redis_never_blocks_startup(self, monkeypatch):
        import core.feature_flags as ff
        import core.shared_cache as sc

        def _boom(*_a, **_kw):
            raise RuntimeError("no redis")

        monkeypatch.setattr(sc, "set_json", _boom)
        assert ff.check_cross_service_flags("api") == []

    def test_mcp_enabled_is_not_cross_service(self):
        """2026-09-05 線上實測：env 兩邊都是 true，worker 仍因 image 沒有
        mcp-servers 而在開機時強制關閉（scripts/analysis_worker.py）。
        env 對齊救不了的分岔不該進「必須一致」清單——放進來只會每次部署
        都響一次永遠修不掉的誤報，把真正要緊的分岔（Mixer 旗標）淹掉。"""
        from core.feature_flags import CROSS_SERVICE_FLAGS

        assert "MCP_ENABLED" not in CROSS_SERVICE_FLAGS


class TestSnapshotLine:
    def test_line_is_greppable_and_lists_enabled(self, monkeypatch):
        import core.feature_flags as ff

        monkeypatch.setattr(
            ff, "all_flags", lambda: {"A_ENABLED": True, "B_ENABLED": False}
        )
        line = ff.flag_snapshot_line("api")
        assert line.startswith("[FlagSnapshot] service=api")
        # 2026-09-05：拆成 shared=／local=。混在一個 on= 裡的話，比對兩個服務
        # 會看到 5 個差異但只有 1 個要緊，用眼睛分不出來。
        assert "A_ENABLED" in line and "B_ENABLED" not in line

    def test_shared_and_local_are_separated(self, monkeypatch):
        """shared= 是兩個服務必須一致的那些；local= 不同很正常。"""
        import core.feature_flags as ff

        monkeypatch.setattr(
            ff, "all_flags",
            lambda: {"ROUTER_ENABLED": True, "MCP_ENABLED": True, "OFF_ONE": False},
        )
        line = ff.flag_snapshot_line("api")
        shared = line.split("shared=")[1].split(" ")[0]
        local = line.split("local=")[1].split(" ")[0]
        assert "ROUTER_ENABLED" in shared, "跨服務旗標要在 shared= 段"
        assert "MCP_ENABLED" in local, (
            "MCP_ENABLED 是 image 結構性的本地差異（worker 刻意關），要在 local= 段"
        )
        assert "OFF_ONE" not in line, "關著的不列"

    def test_every_shared_flag_is_actually_cross_service(self):
        """shared= 只能收 CROSS_SERVICE_FLAGS——多收就把雜訊混進要比對的那段。"""
        import core.feature_flags as ff

        assert set(ff.CROSS_SERVICE_FLAGS) <= set(ff.FLAG_REGISTRY), (
            "CROSS_SERVICE_FLAGS 有不在登記處的項目"
        )


class TestLogFlagState:
    """log_flag_state 的兩個不變式——都是踩過才補的。"""

    def test_lifespan_still_an_async_context_manager(self):
        """把新函式插進 lifespan.py 時，@asynccontextmanager 曾經被擠到新函式上。

        後果：API 啟動壞掉，而快照被包成 context manager → 函式本體不執行，
        整條靜默 no-op。全量測試 92% 都沒紅，所以這裡明確釘住。
        """
        import inspect

        from api.lifespan import lifespan

        assert hasattr(lifespan, "__wrapped__"), "lifespan 沒有被 @asynccontextmanager 裝飾"
        assert inspect.isasyncgenfunction(lifespan.__wrapped__)

    def test_logs_snapshot_and_never_raises(self, monkeypatch):
        import core.feature_flags as ff

        class _Log:
            def __init__(self):
                self.info_lines, self.warn_lines = [], []

            def info(self, msg, *a):
                self.info_lines.append(msg % a if a else msg)

            def warning(self, msg, *a):
                self.warn_lines.append(msg % a if a else msg)

        log = _Log()
        monkeypatch.setattr(ff, "all_flags", lambda: {"A_ENABLED": True})
        monkeypatch.setattr(ff, "check_cross_service_flags", lambda s: [])
        ff.log_flag_state("api", log)
        assert any("[FlagSnapshot]" in ln for ln in log.info_lines)

        # 內部炸掉也不能擋啟動
        def _boom(_s):
            raise RuntimeError("boom")

        monkeypatch.setattr(ff, "check_cross_service_flags", _boom)
        ff.log_flag_state("api", log)  # 不得拋出
        assert any("non-fatal" in ln for ln in log.warn_lines)

    def test_divergence_is_warned_loudly(self, monkeypatch):
        import core.feature_flags as ff

        seen = []

        class _Log:
            def info(self, *a): pass

            def warning(self, msg, *a):
                seen.append(msg % a if a else msg)

        monkeypatch.setattr(ff, "all_flags", lambda: {"A_ENABLED": True})
        monkeypatch.setattr(
            ff, "check_cross_service_flags",
            lambda s: [("analysis-worker", "ROUTER_ENABLED", True, False)],
        )
        ff.log_flag_state("api", _Log())
        assert any("FlagDivergence" in ln and "ROUTER_ENABLED" in ln for ln in seen)


class TestNoPhantomFlags:
    """登記處的旗標必須真的有人讀。

    2026-09-04：AI_STUDIO_ENABLED 全 repo 只有 getter 定義與登記兩處，
    沒有任何產品程式碼讀它。設計文件說「off 時不註冊 route」，那條規格
    從沒實作——所以 #ai-studio 在旗標關著時仍可 deep-link 進去，而它呼叫
    的每支 API 都 404。使用者拿到一個載得起來、什麼都做不了的頁面。

    這一類「開關看起來在、其實沒接線」不會噴錯，只會讓人以為關著。
    """

    _EXCLUDE = (
        "core/feature_flags.py",  # 定義與登記本身不算消費
        "tests/", "docs/", "deploy-env/", "__pycache__",
        "web/assets/",            # Vite 產物是 web/js 的副本
        "alembic/",
    )
    _ROOTS = ["core", "api", "scripts", "web/js", "api_server.py"]

    def _consumers(self, name: str, getter) -> set:
        """回傳「讀」這個旗標的檔案。

        行級判斷而非檔級：core/config.py 裡的 ``NAME = env_flag("NAME")`` 是
        **定義**不是消費，算進去的話 config 系旗標永遠測不出幽靈。
        """
        import re
        import subprocess

        pats = [name]
        fn = getattr(getter, "__name__", "")
        if fn not in ("", "getter", "<lambda>"):
            pats.append(fn)
        # 定義行：NAME = ...、或以 env_flag/_flag/os.getenv 讀原始 env
        definition = re.compile(
            rf"^\s*{re.escape(name)}\s*=|"
            rf"(env_flag|_flag|os\.getenv)\(\s*[\"']{re.escape(name)}[\"']"
        )
        found = set()
        for pat in pats:
            out = subprocess.run(
                ["grep", "-rn", "--include=*.py", "--include=*.js", pat, *self._ROOTS],
                capture_output=True, text=True,
                # grep 會印出含中文的原始碼行；不指定 encoding 的話 Windows 用
                # cp950 解碼就炸，stdout 變 None，這裡直接 AttributeError。
                encoding="utf-8", errors="replace",
            ).stdout.splitlines()
            for line in out:
                path, _, text = line.partition(":")
                if any(e in path for e in self._EXCLUDE):
                    continue
                body = text.partition(":")[2]
                if definition.search(body):
                    continue
                found.add(path)
        return found

    def test_every_registered_flag_has_a_consumer(self):
        import core.feature_flags as ff

        phantom = [
            name
            for name, (getter, _d, _n) in ff.FLAG_REGISTRY.items()
            if name not in ff.UNWIRED_FLAGS and not self._consumers(name, getter)
        ]
        assert not phantom, (
            f"這些旗標沒有任何產品程式碼讀它，開了等於沒開：{phantom}。"
            "接上它，或從 FLAG_REGISTRY 移除；真的暫時修不了就加進 UNWIRED_FLAGS 並寫明原因。"
        )

    def test_unwired_list_stays_honest(self):
        """接好了就要從清單移掉，否則清單本身變成掩蓋用的地毯。"""
        import core.feature_flags as ff

        still_unwired = []
        for name in ff.UNWIRED_FLAGS:
            assert name in ff.FLAG_REGISTRY, f"{name} 已不在登記處，請從 UNWIRED_FLAGS 移除"
            getter = ff.FLAG_REGISTRY[name][0]
            if not self._consumers(name, getter):
                still_unwired.append(name)
        assert set(still_unwired) == set(ff.UNWIRED_FLAGS), (
            "這些旗標已經接上線了，請從 UNWIRED_FLAGS 移除："
            f"{sorted(set(ff.UNWIRED_FLAGS) - set(still_unwired))}"
        )


class TestSchemaVersionPair:
    """worker 開機等 schema 就緒用的 (db, head) 讀取（2026-09-05）。"""

    def test_pair_returns_db_and_head(self, monkeypatch):
        import core.database
        import core.feature_flags as ff

        cur = mock.MagicMock()
        cur.fetchone.return_value = ("c044",)
        conn = mock.MagicMock()
        conn.cursor.return_value = cur
        monkeypatch.setattr(core.database, "get_connection", lambda: conn)
        db, head = ff.schema_version_pair()
        assert db == "c044"
        assert isinstance(head, str) and head, "repo 的 alembic head 要讀得到"

    def test_db_failure_returns_none_db_but_head(self, monkeypatch):
        """db 讀不到＝還在起或暫時斷線——worker 要能分辨「等不到」繼續等。"""
        import core.database
        import core.feature_flags as ff

        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(core.database, "get_connection", _boom)
        db, head = ff.schema_version_pair()
        assert db is None
        assert isinstance(head, str) and head

    def test_line_still_greppable_from_pair(self, monkeypatch):
        """schema_version_line 改吃 pair 之後，輸出格式不能變（可 grep 性）。"""
        import core.feature_flags as ff

        monkeypatch.setattr(ff, "schema_version_pair", lambda: ("c044", "c044"))
        assert ff.schema_version_line() == "[SchemaVersion] db=c044 head=c044 ok"
        monkeypatch.setattr(ff, "schema_version_pair", lambda: ("c042", "c044"))
        line = ff.schema_version_line()
        assert line.startswith("⚠️ [SchemaDrift] db=c042 head=c044")
