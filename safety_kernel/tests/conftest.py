"""safety-kernel 測試路徑設定。

讓 `import safety_kernel` 在任意 cwd 下都能運作：
- 在主平台 repo 內：把 repo root 加入 sys.path（repo root = parents[2]）。
- 鏡像到公開 repo 後：公開 repo root 就是套件根（parents[2] 同樣成立）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
