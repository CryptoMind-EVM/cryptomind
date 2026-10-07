"""部署保留舊版 chunk（scripts/keep_old_assets.sh，2026-10-02）。

部署後 origin 只剩新版 hashed chunk：開著的舊頁面一懶載入舊 chunk 就 404，
stale-chunk-recovery.js 只好整頁重載（一天 merge 6 次＝開著的頁面被刷 6 次）。
app 啟動時把封存 volume 裡的舊 chunk 補回這版的 web/assets、把這版存進封存，14 天後清掉。
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "keep_old_assets.sh"


def _write(path: Path, text: str, days_old: float = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if days_old:
        t = time.time() - days_old * 86400
        os.utime(path, (t, t))


def _run(assets: Path, archive: Path, days: int = 14):
    return subprocess.run(
        ["sh", str(SCRIPT), str(assets), str(archive), str(days)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_restores_old_chunks_and_archives_new(tmp_path):
    assets, archive = tmp_path / "assets", tmp_path / "archive"
    _write(assets / "main-NEW.js", "new main")
    _write(assets / "forum" / "post-NEW.js", "new post")
    _write(assets / "shared-SAME.js", "image copy")
    _write(archive / "main-OLD.js", "old main", days_old=2)
    _write(archive / "forum" / "post-OLD.js", "old post", days_old=2)
    _write(archive / "shared-SAME.js", "archived copy", days_old=2)

    proc = _run(assets, archive)
    assert proc.returncode == 0, proc.stderr

    names = {p.relative_to(assets).as_posix() for p in assets.rglob("*") if p.is_file()}
    assert {"main-OLD.js", "forum/post-OLD.js", "main-NEW.js", "forum/post-NEW.js"} <= names, (
        "舊版補回來（含子目錄），新版還在"
    )
    assert (assets / "shared-SAME.js").read_text() == "image copy", "同名不覆蓋這版的檔"
    assert (archive / "main-NEW.js").read_text() == "new main", "這版存進封存"
    assert (archive / "forum" / "post-NEW.js").exists()


def test_prunes_archive_after_retention_but_keeps_current_build(tmp_path):
    assets, archive = tmp_path / "assets", tmp_path / "archive"
    _write(assets / "vendor-STILLUSED.js", "vendor")
    _write(archive / "vendor-STILLUSED.js", "vendor", days_old=30)  # 一直沿用的 chunk
    _write(archive / "main-ANCIENT.js", "ancient", days_old=30)
    _write(archive / "main-RECENT.js", "recent", days_old=3)

    assert _run(assets, archive, days=14).returncode == 0
    assert not (assets / "main-ANCIENT.js").exists(), "過期的不補回"
    assert not (archive / "main-ANCIENT.js").exists(), "過期的清掉"
    assert (assets / "main-RECENT.js").exists()
    assert (archive / "vendor-STILLUSED.js").exists(), "這版還在用的重新存回封存"


def test_missing_archive_is_a_noop(tmp_path):
    assets = tmp_path / "assets"
    _write(assets / "main-NEW.js", "new")
    proc = _run(assets, tmp_path / "no-volume")
    assert proc.returncode == 0, "沒掛 volume（本機、其他部署方式）不能擋啟動"
    assert [p.name for p in assets.iterdir()] == ["main-NEW.js"]


