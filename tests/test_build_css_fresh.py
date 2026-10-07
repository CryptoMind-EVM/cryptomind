"""tailwind-built.css 新鮮度檢查（2026-08-20，#507 事故的產物）。

web/css/tailwind-built.css 是 repo 內直接部署的編譯產物——HTML/JS 新增
的 Tailwind class 若未重編，部署後樣式靜默失效（#507：md:self-start 不在
部署 CSS 裡、nav 容器蓋滿畫面）。本測試重跑 build:css 並比對位元組，
不一致即 fail——在本地套件與 CI（帳單修復後）都會把關。

無 npm 環境（罕見）跳過，避免誤傷。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
BUILT = ROOT / 'web' / 'css' / 'tailwind-built.css'


def test_tailwind_built_css_is_fresh():
    # Windows 上 which() 回的是 npm.CMD；下面 subprocess 必須用這個解析後的
    # 絕對路徑，不能用裸字串 'npm'——CreateProcess 不做 PATHEXT 查找，
    # 會 FileNotFoundError（skip 守門通過、實際執行卻炸）。
    npm = shutil.which('npm')
    if npm is None:
        pytest.skip('npm 不可用，跳過編譯新鮮度檢查')
    # npm 存在但沒裝 node_modules（如 CI test job 只有 pip 環境）時，
    # tailwindcss binary 不在，build:css 必然 exit 1——這是環境缺件不是
    # 產物過期，照本測試的 skip 哲學跳過（ci.yml 已補 setup-node + npm ci，
    # 讓本檢查在 CI 真的執行）。
    if not (ROOT / 'node_modules' / '.bin' / 'tailwindcss').exists():
        pytest.skip('node_modules 未安裝（缺 tailwindcss binary），跳過編譯新鮮度檢查')

    # 編到暫存檔比對，**不覆寫版控裡的產物**。
    # 2026-09-04：原本是就地重跑 build:css 再比對 before/after——真的過期時
    # 第一次紅、但檔案已被就地修好，第二次跑就綠。等於「再跑一次」可以讓守衛
    # 閉嘴，而該 commit 的產物沒人 commit（實際踩到：#638 的
    # dark:text-amber-400 沒編進去，深色模式警示文字無色）。
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / 'tailwind-built.css'
        subprocess.run(
            [npm, 'exec', '--', 'tailwindcss',
             '-i', str(ROOT / 'web/css/tailwind-src.css'),
             '-o', str(candidate), '--minify'],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=180,
        )
        before = BUILT.read_bytes()
        after = candidate.read_bytes()

    assert before == after, (
        'web/css/tailwind-built.css 與 tailwind-src.css 不同步——'
        '新增了 Tailwind class 但未重編。請跑 `npm run build:css` 並 commit 產物，'
        '否則部署後新 class 樣式靜默失效（見 PR #507 事故）。'
    )
